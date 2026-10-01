"""世界编辑器页签：世界总览 + 玩家。

拆成 mixin 是为了让 `world_panel.py` 保持可读（原来一个文件 2400 行）。
所有 mixin 只依赖 `WorldPanelBase` 提供的公共设施：
`self.session / self.world() / self.tabs / self.statusMessage / self._run_bridge()`。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSpinBox, QSplitter, QToolButton,
    QVBoxLayout, QWidget,
)

from app.core import bridge as bridge_mod
from app.core.nbt_bridge import collect_items, find_item_containers, \
    stack_to_command, stack_to_data_command
from app.core.snbt import SnbtKind, SnbtNode
from app.core.world import (
    COMMON_GAMERULES, DIFFICULTIES, PLAYER_FIELDS, game_time_label,
)
from app.ui.theme import MARGINS, SPACING, hint_style, muted_style
from app.ui.world_common import (
    FilterBar, SectionBox, add_row, make_editable_table, make_table,
    row_payload,
)


class OverviewTabMixin:
    """「世界总览」页签：只读摘要 + 可写设置 + 游戏规则。"""

    def _build_overview_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(*MARGINS["page"])
        outer.setSpacing(SPACING["sm"])

        self.overview_filter = FilterBar(
            placeholder="筛选世界信息（Ctrl+F）", table=None)
        split = QSplitter(Qt.Orientation.Vertical)

        # ---- 只读摘要 ----
        summary_box = SectionBox(
            "世界信息", "这些是只读摘要；写入入口在下面的「世界设置」里。")
        self.overview_table = make_table(["项目", "取值"])
        self.overview_filter.add_table(self.overview_table)
        summary_box.add_widget(self.overview_filter)
        summary_box.add_widget(self.overview_table, 1)
        split.addWidget(summary_box)

        # ---- 可写设置 ----
        settings = SectionBox(
            "世界设置", "解锁编辑后才能写入；每次写入都会先备份。")
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(SPACING["md"])
        form.setVerticalSpacing(SPACING["sm"])
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.level_name_edit = QLineEdit()
        self.level_name_edit.setPlaceholderText("存档显示名")
        form.addRow("存档名称", self.level_name_edit)

        time_row = QHBoxLayout()
        time_row.setSpacing(SPACING["xs"])
        self.time_spin = QSpinBox()
        self.time_spin.setRange(0, 24000 * 1000)
        self.time_spin.setSingleStep(1000)
        self.time_spin.setSuffix(" 刻")
        time_row.addWidget(self.time_spin, 1)
        for label, value in (("白天", 1000), ("中午", 6000), ("夜晚", 13000),
                             ("午夜", 18000)):
            quick = QToolButton()
            quick.setText(label)
            quick.setAutoRaise(True)
            quick.setToolTip(f"填入 {value} 刻")
            quick.clicked.connect(
                lambda _=False, v=value: self.time_spin.setValue(v))
            time_row.addWidget(quick)
            # 只读时也不该改输入值（否则用户以为已经改好了）
            self._extra_write_widgets.append(quick)
        self.btn_set_time = QPushButton("设为该时间")
        self.btn_set_time.clicked.connect(self._on_set_time)
        time_row.addWidget(self.btn_set_time)
        form.addRow("世界时间", time_row)

        weather_row = QHBoxLayout()
        weather_row.setSpacing(SPACING["xs"])
        self.weather_combo = QComboBox()
        for key, label in (("clear", "晴天"), ("rain", "下雨"),
                           ("thunder", "雷暴")):
            self.weather_combo.addItem(label, key)
        weather_row.addWidget(self.weather_combo, 1)
        self.weather_duration = QSpinBox()
        self.weather_duration.setRange(0, 1000000)
        self.weather_duration.setValue(6000)
        self.weather_duration.setSuffix(" 刻")
        weather_row.addWidget(self.weather_duration)
        self.btn_set_weather = QPushButton("设为该天气")
        self.btn_set_weather.clicked.connect(self._on_set_weather)
        weather_row.addWidget(self.btn_set_weather)
        form.addRow("天气", weather_row)

        difficulty_row = QHBoxLayout()
        difficulty_row.setSpacing(SPACING["xs"])
        self.difficulty_combo = QComboBox()
        for value, label in sorted(DIFFICULTIES.items()):
            self.difficulty_combo.addItem(label, value)
        difficulty_row.addWidget(self.difficulty_combo, 1)
        self.difficulty_lock = QCheckBox("锁定难度")
        difficulty_row.addWidget(self.difficulty_lock)
        self.btn_set_difficulty = QPushButton("应用")
        self.btn_set_difficulty.clicked.connect(self._on_set_difficulty)
        difficulty_row.addWidget(self.btn_set_difficulty)
        form.addRow("难度", difficulty_row)

        spawn_row = QHBoxLayout()
        spawn_row.setSpacing(SPACING["xs"])
        self.spawn_x = QSpinBox(); self.spawn_x.setRange(-30000000, 30000000)
        self.spawn_y = QSpinBox(); self.spawn_y.setRange(-2048, 2048)
        self.spawn_z = QSpinBox(); self.spawn_z.setRange(-30000000, 30000000)
        for label, widget in (("X", self.spawn_x), ("Y", self.spawn_y),
                              ("Z", self.spawn_z)):
            spawn_row.addWidget(QLabel(label))
            spawn_row.addWidget(widget, 1)
        self.btn_set_spawn = QPushButton("设为出生点")
        self.btn_set_spawn.clicked.connect(self._on_set_spawn)
        spawn_row.addWidget(self.btn_set_spawn)
        form.addRow("出生点", spawn_row)

        border_row = QHBoxLayout()
        border_row.setSpacing(SPACING["xs"])
        self.border_size = QDoubleSpinBox()
        self.border_size.setRange(1.0, 60000000.0)
        self.border_size.setDecimals(0)
        self.border_size.setGroupSeparatorShown(True)
        border_row.addWidget(QLabel("边长"))
        border_row.addWidget(self.border_size, 1)
        self.border_damage = QDoubleSpinBox()
        self.border_damage.setRange(0.0, 100.0)
        border_row.addWidget(QLabel("伤害"))
        border_row.addWidget(self.border_damage, 1)
        self.btn_set_border = QPushButton("应用边界")
        self.btn_set_border.clicked.connect(self._on_set_border)
        border_row.addWidget(self.btn_set_border)
        form.addRow("世界边界", border_row)

        misc_row = QHBoxLayout()
        misc_row.setSpacing(SPACING["xs"])
        self.allow_commands = QCheckBox("允许命令（单人存档作弊）")
        misc_row.addWidget(self.allow_commands)
        self.btn_set_commands = QPushButton("应用")
        self.btn_set_commands.clicked.connect(self._on_set_commands)
        misc_row.addWidget(self.btn_set_commands)
        misc_row.addStretch(1)
        self.btn_preset = QPushButton("工作区预设…")
        self.btn_preset.setToolTip(
            "内置「创造沙盒 / 硬核生存 / 建筑展示 / PvP 竞技场」，"
            "也可以把当前设置存成自己的预设")
        self.btn_preset.clicked.connect(self._on_apply_preset)
        misc_row.addWidget(self.btn_preset)
        self.btn_scene = QPushButton("一键世界场景…")
        self.btn_scene.setToolTip(
            "永久白天 + 禁止下雨 + 和平 + 出生点 + 边界，一次套用")
        self.btn_scene.clicked.connect(self._on_apply_scene)
        misc_row.addWidget(self.btn_scene)
        form.addRow("其它", misc_row)

        settings.add_layout(form)
        self.overview_hint = QLabel(
            "提示：写入前会先显示差异；确认后自动备份到 backups/<时间戳>/。")
        self.overview_hint.setStyleSheet(hint_style(page))
        self.overview_hint.setWordWrap(True)
        settings.add_widget(self.overview_hint)
        split.addWidget(settings)

        # ---- 游戏规则 ----
        rules_box = SectionBox(
            "游戏规则", "双击「取值」列即可修改；空值表示删除该规则。")
        self.rules_filter = FilterBar(placeholder="筛选规则（Ctrl+F）")
        self.rules_table = make_editable_table(["规则", "取值", "说明"])
        self.rules_filter.add_table(self.rules_table)
        self.rules_table.itemChanged.connect(self._on_rule_edited)
        rules_box.add_widget(self.rules_filter)
        rules_box.add_widget(self.rules_table, 1)
        split.addWidget(rules_box)

        split.setSizes([280, 360, 220])
        outer.addWidget(split, 1)
        return page

    # ---- 刷新 ----

    def refresh_overview(self) -> None:
        world = self.world()
        self.overview_table.setRowCount(0)
        self.rules_table.blockSignals(True)
        self.rules_table.setRowCount(0)
        if world is None or not world.is_open():
            self.rules_table.blockSignals(False)
            return

        overview = world.overview
        for key, value in overview.summary_rows():
            add_row(self.overview_table, [key, value])

        self.level_name_edit.setText(overview.name)
        self.time_spin.setValue(max(0, min(self.time_spin.maximum(),
                                           overview.day_time)))
        index = self.weather_combo.findData(overview.weather)
        self.weather_combo.setCurrentIndex(index if index >= 0 else 0)
        reverse = {name: value for value, name in DIFFICULTIES.items()}
        index = self.difficulty_combo.findData(
            reverse.get(overview.difficulty, 2))
        self.difficulty_combo.setCurrentIndex(index if index >= 0 else 2)
        self.difficulty_lock.setChecked(overview.difficulty_locked)
        self.spawn_x.setValue(overview.spawn[0])
        self.spawn_y.setValue(overview.spawn[1])
        self.spawn_z.setValue(overview.spawn[2])
        border = overview.world_border or {}
        self.border_size.setValue(float(border.get("size", 59999968.0)))
        self.border_damage.setValue(float(border.get("damage", 0.2)))
        self.allow_commands.setChecked(overview.allow_commands)

        known = {name: note for name, _label, note in COMMON_GAMERULES}
        for name in sorted(overview.gamerules):
            add_row(self.rules_table,
                    [name, overview.gamerules[name], known.get(name, "")])
        self.rules_table.blockSignals(False)

        compatibility = overview.compatibility
        self.overview_hint.setText(
            f"{compatibility.message}"
            if compatibility.message else
            "提示：写入前会先显示差异；确认后自动备份到 backups/<时间戳>/。")
        self.overview_hint.setStyleSheet(
            hint_style(self.overview_hint)
            if compatibility.level == "ok" else
            "color: #D29922; font-size: 11pt;")

    # ---- 写入回调 ----

    def _on_set_time(self) -> None:
        value = int(self.time_spin.value())
        operation = bridge_mod.BridgeOperation(
            kind="time", label=f"世界时间设为 {value}",
            source="editor", target="level",
            payload={"action": "set", "value": value},
            runner=lambda s: _apply_time(s, value))
        self._run_bridge(operation)

    def _on_set_weather(self) -> None:
        weather = str(self.weather_combo.currentData())
        duration = int(self.weather_duration.value())
        operation = bridge_mod.BridgeOperation(
            kind="weather", label=f"天气设为 {weather}",
            source="editor", target="level",
            payload={"weather": weather, "duration": duration},
            runner=lambda s: _apply_weather(s, weather, duration))
        self._run_bridge(operation)

    def _on_set_difficulty(self) -> None:
        value = int(self.difficulty_combo.currentData())
        locked = self.difficulty_lock.isChecked()
        label = DIFFICULTIES.get(value, str(value))
        operation = bridge_mod.BridgeOperation(
            kind="player_field", label=f"难度设为 {label}",
            source="editor", target="level",
            payload={"difficulty": value, "locked": locked},
            runner=lambda s: _apply_difficulty(s, value, locked))
        self._run_bridge(operation)

    def _on_set_spawn(self) -> None:
        x = self.spawn_x.value()
        y = self.spawn_y.value()
        z = self.spawn_z.value()
        operation = bridge_mod.BridgeOperation(
            kind="player_field", label=f"出生点设为 {x} {y} {z}",
            source="editor", target="level",
            payload={"spawn": f"{x} {y} {z}"},
            runner=lambda s: _apply_spawn(s, x, y, z))
        self._run_bridge(operation)

    def _on_set_border(self) -> None:
        size = float(self.border_size.value())
        damage = float(self.border_damage.value())
        operation = bridge_mod.BridgeOperation(
            kind="worldborder", label=f"世界边界边长 {size:.0f}",
            source="editor", target="level",
            payload={"action": "set", "size": size, "damage": damage},
            runner=lambda s: _apply_border(s, size, damage))
        self._run_bridge(operation)

    def _on_set_commands(self) -> None:
        enabled = self.allow_commands.isChecked()
        operation = bridge_mod.BridgeOperation(
            kind="player_field",
            label=f"{'允许' if enabled else '禁止'}命令",
            source="editor", target="level", payload={"allow": enabled},
            runner=lambda s: _apply_allow_commands(s, enabled))
        self._run_bridge(operation)

    def _on_rule_edited(self, item) -> None:
        if item is None or item.column() != 1:
            return
        world = self.world()
        if world is None:
            return
        name_item = self.rules_table.item(item.row(), 0)
        if name_item is None:
            return
        name = name_item.text()
        value = item.text().strip()
        previous = world.overview.gamerules.get(name, "")
        if value == previous:
            return
        operation = bridge_mod.BridgeOperation(
            kind="gamerule", label=f"游戏规则 {name} = {value}",
            source="editor", target="level",
            payload={"rule": name, "value": value},
            runner=lambda s: _apply_gamerule(s, name, value))
        self._run_bridge(operation)

    def _on_apply_scene(self) -> None:
        from app.ui.world_dialogs import SceneDialog
        dialog = SceneDialog(self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        operation = bridge_mod.BridgeOperation(
            kind="scene", label="一键世界场景", source="editor",
            target="level", payload=payload,
            runner=bridge_mod.operation_runner(
                bridge_mod.BridgeOperation("scene", payload=payload)))
        self._run_bridge(operation)

    def _on_apply_preset(self) -> None:
        """套用工作区预设（内置 + 用户）。"""
        from app.core.region_io import preset_to_operation
        from app.ui.world_dialogs import PresetDialog
        dialog = PresetDialog(self, session=self.session)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        preset = dialog.preset()
        if preset is None:
            return
        self._run_bridge(preset_to_operation(preset))


class PlayerTabMixin:
    """「玩家」页签：玩家列表 + 字段编辑 + 背包卡片。"""

    def _build_player_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(*MARGINS["page"])
        outer.setSpacing(SPACING["sm"])

        split = QSplitter(Qt.Orientation.Horizontal)

        # ---- 左：玩家列表 ----
        left_box = SectionBox("玩家列表", "来自 playerdata/*.dat 与 level.dat")
        self.player_filter = FilterBar(placeholder="筛选玩家（Ctrl+F）")
        self.player_list = QListWidget()
        self.player_list.setMinimumWidth(180)
        self.player_list.setMaximumWidth(300)
        self.player_list.currentRowChanged.connect(self._on_player_selected)
        left_box.add_widget(self.player_filter)
        left_box.add_widget(self.player_list, 1)
        self.player_filter.add_list(self.player_list)
        self.player_summary = QLabel("")
        self.player_summary.setWordWrap(True)
        self.player_summary.setStyleSheet(muted_style(page))
        left_box.add_widget(self.player_summary)
        self.btn_players_cmd = QPushButton("全部玩家 → /execute 命令")
        self.btn_players_cmd.setToolTip(
            "为每个玩家生成 execute as/at 与 gamemode 命令，送到输出区")
        self.btn_players_cmd.clicked.connect(self._on_players_to_commands)
        left_box.add_widget(self.btn_players_cmd)
        split.addWidget(left_box)

        # ---- 右：字段 + 物品 ----
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(SPACING["sm"])

        fields_box = SectionBox(
            "玩家数据", "双击「取值」列可直接改；容器字段会用 SNBT 校验。")
        self.player_field_filter = FilterBar(
            placeholder="筛选字段（例如 health / 生命）")
        self.player_table = make_table(["字段", "路径", "取值"])
        self.player_field_filter.add_table(self.player_table)
        self.player_table.itemDoubleClicked.connect(
            self._on_player_cell_double_clicked)
        fields_box.add_widget(self.player_field_filter)
        fields_box.add_widget(self.player_table, 1)
        right_layout.addWidget(fields_box, 1)

        items_box = SectionBox(
            "背包 / 容器内容", "双击一行送进 /give 面板；也可以直接复制命令。")
        self.inventory_filter = FilterBar(placeholder="筛选物品（中文名或 ID）")
        self.player_inv_table = make_table(
            ["来源", "槽位", "物品", "数量", "摘要"])
        self.inventory_filter.add_table(self.player_inv_table)
        self.player_inv_table.itemDoubleClicked.connect(
            self._on_inventory_double_clicked)
        items_box.add_widget(self.inventory_filter)
        items_box.add_widget(self.player_inv_table, 1)
        self.items_box = items_box

        item_row = QHBoxLayout()
        item_row.setSpacing(SPACING["xs"])
        for label, slot, tip in (
                ("选中物品 → /give", self._on_inventory_to_give,
                 "把选中物品填进命令面板的 /give"),
                ("复制 /give 命令", self._on_inventory_copy, "复制到剪贴板"),
                ("复制 /data 命令", self._on_inventory_copy_data,
                 "复制成 /data modify 命令"),
                ("全部 → 输出", self._on_inventory_all_to_output,
                 "把当前玩家所有物品生成 /give 命令")):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            item_row.addWidget(button)
        item_row.addStretch(1)
        self.btn_inv_grow = QPushButton("塞满物品…")
        self.btn_inv_grow.setToolTip("往容器里批量写物品（事务化，带备份）")
        self.btn_inv_grow.clicked.connect(self._on_grow_items)
        item_row.addWidget(self.btn_inv_grow)
        items_box.add_layout(item_row)
        right_layout.addWidget(items_box, 1)
        split.addWidget(right)

        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        outer.addWidget(split, 1)
        return page

    # ---- 刷新 ----

    def refresh_players(self) -> None:
        world = self.world()
        self.player_list.clear()
        self.player_table.setRowCount(0)
        self.player_inv_table.setRowCount(0)
        self.player_summary.setText("")
        if world is None or not world.is_open():
            return
        for uuid in sorted(world.players):
            player = world.players[uuid]
            item = QListWidgetItem(f"{player.display()}  ·  {player.uuid[:8]}")
            item.setData(Qt.ItemDataRole.UserRole, uuid)
            item.setToolTip(
                f"{player.uuid}\n{player.path or '（level.dat 内嵌）'}")
            # UserRole+1 放「搜索串」：过滤条会连它一起匹配
            item.setData(Qt.ItemDataRole.UserRole + 1,
                         f"{player.uuid} {player.path or ''} {player.dimension}")
            self.player_list.addItem(item)
        if self.player_list.count():
            self.player_list.setCurrentRow(max(0, self._player_row()))

    def _player_row(self) -> int:
        if not self._current_player:
            return 0
        for index in range(self.player_list.count()):
            if self.player_list.item(index).data(
                    Qt.ItemDataRole.UserRole) == self._current_player:
                return index
        return 0

    def current_player(self):
        world = self.world()
        if world is None or not self._current_player:
            return None
        return world.players.get(self._current_player)

    def _on_player_selected(self, row: int) -> None:
        if row < 0:
            return
        item = self.player_list.item(row)
        if item is None:
            return
        self._current_player = str(item.data(Qt.ItemDataRole.UserRole) or "")
        self._fill_player_detail()
        self._refresh_status_context()

    def _fill_player_detail(self) -> None:
        world = self.world()
        player = self.current_player()
        self.player_table.setRowCount(0)
        self.player_inv_table.setRowCount(0)
        if world is None or player is None:
            return
        source = ("来自 level.dat，随存档一起写回"
                  if player.source == "level.dat" else str(player.path))
        self.player_summary.setText(
            f"{player.summary()}\n坐标 {player.pos_text()}"
            f" · 维度 {player.dimension or '—'}\n"
            f"物品 {player.item_count} 件 · 进度 {len(player.advancements)} 项"
            f" · 统计 {len(player.stats)} 条\n{source}")

        for path, label, kind in PLAYER_FIELDS:
            clean = path.strip()
            try:
                value = world.get_node_value(player.uuid, clean)
            except Exception:                           # pragma: no cover
                value = None
            self._append_player_row(clean, label, value, kind)
        for effect in player.effects:
            self._append_player_row(
                "active_effects",
                f"效果 {effect['id']}",
                f"等级 {effect['amplifier']} · {effect['duration']} 刻",
                "Compound")
        for attribute in player.attributes:
            self._append_player_row(
                "Attributes", attribute["name"],
                f"基础值 {attribute['base']:g}"
                f" · 修饰符 {attribute['modifiers']}", "Compound")
        for name in player.advancements[:40]:
            self._append_player_row("advancements", f"进度 {name}",
                                    "已完成", "String")
        for key in sorted(player.stats)[:40]:
            self._append_player_row("stats", f"统计 {key}", player.stats[key],
                                    "Int")
        self._fill_player_inventory(player)

    def _append_player_row(self, path: str, label: str, value, kind: str) -> None:
        text = "" if value is None else str(value)
        if len(text) > 240:
            text = text[:237] + "…"
        index = add_row(self.player_table, [label, path, text], payload=path)
        type_item = self.player_table.item(index, 2)
        if type_item is not None:
            type_item.setData(Qt.ItemDataRole.UserRole, kind)

    def _fill_player_inventory(self, player) -> None:
        root = player.root
        if root is None:
            return
        for container in find_item_containers(root):
            for stack in container.stacks():
                self._append_inventory_row(stack, container.label)

    def _append_inventory_row(self, stack, source: str) -> None:
        add_row(self.player_inv_table,
                [source, "" if stack.slot is None else stack.slot,
                 stack.display(), stack.count, stack.summary()],
                payload=stack)

    # ---- 交互 ----

    def _on_player_cell_double_clicked(self, item) -> None:
        if item is None or item.column() != 2:
            return
        if not self._guard_write():
            return
        path_item = self.player_table.item(item.row(), 1)
        if path_item is None:
            return
        path = path_item.text()
        player = self.current_player()
        if player is None:
            return
        value, ok = QInputDialog.getText(
            self, "修改玩家字段",
            f"{player.display()} → {path}\n"
            "新取值（SNBT；容器可以写复合标签或列表）：",
            text=item.text())
        if not ok or not value.strip():
            return
        self._write_player_field(player.uuid, path, value.strip())

    def _write_player_field(self, uuid: str, path: str, text: str) -> None:
        from app.core.snbt import SnbtParser
        try:
            node = SnbtParser(text).parse()
        except Exception as e:
            QMessageBox.warning(self, "取值无法解析", str(e))
            return

        def runner(session) -> str:
            world = session.world
            if node.is_container:
                from app.core.bridge import _write_path
                _write_path(world, uuid, path, node)
            else:
                world.set_node_value(uuid, path, node.value, node.kind)
            return f"已写入 {path} = {text}"

        operation = bridge_mod.BridgeOperation(
            kind="player_field", label=f"玩家字段 {path}",
            source="editor", target=uuid, payload={"path": path, "value": text},
            runner=runner)
        self._run_bridge(operation)

    def _on_inventory_double_clicked(self, item) -> None:
        if item is not None:
            self._on_inventory_to_give()

    def _selected_stack(self):
        table = self.player_inv_table
        rows = table.selectionModel().selectedRows() \
            if table.selectionModel() else []
        if not rows:
            return None
        stack = row_payload(table, rows[0].row())
        from app.core.nbt_bridge import ItemStack
        return stack if isinstance(stack, ItemStack) else None

    def _on_inventory_to_give(self) -> None:
        stack = self._selected_stack()
        if stack is None:
            self.statusMessage.emit("请先在背包表里选中一件物品。")
            return
        self.giveRequested.emit(stack)

    def _on_inventory_copy(self) -> None:
        stack = self._selected_stack()
        if stack is None:
            self.statusMessage.emit("请先选中一件物品。")
            return
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(stack_to_command(stack, "@p"))
        self.statusMessage.emit(f"已复制 /give 命令：{stack.display()}")

    def _on_inventory_copy_data(self) -> None:
        stack = self._selected_stack()
        if stack is None:
            self.statusMessage.emit("请先选中一件物品。")
            return
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(stack_to_data_command(stack))
        self.statusMessage.emit("已复制 /data 命令。")

    def _on_inventory_all_to_output(self) -> None:
        player = self.current_player()
        if player is None:
            return
        items = collect_items(player.root or SnbtNode.new_compound())
        if not items:
            self.statusMessage.emit("这个玩家没有可导出的物品。")
            return
        self.commandsReady.emit(
            "\n".join(stack_to_command(stack, "@p") for stack in items))
        self.statusMessage.emit(f"已把 {len(items)} 条 /give 命令送到输出区。")

    def _on_inventory_row_count(self) -> int:
        return self.player_inv_table.rowCount()

    def _on_players_to_commands(self) -> None:
        world = self.world()
        if world is None or not world.players:
            self.statusMessage.emit("没有可导出的玩家。")
            return
        lines: list[str] = []
        for uuid in sorted(world.players):
            player = world.players[uuid]
            selector = bridge_mod.player_to_selector(player)
            lines.append(f"# {player.display()} · {player.summary()}")
            lines.append(f"execute as {selector} at @s run say 你好")
            lines.append(f"gamemode {player.game_type or 'survival'} {selector}")
        self.commandsReady.emit("\n".join(lines))
        self.statusMessage.emit(
            f"已把 {len(world.players)} 名玩家的 /execute 与 /gamemode "
            "送到输出区。")

    def _on_grow_items(self) -> None:
        if not self._guard_write():
            return
        player = self.current_player()
        if player is None:
            QMessageBox.information(self, "提示", "请先选择玩家。")
            return
        from app.ui.world_dialogs import GrowItemsDialog
        container = "Inventory"
        if self._current_container and self._current_container.startswith(
                player.uuid):
            container = self._current_container.split(":", 1)[-1]
        dialog = GrowItemsDialog(self, default_item="minecraft:diamond",
                                 container=container)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        item_id, count, mode, slot, container, components = dialog.values()
        payload = {"item": item_id, "count": count, "mode": mode, "slot": slot,
                   "components": components, "path": container,
                   "player": player.uuid}
        operation = bridge_mod.BridgeOperation(
            kind="give", label=f"写入 {item_id} ×{count}",
            source="editor", target=player.uuid, payload=payload,
            runner=bridge_mod.operation_runner(
                bridge_mod.BridgeOperation("give", payload=payload)))
        self._run_bridge(operation)


# ---------------------------------------------------------------------------
# 内部执行器
# ---------------------------------------------------------------------------

def _apply_time(session, value: int) -> str:
    world = session.world
    world.set_time(day_time=value, time=max(world.overview.time, value))
    return f"世界时间已设为 {value}（{game_time_label(value)}）"


def _apply_weather(session, weather: str, duration: int) -> str:
    session.world.set_weather(weather, duration)
    return f"天气已设为 {weather}，持续 {duration} 刻"


def _apply_difficulty(session, value: int, locked: bool) -> str:
    session.world.set_difficulty(value, locked=locked)
    return (f"难度已设为 {DIFFICULTIES.get(value, value)}"
            + ("（已锁定）" if locked else ""))


def _apply_spawn(session, x: int, y: int, z: int) -> str:
    session.world.set_spawn(x, y, z)
    return f"出生点已设为 {x} {y} {z}"


def _apply_border(session, size: float, damage: float) -> str:
    session.world.set_world_border(size=size, damage=damage)
    return f"世界边界边长 {size:.0f}，伤害 {damage:g}"


def _apply_allow_commands(session, enabled: bool) -> str:
    session.world.set_allow_commands(enabled)
    return f"已{'允许' if enabled else '禁止'}命令"


def _apply_gamerule(session, name: str, value: str) -> str:
    session.world.set_gamerule(name, value)
    return f"游戏规则 {name} = {value}"


def _apply_map_field(session, index: int, path: str, value: int) -> str:
    session.world.set_node_value(f"map:{index}", path, value, SnbtKind.BYTE)
    return f"地图 #{index} 的 {path} 已设为 {value}"


__all__ = ["OverviewTabMixin", "PlayerTabMixin", "_apply_map_field"]
