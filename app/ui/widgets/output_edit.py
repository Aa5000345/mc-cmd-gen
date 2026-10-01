"""可拖放输出的命令文本框。

除了普通文本编辑，还接受来自命令面板 / ID 速查 / 片段库的拖放内容：
    application/x-mccmdgen-command
    application/x-mccmdgen-fragment
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPlainTextEdit

from app.ui.mime import MIME_COMMAND, MIME_FRAGMENT, read_mime


class CommandOutputEdit(QPlainTextEdit):
    """输出区：接受拖放，双击整行可复制。"""

    textDropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setPlaceholderText("生成的命令会显示在这里（可直接拖入命令 / 片段）。")

    # ---- 拖放 ----
    def _extract(self, mime) -> str:
        payload = read_mime(mime, MIME_COMMAND)
        if payload is not None:
            text = str(payload.get("text", "")).strip()
            if text:
                return text
        payload = read_mime(mime, MIME_FRAGMENT)
        if payload is not None:
            text = str(payload.get("command", "") or payload.get("text", "")).strip()
            if not text:
                kind = str(payload.get("kind", "command"))
                body = payload.get("payload")
                if isinstance(body, dict):
                    text = str(body.get(kind, "") or body.get("command", "")
                               or body.get("snbt", "")).strip()
            if text:
                return text
        return mime.text().strip()

    def _accepts(self, mime) -> bool:        return (mime.hasFormat(MIME_COMMAND) or mime.hasFormat(MIME_FRAGMENT)
                or bool(mime.text().strip()))

    def dragEnterEvent(self, event) -> None:
        if self._accepts(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self._accepts(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if not self._accepts(event.mimeData()):
            super().dropEvent(event)
            return
        text = self._extract(event.mimeData())
        if not text:
            return
        event.acceptProposedAction()
        # 拖放默认是替换全部内容；这里尊重用户的 Ctrl/Shift 修饰键
        if self.textCursor().hasSelection():
            self.textCursor().insertText(text)
        else:
            self.setPlainText(text)
        self.textDropped.emit(text)


__all__ = ["CommandOutputEdit"]
