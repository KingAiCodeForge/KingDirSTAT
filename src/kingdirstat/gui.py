"""Desktop interface: directory pane, treemap, and extension list.

Layout follows WinDirStat: a sortable directory tree at the top left, the
extension list at the top right, and the treemap underneath, with selection
synchronised across all three. Every action here is read-only.
"""

from __future__ import annotations

import argparse
import heapq
import os
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Any

from PySide6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .analysis import FileTypeStat
from .drives import DriveSelector, drive_for_path
from .exporters import read_snapshot, write_csv, write_snapshot
from .models import Entry, ScanOptions, ScanProgress, ScanResult
from .scanner import DirectoryScanner
from .treemap import TreemapWidget

COLUMNS = ("Name", "Size", "% of parent", "Items", "Files", "Subdirs", "Last change", "Attributes")
COLUMN_NAME = 0
COLUMN_SIZE = 1
COLUMN_SHARE = 2
COLUMN_ITEMS = 3
COLUMN_FILES = 4
COLUMN_SUBDIRS = 5
COLUMN_MODIFIED = 6
COLUMN_ATTRIBUTES = 7

# Qt's model overrides are declared with a default invalid parent index. A shared
# module-level instance keeps ruff's B008 happy without changing the signatures Qt
# expects; an invalid QModelIndex carries no state, so sharing one is safe.
NO_PARENT = QModelIndex()


def format_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(size) < 1024 or unit == "PiB":
            return f"{int(size)} B" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PiB"


def _format_timestamp(value: float | None) -> str:
    if value is None:
        return ""
    try:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return ""


def _file_details(
    root: Entry,
) -> tuple[list[FileTypeStat], list[Entry], dict[str, Entry]]:
    """Collect extension totals and a bounded largest-file view in one walk."""

    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    largest_for_extension: dict[str, Entry] = {}

    def files() -> Iterator[Entry]:
        for entry in root.walk():
            if entry.is_dir:
                continue
            extension = entry.extension or "(no extension)"
            bucket = totals[extension]
            bucket[0] += 1
            bucket[1] += entry.size
            bucket[2] += entry.allocated_size
            previous = largest_for_extension.get(extension)
            if previous is None or (-entry.size, entry.path.casefold(), entry.path) < (
                -previous.size,
                previous.path.casefold(),
                previous.path,
            ):
                largest_for_extension[extension] = entry
            yield entry

    largest = heapq.nsmallest(
        500,
        files(),
        key=lambda entry: (-entry.size, entry.path.casefold(), entry.path),
    )
    stats = sorted(
        (
            FileTypeStat(
                extension=extension,
                files=values[0],
                size=values[1],
                allocated_size=values[2],
            )
            for extension, values in totals.items()
        ),
        key=lambda item: (-item.size, item.extension),
    )
    return stats, largest, largest_for_extension


def _entry_sort_key(entry: Entry, column: int) -> tuple[object, str, str]:
    """Return a deterministic, native-value key for one tree column."""

    if column == COLUMN_NAME:
        primary: object = (entry.name or entry.path).casefold()
    elif column in {COLUMN_SIZE, COLUMN_SHARE}:
        # A child's share has the same denominator as its siblings, so byte size
        # gives the exact same ordering without sorting formatted percentages.
        primary = entry.size
    elif column == COLUMN_ITEMS:
        primary = entry.file_count + entry.directory_count if entry.is_dir else 0
    elif column == COLUMN_FILES:
        primary = entry.file_count if entry.is_dir else 0
    elif column == COLUMN_SUBDIRS:
        primary = entry.directory_count if entry.is_dir else 0
    elif column == COLUMN_MODIFIED:
        primary = entry.modified_time if entry.modified_time is not None else float("-inf")
    elif column == COLUMN_ATTRIBUTES:
        primary = entry.attribute_string.casefold()
    else:
        primary = (entry.name or entry.path).casefold()
    return (
        primary,
        (entry.name or entry.path).casefold(),
        os.path.normcase(os.path.normpath(entry.path)),
    )


class _Node:
    """Lazily created view wrapper around an Entry.

    Only expanded branches are materialised, so opening a scan of millions of
    files does not build millions of model nodes up front.
    """

    __slots__ = ("_children", "entry", "parent", "row", "sort_column", "sort_order")

    def __init__(
        self,
        entry: Entry,
        parent: _Node | None,
        row: int,
        sort_column: int,
        sort_order: Qt.SortOrder,
    ) -> None:
        self.entry = entry
        self.parent = parent
        self.row = row
        self.sort_column = sort_column
        self.sort_order = sort_order
        self._children: list[_Node] | None = None

    @property
    def children(self) -> list[_Node]:
        if self._children is None:
            ordered = sorted(
                self.entry.children,
                key=lambda entry: _entry_sort_key(entry, self.sort_column),
                reverse=self.sort_order == Qt.SortOrder.DescendingOrder,
            )
            self._children = [
                _Node(child, self, index, self.sort_column, self.sort_order)
                for index, child in enumerate(ordered)
            ]
        return self._children


class EntryTreeModel(QAbstractItemModel):
    """Read-only model over a scanned tree."""

    sorting_started = Signal()
    sorting_finished = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._root: _Node | None = None
        self._sort_column = COLUMN_SIZE
        self._sort_order = Qt.SortOrder.DescendingOrder

    def set_root(self, root: Entry | None) -> None:
        self.beginResetModel()
        self._root = (
            None if root is None else _Node(root, None, 0, self._sort_column, self._sort_order)
        )
        self.endResetModel()

    def sort(
        self,
        column: int,
        order: Qt.SortOrder = Qt.SortOrder.AscendingOrder,
    ) -> None:
        """Sort lazily materialised view nodes without mutating scan data."""

        if not 0 <= column < len(COLUMNS):
            return
        root_entry = self._root.entry if self._root is not None else None
        self.sorting_started.emit()
        self.beginResetModel()
        self._sort_column = column
        self._sort_order = order
        self._root = (
            None
            if root_entry is None
            else _Node(root_entry, None, 0, self._sort_column, self._sort_order)
        )
        self.endResetModel()
        self.sorting_finished.emit()

    def entry_at(self, index: QModelIndex | QPersistentModelIndex) -> Entry | None:
        if not index.isValid():
            return None
        node = index.internalPointer()
        return node.entry if isinstance(node, _Node) else None

    def index_for_entry(self, entry: Entry) -> QModelIndex:
        """Walk down from the root, materialising only the exact path to *entry*.

        Paths from the scan retain their real casing. Exact, case-preserving prefix
        checks distinguish ``Foo`` and ``foo`` in case-sensitive NTFS directories
        without searching the entire scan tree for every GUI selection.
        """

        if self._root is None:
            return QModelIndex()
        root_entry = self._root.entry
        if root_entry is entry:
            return self.index(0, 0, QModelIndex())

        chain: list[Entry] = []
        target_path = os.path.normpath(entry.path)
        current = root_entry
        while current is not entry:
            for child in current.children:
                child_path = os.path.normpath(child.path)
                if child is entry or target_path.startswith(child_path.rstrip(os.sep) + os.sep):
                    chain.append(child)
                    current = child
                    break
            else:
                return QModelIndex()

        index = self.index(0, 0, QModelIndex())
        for step in chain:
            node = index.internalPointer()
            if not isinstance(node, _Node):
                return QModelIndex()
            for row, candidate in enumerate(node.children):
                if candidate.entry is step:
                    index = self.index(row, 0, index)
                    break
            else:
                return QModelIndex()
        return index

    # Qt model interface ---------------------------------------------------
    def index(
        self, row: int, column: int, parent: QModelIndex | QPersistentModelIndex = NO_PARENT
    ) -> QModelIndex:
        if self._root is None or not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            return self.createIndex(row, column, self._root) if row == 0 else QModelIndex()
        node = parent.internalPointer()
        if not isinstance(node, _Node):
            return QModelIndex()
        children = node.children
        if row >= len(children):
            return QModelIndex()
        return self.createIndex(row, column, children[row])

    def parent(self, child: QModelIndex | QPersistentModelIndex = NO_PARENT) -> QModelIndex:  # type: ignore[override]
        if not child.isValid():
            return QModelIndex()
        node = child.internalPointer()
        if not isinstance(node, _Node) or node.parent is None:
            return QModelIndex()
        parent = node.parent
        return self.createIndex(parent.row, 0, parent)

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = NO_PARENT) -> int:
        if self._root is None:
            return 0
        if not parent.isValid():
            return 1
        if parent.column() > 0:
            return 0
        node = parent.internalPointer()
        if not isinstance(node, _Node):
            return 0
        return len(node.entry.children)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = NO_PARENT) -> int:
        return len(COLUMNS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        node = index.internalPointer()
        if not isinstance(node, _Node):
            return None
        entry = node.entry
        column = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if column == COLUMN_NAME:
                name = entry.name or entry.path
                if not entry.scan_complete and entry.skip_reason:
                    return f"{name}  [{entry.skip_reason}]"
                return name
            if column == COLUMN_SIZE:
                return format_size(entry.size)
            if column == COLUMN_SHARE:
                parent_entry = node.parent.entry if node.parent is not None else None
                if parent_entry is None or not parent_entry.size:
                    return "100.0%"
                return f"{entry.size / parent_entry.size * 100:.1f}%"
            if column == COLUMN_ITEMS:
                return f"{entry.file_count + entry.directory_count:,}" if entry.is_dir else ""
            if column == COLUMN_FILES:
                return f"{entry.file_count:,}" if entry.is_dir else ""
            if column == COLUMN_SUBDIRS:
                return f"{entry.directory_count:,}" if entry.is_dir else ""
            if column == COLUMN_MODIFIED:
                return _format_timestamp(entry.modified_time)
            if column == COLUMN_ATTRIBUTES:
                return entry.attribute_string
            return None

        if role == Qt.ItemDataRole.TextAlignmentRole and column in {
            COLUMN_SIZE,
            COLUMN_SHARE,
            COLUMN_ITEMS,
            COLUMN_FILES,
            COLUMN_SUBDIRS,
        }:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.DecorationRole and column == COLUMN_NAME:
            return TreemapWidget.color_for_entry(entry)

        if role == Qt.ItemDataRole.ToolTipRole:
            return entry.path

        return None


class ScanThread(QThread):
    """Runs a scan off the UI thread so the window stays responsive."""

    progressed = Signal(object)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, path: str, options: ScanOptions, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._path = path
        self._scanner = DirectoryScanner(options)

    def cancel(self) -> None:
        self._scanner.cancel()

    def run(self) -> None:
        try:
            result = self._scanner.scan(self._path, progress=self.progressed.emit)
        except (OSError, ValueError) as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(result)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("KingDirSTAT")
        self.resize(1180, 780)

        self._result: ScanResult | None = None
        self._thread: ScanThread | None = None
        self._syncing = False
        self._largest_rows: dict[int, int] = {}
        self._largest_for_extension: dict[str, Entry] = {}
        self._find_query = ""
        self._find_iterator: Iterator[Entry] | None = None
        self._find_match_index = 0
        self._tree_selection_before_sort: Entry | None = None
        self._expanded_entries: dict[int, Entry] = {}
        self._tree_expansion_before_sort: list[Entry] = []

        self._model = EntryTreeModel(self)
        self._tree = QTreeView()
        self._tree.setModel(self._model)
        self._model.sorting_started.connect(self._remember_tree_selection)
        self._model.sorting_finished.connect(self._restore_tree_selection)
        self._tree.setUniformRowHeights(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_tree_menu)
        self._tree.expanded.connect(self._tree_expanded)
        self._tree.collapsed.connect(self._tree_collapsed)
        self._tree.setColumnWidth(COLUMN_NAME, 380)
        header = self._tree.header()
        header.setSectionResizeMode(COLUMN_NAME, QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        header.setSortIndicator(COLUMN_SIZE, Qt.SortOrder.DescendingOrder)
        self._tree.setSortingEnabled(True)

        self._types = QTableWidget(0, 4)
        self._types.setHorizontalHeaderLabels(["Extension", "Files", "Size", "Share"])
        self._types.verticalHeader().setVisible(False)
        self._types.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._types.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._types.horizontalHeader().setStretchLastSection(True)

        self._largest = QTableWidget(0, 4)
        self._largest.setHorizontalHeaderLabels(["Name", "Size", "Modified", "Path"])
        self._largest.verticalHeader().setVisible(False)
        self._largest.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._largest.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._largest.horizontalHeader().setStretchLastSection(True)

        self._details_tabs = QTabWidget()
        self._details_tabs.addTab(self._types, "Extensions")
        self._details_tabs.addTab(self._largest, "Largest Files")

        self._treemap = TreemapWidget()

        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(self._tree)
        top.addWidget(self._details_tabs)
        top.setStretchFactor(0, 3)
        top.setStretchFactor(1, 1)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(self._treemap)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._progress.setMaximumWidth(180)
        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.setVisible(False)
        self._cancel_button.clicked.connect(self._cancel_scan)
        self._status_label = QLabel("Open a folder to begin.")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        self._find_bar = QWidget()
        find_layout = QHBoxLayout(self._find_bar)
        find_layout.setContentsMargins(6, 4, 6, 4)
        find_layout.addWidget(QLabel("Find:"))
        self._find_edit = QLineEdit()
        self._find_edit.setPlaceholderText("Name or path")
        self._find_edit.setClearButtonEnabled(True)
        find_layout.addWidget(self._find_edit, 1)
        self._find_next_button = QPushButton("Find Next")
        find_layout.addWidget(self._find_next_button)
        self._find_feedback = QLabel("")
        self._find_feedback.setMinimumWidth(180)
        find_layout.addWidget(self._find_feedback)
        layout.addWidget(self._find_bar)
        layout.addWidget(split)
        self.setCentralWidget(container)

        bar = self.statusBar()
        bar.addWidget(self._status_label, 1)
        bar.addPermanentWidget(self._progress)
        bar.addPermanentWidget(self._cancel_button)

        self._tree.selectionModel().selectionChanged.connect(self._tree_selection_changed)
        self._treemap.selection_changed.connect(self._treemap_selection_changed)
        self._treemap.item_activated.connect(self._treemap_activated)
        self._types.itemSelectionChanged.connect(self._type_selection_changed)
        self._largest.itemSelectionChanged.connect(self._largest_selection_changed)
        self._find_edit.textChanged.connect(self._find_text_changed)
        self._find_edit.returnPressed.connect(self._find_next)
        self._find_next_button.clicked.connect(self._find_next)

        self._build_menus()
        self._update_actions(scanning=False)

    # Menus ----------------------------------------------------------------
    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self._open_action = QAction("&Open Folder...", self)
        self._open_action.setShortcut(QKeySequence.StandardKey.Open)
        self._open_action.triggered.connect(self._choose_folder)
        file_menu.addAction(self._open_action)

        self._open_drive_action = QAction("Open &Drive...", self)
        self._open_drive_action.triggered.connect(self._choose_drive)
        file_menu.addAction(self._open_drive_action)

        self._open_snapshot_action = QAction("Open &Snapshot...", self)
        self._open_snapshot_action.triggered.connect(self._choose_snapshot)
        file_menu.addAction(self._open_snapshot_action)

        file_menu.addSeparator()
        self._save_action = QAction("Save S&napshot...", self)
        self._save_action.setShortcut(QKeySequence.StandardKey.Save)
        self._save_action.triggered.connect(self._save_snapshot)
        file_menu.addAction(self._save_action)

        self._export_action = QAction("Export &CSV...", self)
        self._export_action.triggered.connect(self._export_csv)
        file_menu.addAction(self._export_action)

        file_menu.addSeparator()
        quit_action = QAction("E&xit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        scan_menu = self.menuBar().addMenu("&Scan")
        self._rescan_action = QAction("&Rescan", self)
        self._rescan_action.setShortcut(QKeySequence.StandardKey.Refresh)
        self._rescan_action.triggered.connect(self._rescan)
        scan_menu.addAction(self._rescan_action)

        self._cancel_action = QAction("&Cancel Scan", self)
        self._cancel_action.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        self._cancel_action.triggered.connect(self._cancel_scan)
        scan_menu.addAction(self._cancel_action)

        view_menu = self.menuBar().addMenu("&View")
        zoom_in = QAction("Zoom to &Selection", self)
        zoom_in.triggered.connect(self._zoom_to_selection)
        view_menu.addAction(zoom_in)
        zoom_out = QAction("Zoom &Out", self)
        zoom_out.triggered.connect(self._treemap.zoom_out)
        view_menu.addAction(zoom_out)
        reset = QAction("&Reset Zoom", self)
        reset.triggered.connect(self._treemap.reset_zoom)
        view_menu.addAction(reset)
        view_menu.addSeparator()
        self._cushion_action = QAction("&Cushion Shading", self)
        self._cushion_action.setCheckable(True)
        self._cushion_action.setChecked(self._treemap.cushion_shading)
        self._cushion_action.toggled.connect(self._treemap.set_cushion_shading)
        view_menu.addAction(self._cushion_action)

        expand = QAction("&Expand Largest Branch", self)
        expand.triggered.connect(self._expand_largest_branch)
        view_menu.addAction(expand)

        edit_menu = self.menuBar().addMenu("&Edit")
        self._find_action = QAction("&Find...", self)
        self._find_action.setShortcut(QKeySequence.StandardKey.Find)
        self._find_action.triggered.connect(self._focus_find)
        edit_menu.addAction(self._find_action)

        self._find_next_action = QAction("Find &Next", self)
        self._find_next_action.setShortcut(QKeySequence(Qt.Key.Key_F3))
        self._find_next_action.triggered.connect(self._find_next)
        edit_menu.addAction(self._find_next_action)

        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("&About", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

    def _update_actions(self, *, scanning: bool) -> None:
        has_result = self._result is not None
        self._open_action.setEnabled(not scanning)
        self._open_drive_action.setEnabled(not scanning)
        self._open_snapshot_action.setEnabled(not scanning)
        self._rescan_action.setEnabled(not scanning and has_result)
        self._cancel_action.setEnabled(scanning)
        self._save_action.setEnabled(not scanning and has_result)
        self._export_action.setEnabled(not scanning and has_result)
        self._find_next_action.setEnabled(not scanning and has_result)
        self._find_next_button.setEnabled(not scanning and has_result)
        self._progress.setVisible(scanning)
        self._cancel_button.setVisible(scanning)

    # Scanning -------------------------------------------------------------
    def start_scan(self, path: str) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        options = ScanOptions(workers=min(8, os.cpu_count() or 4))
        thread = ScanThread(path, options, self)
        thread.progressed.connect(self._on_progress)
        thread.completed.connect(self._on_completed)
        thread.failed.connect(self._on_failed)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        self._status_label.setText(f"Scanning {path} ...")
        self._update_actions(scanning=True)
        thread.start()

    def _cancel_scan(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.cancel()
            self._status_label.setText("Cancelling ...")

    def _on_progress(self, update: ScanProgress) -> None:
        self._status_label.setText(
            f"Scanning: {update.files_scanned:,} files, "
            f"{format_size(update.total_size)}, {update.scan_rate:,.0f} files/s"
        )

    def _on_completed(self, result: ScanResult) -> None:
        self._result = result
        self._expanded_entries.clear()
        self._tree_expansion_before_sort.clear()
        self._model.set_root(result.root)
        self._treemap.set_root(result.root)
        stats, largest, largest_for_extension = _file_details(result.root)
        self._largest_for_extension = largest_for_extension
        self._populate_types(result, stats)
        self._populate_largest(largest)
        self._reset_find_state(clear_feedback=True)
        root_index = self._model.index(0, 0, QModelIndex())
        self._tree.expand(root_index)
        self._tree.setCurrentIndex(root_index)
        self._status_label.setText(self._summary_text(result))
        self.setWindowTitle(f"KingDirSTAT - {result.root.path}")

    def _on_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Scan failed", message)
        self._status_label.setText(f"Scan failed: {message}")

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._update_actions(scanning=False)

    def _summary_text(self, result: ScanResult) -> str:
        parts = [
            f"{format_size(result.total_size)}",
            f"{result.files:,} files",
            f"{result.directories:,} directories",
            f"{result.duration:.1f}s",
        ]
        if result.errors:
            parts.append(f"{len(result.errors):,} unreadable")
        if result.cancelled:
            parts.append("cancelled (partial)")
        try:
            drive = drive_for_path(result.root.path)
            if drive.total_space:
                parts.append(
                    f"volume {drive.path} {format_size(drive.free_space)} free "
                    f"of {format_size(drive.total_space)}"
                )
        except OSError:
            pass
        return "  |  ".join(parts)

    # Extension list -------------------------------------------------------
    def _populate_types(self, result: ScanResult, stats: Sequence[FileTypeStat]) -> None:
        total = result.total_size
        self._types.setRowCount(len(stats))
        for row, item in enumerate(stats):
            swatch = QTableWidgetItem(item.extension)
            colour = TreemapWidget.color_for_entry(
                Entry(name=f"x{item.extension}", path="", is_dir=False, extension=item.extension)
            )
            swatch.setData(Qt.ItemDataRole.DecorationRole, QColor(colour))
            swatch.setData(Qt.ItemDataRole.UserRole, item.extension)
            self._types.setItem(row, 0, swatch)

            files = QTableWidgetItem(f"{item.files:,}")
            files.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._types.setItem(row, 1, files)

            size = QTableWidgetItem(format_size(item.size))
            size.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._types.setItem(row, 2, size)

            share = QTableWidgetItem(f"{item.size / total * 100:.1f}%" if total else "0.0%")
            share.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._types.setItem(row, 3, share)
        self._types.resizeColumnsToContents()

    def _populate_largest(self, entries: Sequence[Entry]) -> None:
        """Populate the previously bounded largest-files view."""

        self._largest_rows.clear()
        self._largest.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            name = QTableWidgetItem(entry.name)
            name.setData(Qt.ItemDataRole.UserRole, entry)
            self._largest.setItem(row, 0, name)
            self._largest.setItem(row, 1, QTableWidgetItem(format_size(entry.size)))
            self._largest.setItem(row, 2, QTableWidgetItem(_format_timestamp(entry.modified_time)))
            self._largest.setItem(row, 3, QTableWidgetItem(entry.path))
            self._largest_rows[id(entry)] = row
        self._largest.resizeColumnsToContents()

    def _type_selection_changed(self) -> None:
        if self._syncing or self._result is None:
            return
        rows = self._types.selectionModel().selectedRows()
        if not rows:
            return
        item = self._types.item(rows[0].row(), 0)
        if item is None:
            return
        extension = item.data(Qt.ItemDataRole.UserRole)
        largest = self._largest_for_extension.get(extension)
        if largest is not None:
            self._select_entry(largest, source="types")

    def _largest_selection_changed(self) -> None:
        if self._syncing:
            return
        rows = self._largest.selectionModel().selectedRows()
        if not rows:
            return
        item = self._largest.item(rows[0].row(), 0)
        entry = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(entry, Entry):
            self._select_entry(entry, source="largest")

    # Find ----------------------------------------------------------------
    def _focus_find(self) -> None:
        self._find_edit.setFocus()
        self._find_edit.selectAll()

    def _find_text_changed(self, _text: str) -> None:
        self._reset_find_state(clear_feedback=True)

    def _reset_find_state(self, *, clear_feedback: bool) -> None:
        self._find_query = ""
        self._find_iterator = None
        self._find_match_index = 0
        if clear_feedback:
            self._find_feedback.clear()

    def _matching_entries(self, query: str) -> Iterator[Entry]:
        result = self._result
        if result is None:
            return
        for entry in result.root.walk():
            if query in entry.name.casefold() or query in entry.path.casefold():
                yield entry

    def _find_next(self) -> None:
        if self._result is None:
            message = "Open a scan or snapshot first."
            self._find_feedback.setText(message)
            self._status_label.setText(message)
            return

        query = self._find_edit.text().strip().casefold()
        if not query:
            message = "Enter a name or path to find."
            self._find_feedback.setText(message)
            self._status_label.setText(message)
            self._focus_find()
            return

        if query != self._find_query or self._find_iterator is None:
            self._find_query = query
            self._find_iterator = self._matching_entries(query)
            self._find_match_index = 0

        wrapped = False
        try:
            entry = next(self._find_iterator)
        except StopIteration:
            if self._find_match_index == 0:
                message = f'No matches for "{self._find_edit.text().strip()}".'
                self._find_feedback.setText(message)
                self._status_label.setText(message)
                return
            self._find_iterator = self._matching_entries(query)
            self._find_match_index = 0
            wrapped = True
            try:
                entry = next(self._find_iterator)
            except StopIteration:
                message = f'No matches for "{self._find_edit.text().strip()}".'
                self._find_feedback.setText(message)
                self._status_label.setText(message)
                return

        self._find_match_index += 1
        self._select_entry(entry, source="find")
        suffix = " (wrapped)" if wrapped else ""
        self._find_feedback.setText(f"Match {self._find_match_index}{suffix}: {entry.name}")

    # Selection sync -------------------------------------------------------
    def _tree_expanded(self, index: QModelIndex) -> None:
        entry = self._model.entry_at(index)
        if entry is not None and entry.is_dir:
            self._expanded_entries[id(entry)] = entry

    def _tree_collapsed(self, index: QModelIndex) -> None:
        entry = self._model.entry_at(index)
        if entry is not None:
            self._expanded_entries.pop(id(entry), None)

    def _remember_tree_selection(self) -> None:
        self._tree_selection_before_sort = self._model.entry_at(self._tree.currentIndex())
        self._tree_expansion_before_sort = list(self._expanded_entries.values())

    def _restore_tree_selection(self) -> None:
        expanded = self._tree_expansion_before_sort
        self._tree_expansion_before_sort = []
        self._expanded_entries.clear()
        for expanded_entry in expanded:
            index = self._model.index_for_entry(expanded_entry)
            if index.isValid():
                self._tree.expand(index)

        entry = self._tree_selection_before_sort
        self._tree_selection_before_sort = None
        if entry is not None:
            self._select_entry(entry, source="sort")

    def _tree_selection_changed(self) -> None:
        if self._syncing:
            return
        entry = self._model.entry_at(self._tree.currentIndex())
        if entry is not None:
            self._select_entry(entry, source="tree")

    def _treemap_selection_changed(self, entry: object) -> None:
        if self._syncing or not isinstance(entry, Entry):
            return
        self._select_entry(entry, source="treemap")

    def _treemap_activated(self, entry: object) -> None:
        if isinstance(entry, Entry) and entry.is_dir:
            self._treemap.zoom_to(entry)

    def _select_entry(self, entry: Entry, *, source: str) -> None:
        self._syncing = True
        try:
            if source != "treemap":
                self._treemap.select_entry(entry)
            if source != "tree":
                index = self._model.index_for_entry(entry)
                if index.isValid():
                    parent = index.parent()
                    ancestors: list[QModelIndex] = []
                    while parent.isValid():
                        ancestors.append(parent)
                        parent = parent.parent()
                    for ancestor in reversed(ancestors):
                        self._tree.expand(ancestor)
                    self._tree.setCurrentIndex(index)
                    self._tree.scrollTo(index)
            if source != "largest":
                row = self._largest_rows.get(id(entry))
                if row is not None:
                    self._largest.selectRow(row)
            self._status_label.setText(f"{entry.path}  |  {format_size(entry.size)}")
        finally:
            self._syncing = False

    def _zoom_to_selection(self) -> None:
        entry = self._model.entry_at(self._tree.currentIndex())
        if entry is not None and entry.is_dir:
            self._treemap.zoom_to(entry)

    def _expand_largest_branch(self) -> None:
        index = self._model.index(0, 0, QModelIndex())
        for _ in range(12):
            if not index.isValid() or self._model.rowCount(index) == 0:
                break
            self._tree.expand(index)
            index = self._model.index(0, 0, index)
        if index.isValid():
            self._tree.setCurrentIndex(index)

    # Read-only context actions -------------------------------------------
    def _show_tree_menu(self, position: Any) -> None:
        index = self._tree.indexAt(position)
        entry = self._model.entry_at(index)
        if entry is None:
            return
        menu = QMenu(self)
        reveal = menu.addAction("Show in File Manager")
        copy = menu.addAction("Copy Path")
        zoom = menu.addAction("Zoom Treemap Here")
        zoom.setEnabled(entry.is_dir)
        chosen = menu.exec(self._tree.viewport().mapToGlobal(position))
        if chosen is reveal:
            self._reveal(entry)
        elif chosen is copy:
            QGuiApplication.clipboard().setText(entry.path)
            self._status_label.setText(f"Copied: {entry.path}")
        elif chosen is zoom:
            self._treemap.zoom_to(entry)

    def _reveal(self, entry: Entry) -> None:
        """Open the containing folder. Never modifies anything."""

        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", f"/select,{entry.path}"])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", entry.path])
            else:
                target = entry.path if entry.is_dir else os.path.dirname(entry.path)
                subprocess.Popen(["xdg-open", target])
        except OSError as exc:
            QMessageBox.warning(self, "Could not open file manager", str(exc))

    # File actions ---------------------------------------------------------
    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a folder to scan")
        if path:
            self.start_scan(path)

    def _choose_drive(self) -> None:
        selector = DriveSelector()
        drives = [drive for drive in selector.get_available_drives() if drive.is_accessible]
        if not drives:
            QMessageBox.information(self, "Open drive", "No accessible drives were found.")
            return
        labels = [selector.format_drive_display(drive) for drive in drives]
        selected, accepted = QInputDialog.getItem(
            self,
            "Open drive",
            "Drive:",
            labels,
            0,
            False,
        )
        if accepted and selected in labels:
            self.start_scan(drives[labels.index(selected)].path)

    def _choose_snapshot(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open snapshot", filter="KingDirSTAT snapshot (*.json);;All files (*)"
        )
        if path:
            self.load_snapshot(path)

    def load_snapshot(self, path: str) -> None:
        try:
            result = read_snapshot(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not open snapshot", str(exc))
            return
        self._on_completed(result)
        self._update_actions(scanning=False)
        self._status_label.setText(f"Snapshot: {path}  |  {self._summary_text(result)}")

    def _save_snapshot(self) -> None:
        if self._result is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save snapshot", filter="KingDirSTAT snapshot (*.json)"
        )
        if not path:
            return
        try:
            write_snapshot(self._result, path)
        except OSError as exc:
            QMessageBox.warning(self, "Could not save snapshot", str(exc))
            return
        self._status_label.setText(f"Saved snapshot: {path}")

    def _export_csv(self) -> None:
        if self._result is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", filter="CSV (*.csv)")
        if not path:
            return
        try:
            write_csv(self._result, path)
        except OSError as exc:
            QMessageBox.warning(self, "Could not export CSV", str(exc))
            return
        self._status_label.setText(f"Exported CSV: {path}")

    def _rescan(self) -> None:
        if self._result is not None:
            self.start_scan(self._result.root.path)

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About KingDirSTAT",
            "KingDirSTAT reports disk usage and never modifies your files.\n\n"
            "Directory pane, extension list, and treemap stay in sync. "
            "Use the kingdirstat command for scripted reports.",
        )

    def closeEvent(self, event: Any) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.cancel()
            self._thread.wait(3000)
        super().closeEvent(event)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kingdirstat-gui", description="KingDirSTAT desktop UI")
    parser.add_argument("path", nargs="?", help="folder to scan on startup")
    parser.add_argument("--snapshot", help="open a saved snapshot instead of scanning")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = MainWindow()
    window.show()
    if args.snapshot:
        window.load_snapshot(args.snapshot)
    elif args.path:
        window.start_scan(args.path)
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
