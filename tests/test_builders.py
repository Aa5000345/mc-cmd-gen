"""核心 Builder：注册表、生成、反向解析、校验分级。"""
from __future__ import annotations

import unittest

from tests import TEST_ROOT  # noqa: F401  （导入即设置环境变量）
from app.core import (
    BuildContext, ValidationIssue, all_builders, get_builder, load_schemas,
    split_command,
)


class TestRegistry(unittest.TestCase):
    def test_core_builders_registered(self) -> None:
        names = all_builders()
        for name in ("summon", "give", "execute"):
            self.assertIn(name, names)

    def test_schema_builders_registered(self) -> None:
        self.assertEqual(load_schemas(), 0, "schema 不应重复注册")
        self.assertGreater(len(all_builders()), 40)

    def test_split_command_keeps_nbt_whole(self) -> None:
        cmd = 'give @p minecraft:stone[minecraft:lore=["a b"]] 1'
        self.assertEqual(
            split_command(cmd),
            ["give", "@p", 'minecraft:stone[minecraft:lore=["a b"]]', "1"])
        self.assertEqual(
            split_command('tellraw @a {"text":"hello world"}'),
            ["tellraw", "@a", '{"text":"hello world"}'])


class TestSummon(unittest.TestCase):
    def test_build_default(self) -> None:
        b = get_builder("summon")()
        self.assertEqual(b.build(), "summon minecraft:zombie")

    def test_build_with_pos_and_nbt(self) -> None:
        b = get_builder("summon")()
        b.entity = "minecraft:pig"
        b.use_pos = True
        b.nbt = "{IsBaby:1b}"
        self.assertEqual(b.build(),
                         "summon minecraft:pig ~ ~ ~ {IsBaby:1b}")

    def test_roundtrip(self) -> None:
        cmd = "summon minecraft:zombie 10 64 10 {IsBaby:0b}"
        b = get_builder("summon")()
        b.load_from(cmd)
        self.assertEqual(b.build(), cmd)

    def test_roundtrip_without_pos(self) -> None:
        cmd = "summon minecraft:zombie {Tags:[\"a\"]}"
        b = get_builder("summon")()
        b.load_from(cmd)
        self.assertEqual(b.build(), cmd)

    def test_validate_returns_levelled_issues(self) -> None:
        b = get_builder("summon")()
        b.entity = "zombie"                 # 缺命名空间
        issues = b.validate(BuildContext())
        self.assertTrue(issues)
        self.assertTrue(all(isinstance(i, ValidationIssue) for i in issues))
        self.assertEqual(issues[0].level, "error")


class TestGive(unittest.TestCase):
    def test_build_with_components(self) -> None:
        b = get_builder("give")()
        b.target = "@a"
        b.item = "minecraft:diamond_sword"
        b.use_count = True
        b.count = 2
        b.components = [{"id": "minecraft:custom_name",
                         "mode": "set", "value": '"剑"'}]
        self.assertEqual(
            b.build(),
            'give @a minecraft:diamond_sword 2 [minecraft:custom_name="剑"]')

    def test_component_remove_mode(self) -> None:
        b = get_builder("give")()
        b.components = [{"id": "minecraft:lore", "mode": "remove", "value": ""}]
        self.assertIn("[!minecraft:lore]", b.build())

    def test_roundtrip_with_components(self) -> None:
        cmd = 'give @p minecraft:stone 3 [minecraft:custom_name="石头"]'
        b = get_builder("give")()
        b.load_from(cmd)
        self.assertEqual(b.build(), cmd)

    def test_validate_flags_missing_namespace(self) -> None:
        b = get_builder("give")()
        b.item = "stone"
        issues = b.validate(BuildContext())
        # 缺命名空间不再报错：物品 ID 会自动补全为 minecraft:stone，只提示一下
        self.assertFalse(any(i.level == "error" for i in issues))
        self.assertTrue(any("minecraft:stone" in str(i) for i in issues))
        self.assertEqual(b.build(), "give @p minecraft:stone")

    def test_auto_namespace_item_and_components(self) -> None:
        b = get_builder("give")()
        b.item = "diamond_sword"
        b.add_component("lore", '["hi"]')
        self.assertEqual(b.build(),
                         'give @p minecraft:diamond_sword [minecraft:lore=["hi"]]')

    def test_invalid_item_id_is_error(self) -> None:
        b = get_builder("give")()
        b.item = "minecraft:"
        issues = b.validate(BuildContext())
        self.assertTrue(any(i.level == "error" for i in issues))


class TestExecute(unittest.TestCase):
    def test_build_chain(self) -> None:
        b = get_builder("execute")()
        from app.core.builders import ExecuteNode
        b.nodes = [ExecuteNode("as", {"target": "@a"}),
                   ExecuteNode("at", {"target": "@s"})]
        b.run_command = "say hi"
        self.assertEqual(b.build(), "execute as @a at @s run say hi")

    def test_roundtrip(self) -> None:
        cmd = "execute as @a at @s run say hi"
        b = get_builder("execute")()
        b.load_from(cmd)
        self.assertEqual(b.build(), cmd)

    def test_roundtrip_condition_and_store(self) -> None:
        cmd = ("execute if block ~ ~ ~ minecraft:stone "
               "store result score @s obj run say hi")
        b = get_builder("execute")()
        b.load_from(cmd)
        self.assertEqual(b.build(), cmd)

    def test_load_from_rejects_other_command(self) -> None:
        b = get_builder("execute")()
        with self.assertRaises(ValueError):
            b.load_from("say hi")


class TestValidateRules(unittest.TestCase):
    """回归：rules.py 曾经因为模块遮蔽而永远不生效。"""

    def test_old_enchant_nbt_warns(self) -> None:
        from app.core.rules import validate_text
        hits = validate_text(
            'give @p minecraft:book[minecraft:custom_data={Enchantments:[1]}]')
        self.assertTrue(any("enchantments" in m for _l, m in hits))

    def test_tp_alias_info(self) -> None:
        from app.core.rules import validate_text
        hits = validate_text("tp @p 1 2 3")
        self.assertTrue(any(level == "info" for level, _m in hits))

    def test_generic_attribute_warns(self) -> None:
        from app.core.rules import validate_text
        hits = validate_text("summon minecraft:zombie ~ ~ ~ "
                             "{attributes:[{id:\"minecraft:generic.max_health\"}]}")
        self.assertTrue(any("generic" in m for _l, m in hits))

    def test_schema_validate_includes_rules(self) -> None:
        b = get_builder("setblock")()
        b.values["pos"] = "1 2 3"
        b.values["block"] = "minecraft:stone"
        b.values["mode"] = "replace"
        self.assertEqual([str(i) for i in b.validate()], [])


if __name__ == "__main__":
    unittest.main()
