"""Analysis helpers, including the tree pane rows and the cleanup planner."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import TOTAL_SIZE
from kingdirstat.analysis import (
    CLEANUP_RULES,
    cleanup_candidates,
    compare_results,
    empty_directories,
    file_type_stats,
    find_duplicates,
    largest_entries,
    scan_summary,
    search_entries,
    stale_files,
    tree_rows,
)
from kingdirstat.models import Entry
from kingdirstat.scanner import DirectoryScanner


def scan(path: Path):
    return DirectoryScanner().scan(path)


def test_largest_entries_orders_by_size(sample_tree: Path) -> None:
    result = scan(sample_tree)
    entries = largest_entries(result.root, limit=3)
    assert [entry.name for entry in entries] == ["c.txt", "b.log", "dup1.bin"]


def test_largest_entries_can_include_directories(sample_tree: Path) -> None:
    result = scan(sample_tree)
    entries = largest_entries(result.root, limit=1, include_directories=True)
    assert entries[0].name == "sub"  # 350 B, larger than any single file


def test_largest_entries_honours_minimum_size(sample_tree: Path) -> None:
    result = scan(sample_tree)
    entries = largest_entries(result.root, limit=10, minimum_size=200)
    assert {entry.name for entry in entries} == {"b.log", "c.txt"}


def test_search_entries_is_literal_case_insensitive_and_largest_first(
    sample_tree: Path,
) -> None:
    result = scan(sample_tree)
    entries = search_entries(result.root, "TXT")
    assert [entry.name for entry in entries] == ["c.txt", "a.txt"]


def test_search_entries_supports_regex_and_case_sensitivity(sample_tree: Path) -> None:
    result = scan(sample_tree)
    entries = search_entries(result.root, r"^(?:c|dup\d)\.", regex=True)
    assert [entry.name for entry in entries] == ["c.txt", "dup1.bin", "dup2.bin"]
    assert search_entries(result.root, "TXT", case_sensitive=True) == []


def test_search_entries_treats_literal_metacharacters_literally_and_matches_relative_paths(
    sample_tree: Path,
) -> None:
    bracketed = sample_tree / "sub" / "[draft].txt"
    bracketed.write_bytes(b"draft")
    result = scan(sample_tree)
    assert [entry.name for entry in search_entries(result.root, "[draft]")] == [bracketed.name]
    assert [entry.name for entry in search_entries(result.root, "sub/[draft].txt")] == [
        bracketed.name
    ]
    assert [entry.name for entry in search_entries(result.root, r"sub\[draft].txt")] == [
        bracketed.name
    ]


def test_search_entries_does_not_normalise_regex_path_separators(sample_tree: Path) -> None:
    bracketed = sample_tree / "sub" / "[draft].txt"
    bracketed.write_bytes(b"draft")
    result = scan(sample_tree)

    assert [
        entry.name for entry in search_entries(result.root, r"sub/\[draft\]\.txt", regex=True)
    ] == [bracketed.name]
    assert search_entries(result.root, r"sub\\\[draft\]\.txt", regex=True) == []


def test_search_entries_filters_sizes_directories_and_limit(sample_tree: Path) -> None:
    result = scan(sample_tree)
    entries = search_entries(
        result.root,
        r"(?:sub|c\.txt)$",
        regex=True,
        include_directories=True,
        minimum_size=300,
        maximum_size=350,
        limit=1,
    )
    assert len(entries) == 1
    assert entries[0].name == "sub"
    assert entries[0].is_dir


def test_search_entries_rejects_invalid_regex(sample_tree: Path) -> None:
    result = scan(sample_tree)
    with pytest.raises(ValueError, match="Invalid regular expression"):
        search_entries(result.root, "[", regex=True)


def test_search_entries_rejects_an_empty_query(sample_tree: Path) -> None:
    result = scan(sample_tree)
    with pytest.raises(ValueError, match="must not be empty"):
        search_entries(result.root, "")


def test_search_entries_rejects_inverted_size_range(sample_tree: Path) -> None:
    result = scan(sample_tree)
    with pytest.raises(ValueError, match="Maximum size"):
        search_entries(result.root, "txt", minimum_size=200, maximum_size=100)


def test_file_type_stats_group_by_extension(sample_tree: Path) -> None:
    result = scan(sample_tree)
    stats = {item.extension: item for item in file_type_stats(result.root)}
    assert stats[".txt"].files == 2
    assert stats[".txt"].size == 400
    assert stats[".bin"].files == 2
    assert stats[".bin"].size == 256
    assert sum(item.size for item in file_type_stats(result.root)) == TOTAL_SIZE


def test_empty_directories_finds_only_the_empty_one(sample_tree: Path) -> None:
    result = scan(sample_tree)
    assert [entry.name for entry in empty_directories(result.root)] == ["empty"]


def test_duplicates_match_on_content(sample_tree: Path) -> None:
    result = scan(sample_tree)
    report = find_duplicates(result.root)
    assert len(report.groups) == 1
    group = report.groups[0]
    assert {entry.name for entry in group.files} == {"dup1.bin", "dup2.bin"}
    assert group.size == 128
    assert report.reclaimable_size == 128
    assert report.errors == []


def test_duplicates_never_hash_reparse_point_files_by_default(sample_tree: Path) -> None:
    result = scan(sample_tree)
    cloud_placeholder = Entry(
        name="cloud-placeholder.bin",
        path=str(sample_tree / "cloud-placeholder.bin"),
        is_dir=False,
        size=128,
        is_reparse_point=True,
    )
    result.root.children.append(cloud_placeholder)

    report = find_duplicates(result.root)

    assert len(report.groups) == 1
    assert {entry.name for entry in report.groups[0].files} == {"dup1.bin", "dup2.bin"}
    assert report.errors == []


def test_stale_files_uses_the_age_threshold(sample_tree: Path) -> None:
    result = scan(sample_tree)
    assert stale_files(result.root, older_than_days=3650) == []
    assert len(stale_files(result.root, older_than_days=0)) == 6


def test_tree_rows_depth_and_shares(sample_tree: Path) -> None:
    result = scan(sample_tree)
    rows = tree_rows(result.root, max_depth=1)
    assert rows[0].entry is result.root
    assert rows[0].depth == 0
    assert rows[0].share_of_parent == 100.0
    assert len(rows) == 1 + len(result.root.children)
    assert all(row.depth <= 1 for row in rows)

    sub = next(row for row in rows if row.entry.name == "sub")
    assert round(sub.share_of_parent, 4) == round(350 / TOTAL_SIZE * 100, 4)
    assert round(sub.share_of_total, 4) == round(350 / TOTAL_SIZE * 100, 4)


def test_tree_rows_are_depth_first_largest_first(sample_tree: Path) -> None:
    result = scan(sample_tree)
    rows = tree_rows(result.root)
    names = [row.entry.name for row in rows]
    # sub is followed immediately by its own subtree, not by a sibling.
    sub_index = names.index("sub")
    assert names[sub_index + 1] == "c.txt"
    assert names[sub_index + 2] == "__pycache__"
    assert names[sub_index + 3] == "d.pyc"


def test_tree_rows_filters(sample_tree: Path) -> None:
    result = scan(sample_tree)
    dirs_only = tree_rows(result.root, directories_only=True)
    assert {row.entry.name for row in dirs_only} == {"root", "sub", "empty", "__pycache__"}

    limited = tree_rows(result.root, max_depth=1, children_limit=2)
    assert len(limited) == 3  # root plus its two largest children

    big = tree_rows(result.root, max_depth=1, minimum_size=300)
    assert {row.entry.name for row in big} == {"root", "sub"}


def test_cleanup_finds_the_cache_without_double_counting(sample_tree: Path) -> None:
    result = scan(sample_tree)
    found = cleanup_candidates(result.root)
    assert len(found) == 1
    assert found[0].entry.name == "__pycache__"
    assert found[0].rule.confidence == "safe"
    # The .pyc inside must not be reported separately on top of its parent.
    assert sum(candidate.size for candidate in found) == 50


def test_cleanup_confidence_filter(sample_tree: Path) -> None:
    result = scan(sample_tree)
    assert cleanup_candidates(result.root, confidence="review") == []
    assert len(cleanup_candidates(result.root, confidence="safe")) == 1


def test_cleanup_minimum_size(sample_tree: Path) -> None:
    result = scan(sample_tree)
    assert cleanup_candidates(result.root, minimum_size=1024) == []


def test_gradle_rule_requires_review_because_user_config_may_be_present(tmp_path: Path) -> None:
    gradle = tmp_path / ".gradle"
    (gradle / "init.d").mkdir(parents=True)
    (gradle / "init.d" / "company.gradle").write_text("// user configuration", encoding="utf-8")
    result = scan(tmp_path)

    rule = next(rule for rule in CLEANUP_RULES if rule.name == "gradle-cache")
    assert rule.confidence == "review"
    assert "configuration and init scripts" in rule.note
    assert cleanup_candidates(result.root, confidence="safe") == []
    assert [
        candidate.entry.name for candidate in cleanup_candidates(result.root, confidence="review")
    ] == [".gradle"]


def test_compare_reports_added_removed_changed(sample_tree: Path) -> None:
    before = scan(sample_tree)
    (sample_tree / "new.txt").write_bytes(b"n" * 500)
    (sample_tree / "a.txt").unlink()
    after = scan(sample_tree)

    difference = compare_results(before, after)
    added = {change.relative_path for change in difference.added}
    removed = {change.relative_path for change in difference.removed}
    assert "new.txt" in added
    assert "a.txt" in removed
    assert difference.bytes_delta == 500 - 100


def test_scan_summary_shape(sample_tree: Path) -> None:
    summary = scan_summary(scan(sample_tree))
    assert summary["logical_size"] == TOTAL_SIZE
    assert summary["files"] == 6
    assert summary["empty_directories"] == 1
    assert summary["errors"] == 0
