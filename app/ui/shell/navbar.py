"""顶部导航条：品牌 + 模式切换 + 全局搜索 + 主要动作。

旧版工具条是 11 个纯文字按钮一字排开（「重新生成」「NBT → 输出」
「目标选择器 → 输出」…），既分不清主次，也不知道当前处在哪个模式。
新版把导航分成四段：

1. 我在哪（品牌 / 首页按钮）
2. 我要做什么（命令生成 / 世界编辑 / 双栏 模式切换，带选中态）
3. 找东西（命令搜索 + 中文语义输入，由主窗口注入）
4. 常用动作（命令面板 Ctrl+K、打开存档、打开 NBT、帮助）
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton,
    QVBoxLayout, QWidget,
)

#: 模式：(key, 中文名, tooltip)
MODES: tuple[tuple[str, str, str], ...] = (
    ("command", "命令生成", "只看命令生成器（左侧命令 + 中间面板 + 右侧输出）"),
    ("dual", "双栏联动", "命令生成 + 世界编辑器并排，通过联动总线互推数据"),
    ("editor", "世界编辑", "只看世界 / NBT 编辑器，适合全屏改存档"),
)


class NavBar(QFrame):
    """主窗口顶部导航条。"""

    modeChanged = Signal(str)
    actionTriggered = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NavBar")
        # 品牌是两行小字，工具条按第一行算高度会把标题切掉 → 给个下限
        self.setMinimumHeight(48)
        self._buttons: dict[str, QPushButton] = {}
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 7, 10, 7)
        row.setSpacing(10)

        # ---- 品牌 ----
        brand_box = QVBoxLayout()
        brand_box.setSpacing(0)
        self.brand = QLabel("MCStudio")
        self.brand.setObjectName("NavBrand")
        self.brand.setMinimumHeight(18)
        brand_box.addWidget(self.brand)
        self.brand_sub = QLabel("MC 工坊 · Minecraft Java 1.21.1")
        self.brand_sub.setObjectName("NavSub")
        self.brand_sub.setMinimumHeight(14)
        brand_box.addWidget(self.brand_sub)
        row.addLayout(brand_box)

        self.btn_home = QPushButton("首页")
        self.btn_home.setObjectName("NavAction")
        self.btn_home.setToolTip("回到首页：挑任务、看最近、看上手进度")
        self.btn_home.clicked.connect(
            lambda: self.actionTriggered.emit("home"))
        row.addWidget(self.btn_home)

        row.addWidget(self._separator())

        # ---- 模式切换 ----
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for key, label, tip in MODES:
            button = QPushButton(label)
            button.setObjectName("NavMode")
            button.setCheckable(True)
            button.setToolTip(tip)
            button.clicked.connect(
                lambda _=False, k=key: self.modeChanged.emit(k))
            self._group.addButton(button)
            self._buttons[key] = button
            row.addWidget(button)

        row.addStretch(1)

        # ---- 由主窗口注入搜索框 / 中文输入 ----
        self.middle = QWidget()
        self.middle_layout = QHBoxLayout(self.middle)
        self.middle_layout.setContentsMargins(0, 0, 0, 0)
        self.middle_layout.setSpacing(6)
        row.addWidget(self.middle, 0)

        row.addStretch(1)

        # ---- 常用动作 ----
        self.btn_palette = QPushButton("命令面板  Ctrl+K")
        self.btn_palette.setObjectName("NavAction")
        self.btn_palette.setToolTip("搜索全部命令、页签与动作（Ctrl+K）")
        self.btn_palette.clicked.connect(
            lambda: self.actionTriggered.emit("palette"))
        row.addWidget(self.btn_palette)

        self.btn_open_world = QPushButton("打开存档")
        self.btn_open_world.setObjectName("NavAction")
        self.btn_open_world.setToolTip("打开 .minecraft/saves/世界名（Ctrl+Shift+K）")
        self.btn_open_world.clicked.connect(
            lambda: self.actionTriggered.emit("open-world"))
        row.addWidget(self.btn_open_world)

        self.btn_open_nbt = QPushButton("打开 NBT")
        self.btn_open_nbt.setObjectName("NavAction")
        self.btn_open_nbt.setToolTip("打开 .dat / .nbt / .snbt 文件（Ctrl+Shift+N）")
        self.btn_open_nbt.clicked.connect(
            lambda: self.actionTriggered.emit("open-nbt"))
        row.addWidget(self.btn_open_nbt)

        self.btn_help = QToolButton()
        self.btn_help.setText("帮助 ▾")
        self.btn_help.setObjectName("NavAction")
        self.btn_help.setToolTip("快速上手、快捷键、设置、关于")
        self.btn_help.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.btn_help)
        for label, action in (("快速上手 / 向导 (F1)", "welcome"),
                              ("快捷键一览 (Shift+F1)", "shortcuts"),
                              ("设置…", "settings"),
                              ("关于 MCStudio", "about")):
            act = menu.addAction(label)
            act.triggered.connect(
                lambda _=False, a=action: self.actionTriggered.emit(a))
        self.btn_help.setMenu(menu)
        row.addWidget(self.btn_help)

        #: 「更多」下拉：主窗口把次要动作塞进来，避免工具条变成一排文字
        self.btn_more = QToolButton()
        self.btn_more.setText("更多 ▾")
        self.btn_more.setObjectName("NavAction")
        self.btn_more.setToolTip("次要动作：重新生成、NBT → 输出、折叠输出区…")
        self.btn_more.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_menu = QMenu(self.btn_more)
        self.btn_more.setMenu(self.more_menu)
        row.addWidget(self.btn_more)

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        line.setFixedWidth(3)
        return line

    # ------------------------------------------------------------------
    # 外部接口
    # ------------------------------------------------------------------
    def add_middle_widget(self, widget: QWidget) -> None:
        self.middle_layout.addWidget(widget)

    def set_mode(self, key: str) -> None:
        """同步模式按钮的选中态（不触发 modeChanged）。"""
        button = self._buttons.get(key)
        if button is not None and not button.isChecked():
            button.setChecked(True)

    def current_mode(self) -> str:
        for key, button in self._buttons.items():
            if button.isChecked():
                return key
        return ""


__all__ = ["MODES", "NavBar"]
