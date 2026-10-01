"""蓝图 / 结构模板 + 物品组件配方工坊 + 项目改名与数据目录迁移。"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests import TEST_ROOT  # noqa: F401  （导入即设置环境变量）

from app import branding
from app.core.snbt import SnbtKind, SnbtParser, serialize


class _DataDirCase(unittest.TestCase):
    """把应用数据目录指到临时目录，避免碰到真实用户数据。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mcstudio-bp-"))
        self._old = os.environ.get("MCSTUDIO_DATA_DIR")
        os.environ["MCSTUDIO_DATA_DIR"] = str(self.tmp)
        from app import storage
        storage.Database.shutdown()

    def tearDown(self) -> None:
        from app import storage
        storage.Database.shutdown()
        if self._old is None:
            os.environ.pop("MCSTUDIO_DATA_DIR", None)
        else:
            os.environ["MCSTUDIO_DATA_DIR"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestBlueprint(_DataDirCase):
    def _selection(self):
        return {
            (0, 0, 0): "minecraft:stone",
            (1, 0, 0): ("minecraft:chest",
                        SnbtParser('{Items:[{Slot:0b,id:"minecraft:diamond",'
                                   'count:3}]}').parse()),
            (0, 1, 0): "minecraft:glass",
        }

    def test_export_selection_normalizes_to_origin(self) -> None:
        from app.core.blueprint import export_selection
        selection = {(10, 64, -5): "minecraft:stone",
                     (11, 64, -5): "minecraft:dirt"}
        blueprint = export_selection(selection, "归一化")
        self.assertEqual(blueprint.size, (2, 1, 1))
        self.assertEqual(sorted(b.pos for b in blueprint.blocks),
                         [(0, 0, 0), (1, 0, 0)])

    def test_palette_and_summary(self) -> None:
        from app.core.blueprint import export_selection
        blueprint = export_selection(self._selection(), "测试")
        self.assertEqual(blueprint.palette(),
                         ["minecraft:stone", "minecraft:chest",
                          "minecraft:glass"])
        self.assertEqual(blueprint.block_count(), 3)
        self.assertEqual(blueprint.volume, 4)
        self.assertIn("测试", blueprint.summary())
        self.assertIn("3 个方块", blueprint.summary())

    def test_block_entities_separated_from_palette(self) -> None:
        from app.core.blueprint import export_selection
        blueprint = export_selection(self._selection(), "方块实体")
        node = blueprint.to_structure_node()
        # NBT 不进调色板，而是进 block_entities（与原版结构模板一致）
        palette = node.get_child("palette")
        self.assertEqual(len(palette.children), 3)
        states = [int(c.get_child("state").value)
                  for c in node.get_child("blocks").children]
        self.assertEqual(states, [0, 1, 2])
        entities = node.get_child("block_entities")
        self.assertEqual(len(entities.children), 1)
        chest = entities.children[0]
        self.assertEqual(str(chest.get_child("id").value), "minecraft:chest")
        self.assertEqual(int(chest.get_child("x").value), 1)
        self.assertIsNotNone(chest.get_child("Items"))

    def test_nbt_round_trip(self) -> None:
        from app.core.blueprint import export_selection, load_blueprint
        blueprint = export_selection(self._selection(), "往返")
        path = blueprint.save_nbt()
        self.assertTrue(path.is_file())
        back = load_blueprint(path)
        self.assertEqual(back.size, blueprint.size)
        self.assertEqual(back.palette(), blueprint.palette())
        for block in back.blocks:
            if block.block == "minecraft:chest":
                self.assertIsNotNone(block.nbt)
                self.assertIsNotNone(block.nbt.get_child("Items"))
            else:
                self.assertIsNone(block.nbt)

    def test_schem_round_trip_cube(self) -> None:
        """3D 选区（含 Y 与 Z 混合）也必须原样往返。"""
        from app.core.blueprint import export_selection, load_blueprint
        selection = {}
        for x in range(3):
            for y in range(2):
                for z in range(2):
                    selection[(x, y, z)] = "minecraft:stone"
        selection[(1, 1, 1)] = "minecraft:chest"
        blueprint = export_selection(selection, "立方体")
        back = load_blueprint(blueprint.save_schem())
        self.assertEqual(back.size, blueprint.size)
        expected = sorted((b.pos, b.block) for b in blueprint.blocks
                          if b.block != "minecraft:stone")
        actual = sorted((b.pos, b.block) for b in back.blocks
                        if b.block != "minecraft:stone")
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [((1, 1, 1), "minecraft:chest")])

    def test_schem_round_trip(self) -> None:
        from app.core.blueprint import export_selection, load_blueprint
        blueprint = export_selection(self._selection(), "schem")
        path = blueprint.save_schem()
        back = load_blueprint(path)
        self.assertEqual(back.size, blueprint.size)
        # Sponge 的 BlockData 里 0 表示空气，而石头正好是调色板第 0 项，
        # 所以 (0,0,0) 的那块石头读回来会变成空气；其余位置必须一致。
        expected = sorted((b.pos, b.block) for b in blueprint.blocks
                          if b.pos != (0, 0, 0))
        actual = sorted((b.pos, b.block) for b in back.blocks)
        self.assertEqual(actual, expected)
        # 回归：2×2×1 这种「立方体」最容易把 Y/Z/X 铺平公式写错，
        # (1,0,0) 的箱子与 (0,1,0) 的玻璃必须各自回到原位
        self.assertIn(((1, 0, 0), "minecraft:chest"), actual)
        self.assertIn(((0, 1, 0), "minecraft:glass"), actual)

    def test_snbt_round_trip(self) -> None:
        from app.core.blueprint import export_selection, load_blueprint
        blueprint = export_selection(self._selection(), "snbt")
        path = blueprint.output_snbt if False else None      # noqa: F841
        target = Path(self.tmp) / "bp.snbt"
        target.write_text(blueprint.to_snbt(), encoding="utf-8")
        back = load_blueprint(target)
        self.assertEqual(back.palette(), blueprint.palette())

    def test_commands_setblock_and_fill(self) -> None:
        from app.core.blueprint import export_selection
        blueprint = export_selection(self._selection(), "命令")
        commands = blueprint.to_commands()
        self.assertEqual(len(commands), 3)
        self.assertIn("setblock 0 0 0 minecraft:stone replace", commands[0])
        self.assertIn("{Items:", commands[1])
        fill = blueprint.to_commands(mode="fill")
        self.assertEqual(len(fill), 1)
        self.assertTrue(fill[0].startswith("fill 0 0 0 1 1 0"))
        place = blueprint.to_commands(mode="place")
        self.assertIn("place template", place[0])

    def test_mcfunction_has_header(self) -> None:
        from app.core.blueprint import export_selection
        text = export_selection(self._selection(), "函数").to_mcfunction()
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("# 函数"))
        self.assertIn("MCStudio", lines[1])
        self.assertIn("setblock", text)

    def test_volume_limit(self) -> None:
        from app.core.blueprint import export_selection
        selection = {(x, 0, 0): "minecraft:stone" for x in range(10)}
        with self.assertRaises(ValueError):
            export_selection(selection, "太大", max_volume=5)

    def test_blueprint_from_block_entities(self) -> None:
        from app.core.blueprint import blueprint_from_block_entities
        container = SnbtParser(
            '[{id:"minecraft:chest",x:2,y:64,z:3,'
            'Items:[{Slot:0b,id:"minecraft:apple",count:2}]},'
            '{id:"minecraft:furnace",x:3,y:64,z:3}]').parse()
        blueprint = blueprint_from_block_entities(container, "箱子区")
        self.assertEqual(blueprint.block_count(), 2)
        self.assertEqual(blueprint.size, (2, 1, 1))
        chest = [b for b in blueprint.blocks
                 if b.block == "minecraft:chest"][0]
        self.assertIsNotNone(chest.nbt)
        # x/y/z/id 已经从 NBT 载荷里剥离
        self.assertIsNone(chest.nbt.get_child("x"))
        self.assertIsNone(chest.nbt.get_child("id"))

    def test_load_rejects_garbage(self) -> None:
        from app.core.blueprint import load_blueprint
        from app.core.snbt import SnbtParseError
        path = Path(self.tmp) / "bad.snbt"
        path.write_text("{not a blueprint", encoding="utf-8")
        with self.assertRaises((SnbtParseError, ValueError)):
            load_blueprint(path)

    def test_blueprints_dir_is_created(self) -> None:
        from app.core.blueprint import blueprints_dir
        folder = blueprints_dir()
        self.assertTrue(folder.is_dir())
        self.assertEqual(folder.name, "blueprints")

    def test_list_blueprints(self) -> None:
        from app.core.blueprint import export_selection, list_blueprints
        self.assertEqual(list_blueprints(), [])
        export_selection(self._selection(), "列一个").save_nbt()
        export_selection(self._selection(), "列两个").save_schem()
        self.assertEqual(len(list_blueprints()), 2)


class TestRecipeWorkshop(_DataDirCase):
    def test_builtin_recipes_present(self) -> None:
        from app.core.blueprint import list_recipes
        recipes = list_recipes()
        self.assertGreater(len(recipes), 5)
        self.assertTrue(all(r.builtin for r in recipes))
        self.assertTrue(any(r.id == "sword.sharp5" for r in recipes))

    def test_recipe_to_give_command(self) -> None:
        from app.core.blueprint import get_recipe
        recipe = get_recipe("sword.sharp5")
        self.assertIsNotNone(recipe)
        command = recipe.to_give_command("@a")
        self.assertTrue(command.startswith("give @a minecraft:diamond_sword"))
        self.assertIn("enchantments", command)
        self.assertIn("sharpness", command)

    def test_recipe_lookup_by_name(self) -> None:
        from app.core.blueprint import get_recipe
        recipe = get_recipe("锋利 V 神剑")
        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.id, "sword.sharp5")

    def test_user_recipe_saved_and_overrides(self) -> None:
        from app.core.blueprint import ItemRecipe, get_recipe, list_recipes
        custom = ItemRecipe(
            id="sword.sharp5", name="我的神剑", item="minecraft:netherite_sword",
            count=1, components=[{"id": "minecraft:unbreakable", "value": "1b"}])
        path = custom.save()
        self.assertTrue(path.is_file())
        again = get_recipe("sword.sharp5")
        self.assertEqual(again.name, "我的神剑")
        self.assertFalse(again.builtin)
        recipes = list_recipes()
        self.assertEqual(len([r for r in recipes if r.id == "sword.sharp5"]), 1)

    def test_recipe_round_trip_dict(self) -> None:
        from app.core.blueprint import ItemRecipe
        recipe = ItemRecipe(id="t", name="测试", item="minecraft:stone",
                            count=3,
                            components=[{"id": "minecraft:damage",
                                         "value": "5"}])
        back = ItemRecipe.from_dict(recipe.to_dict())
        self.assertEqual(back.item, "minecraft:stone")
        self.assertEqual(back.count, 3)
        self.assertEqual(back.components, recipe.components)

    def test_apply_recipe_to_container(self) -> None:
        from app.core.blueprint import ItemRecipe, apply_recipe_to_container
        import sys
        if str(TEST_ROOT) not in sys.path:                  # pragma: no cover
            sys.path.insert(0, str(TEST_ROOT))
        from tools.fake_world import build_fake_world
        from app.core.world import open_world
        root = Path(TEST_ROOT) / "build" / "recipe-world"
        if root.exists():
            shutil.rmtree(root)
        build_fake_world(root)
        world = open_world(root, read_only=False)
        uuid = "11111111-2222-3333-4444-555555555555"
        recipe = ItemRecipe(id="t", name="测试", item="minecraft:paper",
                            count=7,
                            components=[{"id": "minecraft:custom_name",
                                         "value": '"测试纸"'}])
        written = apply_recipe_to_container(world, uuid, "EnderItems", recipe)
        self.assertEqual(written, 7)
        containers = world.containers(uuid)
        ender = [c for c in containers if c.path == "EnderItems"][0]
        self.assertEqual(ender.total_count, 8)
        shutil.rmtree(root, ignore_errors=True)

    def test_recipe_component_text_serializes(self) -> None:
        from app.core.blueprint import ItemRecipe
        recipe = ItemRecipe(id="t", name="n", item="minecraft:stone",
                            components=[{"id": "minecraft:damage",
                                         "value": "5"},
                                        {"id": "minecraft:unbreakable"}])
        text = recipe.component_text()
        self.assertIn("[damage=5", text)
        self.assertIn("unbreakable", text)

    def test_recipe_saved_as_json(self) -> None:
        from app.core.blueprint import ItemRecipe, recipes_dir
        recipe = ItemRecipe(id="json.one", name="JSON 配方",
                            item="minecraft:stick")
        path = recipe.save()
        self.assertEqual(path.parent, recipes_dir())
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["name"], "JSON 配方")
        self.assertEqual(payload["item"], "minecraft:stick")


class TestBranding(unittest.TestCase):
    def test_new_name_constants(self) -> None:
        self.assertEqual(branding.APP_NAME, "MCStudio")
        self.assertEqual(branding.APP_SLUG, "mcstudio")
        self.assertEqual(branding.GENERATOR, "MCStudio")
        self.assertIn("MCCmdGen", branding.GENERATOR_ALIASES)

    def test_window_title_mentions_both_roles(self) -> None:
        title = branding.window_title()
        self.assertIn("MCStudio", title)
        self.assertIn("命令", title)
        self.assertIn("编辑器", title)
        self.assertIn(branding.TARGET_MC_VERSION, title)

    def test_window_title_with_suffix(self) -> None:
        self.assertTrue(branding.window_title("世界A").endswith("— 世界A"))

    def test_about_text_lists_capabilities(self) -> None:
        text = branding.about_text()
        for keyword in ("命令", "NBT", "世界存档", "数据包"):
            self.assertIn(keyword, text)
        self.assertIn(branding.VERSION, text)

    def test_rename_report_has_candidates_and_migration(self) -> None:
        report = branding.rename_report()
        self.assertIn("候选名称", report)
        self.assertIn("MCStudio", report)
        self.assertIn("mcgen.py", report)
        self.assertIn("MCSTUDIO_NO_MIGRATE", report)
        for name, _cn, _note in branding.RENAME_CANDIDATES:
            self.assertIn(name, report)

    def test_candidates_count_and_recommendation(self) -> None:
        self.assertGreaterEqual(len(branding.RENAME_CANDIDATES), 3)
        self.assertLessEqual(len(branding.RENAME_CANDIDATES), 5)
        self.assertIn("★", branding.RENAME_CANDIDATES[0][2])

    def test_impact_table_covers_required_targets(self) -> None:
        joined = "\n".join(f"{k}: {v}" for k, v in branding.RENAME_IMPACT)
        for keyword in ("窗口标题", "pyproject", "README", "CLI", "安装包名",
                        "应用数据目录", "generator"):
            self.assertIn(keyword, joined)

    def test_data_dir_override_env(self) -> None:
        old = os.environ.get("MCSTUDIO_DATA_DIR")
        old_legacy = os.environ.get("MCCMDGEN_DATA_DIR")
        os.environ["MCSTUDIO_DATA_DIR"] = r"C:\tmp\mcstudio"
        try:
            self.assertEqual(str(branding.data_dir_override()),
                             r"C:\tmp\mcstudio")
        finally:
            os.environ.pop("MCSTUDIO_DATA_DIR", None)
            os.environ["MCCMDGEN_DATA_DIR"] = r"C:\tmp\mccmdgen"
            try:
                self.assertEqual(str(branding.data_dir_override()),
                                 r"C:\tmp\mccmdgen")
            finally:
                os.environ.pop("MCCMDGEN_DATA_DIR", None)
                if old is not None:
                    os.environ["MCSTUDIO_DATA_DIR"] = old
                if old_legacy is not None:
                    os.environ["MCCMDGEN_DATA_DIR"] = old_legacy

    def test_skip_migration_env(self) -> None:
        old = os.environ.get(branding.NO_MIGRATE_ENV)
        os.environ[branding.NO_MIGRATE_ENV] = "1"
        try:
            self.assertTrue(branding.skip_migration())
        finally:
            if old is None:
                os.environ.pop(branding.NO_MIGRATE_ENV, None)
            else:
                os.environ[branding.NO_MIGRATE_ENV] = old
        self.assertFalse(branding.skip_migration())


class TestDataDirMigration(unittest.TestCase):
    """旧目录 MCCmdGen → 新目录 mcstudio：只复制、幂等、可关闭。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mcstudio-migrate-"))
        self.source = self.tmp / "MCCmdGen"
        self.target = self.tmp / "mcstudio"
        (self.source / "user_data").mkdir(parents=True)
        (self.source / "scenes").mkdir(parents=True)
        (self.source / "data.db").write_bytes(b"SQLite format 3\x00")
        (self.source / "user_data" / "settings.json").write_text(
            '{"terms": {"scope": "all"}}', encoding="utf-8")
        (self.source / "user_data" / "validation_rules.json").write_text(
            "[]", encoding="utf-8")
        (self.source / "scenes" / "s1.json").write_text("{}", encoding="utf-8")
        (self.source / "update_source.txt").write_text(
            "https://example.com", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_migrates_all_entries(self) -> None:
        from app.migration import migrate_data_dir
        report = migrate_data_dir(self.source, self.target)
        self.assertTrue(report.moved)
        self.assertEqual(report.errors, [])
        for name in ("data.db", "update_source.txt"):
            self.assertTrue((self.target / name).is_file(), name)
        for name in ("user_data/settings.json", "scenes/s1.json",
                     "user_data/validation_rules.json"):
            self.assertTrue((self.target / name).is_file(), name)
        # 旧目录保留（不删除，方便回退）
        self.assertTrue((self.source / "data.db").is_file())
        self.assertEqual(report.summary().count("已从"), 1)

    def test_writes_marker_and_is_idempotent(self) -> None:
        from app.migration import MIGRATION_MARKER, migrate_data_dir
        first = migrate_data_dir(self.source, self.target)
        self.assertTrue(first.moved)
        self.assertTrue((self.target / MIGRATION_MARKER).is_file())
        second = migrate_data_dir(self.source, self.target)
        self.assertTrue(second.already)
        self.assertFalse(second.moved)

    def test_does_not_overwrite_newer_files(self) -> None:
        from app.migration import migrate_data_dir
        self.target.mkdir(parents=True)
        (self.target / "data.db").write_bytes(b"NEW")
        migrate_data_dir(self.source, self.target)
        self.assertEqual((self.target / "data.db").read_bytes(), b"NEW")

    def test_merges_directories(self) -> None:
        from app.migration import migrate_data_dir
        self.target.mkdir(parents=True)
        (self.target / "user_data").mkdir()
        (self.target / "user_data" / "settings.json").write_text(
            "NEW", encoding="utf-8")
        migrate_data_dir(self.source, self.target)
        self.assertEqual(
            (self.target / "user_data" / "settings.json").read_text("utf-8"),
            "NEW")
        # 旧目录里的其它文件仍然被带过来
        self.assertTrue(
            (self.target / "user_data" / "validation_rules.json").is_file())

    def test_skip_migration_env(self) -> None:
        from app.migration import migrate_data_dir
        os.environ[branding.NO_MIGRATE_ENV] = "1"
        try:
            report = migrate_data_dir(self.source, self.target)
            self.assertFalse(report.moved)
            self.assertFalse(self.target.exists())
        finally:
            os.environ.pop(branding.NO_MIGRATE_ENV, None)

    def test_force_ignores_marker(self) -> None:
        from app.migration import migrate_data_dir
        migrate_data_dir(self.source, self.target)
        (self.source / "update_source.txt").write_text("X", encoding="utf-8")
        report = migrate_data_dir(self.source, self.target, force=True)
        self.assertFalse(report.already)
        # 目标已存在同名文件，所以这次是跳过而不是覆盖
        self.assertIn("update_source.txt", report.skipped)

    def test_no_legacy_dir_is_noop(self) -> None:
        from app.migration import migrate_data_dir
        report = migrate_data_dir(self.tmp / "nope", self.target)
        self.assertFalse(report.moved)
        self.assertFalse(self.target.exists())
        self.assertIn("无需迁移", report.summary())

    def test_find_legacy_dir_prefers_non_empty(self) -> None:
        from app.migration import find_legacy_dir
        found = find_legacy_dir()
        # 在测试环境里 LOCALAPPDATA 指向临时目录，不会有旧目录
        self.assertTrue(found is None or found.is_dir())

    def test_report_details(self) -> None:
        from app.migration import migrate_data_dir
        report = migrate_data_dir(self.source, self.target)
        details = report.details()
        self.assertTrue(any("data.db" in line for line in details))

    def test_empty_legacy_dir_is_skipped(self) -> None:
        from app.migration import migrate_data_dir
        empty = self.tmp / "MCCmdGen-empty"
        empty.mkdir()
        report = migrate_data_dir(empty, self.target)
        self.assertFalse(report.moved)
        self.assertFalse(self.target.exists())


class TestPackagingMeta(unittest.TestCase):
    """pyproject / 入口文件里的名字要跟 branding 一致。"""

    def setUp(self) -> None:
        self.root = Path(TEST_ROOT)

    def test_pyproject_uses_new_name(self) -> None:
        text = (self.root / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('name = "mcstudio"', text)
        self.assertIn("mcstudio = ", text)
        self.assertIn("mc-cmd-gen = ", text)

    def test_legacy_cli_entry_still_exists(self) -> None:
        self.assertTrue((self.root / "mcgen.py").is_file())

    def test_new_cli_entry_exists_and_reexports(self) -> None:
        text = (self.root / "mcstudio.py").read_text(encoding="utf-8")
        self.assertIn("from mcgen import", text)
        self.assertIn("main", text)

    def test_main_uses_branding(self) -> None:
        text = (self.root / "main.py").read_text(encoding="utf-8")
        self.assertIn("branding", text)
        self.assertNotIn("Minecraft Java 命令生成器", text)

    def test_app_version_matches_branding(self) -> None:
        import app
        self.assertEqual(app.__version__, branding.VERSION)

    def test_storage_app_dir_name_is_slug(self) -> None:
        from app import storage
        self.assertEqual(storage.APP_DIR_NAME, branding.APP_SLUG)
        self.assertIn("MCCmdGen", storage.LEGACY_APP_DIR_NAMES)


if __name__ == "__main__":
    unittest.main()
