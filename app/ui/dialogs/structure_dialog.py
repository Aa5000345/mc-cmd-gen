"""「文件结构总览」：一眼看出这个 NBT 文件里到底有哪些标签。

用来确认某个标签是不是真的不在文件里（例如 `Data` 不见了），
也会给出压缩格式与根标签名，避免把「模组清单」当成世界存档。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from app.core.nbt_binary import NbtDocument
from app.core.snbt import SnbtKind, SnbtNode

TYPE_NAMES = {
    SnbtKind.BYTE: "Byte", SnbtKind.SHORT: "Short", SnbtKind.INT: "Int",
    SnbtKind.LONG: "Long", SnbtKind.FLOAT: "Float", SnbtKind.DOUBLE: "Double",
    SnbtKind.STRING: "String", SnbtKind.BOOL: "Bool",
    SnbtKind.LIST: "List", SnbtKind.COMPOUND: "Compound",
    SnbtKind.BYTE_ARRAY: "ByteArray", SnbtKind.INT_ARRAY: "IntArray",
    SnbtKind.LONG_ARRAY: "LongArray",
}

#: 展开子节点时最多建多少行，避免大文件卡住
MAX_ROWS = 4000


def _scalar_text(node: SnbtNode, limit: int = 48) -> str:
    if node.kind == SnbtKind.STRING:
        text = str(node.value)
        return text if len(text) <= limit else text[:limit] + "…"
    if node.kind == SnbtKind.BOOL:
        return "1b" if node.value else "0b"
    return str(node.value)


def describe(node: SnbtNode) -> str:
    kind = TYPE_NAMES.get(node.kind, str(node.kind))
    if node.is_container:
        return f"{kind}（{len(node.children)} 项）"
    return f"{kind} = {_scalar_text(node)}"


def structure_lines(node: SnbtNode, max_depth: int = 3,
                    max_rows: int = MAX_ROWS) -> list[str]:
    """文本形式的层级结构（供复制到剪贴板）。"""
    out: list[str] = []

    def walk(item: SnbtNode, depth: int) -> None:
        if depth > max_depth or len(out) >= max_rows:
            return
        pad = "  " * depth
        for index, child in enumerate(item.children):
            if len(out) >= max_rows:
                out.append(f"{pad}…… 已省略其余标签")
                return
            key = child.key if child.key is not None else f"[{index}]"
            out.append(f"{pad}{key}  <{describe(child)}>")
            if child.is_container:
                walk(child, depth + 1)

    walk(node, 0)
    return out


class StructureDialog(QDialog):
    """文件结构总览。"""

    def __init__(self, doc: NbtDocument, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setWindowTitle(f"文件结构 — {doc.name}")
        self.resize(760, 600)

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(self.summary)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["名称", "类型", "取值 / 条目数"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.tree.setFont(mono)
        v.addWidget(self.tree, 1)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(mono)
        self.text.setVisible(False)
        v.addWidget(self.text, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        btn_copy = QPushButton("复制结构清单")
        btn_copy.setToolTip("把层级结构复制到剪贴板（便于贴给别人看）")
        btn_copy.clicked.connect(self._copy)
        row.addWidget(btn_copy)
        self.btn_text = QPushButton("文本视图")
        self.btn_text.setCheckable(True)
        self.btn_text.toggled.connect(self._toggle_text)
        row.addWidget(self.btn_text)
        row.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        box.rejected.connect(self.reject)
        row.addWidget(box)
        v.addLayout(row)
        self._fill()

    # ---- 填充 ----

    def _fill(self) -> None:
        root = self.doc.root
        size = self.doc.source_size
        self.summary.setText(
            f"文件：<code>{self.doc.name}</code>　·　"
            f"{self.doc.compression_label}　·　"
            f"{size} 字节<br>"
            f"根标签名：<code>{self.doc.root_name or '（空）'}</code>　·　"
            f"根类型：{TYPE_NAMES.get(root.kind, root.kind)}　·　"
            f"直接子标签：<b>{len(root.children)}</b> 个")

        self.tree.clear()
        rows = 0
        for index, child in enumerate(root.children):
            if rows >= MAX_ROWS:
                self.tree.addTopLevelItem(
                    QTreeWidgetItem(["…", "", "已省略其余标签"]))
                break
            key = child.key if child.key is not None else f"[{index}]"
            item = QTreeWidgetItem([
                str(key), TYPE_NAMES.get(child.kind, str(child.kind)),
                str(len(child.children)) + " 项" if child.is_container
                else _scalar_text(child)])
            self.tree.addTopLevelItem(item)
            rows += 1
            if child.is_container:
                rows = self._populate(item, child, rows, depth=1)
        for column in range(3):
            self.tree.resizeColumnToContents(column)
        self.text.setPlainText("\n".join(structure_lines(root)))

    def _populate(self, parent_item: QTreeWidgetItem, node: SnbtNode,
                  rows: int, depth: int) -> int:
        if depth > 3:
            return rows
        for index, child in enumerate(node.children):
            if rows >= MAX_ROWS:
                child_item = QTreeWidgetItem(["…", "", "已省略其余标签"])
                parent_item.addChild(child_item)
                parent_item.setExpanded(True)
                return rows + 1
            key = child.key if child.key is not None else f"[{index}]"
            item = QTreeWidgetItem([
                str(key), TYPE_NAMES.get(child.kind, str(child.kind)),
                str(len(child.children)) + " 项" if child.is_container
                else _scalar_text(child)])
            parent_item.addChild(item)
            rows += 1
            if child.is_container:
                rows = self._populate(item, child, rows, depth + 1)
        if depth <= 1:
            parent_item.setExpanded(True)
        return rows

    def _toggle_text(self, on: bool) -> None:
        self.text.setVisible(on)
        self.tree.setVisible(not on)

    def _copy(self) -> None:
        QApplication.clipboard().setText("\n".join(structure_lines(
            self.doc.root, max_depth=6)))


__all__ = ["StructureDialog", "structure_lines", "describe", "TYPE_NAMES"]
