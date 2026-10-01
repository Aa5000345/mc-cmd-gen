"""世界编辑会话（事务 / 撤销 / 备份）+ 命令 ↔ 存档双向桥接。"""
from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from tests import TEST_ROOT  # noqa: F401  （导入即设置环境变量）

from app.core import bridge
from app.core.bridge import (
    OPERATION_KINDS, OPERATION_RUNNERS, BridgeOperation, apply_operation,
    cn_number, coords_to_commands, item_to_give_operation,
    nbt_path_to_command, operation_runner, parse_cn_world_request,
    player_to_execute_commands, player_to_selector, preview_operation,
    split_command, split_item_argument, world_rules_to_commands,
)
from app.core.snbt import SnbtKind, serialize
from app.core.world import open_world
from app.core.world_session import (
    UNDO_LIMIT, WorldDiff, WorldSession, diff_roots,
)

PLAYER = "11111111-2222-3333-4444-555555555555"


def build_world(name: str = "bridge") -> Path:
    import sys
    if str(TEST_ROOT) not in sys.path:
        sys.path.insert(0, str(TEST_ROOT))
    from tools.fake_world import build_fake_world
    root = Path(TEST_ROOT) / "build" / "bridge-worlds" / name
    if root.exists():
        shutil.rmtree(root)
    return build_fake_world(root)


class TestDiff(unittest.TestCase):
    def test_detects_scalar_change(self) -> None:
        from app.core.snbt import SnbtParser
        before = SnbtParser('{Data:{DayTime:1L}}').parse()
        after = SnbtParser('{Data:{DayTime:2L}}').parse()
        entries = diff_roots(before, after)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].path, "Data.DayTime")
        self.assertEqual(entries[0].kind, "change")
        self.assertIn("1L", entries[0].before)
        self.assertIn("2L", entries[0].after)

    def test_detects_add_and_remove(self) -> None:
        from app.core.snbt import SnbtParser
        before = SnbtParser('{a:1,b:2}').parse()
        after = SnbtParser('{a:1,c:3}').parse()
        kinds = {e.path: e.kind for e in diff_roots(before, after)}
        self.assertEqual(kinds["b"], "remove")
        self.assertEqual(kinds["c"], "add")

    def test_detects_list_length_change(self) -> None:
        from app.core.snbt import SnbtParser
        before = SnbtParser('{Items:[{id:"a"}]}').parse()
        after = SnbtParser('{Items:[{id:"a"},{id:"b"}]}').parse()
        entries = diff_roots(before, after)
        self.assertTrue(entries)

    def test_diff_text_and_dicts(self) -> None:
        from app.core.snbt import SnbtParser
        diff = WorldDiff(entries=[])
        self.assertFalse(diff)
        self.assertEqual(diff.text(), "（没有差异）")
        before = SnbtParser('{x:1}').parse()
        after = SnbtParser('{x:2}').parse()
        diff.entries = diff_roots(before, after, "level")
        self.assertTrue(diff)
        self.assertIn("x", diff.text())
        self.assertEqual(diff.as_dicts()[0]["target"], "level")

    def test_entry_summary_prefixes(self) -> None:
        from app.core.snbt import SnbtParser
        before = SnbtParser('{a:1}').parse()
        after = SnbtParser('{a:2,b:3}').parse()
        text = "\n".join(e.summary() for e in diff_roots(before, after,
                                                         "player:X"))
        self.assertIn("player:X", text)


class TestWorldSession(unittest.TestCase):
    def setUp(self) -> None:
        self.path = build_world("session")
        self.session = WorldSession.open(self.path)

    def tearDown(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def test_default_read_only(self) -> None:
        self.assertTrue(self.session.read_only)
        self.assertTrue(self.session.is_open())
        self.assertIn("只读", self.session.summary_line())

    def test_validate_reports_read_only(self) -> None:
        self.assertIn("只读", "；".join(self.session.validate()))

    def test_unlock_and_lock(self) -> None:
        self.session.unlock()
        self.assertFalse(self.session.read_only)
        self.assertEqual(self.session.validate(), [])
        self.session.lock()
        self.assertTrue(self.session.read_only)

    def test_transaction_preview_does_not_write(self) -> None:
        self.session.unlock()
        before = (self.path / "level.dat").stat().st_mtime_ns
        tx = self.session.transaction("改时间")
        with tx:
            self.session.world.set_time(day_time=18000)
            diff = tx.preview()
        self.assertEqual(len(diff), 1)
        self.assertEqual((self.path / "level.dat").stat().st_mtime_ns, before)
        # 预览后内存模型要还原（不能留下未提交的改动）
        self.assertEqual(self.session.world.overview.day_time, 6000)

    def test_transaction_commit_writes_and_backs_up(self) -> None:
        self.session.unlock()
        tx = self.session.transaction("改时间")
        with tx:
            self.session.world.set_time(day_time=18000)
            result = tx.commit()
        self.assertTrue(result.ok)
        self.assertTrue(result.applied)
        self.assertIsNotNone(result.backup)
        self.assertTrue(any(p.name == "level.dat" for p in result.written))
        reopened = open_world(self.path)
        self.assertEqual(reopened.overview.day_time, 18000)
        self.assertEqual(self.session.list_backups()[0]["label"], "改时间")

    def test_transaction_without_commit_is_dry_run(self) -> None:
        self.session.unlock()
        before = (self.path / "level.dat").stat().st_mtime_ns
        with self.session.transaction("dry"):
            self.session.world.set_time(day_time=123)
        self.assertEqual((self.path / "level.dat").stat().st_mtime_ns, before)
        self.assertEqual(self.session.world.overview.day_time, 6000)

    def test_transaction_rolls_back_on_exception(self) -> None:
        self.session.unlock()
        before = (self.path / "level.dat").stat().st_mtime_ns
        with self.assertRaises(RuntimeError):
            with self.session.transaction("boom"):
                self.session.world.set_time(day_time=999)
                raise RuntimeError("boom")
        self.assertEqual((self.path / "level.dat").stat().st_mtime_ns, before)
        self.assertEqual(self.session.world.overview.day_time, 6000)

    def test_no_change_skips_write(self) -> None:
        self.session.unlock()
        tx = self.session.transaction("无变化")
        with tx:
            result = tx.commit()
        self.assertTrue(result.ok)
        self.assertFalse(result.applied)
        self.assertIn("没有变化", result.message)

    def test_commit_refuses_in_read_only(self) -> None:
        """只读时 commit 返回失败结果，而不是把异常抛给 Qt 槽。"""
        tx = self.session.transaction("只读")
        with tx:
            result = tx.commit()
        self.assertFalse(result.ok)
        self.assertFalse(result.applied)
        self.assertIn("只读", result.message)

    def test_undo_and_redo(self) -> None:
        self.session.unlock()
        for value in (18000, 19000):
            tx = self.session.transaction(f"时间 {value}")
            with tx:
                self.session.world.set_time(day_time=value)
                tx.commit()
        self.assertEqual(self.session.world.overview.day_time, 19000)
        self.assertTrue(self.session.can_undo())
        self.session.undo()
        self.assertEqual(self.session.world.overview.day_time, 18000)
        self.session.undo()
        self.assertEqual(self.session.world.overview.day_time, 6000)
        self.assertFalse(self.session.can_undo())
        self.assertTrue(self.session.can_redo())
        self.session.redo()
        self.assertEqual(self.session.world.overview.day_time, 18000)

    def test_undo_can_revert_gamemode(self) -> None:
        """回归：内嵌/独立玩家都要能被撤销正确地还原。"""
        self.session.unlock()
        tx = self.session.transaction("换模式")
        with tx:
            self.session.world.set_player_gamemode(PLAYER, "adventure")
            tx.commit()
        self.assertEqual(self.session.world.players[PLAYER].game_type,
                         "adventure")
        self.session.undo()
        self.assertEqual(self.session.world.players[PLAYER].game_type,
                         "creative")
        root = self.session.world.player_root(PLAYER)
        self.assertEqual(int(root.get_child("playerGameType").value), 1)

    def test_undo_stack_limit(self) -> None:
        self.session.unlock()
        for index in range(UNDO_LIMIT + 5):
            tx = self.session.transaction(f"第 {index} 步")
            with tx:
                self.session.world.set_time(day_time=1000 + index)
                tx.commit()
        self.assertLessEqual(len(self.session.undo_stack), UNDO_LIMIT)

    def test_backup_restore_through_session(self) -> None:
        self.session.unlock()
        tx = self.session.transaction("好版本")
        with tx:
            self.session.world.set_time(day_time=4000)
            tx.commit()
        # 单独再存一份「好版本」的快照（世界时间线用法）
        good = self.session.world.create_backup(label="好版本快照")
        self.assertIsNotNone(good)
        tx2 = self.session.transaction("坏版本")
        with tx2:
            self.session.world.set_time(day_time=9000)
            tx2.commit()
        self.assertEqual(open_world(self.path).overview.day_time, 9000)
        self.session.restore_backup(good.name)
        self.assertEqual(self.session.world.overview.day_time, 4000)
        self.assertEqual(open_world(self.path).overview.day_time, 4000)

    def test_log_records_are_written(self) -> None:
        self.session.unlock()
        tx = self.session.transaction("记录测试")
        with tx:
            self.session.world.set_time(day_time=15000)
            tx.commit()
        self.assertTrue(self.session.log_records)
        self.assertEqual(self.session.log_records[0].action, "记录测试")
        self.assertTrue(self.session.log_records[0].applied)

    def test_reload_rereads_from_disk(self) -> None:
        self.session.unlock()
        tx = self.session.transaction("写盘")
        with tx:
            self.session.world.set_time(day_time=7000)
            tx.commit()
        self.session.reload()
        self.assertEqual(self.session.world.overview.day_time, 7000)

    def test_observers_notified(self) -> None:
        seen: list[int] = []
        self.session.subscribe(lambda _s: seen.append(1))
        self.session.restore(self.session.capture())
        self.assertTrue(seen)

    def test_preview_helper_rolls_back(self) -> None:
        self.session.unlock()
        diff = self.session.preview(
            lambda s: s.world.set_time(day_time=22222), "预览")
        self.assertEqual(len(diff), 1)
        self.assertEqual(self.session.world.overview.day_time, 6000)


class TestCommandParsing(unittest.TestCase):
    def test_split_keeps_nbt_together(self) -> None:
        tokens = split_command('give @p minecraft:stone[damage=5] 64')
        self.assertEqual(tokens[2], "minecraft:stone[damage=5]")
        self.assertEqual(tokens[3], "64")

    def test_split_keeps_braces_and_quotes(self) -> None:
        tokens = split_command(
            'summon minecraft:zombie ~ ~ ~ {CustomName:"僵尸 王"}')
        self.assertEqual(tokens[-1], '{CustomName:"僵尸 王"}')
        self.assertEqual(tokens[1], "minecraft:zombie")

    def test_split_strips_leading_slash(self) -> None:
        self.assertEqual(split_command("/say hi"), ["say", "hi"])

    def test_split_empty(self) -> None:
        self.assertEqual(split_command("   "), [])

    def test_split_item_argument(self) -> None:
        item, components = split_item_argument(
            'minecraft:stone[damage=5,unbreakable={}]')
        self.assertEqual(item, "minecraft:stone")
        self.assertEqual(components, "[damage=5,unbreakable={}]")

    def test_split_item_argument_without_components(self) -> None:
        self.assertEqual(split_item_argument("stone"), ("minecraft:stone", ""))

    def test_give_operation(self) -> None:
        operation = bridge.command_to_operation(
            "give @a minecraft:diamond 10")
        self.assertEqual(operation.kind, "give")
        self.assertEqual(operation.payload["item"], "minecraft:diamond")
        self.assertEqual(operation.payload["count"], 10)
        self.assertEqual(operation.payload["target"], "@a")

    def test_give_operation_with_components(self) -> None:
        operation = bridge.command_to_operation(
            'give @p minecraft:stone[custom_name="x"] 3')
        self.assertEqual(operation.payload["components"],
                         '[custom_name="x"]')

    def test_summon_operation(self) -> None:
        operation = bridge.command_to_operation(
            "summon minecraft:zombie 10 64 10 {IsBaby:0b}")
        self.assertEqual(operation.payload["entity"], "minecraft:zombie")
        self.assertEqual(operation.payload["pos"], "10 64 10")
        self.assertEqual(operation.payload["nbt"], "{IsBaby:0b}")

    def test_setblock_and_fill_operations(self) -> None:
        setblock = bridge.command_to_operation(
            "setblock 1 2 3 minecraft:chest")
        self.assertEqual(setblock.payload["pos"], "1 2 3")
        self.assertEqual(setblock.payload["block"], "minecraft:chest")
        fill = bridge.command_to_operation(
            "fill 0 0 0 5 5 5 minecraft:stone replace")
        self.assertEqual(fill.kind, "fill")
        self.assertEqual(fill.risk, "danger")

    def test_data_operation(self) -> None:
        operation = bridge.command_to_operation(
            'data modify entity @s Health set value 20.0f')
        self.assertEqual(operation.kind, "data")
        self.assertEqual(operation.payload["path"], "Health")
        self.assertEqual(operation.payload["value"], "20.0f")

    def test_time_weather_worldborder_gamerule(self) -> None:
        self.assertEqual(
            bridge.command_to_operation("time set 6000").payload["value"],
            6000)
        self.assertEqual(
            bridge.command_to_operation("time add 100").payload["action"],
            "add")
        self.assertEqual(
            bridge.command_to_operation("weather rain 600").payload["weather"],
            "rain")
        border = bridge.command_to_operation("worldborder center 1 2")
        self.assertEqual(border.payload["center_x"], 1.0)
        rule = bridge.command_to_operation("gamerule keepInventory true")
        self.assertEqual(rule.payload["rule"], "keepInventory")
        self.assertEqual(rule.payload["value"], "true")

    def test_effect_gamemode_xp(self) -> None:
        effect = bridge.command_to_operation(
            "effect give @a minecraft:night_vision 60 1")
        self.assertEqual(effect.payload["id"], "minecraft:night_vision")
        self.assertEqual(effect.payload["duration"], 60)
        self.assertEqual(effect.payload["amplifier"], 1)
        mode = bridge.command_to_operation("gamemode creative @a")
        self.assertEqual(mode.payload["mode"], "creative")
        xp = bridge.command_to_operation("xp add @p 30 levels")
        self.assertEqual(xp.payload["amount"], 30)

    def test_unknown_command_returns_none(self) -> None:
        self.assertIsNone(bridge.command_to_operation("say hi"))
        self.assertIsNone(bridge.command_to_operation(""))

    def test_operation_describe_and_summary(self) -> None:
        operation = bridge.command_to_operation("give @p minecraft:stone 5")
        self.assertIn("minecraft:stone", operation.describe())
        self.assertIn("给予物品", operation.summary())
        self.assertEqual(operation.as_dict()["kind"], "give")

    def test_all_operation_kinds_have_runner_or_note(self) -> None:
        for kind in OPERATION_KINDS:
            operation = BridgeOperation(kind=kind)
            self.assertTrue(operation.title)
            self.assertTrue(callable(operation_runner(operation)))

    def test_unknown_kind_runner_raises(self) -> None:
        runner = operation_runner(BridgeOperation(kind="no-such-kind"))
        with self.assertRaises(NotImplementedError):
            runner(None)


class TestBridgeApply(unittest.TestCase):
    def setUp(self) -> None:
        self.path = build_world("apply")
        self.session = WorldSession.open(self.path)
        self.session.unlock()

    def tearDown(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def _run(self, command: str, **kwargs):
        operation = bridge.command_to_operation(command, session=self.session)
        operation.runner = operation_runner(operation)
        return apply_operation(self.session, operation, **kwargs)

    def test_give_writes_into_inventory(self) -> None:
        result = self._run("give @p minecraft:diamond 10")
        self.assertTrue(result.ok, result.message)
        self.assertTrue(result.applied)
        info = [c for c in self.session.world.containers(PLAYER)
                if c.path == "Inventory"][0]
        self.assertEqual(info.item_count, 3)
        # 写盘后重新打开还在
        reopened = open_world(self.path)
        self.assertEqual(reopened.players[PLAYER].inventory_count, 3)

    def test_give_to_container_path(self) -> None:
        operation = BridgeOperation(
            kind="give", payload={"item": "minecraft:apple", "count": 3,
                                  "player": PLAYER, "path": "EnderItems"},
            runner=None)
        operation.runner = operation_runner(operation)
        result = apply_operation(self.session, operation)
        self.assertTrue(result.ok, result.message)
        info = [c for c in self.session.world.containers(PLAYER)
                if c.path == "EnderItems"][0]
        self.assertEqual(info.item_count, 2)

    def test_give_requires_container(self) -> None:
        operation = BridgeOperation(
            kind="give", payload={"item": "minecraft:apple", "count": 1,
                                  "player": "no-such-player"})
        operation.runner = operation_runner(operation)
        result = apply_operation(self.session, operation)
        self.assertFalse(result.ok)
        self.assertIn("找不到", result.message)

    def test_time_weather_gamerule(self) -> None:
        for command, check in (
                ("time set 13000",
                 lambda w: w.overview.day_time == 13000),
                ("weather thunder 100",
                 lambda w: w.overview.weather == "thunder"),
                ("gamerule keepInventory true",
                 lambda w: w.overview.gamerules.get("keepInventory") == "true"),
                ("gamemode adventure @p",
                 lambda w: w.players[PLAYER].game_type == "adventure")):
            result = self._run(command)
            self.assertTrue(result.ok, f"{command}: {result.message}")
            self.assertTrue(check(self.session.world), command)

    def test_worldborder(self) -> None:
        result = self._run("worldborder set 500")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(
            self.session.world.get_field("WorldBorder")
            .get_child("Size").value, 500.0)

    def test_effect(self) -> None:
        operation = BridgeOperation(
            kind="effect", payload={"id": "minecraft:speed", "duration": 60,
                                    "amplifier": 1, "player": "@p", "uuid":
                                    PLAYER})
        operation.runner = operation_runner(operation)
        result = apply_operation(self.session, operation)
        self.assertTrue(result.ok, result.message)
        root = self.session.world.player_root(PLAYER)
        effects = root.get_child("active_effects")
        self.assertEqual(len(effects.children), 2)

    def test_xp(self) -> None:
        operation = BridgeOperation(
            kind="xp", payload={"amount": 10, "uuid": PLAYER})
        operation.runner = operation_runner(operation)
        result = apply_operation(self.session, operation)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.session.world.players[PLAYER].xp_level, 15)

    def test_summon_appends_to_level_entities(self) -> None:
        self.session.world.data.add_child(
            _empty_list("Entities"))
        operation = BridgeOperation(
            kind="summon", payload={"entity": "minecraft:cow",
                                    "pos": "1 2 3", "nbt": "{CustomName:'x'}"})
        operation.runner = operation_runner(operation)
        result = apply_operation(self.session, operation)
        self.assertTrue(result.ok, result.message)
        entities = self.session.world.data.get_child("Entities")
        self.assertEqual(len(entities.children), 1)
        self.assertEqual(str(entities.children[0].get_child("id").value),
                         "minecraft:cow")

    def test_summon_without_entities_list_reports(self) -> None:
        operation = BridgeOperation(
            kind="summon", payload={"entity": "minecraft:cow", "pos": "~ ~ ~",
                                    "nbt": ""})
        operation.runner = operation_runner(operation)
        result = apply_operation(self.session, operation)
        self.assertFalse(result.ok)
        self.assertIn("Entities", result.message)

    def test_setblock_registers_block_entity(self) -> None:
        operation = BridgeOperation(
            kind="setblock", payload={"pos": "4 65 4",
                                      "block": "minecraft:chest"})
        operation.runner = operation_runner(operation)
        result = apply_operation(self.session, operation)
        self.assertTrue(result.ok, result.message)
        container = self.session.world.data.get_child("block_entities")
        self.assertEqual(len(container.children), 1)
        self.assertEqual(int(container.children[0].get_child("x").value), 4)

    def test_setblock_relative_pos_reports(self) -> None:
        operation = BridgeOperation(
            kind="setblock", payload={"pos": "~ ~ ~",
                                      "block": "minecraft:chest"})
        operation.runner = operation_runner(operation)
        result = apply_operation(self.session, operation)
        # 玩家坐标能解析成绝对坐标，所以这里应该成功
        self.assertTrue(result.ok, result.message)

    def test_fill_generates_commands_instead_of_writing(self) -> None:
        operation = bridge.command_to_operation(
            "fill 0 0 0 5 5 5 minecraft:stone", session=self.session)
        operation.runner = operation_runner(operation)
        result = apply_operation(self.session, operation)
        self.assertTrue(result.ok)
        self.assertIn("/fill", result.message)

    def test_data_writes_nbt_path(self) -> None:
        operation = bridge.command_to_operation(
            "data modify level Data.LevelName set value \"新名字\"",
            session=self.session)
        operation.runner = operation_runner(operation)
        result = apply_operation(self.session, operation)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.session.world.field_value("LevelName"), "新名字")

    def test_data_creates_missing_intermediate(self) -> None:
        operation = BridgeOperation(
            kind="data", payload={"ref": "level",
                                  "path": "Data.Custom.Deep.Value",
                                  "value": "42"},
            target="level")
        operation.runner = operation_runner(operation)
        result = apply_operation(self.session, operation)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(
            self.session.world.get_node_value("level",
                                              "Data.Custom.Deep.Value"), 42)

    def test_scene_applies_multiple_settings(self) -> None:
        operation = BridgeOperation(
            kind="scene", payload={"permanent_day": True, "no_weather": True,
                                   "peaceful": True, "spawn": "0 64 0",
                                   "border_size": 1000},
            runner=operation_runner(BridgeOperation(
                "scene", payload={"permanent_day": True, "no_weather": True,
                                  "peaceful": True, "spawn": "0 64 0",
                                  "border_size": 1000})))
        result = apply_operation(self.session, operation)
        self.assertTrue(result.ok, result.message)
        world = self.session.world
        self.assertEqual(world.overview.difficulty, "peaceful")
        self.assertEqual(world.overview.spawn, (0, 64, 0))
        self.assertEqual(world.overview.gamerules["doDaylightCycle"], "false")
        self.assertEqual(world.overview.world_border["size"], 1000.0)

    def test_apply_refused_when_read_only(self) -> None:
        self.session.lock()
        operation = bridge.command_to_operation(
            "time set 100", session=self.session)
        operation.runner = operation_runner(operation)
        result = apply_operation(self.session, operation)
        self.assertFalse(result.ok)
        self.assertIn("只读", result.message)

    def test_preview_reports_diff_without_writing(self) -> None:
        before = (self.path / "level.dat").stat().st_mtime_ns
        operation = bridge.command_to_operation(
            "time set 12345", session=self.session)
        operation.runner = operation_runner(operation)
        preview = preview_operation(self.session, operation)
        self.assertTrue(preview.ok, preview.message)
        self.assertEqual(len(preview.diff), 1)
        self.assertEqual((self.path / "level.dat").stat().st_mtime_ns, before)
        self.assertEqual(self.session.world.overview.day_time, 6000)

    def test_preview_without_world(self) -> None:
        empty = WorldSession()
        preview = preview_operation(empty, BridgeOperation(kind="time"))
        self.assertFalse(preview.ok)

    def test_transaction_log_has_source_and_target(self) -> None:
        operation = bridge.command_to_operation("time set 5000",
                                                session=self.session)
        operation.runner = operation_runner(operation)
        apply_operation(self.session, operation)
        record = self.session.log_records[0]
        self.assertEqual(record.source, "command")
        self.assertEqual(record.target, "level")
        self.assertTrue(record.backup)


class TestEditorToCommands(unittest.TestCase):
    def setUp(self) -> None:
        self.path = build_world("to-command")
        self.world = open_world(self.path)

    def tearDown(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def test_item_to_give_operation(self) -> None:
        from app.core.nbt_bridge import collect_items
        player = self.world.players[PLAYER]
        stack = collect_items(self.world.player_root(PLAYER))[0]
        operation = item_to_give_operation(stack, "@a")
        self.assertTrue(operation.commands)
        self.assertIn("give @a", operation.commands[0])
        self.assertEqual(operation.source, "editor")
        _ = player

    def test_entity_to_summon_operation(self) -> None:
        from app.core.nbt_bridge import EntityData
        entity = EntityData(entity_id="minecraft:zombie",
                            nbt="{IsBaby:0b}", display_name="僵尸")
        operation = bridge.entity_to_summon_operation(entity, "1 2 3")
        self.assertIn("summon minecraft:zombie 1 2 3", operation.commands[0])

    def test_container_to_commands(self) -> None:
        containers = [c for c in self.world.containers(PLAYER)
                      if c.path == "Inventory"]
        self.assertTrue(containers)
        operation = bridge.container_to_commands(
            containers[0], "@p", world=self.world, document=PLAYER)
        self.assertEqual(len(operation.commands), containers[0].item_count)
        self.assertIn("give @p", operation.commands[0])

    def test_nbt_path_to_command(self) -> None:
        command = nbt_path_to_command("Data.Player.Health", "20.0f")
        self.assertIn("data modify", command)
        self.assertIn("Data.Player.Health", command)
        self.assertIn("set value 20.0f", command)

    def test_player_selector_and_execute(self) -> None:
        player = self.world.players[PLAYER]
        player.name = "Steve"
        self.assertEqual(player_to_selector(player), "@a[name=Steve]")
        operation = player_to_execute_commands(player, "say hi")
        self.assertIn("execute as @a[name=Steve] at @s run say hi",
                      operation.commands[0])
        player.name = ""
        self.assertIn("uuid=", player_to_selector(player))

    def test_coords_to_commands(self) -> None:
        operation = coords_to_commands(1.5, 64.0, -3.25)
        self.assertIn("positioned 1.50 64.00 -3.25", operation.commands[0])
        self.assertIn("armor_stand", operation.commands[1])

    def test_world_rules_to_commands(self) -> None:
        operation = world_rules_to_commands(self.world)
        joined = "\n".join(operation.commands)
        self.assertIn("time set 6000", joined)
        self.assertIn("weather clear", joined)
        self.assertIn("gamerule keepInventory false", joined)
        self.assertIn("worldborder set", joined)

    def test_ops_to_commands_flattens(self) -> None:
        ops = [BridgeOperation(kind="give", commands=["a", "b"]),
               BridgeOperation(kind="time", commands=["c"])]
        self.assertEqual(bridge.ops_to_commands(ops), ["a", "b", "c"])


class TestChineseNlp(unittest.TestCase):
    def setUp(self) -> None:
        self.path = build_world("nlp")
        self.session = WorldSession.open(self.path)
        self.session.unlock()

    def tearDown(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def test_cn_number(self) -> None:
        self.assertEqual(cn_number("十"), 10)
        self.assertEqual(cn_number("二十"), 20)
        self.assertEqual(cn_number("三"), 3)
        self.assertEqual(cn_number("十二"), 12)
        self.assertEqual(cn_number("一百"), 100)
        self.assertEqual(cn_number("15"), 15)
        self.assertEqual(cn_number("10个"), 10)
        self.assertEqual(cn_number("僵尸"), None)

    def test_set_time(self) -> None:
        operations = parse_cn_world_request("把时间设置为夜晚",
                                            session=self.session)
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].kind, "time")
        self.assertEqual(operations[0].payload["value"], 13000)

    def test_set_weather(self) -> None:
        operations = parse_cn_world_request("天气改成下雨",
                                            session=self.session)
        self.assertEqual(operations[0].payload["weather"], "rain")

    def test_set_difficulty(self) -> None:
        operations = parse_cn_world_request("难度改成困难",
                                            session=self.session)
        self.assertTrue(operations)
        result = apply_operation(self.session, operations[0])
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.session.world.overview.difficulty, "hard")

    def test_permanent_day_and_no_weather(self) -> None:
        operations = parse_cn_world_request("永久白天", session=self.session)
        self.assertEqual(operations[0].kind, "scene")
        operations = parse_cn_world_request("禁止下雨", session=self.session)
        self.assertEqual(operations[0].kind, "scene")

    def test_set_border(self) -> None:
        operations = parse_cn_world_request("世界边界设置为 500",
                                            session=self.session)
        self.assertEqual(operations[0].payload["size"], 500)

    def test_fill_inventory_with_diamond_sword(self) -> None:
        operations = parse_cn_world_request("把背包塞满钻石剑",
                                            session=self.session)
        self.assertTrue(operations)
        operation = operations[0]
        self.assertEqual(operation.kind, "give")
        self.assertEqual(operation.payload["item"], "minecraft:diamond_sword")
        self.assertGreater(operation.payload["count"], 1)

    def test_spawn_zombies_at_spawn(self) -> None:
        operations = parse_cn_world_request("在出生点生成 3 只僵尸",
                                            session=self.session)
        self.assertEqual(len(operations), 3)
        self.assertTrue(all(o.kind == "summon" for o in operations))
        self.assertTrue(all(o.payload["entity"] == "minecraft:zombie"
                            for o in operations))
        # 生成位置围绕出生点
        spawn = self.session.world.overview.spawn
        self.assertIn(str(spawn[1]), operations[0].payload["pos"])

    def test_unknown_request_returns_empty(self) -> None:
        self.assertEqual(parse_cn_world_request("今天天气不错",
                                                session=self.session), [])
        self.assertEqual(parse_cn_world_request(""), [])

    def test_preview_then_apply_flow(self) -> None:
        """需求里的「先 diff 再应用」。"""
        operations = parse_cn_world_request("把时间设置为中午",
                                            session=self.session)
        before_day_time = self.session.world.overview.day_time
        preview = preview_operation(self.session, operations[0])
        self.assertTrue(preview.ok, preview.message)
        # 预览后内存模型必须还原
        self.assertEqual(self.session.world.overview.day_time, before_day_time)
        result = apply_operation(self.session, operations[0])
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.session.world.overview.day_time, 6000)
        self.assertEqual(open_world(self.path).overview.day_time, 6000)


def _empty_list(key: str):
    from app.core.snbt import SnbtNode
    return SnbtNode.new_list(key=key)


class TestBridgePayloads(unittest.TestCase):
    """结构化载荷：不能用纯文本糊过去。"""

    def test_operation_payload_is_structured(self) -> None:
        operation = bridge.command_to_operation(
            "give @p minecraft:stone[custom_name=\"x\"] 5")
        payload = operation.payload
        self.assertIsInstance(payload["count"], int)
        self.assertIsInstance(payload["components"], str)
        self.assertEqual(payload["mode"], "first_empty")

    def test_json_round_trip(self) -> None:
        operation = bridge.command_to_operation("time set 6000")
        text = json.dumps(operation.as_dict(), ensure_ascii=False)
        back = json.loads(text)
        self.assertEqual(back["kind"], "time")
        self.assertEqual(back["payload"]["value"], 6000)

    def test_operation_kinds_table_is_complete(self) -> None:
        for kind in OPERATION_RUNNERS:
            self.assertIn(kind, OPERATION_KINDS)

    def test_snbt_value_parse_error_reported(self) -> None:
        session = WorldSession()
        operation = BridgeOperation(
            kind="data", payload={"ref": "level", "path": "Data.X",
                                  "value": "{bad"})
        operation.runner = operation_runner(operation)
        result = apply_operation(session, operation)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
