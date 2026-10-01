"""第二轮易用性/安全性回归测试（2.1.x）。

这些用例锁住的是「静默数据问题」和「假反馈」两类缺陷 —— 它们不会让程序
崩，但会让用户以为事情做成了：
* 空工作台点「发送到输出区」把输出区清空还报成功；
* 批量生成首屏是空表（引导页不可达）；
* 数据包删除函数只在内存里删、磁盘 `.mcfunction` 还在；
* 「发送到输出区」按钮被接到「已复制」（状态栏说错话）；
* 同一个窗口里注册重复快捷键（互相顶掉）。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from tests import TEST_ROOT  # noqa: F401  （导入即设置环境变量）

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                # noqa: E402

from app.ui.main_window import MainWindow                 # noqa: E402
from app.ui.panels import BatchPanel, DatapackPanel, WorkbenchPanel  # noqa: E402

_app = QApplication.instance() or QApplication([])


class BatchPanelCase(unittest.TestCase):
    def test_first_run_shows_guide_page(self) -> None:
        panel = BatchPanel()
        self.assertEqual(panel.command_combo.currentIndex(), -1)
        self.assertIs(panel._stack.currentWidget(), panel._empty_page)
        self.assertEqual(panel.table.columnCount(), 0)
        panel.deleteLater()

    def test_guide_page_buttons_select_commands(self) -> None:
        panel = BatchPanel()
        panel._select_first_command()
        self.assertEqual(panel.command_combo.currentData(), "setblock")
        self.assertIs(panel._stack.currentWidget(), panel._normal_page)
        panel._on_command_changed(-1)
        self.assertIs(panel._stack.currentWidget(), panel._empty_page)
        panel.deleteLater()

    def test_clear_asks_before_wiping_rows(self) -> None:
        import app.ui.panels as panels
        panel = BatchPanel()
        panel._select_first_command()
        panel.table.item(0, 0).setText("VALUE")
        original = panels.QMessageBox.question
        asked: list[str] = []
        panels.QMessageBox.question = staticmethod(
            lambda *a, **k: (asked.append(str(a[1])),
                             panels.QMessageBox.StandardButton.No)[1])
        try:
            panel._on_clear()
        finally:
            panels.QMessageBox.question = original
        self.assertTrue(asked, "清空必须先问一句")
        self.assertEqual(panel.table.item(0, 0).text(), "VALUE",
                         "选了「否」就不该清空")
        panel.deleteLater()


class WorkbenchCase(unittest.TestCase):
    def test_empty_script_does_not_clear_output(self) -> None:
        import app.ui.panels as panels
        panel = WorkbenchPanel()
        panel.script.steps.clear()
        sent: list[str] = []
        panel.commandActivated.connect(sent.append)
        original = panels.QMessageBox.information
        panels.QMessageBox.information = staticmethod(lambda *a, **k: None)
        try:
            panel._on_send_to_output()
        finally:
            panels.QMessageBox.information = original
        self.assertEqual(sent, [], "空脚本绝不能 emit 空串")
        panel.deleteLater()


class DatapackDeleteCase(unittest.TestCase):
    def test_delete_function_removes_file_on_disk(self) -> None:
        import app.ui.panels as panels
        tmp = Path(tempfile.mkdtemp(prefix="mcstudio-dp-"))
        panel = DatapackPanel()
        panel.path_edit.setText(str(tmp))
        panel.ns_edit.setText("mccmdgen")
        panel._on_new_project()
        panel.project.add_function("main", ["say hi"])
        panel.project.save()
        panel._refresh_functions()
        target = panel._function_file("main")
        self.assertIsNotNone(target, "保存后应该能找到 .mcfunction")
        self.assertTrue(target.is_file())

        original = panels.QMessageBox.question
        panels.QMessageBox.information = staticmethod(lambda *a, **k: None)
        panels.QMessageBox.question = staticmethod(
            lambda *a, **k: panels.QMessageBox.StandardButton.Yes)
        try:
            panel.fn_list.setCurrentRow(0)
            panel._on_delete_function()
        finally:
            panels.QMessageBox.question = original
        self.assertFalse(target.is_file(),
                         "删除函数必须同时删掉磁盘文件，否则游戏照样加载")
        self.assertEqual([f.name for f in panel.project.functions], [])
        panel.deleteLater()


class ShortcutConflictCase(unittest.TestCase):
    def test_no_duplicate_shortcuts_in_main_window(self) -> None:
        from PySide6.QtGui import QAction
        window = MainWindow()
        window.resize(1280, 800)
        window.show()
        QApplication.processEvents()
        seen: dict[str, list[str]] = {}
        # findChildren 才能覆盖菜单里的 QAction（它们挂在 QMenu 上，
        # 不在 window.actions() 里 —— 上一版的 Ctrl+Shift+S 冲突就属于这种）
        for action in window.findChildren(QAction):
            for shortcut in action.shortcuts():
                key = shortcut.toString()
                if not key:
                    continue
                seen.setdefault(key, []).append(
                    action.text() or action.objectName() or "?")
        window.close()
        window.deleteLater()
        duplicates = {k: v for k, v in seen.items()
                      if len([t for t in v if t and t != "?"]) > 1}
        self.assertEqual(duplicates, {},
                         f"同一个窗口里注册了重复快捷键：{duplicates}")


class EmptySendGuardCase(unittest.TestCase):
    def test_main_window_ignores_empty_panel_payload(self) -> None:
        window = MainWindow()
        window.output_edit.setPlainText("say keep me")
        window._on_panel_activated("")
        self.assertEqual(window.output_edit.toPlainText(), "say keep me")
        window.close()
        window.deleteLater()


class RegionBatchThreadCase(unittest.TestCase):
    """区域批量替换必须跑在后台线程：不卡界面、可取消、运行期不可重入。"""

    def _world(self, name: str = "ux-region-batch"):
        from app.core.world import open_world
        from tools.fake_world import build_fake_world
        root = Path(TEST_ROOT) / "build" / "ux-worlds" / name
        if root.exists():
            import shutil
            shutil.rmtree(root)
        return open_world(build_fake_world(root))

    @staticmethod
    def _spin_until(predicate, timeout: float = 20.0) -> bool:
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            QApplication.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def test_dry_run_uses_worker_and_restores_buttons(self) -> None:
        from app.ui.world_region_batch import RegionBatchDialog
        dialog = RegionBatchDialog(world=self._world())
        idx = dialog.operation.findData("bump_data_version")
        dialog.operation.setCurrentIndex(idx)
        dialog.run_plan()
        self.assertTrue(dialog._busy, "dry-run 必须在后台线程里跑")
        self.assertFalse(dialog.btn_plan.isEnabled(), "运行期不能再次点 dry-run")
        # 对话框本身没 show()，用 isVisibleTo 判断子控件是否被显式显示
        self.assertTrue(dialog.progress.isVisibleTo(dialog))
        self.assertTrue(self._spin_until(lambda: not dialog._busy))
        self.assertIsNotNone(dialog._plan)
        self.assertTrue(dialog.btn_plan.isEnabled())
        self.assertFalse(dialog.progress.isVisibleTo(dialog))
        dialog.deleteLater()

    def test_worker_cancel_stops_the_loop(self) -> None:
        import time
        import app.ui.world_region_batch as batch
        from app.ui.world_region_batch import _BatchWorker
        calls: list[int] = []

        def slow_plan(world, *, progress=None, **_kwargs):
            for index in range(1, 200):
                calls.append(index)
                time.sleep(0.01)          # 够慢，才来得及取消
                if progress is not None:
                    progress(index, 200, "慢慢来")
            return "should-not-finish"

        original = batch.plan_batch
        batch.plan_batch = slow_plan
        try:
            worker = _BatchWorker(object(), mode="plan", kwargs={})
            cancelled: list[bool] = []
            worker.cancelled.connect(lambda: cancelled.append(True))
            worker.start()
            # 跑到一半就取消
            self.assertTrue(self._spin_until(lambda: len(calls) > 3))
            worker.cancel()
            self.assertTrue(self._spin_until(lambda: bool(cancelled)))
            self.assertTrue(worker.wait(5000))
        finally:
            batch.plan_batch = original
        self.assertLess(len(calls), 199, "取消后不应该继续跑完整批")


class GateAndPanelCase(unittest.TestCase):
    """第三轮：门禁补齐、窄窗自适应、跨存档异步读取。"""

    @staticmethod
    def _spin_until(predicate, timeout: float = 20.0) -> bool:
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            QApplication.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def _world(self, name: str = "ux-gate3"):
        from app.core.world import open_world
        from tools.fake_world import build_fake_world
        root = Path(TEST_ROOT) / "build" / "ux-worlds" / name
        if root.exists():
            import shutil
            shutil.rmtree(root)
        return open_world(build_fake_world(root))

    def test_canvas_region_write_is_gated(self) -> None:
        from app.ui.world_panel import WorldPanel
        panel = WorldPanel()
        world = self._world()
        panel.async_open = False
        panel.open_path(world.path)
        QApplication.processEvents()
        self.assertIn(panel.canvas_region_write, panel._write_widgets(),
                      "画布写区域必须登记进只读门禁")
        self.assertFalse(panel.canvas_region_write.isEnabled())
        panel.session.unlock()
        panel._refresh_session_state()
        self.assertTrue(panel.canvas_region_write.isEnabled())
        panel.deleteLater()

    def test_summon_recommend_bar_auto_collapses(self) -> None:
        from app.ui.panels import SummonPanel
        panel = SummonPanel()
        panel.resize(700, 600)
        panel.show()
        QApplication.processEvents()
        self.assertFalse(panel.recommend.isVisibleTo(panel),
                         "窄窗口要把推荐栏收起来，别挤坐标区")
        panel.btn_recommend.setChecked(True)          # 用户手动打开
        QApplication.processEvents()
        self.assertTrue(panel.recommend.isVisibleTo(panel))
        panel.resize(1300, 600)
        QApplication.processEvents()
        self.assertTrue(panel.recommend.isVisibleTo(panel),
                        "用户手动指定后不该再被自动收起")
        panel.close()
        panel.deleteLater()

    def test_crossworld_source_load_is_async(self) -> None:
        from app.ui.world_panel import WorldPanel
        panel = WorldPanel()
        panel.async_open = False
        panel.open_path(self._world("ux-cross-current").path)
        QApplication.processEvents()
        source = self._world("ux-cross-source")
        panel.cross_path.setText(str(source.path))
        panel._on_load_source_world()
        self.assertIsNotNone(getattr(panel, "_source_loader", None))
        self.assertTrue(self._spin_until(
            lambda: panel._source_world is not None))
        self.assertGreaterEqual(panel.cross_source.count(), 1)
        panel.deleteLater()


if __name__ == "__main__":                                # pragma: no cover
    unittest.main()
