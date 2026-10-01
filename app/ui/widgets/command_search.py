"""命令搜索框：支持命令名、中文名、分类名模糊匹配，Ctrl+P 聚焦。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QStringListModel
from PySide6.QtWidgets import QLineEdit, QCompleter

from app.core import iter_builders


class CommandSearch(QLineEdit):
    """工具栏右侧的命令搜索框。

    - 输入命令名（setblock / /setblock）、中文名（方块）、分类名（方块）
      均可模糊匹配；
    - 回车或从下拉选中后，发出 commandChosen 信号；
    - 支持 Ctrl+P 聚焦（由 MainWindow 注册的 QAction 触发）。
    """

    commandChosen = Signal(str)
    #: 回车/选中但没有任何匹配时发出（便于给用户可见反馈）
    notFound = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("搜索命令… Ctrl+P")
        self.setClearButtonEnabled(True)

        self._items: list[tuple[str, str]] = []   # (显示文本, command_name)
        self._model = QStringListModel(self)
        self._completer = QCompleter(self._model, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion)
        self._completer.setMaxVisibleItems(12)
        self.setCompleter(self._completer)

        self._reload()
        self.returnPressed.connect(self._on_return)
        self._completer.activated.connect(self._on_activated)

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def _reload(self) -> None:
        """从 Builder 注册表重建候选列表。"""
        self._items.clear()
        for cls in iter_builders():
            cmd = cls.command_name
            if not cmd:
                continue
            label = cls.display_name or f"/{cmd}"
            cat = cls.category or "其他"
            # 显示格式：/cmd  中文名  [分类]
            text = f"/{cmd}  {label}  [{cat}]"
            self._items.append((text, cmd))
        self._model.setStringList([t for t, _ in self._items])

    def refresh(self) -> None:
        """外部数据刷新后调用。"""
        self._reload()

    # ------------------------------------------------------------------
    # 匹配逻辑
    # ------------------------------------------------------------------
    def _match_command(self, text: str) -> str | None:
        text = (text or "").strip()
        if not text:
            return None

        # 去掉前导 /
        if text.startswith("/"):
            text = text[1:]

        first = text.split(None, 1)[0]

        # 1) 精确匹配命令名
        for _, cmd in self._items:
            if cmd == first:
                return cmd

        # 2) 包含匹配（命令名 / 中文名 / 分类）
        low = text.lower()
        for display, cmd in self._items:
            if low in display.lower():
                return cmd

        # 3) 子序列模糊匹配
        for display, cmd in self._items:
            if _subsequence(low, display.lower()):
                return cmd

        return None

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def _on_return(self) -> None:
        cmd = self._match_command(self.text())
        if cmd:
            self.commandChosen.emit(cmd)
            self.clear()
        else:
            self.notFound.emit(self.text().strip())

    def _on_activated(self, text: str) -> None:
        cmd = self._match_command(text)
        if cmd:
            self.commandChosen.emit(cmd)
            self.clear()
        else:
            self.notFound.emit(self.text().strip())

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.selectAll()


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------

def _subsequence(needle: str, haystack: str) -> bool:
    """needle 的字符是否按顺序出现在 haystack 中。"""
    it = iter(haystack)
    return all(ch in it for ch in needle)


__all__ = ["CommandSearch"]