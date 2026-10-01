"""Schema 驱动 Builder：默认输出合法、可选参数不错位、往返一致。"""
from __future__ import annotations

import unittest

from tests import TEST_ROOT  # noqa: F401  （导入即设置环境变量）
from app.core import ArgSpec, get_builder


class TestArgSpec(unittest.TestCase):
    def test_visible_respects_when(self) -> None:
        spec = ArgSpec.from_dict({"key": "amount", "when": {"action": ["add"]}})
        self.assertFalse(spec.visible({"action": "set"}))
        self.assertTrue(spec.visible({"action": "add"}))

    def test_when_accepts_scalar(self) -> None:
        spec = ArgSpec.from_dict({"key": "x", "when": {"a": "b"}})
        self.assertTrue(spec.visible({"a": "b"}))
        self.assertFalse(spec.visible({"a": "c"}))


class TestDefaultOutput(unittest.TestCase):
    """默认参数拼出来的命令必须是合法语法（不能塞入分支外参数）。"""

    CASES = {
        "time": "time set",
        "weather": "weather clear",
        "forceload": "forceload query",
        "worldborder": "worldborder get",
        "me": "me waves",
        "gamemode": "gamemode creative @s",
        "xp": "xp add @s 10 points",
        "tp": "tp @p ~ ~ ~",
        "rotate": "rotate @s ~ ~",
        "bossbar": 'bossbar add minecraft:custom:name {"text":"Boss"}',
        "title": 'title @a title {"text":"Hello"}',
        "difficulty": "difficulty normal",
        "seed": "seed",
    }

    def test_defaults(self) -> None:
        for name, expected in self.CASES.items():
            with self.subTest(command=name):
                inst = get_builder(name)()
                self.assertEqual(inst.build(), expected)


class TestOptionalArgs(unittest.TestCase):
    def test_time_branches(self) -> None:
        b = get_builder("time")()
        b.values["action"] = "add"
        b.values["amount"] = "1000"
        self.assertEqual(b.build(), "time add 1000")
        b.values["action"] = "query"
        b.values["value"] = "day"
        self.assertEqual(b.build(), "time query day")

    def test_weather_duration_optional(self) -> None:
        b = get_builder("weather")()
        self.assertEqual(b.build(), "weather clear")
        b.values["duration"] = "600"
        self.assertEqual(b.build(), "weather clear 600")

    def test_bossbar_branches(self) -> None:
        b = get_builder("bossbar")()
        b.values["action"] = "set"
        b.values["field"] = "value"
        b.values["value"] = "50"
        self.assertEqual(b.build(), "bossbar set minecraft:custom:name value 50")

    def test_forceload_query_has_no_coords(self) -> None:
        b = get_builder("forceload")()
        self.assertEqual(b.build(), "forceload query")
        b.values["action"] = "add"
        b.values["from"] = "0 0"
        self.assertIn("0 0", b.build())


class TestSchemaRoundtrip(unittest.TestCase):
    CASES = [
        "time set day",
        "time set midnight",
        "time add 1000",
        "time query day",
        "weather rain 600",
        "weather clear",
        'bossbar add minecraft:foo {"text":"B"}',
        "bossbar set minecraft:foo value 50",
        "forceload query",
        "tp @p 1 2 3",
        "rotate @s 90 0",
        "item replace ~ ~ ~ container.0 minecraft:stone 3",
        "xp add @s 10 levels",
        "xp query @s",
        "setblock ~ ~ ~ minecraft:stone replace",
        "fill 0 0 0 5 5 5 minecraft:stone outline",
        "particle minecraft:flame ~ ~ ~ 0 0 0 0 10 force",
        "gamemode creative @a",
        "difficulty hard",
        "schedule clear minecraft:example",
        "me waves",
    ]

    def test_roundtrip(self) -> None:
        for cmd in self.CASES:
            with self.subTest(command=cmd):
                name = cmd.split()[0]
                inst = get_builder(name)()
                inst.load_from(cmd)
                self.assertEqual(inst.build(), cmd)

    def test_load_from_rejects_wrong_command(self) -> None:
        b = get_builder("setblock")()
        with self.assertRaises(ValueError):
            b.load_from("fill 0 0 0 1 1 1 minecraft:stone")


if __name__ == "__main__":
    unittest.main()
