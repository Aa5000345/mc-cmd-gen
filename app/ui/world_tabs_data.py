"""世界编辑器页签（下半组）：实体 / 容器 / NBT 树 / 地图 / 区域 /
数据包 / 蓝图·配方 / 差异 / 健康检查 / 中文世界编辑。

全部是依赖 `WorldPanelBase` 的 mixin，排版统一走 `world_common` 的
`SectionBox + FilterBar + make_table`。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressDialog,
    QPushButton, QSpinBox,
    QSplitter, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from app.core import bridge as bridge_mod
from app.core import world as world_mod
from app.ui.world_loader import WorldLoader
from app.core.nbt_bridge import (
    collect_items, find_entities, parse_item, stack_to_command,
)
from app.core.snbt import SnbtKind, SnbtNode, serialize
from app.core.world import count_bytes_human, health_check
from app.ui.theme import (
    FONTS, MARGINS, SIZES, SPACING, muted_style,
)
from app.ui.world_common import (
    FilterBar, SectionBox, add_row, make_table, row_payload,
)


class EntityTabMixin:
    """「实体」页签。"""

    def _build_entity_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(*MARGINS["page"])
        outer.setSpacing(SPACING["sm"])

        box = SectionBox(
            "实体列表",
            "默认只列 level.dat / playerdata 里的实体；"
            "区域文件里的实体需要点「扫描区域文件」按需读取（懒加载）。")
        self.entity_filter = FilterBar(
            placeholder="筛选实体（ID / 中文名 / 标签）")
        self.entity_table = make_table(
            ["来源", "实体 ID", "中文名", "生命", "标签", "位置", "NBT"])
        self.entity_filter.add_table(self.entity_table)
        self.entity_table.itemDoubleClicked.connect(
            self._on_entity_double_clicked)
        box.add_widget(self.entity_filter)
        box.add_widget(self.entity_table, 1)

        row = QHBoxLayout()
        row.setSpacing(SPACING["xs"])
        for label, slot, tip in (
                ("→ /summon 面板", self._on_entity_to_summon,
                 "把选中实体填进命令面板的 /summon"),
                ("复制 /summon 命令", self._on_entity_copy, "复制到剪贴板"),
                ("复制实体 NBT", self._on_entity_copy_nbt, "只复制 NBT"),
                ("→ 联动总线", self._on_entity_to_function,
                 "送到联动总线，可预览差异后写入存档")):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        self.btn_scan_entities = QPushButton("扫描区域文件")
        self.btn_scan_entities.setToolTip(
            "逐个区块读取 region/*.mca 里的 Entities（大存档会慢一些）")
        self.btn_scan_entities.clicked.connect(
            lambda: self.refresh_entities(include_regions=True))
        row.addWidget(self.btn_scan_entities)
        self.btn_reload_entities = QPushButton("刷新")
        self.btn_reload_entities.clicked.connect(self.refresh_entities)
        row.addWidget(self.btn_reload_entities)
        box.add_layout(row)
        outer.addWidget(box, 1)
        return page

    def refresh_entities(self, *, include_regions: bool = False) -> None:
        world = self.world()
        self.entity_table.setRowCount(0)
        if world is None or not world.is_open():
            return
        count = 0
        for target, root in self._document_roots():
            for entity in find_entities(root):
                self._append_entity_row(target, entity, position="")
                count += 1
        if include_regions and world.regions:
            from PySide6.QtWidgets import QApplication
            from app.core.world import scan_world_entities
            self.statusMessage.emit("正在扫描区域文件里的实体…")
            QApplication.processEvents()
            try:
                found = scan_world_entities(world, limit_chunks=2048,
                                            limit_entities=3000)
            except Exception as e:                      # pragma: no cover
                self.statusMessage.emit(f"扫描实体失败：{e}")
                found = []
            for entity in found:
                chunk = getattr(entity, "source_chunk", None)
                self._append_entity_row(
                    getattr(entity, "source_dimension", "") or "region",
                    entity,
                    position=f"区块 {chunk}" if chunk else "")
                count += 1
            self.statusMessage.emit(f"实体共 {count} 个（含区域文件扫描）。")
        else:
            self.statusMessage.emit(f"实体共 {count} 个。")
        self.entity_filter._refresh_count()

    def _append_entity_row(self, target: str, entity, *, position: str) -> None:
        add_row(self.entity_table, [
            target, entity.entity_id, entity.display(),
            "" if entity.health is None else f"{entity.health:g}",
            "/".join(entity.tags[:4]), position, len(entity.nbt or "")],
            payload=(target, entity))

    def _selected_entity(self):
        table = self.entity_table
        rows = table.selectionModel().selectedRows() \
            if table.selectionModel() else []
        if not rows:
            return None
        payload = row_payload(table, rows[0].row())
        return payload if isinstance(payload, tuple) else None

    def _on_entity_double_clicked(self, item) -> None:
        if item is not None:
            self._on_entity_to_summon()

    def _on_entity_to_summon(self) -> None:
        selected = self._selected_entity()
        if selected is None:
            self.statusMessage.emit("请先选中一个实体。")
            return
        self.summonRequested.emit(selected[1])

    def _on_entity_copy(self) -> None:
        selected = self._selected_entity()
        if selected is None:
            self.statusMessage.emit("请先选中一个实体。")
            return
        entity = selected[1]
        QGuiApplication.clipboard().setText(entity.summon_command("~ ~ ~"))
        self.statusMessage.emit(f"已复制 /summon 命令：{entity.display()}")

    def _on_entity_copy_nbt(self) -> None:
        selected = self._selected_entity()
        if selected is None:
            self.statusMessage.emit("请先选中一个实体。")
            return
        QGuiApplication.clipboard().setText(selected[1].nbt)
        self.statusMessage.emit("已复制实体 NBT。")

    def _on_entity_to_function(self) -> None:
        selected = self._selected_entity()
        if selected is None:
            self.statusMessage.emit("请先选中一个实体。")
            return
        self.bridgeRequested.emit(
            bridge_mod.entity_to_summon_operation(selected[1], "~ ~ ~"))


class ContainerTabMixin:
    """「容器」页签。"""

    def _build_container_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(*MARGINS["page"])
        outer.setSpacing(SPACING["sm"])

        box = SectionBox(
            "容器与方块实体",
            "包含背包 / 末影箱 / 箱子等；双击一行可查看内容。")
        self.container_filter = FilterBar(
            placeholder="筛选容器（方块 ID / 路径）")
        self.container_table = make_table(
            ["来源", "位置", "方块 / 标签", "格数", "物品数", "路径"])
        self.container_filter.add_table(self.container_table)
        self.container_table.itemDoubleClicked.connect(
            self._on_container_double_clicked)
        box.add_widget(self.container_filter)
        box.add_widget(self.container_table, 1)

        row = QHBoxLayout()
        row.setSpacing(SPACING["xs"])
        for label, slot, tip in (
                ("查看内容", self._on_container_view,
                 "把容器内容载入「玩家」页签的物品表"),
                ("内容 → /give", self._on_container_to_commands,
                 "把容器里所有物品生成 /give 命令"),
                ("复制 /setblock", self._on_container_to_setblock,
                 "复制带容器 NBT 的 /setblock 命令"),
                ("导出 CSV", self._on_container_export, "导出当前可见行")):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        self.btn_reload_containers = QPushButton("刷新")
        self.btn_reload_containers.clicked.connect(self.refresh_containers)
        row.addWidget(self.btn_reload_containers)
        box.add_layout(row)
        outer.addWidget(box, 1)
        return page

    def refresh_containers(self) -> None:
        world = self.world()
        self.container_table.setRowCount(0)
        if world is None or not world.is_open():
            return
        for target in self._targets():
            try:
                containers = world.containers(target)
            except Exception:                           # pragma: no cover
                continue
            for info in containers:
                add_row(self.container_table, [
                    target,
                    "" if info.position is None
                    else " ".join(str(v) for v in info.position),
                    info.block_id or info.label, info.size, info.total_count,
                    info.path], payload=(target, info))
        self.container_filter._refresh_count()

    def _selected_container(self):
        table = self.container_table
        rows = table.selectionModel().selectedRows() \
            if table.selectionModel() else []
        if not rows:
            return None
        payload = row_payload(table, rows[0].row())
        return payload if isinstance(payload, tuple) else None

    def _on_container_double_clicked(self, item) -> None:
        if item is not None:
            self._on_container_view()

    def _on_container_view(self) -> None:
        selected = self._selected_container()
        if selected is None:
            self.statusMessage.emit("请先选中一个容器。")
            return
        target, info = selected
        world = self.world()
        if world is None:                               # pragma: no cover
            return
        self._current_container = f"{target}:{info.path}"
        root, _mark = world._document_for(target)
        node = world_mod.resolve_path(root, info.path)
        if node is None:
            self.statusMessage.emit(f"读不到容器：{info.path}")
            return
        self.player_inv_table.setRowCount(0)
        for position, child in enumerate(node.children):
            try:
                stack = parse_item(child, f"{info.path}[{position}]",
                                   info.label or info.path)
            except Exception:                           # pragma: no cover
                continue
            self._append_inventory_row(stack, info.label or info.path)
        self.tabs.setCurrentIndex(self._tab_index("玩家"))
        self.statusMessage.emit(
            f"已载入容器 {info.path}（{len(node.children)} 格）。")

    def _on_container_to_commands(self) -> None:
        selected = self._selected_container()
        if selected is None:
            self.statusMessage.emit("请先选中一个容器。")
            return
        target, info = selected
        world = self.world()
        if world is None:                               # pragma: no cover
            return
        root, _mark = world._document_for(target)
        node = world_mod.resolve_path(root, info.path)
        if node is None:
            return
        stacks = _stacks_of(node)
        if not stacks:
            self.statusMessage.emit("这个容器是空的。")
            return
        self.commandsReady.emit(
            "\n".join(stack_to_command(stack, "@p") for stack in stacks))
        self.statusMessage.emit(f"已把 {len(stacks)} 条 /give 命令送到输出区。")

    def _on_container_to_setblock(self) -> None:
        selected = self._selected_container()
        if selected is None:
            self.statusMessage.emit("请先选中一个容器。")
            return
        target, info = selected
        world = self.world()
        if world is None:                               # pragma: no cover
            return
        root, _mark = world._document_for(target)
        node = world_mod.resolve_path(root, info.path)
        if node is None:
            return
        position = info.position or (0, 0, 0)
        block = info.block_id or "minecraft:chest"
        command = (f"setblock {position[0]} {position[1]} {position[2]} "
                   f"{block} {_container_payload(node)}")
        QGuiApplication.clipboard().setText(command)
        self.statusMessage.emit("已复制 /setblock 命令（含容器 NBT）。")

    def _on_container_export(self) -> None:
        self._export_table(self.container_table, "容器列表")


class NbtTabMixin:
    """「NBT 树」页签（嵌入式只读视图 + 路径联动）。"""

    def _build_nbt_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(*MARGINS["page"])
        outer.setSpacing(SPACING["sm"])

        box = SectionBox(
            "NBT 树", "这里只是只读视图：写盘请用「世界设置」或联动总线，"
                      "都会走事务 + 备份；要直接改文件请用「NBT 编辑器」页签。")
        row = QHBoxLayout()
        row.setSpacing(SPACING["xs"])
        row.addWidget(QLabel("查看"))
        self.nbt_source = QComboBox()
        self.nbt_source.setMinimumWidth(SIZES["combo_min"])
        row.addWidget(self.nbt_source, 1)
        self.btn_nbt_load = QPushButton("载入到树")
        self.btn_nbt_load.clicked.connect(self._on_load_nbt_source)
        row.addWidget(self.btn_nbt_load)
        for label, slot, tip in (
                ("定位到路径…", self._on_goto_path, "支持 Data.Player.Inventory[0].id"),
                ("复制为 SNBT", self._on_copy_snbt, "复制选中子树"),
                ("复制为 /data 命令", self._on_copy_data_command, ""),
                ("选中路径 → 总线", self._on_nbt_path_to_bridge,
                 "把路径与取值送到联动总线，生成 /data 命令")):
            button = QPushButton(label)
            button.setToolTip(tip or label)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        box.add_layout(row)

        from app.ui.widgets import NbtFileEditor
        self.nbt_editor = NbtFileEditor(embedded=True)
        self.nbt_editor.statusMessage.connect(self.statusMessage.emit)
        self.nbt_editor.giveRequested.connect(self.giveRequested.emit)
        self.nbt_editor.summonRequested.connect(self.summonRequested.emit)
        self.nbt_editor.commandsReady.connect(self.commandsReady.emit)
        box.add_widget(self.nbt_editor, 1)
        outer.addWidget(box, 1)
        return page

    def refresh_nbt_sources(self) -> None:
        current = self.nbt_source.currentData()
        self.nbt_source.clear()
        self.nbt_source.addItem("level.dat", "level")
        world = self.world()
        if world is not None and world.is_open():
            for uuid in sorted(world.players):
                player = world.players[uuid]
                self.nbt_source.addItem(
                    f"playerdata · {player.display()}", f"player:{uuid}")
            for index in sorted(world.maps):
                self.nbt_source.addItem(f"data/map_{index}.dat",
                                        f"map:{index}")
        if current:
            index = self.nbt_source.findData(current)
            if index >= 0:
                self.nbt_source.setCurrentIndex(index)

    def _on_load_nbt_source(self) -> None:
        world = self.world()
        if world is None or not world.is_open():
            return
        target = str(self.nbt_source.currentData() or "level")
        if target == "level":
            root = world.level_root
            path = world.level_file()
            compression = world.level.compression if world.level else "gzip"
        elif target.startswith("map:"):
            index = int(target.split(":", 1)[1])
            info = world.maps.get(index)
            if info is None or info.root is None:
                return
            root, path = info.root, info.path
            compression = world_mod.probe_compression(path)
        else:
            uuid = target.split(":", 1)[1]
            player = world.players.get(uuid)
            root = world.player_root(uuid)
            if player is None or root is None:
                return
            path = player.path or world.level_file()
            compression = (world_mod.probe_compression(path) if player.path
                           else (world.level.compression if world.level
                                 else "gzip"))
        if root is None:
            return
        self.nbt_editor.bind_document(
            root, source=Path(path).name, compression=compression,
            root_name=world.level.root_name if world.level else "",
            source_size=(world.level.source_size if world.level else 0))
        self.statusMessage.emit(f"已把 {Path(path).name} 载入 NBT 树。")

    def _on_goto_path(self) -> None:
        path, ok = QInputDialog.getText(
            self, "定位到路径", "NBT 路径（列表元素用 [0]）：",
            text=self.nbt_editor.editor.current_path())
        if not ok or not path.strip():
            return
        self.tabs.setCurrentIndex(self._tab_index("NBT 树"))
        if self.nbt_editor.editor.select_by_path(path.strip()):
            self.statusMessage.emit(f"已定位到 {path.strip()}")
        else:
            self.statusMessage.emit(f"找不到路径：{path.strip()}")

    def _on_copy_snbt(self) -> None:
        if self.nbt_editor.editor.copy_selected_snbt():
            self.statusMessage.emit("已复制 SNBT。")
        else:
            self.statusMessage.emit("请先在树里选中一个标签。")

    def _on_copy_data_command(self) -> None:
        if self.nbt_editor.editor.selected_node() is None:
            self.statusMessage.emit("请先在树里选中一个标签。")
            return
        self.nbt_editor.editor.copy_as_command()
        self.statusMessage.emit("已复制 /data 命令。")

    def _on_nbt_path_to_bridge(self) -> None:
        editor = self.nbt_editor.editor
        node = editor.selected_node()
        if node is None:
            self.statusMessage.emit("请先在树里选中一个标签。")
            return
        path = editor.current_path()
        value = serialize(node, pretty=False)
        self.bridgeRequested.emit(bridge_mod.BridgeOperation(
            kind="data", source="editor", target="level",
            label=f"NBT 路径 → /data（{path}）",
            payload={"path": path, "value": value},
            commands=[bridge_mod.nbt_path_to_command(path, value)]))


class MapTabMixin:
    """「地图」页签。"""

    def _build_map_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(*MARGINS["page"])
        outer.setSpacing(SPACING["sm"])

        box = SectionBox(
            "地图物品", "读取 data/map_*.dat 的比例 / 锁定 / 追踪 / 中心 / 颜色。")
        self.map_filter = FilterBar(placeholder="筛选地图（维度 / 编号）")
        self.map_table = make_table(
            ["编号", "维度", "比例", "锁定", "追踪", "中心", "旗帜", "画框",
             "颜色字节", "文件"])
        self.map_filter.add_table(self.map_table)
        self.map_table.itemDoubleClicked.connect(self._on_map_double_clicked)
        box.add_widget(self.map_filter)
        box.add_widget(self.map_table, 1)

        row = QHBoxLayout()
        row.setSpacing(SPACING["xs"])
        for label, slot, tip in (
                ("切换锁定", self._on_map_toggle_lock, "locked 0/1"),
                ("切换追踪", self._on_map_toggle_tracking, "tracking 0/1"),
                ("设为中心…", self._on_map_set_center, "x,z"),
                ("导出颜色索引", self._on_map_export_preview,
                 "把 colors 字节导出成文本（可在游戏里还原）"),
                ("导出 CSV", lambda: self._export_table(self.map_table, "地图列表"),
                 "")):
            button = QPushButton(label)
            button.setToolTip(tip or label)
            button.clicked.connect(slot)
            row.addWidget(button)
            # 前三项会改存档：登记进只读门禁（旧版漏登记，只读时照样可点）
            if label in ("切换锁定", "切换追踪", "设为中心…"):
                self._extra_write_widgets.append(button)
        row.addStretch(1)
        self.btn_reload_maps = QPushButton("刷新")
        self.btn_reload_maps.clicked.connect(self.refresh_maps)
        row.addWidget(self.btn_reload_maps)
        box.add_layout(row)
        outer.addWidget(box, 1)
        return page

    def refresh_maps(self) -> None:
        world = self.world()
        self.map_table.setRowCount(0)
        if world is None or not world.is_open():
            return
        for index in sorted(world.maps):
            info = world.maps[index]
            add_row(self.map_table, [
                index, info.dimension or "—", f"1:{1 << max(0, info.scale)}",
                "是" if info.locked else "否", "是" if info.tracking else "否",
                f"{info.x_center},{info.z_center}", info.banners, info.frames,
                info.colors_bytes, info.path.name], payload=index)
        self.map_filter._refresh_count()

    def _selected_map(self):
        table = self.map_table
        rows = table.selectionModel().selectedRows() \
            if table.selectionModel() else []
        if not rows:
            return None
        index = row_payload(table, rows[0].row())
        world = self.world()
        if world is None or index is None:
            return None
        return world.maps.get(int(index))

    def _on_map_double_clicked(self, item) -> None:
        if item is not None:
            self._on_map_toggle_lock()

    def _on_map_toggle_lock(self) -> None:
        info = self._selected_map()
        if info is None or not self._guard_write():
            return
        target = not info.locked
        self._run_bridge(bridge_mod.BridgeOperation(
            kind="player_field",
            label=f"地图 #{info.index} {'锁定' if target else '解锁'}",
            source="editor", target=f"map:{info.index}",
            payload={"path": "locked", "value": 1 if target else 0},
            runner=lambda s: self._map_runner(info.index, "locked",
                                              1 if target else 0)))

    def _map_runner(self, index: int, path: str, value: int) -> str:
        return _apply_map_field(self.session, index, path, value)

    def _on_map_toggle_tracking(self) -> None:
        info = self._selected_map()
        if info is None or not self._guard_write():
            return
        target = not info.tracking
        self._run_bridge(bridge_mod.BridgeOperation(
            kind="player_field",
            label=f"地图 #{info.index} {'开启' if target else '关闭'}追踪",
            source="editor", target=f"map:{info.index}",
            payload={"path": "tracking", "value": 1 if target else 0},
            runner=lambda s: _apply_map_field(s, info.index, "tracking",
                                              1 if target else 0)))

    def _on_map_set_center(self) -> None:
        info = self._selected_map()
        if info is None or not self._guard_write():
            return
        text, ok = QInputDialog.getText(
            self, "地图中心", "x,z（例如 32,-16）：",
            text=f"{info.x_center},{info.z_center}")
        if not ok or "," not in text:
            return
        try:
            x_text, z_text = text.split(",", 1)
            x, z = int(float(x_text)), int(float(z_text))
        except ValueError:
            QMessageBox.warning(self, "格式错误", "请用 `x,z` 的形式。")
            return

        def runner(session) -> str:
            session.world.set_node_value(f"map:{info.index}", "xCenter", x,
                                         SnbtKind.INT)
            session.world.set_node_value(f"map:{info.index}", "zCenter", z,
                                         SnbtKind.INT)
            return f"地图 #{info.index} 中心设为 {x},{z}"

        self._run_bridge(bridge_mod.BridgeOperation(
            kind="player_field", label=f"地图 #{info.index} 中心 {x},{z}",
            source="editor", target=f"map:{info.index}",
            payload={"x": x, "z": z}, runner=runner))

    def _on_map_export_preview(self) -> None:
        info = self._selected_map()
        if info is None or info.root is None:
            return
        node = info.root.get_child("data") or info.root.get_child("Data") \
            or info.root
        colors = node.get_child("colors")
        if colors is None or colors.kind != SnbtKind.BYTE_ARRAY:
            QMessageBox.information(self, "没有颜色数据",
                                    "这张地图还没有上色。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出颜色索引", str(info.path.with_suffix(".colors.txt")),
            "文本文件 (*.txt);;所有文件 (*)")
        if not path:
            return
        values = [int(child.value) & 0xFF for child in colors.children]
        lines = ["# 地图颜色索引（0-255 对应地图调色板）",
                 f"# 地图 #{info.index} · {len(values)} 个像素"]
        for start in range(0, len(values), 128):
            lines.append(" ".join(str(v) for v in values[start:start + 128]))
        try:
            Path(path).write_text("\n".join(lines), encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        self.statusMessage.emit(f"已导出 {len(values)} 个颜色字节到 {path}。")


class RegionTabMixin:
    """「区域」页签（只读索引）。"""

    def _build_region_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(*MARGINS["page"])
        outer.setSpacing(SPACING["sm"])

        box = SectionBox(
            "区域文件",
            "只读 8KiB 位置表建索引，不整块载入。写入是实验功能："
            "只支持「等长或更短」的原地替换，会先 dry-run 再备份，"
            "能不动存档时建议导出 .mcfunction 让游戏执行。")
        self.region_filter = FilterBar(
            placeholder="筛选区域文件（维度 / 文件名）")
        self.region_table = make_table(
            ["文件", "维度", "区块数", "有效", "体积", "状态"])
        self.region_filter.add_table(self.region_table)
        self.region_table.itemSelectionChanged.connect(
            self._on_region_selected)
        box.add_widget(self.region_filter)
        box.add_widget(self.region_table, 1)

        split = QSplitter(Qt.Orientation.Vertical)
        chunks_box = SectionBox(
            "区块索引", "最多渲染前 400 个区块（懒加载，避免几万行拖慢界面）。")
        self.chunk_filter = FilterBar(placeholder="筛选区块（X / Z）")
        self.region_chunks = make_table(
            ["区块 X", "区块 Z", "偏移(扇区)", "扇区数", "更新时间"])
        self.chunk_filter.add_table(self.region_chunks)
        chunks_box.add_widget(self.chunk_filter)
        chunks_box.add_widget(self.region_chunks, 1)
        split.addWidget(chunks_box)

        row = QHBoxLayout()
        row.setSpacing(SPACING["xs"])
        for label, slot, tip in (
                ("导出区块坐标列表", self._on_region_export,
                 "送到输出区，可另存为 .mcfunction"),
                ("导出强制加载函数", self._on_region_forceload,
                 "为选中区域生成 forceload 命令（最多 1024 条）"),
                ("写入区块…（实验）", self._on_region_write,
                 "dry-run → 备份 → 原子替换 → 写后自检"),
                ("批量替换全部区块…", self._on_region_batch,
                 "对所有区域文件的区块应用同一条修改（两步确认）"),
                ("导出 CSV", lambda: self._export_table(self.region_table,
                                                        "区域文件"),
                 "")):
            button = QPushButton(label)
            button.setToolTip(tip or label)
            button.clicked.connect(slot)
            row.addWidget(button)
            # 区域写入是实验性的写盘入口，必须跟着只读门禁一起灰
            if "写入" in label or "批量替换" in label:
                self._extra_write_widgets.append(button)
        row.addStretch(1)
        self.region_count = QLabel("")
        self.region_count.setStyleSheet(muted_style(page))
        row.addWidget(self.region_count)
        chunks_box.add_layout(row)
        outer.addWidget(box, 2)
        outer.addWidget(split, 3)
        return page

    def _on_region_batch(self) -> None:
        """区域多区块批量替换：全量 dry-run 后再执行。"""
        from app.ui.world_region_batch import RegionBatchDialog
        if not self._guard_write():
            return
        world = self.world()
        if world is None or not world.regions:
            self.statusMessage.emit("这个存档没有可用的区域文件。")
            return
        dialog = RegionBatchDialog(self, world=world)
        dialog.exec()
        done = dialog.executed()
        if done:
            self.refresh_regions()
            self.statusMessage.emit(
                f"批量替换完成：写入 {done} 个区块"
                + (f"，{len(dialog.failures())} 个失败"
                   if dialog.failures() else ""))
        dialog.deleteLater()

    def _on_region_write(self) -> None:
        """区域区块写入：dry-run → 备份 → 原子替换 → 自检。"""
        from app.core import region_io
        from app.ui.world_dialogs import RegionChunkWriteDialog

        paths = self._selected_regions()
        if not paths:
            self.statusMessage.emit("请先在区域文件表里选中一个 .mca 文件。")
            return
        if not self._guard_write():
            return
        world = self.world()
        path = Path(paths[0])
        # 默认坐标取「区块索引表里选中的那一行」；没有选中才退回第一行。
        # 旧实现永远读第 0 行：用户选了 A 区块，对话框里却填着 B 的坐标。
        prefill = (0, 0)
        table = self.region_chunks
        selected = table.selectionModel().selectedRows() \
            if table.selectionModel() else []
        row = selected[0].row() if selected else (
            0 if table.rowCount() else -1)
        if row >= 0:
            def _cell(column: int) -> int:
                item = table.item(row, column)
                try:
                    return int(item.text() or 0) if item is not None else 0
                except ValueError:
                    return 0
            prefill = (_cell(0), _cell(1))
        dialog = RegionChunkWriteDialog(
            self, region_name=path.name, chunk=prefill)
        dialog.set_target(path, path.name)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        values = dialog.values()
        plan = dialog.plan()
        if plan is None or not plan.ok:
            QMessageBox.information(self, "不可写",
                                    plan.summary() if plan else "没有可行性结果。")
            return
        if not plan.diff:
            QMessageBox.information(self, "没有变化", "区块内容没有变化。")
            return

        answer = QMessageBox.warning(
            self, "确认写入区域区块",
            f"{plan.summary()}\n\n差异（{len(plan.diff)} 处）：\n"
            + "\n".join(plan.diff[:20])
            + "\n\n将先备份该区域文件，再原子替换并自检。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Ok:
            return

        from app.core.snbt import SnbtParser
        try:
            node = SnbtParser(values["snbt"]).parse()
        except Exception as e:
            QMessageBox.warning(self, "NBT 解析失败", str(e))
            return

        folder = world.backup_dir()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            backup = region_io.backup_region(path, folder)
        except OSError as e:
            QMessageBox.warning(self, "备份失败，已取消", str(e))
            return
        try:
            region_io.write_chunk(path, values["x"], values["z"], node,
                                  compression=values["compression"])
        except Exception as e:
            QMessageBox.warning(
                self, "写入失败",
                f"{e}\n\n原文件已备份到 {backup}")
            return
        problems = region_io.verify_region(path)
        self.refresh_regions()
        message = (f"已写入区块 ({values['x']},{values['z']})，"
                   f"备份 {backup.parent.name}")
        if problems:
            message += "；自检发现问题：" + "；".join(problems[:3])
        self.statusMessage.emit(message)
        QMessageBox.information(self, "写入完成", message)

    def refresh_regions(self) -> None:
        world = self.world()
        self.region_table.setRowCount(0)
        self.region_chunks.setRowCount(0)
        if world is None or not world.is_open():
            self.region_count.setText("")
            return
        for region in world.regions.values():
            add_row(self.region_table, [
                region.name, region.dimension or "—", len(region.chunks),
                region.present, count_bytes_human(region.size_bytes),
                region.error or "正常"], payload=str(region.path))
        total = sum(len(r.chunks) for r in world.regions.values())
        present = sum(r.present for r in world.regions.values())
        self.region_count.setText(
            f"{len(world.regions)} 个区域文件 · {present}/{total} 个区块有数据")
        self.region_filter._refresh_count()

    def _on_region_selected(self) -> None:
        table = self.region_table
        rows = table.selectionModel().selectedRows() \
            if table.selectionModel() else []
        if not rows:
            return
        path = str(row_payload(table, rows[0].row()) or "")
        world = self.world()
        if world is None:
            return
        region = world.regions.get(path)
        self.region_chunks.setRowCount(0)
        if region is None:
            return
        for chunk in region.chunks[:400]:
            add_row(self.region_chunks, [
                chunk.x, chunk.z, chunk.offset, chunk.sectors,
                world_mod.human_time(chunk.timestamp * 1000)])
        if len(region.chunks) > 400:
            add_row(self.region_chunks,
                    ["…", f"另有 {len(region.chunks) - 400} 个区块", "", "",
                     ""])
        self.chunk_filter._refresh_count()

    def _selected_regions(self) -> list[str]:
        table = self.region_table
        rows = table.selectionModel().selectedRows() \
            if table.selectionModel() else []
        return [str(row_payload(table, index.row()) or "") for index in rows]

    def _on_region_export(self) -> None:
        world = self.world()
        paths = self._selected_regions()
        if world is None or not paths:
            self.statusMessage.emit("请先选中至少一个区域文件。")
            return
        lines = [f"# 区块索引导出（{world.name}）"]
        for path in paths:
            region = world.regions.get(path)
            if region is None:
                continue
            lines.append(f"# {region.name} · {region.dimension} · "
                         f"{region.present}/{len(region.chunks)} 区块有数据")
            for chunk in region.chunks:
                lines.append(f"{chunk.x} {chunk.z} 偏移 {chunk.offset} "
                             f"扇区 {chunk.sectors}")
        self.commandsReady.emit("\n".join(lines))
        self.statusMessage.emit(f"已导出 {len(paths)} 个区域文件的区块列表。")

    def _on_region_forceload(self) -> None:
        world = self.world()
        paths = self._selected_regions()
        if world is None or not paths:
            self.statusMessage.emit("请先选中一个区域文件。")
            return
        region = world.regions.get(paths[0])
        if region is None:
            return
        lines = ["# 由 MCStudio 生成：强制加载该区域文件里的所有区块"]
        for chunk in region.chunks[:1024]:
            lines.append(f"forceload add {chunk.x * 16} {chunk.z * 16}")
        self.commandsReady.emit("\n".join(lines))
        self.statusMessage.emit(
            f"已生成 {len(lines) - 1} 条 forceload 命令（最多 1024 条）。")


class DatapackTabMixin:
    """「数据包」页签。"""

    def _build_datapack_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(*MARGINS["page"])
        outer.setSpacing(SPACING["sm"])

        box = SectionBox(
            "数据包",
            "列出 level.dat 里启用 / 缺失的数据包；"
            "「导出世界规则」会把时间 / 天气 / 规则生成一组函数命令。")
        self.datapack_filter = FilterBar(placeholder="筛选数据包")
        self.datapack_table = make_table(["数据包", "类型", "状态", "路径"])
        self.datapack_filter.add_table(self.datapack_table)
        box.add_widget(self.datapack_filter)
        box.add_widget(self.datapack_table, 1)

        row = QHBoxLayout()
        row.setSpacing(SPACING["xs"])
        self.btn_pack_rules = QPushButton("导出世界规则 → 输出区")
        self.btn_pack_rules.setToolTip(
            "把时间 / 天气 / 难度 / 游戏规则 / 边界生成命令，可另存为数据包函数")
        self.btn_pack_rules.clicked.connect(self._on_export_world_function)
        row.addWidget(self.btn_pack_rules)
        self.btn_pack_scene = QPushButton("导出场景函数 → 输出区")
        self.btn_pack_scene.setToolTip("永久白天 + 禁止天气 + 和平 的完整函数")
        self.btn_pack_scene.clicked.connect(self._on_export_scene_function)
        row.addWidget(self.btn_pack_scene)
        self.btn_pack_export = QPushButton("把输出区导出为数据包…")
        self.btn_pack_export.clicked.connect(self._on_export_datapack)
        row.addWidget(self.btn_pack_export)
        row.addStretch(1)
        self.btn_reload_packs = QPushButton("刷新")
        self.btn_reload_packs.clicked.connect(self.refresh_datapacks)
        row.addWidget(self.btn_reload_packs)
        box.add_layout(row)
        outer.addWidget(box, 1)
        return page

    def refresh_datapacks(self) -> None:
        world = self.world()
        self.datapack_table.setRowCount(0)
        if world is None or not world.is_open():
            return
        folder = world.path / "datapacks"
        present: dict[str, bool] = {}
        if folder.is_dir():
            for item in sorted(folder.iterdir()):
                present[item.name] = item.is_dir()
        for pack in world.overview.datapacks:
            name = pack[5:] if pack.startswith("file/") else pack
            if name in present:
                state = "存在"
                path = str(folder / name)
                kind = "目录" if present[name] else "压缩包"
            elif pack.startswith(("vanilla", "minecraft")):
                state, path, kind = "官方内置", "", "内置"
            else:
                state, path, kind = "缺失", str(folder / name), "目录"
            add_row(self.datapack_table, [name, kind, state, path])
        self.datapack_filter._refresh_count()

    def _on_export_world_function(self) -> None:
        world = self.world()
        if world is None:
            return
        operation = bridge_mod.world_rules_to_commands(world)
        self.commandsReady.emit(operation.command_text())
        self.statusMessage.emit(
            f"已把世界规则导出为 {len(operation.commands)} 条命令，"
            "可在输出区导出成数据包函数。")

    def _on_export_scene_function(self) -> None:
        lines = [
            "# 世界场景函数（由 MCStudio 生成）",
            "# 用法：放进数据包 data/<命名空间>/function/ 下，用 /function 执行",
            "time set 6000",
            "gamerule doDaylightCycle false",
            "weather clear 1000000",
            "gamerule doWeatherCycle false",
            "difficulty peaceful",
            "gamerule doMobSpawning true",
            "gamerule keepInventory true",
        ]
        self.commandsReady.emit("\n".join(lines))
        self.statusMessage.emit("已生成场景函数（9 行），可在输出区导出数据包。")

    def _on_export_datapack(self) -> None:
        self.statusMessage.emit(
            "请在命令面板的输出区使用「导出数据包」；"
            "这里生成的内容会直接进输出区。")


class BlueprintTabMixin:
    """「蓝图 / 配方」页签：结构模板导出 + 物品组件配方工坊。"""

    def _build_blueprint_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(*MARGINS["page"])
        outer.setSpacing(SPACING["sm"])

        self.blueprint_tabs = QTabWidget()
        self.blueprint_tabs.setDocumentMode(True)
        self.blueprint_tabs.addTab(self._build_blueprint_section(), "结构蓝图")
        self.blueprint_tabs.addTab(self._build_recipe_section(), "物品配方")
        outer.addWidget(self.blueprint_tabs, 1)
        return page

    # ---- 结构蓝图 ----

    def _build_blueprint_section(self) -> QWidget:
        page = QWidget()
        inner = QVBoxLayout(page)
        inner.setContentsMargins(*MARGINS["page"])
        inner.setSpacing(SPACING["sm"])

        box = SectionBox(
            "结构蓝图",
            "从存档里的方块实体（block_entities）导出选区，写成 .nbt"
            "（原版结构）与 .schem（Sponge v2），也可以直接生成 "
            "/setblock、/fill、/place 或数据包函数。")
        row = QHBoxLayout()
        row.setSpacing(SPACING["xs"])
        row.addWidget(QLabel("选区"))
        self.blueprint_source = QComboBox()
        self.blueprint_source.setMinimumWidth(SIZES["combo_min"])
        row.addWidget(self.blueprint_source, 1)
        self.btn_blueprint_export = QPushButton("导出蓝图…")
        self.btn_blueprint_export.setToolTip("选择格式与命令模式后导出")
        self.btn_blueprint_export.clicked.connect(self._on_export_blueprint)
        row.addWidget(self.btn_blueprint_export)
        self.btn_blueprint_load = QPushButton("载入蓝图…")
        self.btn_blueprint_load.clicked.connect(self._on_load_blueprint)
        row.addWidget(self.btn_blueprint_load)
        row.addStretch(1)
        self.btn_blueprint_refresh = QPushButton("刷新选区")
        self.btn_blueprint_refresh.clicked.connect(self.refresh_blueprints)
        row.addWidget(self.btn_blueprint_refresh)
        box.add_layout(row)

        self.blueprint_info = QLabel("还没有导出蓝图。")
        self.blueprint_info.setStyleSheet(muted_style(page))
        self.blueprint_info.setWordWrap(True)
        box.add_widget(self.blueprint_info)

        self.blueprint_preview = QPlainTextEdit()
        self.blueprint_preview.setReadOnly(True)
        self.blueprint_preview.setFont(QFont("Consolas", FONTS["mono"]))
        self.blueprint_preview.setPlaceholderText(
            "导出后会在这里显示生成的内容（含注释头）")
        box.add_widget(self.blueprint_preview, 1)

        cmd_row = QHBoxLayout()
        cmd_row.setSpacing(SPACING["xs"])
        for label, mode in (("生成 /setblock", "setblock"),
                            ("生成 /fill", "fill"),
                            ("生成 /place", "place"),
                            ("导出为函数内容", None)):
            button = QPushButton(label)
            if mode is None:
                button.clicked.connect(self._on_blueprint_function)
            else:
                button.clicked.connect(
                    lambda _=False, m=mode: self._blueprint_commands(m))
            cmd_row.addWidget(button)
        cmd_row.addStretch(1)
        box.add_layout(cmd_row)
        inner.addWidget(box, 1)
        return page

    # ---- 物品配方 ----

    def _build_recipe_section(self) -> QWidget:
        page = QWidget()
        inner = QVBoxLayout(page)
        inner.setContentsMargins(*MARGINS["page"])
        inner.setSpacing(SPACING["sm"])

        box = SectionBox(
            "物品组件配方",
            "内置处方 + 用户配方（user_data/recipes/）。双击一行可生成 /give，"
            "也可以把整套组件批量写进玩家容器。")
        self.recipe_filter = FilterBar(
            placeholder="筛选配方（名称 / 物品 ID）")
        self.recipe_table = make_table(
            ["配方", "物品", "数量", "组件数", "来源"])
        self.recipe_filter.add_table(self.recipe_table)
        self.recipe_table.itemDoubleClicked.connect(
            lambda _i: self._on_recipe_commands())
        box.add_widget(self.recipe_filter)
        box.add_widget(self.recipe_table, 1)

        form_row = QHBoxLayout()
        form_row.setSpacing(SPACING["xs"])
        form_row.addWidget(QLabel("目标容器"))
        self.recipe_container = QLineEdit("Inventory")
        self.recipe_container.setMaximumWidth(SIZES["field_width_wide"])
        form_row.addWidget(self.recipe_container)
        self.recipe_mode = QComboBox()
        for key, label in (("first_empty", "首个空槽"), ("merge", "合并同类"),
                           ("overwrite", "覆盖指定槽")):
            self.recipe_mode.addItem(label, key)
        form_row.addWidget(self.recipe_mode)
        self.recipe_slot = QSpinBox()
        self.recipe_slot.setRange(-1, 255)
        self.recipe_slot.setValue(-1)
        self.recipe_slot.setSpecialValueText("自动")
        form_row.addWidget(QLabel("槽位"))
        form_row.addWidget(self.recipe_slot)
        form_row.addStretch(1)
        box.add_layout(form_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(SPACING["xs"])
        self.btn_recipe_apply = QPushButton("批量应用到当前玩家")
        self.btn_recipe_apply.setToolTip("按上面的容器 / 方式 / 槽位写入（先备份）")
        self.btn_recipe_apply.clicked.connect(self._on_apply_recipe)
        action_row.addWidget(self.btn_recipe_apply)
        self.btn_recipe_commands = QPushButton("生成 /give")
        self.btn_recipe_commands.clicked.connect(self._on_recipe_commands)
        action_row.addWidget(self.btn_recipe_commands)
        self.btn_recipe_edit = QPushButton("保存为新配方…")
        self.btn_recipe_edit.setToolTip("把选中的配方改名后存进 user_data/recipes/")
        self.btn_recipe_edit.clicked.connect(self._on_save_recipe)
        action_row.addWidget(self.btn_recipe_edit)
        action_row.addStretch(1)
        self.btn_recipe_reload = QPushButton("刷新配方")
        self.btn_recipe_reload.clicked.connect(self.refresh_recipes)
        action_row.addWidget(self.btn_recipe_reload)
        box.add_layout(action_row)
        inner.addWidget(box, 1)
        return page

    # ---- 蓝图逻辑 ----

    def refresh_blueprints(self) -> None:
        current = self.blueprint_source.currentData()
        self.blueprint_source.clear()
        world = self.world()
        if world is None or not world.is_open():
            return
        entries = self._block_entity_lists()
        if not entries:
            self.blueprint_source.addItem(
                "（这个存档没有 block_entities：先在游戏里放个箱子并保存）", "")
        for label, node in entries:
            self.blueprint_source.addItem(
                f"{label}（{len(node.children)} 个方块实体）", label)
        if current:
            index = self.blueprint_source.findData(current)
            if index >= 0:
                self.blueprint_source.setCurrentIndex(index)

    def _block_entity_lists(self) -> list[tuple[str, SnbtNode]]:
        out: list[tuple[str, SnbtNode]] = []
        for target, root in self._document_roots():
            for key in ("block_entities", "BlockEntities"):
                node = root.get_child(key)
                if node is not None and node.kind == SnbtKind.LIST \
                        and node.children:
                    out.append((f"{target}.{key}", node))
        return out

    def _selected_blueprint_source(self) -> SnbtNode | None:
        label = self.blueprint_source.currentData()
        if not label:
            return None
        for name, node in self._block_entity_lists():
            if name == label:
                return node
        return None

    def _on_export_blueprint(self) -> None:
        from app.core.blueprint import blueprint_from_block_entities
        from app.ui.world_dialogs import BlueprintExportDialog
        node = self._selected_blueprint_source()
        if node is None:
            QMessageBox.information(
                self, "没有可导出的选区",
                "这个存档里没有 block_entities（方块实体数据）。\n\n"
                "可以先在游戏里放一个箱子 / 熔炉并保存，再回来导出。")
            return
        try:
            blueprint = blueprint_from_block_entities(node, name="我的建筑")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        dialog = BlueprintExportDialog(
            self, name=blueprint.name, block_count=blueprint.block_count(),
            size=blueprint.size,
            has_entities=bool(blueprint.block_entities()))
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        options = dialog.values()
        blueprint.name = options["name"]
        if not options["include_nbt"]:
            for block in blueprint.blocks:
                block.nbt = None

        self._blueprint = blueprint
        written: list[str] = []
        try:
            if options["format"] in ("both", "nbt"):
                written.append(blueprint.save_nbt().name)
            if options["format"] in ("both", "schem"):
                written.append(blueprint.save_schem().name)
        except Exception as e:
            QMessageBox.warning(self, "写入失败", str(e))
            return

        info = (f"{blueprint.summary()}\n已写出：{'、'.join(written)}"
                f"（应用数据目录 user_data/blueprints/）")
        self.blueprint_info.setText(info)
        if options["command_mode"] == "none":
            self.blueprint_preview.setPlainText(info)
        else:
            self.blueprint_preview.setPlainText(
                _limit_function(blueprint.to_mcfunction(), options["limit"]))
            self._blueprint_commands(options["command_mode"],
                                     limit=options["limit"])
        self.statusMessage.emit(
            f"已导出蓝图「{blueprint.name}」：{blueprint.block_count()} 个方块")

    def _on_load_blueprint(self) -> None:
        from app.core.blueprint import blueprints_dir, load_blueprint
        path, _ = QFileDialog.getOpenFileName(
            self, "载入蓝图", str(blueprints_dir()),
            "结构文件 (*.nbt *.schem *.snbt);;所有文件 (*)")
        if not path:
            return
        try:
            blueprint = load_blueprint(path)
        except Exception as e:
            QMessageBox.warning(self, "载入失败", str(e))
            return
        self._blueprint = blueprint
        self.blueprint_info.setText(f"{blueprint.summary()}\n来源：{path}")
        self.blueprint_preview.setPlainText(
            _limit_function(blueprint.to_mcfunction(), 4096))
        self.statusMessage.emit(f"已载入蓝图：{blueprint.summary()}")

    def _current_blueprint(self):
        blueprint = getattr(self, "_blueprint", None)
        if blueprint is None:
            self.statusMessage.emit("请先导出或载入一份蓝图。")
        return blueprint

    def _blueprint_commands(self, mode: str, *, limit: int = 4096) -> None:
        blueprint = self._current_blueprint()
        if blueprint is None:
            return
        commands = blueprint.to_commands(mode=mode, limit=limit)
        if not commands:
            self.statusMessage.emit("蓝图里没有方块。")
            return
        self.commandsReady.emit("\n".join(commands))
        label = {"fill": "/fill", "place": "/place"}.get(mode, "/setblock")
        self.statusMessage.emit(f"已生成 {len(commands)} 条 {label} 命令。")

    def _on_blueprint_function(self) -> None:
        blueprint = self._current_blueprint()
        if blueprint is None:
            return
        self.commandsReady.emit(blueprint.to_mcfunction())
        self.statusMessage.emit("已把蓝图导出为函数内容（含注释头）。")

    # ---- 配方逻辑 ----

    def refresh_recipes(self) -> None:
        from app.core.blueprint import list_recipes
        self.recipe_table.setRowCount(0)
        try:
            recipes = list_recipes()
        except Exception as e:                          # pragma: no cover
            self.statusMessage.emit(f"读取配方失败：{e}")
            return
        for recipe in recipes:
            add_row(self.recipe_table, [
                recipe.name, recipe.item, recipe.count,
                len(recipe.components),
                "内置" if recipe.builtin else "用户"], payload=recipe)
        self.recipe_filter._refresh_count()

    def _selected_recipe(self):
        from app.core.blueprint import ItemRecipe
        table = self.recipe_table
        rows = table.selectionModel().selectedRows() \
            if table.selectionModel() else []
        if not rows:
            return None
        recipe = row_payload(table, rows[0].row())
        return recipe if isinstance(recipe, ItemRecipe) else None

    def _on_apply_recipe(self) -> None:
        recipe = self._selected_recipe()
        if recipe is None:
            self.statusMessage.emit("请先选中一个配方。")
            return
        if not self._guard_write():
            return
        player = self.current_player()
        if player is None:
            QMessageBox.information(self, "提示", "请先在「玩家」页签选择玩家。")
            return
        container = self.recipe_container.text().strip() or "Inventory"
        mode = str(self.recipe_mode.currentData())
        slot = self.recipe_slot.value()
        payload = {"item": recipe.item, "count": recipe.count, "mode": mode,
                   "slot": None if slot < 0 else slot,
                   "player": player.uuid, "path": container,
                   "components": _recipe_components(recipe)}
        self._run_bridge(bridge_mod.BridgeOperation(
            kind="give", label=f"批量应用配方「{recipe.name}」",
            source="editor", target=player.uuid, payload=payload,
            runner=bridge_mod.operation_runner(
                bridge_mod.BridgeOperation("give", payload=payload))))

    def _on_recipe_commands(self) -> None:
        recipe = self._selected_recipe()
        if recipe is None:
            self.statusMessage.emit("请先选中一个配方。")
            return
        self.commandsReady.emit(recipe.to_give_command("@p"))
        self.statusMessage.emit(f"已生成 /give 命令：{recipe.name}")

    def _on_save_recipe(self) -> None:
        from app.core.blueprint import ItemRecipe
        recipe = self._selected_recipe()
        if recipe is None:
            self.statusMessage.emit("请先选中一个配方。")
            return
        name, ok = QInputDialog.getText(
            self, "另存为配方", "新的配方名：", text=f"{recipe.name}（副本）")
        if not ok or not name.strip():
            return
        clone = ItemRecipe(name=name.strip(), item=recipe.item,
                           count=recipe.count,
                           components=list(recipe.components),
                           note=recipe.note)
        clone.id = _safe_recipe_id(name)
        try:
            path = clone.save()
        except Exception as e:                          # pragma: no cover
            QMessageBox.warning(self, "保存失败", str(e))
            return
        self.refresh_recipes()
        self.statusMessage.emit(f"已保存配方到 {path.name}")


class DiffTabMixin:
    """「差异」页签：待应用差异 + 备份列表 + 回滚。"""

    def _build_diff_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(*MARGINS["page"])
        outer.setSpacing(SPACING["sm"])

        split = QSplitter(Qt.Orientation.Vertical)
        box = SectionBox(
            "待应用的差异", "点「预览差异」先看清楚再决定；空表示内存与磁盘一致。")
        self.diff_view = QPlainTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setFont(QFont("Consolas", FONTS["mono"]))
        self.diff_view.setPlaceholderText(
            "这里是「待应用的差异」。点「预览差异」查看。")
        box.add_widget(self.diff_view, 1)
        action = QHBoxLayout()
        action.setSpacing(SPACING["xs"])
        for label, slot, tip in (
                ("预览差异", self.preview_diff, "计算内存与磁盘的差异"),
                ("应用修改", self.apply_changes, "事务化写盘（备份 + 原子替换）"),
                ("撤销上一步", self.undo, "回滚最近一次写入"),
                ("重做", self.redo, "")):
            button = QPushButton(label)
            button.setToolTip(tip or label)
            button.clicked.connect(slot)
            action.addWidget(button)
        action.addStretch(1)
        self.diff_summary = QLabel("")
        self.diff_summary.setStyleSheet(muted_style(page))
        action.addWidget(self.diff_summary)
        box.add_layout(action)
        split.addWidget(box)

        backup_box = SectionBox(
            "备份 / 世界时间线", "打开世界会自动快照；选中后可以回滚。")
        self.backup_filter = FilterBar(placeholder="筛选备份（时间 / 说明）")
        self.backup_table = make_table(["备份", "时间", "说明", "文件数"])
        self.backup_filter.add_table(self.backup_table)
        self.backup_table.itemSelectionChanged.connect(
            self._on_backup_selected)
        backup_box.add_widget(self.backup_filter)
        backup_box.add_widget(self.backup_table, 1)
        backup_row = QHBoxLayout()
        backup_row.setSpacing(SPACING["xs"])
        self.btn_backup_now = QPushButton("立即创建快照")
        self.btn_backup_now.setToolTip("给当前 level.dat / 玩家文件拍一张快照")
        self.btn_backup_now.clicked.connect(self._on_backup_now)
        backup_row.addWidget(self.btn_backup_now)
        self.btn_snapshot = QPushButton("创建完整快照")
        self.btn_snapshot.setToolTip(
            "额外复制 playerdata / 地图文件，适合改动前的保险")
        self.btn_snapshot.clicked.connect(self._on_full_snapshot)
        backup_row.addWidget(self.btn_snapshot)
        self.btn_restore = QPushButton("回滚到选中备份…")
        self.btn_restore.clicked.connect(self._on_restore_backup)
        backup_row.addWidget(self.btn_restore)
        backup_row.addStretch(1)
        self.btn_backup_reload = QPushButton("刷新")
        self.btn_backup_reload.clicked.connect(self.refresh_backups)
        backup_row.addWidget(self.btn_backup_reload)
        backup_box.add_layout(backup_row)
        split.addWidget(backup_box)
        split.setSizes([320, 260])
        outer.addWidget(split, 1)
        return page

    def refresh_backups(self) -> None:
        self.backup_table.setRowCount(0)
        session = self.session
        if session is None or session.world is None:
            return
        for entry in session.list_backups():
            add_row(self.backup_table, [
                entry["name"], entry.get("created_at", ""),
                entry.get("label", ""), len(entry.get("files", []))],
                payload=entry["name"])
        self.backup_filter._refresh_count()

    def _on_backup_selected(self) -> None:
        table = self.backup_table
        rows = table.selectionModel().selectedRows() \
            if table.selectionModel() else []
        if not rows:
            return
        self.statusMessage.emit(
            f"选中备份 {row_payload(table, rows[0].row())} —— "
            "点「回滚到选中备份…」可恢复。")

    def _on_backup_now(self) -> None:
        if self.session is None or self.session.world is None:
            return
        folder = self.session.world.create_backup(label="手动快照")
        if folder is None:
            self.statusMessage.emit("没有可备份的文件。")
            return
        self.refresh_backups()
        self.statusMessage.emit(f"已创建快照 backups/{folder.name}")

    def _on_full_snapshot(self) -> None:
        if self.session is None or self.session.world is None:
            return
        world = self.session.world
        files = [world.level_file()]
        old = world.path / "level.dat_old"
        if old.is_file():
            files.append(old)
        files.extend(world.player_files())
        files.extend(world.map_files())
        folder = world.create_backup(files, label="完整快照")
        if folder is None:
            self.statusMessage.emit("没有可备份的文件。")
            return
        self.refresh_backups()
        self.statusMessage.emit(
            f"已创建完整快照 backups/{folder.name}（{len(files)} 个文件）")

    def _on_restore_backup(self) -> None:
        table = self.backup_table
        rows = table.selectionModel().selectedRows() \
            if table.selectionModel() else []
        if not rows:
            QMessageBox.information(self, "提示", "请先选择一个备份。")
            return
        name = str(row_payload(table, rows[0].row()) or "")
        if not name or self.session is None:
            return
        if self.session.read_only:
            QMessageBox.information(self, "只读", "回滚也算写入，请先解锁编辑。")
            return
        answer = QMessageBox.warning(
            self, "确认回滚",
            f"将把备份 {name} 里的文件复制回存档目录。\n\n"
            "当前 level.dat / 玩家文件会被覆盖（回滚前也会再备份一次）。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Ok:
            return
        try:
            restored = self.session.restore_backup(name)
        except Exception as e:
            QMessageBox.warning(self, "回滚失败", str(e))
            return
        self.refresh_all()
        QMessageBox.information(
            self, "已回滚",
            "已恢复：" + "、".join(p.name for p in restored)
            + "\n\n注意：回滚会重建世界模型，撤销栈已清空"
              "（想再回到回滚前，请从备份列表里选另一份）。")

    def preview_diff(self) -> None:
        if self.session is None or self.session.world is None:
            return
        diff = self.session.diff_against(self.session.capture(), "待应用修改")
        self.diff_view.setPlainText(diff.text(limit=500) if diff
                                    else "（没有待应用的差异）")
        self.diff_summary.setText(
            f"{len(diff)} 处差异" if diff else "内存与磁盘一致")
        self.tabs.setCurrentIndex(self._tab_index("差异"))


class HealthTabMixin:
    """「健康检查」页签。"""

    def _build_health_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(*MARGINS["page"])
        outer.setSpacing(SPACING["sm"])

        box = SectionBox(
            "世界健康检查",
            "检查损坏 / 越界区块、缺失数据包、版本不匹配、超大 NBT、"
            "UUID 冲突、区域文件缺失；双击一行可复制路径。")
        self.health_filter = FilterBar(
            placeholder="筛选问题（级别 / 标题 / 路径）")
        self.health_table = make_table(["级别", "问题", "详情", "位置"])
        self.health_filter.add_table(self.health_table)
        self.health_table.itemDoubleClicked.connect(
            self._on_health_double_clicked)
        box.add_widget(self.health_filter)
        box.add_widget(self.health_table, 1)

        row = QHBoxLayout()
        row.setSpacing(SPACING["xs"])
        self.btn_health_run = QPushButton("重新检查")
        self.btn_health_run.clicked.connect(self.run_health_check)
        row.addWidget(self.btn_health_run)
        self.btn_health_export = QPushButton("导出报告")
        self.btn_health_export.clicked.connect(self._on_health_export)
        row.addWidget(self.btn_health_export)
        row.addStretch(1)
        self.health_summary = QLabel("")
        self.health_summary.setStyleSheet(muted_style(page))
        row.addWidget(self.health_summary)
        box.add_layout(row)
        outer.addWidget(box, 1)
        return page

    def run_health_check(self) -> None:
        world = self.world()
        self.health_table.setRowCount(0)
        if world is None:
            self.health_summary.setText("未打开世界")
            return
        issues = health_check(world)
        counts = {"error": 0, "warn": 0, "info": 0}
        for issue in issues:
            counts[issue.level] = counts.get(issue.level, 0) + 1
            add_row(self.health_table,
                    [_level_label(issue.level), issue.title, issue.detail,
                     issue.path])
        self.health_summary.setText(
            f"错误 {counts.get('error', 0)} · 警告 {counts.get('warn', 0)}"
            f" · 提示 {counts.get('info', 0)}")
        self.health_filter._refresh_count()
        self.tabs.setCurrentIndex(self._tab_index("健康检查"))

    def _on_health_double_clicked(self, item) -> None:
        if item is None:
            return
        path_item = self.health_table.item(item.row(), 3)
        text = path_item.text() if path_item is not None else ""
        if text:
            QGuiApplication.clipboard().setText(text)
            self.statusMessage.emit(f"已复制路径：{text}")

    def _on_health_export(self) -> None:
        if not self.health_table.rowCount():
            self.statusMessage.emit("还没有检查结果，先点「重新检查」。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出健康检查报告", "world_health.txt",
            "文本文件 (*.txt);;所有文件 (*)")
        if not path:
            return
        world = self.world()
        lines = [f"# 世界健康检查报告 — {world.name if world else '—'}", ""]
        for row in range(self.health_table.rowCount()):
            lines.append(" | ".join(
                self.health_table.item(row, column).text()
                if self.health_table.item(row, column) else ""
                for column in range(self.health_table.columnCount())))
        try:
            Path(path).write_text("\n".join(lines), encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        self.statusMessage.emit(f"已导出报告到 {path}")


class NlpTabMixin:
    """「中文世界编辑」页签：自然语言 → 操作 → 差异 → 应用。"""

    #: 示例句式（GUI 与 CLI 帮助共用）
    NLP_EXAMPLES: tuple[str, ...] = (
        "把时间设置为夜晚", "天气改成下雨", "难度改成困难",
        "永久白天", "禁止下雨", "世界边界设置为 2000",
        "在出生点生成 3 只僵尸", "把背包塞满钻石剑",
    )

    def _build_nlp_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(*MARGINS["page"])
        outer.setSpacing(SPACING["sm"])

        box = SectionBox(
            "中文世界编辑",
            "用中文描述要改什么。流程固定为：解析 → 列出操作 → 预览差异 → "
            "确认写入（每一步都能看到发生了什么）。")
        row = QHBoxLayout()
        row.setSpacing(SPACING["xs"])
        self.nlp_input = QLineEdit()
        self.nlp_input.setPlaceholderText(
            "例如：把时间设置为夜晚 / 在出生点生成 3 只僵尸 / 把背包塞满钻石剑")
        self.nlp_input.returnPressed.connect(self._on_nlp_parse)
        row.addWidget(self.nlp_input, 1)
        self.btn_nlp_parse = QPushButton("解析并预览")
        self.btn_nlp_parse.clicked.connect(self._on_nlp_parse)
        row.addWidget(self.btn_nlp_parse)
        box.add_layout(row)

        examples = QHBoxLayout()
        examples.setSpacing(SPACING["xxs"])
        examples.addWidget(QLabel("示例"))
        for text in self.NLP_EXAMPLES:
            chip = QToolButton()
            chip.setText(text)
            chip.setAutoRaise(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(
                lambda _=False, t=text: (self.nlp_input.setText(t),
                                         self._on_nlp_parse()))
            examples.addWidget(chip)
        examples.addStretch(1)
        box.add_layout(examples)

        self.nlp_view = QPlainTextEdit()
        self.nlp_view.setReadOnly(True)
        self.nlp_view.setFont(QFont("Consolas", FONTS["mono"]))
        self.nlp_view.setPlaceholderText("解析结果与差异会显示在这里")
        box.add_widget(self.nlp_view, 1)

        action = QHBoxLayout()
        action.setSpacing(SPACING["xs"])
        self.btn_nlp_apply = QPushButton("应用到世界（备份后）")
        self.btn_nlp_apply.setEnabled(False)
        self.btn_nlp_apply.clicked.connect(self._on_nlp_apply)
        action.addWidget(self.btn_nlp_apply)
        self.btn_nlp_commands = QPushButton("改成命令 → 输出区")
        self.btn_nlp_commands.setToolTip("不写存档，只生成等价的命令")
        self.btn_nlp_commands.setEnabled(False)
        self.btn_nlp_commands.clicked.connect(self._on_nlp_commands)
        action.addWidget(self.btn_nlp_commands)
        self.btn_nlp_clear = QPushButton("清空")
        self.btn_nlp_clear.clicked.connect(self._on_nlp_clear)
        action.addWidget(self.btn_nlp_clear)
        action.addStretch(1)
        self.nlp_status = QLabel("")
        self.nlp_status.setStyleSheet(muted_style(page))
        action.addWidget(self.nlp_status)
        box.add_layout(action)
        outer.addWidget(box, 1)

        self._nlp_operations: list = []
        return page

    def _on_nlp_parse(self) -> None:
        text = self.nlp_input.text().strip()
        if not text:
            self.nlp_status.setText("请输入一句中文描述。")
            return
        operations = bridge_mod.parse_cn_world_request(text,
                                                       session=self.session)
        self._nlp_operations = operations
        if not operations:
            self.nlp_view.setPlainText(
                "无法识别的请求。可以试试：\n"
                + "\n".join(f"  · {example}"
                            for example in self.NLP_EXAMPLES))
            self.btn_nlp_apply.setEnabled(False)
            self.btn_nlp_commands.setEnabled(False)
            self.nlp_status.setText("未识别")
            return

        lines = [f"■ 解析出 {len(operations)} 个操作："]
        for operation in operations:
            lines.append(f"    · {operation.summary()}")
        lines.append("")
        writable = (self.session is not None
                    and self.session.world is not None
                    and not self.session.read_only)
        if writable:
            lines.append("■ 差异预览：")
            for operation in operations[:8]:
                preview = bridge_mod.preview_operation(self.session, operation)
                lines.append(f"    [{operation.title}] {preview.message}")
        else:
            lines.append("■ 差异预览：需要先打开世界并解锁编辑")
        self.nlp_view.setPlainText("\n".join(lines))
        self.btn_nlp_apply.setEnabled(True)
        self.btn_nlp_commands.setEnabled(True)
        self.nlp_status.setText(f"{len(operations)} 个操作待确认")

    def _on_nlp_apply(self) -> None:
        if not self._nlp_operations:
            return
        if not self._guard_write():
            return
        text = self.nlp_input.text().strip() or "中文世界编辑"
        count = len(self._nlp_operations)
        answer = QMessageBox.question(
            self, "确认应用",
            f"将按「{text}」连续写入 {count} 个操作，"
            "确认后不再逐步询问（每步仍会自动备份）。\n\n"
            "中途某一步失败时会停下来，**已经写入的步骤不会回滚** —— "
            "想逐步核对请改用「改成命令 → 输出区」。\n\n确认继续？",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Ok:
            return
        lines: list[str] = []
        applied = 0
        for operation in self._nlp_operations:
            result = bridge_mod.apply_operation(self.session, operation)
            if result.ok:
                applied += 1
            lines.append(f"[{'✔' if result.ok else '✘'}] {operation.title}："
                         f"{result.message}")
            if not result.ok:
                lines.append(
                    f"\n已应用 {applied}/{count} 步，第 {applied + 1} 步失败并中止。"
                    "前几步已经写进存档（各有备份），"
                    "可以用「差异」页签逐个回滚。")
                break
        else:
            lines.append(f"\n已应用 {applied}/{count} 步（全部成功）。")
        self.nlp_view.setPlainText("\n".join(lines))
        self.refresh_all()
        self.statusMessage.emit(f"中文世界编辑已应用 {applied}/{count} 步。")

    def _on_nlp_commands(self) -> None:
        commands = [cmd for operation in self._nlp_operations
                    for cmd in operation.commands]
        if not commands:
            commands = self._nlp_equivalent_commands()
        if not commands:
            self.statusMessage.emit("这些操作没有等价的命令（属于存档直改）。")
            return
        self.commandsReady.emit("\n".join(commands))
        self.statusMessage.emit(f"已生成 {len(commands)} 条命令到输出区。")

    def _nlp_equivalent_commands(self) -> list[str]:
        """把「存档直改」的操作翻译成等价命令（不写存档时用）。"""
        out: list[str] = []
        for operation in self._nlp_operations:
            payload = operation.payload
            if operation.kind == "time":
                out.append(f"time set {payload.get('value', 0)}")
            elif operation.kind == "weather":
                out.append(f"weather {payload.get('weather', 'clear')} "
                           f"{payload.get('duration', 6000)}")
            elif operation.kind == "gamerule":
                out.append(f"gamerule {payload.get('rule', '')} "
                           f"{payload.get('value', 'true')}")
            elif operation.kind == "worldborder":
                out.append(f"worldborder set {payload.get('size', 1000)}")
            elif operation.kind == "summon":
                out.append(f"summon {payload.get('entity', 'minecraft:zombie')} "
                           f"{payload.get('pos', '~ ~ ~')}")
            elif operation.kind == "give":
                out.append(f"give @p {payload.get('item', 'minecraft:stone')} "
                           f"{payload.get('count', 1)}")
            elif operation.kind == "scene":
                out.extend(["time set 6000",
                            "gamerule doDaylightCycle false",
                            "weather clear 1000000",
                            "gamerule doWeatherCycle false",
                            "difficulty peaceful"])
        return out

    def _on_nlp_clear(self) -> None:
        self.nlp_input.clear()
        self.nlp_view.clear()
        self._nlp_operations = []
        self.btn_nlp_apply.setEnabled(False)
        self.btn_nlp_commands.setEnabled(False)
        self.nlp_status.setText("")


class CrossWorldTabMixin:
    """「跨存档」页签：从另一个存档搬运玩家数据 / 进度 / 地图。"""

    def _build_crossworld_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(*MARGINS["page"])
        outer.setSpacing(SPACING["sm"])

        box = SectionBox(
            "跨存档复制",
            "从另一个存档读取内容，写进当前存档。物品 / 进度 / 统计 / 地图"
            "都能搬；确认后会先备份，也可以改成只生成命令。")
        row = QHBoxLayout()
        row.setSpacing(SPACING["xs"])
        self.cross_path = QLineEdit()
        self.cross_path.setPlaceholderText("另一个存档目录（含 level.dat）")
        row.addWidget(self.cross_path, 1)
        self.btn_cross_pick = QPushButton("选择目录…")
        self.btn_cross_pick.clicked.connect(self._on_pick_source_world)
        row.addWidget(self.btn_cross_pick)
        self.btn_cross_load = QPushButton("读取")
        self.btn_cross_load.clicked.connect(self._on_load_source_world)
        row.addWidget(self.btn_cross_load)
        box.add_layout(row)

        self.cross_source = QComboBox()
        self.cross_source.setMinimumWidth(SIZES["combo_width"]
                                          if hasattr(SIZES, "combo_width")
                                          else SIZES["combo_min"])
        box.add_layout(_label_row("来源玩家", self.cross_source))
        self.cross_target = QComboBox()
        box.add_layout(_label_row("目标玩家", self.cross_target))

        self.cross_info = QLabel("还没有读取来源存档。")
        self.cross_info.setStyleSheet(muted_style(page))
        self.cross_info.setWordWrap(True)
        box.add_widget(self.cross_info)

        content_row = QHBoxLayout()
        content_row.setSpacing(SPACING["sm"])
        self.cross_checks: dict[str, QCheckBox] = {}
        for key, label in (("inventory", "背包"), ("ender", "末影箱"),
                           ("effects", "状态效果"), ("attributes", "属性"),
                           ("advancements", "进度"), ("stats", "统计"),
                           ("maps", "地图")):
            check = QCheckBox(label)
            check.setChecked(key in ("inventory", "ender"))
            self.cross_checks[key] = check
            content_row.addWidget(check)
        content_row.addStretch(1)
        box.add_layout(content_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(SPACING["xs"])
        self.btn_cross_preview = QPushButton("预览差异")
        self.btn_cross_preview.clicked.connect(self._on_cross_preview)
        action_row.addWidget(self.btn_cross_preview)
        self.btn_cross_apply = QPushButton("复制到当前存档（备份后）")
        self.btn_cross_apply.clicked.connect(self._on_cross_apply)
        action_row.addWidget(self.btn_cross_apply)
        self.btn_cross_commands = QPushButton("改成 /give 命令")
        self.btn_cross_commands.clicked.connect(self._on_cross_commands)
        action_row.addWidget(self.btn_cross_commands)
        action_row.addStretch(1)
        box.add_layout(action_row)

        self.cross_view = QPlainTextEdit()
        self.cross_view.setReadOnly(True)
        self.cross_view.setFont(QFont("Consolas", FONTS["mono"]))
        self.cross_view.setPlaceholderText("预览结果会显示在这里")
        box.add_widget(self.cross_view, 1)
        outer.addWidget(box, 1)

        self._source_world = None
        return page

    def _on_pick_source_world(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择来源世界存档目录", self.cross_path.text().strip())
        if path:
            self.cross_path.setText(path)
            self._on_load_source_world()

    def _on_load_source_world(self) -> None:
        """读取来源存档：放到后台线程，避免大存档把界面卡成「未响应」。"""
        path = self.cross_path.text().strip()
        if not path:
            self.statusMessage.emit("请先选择来源存档目录。")
            return
        if getattr(self, "_source_loader", None) is not None \
                and self._source_loader.busy:
            self.statusMessage.emit("正在读取上一个来源存档，请稍候…")
            return
        loader = getattr(self, "_source_loader", None)
        if loader is None:
            loader = WorldLoader(self)
            loader.progress.connect(self._on_source_progress)
            loader.loaded.connect(self._adopt_source_world)
            loader.failed.connect(self._on_source_failed)
            self._source_loader = loader
        self._source_progress = QProgressDialog(
            "正在读取来源存档…", "取消", 0, 0, self)
        self._source_progress.setWindowTitle("读取来源存档")
        self._source_progress.setWindowModality(
            Qt.WindowModality.WindowModal)
        self._source_progress.setMinimumDuration(0)
        self._source_progress.canceled.connect(loader.cancel)
        self._source_progress.show()
        loader.load(path)

    def _on_source_progress(self, done: int, total: int, text: str) -> None:
        dialog = getattr(self, "_source_progress", None)
        if dialog is None or dialog.wasCanceled():
            return
        if total > 0:
            dialog.setRange(0, total)
            dialog.setValue(min(done, total))
        dialog.setLabelText(text or "正在读取来源存档…")

    def _close_source_progress(self) -> None:
        dialog = getattr(self, "_source_progress", None)
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
            self._source_progress = None

    def _on_source_failed(self, message: str) -> None:
        self._close_source_progress()
        QMessageBox.warning(self, "读取失败", message)

    def _adopt_source_world(self, world) -> None:
        self._close_source_progress()
        if world is None:                                # pragma: no cover
            return
        if not world.is_open():
            QMessageBox.warning(
                self, "读取失败",
                "；".join(world.errors) or "这不是一个完整的存档目录")
            return
        self._source_world = world
        self.cross_source.clear()
        for uuid in sorted(world.players):
            player = world.players[uuid]
            self.cross_source.addItem(
                f"{player.display()} · {player.item_count} 件物品", uuid)
        self.cross_target.clear()
        current = self.world()
        if current is not None:
            for uuid in sorted(current.players):
                self.cross_target.addItem(current.players[uuid].display(), uuid)
        self.cross_info.setText(
            f"来源：{world.name}（{world.path}）\n"
            f"玩家 {len(world.players)} 名 · 地图 {len(world.maps)} 张 · "
            f"DataVersion {world.overview.data_version}"
            f"（{world.overview.version_label}）")
        self.statusMessage.emit(f"已读取来源存档：{world.name}")

    def _selected_cross_player(self, combo: QComboBox):
        uuid = str(combo.currentData() or "")
        if self._source_world is None or not uuid:
            return None
        return self._source_world.players.get(uuid)

    def _cross_plan(self) -> dict:
        return {key: check.isChecked()
                for key, check in self.cross_checks.items()}

    def _on_cross_preview(self) -> None:
        source = self._selected_cross_player(self.cross_source)
        if source is None:
            self.statusMessage.emit("请先读取来源存档并选择玩家。")
            return
        plan = self._cross_plan()
        lines = [f"■ 来源玩家：{source.display()}", "■ 计划搬运："
                 + "、".join(k for k, v in plan.items() if v) or "（未勾选）"]
        if plan.get("inventory") or plan.get("ender"):
            from app.core.nbt_bridge import collect_items
            items = collect_items(source.root) if source.root else []
            lines.append(f"■ 可搬运物品：{len(items)} 件")
            for stack in items[:20]:
                lines.append(f"    · {stack.summary()}")
        if plan.get("effects"):
            lines.append(f"■ 状态效果：{len(source.effects)} 条")
        if plan.get("attributes"):
            lines.append(f"■ 属性：{len(source.attributes)} 条")
        if plan.get("advancements"):
            lines.append(f"■ 进度：{len(source.advancements)} 项")
        if plan.get("stats"):
            lines.append(f"■ 统计：{len(source.stats)} 条")
        if plan.get("maps"):
            lines.append(f"■ 地图：{len(self._source_world.maps)} 张")
        self.cross_view.setPlainText("\n".join(lines))

    def _on_cross_apply(self) -> None:
        if not self._guard_write():
            return
        source = self._selected_cross_player(self.cross_source)
        target = self._selected_cross_player(self.cross_target)
        if source is None or target is None:
            self.statusMessage.emit("请选择来源玩家与目标玩家。")
            return
        plan = self._cross_plan()
        if not any(plan.values()):
            self.statusMessage.emit("请至少勾选一项要搬运的内容。")
            return

        def runner(session) -> str:
            return _cross_copy(session, self._source_world, source, target,
                               plan)

        operation = bridge_mod.BridgeOperation(
            kind="player_field",
            label=f"跨存档复制：{source.display()} → {target.display()}",
            source="editor", target=target.uuid,
            payload={"plan": [k for k, v in plan.items() if v]},
            runner=runner)
        self._run_bridge(operation)

    def _on_cross_commands(self) -> None:
        source = self._selected_cross_player(self.cross_source)
        if source is None:
            self.statusMessage.emit("请先读取来源存档并选择玩家。")
            return
        from app.core.nbt_bridge import stack_to_command
        target = self._selected_cross_player(self.cross_target)
        selector = f"@a[name={target.display()}]" if target is not None \
            and target.name else "@p"
        items = collect_items(source.root) if source.root else []
        lines = [stack_to_command(stack, selector) for stack in items]
        for effect in source.effects:
            lines.append(f"effect give {selector} {effect['id']} "
                         f"{effect['duration']} {effect['amplifier']}")
        if not lines:
            self.statusMessage.emit("没有可生成的命令。")
            return
        self.commandsReady.emit("\n".join(lines))
        self.statusMessage.emit(f"已生成 {len(lines)} 条搬运命令。")


# ---------------------------------------------------------------------------
# 模块内辅助
# ---------------------------------------------------------------------------

def _stacks_of(node: SnbtNode):
    """把一个物品容器节点转成 ItemStack 列表（识别不了就返回空）。"""
    from app.core.nbt_bridge import is_item_container
    out = []
    if not is_item_container(node):
        return out
    for position, child in enumerate(node.children):
        try:
            out.append(parse_item(child, f"[{position}]", "容器"))
        except Exception:                               # pragma: no cover
            continue
    return out


def _container_payload(node: SnbtNode) -> str:
    """把容器内容包成 `{Items:[…]}`，供 `/setblock` 使用。"""
    wrapper = SnbtNode.new_compound()
    clone = node.clone()
    clone.key = "Items"
    wrapper.add_child(clone)
    return serialize(wrapper, pretty=False)


def _label_row(text: str, widget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(SPACING["xs"])
    label = QLabel(text)
    label.setMinimumWidth(72)
    row.addWidget(label)
    row.addWidget(widget, 1)
    return row


def _cross_copy(session, source_world, source, target, plan: dict) -> str:
    """把来源玩家的内容写进当前存档的目标玩家。"""
    from app.core.snbt import SnbtNode as _Node
    world = session.world
    root = world.player_root(target.uuid)
    if root is None:
        raise KeyError("目标玩家没有可写的节点")
    done: list[str] = []

    def copy_container(key: str, path: str, label: str) -> None:
        node = source.root.get_child(path) if source.root else None
        if node is None or not node.children:
            return
        existing = root.get_child(path)
        payload = node.clone()
        payload.key = path
        if existing is not None:
            index = root.children.index(existing)
            root.children[index] = payload
        else:
            root.add_child(payload)
        done.append(f"{label} {len(node.children)} 格")

    if plan.get("inventory"):
        copy_container("inventory", "Inventory", "背包")
    if plan.get("ender"):
        copy_container("ender", "EnderItems", "末影箱")
    if plan.get("effects"):
        effects = source.root.get_child("active_effects") if source.root \
            else None
        if effects is not None:
            payload = effects.clone()
            payload.key = "active_effects"
            existing = root.get_child("active_effects")
            if existing is not None:
                root.children[root.children.index(existing)] = payload
            else:
                root.add_child(payload)
            done.append(f"效果 {len(effects.children)} 条")
    if plan.get("attributes"):
        attributes = source.root.get_child("Attributes") if source.root \
            else None
        if attributes is not None:
            payload = attributes.clone()
            payload.key = "Attributes"
            existing = root.get_child("Attributes")
            if existing is not None:
                root.children[root.children.index(existing)] = payload
            else:
                root.add_child(payload)
            done.append(f"属性 {len(attributes.children)} 条")
    if plan.get("advancements"):
        target.advancements = list(source.advancements)
        target.stats = dict(target.stats)
        done.append(f"进度 {len(source.advancements)} 项（记录在会话里）")
    if plan.get("stats"):
        target.stats = dict(source.stats)
        done.append(f"统计 {len(source.stats)} 条（记录在会话里）")
    if plan.get("maps"):
        for index, info in source_world.maps.items():
            if info.root is None:
                continue
            payload = info.root.clone()
            existing = world.maps.get(index)
            if existing is not None:
                existing.root = payload
                existing.dirty = True
            done.append(f"地图 #{index}")
    _ = _Node
    return "已复制：" + ("、".join(done) if done else "（没有可复制的内容）")


def _limit_function(text: str, limit: int) -> str:
    """按命令条数上限裁剪函数内容（保留注释头）。"""
    lines = text.splitlines()
    head = [line for line in lines if line.startswith("#")]
    body = [line for line in lines if not line.startswith("#")]
    if len(body) <= limit:
        return text
    return "\n".join(head + body[:limit]
                     + [f"# … 还有 {len(body) - limit} 条未显示，"
                        "完整内容可在输出区用「导出数据包」拿到"])


def _recipe_components(recipe) -> str:
    from app.core.nbt_bridge import component_value_snbt
    chunks = []
    for entry in getattr(recipe, "components", []) or []:
        cid = str(entry.get("id", "")).split(":", 1)[-1]
        value = component_value_snbt(entry.get("value", ""))
        chunks.append(f"{cid}={value}" if value else cid)
    return ",".join(chunks)


def _safe_recipe_id(name: str) -> str:
    import re
    text = re.sub(r"[^\w\u4e00-\u9fa5.\-]+", "_", str(name or "")).strip("_")
    return text or "recipe"


def _level_label(level: str) -> str:
    return {"error": "错误", "warn": "警告", "info": "提示"}.get(level, level)


def _apply_map_field(session, index: int, path: str, value: int) -> str:
    session.world.set_node_value(f"map:{index}", path, value, SnbtKind.BYTE)
    return f"地图 #{index} 的 {path} 已设为 {value}"


__all__ = [
    "EntityTabMixin", "ContainerTabMixin", "NbtTabMixin", "MapTabMixin",
    "RegionTabMixin", "DatapackTabMixin", "BlueprintTabMixin", "DiffTabMixin",
    "HealthTabMixin", "NlpTabMixin", "CrossWorldTabMixin",
]
