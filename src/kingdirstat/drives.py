"""Cross-platform drive discovery without optional native dependencies."""

from __future__ import annotations

import ctypes
import os
import shutil
import string
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class DriveInfo:
    path: str
    label: str
    total_space: int
    free_space: int
    used_space: int
    drive_type: str
    file_system: str
    is_accessible: bool
    error_message: str | None = None

    @property
    def letter(self) -> str:
        return self.path.rstrip("\\/") or self.path

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DriveSelector:
    def get_available_drives(self) -> list[DriveInfo]:
        return _windows_drives() if os.name == "nt" else _unix_mounts()

    def validate_path(self, path: str) -> tuple[bool, str | None]:
        try:
            candidate = Path(path).expanduser()
            if not candidate.exists():
                return False, f"Path does not exist: {path}"
            if not candidate.is_dir():
                return False, f"Path is not a directory: {path}"
            with os.scandir(candidate) as iterator:
                next(iterator, None)
            return True, None
        except OSError as exc:
            return False, exc.strerror or str(exc)

    def get_drive_statistics(self, drive_info: DriveInfo) -> dict[str, Any]:
        payload = drive_info.to_dict()
        payload["usage_percentage"] = (
            drive_info.used_space / drive_info.total_space * 100 if drive_info.total_space else 0.0
        )
        return payload

    def format_drive_display(self, drive_info: DriveInfo) -> str:
        if not drive_info.is_accessible:
            return f"{drive_info.path} ({drive_info.label}) - unavailable"
        return (
            f"{drive_info.path} ({drive_info.label}) - "
            f"{_format_size(drive_info.used_space)} used / "
            f"{_format_size(drive_info.total_space)} total"
        )


def get_drive_selector() -> DriveSelector:
    return DriveSelector()


def drive_for_path(path: str | os.PathLike[str]) -> DriveInfo:
    """Return the volume that stores *path*, for free-space context on a scan."""

    resolved = Path(path).expanduser().resolve()
    if os.name == "nt":
        return _drive_details(resolved.anchor or os.fspath(resolved))

    best = "/"
    for mount in _unix_mounts():
        candidate = mount.path
        if os.fspath(resolved).startswith(candidate) and len(candidate) > len(best):
            best = candidate
    return _drive_details(best, drive_type="Mount")


def _windows_drives() -> list[DriveInfo]:
    try:
        mask = int(ctypes.windll.kernel32.GetLogicalDrives())
    except AttributeError:
        mask = 0
    paths = [
        f"{letter}:\\" for index, letter in enumerate(string.ascii_uppercase) if mask & (1 << index)
    ]
    return [_drive_details(path) for path in paths]


def _drive_details(path: str, *, drive_type: str | None = None) -> DriveInfo:
    label = "Local Disk"
    file_system = "Unknown"
    detected_type = drive_type or "Unknown"

    if os.name == "nt":
        type_names = {
            0: "Unknown",
            1: "Invalid",
            2: "Removable",
            3: "Fixed",
            4: "Network",
            5: "Optical",
            6: "RAM disk",
        }
        try:
            detected_type = type_names.get(
                int(ctypes.windll.kernel32.GetDriveTypeW(path)), "Unknown"
            )
            volume_name = ctypes.create_unicode_buffer(261)
            filesystem_name = ctypes.create_unicode_buffer(261)
            ok = ctypes.windll.kernel32.GetVolumeInformationW(
                path,
                volume_name,
                len(volume_name),
                None,
                None,
                None,
                filesystem_name,
                len(filesystem_name),
            )
            if ok:
                label = volume_name.value or label
                file_system = filesystem_name.value or file_system
        except (AttributeError, OSError):
            pass

    try:
        total, used, free = shutil.disk_usage(path)
        return DriveInfo(
            path=path,
            label=label,
            total_space=total,
            free_space=free,
            used_space=used,
            drive_type=detected_type,
            file_system=file_system,
            is_accessible=True,
        )
    except OSError as exc:
        return DriveInfo(
            path=path,
            label=label,
            total_space=0,
            free_space=0,
            used_space=0,
            drive_type=detected_type,
            file_system=file_system,
            is_accessible=False,
            error_message=exc.strerror or str(exc),
        )


def _unix_mounts() -> list[DriveInfo]:
    mounts = {"/"}
    try:
        with open("/proc/mounts", encoding="utf-8") as stream:
            for line in stream:
                fields = line.split()
                if len(fields) >= 3 and fields[2] not in {
                    "proc",
                    "sysfs",
                    "tmpfs",
                    "devtmpfs",
                    "devpts",
                    "cgroup",
                    "cgroup2",
                    "overlay",
                }:
                    mounts.add(fields[1].replace("\\040", " "))
    except OSError:
        pass
    return [_drive_details(path, drive_type="Mount") for path in sorted(mounts)]


def _format_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(size) < 1024 or unit == "PiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} PiB"
