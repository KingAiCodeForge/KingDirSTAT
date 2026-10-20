"""Data preservation and error regressions for the portable 2.1.1 release."""

from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
from typing import Any

import pytest

from kingdirstat.analysis import empty_directories, find_duplicates
from kingdirstat.cli import EXIT_FATAL, EXIT_OK, main
from kingdirstat.exporters import read_snapshot, write_csv_rows, write_csv_stream
from kingdirstat.scanner import DirectoryScanner


@pytest.mark.parametrize("command", ["scan", "duplicates", "export", "clean"])
def test_cli_existing_output_is_preserved_before_scanning(
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "original.ps1"
    output.write_bytes(b"user data\x00\xff")
    options = {
        "scan": ["--save", str(output)],
        "duplicates": ["--csv", str(output)],
        "export": [str(output)],
        "clean": ["--emit-script", str(output)],
    }

    def unexpected_scan(*_args: Any) -> None:
        pytest.fail("scanning began before output validation")

    monkeypatch.setattr("kingdirstat.cli._scan", unexpected_scan)
    assert main([command, "never-scanned", *options[command]]) == EXIT_FATAL
    assert "--overwrite" in capsys.readouterr().err
    assert output.read_bytes() == b"user data\x00\xff"


def test_cli_explicit_overwrite_and_source_alias_protection(
    sample_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = sample_tree / "a.txt"
    original = source.read_bytes()
    args = ["export", str(sample_tree), str(source), "--no-progress"]
    assert main(args) == EXIT_FATAL
    assert source.read_bytes() == original
    assert main([*args, "--overwrite"]) == EXIT_OK
    assert json.loads(source.read_text(encoding="utf-8"))["root"]["name"] == "root"
    capsys.readouterr()


def test_output_created_during_csv_generation_is_never_replaced(tmp_path: Path) -> None:
    destination = tmp_path / "output.csv"

    def rows():
        destination.write_bytes(b"concurrent writer")
        yield ["new report"]

    with pytest.raises(FileExistsError):
        write_csv_rows(destination, ["header"], rows(), overwrite=False)
    assert destination.read_bytes() == b"concurrent writer"
    assert not list(tmp_path.glob("*.tmp"))


def test_cli_preserves_hardlink_output_alias(sample_tree: Path, tmp_path: Path) -> None:
    original = sample_tree / "a.txt"
    alias = tmp_path / "alias.json"
    try:
        os.link(original, alias)
    except OSError:
        pytest.skip("filesystem does not support creating hard links")
    content = original.read_bytes()
    assert main(["export", str(sample_tree), str(alias), "--no-progress"]) == EXIT_FATAL
    assert original.read_bytes() == alias.read_bytes() == content


@pytest.mark.parametrize("algorithm", ["shake_128", "shake_256", "not-a-hash"])
def test_hash_validation_precedes_scan(
    algorithm: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("kingdirstat.cli._scan", lambda *_args: pytest.fail("unexpected scan"))
    assert main(["duplicates", "never-scanned", "--algorithm", algorithm]) == EXIT_FATAL
    assert "error:" in capsys.readouterr().err


def test_library_rejects_variable_length_hash_before_opening_files(sample_tree: Path) -> None:
    with pytest.raises(ValueError, match="output length"):
        find_duplicates(DirectoryScanner().scan(sample_tree).root, algorithm="shake_128")


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r", "\n"])
def test_all_snapshot_csv_text_columns_are_formula_safe(sample_tree: Path, prefix: str) -> None:
    result = DirectoryScanner().scan(sample_tree)
    result.root.extension = f"{prefix}formula"
    result.root.skip_reason = f"{prefix}formula"
    stream = io.StringIO(newline="")
    write_csv_stream(result, stream)
    row = next(csv.DictReader(io.StringIO(stream.getvalue(), newline="")))
    assert row["extension"] == row["skip_reason"] == f"'{prefix}formula"


@pytest.mark.parametrize("root", [None, [], "wrong", {}, {"name": "x", "path": "x"}])
def test_malformed_snapshot_structure_is_a_clear_cli_error(
    root: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = tmp_path / "invalid.json"
    snapshot.write_text(json.dumps({"root": root}), encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot"):
        read_snapshot(snapshot)
    assert main(["compare", str(snapshot), str(snapshot)]) == EXIT_FATAL
    assert "Traceback" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "field,value", [("name", 3), ("size", "1"), ("is_dir", "yes"), ("modified_time", float("nan"))]
)
def test_snapshot_rejects_invalid_entry_values(
    field: str, value: Any, tmp_path: Path, sample_tree: Path
) -> None:
    payload = DirectoryScanner().scan(sample_tree).to_dict()
    payload["root"][field] = value
    snapshot = tmp_path / "invalid.json"
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid snapshot"):
        read_snapshot(snapshot)


def test_stat_failure_does_not_report_an_unreadable_directory_as_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    child = root / "unreadable"
    child.mkdir(parents=True)
    (child / "still-here.txt").write_text("user data", encoding="utf-8")
    real_scandir = os.scandir

    class UnreadableEntry:
        name = "still-here.txt"
        path = str(child / name)

        def stat(self, **_kwargs: Any) -> None:
            raise PermissionError("synthetic stat denied")

    class UnreadableIterator:
        def __iter__(self):
            return iter([UnreadableEntry()])

        def __enter__(self):
            return self

        def __exit__(self, *_args: Any) -> None:
            pass

    def scandir(path: str):
        return UnreadableIterator() if os.fspath(path) == str(child) else real_scandir(path)

    monkeypatch.setattr("kingdirstat.scanner.os.scandir", scandir)
    result = DirectoryScanner().scan(root)
    assert len(result.errors) == 1
    assert not result.root.children[0].scan_complete
    assert empty_directories(result.root) == []
