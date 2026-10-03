"""Serializable data models shared by the scanner, CLI, and GUI."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1

# Windows file attribute bits, in the order WinDirStat displays them.
FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_ARCHIVE = 0x20
FILE_ATTRIBUTE_TEMPORARY = 0x100
FILE_ATTRIBUTE_SPARSE_FILE = 0x200
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_COMPRESSED = 0x800
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_ENCRYPTED = 0x4000
ATTRIBUTE_FLAGS: tuple[tuple[str, int], ...] = (
    ("R", FILE_ATTRIBUTE_READONLY),
    ("H", FILE_ATTRIBUTE_HIDDEN),
    ("S", FILE_ATTRIBUTE_SYSTEM),
    ("A", FILE_ATTRIBUTE_ARCHIVE),
    ("T", FILE_ATTRIBUTE_TEMPORARY),
    ("P", FILE_ATTRIBUTE_SPARSE_FILE),
    ("J", FILE_ATTRIBUTE_REPARSE_POINT),
    ("C", FILE_ATTRIBUTE_COMPRESSED),
    ("O", FILE_ATTRIBUTE_OFFLINE),
    ("E", FILE_ATTRIBUTE_ENCRYPTED),
)


@dataclass(slots=True)
class Entry:
    """One filesystem entry in a completed scan tree."""

    name: str
    path: str
    is_dir: bool
    size: int = 0
    allocated_size: int = 0
    depth: int = 0
    modified_time: float | None = None
    created_time: float | None = None
    extension: str = ""
    is_symlink: bool = False
    is_reparse_point: bool = False
    file_attributes: int = 0
    is_hardlink_duplicate: bool = False
    scan_complete: bool = True
    skip_reason: str | None = None
    file_count: int = 0
    directory_count: int = 0
    device: int | None = None
    inode: int | None = None
    children: list[Entry] = field(default_factory=list)

    @property
    def is_directory(self) -> bool:
        """Compatibility alias used by KingDirSTAT 1.x integrations."""

        return self.is_dir

    @property
    def last_modified(self) -> float | None:
        """Compatibility alias used by KingDirSTAT 1.x integrations."""

        return self.modified_time

    @property
    def creation_time(self) -> float | None:
        """Compatibility alias used by KingDirSTAT 1.x integrations."""

        return self.created_time

    @property
    def attribute_string(self) -> str:
        """Compact WinDirStat-style attribute flags, for example ``RHSAC``."""

        flags = self.file_attributes
        if not flags:
            return ""
        return "".join(letter for letter, mask in ATTRIBUTE_FLAGS if flags & mask)

    def walk(self, *, include_self: bool = True) -> Iterator[Entry]:
        """Iterate without recursion so very deep trees remain safe."""

        stack = [self]
        first = True
        while stack:
            item = stack.pop()
            if include_self or not first:
                yield item
            first = False
            stack.extend(reversed(item.children))

    def to_dict(self, *, include_children: bool = True) -> dict[str, Any]:
        payload = self._base_dict()
        if not include_children:
            return payload
        payload["children"] = []
        stack: list[tuple[Entry, dict[str, Any]]] = [(self, payload)]
        while stack:
            source, target = stack.pop()
            target_children: list[dict[str, Any]] = []
            target["children"] = target_children
            for child in source.children:
                child_payload = child._base_dict()
                child_payload["children"] = []
                target_children.append(child_payload)
                stack.append((child, child_payload))
        return payload

    def _base_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "is_dir": self.is_dir,
            "size": self.size,
            "allocated_size": self.allocated_size,
            "depth": self.depth,
            "modified_time": self.modified_time,
            "created_time": self.created_time,
            "extension": self.extension,
            "is_symlink": self.is_symlink,
            "is_reparse_point": self.is_reparse_point,
            "file_attributes": self.file_attributes,
            "is_hardlink_duplicate": self.is_hardlink_duplicate,
            "scan_complete": self.scan_complete,
            "skip_reason": self.skip_reason,
            "file_count": self.file_count,
            "directory_count": self.directory_count,
            "device": self.device,
            "inode": self.inode,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Entry:
        root = cls._from_base_dict(payload)
        stack: list[tuple[Entry, dict[str, Any]]] = [(root, payload)]
        while stack:
            target, source = stack.pop()
            for child_payload in source.get("children", []):
                child = cls._from_base_dict(child_payload)
                target.children.append(child)
                stack.append((child, child_payload))
        return root

    @classmethod
    def _from_base_dict(cls, payload: dict[str, Any]) -> Entry:
        known = {
            "name",
            "path",
            "is_dir",
            "size",
            "allocated_size",
            "depth",
            "modified_time",
            "created_time",
            "extension",
            "is_symlink",
            "is_reparse_point",
            "file_attributes",
            "is_hardlink_duplicate",
            "scan_complete",
            "skip_reason",
            "file_count",
            "directory_count",
            "device",
            "inode",
        }
        values = {key: value for key, value in payload.items() if key in known}
        values.setdefault("allocated_size", values.get("size", 0))
        values.setdefault("depth", 0)
        values.setdefault("extension", "")
        values.setdefault("scan_complete", True)
        return cls(**values)


@dataclass(frozen=True, slots=True)
class ScanOptions:
    """Controls traversal. Scans include hidden and system entries by default."""

    workers: int = 4
    exclude: tuple[str, ...] = ()
    max_depth: int | None = None
    follow_symlinks: bool = False
    same_file_system: bool = False
    measure_allocated: bool = False
    dedupe_hardlinks: bool = False

    def normalized(self) -> ScanOptions:
        depth = None if self.max_depth is None else max(0, self.max_depth)
        return ScanOptions(
            workers=max(1, min(32, self.workers)),
            exclude=tuple(pattern.strip() for pattern in self.exclude if pattern.strip()),
            max_depth=depth,
            follow_symlinks=self.follow_symlinks,
            same_file_system=self.same_file_system,
            measure_allocated=self.measure_allocated,
            dedupe_hardlinks=self.dedupe_hardlinks,
        )


@dataclass(frozen=True, slots=True)
class ScanError:
    path: str
    operation: str
    message: str


@dataclass(frozen=True, slots=True)
class ScanProgress:
    current_path: str
    files_scanned: int
    directories_scanned: int
    total_size: int
    errors_encountered: int
    scan_rate: float
    elapsed: float
    cancelled: bool = False
    estimated_remaining: float | None = None


@dataclass(slots=True)
class ScanResult:
    root: Entry
    duration: float
    files: int
    directories: int
    total_size: int
    total_allocated_size: int
    errors: list[ScanError] = field(default_factory=list)
    cancelled: bool = False
    options: ScanOptions = field(default_factory=ScanOptions)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "duration": self.duration,
            "files": self.files,
            "directories": self.directories,
            "total_size": self.total_size,
            "total_allocated_size": self.total_allocated_size,
            "cancelled": self.cancelled,
            "options": asdict(self.options),
            "errors": [asdict(error) for error in self.errors],
            "root": self.root.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ScanResult:
        version = int(payload.get("schema_version", 1))
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"Snapshot schema {version} is newer than supported schema {SCHEMA_VERSION}"
            )
        raw_options = payload.get("options", {})
        if "exclude" in raw_options:
            raw_options = {**raw_options, "exclude": tuple(raw_options["exclude"])}
        return cls(
            root=Entry.from_dict(payload["root"]),
            duration=float(payload.get("duration", 0.0)),
            files=int(payload.get("files", 0)),
            directories=int(payload.get("directories", 0)),
            total_size=int(payload.get("total_size", 0)),
            total_allocated_size=int(
                payload.get("total_allocated_size", payload.get("total_size", 0))
            ),
            errors=[ScanError(**item) for item in payload.get("errors", [])],
            cancelled=bool(payload.get("cancelled", False)),
            options=ScanOptions(**raw_options).normalized(),
            created_at=str(payload.get("created_at", "")),
            schema_version=version,
        )
