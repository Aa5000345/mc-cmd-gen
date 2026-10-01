"""世界编辑器的辅助对话框（排版统一走 theme 里的设计 token）。

* `SceneDialog` —— 一键世界场景
* `GrowItemsDialog` —— 往容器里写物品（首个空槽 / 合并 / 指定槽 / 覆盖）
* `BlueprintExportDialog` —— 蓝图导出（规格 / 是否含实体 / 目标目录）
* `CrossWorldCopyDialog` —— 跨存档复制（玩家数据 / 物品 / 进度 / 地图）

所有对话框都遵守同一套版式：说明行 + 表单 + 右对齐按钮行，
表单边距与间距来自 `app.ui.theme.MARGINS / SPACING`。
"""
from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.ui.theme import (
    FONTS, MARGINS, SPACING, hint_style, muted_style, polish_form,
    polish_tables,
)

#: 各类对话框的统一最小宽度，避免窄窗口里文字被挤成换行乱码
DIALOG_WIDTH = 460


def _dialog_shell(dialog: QDialog, title: str, hint: str = "",
                  width: int = DIALOG_WIDTH, height: int = 320) -> QVBoxLayout:
    """所有对话框共用的外壳：标题说明 + 内容区 + 按钮行。"""
    dialog.setWindowTitle(title)
    dialog.resize(width, height)
    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(*MARGINS["page"])
    outer.setSpacing(SPACING["md"])
    if hint:
        label = QLabel(hint)
        label.setWordWrap(True)
        label.setStyleSheet(muted_style(dialog, size="label"))
        outer.addWidget(label)
    return outer


def _button_row(dialog: QDialog, ok_text: str = "确定",
                cancel_text: str = "取消") -> tuple[QDialogButtonBox, QPushButton]:
    box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok
        | QDialogButtonBox.StandardButton.Cancel, parent=dialog)
    button = box.button(QDialogButtonBox.StandardButton.Ok)
    button.setText(ok_text)
    button.setDefault(True)
    box.button(QDialogButtonBox.StandardButton.Cancel).setText(cancel_text)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box, button


class SceneDialog(QDialog):
    """一键世界场景。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        outer = _dialog_shell(
            self, "一键世界场景",
            "勾选需要的项目，确认后会先备份、再把整组设置一次写入。",
            height=400)

        options = QGroupBox("开关项")
        options_layout = QVBoxLayout(options)
        options_layout.setContentsMargins(*MARGINS["section"])
        options_layout.setSpacing(SPACING["xs"])
        self.permanent_day = QCheckBox("永久白天（DayTime=6000 且关闭昼夜循环）")
        self.permanent_day.setChecked(True)
        self.no_weather = QCheckBox("禁止下雨 / 天气变化")
        self.no_weather.setChecked(True)
        self.peaceful = QCheckBox("难度设为和平")
        self.peaceful.setChecked(True)
        self.clear_timer = QCheckBox("清空天气计时（避免马上变天）")
        self.clear_timer.setChecked(True)
        for widget in (self.permanent_day, self.no_weather, self.peaceful,
                       self.clear_timer):
            options_layout.addWidget(widget)
        outer.addWidget(options)

        form = QFormLayout()
        polish_form(form)
        self.spawn = QLineEdit("0 64 0")
        self.spawn.setPlaceholderText("x y z，留空表示不改出生点")
        form.addRow("出生点", self.spawn)
        self.border = QLineEdit("")
        self.border.setPlaceholderText("例如 2000，留空不改")
        form.addRow("世界边界边长", self.border)
        outer.addLayout(form)

        outer.addWidget(self._preview_label())
        box, _ok = _button_row(self, "应用（会先备份）")
        outer.addWidget(box)

    def _preview_label(self) -> QWidget:
        self.preview = QLabel("")
        self.preview.setStyleSheet(hint_style(self))
        self.preview.setWordWrap(True)
        for widget in (self.spawn, self.border):
            widget.textChanged.connect(self._refresh_preview)
        for widget in (self.permanent_day, self.no_weather, self.peaceful,
                       self.clear_timer):
            widget.toggled.connect(self._refresh_preview)
        self._refresh_preview()
        return self.preview

    def _refresh_preview(self) -> None:
        parts = []
        if self.permanent_day.isChecked():
            parts.append("锁定白天")
        if self.no_weather.isChecked():
            parts.append("禁止天气")
        if self.peaceful.isChecked():
            parts.append("和平")
        if self.spawn.text().strip():
            parts.append(f"出生点 {self.spawn.text().strip()}")
        if self.border.text().strip():
            parts.append(f"边界 {self.border.text().strip()}")
        self.preview.setText("将应用：" + ("、".join(parts) or "（没有勾选）"))

    def payload(self) -> dict:
        data = {
            "permanent_day": self.permanent_day.isChecked(),
            "no_weather": self.no_weather.isChecked(),
            "peaceful": self.peaceful.isChecked(),
            "clear_weather_timer": self.clear_timer.isChecked(),
        }
        if self.spawn.text().strip():
            data["spawn"] = self.spawn.text().strip()
        if self.border.text().strip():
            try:
                data["border_size"] = float(self.border.text().strip())
            except ValueError:
                pass
        return data


class GrowItemsDialog(QDialog):
    """往容器里写物品（首个空槽 / 合并 / 指定槽 / 覆盖）。"""

    def __init__(self, parent=None, *, default_item: str = "minecraft:stone",
                 container: str = "Inventory") -> None:
        super().__init__(parent)
        outer = _dialog_shell(
            self, "往容器里写物品",
            "写入会在确认差异后事务化落盘；物品组件用 `id=值` 语法，"
            "例如 custom_name=\"神剑\",damage=5。",
            height=420)

        form = QFormLayout()
        polish_form(form)

        from app.core.data import store as data_store
        self.item_combo = QComboBox()
        self.item_combo.setEditable(True)
        self.item_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for entry in data_store().items()[:4000]:
            if isinstance(entry, dict) and entry.get("id"):
                self.item_combo.addItem(
                    f"{entry['id']} — {entry.get('display', '')}",
                    entry["id"])
        self.item_combo.setCurrentText(default_item)
        form.addRow("物品", self.item_combo)

        self.count = QSpinBox()
        self.count.setRange(1, 6400)
        self.count.setValue(64)
        form.addRow("数量", self.count)

        self.mode = QComboBox()
        for key, label in (("first_empty", "首个空槽（优先合并同类）"),
                           ("merge", "只合并同类堆叠"),
                           ("slot", "写进指定槽位"),
                           ("overwrite", "覆盖指定槽位")):
            self.mode.addItem(label, key)
        form.addRow("写入方式", self.mode)

        self.slot = QSpinBox()
        self.slot.setRange(-1, 255)
        self.slot.setValue(-1)
        self.slot.setSpecialValueText("自动")
        form.addRow("槽位", self.slot)

        self.container = QLineEdit(container or "Inventory")
        self.container.setToolTip(
            "容器路径，例如 Inventory / EnderItems / Items")
        form.addRow("容器路径", self.container)

        self.components = QLineEdit()
        self.components.setPlaceholderText(
            'custom_name="神剑",damage=5（留空表示普通物品）')
        form.addRow("物品组件", self.components)
        outer.addLayout(form)

        self.summary = QLabel("")
        self.summary.setStyleSheet(hint_style(self))
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)
        for widget in (self.count, self.slot):
            widget.valueChanged.connect(self._refresh_summary)
        self.mode.currentIndexChanged.connect(self._refresh_summary)
        self.item_combo.currentTextChanged.connect(self._refresh_summary)
        self.container.textChanged.connect(self._refresh_summary)
        self._refresh_summary()

        box, _ok = _button_row(self, "写入")
        outer.addWidget(box)

    def _refresh_summary(self) -> None:
        item = str(self.item_combo.currentData()
                   or self.item_combo.currentText()).split(" — ", 1)[0]
        mode = str(self.mode.currentData())
        slot = self.slot.value()
        where = self.container.text().strip() or "Inventory"
        target = {"first_empty": "首个可用空槽", "merge": "已有的同类堆叠",
                  "slot": f"槽位 {slot}" if slot >= 0 else "首个空槽",
                  "overwrite": f"槽位 {slot}（覆盖）"}.get(mode, mode)
        self.summary.setText(
            f"将把 {item} ×{self.count.value()} 写进 {where} 的 {target}")

    def values(self) -> tuple[str, int, str, int | None, str, str]:
        item = self.item_combo.currentData() or self.item_combo.currentText()
        item = str(item).split(" — ", 1)[0].strip()
        slot = self.slot.value()
        return (item, int(self.count.value()), str(self.mode.currentData()),
                None if slot < 0 else slot,
                self.container.text().strip() or "Inventory",
                self.components.text().strip())


class BlueprintExportDialog(QDialog):
    """蓝图导出选项。"""

    def __init__(self, parent=None, *, name: str = "我的建筑",
                 block_count: int = 0, size: tuple[int, int, int] = (1, 1, 1),
                 has_entities: bool = False) -> None:
        super().__init__(parent)
        outer = _dialog_shell(
            self, "导出蓝图 / 结构模板",
            "会写出 .nbt（原版结构规范）与 .schem（Sponge v2），"
            "并存到应用数据目录的 user_data/blueprints/。",
            height=380)

        form = QFormLayout()
        polish_form(form)
        self.name_edit = QLineEdit(name)
        form.addRow("名称", self.name_edit)

        self.formats = QComboBox()
        for key, label in (("both", "同时导出 .nbt 与 .schem"),
                           ("nbt", "只导出 .nbt（原版结构）"),
                           ("schem", "只导出 .schem（WorldEdit / 投影）")):
            self.formats.addItem(label, key)
        form.addRow("格式", self.formats)

        self.include_nbt = QCheckBox("保留方块实体数据（箱子 / 熔炉内容）")
        self.include_nbt.setChecked(True)
        form.addRow("", self.include_nbt)

        self.commands = QComboBox()
        for key, label in (("setblock", "逐方块 /setblock（最精确）"),
                           ("fill", "整体 /fill（先铺底）"),
                           ("place", "place template（需要放进数据包）"),
                           ("none", "不生成命令")):
            self.commands.addItem(label, key)
        form.addRow("同时生成", self.commands)

        self.limit = QSpinBox()
        self.limit.setRange(1, 200000)
        self.limit.setValue(4096)
        self.limit.setSuffix(" 条")
        form.addRow("命令上限", self.limit)
        outer.addLayout(form)

        info = QLabel(
            f"当前选区：{size[0]}×{size[1]}×{size[2]}，"
            f"{block_count} 个方块"
            + ("，含方块实体" if has_entities else "，无方块实体数据"))
        info.setStyleSheet(muted_style(self))
        info.setWordWrap(True)
        outer.addWidget(info)

        box, _ok = _button_row(self, "导出")
        outer.addWidget(box)

    def values(self) -> dict:
        return {
            "name": self.name_edit.text().strip() or "我的建筑",
            "format": str(self.formats.currentData()),
            "include_nbt": self.include_nbt.isChecked(),
            "command_mode": str(self.commands.currentData()),
            "limit": int(self.limit.value()),
        }


class CrossWorldCopyDialog(QDialog):
    """跨存档复制：从另一个存档挑玩家数据 / 物品 / 进度 / 地图。"""

    def __init__(self, parent=None, *, source_world=None) -> None:
        super().__init__(parent)
        outer = _dialog_shell(
            self, "跨存档复制",
            "选择要搬运的内容，确认后会写入当前存档（先备份）。"
            "物品也可以改成生成 /give 命令，由游戏执行。",
            width=560, height=460)

        self.source_label = QLabel("")
        self.source_label.setStyleSheet(muted_style(self))
        self.source_label.setWordWrap(True)
        outer.addWidget(self.source_label)
        self._source = source_world
        if source_world is not None:
            self.source_label.setText(
                f"来源：{source_world.name}（{source_world.path}）")

        self.contents = QListWidget()
        self.contents.setSelectionMode(
            QListWidget.SelectionMode.NoSelection)
        for key, label, note in (
                ("inventory", "玩家背包", "把来源玩家的背包物品写进目标玩家"),
                ("ender", "末影箱", "写进 EnderItems"),
                ("effects", "状态效果", "复制 active_effects"),
                ("attributes", "属性", "复制 Attributes（含修饰符）"),
                ("advancements", "进度", "复制 advancements/*.json"),
                ("stats", "统计", "复制 stats/*.json"),
                ("maps", "地图", "复制 data/map_*.dat"),
        ):
            item = QListWidgetItem(f"{label} — {note}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.contents.addItem(item)
        self.contents.setMinimumHeight(180)
        outer.addWidget(self.contents, 1)

        row = QHBoxLayout()
        row.setSpacing(SPACING["sm"])
        self.as_commands = QCheckBox("改成生成命令（不写存档）")
        row.addWidget(self.as_commands)
        row.addStretch(1)
        select_all = QPushButton("全选")
        select_all.clicked.connect(lambda: self._set_all(True))
        clear = QPushButton("全不选")
        clear.clicked.connect(lambda: self._set_all(False))
        row.addWidget(select_all)
        row.addWidget(clear)
        outer.addLayout(row)

        box, _ok = _button_row(self, "复制")
        outer.addWidget(box)

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for index in range(self.contents.count()):
            self.contents.item(index).setCheckState(state)

    def payload(self) -> dict:
        keys = []
        for index in range(self.contents.count()):
            item = self.contents.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                keys.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return {"contents": keys,
                "as_commands": self.as_commands.isChecked()}


class NlpWorldDialog(QDialog):
    """中文自然语言世界编辑：输入 → 解析 → 预览差异。"""

    def __init__(self, parent=None, *, examples: list[str] | None = None,
                 session=None) -> None:
        super().__init__(parent)
        outer = _dialog_shell(
            self, "中文世界编辑",
            "用中文描述要改什么，先解析成操作、列出差异，确认后才写入。",
            width=560, height=460)

        row = QHBoxLayout()
        row.setSpacing(SPACING["sm"])
        self.input = QLineEdit()
        self.input.setPlaceholderText("例如：把时间设置为夜晚")
        self.input.returnPressed.connect(self.parse)
        row.addWidget(self.input, 1)
        self.btn_parse = QPushButton("解析并预览")
        self.btn_parse.clicked.connect(self.parse)
        row.addWidget(self.btn_parse)
        outer.addLayout(row)

        self.examples = QListWidget()
        self.examples.setMaximumHeight(96)
        for text in (examples or [
                "把时间设置为夜晚", "天气改成下雨", "难度改成困难",
                "永久白天", "禁止下雨", "在出生点生成 3 只僵尸",
                "把背包塞满钻石剑", "世界边界设置为 2000"]):
            self.examples.addItem(text)
        self.examples.itemDoubleClicked.connect(self._use_example)
        outer.addWidget(QLabel("双击示例填入输入框"))
        outer.addWidget(self.examples)

        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setPlaceholderText("解析结果与差异会显示在这里")
        outer.addWidget(self.report, 1)

        self._session = session
        self._operations: list = []
        box, ok = _button_row(self, "写入存档")
        self.btn_apply = ok
        ok.setEnabled(False)
        ok.clicked.disconnect()
        ok.clicked.connect(self._on_apply)
        outer.addWidget(box)

    # ---- 交互 ----

    def _use_example(self, item: QListWidgetItem) -> None:
        self.input.setText(item.text())
        self.parse()

    def parse(self) -> None:
        from app.core import bridge as bridge_mod
        text = self.input.text().strip()
        if not text:
            self.report.setPlainText("请输入一句中文描述。")
            return
        operations = bridge_mod.parse_cn_world_request(
            text, session=self._session)
        self._operations = operations
        if not operations:
            self.report.setPlainText(
                "无法识别的请求。可以试试：把时间设置为夜晚 / "
                "天气改成下雨 / 难度改成困难 / 在出生点生成 3 只僵尸 / "
                "把背包塞满钻石剑")
            self.btn_apply.setEnabled(False)
            return
        lines = [f"解析出 {len(operations)} 个操作："]
        for operation in operations:
            lines.append(f"  · {operation.summary()}")
        if self._session is not None and getattr(self._session, "world", None):
            lines.append("")
            lines.append("差异预览：")
            for operation in operations[:6]:
                preview = bridge_mod.preview_operation(self._session, operation)
                lines.append(f"  [{operation.title}] {preview.message}")
        self.report.setPlainText("\n".join(lines))
        self.btn_apply.setEnabled(True)

    def _on_apply(self) -> None:
        self.accept()

    def operations(self) -> list:
        return list(self._operations)


class RegionChunkWriteDialog(QDialog):
    """区域区块写入（实验功能，默认 dry-run）。"""

    def __init__(self, parent=None, *, region_name: str = "",
                 chunk: tuple[int, int] | None = None) -> None:
        super().__init__(parent)
        outer = _dialog_shell(
            self, "写入区域区块（实验功能）",
            "只支持「等长或更短」的原地替换：新区块占用的扇区数不能超过原位置。"
            "不满足会直接拒绝，绝不会移动其它区块。",
            width=560, height=480)

        form = QFormLayout()
        polish_form(form)
        self.region_label = QLabel(region_name or "（未选择区域文件）")
        self.region_label.setStyleSheet(muted_style(self))
        self.region_label.setWordWrap(True)
        form.addRow("区域文件", self.region_label)

        row = QHBoxLayout()
        row.setSpacing(SPACING["xs"])
        self.chunk_x = QSpinBox()
        self.chunk_x.setRange(-2000000, 2000000)
        self.chunk_z = QSpinBox()
        self.chunk_z.setRange(-2000000, 2000000)
        if chunk is not None:
            self.chunk_x.setValue(int(chunk[0]))
            self.chunk_z.setValue(int(chunk[1]))
        row.addWidget(QLabel("X"))
        row.addWidget(self.chunk_x, 1)
        row.addWidget(QLabel("Z"))
        row.addWidget(self.chunk_z, 1)
        form.addRow("区块坐标", row)

        self.compression = QComboBox()
        for key, label in (("zlib", "zlib（默认，原版常用）"),
                           ("gzip", "gzip"), ("none", "不压缩（更占空间）")):
            self.compression.addItem(label, key)
        form.addRow("压缩方式", self.compression)
        outer.addLayout(form)

        outer.addWidget(QLabel("要写入的 NBT（完整区块内容，谨慎修改）："))
        self.snbt = QPlainTextEdit()
        self.snbt.setFont(QFont("Consolas", FONTS["mono"]))
        self.snbt.setPlaceholderText("{DataVersion:3955, xPos:0, zPos:0, …}")
        outer.addWidget(self.snbt, 1)

        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMaximumHeight(110)
        self.report.setFont(QFont("Consolas", FONTS["mono"]))
        self.report.setPlaceholderText("点「可行性检查」后这里显示结果")
        outer.addWidget(self.report)

        row2 = QHBoxLayout()
        row2.setSpacing(SPACING["xs"])
        self.btn_load_current = QPushButton("载入原区块")
        self.btn_load_current.setToolTip("把该区块当前内容读进编辑框（推荐先看一眼）")
        self.btn_load_current.clicked.connect(self.load_current)
        row2.addWidget(self.btn_load_current)
        self.btn_plan = QPushButton("可行性检查 (dry-run)")
        self.btn_plan.clicked.connect(self.check_plan)
        row2.addWidget(self.btn_plan)
        row2.addStretch(1)
        outer.addLayout(row2)

        box, ok = _button_row(self, "写入（先备份）")
        self.btn_write = ok
        ok.setEnabled(False)
        ok.clicked.disconnect()
        ok.clicked.connect(self.accept)
        outer.addWidget(box)

        self._path = None
        self._plan = None

    # ---- 对外 ----

    def set_target(self, path, name: str = "") -> None:
        self._path = path
        self.region_label.setText(name or str(path))

    def values(self) -> dict:
        return {"path": self._path,
                "x": int(self.chunk_x.value()),
                "z": int(self.chunk_z.value()),
                "snbt": self.snbt.toPlainText().strip(),
                "compression": str(self.compression.currentData())}

    def plan(self):
        return self._plan

    # ---- 交互 ----

    def load_current(self) -> None:
        from app.core.snbt import serialize
        from app.core.world import read_region_chunk
        if self._path is None:
            self.report.setPlainText("请先在区域页签选中一个 .mca 文件。")
            return
        node = read_region_chunk(self._path, int(self.chunk_x.value()),
                                 int(self.chunk_z.value()))
        if node is None:
            self.report.setPlainText(
                "这个区块在文件里不存在或读不出来（本工具不会新建区块）。")
            return
        self.snbt.setPlainText(serialize(node, pretty=True))
        self.report.setPlainText(
            f"已载入区块 ({self.chunk_x.value()},"
            f"{self.chunk_z.value()}) 的当前内容。")

    def check_plan(self) -> None:
        from app.core.region_io import plan_chunk_write
        from app.core.snbt import SnbtParser
        if self._path is None:
            self.report.setPlainText("请先在区域页签选中一个 .mca 文件。")
            return
        text = self.snbt.toPlainText().strip()
        if not text:
            self.report.setPlainText("请先点「载入原区块」或粘贴 NBT。")
            return
        try:
            node = SnbtParser(text).parse()
        except Exception as e:
            self.report.setPlainText(f"NBT 解析失败：{e}")
            self.btn_write.setEnabled(False)
            return
        plan = plan_chunk_write(self._path, int(self.chunk_x.value()),
                                int(self.chunk_z.value()), node,
                                compression=str(self.compression.currentData()))
        self._plan = plan
        lines = [plan.summary()]
        if plan.diff:
            lines.append("差异（最多 30 条）：")
            lines.extend(f"  {entry}" for entry in plan.diff[:30])
        elif plan.ok:
            lines.append("（内容没有变化）")
        self.report.setPlainText("\n".join(lines))
        self.btn_write.setEnabled(plan.ok and bool(plan.diff))


class PresetDialog(QDialog):
    """工作区预设：一键套用世界规则组合。"""

    def __init__(self, parent=None, *, session=None) -> None:
        super().__init__(parent)
        outer = _dialog_shell(
            self, "工作区预设",
            "内置 4 套常用规则组合，也可以把当前世界设置另存成自己的预设。",
            width=600, height=440)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["预设", "内容", "来源", "说明"])
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        polish_tables([self.table], self)
        outer.addWidget(self.table, 1)

        self.info = QLabel("")
        self.info.setStyleSheet(hint_style(self))
        self.info.setWordWrap(True)
        outer.addWidget(self.info)
        self._session = session
        self._chosen: dict | None = None

        row = QHBoxLayout()
        row.setSpacing(SPACING["xs"])
        self.btn_use = QPushButton("套用选中预设")
        self.btn_use.clicked.connect(self._on_use)
        row.addWidget(self.btn_use)
        self.btn_save = QPushButton("把当前世界设置存为预设…")
        self.btn_save.clicked.connect(self._on_save_current)
        row.addWidget(self.btn_save)
        row.addStretch(1)
        box, _ok = _button_row(self, "关闭")
        row.addWidget(box)
        outer.addLayout(row)
        self._reload()

    def _reload(self) -> None:
        from app.core.region_io import list_presets, preset_summary
        self.table.setRowCount(0)
        for preset in list_presets():
            index = self.table.rowCount()
            self.table.insertRow(index)
            values = [preset.get("name", preset.get("id", "")),
                      preset_summary(preset),
                      "内置" if preset.get("builtin") else "用户",
                      str(preset.get("note", ""))]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, preset)
                self.table.setItem(index, column, item)

    def _selected(self) -> dict | None:
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_use(self) -> None:
        preset = self._selected()
        if preset is None:
            self.info.setText("请先选中一个预设。")
            return
        self._chosen = preset
        self.accept()

    def _on_save_current(self) -> None:
        from app.core.region_io import save_preset
        world = getattr(self._session, "world", None) if self._session else None
        if world is None:
            self.info.setText("需要先打开世界才能保存当前设置。")
            return
        name, ok = QInputDialog.getText(self, "保存预设", "预设名：",
                                        text="我的世界预设")
        if not ok or not name.strip():
            return
        overview = world.overview
        payload = {
            "permanent_day": overview.gamerules.get("doDaylightCycle")
            == "false",
            "no_weather": overview.gamerules.get("doWeatherCycle") == "false",
            "peaceful": overview.difficulty == "peaceful",
            "allow_commands": overview.allow_commands,
            "difficulty": overview.difficulty,
            "gamerules": dict(overview.gamerules),
        }
        if overview.world_border:
            payload["worldborder"] = {
                "size": overview.world_border.get("size"),
                "damage": overview.world_border.get("damage")}
        path = save_preset({"id": name.strip(), "name": name.strip(),
                            "note": "由当前世界保存", "payload": payload})
        self.info.setText(f"已保存到 {path.name}")
        self._reload()

    def preset(self) -> dict | None:
        return self._chosen


__all__ = [
    "DIALOG_WIDTH", "SceneDialog", "GrowItemsDialog",
    "BlueprintExportDialog", "CrossWorldCopyDialog", "NlpWorldDialog",
    "RegionChunkWriteDialog", "PresetDialog",
]
