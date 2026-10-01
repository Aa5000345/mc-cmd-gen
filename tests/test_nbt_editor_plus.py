"""NBT 编辑器（对标 NBTExplorer）增强部分的测试。

覆盖：按类型就地编辑与范围折算、追加元素对话框、筛选与命中高亮、
路径解析（含带引号的键名）、容器类型约束、详情面板、多选删除、
复制为 /data 命令、字节视图与折叠展开、文件编辑器的重新载入
与「最近打开」。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401  （导入即设置环境变量）

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.snbt import SnbtKind, SnbtNode, SnbtParser, serialize  # noqa: E402
from app.ui.widgets.nbt_dialogs import (                             # noqa: E402
    ValueEditDialog, create_inline_editor, read_inline_editor,
)
from app.ui.widgets.nbt_tags import (                                # noqa: E402
    ValueParseError, array_hexdump, array_preview, can_hold,
    container_summary, element_kind, filter_nodes, find_nodes,
    format_element, format_value, is_descendant, match_segments,
    parse_data_path, parse_element, path_segments,
)


class TestTypeRules(unittest.TestCase):
    """容器类型约束与元素类型。"""

    def setUp(self) -> None:
        self.root = SnbtParser(
            '{L:[1,2],Empty:[],Nums:[I;1,2],S:"x",B:1b,C:{}}').parse()

    def test_element_kind(self) -> None:
        self.assertIs(element_kind(self.root.get_child("L")), SnbtKind.INT)
        self.assertIsNone(element_kind(self.root.get_child("Empty")))
        self.assertIs(element_kind(self.root.get_child("Nums")), SnbtKind.INT)
        self.assertIsNone(element_kind(self.root.get_child("C")))

    def test_can_hold(self) -> None:
        lst = self.root.get_child("L")
        empty = self.root.get_child("Empty")
        nums = self.root.get_child("Nums")
        string = self.root.get_child("S")
        self.assertTrue(can_hold(lst, SnbtNode.new_scalar(SnbtKind.INT, 5)))
        self.assertFalse(can_hold(lst, SnbtNode.new_scalar(SnbtKind.STRING, "a")))
        # 空 List 可以接受任意类型（放进第一个元素就定死类型）
        self.assertTrue(can_hold(empty, SnbtNode.new_scalar(SnbtKind.STRING, "a")))
        # 数组只接受对应整数
        self.assertTrue(can_hold(nums, SnbtNode.new_scalar(SnbtKind.INT, 1)))
        self.assertFalse(can_hold(nums, SnbtNode.new_scalar(SnbtKind.LONG, 1)))
        # 标量不能当容器
        self.assertFalse(can_hold(string, SnbtNode.new_scalar(SnbtKind.INT, 1)))

    def test_container_summary(self) -> None:
        self.assertIn("2 项", container_summary(self.root.get_child("Nums")))
        self.assertIn("元素类型 Int", container_summary(self.root.get_child("L")))
        self.assertIn("元素类型 任意", container_summary(
            self.root.get_child("Empty")))

    def test_is_descendant(self) -> None:
        a = SnbtParser("{A:{B:{}}}").parse().get_child("A")
        b = a.get_child("B")
        self.assertTrue(is_descendant(b, a))
        self.assertTrue(is_descendant(a, a))
        self.assertFalse(is_descendant(a, b))
        # 内容相同但不是同一个对象时不应误判
        other = SnbtParser("{B:{}}").parse()
        self.assertFalse(is_descendant(other.get_child("B"), a))


class TestPathParsing(unittest.TestCase):
    def test_parse_data_path(self) -> None:
        self.assertEqual(parse_data_path("Data.Player.id"),
                         [("Data", []), ("Player", []), ("id", [])])
        self.assertEqual(parse_data_path("Data.Inventory[0].id"),
                         [("Data", []), ("Inventory", [0]), ("id", [])])
        self.assertEqual(parse_data_path("Nums[1]"), [("Nums", [1])])
        self.assertEqual(parse_data_path('"my.key".x'),
                         [("my.key", []), ("x", [])])
        self.assertEqual(parse_data_path(""), [])

    def test_path_segments_quotes_odd_keys(self) -> None:
        root = SnbtParser('{"my.key":{inner:1}}').parse()
        inner = root.get_child("my.key").get_child("inner")
        self.assertEqual(path_segments(inner, root), '"my.key".inner')


class TestContainersHelpers(unittest.TestCase):
    def test_array_preview_and_hexdump(self) -> None:
        node = SnbtParser("[I;1,2,3]").parse()
        self.assertIn("1, 2, 3", array_preview(node))
        self.assertIn("0x1", array_preview(node, hex_mode=True))
        dump = array_hexdump(node)
        self.assertIn("00000000", dump)
        self.assertIn("00 00 00 01", dump)

    def test_byte_array_preview_truncates(self) -> None:
        node = SnbtParser("[B;" + ",".join(str(i) for i in range(100)) + "]").parse()
        text = array_preview(node, max_items=12)
        self.assertIn("已省略", text)

    def test_element_roundtrip(self) -> None:
        self.assertEqual(parse_element("0xFF", SnbtKind.BYTE), 255)
        self.assertEqual(parse_element("hi", SnbtKind.STRING), "hi")
        self.assertTrue(parse_element("1b", SnbtKind.BOOL))
        node = SnbtNode.new_scalar(SnbtKind.LONG, 7)
        self.assertEqual(format_element(node), "7L")
        with self.assertRaises(ValueParseError):
            parse_element("abc", SnbtKind.INT)


class TestCompoundPreview(unittest.TestCase):
    """Compound / 列表在「值」列里的可读预览（`{ 2 项 }` 太含糊）。"""

    def test_compound_shows_fields(self) -> None:
        node = SnbtParser('{ModId:"neoforge",ModSize:0}').parse()
        self.assertEqual(format_value(node),
                         "{ModId: neoforge, ModSize: 0}")
        # 搜索路径用简短占位，避免把字段名当成取值
        self.assertEqual(format_value(node, preview=False), "{ 2 项 }")

    def test_priority_keys_come_first(self) -> None:
        """优先字段（id / ModId / type …）排在前面，其余保持原顺序。"""
        node = SnbtParser('{Zeta:1,id:"minecraft:stone",Alpha:2}').parse()
        text = format_value(node)
        self.assertTrue(text.startswith("{id:"), text)
        self.assertLess(text.index("Zeta:"), text.index("Alpha:"))
        # ModId 是优先字段，会排到前面
        mod = SnbtParser('{ModSize:1,ModId:"create"}').parse()
        text = format_value(mod)
        self.assertTrue(text.startswith("{ModId:"), text)

    def test_list_of_compounds_is_readable(self) -> None:
        node = SnbtParser(
            '[{ModId:"create",ModSize:1},{ModId:"jei",ModSize:2}]').parse()
        self.assertEqual(
            format_value(node),
            "[ {ModId: create, ModSize: 1}, {ModId: jei, ModSize: 2} ]")

    def test_large_containers_stay_compact(self) -> None:
        node = SnbtParser("{" + ",".join(f"K{i}:{i}" for i in range(40))
                          + "}").parse()
        self.assertEqual(format_value(node), "{ 40 项 }")
        big_list = SnbtParser(
            "[" + ",".join(str(i) for i in range(50)) + "]").parse()
        text = format_value(big_list)
        self.assertTrue(text.endswith("（50 项）"), text)
        self.assertIn("…", text)

    def test_strings_quoted_in_compound_preview(self) -> None:
        node = SnbtParser('{name:"hello world",flag:1b}').parse()
        self.assertIn('name: "hello world"', format_value(node))

    def test_search_ignores_preview_field_names(self) -> None:
        """搜 ModSize 只应命中那个标签本身，而不是它的父 Compound。"""
        root = SnbtParser(
            '{LoadingModList:[{ModId:"create",ModSize:1}]}').parse()
        matches = find_nodes(root, "ModId", in_keys=True, in_values=False)
        self.assertEqual([m.key for m in matches], ["ModId"])
        by_key = find_nodes(root, "ModSize", in_keys=True, in_values=False)
        self.assertEqual([m.key for m in by_key], ["ModSize"])
        # 父 Compound 的「取值」是简短占位，不会因为字段名而误命中
        parent = root.get_child("LoadingModList").children[0]
        self.assertEqual(format_value(parent, preview=False), "{ 2 项 }")
        self.assertNotIn("ModSize", format_value(parent, preview=False))


class TestStructureHint(unittest.TestCase):
    """已知结构（模组清单 / 世界存档）给一句说明。"""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_mod_list_hint(self) -> None:
        from app.ui.widgets.nbt_tree import _structure_hint
        root = SnbtParser('{LoadingModList:[],ModFileList:[]}').parse()
        hint = _structure_hint(root, "fml")
        self.assertIn("模组清单", hint)
        self.assertIn("不是世界存档", hint)

    def test_world_save_hint(self) -> None:
        from app.ui.widgets.nbt_tree import _structure_hint
        root = SnbtParser('{Data:{LevelName:"w",Version:19133}}').parse()
        hint = _structure_hint(root, "")
        self.assertIn("世界存档", hint)
        self.assertIn("Inventory", hint)

    def test_unknown_structure_has_no_hint(self) -> None:
        from app.ui.widgets.nbt_tree import _structure_hint
        self.assertEqual(_structure_hint(SnbtParser('{A:1}').parse()), "")


class TestFiltering(unittest.TestCase):
    def setUp(self) -> None:
        self.root = SnbtParser(
            '{Data:{LevelName:"world",SpawnX:0},Sub:{Name:"nether"},'
            'Nums:[I;1,2,3]}').parse()

    def test_filter_keeps_ancestors(self) -> None:
        keep = filter_nodes(self.root, "LevelName", fields=("key",))
        self.assertIn(id(self.root.get_child("Data").get_child("LevelName")), keep)
        self.assertIn(id(self.root.get_child("Data")), keep)
        self.assertIn(id(self.root), keep)
        self.assertNotIn(id(self.root.get_child("Sub")), keep)

    def test_filter_by_value_and_case(self) -> None:
        # 默认不区分大小写
        self.assertTrue(filter_nodes(self.root, "nether", fields=("value",)))
        self.assertTrue(filter_nodes(self.root, "Nether", fields=("value",)))
        # 勾上「区分大小写」后 "Nether" 就不再命中 "nether"
        self.assertEqual(
            filter_nodes(self.root, "Nether", fields=("value",),
                         case_sensitive=True), set())

    def test_filter_by_type(self) -> None:
        keep = filter_nodes(self.root, "", types={SnbtKind.INT_ARRAY})
        self.assertIn(id(self.root.get_child("Nums")), keep)
        self.assertIn(id(self.root), keep)

    def test_filter_inactive_returns_empty(self) -> None:
        self.assertEqual(filter_nodes(self.root, ""), set())

    def test_match_segments(self) -> None:
        self.assertEqual(match_segments("minecraft:stone", "stone"),
                         [(10, 15)])
        self.assertEqual(match_segments("abcabc", "abc"), [(0, 3), (3, 6)])
        # 默认不区分大小写
        self.assertEqual(match_segments("Abc", "abc"), [(0, 3)])
        self.assertEqual(match_segments("Abc", "abc", case_sensitive=True), [])
        self.assertEqual(match_segments("abc", "abc", exact=True), [(0, 3)])
        self.assertEqual(match_segments("abcd", "abc", exact=True), [])


class TestInlineEditors(unittest.TestCase):
    """按类型的就地编辑控件与范围折算。"""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _roundtrip(self, kind, value):
        node = SnbtNode.new_scalar(kind, value)
        editor = create_inline_editor(node)
        back = read_inline_editor(node, editor)
        return node, editor, back

    def test_int_editors(self) -> None:
        # 就地编辑读回来的值一律折成有符号（Byte 的 255 与 -1 是同一颗字节）
        for kind, value, expected in (
                (SnbtKind.BYTE, 100, 100),
                (SnbtKind.BYTE, 255, -1),
                (SnbtKind.SHORT, 1234, 1234),
                (SnbtKind.SHORT, 65535, -1),
                (SnbtKind.INT, 2147483647, 2147483647),
                (SnbtKind.LONG, 1234567890123456789, 1234567890123456789)):
            with self.subTest(kind=kind, value=value):
                node, editor, back = self._roundtrip(kind, value)
                self.assertEqual(back, expected)
                if hasattr(editor, "setValue"):
                    editor.setValue(value)
                else:
                    editor.setText(str(value))
                self.assertEqual(read_inline_editor(node, editor), expected)

    def test_int_hex_in_full_dialog_folds_to_signed(self) -> None:
        """完整编辑对话框里写 0xFFFFFFFF 与写 -1 是同一个值。"""
        from app.ui.widgets.nbt_dialogs import canonical_int
        node = SnbtNode.new_scalar(SnbtKind.INT, 0)
        dialog = ValueEditDialog(node)
        dialog.input.setText("0xFFFFFFFF")
        dialog.accept()
        dialog.apply_to(node)
        self.assertEqual(node.value, -1)
        dialog.deleteLater()
        self.assertEqual(canonical_int(4294967295, SnbtKind.INT), -1)
        self.assertEqual(canonical_int(4294967295, SnbtKind.LONG),
                         4294967295)

    def test_long_editor_accepts_text(self) -> None:
        """Long 超出 Qt 微调框范围，用文本框，且认 0x 与 L 后缀。"""
        from PySide6.QtWidgets import QLineEdit
        node = SnbtNode.new_scalar(SnbtKind.LONG, 0)
        editor = create_inline_editor(node)
        self.assertIsInstance(editor, QLineEdit)
        editor.setText("0xFF")
        self.assertEqual(read_inline_editor(node, editor), 255)
        editor.setText("12L")
        self.assertEqual(read_inline_editor(node, editor), 12)

    def test_float_and_double(self) -> None:
        for kind, value in ((SnbtKind.FLOAT, 1.5), (SnbtKind.DOUBLE, -2.25)):
            node, editor, back = self._roundtrip(kind, value)
            self.assertAlmostEqual(back, value)

    def test_string_and_bool(self) -> None:
        from app.ui.widgets.nbt_dialogs import read_inline_editor
        node, editor, back = self._roundtrip(SnbtKind.STRING, "hello")
        self.assertEqual(back, "hello")
        editor.setText("changed")
        self.assertEqual(read_inline_editor(node, editor), "changed")
        node, editor, back = self._roundtrip(SnbtKind.BOOL, True)
        self.assertTrue(back)
        editor.setChecked(False)
        self.assertFalse(read_inline_editor(node, editor))

    def test_canonical_int_folds_unsigned_writes(self) -> None:
        from app.ui.widgets.nbt_dialogs import canonical_int
        self.assertEqual(canonical_int(255, SnbtKind.BYTE), -1)
        self.assertEqual(canonical_int(200, SnbtKind.BYTE), -56)
        self.assertEqual(canonical_int(-1, SnbtKind.BYTE), -1)
        self.assertEqual(canonical_int(100, SnbtKind.BYTE), 100)
        self.assertEqual(canonical_int(65535, SnbtKind.SHORT), -1)


class TestValueDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_int_dialog_accepts_hex(self) -> None:
        from app.ui.widgets.nbt_dialogs import ValueEditDialog
        node = SnbtNode.new_scalar(SnbtKind.BYTE, 0)
        dialog = ValueEditDialog(node)
        dialog.input.setText("0xFF")
        dialog.accept()
        dialog.apply_to(node)
        self.assertEqual(node.value, -1)
        dialog.deleteLater()

    def test_int_dialog_rejects_garbage(self) -> None:
        from PySide6.QtWidgets import QDialogButtonBox
        from app.ui.widgets.nbt_dialogs import ValueEditDialog
        node = SnbtNode.new_scalar(SnbtKind.INT, 0)
        dialog = ValueEditDialog(node)
        dialog.input.setText("abc")
        ok = dialog.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Ok)
        self.assertFalse(ok.isEnabled())
        self.assertTrue(dialog.message.text())
        dialog.deleteLater()

    def test_string_dialog_is_multiline(self) -> None:
        from app.ui.widgets.nbt_dialogs import ValueEditDialog
        node = SnbtNode.new_scalar(SnbtKind.STRING, "a\nb")
        dialog = ValueEditDialog(node)
        self.assertIsNotNone(dialog.text)
        dialog.text.setPlainText("line1\nline2")
        dialog.accept()
        dialog.apply_to(node)
        self.assertEqual(node.value, "line1\nline2")
        dialog.deleteLater()

    def test_array_dialog_reports_size_and_rejects_range(self) -> None:
        from PySide6.QtWidgets import QDialogButtonBox
        from app.ui.widgets.nbt_dialogs import ArrayEditDialog
        node = SnbtParser("[I;1,2,3]").parse()
        dialog = ArrayEditDialog(node)
        self.assertIn("12 字节", dialog.size_label.text())
        dialog.editor.setPlainText("1, 0x10, 3")
        self.assertIn("将写入 3 项", dialog.message.text())
        dialog.editor.setPlainText("99999999999999999999")
        ok = dialog.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Ok)
        self.assertFalse(ok.isEnabled())
        dialog.deleteLater()

    def test_array_dialog_hexdump_for_byte_array(self) -> None:
        from app.ui.widgets.nbt_dialogs import ArrayEditDialog
        node = SnbtParser("[B;72,105]").parse()
        dialog = ArrayEditDialog(node)
        self.assertIn("48 69", dialog.hexdump.toPlainText())
        dialog.deleteLater()

    def test_add_element_dialog_list_and_array(self) -> None:
        from app.ui.widgets.nbt_dialogs import AddElementDialog
        lst = SnbtParser("[1,2]").parse()
        dialog = AddElementDialog(lst)
        self.assertFalse(dialog.kind_combo.isEnabled())   # 跟随首元素
        dialog.editor.setPlainText("3, 4, 5")
        self.assertEqual(dialog.values(), [3, 4, 5])
        dialog.deleteLater()

        arr = SnbtParser("[B;1]").parse()
        dialog = AddElementDialog(arr)
        dialog.editor.setPlainText("0x0A 0x0B")
        self.assertEqual(dialog.values(), [10, 11])
        dialog.deleteLater()


class EditorCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def make(self, snbt: str = '{Data:{LevelName:"world",SpawnX:0},'
                               'Nums:[I;1,2,3],L:[1,2],Empty:{}}'):
        from app.ui.widgets import NbtTreeEditor
        editor = NbtTreeEditor()
        editor.resize(900, 600)
        editor.set_root(SnbtParser(snbt).parse())
        return editor


class TestEditorEnhancements(EditorCase):
    def test_details_panel_follows_selection(self) -> None:
        editor = self.make()
        editor.select_by_path("Nums")
        self.assertEqual(editor.detail_type.text(), "IntArray")
        self.assertIn("3 项", editor.detail_meta.text())
        # 默认十进制，勾上「字节视图」后按十六进制显示
        self.assertIn("1, 2, 3", editor.detail_preview.toPlainText())
        editor._toggle_hex_mode(True)
        self.assertIn("0x1", editor.detail_preview.toPlainText())
        editor._toggle_hex_mode(False)
        self.assertTrue(editor.btn_add_item.isEnabled())
        self.assertFalse(editor.btn_add_child.isEnabled())
        editor.select_by_path("Data")
        self.assertTrue(editor.btn_add_child.isEnabled())
        self.assertFalse(editor.btn_add_item.isEnabled())
        editor.select_by_path("Data.LevelName")
        self.assertEqual(editor.detail_value.text(), "world")

    def test_rename_updates_paths_of_subtree(self) -> None:
        """改名后子树里的路径前缀要跟着更新（定位与状态栏都依赖它）。"""
        editor = self.make('{A:{B:{C:1},L:[1,2]}}')
        self.assertTrue(editor.select_by_path("A.B"))
        editor.tree.currentItem().setText(0, "Renamed")
        self.assertEqual(editor.current_path(), "A.Renamed")
        # 子节点的路径跟着换成新前缀
        editor.select_by_path("A.Renamed.C")
        self.assertEqual(editor.current_path(), "A.Renamed.C")
        self.assertEqual(editor.selected_node().value, 1)
        # 兄弟节点不受影响
        editor.select_by_path("A.L[1]")
        self.assertEqual(editor.current_path(), "A.L[1]")
        self.assertEqual(editor.selected_node().value, 2)

    def test_apply_detail_value(self) -> None:
        editor = self.make()
        editor.select_by_path("Data.SpawnX")
        editor.detail_value.setText("64")
        editor.apply_detail_value()
        self.assertEqual(editor.root().get_child("Data").get_child("SpawnX").value,
                         64)
        # 非法输入不应改动模型
        editor.detail_value.setText("abc")
        editor.apply_detail_value()
        self.assertEqual(editor.root().get_child("Data").get_child("SpawnX").value,
                         64)
        self.assertTrue(editor.detail_value_state.text())

    def test_append_element_to_array_and_list(self) -> None:
        from app.ui.widgets.nbt_tags import array_values
        editor = self.make()
        nums = editor.root().get_child("Nums")
        nums.add_child(SnbtNode.new_scalar(SnbtKind.INT, 4))
        self.assertEqual(array_values(nums), [1, 2, 3, 4])
        # 类型不匹配时拒绝，而且不弹模态框（只发状态信息）
        messages: list[str] = []
        editor.statusMessage.connect(messages.append)
        self.assertIsNone(editor.add_list_element(nums, SnbtKind.STRING))
        self.assertTrue(any("只能放" in m for m in messages))
        added = editor.add_list_element(nums, SnbtKind.INT)
        self.assertIsNotNone(added)
        self.assertEqual(len(nums.children), 5)
        # List 只接受同类型
        lst = editor.root().get_child("L")
        self.assertIsNone(editor.add_list_element(lst, SnbtKind.STRING))
        self.assertIsNotNone(editor.add_list_element(lst, SnbtKind.INT))

    def test_filter_hides_non_matches(self) -> None:
        editor = self.make()
        # 默认同时搜键名与取值
        editor.filter_edit.setText("LevelName")
        visible = [it.text(0) for it in editor._all_items() if not it.isHidden()]
        self.assertIn("LevelName", visible)
        self.assertIn("Data", visible)
        self.assertNotIn("Nums", visible)
        # 按取值也能搜到
        editor.filter_edit.setText("world")
        visible = [it.text(0) for it in editor._all_items() if not it.isHidden()]
        self.assertIn("LevelName", visible)
        self.assertNotIn("Nums", visible)
        editor.filter_edit.clear()
        hidden = [it for it in editor._all_items() if it.isHidden()]
        self.assertEqual(hidden, [])

    def test_filter_can_be_limited_to_keys(self) -> None:
        editor = self.make()
        editor.filter_values.setChecked(False)
        editor.filter_edit.setText("world")
        hidden = [it for it in editor._all_items() if it.isHidden()]
        self.assertTrue(hidden)          # 只看键名时 "world" 不该命中任何标签
        editor.filter_edit.clear()

    def test_filter_by_type(self) -> None:
        editor = self.make()
        index = editor.filter_types.findData(SnbtKind.INT_ARRAY)
        editor.filter_types.setCurrentIndex(index)
        visible = [it.text(0) for it in editor._all_items() if not it.isHidden()]
        self.assertIn("Nums", visible)
        self.assertNotIn("LevelName", visible)

    def test_multi_select_delete(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        editor = self.make()
        data = editor.root().get_child("Data")
        for item in editor._all_items():
            if item.text(0) in ("SpawnX",):
                item.setSelected(True)
            if item.text(0) == "Nums":
                item.setSelected(True)
        original = QMessageBox.question
        QMessageBox.question = staticmethod(
            lambda *a, **k: QMessageBox.StandardButton.Yes)
        try:
            editor.delete_selected()
        finally:
            QMessageBox.question = original
        self.assertIsNone(data.get_child("SpawnX"))
        self.assertIsNone(editor.root().get_child("Nums"))

    def test_copy_nodes_uses_internal_clipboard(self) -> None:
        editor = self.make()
        editor.select_by_path("Data")
        editor.copy_selected()
        editor.select_by_path("Empty")
        editor.paste_into_selected()
        pasted = editor.root().get_child("Empty").get_child("Data")
        self.assertIsNotNone(pasted)
        self.assertEqual(pasted.get_child("LevelName").value, "world")

    def test_copy_as_command(self) -> None:
        from PySide6.QtWidgets import QApplication
        editor = self.make()
        editor.select_by_path("Data.SpawnX")
        editor.copy_as_command()
        text = QApplication.clipboard().text()
        self.assertIn("/data modify", text)
        self.assertIn("Data.SpawnX", text)
        self.assertIn("set value 0", text)

    def test_expand_and_collapse_all(self) -> None:
        editor = self.make()
        editor.expand_all()
        self.assertTrue(all(it.isExpanded() for it in editor._all_items()))
        editor.collapse_all()
        items = list(editor._all_items())
        self.assertFalse(any(it.isExpanded() for it in items[1:]))
        self.assertTrue(items[0].isExpanded())

    def test_details_toggle(self) -> None:
        editor = self.make()
        self.assertFalse(editor._details_panel.isHidden())
        editor.toggle_details(False)
        self.assertTrue(editor._details_panel.isHidden())
        self.assertFalse(editor.btn_details.isChecked())
        editor.toggle_details(True)
        self.assertFalse(editor._details_panel.isHidden())

    def test_root_name_shown_in_tree(self) -> None:
        editor = self.make()
        editor.set_root_name("hello")
        self.assertIn("hello", editor.tree.topLevelItem(0).text(0))
        editor.set_root_name("")
        self.assertEqual(editor.tree.topLevelItem(0).text(0), "(根)")

    def test_byte_text_toggle_only_for_byte_array(self) -> None:
        editor = self.make('{B:[B;72,105],I:[I;1]}')
        editor.select_by_path("B")
        self.assertTrue(editor.btn_byte_text.isEnabled())
        editor.select_by_path("I")
        self.assertFalse(editor.btn_byte_text.isEnabled())
        editor.select_by_path("B")
        editor._toggle_byte_text(True)
        self.assertIn("Hi", editor.detail_preview.toPlainText())

    def test_undo_restores_selection(self) -> None:
        editor = self.make()
        editor.select_by_path("Data.SpawnX")
        editor.detail_value.setText("99")
        editor.apply_detail_value()
        self.assertEqual(editor.root().get_child("Data").get_child("SpawnX").value,
                         99)
        self.assertTrue(editor.undo())
        self.assertEqual(editor.current_path(), "Data.SpawnX")

    def test_illegal_drop_is_rejected(self) -> None:
        """List 里不能塞字符串：拖放必须被拒绝且模型不变。"""
        editor = self.make()
        lst = editor.root().get_child("L")
        level = editor.root().get_child("Data").get_child("LevelName")
        before = serialize(editor.root(), pretty=False)
        for item in editor._all_items():
            if item.text(0) == "LevelName":
                editor.tree.setCurrentItem(item)
        self.assertFalse(editor._reparent(level, lst, 0))
        self.assertEqual(serialize(editor.root(), pretty=False), before)

    def test_legal_drop_between_siblings(self) -> None:
        editor = self.make()
        root = editor.root()
        node = root.get_child("L")
        self.assertTrue(editor._reparent(node, root, 0))
        self.assertIs(root.children[0], node)

    def test_select_by_path_into_collapsed_container(self) -> None:
        """`Nums[1]` 这种路径要能自动展开父容器再选中。"""
        editor = self.make()
        self.assertTrue(editor.select_by_path("Nums[1]"))
        self.assertEqual(editor.selected_node().value, 2)
        self.assertEqual(editor.current_path(), "Nums[1]")
        # 大数组里的深层元素也要定位得到（懒渲染不截断定位目标）
        root = editor.root()
        big = SnbtNode(kind=SnbtKind.INT_ARRAY, key="Big")
        for i in range(600):
            big.add_child(SnbtNode.new_scalar(SnbtKind.INT, i))
        root.add_child(big)
        editor.set_root(root)
        self.assertTrue(editor.select_by_path("Big[499]"))
        self.assertEqual(editor.selected_node().value, 499)

    def test_lazy_elements_are_not_rendered_until_expanded(self) -> None:
        editor = self.make()
        root = editor.root()
        big = SnbtNode(kind=SnbtKind.INT_ARRAY, key="Big")
        for i in range(500):
            big.add_child(SnbtNode.new_scalar(SnbtKind.INT, i))
        root.add_child(big)
        editor.set_root(root)
        # 没展开时不该为 500 个元素建节点
        paths = [it.path for it in editor._all_items()]
        self.assertNotIn("Big[0]", paths)
        editor.select_by_path("Big")
        editor.expand_selected_elements()
        paths = [it.path for it in editor._all_items()]
        self.assertIn("Big[0]", paths)
        # 超过上限的部分不渲染，避免界面卡死
        self.assertNotIn("Big[499]", paths)

    def test_filter_finds_unrendered_element_value(self) -> None:
        editor = self.make()
        root = editor.root()
        big = SnbtNode.new_list(key="Names")
        for i in range(600):
            big.add_child(SnbtNode.new_scalar(SnbtKind.STRING, f"n{i}"))
        root.add_child(big)
        editor.set_root(root)
        editor.filter_edit.setText("n599")
        visible = [it.path for it in editor._all_items() if not it.isHidden()]
        self.assertIn("Names[599]", visible)
        editor.filter_edit.clear()
        self.assertNotIn("Names[599]",
                         [it.path for it in editor._all_items()])


class TestFileEditorEnhancements(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from app.core.nbt_binary import save_nbt_file
        from app.core.snbt import compound, integer, string
        self.tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-nbt-"))
        self.path = self.tmp / "level.dat"
        save_nbt_file(compound(Data=compound(LevelName=string("w"),
                                            SpawnX=integer(3))),
                      self.path, compress="none")
        from app.ui.widgets import NbtFileEditor
        self.editor = NbtFileEditor()

    def test_load_reload_and_recent(self) -> None:
        self.assertTrue(self.editor.load_path(str(self.path)))
        # 「最近打开」里应该记下这个文件（菜单项的提示里存着完整路径）
        recent = self.editor._recent_paths()
        self.assertIn(str(self.path.resolve()), recent)
        tips = [a.toolTip() for a in self.editor.recent_menu.actions()]
        self.assertTrue(any("level.dat" in t for t in tips), tips)
        # 改动 → 重新载入后回到磁盘上的内容
        node = self.editor.root().get_child("Data").get_child("SpawnX")
        node.value = 999
        self.editor.editor.changed.emit()
        self.assertTrue(self.editor.doc.dirty)
        self.assertTrue(self.editor.reload())
        self.assertFalse(self.editor.doc.dirty)
        self.assertEqual(
            self.editor.root().get_child("Data").get_child("SpawnX").value, 3)

    def test_root_name_roundtrip(self) -> None:
        self.editor.load_path(str(self.path))
        self.editor.root_name.setText("named")
        self.assertEqual(self.editor.editor.root_name(), "named")
        self.assertTrue(self.editor.doc.dirty)

    def test_request_new_file_signal(self) -> None:
        self.editor.load_path(str(self.path))
        self.editor.doc.dirty = False
        self.editor.editor.requestNewFile.emit()
        self.assertIsNone(self.editor.doc.path)

    def test_snbt_file_switches_to_text_mode(self) -> None:
        snbt = self.tmp / "x.snbt"
        snbt.write_text('{A:1b}', encoding="utf-8")
        self.editor.load_path(str(snbt))
        self.assertEqual(self.editor.editor.stack.currentIndex(), 1)


if __name__ == "__main__":
    unittest.main()
