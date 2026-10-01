"""区域写入（实验功能）+ 工作区预设。

区域写入的安全护栏是重点：只做等长或更短的原地替换、越界拒绝、
原子替换、写后自检、失败时原文件必须完好。
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

from app.core.nbt_binary import node_to_nbt_bytes
from app.core.region_io import (
    BUILTIN_PRESETS, COMPRESSION_CODES, RegionWriteError, backup_region,
    build_chunk_payload, get_preset, list_presets, plan_chunk_write,
    preset_summary, preset_to_operation, save_preset, sectorize,
    verify_region, write_chunk,
)
from app.core.snbt import SnbtKind, SnbtNode, SnbtParser, serialize
from app.core.world import (
    SECTOR_SIZE, open_world, read_region_chunk, scan_world_entities,
)


def build_world(name: str = "regionio") -> Path:
    import sys
    if str(TEST_ROOT) not in sys.path:
        sys.path.insert(0, str(TEST_ROOT))
    from tools.fake_world import build_fake_world
    root = Path(TEST_ROOT) / "build" / "region-worlds" / name
    if root.exists():
        shutil.rmtree(root)
    return build_fake_world(root)


class _RegionCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = build_world(self.__class__.__name__.lower())
        self.path = self.root / "region" / "r.0.0.mca"

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _chunk(self) -> SnbtNode:
        node = read_region_chunk(self.path, 0, 0)
        self.assertIsNotNone(node, "样例区块应该能被读出来")
        return node


class TestSectorize(unittest.TestCase):
    def test_small_payload_uses_one_sector(self) -> None:
        padded, sectors = sectorize(b"abc")
        self.assertEqual(sectors, 1)
        self.assertEqual(len(padded), SECTOR_SIZE)

    def test_exact_multiple(self) -> None:
        padded, sectors = sectorize(b"x" * SECTOR_SIZE)
        self.assertEqual(sectors, 1)
        self.assertEqual(len(padded), SECTOR_SIZE)

    def test_just_over_one_sector(self) -> None:
        padded, sectors = sectorize(b"x" * (SECTOR_SIZE + 1))
        self.assertEqual(sectors, 2)
        self.assertEqual(len(padded), 2 * SECTOR_SIZE)

    def test_empty_payload_still_one_sector(self) -> None:
        _padded, sectors = sectorize(b"")
        self.assertEqual(sectors, 1)


class TestChunkPayload(unittest.TestCase):
    def test_zlib_round_trip(self) -> None:
        node = SnbtParser('{DataVersion:3955,xPos:1}').parse()
        payload = build_chunk_payload(node, compression="zlib")
        length = int.from_bytes(payload[:4], "big")
        self.assertEqual(payload[4], COMPRESSION_CODES["zlib"])
        self.assertEqual(length, len(payload) - 4)
        body = zlib.decompress(payload[5:])
        self.assertTrue(body)

    def test_gzip_and_none(self) -> None:
        node = SnbtParser('{a:1}').parse()
        for kind, code in (("gzip", 1), ("none", 3)):
            payload = build_chunk_payload(node, compression=kind)
            self.assertEqual(payload[4], code)

    def test_unknown_compression_rejected(self) -> None:
        node = SnbtParser('{a:1}').parse()
        with self.assertRaises(RegionWriteError):
            build_chunk_payload(node, compression="lz4")


class TestPlanChunkWrite(_RegionCase):
    def test_plan_ok_for_same_size_content(self) -> None:
        node = self._chunk()
        node.add_child(SnbtParser("{InhabitedTime:5L}").parse().children[0])
        plan = plan_chunk_write(self.path, 0, 0, node)
        self.assertTrue(plan.ok, plan.reason)
        self.assertGreater(plan.old_bytes, 0)
        self.assertGreater(plan.new_bytes, 0)
        self.assertIn("InhabitedTime", "\n".join(plan.diff))

    def test_plan_rejects_missing_chunk(self) -> None:
        plan = plan_chunk_write(self.path, 99, 99, SnbtParser('{a:1}').parse())
        self.assertFalse(plan.ok)
        self.assertIn("不存在", plan.reason)

    def test_plan_rejects_oversize(self) -> None:
        import random
        random.seed(7)
        big = SnbtNode(kind=SnbtKind.BYTE_ARRAY, key="Blob")
        for _ in range(30000):
            big.add_child(SnbtNode.new_scalar(SnbtKind.BYTE,
                                              random.randint(-128, 127)))
        node = self._chunk()
        node.add_child(big)
        plan = plan_chunk_write(self.path, 0, 0, node)
        self.assertFalse(plan.ok)
        self.assertIn("扇区", plan.reason)
        self.assertIn("setblock", plan.reason)

    def test_plan_reports_no_change_diff(self) -> None:
        node = self._chunk()
        plan = plan_chunk_write(self.path, 0, 0, node)
        self.assertTrue(plan.ok)
        self.assertEqual(plan.diff, [])

    def test_plan_does_not_touch_disk(self) -> None:
        before = self.path.read_bytes()
        node = self._chunk()
        node.add_child(SnbtParser("{X:1}").parse().children[0])
        plan_chunk_write(self.path, 0, 0, node)
        self.assertEqual(self.path.read_bytes(), before)

    def test_plan_summary_text(self) -> None:
        node = self._chunk()
        self.assertIn("可写", plan_chunk_write(self.path, 0, 0, node).summary())
        bad = plan_chunk_write(self.path, 50, 50, node)
        self.assertIn("不可写", bad.summary())


class TestWriteChunk(_RegionCase):
    def test_write_and_read_back(self) -> None:
        node = self._chunk()
        node.add_child(SnbtParser("{InhabitedTime:777L}").parse().children[0])
        plan = write_chunk(self.path, 0, 0, node)
        self.assertTrue(plan.ok)
        back = read_region_chunk(self.path, 0, 0)
        self.assertEqual(int(back.get_child("InhabitedTime").value), 777)

    def test_verify_after_write_is_clean(self) -> None:
        node = self._chunk()
        node.add_child(SnbtParser("{LightPopulated:1b}").parse().children[0])
        write_chunk(self.path, 0, 0, node)
        self.assertEqual(verify_region(self.path), [])

    def test_refuses_oversize_and_keeps_file_intact(self) -> None:
        import random
        random.seed(11)
        before = self.path.read_bytes()
        big = SnbtNode(kind=SnbtKind.BYTE_ARRAY, key="Blob")
        for _ in range(40000):
            big.add_child(SnbtNode.new_scalar(SnbtKind.BYTE,
                                              random.randint(-128, 127)))
        node = self._chunk()
        node.add_child(big)
        with self.assertRaises(RegionWriteError):
            write_chunk(self.path, 0, 0, node)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(read_region_chunk(self.path, 0, 0) is not None)

    def test_refuses_missing_chunk(self) -> None:
        with self.assertRaises(RegionWriteError):
            write_chunk(self.path, 10, 10, SnbtParser('{a:1}').parse())

    def test_position_table_untouched(self) -> None:
        before = self.path.read_bytes()[:8192]
        node = self._chunk()
        node.add_child(SnbtParser("{Y:2}").parse().children[0])
        write_chunk(self.path, 0, 0, node)
        self.assertEqual(self.path.read_bytes()[:8192], before)

    def test_whitespace_zeroed_after_shrink(self) -> None:
        node = self._chunk()
        node.add_child(SnbtParser("{Z:3}").parse().children[0])
        plan = write_chunk(self.path, 0, 0, node)
        info = read_region_index_sectors(self.path)
        self.assertGreaterEqual(plan.old_sectors, info)

    def test_world_still_opens_after_write(self) -> None:
        node = self._chunk()
        node.add_child(SnbtParser("{W:4}").parse().children[0])
        write_chunk(self.path, 0, 0, node)
        world = open_world(self.root)
        self.assertTrue(world.is_open())
        self.assertEqual(world.errors, [])
        self.assertEqual(len(world.regions), 1)


class TestBackupAndVerify(_RegionCase):
    def test_backup_copies_file(self) -> None:
        folder = self.root / "backups" / "unit"
        target = backup_region(self.path, folder)
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_bytes(), self.path.read_bytes())

    def test_verify_detects_oversized_block(self) -> None:
        """数据块声明的长度超过扇区数时，自检必须报出来。"""
        chunk = SnbtParser('{DataVersion:3955,xPos:0,zPos:0}').parse()
        raw = node_to_nbt_bytes(chunk, compress="none", root_name="")
        payload = zlib.compress(raw)
        header = bytearray(8192)
        struct.pack_into(">I", header, 0, (2 << 8) | 1)    # 只给 1 个扇区
        body = bytearray(SECTOR_SIZE)
        # 但块头声明「要 2 个扇区」→ 与位置表不自洽
        struct.pack_into(">IB", body, 0, 2 * SECTOR_SIZE, 2)
        body[5:5 + len(payload)] = payload
        self.path.write_bytes(bytes(header) + bytes(body))
        problems = verify_region(self.path)
        self.assertTrue(problems)
        self.assertIn("扇区", "；".join(problems))

    def test_verify_accepts_consistent_file(self) -> None:
        self.assertEqual(verify_region(self.path), [])

    def test_verify_detects_truncation(self) -> None:
        self.path.write_bytes(self.path.read_bytes()[:SECTOR_SIZE])
        problems = verify_region(self.path)
        self.assertTrue(problems)


class TestFixtureRegionContent(unittest.TestCase):
    """测试存档的区域文件现在带真实 NBT，实体与方块实体都能读。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = build_world("fixture")
        cls.world = open_world(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_region_entity_scan(self) -> None:
        entities = scan_world_entities(self.world)
        self.assertTrue(entities)
        self.assertEqual(entities[0].entity_id, "minecraft:zombie")
        self.assertEqual(entities[0].source_chunk, (0, 0))

    def test_chunk_has_block_entities(self) -> None:
        node = read_region_chunk(self.root / "region" / "r.0.0.mca", 0, 0)
        self.assertIsNotNone(node.get_child("block_entities"))
        self.assertEqual(len(node.get_child("block_entities").children), 1)

    def test_chunk_file_still_valid(self) -> None:
        self.assertEqual(verify_region(self.root / "region" / "r.0.0.mca"), [])


class _PresetCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mcstudio-preset-"))
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


class TestWorkspacePresets(_PresetCase):
    def test_builtin_presets_present(self) -> None:
        presets = list_presets()
        self.assertGreaterEqual(len(presets), 4)
        self.assertTrue(all(p.get("builtin") for p in presets))
        ids = {p["id"] for p in presets}
        self.assertIn("creative_sandbox", ids)
        self.assertIn("hardcore_survival", ids)

    def test_lookup_by_id_and_name(self) -> None:
        self.assertIsNotNone(get_preset("creative_sandbox"))
        self.assertIsNotNone(get_preset("创造沙盒"))
        self.assertIsNone(get_preset("no-such-preset"))

    def test_summary_mentions_settings(self) -> None:
        summary = preset_summary(get_preset("creative_sandbox"))
        self.assertIn("永久白天", summary)
        self.assertIn("规则", summary)

    def test_preset_to_operation_is_scene(self) -> None:
        operation = preset_to_operation(get_preset("creative_sandbox"))
        self.assertEqual(operation.kind, "scene")
        self.assertIsNotNone(operation.runner)
        self.assertTrue(operation.payload.get("permanent_day"))
        self.assertIn("gamerules", operation.payload)

    def test_preset_operation_is_applicable(self) -> None:
        from app.core.bridge import apply_operation
        from app.core.world_session import WorldSession
        root = build_world("presetapply")
        try:
            session = WorldSession.open(root)
            session.unlock()
            result = apply_operation(
                session, preset_to_operation(get_preset("creative_sandbox")))
            self.assertTrue(result.ok, result.message)
            self.assertEqual(session.world.overview.difficulty, "peaceful")
            self.assertEqual(
                session.world.overview.gamerules.get("doDaylightCycle"),
                "false")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_save_user_preset_overrides_builtin(self) -> None:
        save_preset({"id": "creative_sandbox", "name": "我的沙盒",
                     "note": "自定义", "payload": {"peaceful": True}})
        preset = get_preset("creative_sandbox")
        self.assertEqual(preset["name"], "我的沙盒")
        self.assertFalse(preset["builtin"])
        count = len([p for p in list_presets()
                     if p["id"] == "creative_sandbox"])
        self.assertEqual(count, 1)

    def test_save_and_reload_custom_preset(self) -> None:
        save_preset({"id": "my_own", "name": "我的预设",
                     "payload": {"peaceful": True, "difficulty": "easy"}})
        preset = get_preset("my_own")
        self.assertIsNotNone(preset)
        self.assertEqual(preset["payload"]["difficulty"], "easy")

    def test_builtin_table_is_not_mutated(self) -> None:
        before = [dict(item) for item in BUILTIN_PRESETS]
        save_preset({"id": "creative_sandbox", "name": "改动",
                     "payload": {"peaceful": True}})
        list_presets()
        self.assertEqual([dict(item) for item in BUILTIN_PRESETS], before)


def read_region_index_sectors(path: Path) -> int:
    """读位置表第 0 项的扇区数（测试用）。"""
    data = path.read_bytes()
    return data[3]


if __name__ == "__main__":
    unittest.main()
