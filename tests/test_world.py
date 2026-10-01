"""世界存档核心层：打开 / 总览 / 字段编辑 / 容器 / 地图 / 区域 / 健康检查。

这些测试全部在临时目录里跑，不碰真实存档。
"""
from __future__ import annotations

import os
import shutil
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from tests import TEST_ROOT  # noqa: F401  （导入即设置环境变量）

from app.core.nbt_binary import NbtDocument, node_to_nbt_bytes, save_nbt_file
from app.core.snbt import SnbtKind, SnbtNode, SnbtParser, serialize
from app.core.world import (
    DIFFICULTIES, GAME_MODES, KNOWN_DATA_VERSIONS, SECTOR_SIZE, WorldSave,
    check_compatibility, count_bytes_human, data_version_label,
    find_save_dirs, game_time_label, health_check, human_time,
    looks_like_save_dir, normalize_uuid, open_world, parse_field_path,
    read_mod_list, read_region_chunk, read_region_index, resolve_path,
    scan_world_entities, set_value_at, snapshot_world,
)


def _fixture_root() -> Path:
    return Path(TEST_ROOT) / "build" / "test-worlds"


def build_world(name: str = "unit", *, with_region: bool = True) -> Path:
    """用 tools/fake_world 生成一个测试存档（避免测试之间互相干扰）。"""
    import sys
    if str(TEST_ROOT) not in sys.path:
        sys.path.insert(0, str(TEST_ROOT))
    from tools.fake_world import build_fake_world
    root = _fixture_root() / name
    if root.exists():
        shutil.rmtree(root)
    return build_fake_world(root, with_region=with_region)


class TestFieldPath(unittest.TestCase):
    def test_parse_simple(self) -> None:
        self.assertEqual(parse_field_path("Data.Player.Health"),
                         ["Data", "Player", "Health"])

    def test_parse_index(self) -> None:
        self.assertEqual(parse_field_path("Data.Player.Inventory[0].id"),
                         ["Data", "Player", "Inventory", 0, "id"])

    def test_parse_leading_dot_and_spaces(self) -> None:
        self.assertEqual(parse_field_path(" .Data.DayTime "), ["Data", "DayTime"])

    def test_parse_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            parse_field_path("")

    def test_parse_rejects_missing_bracket(self) -> None:
        with self.assertRaises(ValueError):
            parse_field_path("Data.Inventory[0")

    def test_parse_rejects_non_integer_index(self) -> None:
        with self.assertRaises(ValueError):
            parse_field_path("Data.Inventory[{Slot:0b}]")

    def test_resolve_and_missing(self) -> None:
        root = SnbtParser('{Data:{Player:{Health:20.0f}}}').parse()
        self.assertIsNotNone(resolve_path(root, "Data.Player.Health"))
        self.assertIsNone(resolve_path(root, "Data.Player.Armor"))
        self.assertIsNone(resolve_path(root, "Data.Missing.Deep.Path"))


class TestSetValueAt(unittest.TestCase):
    def setUp(self) -> None:
        self.root = SnbtParser(
            '{Data:{DayTime:1000L,A:1b,B:2s,Nums:[I;1,2,3],'
            'Player:{Health:20.0f}}}').parse()

    def test_set_existing_keeps_type(self) -> None:
        node = set_value_at(self.root, "Data.DayTime", "5000")
        self.assertEqual(node.kind, SnbtKind.LONG)
        self.assertEqual(node.value, 5000)

    def test_set_coerces_byte_range(self) -> None:
        node = set_value_at(self.root, "Data.A", "0")
        self.assertEqual(node.value, 0)
        with self.assertRaises(ValueError):
            set_value_at(self.root, "Data.A", "99999")

    def test_set_short(self) -> None:
        node = set_value_at(self.root, "Data.B", "300")
        self.assertEqual(node.kind, SnbtKind.SHORT)
        self.assertEqual(node.value, 300)

    def test_set_list_index(self) -> None:
        node = set_value_at(self.root, "Data.Nums[1]", "42")
        self.assertEqual(node.value, 42)

    def test_set_missing_requires_create(self) -> None:
        with self.assertRaises(KeyError):
            set_value_at(self.root, "Data.NewField", "x")
        node = set_value_at(self.root, "Data.NewField", "x", create=True)
        self.assertEqual(node.kind, SnbtKind.STRING)
        self.assertEqual(node.value, "x")

    def test_set_scalar_rejects_container_value(self) -> None:
        """给标量赋一个「看起来像容器」的取值应被拒绝，而不是写出坏数据。"""
        with self.assertRaises(ValueError):
            set_value_at(self.root, "Data.DayTime", "{a:1}")

    def test_set_container_replaces_node(self) -> None:
        """容器（List / Compound）可以整体替换，例如 Pos / Rotation。"""
        node = set_value_at(self.root, "Data.Nums", [7, 8],
                            kind=SnbtKind.LIST)
        self.assertEqual(node.kind, SnbtKind.LIST)
        self.assertEqual([c.value for c in node.children], [7, 8])
        root_child = resolve_path(self.root, "Data.Nums")
        self.assertIs(root_child, node)

    def test_set_array_keeps_array_kind(self) -> None:
        """不给 kind 时沿用原容器的类型（IntArray 还是 IntArray）。"""
        node = set_value_at(self.root, "Data.Nums", [4, 5, 6])
        self.assertEqual(node.kind, SnbtKind.INT_ARRAY)
        self.assertEqual([c.value for c in node.children], [4, 5, 6])

    def test_set_compound_from_dict(self) -> None:
        node = set_value_at(self.root, "Data.Player", {"Health": 5.0})
        self.assertEqual(node.kind, SnbtKind.COMPOUND)
        self.assertAlmostEqual(
            float(node.get_child("Health").value), 5.0)

    def test_set_bool_from_text(self) -> None:
        node = set_value_at(self.root, "Data.A", "true", kind=SnbtKind.BOOL)
        self.assertTrue(node.value)


class TestSmallHelpers(unittest.TestCase):
    def test_normalize_uuid_string(self) -> None:
        self.assertEqual(
            normalize_uuid("11111111222233334444555555555555"),
            "11111111-2222-3333-4444-555555555555")

    def test_normalize_uuid_int_array(self) -> None:
        node = SnbtParser("[I; 0x11111111, 0x22223333, 0x44445555, 0x55555555]"
                          ).parse()
        self.assertEqual(normalize_uuid(node),
                         "11111111-2222-3333-4444-555555555555")

    def test_normalize_uuid_empty(self) -> None:
        self.assertEqual(normalize_uuid(None), "")
        self.assertEqual(normalize_uuid(SnbtParser("[I; 1, 2]").parse()), "")

    def test_data_version_label(self) -> None:
        self.assertEqual(data_version_label(3955), "1.21.1")
        self.assertIn("1.2", data_version_label(3900))
        self.assertEqual(data_version_label(None), "未知")

    def test_game_time_label(self) -> None:
        self.assertEqual(game_time_label(6000), "中午")
        self.assertEqual(game_time_label(18000), "午夜")

    def test_human_time(self) -> None:
        self.assertTrue(human_time(1735689600000))
        self.assertEqual(human_time(0), "")
        self.assertEqual(human_time(None), "")

    def test_count_bytes_human(self) -> None:
        self.assertEqual(count_bytes_human(512), "512 B")
        self.assertIn("KB", count_bytes_human(2048))

    def test_compatibility_marks_newer_read_only(self) -> None:
        result = check_compatibility(4000)
        self.assertEqual(result.level, "read_only")
        self.assertFalse(result.writable)

    def test_compatibility_marks_older_read_only(self) -> None:
        self.assertEqual(check_compatibility(1631).level, "read_only")

    def test_compatibility_ok_for_target(self) -> None:
        self.assertEqual(check_compatibility(3955).level, "ok")

    def test_compatibility_unknown(self) -> None:
        self.assertEqual(check_compatibility(None).level, "unknown")


class TestWorldOpen(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.path = build_world("open")

    def test_opens_and_reports_no_errors(self) -> None:
        world = open_world(self.path)
        self.assertTrue(world.is_open())
        self.assertEqual(world.errors, [])

    def test_default_is_read_only(self) -> None:
        world = open_world(self.path)
        self.assertTrue(world.read_only)
        with self.assertRaises(PermissionError):
            world.set_level_field("DayTime", 1)

    def test_overview_fields(self) -> None:
        world = open_world(self.path)
        overview = world.overview
        self.assertEqual(overview.name, "测试世界")
        self.assertEqual(overview.data_version, 3955)
        self.assertEqual(overview.version_label, "1.21.1")
        self.assertEqual(overview.difficulty, "normal")
        self.assertTrue(overview.allow_commands)
        self.assertFalse(overview.hardcore)
        self.assertEqual(overview.weather, "clear")
        self.assertEqual(overview.spawn, (8, 65, 8))
        self.assertEqual(overview.seed, 1234567890123)
        self.assertTrue(overview.last_played)
        self.assertIn("keepInventory", overview.gamerules)
        self.assertTrue(overview.world_border)

    def test_entries_listed(self) -> None:
        world = open_world(self.path)
        for name in ("level.dat", "playerdata", "region", "datapacks", "data"):
            self.assertIn(name, world.overview.entries)

    def test_players_and_sidecars(self) -> None:
        world = open_world(self.path)
        player = world.players["11111111-2222-3333-4444-555555555555"]
        self.assertEqual(player.health, 20.0)
        self.assertEqual(player.xp_level, 5)
        self.assertEqual(player.game_type, "creative")
        self.assertEqual(player.inventory_count, 2)
        self.assertEqual(player.ender_count, 1)
        self.assertTrue(player.stats)
        self.assertIn("minecraft:story/mine_stone", player.advancements)
        self.assertTrue(player.effects)
        self.assertTrue(player.attributes)

    def test_embedded_player_merge_keeps_playerdata(self) -> None:
        """level.dat(Player) 与 playerdata 是同一个玩家，只保留一份。"""
        world = open_world(self.path)
        self.assertEqual(len(world.players), 1)
        player = world.players["11111111-2222-3333-4444-555555555555"]
        # 独立文件内容更新，所以以它为准
        self.assertEqual(player.source, "playerdata")
        self.assertTrue(getattr(world, "notes", []))

    def test_containers(self) -> None:
        world = open_world(self.path)
        paths = {info.path for info in world.containers("level")}
        self.assertIn("Data.Player.Inventory", paths)
        self.assertIn("Data.Player.EnderItems", paths)

    def test_maps(self) -> None:
        world = open_world(self.path)
        info = world.maps[0]
        # 原版 map_*.dat 是 {data:{scale:…}}，读取时要下钻一层
        self.assertIsNotNone(info.root.get_child("data"))
        self.assertEqual(info.scale, 2)
        self.assertTrue(info.locked)
        self.assertTrue(info.tracking)
        self.assertEqual(info.colors_bytes, 4)
        self.assertEqual(info.x_center, 32)
        self.assertEqual(info.z_center, -16)
        self.assertEqual(info.dimension, "minecraft:overworld")

    def test_mods_read_only(self) -> None:
        world = open_world(self.path)
        self.assertEqual(read_mod_list(world.level_root)[0]["mod_id"], "create")

    def test_progress_callback_called(self) -> None:
        seen: list[str] = []
        open_world(self.path, progress=lambda _d, _t, text: seen.append(text))
        self.assertTrue(any("玩家" in text for text in seen))

    def test_rejects_non_save_directory(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-nosave-"))
        world = open_world(tmp)
        self.assertFalse(world.is_open())
        self.assertTrue(world.errors)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_level_dat_warns(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-nolevel-"))
        (tmp / "region").mkdir()
        self.assertTrue(looks_like_save_dir(tmp))
        world = open_world(tmp)
        self.assertIn("找不到 level.dat —— 这不是一个完整的存档目录",
                      world.errors)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_find_save_dirs(self) -> None:
        root = _fixture_root() / "discover" / ".minecraft"
        for name in ("A", "B"):
            build_world(f"discover/.minecraft/saves/{name}")
        found = find_save_dirs(root)
        self.assertEqual(len(found), 2)
        self.assertTrue(all(p.name in ("A", "B") for p in found))

    def test_find_save_dirs_accepts_save_itself(self) -> None:
        self.assertEqual(find_save_dirs(self.path), [self.path.resolve()])


class TestWorldEditing(unittest.TestCase):
    def setUp(self) -> None:
        self.path = build_world("edit")
        self.world = open_world(self.path, read_only=False)

    def tearDown(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def test_set_time_and_weather(self) -> None:
        self.world.set_time(day_time=18000, time=30000)
        self.world.set_weather("thunder", duration=100)
        self.assertEqual(self.world.field_value("DayTime"), 18000)
        self.assertEqual(self.world.field_value("thundering"), 1)
        self.assertEqual(self.world.field_value("thunderTime"), 100)
        self.assertTrue(self.world.dirty)

    def test_set_weather_rejects_unknown(self) -> None:
        with self.assertRaises(ValueError):
            self.world.set_weather("snow")

    def test_set_difficulty_by_name_and_value(self) -> None:
        self.world.set_difficulty("hard", locked=True)
        self.assertEqual(self.world.field_value("Difficulty"), 3)
        self.assertEqual(self.world.field_value("DifficultyLocked"), 1)
        self.world.set_difficulty(0)
        self.assertEqual(self.world.field_value("Difficulty"), 0)
        with self.assertRaises(ValueError):
            self.world.set_difficulty("nightmare")

    def test_set_spawn_and_border(self) -> None:
        self.world.set_spawn(1, 2, 3)
        data = self.world.data
        self.assertEqual(int(data.get_child("SpawnX").value), 1)
        self.assertEqual(int(data.get_child("SpawnY").value), 2)
        self.assertEqual(int(data.get_child("SpawnZ").value), 3)
        self.world.set_world_border(center_x=100, center_z=-200, size=500)
        border = self.world.get_field("WorldBorder")
        self.assertIsNotNone(border)
        self.assertEqual(border.get_child("CenterX").value, 100.0)
        self.assertEqual(border.get_child("CenterZ").value, -200.0)
        self.assertEqual(border.get_child("Size").value, 500.0)
        # 刷新后总览应反映新值
        self.world.sync_view()
        self.assertEqual(self.world.overview.spawn, (1, 2, 3))
        self.assertEqual(self.world.overview.world_border["center_x"], 100.0)

    def test_set_gamerule_creates_when_missing(self) -> None:
        self.world.set_gamerule("doVinesSpread", "false")
        rules = self.world.get_field("GameRules")
        self.assertEqual(rules.get_child("doVinesSpread").value, "false")
        self.world.set_gamerule("keepInventory", "true")
        self.assertEqual(rules.get_child("keepInventory").value, "true")

    def test_set_level_name_and_commands(self) -> None:
        self.world.set_level_name("改名了")
        self.world.set_allow_commands(False)
        self.world.set_game_type("survival")
        self.assertEqual(self.world.field_value("LevelName"), "改名了")
        self.assertEqual(self.world.field_value("allowCommands"), 0)
        self.assertEqual(self.world.field_value("GameType"), 0)

    def test_set_game_type_rejects_unknown(self) -> None:
        with self.assertRaises(ValueError):
            self.world.set_game_type("god")

    def test_player_field_write(self) -> None:
        uuid = "11111111-2222-3333-4444-555555555555"
        self.world.set_player_health(uuid, 7.5)
        self.world.set_player_xp(uuid, level=30, total=500)
        self.world.set_player_gamemode(uuid, "adventure")
        self.world.set_player_pos(uuid, 1.0, 2.0, 3.0)
        self.world.sync_view()
        player = self.world.players[uuid]
        self.assertEqual(player.health, 7.5)
        self.assertEqual(player.xp_level, 30)
        self.assertEqual(player.game_type, "adventure")
        self.assertEqual(player.pos, (1.0, 2.0, 3.0))
        root = self.world.player_root(uuid)
        self.assertEqual(float(root.get_child("Health").value), 7.5)
        self.assertEqual(root.get_child("playerGameType").value, 2)
        self.assertEqual([c.value for c in root.get_child("Pos").children],
                         [1.0, 2.0, 3.0])

    def test_playerdata_player_marks_its_own_file_dirty(self) -> None:
        uuid = "11111111-2222-3333-4444-555555555555"
        self.world._level_dirty = False
        self.world.set_player_health(uuid, 3.0)
        player = self.world.players[uuid]
        self.assertTrue(player.dirty)
        self.assertIsNotNone(player.path)
        self.assertIn(player.path, self.world._dirty_files())

    def test_embedded_player_marks_level_dirty(self) -> None:
        """没有任何 playerdata 文件时，玩家改的是 level.dat 里的内嵌节点。"""
        uuid = "11111111-2222-3333-4444-555555555555"
        player = self.world.players[uuid]
        player.source = "level.dat"
        player.path = None
        player.root = self.world.embedded_player_node()
        self.world._level_dirty = False
        self.world.set_player_health(uuid, 3.0)
        self.assertTrue(self.world._level_dirty)
        self.assertEqual(float(self.world.embedded_player_node()
                               .get_child("Health").value), 3.0)

    def test_grow_items_first_empty(self) -> None:
        uuid = "11111111-2222-3333-4444-555555555555"
        written = self.world.grow_items(uuid, "Inventory", "minecraft:diamond",
                                        10)
        self.assertEqual(written, 10)
        items = self.world.containers(uuid)
        inventory = next(info for info in items if info.path == "Inventory")
        self.assertEqual(inventory.item_count, 3)

    def test_grow_items_merges_stack(self) -> None:
        uuid = "11111111-2222-3333-4444-555555555555"
        root = self.world.player_root(uuid)
        inventory = root.get_child("Inventory")
        # 清掉样板里的石头（那是满堆叠，没法再合并），换成一个 count=20 的
        for child in list(inventory.children):
            if str(child.get_child("id").value) == "minecraft:stone":
                inventory.remove_child(child)
        inventory.add_child(
            SnbtParser('{Slot:9b,id:"minecraft:stone",count:20}').parse())
        written = self.world.grow_items(uuid, "Inventory",
                                        "minecraft:stone", 10, mode="merge")
        self.assertEqual(written, 10)
        after = [child for child in inventory.children
                 if str(child.get_child("id").value) == "minecraft:stone"]
        self.assertEqual(len(after), 1)
        self.assertEqual(int(after[0].get_child("count").value), 30)

    def test_grow_items_merge_does_not_add_slot(self) -> None:
        uuid = "11111111-2222-3333-4444-555555555555"
        root = self.world.player_root(uuid)
        before = len(root.get_child("Inventory").children)
        # 背包里没有钻石，merge 不应该新建槽位
        written = self.world.grow_items(uuid, "Inventory",
                                        "minecraft:diamond", 5, mode="merge")
        self.assertEqual(written, 0)
        self.assertEqual(len(root.get_child("Inventory").children), before)
        self.assertTrue(self.world.warnings)

    def test_grow_items_first_empty_adds_slot(self) -> None:
        uuid = "11111111-2222-3333-4444-555555555555"
        root = self.world.player_root(uuid)
        before = len(root.get_child("Inventory").children)
        written = self.world.grow_items(uuid, "Inventory",
                                        "minecraft:diamond", 5)
        self.assertEqual(written, 5)
        self.assertEqual(len(root.get_child("Inventory").children), before + 1)

    def test_grow_items_with_components(self) -> None:
        uuid = "11111111-2222-3333-4444-555555555555"
        self.world.grow_items(uuid, "Inventory", "minecraft:paper", 1,
                              components='custom_name="测试"')
        root = self.world.player_root(uuid)
        paper = [c for c in root.get_child("Inventory").children
                 if str(c.get_child("id").value) == "minecraft:paper"][0]
        components = paper.get_child("components")
        self.assertIsNotNone(components)
        self.assertIn("minecraft:custom_name", 
                      {str(c.key) for c in components.children})

    def test_grow_items_overwrite_slot(self) -> None:
        uuid = "11111111-2222-3333-4444-555555555555"
        self.world.grow_items(uuid, "Inventory", "minecraft:gold_ingot", 5,
                              mode="overwrite", slot=0)
        root = self.world.player_root(uuid)
        first = [c for c in root.get_child("Inventory").children
                 if int(c.get_child("Slot").value) == 0][0]
        self.assertEqual(str(first.get_child("id").value),
                         "minecraft:gold_ingot")

    def test_set_node_value_and_read_back(self) -> None:
        self.world.set_node_value("level", "Data.LevelName", "新名字")
        self.assertEqual(self.world.get_node_value("level", "Data.LevelName"),
                         "新名字")

    def test_set_node_value_unknown_target(self) -> None:
        with self.assertRaises(KeyError):
            self.world.set_node_value("no-such-player", "Health", 1)

    def test_save_round_trip_keeps_format(self) -> None:
        original = self.path / "level.dat"
        before = original.read_bytes()
        self.world.set_level_name("写回测试")
        written = self.world.save()
        self.assertEqual(len(written), 1)
        # gzip 容器头保持不变
        self.assertEqual(original.read_bytes()[:2], b"\x1f\x8b")
        self.assertNotEqual(original.read_bytes(), before)
        reopened = open_world(self.path)
        self.assertEqual(reopened.overview.name, "写回测试")

    def test_save_refuses_when_locked(self) -> None:
        world = open_world(self.path)
        with self.assertRaises(PermissionError):
            world.save()

    def test_save_after_unlock_flag(self) -> None:
        world = open_world(self.path)
        world.set_level_name = world.set_level_name              # noqa: B010
        with self.assertRaises(PermissionError):
            world.save()
        world.unlock()
        self.assertFalse(world.read_only)


class TestBackups(unittest.TestCase):
    def setUp(self) -> None:
        self.path = build_world("backup")
        self.world = open_world(self.path, read_only=False)

    def tearDown(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def test_backup_and_list(self) -> None:
        folder = self.world.create_backup(label="测试备份")
        self.assertIsNotNone(folder)
        self.assertTrue((folder / "manifest.json").is_file())
        self.assertTrue((folder / "level.dat").is_file())
        entries = self.world.list_backups()
        self.assertEqual(entries[0]["label"], "测试备份")

    def test_backup_names_do_not_collide(self) -> None:
        first = self.world.create_backup(label="一")
        second = self.world.create_backup(label="二")
        self.assertNotEqual(first, second)

    def test_restore_backup_round_trip(self) -> None:
        self.world.set_level_name("改坏了")
        self.world.save()
        folder = self.world.create_backup(label="改坏前")  # 已经改坏了，仅测试机制
        self.assertTrue(folder)
        # 真正的回滚：先备份好版本，改坏，再回滚
        good = self.world.create_backup(label="好版本")
        self.world.set_level_name("又坏了")
        self.world.save()
        self.assertEqual(open_world(self.path).overview.name, "又坏了")
        self.world.restore_backup(good.name)
        self.assertEqual(open_world(self.path).overview.name, "改坏了")

    def test_restore_missing_backup(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.world.restore_backup("19990101-000000")

    def test_snapshot_world(self) -> None:
        folder = snapshot_world(self.world, label="自动快照")
        self.assertIsNotNone(folder)
        self.assertTrue(folder.name)


class TestRegionIndex(unittest.TestCase):
    def setUp(self) -> None:
        self.path = build_world("region")
        self.world = open_world(self.path)

    def tearDown(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def test_index_reads_position_table(self) -> None:
        region = read_region_index(self.path / "region" / "r.0.0.mca")
        self.assertEqual(region.error, "")
        self.assertEqual(len(region.chunks), 1)
        chunk = region.chunks[0]
        self.assertEqual((chunk.x, chunk.z), (0, 0))
        self.assertEqual(chunk.offset, 2)
        # 测试存档故意留出 2 个扇区，给「原地等长替换」留空间
        self.assertGreaterEqual(chunk.sectors, 1)
        self.assertTrue(chunk.exists())
        self.assertEqual(region.present, 1)

    def test_index_ignores_sparse_entries(self) -> None:
        region = read_region_index(self.path / "region" / "r.0.0.mca")
        self.assertLessEqual(len(region.chunks), 1024)
        for chunk in region.chunks:
            self.assertTrue(chunk.offset or chunk.sectors)

    def test_reads_chunk_lazily(self) -> None:
        chunk = SnbtParser('{DataVersion:3955,Entities:[]}').parse()
        raw = node_to_nbt_bytes(chunk, compress="none", root_name="")
        header = bytearray(8192)
        struct.pack_into(">I", header, 0, (2 << 8) | 1)
        body = bytearray(SECTOR_SIZE)
        struct.pack_into(">IB", body, 0, len(raw) + 1, 3)   # 3 = 未压缩
        body[5:5 + len(raw)] = raw
        path = self.path / "region" / "r.0.0.mca"
        path.write_bytes(bytes(header) + bytes(body))
        node = read_region_chunk(path, 0, 0)
        self.assertIsNotNone(node)
        self.assertEqual(node.kind, SnbtKind.COMPOUND)
        self.assertEqual(node.get_child("DataVersion").value, 3955)

    def test_reads_chunk_with_zlib_nbt(self) -> None:
        chunk = SnbtParser(
            '{DataVersion:3955,Entities:[{id:"minecraft:zombie",'
            'Health:20.0f,Pos:[0.5d,64.0d,0.5d]}]}').parse()
        raw = node_to_nbt_bytes(chunk, compress="none", root_name="")
        payload = zlib.compress(raw)
        header = bytearray(8192)
        struct.pack_into(">I", header, 0, (2 << 8) | 2)
        body = bytearray(2 * SECTOR_SIZE)
        struct.pack_into(">IB", body, 0, len(payload) + 1, 2)
        body[5:5 + len(payload)] = payload
        path = self.path / "region" / "r.0.0.mca"
        path.write_bytes(bytes(header) + bytes(body))
        world = open_world(self.path)
        entities = scan_world_entities(world)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].entity_id, "minecraft:zombie")
        self.assertEqual(entities[0].source_chunk, (0, 0))

    def test_missing_chunk_returns_none(self) -> None:
        header = bytearray(8192)
        path = self.path / "region" / "r.0.0.mca"
        path.write_bytes(bytes(header))
        self.assertIsNone(read_region_chunk(path, 0, 0))

    def test_out_of_range_chunk(self) -> None:
        self.assertIsNone(
            read_region_chunk(self.path / "region" / "r.0.0.mca", 99, 0))

    def test_bad_filename(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-region-"))
        path = tmp / "notaregion.mca"
        path.write_bytes(b"\x00" * 8192)
        region = read_region_index(path)
        self.assertIn("不是", region.error)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_region_index_has_dimension(self) -> None:
        region = read_region_index(
            self.path / "region" / "r.0.0.mca",
            dimension="minecraft:overworld")
        self.assertEqual(region.dimension, "minecraft:overworld")


class TestHealthCheck(unittest.TestCase):
    def tearDown(self) -> None:
        shutil.rmtree(_fixture_root() / "health", ignore_errors=True)

    def test_healthy_world_reports_info_only(self) -> None:
        build_world("health")
        world = open_world(_fixture_root() / "health")
        issues = health_check(world)
        self.assertTrue(all(issue.level == "info" for issue in issues))

    def test_detects_broken_region_entry(self) -> None:
        path = build_world("health")
        region = path / "region" / "r.0.0.mca"
        header = bytearray(region.read_bytes()[:8192])
        struct.pack_into(">I", header, 0, (9999 << 8) | 1)   # 越界偏移
        region.write_bytes(bytes(header) + b"\x00" * SECTOR_SIZE)
        world = open_world(path)
        issues = health_check(world)
        self.assertTrue(any("越界" in issue.title for issue in issues))

    def test_detects_missing_datapack(self) -> None:
        path = build_world("health")
        shutil.rmtree(path / "datapacks", ignore_errors=True)
        world = open_world(path)
        issues = health_check(world)
        self.assertTrue(any("数据包" in issue.title for issue in issues))

    def test_flags_old_data_version(self) -> None:
        path = build_world("health")
        doc = NbtDocument.load(path / "level.dat")
        doc.root.get_child("Data").get_child("DataVersion").value = 1631
        save_nbt_file(doc.root, path / "level.dat", compress=doc.compression,
                      root_name=doc.root_name)
        world = open_world(path)
        issues = health_check(world)
        self.assertTrue(any("版本" in issue.title for issue in issues))

    def test_flags_oversized_level_dat(self) -> None:
        path = build_world("health")
        world = open_world(path)
        world.level.source_size = 32 * 1024 * 1024
        issues = health_check(world)
        self.assertTrue(any("过大" in issue.title for issue in issues))

    def test_flags_missing_region(self) -> None:
        path = build_world("health", with_region=False)
        world = open_world(path)
        issues = health_check(world)
        self.assertTrue(any("区域文件" in issue.title for issue in issues))

    def test_empty_world_reports_error(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-health-"))
        issues = health_check(WorldSave(tmp))
        self.assertTrue(any(issue.level == "error" for issue in issues))
        shutil.rmtree(tmp, ignore_errors=True)


class TestDataVersionsTable(unittest.TestCase):
    def test_known_versions_has_1211(self) -> None:
        self.assertEqual(KNOWN_DATA_VERSIONS[3955], "1.21.1")

    def test_difficulty_and_gamemode_tables(self) -> None:
        self.assertEqual(DIFFICULTIES[3], "hard")
        self.assertEqual(GAME_MODES[3], "spectator")


class TestSaveFileStaysWritable(unittest.TestCase):
    """回归：写回后必须还能被本工具打开，且根标签名与压缩方式不变。"""

    def setUp(self) -> None:
        self.path = build_world("rewrite")

    def tearDown(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def test_compression_and_root_name_preserved(self) -> None:
        for compress in ("gzip", "none", "zlib"):
            target = self.path / f"level_{compress}.dat"
            save_nbt_file(SnbtParser('{Data:{LevelName:"x"}}').parse(), target,
                          compress=compress, root_name="")
            doc = NbtDocument.load(target)
            self.assertEqual(doc.compression, compress)
            doc.root.get_child("Data").get_child("LevelName").value = "y"
            doc.save()
            again = NbtDocument.load(target)
            self.assertEqual(again.compression, compress)
            self.assertEqual(again.root.get_child("Data")
                             .get_child("LevelName").value, "y")

    def test_named_root_preserved(self) -> None:
        target = self.path / "named.dat"
        save_nbt_file(SnbtParser('{a:1}').parse(), target, compress="none",
                      root_name="hello")
        doc = NbtDocument.load(target)
        self.assertEqual(doc.root_name, "hello")
        doc.save()
        self.assertEqual(NbtDocument.load(target).root_name, "hello")


class TestWorldCacheModule(unittest.TestCase):
    """世界索引缓存：命中 / 过期 / 玩家与区域表。"""

    def setUp(self) -> None:
        from app.storage.world_cache import WorldIndexCache
        from app.storage import Database
        self.tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-cache-"))
        self.db = Database(self.tmp / "cache.db")
        self.cache = WorldIndexCache(self.db)
        self.path = build_world("cache")

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.path, ignore_errors=True)

    def test_store_and_load(self) -> None:
        world = open_world(self.path)
        self.cache.store(world)
        entry = self.cache.load(self.path)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["name"], "测试世界")
        self.assertFalse(entry["_stale"])

    def test_signature_marks_stale_after_touch(self) -> None:
        world = open_world(self.path)
        self.cache.store(world)
        os.utime(self.path / "level.dat", (1, 1))
        entry = self.cache.load(self.path)
        self.assertTrue(entry["_stale"])

    def test_players_and_regions_tables(self) -> None:
        world = open_world(self.path)
        self.cache.store(world)
        players = self.cache.players(self.path)
        self.assertEqual(len(players), 1)
        self.assertEqual(players[0]["uuid"],
                         "11111111-2222-3333-4444-555555555555")
        regions = self.cache.regions(self.path)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["present"], 1)

    def test_recent_and_forget(self) -> None:
        world = open_world(self.path)
        self.cache.store(world)
        self.assertTrue(self.cache.recent())
        self.cache.forget(self.path)
        self.assertIsNone(self.cache.load(self.path))

    def test_missing_world_returns_none(self) -> None:
        self.assertIsNone(self.cache.load(self.tmp / "nope"))

    def test_cached_overview_line(self) -> None:
        from app.storage.world_cache import cached_overview_line
        world = open_world(self.path)
        self.cache.store(world)
        line = cached_overview_line(self.cache.load(self.path))
        self.assertIn("测试世界", line)


class TestSnbtHexNumbers(unittest.TestCase):
    """新增：SNBT 支持原版也接受的 0x 十六进制数字写法。"""

    def test_hex_in_int_array(self) -> None:
        node = SnbtParser("[I; 0x10, 0xFF]").parse()
        self.assertEqual([child.value for child in node.children], [16, 255])

    def test_hex_with_suffix(self) -> None:
        node = SnbtParser("{a:0x7Fb,b:0x1000L}").parse()
        self.assertEqual(node.get_child("a").kind, SnbtKind.BYTE)
        self.assertEqual(node.get_child("a").value, 127)
        self.assertEqual(node.get_child("b").kind, SnbtKind.LONG)
        self.assertEqual(node.get_child("b").value, 4096)

    def test_hex_byte_wraps_to_signed(self) -> None:
        node = SnbtParser("{a:0xFFb}").parse()
        self.assertEqual(node.get_child("a").value, -1)

    def test_decimal_still_works(self) -> None:
        node = SnbtParser("{a:42,b:-7,c:1.5d}").parse()
        self.assertEqual(node.get_child("a").value, 42)
        self.assertEqual(node.get_child("b").value, -7)
        self.assertAlmostEqual(node.get_child("c").value, 1.5)

    def test_serialize_hex_round_trip_is_decimal(self) -> None:
        text = serialize(SnbtParser("[I; 0x1F]").parse(), pretty=False)
        self.assertEqual(text, "[I;31]")


if __name__ == "__main__":
    unittest.main()
