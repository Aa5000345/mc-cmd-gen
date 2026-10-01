"""命令 diff 对话框。"""
from __future__ import annotations
import difflib

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QPlainTextEdit, QDialogButtonBox,
)


class DiffDialog(QDialog):
    def __init__(self, old: str, new: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("命令对比")
        self.resize(900, 560)

        v = QVBoxLayout(self)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(QFont("Consolas", 10))
        v.addWidget(self.view, 1)

        diff = difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile="旧", tofile="新", lineterm="")
        self.view.setPlainText("\n".join(diff))

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self.accept)
        v.addWidget(bb)


__all__ = ["DiffDialog"]