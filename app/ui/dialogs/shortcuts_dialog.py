"""快捷键帮助对话框。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QDialogButtonBox, QHeaderView,
)

from app.ui.theme import muted_style


SHORTCUTS: list[tuple[str, str]] = [
    # ---- 找东西 / 上手 ----
    ("Ctrl+K",        "命令面板：搜命令 / 页签 / 动作"),
    ("F1",            "快速上手向导"),
    ("Shift+F1",      "本快捷键帮助（等同 Ctrl+/）"),    ("Ctrl+Shift+H",  "回到首页（任务卡 / 上手清单 / 最近）"),
    ("Ctrl+P",        "聚焦「查找命令」搜索框"),
    # ---- 生成与输出 ----
    ("Ctrl+R",        "重新生成当前面板命令"),
    ("Ctrl+O",        "聚焦输出区"),
    ("Ctrl+Shift+C",  "复制输出命令"),
    ("Ctrl+Shift+F",  "把输出命令加入收藏"),
    ("Ctrl+Shift+B",  "把输出命令送进联动总线（→ 存档）"),
    ("F5",            "校验当前命令"),
    ("Ctrl+1..9",     "切换命令面板（按面板顺序）"),
    ("Ctrl+G",        "跳到 /give 面板"),
    # ---- 世界 / NBT ----
    ("Ctrl+Shift+K",  "打开世界存档"),
    ("Ctrl+Shift+W",  "打开世界编辑器"),
    ("Ctrl+Shift+N",  "打开 NBT / .dat 文件"),
    ("Ctrl+Shift+M",  "新建 NBT 文档"),
    ("Ctrl+S",        "保存 NBT 文件（按原压缩格式写回）"),
    ("Ctrl+Shift+S",  "NBT 另存为…"),
    # ---- 窗口 ----
    ("Ctrl+Shift+D",  "编辑器独立成窗口 / 收回"),
    ("Ctrl+Shift+O",  "折叠 / 展开输出区"),
    ("Ctrl+Shift+1/2/3", "工作区：命令生成 / 世界编辑 / 双栏联动"),
    ("Ctrl+/",        "显示本快捷键帮助"),
    ("Ctrl+Q",        "退出应用"),
]


class ShortcutsDialog(QDialog):
    """展示全部快捷键的只读表格。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("快捷键")
        self.resize(620, 560)

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        tip = QLabel("Ctrl+/ 或 Shift+F1 可随时打开本对话框；Ctrl+K 打开命令面板，"
                     "F1 打开快速上手。")
        tip.setStyleSheet(muted_style(tip))
        tip.setWordWrap(True)
        v.addWidget(tip)

        table = QTableWidget(len(SHORTCUTS), 2)
        table.setHorizontalHeaderLabels(["快捷键", "功能"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setAlternatingRowColors(True)

        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)

        for row, (key, desc) in enumerate(SHORTCUTS):
            item_key = QTableWidgetItem(key)
            item_key.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(row, 0, item_key)

            item_desc = QTableWidgetItem(desc)
            item_desc.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(row, 1, item_desc)

        v.addWidget(table, 1)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("知道了")
        bb.accepted.connect(self.accept)
        v.addWidget(bb)


__all__ = ["ShortcutsDialog", "SHORTCUTS"]