"""离屏 GUI 测试：主窗口可构建、面板齐全、关键交互不崩溃、条件字段显隐。"""
from __future__ import annotations

import os
import unittest

from tests import TEST_ROOT  # noqa: F401  （导入即设置环境变量）

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication            # noqa: E402

_app = QApplication.instance() or QApplication([])


class GuiCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from app.ui.main_window import MainWindow
        cls.window = MainWindow()
        # 不 show 的话 Qt 不会做布局，splitter 宽度会是占位值 100，
        # 布局类断言就失去意义了（离屏平台同样支持 show）。
        cls.window.resize(1440, 900)
        cls.window.show()
        QApplication.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        cls.window.deleteLater()


class TestMainWindow(GuiCase):
    def test_panels_registered(self) -> None:
        for name in ("summon", "give", "execute"):
            self.assertIn(name, self.window._panels)
        self.assertGreater(len(self.window._panels), 40)

    def test_command_tree_populated(self) -> None:
        self.assertGreater(self.window.command_tree.topLevelItemCount(), 1)

    def test_validate_switches_tab(self) -> None:
        for i in range(min(6, self.window.panel_stack.count())):
            self.window.panel_stack.setCurrentIndex(i)
            self.window._on_validate_current()
            self.assertGreater(self.window.validation_tree.topLevelItemCount(), 0)

    def test_parse_output_roundtrip(self) -> None:
        for cmd in ("summon minecraft:zombie ~ ~ ~ {IsBaby:0b}",
                    "give @p minecraft:stone 2",
                    "execute as @a run say hi"):
            with self.subTest(cmd=cmd):
                self.window.output_edit.setPlainText(cmd)
                self.window._current_command_type = cmd.split()[0]
                self.window._on_parse_output(force=True)
                self.assertTrue(self.window.output_edit.toPlainText().strip())

    def test_panel_shortcuts_stay_in_sync(self) -> None:
        for i in range(3):
            self.window._switch_panel_index(i)
            widget = self.window.panel_stack.currentWidget()
            self.assertIsNotNone(widget)
        # 越界索引应被忽略而不是抛异常
        self.window._switch_panel_index(999)

    def test_generate_without_support_reports(self) -> None:
        # 批量面板没有 current_command()，F5/Ctrl+R 不应抛异常
        self.window.panel_stack.setCurrentWidget(self.window.batch_panel)
        self.window._on_generate()

    def test_sync_buttons(self) -> None:
        self.window._on_sync_nbt_to_output()
        self.assertTrue(self.window.output_edit.toPlainText().strip())
        self.window._on_sync_target_to_output()
        self.assertIn("@", self.window.output_edit.toPlainText())

    def test_reference_panel_reload(self) -> None:
        self.window.reference_panel.reload()
        self.assertGreater(self.window.reference_panel.tree.topLevelItemCount(), 0)

    def test_output_edit_accepts_drops(self) -> None:
        self.assertTrue(self.window.output_edit.acceptDrops())

    def test_recent_commands_refresh(self) -> None:
        self.window.output_edit.setPlainText("say hello world")
        self.window._current_command_type = "say"
        self.window._flush_history()
        self.window._refresh_recent()       # 不应抛异常


class TestSchemaPanelVisibility(GuiCase):
    def _panel(self, name: str):
        from app.core import get_builder
        from app.ui.panels import SchemaPanel
        panel = SchemaPanel(get_builder(name)())
        panel.show()
        _app.processEvents()
        return panel

    def test_time_fields_follow_action(self) -> None:
        panel = self._panel("time")
        combo = panel._editors["action"][0]
        combo.setCurrentIndex(combo.findText("add"))
        _app.processEvents()
        self.assertFalse(panel._editors["value"][0].isVisible())
        self.assertTrue(panel._editors["amount"][0].isVisible())
        combo.setCurrentIndex(combo.findText("query"))
        _app.processEvents()
        self.assertTrue(panel._editors["value"][0].isVisible())
        self.assertFalse(panel._editors["amount"][0].isVisible())
        panel.deleteLater()

    def test_bossbar_fields_follow_action(self) -> None:
        panel = self._panel("bossbar")
        combo = panel._editors["action"][0]
        combo.setCurrentIndex(combo.findText("set"))
        _app.processEvents()
        self.assertTrue(panel._editors["field"][0].isVisible())
        self.assertFalse(panel._editors["name"][0].isVisible())
        panel.deleteLater()

    def test_current_command_matches_builder(self) -> None:
        panel = self._panel("weather")
        self.assertEqual(panel.current_command(), panel.builder.build())
        panel.deleteLater()


class TestPanelsBehaviour(GuiCase):
    def test_batch_panel_keeps_rows_per_command(self) -> None:
        bp = self.window.batch_panel
        combo = bp.command_combo
        if combo.count() < 2:
            self.skipTest("schema 命令不足")
        combo.setCurrentIndex(0)
        first = combo.itemData(0)
        bp.table.item(0, 0).setText("VALUE")
        # 切到另一个命令再切回来，已填内容应被保留
        combo.setCurrentIndex(1)
        combo.setCurrentIndex(combo.findData(first))
        self.assertEqual(bp._drafts.get(first)[0][bp._args[0].key], "VALUE")

    def test_batch_table_not_sorted(self) -> None:
        self.assertFalse(self.window.batch_panel.table.isSortingEnabled())

    def test_summon_nbt_toggle_syncs_builder(self) -> None:
        panel = self.window.summon_panel
        panel.nbt_enable.setChecked(True)
        _app.processEvents()
        self.assertTrue(panel.builder.nbt.strip(),
                        "勾选 NBT 后 builder 应同步到编辑器内容")
        panel.nbt_enable.setChecked(False)
        self.assertEqual(panel.builder.nbt, "")

    def test_workbench_accepts_fragment_mime(self) -> None:
        from app.ui.mime import MIME_COMMAND, MIME_FRAGMENT
        wp = self.window.workbench_panel
        self.assertTrue(wp._accepts(_mime(MIME_COMMAND)))
        self.assertTrue(wp._accepts(_mime(MIME_FRAGMENT)))
        self.assertIsNone(wp._drop_payload(_mime("application/x-other")))

    def test_batch_fill_sample_all_commands(self) -> None:
        """「填充示例」对每条命令都应生成至少一条合法命令。"""
        bp = self.window.batch_panel
        failures = []
        for i in range(bp.command_combo.count()):
            bp.command_combo.setCurrentIndex(i)
            bp._on_fill_sample()
            bp._refresh_preview_now()
            cmds = bp._build_all()
            if not cmds or any(c.startswith("# 错误") for c in cmds):
                failures.append(bp._current_command)
        self.assertEqual(failures, [], f"填充示例失败：{failures}")
        bp._select_first_command()

    def test_batch_clear_resets_preview(self) -> None:
        bp = self.window.batch_panel
        bp._select_first_command()
        bp._on_clear()
        bp._refresh_preview_now()
        self.assertEqual(bp.preview.toPlainText(), "")
        self.assertIn("填充示例", bp.count_label.text())

    def test_batch_no_argument_command_outputs(self) -> None:
        bp = self.window.batch_panel
        idx = bp.command_combo.findData("seed")
        if idx < 0:
            self.skipTest("没有 /seed")
        bp.command_combo.setCurrentIndex(idx)
        bp._refresh_preview_now()
        self.assertEqual(bp._build_all(), ["seed"])

    def test_batch_cell_hint_follows_column_kind(self) -> None:
        bp = self.window.batch_panel
        for name, expect in (("summon", "选实体…"), ("give", "选物品…"),
                             ("setblock", "选方块…")):
            idx = bp.command_combo.findData(name)
            if idx < 0:
                continue
            bp.command_combo.setCurrentIndex(idx)
            for col, spec in enumerate(bp._args):
                if spec.kind in ("entity", "item", "block"):
                    bp._update_cell_hint(bp.table.item(0, col))
                    self.assertEqual(bp.btn_pick.text(), expect)
                    break

    def test_reference_panel_search(self) -> None:
        ref = self.window.reference_panel
        ref.reload()
        self.assertGreater(ref.tree.topLevelItemCount(), 0)
        ref.search.setText("僵尸")
        ref._apply_filter()
        self.assertIn("条", ref.count_label.text())
        ref.search.clear()

    def test_fragment_to_command_for_each_kind(self) -> None:
        from app.storage import Fragment
        from app.ui.panels import _fragment_to_command
        cases = {
            "command": ({"text": "say hi"}, "say hi"),
            "nbt": ({"snbt": "{a:1}"}, "{a:1}"),
            "block": ({"id": "minecraft:stone"}, "minecraft:stone"),
            "component": ({"id": "minecraft:custom_name", "value": '"x"'},
                          'minecraft:custom_name="x"'),
        }
        for kind, (payload, expected) in cases.items():
            frag = Fragment(1, "n", kind, "c", payload, "", "")
            with self.subTest(kind=kind):
                self.assertIn(expected, _fragment_to_command(frag))

    def test_fragment_drop_payload_uses_kind_specific_keys(self) -> None:
        """回归：片段载荷用的是 text/snbt 键，拖放不能只认 command。"""
        from PySide6.QtCore import QMimeData
        import json as _json
        from app.ui.mime import MIME_FRAGMENT
        wp = self.window.workbench_panel
        for kind, payload, expected in (
                ("command", {"text": "say hi"}, "say hi"),
                ("nbt", {"snbt": "{a:1}"}, "{a:1}"),
                ("block", {"id": "minecraft:stone"}, "minecraft:stone")):
            mime = QMimeData()
            mime.setData(MIME_FRAGMENT, _json.dumps({
                "id": 1, "name": "n", "kind": kind, "category": "c",
                "payload": payload}).encode("utf-8"))
            with self.subTest(kind=kind):
                result = wp._drop_payload(mime)
                self.assertIsNotNone(result, f"{kind} 片段无法拖入")
                self.assertEqual(result["text"], expected)

    def test_placeholder_extraction(self) -> None:
        from app.core.datapack import extract_placeholders
        self.assertEqual(extract_placeholders("give @p minecraft:stone {count}"),
                         ["count"])
        self.assertEqual(extract_placeholders("say hi"), [])


class TestGivePanelGui(GuiCase):
    """/give 面板：物品选择、组件表格、处方、建议、草稿恢复。"""

    def setUp(self) -> None:
        self.panel = self.window.give_panel
        self._saved = self.panel.dump_state()
        self.panel.builder.target = "@p"
        self.panel.target_line.setText("@p")
        self.panel.builder.components = []
        self.panel.item_select.set_id("minecraft:stone")
        self.panel.builder.item = "minecraft:stone"
        self.panel.count_enable.setChecked(False)
        self.panel._refresh_table()
        self.panel._refresh_command()

    def tearDown(self) -> None:
        self.panel.restore_state(self._saved)

    def test_item_select_returns_id_not_display(self) -> None:
        """回归：下拉项显示「ID — 中文名」时不能把显示文本当成 ID。"""
        self.panel.item_select.set_id("minecraft:diamond_sword")
        index = self.panel.item_select.combo.findData("minecraft:diamond_sword")
        if index >= 0:
            self.panel.item_select._on_picked(index)
        self.assertEqual(self.panel.item_select.get_id(),
                         "minecraft:diamond_sword")
        self.assertIn("minecraft:diamond_sword",
                      self.panel.current_command())

    def test_typing_bare_id_is_accepted(self) -> None:
        self.panel._on_item_changed("diamond_sword")
        self.panel._on_item_settled()
        self.assertEqual(self.panel.current_command(),
                         "give @p minecraft:diamond_sword")

    def test_add_and_remove_component_updates_command(self) -> None:
        self.panel.builder.add_component("minecraft:custom_name", '"石"')
        self.panel._refresh_table()
        self.panel._refresh_command()
        self.assertIn('minecraft:custom_name="石"', self.panel.current_command())
        self.panel._delete_component(0)
        self.assertEqual(self.panel.comp_table.rowCount(), 0)
        self.assertNotIn("custom_name", self.panel.current_command())

    def test_move_and_duplicate_components(self) -> None:
        self.panel.builder.components = []
        self.panel.builder.add_component("minecraft:lore", '["a"]')
        self.panel.builder.add_component("minecraft:damage", "3")
        self.panel._move_component(0, 1)
        self.assertEqual(self.panel.builder.components[0].id,
                         "minecraft:damage")
        self.panel._duplicate_component(0)
        self.assertEqual(self.panel.comp_table.rowCount(), 3)

    def test_toggle_component_mode(self) -> None:
        self.panel.builder.add_component("minecraft:lore", '["a"]')
        self.panel._toggle_component_mode(0)
        self.assertIn("!minecraft:lore", self.panel.current_command())
        self.panel._toggle_component_mode(0)
        self.assertNotIn("!", self.panel.current_command())

    def test_count_presets_and_unstackable_hint(self) -> None:
        self.panel.item_select.set_id("minecraft:potion")
        self.panel.builder.item = "minecraft:potion"
        self.panel._on_count_preset(self.panel.count_combo.findData(64))
        self.assertEqual(self.panel.current_command(),
                         "give @p minecraft:potion 64")
        self.assertIn("不可堆叠", self.panel.hint_label.text())

    def test_preset_application(self) -> None:
        self.assertTrue(self.panel._presets, "缺少物品处方数据")
        self.panel._apply_preset(self.panel._presets[0])
        self.assertTrue(self.panel.builder.components)
        self.assertIn("minecraft:enchantments", self.panel.current_command())

    def test_suggestions_populated_and_applied(self) -> None:
        self.panel.item_select.set_id("minecraft:diamond_sword")
        self.panel.builder.item = "minecraft:diamond_sword"
        self.panel._refresh_suggestions()
        self.assertGreater(self.panel.suggest.count(), 2)
        self.panel._on_apply_suggestions()
        self.assertTrue(self.panel.builder.components)

    def test_state_roundtrip_through_panel(self) -> None:
        self.panel.builder.add_component("minecraft:unbreakable", "1b")
        state = self.panel.dump_state()
        self.panel._on_clear_components()
        self.assertTrue(self.panel.restore_state(state))
        self.assertIn("minecraft:unbreakable=1b", self.panel.current_command())

    def test_main_window_restores_panel_state(self) -> None:
        self.window._apply_panel_state({"give": {
            "target": "@a", "item": "minecraft:stick", "use_count": True,
            "count": 5,
            "components": [{"id": "minecraft:custom_name",
                            "mode": "set", "value": '"棍"'}]}})
        self.assertEqual(
            self.window.give_panel.current_command(),
            'give @a minecraft:stick 5 [minecraft:custom_name="棍"]')

    def test_enchantment_dialog_roundtrip(self) -> None:
        from app.ui.widgets import EnchantmentsEditDialog
        dlg = EnchantmentsEditDialog(
            '{"levels":{"minecraft:sharpness":5,"minecraft:mending":1}}')
        self.assertEqual(dlg.table.rowCount(), 2)
        value = dlg.value()
        self.assertIn('"levels"', value)
        self.assertIn('"minecraft:sharpness":5', value)
        dlg.deleteLater()

    def test_component_picker_dialog_selects(self) -> None:
        from app.ui.widgets import ComponentPickerDialog
        dlg = ComponentPickerDialog(None, item="minecraft:stone")
        node = dlg.tree.topLevelItem(0)
        child = node.child(0)
        dlg._on_double_clicked(child)
        self.assertEqual(len(dlg.selected_ids()), 1)
        dlg.deleteLater()

    def test_value_edit_dialog_validates(self) -> None:
        from app.ui.widgets import ValueEditDialog
        dlg = ValueEditDialog(
            "t", "[1,2",
            validator=lambda text: [("error", "语法错误")])
        self.assertIn("语法错误", dlg.message.text())
        dlg.deleteLater()

    def test_give_panel_reload_data(self) -> None:
        self.panel.reload_data()
        self.assertTrue(self.panel._comp_defs)
        self.assertIn("minecraft:custom_name", self.panel._comp_defs)

    def test_component_table_has_three_columns(self) -> None:
        """回归：组件表列太多会在窄面板里把内容挤没。"""
        self.assertEqual(self.panel.comp_table.columnCount(), 3)
        widths = [self.panel.comp_table.columnWidth(c)
                  for c in range(self.panel.comp_table.columnCount())]
        self.assertGreaterEqual(widths[0], 150, "组件列太窄，看不到中文名")

    def test_hint_line_is_single_line_with_tooltip(self) -> None:
        self.panel._on_item_changed("stone")
        self.panel._on_item_settled()
        self.assertFalse(self.panel.hint_label.wordWrap())
        self.assertTrue(self.panel.hint_label.toolTip())


class TestWindowLayout(GuiCase):
    """布局：面板记忆、折叠按钮、默认布局还原。"""

    def test_splitter_sizes_are_sane(self) -> None:
        sizes = self.window.main_splitter.sizes()
        self.assertEqual(len(sizes), 3)
        self.assertGreater(sizes[0], 0)
        self.assertGreater(sizes[1], 0)
        self.assertGreater(sizes[2], 0)

    def test_layout_sizes_persist_and_restore(self) -> None:
        self.window.main_splitter.setSizes([300, 800, 360])
        self.window._bottom_height = 0
        self.window._save_layout_sizes()
        saved = self.window._draft_store.load("layout")
        self.assertEqual(saved["bottom"], 0)
        self.assertEqual(len(saved["sizes"]), 3)
        self.assertAlmostEqual(sum(saved["ratios"]), 1.0, places=2)
        # 新建窗口应读回同样的尺寸
        from app.ui.main_window import MainWindow
        other = MainWindow()
        other.resize(self.window.width(), self.window.height())
        other.show()
        QApplication.processEvents()
        self.assertEqual(other.main_splitter.sizes(),
                         self.window.main_splitter.sizes())
        other.close()

    def test_toggle_tree_and_output(self) -> None:
        self.window._toggle_tree_area()
        self.assertEqual(self.window.main_splitter.sizes()[0], 0)
        self.window._toggle_tree_area()
        self.assertGreater(self.window.main_splitter.sizes()[0], 0)

        self.window._toggle_output_area()
        self.assertEqual(self.window.main_splitter.sizes()[2], 0)
        self.window._toggle_output_area()
        self.assertGreater(self.window.main_splitter.sizes()[2], 0)

    def test_toggle_bottom_area(self) -> None:
        self.window._bottom_height = 150
        self.window._toggle_bottom_area()
        self.assertEqual(self.window.bottom_tabs.maximumHeight(), 0)
        self.window._toggle_bottom_area()
        self.assertGreater(self.window.bottom_tabs.maximumHeight(), 0)

    def test_reset_layout_restores_defaults(self) -> None:
        self.window._apply_proportional_layout()
        QApplication.processEvents()
        before = self.window.main_splitter.sizes()
        self.window.main_splitter.setSizes([40, 900, 60])
        self.window._reset_layout()
        sizes = self.window.main_splitter.sizes()
        self.assertAlmostEqual(sizes[0], before[0], delta=40)
        self.assertAlmostEqual(sizes[2], before[2], delta=40)
        self.assertEqual(self.window._bottom_height, 150)

    def test_toggle_output_keeps_sensible_width(self) -> None:
        """回归：反复折叠/展开不应把输出区挤成 0 宽。"""
        self.window._apply_proportional_layout()
        QApplication.processEvents()
        for _ in range(3):
            self.window._toggle_output_area()
            self.window._toggle_output_area()
        self.assertGreater(self.window.main_splitter.sizes()[2], 200)


def _mime(kind: str):
    from PySide6.QtCore import QMimeData
    m = QMimeData()
    m.setData(kind, b"{}")
    return m


if __name__ == "__main__":
    unittest.main()
