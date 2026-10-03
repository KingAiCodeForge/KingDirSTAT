"""CLI behaviour: parsing, output shape, exit codes, and the new commands."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import pytest

from conftest import TOTAL_SIZE
from kingdirstat.cli import (
    EXIT_FATAL,
    EXIT_OK,
    EXIT_USAGE,
    _write_cleanup_script,
    format_size,
    main,
    parse_size,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0", 0),
        ("512", 512),
        ("1kb", 1024),
        ("1KiB", 1024),
        ("2.5MiB", int(2.5 * 1024**2)),
        ("1gb", 1024**3),
        ("  4 TiB  ", 4 * 1024**4),
    ],
)
def test_parse_size(text: str, expected: int) -> None:
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "10 furlongs", "1.2.3MiB"])
def test_parse_size_rejects_nonsense(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_size(text)


def test_format_size_units() -> None:
    assert format_size(0) == "0 B"
    assert format_size(999) == "999 B"
    assert format_size(1024) == "1.00 KiB"
    assert format_size(1024**3) == "1.00 GiB"


def test_scan_json_matches_the_tree(sample_tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["scan", str(sample_tree), "--json", "--no-progress"])
    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["logical_size"] == TOTAL_SIZE
    assert payload["files"] == 6
    assert payload["empty_directories"] == 1
    assert payload["largest_files"][0]["name"] == "c.txt"


def test_scan_text_output(sample_tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", str(sample_tree), "--no-progress"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Root:" in out
    assert "Largest files" in out
    assert "c.txt" in out


def test_bare_path_is_treated_as_a_scan(
    sample_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(sample_tree), "--no-progress"]) == EXIT_OK
    assert "Root:" in capsys.readouterr().out


def test_tree_json_has_shares_and_attributes(
    sample_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["tree", str(sample_tree), "--json", "--no-progress"]) == EXIT_OK
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["depth"] == 0
    assert rows[0]["share_of_total"] == 100.0
    assert {"attributes", "share_of_parent", "files", "directories"} <= set(rows[0])
    assert all(row["depth"] <= 2 for row in rows)  # default --depth 2


def test_tree_depth_and_dirs_only(sample_tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(["tree", str(sample_tree), "--depth", "1", "--dirs-only", "--no-progress"]) == EXIT_OK
    )
    out = capsys.readouterr().out
    assert "sub/" in out
    assert "empty/" in out
    assert "a.txt" not in out


def test_tree_indents_by_depth(sample_tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tree", str(sample_tree), "--depth", "2", "--no-progress"]) == EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    c_txt = next(line for line in lines if line.rstrip().endswith("c.txt"))
    sub = next(line for line in lines if line.rstrip().endswith("sub/"))
    assert c_txt.index("c.txt") > sub.index("sub/")


def test_space_reports_volume_context(
    sample_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["space", str(sample_tree), "--json", "--no-progress"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["scanned_size"] == TOTAL_SIZE
    assert payload["total_space"] > 0
    assert payload["free_space"] >= 0
    assert payload["unmeasured_space"] >= 0
    assert 0 <= payload["free_percent"] <= 100


def test_clean_reports_the_cache_and_deletes_nothing(
    sample_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = sample_tree / "sub" / "__pycache__"
    assert cache.exists()

    assert main(["clean", str(sample_tree), "--json", "--no-progress"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["reclaimable_size"] == 50
    assert len(payload["candidates"]) == 1
    assert payload["candidates"][0]["rule"] == "python-bytecode"

    assert cache.exists(), "clean must never delete anything"
    assert (cache / "d.pyc").exists()


def test_clean_emits_a_dry_run_powershell_script(sample_tree: Path, tmp_path: Path) -> None:
    script = tmp_path / "review.ps1"
    assert (
        main(["clean", str(sample_tree), "--emit-script", str(script), "--no-progress"]) == EXIT_OK
    )
    text = script.read_text(encoding="utf-8")
    assert "-WhatIf" in text
    assert "__pycache__" in text
    # Every destructive line must be guarded.
    for line in text.splitlines():
        if line.startswith("Remove-Item"):
            assert line.rstrip().endswith("-WhatIf")
    assert (sample_tree / "sub" / "__pycache__").exists()


def test_clean_emits_a_dry_run_shell_script(sample_tree: Path, tmp_path: Path) -> None:
    script = tmp_path / "review.sh"
    assert (
        main(["clean", str(sample_tree), "--emit-script", str(script), "--no-progress"]) == EXIT_OK
    )
    text = script.read_text(encoding="utf-8")
    assert "DRY_RUN=1" in text
    assert "rm -rf" in text
    assert "run '" in text


@pytest.mark.parametrize(
    ("suffix", "payload"),
    [
        (".ps1", "Write-Output 'KINGDIRSTAT_INJECTION_MARKER'"),
        (".sh", "touch /tmp/KINGDIRSTAT_INJECTION_MARKER"),
    ],
)
@pytest.mark.parametrize(
    "separator",
    ["\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"],
)
def test_cleanup_script_root_comment_cannot_inject_a_command(
    suffix: str,
    payload: str,
    separator: str,
    tmp_path: Path,
) -> None:
    script = tmp_path / f"review{suffix}"

    _write_cleanup_script(script.as_posix(), (), f"trusted-root{separator}{payload}")

    text = script.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines.count(f"# Root: trusted-root {payload}") == 1
    assert payload not in lines
    assert all(line.startswith("# Root: ") for line in lines if payload in line)


def test_largest_and_types_and_empty_and_stale(
    sample_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["largest", str(sample_tree), "--json", "--no-progress"]) == EXIT_OK
    entries = json.loads(capsys.readouterr().out)
    assert entries[0]["name"] == "c.txt"

    assert main(["types", str(sample_tree), "--json", "--no-progress"]) == EXIT_OK
    types = json.loads(capsys.readouterr().out)
    assert {item["extension"] for item in types} == {".txt", ".log", ".bin", ".pyc"}

    assert main(["empty", str(sample_tree), "--json", "--no-progress"]) == EXIT_OK
    assert len(json.loads(capsys.readouterr().out)) == 1

    assert main(["stale", str(sample_tree), "--days", "3650", "--json", "--no-progress"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == []


def test_search_json_uses_entry_records_and_default_matching(
    sample_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["search", str(sample_tree), "TXT", "--json", "--no-progress"]) == EXIT_OK
    entries = json.loads(capsys.readouterr().out)
    assert [entry["name"] for entry in entries] == ["c.txt", "a.txt"]
    assert set(entries[0]) == {
        "path",
        "name",
        "type",
        "logical_size",
        "allocated_size",
        "modified_time",
    }


def test_search_flags_filter_and_include_directories(
    sample_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = [
        "search",
        str(sample_tree),
        r"(?:sub|c\.txt)$",
        "--regex",
        "--include-directories",
        "--min-size",
        "300",
        "--max-size",
        "350",
        "--limit",
        "1",
        "--json",
        "--no-progress",
    ]
    assert main(arguments) == EXIT_OK
    entries = json.loads(capsys.readouterr().out)
    assert [(entry["name"], entry["type"]) for entry in entries] == [("sub", "directory")]


def test_search_case_sensitive_and_global_scan_options(
    sample_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case_sensitive = [
        "search",
        str(sample_tree),
        "TXT",
        "--case-sensitive",
        "--json",
        "--no-progress",
    ]
    assert main(case_sensitive) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == []

    with_exclusion = [
        "search",
        str(sample_tree),
        "txt",
        "--exclude",
        "sub",
        "--json",
        "--no-progress",
    ]
    assert main(with_exclusion) == EXIT_OK
    assert [entry["name"] for entry in json.loads(capsys.readouterr().out)] == ["a.txt"]


def test_search_invalid_regex_is_a_clear_cli_error(
    sample_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["search", str(sample_tree), "[", "--regex", "--no-progress"])
    captured = capsys.readouterr()
    assert code != EXIT_OK
    assert "error: Invalid regular expression" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("search_arguments", "message"),
    [
        ([""], "Search query must not be empty"),
        (["[", "--regex"], "Invalid regular expression"),
        (["txt", "--min-size", "200", "--max-size", "100"], "Maximum size"),
    ],
)
def test_search_validation_happens_before_scan(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    search_arguments: list[str],
    message: str,
) -> None:
    def unexpected_scan(_path: str, _args: argparse.Namespace):
        pytest.fail("filesystem scan started before search validation")

    monkeypatch.setattr("kingdirstat.cli._scan", unexpected_scan)

    code = main(["search", "never-scanned", *search_arguments, "--no-progress"])

    captured = capsys.readouterr()
    assert code == EXIT_FATAL
    assert f"error: {message}" in captured.err


def test_types_text_output_includes_a_share_column(
    sample_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["types", str(sample_tree), "--no-progress"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Share" in out
    assert "%" in out


def test_duplicates_finds_the_pair(sample_tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["duplicates", str(sample_tree), "--json", "--no-progress"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["reclaimable_size"] == 128
    assert len(payload["groups"]) == 1


def test_duplicates_csv_has_one_deterministic_formula_safe_row_per_path(
    sample_tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    malicious = sample_tree / "=formula.bin"
    malicious.write_bytes((sample_tree / "dup1.bin").read_bytes())
    destination = tmp_path / "reports" / "duplicates.csv"

    code = main(
        [
            "duplicates",
            str(sample_tree),
            "--csv",
            str(destination),
            "--json",
            "--no-progress",
        ]
    )
    assert code == EXIT_OK
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert len(payload["groups"]) == 1
    assert f"Saved duplicate report CSV: {destination.resolve()}" in captured.err

    with destination.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert list(rows[0]) == [
        "group_index",
        "algorithm",
        "digest",
        "file_size",
        "reclaimable_group_size",
        "path",
    ]
    assert len(rows) == 3
    assert [Path(row["path"]).name for row in rows] == [
        "=formula.bin",
        "dup1.bin",
        "dup2.bin",
    ]
    assert {row["group_index"] for row in rows} == {"1"}
    assert {row["algorithm"] for row in rows} == {"sha256"}
    assert len({row["digest"] for row in rows}) == 1
    assert {int(row["file_size"]) for row in rows} == {128}
    assert {int(row["reclaimable_group_size"]) for row in rows} == {256}
    assert not list(destination.parent.glob(f".{destination.name}.*.tmp"))


def test_duplicates_csv_retains_text_output(
    sample_tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    destination = tmp_path / "duplicates.csv"
    assert (
        main(
            [
                "duplicates",
                str(sample_tree),
                "--csv",
                str(destination),
                "--no-progress",
            ]
        )
        == EXIT_OK
    )
    captured = capsys.readouterr()
    assert "1 duplicate groups" in captured.out
    assert "dup1.bin" in captured.out
    assert destination.exists()


def test_export_and_compare_round_trip(
    sample_tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = tmp_path / "before.json"
    assert main(["export", str(sample_tree), str(before), "--no-progress"]) == EXIT_OK
    capsys.readouterr()

    (sample_tree / "extra.bin").write_bytes(b"e" * 1000)
    after = tmp_path / "after.json"
    assert main(["export", str(sample_tree), str(after), "--no-progress"]) == EXIT_OK
    capsys.readouterr()

    assert main(["compare", str(before), str(after), "--json"]) == EXIT_OK
    difference = json.loads(capsys.readouterr().out)
    assert difference["bytes_delta"] == 1000
    assert any(change["relative_path"] == "extra.bin" for change in difference["added"])


def test_export_csv_to_stdout(sample_tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["export", str(sample_tree), "-", "--format", "csv", "--no-progress"]) == EXIT_OK
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("path,name,type")


def test_drives_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["drives", "--json"]) == EXIT_OK
    drives = json.loads(capsys.readouterr().out)
    assert isinstance(drives, list)
    assert drives, "expected at least one volume"
    assert {"path", "total_space", "free_space"} <= set(drives[0])


@pytest.mark.parametrize("shell", ["powershell", "bash", "zsh"])
def test_completion_mentions_the_new_commands(
    shell: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["completion", shell]) == EXIT_OK
    out = capsys.readouterr().out
    for command in ("tree", "space", "clean", "scan", "largest", "search"):
        assert command in out


def test_unknown_command_is_a_usage_error(sample_tree: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--nonsense"])
    assert excinfo.value.code == EXIT_USAGE


def test_missing_path_reports_an_error_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["scan", str(tmp_path / "nope"), "--no-progress"])
    assert code != EXIT_OK
    assert "error:" in capsys.readouterr().err
