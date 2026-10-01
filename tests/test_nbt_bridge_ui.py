"""图形化存档物品浏览 / 设置对话框的测试。"""
from __future__ import annotations

import os
import unittest

import tests  # noqa: F401

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.snbt import SnbtParser                                  # noqa: E402

PLAYER_NBT = """
{Data:{Player:{
  Inventory:[
    {Slot:0b,id:"minecraft:diamond_sword",count:1,components:{
        "minecraft:custom_name":'{"text":"A"}',
        "minecraft:enchantments":{levels:{"minecraft:sharpness":5}}}},
    {Slot:1b,id:"minecraft:stone",count:64},
    {Slot:2b,id:"minecraft:stone",count:32}
  ],
  EnderItems:[{Slot:0b,id:"minecraft:elytra",count:1}],
  Health:20.0f
}}}
"""


class GuiCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])


class TestItemOverview(GuiCase):
    def make(self, merge: bool = True):
        from app.ui.dialogs import ItemOverviewDialog
        dialog = ItemOverviewDialog(SnbtParser(PLAYER_NBT).parse(),
                                    source="level.dat")
        dialog.merge_check.setChecked(merge)
        return dialog

    def _row_of(self, dialog, item_id: str) -> int:
        for row in range(dialog.table.rowCount()):
            if dialog.table.item(row, 2).text() == item_id:
                return row
        raise AssertionError(f"没有 {item_id} 这一行")

    def test_lists_merged_items(self) -> None:
        dialog = self.make(merge=True)
        try:
            # 两组石头合并成一行：64 + 32
            ids = [dialog.table.item(r, 2).text()
                   for r in range(dialog.table.rowCount())]
            self.assertEqual(ids.count("minecraft:stone"), 1)
            self.assertEqual(dialog.table.rowCount(), 3)
            row = self._row_of(dialog, "minecraft:stone")
            self.assertEqual(dialog.table.item(row, 3).text(), "96")
            self.assertIn("3 行", dialog.summary_label.text())
            self.assertIn("共 98 个", dialog.summary_label.text())
        finally:
            dialog.deleteLater()

    def test_unmerged_keeps_slots(self) -> None:
        dialog = self.make(merge=False)
        try:
            self.assertEqual(dialog.table.rowCount(), 4)
            slots = [dialog.table.item(r, 0).text()
                     for r in range(dialog.table.rowCount())]
            self.assertIn("1", slots)
            self.assertIn("2", slots)
        finally:
            dialog.deleteLater()

    def test_container_selector(self) -> None:
        dialog = self.make()
        try:
            labels = [dialog.source_combo.itemText(i)
                      for i in range(dialog.source_combo.count())]
            self.assertIn("全部容器（按出现顺序）", labels)
            self.assertTrue(any("Inventory" in x for x in labels))
            self.assertTrue(any("EnderItems" in x for x in labels))
            index = next(i for i in range(dialog.source_combo.count())
                         if "EnderItems" in dialog.source_combo.itemText(i))
            dialog.source_combo.setCurrentIndex(index)
            self.assertEqual(dialog.table.rowCount(), 1)
            self.assertEqual(dialog.table.item(0, 2).text(), "minecraft:elytra")
        finally:
            dialog.deleteLater()

    def test_selection_detail_and_commands(self) -> None:
        dialog = self.make()
        try:
            dialog.table.selectRow(self._row_of(dialog,
                                                "minecraft:diamond_sword"))
            detail = dialog.detail.toPlainText()
            self.assertIn("give", detail)
            self.assertIn("SNBT", detail)
            items = dialog.selected_items()
            self.assertEqual(len(items), 1)
            from app.core.nbt_bridge import stack_to_command
            self.assertEqual(stack_to_command(items[0], "@p"),
                             "give @p minecraft:diamond_sword"
                             '[custom_name={"text":"A"},'
                             "enchantments={levels:{"
                             '"minecraft:sharpness":5}}] 1')
        finally:
            dialog.deleteLater()

    def test_give_request_signal(self) -> None:
        dialog = self.make()
        seen: list[tuple] = []
        dialog.giveRequested.connect(
            lambda stack, target: seen.append((stack, target)))
        try:
            dialog.target_combo.setCurrentText("@a")
            dialog.table.selectRow(self._row_of(dialog,
                                                "minecraft:diamond_sword"))
            dialog._send_to_give()
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0][1], "@a")
            self.assertEqual(seen[0][0].clean_id(), "minecraft:diamond_sword")
        finally:
            dialog.deleteLater()

    def test_output_all(self) -> None:
        dialog = self.make()
        try:
            dialog._emit_all_to_output()
            self.assertEqual(len(dialog.outputLines), 3)
            self.assertTrue(all(line.startswith("give @p ")
                                for line in dialog.outputLines))
        finally:
            dialog.deleteLater()


class TestSettingsDialog(GuiCase):
    def make(self):
        import tempfile
        from pathlib import Path
        import app.core.settings as settings
        self.settings = settings
        self.tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-setdlg-"))
        settings._path_override = self.tmp / "settings.json"
        settings._cache = None
        from app.ui.dialogs import SettingsDialog
        return SettingsDialog()

    def tearDown(self) -> None:
        if hasattr(self, "settings"):
            self.settings._path_override = None
            self.settings._cache = None

    def test_pages_and_term_tables(self) -> None:
        dialog = self.make()
        try:
            titles = [dialog.tabs.tabText(i)
                      for i in range(dialog.tabs.count())]
            self.assertEqual(titles,
                             ["专有名词翻译", "NBT 显示", "数据目录"])
            self.assertEqual(set(dialog.term_tables),
                             {"nbt_types", "categories", "words"})
            table = dialog.term_tables["nbt_types"].table
            self.assertEqual(table.rowCount(),
                             len(self.settings.DEFAULT_NBT_TYPES))
        finally:
            dialog.deleteLater()

    def test_edit_translation_applies_immediately(self) -> None:
        dialog = self.make()
        try:
            table = dialog.term_tables["nbt_types"]
            row = next(r for r in range(table.table.rowCount())
                       if table.table.item(r, 0).text() == "String")
            table.table.item(row, 1).setText("字串")
            self.assertEqual(self.settings.translate_nbt_type("String"),
                             "字串")
            # 清空表示回到默认
            table.table.item(row, 1).setText("")
            self.assertEqual(self.settings.translate_nbt_type("String"),
                             "字符串")
        finally:
            dialog.deleteLater()

    def test_filter_rows(self) -> None:
        dialog = self.make()
        try:
            table = dialog.term_tables["nbt_types"]
            table.filter_edit.setText("数组")
            shown = [r for r in range(table.table.rowCount())
                     if not table.table.isRowHidden(r)]
            self.assertTrue(shown)
            names = {table.table.item(r, 0).text() for r in shown}
            self.assertEqual(names, {"ByteArray", "IntArray", "LongArray"})
        finally:
            dialog.deleteLater()

    def test_scope_switch_and_display_options(self) -> None:
        dialog = self.make()
        try:
            dialog.scope_combo.setCurrentIndex(
                dialog.scope_combo.findData("none"))
            self.assertEqual(self.settings.translate_nbt_type("String"),
                             "String")
            dialog.enable_check.setChecked(False)
            self.assertFalse(self.settings.use_translations())
            dialog.icons_check.setChecked(False)
            self.assertFalse(self.settings.nbt_show_icons())
        finally:
            dialog.deleteLater()


class TestEditorIntegration(GuiCase):
    """NBT 编辑器 ↔ /give、/summon 面板的联动。"""

    def setUp(self) -> None:
        """每个用例都用自己的设置文件，避免相互影响。"""
        import tempfile
        from pathlib import Path
        import app.core.settings as settings
        self.settings = settings
        self.tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-editint-"))
        self._old_path = settings._path_override
        self._old_cache = settings._cache
        settings._path_override = self.tmp / "settings.json"
        settings._cache = None

    def tearDown(self) -> None:
        self.settings._path_override = self._old_path
        self.settings._cache = self._old_cache

    def make_editor(self):
        from app.ui.widgets import NbtTreeEditor
        editor = NbtTreeEditor()
        editor.resize(1000, 700)
        editor.set_root(SnbtParser(PLAYER_NBT).parse())
        return editor

    def test_item_rows_show_chinese_names(self) -> None:
        editor = self.make_editor()
        try:
            self.assertTrue(editor._item_paths)
            row = editor.item_by_path("Data.Player.Inventory[1]")
            self.assertIsNotNone(row)
            self.assertIsNotNone(row.stack)
            self.assertIn("石头", row.text(2))       # 取值列显示中文名 + 数量
            self.assertIn("×64", row.text(2))
        finally:
            editor.deleteLater()

    def test_item_card_and_give_signal(self) -> None:
        editor = self.make_editor()
        seen: list = []
        editor.giveRequested.connect(seen.append)
        try:
            self.assertTrue(
                editor.select_by_path("Data.Player.Inventory[0]"))
            self.assertEqual(editor.detail_stack.currentWidget(),
                             editor.detail_item_page)
            self.assertIn("A ×1", editor.item_name.text())
            self.assertIn("锋利", editor.item_components.toPlainText())
            editor._send_item_to_give()
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0].clean_id(), "minecraft:diamond_sword")
        finally:
            editor.deleteLater()

    def test_item_copy_actions(self) -> None:
        from PySide6.QtWidgets import QApplication
        editor = self.make_editor()
        try:
            editor.select_by_path("Data.Player.Inventory[1]")
            editor.copy_item_command()
            self.assertEqual(QApplication.clipboard().text(),
                             "give @p minecraft:stone 64")
            editor.copy_item_snbt()
            self.assertIn('id:"minecraft:stone"',
                          QApplication.clipboard().text())
        finally:
            editor.deleteLater()

    def test_type_column_is_translated(self) -> None:
        editor = self.make_editor()
        try:
            self.assertTrue(editor.select_by_path("Data.Player.Inventory"))
            self.assertEqual(editor.tree.currentItem().text(1), "列表")
            self.assertTrue(editor.select_by_path("Data.Player.Health"))
            self.assertEqual(editor.tree.currentItem().text(1), "单精度浮点")
        finally:
            editor.deleteLater()

    def test_settings_change_refreshes_tree(self) -> None:
        editor = self.make_editor()
        try:
            editor.select_by_path("Data.Player.Health")
            self.assertEqual(editor.tree.currentItem().text(1), "单精度浮点")
            self.settings.set_value("terms.scope", "none")
            self.assertEqual(editor.tree.currentItem().text(1), "Float")
        finally:
            editor.deleteLater()

    def test_entity_card_and_summon_signal(self) -> None:
        from app.ui.widgets import NbtTreeEditor
        editor = NbtTreeEditor()
        editor.resize(1000, 700)
        editor.set_root(SnbtParser(
            '{Entities:[{id:"minecraft:zombie",Health:20.0f,'
            'CustomName:\'{"text":"僵尸王"}\'}]}').parse())
        seen: list = []
        editor.summonRequested.connect(seen.append)
        try:
            self.assertTrue(editor.select_by_path("Entities[0]"))
            self.assertEqual(editor.detail_stack.currentWidget(),
                             editor.detail_entity_page)
            self.assertIn("僵尸王", editor.entity_title.text())
            self.assertIn("Health", editor.entity_nbt.toPlainText())
            editor._send_entity_to_summon()
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0].entity_id, "minecraft:zombie")
            editor.copy_entity_command()
            from PySide6.QtWidgets import QApplication
            self.assertTrue(QApplication.clipboard().text().startswith(
                "summon minecraft:zombie"))
        finally:
            editor.deleteLater()

    def test_overview_button_enabled_only_with_items(self) -> None:
        from app.ui.widgets import NbtTreeEditor
        editor = NbtTreeEditor()
        try:
            editor.set_root(SnbtParser(PLAYER_NBT).parse())
            self.assertTrue(editor.btn_overview.isEnabled())
            editor.set_root(SnbtParser('{A:1}').parse())
            self.assertFalse(editor.btn_overview.isEnabled())
        finally:
            editor.deleteLater()


class TestPanelHandoff(GuiCase):
    """面板接收存档数据。"""

    def test_give_panel_receives_item(self) -> None:
        from app.ui.panels import GivePanel
        from app.core.nbt_bridge import parse_item
        panel = GivePanel()
        stack = parse_item(SnbtParser(
            '{id:"minecraft:diamond_sword",count:2,components:{'
            '"minecraft:enchantments":{levels:{"minecraft:sharpness":5}}}}'
        ).parse())
        try:
            self.assertTrue(panel.receive_item(stack, "@a"))
            self.assertEqual(panel.builder.item, "minecraft:diamond_sword")
            self.assertEqual(panel.builder.count, 2)
            self.assertEqual(panel.builder.target, "@a")
            self.assertEqual(len(panel.builder.components), 1)
            command = panel.current_command()
            self.assertIn("give @a minecraft:diamond_sword", command)
            self.assertIn("sharpness", command)
        finally:
            panel.deleteLater()

    def test_summon_panel_receives_entity(self) -> None:
        from app.ui.panels import SummonPanel
        from app.core.nbt_bridge import parse_entity
        panel = SummonPanel()
        entity = parse_entity(SnbtParser(
            '{id:"minecraft:zombie",Health:20.0f,IsBaby:1b}').parse())
        try:
            self.assertTrue(panel.receive_entity(entity, "10 64 10"))
            command = panel.current_command()
            self.assertTrue(command.startswith(
                "summon minecraft:zombie 10 64 10 {"))
            self.assertIn("IsBaby:1b", command)
            self.assertIn("Health:20.0f", command)
        finally:
            panel.deleteLater()


class TestStructureDialog(GuiCase):
    """文件结构总览：确认标签是否真的在文件里。"""

    def _make_doc(self, snbt: str, root_name: str = ""):
        from pathlib import Path
        import tempfile
        from app.core.nbt_binary import NbtDocument, save_nbt_file
        tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-struct-"))
        path = tmp / "level.dat"
        save_nbt_file(SnbtParser(snbt).parse(), path, compress="gzip",
                      root_name=root_name)
        return NbtDocument.load(path)

    def test_lists_root_children(self) -> None:
        from app.ui.dialogs import StructureDialog
        doc = self._make_doc(
            '{Data:{version:19133,LevelName:"w",Player:{Health:20.0f}},'
            'fml:{LoadingModList:[{ModId:"create",ModSize:1}]}}')
        dialog = StructureDialog(doc)
        try:
            rows = [dialog.tree.topLevelItem(i).text(0)
                    for i in range(dialog.tree.topLevelItemCount())]
            self.assertEqual(rows, ["Data", "fml"])
            # Data 的条目数写在取值列
            self.assertEqual(dialog.tree.topLevelItem(0).text(2), "3 项")
            self.assertIn("直接子标签：<b>2</b>", dialog.summary.text())
            self.assertIn("根标签名：<code>（空）</code>",
                          dialog.summary.text())
        finally:
            dialog.deleteLater()

    def test_structure_lines(self) -> None:
        from app.ui.dialogs.structure_dialog import structure_lines
        root = SnbtParser('{Data:{LevelName:"w"},fml:{A:1}}').parse()
        lines = structure_lines(root)
        self.assertIn("Data  <Compound（1 项）>", lines)
        self.assertTrue(any("<String = w>" in line for line in lines))

    def test_mod_list_structure_visible(self) -> None:
        """模组清单里根下只有 LoadingModList —— 说明 Data 真的不在文件里。"""
        from app.ui.dialogs.structure_dialog import structure_lines
        root = SnbtParser(
            '{LoadingModList:[{ModId:"create",ModSize:1},'
            '{ModId:"jei",ModSize:2}]}').parse()
        lines = structure_lines(root)
        self.assertIn("LoadingModList  <List（2 项）>", lines)
        self.assertFalse(any(line.strip().startswith("Data")
                             for line in lines))

    def test_world_save_root_name_empty(self) -> None:
        from app.ui.dialogs import StructureDialog
        doc = self._make_doc('{Data:{LevelName:"w"}}', root_name="")
        dialog = StructureDialog(doc)
        try:
            self.assertIn("（空）", dialog.summary.text())
        finally:
            dialog.deleteLater()

    def test_fml_root_name_shown(self) -> None:
        from app.ui.dialogs import StructureDialog
        doc = self._make_doc('{LoadingModList:[]}', root_name="fml")
        dialog = StructureDialog(doc)
        try:
            self.assertIn("<code>fml</code>", dialog.summary.text())
            self.assertEqual(dialog.tree.topLevelItem(0).text(0),
                             "LoadingModList")
        finally:
            dialog.deleteLater()


class TestInspectTool(unittest.TestCase):
    """CLI 诊断脚本：把文件结构打印出来。"""

    def test_inspect_runs_on_sample(self) -> None:
        import subprocess
        import sys
        from pathlib import Path
        import tempfile
        from app.core.nbt_binary import save_nbt_file
        root = Path(__file__).resolve().parents[1]
        tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-inspect-"))
        path = tmp / "t.dat"
        save_nbt_file(SnbtParser('{Data:{LevelName:"w"},fml:{}}').parse(),
                      path, compress="gzip", root_name="")
        result = subprocess.run(
            [sys.executable, str(root / "tools" / "inspect_nbt.py"), str(path)],
            capture_output=True, text=True, timeout=90, encoding="utf-8",
            errors="replace")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("根标签名：''", result.stdout)
        self.assertIn("Data  <Compound", result.stdout)
        self.assertIn("fml  <Compound", result.stdout)


if __name__ == "__main__":
    unittest.main()
