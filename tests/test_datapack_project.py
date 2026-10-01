"""数据包工程（创作层）：命名空间 / 资源 / 标签 / 布局兼容 / 原子落盘。"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests import TEST_ROOT  # noqa: F401  （导入即设置环境变量）

from app.core.datapack_project import (
    ASSET_KINDS, COMMON_TAGS, LAYOUT_PLURAL, LAYOUT_SINGULAR, PACK_FORMATS,
    AuthoringPack, PackAsset, advancement_template, function_template,
    import_pack, kind_from_suffix, loot_table_template, predicate_template,
)


class _PackCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mcstudio-pack-"))
        self.root = self.tmp / "pack"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pack(self, namespace: str = "demo") -> AuthoringPack:
        return AuthoringPack(root=self.root, namespace=namespace,
                             description="测试包")


class TestPackBasics(_PackCase):
    def test_accepts_string_root(self) -> None:
        pack = AuthoringPack(root=str(self.root), namespace="Demo")
        self.assertIsInstance(pack.root, Path)
        self.assertEqual(pack.namespace, "demo")        # 自动小写

    def test_asset_dirs_follow_layout(self) -> None:
        pack = self._pack()
        self.assertTrue(str(pack.asset_dir("function")).endswith(
            "function".replace("/", os.sep)))
        pack.layout = LAYOUT_PLURAL
        self.assertTrue(str(pack.asset_dir("function")).endswith(
            "functions".replace("/", os.sep)))

    def test_add_find_remove(self) -> None:
        pack = self._pack()
        asset = pack.add_asset("function", "shop/buy", "say hi")
        self.assertEqual(asset.name, "shop/buy")
        self.assertIs(pack.find("function", "shop/buy"), asset)
        # 名字会被规范化（空格 → 下划线，非法字符 → _）
        other = pack.add_asset("function", "Bad Name!", "say x")
        self.assertEqual(other.name, "Bad_Name_")
        self.assertTrue(pack.remove_asset("function", "shop/buy"))
        self.assertIsNone(pack.find("function", "shop/buy"))
        self.assertFalse(pack.remove_asset("function", "nope"))

    def test_add_existing_updates_text(self) -> None:
        pack = self._pack()
        pack.add_asset("function", "main", "a")
        pack.add_asset("function", "main", "b")
        self.assertEqual(len(pack.by_kind("function")), 1)
        self.assertEqual(pack.find("function", "main").text, "b")

    def test_strips_extension_from_name(self) -> None:
        pack = self._pack()
        self.assertEqual(pack.add_asset("function", "main.mcfunction").name,
                         "main")
        self.assertEqual(pack.add_asset("advancement", "a.json").name, "a")

    def test_summary_and_stats(self) -> None:
        pack = self._pack()
        pack.add_asset("function", "main", "say a\nsay b")
        pack.add_asset("advancement", "first", advancement_template())
        summary = pack.summary()
        self.assertIn("demo", summary)
        self.assertIn("function 1", summary)
        stats = pack.stats()
        self.assertEqual(stats["functions"], 1)
        self.assertEqual(stats["commands"], 2)
        self.assertEqual(stats["advancements"], 1)

    def test_empty_summary(self) -> None:
        self.assertIn("空", self._pack().summary())

    def test_asset_helpers(self) -> None:
        asset = PackAsset(kind="function", name="main",
                          text="# 注释\nsay a\n\nsay b\n")
        self.assertEqual(asset.line_count(), 2)          # 注释与空行不算
        self.assertEqual(asset.commands(), ["say a", "say b"])
        asset2 = PackAsset(kind="advancement", name="x", text='{"a":1}')
        self.assertEqual(asset2.to_json(), {"a": 1})
        self.assertEqual(asset2.suffix, ".json")
        asset2.set_json({"b": 2})
        self.assertEqual(asset2.to_json(), {"b": 2})

    def test_commands_strip_leading_slash(self) -> None:
        asset = PackAsset(kind="function", name="m", text="/say hi\n# c")
        self.assertEqual(asset.commands(), ["say hi"])


class TestPackSaveLoad(_PackCase):
    def test_save_writes_expected_files(self) -> None:
        pack = self._pack()
        pack.add_from_commands("main", ["say hi", "give @s minecraft:stone"])
        pack.add_asset("advancement", "first", advancement_template("第一步"))
        pack.add_asset("loot_table", "reward", loot_table_template())
        pack.add_asset("predicate", "is_zombie", predicate_template())
        pack.auto_tag_load()
        written = pack.save()
        names = {path.name for path in written}
        self.assertIn("pack.mcmeta", names)
        self.assertIn("main.mcfunction", names)
        self.assertIn("first.json", names)
        self.assertIn("reward.json", names)
        self.assertIn("is_zombie.json", names)
        self.assertIn("load.json", names)
        meta = json.loads((self.root / "pack.mcmeta").read_text("utf-8"))
        self.assertEqual(meta["pack"]["pack_format"], 48)

    def test_round_trip(self) -> None:
        pack = self._pack()
        pack.add_from_commands("shop/buy", ["say buy"])
        pack.add_asset("advancement", "first", advancement_template("A"))
        pack.auto_tag_load()
        pack.add_to_tag("minecraft:tick", "demo:shop/buy")
        pack.save()

        back = AuthoringPack.load(self.root)
        self.assertEqual(back.namespace, "demo")
        self.assertEqual(back.layout, LAYOUT_SINGULAR)
        self.assertEqual(len(back.by_kind("function")), 1)
        self.assertEqual(back.find("function", "shop/buy").commands(),
                         ["say buy"])
        self.assertEqual(back.tags["minecraft:load"], ["demo:shop/buy"])
        self.assertEqual(back.tags["minecraft:tick"], ["demo:shop/buy"])
        self.assertEqual(back.validate(), [])

    def test_plural_layout_round_trip(self) -> None:
        pack = self._pack()
        pack.layout = LAYOUT_PLURAL
        pack.add_from_commands("main", ["say hi"])
        pack.save()
        self.assertTrue((self.root / "data" / "demo" / "functions"
                         / "main.mcfunction").is_file())
        back = AuthoringPack.load(self.root)
        self.assertEqual(back.layout, LAYOUT_PLURAL)
        self.assertEqual(len(back.by_kind("function")), 1)

    def test_layout_switch_removes_old_file(self) -> None:
        pack = self._pack()
        pack.add_from_commands("main", ["say hi"])
        pack.save()
        pack.layout = LAYOUT_PLURAL
        pack.save()
        self.assertFalse((self.root / "data" / "demo" / "function"
                          / "main.mcfunction").exists())
        self.assertTrue((self.root / "data" / "demo" / "functions"
                         / "main.mcfunction").is_file())

    def test_json_asset_pretty_printed(self) -> None:
        pack = self._pack()
        pack.add_asset("advancement", "x", '{"a":1}')
        pack.save()
        text = (self.root / "data" / "demo" / "advancement"
                / "x.json").read_text("utf-8")
        self.assertIn("\n", text)
        self.assertEqual(json.loads(text), {"a": 1})

    def test_invalid_json_written_as_is(self) -> None:
        pack = self._pack()
        pack.add_asset("advancement", "x", "{not json")
        pack.save()
        self.assertEqual(
            (self.root / "data" / "demo" / "advancement"
             / "x.json").read_text("utf-8"), "{not json")

    def test_mcfunction_gets_trailing_newline(self) -> None:
        pack = self._pack()
        pack.add_asset("function", "x", "say hi")
        pack.save()
        self.assertTrue(
            (self.root / "data" / "demo" / "function" / "x.mcfunction")
            .read_text("utf-8").endswith("\n"))

    def test_load_missing_pack_gives_empty_engine(self) -> None:
        pack = AuthoringPack.load(self.tmp / "nothing")
        self.assertEqual(pack.assets, [])
        self.assertIn("空", pack.summary())

    def test_load_picks_first_namespace(self) -> None:
        pack = self._pack("alpha")
        pack.add_from_commands("main", ["say a"])
        pack.save()
        self.assertEqual(AuthoringPack.load(self.root).namespace, "alpha")
        self.assertEqual(AuthoringPack.load(self.root, "other").namespace,
                         "other")

    def test_load_reads_description_and_format(self) -> None:
        pack = self._pack()
        pack.description = "自定义说明"
        pack.pack_format = 41
        pack.save()
        back = AuthoringPack.load(self.root)
        self.assertEqual(back.description, "自定义说明")
        self.assertEqual(back.pack_format, 41)

    def test_save_changed_only_skips_clean(self) -> None:
        pack = self._pack()
        pack.add_from_commands("a", ["say a"])
        pack.add_from_commands("b", ["say b"])
        pack.save()
        for asset in pack.assets:
            asset.dirty = False
        pack.find("function", "a").text = "say changed"
        pack.find("function", "a").dirty = True
        written = pack.save(changed_only=True)
        names = [path.name for path in written]
        self.assertIn("a.mcfunction", names)
        self.assertNotIn("b.mcfunction", names)

    def test_pack_mcmeta_text(self) -> None:
        pack = self._pack()
        payload = json.loads(pack.pack_mcmeta_text())
        self.assertEqual(payload["pack"]["description"], pack.description)

    def test_save_is_atomic_no_tmp_left(self) -> None:
        pack = self._pack()
        pack.add_from_commands("main", ["say hi"])
        pack.save()
        leftovers = list(self.root.rglob("*.tmp"))
        self.assertEqual(leftovers, [])


class TestPackTags(_PackCase):
    def test_auto_tag_load_and_tick(self) -> None:
        pack = self._pack()
        pack.add_from_commands("a", ["say a"])
        pack.add_from_commands("b", ["say b"])
        pack.auto_tag_load()
        pack.auto_tag_tick(["a"])
        self.assertEqual(pack.tag_entries("minecraft:load"),
                         ["demo:a", "demo:b"])
        self.assertEqual(pack.tag_entries("minecraft:tick"), ["demo:a"])

    def test_add_to_tag_is_idempotent(self) -> None:
        pack = self._pack()
        pack.add_to_tag("minecraft:load", "demo:a")
        pack.add_to_tag("minecraft:load", "demo:a")
        self.assertEqual(len(pack.tag_entries("minecraft:load")), 1)

    def test_tag_namespace_defaults(self) -> None:
        pack = self._pack()
        pack.add_to_tag("my_tag", "demo:a")
        pack.add_to_tag("minecraft:load", "demo:a")
        self.assertIn("demo:my_tag", pack.tags)
        self.assertIn("minecraft:load", pack.tags)

    def test_tag_path_uses_namespace(self) -> None:
        pack = self._pack()
        path = pack.tag_path("minecraft:load")
        self.assertIn("minecraft", str(path))
        self.assertTrue(str(path).endswith("load" + os.sep + ".."
                                           if False else "load.json")
                        or path.name == "load.json")

    def test_common_tags_constant(self) -> None:
        self.assertIn("minecraft:load", COMMON_TAGS)
        self.assertIn("minecraft:tick", COMMON_TAGS)

    def test_set_tag_replaces(self) -> None:
        pack = self._pack()
        pack.set_tag("minecraft:load", ["demo:a", "demo:b"])
        self.assertEqual(pack.tag_entries("minecraft:load"),
                         ["demo:a", "demo:b"])


class TestPackValidate(_PackCase):
    def test_clean_pack_passes(self) -> None:
        pack = self._pack()
        pack.add_from_commands("main", ["say hi"])
        pack.auto_tag_load()
        self.assertEqual(pack.validate(), [])

    def test_bad_namespace_reported(self) -> None:
        pack = self._pack("Bad Namespace!")
        problems = pack.validate()
        self.assertTrue(any("命名空间" in item for item in problems))

    def test_bad_json_reported(self) -> None:
        pack = self._pack()
        pack.add_asset("advancement", "x", '{"a":')
        problems = pack.validate()
        self.assertTrue(any("JSON" in item for item in problems))

    def test_dangling_tag_reference_reported(self) -> None:
        pack = self._pack()
        pack.add_from_commands("main", ["say hi"])
        pack.add_to_tag("minecraft:load", "demo:ghost")
        problems = pack.validate()
        self.assertTrue(any("不存在的函数" in item for item in problems))

    def test_external_tag_entry_ignored(self) -> None:
        pack = self._pack()
        pack.add_to_tag("minecraft:load", "other:thing")
        self.assertEqual(pack.validate(), [])


class TestTemplates(unittest.TestCase):
    def test_advancement_template_is_valid_json(self) -> None:
        payload = json.loads(advancement_template("标题", "说明"))
        self.assertEqual(payload["display"]["title"]["text"], "标题")
        self.assertIn("criteria", payload)

    def test_loot_table_template(self) -> None:
        payload = json.loads(loot_table_template("minecraft:apple", 3, 2))
        self.assertEqual(payload["pools"][0]["rolls"], 2)
        entry = payload["pools"][0]["entries"][0]
        self.assertEqual(entry["name"], "minecraft:apple")
        self.assertEqual(entry["functions"][0]["count"], 3)

    def test_predicate_template(self) -> None:
        payload = json.loads(predicate_template("minecraft:pig"))
        self.assertEqual(payload["predicate"]["type"], "minecraft:pig")

    def test_function_templates(self) -> None:
        self.assertIn("say hello", function_template("main"))
        self.assertIn("doDaylightCycle", function_template("load"))
        self.assertIn("tick", function_template("tick"))
        self.assertIn("clear", function_template("shop"))
        self.assertIn("iron_helmet", function_template("kit"))
        # 未知 key 退回主模板
        self.assertEqual(function_template("nope"), function_template("main"))

    def test_pack_formats_table(self) -> None:
        self.assertEqual(PACK_FORMATS["1.21.1"], 48)
        self.assertEqual(PACK_FORMATS["1.20.4"], 26)

    def test_asset_kinds_table(self) -> None:
        for kind in ("function", "advancement", "loot_table", "predicate",
                     "tag/function"):
            self.assertIn(kind, ASSET_KINDS)
        self.assertEqual(ASSET_KINDS["function"][2], ".mcfunction")
        self.assertEqual(ASSET_KINDS["advancement"][2], ".json")

    def test_kind_from_suffix(self) -> None:
        self.assertEqual(kind_from_suffix("data/x/function/a.mcfunction"),
                         "function")
        self.assertEqual(kind_from_suffix("data/x/functions/a.mcfunction"),
                         "function")
        self.assertEqual(kind_from_suffix("data/x/advancement/y.json"),
                         "advancement")
        self.assertEqual(kind_from_suffix("data/x/loot_tables/z.json"),
                         "loot_table")
        self.assertIsNone(kind_from_suffix("notes.txt"))


class TestImportPack(_PackCase):
    def test_import_copies_and_loads(self) -> None:
        source = self.tmp / "source"
        pack = AuthoringPack(root=source, namespace="orig")
        pack.add_from_commands("main", ["say imported"])
        pack.auto_tag_load()
        pack.save()

        target = self.tmp / "target"
        imported = import_pack(source, target)
        self.assertEqual(imported.root, target)
        self.assertEqual(imported.namespace, "orig")
        self.assertTrue(imported.by_kind("function"))
        self.assertEqual(imported.find("function", "main").commands(),
                         ["say imported"])
        self.assertTrue((target / "pack.mcmeta").is_file())

    def test_import_replaces_existing_target(self) -> None:
        source = self.tmp / "s2"
        AuthoringPack(root=source, namespace="s2").save()
        target = self.tmp / "t2"
        target.mkdir()
        (target / "junk.txt").write_text("x", encoding="utf-8")
        import_pack(source, target)
        self.assertFalse((target / "junk.txt").exists())


if __name__ == "__main__":
    unittest.main()
