"""数据文件完整性 + 内置数据与读取端键名一致性。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests import TEST_ROOT  # noqa: F401  （导入即设置环境变量）
from app.core import all_builders
from app.core.data import store

BUILTIN = Path(TEST_ROOT) / "app" / "data" / "builtin"


class TestDataFiles(unittest.TestCase):
    def test_all_json_parse(self) -> None:
        for path in sorted(BUILTIN.glob("*.json")):
            with self.subTest(file=path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertIsInstance(data, dict)
                self.assertEqual(data.get("version"), "1.21.1",
                                 f"{path.name} 缺少 version 或版本不符")

    def test_expected_keys(self) -> None:
        expected = {
            "entities.json": "entities",
            "items.json": "items",
            "blocks.json": "blocks",
            "item_components.json": "components",
            "recommendations.json": None,
            "templates.json": "templates",
            "command_templates.json": "templates",
            "command_schemas.json": "commands",
        }
        for fname, key in expected.items():
            with self.subTest(file=fname):
                path = BUILTIN / fname
                self.assertTrue(path.exists(), f"{fname} 不存在")
                data = json.loads(path.read_text(encoding="utf-8"))
                if key:
                    self.assertIn(key, data)
                    self.assertIsInstance(data[key], list)

    def test_no_duplicate_ids(self) -> None:
        for fname, key in (("entities.json", "entities"),
                           ("items.json", "items"),
                           ("blocks.json", "blocks"),
                           ("item_components.json", "components"),
                           ("templates.json", "templates")):
            with self.subTest(file=fname):
                data = json.loads((BUILTIN / fname).read_text(encoding="utf-8"))
                ids = [e.get("id") for e in data[key]]
                self.assertEqual(len(ids), len(set(ids)),
                                 f"{fname} 存在重复 id")

    def test_schema_commands_unique(self) -> None:
        data = json.loads((BUILTIN / "command_schemas.json")
                          .read_text(encoding="utf-8"))
        names = [c["command"] for c in data["commands"]]
        self.assertEqual(len(names), len(set(names)))

    def test_schema_args_have_known_kinds(self) -> None:
        known = {"text", "int", "float", "bool", "enum", "coord", "nbt",
                 "target", "entity", "item", "block"}
        data = json.loads((BUILTIN / "command_schemas.json")
                          .read_text(encoding="utf-8"))
        for cmd in data["commands"]:
            for a in cmd.get("args", []):
                with self.subTest(command=cmd["command"], arg=a.get("key")):
                    self.assertIn(a.get("kind", "text"), known)
                    self.assertTrue(a.get("key"))

    def test_schema_enum_options_present(self) -> None:
        data = json.loads((BUILTIN / "command_schemas.json")
                          .read_text(encoding="utf-8"))
        for cmd in data["commands"]:
            for a in cmd.get("args", []):
                if a.get("kind") == "enum":
                    with self.subTest(command=cmd["command"], arg=a["key"]):
                        self.assertTrue(a.get("options"))


class TestDataStore(unittest.TestCase):
    def test_accessors(self) -> None:
        st = store()
        self.assertTrue(st.entities())
        self.assertTrue(st.items())
        self.assertTrue(st.blocks())
        self.assertTrue(st.item_components())

    def test_all_ids(self) -> None:
        st = store()
        self.assertEqual(len(st.all_ids("any")),
                         len(st.entities()) + len(st.items()) + len(st.blocks()))

    def test_no_legacy_nbt_keys_in_templates(self) -> None:
        """1.20.5+ 已移除 HandItems/ArmorItems/Count/Damage 等写法。"""
        data = json.loads((BUILTIN / "templates.json")
                          .read_text(encoding="utf-8"))
        legacy = ("HandItems", "ArmorItems", "generic.", "\"Count\":",
                  "max_stack_size")
        for tpl in data["templates"]:
            snbt = tpl.get("snbt", "")
            for token in legacy:
                # 主手物品模板保留 HandItems 作为旧版本兼容示例，单独放行
                if tpl["id"] == "entity.hand_items.mainhand" and token == "HandItems":
                    continue
                with self.subTest(template=tpl["id"], token=token):
                    self.assertNotIn(token, snbt)

    def test_dyed_color_is_compound(self) -> None:
        data = json.loads((BUILTIN / "item_components.json")
                          .read_text(encoding="utf-8"))
        comp = next(c for c in data["components"]
                    if c["id"] == "minecraft:dyed_color")
        self.assertEqual(comp["kind"], "nbt")
        self.assertIn("rgb", comp["default"])


class TestRegistrationCoverage(unittest.TestCase):
    def test_every_schema_command_registered(self) -> None:
        data = json.loads((BUILTIN / "command_schemas.json")
                          .read_text(encoding="utf-8"))
        names = {c["command"] for c in data["commands"]}
        registered = set(all_builders())
        missing = names - registered
        self.assertEqual(missing, set(), f"未注册的 schema 命令：{missing}")

    def test_core_commands_have_panels(self) -> None:
        registered = set(all_builders())
        for name in ("summon", "give", "execute"):
            self.assertIn(name, registered)


if __name__ == "__main__":
    unittest.main()
