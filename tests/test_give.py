"""`/give` 物品组件引擎与 Builder 的深度测试。

覆盖：组件切分/解析/生成、ID 规范化、自动补命名空间、数量位置、
移除组件、反向解析宽容度、校验分级、处方数据完整性、CLI 入口。
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest

import tests  # noqa: F401  （导入即设置环境变量）
from app.core.builders import GiveBuilder, parse_give_command
from app.core.data import store
from app.core.item_components import (
    ComponentSpec, MODE_REMOVE, MODE_SET, component_defs, component_list,
    component_preview, dedupe_components, find_component_start,
    grouped_components, is_namespaced, max_stack_size, normalize_id,
    parse_components, serialize_components, short_id, split_top_level,
    validate_components, wrap_bare_value,
)


class TestSplitAndParse(unittest.TestCase):
    def test_split_top_level_respects_quotes_and_brackets(self) -> None:
        self.assertEqual(
            split_top_level('a="x,y",b={c:[1,2]},d'),
            ['a="x,y"', 'b={c:[1,2]}', 'd'])
        self.assertEqual(split_top_level('a="he said \\"hi\\", ok",b=1'),
                         ['a="he said \\"hi\\", ok"', 'b=1'])

    def test_parse_set_and_remove(self) -> None:
        specs = parse_components('[minecraft:lore=["a"],!minecraft:damage]')
        self.assertEqual([(s.id, s.mode) for s in specs],
                         [("minecraft:lore", MODE_SET),
                          ("minecraft:damage", MODE_REMOVE)])
        self.assertEqual(specs[1].value, "")

    def test_parse_plain_id_without_equals(self) -> None:
        specs = parse_components("[minecraft:unbreakable]")
        self.assertEqual(specs[0].id, "minecraft:unbreakable")
        self.assertEqual(specs[0].to_text(), "minecraft:unbreakable")

    def test_parse_tolerates_missing_brackets_and_spaces(self) -> None:
        specs = parse_components('  a=1 , b=2  ')
        self.assertEqual([s.to_text() for s in specs], ["minecraft:a=1", "minecraft:b=2"])

    def test_values_with_commas_and_spaces_survive(self) -> None:
        block = '[minecraft:lore=["line1, line2","x"],minecraft:custom_name="stone head"]'
        specs = parse_components(block)
        self.assertEqual(specs[0].value, '["line1, line2","x"]')
        self.assertEqual(specs[1].value, '"stone head"')
        self.assertEqual(serialize_components(specs), block)

    def test_empty_parse(self) -> None:
        self.assertEqual(parse_components(""), [])
        self.assertEqual(parse_components("[]"), [])
        self.assertEqual(serialize_components([]), "")

    def test_dedupe_keeps_last_value_and_first_position(self) -> None:
        specs = parse_components("[a=1,b=2,a=3]")
        merged = dedupe_components(specs)
        self.assertEqual([s.id for s in merged], ["minecraft:a", "minecraft:b"])
        self.assertEqual(merged[0].value, "3")


class TestIdsAndHelpers(unittest.TestCase):
    def test_normalize_id(self) -> None:
        self.assertEqual(normalize_id("stone"), "minecraft:stone")
        self.assertEqual(normalize_id(" minecraft:stone "), "minecraft:stone")
        self.assertEqual(normalize_id("mypack:item"), "mypack:item")
        self.assertEqual(normalize_id(""), "")

    def test_is_namespaced(self) -> None:
        self.assertTrue(is_namespaced("minecraft:stone"))
        self.assertTrue(is_namespaced("a_b:c/d"))
        self.assertFalse(is_namespaced("stone"))
        self.assertFalse(is_namespaced("minecraft:"))

    def test_short_id(self) -> None:
        self.assertEqual(short_id("minecraft:diamond_sword"), "diamond_sword")
        self.assertEqual(short_id("mypack:thing"), "mypack:thing")

    def test_max_stack_size(self) -> None:
        self.assertEqual(max_stack_size("minecraft:stone"), 64)
        self.assertEqual(max_stack_size("minecraft:potion"), 1)
        self.assertEqual(max_stack_size("minecraft:diamond_sword"), 1)
        self.assertEqual(max_stack_size("minecraft:written_book"), 16)

    def test_find_component_start_skips_selector(self) -> None:
        self.assertEqual(find_component_start("stone[damage=1]"), 5)
        self.assertEqual(
            find_component_start("@e[nbt={a:1}] stone[d=1]"), 19)
        self.assertEqual(find_component_start("@p stone"), -1)
        self.assertEqual(find_component_start("@a{foo:1} stone"), -1)
        self.assertEqual(find_component_start("@a{foo:1} stone[x=1]"), 15)

    def test_wrap_bare_value(self) -> None:
        self.assertEqual(wrap_bare_value("重命名物品"), '"重命名物品"')
        self.assertEqual(wrap_bare_value("{a:1}"), "{a:1}")
        self.assertEqual(wrap_bare_value('"already"'), '"already"')
        self.assertEqual(wrap_bare_value("123"), "123")
        self.assertEqual(wrap_bare_value("minecraft:stone"), "minecraft:stone")


class TestComponentSpec(unittest.TestCase):
    def test_to_text(self) -> None:
        self.assertEqual(ComponentSpec("lore", '["a"]').to_text(),
                         'minecraft:lore=["a"]')
        self.assertEqual(ComponentSpec("unbreakable", "").to_text(),
                         "minecraft:unbreakable")
        self.assertEqual(ComponentSpec("damage", "5", MODE_REMOVE).to_text(),
                         "!minecraft:damage")

    def test_remove_mode_clears_value(self) -> None:
        spec = ComponentSpec("damage", "5", MODE_REMOVE)
        self.assertEqual(spec.value, "")

    def test_dict_roundtrip_and_clone(self) -> None:
        spec = ComponentSpec("custom_name", '"A"')
        again = ComponentSpec.from_dict(spec.to_dict())
        self.assertEqual(again.to_text(), spec.to_text())
        clone = ComponentSpec.from_dict(spec)
        self.assertIsNot(clone, spec)
        self.assertEqual(clone.to_text(), spec.to_text())
        with self.assertRaises(ValueError):
            ComponentSpec.from_dict("not-a-dict")


class TestValidation(unittest.TestCase):
    def test_unknown_component_warns(self) -> None:
        issues = validate_components(parse_components("[minecraft:nope=1]"),
                                     item="minecraft:stone")
        self.assertTrue(any("未知组件" in m for _l, m in issues))

    def test_bad_snbt_is_error(self) -> None:
        issues = validate_components(parse_components("[minecraft:lore=[1,]x]"))
        self.assertTrue(any(level == "error" for level, _m in issues))

    def test_int_component_rejects_text(self) -> None:
        issues = validate_components(
            parse_components("[minecraft:max_stack_size=abc]"))
        self.assertTrue(any(level == "error" for level, _m in issues))

    def test_bool_component_accepts_1b(self) -> None:
        issues = validate_components(
            parse_components("[minecraft:unbreakable=1b]"))
        self.assertEqual(issues, [])

    def test_enchantments_without_levels_warns(self) -> None:
        issues = validate_components(parse_components(
            '[minecraft:enchantments={"minecraft:sharpness":5}]'))
        self.assertTrue(any("levels" in m for _l, m in issues))
        ok = validate_components(parse_components(
            '[minecraft:enchantments={"levels":{"minecraft:sharpness":5}}]'))
        self.assertEqual([m for _l, m in ok if "levels" in m], [])

    def test_attribute_modifiers_name_field_warns(self) -> None:
        issues = validate_components(parse_components(
            '[minecraft:attribute_modifiers=[{"type":"minecraft:attack_damage",'
            '"amount":1.0,"operation":"add_value","slot":"mainhand","name":"x"}]]'))
        self.assertTrue(any("id 字段" in m for _l, m in issues))

    def test_duplicate_component_warns(self) -> None:
        issues = validate_components(parse_components("[lore=[],lore=[1]]"))
        self.assertTrue(any("重复" in m for _l, m in issues))

    def test_applies_warns_on_wrong_item(self) -> None:
        issues = validate_components(parse_components("[minecraft:trim={}]"),
                                     item="minecraft:stick")
        self.assertTrue(any("不适用" in m for _l, m in issues))
        ok = validate_components(parse_components("[minecraft:trim={}]"),
                                 item="minecraft:diamond_helmet")
        self.assertEqual([m for _l, m in ok if "不适用" in m], [])

    def test_preview_text(self) -> None:
        self.assertIn("锋利", component_preview(ComponentSpec(
            "enchantments", '{"levels":{"minecraft:sharpness":5}}')))
        self.assertIn("移除", component_preview(
            ComponentSpec("lore", mode=MODE_REMOVE)))


class TestGiveBuilder(unittest.TestCase):
    def test_auto_namespace_item(self) -> None:
        b = GiveBuilder()
        b.item = "stone"
        self.assertEqual(b.build(), "give @p minecraft:stone")

    def test_build_with_count_and_components(self) -> None:
        b = GiveBuilder()
        b.target = "@a"
        b.item = "minecraft:diamond_sword"
        b.use_count = True
        b.count = 2
        b.add_component("minecraft:custom_name", '"剑"')
        b.add_component("minecraft:damage", mode=MODE_REMOVE)
        self.assertEqual(
            b.build(),
            'give @a minecraft:diamond_sword 2 '
            '[minecraft:custom_name="剑",!minecraft:damage]')

    def test_add_component_replaces_same_id(self) -> None:
        b = GiveBuilder()
        b.add_component("minecraft:damage", "5")
        b.add_component("damage", "9")
        self.assertEqual(len(b.components), 1)
        self.assertEqual(b.components[0].value, "9")

    def test_remove_and_move_component(self) -> None:
        b = GiveBuilder()
        b.add_component("minecraft:lore", '["a"]')
        b.add_component("minecraft:damage", "1")
        self.assertEqual(b.move_component(0, 1), 1)
        self.assertEqual([c.id for c in b.components],
                         ["minecraft:damage", "minecraft:lore"])
        self.assertTrue(b.remove_component("minecraft:damage"))
        self.assertFalse(b.remove_component("minecraft:damage"))

    def test_status_summary(self) -> None:
        b = GiveBuilder()
        b.item = "diamond_sword"
        b.use_count = True
        b.count = 3
        b.add_component("minecraft:unbreakable", "1b")
        b.add_component("minecraft:damage", mode=MODE_REMOVE)
        summary = b.status_summary()
        self.assertIn("diamond_sword", summary)
        self.assertIn("×3", summary)
        self.assertIn("移除", summary)

    def test_state_roundtrip(self) -> None:
        b = GiveBuilder()
        b.target = "@e[type=zombie]"
        b.item = "minecraft:stick"
        b.use_count = True
        b.count = 7
        b.add_component("minecraft:custom_name", '"棍"')
        state = json.loads(json.dumps(b.to_dict()))
        again = GiveBuilder()
        again.load_state(state)
        self.assertEqual(again.build(), b.build())
        self.assertEqual(again.target, b.target)

    def test_load_state_tolerates_garbage(self) -> None:
        b = GiveBuilder()
        b.load_state({"components": [{"no_id": 1}, 42, None],
                      "count": "abc"})
        self.assertEqual(b.components, [])
        self.assertEqual(b.count, 1)
        b.load_state(None)
        self.assertTrue(b.build().startswith("give "))

    def test_values_bridge_for_cli(self) -> None:
        b = GiveBuilder()
        b.values["target"] = "@a"
        b.values["item"] = "stone"
        b.values["count"] = "5"
        b.values["components"] = "[minecraft:unbreakable=1b]"
        self.assertEqual(b.build(),
                         "give @a minecraft:stone 5 [minecraft:unbreakable=1b]")
        # 再生成一次不应被 values 覆盖（幂等）
        self.assertEqual(b.build(),
                         "give @a minecraft:stone 5 [minecraft:unbreakable=1b]")

    def test_values_do_not_clobber_after_load_from(self) -> None:
        b = GiveBuilder()
        b.load_from("give @p minecraft:stone[damage=3]")
        self.assertEqual(b.build(), "give @p minecraft:stone [minecraft:damage=3]")

    def test_validate_count_levels(self) -> None:
        b = GiveBuilder()
        b.item = "minecraft:diamond_sword"
        b.use_count = True
        b.count = 4
        levels = {i.level for i in b.validate()}
        self.assertIn("warn", levels)          # 不可堆叠
        b.count = 7000
        self.assertTrue(any(i.level == "error" for i in b.validate()))

    def test_validate_nbt_syntax_error(self) -> None:
        b = GiveBuilder()
        b.add_component("minecraft:lore", "[1,2")
        self.assertTrue(any(i.level == "error" for i in b.validate()))
        b.components = [ComponentSpec("minecraft:lore", "{a:}")]
        self.assertTrue(any(i.level == "error" for i in b.validate()))

    def test_hints_are_actionable(self) -> None:
        b = GiveBuilder()
        b.item = "stone"
        self.assertTrue(any("minecraft:stone" in h for h in b.hints))
        b.item = "minecraft:stone"
        b.add_component("minecraft:trim", "{}")
        self.assertTrue(any("不适用" in h for h in b.hints))

    def test_parse_rejects_item_nbt(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_give_command('give @p minecraft:stone {display:{Name:"x"}}')
        self.assertIn("迁移", str(ctx.exception))

    def test_parse_rejects_garbage(self) -> None:
        with self.assertRaises(ValueError):
            parse_give_command("say hi")
        with self.assertRaises(ValueError):
            parse_give_command("give @p")


class TestGiveRoundTrip(unittest.TestCase):
    CASES = (
        "give @p minecraft:stone",
        "give @p minecraft:stone 3",
        "give @p minecraft:stone[damage=1]",
        "give @a minecraft:diamond_sword 5 [minecraft:custom_name=\"A\"]",
        "give @e[nbt={a:1},limit=1] minecraft:stone 3 "
        "[minecraft:lore=[\"l1, l2\"],minecraft:custom_name=\"sh\"]",
        "give @p minecraft:stone[!minecraft:lore,minecraft:unbreakable=1b]",
        "give @p minecraft:potion 3 "
        "[minecraft:potion_contents={potion:\"minecraft:water\"}]",
        "give @s minecraft:player_head "
        "[minecraft:profile={name:\"Notch\"}]",
        "give @p minecraft:shulker_box 1 "
        "[minecraft:container=[{slot:0,item:{id:\"minecraft:stone\",count:1}}]]",
    )

    def test_parse_then_build_is_stable(self) -> None:
        for command in self.CASES:
            with self.subTest(command=command):
                b = GiveBuilder()
                b.load_from(command)
                once = b.build()
                again = GiveBuilder()
                again.load_from(once)
                self.assertEqual(again.build(), once,
                                 f"二次解析不稳定：{once!r} → {again.build()!r}")

    def test_count_at_tail_is_normalized_to_middle(self) -> None:
        b = GiveBuilder()
        b.load_from("give @p minecraft:potion[potion_contents={potion:\"x\"}] 3")
        self.assertTrue(b.build().endswith(
            "[minecraft:potion_contents={potion:\"x\"}]"))
        self.assertIn(" 3 ", b.build())

    def test_remove_component_needs_namespace(self) -> None:
        b = GiveBuilder()
        b.load_from("give @p minecraft:stone[!lore]")
        self.assertEqual(b.build(), "give @p minecraft:stone [!minecraft:lore]")


class TestGiveData(unittest.TestCase):
    def test_component_defs_are_complete(self) -> None:
        for entry in component_list():
            with self.subTest(component=entry.get("id")):
                self.assertTrue(is_namespaced(str(entry.get("id", ""))))
                self.assertTrue(str(entry.get("display", "")).strip())
                self.assertIn(str(entry.get("kind", "text")),
                              ("text", "bool", "int", "enum", "nbt"))

    def test_component_defaults_are_valid(self) -> None:
        for cid, definition in component_defs().items():
            if definition.get("kind") in ("bool", "int", "enum", "text"):
                continue
            with self.subTest(component=cid):
                issues = validate_components(
                    [ComponentSpec(cid, str(definition.get("default", "")))])
                errors = [m for level, m in issues if level == "error"]
                self.assertEqual(errors, [],
                                 f"{cid} 的默认值不合法：{errors}")

    def test_grouped_components_cover_all(self) -> None:
        total = sum(len(items) for _label, items in grouped_components())
        self.assertEqual(total, len(component_list()))

    def test_item_presets_are_parseable(self) -> None:
        presets = store().item_presets()
        self.assertTrue(presets, "item_presets.json 不应为空")
        for preset in presets:
            with self.subTest(preset=preset.get("id")):
                specs = parse_components(str(preset.get("components", "")))
                self.assertTrue(specs, "处方至少应有一个组件")
                self.assertTrue(normalize_id(str(preset.get("item", ""))))

    def test_preset_command_is_valid(self) -> None:
        failures: list[str] = []
        for preset in store().item_presets():
            b = GiveBuilder()
            b.item = str(preset.get("item", "minecraft:stone"))
            count = preset.get("count")
            if count is not None:
                b.use_count = True
                b.count = int(count)
            b.components = parse_components(str(preset.get("components", "")))
            errors = [str(i) for i in b.validate()
                      if getattr(i, "level", "error") == "error"]
            if errors:
                failures.append(f"{preset.get('id')}: {errors}")
        self.assertEqual(failures, [])

    def test_item_tags_cover_preset_items(self) -> None:
        known = {str(e.get("id")) for e in store().items()}
        for preset in store().item_presets():
            with self.subTest(preset=preset.get("id")):
                self.assertIn(str(preset.get("item")), known)


class TestCliGive(unittest.TestCase):
    """CLI 入口（子进程），确认 `mcgen give` 可用。"""

    def _run(self, args: list[str]):
        cmd = [sys.executable, str(tests.TEST_ROOT / "mcgen.py"), *args]
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=str(tests.TEST_ROOT))

    def test_give_basic(self) -> None:
        result = self._run(["give", "--target", "@a", "--item", "diamond_sword",
                            "--count", "2", "-c", 'minecraft:custom_name="S"'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("give @a minecraft:diamond_sword 2", result.stdout)
        self.assertIn('minecraft:custom_name="S"', result.stdout)

    def test_give_remove_and_from(self) -> None:
        result = self._run(["give", "--from", "give @p minecraft:stone[damage=5]",
                            "-r", "minecraft:damage"])
        self.assertIn("!minecraft:damage", result.stdout)

    def test_give_preset(self) -> None:
        result = self._run(["give", "--preset", "sword.sharp5"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("minecraft:enchantments", result.stdout)

    def test_give_invalid_component_fails_validation(self) -> None:
        result = self._run(["give", "--item", "stone",
                            "-c", "minecraft:max_stack_size=abc",
                            "--validate"])
        self.assertEqual(result.returncode, 1)

    def test_unknown_preset(self) -> None:
        result = self._run(["give", "--preset", "nope"])
        self.assertEqual(result.returncode, 2)

    def test_component_listing(self) -> None:
        result = self._run(["give-presets", "--search", "附魔"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("enchantments", result.stdout)


if __name__ == "__main__":
    unittest.main()
