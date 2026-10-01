"""离屏 GUI 测试：双面板骨架、世界编辑器面板、联动总线、工作区模式。"""
from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path

from tests import TEST_ROOT  # noqa: F401  （导入即设置环境变量）

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication            # noqa: E402

from app.core.bridge import BridgeOperation           # noqa: E402
from app.ui.bridge_drawer import BridgeDrawer, LinkContext  # noqa: E402
from app.ui.theme import MARGINS, SPACING             # noqa: E402
from app.ui.world_common import (                     # noqa: E402
    FilterBar, SectionBox, StateBanner, export_table_csv, filter_tables,
    make_table,
)
from app.ui.world_dialogs import (                     # noqa: E402
    BlueprintExportDialog, CrossWorldCopyDialog, GrowItemsDialog,
    NlpWorldDialog, PresetDialog, RegionChunkWriteDialog, SceneDialog,
)
from app.ui.world_panel import EDITOR_TABS, WorldPanel  # noqa: E402

_app = QApplication.instance() or QApplication([])

PLAYER = "11111111-2222-3333-4444-555555555555"


def build_world(name: str) -> Path:
    import sys
    if str(TEST_ROOT) not in sys.path:                  # pragma: no cover
        sys.path.insert(0, str(TEST_ROOT))
    from tools.fake_world import build_fake_world
    root = Path(TEST_ROOT) / "build" / "gui-worlds" / name
    if root.exists():
        shutil.rmtree(root)
    return build_fake_world(root)


class GuiCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from app.ui.main_window import MainWindow
        cls.window = MainWindow()
        cls.window.resize(1440, 900)
        cls.window.show()
        QApplication.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        cls.window.deleteLater()


class TestDualPanelSkeleton(GuiCase):
    def test_world_panel_exists(self) -> None:
        self.assertTrue(hasattr(self.window, "world_panel"))
        self.assertIsNotNone(self.window.world_panel)

    def test_bridge_drawer_exists(self) -> None:
        self.assertTrue(hasattr(self.window, "bridge_panel"))
        self.assertIsInstance(self.window.bridge_panel, BridgeDrawer)

    def test_editor_tabs_order(self) -> None:
        tabs = [self.window.world_panel.tabs.tabText(i)
                for i in range(self.window.world_panel.tabs.count())]
        self.assertEqual(tabs, list(EDITOR_TABS))
        for required in ("世界总览", "玩家", "实体", "容器", "NBT 树",
                         "区域", "地图", "数据包", "差异"):
            self.assertIn(required, tabs)

    def test_right_tabs_have_output_and_bridge(self) -> None:
        tabs = [self.window.right_tabs.tabText(i)
                for i in range(self.window.right_tabs.count())]
        self.assertIn("命令输出", tabs)
        self.assertIn("联动总线", tabs)

    def test_workspace_modes(self) -> None:
        for mode in ("command", "editor", "dual"):
            self.window.set_workspace(mode)
            QApplication.processEvents()
            self.assertEqual(self.window.workspace, mode)
            if mode == "editor":
                self.assertTrue(self.window.left_pane.isHidden())
                self.assertTrue(self.window.right_pane.isHidden())
                self.assertIs(self.window.center_stack.currentWidget(),
                              self.window.world_panel)
            elif mode == "command":
                self.assertTrue(self.window.right_pane.isHidden() is False)
                self.assertFalse(self.window.left_pane.isHidden())
                self.assertIs(self.window.center_stack.currentWidget(),
                              self.window.center_tabs)
            else:
                self.assertFalse(self.window.left_pane.isHidden())
                self.assertFalse(self.window.right_pane.isHidden())
                self.assertFalse(self.window.bridge_panel.isHidden())
        self.window.set_workspace("dual")

    def test_unknown_workspace_ignored(self) -> None:
        before = self.window.workspace
        self.window.set_workspace("nope")
        self.assertEqual(self.window.workspace, before)

    def test_workspace_remembered(self) -> None:
        self.window.set_workspace("editor")
        self.window._save_session_draft()
        saved = self.window._draft_store.load("workspace")
        self.assertEqual(saved["mode"], "editor")
        self.window.set_workspace("dual")

    def test_detach_and_reattach_editor(self) -> None:
        self.window.set_workspace("dual")
        self.window.toggle_editor_window()
        QApplication.processEvents()
        self.assertTrue(self.window._editor_detached)
        self.assertEqual(self.window.world_panel.parent(),
                         self.window.editor_window)
        self.window.toggle_editor_window()
        QApplication.processEvents()
        self.assertFalse(self.window._editor_detached)
        self.assertGreaterEqual(self.window.center_stack.indexOf(
            self.window.world_panel), 0)

    def test_statusbar_context_widgets(self) -> None:
        for name in ("ctx_world", "ctx_player", "ctx_dimension", "ctx_pos",
                     "ctx_save"):
            self.assertTrue(hasattr(self.window, name), name)
        self.window._refresh_status_context()
        self.assertIn("世界", self.window.ctx_world.text())

    def test_window_title_uses_brand(self) -> None:
        from app import branding
        self.assertTrue(self.window.windowTitle().startswith(
            branding.APP_NAME))

    def test_open_output_to_bridge_shortcut_wired(self) -> None:
        # 只验证按钮与 API 存在，不弹模态框
        self.assertTrue(hasattr(self.window, "send_output_to_bridge"))
        self.assertTrue(hasattr(self.window, "_on_bridge_apply"))
        self.assertTrue(hasattr(self.window, "_on_bridge_commands"))


class TestLinkContext(unittest.TestCase):
    def test_describe_has_all_fields(self) -> None:
        context = LinkContext(current_world="W", current_player="P",
                              current_dimension="minecraft:the_nether",
                              current_pos=(1.0, 2.0, 3.0),
                              selected_block="minecraft:stone",
                              selected_nbt_path="Data.X",
                              selected_container="Inventory")
        rows = dict(context.describe())
        self.assertEqual(rows["当前世界"], "W")
        self.assertEqual(rows["当前玩家"], "P")
        self.assertIn("the_nether", rows["当前维度"])
        self.assertEqual(rows["当前坐标"], "1.00 2.00 3.00")
        self.assertEqual(rows["选中方块"], "minecraft:stone")
        self.assertEqual(rows["选中 NBT 路径"], "Data.X")
        self.assertEqual(rows["选中容器"], "Inventory")

    def test_is_empty(self) -> None:
        self.assertTrue(LinkContext().is_empty())
        self.assertFalse(LinkContext(current_world="x").is_empty())

    def test_long_values_truncated(self) -> None:
        context = LinkContext(current_world="x" * 200)
        rows = dict(context.describe())
        self.assertLessEqual(len(rows["当前世界"]), 64)


class TestBridgeDrawer(unittest.TestCase):
    def setUp(self) -> None:
        self.drawer = BridgeDrawer()
        self.messages: list[str] = []
        self.drawer.statusMessage.connect(self.messages.append)

    def tearDown(self) -> None:
        self.drawer.deleteLater()

    def test_push_and_queue(self) -> None:
        self.assertTrue(self.drawer.push(BridgeOperation(kind="time",
                                                         label="设置时间",
                                                         commands=["time set 1"])))
        self.assertEqual(len(self.drawer.pending), 1)
        self.assertEqual(self.drawer.pending_table.rowCount(), 1)
        self.assertIn("队列 1 项", self.drawer.count_label.text())

    def test_link_off_ignores_push(self) -> None:
        self.drawer.set_linked(False)
        self.assertFalse(self.drawer.push(BridgeOperation(kind="time")))
        self.assertEqual(self.drawer.pending, [])
        self.assertTrue(any("联动总线已关闭" in m for m in self.messages))

    def test_push_none_ignored(self) -> None:
        self.assertFalse(self.drawer.push(None))

    def test_push_many_and_clear(self) -> None:
        count = self.drawer.push_many([
            BridgeOperation(kind="time"), BridgeOperation(kind="weather"),
            BridgeOperation(kind="gamerule")])
        self.assertEqual(count, 3)
        self.drawer.clear_pending()
        self.assertEqual(self.drawer.pending, [])

    def test_preview_lists_sources_and_targets(self) -> None:
        self.drawer.push(BridgeOperation(
            kind="give", source="editor", target="player:X",
            payload={"item": "minecraft:stone", "count": 5},
            commands=["give @p minecraft:stone 5"], notes=["来源：背包"]))
        self.drawer.on_preview()
        text = self.drawer.log_edit.toPlainText()
        self.assertIn("give @p minecraft:stone 5", text)
        self.assertIn("来源：背包", text)
        self.assertIn("item = minecraft:stone", text)

    def test_commands_requested_signal(self) -> None:
        seen: list[str] = []
        self.drawer.commandsRequested.connect(seen.append)
        self.drawer.push(BridgeOperation(kind="give", commands=["give @p x 1"]))
        self.drawer.on_commands()
        self.assertEqual(seen, ["give @p x 1"])

    def test_commands_without_commands_warns(self) -> None:
        self.drawer.push(BridgeOperation(kind="time"))
        self.drawer.on_commands()
        self.assertTrue(any("没有可生成的命令" in m or "方向" in m
                            for m in self.messages))

    def test_apply_requested_signal(self) -> None:
        seen: list[object] = []
        self.drawer.applyRequested.connect(seen.append)
        operation = BridgeOperation(kind="time")
        self.drawer.push(operation)
        self.drawer.on_apply()
        self.assertEqual(seen, [operation])

    def test_copy_commands(self) -> None:
        self.drawer.push(BridgeOperation(kind="give", commands=["give @p x 1"]))
        self.drawer.on_copy()
        self.assertEqual(QApplication.clipboard().text(), "give @p x 1")

    def test_log_appends_newest_first(self) -> None:
        self.drawer.log("第一条")
        self.drawer.log("第二条")
        lines = self.drawer.log_edit.toPlainText().splitlines()
        self.assertIn("第二条", lines[0])
        self.assertIn("第一条", lines[1])

    def test_send_item_and_entity(self) -> None:
        from app.core.nbt_bridge import EntityData, ItemStack
        self.assertTrue(self.drawer.send_item(
            ItemStack(item_id="minecraft:stone", count=3)))
        self.assertTrue(self.drawer.send_entity(
            EntityData(entity_id="minecraft:zombie", nbt="{IsBaby:0b}")))
        self.assertEqual(len(self.drawer.pending), 2)

    def test_send_coords_and_nbt_path(self) -> None:
        self.assertTrue(self.drawer.send_coords(1.0, 2.0, 3.0))
        self.assertTrue(self.drawer.send_nbt_path("Data.X", "1"))
        kinds = [op.kind for op in self.drawer.pending]
        self.assertIn("data", kinds)

    def test_send_command_unknown(self) -> None:
        self.assertFalse(self.drawer.send_command("say hi"))

    def test_send_command_known(self) -> None:
        self.assertTrue(self.drawer.send_command("time set 1000"))
        self.assertEqual(self.drawer.pending[0].kind, "time")
        self.assertIsNotNone(self.drawer.pending[0].runner)

    def test_selected_commands_defaults_to_all(self) -> None:
        self.drawer.push_many([BridgeOperation(kind="give", commands=["a"]),
                               BridgeOperation(kind="give", commands=["b"])])
        self.assertEqual(self.drawer.selected_commands(), ["a", "b"])


class TestWorldPanel(GuiCase):
    def setUp(self) -> None:
        self.window.set_workspace("editor")
        self.panel = self.window.world_panel
        self.panel.async_open = False
        self.path = build_world("panel")
        self.panel.open_path(self.path)
        QApplication.processEvents()

    def tearDown(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
        self.window.set_workspace("dual")

    def test_session_opened_read_only(self) -> None:
        self.assertIsNotNone(self.panel.session)
        self.assertTrue(self.panel.session.read_only)
        self.assertIn("只读", self.panel.state_label.text())

    def test_overview_populated(self) -> None:
        self.assertGreater(self.panel.overview_table.rowCount(), 10)
        self.assertGreater(self.panel.rules_table.rowCount(), 0)
        self.assertEqual(self.panel.level_name_edit.text(), "测试世界")

    def test_players_and_inventory(self) -> None:
        self.assertEqual(self.panel.player_list.count(), 1)
        self.panel.player_list.setCurrentRow(0)
        QApplication.processEvents()
        self.assertGreater(self.panel.player_table.rowCount(), 10)
        self.assertGreater(self.panel.player_inv_table.rowCount(), 0)
        self.assertIn("生命", self.panel.player_summary.text())

    def test_containers_and_maps_and_regions(self) -> None:
        self.assertGreater(self.panel.container_table.rowCount(), 0)
        self.assertEqual(self.panel.map_table.rowCount(), 1)
        self.assertEqual(self.panel.region_table.rowCount(), 1)
        self.assertIn("区块", self.panel.region_count.text())

    def test_region_chunks_rendered(self) -> None:
        self.panel.region_table.selectRow(0)
        QApplication.processEvents()
        self.assertGreater(self.panel.region_chunks.rowCount(), 0)

    def test_health_check_fills_table(self) -> None:
        self.panel.run_health_check()
        self.assertGreater(self.panel.health_table.rowCount(), 0)
        self.assertIn("错误", self.panel.health_summary.text())

    def test_nbt_tree_bound(self) -> None:
        self.panel._on_load_nbt_source()
        QApplication.processEvents()
        root = self.panel.nbt_editor.editor.root()
        self.assertIsNotNone(root.get_child("Data"))
        self.assertIsNone(self.panel.nbt_editor.doc.path)

    def test_write_buttons_disabled_in_read_only(self) -> None:
        self.assertFalse(self.panel.btn_set_time.isEnabled())
        self.assertFalse(self.panel.btn_apply.isEnabled())

    def test_unlock_enables_buttons(self) -> None:
        self.panel.session.unlock()
        self.panel._refresh_session_state()
        self.assertTrue(self.panel.btn_set_time.isEnabled())
        self.assertIn("可编辑", self.panel.state_label.text())

    def test_apply_changes_requires_diff(self) -> None:
        self.panel.session.unlock()
        self.panel._refresh_session_state()
        # 没有修改时按钮应该是禁用的
        self.assertFalse(self.panel.btn_apply.isEnabled())

    def test_grow_items_dialog_values(self) -> None:
        dialog = GrowItemsDialog(self.panel)
        dialog.count.setValue(12)
        dialog.container.setText("EnderItems")
        item, count, mode, slot, container, components = dialog.values()
        self.assertEqual(count, 12)
        self.assertEqual(container, "EnderItems")
        self.assertEqual(mode, "first_empty")
        self.assertIsNone(slot)
        self.assertEqual(components, "")
        self.assertTrue(item)
        dialog.deleteLater()

    def test_scene_dialog_payload(self) -> None:
        dialog = SceneDialog(self.panel)
        dialog.spawn.setText("5 70 5")
        dialog.border.setText("2000")
        payload = dialog.payload()
        self.assertTrue(payload["permanent_day"])
        self.assertEqual(payload["spawn"], "5 70 5")
        self.assertEqual(payload["border_size"], 2000.0)
        dialog.deleteLater()

    def test_recipe_table_populated(self) -> None:
        self.panel.refresh_recipes()
        self.assertGreater(self.panel.recipe_table.rowCount(), 0)

    def test_blueprint_source_lists_block_entities(self) -> None:
        self.panel.refresh_blueprints()
        # 假世界没有 block_entities，下拉里应该给出提示项
        self.assertGreaterEqual(self.panel.blueprint_source.count(), 1)

    def test_bridge_context_updated_on_open(self) -> None:
        self.assertIn("测试世界",
                      self.window.bridge_panel.context.current_world)
        self.assertIn("测试世界", self.window.ctx_world.text())

    def test_statusbar_save_state(self) -> None:
        self.assertIn("只读", self.window.ctx_save.text())
        self.panel.session.unlock()
        self.panel._refresh_session_state()
        self.window._refresh_status_context()
        self.assertIn("可编辑", self.window.ctx_save.text())

    def test_auto_snapshot_created(self) -> None:
        backups = self.panel.session.list_backups()
        self.assertTrue(any("打开世界自动快照" in (b.get("label") or "")
                            for b in backups))

    def test_reload_keeps_world(self) -> None:
        self.panel.reload()
        QApplication.processEvents()
        self.assertIsNotNone(self.panel.session)
        self.assertEqual(self.panel.session.world.overview.name, "测试世界")

    def test_players_to_commands_button(self) -> None:
        seen: list[str] = []
        self.panel.commandsReady.connect(seen.append)
        self.panel._on_players_to_commands()
        self.assertTrue(seen)
        self.assertIn("execute as", seen[0])

    def test_world_function_export(self) -> None:
        seen: list[str] = []
        self.panel.commandsReady.connect(seen.append)
        self.panel._on_export_world_function()
        self.assertTrue(seen)
        self.assertIn("time set", seen[0])

    def test_region_export(self) -> None:
        seen: list[str] = []
        self.panel.commandsReady.connect(seen.append)
        self.panel.region_table.selectRow(0)
        self.panel._on_region_export()
        self.assertTrue(seen)
        self.assertIn("区块", seen[0])

    def test_nbt_source_combo_lists_players_and_maps(self) -> None:
        labels = [self.panel.nbt_source.itemText(i)
                  for i in range(self.panel.nbt_source.count())]
        self.assertIn("level.dat", labels)
        self.assertTrue(any("playerdata" in label for label in labels))
        self.assertTrue(any("map_" in label for label in labels))


class TestNewTabs(GuiCase):
    """新增页签：可视化编排 / 数据包工程。"""

    def setUp(self) -> None:
        self.window.set_workspace("editor")
        self.panel = self.window.world_panel
        self.panel.async_open = False
        self.path = build_world("newtabs")
        self.panel.open_path(self.path)
        QApplication.processEvents()

    def tearDown(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
        self.window.set_workspace("dual")

    def test_tab_list_includes_new_tabs(self) -> None:
        tabs = [self.panel.tabs.tabText(i)
                for i in range(self.panel.tabs.count())]
        self.assertIn("可视化编排", tabs)
        self.assertIn("数据包工程", tabs)
        self.assertEqual(len(tabs), 15)

    def test_canvas_markers_refresh(self) -> None:
        self.panel.refresh_creative()
        kinds = {marker.kind for marker in self.panel.canvas.markers}
        self.assertIn("spawn", kinds)
        self.assertTrue(self.panel.canvas.markers)

    def test_canvas_paint_and_export(self) -> None:
        from app.ui.world_creative import TOOL_PAINT
        canvas = self.panel.canvas
        canvas.clear_grid()                     # 前面用例可能留下内容
        canvas.set_layer(64)
        canvas.brush = "minecraft:gold_block"
        canvas.tool = TOOL_PAINT
        canvas.snapshot()
        for x in range(3):
            canvas._apply_tool(x, 0)
        self.assertEqual(len(canvas.grid), 3)
        seen: list[str] = []
        self.panel.commandsReady.connect(seen.append)
        self.panel._on_canvas_fill()
        self.assertTrue(seen)
        self.assertIn("gold_block", seen[0])

    def test_canvas_copies_data_command(self) -> None:
        canvas = self.panel.canvas
        canvas.set_layer(64)
        canvas.brush = "minecraft:stone"
        canvas.tool = "paint"
        canvas._apply_tool(0, 0)
        self.panel._on_canvas_copy_data()
        self.assertIn("data modify block", QApplication.clipboard().text())

    def test_canvas_region_write_targets_existing_chunks(self) -> None:
        """选区里的可写区块必须能在现有区域索引里被找到。

        这里只验证「选区 → 候选区块」这一段的纯逻辑，
        真正的写入（会弹确认框）在 test_creative 里用 API 覆盖。
        """
        from app.ui.world_creative import BlockGrid
        canvas = self.panel.canvas
        canvas.grid = BlockGrid()
        for x in range(2):
            for z in range(2):
                canvas.grid.set(x, 64, z, "minecraft:gold_block")
        world = self.panel.session.world
        targets = [
            (str(region.path), chunk.x, chunk.z)
            for region in world.regions.values()
            for chunk in region.chunks
            if 0 <= chunk.x <= 0 and 0 <= chunk.z <= 0 and chunk.exists()
        ]
        self.assertTrue(targets)
        path, cx, cz = targets[0]
        node = self.panel._chunk_with_paint(world, path, cx, cz)
        self.assertIsNotNone(node)

    def test_canvas_merges_paint_into_chunk(self) -> None:
        canvas = self.panel.canvas
        canvas.set_layer(64)
        for x in range(2):
            for z in range(2):
                canvas.grid.set(x, 64, z, "minecraft:gold_block")
        world = self.panel.session.world
        region_path = str(next(iter(world.regions.values())).path)
        node = self.panel._chunk_with_paint(world, region_path, 0, 0)
        self.assertIsNotNone(node)
        sections = node.get_child("sections")
        self.assertIsNotNone(sections)
        names = []
        for section in sections.children:
            block_states = section.get_child("block_states")
            if block_states is None:
                continue
            palette = block_states.get_child("palette")
            names.extend(str(entry.get_child("Name").value)
                         for entry in palette.children)
        self.assertIn("minecraft:gold_block", names)

    def test_pack_tab_builds_and_saves(self) -> None:
        from app.core.datapack_project import AuthoringPack, function_template
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="mcstudio-gui-pack-"))
        try:
            pack = AuthoringPack(root=root, namespace="guitest")
            pack.add_from_commands("main",
                                   function_template("main").splitlines())
            self.panel._set_pack(pack)
            self.assertEqual(self.panel.pack_tree.topLevelItemCount(), 5)
            self.assertIn("guitest", self.panel.pack_info.text())
            asset = pack.by_kind("function")[0]
            self.panel._load_pack_asset(asset)
            self.assertIn("main", self.panel.pack_asset_label.text())
            self.panel._on_pack_validate()
            self.assertIn("自检", self.panel.pack_report.toPlainText())
            self.panel._on_pack_save()
            self.assertTrue((root / "pack.mcmeta").is_file())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_pack_editor_edit_updates_asset(self) -> None:
        from app.core.datapack_project import AuthoringPack
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="mcstudio-gui-pack2-"))
        try:
            pack = AuthoringPack(root=root, namespace="edit")
            asset = pack.add_asset("function", "main", "say a")
            self.panel._set_pack(pack)
            self.panel._load_pack_asset(asset)
            self.panel.pack_editor.setPlainText("say changed")
            self.assertEqual(pack.find("function", "main").text, "say changed")
            self.assertTrue(pack.find("function", "main").dirty)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_pack_validate_reports_bad_json(self) -> None:
        from app.core.datapack_project import AuthoringPack
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="mcstudio-gui-pack3-"))
        try:
            pack = AuthoringPack(root=root, namespace="bad")
            pack.add_asset("advancement", "x", '{"a":')
            self.panel._set_pack(pack)
            self.panel._on_pack_validate()
            self.assertIn("JSON", self.panel.pack_report.toPlainText())
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestTypography(unittest.TestCase):
    """排版回归：所有页签必须用同一套 token，不能又写回魔法数字。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.panel = WorldPanel()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.panel.deleteLater()

    def test_tab_count(self) -> None:
        self.assertEqual(self.panel.tabs.count(), 15)

    def test_every_tab_uses_page_margins(self) -> None:
        expected = MARGINS["page"]
        for index in range(self.panel.tabs.count()):
            page = self.panel.tabs.widget(index)
            layout = page.layout()
            margins = layout.contentsMargins()
            actual = (margins.left(), margins.top(), margins.right(),
                      margins.bottom())
            self.assertEqual(actual, expected,
                             f"页签「{self.panel.tabs.tabText(index)}」边距不一致")

    def test_page_spacing_matches_token(self) -> None:
        for index in range(self.panel.tabs.count()):
            layout = self.panel.tabs.widget(index).layout()
            self.assertEqual(layout.spacing(), SPACING["sm"],
                             f"页签 {index} 间距不一致")

    def test_tables_share_row_height(self) -> None:
        tables = [
            self.panel.overview_table, self.panel.rules_table,
            self.panel.player_table, self.panel.player_inv_table,
            self.panel.entity_table, self.panel.container_table,
            self.panel.map_table, self.panel.region_table,
            self.panel.region_chunks, self.panel.datapack_table,
            self.panel.recipe_table, self.panel.backup_table,
            self.panel.health_table,
        ]
        for table in tables:
            self.assertFalse(table.verticalHeader().isVisible())
            self.assertTrue(table.alternatingRowColors())
            self.assertFalse(table.showGrid())
            self.assertTrue(table.horizontalHeader().stretchLastSection())

    def test_every_big_tab_has_a_filter_bar(self) -> None:
        for name in ("overview", "player", "inventory", "entity", "container",
                     "map", "region", "chunk", "datapack", "recipe", "backup",
                     "health"):
            bar = getattr(self.panel, f"{name}_filter", None)
            self.assertIsNotNone(bar, f"缺少 {name}_filter")
            self.assertIsInstance(bar, FilterBar)
            self.assertGreaterEqual(bar.table_count(), 1)

    def test_filter_shortcut_is_scoped(self) -> None:
        # 每个过滤条只在自己可见时响应 Ctrl+F，避免多个页签抢快捷键
        bar = self.panel.player_filter
        self.assertEqual(bar.edit.placeholderText(), "筛选玩家（Ctrl+F）")

    def test_no_hardcoded_muted_color(self) -> None:
        """源码里不应再出现写死的 #8B949E（改用 theme.colors()）。"""
        source = Path(TEST_ROOT, "app", "ui", "world_panel.py").read_text(
            encoding="utf-8")
        self.assertNotIn("#8B949E", source)
        for name in ("world_tabs_world.py", "world_tabs_data.py",
                     "world_common.py", "world_dialogs.py"):
            text = Path(TEST_ROOT, "app", "ui", name).read_text(
                encoding="utf-8")
            self.assertNotIn("#8B949E", text, f"{name} 里还有写死的灰色")

    def test_banner_reflects_state(self) -> None:
        self.panel.banner.update_state(name="测试", detail="细节",
                                       readonly=True, dirty=False)
        self.assertEqual(self.panel.banner.title.text(), "测试")
        self.assertIn("只读", self.panel.banner.state.text())
        self.panel.banner.update_state(name="测试", detail="细节",
                                       readonly=False, dirty=True)
        self.assertIn("未应用的修改", self.panel.banner.state.text())
        self.panel.banner.update_state(name="测试", detail="细节",
                                       readonly=False, dirty=False)
        self.assertIn("可编辑", self.panel.banner.state.text())


class TestTableHelpers(unittest.TestCase):
    def setUp(self) -> None:
        self.table = make_table(["A", "B"])
        for row in (("apple", "1"), ("banana", "2"), ("cherry", "3")):
            self.table.insertRow(self.table.rowCount())
            for column, value in enumerate(row):
                from PySide6.QtWidgets import QTableWidgetItem
                self.table.setItem(self.table.rowCount() - 1, column,
                                   QTableWidgetItem(value))

    def tearDown(self) -> None:
        self.table.deleteLater()

    def test_filter_matches_any_column(self) -> None:
        self.assertEqual(filter_tables([self.table], "ban"), 1)
        self.assertEqual(filter_tables([self.table], "2"), 1)
        # 三行都含字母 e（apple / banana? 不，banana 没有 e）→ 只有 apple 与 cherry
        self.assertEqual(filter_tables([self.table], "e"), 2)

    def test_filter_matches_every_row(self) -> None:
        # apple / banana 含 "a"，cherry 不含
        self.assertEqual(filter_tables([self.table], "a"), 2)
        self.assertEqual(filter_tables([self.table], "an"), 1)
        # 第二列是数字，"2" 只命中 banana 那一行
        self.assertEqual(filter_tables([self.table], "2"), 1)
        # 只含 "r" 的是 cherry
        self.assertEqual(filter_tables([self.table], "r"), 1)

    def test_filter_list_widget(self) -> None:
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import QListWidget, QListWidgetItem
        from app.ui.world_common import filter_list_widget
        widget = QListWidget()
        try:
            for text in ("Steve", "Alex", "Herobrine"):
                item = QListWidgetItem(text)
                # 与代码一致：搜索串放在 UserRole+1
                item.setData(_Qt.ItemDataRole.UserRole + 1,
                             f"{text} 11111111 存档路径")
                widget.addItem(item)
            self.assertEqual(filter_list_widget(widget, "ste"), 1)
            self.assertEqual(filter_list_widget(widget, "11111111"), 3)
            self.assertEqual(filter_list_widget(widget, "hero"), 1)
            self.assertEqual(filter_list_widget(widget, ""), 3)
            self.assertEqual(filter_list_widget(widget, "没有这个"), 0)
        finally:
            widget.deleteLater()

    def test_filter_case_insensitive(self) -> None:
        self.assertEqual(filter_tables([self.table], "APPLE"), 1)

    def test_filter_empty_shows_all(self) -> None:
        filter_tables([self.table], "apple")
        self.assertEqual(filter_tables([self.table], ""), 3)

    def test_export_csv_skips_hidden_rows(self) -> None:
        import tempfile
        filter_tables([self.table], "apple")
        path = Path(tempfile.mkdtemp(prefix="mcstudio-csv-")) / "out.csv"
        try:
            written = export_table_csv(self.table, path)
            self.assertEqual(written, 1)
            text = path.read_text(encoding="utf-8-sig")
            self.assertIn("A,B", text)
            self.assertIn("apple", text)
            self.assertNotIn("banana", text)
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)

    def test_filter_bar_counts(self) -> None:
        bar = FilterBar(table=self.table)
        try:
            self.assertIn("3 行", bar.count_label.text())
            bar.edit.setText("apple")
            self.assertIn("1/3", bar.count_label.text())
            bar.edit.clear()
            self.assertIn("3 行", bar.count_label.text())
        finally:
            bar.deleteLater()

    def test_section_box_header_button(self) -> None:
        box = SectionBox("标题", "说明")
        try:
            clicked: list[int] = []
            box.add_button("点我", lambda: clicked.append(1), tooltip="提示")
            self.assertEqual(box.hint_label.text(), "说明")
            self.assertTrue(callable(box.add_widget))
        finally:
            box.deleteLater()


class TestNewDialogs(GuiCase):
    """新增对话框：排版与取值都要能用。"""

    def test_region_write_dialog(self) -> None:
        dialog = RegionChunkWriteDialog(self.window, region_name="r.0.0.mca",
                                       chunk=(1, 2))
        try:
            self.assertEqual(dialog.chunk_x.value(), 1)
            self.assertEqual(dialog.chunk_z.value(), 2)
            self.assertFalse(dialog.btn_write.isEnabled())
            dialog.check_plan()                    # 没有目标 / NBT，应给出提示
            self.assertIn("请先", dialog.report.toPlainText())
        finally:
            dialog.deleteLater()

    def test_preset_dialog_lists_builtins(self) -> None:
        dialog = PresetDialog(self.window)
        try:
            self.assertGreaterEqual(dialog.table.rowCount(), 4)
            dialog.table.selectRow(0)
            dialog._on_use()
            self.assertIsNotNone(dialog.preset())
            self.assertIn("payload", dialog.preset())
        finally:
            dialog.deleteLater()

    def test_nlp_dialog_parses(self) -> None:
        dialog = NlpWorldDialog(self.window)
        try:
            dialog.input.setText("把时间设置为夜晚")
            dialog.parse()
            self.assertTrue(dialog.operations())
            self.assertTrue(dialog.btn_apply.isEnabled())
            dialog.input.setText("今天天气不错")
            dialog.parse()
            self.assertFalse(dialog.operations())
            self.assertFalse(dialog.btn_apply.isEnabled())
        finally:
            dialog.deleteLater()

    def test_blueprint_export_dialog_values(self) -> None:
        dialog = BlueprintExportDialog(self.window, name="塔",
                                       block_count=9, size=(2, 2, 2),
                                       has_entities=True)
        try:
            values = dialog.values()
            self.assertEqual(values["name"], "塔")
            self.assertEqual(values["format"], "both")
            self.assertTrue(values["include_nbt"])
            self.assertEqual(values["command_mode"], "setblock")
        finally:
            dialog.deleteLater()

    def test_cross_world_dialog_checks(self) -> None:
        dialog = CrossWorldCopyDialog(self.window)
        try:
            dialog._set_all(True)
            payload = dialog.payload()
            self.assertIn("inventory", payload["contents"])
            self.assertIn("maps", payload["contents"])
            dialog._set_all(False)
            self.assertEqual(dialog.payload()["contents"], [])
        finally:
            dialog.deleteLater()

    def test_scene_dialog_preview_updates(self) -> None:
        dialog = SceneDialog(self.window)
        try:
            dialog.spawn.setText("1 2 3")
            dialog._refresh_preview()
            self.assertIn("1 2 3", dialog.preview.text())
            dialog.permanent_day.setChecked(False)
            dialog._refresh_preview()
            self.assertNotIn("锁定白天", dialog.preview.text())
        finally:
            dialog.deleteLater()

    def test_grow_items_dialog_summary(self) -> None:
        dialog = GrowItemsDialog(self.window, default_item="minecraft:stone",
                                 container="EnderItems")
        try:
            dialog.count.setValue(5)
            dialog._refresh_summary()
            self.assertIn("EnderItems", dialog.summary.text())
            _item, count, mode, slot, container, _components = dialog.values()
            self.assertEqual(count, 5)
            self.assertEqual(container, "EnderItems")
            self.assertEqual(mode, "first_empty")
            self.assertIsNone(slot)
        finally:
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()