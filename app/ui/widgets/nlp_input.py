"""自然语言 → 命令 输入框（带示例补全与失败提示）。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCompleter, QLineEdit, QToolTip

from app.core.nlp.engine import examples, parse, parse_with_pattern


class NlpInput(QLineEdit):
    """输入中文句子直接生成命令。

    - 回车生成；识别失败时用气泡提示并列出可用示例；
    - 内置示例补全，输入时按前缀/包含匹配提示。
    """

    commandGenerated = Signal(str)
    parseFailed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("语义化命令：给所有玩家10个钻石")
        self.setToolTip(
            "支持中文描述直接生成命令，例如：\n"
            + "\n".join(f"· {s}" for s in examples(6)))
        self.setClearButtonEnabled(True)

        self._examples = examples(12)
        self._completer = QCompleter(self._examples, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion)
        self.setCompleter(self._completer)

        self.returnPressed.connect(self._on_return)

    # ------------------------------------------------------------------
    def refresh_examples(self) -> None:
        self._examples = examples(12)
        self._completer.model().setStringList(self._examples)

    def _on_return(self) -> None:
        text = self.text().strip()
        if not text:
            return
        hit = parse_with_pattern(text)
        if hit is None:
            tip = "无法识别的句式。试试：\n" + "\n".join(
                f"· {s}" for s in self._examples[:5])
            QToolTip.showText(self.mapToGlobal(self.rect().bottomLeft()), tip, self)
            self.parseFailed.emit(text)
            return
        self.commandGenerated.emit(parse(text) or hit[0])
        self.clear()


__all__ = ["NlpInput"]
