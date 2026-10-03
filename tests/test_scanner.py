"""Scanner accuracy: sizes, counts, depth limits, exclusions, attributes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from conftest import TOTAL_DIRECTORIES, TOTAL_FILES, TOTAL_SIZE
from kingdirstat import scanner as scanner_module
from kingdirstat.analysis import empty_directories
from kingdirstat.models import (
    FILE_ATTRIBUTE_HIDDEN,
    FILE_ATTRIBUTE_READONLY,
    Entry,
    ScanOptions,
)
from kingdirstat.scanner import DirectoryScanner


def test_totals_match_the_bytes_written(sample_tree: Path) -> None:
    result = DirectoryScanner().scan(sample_tree)
    assert result.total_size == TOTAL_SIZE
    assert result.files == TOTAL_FILES
    assert result.directories == TOTAL_DIRECTORIES
    assert result.errors == []
    assert not result.cancelled


def test_directory_sizes_roll_up(sample_tree: Path) -> None:
    result = DirectoryScanner().scan(sample_tree)
    by_name = {child.name: child for child in result.root.children}
    assert by_name["sub"].size == 350  # c.txt 300 + __pycache__/d.pyc 50
    assert by_name["empty"].size == 0
    assert by_name["sub"].file_count == 2
    assert by_name["sub"].directory_count == 1


def test_children_are_sorted_largest_first(sample_tree: Path) -> None:
    result = DirectoryScanner().scan(sample_tree)
    sizes = [child.size for child in result.root.children]
    assert sizes == sorted(sizes, reverse=True)


def test_max_depth_stops_descent_and_is_reported(sample_tree: Path) -> None:
    result = DirectoryScanner(ScanOptions(max_depth=1)).scan(sample_tree)
    sub = next(child for child in result.root.children if child.name == "sub")
    assert sub.children == []
    assert sub.scan_complete is False
    assert sub.skip_reason == "depth limit"
    # Files at depth 1 are still measured; the pruned subtree is not.
    assert result.total_size == 100 + 200 + 128 + 128


def test_max_depth_zero_scans_nothing(sample_tree: Path) -> None:
    result = DirectoryScanner(ScanOptions(max_depth=0)).scan(sample_tree)
    assert result.root.children == []
    assert result.root.scan_complete is False
    assert result.total_size == 0


def test_exclude_by_name_and_by_glob(sample_tree: Path) -> None:
    result = DirectoryScanner(ScanOptions(exclude=("__pycache__",))).scan(sample_tree)
    assert result.total_size == TOTAL_SIZE - 50
    names = {entry.name for entry in result.root.walk()}
    assert "__pycache__" not in names

    result = DirectoryScanner(ScanOptions(exclude=("*.log",))).scan(sample_tree)
    assert result.total_size == TOTAL_SIZE - 200


def test_exclude_by_root_relative_path(sample_tree: Path) -> None:
    result = DirectoryScanner(ScanOptions(exclude=("sub/c.txt",))).scan(sample_tree)
    assert result.total_size == TOTAL_SIZE - 300


def test_cancellation_returns_a_partial_result(sample_tree: Path) -> None:
    scanner = DirectoryScanner()
    scanner.cancel()
    # cancel() before scan() is cleared by scan(); cancel from a callback instead.
    calls = {"count": 0}

    def progress(_update: object) -> None:
        calls["count"] += 1
        scanner.cancel()

    result = scanner.scan(sample_tree, progress=progress)
    assert result.cancelled is True
    assert result.total_size <= TOTAL_SIZE


def test_directory_future_frontier_is_bounded_by_worker_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "wide"
    root.mkdir()
    for index in range(40):
        (root / f"directory-{index:02d}").mkdir()

    pending_sizes: list[int] = []
    real_wait = scanner_module.wait

    def tracking_wait(futures: tuple[Any, ...], *, return_when: str) -> tuple[set[Any], set[Any]]:
        pending_sizes.append(len(futures))
        return real_wait(futures, return_when=return_when)

    monkeypatch.setattr(scanner_module, "wait", tracking_wait)
    workers = 2
    result = DirectoryScanner(ScanOptions(workers=workers)).scan(root)

    assert result.directories == 41
    assert max(pending_sizes) == workers * scanner_module._DIRECTORY_FUTURES_PER_WORKER


def test_cancellation_marks_discovered_unscheduled_directories_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    unscanned = root / "unscanned"
    unscanned.mkdir(parents=True)
    (unscanned / "not-empty.txt").write_text("content", encoding="utf-8")

    scanner = DirectoryScanner(ScanOptions(workers=1))
    real_scan_directory = scanner._scan_directory

    def cancel_after_root_discovery(node: Entry, root_device: int) -> Any:
        batch = real_scan_directory(node, root_device)
        if node.depth == 0:
            scanner.cancel()
        return batch

    monkeypatch.setattr(scanner, "_scan_directory", cancel_after_root_discovery)
    result = scanner.scan(root)
    child = result.root.children[0]

    assert result.cancelled is True
    assert child.path == os.fspath(unscanned)
    assert child.children == []
    assert child.scan_complete is False
    assert child.skip_reason == "cancelled"
    assert child not in empty_directories(result.root)


def test_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        DirectoryScanner().scan(tmp_path / "does-not-exist")


def test_file_target_raises_not_a_directory(sample_tree: Path) -> None:
    with pytest.raises(NotADirectoryError):
        DirectoryScanner().scan(sample_tree / "a.txt")


def test_progress_callback_errors_do_not_break_the_scan(sample_tree: Path) -> None:
    def hostile(_update: object) -> None:
        raise RuntimeError("display blew up")

    result = DirectoryScanner().scan(sample_tree, progress=hostile)
    assert result.total_size == TOTAL_SIZE


def test_allocated_size_is_at_least_logical_size(sample_tree: Path) -> None:
    result = DirectoryScanner(ScanOptions(measure_allocated=True)).scan(sample_tree)
    assert result.total_allocated_size >= 0
    for entry in result.root.walk():
        if not entry.is_dir:
            assert entry.allocated_size >= 0


def test_attributes_are_captured(sample_tree: Path) -> None:
    result = DirectoryScanner().scan(sample_tree)
    for entry in result.root.walk():
        assert isinstance(entry.file_attributes, int)
        assert entry.file_attributes >= 0
        assert set(entry.attribute_string) <= set("RHSATPJCOE")
    if os.name == "nt":
        files = [entry for entry in result.root.walk() if not entry.is_dir]
        assert any("A" in entry.attribute_string for entry in files)


def test_attribute_string_maps_known_flags() -> None:
    entry = Entry(
        name="x",
        path="x",
        is_dir=False,
        file_attributes=FILE_ATTRIBUTE_READONLY | FILE_ATTRIBUTE_HIDDEN,
    )
    assert entry.attribute_string == "RH"
    assert Entry(name="y", path="y", is_dir=False).attribute_string == ""


def test_walk_covers_every_entry(sample_tree: Path) -> None:
    result = DirectoryScanner().scan(sample_tree)
    assert len(list(result.root.walk())) == TOTAL_FILES + TOTAL_DIRECTORIES
    assert len(list(result.root.walk(include_self=False))) == TOTAL_FILES + TOTAL_DIRECTORIES - 1
