"""GUI model tests. These run headless and are skipped when PySide6 is absent."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="GUI extra not installed")

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QApplication, QInputDialog

from kingdirstat.drives import DriveInfo
from kingdirstat.gui import (
    COLUMN_ATTRIBUTES,
    COLUMN_FILES,
    COLUMN_ITEMS,
    COLUMN_MODIFIED,
    COLUMN_NAME,
    COLUMN_SHARE,
    COLUMN_SIZE,
    COLUMN_SUBDIRS,
    COLUMNS,
    EntryTreeModel,
    MainWindow,
    _file_details,
    format_size,
)
from kingdirstat.models import Entry, ScanResult
from kingdirstat.scanner import DirectoryScanner


def test_closing_during_a_slow_scan_keeps_the_worker_alive(qt_app) -> None:
    """A cancellation timeout must not destroy a running QThread."""

    from PySide6.QtGui import QCloseEvent

    class SlowWorker:
        cancelled = False

        def isRunning(self):
            return True

        def cancel(self):
            self.cancelled = True

        def wait(self, milliseconds):
            assert milliseconds == 3000
            return False

    window = MainWindow()
    worker = SlowWorker()
    window._thread = worker
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert worker.cancelled
    assert window._thread is worker
    window._thread = None
    window.close()


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def model(qt_app, sample_tree: Path) -> EntryTreeModel:
    result = DirectoryScanner().scan(sample_tree)
    instance = EntryTreeModel()
    instance.set_root(result.root)
    instance._scan_result = result  # type: ignore[attr-defined]
    return instance


def test_empty_model_has_no_rows(qt_app) -> None:
    instance = EntryTreeModel()
    assert instance.rowCount() == 0
    assert not instance.index(0, 0, QModelIndex()).isValid()
    assert instance.entry_at(QModelIndex()) is None


def test_root_row_and_columns(model: EntryTreeModel) -> None:
    assert model.columnCount() == len(COLUMNS)
    assert model.rowCount() == 1
    root = model.index(0, 0, QModelIndex())
    assert root.isValid()
    assert model.data(root) == "root"
    assert model.data(model.index(0, COLUMN_SHARE, QModelIndex())) == "100.0%"


def test_children_are_lazy_but_counted(model: EntryTreeModel) -> None:
    root = model.index(0, 0, QModelIndex())
    assert model.rowCount(root) == 6  # 4 files, 2 directories
    first = model.index(0, COLUMN_NAME, root)
    assert model.data(first) == "sub"  # largest child first


def test_parent_round_trip_at_two_levels(model: EntryTreeModel) -> None:
    root = model.index(0, 0, QModelIndex())
    child = model.index(0, 0, root)
    assert model.parent(child) == root

    grandchild = model.index(0, 0, child)
    assert grandchild.isValid()
    assert model.parent(grandchild) == child
    assert model.parent(root) == QModelIndex()


def test_share_column_is_relative_to_the_parent(model: EntryTreeModel) -> None:
    root = model.index(0, 0, QModelIndex())
    for row in range(model.rowCount(root)):
        text = model.data(model.index(row, COLUMN_SHARE, root))
        assert text.endswith("%")
        assert 0.0 <= float(text.rstrip("%")) <= 100.0


def test_attributes_column_renders(model: EntryTreeModel) -> None:
    root = model.index(0, 0, QModelIndex())
    value = model.data(model.index(0, COLUMN_ATTRIBUTES, root))
    assert isinstance(value, str)
    assert set(value) <= set("RHSATPJCOE")


def test_index_for_entry_finds_a_nested_file(model: EntryTreeModel) -> None:
    result = model._scan_result  # type: ignore[attr-defined]
    nested = next(entry for entry in result.root.walk() if entry.name == "d.pyc")
    index = model.index_for_entry(nested)
    assert index.isValid()
    assert model.entry_at(index) is nested
    assert model.data(index) == "d.pyc"


def test_index_for_entry_returns_invalid_for_a_stranger(model: EntryTreeModel) -> None:
    from kingdirstat.models import Entry

    stranger = Entry(name="nope", path=os.path.join("x", "nope"), is_dir=False)
    assert not model.index_for_entry(stranger).isValid()


def test_index_for_entry_uses_identity_for_case_colliding_siblings(qt_app) -> None:
    wrong_file = Entry(name="wrong.txt", path="C:/root/Foo/wrong.txt", is_dir=False, size=1)
    target = Entry(name="target.txt", path="C:/root/foo/target.txt", is_dir=False, size=2)
    upper = Entry(
        name="Foo",
        path="C:/root/Foo",
        is_dir=True,
        size=1,
        file_count=1,
        children=[wrong_file],
    )
    lower = Entry(
        name="foo",
        path="C:/root/foo",
        is_dir=True,
        size=2,
        file_count=1,
        children=[target],
    )
    root = Entry(
        name="root",
        path="C:/root",
        is_dir=True,
        size=3,
        file_count=2,
        directory_count=2,
        children=[upper, lower],
    )
    instance = EntryTreeModel()
    instance.set_root(root)

    index = instance.index_for_entry(target)

    assert index.isValid()
    assert instance.entry_at(index) is target
    assert instance.entry_at(index.parent()) is lower


def _sortable_result() -> tuple[ScanResult, dict[str, Entry]]:
    entries = {
        "alpha": Entry(
            name="alpha",
            path="C:/root/alpha",
            is_dir=True,
            size=30,
            modified_time=200,
            file_attributes=0x1,
            file_count=2,
            directory_count=1,
        ),
        "beta": Entry(
            name="beta",
            path="C:/root/beta",
            is_dir=True,
            size=10,
            modified_time=100,
            file_attributes=0x2,
            directory_count=2,
        ),
        "gamma": Entry(
            name="gamma",
            path="C:/root/gamma",
            is_dir=True,
            size=20,
            modified_time=300,
            file_attributes=0x4,
            file_count=1,
        ),
    }
    root = Entry(
        name="root",
        path="C:/root",
        is_dir=True,
        size=60,
        file_count=3,
        directory_count=6,
        children=[entries["gamma"], entries["alpha"], entries["beta"]],
    )
    result = ScanResult(
        root=root,
        duration=0.0,
        files=3,
        directories=7,
        total_size=60,
        total_allocated_size=60,
    )
    return result, entries


@pytest.mark.parametrize(
    ("column", "ascending"),
    [
        (COLUMN_NAME, ["alpha", "beta", "gamma"]),
        (COLUMN_SIZE, ["beta", "gamma", "alpha"]),
        (COLUMN_SHARE, ["beta", "gamma", "alpha"]),
        (COLUMN_ITEMS, ["gamma", "beta", "alpha"]),
        (COLUMN_FILES, ["beta", "gamma", "alpha"]),
        (COLUMN_SUBDIRS, ["gamma", "alpha", "beta"]),
        (COLUMN_MODIFIED, ["beta", "alpha", "gamma"]),
        (COLUMN_ATTRIBUTES, ["beta", "alpha", "gamma"]),
    ],
)
def test_tree_sort_uses_native_values_in_both_directions(
    qt_app, column: int, ascending: list[str]
) -> None:
    result, _ = _sortable_result()
    instance = EntryTreeModel()
    instance.set_root(result.root)

    def child_names() -> list[str]:
        root_index = instance.index(0, COLUMN_NAME, QModelIndex())
        return [
            instance.data(instance.index(row, COLUMN_NAME, root_index))
            for row in range(instance.rowCount(root_index))
        ]

    instance.sort(column, Qt.SortOrder.AscendingOrder)
    assert child_names() == ascending
    instance.sort(column, Qt.SortOrder.DescendingOrder)
    assert child_names() == list(reversed(ascending))
    assert [entry.name for entry in result.root.children] == ["gamma", "alpha", "beta"]


def test_sort_rebuilds_nodes_with_current_rows(qt_app) -> None:
    result, _ = _sortable_result()
    instance = EntryTreeModel()
    instance.set_root(result.root)
    root_before = instance.index(0, COLUMN_NAME, QModelIndex())
    first_before = instance.index(0, COLUMN_NAME, root_before).internalPointer()

    instance.sort(COLUMN_NAME, Qt.SortOrder.AscendingOrder)
    root_after = instance.index(0, COLUMN_NAME, QModelIndex())
    for row in range(instance.rowCount(root_after)):
        child = instance.index(row, COLUMN_NAME, root_after)
        assert child.internalPointer().row == row
        assert instance.parent(child) == root_after
    assert instance.index(0, COLUMN_NAME, root_after).internalPointer() is not first_before


def test_format_size_matches_the_cli_style() -> None:
    assert format_size(0) == "0 B"
    assert format_size(1024) == "1.00 KiB"


def test_main_window_has_extension_and_largest_file_tabs(qt_app, sample_tree: Path) -> None:
    window = MainWindow()
    result = DirectoryScanner().scan(sample_tree)
    window._on_completed(result)

    assert window._details_tabs.count() == 2
    assert [window._details_tabs.tabText(index) for index in range(2)] == [
        "Extensions",
        "Largest Files",
    ]
    assert window._largest.rowCount() == result.files
    assert window._largest.item(0, 0).text() == "c.txt"
    assert window._largest.item(0, 1).text() == "300 B"
    window.close()


def test_tree_sort_preserves_selected_entry(qt_app) -> None:
    result, entries = _sortable_result()
    window = MainWindow()
    window._on_completed(result)
    window._select_entry(entries["beta"], source="test")

    window._model.sort(COLUMN_NAME, Qt.SortOrder.AscendingOrder)

    assert window._model.entry_at(window._tree.currentIndex()) is entries["beta"]
    assert window._treemap.selected_entry is entries["beta"]
    assert window._tree.isSortingEnabled()
    assert window._tree.header().sortIndicatorSection() == COLUMN_SIZE
    window.close()


def test_tree_sort_preserves_all_expanded_branches(qt_app) -> None:
    leaf = Entry(name="leaf.bin", path="C:/root/alpha/inner/leaf.bin", is_dir=False, size=1)
    inner = Entry(
        name="inner",
        path="C:/root/alpha/inner",
        is_dir=True,
        size=1,
        file_count=1,
        children=[leaf],
    )
    alpha = Entry(
        name="alpha",
        path="C:/root/alpha",
        is_dir=True,
        size=1,
        file_count=1,
        directory_count=1,
        children=[inner],
    )
    beta = Entry(name="beta", path="C:/root/beta", is_dir=True)
    root = Entry(
        name="root",
        path="C:/root",
        is_dir=True,
        size=1,
        file_count=1,
        directory_count=3,
        children=[beta, alpha],
    )
    result = ScanResult(
        root=root,
        duration=0,
        files=1,
        directories=4,
        total_size=1,
        total_allocated_size=1,
    )
    window = MainWindow()
    window._on_completed(result)
    alpha_index = window._model.index_for_entry(alpha)
    inner_index = window._model.index_for_entry(inner)
    window._tree.expand(alpha_index)
    window._tree.expand(inner_index)
    window._tree.setCurrentIndex(window._model.index_for_entry(root))

    window._model.sort(COLUMN_NAME, Qt.SortOrder.AscendingOrder)

    assert window._tree.isExpanded(window._model.index_for_entry(root))
    assert window._tree.isExpanded(window._model.index_for_entry(alpha))
    assert window._tree.isExpanded(window._model.index_for_entry(inner))
    assert not window._tree.isExpanded(window._model.index_for_entry(beta))
    window.close()


def test_open_drive_chooses_accessible_drive_and_starts_scan(qt_app, monkeypatch) -> None:
    accessible = DriveInfo(
        path="Z:\\",
        label="Data",
        total_space=2_000,
        free_space=500,
        used_space=1_500,
        drive_type="Fixed",
        file_system="NTFS",
        is_accessible=True,
    )
    unavailable = DriveInfo(
        path="Y:\\",
        label="Offline",
        total_space=0,
        free_space=0,
        used_space=0,
        drive_type="Network",
        file_system="Unknown",
        is_accessible=False,
    )
    shown_items: list[str] = []
    started: list[str] = []

    class FakeDriveSelector:
        def get_available_drives(self) -> list[DriveInfo]:
            return [unavailable, accessible]

        def format_drive_display(self, drive: DriveInfo) -> str:
            return f"{drive.path} {drive.label}"

    def choose_drive(*args):
        shown_items.extend(args[3])
        return args[3][0], True

    monkeypatch.setattr("kingdirstat.gui.DriveSelector", FakeDriveSelector)
    monkeypatch.setattr(QInputDialog, "getItem", choose_drive)
    monkeypatch.setattr(MainWindow, "start_scan", lambda self, path: started.append(path))
    window = MainWindow()

    window._choose_drive()

    assert shown_items == ["Z:\\ Data"]
    assert started == ["Z:\\"]
    assert window._open_drive_action.isEnabled()
    window.close()


def test_largest_file_selection_synchronises_tree_and_treemap(qt_app, sample_tree: Path) -> None:
    window = MainWindow()
    result = DirectoryScanner().scan(sample_tree)
    window._on_completed(result)

    row = next(
        index
        for index in range(window._largest.rowCount())
        if window._largest.item(index, 0).text() == "c.txt"
    )
    window._largest.selectRow(row)
    qt_app.processEvents()

    selected = window._model.entry_at(window._tree.currentIndex())
    assert selected is not None and selected.name == "c.txt"
    assert window._treemap.selected_entry is selected
    window.close()


def test_largest_files_view_is_bounded_to_500(qt_app) -> None:
    children = [
        Entry(
            name=f"file-{index:03}.bin",
            path=f"C:/root/file-{index:03}.bin",
            is_dir=False,
            size=index,
        )
        for index in range(600)
    ]
    root = Entry(
        name="root",
        path="C:/root",
        is_dir=True,
        size=sum(entry.size for entry in children),
        file_count=len(children),
        children=children,
    )
    result = ScanResult(
        root=root,
        duration=0.0,
        files=len(children),
        directories=1,
        total_size=root.size,
        total_allocated_size=root.size,
    )
    window = MainWindow()
    window._on_completed(result)

    assert window._largest.rowCount() == 500
    assert window._largest.item(0, 0).text() == "file-599.bin"
    assert window._largest.item(499, 0).text() == "file-100.bin"
    window.close()


def test_file_details_collects_both_views_in_one_bounded_walk(qt_app, monkeypatch) -> None:
    children = [
        Entry(
            name=f"file-{index:03}.bin",
            path=f"C:/root/file-{index:03}.bin",
            is_dir=False,
            size=index,
            allocated_size=index + 1,
            extension=".bin",
        )
        for index in range(600)
    ]
    root = Entry(
        name="root",
        path="C:/root",
        is_dir=True,
        size=sum(entry.size for entry in children),
        file_count=len(children),
        children=children,
    )
    original_walk = Entry.walk
    walk_calls = 0

    def counted_walk(self, *args, **kwargs):
        nonlocal walk_calls
        walk_calls += 1
        yield from original_walk(self, *args, **kwargs)

    monkeypatch.setattr(Entry, "walk", counted_walk)

    stats, largest, largest_for_extension = _file_details(root)

    assert walk_calls == 1
    assert len(largest) == 500
    assert largest[0] is children[599]
    assert largest[-1] is children[100]
    assert [(item.extension, item.files, item.size) for item in stats] == [(".bin", 600, root.size)]
    assert largest_for_extension[".bin"] is children[599]


def test_find_is_case_insensitive_cycles_and_reports_not_found(qt_app, sample_tree: Path) -> None:
    window = MainWindow()
    result = DirectoryScanner().scan(sample_tree)
    window._on_completed(result)

    window._find_edit.setText("DUP")
    window._find_next()
    first = window._model.entry_at(window._tree.currentIndex())
    window._find_next()
    second = window._model.entry_at(window._tree.currentIndex())
    window._find_next()
    wrapped = window._model.entry_at(window._tree.currentIndex())

    assert first is not None and first.name == "dup1.bin"
    assert second is not None and second.name == "dup2.bin"
    assert wrapped is first
    assert "wrapped" in window._find_feedback.text()

    window._find_edit.setText("definitely-not-present")
    window._find_next()
    assert window._find_feedback.text().startswith("No matches")
    assert window._status_label.text().startswith("No matches")
    window.close()


def test_find_materialises_only_the_matching_tree_branch(qt_app) -> None:
    alpha_file = Entry(name="needle.txt", path="C:/root/alpha/needle.txt", is_dir=False, size=1)
    beta_file = Entry(name="other.txt", path="C:/root/beta/other.txt", is_dir=False, size=1)
    alpha = Entry(
        name="alpha", path="C:/root/alpha", is_dir=True, size=1, file_count=1, children=[alpha_file]
    )
    beta = Entry(
        name="beta", path="C:/root/beta", is_dir=True, size=1, file_count=1, children=[beta_file]
    )
    root = Entry(
        name="root",
        path="C:/root",
        is_dir=True,
        size=2,
        file_count=2,
        directory_count=2,
        children=[alpha, beta],
    )
    result = ScanResult(
        root=root,
        duration=0.0,
        files=2,
        directories=3,
        total_size=2,
        total_allocated_size=2,
    )
    window = MainWindow()
    window._on_completed(result)
    window._find_edit.setText("needle")
    window._find_next()

    root_node = window._model._root
    assert root_node is not None and root_node._children is not None
    beta_node = next(node for node in root_node._children if node.entry is beta)
    assert beta_node._children is None
    assert window._model.entry_at(window._tree.currentIndex()) is alpha_file
    window.close()
