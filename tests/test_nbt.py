"""二进制 NBT 读写：三种容器往返、文本 SNBT、错误输入。"""
from __future__ import annotations

import gzip
import io
import tempfile
import unittest
from pathlib import Path

from tests import TEST_ROOT  # noqa: F401  （导入即设置环境变量）
from app.core.nbt_binary import (
    NbtFormatError, NbtWriteError, load_nbt_file, parse_nbt_bytes,
    parse_snbt_text, save_nbt_file,
)
from app.core.snbt import (
    SnbtKind, SnbtParser, SnbtNode, byte, boolean, compound, double, integer,
    list_node, serialize, string,
)

try:
    import nbtlib
    from nbtlib import Compound, Int, String
    HAS_NBTLIB = True
except Exception:                                   # pragma: no cover
    HAS_NBTLIB = False


def sample_root() -> SnbtNode:
    return compound(
        CustomName=string('{"text":"demo"}'),
        IsBaby=byte(0),
        CanPickUpLoot=boolean(True),
        Health=integer(20),
        Speed=double(0.25),
        Tags=list_node([string("demo"), string("stage7")]),
        Nums=list_node([integer(1), integer(2), integer(3)]),
    )


class TestSnbt(unittest.TestCase):
    def test_roundtrip_serialize(self) -> None:
        root = sample_root()
        text = serialize(root, pretty=False)
        again = SnbtParser(text).parse()
        self.assertEqual(serialize(again, pretty=False), text)

    def test_parse_errors_have_position(self) -> None:
        from app.core.snbt import SnbtParseError
        with self.assertRaises(SnbtParseError) as ctx:
            SnbtParser("{a:}").parse()
        self.assertGreater(ctx.exception.line, 0)

    def test_arrays(self) -> None:
        node = SnbtParser("[I;1,2,3]").parse()
        self.assertEqual(node.kind, SnbtKind.INT_ARRAY)
        self.assertEqual(serialize(node), "[I;1,2,3]")


class TestNbtBinary(unittest.TestCase):
    def setUp(self) -> None:
        self.root = sample_root()
        self.expected = serialize(self.root, pretty=False)
        self.tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-nbt-"))

    def test_roundtrip_all_containers(self) -> None:
        for compress in ("gzip", "zlib", "none"):
            with self.subTest(compress=compress):
                path = self.tmp / f"rt_{compress}.nbt"
                save_nbt_file(self.root, path, compress=compress)
                back = serialize(load_nbt_file(path), pretty=False)
                self.assertEqual(back, self.expected)

    def test_container_magic_bytes(self) -> None:
        gz = self.tmp / "a.nbt"
        save_nbt_file(self.root, gz, compress="gzip")
        self.assertEqual(gz.read_bytes()[:2], b"\x1f\x8b")
        raw = self.tmp / "b.nbt"
        save_nbt_file(self.root, raw, compress="none")
        self.assertEqual(raw.read_bytes()[0], 0x0A, "未压缩应以 TAG_Compound 开头")

    def test_snbt_text_roundtrip(self) -> None:
        path = self.tmp / "demo.snbt"
        save_nbt_file(self.root, path)
        self.assertEqual(
            serialize(load_nbt_file(path), pretty=False), self.expected)

    def test_named_root(self) -> None:
        path = self.tmp / "named.nbt"
        save_nbt_file(self.root, path, root_name="")
        self.assertEqual(
            serialize(load_nbt_file(path), pretty=False), self.expected)

    def test_rejects_garbage(self) -> None:
        for bad in (b"", b"\x01\x02\x03\x04", b"definitely not nbt"):
            with self.subTest(data=bad):
                with self.assertRaises(NbtFormatError):
                    parse_nbt_bytes(bad)

    def test_missing_file(self) -> None:
        with self.assertRaises(NbtFormatError):
            load_nbt_file(self.tmp / "nope.nbt")

    def test_parse_snbt_text_error(self) -> None:
        with self.assertRaises(NbtFormatError):
            parse_snbt_text("{bad", "unit")

    def test_heterogeneous_list_is_widened(self) -> None:
        root = compound(Mixed=list_node([integer(1), double(2.5)]))
        path = self.tmp / "mixed.nbt"
        save_nbt_file(root, path)
        back = load_nbt_file(path)
        self.assertEqual(len(back.children), 1)

    @unittest.skipUnless(HAS_NBTLIB, "需要 nbtlib")
    def test_reads_external_file(self) -> None:
        """回归：读者必须能读别人生成的标准 gzip NBT（File.parse 需要文件对象）。"""
        buf = io.BytesIO()
        nbtlib.File({"": Compound({"Health": Int(20), "Name": String("demo")})}) \
            .write(buf, byteorder="big")
        path = self.tmp / "external.nbt"
        path.write_bytes(gzip.compress(buf.getvalue()))
        node = load_nbt_file(path)
        self.assertEqual(node.get_child("Health").value, 20)
        self.assertEqual(node.get_child("Name").value, "demo")

    def test_non_compound_root_is_wrapped(self) -> None:
        path = self.tmp / "scalar.nbt"
        save_nbt_file(integer(7), path)
        back = load_nbt_file(path)
        self.assertEqual(back.kind, SnbtKind.COMPOUND)

    def test_write_error_for_bad_compress(self) -> None:
        with self.assertRaises(NbtWriteError):
            save_nbt_file(self.root, self.tmp / "x.nbt", compress="rar")

    def test_byte_array_folds_out_of_range_values(self) -> None:
        """回归：`[B;10,20,255]` 曾让写入抛 OverflowError（nbtlib 用有符号字节）。"""
        root = SnbtParser("{B:[B;10,20,255,0],H:[B;128,255,127]}").parse()
        path = self.tmp / "bytes.dat"
        save_nbt_file(root, path, compress="none", root_name="")
        back = load_nbt_file(path)
        values = [c.value for c in back.get_child("B").children]
        self.assertEqual(values, [10, 20, -1, 0])
        self.assertEqual([c.value for c in back.get_child("H").children],
                         [-128, -1, 127])

    def test_int_array_out_of_range_is_folded(self) -> None:
        root = SnbtParser("{I:[I;2147483647,-2147483648]}").parse()
        path = self.tmp / "ints.dat"
        save_nbt_file(root, path, compress="none", root_name="")
        back = load_nbt_file(path)
        self.assertEqual([c.value for c in back.get_child("I").children],
                         [2147483647, -2147483648])


class TestNbtDocument(unittest.TestCase):
    """文档模型：打开 → 保存要保留源文件的格式与根标签名。"""

    def setUp(self) -> None:
        from app.core.nbt_binary import NbtDocument
        self.NbtDocument = NbtDocument
        self.tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-doc-"))
        self.root = sample_root()

    def test_detects_and_keeps_compression(self) -> None:
        from app.core.nbt_binary import detect_compression
        for kind in ("none", "gzip", "zlib"):
            path = self.tmp / f"t_{kind}.dat"
            save_nbt_file(self.root, path, compress=kind, root_name="")
            doc = self.NbtDocument.load(path)
            with self.subTest(kind=kind):
                self.assertEqual(doc.compression, kind)
                doc.dirty = True
                doc.save()
                self.assertEqual(detect_compression(path.read_bytes()), kind)
                self.assertFalse(doc.dirty)

    def test_keeps_root_name(self) -> None:
        path = self.tmp / "named.dat"
        save_nbt_file(self.root, path, compress="none", root_name="hello")
        doc = self.NbtDocument.load(path)
        self.assertEqual(doc.root_name, "hello")
        doc.save()
        self.assertEqual(self.NbtDocument.load(path).root_name, "hello")

    def test_snbt_document(self) -> None:
        path = self.tmp / "x.snbt"
        save_nbt_file(self.root, path)
        doc = self.NbtDocument.load(path)
        self.assertTrue(doc.is_text)
        self.assertEqual(doc.compression, "none")
        doc.save()
        self.assertTrue(path.read_text(encoding="utf-8").startswith("{"))

    def test_title_marks_dirty(self) -> None:
        path = self.tmp / "a.dat"
        save_nbt_file(self.root, path, compress="none")
        doc = self.NbtDocument.load(path)
        self.assertNotIn("*", doc.display_title())
        doc.dirty = True
        self.assertIn("*", doc.display_title())
        self.assertIn("未保存", doc.info_line())

    def test_new_document_defaults(self) -> None:
        doc = self.NbtDocument()
        self.assertIsNone(doc.path)
        self.assertEqual(doc.compression, "gzip")
        self.assertEqual(doc.display_title(), "未命名")
        with self.assertRaises(NbtWriteError):
            doc.save()

    def test_save_as_switches_format(self) -> None:
        from app.core.nbt_binary import detect_compression
        src = self.tmp / "src.dat"
        save_nbt_file(self.root, src, compress="none")
        doc = self.NbtDocument.load(src)
        out = self.tmp / "out.dat"
        doc.save(out, compression="gzip")
        self.assertEqual(detect_compression(out.read_bytes()), "gzip")
        self.assertEqual(doc.path, out)
        self.assertFalse(doc.dirty)


class TestNbtEditorWidget(unittest.TestCase):
    """离屏测试编辑器控件：撤销/重做、复制、路径拼接。"""

    @classmethod
    def setUpClass(cls) -> None:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _editor(self):
        from app.ui.widgets import NbtTreeEditor
        editor = NbtTreeEditor()
        editor.set_root(compound(
            Data=compound(LevelName=string("world"), SpawnX=integer(0))))
        return editor

    def test_undo_redo_roundtrip(self) -> None:
        editor = self._editor()
        data = editor.root().get_child("Data")
        editor._push_history()
        data.add_child(SnbtNode.new_scalar(SnbtKind.STRING, "x", key="Added"))
        editor._refresh_all()
        editor._mark_changed()
        self.assertIn("Added", editor.to_snbt(pretty=False))
        self.assertTrue(editor.undo())
        self.assertNotIn("Added", editor.to_snbt(pretty=False))
        self.assertTrue(editor.redo())
        self.assertIn("Added", editor.to_snbt(pretty=False))

    def test_undo_without_history(self) -> None:
        editor = self._editor()
        self.assertFalse(editor.can_undo())
        self.assertFalse(editor.undo())
        self.assertFalse(editor.redo())

    def test_copy_selected_and_path(self) -> None:
        from PySide6.QtCore import Qt
        editor = self._editor()
        items = editor.tree.findItems(
            "Data", Qt.MatchFlag.MatchExactly
            | Qt.MatchFlag.MatchRecursive, 0)
        self.assertTrue(items)
        editor.tree.setCurrentItem(items[0])
        text = editor.copy_selected_snbt()
        self.assertIn("LevelName", text)
        self.assertEqual(editor._node_path(editor.selected_node()), "Data")

    def test_file_editor_roundtrip(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        from app.ui.widgets import NbtFileEditor
        tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-nbted-"))
        path = tmp / "level.dat"
        save_nbt_file(compound(Data=compound(LevelName=string("w"))),
                      path, compress="none")
        widget = NbtFileEditor()
        # 离屏环境下模态对话框会永久阻塞，这里把它变成空操作
        original = QMessageBox.warning
        QMessageBox.warning = staticmethod(lambda *a, **k: None)
        try:
            self.assertTrue(widget.load_path(str(path)))
            self.assertEqual(widget.doc.name, "level.dat")
            self.assertFalse(widget.doc.dirty)
            # 编辑后变脏，保存后恢复
            widget.editor.root().add_child(
                SnbtNode.new_scalar(SnbtKind.INT, 1, key="New"))
            widget.editor._mark_changed()
            self.assertTrue(widget.doc.dirty)
            self.assertTrue(widget.save())
            self.assertFalse(widget.doc.dirty)
        finally:
            QMessageBox.warning = original

    def test_file_editor_rejects_garbage(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        from app.ui.widgets import NbtFileEditor
        tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-nbted-"))
        bad = tmp / "bad.dat"
        bad.write_bytes(b"not nbt at all")
        widget = NbtFileEditor()
        original = QMessageBox.warning
        QMessageBox.warning = staticmethod(lambda *a, **k: None)
        try:
            self.assertFalse(widget.load_path(str(bad)))
        finally:
            QMessageBox.warning = original

    def test_file_editor_new_document(self) -> None:
        from app.ui.widgets import NbtFileEditor
        widget = NbtFileEditor()
        widget.new_document()
        self.assertIsNone(widget.doc.path)
        self.assertEqual(widget.root().kind, SnbtKind.COMPOUND)


class TestUnnamedRootCompound(unittest.TestCase):
    """根标签没有名字时，根下的**所有**子标签都要读出来。

    回归：nbtlib.File 在根无名时会把「根 compound 的子标签」当作键，
    以前这里只取第一项当根，于是同级标签（世界存档的 `Data`）被整个丢掉。
    """

    def setUp(self) -> None:
        from app.core.nbt_binary import load_nbt_file, save_nbt_file
        from app.core.snbt import compound, integer, list_node, string
        self.load_nbt_file = load_nbt_file
        self.save_nbt_file = save_nbt_file
        self.tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-root-"))
        # 世界存档：根无名，根下 fml（mod 清单）+ Data（世界数据）
        mods = list_node([
            compound(ModId=string("create"), ModVersion=string("1.0")),
        ])
        root = compound(
            fml=compound(LoadingModList=mods),
            Data=compound(version=integer(19133),
                          LevelName=string("world"),
                          DataVersion=integer(3955)),
        )
        self.path = self.tmp / "test.dat"
        self.save_nbt_file(root, self.path, compress="gzip", root_name="")

    def test_all_root_children_survive(self) -> None:
        node, meta = self.load_nbt_file(self.path, with_meta=True)
        self.assertEqual(meta["root_name"], "")
        self.assertEqual([c.key for c in node.children], ["fml", "Data"])
        data = node.get_child("Data")
        self.assertIsNotNone(data)
        self.assertEqual(len(data.children), 3)
        self.assertEqual(data.get_child("LevelName").value, "world")
        mods = node.get_child("fml").get_child("LoadingModList")
        self.assertEqual(len(mods.children), 1)

    def test_roundtrip_keeps_siblings(self) -> None:
        node, _ = self.load_nbt_file(self.path, with_meta=True)
        out = self.tmp / "again.dat"
        self.save_nbt_file(node, out, compress="gzip", root_name="")
        back, meta = self.load_nbt_file(out, with_meta=True)
        self.assertEqual(meta["root_name"], "")
        self.assertEqual([c.key for c in back.children], ["fml", "Data"])

    def test_named_root_still_works(self) -> None:
        """根标签**有**名字时仍然只取那一项（不能被新逻辑带偏）。"""
        from app.core.snbt import compound, integer, string
        named = compound(compass=integer(3), wool=string("felt"))
        path = self.tmp / "named.dat"
        self.save_nbt_file(named, path, compress="none", root_name="hello")
        node, meta = self.load_nbt_file(path, with_meta=True)
        self.assertEqual(meta["root_name"], "hello")
        self.assertEqual([c.key for c in node.children], ["compass", "wool"])

    def test_single_unnamed_child(self) -> None:
        """根下只有一个子标签时，不能把它当成根。"""
        from app.core.snbt import compound
        root = compound(OnlyTag=compound(A=compound()))
        path = self.tmp / "single.dat"
        self.save_nbt_file(root, path, compress="none", root_name="")
        node, meta = self.load_nbt_file(path, with_meta=True)
        self.assertEqual(meta["root_name"], "")
        self.assertEqual([c.key for c in node.children], ["OnlyTag"])


if __name__ == "__main__":
    unittest.main()
