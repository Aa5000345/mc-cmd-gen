"""世界编辑器的公用构件：设计 token 封装 + 表格 / 分节 / 过滤条。

排版约定（唯一事实来源是 `app.ui.theme`）
----------------------------------------
* 页签内容用 `theme.MARGINS["page"]` + `theme.SPACING["sm"]`；
* 每个功能块套一个 `SectionBox`（标题 + 说明 + 内容），不再散落 QLabel；
* 所有表格都走 `make_table()`，行高 / 表头 / 网格线统一；
* 每个表格配一个 `FilterBar`（`Ctrl+F` 聚焦），大存档里能立刻筛出目标行。
"""
from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QSizePolicy, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from app.ui.theme import (
    FONTS, MARGINS, SIZES, SPACING, hint_style, muted_style, polish_tables,
)


# ---------------------------------------------------------------------------
# 表格
# ---------------------------------------------------------------------------

def make_table(headers: Iterable[str], parent=None, *,
               stretch_last: bool = True,
               resize: QHeaderView.ResizeMode | None = None) -> QTableWidget:
    """统一风格的只读表格。

    默认：不显示竖向表头、按内容自适应列宽、最后一列拉伸、行高统一、
    不显示网格线、交替行底色。
    """
    headers = list(headers)
    table = QTableWidget(0, len(headers), parent)
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setVerticalScrollMode(
        QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setHorizontalScrollMode(
        QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.horizontalHeader().setStretchLastSection(stretch_last)
    table.horizontalHeader().setSectionResizeMode(
        resize or QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setDefaultSectionSize(96)
    polish_tables([table], parent)
    return table


def make_editable_table(headers: Iterable[str], parent=None) -> QTableWidget:
    """可编辑取值的表格（游戏规则那种双击改值的）。"""
    table = make_table(headers, parent)
    table.setEditTriggers(
        QAbstractItemView.EditTrigger.DoubleClicked
        | QAbstractItemView.EditTrigger.EditKeyPressed
        | QAbstractItemView.EditTrigger.SelectedClicked)
    return table


def add_row(table: QTableWidget, values: Iterable[object],
            tooltip: str = "", *, payload=None) -> int:
    """追加一行；`payload` 会挂到第 0 列的 UserRole 上。"""
    index = table.rowCount()
    table.insertRow(index)
    for column, value in enumerate(values):
        item = QTableWidgetItem("" if value is None else str(value))
        if tooltip:
            item.setToolTip(tooltip)
        table.setItem(index, column, item)
    if payload is not None and table.item(index, 0) is not None:
        table.item(index, 0).setData(Qt.ItemDataRole.UserRole, payload)
    return index


def row_payload(table: QTableWidget, row: int):
    item = table.item(row, 0)
    return item.data(Qt.ItemDataRole.UserRole) if item is not None else None


def cell_text(table: QTableWidget, row: int, column: int) -> str:
    item = table.item(row, column)
    return item.text() if item is not None else ""


def filter_tables(tables: Iterable[QTableWidget], text: str) -> int:
    """按关键字过滤表格行（大小写不敏感，匹配任意列），返回可见行数。"""
    needle = str(text or "").strip().lower()
    visible = 0
    for table in tables:
        for row in range(table.rowCount()):
            if not needle:
                table.setRowHidden(row, False)
                visible += 1
                continue
            hit = any(
                needle in (table.item(row, column).text().lower()
                           if table.item(row, column) else "")
                for column in range(table.columnCount()))
            table.setRowHidden(row, not hit)
            if hit:
                visible += 1
    return visible


def filter_list_widget(list_widget, text: str) -> int:
    """按关键字过滤 QListWidget（匹配条目文本或 UserRole+1 里的搜索串）。"""
    needle = str(text or "").strip().lower()
    visible = 0
    for row in range(list_widget.count()):
        item = list_widget.item(row)
        haystack = item.text()
        extra = item.data(Qt.ItemDataRole.UserRole + 1)
        if extra:
            haystack = f"{haystack}\n{extra}"
        hit = not needle or needle in haystack.lower()
        item.setHidden(not hit)
        if hit:
            visible += 1
    return visible


def export_table_csv(table: QTableWidget, path) -> int:
    """把表格（含表头）导出成 CSV（UTF-8 BOM，Excel 直接打开不乱码）。"""
    import csv
    from pathlib import Path
    headers = [table.horizontalHeaderItem(i).text()
               if table.horizontalHeaderItem(i) else f"列{i}"
               for i in range(table.columnCount())]
    rows = []
    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        rows.append([cell_text(table, row, column)
                     for column in range(table.columnCount())])
    target = Path(path)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)
    return len(rows)


# ---------------------------------------------------------------------------
# 分节与提示
# ---------------------------------------------------------------------------

class SectionBox(QGroupBox):
    """带标题 + 说明 + 可选右上角按钮的功能块。"""

    def __init__(self, title: str, hint: str = "", parent=None) -> None:
        super().__init__(title, parent)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(*MARGINS["section"])
        self._outer.setSpacing(SPACING["sm"])

        self._header = QHBoxLayout()
        self._header.setSpacing(SPACING["sm"])
        self.hint_label = QLabel(hint)
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet(hint_style(self))
        self.hint_label.setVisible(bool(hint))
        self._header.addWidget(self.hint_label, 1)
        self._outer.addLayout(self._header)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(SPACING["sm"])
        self._outer.addLayout(self.body)

    def add_widget(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.body.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)

    def add_button(self, text: str, slot, *, tooltip: str = "",
                   primary: bool = False) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setAutoRaise(not primary)
        button.setToolTip(tooltip or text)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if primary:
            button.setStyleSheet(
                "QToolButton { padding: 3px 10px; border-radius: 4px; }")
        button.clicked.connect(slot)
        self._header.addWidget(button)
        return button


class FilterBar(QWidget):
    """表格过滤条：输入框 + 命中计数 + 导出按钮，带 Ctrl+F 快捷键。"""

    filterChanged = Signal(str)
    exportRequested = Signal()

    def __init__(self, parent=None, *, placeholder: str = "筛选…",
                 shortcut: str = "Ctrl+F", table: QTableWidget | None = None,
                 tables: Iterable[QTableWidget] | None = None,
                 list_widget=None) -> None:
        super().__init__(parent)
        self._tables: list[QTableWidget] = list(tables or [])
        if table is not None:
            self._tables.insert(0, table)
        self._lists = [list_widget] if list_widget is not None else []

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACING["sm"])

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setClearButtonEnabled(True)
        self.edit.setFixedHeight(SIZES["input_height"])
        self.edit.textChanged.connect(self._on_changed)
        row.addWidget(self.edit, 1)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet(muted_style(self))
        row.addWidget(self.count_label)

        self.btn_clear = QToolButton()
        self.btn_clear.setText("清空")
        self.btn_clear.setAutoRaise(True)
        self.btn_clear.clicked.connect(self.edit.clear)
        row.addWidget(self.btn_clear)

        self.btn_export = QToolButton()
        self.btn_export.setText("导出 CSV")
        self.btn_export.setAutoRaise(True)
        self.btn_export.setToolTip("把当前可见行导出为 CSV")
        self.btn_export.clicked.connect(self.exportRequested.emit)
        row.addWidget(self.btn_export)

        if shortcut:
            # 只在本页可见时生效，避免多个页签抢同一个快捷键
            action = QShortcut(QKeySequence(shortcut), self)
            action.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            action.activated.connect(self.focus)

        self._refresh_count()

    # ---- 交互 ----

    def focus(self) -> None:
        self.edit.setFocus()
        self.edit.selectAll()

    def text(self) -> str:
        return self.edit.text()

    def add_table(self, table: QTableWidget) -> None:
        if table not in self._tables:
            self._tables.append(table)
            self._on_changed(self.edit.text())

    def add_list(self, list_widget) -> None:
        if list_widget not in self._lists:
            self._lists.append(list_widget)
            self._on_changed(self.edit.text())

    def _on_changed(self, text: str) -> None:
        visible = filter_tables(self._tables, text)
        for widget in self._lists:
            visible += filter_list_widget(widget, text)
        self._refresh_count(visible)
        self.filterChanged.emit(text)

    def _refresh_count(self, visible: int | None = None) -> None:
        total = sum(table.rowCount() for table in self._tables)
        total += sum(widget.count() for widget in self._lists)
        if visible is None:
            visible = sum(
                1 for table in self._tables for row in range(table.rowCount())
                if not table.isRowHidden(row))
            visible += sum(
                1 for widget in self._lists for row in range(widget.count())
                if not widget.item(row).isHidden())
        if not total:
            self.count_label.setText("")
        elif visible == total:
            self.count_label.setText(f"{total} 行")
        else:
            self.count_label.setText(f"{visible}/{total} 行")

    def table_count(self) -> int:
        return len(self._tables) + len(self._lists)


# ---------------------------------------------------------------------------
# 只读提示条
# ---------------------------------------------------------------------------

class StateBanner(QFrame):
    """顶部的状态条：世界名 + 只读/可编辑 + 未应用修改 + 版本。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(SPACING["md"], SPACING["xs"],
                               SPACING["md"], SPACING["xs"])
        row.setSpacing(SPACING["md"])

        self.dot = QLabel("●")
        self.dot.setStyleSheet("font-size: 10pt;")
        row.addWidget(self.dot)

        self.title = QLabel("未打开世界")
        font = self.title.font()
        font.setPointSize(FONTS["body"])
        font.setBold(True)
        self.title.setFont(font)
        row.addWidget(self.title)

        self.detail = QLabel("")
        self.detail.setStyleSheet(muted_style(self))
        row.addWidget(self.detail, 1)

        self.state = QLabel("只读")
        self.state.setStyleSheet(muted_style(self))
        row.addWidget(self.state)

    def update_state(self, *, name: str = "", detail: str = "",
                     readonly: bool = True, dirty: bool = False,
                     level: str = "ok") -> None:
        palette_ok = "#3FB950"
        palette_warn = "#D29922"
        palette_error = "#F85149"
        color = palette_ok if level == "ok" else (
            palette_warn if level == "warn" else palette_error)
        self.dot.setStyleSheet(f"color: {color}; font-size: 10pt;")
        self.title.setText(name or "未打开世界")
        self.detail.setText(detail)
        if not name:
            self.state.setText("只读")
        elif readonly:
            self.state.setText("只读（未解锁）")
        elif dirty:
            self.state.setText("可编辑 · 有未应用的修改")
        else:
            self.state.setText("可编辑")


__all__ = [
    "make_table", "make_editable_table", "add_row", "row_payload",
    "cell_text", "filter_tables", "filter_list_widget", "export_table_csv",
    "SectionBox", "FilterBar", "StateBanner",
]
