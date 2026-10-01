"""首页：把「今天想做什么」摆在用户面前。

设计动机
--------
旧版首屏直接把人丢进一个**空白的世界编辑器**：15 个页签全是空表格，
写入口是灰的，用户既不知道能干什么，也不知道从哪一步开始。

首页把最强的三个入口做成大卡片（造物品 / 生成命令 / 编辑存档），
外加一个会随操作自动打勾的「3 步上手」清单，以及最近命令 / 最近存档，
所有按钮都直接跳到对应面板 —— 首屏不再有「无路可走」的状态。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from app.ui.shell.cards import Banner, Card, SectionTitle

#: 任务卡：(标题, 说明, 快捷键胶囊, 路由)
TASKS: tuple[tuple[str, str, str, str], ...] = (
    ("造一件自定义物品",
     "用 /give 加名字、附魔、属性、无法破坏 —— 图形化拼物品组件。",
     "/give", "cmd:give"),
    ("生成一条命令",
     "44 条命令（summon / execute / setblock …）全都表单化，实时校验。",
     "Ctrl+K", "palette"),
    ("编辑世界存档",
     "打开 .minecraft/saves/世界名：改时间、天气、玩家、实体、容器。",
     "Ctrl+Shift+K", "world"),
    ("编辑 NBT 文件",
     "打开 .dat / .nbt / .snbt / .schem，像 NBTExplorer 一样改。",
     "Ctrl+Shift+N", "nbt-open"),
    ("数据包工程",
     "图形化写函数、管理目录，一键导出成可加载的数据包。",
     "批量生成", "datapack"),
    ("用中文写命令",
     "直接说「给所有玩家 10 个钻石」，先给预览再决定要不要用。",
     "中文", "nlp"),
)

#: 上手清单：(key, 文案)
CHECKLIST: tuple[tuple[str, str], ...] = (
    ("generate", "生成第一条命令（选一条命令，填好参数）"),
    ("copy", "复制到剪贴板（游戏里直接粘贴）"),
    ("open", "打开一个世界存档或 NBT 文件"),
)


class _ChecklistRow(QWidget):
    """一行清单：状态点 + 文案 + 「去做」按钮。"""

    actionRequested = Signal()

    def __init__(self, text: str, action_label: str, parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(8)
        self.dot = QLabel("○")
        self.dot.setFixedWidth(16)
        self.dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.dot)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        row.addWidget(self.label, 1)
        self.button = QPushButton(action_label)
        self.button.setObjectName("Secondary")
        self.button.clicked.connect(self.actionRequested.emit)
        row.addWidget(self.button)
        self.set_done(False)

    def set_done(self, done: bool) -> None:
        self.dot.setText("✔" if done else "○")
        self.dot.setStyleSheet(
            "color: #3FB950; font-size: 14px;" if done
            else "color: #6E7681; font-size: 14px;")
        font = self.label.font()
        font.setStrikeOut(done)
        self.label.setFont(font)
        self.button.setText("已完成" if done else "去做")
        self.button.setEnabled(not done)


class HomePage(QWidget):
    """首页页签（主窗口把它放在中间栏的第一个页签）。"""

    #: 请求跳转：路由见 TASKS 的第四列
    navigateRequested = Signal(str)
    #: 在输出区填入历史命令
    commandActivated = Signal(str)
    #: 直接打开某个世界路径
    worldActivated = Signal(str)
    #: 需要主窗口刷新首页数据（最近命令 / 存档 / 清单）
    refreshRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: dict[str, _ChecklistRow] = {}
        self._build_ui()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        page = QWidget()
        scroll.setWidget(page)
        v = QVBoxLayout(page)
        v.setContentsMargins(18, 16, 18, 18)
        v.setSpacing(14)

        # ---- 抬头 ----
        hero = QVBoxLayout()
        hero.setSpacing(2)
        title = QLabel("MC 工坊 · 想做什么？")
        title.setObjectName("HeroTitle")
        hero.addWidget(title)
        sub = QLabel(
            "面向 Minecraft Java 1.21.1：一边生成命令，一边直接改存档与 NBT。"
            "下面挑一个任务开始，或者按 Ctrl+K 搜命令。")
        sub.setObjectName("HeroSub")
        sub.setWordWrap(True)
        hero.addWidget(sub)
        v.addLayout(hero)

        # ---- 首次使用提示条 ----
        self.banner = Banner(
            "第一次用？最短路径是：左边选一条命令 → 填参数 → 右侧点「复制命令」，"
            "粘到游戏聊天栏即可。")
        btn_give = QPushButton("去 /give 造物品")
        btn_give.setObjectName("Primary")
        btn_give.clicked.connect(lambda: self.navigateRequested.emit("cmd:give"))
        self.banner.add_action(btn_give)
        btn_palette = QPushButton("搜索命令 (Ctrl+K)")
        btn_palette.setObjectName("Secondary")
        btn_palette.clicked.connect(
            lambda: self.navigateRequested.emit("palette"))
        self.banner.add_action(btn_palette)
        v.addWidget(self.banner)

        # ---- 任务卡 ----
        v.addWidget(SectionTitle("常见任务"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for index, (title_, desc, key, route) in enumerate(TASKS):
            card = Card(title_, desc, key)
            card.clicked.connect(
                lambda _=False, r=route: self.navigateRequested.emit(r))
            grid.addWidget(card, index // 3, index % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        v.addLayout(grid)

        # ---- 清单 + 最近 ----
        columns = QHBoxLayout()
        columns.setSpacing(18)

        checklist_box = QVBoxLayout()
        checklist_box.setSpacing(6)
        checklist_box.addWidget(SectionTitle("3 步上手"))
        self.checklist_rows: list[_ChecklistRow] = []
        actions = {"generate": "去生成", "copy": "去复制", "open": "去打开"}
        routes = {"generate": "cmd:give", "copy": "copy-current",
                  "open": "world"}
        for key, text in CHECKLIST:
            row = _ChecklistRow(text, actions[key])
            row.actionRequested.connect(
                lambda k=key, r=routes[key]: self.navigateRequested.emit(
                    r if k != "generate" else "cmd:give"))
            self._rows[key] = row
            self.checklist_rows.append(row)
            checklist_box.addWidget(row)
        self.progress_label = QLabel("已完成 0/3")
        self.progress_label.setObjectName("CardDesc")
        checklist_box.addWidget(self.progress_label)
        checklist_box.addStretch(1)
        left = QWidget()
        left.setLayout(checklist_box)
        columns.addWidget(left, 1)

        recent_box = QVBoxLayout()
        recent_box.setSpacing(6)
        recent_box.addWidget(SectionTitle("最近命令"))
        self.recent_list = QListWidget()
        self.recent_list.setObjectName("HomeRecent")
        self.recent_list.setMaximumHeight(120)
        self.recent_list.itemClicked.connect(self._on_recent_command)
        recent_box.addWidget(self.recent_list)

        recent_box.addWidget(SectionTitle("最近存档"))
        self.world_list = QListWidget()
        self.world_list.setMaximumHeight(120)
        self.world_list.itemClicked.connect(self._on_recent_world)
        recent_box.addWidget(self.world_list)
        right = QWidget()
        right.setLayout(recent_box)
        columns.addWidget(right, 1)
        v.addLayout(columns)

        v.addStretch(1)
        footer = QLabel(
            "快捷键：Ctrl+K 命令面板 · Ctrl+R 重新生成 · Ctrl+Shift+C 复制输出 · "
            "Ctrl+Shift+K 打开存档 · F1 上手向导 · Shift+F1 全部快捷键")
        footer.setObjectName("CardDesc")
        footer.setWordWrap(True)
        v.addWidget(footer)

    # ------------------------------------------------------------------
    # 数据刷新
    # ------------------------------------------------------------------
    def set_checklist(self, done: set[str]) -> None:
        for key, row in self._rows.items():
            row.set_done(key in done)
        self.progress_label.setText(f"已完成 {len(done & set(self._rows))}/3")
        finished = len(done & set(self._rows)) >= 3
        self.banner.setVisible(not finished)

    def set_recent_commands(self, commands: list[str]) -> None:
        self.recent_list.clear()
        for cmd in commands[:6]:
            item = QListWidgetItem(cmd if cmd.startswith("/") else f"/{cmd}")
            item.setData(Qt.ItemDataRole.UserRole, cmd)
            item.setToolTip("点击填入右侧输出区")
            self.recent_list.addItem(item)
        if not commands:
            item = QListWidgetItem("还没有历史命令 —— 生成一条就会出现在这里")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent_list.addItem(item)

    def set_recent_worlds(self, entries: list[dict]) -> None:
        self.world_list.clear()
        for entry in entries[:6]:
            label = f"{entry.get('name') or '未命名'}　DV {entry.get('data_version')}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(entry.get("path", "")))
            item.setToolTip(str(entry.get("path", "")))
            self.world_list.addItem(item)
        if not entries:
            item = QListWidgetItem("还没有打开过存档 —— 点上面「编辑世界存档」")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.world_list.addItem(item)

    # ------------------------------------------------------------------
    def _on_recent_command(self, item) -> None:
        cmd = item.data(Qt.ItemDataRole.UserRole)
        if cmd:
            self.commandActivated.emit(str(cmd))

    def _on_recent_world(self, item) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.worldActivated.emit(str(path))


__all__ = ["CHECKLIST", "HomePage", "TASKS"]
