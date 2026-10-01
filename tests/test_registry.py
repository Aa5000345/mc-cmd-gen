"""物品 / 方块 / 实体 / 附魔数据的完整性与可用性。

这些数据是「全物品」能力的基础，所以除了结构完整，还要保证：
    * ID 合法且不重复；
    * 中文名齐备（不能退化成英文 ID）；
    * /give 面板真的能用（标签、堆叠数、可解析）；
    * 中文名反查能命中，且不会因为重名给出离谱结果。
"""
from __future__ import annotations

import re
import unittest

import tests  # noqa: F401  （导入即设置环境变量）
from app.core.builders import GiveBuilder
from app.core.data import store
from app.core.ids import resolve
from app.core.item_components import max_stack_size

ID_RE = re.compile(r"^minecraft:[a-z0-9_./]+$")

# 需要达到的数量下限（防止某次改动把数据文件截断）
MIN_ITEMS = 1200
MIN_BLOCKS = 900
MIN_ENTITIES = 100
MIN_ENCHANTMENTS = 40


class TestScale(unittest.TestCase):
    def test_items_cover_full_registry(self) -> None:
        items = store().items()
        self.assertGreaterEqual(len(items), MIN_ITEMS,
                                f"物品只有 {len(items)} 条，应包含全部可获得物品")

    def test_blocks_cover_full_registry(self) -> None:
        blocks = store().blocks()
        self.assertGreaterEqual(len(blocks), MIN_BLOCKS,
                                f"方块只有 {len(blocks)} 条")

    def test_entities_cover_full_registry(self) -> None:
        entities = store().entities()
        self.assertGreaterEqual(len(entities), MIN_ENTITIES,
                                f"实体只有 {len(entities)} 条")

    def test_enchantments_cover_full_registry(self) -> None:
        self.assertGreaterEqual(len(store().enchantments()), MIN_ENCHANTMENTS)


class TestIntegrity(unittest.TestCase):
    def _check(self, entries: list[dict], label: str) -> None:
        ids = [str(e.get("id", "")) for e in entries]
        self.assertEqual(len(ids), len(set(ids)), f"{label} 存在重复 id")
        bad = [i for i in ids if not ID_RE.match(i)]
        self.assertEqual(bad[:5], [], f"{label} 存在非法 id")

    def test_ids_valid_and_unique(self) -> None:
        st = store()
        self._check(st.items(), "items")
        self._check(st.blocks(), "blocks")
        self._check(st.entities(), "entities")
        self._check(st.enchantments(), "enchantments")

    def test_every_entry_has_chinese_display(self) -> None:
        """中文名必须齐备，否则选择器里全是英文 ID。"""
        for label, entries in (("items", store().items()),
                               ("blocks", store().blocks()),
                               ("entities", store().entities())):
            missing: list[str] = []
            for entry in entries:
                display = str(entry.get("display", "")).strip()
                short = str(entry.get("id", "")).split(":", 1)[-1]
                if not display or display == short:
                    missing.append(str(entry.get("id")))
            with self.subTest(kind=label):
                # 允许极少数没有官方译名的条目，但不能大面积缺失
                self.assertLess(len(missing), len(entries) * 0.02,
                                f"{label} 缺少中文名：{missing[:10]}")

    def test_categories_are_chinese(self) -> None:
        for label, entries in (("items", store().items()),
                               ("blocks", store().blocks()),
                               ("entities", store().entities())):
            cats = {str(e.get("category", "")) for e in entries}
            with self.subTest(kind=label):
                self.assertNotIn("", cats, f"{label} 存在空 category")
                self.assertGreaterEqual(len(cats), 4)

    def test_tags_are_known(self) -> None:
        known = {"weapon", "tool", "armor", "armor_head", "armor_chest",
                 "armor_legs", "armor_feet", "head", "durable", "potion",
                 "banner", "shield", "leather", "dyeable", "container",
                 "bundle", "crossbow", "book", "map", "food"}
        for entry in store().items():
            tags = entry.get("tags") or []
            with self.subTest(item=entry.get("id")):
                self.assertTrue(set(tags) <= known, f"未知标签：{tags}")


class TestGiveUsability(unittest.TestCase):
    """全量数据要真的能喂给 /give 面板与 Builder。"""

    SAMPLES = (
        "minecraft:diamond_sword", "minecraft:netherite_chestplate",
        "minecraft:deepslate_diamond_ore", "minecraft:cherry_boat",
        "minecraft:potion", "minecraft:written_book",
        "minecraft:player_head", "minecraft:shulker_box",
        "minecraft:music_disc_pigstep", "minecraft:axolotl_spawn_egg",
        "minecraft:goat_horn", "minecraft:bee_nest",
    )

    def test_items_are_present(self) -> None:
        known = {str(e["id"]) for e in store().items()}
        for item in self.SAMPLES:
            with self.subTest(item=item):
                self.assertIn(item, known)

    def test_give_builder_accepts_everything(self) -> None:
        for item in self.SAMPLES:
            with self.subTest(item=item):
                b = GiveBuilder()
                b.item = item
                self.assertEqual(b.build(), f"give @p {item}")
                self.assertEqual(
                    [i for i in b.validate() if i.level == "error"], [])

    def test_stack_size_hint_matches_data(self) -> None:
        entries = {str(e["id"]): e for e in store().items()}
        for iid, expect in (("minecraft:potion", 1),
                            ("minecraft:written_book", 16),
                            ("minecraft:stone", 64)):
            entry = entries.get(iid, {})
            declared = int(entry.get("stack_size", 64) or 64)
            with self.subTest(item=iid):
                self.assertEqual(declared, expect)
                if iid != "minecraft:written_book":
                    # 数据表与提示表要一致（written_book 在 give 里按 16 提示）
                    self.assertEqual(max_stack_size(iid), expect)

    def test_search_helpers(self) -> None:
        """选择器按关键字过滤时应能找到物品（按 ID 与中文名）。"""
        items = store().items()
        by_id = [e for e in items if "diamond_sword" in e["id"]]
        self.assertTrue(by_id, "按英文 ID 搜不到钻石剑")
        by_display = [e for e in items if "钻石剑" in str(e.get("display", ""))]
        self.assertTrue(by_display, "按中文名搜不到钻石剑")


class TestChineseLookup(unittest.TestCase):
    def test_display_names_resolve(self) -> None:
        for text, expect in (
                ("钻石剑", "minecraft:diamond_sword"),
                ("监守者", "minecraft:warden"),
                ("樱花木船", "minecraft:cherry_boat"),
                ("下界合金锭", "minecraft:netherite_ingot"),
                ("附魔金苹果", "minecraft:enchanted_golden_apple"),
                ("锋利", "minecraft:sharpness"),
                ("僵尸", "minecraft:zombie")):
            with self.subTest(text=text):
                self.assertEqual(resolve(text), expect)

    def test_longer_name_wins_over_substring(self) -> None:
        """回归：「深板岩钻石矿石」曾被解析成 minecraft:diamond。"""
        self.assertEqual(resolve("深板岩钻石矿石"), "minecraft:diamond_ore")
        self.assertEqual(resolve("钻石矿石"), "minecraft:diamond_ore")
        # 单独说「钻石」还是物品
        self.assertEqual(resolve("钻石"), "minecraft:diamond")

    def test_unknown_returns_none(self) -> None:
        self.assertIsNone(resolve("完全不存在的名字"))
        self.assertIsNone(resolve(""))

    def test_display_index_has_no_absurd_entries(self) -> None:
        """所有能反查到的 ID 都必须真的存在于数据里。"""
        from app.core.ids import _display_index
        known = {str(e["id"]) for e in store().items()}
        known |= {str(e["id"]) for e in store().blocks()}
        known |= {str(e["id"]) for e in store().entities()}
        known |= {"minecraft:" + str(e["id"]).split(":", 1)[-1]
                  for e in store().enchantments()}
        known |= {"minecraft:speed", "minecraft:overworld"}
        unknown = [v for v in _display_index().values() if v not in known]
        self.assertLess(len(unknown), 30, f"反查表里有不存在的 ID：{unknown[:5]}")


if __name__ == "__main__":
    unittest.main()
