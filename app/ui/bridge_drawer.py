"""联动总线 / 桥接抽屉。

职责
----
* 保存「联动上下文」：`current_world / current_player / current_dimension /
  current_pos / selected_item / selected_entity / selected_block /
  selected_nbt_path / selected_container`；
* 接收两侧面板发来的**结构化对象**（`BridgeOperation` / `ItemStack` /
  `EntityData` / 坐标 / NBT 路径），转成对侧能用的形式；
* 中间抽屉窗口：显示「转换来源 → 目标」「差异对照」「撤销入口」；
* 顶部联动开关：关掉后两个面板完全独立（不发也不收）。

底部「桥接日志」显示每条记录：来源、目标、差异条数、备份、撤销入口。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.core import bridge as bridge_mod
from app.core.bridge import BridgeOperation, BridgeResult
from app.core.nbt_bridge import EntityData, ItemStack
from app.core.snbt import SnbtNode, serialize
from app.ui.theme import hint_style


@dataclass
class LinkContext:
    """统一上下文：两面板共享的「当前在看什么」。"""

    current_world: str = ""
    current_player: str = ""
    current_dimension: str = "minecraft:overworld"
    current_pos: tuple[float, float, float] = (0.0, 64.0, 0.0)
    selected_item: Any = None
    selected_entity: Any = None
    selected_block: str = ""
    selected_nbt_path: str = ""
    selected_nbt_value: str = ""
    selected_container: str = ""
    selected_map: int | None = None

    def describe(self) -> list[tuple[str, str]]:
        def short(value: Any, limit: int = 64) -> str:
            text = "" if value is None else str(value)
            return text if len(text) <= limit else text[:limit - 1] + "…"

        item = self.selected_item
        entity = self.selected_entity
        return [
            ("当前世界", short(self.current_world) or "（未打开）"),
            ("当前玩家", short(self.current_player) or "（未选中）"),
            ("当前维度", self.current_dimension or "—"),
            ("当前坐标", " ".join(f"{v:.2f}" for v in self.current_pos)),
            ("选中物品", short(getattr(item, "summary", lambda: item)())
             if item is not None else "（未选中）"),
            ("选中实体", short(getattr(entity, "summary", lambda: entity)())
             if entity is not None else "（未选中）"),
            ("选中方块", self.selected_block or "（未选中）"),
            ("选中 NBT 路径", self.selected_nbt_path or "（未选中）"),
            ("选中容器", self.selected_container or "（未选中）"),
            ("选中地图", "" if self.selected_map is None
             else f"map_{self.selected_map}"),
        ]

    def is_empty(self) -> bool:
        return not any((self.current_world, self.current_player,
                        self.selected_item, self.selected_entity,
                        self.selected_block, self.selected_nbt_path,
                        self.selected_container))


class BridgeDrawer(QWidget):
    """联动总线抽屉：上下文 + 待执行操作 + 桥接日志。"""

    #: 请求把操作应用到世界（由主窗口转给 WorldPanel / WorldSession）
    applyRequested = Signal(object)
    #: 请求把操作送到命令面板（生成命令）
    commandsRequested = Signal(object)
    #: 请求撤销
    undoRequested = Signal()
    #: 联动开关变化
    linkToggled = Signal(bool)
    #: 状态提示
    statusMessage = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.context = LinkContext()
        self.pending: list[BridgeOperation] = []
        self._build_ui()

    # ---- 界面 ----

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        head = QHBoxLayout()
        self.link_check = QCheckBox("启用联动总线")
        self.link_check.setChecked(True)
        self.link_check.setToolTip(
            "关闭后两个面板完全独立：不会自动传递选中项，也不会互相发送数据")
        self.link_check.toggled.connect(self.linkToggled.emit)
        head.addWidget(self.link_check)
        head.addStretch(1)
        self.hint = QLabel("跨面板操作都会先预览差异，再确认写入")
        self.hint.setStyleSheet(hint_style(self.hint))
        head.addWidget(self.hint)
        v.addLayout(head)

        split = QSplitter(Qt.Orientation.Vertical)

        self.context_table = QTableWidget(0, 2)
        self.context_table.setHorizontalHeaderLabels(["上下文", "取值"])
        self.context_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.context_table.verticalHeader().setVisible(False)
        self.context_table.horizontalHeader().setStretchLastSection(True)
        self.context_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        split.addWidget(self.context_table)

        self.pending_table = QTableWidget(0, 4)
        self.pending_table.setHorizontalHeaderLabels(
            ["操作", "来源", "目标", "命令"])
        self.pending_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pending_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.pending_table.verticalHeader().setVisible(False)
        self.pending_table.horizontalHeader().setStretchLastSection(True)
        self.pending_table.itemDoubleClicked.connect(
            lambda _item: self.on_preview())
        split.addWidget(self.pending_table)

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setPlaceholderText("桥接日志：来源 → 目标 / 差异 / 撤销入口")
        self.log_edit.setMaximumHeight(150)
        split.addWidget(self.log_edit)
        split.setSizes([150, 160, 150])
        v.addWidget(split, 1)

        # 右栏只有 240~400px：按钮分两行，否则会被压成「看来源/目 世界 (备」
        row = QHBoxLayout()
        self.btn_apply = QPushButton("应用到世界（备份后）")
        self.btn_apply.setObjectName("Primary")
        self.btn_apply.setToolTip("预览差异 → 确认 → 自动备份 → 原子写入")
        self.btn_apply.clicked.connect(self.on_apply)
        row.addWidget(self.btn_apply, 1)
        self.btn_commands = QPushButton("生成命令 → 命令面板")
        self.btn_commands.setToolTip(
            "把「存档 → 命令」方向的操作变成命令，填进命令输出区")
        self.btn_commands.clicked.connect(self.on_commands)
        row.addWidget(self.btn_commands, 1)
        v.addLayout(row)

        row = QHBoxLayout()
        row.setSpacing(6)
        # 这个按钮只列出「来源 → 目标」，不算差异；旧名字「预览差异」会让
        # 用户以为差异已经核对过了，改名并把真正的差异指引写进 tooltip
        self.btn_preview = QPushButton("查看来源/目标")
        self.btn_preview.setToolTip(
            "列出队列里每个操作的来源、目标与命令（不是逐条差异）。"
            "真正的差异在「应用到世界」的确认框里，之后也会显示在"
            "编辑器的「差异」页签。")
        self.btn_preview.clicked.connect(self.on_preview)
        row.addWidget(self.btn_preview)
        self.btn_copy = QPushButton("复制命令")
        self.btn_copy.clicked.connect(self.on_copy)
        row.addWidget(self.btn_copy)
        self.btn_undo = QPushButton("撤销")
        self.btn_undo.setToolTip("撤销上一次通过联动总线写入的改动")
        self.btn_undo.clicked.connect(self.undoRequested.emit)
        row.addWidget(self.btn_undo)
        self.btn_clear = QPushButton("清空队列")
        self.btn_clear.clicked.connect(self.clear_pending)
        row.addWidget(self.btn_clear)
        row.addStretch(1)
        self.count_label = QLabel("队列 0 项")
        self.count_label.setStyleSheet(hint_style(self.count_label))
        row.addWidget(self.count_label)
        v.addLayout(row)

    # ---- 联动开关 ----

    def linked(self) -> bool:
        return self.link_check.isChecked()

    def set_linked(self, value: bool) -> None:
        self.link_check.setChecked(bool(value))

    # ---- 上下文 ----

    def set_context(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            if hasattr(self.context, key):
                setattr(self.context, key, value)
        self.refresh_context()

    def refresh_context(self) -> None:
        self.context_table.setRowCount(0)
        for label, value in self.context.describe():
            index = self.context_table.rowCount()
            self.context_table.insertRow(index)
            self.context_table.setItem(index, 0, QTableWidgetItem(label))
            self.context_table.setItem(index, 1, QTableWidgetItem(value))

    # ---- 队列 ----

    def push(self, operation: BridgeOperation | None) -> bool:
        """接收一条结构化操作（联动关闭时直接忽略）。"""
        if operation is None:
            return False
        if not self.linked():
            self.statusMessage.emit("联动总线已关闭，操作没有传递。")
            return False
        self.pending.append(operation)
        self._refresh_pending()
        self.statusMessage.emit(
            f"联动总线收到：{operation.summary()}")
        return True

    def push_many(self, operations: list[BridgeOperation]) -> int:
        count = 0
        for operation in operations:
            if self.push(operation):
                count += 1
        return count

    def clear_pending(self) -> None:
        self.pending.clear()
        self._refresh_pending()

    def _refresh_pending(self) -> None:
        self.pending_table.setRowCount(0)
        for operation in self.pending:
            index = self.pending_table.rowCount()
            self.pending_table.insertRow(index)
            values = [operation.title, operation.source,
                      operation.target, operation.command_text()
                      .replace("\n", " ; ")[:160]]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, operation)
                self.pending_table.setItem(index, column, item)
        self.count_label.setText(f"队列 {len(self.pending)} 项")

    def selected_operations(self) -> list[BridgeOperation]:
        rows = self.pending_table.selectionModel().selectedRows() \
            if self.pending_table.selectionModel() else []
        if not rows:
            return list(self.pending)
        out = []
        for index in rows:
            item = self.pending_table.item(index.row(), 0)
            if item is None:
                continue
            operation = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(operation, BridgeOperation):
                out.append(operation)
        return out

    def selected_commands(self) -> list[str]:
        commands: list[str] = []
        for operation in self.selected_operations():
            commands.extend(operation.commands)
        return commands

    # ---- 动作 ----

    def on_preview(self) -> None:
        operations = self.selected_operations()
        if not operations:
            self.statusMessage.emit("队列为空，没有可预览的操作。")
            return
        lines = []
        for operation in operations:
            lines.append(f"■ {operation.summary()}")
            if operation.notes:
                lines.extend(f"    · {note}" for note in operation.notes)
            if operation.commands:
                lines.extend(f"    $ {cmd}" for cmd in operation.commands)
            for key, value in operation.payload.items():
                if isinstance(value, (str, int, float, bool)) and value != "":
                    lines.append(f"    {key} = {value}")
        self.log_edit.setPlainText("\n".join(lines))
        self.statusMessage.emit(
            f"已列出 {len(operations)} 个操作的转换来源与目标。"
            "（这里只是**来源与目标**摘要；真正的逐条差异在点"
            "「应用到世界」后的确认框里，或编辑器「差异」页签。）")

    def on_apply(self) -> None:
        operations = self.selected_operations()
        if not operations:
            self.statusMessage.emit("队列为空。")
            return
        for operation in operations:
            self.applyRequested.emit(operation)

    def on_commands(self) -> None:
        operations = self.selected_operations()
        if not operations:
            self.statusMessage.emit("队列为空。")
            return
        commands = [cmd for operation in operations
                    for cmd in operation.commands]
        if not commands:
            self.statusMessage.emit(
                "这些操作是「命令 → 存档」方向，没有可生成的命令；"
                "请用「应用到世界」。")
            return
        self.commandsRequested.emit("\n".join(commands))
        self.statusMessage.emit(f"已把 {len(commands)} 条命令送到命令面板。")

    def on_copy(self) -> None:
        commands = self.selected_commands()
        if not commands:
            self.statusMessage.emit("没有可复制的命令。")
            return
        QGuiApplication.clipboard().setText("\n".join(commands))
        self.statusMessage.emit(f"已复制 {len(commands)} 条命令到剪贴板。")

    # ---- 日志 ----

    def log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {text}"
        current = self.log_edit.toPlainText()
        lines = [line] + current.splitlines()
        self.log_edit.setPlainText("\n".join(lines[:400]))

    def log_operations(self, operations: list[BridgeOperation],
                       result: BridgeResult | None = None) -> None:
        for operation in operations:
            text = (f"{operation.source} → {operation.target} · "
                    f"{operation.summary()}")
            self.log(text)
        if result is not None:
            self.log(result.summary())

    # ---- 便捷构造（编辑器 → 命令面板） ----

    def send_item(self, stack: ItemStack, target: str = "@p") -> bool:
        return self.push(bridge_mod.item_to_give_operation(stack, target))

    def send_entity(self, entity: EntityData, pos: str = "~ ~ ~") -> bool:
        return self.push(bridge_mod.entity_to_summon_operation(entity, pos))

    def send_command(self, command: str, *, session=None) -> bool:
        operation = bridge_mod.command_to_operation(command, session=session)
        if operation is None:
            self.statusMessage.emit(f"命令面板的操作无法转换：{command}")
            return False
        operation.runner = bridge_mod.operation_runner(operation)
        return self.push(operation)

    def send_nbt_path(self, path: str, value: str) -> bool:
        return self.push(BridgeOperation(
            kind="data", source="editor", target="level",
            label=f"NBT 路径 → /data（{path}）",
            payload={"path": path, "value": value},
            commands=[bridge_mod.nbt_path_to_command(path, value)]))

    def send_coords(self, x: float, y: float, z: float) -> bool:
        return self.push(bridge_mod.coords_to_commands(x, y, z))

    def send_player(self, player: Any, command: str = "say hi") -> bool:
        return self.push(bridge_mod.player_to_execute_commands(player, command))

    def send_world_rules(self, world: Any) -> bool:
        return self.push(bridge_mod.world_rules_to_commands(world))


def nbt_node_to_text(node: SnbtNode | None) -> str:
    if node is None:
        return ""
    return serialize(node, pretty=False)


__all__ = ["BridgeDrawer", "LinkContext"]
