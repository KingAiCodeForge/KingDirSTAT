"""Read-only analysis helpers built on a completed scan."""

from __future__ import annotations

import fnmatch
import hashlib
import heapq
import re
import threading
from collections import defaultdict
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import Entry, ScanError, ScanResult


@dataclass(frozen=True, slots=True)
class FileTypeStat:
    extension: str
    files: int
    size: int
    allocated_size: int


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    digest: str
    size: int
    files: tuple[Entry, ...]
    reclaimable_size: int


@dataclass(slots=True)
class DuplicateReport:
    groups: list[DuplicateGroup] = field(default_factory=list)
    errors: list[ScanError] = field(default_factory=list)

    @property
    def reclaimable_size(self) -> int:
        return sum(group.reclaimable_size for group in self.groups)


@dataclass(frozen=True, slots=True)
class SnapshotChange:
    relative_path: str
    before_size: int | None
    after_size: int | None

    @property
    def delta(self) -> int:
        return (self.after_size or 0) - (self.before_size or 0)


@dataclass(slots=True)
class SnapshotDiff:
    before_root: str
    after_root: str
    added: list[SnapshotChange]
    removed: list[SnapshotChange]
    changed: list[SnapshotChange]
    bytes_delta: int


def iter_files(root: Entry) -> Iterable[Entry]:
    return (entry for entry in root.walk() if not entry.is_dir)


def iter_directories(root: Entry, *, include_root: bool = True) -> Iterable[Entry]:
    return (entry for entry in root.walk(include_self=include_root) if entry.is_dir)


def largest_entries(
    root: Entry,
    *,
    limit: int = 20,
    minimum_size: int = 0,
    include_directories: bool = False,
) -> list[Entry]:
    candidates = (
        entry
        for entry in root.walk(include_self=False)
        if entry.size >= minimum_size and (include_directories or not entry.is_dir)
    )
    if limit <= 0:
        return []
    return heapq.nsmallest(
        limit,
        candidates,
        key=lambda entry: (-entry.size, entry.path.casefold(), entry.path),
    )


def validate_search_options(
    query: str,
    *,
    regex: bool = False,
    case_sensitive: bool = False,
    minimum_size: int = 0,
    maximum_size: int | None = None,
) -> re.Pattern[str] | None:
    """Validate search inputs and compile a regular expression when requested."""

    if query == "":
        raise ValueError("Search query must not be empty")
    if maximum_size is not None and maximum_size < minimum_size:
        raise ValueError("Maximum size must be greater than or equal to minimum size")
    if not regex:
        return None

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(query, flags)
    except re.error as exc:
        raise ValueError(f"Invalid regular expression {query!r}: {exc}") from exc


def search_entries(
    root: Entry,
    query: str,
    *,
    regex: bool = False,
    case_sensitive: bool = False,
    include_directories: bool = False,
    minimum_size: int = 0,
    maximum_size: int | None = None,
    limit: int | None = None,
) -> list[Entry]:
    """Search entry names and root-relative paths without changing the scan.

    Literal, case-insensitive matching is the default. Regular expressions are
    compiled once so malformed patterns fail before any results are returned.
    Size bounds are inclusive.
    """

    pattern = validate_search_options(
        query,
        regex=regex,
        case_sensitive=case_sensitive,
        minimum_size=minimum_size,
        maximum_size=maximum_size,
    )
    if limit is not None and limit <= 0:
        return []

    if pattern is not None:

        def matches(name: str, relative_path: str) -> bool:
            return pattern.search(name) is not None or pattern.search(relative_path) is not None

    else:
        name_needle = query if case_sensitive else query.casefold()
        path_needle = name_needle.replace("\\", "/")

        def matches(name: str, relative_path: str) -> bool:
            if not case_sensitive:
                name = name.casefold()
                relative_path = relative_path.casefold()
            return name_needle in name or path_needle in relative_path

    def matching_entries() -> Iterable[Entry]:
        stack: list[tuple[Entry, str]] = [(child, child.name) for child in reversed(root.children)]
        while stack:
            entry, relative_path = stack.pop()
            if (
                (include_directories or not entry.is_dir)
                and entry.size >= minimum_size
                and (maximum_size is None or entry.size <= maximum_size)
                and matches(entry.name, relative_path)
            ):
                yield entry
            if entry.is_dir:
                for child in reversed(entry.children):
                    stack.append((child, f"{relative_path}/{child.name}"))

    def order(entry: Entry) -> tuple[int, str, str]:
        return -entry.size, entry.path.casefold(), entry.path

    if limit is None:
        return sorted(matching_entries(), key=order)
    return heapq.nsmallest(limit, matching_entries(), key=order)


def file_type_stats(root: Entry) -> list[FileTypeStat]:
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for entry in iter_files(root):
        extension = entry.extension or "(no extension)"
        bucket = totals[extension]
        bucket[0] += 1
        bucket[1] += entry.size
        bucket[2] += entry.allocated_size
    return sorted(
        (
            FileTypeStat(
                extension=extension, files=values[0], size=values[1], allocated_size=values[2]
            )
            for extension, values in totals.items()
        ),
        key=lambda item: (-item.size, item.extension),
    )


def empty_directories(root: Entry) -> list[Entry]:
    return sorted(
        (
            entry
            for entry in iter_directories(root, include_root=False)
            if entry.scan_complete and not entry.children
        ),
        key=lambda entry: entry.path.casefold(),
    )


def stale_files(root: Entry, *, older_than_days: int) -> list[Entry]:
    threshold = datetime.now(timezone.utc).timestamp() - max(0, older_than_days) * 86400
    return sorted(
        (
            entry
            for entry in iter_files(root)
            if entry.modified_time is not None and entry.modified_time < threshold
        ),
        key=lambda entry: (entry.modified_time or 0, -entry.size),
    )


def validate_hash_algorithm(algorithm: str) -> None:
    """Reject unsupported or variable-length digests before opening files."""

    try:
        hasher = hashlib.new(algorithm)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Unsupported hash algorithm: {algorithm}") from exc
    if hasher.digest_size <= 0:
        raise ValueError(f"Hash algorithm requires an output length: {algorithm}")


def find_duplicates(
    root: Entry,
    *,
    minimum_size: int = 1,
    algorithm: str = "sha256",
    workers: int = 4,
    stop_event: threading.Event | None = None,
) -> DuplicateReport:
    """Hash same-size candidates; matching hashes are true content duplicates."""

    validate_hash_algorithm(algorithm)
    stop = stop_event or threading.Event()
    by_size: dict[int, list[Entry]] = defaultdict(list)
    for entry in iter_files(root):
        # Opening a cloud placeholder or another reparse-point file can hydrate
        # remote content. Duplicate analysis hashes regular local files only.
        if entry.size >= minimum_size and not entry.is_symlink and not entry.is_reparse_point:
            by_size[entry.size].append(entry)

    candidates = [entry for entries in by_size.values() if len(entries) > 1 for entry in entries]
    report = DuplicateReport()

    def digest(entry: Entry) -> tuple[Entry, str | None, ScanError | None]:
        if stop.is_set():
            return entry, None, None
        try:
            hasher = hashlib.new(algorithm)
            with open(entry.path, "rb") as stream:
                while block := stream.read(1024 * 1024):
                    if stop.is_set():
                        return entry, None, None
                    hasher.update(block)
            return entry, hasher.hexdigest(), None
        except OSError as exc:
            return (
                entry,
                None,
                ScanError(
                    path=entry.path,
                    operation="hash",
                    message=exc.strerror or str(exc),
                ),
            )

    by_digest: dict[tuple[int, str], list[Entry]] = defaultdict(list)
    with ThreadPoolExecutor(max_workers=max(1, min(32, workers))) as executor:
        for entry, file_digest, error in executor.map(digest, candidates):
            if error is not None:
                report.errors.append(error)
            elif file_digest is not None:
                by_digest[(entry.size, file_digest)].append(entry)

    for (size, file_digest), entries in by_digest.items():
        if len(entries) < 2:
            continue
        identities = {
            (entry.device, entry.inode)
            if entry.device is not None and entry.inode not in (None, 0)
            else (entry.path, entry.path)
            for entry in entries
        }
        reclaimable = max(0, len(identities) - 1) * size
        report.groups.append(
            DuplicateGroup(
                digest=file_digest,
                size=size,
                files=tuple(sorted(entries, key=lambda entry: entry.path.casefold())),
                reclaimable_size=reclaimable,
            )
        )
    report.groups.sort(key=lambda group: (-group.reclaimable_size, -group.size, group.digest))
    return report


def compare_results(before: ScanResult, after: ScanResult) -> SnapshotDiff:
    before_items = _relative_entries(before.root)
    after_items = _relative_entries(after.root)
    before_keys = set(before_items)
    after_keys = set(after_items)

    added = [
        SnapshotChange(path, None, after_items[path].size)
        for path in sorted(after_keys - before_keys)
    ]
    removed = [
        SnapshotChange(path, before_items[path].size, None)
        for path in sorted(before_keys - after_keys)
    ]
    changed = [
        SnapshotChange(path, before_items[path].size, after_items[path].size)
        for path in sorted(before_keys & after_keys)
        if before_items[path].size != after_items[path].size
        or before_items[path].modified_time != after_items[path].modified_time
    ]
    return SnapshotDiff(
        before_root=before.root.path,
        after_root=after.root.path,
        added=added,
        removed=removed,
        changed=changed,
        bytes_delta=after.total_size - before.total_size,
    )


def _relative_entries(root: Entry) -> dict[str, Entry]:
    result: dict[str, Entry] = {}
    stack: list[tuple[Entry, str]] = [(root, ".")]
    while stack:
        entry, relative_path = stack.pop()
        result[relative_path] = entry
        for child in entry.children:
            child_path = child.name if relative_path == "." else f"{relative_path}/{child.name}"
            stack.append((child, child_path))
    return result


def scan_summary(result: ScanResult) -> dict[str, object]:
    types = file_type_stats(result.root)
    empties = empty_directories(result.root)
    return {
        "root": result.root.path,
        "created_at": result.created_at,
        "duration_seconds": round(result.duration, 6),
        "files": result.files,
        "directories": result.directories,
        "logical_size": result.total_size,
        "allocated_size": result.total_allocated_size,
        "errors": len(result.errors),
        "cancelled": result.cancelled,
        "file_types": len(types),
        "empty_directories": len(empties),
    }


@dataclass(frozen=True, slots=True)
class TreeRow:
    """One line of the WinDirStat-style directory pane."""

    entry: Entry
    depth: int
    share_of_parent: float
    share_of_total: float


def tree_rows(
    root: Entry,
    *,
    max_depth: int | None = None,
    minimum_size: int = 0,
    directories_only: bool = False,
    children_limit: int | None = None,
) -> list[TreeRow]:
    """Flatten the tree depth-first, largest first, carrying percentage shares.

    Children are already size-sorted by the scanner, so this preserves the same
    ordering WinDirStat shows in its directory pane.
    """

    total = root.size
    rows: list[TreeRow] = []
    stack: list[tuple[Entry, int, float, float]] = [(root, 0, 100.0, 100.0)]
    while stack:
        entry, depth, share_parent, share_total = stack.pop()
        rows.append(
            TreeRow(
                entry=entry,
                depth=depth,
                share_of_parent=share_parent,
                share_of_total=share_total,
            )
        )
        if max_depth is not None and depth >= max_depth:
            continue
        children = [
            child
            for child in entry.children
            if child.size >= minimum_size and (child.is_dir or not directories_only)
        ]
        if children_limit is not None:
            children = children[:children_limit]
        for child in reversed(children):
            stack.append(
                (
                    child,
                    depth + 1,
                    _share(child.size, entry.size),
                    _share(child.size, total),
                )
            )
    return rows


def _share(part: int, whole: int) -> float:
    return part / whole * 100.0 if whole else 0.0


@dataclass(frozen=True, slots=True)
class CleanupRule:
    name: str
    pattern: str
    kind: str
    confidence: str
    note: str


@dataclass(frozen=True, slots=True)
class CleanupCandidate:
    entry: Entry
    rule: CleanupRule

    @property
    def size(self) -> int:
        return self.entry.size


# Regenerable build and cache artefacts. "safe" means the content is rebuilt
# automatically; "review" means it may be a deliverable you still want.
CLEANUP_RULES: tuple[CleanupRule, ...] = (
    CleanupRule("python-bytecode", "__pycache__", "dir", "safe", "rebuilt on next import"),
    CleanupRule("mypy-cache", ".mypy_cache", "dir", "safe", "rebuilt on next type check"),
    CleanupRule("pytest-cache", ".pytest_cache", "dir", "safe", "rebuilt on next test run"),
    CleanupRule("ruff-cache", ".ruff_cache", "dir", "safe", "rebuilt on next lint"),
    CleanupRule("tox-envs", ".tox", "dir", "safe", "rebuilt by tox"),
    CleanupRule("node-modules", "node_modules", "dir", "safe", "restore with npm install"),
    CleanupRule("next-cache", ".next", "dir", "safe", "rebuilt by next build"),
    CleanupRule("parcel-cache", ".parcel-cache", "dir", "safe", "rebuilt by parcel"),
    CleanupRule(
        "gradle-cache",
        ".gradle",
        "dir",
        "review",
        "user-level .gradle may contain configuration and init scripts",
    ),
    CleanupRule("cmake-files", "CMakeFiles", "dir", "safe", "regenerated by cmake"),
    CleanupRule(
        "cargo-target", "target", "dir", "review", "Rust output; also a common folder name"
    ),
    CleanupRule("build-output", "build", "dir", "review", "build output; may hold deliverables"),
    CleanupRule("dist-output", "dist", "dir", "review", "packaged output; may hold deliverables"),
    CleanupRule("python-venv", ".venv", "dir", "review", "virtualenv; rebuild costs a reinstall"),
    CleanupRule("compiled-python", "*.pyc", "file", "safe", "rebuilt on next import"),
    CleanupRule("compiled-python", "*.pyo", "file", "safe", "rebuilt on next import"),
    CleanupRule("thumbnail-cache", "Thumbs.db", "file", "safe", "regenerated by Explorer"),
    CleanupRule("temp-file", "*.tmp", "file", "review", "temporary file; may be in use"),
)


def cleanup_candidates(
    root: Entry,
    *,
    rules: Sequence[CleanupRule] = CLEANUP_RULES,
    confidence: str | None = None,
    minimum_size: int = 0,
) -> list[CleanupCandidate]:
    """Find regenerable artefacts. Nothing is deleted; this only reports.

    A matched directory is not descended into, so nested matches are never
    double counted in the reported total.
    """

    directory_rules = [rule for rule in rules if rule.kind == "dir"]
    file_rules = [rule for rule in rules if rule.kind == "file"]
    if confidence is not None:
        directory_rules = [rule for rule in directory_rules if rule.confidence == confidence]
        file_rules = [rule for rule in file_rules if rule.confidence == confidence]

    found: list[CleanupCandidate] = []
    stack: list[Entry] = [root]
    while stack:
        entry = stack.pop()
        for child in entry.children:
            if child.is_dir:
                match = _first_match(child.name, directory_rules)
                if match is not None:
                    if child.size >= minimum_size:
                        found.append(CleanupCandidate(entry=child, rule=match))
                    continue  # do not descend: avoids double counting
                stack.append(child)
            else:
                match = _first_match(child.name, file_rules)
                if match is not None and child.size >= minimum_size:
                    found.append(CleanupCandidate(entry=child, rule=match))
    found.sort(key=lambda item: (-item.size, item.entry.path.casefold()))
    return found


def _first_match(name: str, rules: Sequence[CleanupRule]) -> CleanupRule | None:
    lowered = name.casefold()
    for rule in rules:
        pattern = rule.pattern.casefold()
        if "*" in pattern or "?" in pattern:
            if fnmatch.fnmatch(lowered, pattern):
                return rule
        elif lowered == pattern:
            return rule
    return None
