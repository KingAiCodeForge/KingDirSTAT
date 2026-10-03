"""Interactive, dependency-free (apart from Qt) squarified treemap widget."""

from __future__ import annotations

import zlib
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from .models import Entry


@dataclass(slots=True)
class TreemapCell:
    entry: Entry
    rectangle: QRectF
    depth: int


class TreemapWidget(QWidget):
    selection_changed = Signal(object)
    item_activated = Signal(object)
    view_root_changed = Signal(object)

    _PALETTE = (
        "#38BDF8",
        "#2DD4BF",
        "#A78BFA",
        "#FB7185",
        "#FBBF24",
        "#60A5FA",
        "#34D399",
        "#F472B6",
        "#818CF8",
        "#F97316",
        "#22D3EE",
        "#C084FC",
    )
    _MAX_CELLS = 20_000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root: Entry | None = None
        self._view_root: Entry | None = None
        self._history: list[Entry] = []
        self._cells: list[TreemapCell] = []
        self._selected: Entry | None = None
        self._hovered: TreemapCell | None = None
        self._cushion_shading = True
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(320, 220)
        self.setAccessibleName("Disk usage treemap")
        self.setAccessibleDescription(
            "Rectangles represent files and folders; larger rectangles use more disk space."
        )

    @property
    def view_root(self) -> Entry | None:
        return self._view_root

    @property
    def selected_entry(self) -> Entry | None:
        return self._selected

    def set_root(self, root: Entry | None) -> None:
        self._root = root
        self._view_root = root
        self._history.clear()
        self._selected = None
        self._rebuild()

    def select_entry(self, entry: Entry | None, *, emit: bool = False) -> None:
        self._selected = entry
        self.update()
        if emit and entry is not None:
            self.selection_changed.emit(entry)

    def zoom_to(self, entry: Entry) -> None:
        if not entry.is_dir or not entry.children or entry is self._view_root:
            return
        if self._view_root is not None:
            self._history.append(self._view_root)
        self._view_root = entry
        self._selected = entry
        self._rebuild()
        self.view_root_changed.emit(entry)

    def zoom_out(self) -> None:
        if not self._history:
            return
        self._view_root = self._history.pop()
        self._rebuild()
        if self._view_root is not None:
            self.view_root_changed.emit(self._view_root)

    def reset_zoom(self) -> None:
        if self._root is None:
            return
        self._view_root = self._root
        self._history.clear()
        self._rebuild()
        self.view_root_changed.emit(self._root)

    def _rebuild(self) -> None:
        self._cells.clear()
        if self._view_root is not None and self.width() > 2 and self.height() > 2:
            bounds = QRectF(2.0, 2.0, self.width() - 4.0, self.height() - 4.0)
            self._layout_children(self._view_root, bounds, 0)
        self.update()

    def _layout_children(self, parent: Entry, bounds: QRectF, depth: int) -> None:
        if len(self._cells) >= self._MAX_CELLS or bounds.width() < 2 or bounds.height() < 2:
            return
        children = [child for child in parent.children if child.size > 0]
        if not children:
            return
        for child, rectangle in self._squarify(children, bounds):
            if len(self._cells) >= self._MAX_CELLS:
                break
            if rectangle.width() < 0.75 or rectangle.height() < 0.75:
                continue
            cell = TreemapCell(child, rectangle, depth)
            self._cells.append(cell)
            if (
                child.is_dir
                and child.children
                and depth < 9
                and rectangle.width() * rectangle.height() > 900
            ):
                inset = min(3.0, max(1.0, min(rectangle.width(), rectangle.height()) * 0.015))
                child_bounds = rectangle.adjusted(inset, 17.0, -inset, -inset)
                if child_bounds.isValid():
                    self._layout_children(child, child_bounds, depth + 1)

    @staticmethod
    def _squarify(entries: list[Entry], bounds: QRectF) -> list[tuple[Entry, QRectF]]:
        total = sum(entry.size for entry in entries)
        area = bounds.width() * bounds.height()
        if total <= 0 or area <= 0:
            return []
        remaining = [(entry, entry.size / total * area) for entry in entries]
        result: list[tuple[Entry, QRectF]] = []
        row: list[tuple[Entry, float]] = []
        rectangle = QRectF(bounds)

        while remaining:
            candidate = remaining[0]
            side = max(0.001, min(rectangle.width(), rectangle.height()))
            if not row or TreemapWidget._worst([*row, candidate], side) <= TreemapWidget._worst(
                row, side
            ):
                row.append(remaining.pop(0))
                continue
            rectangle = TreemapWidget._place_row(row, rectangle, result)
            row = []
        if row:
            TreemapWidget._place_row(row, rectangle, result)
        return result

    @staticmethod
    def _worst(row: list[tuple[Entry, float]], side: float) -> float:
        if not row:
            return float("inf")
        values = [value for _, value in row if value > 0]
        if not values:
            return float("inf")
        total = sum(values)
        side_squared = side * side
        return max(
            side_squared * max(values) / (total * total),
            (total * total) / (side_squared * min(values)),
        )

    @staticmethod
    def _place_row(
        row: list[tuple[Entry, float]],
        bounds: QRectF,
        result: list[tuple[Entry, QRectF]],
    ) -> QRectF:
        row_area = sum(value for _, value in row)
        if row_area <= 0:
            return bounds
        x, y, width, height = bounds.x(), bounds.y(), bounds.width(), bounds.height()
        if width >= height:
            strip_width = min(width, row_area / max(height, 0.001))
            cursor = y
            for entry, value in row:
                cell_height = value / max(strip_width, 0.001)
                result.append((entry, QRectF(x, cursor, strip_width, cell_height)))
                cursor += cell_height
            return QRectF(x + strip_width, y, max(0.0, width - strip_width), height)
        strip_height = min(height, row_area / max(width, 0.001))
        cursor = x
        for entry, value in row:
            cell_width = value / max(strip_height, 0.001)
            result.append((entry, QRectF(cursor, y, cell_width, strip_height)))
            cursor += cell_width
        return QRectF(x, y + strip_height, width, max(0.0, height - strip_height))

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#09111F"))
        if not self._cells:
            painter.setPen(QColor("#8091A7"))
            painter.setFont(QFont("Segoe UI", 11))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "Scan a folder to build the treemap"
            )
            return

        painter.setFont(QFont("Segoe UI", 8))
        metrics = painter.fontMetrics()
        for cell in self._cells:
            rectangle = cell.rectangle.adjusted(0.5, 0.5, -0.5, -0.5)
            color = self.color_for_entry(cell.entry)
            if cell.entry.is_dir:
                color = color.darker(135)
            if self._cushion_shading and rectangle.width() >= 3 and rectangle.height() >= 3:
                painter.fillRect(rectangle, self._cushion_brush(rectangle, color))
            else:
                painter.fillRect(rectangle, color)
            border = QColor("#08101D")
            border.setAlpha(180)
            painter.setPen(QPen(border, 1.0))
            painter.drawRect(rectangle)

            if cell.entry is self._selected:
                painter.setPen(QPen(QColor("#F8FAFC"), 2.5))
                painter.drawRect(rectangle.adjusted(1.5, 1.5, -1.5, -1.5))
            elif self._hovered is cell:
                painter.setPen(QPen(QColor("#7DD3FC"), 2.0))
                painter.drawRect(rectangle.adjusted(1.0, 1.0, -1.0, -1.0))

            if rectangle.width() >= 76 and rectangle.height() >= 28:
                painter.setPen(QColor("#F8FAFC"))
                label = metrics.elidedText(
                    cell.entry.name,
                    Qt.TextElideMode.ElideMiddle,
                    int(rectangle.width() - 10),
                )
                painter.drawText(
                    rectangle.adjusted(5, 4, -5, -4),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                    label,
                )

    @property
    def cushion_shading(self) -> bool:
        return self._cushion_shading

    def set_cushion_shading(self, enabled: bool) -> None:
        """Toggle the shaded, rounded look. Flat fill is faster on huge trees."""

        if self._cushion_shading != enabled:
            self._cushion_shading = enabled
            self.update()

    @staticmethod
    def _cushion_brush(rectangle: QRectF, color: QColor) -> QBrush:
        """Approximate a cushion surface with a lit highlight and a shaded edge.

        WinDirStat computes a real per-pixel cushion normal. A radial gradient
        reproduces the look closely while staying fast enough to repaint tens of
        thousands of cells on a resize.
        """

        gradient = QRadialGradient(
            rectangle.left() + rectangle.width() * 0.32,
            rectangle.top() + rectangle.height() * 0.26,
            max(rectangle.width(), rectangle.height()) * 0.95,
        )
        gradient.setColorAt(0.0, color.lighter(150))
        gradient.setColorAt(0.55, color)
        gradient.setColorAt(1.0, color.darker(160))
        return QBrush(gradient)

    @classmethod
    def color_for_entry(cls, entry: Entry) -> QColor:
        key = entry.extension or ("folder" if entry.is_dir else "no-extension")
        index = zlib.crc32(key.encode("utf-8")) % len(cls._PALETTE)
        return QColor(cls._PALETTE[index])

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        cell = self._cell_at(event.position())
        if cell is not self._hovered:
            self._hovered = cell
            self.update()
        if cell is not None:
            percentage = (
                cell.entry.size / self._view_root.size * 100
                if self._view_root is not None and self._view_root.size
                else 0.0
            )
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"{cell.entry.path}\n{_format_size(cell.entry.size)} ({percentage:.2f}%)",
                self,
            )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: object) -> None:
        self._hovered = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)  # type: ignore[arg-type]

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            cell = self._cell_at(event.position())
            if cell is not None:
                self.select_entry(cell.entry, emit=True)
                self.setFocus()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        cell = self._cell_at(event.position())
        if cell is not None:
            if cell.entry.is_dir:
                self.zoom_to(cell.entry)
            else:
                self.item_activated.emit(cell.entry)
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in {Qt.Key.Key_Backspace, Qt.Key.Key_Left}:
            self.zoom_out()
            event.accept()
            return
        if (
            event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}
            and self._selected
        ):
            if self._selected.is_dir:
                self.zoom_to(self._selected)
            else:
                self.item_activated.emit(self._selected)
            event.accept()
            return
        if event.key() in {Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_Up} and self._cells:
            entries = [cell.entry for cell in self._cells]
            try:
                index = entries.index(self._selected) if self._selected is not None else -1
            except ValueError:
                index = -1
            direction = -1 if event.key() == Qt.Key.Key_Up else 1
            self.select_entry(entries[(index + direction) % len(entries)], emit=True)
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._rebuild()

    def _cell_at(self, position: QPointF) -> TreemapCell | None:
        for cell in reversed(self._cells):
            if cell.rectangle.contains(position):
                return cell
        return None


def _format_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(size) < 1024 or unit == "PiB":
            return f"{int(size)} B" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PiB"
