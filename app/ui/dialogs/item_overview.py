"""图形化的存档物品浏览：把 NBT 里的背包 / 箱子内容列成表。

支持：

* 按容器分组（背包 / 末影箱 / 手持…）或「合并同类物品」；
* 每行显示中文名、物品 ID、数量、附魔与组件摘要；
* 双击或按钮把物品送进 `/give` 面板，或复制成 `/give` / `/data` 命令。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from app.core.nbt_bridge import (
    ItemStack, aggregate_items, collect_items, find_item_containers,
    stack_to_command, stack_to_snbt, total_count, unique_kinds,
)
from app.core.snbt import SnbtNode
from app.ui.theme import muted_style

COL_SLOT, COL_NAME, COL_ID, COL_COUNT, COL_EXTRA, COL_PATH = range(6)


class ItemOverviewDialog(QDialog):
    """存档物品一览。"""

    #: 请求把某个物品送进 /give 面板
    giveRequested = Signal(object, str)      # (ItemStack, 目标选择器)

    def __init__(self, root: SnbtNode, parent=None, source: str = ""):
        super().__init__(parent)
        self.root = root
        self.source = source
        self._all_items: list[ItemStack] = collect_items(root, source=source)
        self._containers = find_item_containers(root)
        self._current: list[ItemStack] = []
        self._target = "@p"
        #: 「全部 → 输出」时填好，调用方在对话框关闭后读取
        self.outputLines: list[str] = []

        self.setWindowTitle("存档物品一览")
        self.resize(920, 620)
        self._build_ui()
        self._reload()

    # ---- 界面 ----

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.title = QLabel("")
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(QLabel("目标"))
        self.target_combo = QComboBox()
        self.target_combo.setEditable(True)
        for value in ("@p", "@s", "@a", "@r", "@e[type=player]"):
            self.target_combo.addItem(value)
        self.target_combo.setFixedWidth(140)
        self.target_combo.setToolTip("送出后 /give 命令的目标选择器")
        self.target_combo.currentTextChanged.connect(self._on_target_changed)
        head.addWidget(self.target_combo)
        v.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("来源"))
        self.source_combo = QComboBox()
        self.source_combo.setMinimumWidth(260)
        self.source_combo.setToolTip("按容器分组，或把同类物品合并成一行")
        self.source_combo.currentIndexChanged.connect(self._reload)
        row.addWidget(self.source_combo, 1)
        self.merge_check = QCheckBox("合并同类物品")
        self.merge_check.setChecked(True)
        self.merge_check.setToolTip(
            "把名称与组件都相同的物品合并成一行，数量相加")
        self.merge_check.toggled.connect(self._reload)
        row.addWidget(self.merge_check)
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet(muted_style(self.summary_label))
        row.addWidget(self.summary_label)
        v.addLayout(row)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["槽位", "名称", "物品 ID", "数量", "附魔 / 组件", "位置"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_SLOT, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_NAME,
                                    QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(COL_ID, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(COL_COUNT, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_EXTRA, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_PATH,
                                    QHeaderView.ResizeMode.Interactive)
        for column, width in ((COL_SLOT, 58), (COL_NAME, 150), (COL_ID, 210),
                              (COL_COUNT, 64), (COL_PATH, 240)):
            self.table.setColumnWidth(column, width)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemDoubleClicked.connect(
            lambda _item: self._send_to_give())
        v.addWidget(self.table, 1)

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.detail.setFont(mono)
        self.detail.setPlaceholderText(
            "选中一行看完整数据；双击直接送进 /give 面板")
        self.detail.setMaximumHeight(150)
        v.addWidget(self.detail)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.btn_give_panel = QPushButton("→ /give 面板")
        self.btn_give_panel.setToolTip("把选中的物品填进 /give 编辑面板（可继续改）")
        self.btn_give_panel.clicked.connect(self._send_to_give)
        buttons.addWidget(self.btn_give_panel)

        self.btn_give_command = QPushButton("复制 /give 命令")
        self.btn_give_command.setToolTip("按存档里的组件生成 /give 命令并复制")
        self.btn_give_command.clicked.connect(self._copy_give_command)
        buttons.addWidget(self.btn_give_command)

        self.btn_snbt = QPushButton("复制物品 SNBT")
        self.btn_snbt.setToolTip("复制成存档里的 SNBT（可配合 /data 命令）")
        self.btn_snbt.clicked.connect(self._copy_snbt)
        buttons.addWidget(self.btn_snbt)

        self.btn_data = QPushButton("复制 /data 命令")
        self.btn_data.clicked.connect(self._copy_data_command)
        buttons.addWidget(self.btn_data)

        buttons.addStretch(1)
        self.btn_all = QPushButton("全部 → 输出")
        self.btn_all.setToolTip("把当前列表里的每一件物品都生成 /give 命令")
        self.btn_all.clicked.connect(self._emit_all_to_output)
        buttons.addWidget(self.btn_all)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        buttons.addWidget(box)
        v.addLayout(buttons)

    # ---- 数据 ----

    def _reload(self, *_args) -> None:
        selected = self.source_combo.currentData()
        self.source_combo.blockSignals(True)
        if self.source_combo.count() == 0:
            self.source_combo.addItem("全部容器（按出现顺序）", None)
            for index, container in enumerate(self._containers):
                self.source_combo.addItem(
                    f"{container.label}（{len(container.stacks())} 件）", index)
        self.source_combo.blockSignals(False)

        if selected is None:
            items = self._all_items
        else:
            try:
                container = self._containers[int(selected)]
            except (IndexError, TypeError, ValueError):
                container = None
            items = container.stacks() if container is not None else []
        self._current = aggregate_items(items) if self.merge_check.isChecked() \
            else list(items)
        self._fill_table()

    def _fill_table(self) -> None:
        self.table.setRowCount(0)
        for stack in self._current:
            row = self.table.rowCount()
            self.table.insertRow(row)
            slot = "—" if stack.slot is None else str(stack.slot)
            values = (slot, stack.display(), stack.clean_id(),
                      str(stack.count), self._extra_text(stack), stack.path)
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == COL_COUNT:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                elif column == COL_SLOT:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == COL_NAME and stack.display_name:
                    item.setForeground(QBrush(QColor("#A371F7")))
                    item.setToolTip("存档里带有自定义名称")
                self.table.setItem(row, column, item)
            self.table.item(row, COL_ID).setToolTip(
                self._component_tooltip(stack))
            self.table.item(row, COL_EXTRA).setToolTip(
                self._component_tooltip(stack))
        self._update_summary()
        if self._current:
            self.table.selectRow(0)

    def _update_summary(self) -> None:
        items = self._current
        self.summary_label.setText(
            f"{len(items)} 行 · {unique_kinds(items)} 种 · "
            f"共 {total_count(items)} 个")
        self.title.setText(
            f"存档物品一览　{self.source}" if self.source else "存档物品一览")

    @staticmethod
    def _extra_text(stack: ItemStack) -> str:
        parts = []
        for enchant_id, level in stack.enchantments[:3]:
            from app.core.nbt_bridge import enchant_display, roman
            parts.append(f"{enchant_display(enchant_id)} {roman(level)}")
        if len(stack.enchantments) > 3:
            parts.append(f"另 {len(stack.enchantments) - 3} 项")
        others = stack.component_count() - (1 if stack.enchantments else 0)
        if others > 0:
            parts.append(f"{others} 个组件")
        if stack.lore:
            parts.append(f"{len(stack.lore)} 行描述")
        return " · ".join(parts)

    @staticmethod
    def _component_tooltip(stack: ItemStack) -> str:
        if not stack.components:
            return "（没有组件）"
        lines = []
        for cid, value in stack.components.items():
            text = str(value)
            if len(text) > 90:
                text = text[:87] + "…"
            lines.append(f"{cid} = {text}")
        return "\n".join(lines)

    # ---- 选择与动作 ----

    def _selected_rows(self) -> list[int]:
        return sorted({index.row()
                       for index in self.table.selectionModel().selectedRows()})

    def selected_items(self) -> list[ItemStack]:
        return [self._current[row] for row in self._selected_rows()
                if 0 <= row < len(self._current)]

    def _on_selection_changed(self) -> None:
        items = self.selected_items()
        if not items:
            self.detail.setPlainText("")
            return
        stack = items[0]
        lines = [f"{stack.display()}（{stack.clean_id()}）× {stack.count}"]
        if stack.slot is not None:
            lines.append(f"槽位：{stack.slot}")
        if stack.path:
            lines.append(f"位置：{stack.path}")
        if stack.lore:
            lines.append("描述：" + " / ".join(stack.lore))
        lines.append(f"/give：{stack_to_command(stack, self._target)}")
        lines.append("SNBT：" + stack_to_snbt(stack))
        self.detail.setPlainText("\n".join(lines))

    def _on_target_changed(self, text: str) -> None:
        self._target = text.strip() or "@p"
        self._on_selection_changed()

    # ---- 送出 ----

    def _send_to_give(self) -> None:
        items = self.selected_items()
        if not items:
            self.detail.setPlainText("先在上面选中一件或多件物品。")
            return
        self.giveRequested.emit(items[0], self._target)

    def _copy_give_command(self) -> None:
        items = self.selected_items()
        if not items:
            self.detail.setPlainText("先在上面选中一件或多件物品。")
            return
        text = "\n".join(stack_to_command(s, self._target) for s in items)
        QApplication.clipboard().setText(text)
        self.detail.setPlainText(
            f"✔ 已复制 {len(items)} 条 /give 命令到剪贴板：\n\n{text}")

    def _copy_snbt(self) -> None:
        items = self.selected_items()
        if not items:
            self.detail.setPlainText("先在上面选中一件或多件物品。")
            return
        text = "\n".join(stack_to_snbt(s) for s in items)
        QApplication.clipboard().setText(text)
        # 旧版复制成功什么都不显示（只有 /give 那条会写详情区）
        self.detail.setPlainText(
            f"✔ 已复制 {len(items)} 条物品 SNBT 到剪贴板：\n\n{text}")

    def _copy_data_command(self) -> None:
        from app.core.nbt_bridge import stack_to_data_command
        items = self.selected_items()
        if not items:
            self.detail.setPlainText("先在上面选中一件或多件物品。")
            return
        text = "\n".join(stack_to_data_command(s) for s in items)
        QApplication.clipboard().setText(text)
        self.detail.setPlainText(
            f"✔ 已复制 {len(items)} 条 /data 命令到剪贴板：\n\n{text}")

    def _emit_all_to_output(self) -> None:
        if not self._current:
            return
        self.outputLines = [stack_to_command(s, self._target)
                            for s in self._current]
        self.accept()


__all__ = ["ItemOverviewDialog", "COL_SLOT", "COL_NAME", "COL_ID",
           "COL_COUNT", "COL_EXTRA", "COL_PATH"]
