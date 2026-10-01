"""可视化坐标 / 区块选择器 + 方块调色板编排器。

这是一个纯 Qt 画布（不依赖存档写入），职责单一：

* **俯视图**：按区块画出网格，可平移 / 缩放，鼠标悬停显示区块与世界坐标；
* **标记**：把实体、容器、玩家、出生点、地图中心画成不同形状的标记；
* **选择**：框选区块范围 / 点选单个方块格；
* **编排**：配一个调色板（方块 ID 列表），在格子上「画」方块，支持
  画笔 / 橡皮 / 填充 / 吸管，撤销栈独立于存档；
* **导出**：选区 → `/setblock`、`/fill` 段、蓝图对象、或区域区块写入。

与存档的耦合只通过构造时传入的「标记列表」，方便单测。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QPainter, QPen, QPolygonF,
)
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton,
    QSizePolicy, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from app.core.nbt_bridge import find_entities
from app.core.snbt import SnbtKind
from app.ui.theme import (
    FONTS, MARGINS, SPACING, colors, muted_style,
)
from app.ui.world_common import SectionBox

#: 工具模式
TOOL_PAINT = "paint"
TOOL_ERASE = "erase"
TOOL_FILL = "fill"
TOOL_PICK = "pick"
TOOL_SELECT = "select"

#: 每个区块的方块格数（1.21 一个区块 16×16 列）
CHUNK_SIZE = 16

#: 标记类型 → (形状, 颜色 key)
MARKER_STYLES = {
    "entity": ("circle", "warn"),
    "container": ("square", "accent"),
    "block_entity": ("square", "accent"),
    "player": ("star", "ok"),
    "spawn": ("cross", "error"),
    "map": ("diamond", "muted"),
}


@dataclass
class MapMarker:
    """俯视图上的一个标记。"""

    x: float
    z: float
    kind: str = "entity"
    label: str = ""
    detail: str = ""
    dimension: str = ""


@dataclass
class BlockGrid:
    """一块「稀疏方块画布」：只记录被编辑过的格子。"""

    cells: dict[tuple[int, int, int], str] = field(default_factory=dict)

    def set(self, x: int, y: int, z: int, block: str) -> None:
        self.cells[(int(x), int(y), int(z))] = block

    def erase(self, x: int, y: int, z: int) -> None:
        self.cells.pop((int(x), int(y), int(z)), None)

    def get(self, x: int, y: int, z: int) -> str:
        return self.cells.get((int(x), int(y), int(z)), "")

    def __len__(self) -> int:
        return len(self.cells)

    def palette(self) -> list[str]:
        seen: list[str] = []
        for block in self.cells.values():
            if block and block not in seen:
                seen.append(block)
        return seen

    def bounds(self) -> tuple[tuple[int, int, int], tuple[int, int, int]] | None:
        if not self.cells:
            return None
        xs = [key[0] for key in self.cells]
        ys = [key[1] for key in self.cells]
        zs = [key[2] for key in self.cells]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def by_layer(self, y: int) -> dict[tuple[int, int], str]:
        return {(x, z): block for (x, yy, z), block in self.cells.items()
                if yy == y}

    def clone(self) -> "BlockGrid":
        return BlockGrid(cells=dict(self.cells))

    # ---- 导出 ----

    def to_commands(self, *, mode: str = "setblock",
                    limit: int = 4096) -> list[str]:
        if not self.cells:
            return []
        if mode == "fill":
            return self._to_fill_commands()
        commands: list[str] = []
        for (x, y, z), block in sorted(self.cells.items(),
                                       key=lambda kv: (kv[0][1], kv[0][2],
                                                       kv[0][0]))[:limit]:
            commands.append(f"setblock {x} {y} {z} {block} replace")
        if len(self.cells) > limit:
            commands.append(f"# 还有 {len(self.cells) - limit} 个方块未生成")
        return commands

    def _to_fill_commands(self) -> list[str]:
        """按 Y 层 + 矩形分解，生成尽量少的 /fill。"""
        commands: list[str] = []
        layers: dict[int, dict[tuple[int, int], str]] = {}
        for (x, y, z), block in self.cells.items():
            layers.setdefault(y, {})[(x, z)] = block
        for y in sorted(layers):
            grid = layers[y]
            for block in sorted(set(grid.values())):
                cells = {key for key, value in grid.items() if value == block}
                commands.extend(_rect_decompose(cells, y, block))
        return commands

    def to_blueprint(self, name: str = "画布选区"):
        """转成蓝图对象（复用 app.core.blueprint）。"""
        from app.core.blueprint import Blueprint, BlueprintBlock
        bounds = self.bounds()
        blueprint = Blueprint(name=name)
        if bounds is None:
            return blueprint
        (x0, y0, z0), (x1, y1, z1) = bounds
        for (x, y, z), block in self.cells.items():
            blueprint.blocks.append(
                BlueprintBlock((x - x0, y - y0, z - z0), block))
        blueprint.size = (x1 - x0 + 1, y1 - y0 + 1, z1 - z0 + 1)
        return blueprint


def _rect_decompose(cells: set[tuple[int, int]], y: int,
                    block: str) -> list[str]:
    """把一层的同种方块贪心切成矩形，产出 /fill 命令。"""
    remaining = set(cells)
    out: list[str] = []
    while remaining:
        x0, z0 = min(remaining, key=lambda c: (c[1], c[0]))
        width = 1
        while (x0 + width, z0) in remaining:
            width += 1
        depth = 1
        while all((x0 + dx, z0 + depth) in remaining
                  for dx in range(width)):
            depth += 1
        for dx in range(width):
            for dz in range(depth):
                remaining.discard((x0 + dx, z0 + dz))
        if width == 1 and depth == 1:
            out.append(f"setblock {x0} {y} {z0} {block} replace")
        else:
            out.append(f"fill {x0} {y} {z0} "
                       f"{x0 + width - 1} {y} {z0 + depth - 1} {block} replace")
    return out


class CoordinateCanvas(QFrame):
    """2D 俯视图：区块网格 + 标记 + 方块编辑。"""

    #: 选中的格子（世界坐标）发生变化
    cellHovered = Signal(int, int, int)
    #: 点选了方块格
    cellClicked = Signal(int, int, int)
    #: 选区（区块范围）变化：x0, z0, x1, z1
    chunkSelectionChanged = Signal(int, int, int, int)
    #: 画布内容变化（方块被编辑）
    gridChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

        self.grid = BlockGrid()
        self.markers: list[MapMarker] = []
        self.layer = 64
        self.tool = TOOL_PAINT
        self.brush = "minecraft:stone"
        self.zoom = 1.0
        self._offset = QPointF(0.0, 0.0)
        self._hover: tuple[int, int, int] | None = None
        self._anchor: tuple[int, int] | None = None
        self._chunk_sel: tuple[int, int, int, int] | None = None
        self._dragging = False
        self._panning = False
        self._last_mouse: QPoint | None = None
        self._undo: list[BlockGrid] = []
        self._redo: list[BlockGrid] = []
        self.show_grid = True
        self.show_markers = True

    # ---- 缩放 / 平移 ----

    def set_zoom(self, zoom: float, *, anchor: QPointF | None = None) -> None:
        new = max(0.25, min(8.0, float(zoom)))
        if anchor is None:
            anchor = QPointF(self.width() / 2.0, self.height() / 2.0)
        factor = new / self.zoom
        self._offset = anchor - (anchor - self._offset) * factor
        self.zoom = new
        self.update()

    def center_on(self, x: float, z: float) -> None:
        self._offset = QPointF(self.width() / 2.0 - x * self.zoom,
                               self.height() / 2.0 - z * self.zoom)
        self.update()

    def fit_to(self, padding: int = 24) -> None:
        """把选区 / 标记 / 画布内容缩放到刚好铺满。"""
        points: list[tuple[float, float]] = []
        if self.grid.cells:
            for (x, _y, z) in self.grid.cells:
                points.append((x, z))
        points.extend((m.x, m.z) for m in self.markers)
        if not points:
            points = [(0.0, 0.0), (64.0, 64.0)]
        xs = [p[0] for p in points]
        zs = [p[1] for p in points]
        width = max(16.0, max(xs) - min(xs) + 16)
        depth = max(16.0, max(zs) - min(zs) + 16)
        available_w = max(64, self.width() - padding * 2)
        available_h = max(64, self.height() - padding * 2)
        self.zoom = max(0.25, min(8.0, min(available_w / width,
                                          available_h / depth)))
        cx = (min(xs) + max(xs)) / 2.0
        cz = (min(zs) + max(zs)) / 2.0
        self.center_on(cx, cz)

    # ---- 坐标换算 ----

    def _to_screen(self, x: float, z: float) -> QPointF:
        return QPointF(x * self.zoom + self._offset.x(),
                       z * self.zoom + self._offset.y())

    def _to_world(self, point: QPointF) -> tuple[float, float]:
        return ((point.x() - self._offset.x()) / self.zoom,
                (point.y() - self._offset.y()) / self.zoom)

    # ---- 画布状态 ----

    def snapshot(self) -> None:
        self._undo.append(self.grid.clone())
        del self._undo[:-100]
        self._redo.clear()

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self.grid.clone())
        self.grid = self._undo.pop()
        self.gridChanged.emit()
        self.update()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self.grid.clone())
        self.grid = self._redo.pop()
        self.gridChanged.emit()
        self.update()
        return True

    def clear_grid(self) -> None:
        self.snapshot()
        self.grid = BlockGrid()
        self.gridChanged.emit()
        self.update()

    def set_layer(self, y: int) -> None:
        self.layer = int(y)
        self.update()

    def selection(self) -> tuple[int, int, int, int] | None:
        return self._chunk_sel

    # ---- 编辑 ----

    def _apply_tool(self, x: int, z: int) -> None:
        y = self.layer
        if self.tool == TOOL_PAINT:
            self.grid.set(x, y, z, self.brush)
        elif self.tool == TOOL_ERASE:
            self.grid.erase(x, y, z)
        elif self.tool == TOOL_PICK:
            block = self.grid.get(x, y, z)
            if block:
                self.brush = block
        elif self.tool == TOOL_FILL:
            existing = self.grid.get(x, y, z)
            if existing == self.brush:
                return
            self._flood_fill(x, z, existing, self.brush)
        self.gridChanged.emit()

    def _flood_fill(self, x: int, z: int, target: str, block: str,
                    limit: int = 20000) -> None:
        """同一 Y 层、同一原始方块的连通区域替换（四连通）。"""
        start = (x, z)
        if target == block or self.grid.get(x, self.layer, z) != target:
            return
        stack = [start]
        seen = {start}
        while stack and len(seen) < limit:
            cx, cz = stack.pop()
            self.grid.set(cx, self.layer, cz, block)
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                key = (cx + dx, cz + dz)
                if key in seen:
                    continue
                if self.grid.get(key[0], self.layer, key[1]) == target:
                    seen.add(key)
                    stack.append(key)

    # ---- 鼠标 ----

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._last_mouse = event.position().toPoint()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x, z = self._to_world(event.position())
        if self.tool == TOOL_SELECT:
            self._dragging = True
            self._anchor = (int(math.floor(x / CHUNK_SIZE)),
                            int(math.floor(z / CHUNK_SIZE)))
            self._chunk_sel = (*self._anchor, *self._anchor)
            self._emit_selection()
            self.update()
            return
        self.snapshot()
        self._dragging = True
        self._apply_tool(int(math.floor(x)), int(math.floor(z)))
        self.cellClicked.emit(int(math.floor(x)), self.layer,
                              int(math.floor(z)))
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        position = event.position()
        x, z = self._to_world(position)
        cell = (int(math.floor(x)), self.layer, int(math.floor(z)))
        if cell != self._hover:
            self._hover = cell
            self.cellHovered.emit(*cell)
        if self._panning and self._last_mouse is not None:
            delta = position.toPoint() - self._last_mouse
            self._offset += QPointF(delta.x(), delta.y())
            self._last_mouse = position.toPoint()
            self.update()
            return
        if self._dragging:
            if self.tool == TOOL_SELECT and self._anchor is not None:
                current = (int(math.floor(x / CHUNK_SIZE)),
                           int(math.floor(z / CHUNK_SIZE)))
                self._chunk_sel = (min(self._anchor[0], current[0]),
                                   min(self._anchor[1], current[1]),
                                   max(self._anchor[0], current[0]),
                                   max(self._anchor[1], current[1]))
                self._emit_selection()
            else:
                self._apply_tool(cell[0], cell[2])
            self.update()
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._dragging = False
        self._panning = False
        self._last_mouse = None
        self.update()

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if not delta:
            return
        factor = 1.15 if delta > 0 else 1 / 1.15
        self.set_zoom(self.zoom * factor, anchor=event.position())
        event.accept()

    def _emit_selection(self) -> None:
        if self._chunk_sel is None:
            return
        self.chunkSelectionChanged.emit(*self._chunk_sel)

    # ---- 绘制 ----

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = colors(self)
        painter.fillRect(self.rect(), QColor(palette["surface_alt"]))

        if self.show_grid:
            self._paint_chunk_grid(painter, palette)
        self._paint_cells(painter, palette)
        if self.show_markers:
            self._paint_markers(painter, palette)
        self._paint_selection(painter, palette)
        self._paint_hover(painter, palette)
        self._paint_legend(painter, palette)
        painter.end()

    def _paint_chunk_grid(self, painter: QPainter, palette: dict) -> None:
        top_left = self._to_world(QPointF(0.0, 0.0))
        bottom_right = self._to_world(
            QPointF(float(self.width()), float(self.height())))
        first_chunk_x = int(math.floor(top_left[0] / CHUNK_SIZE))
        last_chunk_x = int(math.floor(bottom_right[0] / CHUNK_SIZE))
        first_chunk_z = int(math.floor(top_left[1] / CHUNK_SIZE))
        last_chunk_z = int(math.floor(bottom_right[1] / CHUNK_SIZE))
        if (last_chunk_x - first_chunk_x) * (last_chunk_z - first_chunk_z) \
                > 20000:                                    # pragma: no cover
            return

        if self.zoom >= 1.0:
            painter.setPen(QPen(QColor(palette["border"]), 1, Qt.PenStyle.DotLine))
            for chunk_x in range(first_chunk_x, last_chunk_x + 2):
                x = self._to_screen(chunk_x * CHUNK_SIZE, 0).x()
                painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            for chunk_z in range(first_chunk_z, last_chunk_z + 2):
                y = self._to_screen(0, chunk_z * CHUNK_SIZE).y()
                painter.drawLine(QPointF(0, y), QPointF(self.width(), y))

        painter.setPen(QPen(QColor(palette["muted"]), 1))
        font = QFont()
        font.setPointSize(FONTS["label"])
        painter.setFont(font)
        for chunk_x in range(first_chunk_x, last_chunk_x + 1):
            for chunk_z in range(first_chunk_z, last_chunk_z + 1):
                center = self._to_screen(
                    (chunk_x + 0.5) * CHUNK_SIZE,
                    (chunk_z + 0.5) * CHUNK_SIZE)
                if 0 <= center.x() <= self.width() \
                        and 0 <= center.y() <= self.height():
                    painter.drawText(
                        QPointF(center.x() - 22, center.y() - 14),
                        f"{chunk_x},{chunk_z}")

        # 世界原点十字
        origin = self._to_screen(0, 0)
        painter.setPen(QPen(QColor(palette["error"]), 1))
        painter.drawLine(QPointF(origin.x() - 8, origin.y()),
                         QPointF(origin.x() + 8, origin.y()))
        painter.drawLine(QPointF(origin.x(), origin.y() - 8),
                         QPointF(origin.x(), origin.y() + 8))

    def _paint_cells(self, painter: QPainter, palette: dict) -> None:
        if not self.grid.cells:
            return
        layer = self.grid.by_layer(self.layer)
        if not layer:
            return
        size = max(1.0, self.zoom)
        brush = QBrush(QColor(palette["accent"]))
        for (x, z) in layer:
            point = self._to_screen(x, z)
            if point.x() < -size or point.y() < -size \
                    or point.x() > self.width() or point.y() > self.height():
                continue
            painter.fillRect(QRectF(point.x(), point.y(), size, size), brush)
        # 其它层的方块用淡色提示，避免「看不到东西」
        other = 0
        for (x, y, z) in self.grid.cells:
            if y == self.layer:
                continue
            other += 1
            point = self._to_screen(x, z)
            if not (0 <= point.x() <= self.width()
                    and 0 <= point.y() <= self.height()):
                continue
            painter.fillRect(
                QRectF(point.x(), point.y(), size, size),
                QBrush(QColor(124, 124, 124, 60)))
        _ = other

    def _paint_markers(self, painter: QPainter, palette: dict) -> None:
        for marker in self.markers:
            style, color_key = MARKER_STYLES.get(marker.kind,
                                                 ("circle", "muted"))
            point = self._to_screen(marker.x, marker.z)
            if not (-40 <= point.x() <= self.width() + 40
                    and -40 <= point.y() <= self.height() + 40):
                continue
            color = QColor(palette.get(color_key, palette["muted"]))
            painter.setPen(QPen(color, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            self._draw_marker_shape(painter, style, point)
            if marker.label and self.zoom >= 0.8:
                font = QFont()
                font.setPointSize(FONTS["label"])
                painter.setFont(font)
                painter.drawText(QPointF(point.x() + 8, point.y() - 6),
                                 marker.label)

    def _draw_marker_shape(self, painter: QPainter, style: str,
                           point: QPointF) -> None:
        if style == "square":
            painter.drawRect(QRectF(point.x() - 4, point.y() - 4, 8, 8))
        elif style == "star":
            polygon = QPolygonF()
            for index in range(10):
                radius = 6.0 if index % 2 == 0 else 2.5
                angle = math.pi * index / 5.0 - math.pi / 2
                polygon.append(QPointF(point.x() + radius * math.cos(angle),
                                       point.y() + radius * math.sin(angle)))
            painter.drawPolygon(polygon)
        elif style == "cross":
            painter.drawLine(QPointF(point.x() - 5, point.y() - 5),
                             QPointF(point.x() + 5, point.y() + 5))
            painter.drawLine(QPointF(point.x() - 5, point.y() + 5),
                             QPointF(point.x() + 5, point.y() - 5))
        elif style == "diamond":
            polygon = QPolygonF([QPointF(point.x(), point.y() - 5),
                                 QPointF(point.x() + 5, point.y()),
                                 QPointF(point.x(), point.y() + 5),
                                 QPointF(point.x() - 5, point.y())])
            painter.drawPolygon(polygon)
        else:
            painter.drawEllipse(point, 4.0, 4.0)

    def _paint_selection(self, painter: QPainter, palette: dict) -> None:
        if self._chunk_sel is None:
            return
        x0, z0, x1, z1 = self._chunk_sel
        top_left = self._to_screen(x0 * CHUNK_SIZE, z0 * CHUNK_SIZE)
        bottom_right = self._to_screen((x1 + 1) * CHUNK_SIZE,
                                       (z1 + 1) * CHUNK_SIZE)
        rect = QRectF(top_left, bottom_right)
        painter.setBrush(QBrush(QColor(31, 111, 235, 40)))
        painter.setPen(QPen(QColor(palette["accent"]), 2,
                            Qt.PenStyle.DashLine))
        painter.drawRect(rect)

    def _paint_hover(self, painter: QPainter, palette: dict) -> None:
        if self._hover is None:
            return
        x, _y, z = self._hover
        size = max(1.0, self.zoom)
        point = self._to_screen(x, z)
        painter.setPen(QPen(QColor(palette["ok"]), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(point.x(), point.y(), size, size))

    def _paint_legend(self, painter: QPainter, palette: dict) -> None:
        text = (f"Y={self.layer}  ·  缩放 {self.zoom:.2f}x  ·  "
                f"已编排 {len(self.grid)} 格  ·  标记 {len(self.markers)} 个")
        if self._hover is not None:
            x, y, z = self._hover
            chunk = (int(math.floor(x / CHUNK_SIZE)),
                     int(math.floor(z / CHUNK_SIZE)))
            text += (f"  ·  坐标 {x} {y} {z}"
                     f"  ·  区块 {chunk[0]},{chunk[1]}")
        font = QFont()
        font.setPointSize(FONTS["label"])
        painter.setFont(font)
        painter.setPen(QPen(QColor(palette["muted"]), 1))
        metrics = QFontMetrics(font)
        rect = metrics.boundingRect(text)
        painter.fillRect(QRect(6, self.height() - rect.height() - 12,
                               rect.width() + 12, rect.height() + 8),
                         QColor(palette["surface"]))
        painter.drawText(QPointF(12, self.height() - 10), text)


class CreativeTabMixin:
    """「可视化编排」页签：坐标/区块选择器 + 方块调色板 + 导出。"""

    def _build_creative_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(*MARGINS["page"])
        outer.setSpacing(SPACING["sm"])

        box = SectionBox(
            "可视化坐标 / 区块选择器",
            "拖动平移、滚轮缩放；「选择」工具框选区块范围，"
            "「画/擦/填充/吸管」在格子上编排方块；"
            "导出成命令、蓝图或写入区域区块。")

        # 画布要先建：工具条上的按钮要连它的方法
        self.canvas = CoordinateCanvas()
        self.canvas.cellHovered.connect(self._on_canvas_hover)
        self.canvas.gridChanged.connect(self._on_canvas_changed)
        self.canvas.chunkSelectionChanged.connect(self._on_canvas_selection)

        box.add_widget(self._build_canvas_toolbar())
        box.add_widget(self.canvas, 1)
        box.add_widget(self._build_canvas_actions())
        outer.addWidget(box, 1)
        return page

    def _build_canvas_toolbar(self) -> QWidget:
        holder = QWidget()
        rows = QVBoxLayout(holder)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(SPACING["xs"])

        first = QHBoxLayout()
        first.setSpacing(SPACING["xs"])
        first.addWidget(QLabel("工具"))
        self.canvas_tool = QComboBox()
        for key, label in ((TOOL_SELECT, "选择（框选区块）"),
                           (TOOL_PAINT, "画方块"),
                           (TOOL_ERASE, "擦除"),
                           (TOOL_FILL, "同层填充"),
                           (TOOL_PICK, "吸管")):
            self.canvas_tool.addItem(label, key)
        self.canvas_tool.setCurrentIndex(1)
        self.canvas_tool.currentIndexChanged.connect(self._on_canvas_tool)
        first.addWidget(self.canvas_tool)

        first.addWidget(QLabel("Y 层"))
        self.canvas_layer = QSpinBox()
        self.canvas_layer.setRange(-64, 320)
        self.canvas_layer.setValue(64)
        self.canvas_layer.valueChanged.connect(
            lambda value: self.canvas.set_layer(value))
        first.addWidget(self.canvas_layer)

        first.addWidget(QLabel("画笔"))
        self.canvas_brush = QComboBox()
        self.canvas_brush.setEditable(True)
        self.canvas_brush.setMinimumWidth(200)
        for block in self._palette_blocks():
            self.canvas_brush.addItem(block)
        self.canvas_brush.setCurrentText("minecraft:stone")
        self.canvas_brush.currentTextChanged.connect(
            lambda text: setattr(self.canvas, "brush", text.strip()))
        first.addWidget(self.canvas_brush, 1)
        rows.addLayout(first)

        second = QHBoxLayout()
        second.setSpacing(SPACING["xs"])
        for label, slot in (("缩小", lambda: self.canvas.set_zoom(
            self.canvas.zoom / 1.25)),
                ("放大", lambda: self.canvas.set_zoom(
                    self.canvas.zoom * 1.25)),
                ("适应视野", self.canvas.fit_to),
                ("回到原点", lambda: self.canvas.center_on(0, 0))):
            button = QToolButton()
            button.setText(label)
            button.setAutoRaise(True)
            button.clicked.connect(slot)
            second.addWidget(button)
        self.canvas_show_grid = QPushButton("区块网格")
        self.canvas_show_grid.setCheckable(True)
        self.canvas_show_grid.setChecked(True)
        self.canvas_show_grid.toggled.connect(self._on_toggle_grid)
        second.addWidget(self.canvas_show_grid)
        self.canvas_show_markers = QPushButton("标记")
        self.canvas_show_markers.setCheckable(True)
        self.canvas_show_markers.setChecked(True)
        self.canvas_show_markers.toggled.connect(self._on_toggle_markers)
        second.addWidget(self.canvas_show_markers)
        self.canvas_undo = QPushButton("撤销编排")
        self.canvas_undo.clicked.connect(self.canvas.undo)
        second.addWidget(self.canvas_undo)
        self.canvas_redo = QPushButton("重做编排")
        self.canvas_redo.clicked.connect(self.canvas.redo)
        second.addWidget(self.canvas_redo)
        self.canvas_clear = QPushButton("清空编排")
        self.canvas_clear.clicked.connect(self.canvas.clear_grid)
        second.addWidget(self.canvas_clear)
        self.canvas_refresh = QPushButton("刷新标记")
        self.canvas_refresh.setToolTip(
            "把存档里的玩家 / 实体 / 容器 / 出生点 / 区块中心重新画到画布上")
        self.canvas_refresh.clicked.connect(self.refresh_creative)
        second.addWidget(self.canvas_refresh)
        second.addStretch(1)
        self.canvas_status = QLabel("")
        self.canvas_status.setStyleSheet(muted_style(holder))
        second.addWidget(self.canvas_status)
        rows.addLayout(second)
        return holder

    def _build_canvas_actions(self) -> QWidget:
        holder = QWidget()
        rows = QVBoxLayout(holder)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(SPACING["xs"])

        first = QHBoxLayout()
        first.setSpacing(SPACING["xs"])
        for label, slot, tip in (
                ("生成 /setblock → 输出区", self._on_canvas_setblock,
                 "每个格子一条 setblock（最精确）"),
                ("生成 /fill → 输出区", self._on_canvas_fill,
                 "按层做矩形分解，生成尽量少的 fill"),
                ("导出为蓝图…", self._on_canvas_blueprint,
                 "写成 .nbt / .schem 并生成 /place"),
                ("复制 /data → 剪贴板", self._on_canvas_copy_data,
                 "复制成 data modify block … 命令"),
                ("清空选区", self._on_canvas_clear_selection, "")):
            button = QPushButton(label)
            button.setToolTip(tip or label)
            button.clicked.connect(slot)
            first.addWidget(button)
        first.addStretch(1)
        rows.addLayout(first)

        second = QHBoxLayout()
        second.setSpacing(SPACING["xs"])
        self.canvas_region_write = QPushButton("把选区写进区域区块…（实验）")
        self.canvas_region_write.setToolTip(
            "需要选中 .mca 区域文件所在存档；只支持等长替换，写前自动备份")
        self.canvas_region_write.clicked.connect(self._on_canvas_region_write)
        second.addWidget(self.canvas_region_write)
        # 这个按钮会写区域文件，必须跟着只读门禁一起灰（旧版漏登记）
        self._extra_write_widgets.append(self.canvas_region_write)
        self.canvas_palette = QToolButton()
        self.canvas_palette.setText("常用方块 ▾")
        self.canvas_palette.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.canvas_palette)
        for block in self._palette_blocks():
            action = menu.addAction(block)
            action.triggered.connect(
                lambda _=False, b=block: self._set_canvas_brush(b))
        self.canvas_palette.setMenu(menu)
        second.addWidget(self.canvas_palette)
        second.addStretch(1)
        self.canvas_selection_label = QLabel("未框选区块")
        self.canvas_selection_label.setStyleSheet(muted_style(holder))
        second.addWidget(self.canvas_selection_label)
        rows.addLayout(second)
        return holder

    # ---- 画布回调 ----

    def _palette_blocks(self) -> list[str]:
        """常用方块（取自注册表，只挑建筑常用的那些）。"""
        preferred = [
            "minecraft:stone", "minecraft:cobblestone", "minecraft:stone_bricks",
            "minecraft:deepslate_bricks", "minecraft:oak_planks",
            "minecraft:spruce_planks", "minecraft:oak_log",
            "minecraft:glass", "minecraft:white_concrete",
            "minecraft:black_concrete", "minecraft:gray_concrete",
            "minecraft:bricks", "minecraft:quartz_block",
            "minecraft:smooth_stone", "minecraft:dirt",
            "minecraft:grass_block", "minecraft:sand", "minecraft:gravel",
            "minecraft:water", "minecraft:lava", "minecraft:glowstone",
            "minecraft:sea_lantern", "minecraft:redstone_lamp",
            "minecraft:chest", "minecraft:barrel", "minecraft:furnace",
            "minecraft:torch", "minecraft:lantern", "minecraft:air",
        ]
        known = set()
        try:
            from app.core.data import store as data_store
            known = {str(entry.get("id")) for entry in data_store().blocks()
                     if isinstance(entry, dict) and entry.get("id")}
        except Exception:                               # pragma: no cover
            known = set()
        if not known:
            return preferred
        # 注册表里有的优先，没有的也保留（用户可能想手输）
        return preferred + sorted(known - set(preferred))[:120]

    def _set_canvas_brush(self, block: str) -> None:
        self.canvas_brush.setCurrentText(block)
        self.canvas.brush = block
        self.statusMessage.emit(f"画笔已切换为 {block}")

    def _on_canvas_tool(self, _index: int) -> None:
        self.canvas.tool = str(self.canvas_tool.currentData())
        self.statusMessage.emit(f"画布工具：{self.canvas_tool.currentText()}")

    def _on_toggle_grid(self, enabled: bool) -> None:
        self.canvas.show_grid = bool(enabled)
        self.canvas.update()

    def _on_toggle_markers(self, enabled: bool) -> None:
        self.canvas.show_markers = bool(enabled)
        self.canvas.update()

    def _on_canvas_hover(self, x: int, y: int, z: int) -> None:
        chunk = (int(math.floor(x / CHUNK_SIZE)),
                 int(math.floor(z / CHUNK_SIZE)))
        self.canvas_status.setText(
            f"坐标 {x} {y} {z}  ·  区块 {chunk[0]},{chunk[1]}"
            f"  ·  该格：{self.canvas.grid.get(x, y, z) or '空'}")

    def _on_canvas_changed(self) -> None:
        self.canvas_selection_label.setText(
            f"已编排 {len(self.canvas.grid)} 格 · "
            f"{len(self.canvas.grid.palette())} 种方块")

    def _on_canvas_selection(self, x0: int, z0: int, x1: int, z1: int) -> None:
        chunks = (x1 - x0 + 1) * (z1 - z0 + 1)
        self.canvas_selection_label.setText(
            f"选区：区块 ({x0},{z0}) → ({x1},{z1}) · {chunks} 个区块"
            f" · 世界坐标 {x0 * CHUNK_SIZE} {z0 * CHUNK_SIZE} → "
            f"{(x1 + 1) * CHUNK_SIZE - 1} {(z1 + 1) * CHUNK_SIZE - 1}")

    def _on_canvas_clear_selection(self) -> None:
        self.canvas._chunk_sel = None
        self.canvas.update()
        self.canvas_selection_label.setText("未框选区块")

    # ---- 导出 ----

    def _on_canvas_setblock(self) -> None:
        commands = self.canvas.grid.to_commands(mode="setblock")
        if not commands:
            self.statusMessage.emit("画布上还没有方块。")
            return
        self.commandsReady.emit("\n".join(commands))
        self.statusMessage.emit(f"已生成 {len(commands)} 条 /setblock 命令。")

    def _on_canvas_fill(self) -> None:
        commands = self.canvas.grid.to_commands(mode="fill")
        if not commands:
            self.statusMessage.emit("画布上还没有方块。")
            return
        self.commandsReady.emit("\n".join(commands))
        self.statusMessage.emit(
            f"已生成 {len(commands)} 条命令（按层矩形分解，尽量少用 fill）。")

    def _on_canvas_blueprint(self) -> None:
        from app.core.blueprint import save_named_blueprint
        from app.ui.world_dialogs import BlueprintExportDialog
        if not self.canvas.grid.cells:
            self.statusMessage.emit("画布上还没有方块。")
            return
        blueprint = self.canvas.grid.to_blueprint("画布选区")
        dialog = BlueprintExportDialog(
            self, name=blueprint.name, block_count=blueprint.block_count(),
            size=blueprint.size, has_entities=False)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        options = dialog.values()
        blueprint.name = options["name"]
        try:
            written = save_named_blueprint(
                blueprint, fmt=options["format"],
                include_nbt=False)
        except Exception as e:
            QMessageBox.warning(self, "写入失败", str(e))
            return
        self._blueprint = blueprint
        self.statusMessage.emit(
            f"已导出蓝图「{blueprint.name}」：{'、'.join(written)}")
        if options["command_mode"] != "none":
            self._blueprint_commands(options["command_mode"],
                                     limit=options["limit"])

    def _on_canvas_copy_data(self) -> None:
        from app.core.snbt import SnbtNode, SnbtKind, serialize
        from PySide6.QtGui import QGuiApplication
        selection = self.canvas.selection()
        layer = self.canvas.layer
        cells = self.canvas.grid.by_layer(layer)
        if not cells:
            self.statusMessage.emit("当前 Y 层没有方块。")
            return
        x, z = sorted(cells)[0]
        payload = SnbtNode.new_list(key="blocks")
        for (cx, cz), block in sorted(cells.items()):
            entry = SnbtNode.new_compound()
            entry.add_child(SnbtNode.new_scalar(SnbtKind.INT, cx, key="x"))
            entry.add_child(SnbtNode.new_scalar(SnbtKind.INT, layer, key="y"))
            entry.add_child(SnbtNode.new_scalar(SnbtKind.INT, cz, key="z"))
            entry.add_child(SnbtNode.new_scalar(SnbtKind.STRING, block,
                                                key="id"))
            payload.add_child(entry)
        command = (f"data modify block {x} {layer} {z} mcstudio:paint "
                   f"set value {serialize(payload, pretty=False)}")
        QGuiApplication.clipboard().setText(command)
        _ = selection
        self.statusMessage.emit("已复制 /data 命令（含本层编排的方块列表）。")

    def _on_canvas_region_write(self) -> None:
        """把编排结果写进区域区块（实验功能，走与区域页签同一条安全链路）。"""
        from app.core.region_io import (
            backup_region, plan_chunk_write, verify_region, write_chunk,
        )
        selection = self.canvas.selection()
        if selection is None:
            QMessageBox.information(self, "提示",
                                    "请先用「选择」工具框选区块范围。")
            return
        if not self.canvas.grid.cells:
            QMessageBox.information(self, "提示", "画布上还没有方块。")
            return
        world = self.world()
        if world is None or not world.is_open():
            QMessageBox.information(self, "提示", "请先打开一个世界存档。")
            return
        if not self._guard_write():
            return

        x0, z0, x1, z1 = selection
        targets: list[tuple[str, int, int]] = []
        for region in world.regions.values():
            for chunk in region.chunks:
                if x0 <= chunk.x <= x1 and z0 <= chunk.z <= z1 \
                        and chunk.exists():
                    targets.append((str(region.path), chunk.x, chunk.z))
        if not targets:
            QMessageBox.information(
                self, "选区里没有可写区块",
                "选中的区块在现有区域文件里不存在或不可读；"
                "本工具不会新建区块（需要先在游戏里加载那片区域）。")
            return
        if len(targets) > 64:
            answer = QMessageBox.question(
                self, "区块很多",
                f"选区里有 {len(targets)} 个可写区块，逐个 dry-run + 备份"
                "会花一些时间。继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return

        plans = []
        for path, chunk_x, chunk_z in targets:
            node = self._chunk_with_paint(world, path, chunk_x, chunk_z)
            if node is None:
                continue
            plan = plan_chunk_write(path, chunk_x, chunk_z, node)
            plans.append((path, chunk_x, chunk_z, node, plan))
        writable = [item for item in plans if item[4].ok]
        if not writable:
            reasons = "；".join(
                f"({item[1]},{item[2]}) {item[4].reason}" for item in plans[:4])
            QMessageBox.information(
                self, "没有可写区块",
                "所有候选区块都被拒绝：" + (reasons or "未知原因"))
            return

        lines = [f"■ {len(writable)}/{len(plans)} 个区块可写"]
        for _path, chunk_x, chunk_z, _node, plan in writable[:12]:
            lines.append(f"    ({chunk_x},{chunk_z}) {plan.summary()}")
        skipped = [item for item in plans if not item[4].ok]
        if skipped:
            lines.append(f"■ {len(skipped)} 个区块被拒绝（会在结果里说明）")
        answer = QMessageBox.warning(
            self, "确认写入区域区块",
            "\n".join(lines) + "\n\n每个文件都会先整体备份，再原子替换并自检。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Ok:
            return

        stamp = __import__("datetime").datetime.now().strftime(
            "%Y%m%d-%H%M%S")
        folder = world.backup_dir(stamp)
        done: list[str] = []
        failed: list[str] = []
        for path, chunk_x, chunk_z, node, _plan in writable:
            try:
                backup_region(path, folder)
                write_chunk(path, chunk_x, chunk_z, node)
                problems = verify_region(path)
            except Exception as e:
                failed.append(f"({chunk_x},{chunk_z}) {e}")
                continue
            if problems:
                failed.append(f"({chunk_x},{chunk_z}) 自检："
                              + "；".join(problems[:2]))
            else:
                done.append(f"({chunk_x},{chunk_z})")
        self.refresh_regions()
        message = (f"已写入 {len(done)} 个区块"
                   f"（备份 backups/{folder.name}）")
        if failed:
            message += f"；{len(failed)} 个失败：" + "；".join(failed[:3])
        self.statusMessage.emit(message)
        QMessageBox.information(self, "区域编排写入完成", message)

    def _chunk_with_paint(self, world, path: str, chunk_x: int, chunk_z: int):
        """把画布上落在该区块范围内的方块合并进区块 NBT。"""
        from app.core.snbt import SnbtKind, SnbtNode
        from app.core.world import read_region_chunk
        node = read_region_chunk(path, chunk_x, chunk_z)
        if node is None:
            return None
        base_x = chunk_x * CHUNK_SIZE
        base_z = chunk_z * CHUNK_SIZE
        mine = {key: block for key, block in self.canvas.grid.cells.items()
                if base_x <= key[0] < base_x + CHUNK_SIZE
                and base_z <= key[2] < base_z + CHUNK_SIZE}
        if not mine:
            return node
        # 极简但结构正确的 1.21 区块内容：sections 里放一层 palette + block_states
        layers: dict[int, dict[tuple[int, int], str]] = {}
        for (x, y, z), block in mine.items():
            layers.setdefault(y, {})[(x - base_x, z - base_z)] = block
        sections = node.get_child("sections")
        if sections is None or sections.kind != SnbtKind.LIST:
            sections = SnbtNode.new_list(key="sections")
            node.add_child(sections)
        for y, cells in sorted(layers.items()):
            self._merge_section(sections, y, cells)
        return node

    def _merge_section(self, sections, y: int,
                       cells: dict[tuple[int, int], str]) -> None:
        """把一层方块写进 sections（按原版 1.18+ 的 palette + 位数组）。"""
        from app.core.snbt import SnbtKind, SnbtNode
        section_y = y >> 4
        target = None
        for section in sections.children:
            child = section.get_child("Y")
            if child is not None and int(child.value) == section_y:
                target = section
                break
        palette_ids = sorted(set(cells.values()))
        if target is None:
            target = SnbtNode.new_compound()
            target.add_child(SnbtNode.new_scalar(SnbtKind.BYTE, section_y,
                                                 key="Y"))
            sections.add_child(target)
        block_states = target.get_child("block_states")
        if block_states is None:
            block_states = SnbtNode.new_compound(key="block_states")
            target.add_child(block_states)
        palette = block_states.get_child("palette")
        if palette is None or palette.kind != SnbtKind.LIST:
            palette = SnbtNode.new_list(key="palette")
            block_states.add_child(palette)
        index_of: dict[str, int] = {}
        for position, entry in enumerate(palette.children):
            name = entry.get_child("Name")
            if name is not None:
                index_of[str(name.value)] = position
        for block in palette_ids:
            if block not in index_of:
                entry = SnbtNode.new_compound()
                entry.add_child(SnbtNode.new_scalar(SnbtKind.STRING, block,
                                                    key="Name"))
                palette.add_child(entry)
                index_of[block] = len(palette.children) - 1
        # 位数组：1.18+ 每格 4 位起（bits = max(4, ceil(log2(palette))))
        bits = max(4, (len(palette.children) - 1).bit_length())
        per_long = 64 // bits
        values = [0] * ((4096 + per_long - 1) // per_long)
        for (x, z), block in cells.items():
            position = (y & 15) * 256 + z * 16 + x
            index = index_of.get(block, 0)
            word = position // per_long
            shift = (position % per_long) * bits
            values[word] |= (index & ((1 << bits) - 1)) << shift
        data = SnbtNode(kind=SnbtKind.LONG_ARRAY, key="data")
        for value in values:
            data.add_child(SnbtNode.new_scalar(SnbtKind.LONG, value))
        existing = block_states.get_child("data")
        if existing is not None:
            block_states.children[block_states.children.index(existing)] = data
        else:
            block_states.add_child(data)


    def refresh_creative(self) -> None:
        """把存档里的实体 / 容器 / 玩家 / 出生点 / 区域区块中心画到画布上。"""
        canvas = getattr(self, "canvas", None)
        if canvas is None:                              # pragma: no cover
            return
        world = self.world()
        markers: list[MapMarker] = []
        if world is None or not world.is_open():
            canvas.markers = []
            canvas.update()
            self.canvas_status.setText("未打开世界")
            return

        spawn = world.overview.spawn
        markers.append(MapMarker(float(spawn[0]), float(spawn[2]),
                                 kind="spawn", label="出生点",
                                 detail=f"{spawn[0]} {spawn[1]} {spawn[2]}"))
        for uuid in sorted(world.players):
            player = world.players[uuid]
            pos = player.pos
            markers.append(MapMarker(
                float(pos[0]), float(pos[2]), kind="player",
                label=player.display(),
                detail=f"{player.pos_text()} · {player.dimension}",
                dimension=player.dimension))
        try:
            for _target, root in self._document_roots():
                for entity in find_entities(root):
                    x, z = _entity_xz(entity)
                    if x is None:
                        continue
                    markers.append(MapMarker(
                        x, z, kind="entity", label=entity.display(),
                        detail=entity.summary()))
        except Exception:                               # pragma: no cover
            pass
        try:
            for target in self._targets():
                for info in world.containers(target):
                    if info.position is None:
                        continue
                    markers.append(MapMarker(
                        float(info.position[0]), float(info.position[2]),
                        kind="container",
                        label=info.block_id or info.label,
                        detail=info.summary()))
        except Exception:                               # pragma: no cover
            pass
        # 区域文件里的实体解析太重，这里只标已有数据的区块中心
        for region in world.regions.values():
            if not region.present:
                continue
            for chunk in region.chunks[:64]:
                markers.append(MapMarker(
                    chunk.x * CHUNK_SIZE + CHUNK_SIZE / 2.0,
                    chunk.z * CHUNK_SIZE + CHUNK_SIZE / 2.0,
                    kind="map", label="",
                    detail=f"{region.name} 区块 ({chunk.x},{chunk.z})"))

        canvas.markers = markers
        canvas.fit_to()
        canvas.update()
        counts: dict[str, int] = {}
        for marker in markers:
            counts[marker.kind] = counts.get(marker.kind, 0) + 1
        summary = " · ".join(f"{key} {value}"
                             for key, value in sorted(counts.items())) \
            or "无标记"
        self.canvas_status.setText(f"标记：{summary}")
        self.statusMessage.emit(
            f"已把 {len(markers)} 个标记画到坐标选择器上（{summary}）。")


def _entity_xz(entity) -> tuple[float | None, float | None]:
    """从实体 NBT 里取 X/Z（没有 Pos 就返回 None）。"""
    nbt = getattr(entity, "nbt", "") or ""
    if "Pos" not in nbt:
        return None, None
    try:
        from app.core.snbt import SnbtParser
        node = SnbtParser(nbt).parse()
    except Exception:                                   # pragma: no cover
        return None, None
    pos = node.get_child("Pos")
    if pos is None or pos.kind != SnbtKind.LIST or len(pos.children) < 3:
        return None, None
    try:
        return float(pos.children[0].value), float(pos.children[2].value)
    except (TypeError, ValueError):                     # pragma: no cover
        return None, None


__all__ = [
    "CHUNK_SIZE", "MARKER_STYLES", "TOOL_PAINT", "TOOL_ERASE", "TOOL_FILL",
    "TOOL_PICK", "TOOL_SELECT", "MapMarker", "BlockGrid", "CoordinateCanvas",
    "CreativeTabMixin",
]
