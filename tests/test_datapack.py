"""版本识别与迁移：1.20.4 → 1.21.1。"""
from __future__ import annotations

import unittest

from tests import TEST_ROOT  # noqa: F401  （导入即设置环境变量）
from app.core.datapack import (
    detect_version, is_older, migrate_command, version_index,
)


class TestVersionCompare(unittest.TestCase):
    def test_index_monotonic(self) -> None:
        self.assertLess(version_index("1.20.4"), version_index("1.20.5"))
        self.assertLess(version_index("1.20.6"), version_index("1.21"))
        self.assertLess(version_index("1.21"), version_index("1.21.1"))

    def test_is_older(self) -> None:
        self.assertTrue(is_older("1.20.4"))
        self.assertFalse(is_older("1.21.1"))

    def test_unknown_version_is_oldest(self) -> None:
        self.assertEqual(version_index("1.7.10"), 0)


class TestDetectVersion(unittest.TestCase):
    CASES = [
        ("give @p diamond_sword 1 {display:{Name:\"x\"}}", "1.20.4"),
        ('give @p minecraft:diamond_sword[minecraft:custom_name="A"]', "1.20.5"),
        ("give @p minecraft:diamond_sword[minecraft:max_count=1]", "1.21"),
        ('summon minecraft:zombie ~ ~ ~ {attributes:[{id:"minecraft:generic.max_health"}]}',
         "1.20.6"),
        ('give @p minecraft:sword[minecraft:enchantments={levels:{"minecraft:sharpness":5}}]',
         "1.20.5"),
    ]

    def test_detect(self) -> None:
        for cmd, expected in self.CASES:
            with self.subTest(command=cmd):
                self.assertEqual(detect_version(cmd), expected)

    def test_empty(self) -> None:
        self.assertEqual(detect_version(""), "1.20.4")


class TestMigration(unittest.TestCase):
    def test_give_nbt_to_components(self) -> None:
        cmd = ('give @p diamond_sword 1 {display:{Name:"Excalibur"},'
               'Enchantments:[{id:"minecraft:sharpness",lvl:5}]}')
        result = migrate_command(cmd)
        self.assertTrue(result.changed)
        self.assertIn("minecraft:custom_name", result.migrated)
        self.assertIn("minecraft:enchantments", result.migrated)
        self.assertIn("levels", result.migrated)

    def test_unmapped_nbt_is_kept_as_custom_data(self) -> None:
        cmd = "give @p stick 5 {SomePluginData:{a:1}}"
        result = migrate_command(cmd)
        self.assertIn("minecraft:custom_data", result.migrated)
        self.assertIn("SomePluginData", result.migrated)

    def test_plain_give_is_untouched(self) -> None:
        cmd = "give @p minecraft:apple 3"
        result = migrate_command(cmd)
        self.assertFalse(result.changed)
        self.assertEqual(result.migrated, cmd)

    def test_dimension_prefix(self) -> None:
        result = migrate_command("execute in overworld run say hi")
        self.assertIn("in minecraft:overworld", result.migrated)

    def test_generic_attribute_renamed(self) -> None:
        result = migrate_command(
            'summon minecraft:zombie ~ ~ ~ '
            '{Attributes:[{Name:"minecraft:generic.max_health",Base:20.0}]}')
        self.assertIn("minecraft:max_health", result.migrated)
        self.assertNotIn("generic.", result.migrated)

    def test_max_stack_size_renamed(self) -> None:
        result = migrate_command(
            "give @p minecraft:stone[minecraft:max_stack_size=16]")
        self.assertIn("max_count", result.migrated)

    def test_modern_command_gets_no_changes(self) -> None:
        cmd = ('give @p minecraft:diamond_sword'
               '[minecraft:custom_name="A",minecraft:max_count=1]')
        result = migrate_command(cmd)
        self.assertFalse(result.changed)

    def test_empty_command(self) -> None:
        result = migrate_command("   ")
        self.assertFalse(result.changed)

    def test_warnings_for_legacy_structures(self) -> None:
        result = migrate_command(
            'summon minecraft:zombie ~ ~ ~ {HandItems:[{id:"minecraft:stone"}]}')
        self.assertTrue(any("equipment" in w for w in result.warnings))


if __name__ == "__main__":
    unittest.main()
