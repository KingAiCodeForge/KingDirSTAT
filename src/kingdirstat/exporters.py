"""Versioned, deterministic snapshot and tabular exports."""

from __future__ import annotations

import contextlib
import csv
import json
import math
import os
import tempfile
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, Literal, TextIO

from .models import Entry, ScanResult

PathMode = Literal["absolute", "relative", "redacted"]


def snapshot_payload(result: ScanResult, *, path_mode: PathMode = "absolute") -> dict[str, Any]:
    if path_mode not in {"absolute", "relative", "redacted"}:
        raise ValueError(f"Unknown path mode: {path_mode}")
    payload = result.to_dict()
    payload["path_mode"] = path_mode
    if path_mode == "absolute":
        return payload

    root_label = "." if path_mode == "relative" else "<ROOT>"
    root_payload = payload["root"]
    root_payload["path"] = root_label
    stack: list[tuple[dict[str, Any], str]] = [(root_payload, root_label)]
    while stack:
        parent, parent_path = stack.pop()
        for child in parent.get("children", []):
            child_path = child["name"] if parent_path == "." else f"{parent_path}/{child['name']}"
            child["path"] = child_path
            stack.append((child, child_path))
    for error in payload["errors"]:
        try:
            relative_error = os.path.relpath(error["path"], result.root.path).replace(os.sep, "/")
        except (TypeError, ValueError):
            relative_error = "."
        error["path"] = f"<ROOT>/{relative_error}" if path_mode == "redacted" else relative_error
    return payload


def dumps_snapshot(result: ScanResult, *, path_mode: PathMode = "absolute") -> str:
    return (
        json.dumps(
            snapshot_payload(result, path_mode=path_mode),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def write_snapshot(
    result: ScanResult,
    destination: str | os.PathLike[str],
    *,
    path_mode: PathMode = "absolute",
    overwrite: bool = True,
) -> Path:
    output = Path(destination)
    atomic_write_text(output, dumps_snapshot(result, path_mode=path_mode), overwrite=overwrite)
    return output


def read_snapshot(source: str | os.PathLike[str]) -> ScanResult:
    try:
        with Path(source).open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict) or "root" not in payload:
            raise ValueError("Not a KingDirSTAT snapshot")
        result = ScanResult.from_dict(payload)
        _validate_snapshot_entries(result)
        return result
    except (AttributeError, KeyError, TypeError, OverflowError, RecursionError) as exc:
        raise ValueError("Invalid KingDirSTAT snapshot structure") from exc


def _validate_snapshot_entries(result: ScanResult) -> None:
    """Validate values used by CLI analysis and the GUI before displaying a file."""

    value: Any
    for entry in result.root.walk():
        for value in (entry.name, entry.path, entry.extension):
            if not isinstance(value, str):
                raise ValueError("Invalid snapshot: entry text fields must be strings")
        if entry.skip_reason is not None and not isinstance(entry.skip_reason, str):
            raise ValueError("Invalid snapshot: skip_reason must be a string or null")
        for value in (
            entry.is_dir,
            entry.is_symlink,
            entry.is_reparse_point,
            entry.is_hardlink_duplicate,
            entry.scan_complete,
        ):
            if not isinstance(value, bool):
                raise ValueError("Invalid snapshot: entry flags must be booleans")
        for value in (
            entry.size,
            entry.allocated_size,
            entry.depth,
            entry.file_attributes,
            entry.file_count,
            entry.directory_count,
        ):
            if type(value) is not int or value < 0:
                raise ValueError(
                    "Invalid snapshot: entry counts and sizes must be nonnegative integers"
                )
        for value in (entry.modified_time, entry.created_time):
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                raise ValueError("Invalid snapshot: timestamps must be finite numbers or null")


def write_csv(
    result: ScanResult,
    destination: str | os.PathLike[str],
    *,
    path_mode: PathMode = "absolute",
    overwrite: bool = True,
) -> Path:
    output = Path(destination)
    _write_csv_atomically(
        output,
        lambda stream: write_csv_stream(result, stream, path_mode=path_mode),
        overwrite=overwrite,
    )
    return output


def write_csv_rows(
    destination: str | os.PathLike[str],
    header: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    overwrite: bool = True,
) -> Path:
    """Atomically write formula-safe tabular rows to a CSV file."""

    output = Path(destination)

    def emit(stream: TextIO) -> None:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow([_spreadsheet_safe(value) for value in header])
        for row in rows:
            writer.writerow(
                [_spreadsheet_safe(value) if isinstance(value, str) else value for value in row]
            )

    _write_csv_atomically(output, emit, overwrite=overwrite)
    return output


def _write_csv_atomically(
    output: Path, emit: Callable[[TextIO], None], *, overwrite: bool = True
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, text=True
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            emit(stream)
            stream.flush()
            os.fsync(stream.fileno())
        _publish_output(temporary_name, output, overwrite=overwrite)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(temporary_name)
        raise


def write_csv_stream(
    result: ScanResult,
    stream: TextIO,
    *,
    path_mode: PathMode = "absolute",
) -> None:
    if path_mode not in {"absolute", "relative", "redacted"}:
        raise ValueError(f"Unknown path mode: {path_mode}")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(
        [
            "path",
            "name",
            "type",
            "logical_size",
            "allocated_size",
            "files",
            "directories",
            "modified_time",
            "extension",
            "symlink",
            "reparse_point",
            "scan_complete",
            "skip_reason",
        ]
    )
    stack: list[tuple[Entry, str]] = [(result.root, ".")]
    while stack:
        entry, relative = stack.pop()
        if path_mode == "absolute":
            display_path = entry.path
        elif path_mode == "redacted":
            display_path = "<ROOT>" if relative == "." else f"<ROOT>/{relative}"
        else:
            display_path = relative
        writer.writerow(
            [
                _spreadsheet_safe(display_path),
                _spreadsheet_safe(entry.name),
                "directory" if entry.is_dir else "file",
                entry.size,
                entry.allocated_size,
                entry.file_count,
                entry.directory_count,
                entry.modified_time,
                _spreadsheet_safe(entry.extension),
                entry.is_symlink,
                entry.is_reparse_point,
                entry.scan_complete,
                _spreadsheet_safe(entry.skip_reason or ""),
            ]
        )
        for child in reversed(entry.children):
            child_relative = child.name if relative == "." else f"{relative}/{child.name}"
            stack.append((child, child_relative))


def atomic_write_text(path: Path, content: str, *, overwrite: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _publish_output(temporary_name, path, overwrite=overwrite)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(temporary_name)
        raise


def _publish_output(temporary_name: str, output: Path, *, overwrite: bool) -> None:
    """Publish complete output, optionally refusing existing files without a race."""

    if overwrite:
        os.replace(temporary_name, output)
    elif os.name == "nt":
        # Windows rename refuses an existing destination, including link aliases.
        os.rename(temporary_name, output)
    else:
        # Same-directory hard linking is atomic and never replaces a destination.
        # If unsupported by the filesystem, fail safely instead of falling back
        # to a racy exists()/replace() sequence.
        os.link(temporary_name, output)
        with contextlib.suppress(OSError):
            os.unlink(temporary_name)


def _spreadsheet_safe(value: str) -> str:
    """Prevent a path or filename from becoming a spreadsheet formula."""

    if value.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return f"'{value}"
    return value
