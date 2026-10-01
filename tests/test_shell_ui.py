"""壳层（2.1 重新设计）测试：首页 / 命令面板 / 引导条 / 导航条 / 交互 bug 修复。

这些测试锁住的是「易用性」本身，而不是实现细节：
任务卡必须能跳、命令面板必须能搜、引导条必须告诉用户下一步、
点了左侧命令树必须切回命令面板。
"""
from __future__ import annotations

import os
import unittest

from tests import TEST_ROOT  # noqa: F401  （导入即设置环境变量）

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                             # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton    # noqa: E402

from app.ui.main_window import MainWindow                 # noqa: E402
from app.ui.shell import (                                # noqa: E402
    CommandPalette, GuideStrip, HomePage, NavBar, PaletteEntry, WelcomeDialog,
    score_entry,
)
from app.ui.shell.cards import Card                       # noqa: E402
from app.ui.shell.home import TASKS                       # noqa: E402

_app = QApplication.instance() or QApplication([])


class ShellWidgetCase(unittest.TestCase):
    """纯控件层（不建主窗口，跑得快）。"""

    def test_home_cards_emit_routes(self) -> None:
        page = HomePage()
        seen: list[str] = []
        page.navigateRequested.connect(seen.append)
        cards = page.findChildren(Card)
        self.assertEqual(len(cards), len(TASKS))
        for card in cards:
            card.clicked.emit()
        self.assertEqual(seen, [route for *_rest, route in TASKS])
        page.deleteLater()

    def test_home_checklist_progress(self) -> None:
        page = HomePage()
        page.set_checklist({"generate"})
        self.assertEqual(page.progress_label.text(), "已完成 1/3")
        self.assertTrue(page.banner.isVisibleTo(page))
        page.set_checklist({"generate", "copy", "open"})
        self.assertEqual(page.progress_label.text(), "已完成 3/3")
        # 全部完成后首用引导条自动收起
        self.assertFalse(page.banner.isVisibleTo(page))
        page.deleteLater()

    def test_home_recent_lists_have_empty_hint(self) -> None:
        page = HomePage()
        page.set_recent_commands([])
        page.set_recent_worlds([])
        self.assertEqual(page.recent_list.count(), 1)
        self.assertIn("还没有", page.recent_list.item(0).text())
        page.set_recent_commands(["give @a diamond", "time set day"])
        self.assertEqual(page.recent_list.count(), 2)
        # 没有 "/" 前缀时自动补上，复制进游戏就能用
        self.assertTrue(page.recent_list.item(0).text().startswith("/"))
        page.deleteLater()

    def test_palette_filters_and_activates(self) -> None:
        called: list[str] = []
        entries = [
            PaletteEntry("/give", "给予物品", "物品 装备", "命令",
                         lambda: called.append("give")),
            PaletteEntry("/time", "时间", "白天 夜晚", "命令",
                         lambda: called.append("time")),
            PaletteEntry("打开世界存档…", "编辑存档", "存档 世界", "动作",
                         lambda: called.append("world")),
        ]
        palette = CommandPalette()
        palette.set_entries(entries)
        self.assertEqual(len(palette.filter_entries("")), 3)
        self.assertEqual([e.title for e in palette.filter_entries("give")],
                         ["/give"])
        # 中文关键字也要能搜到
        self.assertEqual([e.title for e in palette.filter_entries("存档")],
                         ["打开世界存档…"])
        self.assertEqual(palette.filter_entries("不存在的东西"), [])
        palette.activate_entry(entries[0])
        self.assertEqual(called, ["give"])
        palette.deleteLater()

    def test_palette_scoring_prefers_prefix(self) -> None:
        exact = PaletteEntry("/give", "给予物品")
        loose = PaletteEntry("存档 → give 面板", "别的")
        self.assertGreater(score_entry(exact, ["give"], "give"),
                           score_entry(loose, ["give"], "give"))

    def test_palette_empty_result_is_explained(self) -> None:
        palette = CommandPalette()
        palette.set_entries([PaletteEntry("唯一", "只有它")])
        palette.search.setText("zzz")
        QApplication.processEvents()
        self.assertEqual(palette.list.count(), 1)
        self.assertIn("没有匹配项", palette.list.item(0).text())
        palette.deleteLater()

    def test_guide_strip_steps_and_actions(self) -> None:
        strip = GuideStrip()
        hit: list[str] = []
        strip.set_steps(["选命令", "填参数", "复制"], current=1)
        strip.set_hint("填好参数就能生成", [("复制命令", lambda: hit.append("c")),
                                            ("→ 存档", lambda: hit.append("w"))])
        self.assertEqual(strip.action_labels(), ["复制命令", "→ 存档"])
        self.assertIn("填好参数", strip.current_text())
        # 第一个动作是主按钮（引导条告诉用户「点这里」）
        buttons = list(strip.actions_box.findChildren(QPushButton))
        self.assertEqual(buttons[0].objectName(), "Primary")
        buttons[0].click()
        self.assertEqual(hit, ["c"])
        strip.set_hint("下一句", [])
        self.assertEqual(strip.action_labels(), [])
        strip.set_steps([])
        self.assertFalse(strip.steps_box.isVisibleTo(strip))
        strip.deleteLater()

    def test_navbar_modes_and_actions(self) -> None:
        bar = NavBar()
        modes: list[str] = []
        actions: list[str] = []
        bar.modeChanged.connect(modes.append)
        bar.actionTriggered.connect(actions.append)
        bar.set_mode("editor")
        self.assertEqual(bar.current_mode(), "editor")
        bar._buttons["dual"].click()
        self.assertEqual(modes, ["dual"])
        bar.btn_palette.click()
        bar.btn_open_world.click()
        self.assertEqual(actions, ["palette", "open-world"])
        self.assertTrue(bar.more_menu.isEmpty())
        bar.deleteLater()

    def test_welcome_dialog_routes(self) -> None:
        dialog = WelcomeDialog()
        seen: list[str] = []
        dialog.navigateRequested.connect(seen.append)
        dialog._go("cmd:give")
        self.assertEqual(seen, ["cmd:give"])
        self.assertTrue(dialog.show_home_enabled())
        dialog.deleteLater()


class ShellWindowCase(unittest.TestCase):
    """主窗口集成层。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.window = MainWindow()
        cls.window.resize(1440, 900)
        cls.window.show()
        QApplication.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        cls.window.deleteLater()

    def test_home_tab_exists_and_is_first(self) -> None:
        self.assertIs(self.window.center_tabs.widget(0), self.window.home_page)
        self.assertEqual(self.window.center_tabs.tabText(0), "首页")

    def test_show_home_switches_center_stack(self) -> None:
        self.window.set_workspace("editor")
        QApplication.processEvents()
        self.window.show_home()
        QApplication.processEvents()
        self.assertIs(self.window.center_stack.currentWidget(),
                      self.window.center_tabs)
        self.assertIs(self.window.center_tabs.currentWidget(),
                      self.window.home_page)

    def test_world_editor_reachable_in_dual_mode(self) -> None:
        """双栏模式下也必须能打开世界编辑器（旧版这里点了没反应）。"""
        self.window.set_workspace("dual")
        QApplication.processEvents()
        self.window._focus_world_editor()
        QApplication.processEvents()
        self.assertIs(self.window.center_stack.currentWidget(),
                      self.window.world_panel)
        # 切到别的页签要能回到命令面板
        index = self.window.center_tabs.indexOf(self.window.panel_stack)
        self.window.center_tabs.setCurrentIndex(index)
        QApplication.processEvents()
        self.assertIs(self.window.center_stack.currentWidget(),
                      self.window.center_tabs)

    def test_clicking_command_tree_returns_to_command_panel(self) -> None:
        """在「世界编辑器」页签上点左侧命令树，必须切回命令面板。"""
        self.window.set_workspace("dual")
        self.window._focus_world_editor()
        QApplication.processEvents()
        self.window._goto_command("give")
        QApplication.processEvents()
        self.assertIs(self.window.center_stack.currentWidget(),
                      self.window.center_tabs)
        self.assertIs(self.window.center_tabs.currentWidget(),
                      self.window.panel_stack)
        self.assertIs(self.window.panel_stack.currentWidget(),
                      self.window.give_panel)

    def test_command_tree_filter(self) -> None:
        tree = self.window.command_tree
        self.window.tree_filter.setText("give")
        QApplication.processEvents()
        visible = []
        for index in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(index)
            if top.isHidden():
                continue
            for child in range(top.childCount()):
                item = top.child(child)
                if not item.isHidden():
                    visible.append(str(item.data(0, Qt.ItemDataRole.UserRole)))
        self.assertIn("give", visible)
        self.assertNotIn("time", visible)
        self.window.tree_filter.clear()
        QApplication.processEvents()
        hidden = [top.isHidden() for index in range(tree.topLevelItemCount())
                  for top in [tree.topLevelItem(index)]]
        self.assertFalse(any(hidden))

    def test_guidance_follows_state(self) -> None:
        strip = self.window.guide_strip
        self.window.show_home()
        QApplication.processEvents()
        self.assertIn("Ctrl+K", strip.current_text())
        # 会话草稿可能把上次的编辑恢复进来 —— 先明确回到「初始示例」状态
        self.window.give_panel._load_demo()
        self.window._goto_command("give")
        QApplication.processEvents()
        # /give 面板里是初始示例 → 引导要说明这是示例而不是用户的结果
        self.assertIn("示例", strip.current_text())
        self.window.give_panel.target_line.setText("@a")
        QApplication.processEvents()
        self.assertIn("复制", " ".join(strip.action_labels()))
        self.window._focus_world_editor()
        QApplication.processEvents()
        self.assertIn("打开", " ".join(strip.action_labels()))

    def test_copy_output_gives_visible_feedback(self) -> None:
        self.window.output_edit.setPlainText("say hello")
        QApplication.processEvents()
        self.window._on_copy_output()
        QApplication.processEvents()
        self.assertEqual(QApplication.clipboard().text(), "say hello")
        self.assertIn("copy", self.window._onboarding)
        self.assertTrue(self.window._toast.isVisible())
        self.assertIn("复制", self.window._toast.text())

    def test_panel_action_bar_copies_command(self) -> None:
        self.window._goto_command("give")
        QApplication.processEvents()
        bar = self.window.give_panel.action_bar
        bar._on_copy()
        QApplication.processEvents()
        self.assertTrue(QApplication.clipboard().text())
        self.assertIn("give", QApplication.clipboard().text())

    def test_palette_entries_cover_commands_and_actions(self) -> None:
        entries = self.window._palette_entries()
        titles = {entry.title for entry in entries}
        self.assertIn("/give", titles)
        self.assertIn("打开世界存档…", titles)
        self.assertIn("世界编辑器", titles)
        categories = {entry.category for entry in entries}
        self.assertTrue({"命令", "页签", "动作"} <= categories)

    def test_open_palette_wires_entries(self) -> None:
        self.window.open_palette()
        QApplication.processEvents()
        palette = self.window._palette
        self.assertGreater(len(palette.entries()), 40)
        palette.close()

    def test_onboarding_persisted(self) -> None:
        self.window._mark_onboarding("open")
        saved = self.window._draft_store.load("onboarding") or {}
        self.assertIn("open", saved.get("done") or [])

    def test_unknown_command_placeholder_has_guidance(self) -> None:
        self.window._current_command_type = "definitely_not_a_command"
        self.window._placeholder.setText("占位")
        self.window._on_command_selected(
            self._fake_item("definitely_not_a_command"), None)
        self.assertIn("批量生成", self.window._placeholder.text())

    def _fake_item(self, key: str):
        from PySide6.QtWidgets import QTreeWidgetItem
        item = QTreeWidgetItem([key])
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        return item


if __name__ == "__main__":                                # pragma: no cover
    unittest.main()
