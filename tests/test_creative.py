"""可视化编排（坐标/区块选择器 + 方块调色板）与区域多区块批量替换。"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests import TEST_ROOT  # noqa: F401  （导入即设置环境变量）

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt               # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent           # noqa: E402
from PySide6.QtWidgets import QApplication                   # noqa: E402

from app.core.region_io import verify_region                  # noqa: E402
from app.core.snbt import SnbtParser                          # noqa: E402
from app.core.world import open_world, read_region_chunk      # noqa: E402
from app.ui.world_creative import (                           # noqa: E402
    CHUNK_SIZE, MARKER_STYLES, TOOL_ERASE, TOOL_FILL, TOOL_PAINT, TOOL_PICK,
    TOOL_SELECT, BlockGrid, CoordinateCanvas, MapMarker,
)
from app.ui.world_region_batch import (                        # noqa: E402
    BATCH_OPERATIONS, ChunkBatchPlan, apply_to_node, execute_batch,
    plan_batch,
)

_app = QApplication.instance() or QApplication([])


def build_world(name: str = "creative") -> Path:
    import sys
    if str(TEST_ROOT) not in sys.path:                  # pragma: no cover
        sys.path.insert(0, str(TEST_ROOT))
    from tools.fake_world import build_fake_world
    root = Path(TEST_ROOT) / "build" / "creative-worlds" / name
    if root.exists():
        shutil.rmtree(root)
    return build_fake_world(root)


class TestBlockGrid(unittest.TestCase):
    def test_set_get_erase(self) -> None:
        grid = BlockGrid()
        grid.set(1, 2, 3, "minecraft:stone")
        self.assertEqual(grid.get(1, 2, 3), "minecraft:stone")
        self.assertEqual(len(grid), 1)
        grid.erase(1, 2, 3)
        self.assertEqual(len(grid), 0)
        self.assertEqual(grid.get(1, 2, 3), "")

    def test_palette_is_ordered_unique(self) -> None:
        grid = BlockGrid()
        grid.set(0, 0, 0, "minecraft:stone")
        grid.set(1, 0, 0, "minecraft:glass")
        grid.set(2, 0, 0, "minecraft:stone")
        self.assertEqual(grid.palette(),
                         ["minecraft:stone", "minecraft:glass"])

    def test_bounds_and_layers(self) -> None:
        grid = BlockGrid()
        grid.set(0, 64, 0, "minecraft:stone")
        grid.set(3, 64, 5, "minecraft:glass")
        grid.set(1, 65, 1, "minecraft:dirt")
        self.assertEqual(grid.bounds(), ((0, 64, 0), (3, 65, 5)))
        self.assertEqual(len(grid.by_layer(64)), 2)
        self.assertEqual(len(grid.by_layer(99)), 0)

    def test_empty_bounds(self) -> None:
        self.assertIsNone(BlockGrid().bounds())

    def test_clone_is_independent(self) -> None:
        grid = BlockGrid()
        grid.set(0, 0, 0, "minecraft:stone")
        clone = grid.clone()
        clone.set(1, 0, 0, "minecraft:glass")
        self.assertEqual(len(grid), 1)
        self.assertEqual(len(clone), 2)

    def test_setblock_commands_sorted_by_layer(self) -> None:
        grid = BlockGrid()
        grid.set(0, 70, 0, "minecraft:stone")
        grid.set(0, 64, 0, "minecraft:dirt")
        commands = grid.to_commands(mode="setblock")
        self.assertTrue(commands[0].startswith("setblock 0 64 0"))
        self.assertTrue(commands[1].startswith("setblock 0 70 0"))

    def test_setblock_limit(self) -> None:
        grid = BlockGrid()
        for x in range(10):
            grid.set(x, 64, 0, "minecraft:stone")
        commands = grid.to_commands(mode="setblock", limit=3)
        self.assertEqual(len(commands), 4)          # 3 条 + 1 条提示
        self.assertIn("还有 7 个方块", commands[-1])

    def test_fill_decomposes_single_rect(self) -> None:
        grid = BlockGrid()
        for x in range(4):
            for z in range(3):
                grid.set(x, 64, z, "minecraft:stone")
        commands = grid.to_commands(mode="fill")
        self.assertEqual(commands,
                         ["fill 0 64 0 3 64 2 minecraft:stone replace"])

    def test_fill_decomposes_two_rects(self) -> None:
        grid = BlockGrid()
        for x in range(2):
            grid.set(x, 64, 0, "minecraft:stone")
        grid.set(5, 64, 5, "minecraft:stone")
        commands = grid.to_commands(mode="fill")
        self.assertEqual(len(commands), 2)
        self.assertTrue(commands[0].startswith("fill 0 64 0 1 64 0"))

    def test_fill_uses_setblock_for_single_cell(self) -> None:
        grid = BlockGrid()
        grid.set(7, 64, 9, "minecraft:glass")
        self.assertEqual(grid.to_commands(mode="fill"),
                         ["setblock 7 64 9 minecraft:glass replace"])

    def test_fill_separates_palette(self) -> None:
        grid = BlockGrid()
        grid.set(0, 64, 0, "minecraft:stone")
        grid.set(1, 64, 0, "minecraft:glass")
        commands = grid.to_commands(mode="fill")
        self.assertEqual(len(commands), 2)

    def test_to_blueprint_normalizes_origin(self) -> None:
        grid = BlockGrid()
        grid.set(10, 64, -5, "minecraft:stone")
        grid.set(11, 64, -5, "minecraft:glass")
        blueprint = grid.to_blueprint("画布")
        self.assertEqual(blueprint.size, (2, 1, 1))
        self.assertEqual(sorted(b.pos for b in blueprint.blocks),
                         [(0, 0, 0), (1, 0, 0)])
        self.assertEqual(blueprint.palette(),
                         ["minecraft:stone", "minecraft:glass"])

    def test_empty_grid_commands(self) -> None:
        self.assertEqual(BlockGrid().to_commands(), [])


class TestCanvasLogic(unittest.TestCase):
    """不依赖真实鼠标事件的画布逻辑（坐标换算 / 工具 / 撤销）。"""

    def setUp(self) -> None:
        self.canvas = CoordinateCanvas()
        self.canvas.resize(400, 300)
        self.canvas.zoom = 1.0
        self.canvas._offset = QPointF(100.0, 100.0)
        self.canvas.set_layer(64)

    def tearDown(self) -> None:
        self.canvas.deleteLater()

    def test_to_screen_and_back(self) -> None:
        point = self.canvas._to_screen(10, -5)
        self.assertAlmostEqual(point.x(), 110.0)
        self.assertAlmostEqual(point.y(), 95.0)
        back = self.canvas._to_world(point)
        self.assertAlmostEqual(back[0], 10.0)
        self.assertAlmostEqual(back[1], -5.0)

    def test_zoom_clamped(self) -> None:
        self.canvas.set_zoom(100.0)
        self.assertEqual(self.canvas.zoom, 8.0)
        self.canvas.set_zoom(0.01)
        self.assertEqual(self.canvas.zoom, 0.25)

    def test_center_on(self) -> None:
        self.canvas.center_on(0, 0)
        self.assertAlmostEqual(self.canvas._offset.x(), 200.0)
        self.assertAlmostEqual(self.canvas._offset.y(), 150.0)

    def test_fit_to_uses_content(self) -> None:
        self.canvas.grid.set(0, 64, 0, "minecraft:stone")
        self.canvas.grid.set(64, 64, 64, "minecraft:stone")
        self.canvas.fit_to()
        self.assertGreater(self.canvas.zoom, 0)
        self.assertLessEqual(self.canvas.zoom, 8.0)

    def test_paint_tool(self) -> None:
        self.canvas.tool = TOOL_PAINT
        self.canvas.brush = "minecraft:gold_block"
        self.canvas._apply_tool(3, 4)
        self.assertEqual(self.canvas.grid.get(3, 64, 4),
                         "minecraft:gold_block")

    def test_erase_tool(self) -> None:
        self.canvas.tool = TOOL_PAINT
        self.canvas._apply_tool(3, 4)
        self.canvas.tool = TOOL_ERASE
        self.canvas._apply_tool(3, 4)
        self.assertEqual(self.canvas.grid.get(3, 64, 4), "")

    def test_pick_tool_reads_brush(self) -> None:
        self.canvas.grid.set(1, 64, 1, "minecraft:obsidian")
        self.canvas.tool = TOOL_PICK
        self.canvas._apply_tool(1, 1)
        self.assertEqual(self.canvas.brush, "minecraft:obsidian")

    def test_pick_on_empty_keeps_brush(self) -> None:
        self.canvas.brush = "minecraft:stone"
        self.canvas.tool = TOOL_PICK
        self.canvas._apply_tool(9, 9)
        self.assertEqual(self.canvas.brush, "minecraft:stone")

    def test_fill_tool_flood_fills_same_block(self) -> None:
        self.canvas.tool = TOOL_PAINT
        self.canvas.brush = "minecraft:stone"
        for x in range(4):
            self.canvas._apply_tool(x, 0)
        self.canvas.brush = "minecraft:glass"
        self.canvas.tool = TOOL_FILL
        self.canvas._apply_tool(0, 0)
        self.assertEqual(len(self.canvas.grid), 4)
        self.assertEqual(self.canvas.grid.palette(), ["minecraft:glass"])

    def test_fill_does_not_leak_diagonally(self) -> None:
        self.canvas.tool = TOOL_PAINT
        self.canvas.brush = "minecraft:stone"
        self.canvas._apply_tool(0, 0)
        self.canvas.grid.set(1, 64, 1, "minecraft:stone")
        self.canvas.brush = "minecraft:glass"
        self.canvas.tool = TOOL_FILL
        self.canvas._apply_tool(0, 0)
        self.assertEqual(self.canvas.grid.get(1, 64, 1), "minecraft:stone")

    def test_layer_isolation(self) -> None:
        self.canvas.set_layer(64)
        self.canvas.tool = TOOL_PAINT
        self.canvas.brush = "minecraft:stone"
        self.canvas._apply_tool(0, 0)
        self.canvas.set_layer(70)
        self.canvas.brush = "minecraft:glass"
        self.canvas._apply_tool(0, 0)
        self.assertEqual(self.canvas.grid.get(0, 64, 0), "minecraft:stone")
        self.assertEqual(self.canvas.grid.get(0, 70, 0), "minecraft:glass")

    def test_undo_redo(self) -> None:
        self.canvas.tool = TOOL_PAINT
        self.canvas.brush = "minecraft:stone"
        self.canvas.snapshot()
        self.canvas._apply_tool(0, 0)
        self.assertEqual(len(self.canvas.grid), 1)
        self.assertTrue(self.canvas.undo())
        self.assertEqual(len(self.canvas.grid), 0)
        self.assertTrue(self.canvas.redo())
        self.assertEqual(len(self.canvas.grid), 1)

    def test_undo_without_history(self) -> None:
        self.assertFalse(self.canvas.undo())
        self.assertFalse(self.canvas.redo())

    def test_clear_grid_is_undoable(self) -> None:
        self.canvas.grid.set(0, 64, 0, "minecraft:stone")
        self.canvas.clear_grid()
        self.assertEqual(len(self.canvas.grid), 0)
        self.canvas.undo()
        self.assertEqual(len(self.canvas.grid), 1)

    def test_selection_reported(self) -> None:
        seen: list[tuple[int, int, int, int]] = []
        self.canvas.chunkSelectionChanged.connect(
            lambda *args: seen.append(args))
        self.canvas.tool = TOOL_SELECT
        self.canvas._anchor = (0, 0)
        self.canvas._chunk_sel = (0, 0, 1, 2)
        self.canvas._emit_selection()
        self.assertEqual(seen, [(0, 0, 1, 2)])

    def test_marker_styles_cover_kinds(self) -> None:
        for kind in ("entity", "container", "player", "spawn", "map"):
            self.assertIn(kind, MARKER_STYLES)

    def test_chunk_size_constant(self) -> None:
        self.assertEqual(CHUNK_SIZE, 16)


class TestCanvasRendering(unittest.TestCase):
    """真的画一遍：确保 paintEvent 在各种缩放下都不抛异常。"""

    def setUp(self) -> None:
        self.canvas = CoordinateCanvas()
        self.canvas.resize(320, 240)

    def tearDown(self) -> None:
        self.canvas.deleteLater()

    def _render(self) -> None:
        from PySide6.QtGui import QPixmap
        pixmap = QPixmap(self.canvas.size())
        self.canvas.render(pixmap)

    def test_renders_empty(self) -> None:
        self._render()

    def test_renders_with_content_and_markers(self) -> None:
        for x in range(4):
            for z in range(4):
                self.canvas.grid.set(x, 64, z, "minecraft:stone")
        self.canvas.markers = [
            MapMarker(1.0, 1.0, "entity", "僵尸"),
            MapMarker(2.0, 2.0, "container", "箱子"),
            MapMarker(0.0, 0.0, "player", "Steve"),
            MapMarker(0.0, 0.0, "spawn", "出生点"),
            MapMarker(3.0, 3.0, "map", ""),
        ]
        self.canvas._chunk_sel = (0, 0, 1, 1)
        self.canvas._hover = (2, 64, 2)
        for zoom in (0.25, 1.0, 4.0, 8.0):
            self.canvas.zoom = zoom
            self._render()

    def test_renders_without_grid_or_markers(self) -> None:
        self.canvas.show_grid = False
        self.canvas.show_markers = False
        self._render()

    def test_huge_zoom_out_does_not_hang(self) -> None:
        self.canvas.zoom = 0.25
        self.canvas.grid.set(0, 64, 0, "minecraft:stone")
        self._render()


class TestRegionBatchApply(unittest.TestCase):
    """批量操作的字段级语义（不碰磁盘）。"""

    def test_set_creates_and_detects_no_change(self) -> None:
        node = SnbtParser('{DataVersion:3955}').parse()
        self.assertTrue(apply_to_node(node, "set", path="InhabitedTime",
                                      value="100L"))
        self.assertEqual(int(node.get_child("InhabitedTime").value), 100)
        self.assertFalse(apply_to_node(node, "set", path="InhabitedTime",
                                       value="100L"))

    def test_set_treats_0b_and_0_as_same(self) -> None:
        node = SnbtParser('{A:0b}').parse()
        self.assertFalse(apply_to_node(node, "set", path="A", value="0"))
        self.assertFalse(apply_to_node(node, "set", path="A", value="0b"))
        self.assertTrue(apply_to_node(node, "set", path="A", value="1b"))
        self.assertEqual(int(node.get_child("A").value), 1)

    def test_set_string_and_number(self) -> None:
        node = SnbtParser('{Status:"minecraft:full"}').parse()
        self.assertTrue(apply_to_node(node, "set", path="Status",
                                      value='"minecraft:empty"'))
        self.assertEqual(str(node.get_child("Status").value),
                         "minecraft:empty")
        self.assertTrue(apply_to_node(node, "set", path="Y", value="-4"))
        self.assertEqual(int(node.get_child("Y").value), -4)

    def test_set_bare_word_falls_back_to_string(self) -> None:
        node = SnbtParser('{A:1}').parse()
        self.assertTrue(apply_to_node(node, "set", path="Note",
                                      value="hello world"))
        self.assertEqual(str(node.get_child("Note").value), "hello world")

    def test_remove(self) -> None:
        node = SnbtParser('{A:1,B:2}').parse()
        self.assertTrue(apply_to_node(node, "remove", path="A"))
        self.assertIsNone(node.get_child("A"))
        self.assertFalse(apply_to_node(node, "remove", path="A"))

    def test_remove_list_element(self) -> None:
        node = SnbtParser('{Nums:[I;1,2,3]}').parse()
        self.assertTrue(apply_to_node(node, "remove", path="Nums[1]"))
        self.assertEqual([c.value for c in node.get_child("Nums").children],
                         [1, 3])

    def test_replace_text(self) -> None:
        node = SnbtParser('{Status:"minecraft:full"}').parse()
        self.assertTrue(apply_to_node(node, "replace_text", path="Status",
                                      search="full", replace="empty"))
        self.assertEqual(str(node.get_child("Status").value),
                         "minecraft:empty")
        self.assertFalse(apply_to_node(node, "replace_text", path="Status",
                                       search="nope", replace="x"))

    def test_replace_same_value_is_no_change(self) -> None:
        node = SnbtParser('{Status:"full"}').parse()
        self.assertFalse(apply_to_node(node, "replace_text", path="Status",
                                       search="full", replace="full"))

    def test_replace_on_non_string_raises(self) -> None:
        node = SnbtParser('{A:1}').parse()
        with self.assertRaises(ValueError):
            apply_to_node(node, "replace_text", path="A", search="1",
                          replace="2")

    def test_bump_data_version(self) -> None:
        node = SnbtParser('{DataVersion:3465}').parse()
        self.assertTrue(apply_to_node(node, "bump_data_version",
                                      data_version=3955))
        self.assertEqual(int(node.get_child("DataVersion").value), 3955)
        self.assertFalse(apply_to_node(node, "bump_data_version",
                                       data_version=3955))

    def test_bump_creates_missing(self) -> None:
        node = SnbtParser('{A:1}').parse()
        self.assertTrue(apply_to_node(node, "bump_data_version",
                                      data_version=3955))
        self.assertEqual(int(node.get_child("DataVersion").value), 3955)

    def test_missing_path_rejected(self) -> None:
        node = SnbtParser('{A:1}').parse()
        with self.assertRaises(ValueError):
            apply_to_node(node, "set", path="", value="1")

    def test_operations_table(self) -> None:
        keys = {key for key, _label, _note in BATCH_OPERATIONS}
        self.assertEqual(keys, {"set", "remove", "replace_text",
                                "bump_data_version"})


class TestRegionBatchPlan(unittest.TestCase):
    """plan / execute 的完整链路（在假世界上跑真磁盘 IO）。"""

    def setUp(self) -> None:
        self.root = build_world("batch")
        self.world = open_world(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_plan_then_execute(self) -> None:
        plan = plan_batch(self.world, operation="set", path="LightPopulated",
                          value="0b")
        self.assertEqual(len(plan.writable), 1)
        self.assertEqual(plan.skipped, [])
        self.assertEqual(len(plan.files), 1)
        self.assertIn("可写 1", plan.summary())
        done, failures, folder = execute_batch(self.world, plan)
        self.assertEqual(done, 1)
        self.assertEqual(failures, [])
        self.assertIsNotNone(folder)
        node = read_region_chunk(self.root / "region" / "r.0.0.mca", 0, 0)
        self.assertEqual(int(node.get_child("LightPopulated").value), 0)

    def test_execute_is_idempotent(self) -> None:
        plan = plan_batch(self.world, operation="set", path="LightPopulated",
                          value="0b")
        execute_batch(self.world, plan)
        again = plan_batch(self.world, operation="set", path="LightPopulated",
                           value="0b")
        self.assertEqual(len(again.writable), 0)
        self.assertEqual(len(again.skipped), 1)
        self.assertIn("没有变化", again.skipped[0][3])

    def test_execute_backs_up_once_per_file(self) -> None:
        plan = plan_batch(self.world, operation="set", path="LightPopulated",
                          value="1b")
        _done, _fail, folder = execute_batch(self.world, plan)
        backups = list((folder / "region").glob("*.mca"))
        self.assertEqual(len(backups), 1)

    def test_execute_keeps_other_fields(self) -> None:
        plan = plan_batch(self.world, operation="set", path="LightPopulated",
                          value="1b")
        execute_batch(self.world, plan)
        node = read_region_chunk(self.root / "region" / "r.0.0.mca", 0, 0)
        self.assertEqual(len(node.get_child("Entities").children), 1)
        self.assertIsNotNone(node.get_child("block_entities"))
        self.assertEqual(verify_region(self.root / "region" / "r.0.0.mca"), [])

    def test_plan_reports_skip_when_absent(self) -> None:
        plan = plan_batch(self.world, operation="remove", path="NoSuchField")
        self.assertEqual(plan.writable, [])
        self.assertIn("没有变化", plan.skipped[0][3])

    def test_plan_report_text(self) -> None:
        plan = plan_batch(self.world, operation="set", path="LightPopulated",
                          value="1b")
        text = plan.report(limit=5)
        self.assertIn("可写区块", text)
        self.assertIn("r.0.0.mca", text)

    def test_bump_version_batch(self) -> None:
        plan = plan_batch(self.world, operation="bump_data_version",
                          data_version=5000)
        self.assertTrue(plan.writable)
        execute_batch(self.world, plan)
        node = read_region_chunk(self.root / "region" / "r.0.0.mca", 0, 0)
        self.assertEqual(int(node.get_child("DataVersion").value), 5000)

    def test_progress_callback_called(self) -> None:
        seen: list[str] = []
        plan_batch(self.world, operation="set", path="LightPopulated",
                   value="1b",
                   progress=lambda _d, _t, text: seen.append(text))
        self.assertTrue(seen)

    def test_empty_plan_summary(self) -> None:
        plan = ChunkBatchPlan(operation="set")
        self.assertEqual(plan.total, 0)
        self.assertIn("共 0 个区块", plan.summary())


def _wait_batch(dialog, timeout: float = 20.0) -> bool:
    """等区域批量对话框的后台任务结束（信号要靠事件循环投递）。"""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        QApplication.processEvents()
        if not getattr(dialog, "_busy", False):
            return True
        time.sleep(0.01)
    return False


class TestBatchDialog(unittest.TestCase):
    """对话框构建与 dry-run（离屏）。"""

    @classmethod
    def setUpClass(cls) -> None:
        from app.ui.world_region_batch import RegionBatchDialog
        cls.pack_root = build_world("dlg")
        cls.world = open_world(cls.pack_root)
        cls.dialog = RegionBatchDialog(None, world=cls.world)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.dialog.deleteLater()
        shutil.rmtree(cls.pack_root, ignore_errors=True)

    def test_operation_combo(self) -> None:
        self.assertEqual(self.dialog.operation.count(), len(BATCH_OPERATIONS))

    def test_switching_operation_toggles_fields(self) -> None:
        index = self.dialog.operation.findData("replace_text")
        self.dialog.operation.setCurrentIndex(index)
        self.assertTrue(self.dialog.search_edit.isEnabled())
        self.assertFalse(self.dialog.value_edit.isEnabled())
        index = self.dialog.operation.findData("bump_data_version")
        self.dialog.operation.setCurrentIndex(index)
        self.assertFalse(self.dialog.path_edit.isEnabled())
        self.assertTrue(self.dialog.version_spin.isEnabled())

    def test_dry_run_fills_report(self) -> None:
        index = self.dialog.operation.findData("set")
        self.dialog.operation.setCurrentIndex(index)
        self.dialog.path_edit.setText("LightPopulated")
        self.dialog.value_edit.setText("1b")
        self.dialog.run_plan()
        # dry-run 现在跑在后台线程（界面不卡）：等 worker 结束再断言
        self.assertTrue(_wait_batch(self.dialog), "dry-run 没有在预期时间内结束")
        self.assertIn("可写", self.dialog.count_label.text())
        self.assertIn("r.0.0.mca", self.dialog.report.toPlainText())
        self.assertTrue(self.dialog.btn_execute.isEnabled())


if __name__ == "__main__":
    unittest.main()
