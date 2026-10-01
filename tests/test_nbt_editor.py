"""NBT 编辑器（对标 NBTExplorer）的标签层与树形编辑器测试。

覆盖：类型名与格式化、值解析（含十六进制/范围）、数组解析与 hex dump、
节点路径、查找替换，以及树控件的增删改/撤销重做/复制粘贴/拖放。
"""
from __future__ import annotations

import os
import unittest

import tests  # noqa: F401  （导入即设置环境变量）

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.snbt import SnbtKind, SnbtNode, SnbtParser, serialize  # noqa: E402
from app.ui.widgets.nbt_tags import (                                # noqa: E402
    ValueParseError, bytes_to_hexdump, count_nodes, default_value,
    find_nodes, format_value, iter_nodes, node_path, parse_array_text,
    parse_bool, parse_int, parse_value, summarize, type_label,
)


class TestTagLayer(unittest.TestCase):
    def test_type_labels(self) -> None:
        root = SnbtParser(
            '{a:1b,b:1s,c:1,d:1L,e:1.5f,f:1.5,g:"s",h:[1],i:{},'
            'j:[B;1],k:[I;1],l:[L;1]}').parse()
        got = {c.key: type_label(c) for c in root.children}
        self.assertEqual(got, {
            "a": "Bool", "b": "Short", "c": "Int", "d": "Long",
            "e": "Float", "f": "Double", "g": "String", "h": "List",
            "i": "Compound", "j": "ByteArray", "k": "IntArray",
            "l": "LongArray"})

    def test_format_value(self) -> None:
        root = SnbtParser(
            '{s:"hi",b:1b,by:[B;10,255],ia:[I;1,2,3],c:{x:1},l:[1,2]}'
            ).parse()
        for key, expect in (("s", "hi"), ("b", "1b"), ("c", "{x: 1}"),
                            ("l", "[ 1, 2 ]")):
            with self.subTest(key=key):
                self.assertEqual(format_value(root.get_child(key)), expect)
        self.assertIn("255", format_value(root.get_child("by")))
        self.assertIn("FF", format_value(root.get_child("by"), hex_mode=True))
        self.assertIn("0x1", format_value(root.get_child("ia"), hex_mode=True))
        # 查找 / 筛选时用简短占位，避免把字段名当成「取值」命中
        self.assertEqual(
            format_value(root.get_child("c"), preview=False), "{ 1 项 }")
        self.assertEqual(
            format_value(root.get_child("l"), preview=False), "[ 1, 2 ]")

    def test_parse_int_accepts_hex_and_suffix(self) -> None:
        self.assertEqual(parse_int("1b", SnbtKind.BYTE), 1)
        self.assertEqual(parse_int("0xFF", SnbtKind.BYTE), 255)
        self.assertEqual(parse_int("-7", SnbtKind.INT), -7)
        self.assertEqual(parse_int("12L", SnbtKind.LONG), 12)
        self.assertEqual(parse_int("0x10", SnbtKind.INT), 16)

    def test_parse_int_rejects_garbage_and_range(self) -> None:
        with self.assertRaises(ValueParseError):
            parse_int("abc", SnbtKind.INT)
        with self.assertRaises(ValueParseError):
            parse_int("", SnbtKind.INT)
        with self.assertRaises(ValueParseError):
            parse_int("300", SnbtKind.BYTE)          # Byte 上限 255
        with self.assertRaises(ValueParseError):
            parse_int("99999", SnbtKind.SHORT)

    def test_parse_bool(self) -> None:
        for text in ("1b", "1", "true", "yes"):
            self.assertTrue(parse_bool(text))
        for text in ("0b", "0", "false", "no"):
            self.assertFalse(parse_bool(text))
        with self.assertRaises(ValueParseError):
            parse_bool("maybe")

    def test_parse_array_text(self) -> None:
        self.assertEqual(parse_array_text("[B;1,2,3]"), [1, 2, 3])
        self.assertEqual(parse_array_text("[I; 1 2 3]"), [1, 2, 3])
        self.assertEqual(parse_array_text("1, 2, 3"), [1, 2, 3])
        self.assertEqual(parse_array_text("0x0A, 0x0B"), [10, 11])
        self.assertEqual(parse_array_text("-1, -2"), [-1, -2])
        with self.assertRaises(ValueParseError):
            parse_array_text("1, x, 3")

    def test_hexdump(self) -> None:
        text = bytes_to_hexdump(b"Hello, NBT!")
        self.assertIn("48 65 6C 6C 6F", text)
        self.assertIn("|Hello, NBT!|", text)
        self.assertIn("00000000", text)

    def test_node_path(self) -> None:
        root = SnbtParser('{D:{A:1,L:[10,20]},N:[I;5]}').parse()
        self.assertEqual(node_path(root, root), "(根)")
        self.assertEqual(node_path(root.get_child("D"), root), "D")
        self.assertEqual(
            node_path(root.get_child("D").get_child("A"), root), "D.A")
        self.assertEqual(
            node_path(root.get_child("L") if False else
                      root.get_child("D").get_child("L").children[1], root),
            "D.L[1]")
        self.assertEqual(node_path(root.get_child("N").children[0], root),
                         "N[0]")

    def test_find_nodes(self) -> None:
        root = SnbtParser('{Name:"world",Sub:{Name:"nether"},N:1}').parse()
        by_name = find_nodes(root, "Name")
        self.assertEqual(len(by_name), 2)
        by_value = find_nodes(root, "nether", in_keys=False)
        self.assertEqual([n.key for n in by_value], ["Name"])
        exact = find_nodes(root, "N", exact=True, in_keys=True,
                           in_values=False)
        self.assertEqual([n.key for n in exact], ["N"])
        # 区分大小写时 "Nether" 不应命中 "nether"
        self.assertEqual(find_nodes(root, "Nether", exact=True,
                                   case_sensitive=True), [])
        self.assertEqual(len(find_nodes(root, "Nether", exact=True)), 1)

    def test_summary_and_count(self) -> None:
        root = SnbtParser('{A:{B:{C:1}}}').parse()
        self.assertEqual(count_nodes(root), 4)
        self.assertIn("最深 4 层", summarize(root))

    def test_defaults(self) -> None:
        self.assertEqual(default_value(SnbtKind.INT), 0)
        self.assertEqual(default_value(SnbtKind.STRING), "")


class EditorCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def make(self, snbt: str = '{Data:{LevelName:"world",SpawnX:0},'
                               'Nums:[I;1,2,3],Empty:{}}'):
        from app.ui.widgets import NbtTreeEditor
        editor = NbtTreeEditor()
        editor.resize(900, 600)
        editor.set_root(SnbtParser(snbt).parse())
        return editor


class TestTreeEditor(EditorCase):
    def test_three_columns_and_types(self) -> None:
        editor = self.make()
        self.assertEqual(editor.tree.columnCount(), 3)
        self.assertEqual(
            [editor.tree.headerItem().text(i) for i in range(3)],
            ["名称", "类型", "取值"])
        root = editor.tree.topLevelItem(0)
        types = [root.child(i).text(1) for i in range(root.childCount())]
        # 类型列按「专有名词翻译」显示（默认中文），原文见 tooltip
        self.assertEqual(types, ["复合标签", "整型数组", "复合标签"])
        tips = [root.child(i).toolTip(1) for i in range(root.childCount())]
        self.assertEqual(tips, ["Compound（复合标签）", "IntArray（整型数组）",
                                "Compound（复合标签）"])

    def test_select_by_path_and_status(self) -> None:
        editor = self.make()
        self.assertTrue(editor.select_by_path("Data.SpawnX"))
        self.assertEqual(editor.selected_node().key, "SpawnX")
        self.assertIn("Data.SpawnX", editor.status.text())
        self.assertTrue(editor.select_by_path("Nums[1]"))
        self.assertEqual(editor.selected_node().value, 2)

    def test_insert_rename_delete(self) -> None:
        editor = self.make()
        data = editor.root().get_child("Data")
        node = editor.insert_node(data, SnbtKind.INT, key="Added")
        self.assertIsNotNone(node)
        self.assertEqual(node.value, 0)
        self.assertEqual(editor.selected_node().key, "Added")
        # 重名自动编号
        again = editor.insert_node(editor.root().get_child("Data"),
                                   SnbtKind.INT, key="Added")
        self.assertEqual(again.key, "Added1")
        editor.select_by_path("Data.Added1")
        editor.delete_selected()
        self.assertIsNone(editor.root().get_child("Data").get_child("Added1"))
        # 新建 Compound
        compound = editor.insert_node(editor.root().get_child("Data"),
                                      SnbtKind.COMPOUND, key="Sub")
        self.assertTrue(compound.is_container)

    def test_undo_redo(self) -> None:
        editor = self.make()
        editor.insert_node(editor.root().get_child("Data"), SnbtKind.INT,
                           key="Temp")
        self.assertIn("Temp", serialize(editor.root(), pretty=False))
        self.assertTrue(editor.undo())
        self.assertNotIn("Temp", serialize(editor.root(), pretty=False))
        self.assertTrue(editor.redo())
        self.assertIn("Temp", serialize(editor.root(), pretty=False))
        self.assertTrue(editor.undo())
        self.assertFalse(editor.redo() is False)   # 还能重做

    def test_copy_and_paste(self) -> None:
        from PySide6.QtWidgets import QApplication
        editor = self.make()
        editor.select_by_path("Data.LevelName")
        text = editor.copy_selected_snbt()
        self.assertEqual(text, "world")
        QApplication.clipboard().setText('{Copied:1b}')
        editor.select_by_path("Empty")
        editor.paste_into_selected()
        pasted = editor.root().get_child("Empty")
        self.assertIsNotNone(pasted)
        self.assertEqual(len(pasted.children), 1)

    def test_duplicate_and_move(self) -> None:
        editor = self.make()
        editor.select_by_path("Data.LevelName")
        editor.duplicate_selected()
        data = editor.root().get_child("Data")
        self.assertIsNotNone(data.get_child("LevelName_copy"))
        editor.select_by_path("Data.LevelName")
        editor._move_selected(1)
        # LevelName 与它后面的 LevelName_copy 交换位置
        self.assertEqual([c.key for c in data.children],
                         ["LevelName_copy", "LevelName", "SpawnX"])

    def test_edit_value_parses_by_type(self) -> None:
        editor = self.make()
        spawn = editor.root().get_child("Data").get_child("SpawnX")
        spawn.value = parse_value(spawn, "0x1F")
        self.assertEqual(spawn.value, 31)
        hardcore = SnbtParser('{H:0b}').parse().get_child("H")
        hardcore.value = parse_value(hardcore, "1b")
        self.assertTrue(hardcore.value)

    def test_hex_mode_toggle(self) -> None:
        editor = self.make()

        def nums_text() -> str:
            root = editor.tree.topLevelItem(0)
            for i in range(root.childCount()):
                if root.child(i).text(0) == "Nums":
                    return root.child(i).text(2)
            return ""

        editor._toggle_hex_mode(True)
        self.assertIn("0x1", nums_text())
        editor._toggle_hex_mode(False)
        self.assertNotIn("0x1", nums_text())

    def test_find_next_and_replace(self) -> None:
        editor = self.make()
        editor.show_find_bar()
        editor.find_bar.find_edit.setText("world")
        editor.find_next()
        self.assertEqual(editor.selected_node().key, "LevelName")
        editor.find_bar.replace_edit.setText("newworld")
        editor.replace_current()
        self.assertEqual(
            editor.root().get_child("Data").get_child("LevelName").value,
            "newworld")
        editor.find_bar.find_edit.setText("no-such-text")
        editor.replace_all()
        self.assertIn("没有匹配", editor.find_bar.result.text())

    def test_text_mode_roundtrip(self) -> None:
        editor = self.make()
        editor._set_mode("text")
        editor.text.setPlainText('{A:1b,B:[B;1,2]}')
        editor._apply_text_now()
        self.assertEqual(serialize(editor.root(), pretty=False),
                         "{A:1b,B:[B;1b,2b]}")
        # 非法文本不应破坏模型
        editor.text.setPlainText("{bad")
        editor._apply_text_now()
        self.assertTrue(editor.error_label.text())
        self.assertIn("A", serialize(editor.root(), pretty=False))

    def test_drag_drop_moves_model(self) -> None:
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QDropEvent

        editor = self.make('{A:{X:1},B:{}}')
        root_item = editor.tree.topLevelItem(0)
        a_item = root_item.child(0)
        b_item = root_item.child(1)
        editor.tree.setCurrentItem(a_item)
        # 把 itemAt 指到 B，模拟「放到 B 上」→ A 变成 B 的子节点
        original = editor.tree.itemAt
        editor.tree.itemAt = lambda *_: b_item
        try:
            event = QDropEvent(
                QPoint(1, 1), Qt.DropAction.MoveAction,
                __import__("PySide6.QtCore", fromlist=["QMimeData"]).QMimeData(),
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            editor.tree.dropEvent(event)
        finally:
            editor.tree.itemAt = original
        b_node = editor.root().get_child("B")
        self.assertEqual([c.key for c in b_node.children], ["A"])
        self.assertIsNone(editor.root().get_child("A"))

    def test_cannot_move_into_own_subtree(self) -> None:
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QDropEvent

        editor = self.make('{A:{B:{}}}')
        root_item = editor.tree.topLevelItem(0)
        a_item = root_item.child(0)
        b_item = a_item.child(0)
        editor.tree.setCurrentItem(a_item)
        original = editor.tree.itemAt
        editor.tree.itemAt = lambda *_: b_item
        try:
            event = QDropEvent(
                QPoint(1, 1), Qt.DropAction.MoveAction,
                __import__("PySide6.QtCore", fromlist=["QMimeData"]).QMimeData(),
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            editor.tree.dropEvent(event)
        finally:
            editor.tree.itemAt = original
        self.assertIsNotNone(editor.root().get_child("A"))
        self.assertIsNotNone(editor.root().get_child("A").get_child("B"))

    def test_path_helpers(self) -> None:
        editor = self.make()
        node = editor.root().get_child("Nums").children[2]
        self.assertEqual(editor._node_path(node), "Nums[2]")
        self.assertEqual(editor._node_path(None), "")


if __name__ == "__main__":
    unittest.main()
