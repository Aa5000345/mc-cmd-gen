"""命令面板（Ctrl+K）：一个输入框跳到任何命令、页签或动作。

旧版把 44 条命令塞进左侧树、7 个中间页签 + 15 个编辑器页签横排在顶部，
窗口一窄就都要靠滚动找。命令面板用「模糊搜索 + 键盘」把跳转成本降到一次
输入，且不需要用户记住功能藏在哪个页签。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout,
    QWidget,
)


@dataclass
class PaletteEntry:
    """一条可跳转的条目。"""

    title: str
    subtitle: str = ""
    keywords: str = ""
    category: str = ""
    callback: Callable[[], None] | None = None
    payload: object = None

    def haystack(self) -> str:
        return " ".join((self.title, self.subtitle, self.keywords,
                         self.category)).lower()

    def display(self) -> str:
        if self.subtitle:
            return f"{self.title}　—　{self.subtitle}"
        return self.title


def score_entry(entry: PaletteEntry, tokens: list[str], raw: str) -> int:
    """粗排：标题前缀 > 标题包含 > 关键词包含。返回 0 表示不匹配。"""
    haystack = entry.haystack()
    title = entry.title.lower()
    for token in tokens:
        if token not in haystack:
            return 0
    score = 1
    if raw and title.startswith(raw):
        score += 60
    elif raw and raw in title:
        score += 40
    elif raw and raw in haystack:
        score += 20
    # 短标题更像「命令名」，优先
    score += max(0, 20 - len(entry.title))
    if entry.category:
        score += 2
    return score


class _SearchEdit(QLineEdit):
    """支持 ↑↓ 在结果里移动、Enter 直接执行的搜索框。"""

    moveDown = Signal()
    moveUp = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            (self.moveDown if key == Qt.Key.Key_Down else
             self.moveUp).emit()
            return
        if key in (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
            (self.moveDown if key == Qt.Key.Key_PageDown else
             self.moveUp).emit()
            return
        super().keyPressEvent(event)


class CommandPalette(QDialog):
    """命令面板对话框：搜索 → 回车执行。"""

    entryTriggered = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Palette")
        self.setWindowTitle("命令面板")
        self.setWindowFlags(Qt.WindowType.Popup |
                            Qt.WindowType.FramelessWindowHint)
        self.setMinimumWidth(560)
        self._entries: list[PaletteEntry] = []
        self._filtered: list[PaletteEntry] = []
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 10)
        v.setSpacing(8)

        self.search = _SearchEdit()
        self.search.setObjectName("PaletteSearch")
        self.search.setPlaceholderText(
            "搜索命令、功能、页签或动作…（例如 give、存档、附魔、主题）")
        self.search.textChanged.connect(self._on_text_changed)
        self.search.moveDown.connect(lambda: self._move(1))
        self.search.moveUp.connect(lambda: self._move(-1))
        self.search.returnPressed.connect(self._activate_current)
        v.addWidget(self.search)

        self.list = QListWidget()
        self.list.setObjectName("PaletteList")
        self.list.setMinimumHeight(320)
        self.list.itemActivated.connect(lambda _i: self._activate_current())
        self.list.itemClicked.connect(lambda _i: self._activate_current())
        v.addWidget(self.list, 1)

        self.hint = QLabel("↑↓ 选择　Enter 执行　Esc 关闭")
        self.hint.setObjectName("PaletteHint")
        v.addWidget(self.hint)

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def set_entries(self, entries: list[PaletteEntry]) -> None:
        self._entries = list(entries)
        self._refilter()

    def entries(self) -> list[PaletteEntry]:
        return list(self._entries)

    def filter_entries(self, text: str) -> list[PaletteEntry]:
        raw = (text or "").strip().lower()
        if not raw:
            return list(self._entries)
        tokens = [t for t in raw.split() if t]
        scored = []
        for entry in self._entries:
            score = score_entry(entry, tokens, raw)
            if score:
                scored.append((score, entry))
        scored.sort(key=lambda pair: (-pair[0], pair[1].title))
        return [entry for _score, entry in scored]

    def _refilter(self) -> None:
        self._filtered = self.filter_entries(self.search.text())
        self.list.clear()
        if not self._filtered:
            item = QListWidgetItem("没有匹配项 —— 换个说法试试，"
                                   "例如「存档」「物品」「时间」")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
            return
        for entry in self._filtered[:200]:
            text = entry.display()
            if entry.category:
                text = f"[{entry.category}] {text}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self.hint.setText(
            f"{len(self._filtered)} 条结果　↑↓ 选择　Enter 执行　Esc 关闭")

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def _on_text_changed(self, text: str) -> None:
        self._refilter()

    def _move(self, delta: int) -> None:
        count = self.list.count()
        if not count:
            return
        row = self.list.currentRow()
        row = 0 if row < 0 else (row + delta) % count
        self.list.setCurrentRow(row)

    def _activate_current(self) -> None:
        item = self.list.currentItem()
        entry = item.data(Qt.ItemDataRole.UserRole) if item else None
        if entry is None:
            return
        self.close()
        self.entryTriggered.emit(entry)
        if entry.callback is not None:
            entry.callback()

    def activate_entry(self, entry: PaletteEntry) -> None:
        """供测试与程序化调用：直接执行某条条目。"""
        self.close()
        self.entryTriggered.emit(entry)
        if entry.callback is not None:
            entry.callback()

    def open_palette(self, entries: list[PaletteEntry] | None = None) -> None:
        if entries is not None:
            self.set_entries(entries)
        self.search.clear()
        self._refilter()
        parent = self.parentWidget()
        if parent is not None:
            geo = parent.window().geometry()
            self.move(geo.center().x() - self.width() // 2 or 200,
                      max(60, geo.top() + 120))
        self.show()
        self.raise_()
        self.search.setFocus()


__all__ = ["CommandPalette", "PaletteEntry", "score_entry"]
