"""Accurate, cancellable filesystem traversal for KingDirSTAT."""

from __future__ import annotations

import ctypes
import fnmatch
import os
import stat
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

from .models import (
    FILE_ATTRIBUTE_HIDDEN,
    FILE_ATTRIBUTE_READONLY,
    FILE_ATTRIBUTE_REPARSE_POINT,
    Entry,
    ScanError,
    ScanOptions,
    ScanProgress,
    ScanResult,
)

ProgressCallback = Callable[[ScanProgress], None]
_REPARSE_POINT = 0x400
_DIRECTORY_FUTURES_PER_WORKER = 4


class ScanCancelled(RuntimeError):
    """Raised by callers that choose to treat a partial scan as an error."""


@dataclass(slots=True)
class _DirectoryBatch:
    node: Entry
    children: list[Entry] = field(default_factory=list)
    descend: list[Entry] = field(default_factory=list)
    errors: list[ScanError] = field(default_factory=list)


class DirectoryScanner:
    """Scan a directory tree once and produce a shared, immutable-in-practice result.

    Directory work is distributed across a small worker pool. Sizes are aggregated only
    after traversal finishes, which avoids the stale-parent bug common in breadth-first
    scanners. No files are modified and no directories are excluded unless requested.
    """

    def __init__(
        self,
        options: ScanOptions | None = None,
        *,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.options = (options or ScanOptions()).normalized()
        self._stop_event = stop_event or threading.Event()
        self._root_path = ""
        self._hardlink_lock = threading.Lock()
        self._seen_hardlinks: set[tuple[int, int]] = set()

    @property
    def cancelled(self) -> bool:
        return self._stop_event.is_set()

    def cancel(self) -> None:
        """Request cooperative cancellation. A partial result is still returned."""

        self._stop_event.set()

    def scan(
        self,
        path: str | os.PathLike[str],
        progress: ProgressCallback | None = None,
    ) -> ScanResult:
        """Scan *path* synchronously; use a thread when calling from a GUI."""

        self._stop_event.clear()
        self._seen_hardlinks.clear()
        root_path = Path(path).expanduser().resolve(strict=True)
        if not root_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {root_path}")

        self._root_path = os.fspath(root_path)
        root_stat = root_path.stat()
        root = self._entry_from_stat(
            name=root_path.name or root_path.anchor or os.fspath(root_path),
            path=os.fspath(root_path),
            stat_info=root_stat,
            is_dir=True,
            depth=0,
        )
        started = time.monotonic()
        errors: list[ScanError] = []
        discovered_files = 0
        discovered_directories = 1
        discovered_size = 0
        last_progress = 0.0

        if self.options.max_depth == 0:
            root.scan_complete = False
            root.skip_reason = "depth limit"
        else:
            visited_directories: set[tuple[int, int]] = set()
            root_identity = self._identity(root)
            if root_identity is not None:
                visited_directories.add(root_identity)

            with ThreadPoolExecutor(
                max_workers=self.options.workers,
                thread_name_prefix="kingdirstat-scan",
            ) as executor:
                # A small multiple of the worker count absorbs uneven directory
                # latency while preventing a wide volume from allocating one
                # Future (and executor work item) for every directory at once.
                future_limit = self.options.workers * _DIRECTORY_FUTURES_PER_WORKER
                pending: dict[Future[_DirectoryBatch], Entry] = {
                    executor.submit(self._scan_directory, root, root_stat.st_dev): root
                }
                ready: deque[Entry] = deque()

                def mark_cancelled(entries: list[Entry] | deque[Entry]) -> None:
                    while entries:
                        child = entries.pop()
                        child.scan_complete = False
                        child.skip_reason = "cancelled"

                def submit_ready() -> None:
                    while ready and len(pending) < future_limit and not self.cancelled:
                        child = ready.popleft()
                        identity = self._identity(child)
                        if identity is not None and identity in visited_directories:
                            child.scan_complete = False
                            child.skip_reason = "filesystem cycle"
                            continue
                        if identity is not None:
                            visited_directories.add(identity)
                        pending[executor.submit(self._scan_directory, child, root_stat.st_dev)] = (
                            child
                        )

                    if self.cancelled:
                        mark_cancelled(ready)

                try:
                    while pending or ready:
                        submit_ready()
                        if not pending:
                            break
                        completed, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
                        for future in completed:
                            node = pending.pop(future)
                            try:
                                batch = future.result()
                            except OSError as exc:
                                node.scan_complete = False
                                batch = _DirectoryBatch(
                                    node=node,
                                    errors=[self._error(node.path, "scan", exc)],
                                )

                            node.children = batch.children
                            errors.extend(batch.errors)
                            discovered_files += sum(not child.is_dir for child in batch.children)
                            discovered_directories += sum(child.is_dir for child in batch.children)
                            discovered_size += sum(
                                child.size for child in batch.children if not child.is_dir
                            )

                            if self.cancelled:
                                # These directories are visible in the partial tree but
                                # were never submitted, so they must not look verified
                                # empty to cleanup analysis.
                                mark_cancelled(batch.descend)
                            else:
                                ready.extend(batch.descend)

                            now = time.monotonic()
                            if progress is not None and now - last_progress >= 0.05:
                                self._notify_progress(
                                    progress,
                                    current_path=node.path,
                                    files=discovered_files,
                                    directories=discovered_directories,
                                    size=discovered_size,
                                    errors=len(errors),
                                    started=started,
                                )
                                last_progress = now

                        if self.cancelled:
                            mark_cancelled(ready)
                            for future, node in list(pending.items()):
                                if future.cancel():
                                    node.scan_complete = False
                                    node.skip_reason = "cancelled"
                                    pending.pop(future)
                except KeyboardInterrupt:
                    self.cancel()
                    mark_cancelled(ready)
                    for future, node in pending.items():
                        future.cancel()
                        node.scan_complete = False
                        node.skip_reason = "cancelled"
                    raise

        self._aggregate(root)
        duration = time.monotonic() - started
        result = ScanResult(
            root=root,
            duration=duration,
            files=root.file_count,
            directories=1 + root.directory_count,
            total_size=root.size,
            total_allocated_size=root.allocated_size,
            errors=errors,
            cancelled=self.cancelled,
            options=self.options,
        )
        if progress is not None:
            self._notify_progress(
                progress,
                current_path=root.path,
                files=result.files,
                directories=result.directories,
                size=result.total_size,
                errors=len(errors),
                started=started,
            )
        return result

    def _scan_directory(self, node: Entry, root_device: int) -> _DirectoryBatch:
        batch = _DirectoryBatch(node=node)
        if self.cancelled:
            node.scan_complete = False
            node.skip_reason = "cancelled"
            return batch

        try:
            iterator = os.scandir(node.path)
        except OSError as exc:
            node.scan_complete = False
            batch.errors.append(self._error(node.path, "list directory", exc))
            return batch

        with iterator:
            for directory_entry in iterator:
                # Read the event directly: another thread can set it mid-loop, and
                # the `cancelled` property would be treated as already-false here.
                if self._stop_event.is_set():
                    node.scan_complete = False
                    node.skip_reason = "cancelled"
                    break
                full_path = directory_entry.path
                relative_path = os.path.relpath(full_path, self._root_path).replace(os.sep, "/")
                if self._is_excluded(directory_entry.name, relative_path):
                    continue

                try:
                    link_stat = directory_entry.stat(follow_symlinks=False)
                    is_symlink = directory_entry.is_symlink()
                    is_reparse = bool(getattr(link_stat, "st_file_attributes", 0) & _REPARSE_POINT)
                    if self.options.follow_symlinks:
                        stat_info = directory_entry.stat(follow_symlinks=True)
                        is_directory = directory_entry.is_dir(follow_symlinks=True)
                    else:
                        stat_info = link_stat
                        is_directory = directory_entry.is_dir(follow_symlinks=False)
                        if is_symlink or is_reparse:
                            # Kept as try/except rather than contextlib.suppress: this runs
                            # for every junction and symlink in the tree, and suppress()
                            # measured ~10x slower here.
                            try:  # noqa: SIM105
                                is_directory = directory_entry.is_dir(follow_symlinks=True)
                            except OSError:
                                pass

                    child = self._entry_from_stat(
                        name=directory_entry.name,
                        path=full_path,
                        stat_info=stat_info,
                        is_dir=is_directory,
                        depth=node.depth + 1,
                        is_symlink=is_symlink,
                        is_reparse_point=is_reparse,
                    )

                    if is_directory:
                        can_descend = True
                        if (is_symlink or is_reparse) and not self.options.follow_symlinks:
                            can_descend = False
                            child.scan_complete = False
                            child.skip_reason = "link not followed"
                        elif self.options.same_file_system and child.device != root_device:
                            can_descend = False
                            child.scan_complete = False
                            child.skip_reason = "different filesystem"
                        elif (
                            self.options.max_depth is not None
                            and child.depth >= self.options.max_depth
                        ):
                            can_descend = False
                            child.scan_complete = False
                            child.skip_reason = "depth limit"
                        if can_descend:
                            batch.descend.append(child)
                    else:
                        self._apply_file_sizes(child, stat_info)

                    batch.children.append(child)
                except OSError as exc:
                    node.scan_complete = False
                    batch.errors.append(self._error(full_path, "stat", exc))

        return batch

    def _entry_from_stat(
        self,
        *,
        name: str,
        path: str,
        stat_info: os.stat_result,
        is_dir: bool,
        depth: int,
        is_symlink: bool = False,
        is_reparse_point: bool = False,
    ) -> Entry:
        created = getattr(stat_info, "st_birthtime", None)
        if created is None and os.name == "nt":
            created = stat_info.st_ctime
        attributes = int(getattr(stat_info, "st_file_attributes", 0))
        if not attributes and os.name != "nt":
            attributes = self._posix_attributes(name, stat_info)
        return Entry(
            name=name,
            path=path,
            is_dir=is_dir,
            depth=depth,
            modified_time=stat_info.st_mtime,
            created_time=created,
            extension="" if is_dir else Path(name).suffix.lower(),
            is_symlink=is_symlink,
            is_reparse_point=is_reparse_point,
            file_attributes=attributes,
            device=getattr(stat_info, "st_dev", None),
            inode=getattr(stat_info, "st_ino", None),
        )

    def _apply_file_sizes(self, entry: Entry, stat_info: os.stat_result) -> None:
        size = max(0, int(stat_info.st_size))
        allocated = size
        if self.options.measure_allocated:
            allocated = self._allocated_size(entry.path, stat_info)

        if self.options.dedupe_hardlinks and getattr(stat_info, "st_nlink", 1) > 1:
            identity = self._identity(entry)
            if identity is not None:
                with self._hardlink_lock:
                    if identity in self._seen_hardlinks:
                        entry.is_hardlink_duplicate = True
                        size = 0
                        allocated = 0
                    else:
                        self._seen_hardlinks.add(identity)

        entry.size = size
        entry.allocated_size = allocated

    def _aggregate(self, root: Entry) -> None:
        # Post-order directories without first materialising a second list of
        # every file. Whole-drive scans can contain millions of entries, so a
        # full `list(root.walk())` creates avoidable peak-memory pressure.
        stack: list[tuple[Entry, bool]] = [(root, False)]
        while stack:
            node, visited = stack.pop()
            if not visited:
                stack.append((node, True))
                stack.extend((child, False) for child in reversed(node.children) if child.is_dir)
                continue
            node.size = sum(child.size for child in node.children)
            node.allocated_size = sum(child.allocated_size for child in node.children)
            node.file_count = sum(
                child.file_count if child.is_dir else 1 for child in node.children
            )
            node.directory_count = sum(
                1 + child.directory_count for child in node.children if child.is_dir
            )
            node.children.sort(key=lambda item: (-item.size, not item.is_dir, item.name.casefold()))

    def _is_excluded(self, name: str, relative_path: str) -> bool:
        for pattern in self.options.exclude:
            normalized = pattern.replace("\\", "/")
            if fnmatch.fnmatch(name, normalized) or fnmatch.fnmatch(relative_path, normalized):
                return True
            if normalized.endswith("/") and relative_path.startswith(normalized.rstrip("/")):
                return True
        return False

    @staticmethod
    def _posix_attributes(name: str, stat_info: os.stat_result) -> int:
        """Map POSIX mode bits onto the Windows attribute flags we display."""

        flags = 0
        if not stat_info.st_mode & stat.S_IWUSR:
            flags |= FILE_ATTRIBUTE_READONLY
        if name.startswith("."):
            flags |= FILE_ATTRIBUTE_HIDDEN
        if stat.S_ISLNK(stat_info.st_mode):
            flags |= FILE_ATTRIBUTE_REPARSE_POINT
        return flags

    @staticmethod
    def _identity(entry: Entry) -> tuple[int, int] | None:
        device = entry.device
        inode = entry.inode
        if device is None or inode is None or inode == 0:
            return None
        return device, inode

    @staticmethod
    def _error(path: str, operation: str, exc: OSError) -> ScanError:
        message = exc.strerror or str(exc)
        return ScanError(path=path, operation=operation, message=message)

    @staticmethod
    def _allocated_size(path: str, stat_info: os.stat_result) -> int:
        blocks = getattr(stat_info, "st_blocks", None)
        if blocks is not None:
            return max(0, int(blocks) * 512)
        if os.name != "nt":
            return max(0, int(stat_info.st_size))

        try:
            get_size = ctypes.windll.kernel32.GetCompressedFileSizeW
            get_size.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
            get_size.restype = ctypes.c_ulong
            high = ctypes.c_ulong(0)
            ctypes.set_last_error(0)
            low = get_size(path, ctypes.byref(high))
            if low == 0xFFFFFFFF and ctypes.get_last_error() != 0:
                return max(0, int(stat_info.st_size))
            return (int(high.value) << 32) | int(low)
        except (AttributeError, OSError):
            return max(0, int(stat_info.st_size))

    def _notify_progress(
        self,
        callback: ProgressCallback,
        *,
        current_path: str,
        files: int,
        directories: int,
        size: int,
        errors: int,
        started: float,
    ) -> None:
        elapsed = max(time.monotonic() - started, 1e-9)
        update = ScanProgress(
            current_path=current_path,
            files_scanned=files,
            directories_scanned=directories,
            total_size=size,
            errors_encountered=errors,
            scan_rate=files / elapsed,
            elapsed=elapsed,
            cancelled=self.cancelled,
        )
        try:
            callback(update)
        except Exception:
            # A display callback must never invalidate an otherwise good scan.
            return
