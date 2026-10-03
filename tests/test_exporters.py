"""Snapshot and CSV round trips, path modes, and the formula-injection guard."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from conftest import TOTAL_SIZE
from kingdirstat.exporters import (
    dumps_snapshot,
    read_snapshot,
    write_csv,
    write_csv_rows,
    write_csv_stream,
    write_snapshot,
)
from kingdirstat.models import ScanResult
from kingdirstat.scanner import DirectoryScanner


def test_snapshot_round_trip_preserves_the_tree(sample_tree: Path, tmp_path: Path) -> None:
    result = DirectoryScanner().scan(sample_tree)
    destination = tmp_path / "snap.json"
    write_snapshot(result, destination)

    restored = read_snapshot(destination)
    assert restored.total_size == result.total_size
    assert restored.files == result.files
    assert restored.directories == result.directories
    assert len(list(restored.root.walk())) == len(list(result.root.walk()))
    assert [child.name for child in restored.root.children] == [
        child.name for child in result.root.children
    ]


def test_snapshot_preserves_attributes(sample_tree: Path, tmp_path: Path) -> None:
    result = DirectoryScanner().scan(sample_tree)
    destination = tmp_path / "snap.json"
    write_snapshot(result, destination)
    restored = read_snapshot(destination)

    original = {entry.path: entry.file_attributes for entry in result.root.walk()}
    for entry in restored.root.walk():
        assert entry.file_attributes == original[entry.path]


def test_snapshot_is_deterministic(sample_tree: Path) -> None:
    result = DirectoryScanner().scan(sample_tree)
    assert dumps_snapshot(result) == dumps_snapshot(result)


def test_redacted_mode_removes_absolute_paths(sample_tree: Path) -> None:
    result = DirectoryScanner().scan(sample_tree)
    payload = json.loads(dumps_snapshot(result, path_mode="redacted"))
    assert payload["path_mode"] == "redacted"
    assert payload["root"]["path"] == "<ROOT>"
    text = json.dumps(payload)
    assert str(sample_tree) not in text


def test_relative_mode_paths(sample_tree: Path) -> None:
    result = DirectoryScanner().scan(sample_tree)
    payload = json.loads(dumps_snapshot(result, path_mode="relative"))
    assert payload["root"]["path"] == "."
    names = {child["path"] for child in payload["root"]["children"]}
    assert "a.txt" in names
    assert str(sample_tree) not in json.dumps(payload)


def test_unknown_path_mode_rejected(sample_tree: Path) -> None:
    result = DirectoryScanner().scan(sample_tree)
    with pytest.raises(ValueError):
        dumps_snapshot(result, path_mode="sideways")  # type: ignore[arg-type]


def test_csv_has_one_row_per_entry(sample_tree: Path, tmp_path: Path) -> None:
    result = DirectoryScanner().scan(sample_tree)
    destination = tmp_path / "out.csv"
    write_csv(result, destination)

    with destination.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len(list(result.root.walk()))
    assert sum(int(row["logical_size"]) for row in rows if row["type"] == "file") == TOTAL_SIZE


def test_csv_escapes_leading_formula_characters(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "=cmd.txt").write_bytes(b"x" * 10)
    result = DirectoryScanner().scan(root)

    buffer = io.StringIO()
    write_csv_stream(result, buffer, path_mode="relative")
    text = buffer.getvalue()
    assert "'=cmd.txt" in text
    assert "\n=cmd.txt" not in text


def test_csv_rows_escape_formula_leading_paths(tmp_path: Path) -> None:
    destination = tmp_path / "duplicates.csv"
    write_csv_rows(
        destination,
        ["group_index", "path"],
        [[1, '=HYPERLINK("https://example.invalid")'], [2, "+SUM(1,1)"]],
    )

    with destination.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["path"].startswith("'=")
    assert rows[1]["path"].startswith("'+")


def test_reading_a_non_snapshot_fails_clearly(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"hello": "world"}', encoding="utf-8")
    with pytest.raises(ValueError):
        read_snapshot(bad)


def test_future_schema_is_rejected(sample_tree: Path, tmp_path: Path) -> None:
    result = DirectoryScanner().scan(sample_tree)
    payload = result.to_dict()
    payload["schema_version"] = 999
    with pytest.raises(ValueError):
        ScanResult.from_dict(payload)


def test_writes_leave_no_temporary_files_behind(sample_tree: Path, tmp_path: Path) -> None:
    result = DirectoryScanner().scan(sample_tree)
    destination = tmp_path / "out" / "snap.json"
    write_snapshot(result, destination)
    write_csv(result, tmp_path / "out" / "snap.csv")

    leftovers = [path.name for path in (tmp_path / "out").iterdir() if path.suffix == ".tmp"]
    assert leftovers == []
    assert sorted(path.name for path in (tmp_path / "out").iterdir()) == ["snap.csv", "snap.json"]


def test_failed_write_does_not_clobber_the_existing_file(
    sample_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the replace step fails, the previous snapshot must survive intact."""

    result = DirectoryScanner().scan(sample_tree)
    destination = tmp_path / "snap.json"
    write_snapshot(result, destination)
    original = destination.read_text(encoding="utf-8")

    import kingdirstat.exporters as exporters

    def exploding_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("replace refused")

    monkeypatch.setattr(exporters.os, "replace", exploding_replace)
    with pytest.raises(OSError):
        write_snapshot(result, destination)

    assert destination.read_text(encoding="utf-8") == original
    assert json.loads(original)["root"]["name"] == "root"
    leftovers = [path.name for path in tmp_path.iterdir() if path.suffix == ".tmp"]
    assert leftovers == [], f"temporary file left behind: {leftovers}"


def test_failed_csv_rows_write_preserves_existing_file_and_removes_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "duplicates.csv"
    destination.write_text("existing\n", encoding="utf-8")

    import kingdirstat.exporters as exporters

    def exploding_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("replace refused")

    monkeypatch.setattr(exporters.os, "replace", exploding_replace)
    with pytest.raises(OSError, match="replace refused"):
        write_csv_rows(destination, ["path"], [["=malicious"]])

    assert destination.read_text(encoding="utf-8") == "existing\n"
    leftovers = [path.name for path in tmp_path.iterdir() if path.suffix == ".tmp"]
    assert leftovers == [], f"temporary file left behind: {leftovers}"
