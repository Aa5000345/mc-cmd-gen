"""上手向导：把「最短成功路径」一次讲清楚。

只在用户按 F1 / 点「帮助 → 快速上手」时打开（**不自动弹窗**），
避免打断已熟悉工具的用户，也避免在自动化测试里弹出模态窗口。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
    QWidget,
)

from app import branding
from app.ui.shell.cards import SectionTitle

STEPS: tuple[tuple[str, str], ...] = (
    ("选一条命令",
     "首页挑「造一件自定义物品」，或在左侧命令树里点 /summon、/execute。"
     "不确定名字就按 Ctrl+K 搜索。"),
    ("填参数",
     "表单里的每一项都有 tooltip；物品、实体、方块可以用中文名搜索。"
     "填错了顶部提示行会直接说明问题，不用猜。"),
    ("复制到游戏",
     "右侧「命令输出」就是结果，点「复制命令」粘到游戏聊天栏；"
     "想直接写进存档就点「→ 存档」，写入前会先给你看差异。"),
)

SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("Ctrl+K", "命令面板：搜命令 / 功能 / 页签"),
    ("Ctrl+R", "重新生成命令"),
    ("Ctrl+Shift+C", "复制输出命令"),
    ("Ctrl+Shift+K", "打开世界存档"),
    ("Ctrl+Shift+N", "打开 NBT / .dat 文件"),
    ("Ctrl+Shift+1/2/3", "命令模式 / 编辑模式 / 双栏联动"),
    ("F1 / Shift+F1", "本向导 / 快捷键一览"),
)


class WelcomeDialog(QDialog):
    """上手向导（非模态，可反复打开）。"""

    #: 请求跳转到某个路由（与 HomePage 的路由一致）
    navigateRequested = Signal(str)
    #: 「启动时直接显示首页」被改动（主窗口负责落盘）
    homePreferenceChanged = Signal(bool)

    def __init__(self, parent: QWidget | None = None,
                 home_enabled: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"快速上手 — {branding.APP_NAME}")
        self.setMinimumWidth(620)
        self._build_ui()
        self.check_home.setChecked(bool(home_enabled))

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(10)

        title = QLabel("三步生成第一条命令")
        title.setObjectName("HeroTitle")
        v.addWidget(title)
        sub = QLabel(
            f"{branding.APP_NAME}（{branding.APP_NAME_CN}）把命令生成、世界存档编辑、"
            "NBT 文件编辑放在同一个窗口里，任何时候都可以按 Ctrl+K 搜索功能。")
        sub.setObjectName("HeroSub")
        sub.setWordWrap(True)
        v.addWidget(sub)

        for index, (name, detail) in enumerate(STEPS, 1):
            row = QHBoxLayout()
            row.setSpacing(10)
            number = QLabel(str(index))
            number.setFixedSize(24, 24)
            number.setAlignment(Qt.AlignmentFlag.AlignCenter)
            number.setObjectName("CardKey")
            row.addWidget(number, 0, Qt.AlignmentFlag.AlignTop)
            text = QLabel(f"<b>{name}</b><br>{detail}")
            text.setWordWrap(True)
            text.setObjectName("GuideText")
            row.addWidget(text, 1)
            v.addLayout(row)

        v.addWidget(SectionTitle("常用快捷键"))
        for keys, detail in SHORTCUTS:
            line = QLabel(f"{keys}　—　{detail}")
            line.setObjectName("CardDesc")
            v.addWidget(line)

        hint = QLabel(
            "提示：存档默认只读，所有写入都会先备份到 backups/ 并需要你确认，"
            "改坏了可以回滚。")
        hint.setObjectName("CardDesc")
        hint.setWordWrap(True)
        v.addWidget(hint)

        self.check_home = QCheckBox("启动时直接显示首页（推荐）")
        self.check_home.setChecked(True)
        self.check_home.setToolTip(
            "关掉后启动会回到上次的工作区与页签")
        self.check_home.toggled.connect(self.homePreferenceChanged.emit)
        v.addWidget(self.check_home)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for label, route, primary in (
                ("造一件物品", "cmd:give", True),
                ("编辑世界存档", "world", False),
                ("知道了", "", False)):
            button = QPushButton(label)
            button.setObjectName("Primary" if primary else "Secondary")
            if route:
                button.clicked.connect(
                    lambda _=False, r=route: self._go(r))
            else:
                button.clicked.connect(self.close)
            buttons.addWidget(button)
        v.addLayout(buttons)

    def _go(self, route: str) -> None:
        self.close()
        self.navigateRequested.emit(route)

    def show_home_enabled(self) -> bool:
        return self.check_home.isChecked()


__all__ = ["SHORTCUTS", "STEPS", "WelcomeDialog"]
