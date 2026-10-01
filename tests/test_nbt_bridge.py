"""存档 NBT → 命令的桥梁，以及设置 / 专有名词翻译的测试。"""
from __future__ import annotations

import unittest

import tests  # noqa: F401  （导入即重定向应用数据目录）

from app.core.nbt_bridge import (                                    # noqa: E402
    ItemParseError, ItemStack, aggregate_items, apply_to_give_panel,
    child, collect_items, component_value_snbt, enchant_display,
    entity_display, find_entities, find_item_containers, item_display,
    looks_like_entity, looks_like_item, normalize_item_id, parse_entity,
    parse_item, quote_snbt, roman, stack_to_command, stack_to_data_command,
    stack_to_editor_state, stack_to_snbt, total_count, unique_kinds,
)
from app.core.snbt import SnbtParser                                 # noqa: E402

#: 1.21 存档里玩家背包的一小段（含 1.20.5+ 的 components 写法与旧 tag 写法）
PLAYER_NBT = """
{Data:{Player:{
  Inventory:[
    {Slot:0b,id:"minecraft:diamond_sword",count:1,components:{
        "minecraft:custom_name":'{"text":"神剑"}',
        "minecraft:enchantments":{levels:{"minecraft:sharpness":5,
                                          "minecraft:mending":1}},
        "minecraft:unbreakable":{}}},
    {Slot:1b,id:"minecraft:stone",count:64},
    {Slot:2b,id:"minecraft:golden_apple",Count:3b,tag:{
        display:{Name:'{"text":"金苹果"}'},
        Enchantments:[{id:"minecraft:sharpness",lvl:2s}]}}
  ],
  EnderItems:[{Slot:0b,id:"minecraft:elytra",count:1}],
  Health:20.0f,
  XpLevel:30
}}}
"""

ENTITY_NBT = """
{Entities:[
  {id:"minecraft:zombie",Health:20.0f,Tags:["boss","elite"],
   CustomName:'{"text":"僵尸王"}',
   HandItems:[{id:"minecraft:iron_sword",count:1},{}],
   Pos:[1.0d,64.0d,2.0d]},
  {id:"minecraft:arrow",damage:2.0d}
]}
"""


class TestItemParsing(unittest.TestCase):
    def setUp(self) -> None:
        self.root = SnbtParser(PLAYER_NBT).parse()
        self.inventory = (self.root.get_child("Data").get_child("Player")
                          .get_child("Inventory"))

    def test_looks_like_item(self) -> None:
        self.assertTrue(looks_like_item(self.inventory.children[0]))
        self.assertTrue(looks_like_item(self.inventory.children[1]))
        self.assertFalse(looks_like_item(self.root))
        self.assertFalse(looks_like_item(
            SnbtParser('{A:1}').parse()))

    def test_parse_new_style_item(self) -> None:
        stack = parse_item(self.inventory.children[0], "Inv[0]")
        self.assertEqual(stack.clean_id(), "minecraft:diamond_sword")
        self.assertEqual(stack.count, 1)
        self.assertEqual(stack.slot, 0)
        self.assertEqual(stack.display_name, "神剑")
        self.assertEqual(stack.enchantments,
                         [("minecraft:sharpness", 5),
                          ("minecraft:mending", 1)])
        self.assertIn("minecraft:unbreakable", stack.components)
        self.assertIn("神剑", stack.display())
        self.assertIn("锋利", stack.summary())
        self.assertIn("V", stack.summary())

    def test_parse_plain_item(self) -> None:
        stack = parse_item(self.inventory.children[1])
        self.assertEqual(stack.clean_id(), "minecraft:stone")
        self.assertEqual(stack.count, 64)
        self.assertEqual(stack.components, {})
        self.assertEqual(stack.display(), item_display("minecraft:stone"))

    def test_parse_legacy_tag_item(self) -> None:
        stack = parse_item(self.inventory.children[2])
        self.assertEqual(stack.count, 3)                 # Count:3b
        self.assertEqual(stack.display_name, "金苹果")
        # 旧 tag 里的 Enchantments 映射成 1.21 的 enchantments 组件
        self.assertIn("minecraft:enchantments", stack.components)
        self.assertEqual(stack.enchantments, [("minecraft:sharpness", 2)])

    def test_give_argument_and_command(self) -> None:
        stack = parse_item(self.inventory.children[1])
        self.assertEqual(stack.give_argument(), "minecraft:stone")
        self.assertEqual(stack_to_command(stack),
                         "give @p minecraft:stone 64")
        sword = parse_item(self.inventory.children[0])
        argument = sword.give_argument()
        self.assertTrue(argument.startswith("minecraft:diamond_sword["))
        self.assertIn("custom_name=", argument)
        self.assertIn("enchantments=", argument)
        self.assertIn("unbreakable", argument)

    def test_give_argument_roundtrips_through_parser(self) -> None:
        """转换出的物品参数必须能被 SNBT 解析（面板要用它）。"""
        from app.core.item_components import parse_components
        for index in range(3):
            stack = parse_item(self.inventory.children[index])
            specs = parse_components(stack.components_snbt())
            parsed = {spec.id: spec.value for spec in specs}
            self.assertEqual(set(parsed), set(stack.components),
                             stack.give_argument())

    def test_snbt_and_data_command(self) -> None:
        stack = parse_item(self.inventory.children[1], "Data.Player.Inventory[1]")
        snbt = stack_to_snbt(stack)
        self.assertIn('id:"minecraft:stone"', snbt)
        self.assertIn("count:64", snbt)
        command = stack_to_data_command(stack, "Data.Player.Inventory")
        self.assertIn("data modify Data.Player.Inventory", command)
        self.assertIn("Slot:1b", command)
        self.assertIn("set value", command)

    def test_editor_state_and_panel_handoff(self) -> None:
        stack = parse_item(self.inventory.children[0])
        state = stack_to_editor_state(stack, "@a")
        self.assertEqual(state["item"], "minecraft:diamond_sword")
        self.assertEqual(state["count"], 1)
        self.assertEqual(state["target"], "@a")
        self.assertTrue(state["components"])

        class FakeBuilder:
            def __init__(self):
                self.components = []

            def add_component(self, cid, value="", mode="set"):
                self.components.append((cid, value))

        class FakePanel:
            def __init__(self):
                self.builder = FakeBuilder()
                self.reloaded = 0

            def reload_from_builder(self):
                self.reloaded += 1

        panel = FakePanel()
        self.assertTrue(apply_to_give_panel(panel, stack, "@a"))
        self.assertEqual(panel.builder.item, "minecraft:diamond_sword")
        self.assertEqual(panel.builder.count, 1)
        self.assertEqual(panel.reloaded, 1)
        ids = [cid for cid, _ in panel.builder.components]
        self.assertIn("minecraft:custom_name", ids)


class TestContainers(unittest.TestCase):
    def setUp(self) -> None:
        self.root = SnbtParser(PLAYER_NBT).parse()

    def test_find_containers(self) -> None:
        containers = find_item_containers(self.root)
        paths = [c.path for c in containers]
        self.assertIn("Data.Player.Inventory", paths)
        self.assertIn("Data.Player.EnderItems", paths)

    def test_collect_and_aggregate(self) -> None:
        items = collect_items(self.root, source="level.dat")
        self.assertEqual(len(items), 4)
        self.assertEqual(total_count(items), 1 + 64 + 3 + 1)
        self.assertEqual(unique_kinds(items), 4)
        # 两把剑（同名同组件）会合并
        sword = parse_item(
            SnbtParser('{id:"minecraft:stone",count:1}').parse())
        merged = aggregate_items([sword, parse_item(
            SnbtParser('{id:"minecraft:stone",count:5}').parse())])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].count, 6)
        self.assertIsNone(merged[0].slot)

    def test_container_stacks_carry_paths(self) -> None:
        containers = {c.path: c for c in find_item_containers(self.root)}
        inv = containers["Data.Player.Inventory"]
        stacks = inv.stacks()
        self.assertEqual(len(stacks), 3)
        self.assertEqual(stacks[0].path, "Data.Player.Inventory[0]")


class TestEntities(unittest.TestCase):
    def setUp(self) -> None:
        self.root = SnbtParser(ENTITY_NBT).parse()

    def test_find_and_parse(self) -> None:
        entities = find_entities(self.root)
        zombies = [e for e in entities if e.entity_id == "minecraft:zombie"]
        self.assertEqual(len(zombies), 1)
        arrow = [e for e in entities if e.entity_id == "minecraft:arrow"]
        self.assertEqual(len(arrow), 1)             # 投掷物也是实体
        zombie = zombies[0]
        self.assertEqual(zombie.entity_id, "minecraft:zombie")
        self.assertEqual(zombie.custom_name, "僵尸王")
        self.assertEqual(zombie.tags, ["boss", "elite"])
        self.assertAlmostEqual(zombie.health, 20.0)
        self.assertIn("僵尸王", zombie.display())
        # NBT 里不该再带 id（/summon 已经写了实体类型）
        self.assertNotIn('id:"minecraft:zombie"', zombie.nbt)
        self.assertIn("HandItems", zombie.nbt)
        self.assertEqual(len(zombie.items), 1)
        self.assertEqual(zombie.items[0].clean_id(), "minecraft:iron_sword")

    def test_summon_command(self) -> None:
        zombie = [e for e in find_entities(self.root)
                  if e.entity_id == "minecraft:zombie"][0]
        command = zombie.summon_command("~ ~1 ~")
        self.assertTrue(command.startswith(
            "summon minecraft:zombie ~ ~1 ~ {"))
        self.assertIn("Health:20.0f", command)

    def test_looks_like_entity_rejects_items(self) -> None:
        item = SnbtParser('{id:"minecraft:stone",count:1}').parse()
        self.assertFalse(looks_like_entity(item))
        self.assertTrue(looks_like_entity(
            SnbtParser('{id:"minecraft:zombie"}').parse()))


class TestFormatting(unittest.TestCase):
    def test_quote_snbt(self) -> None:
        self.assertEqual(quote_snbt("stone"), "stone")
        self.assertEqual(quote_snbt(""), '""')
        self.assertEqual(quote_snbt("hello world"), '"hello world"')
        self.assertEqual(quote_snbt('say "hi"'), '"say \\"hi\\""')
        self.assertEqual(quote_snbt("true"), '"true"')

    def test_component_value_snbt(self) -> None:
        self.assertEqual(component_value_snbt("stone"), "stone")
        self.assertEqual(component_value_snbt(5), "5")
        self.assertEqual(component_value_snbt(True), "1b")
        self.assertEqual(component_value_snbt('{"text":"神剑"}'),
                         '{"text":"神剑"}')
        self.assertEqual(component_value_snbt("1.5f"), "1.5f")
        self.assertEqual(component_value_snbt("hello world"), '"hello world"')

    def test_roman_and_display(self) -> None:
        self.assertEqual(roman(1), "I")
        self.assertEqual(roman(4), "IV")
        self.assertEqual(roman(5), "V")
        self.assertEqual(roman(9), "IX")
        self.assertEqual(roman(40), "XL")
        self.assertEqual(roman(0), "0")
        self.assertTrue(enchant_display("minecraft:sharpness"))
        self.assertEqual(normalize_item_id("stone"), "minecraft:stone")
        self.assertEqual(normalize_item_id("minecraft:stone"),
                         "minecraft:stone")


class TestSettings(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        import app.core.settings as settings
        self.settings = settings
        self.tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-settings-"))
        settings._path_override = self.tmp / "settings.json"
        settings._cache = None

    def tearDown(self) -> None:
        self.settings._path_override = None
        self.settings._cache = None

    def test_defaults(self) -> None:
        data = self.settings.load()
        self.assertTrue(data["terms"]["use_translations"])
        self.assertEqual(self.settings.translate_nbt_type("String"), "字符串")
        self.assertEqual(self.settings.translate_nbt_type("Compound"), "复合标签")
        self.assertEqual(self.settings.translate_nbt_type("ByteArray"), "字节数组")

    def test_user_override_persists(self) -> None:
        data = self.settings.load()
        data["terms"]["nbt_types"]["String"] = "字串"
        self.assertTrue(self.settings.save(data))
        self.settings._cache = None
        self.assertEqual(self.settings.translate_nbt_type("String"), "字串")
        # 未覆盖的仍然用默认
        self.assertEqual(self.settings.translate_nbt_type("List"), "列表")

    def test_set_value_and_scope(self) -> None:
        self.settings.set_value("terms.scope", "none")
        self.assertEqual(self.settings.translate_nbt_type("String"), "String")
        self.settings.set_value("terms.scope", "types")
        self.assertEqual(self.settings.translate_nbt_type("String"), "字符串")
        self.settings.set_value("terms.scope", "all")
        self.assertEqual(self.settings.translate_category("Boss"), "Boss")
        self.settings.set_value("terms.use_translations", False)
        self.assertEqual(self.settings.translate_nbt_type("String"), "String")

    def test_effective_terms_merges_defaults(self) -> None:
        terms = self.settings.effective_terms("nbt_types")
        self.assertEqual(terms["String"], "字符串")
        self.settings.set_value("terms.nbt_types.String", "字串")
        terms = self.settings.effective_terms("nbt_types")
        self.assertEqual(terms["String"], "字串")
        self.assertEqual(terms["Int"], "整型")

    def test_subscribe_notified_on_save(self) -> None:
        seen: list[dict] = []
        unsubscribe = self.settings.subscribe(seen.append)
        self.settings.set_value("terms.scope", "types")
        self.assertTrue(seen)
        unsubscribe()
        count = len(seen)
        self.settings.set_value("terms.scope", "all")
        self.assertEqual(len(seen), count)


if __name__ == "__main__":
    unittest.main()
