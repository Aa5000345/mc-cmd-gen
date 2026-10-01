"""中文 → 命名空间 ID 解析，以及自然语言句式。"""
from __future__ import annotations

import unittest

from tests import TEST_ROOT  # noqa: F401  （导入即设置环境变量）
from app.core.ids import clear_cache, is_id, resolve
from app.core.nlp.engine import cn_to_int, examples, parse, parse_with_pattern


class TestIds(unittest.TestCase):
    def test_chinese_alias(self) -> None:
        self.assertEqual(resolve("僵尸"), "minecraft:zombie")
        self.assertEqual(resolve("钻石剑"), "minecraft:diamond_sword")
        self.assertEqual(resolve("夜视"), "minecraft:night_vision")
        self.assertEqual(resolve("附魔台"), "minecraft:enchanting_table")

    def test_display_index_from_data(self) -> None:
        # 实体/物品数据里的 display 字段也应能反查
        self.assertEqual(resolve("苦力怕"), "minecraft:creeper")
        self.assertEqual(resolve("石头"), "minecraft:stone")

    def test_full_id_passthrough(self) -> None:
        self.assertEqual(resolve("minecraft:stone"), "minecraft:stone")
        self.assertTrue(is_id("mymod:custom_thing"))

    def test_plain_english_id(self) -> None:
        self.assertEqual(resolve("diamond_pickaxe"),
                         "minecraft:diamond_pickaxe")

    def test_unknown_returns_none(self) -> None:
        self.assertIsNone(resolve("完全不存在的玩意儿"))
        self.assertIsNone(resolve(""))

    def test_clear_cache(self) -> None:
        resolve("僵尸")
        clear_cache()
        self.assertEqual(resolve("僵尸"), "minecraft:zombie")


class TestChineseNumbers(unittest.TestCase):
    CASES = {"五": 5, "十": 10, "十二": 12, "五十": 50, "一百": 100,
             "两千": 2000, "一万": 10000, "二十": 20, "64": 64}

    def test_convert(self) -> None:
        for text, expected in self.CASES.items():
            with self.subTest(text=text):
                self.assertEqual(cn_to_int(text), expected)

    def test_invalid(self) -> None:
        self.assertIsNone(cn_to_int("abc"))
        self.assertIsNone(cn_to_int(""))


class TestNlp(unittest.TestCase):
    CASES = {
        "给所有玩家10个钻石": "give @a minecraft:diamond 10",
        "给Steve添加夜视效果60秒": "effect give Steve minecraft:night_vision 60",
        "在10 64 10生成一只僵尸": "summon minecraft:zombie 10 64 10",
        "把Steve传送到10 64 10": "tp Steve 10 64 10",
        "杀死所有僵尸": "kill @e[type=minecraft:zombie]",
        "用石头填充0 0 0到5 5 5": "fill 0 0 0 5 5 5 minecraft:stone",
        "把Steve设置成创造模式": "gamemode creative Steve",
        "设置成下雨": "weather rain",
        "把时间设置成夜晚": "time set night",
        "给所有玩家50点经验": "xp add @a 50 points",
        "对所有人说你好": 'tellraw @a {"text":"你好"}',
        "给Steve附魔锋利5级": "enchant Steve minecraft:sharpness 5",
        "清空Steve的钻石": "clear Steve minecraft:diamond",
        "难度设置成困难": "difficulty hard",
        # 中文数字
        "给所有人三个钻石": "give @a minecraft:diamond 3",
        "给所有玩家添加速度效果十秒": "effect give @a minecraft:speed 10",
    }

    def test_parse(self) -> None:
        for text, expected in self.CASES.items():
            with self.subTest(text=text):
                self.assertEqual(parse(text), expected)

    def test_particle_branch(self) -> None:
        self.assertEqual(parse("在~ ~1 ~生成火焰粒子"),
                         "particle minecraft:flame ~ ~1 ~ 0 0 0 1 1")

    def test_unknown_returns_none(self) -> None:
        self.assertIsNone(parse("今天天气不错"))
        self.assertIsNone(parse(""))

    def test_pattern_metadata(self) -> None:
        hit = parse_with_pattern("杀死所有僵尸")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[1]["id"], "kill_all")

    def test_examples_are_parseable(self) -> None:
        for sample in examples(12):
            with self.subTest(sample=sample):
                self.assertIsNotNone(parse(sample),
                                     f"示例自身无法解析：{sample}")


if __name__ == "__main__":
    unittest.main()
