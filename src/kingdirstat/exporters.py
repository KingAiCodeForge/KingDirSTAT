"""Versioned, deterministic snapshot and tabular exports."""

from __future__ import annotations

import contextlib
import csv
import json
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
) -> Path:
    output = Path(destination)
    atomic_write_text(output, dumps_snapshot(result, path_mode=path_mode))
    return output


def read_snapshot(source: str | os.PathLike[str]) -> ScanResult:
    with Path(source).open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict) or "root" not in payload:
        raise ValueError("Not a KingDirSTAT snapshot")
    return ScanResult.from_dict(payload)


def write_csv(
    result: ScanResult,
    destination: str | os.PathLike[str],
    *,
    path_mode: PathMode = "absolute",
) -> Path:
    output = Path(destination)
    _write_csv_atomically(
        output,
        lambda stream: write_csv_stream(result, stream, path_mode=path_mode),
    )
    return output


def write_csv_rows(
    destination: str | os.PathLike[str],
    header: Sequence[str],
    rows: Iterable[Sequence[Any]],
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

    _write_csv_atomically(output, emit)
    return output


def _write_csv_atomically(output: Path, emit: Callable[[TextIO], None]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, text=True
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            emit(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, output)
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
                entry.extension,
                entry.is_symlink,
                entry.is_reparse_point,
                entry.scan_complete,
                entry.skip_reason or "",
            ]
        )
        for child in reversed(entry.children):
            child_relative = child.name if relative == "." else f"{relative}/{child.name}"
            stack.append((child, child_relative))


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(temporary_name)
        raise


def _spreadsheet_safe(value: str) -> str:
    """Prevent a path or filename from becoming a spreadsheet formula."""

    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
    return value
