"""世界编辑器的「易用性 / 安全绳」测试（2.1 重新设计）。

盯住四件事：
1. 没打开世界的首屏不再是 15 个空表格，而是带按钮的引导页；
2. 「应用修改」真的会算差异、真的会先备份（旧实现拿当前状态跟自己对 diff，
   永远「没有变化」；写盘路径还绕过了事务，不建备份）；
3. 「撤销」之后不会假装磁盘是干净的；
4. 只读门禁方向正确：会写存档的按钮灰掉且说明原因，只读安全的（创建快照）
   保持可用。
"""
from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path

from tests import TEST_ROOT  # noqa: F401  （导入即设置环境变量）

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication            # noqa: E402

from app.ui.world_panel import WorldPanel             # noqa: E402

_app = QApplication.instance() or QApplication([])


def build_world(name: str) -> Path:
    from tools.fake_world import build_fake_world
    root = Path(TEST_ROOT) / "build" / "gui-worlds" / name
    if root.exists():
        shutil.rmtree(root)
    return build_fake_world(root)


class WorldPanelCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.panel = WorldPanel()
        cls.panel.resize(1200, 760)
        cls.panel.show()
        QApplication.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.panel.close()
        cls.panel.deleteLater()

    def test_empty_state_replaces_empty_tables(self) -> None:
        self.assertIsNone(self.panel.session)
        self.assertIs(self.panel.tab_stack.currentWidget(),
                      self.panel.tab_empty)
        texts = [w.text() for w in self.panel.tab_empty.findChildren(
            type(self.panel.empty_hint))]
        self.assertTrue(any("还没有打开世界存档" in t for t in texts))

    def test_tab_jump_lists_all_tabs(self) -> None:
        combo = self.panel.tab_jump
        # 第 0 项是占位（全部页签 ▾），其余与页签一一对应
        self.assertEqual(combo.count(), self.panel.tabs.count() + 1)
        self.assertEqual(combo.itemData(1), 0)
        combo.setCurrentIndex(3)
        combo.activated.emit(3)
        QApplication.processEvents()
        self.assertEqual(self.panel.tabs.currentIndex(), 2)

    def test_read_only_gate_keeps_backup_available(self) -> None:
        world = build_world("ux-gate")
        self.panel.open_path(world)
        QApplication.processEvents()
        self.assertTrue(self.panel.session.read_only)
        # 会写存档的按钮：灰掉，并且说明为什么灰
        self.assertFalse(self.panel.btn_set_time.isEnabled())
        self.assertIn("只读", self.panel.btn_set_time.toolTip())
        # 只读也安全的快照按钮：保持可用（先备份再解锁是合理顺序）
        self.assertTrue(self.panel.btn_backup_now.isEnabled())
        self.assertTrue(self.panel.btn_snapshot.isEnabled())
        # 区域 / 地图这些页签里的写入口也被收进门禁
        gated = self.panel._write_widgets()
        self.assertTrue(any(w.text().startswith("写入区块")
                            for w in gated if hasattr(w, "text")))

    def test_write_gate_tooltip_restored_after_unlock(self) -> None:
        self.panel.session.unlock()
        self.panel._refresh_session_state()
        self.assertTrue(self.panel.btn_set_time.isEnabled())
        self.assertNotIn("只读", self.panel.btn_set_time.toolTip())


class WorldPanelTransactionCase(unittest.TestCase):
    """每个用例一个全新的存档目录，避免互相污染。"""

    def setUp(self) -> None:
        self.panel = WorldPanel()
        self.panel.resize(1200, 760)
        self.panel.async_open = False
        self.world = build_world(f"ux-tx-{self.id().rsplit('.', 1)[-1]}")
        self.panel.open_path(self.world)
        self.panel.session.unlock()
        self.panel._refresh_session_state()
        QApplication.processEvents()

    def tearDown(self) -> None:
        self.panel.close()
        self.panel.deleteLater()

    def test_pending_diff_follows_real_edits(self) -> None:
        session = self.panel.session
        self.assertFalse(session.pending_diff())
        session.world.set_time(day_time=13000)
        session.world.mark_level_dirty()
        diff = session.pending_diff()
        self.assertTrue(diff, "改了时间必须算出差异（旧实现永远是 0 处）")
        self.assertTrue(session.unsaved_changes())
        self.panel._refresh_session_state()
        self.assertTrue(self.panel.btn_apply.isEnabled())

    def test_apply_changes_creates_backup_and_commits(self) -> None:
        import app.ui.world_panel as wp
        session = self.panel.session
        session.world.set_time(day_time=13000)
        session.world.mark_level_dirty()
        self.panel._refresh_session_state()
        # 自动化环境里不弹确认框：直接放行
        original_question = wp.QMessageBox.question
        original_info = wp.QMessageBox.information
        wp.QMessageBox.question = staticmethod(
            lambda *a, **k: wp.QMessageBox.StandardButton.Ok)
        wp.QMessageBox.information = staticmethod(lambda *a, **k: None)
        try:
            self.panel.apply_changes()
        finally:
            wp.QMessageBox.question = original_question
            wp.QMessageBox.information = original_info
        backups = list((self.world / "backups").glob("*/manifest.json"))
        self.assertTrue(backups, "写盘前必须留下备份（旧实现不建备份）")
        # 写完后内存 == 磁盘，差异清零、按钮回到禁用
        self.assertFalse(session.pending_diff())
        self.panel._refresh_session_state()
        self.assertFalse(self.panel.btn_apply.isEnabled())

    def test_undo_after_save_is_not_reported_as_clean(self) -> None:
        """撤销之后不能假装磁盘是干净的（旧实现 save() 清 dirty 就露馅）。"""
        import app.ui.world_panel as wp
        session = self.panel.session
        session.world.set_time(day_time=13000)
        session.world.mark_level_dirty()
        result = session.transaction("改时间",
                                     snapshot=session.baseline).commit()
        self.assertTrue(result.applied)
        self.assertFalse(session.pending_diff())
        self.assertFalse(session.unsaved_changes())

        # 再改一次，压一个「写盘后状态」的撤销点，然后撤销
        session.world.set_time(day_time=6000)
        session.world.mark_level_dirty()
        session.world.save()                      # 原生 save 会清掉 dirty 标记
        self.assertFalse(session.world.dirty)
        self.assertTrue(session.undo())
        self.panel._refresh_session_state()
        # dirty 是 False，但撤销让内存和磁盘的关系变成「待确认」：
        # 界面必须仍然提示可能未应用，而不是绿灯放行
        self.assertTrue(session.unsaved_changes())
        self.assertTrue(self.panel.btn_apply.isEnabled())

        # 用户点「应用修改」：算出来确实没有差异 → 只刷新基准，不写盘
        original_question = wp.QMessageBox.question
        original_info = wp.QMessageBox.information
        wp.QMessageBox.question = staticmethod(
            lambda *a, **k: wp.QMessageBox.StandardButton.Ok)
        wp.QMessageBox.information = staticmethod(lambda *a, **k: None)
        try:
            self.panel.apply_changes()
        finally:
            wp.QMessageBox.question = original_question
            wp.QMessageBox.information = original_info
        self.assertFalse(session.unsaved_changes())
        self.panel._refresh_session_state()
        self.assertFalse(self.panel.btn_apply.isEnabled())


    def test_failed_commit_does_not_alias_baseline(self) -> None:
        """失败的提交不能把 baseline 的节点直接装进活树。

        旧实现里 `restore()` 直接接管快照节点，而 `apply_changes` 传的正是
        `session.baseline` 本体 → 之后活树和基准是同一批对象，
        `pending_diff()` 恒为 0，用户的改动**永远不会写盘**，界面还报
        「内存模型与磁盘一致」。
        """
        session = self.panel.session
        session.world.set_time(day_time=13000)
        session.world.mark_level_dirty()
        # 制造一次必定失败的提交：删掉 DataVersion，validate() 会拒绝
        data = session.world.level_root.get_child("Data")
        version = data.get_child("DataVersion")
        data.remove_child(version)
        baseline_level = session.baseline["level"]
        result = session.transaction(
            "坏改动", snapshot=dict(session.baseline)).commit()
        self.assertFalse(result.ok)
        self.assertIsNot(session.world.level_root, baseline_level,
                         "回滚后的活树必须是克隆，不能与 baseline 共用节点")

        # 再改一次仍然能算出差异（别名存在时这里会是 0 处）
        session.world.set_time(day_time=5000)
        session.world.mark_level_dirty()
        diff = session.pending_diff()
        self.assertIsNotNone(diff)
        self.assertTrue(diff, "失败提交之后，新的改动必须还能被算出来")

    def test_undo_with_rollback_restores_disk(self) -> None:
        """「撤销并回滚磁盘」要真的把文件退回去（纯 undo 只改内存）。"""
        from app.core.world import open_world
        session = self.panel.session
        session.world.set_time(day_time=13000)
        session.world.mark_level_dirty()
        result = session.transaction("改时间",
                                     snapshot=session.baseline).commit()
        self.assertTrue(result.applied)
        self.assertIsNotNone(result.backup)
        self.assertEqual(open_world(self.world).overview.day_time, 13000)
        self.assertIsNotNone(session.last_undo_backup())

        ok, message = session.undo_with_rollback()
        self.assertTrue(ok, message)
        self.assertIn("backups/", message)
        self.assertNotEqual(open_world(self.world).overview.day_time, 13000,
                            "磁盘必须一起回退，否则用户以为撤销完了")
        self.assertEqual(session.undo_stack, [])
        self.assertIsNone(session.last_undo_backup())

    def test_pending_diff_without_baseline_is_unknown(self) -> None:
        """没有基准时必须返回 None，不能假装「没有差异」。"""
        session = self.panel.session
        session.baseline = None
        self.assertIsNone(session.pending_diff())


if __name__ == "__main__":                                # pragma: no cover
    unittest.main()
