"""主窗口（MCStudio）。

工作区模式（视图 → 工作区）
--------------------------
* **命令模式**：只显示命令生成器（左：命令列表 / 中：命令面板）
* **编辑模式**：只显示世界与 NBT 编辑器
* **双栏联动**：左「命令生成器面板」 ｜ 中「编辑器面板」 ｜ 右「输出 / 桥接」

两个面板通过 `BridgeDrawer`（联动总线）交换**结构化对象**：
`BridgeOperation` / `ItemStack` / `EntityData` / 坐标 / NBT 路径。
所有跨面板写入都要先预览差异、再确认，并且可撤销、可记录。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QSplitter,
    QVBoxLayout, QHBoxLayout, QLabel, QToolButton,
    QTabWidget, QMessageBox, QToolBar, QStatusBar, QApplication,
    QStackedWidget, QProgressDialog, QTreeWidget, QTreeWidgetItem,
    QInputDialog, QDialog, QLineEdit, QPushButton,
)

from app import branding
from app.core import (
    CommandBuilder, SchemaBuilder, BuildContext,
    iter_builders, all_builders, load_schemas, get_builder,
)
from app.core.data import store as data_store, recommender, run_update
from app.storage import PresetStore, DraftStore, HistoryStore, Database
from app.storage.world_cache import WorldIndexCache
from app.core.nbt_binary import detect_compression  # noqa: F401  (供外部脚本使用)
from app.core.bridge import command_to_operation
from app.core.scenes import save_scene, load_scene, list_scenes
from app.ui.dialogs import (
    SavePresetDialog, DataImportDialog, MigrateDialog,
    DiffDialog, ShortcutsDialog,
)
from app.ui.panels import (
    SummonPanel, GivePanel, ExecutePanel, SchemaPanel, BatchPanel,
    DatapackPanel, HistoryPanel, PresetPanel, FragmentPanel, WorkbenchPanel,
    ReferencePanel,
)
from app.ui.widgets import NbtFileEditor, TargetSelectorEditor
from app.ui.widgets.command_search import CommandSearch
from app.ui.widgets.nlp_input import NlpInput
from app.ui.widgets.output_edit import CommandOutputEdit
from app.ui.theme import apply_theme, hint_style, muted_style
from app.ui.graph.execute_graph import ExecuteGraphView
from app.ui.bridge_drawer import BridgeDrawer
from app.ui.world_loader import WorldLoader
from app.ui.world_panel import WorldPanel
from app.ui.shell import (
    CommandPalette, GuideStrip, HomePage, NavBar, PaletteEntry, WelcomeDialog,
)


CATEGORY_ORDER = ["核心命令", "其他基础命令", "复杂命令", "其他"]

#: 工作区模式 key → 中文名
WORKSPACES: tuple[tuple[str, str], ...] = (
    ("command", "命令模式"),
    ("editor", "编辑模式"),
    ("dual", "双栏联动"),
)


# ✅ 更新线程
class UpdaterThread(QThread):
    progress = Signal(str)
    finished_ok = Signal(int, str)
    failed = Signal(str)

    def run(self) -> None:
        count, msg = run_update(progress_cb=self.progress.emit)
        if count == 0:
            self.failed.emit(msg)
        else:
            self.finished_ok.emit(count, msg)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(branding.window_title())
        # 三栏 + 底部面板在更窄的窗口里会互相挤压，给一个合理下限
        self.setMinimumSize(1120, 700)
        self.resize(1440, 900)

        self._panels: dict[str, QWidget] = {}
        self._current_command_type = ""
        self._current_nbt_path: Path | None = None
        self._recent_item = None
        # 布局记忆（会被 data.db 里的 layout 草稿覆盖）
        self.left_width = 240
        self.right_width = 400
        self._bottom_height = 150
        self._layout_pending = False
        #: 工作区模式：command / editor / dual
        self.workspace = "dual"
        #: 编辑器面板是否独立成窗口
        self._editor_detached = False
        #: 自动快照（打开世界时的「世界时间线」）
        self.auto_snapshot = True
        self.world_cache = WorldIndexCache()

        self._history_timer = QTimer(self)
        self._history_timer.setSingleShot(True)
        self._history_timer.setInterval(1500)
        self._history_timer.timeout.connect(self._flush_history)
        self._pending_history: tuple[str, str] | None = None

        self._updater: UpdaterThread | None = None
        self._last_clip_cmd = ""
        #: 命令面板 / 上手向导（按需创建）
        self._palette: CommandPalette | None = None
        self._welcome: WelcomeDialog | None = None
        #: 上手清单完成情况（generate / copy / open）
        self._onboarding: set[str] = set()
        #: 输出区里有当前面板之外的命令时提示一次
        self._output_from_home = False

        self._draft_store = DraftStore()
        self._draft_timer = QTimer(self)
        self._draft_timer.setInterval(5000)
        self._draft_timer.timeout.connect(self._save_session_draft)

        # 注册 schema 驱动的命令
        load_schemas()

        # 上手清单（首次启动时为空，完成一步打一个勾）
        try:
            saved = self._draft_store.load("onboarding") or {}
            self._onboarding = set(saved.get("done") or [])
        except Exception:
            self._onboarding = set()

        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()
        self._build_shell_shortcuts()

        self._load_demo_nbt()
        self._load_demo_target()

        try:
            QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)
        except Exception:
            pass

        self._draft_timer.start()
        self._restore_session_draft()
        self._update_guidance()

        # 恢复主题
        try:
            theme = self._draft_store.load("theme") or {}
            apply_theme(QApplication.instance(), theme.get("name", "system"))
        except Exception:
            pass

    # ==================================================================
    # 工作区模式
    # ==================================================================
    def set_workspace(self, key: str) -> None:
        """切换命令模式 / 编辑模式 / 双栏联动。"""
        if key not in dict(WORKSPACES):
            return
        self.workspace = key
        # 用 setHidden 而不是 setVisible：前者能可靠地「显式隐藏」，
        # isVisible() 在测试/无缝布局下不会因为父窗口时序而飘。
        if hasattr(self, "left_pane"):
            self.left_pane.setHidden(key not in ("command", "dual"))
        if hasattr(self, "right_pane"):
            self.right_pane.setHidden(key == "editor")
        if hasattr(self, "center_tabs") and hasattr(self, "center_stack"):
            target = self.center_tabs if key in ("command", "dual") \
                else self.world_panel
            # 编辑器可能被独立成窗口（此时不在 stack 里），要防一手
            if self.center_stack.indexOf(target) >= 0:
                self.center_stack.setCurrentWidget(target)
            # 中间栏的「世界编辑器」是占位页签：选着它的时候 center_stack
            # 必须显示真正的世界面板，否则双栏模式会显示一个空 QWidget
            # （旧版守卫漏了 dual，一点「双栏联动」中间栏就空白）
            proxy = getattr(self, "world_tab_proxy", None)
            if proxy is not None and self.center_tabs.currentWidget() is proxy:
                if key == "dual" and self.center_stack.indexOf(
                        self.world_panel) >= 0 and not self._editor_detached:
                    self.center_stack.setCurrentWidget(self.world_panel)
                elif key == "command":
                    self.center_tabs.setCurrentWidget(self.panel_stack)
        if hasattr(self, "bridge_panel"):
            self.bridge_panel.setHidden(key != "dual")
        if hasattr(self, "bottom_tabs"):
            self.bottom_tabs.setHidden(key == "editor")
        if hasattr(self, "navbar"):
            self.navbar.set_mode(key)
        self._update_collapse_buttons()
        self._status(f"工作区：{dict(WORKSPACES)[key]}", 3000)
        self._update_guidance()
        try:
            self._draft_store.save("workspace", {"mode": key})
        except Exception:
            pass

    def _on_workspace_action(self, key: str) -> None:
        self.set_workspace(key)

    def toggle_editor_window(self) -> None:
        """把编辑器面板独立成窗口 / 收回主窗口。"""
        if self._editor_detached:
            self._reattach_editor()
            return
        self._detach_editor()

    def _detach_editor(self) -> None:
        self.editor_window = QDialog(self)
        self.editor_window.setWindowTitle(
            f"{branding.APP_NAME} — 世界与 NBT 编辑器")
        self.editor_window.resize(1100, 760)
        layout = QVBoxLayout(self.editor_window)
        layout.setContentsMargins(0, 0, 0, 0)
        self.editor_window.setLayout(layout)
        self.center_stack.removeWidget(self.world_panel)
        layout.addWidget(self.world_panel)
        self.world_panel.setParent(self.editor_window)
        self.editor_window.show()
        self._editor_detached = True
        self.center_stack.setCurrentWidget(self.center_tabs)
        self._update_guidance()
        self._status("编辑器已独立成窗口，可拖到副屏使用。", 4000)

    def _reattach_editor(self) -> None:
        window = getattr(self, "editor_window", None)
        if window is not None:
            layout = window.layout()
            if layout is not None:
                layout.removeWidget(self.world_panel)
            self.center_stack.addWidget(self.world_panel)
            self.world_panel.setParent(self.center_stack)
            window.setParent(None)
            window.deleteLater()
            self.editor_window = None
        self._editor_detached = False
        self.set_workspace(self.workspace)
        self._update_guidance()
        self._status("编辑器已回到主窗口。", 3000)

    # ==================================================================
    # 联动总线
    # ==================================================================
    def _on_bridge_operation(self, operation) -> None:
        """编辑器 → 联动总线（结构化对象）。"""
        if operation is None:                           # pragma: no cover
            return
        # 命令 → 存档方向的操作补上执行器
        if operation.runner is None:
            from app.core.bridge import operation_runner
            operation.runner = operation_runner(operation)
        if self.bridge_panel.push(operation):
            self._show_bridge_drawer()

    def _show_bridge_drawer(self) -> None:
        if self.workspace != "dual":
            self.set_workspace("dual")
        if hasattr(self, "bridge_tabs"):
            self.bridge_tabs.setCurrentWidget(self.bridge_panel)

    def _on_bridge_apply(self, operation) -> None:
        """联动总线 → 世界。

        预览 / 确认 / 备份 / 撤销这一整套都复用 `WorldPanel._run_bridge`，
        避免两处各写一份确认逻辑（曾经就因为两份逻辑不一致出过 bug）。
        这里只负责：把日志写进总线、把编辑器面板切到「差异」页。
        """
        panel = self.world_panel
        session = panel.session
        if session is None or session.world is None:
            QMessageBox.information(
                self, "提示",
                "还没有打开世界存档。\n\n请先在「编辑器」面板里打开一个存档。")
            return
        if session.read_only:
            QMessageBox.information(
                self, "只读", "存档处于只读模式，请先在编辑器面板解锁编辑。")
            return
        from app.core import bridge as bridge_mod
        preview = bridge_mod.preview_operation(session, operation)
        if not preview.ok:
            QMessageBox.warning(self, "无法应用", preview.message)
            return
        if len(preview.diff) == 0:
            self.bridge_panel.log(f"{operation.title}：没有变化")
            QMessageBox.information(self, "没有变化", "该操作不会改变数据。")
            return
        panel.diff_view.setPlainText(preview.diff.text(limit=200))
        panel.diff_summary.setText(f"{len(preview.diff)} 处差异")
        answer = QMessageBox.question(
            self, f"确认写入存档：{operation.title}",
            f"{operation.summary()}\n\n差异（{len(preview.diff)} 处）：\n\n"
            f"{preview.diff.text(limit=30)}\n\n"
            "确认后自动备份并写入。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Ok:
            self.bridge_panel.log(f"{operation.title}：用户取消")
            return
        result = bridge_mod.apply_operation(session, operation)
        self.bridge_panel.log(
            f"{operation.source} → {operation.target} · {operation.title} · "
            f"{'✔ ' + result.message if result.ok else '✘ ' + result.message}")
        if not result.ok:
            QMessageBox.warning(self, "写入失败", result.message)
            return
        panel.refresh_all()
        self._refresh_status_context()
        # 写入结果切到「差异」页签，用户能立刻看到这次改了什么
        index = panel._tab_index("差异")
        if index >= 0:
            panel.tabs.setCurrentIndex(index)
        summary = result.summary() if hasattr(result, "summary") else ""
        self._show_toast(f"✔ 已写入存档：{summary or result.message}")
        self._update_guidance()
        self._status(result.message, 6000)
    def _on_bridge_commands(self, text: str) -> None:
        """联动总线 → 命令面板输出区。"""
        lines = [line for line in str(text).splitlines() if line.strip()]
        if not lines:
            return
        self.output_edit.setPlainText("\n".join(lines))
        self.set_workspace(self.workspace)
        self._status(f"已把 {len(lines)} 条命令送到输出区。", 4000)

    def _on_bridge_undo(self) -> None:
        session = self.world_panel.session
        if session is None or not session.can_undo():
            self._status("没有可撤销的世界编辑。", 3000)
            return
        session.undo()
        self.world_panel.refresh_all()
        self.bridge_panel.log("撤销了上一步世界编辑")
        self._status("已撤销上一步世界编辑。", 3000)

    def _on_link_toggled(self, enabled: bool) -> None:
        self._status(
            "联动总线已开启：两侧面板会互相传递结构化对象。" if enabled
            else "联动总线已关闭：两个面板完全独立。", 4000)

    # ---- 命令 → 编辑器 ----

    def send_output_to_bridge(self) -> None:
        """把输出区的命令送到联动总线（可解析成存档操作）。"""
        text = self.output_edit.toPlainText().strip()
        if not text:
            self._status("输出区为空。", 3000)
            return
        session = self.world_panel.session
        count = 0
        failures: list[str] = []
        for line in (ln for ln in text.splitlines() if ln.strip()
                     and not ln.strip().startswith("#")):
            operation = command_to_operation(line, session=session)
            if operation is None:
                failures.append(line)
                continue
            if operation.runner is None:
                from app.core.bridge import operation_runner
                operation.runner = operation_runner(operation)
            if self.bridge_panel.push(operation):
                count += 1
        if count:
            self._show_bridge_drawer()
            self._status(f"已把 {count} 条命令送进联动总线，"
                         "点「应用到世界」即可写入存档。", 6000)
        if failures:
            QMessageBox.information(
                self, "部分命令无法转换",
                "以下命令暂不支持「命令 → 存档」：\n\n"
                + "\n".join(failures[:12]))

    def _on_bridge_send_output(self) -> None:
        self.send_output_to_bridge()

    # ---- 编辑器 → 命令 ----

    def _on_world_give_request(self, stack) -> None:
        panel = self._panels.get("give")
        if panel is None:                               # pragma: no cover
            return
        panel.receive_item(stack)
        self.set_workspace("dual")
        self.panel_stack.setCurrentWidget(panel)
        self.center_tabs.setCurrentWidget(self.panel_stack)
        self.bridge_panel.set_context(selected_item=stack)
        self._status(f"已把「{stack.display()}」载入 /give 面板。", 5000)

    def _on_world_changed(self, world) -> None:
        """打开世界后：更新联动上下文、自动快照、状态栏、缓存。"""
        if world is None:
            return
        self.bridge_panel.set_context(
            current_world=f"{world.name}（{world.path}）",
            current_dimension="minecraft:overworld",
            current_pos=tuple(float(v) for v in world.overview.spawn))
        self._refresh_status_context()
        path = str(world.path)
        try:
            self._draft_store.save("last_world", {"path": path})
        except Exception:                               # pragma: no cover
            pass
        from app.core.world import snapshot_world
        if self.auto_snapshot:
            try:
                folder = snapshot_world(world, label="打开世界自动快照")
                if folder is not None:
                    self.bridge_panel.log(
                        f"世界时间线：已创建快照 backups/{folder.name}")
            except Exception as e:                      # pragma: no cover
                self.bridge_panel.log(f"自动快照失败：{e}")
        self.world_panel.refresh_nbt_sources()
        self._mark_onboarding("open")
        self._update_guidance()
        self._status(
            f"已打开世界「{world.name}」：{world.overview.data_version} "
            f"（{world.overview.version_label}）· "
            f"{len(world.players)} 名玩家 · {len(world.regions)} 个区域文件",
            8000)

    # ==================================================================
    # 菜单 / 工具栏
    # ==================================================================
    def _build_menu(self) -> None:
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件(&F)")

        act_copy = QAction("复制输出命令", self)
        act_copy.setShortcut("Ctrl+Shift+C")
        act_copy.triggered.connect(self._on_copy_output)
        file_menu.addAction(act_copy)

        act_clear = QAction("清空输出", self)
        act_clear.triggered.connect(lambda: self.output_edit.clear())
        file_menu.addAction(act_clear)

        act_settings = QAction("设置…", self)
        act_settings.setToolTip("专有名词翻译、NBT 显示选项")
        act_settings.triggered.connect(self._on_open_settings)
        file_menu.addAction(act_settings)

        file_menu.addSeparator()

        act_open_world = QAction("打开世界存档…", self)
        act_open_world.setShortcut("Ctrl+Shift+K")
        act_open_world.setToolTip(
            "打开 .minecraft/saves/世界名（也支持启动器版本隔离目录）")
        act_open_world.triggered.connect(self._on_open_world)
        file_menu.addAction(act_open_world)

        act_check_save = QAction("检查存档并预览差异", self)
        act_check_save.triggered.connect(self._on_check_world)
        file_menu.addAction(act_check_save)

        file_menu.addSeparator()

        act_new_nbt = QAction("新建 NBT 文档", self)
        act_new_nbt.setShortcut("Ctrl+Shift+M")
        act_new_nbt.triggered.connect(self._on_new_nbt_file)
        file_menu.addAction(act_new_nbt)

        act_open_nbt = QAction("打开 NBT / .dat 文件…", self)
        act_open_nbt.setShortcut("Ctrl+Shift+N")
        act_open_nbt.triggered.connect(self._on_open_nbt_file)
        file_menu.addAction(act_open_nbt)

        act_save_nbt = QAction("保存 NBT 文件", self)
        act_save_nbt.setShortcut("Ctrl+S")
        act_save_nbt.triggered.connect(self._on_save_nbt_file_direct)
        file_menu.addAction(act_save_nbt)

        act_save_nbt_as = QAction("NBT 另存为…", self)
        act_save_nbt_as.setShortcut("Ctrl+Shift+S")
        act_save_nbt_as.triggered.connect(self._on_save_nbt_file)
        file_menu.addAction(act_save_nbt_as)

        nbt_menu = file_menu.addMenu("NBT 编辑器")
        for label, cb in (
                ("文件结构总览…", self._on_nbt_structure),
                ("存档物品一览…", self._on_nbt_item_overview),
                ("定位到路径…", self._on_nbt_goto_path),
                ("按关键字筛选", self._on_nbt_filter),
                ("查找 / 替换…", self._on_nbt_find),
                ("全部展开", self._on_nbt_expand_all),
                ("全部折叠", self._on_nbt_collapse_all),
                ("复制选中标签为 SNBT", self._on_nbt_copy_snbt),
                ("复制选中标签为 /data 命令", self._on_nbt_copy_command)):
            a = QAction(label, self)
            a.triggered.connect(cb)
            nbt_menu.addAction(a)

        file_menu.addSeparator()

        act_exit = QAction("退出", self)
        act_exit.setShortcut("Ctrl+Q")
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        data_menu = menubar.addMenu("数据(&D)")
        for label, cb in (("导入用户数据…", self._on_import_data),
                          ("检查更新…", self._on_check_update),
                          ("重新加载数据", self._on_reload_data)):
            a = QAction(label, self)
            a.triggered.connect(cb)
            data_menu.addAction(a)
        data_menu.addSeparator()

        a = QAction("迁移命令…", self)
        a.triggered.connect(self._on_migrate_command)
        data_menu.addAction(a)

        data_menu.addSeparator()
        act_graph = QAction("查看 Execute 节点图", self)
        act_graph.triggered.connect(self._on_show_execute_graph)
        data_menu.addAction(act_graph)

        scene_menu = menubar.addMenu("场景(&S)")
        act_save_scene = QAction("保存当前场景…", self)
        act_save_scene.triggered.connect(self._on_save_scene)
        scene_menu.addAction(act_save_scene)

        act_load_scene = QAction("加载场景…", self)
        act_load_scene.triggered.connect(self._on_load_scene)
        scene_menu.addAction(act_load_scene)

        help_menu = menubar.addMenu("帮助(&H)")
        about = QAction("关于", self)
        about.triggered.connect(self._on_about)
        help_menu.addAction(about)

        self._build_view_menu()
        self._build_shortcuts()

    def _build_view_menu(self) -> None:
        view = self.menuBar().addMenu("视图(&V)")

        workspace_menu = view.addMenu("工作区")
        for index, (key, label) in enumerate(WORKSPACES, 1):
            act = QAction(label, self)
            act.setShortcut(f"Ctrl+Shift+{index}")
            act.triggered.connect(
                lambda _=False, k=key: self._on_workspace_action(k))
            workspace_menu.addAction(act)
        workspace_menu.addSeparator()
        act_detach = QAction("编辑器独立成窗口", self)
        act_detach.setShortcut("Ctrl+Shift+D")
        act_detach.triggered.connect(self.toggle_editor_window)
        workspace_menu.addAction(act_detach)
        act_snapshot = QAction("打开世界时自动快照", self)
        act_snapshot.setCheckable(True)
        act_snapshot.setChecked(self.auto_snapshot)
        act_snapshot.toggled.connect(self._on_toggle_auto_snapshot)
        workspace_menu.addAction(act_snapshot)

        layout_menu = view.addMenu("布局")
        for label, key in (
            ("专注编辑", "focus"),
            ("对照输出", "output"),
            ("三栏并列", "three"),
        ):
            act = QAction(label, self)
            act.triggered.connect(lambda _=False, k=key: self._apply_layout(k))
            layout_menu.addAction(act)
        layout_menu.addSeparator()
        act_reset = QAction("恢复默认布局并记住", self)
        act_reset.triggered.connect(self._reset_layout)
        layout_menu.addAction(act_reset)

        act_toggle_output = QAction("折叠/展开输出区", self)
        act_toggle_output.setShortcut("Ctrl+Shift+O")
        act_toggle_output.triggered.connect(self._toggle_output_area)
        view.addAction(act_toggle_output)

        theme_menu = view.addMenu("主题")
        for label, key in (("深色", "dark"), ("浅色", "light"), ("跟随系统", "system")):
            act = QAction(label, self)
            act.triggered.connect(lambda _=False, k=key: self._set_theme(k))
            theme_menu.addAction(act)

    def _on_toggle_auto_snapshot(self, enabled: bool) -> None:
        self.auto_snapshot = bool(enabled)
        self._status("打开世界时会自动创建快照。" if enabled
                     else "已关闭自动快照。", 3000)

    def _build_shortcuts(self) -> None:
        for i in range(1, 10):
            act = QAction(self)
            act.setShortcut(f"Ctrl+{i}")
            act.triggered.connect(
                lambda _=False, idx=i - 1: self._switch_panel_index(idx))
            self.addAction(act)

        act_output = QAction(self)
        act_output.setShortcut("Ctrl+O")
        act_output.triggered.connect(lambda: self.output_edit.setFocus())
        self.addAction(act_output)

        act_regen = QAction(self)
        act_regen.setShortcut("Ctrl+R")
        act_regen.triggered.connect(self._on_generate)
        self.addAction(act_regen)

        act_search = QAction(self)
        act_search.setShortcut("Ctrl+P")
        act_search.triggered.connect(self._focus_command_search)
        self.addAction(act_search)

        act_help = QAction(self)
        act_help.setShortcut("Ctrl+/")
        act_help.triggered.connect(lambda: ShortcutsDialog(self).exec())
        self.addAction(act_help)

        act_validate = QAction(self)
        act_validate.setShortcut("F5")
        act_validate.triggered.connect(self._on_validate_current)
        self.addAction(act_validate)

        # Ctrl+1..9 之外，再给几个高频操作直接上快捷键。
        # Ctrl+Shift+C / Ctrl+Shift+S 的菜单项已经在 _build_menu 里注册过，
        # 这里不再重复；收藏改用 Ctrl+Shift+F（Ctrl+Shift+S 已被「NBT 另存为」占用）
        act_fav = QAction(self)
        act_fav.setShortcut("Ctrl+Shift+F")
        act_fav.triggered.connect(self._on_favorite)
        self.addAction(act_fav)

        act_give = QAction(self)
        act_give.setShortcut("Ctrl+G")
        act_give.triggered.connect(self._focus_give_panel)
        self.addAction(act_give)

        act_to_world = QAction(self)
        act_to_world.setShortcut("Ctrl+Shift+B")
        act_to_world.triggered.connect(self.send_output_to_bridge)
        self.addAction(act_to_world)

        act_world = QAction(self)
        act_world.setShortcut("Ctrl+Shift+W")
        act_world.triggered.connect(self._focus_world_editor)
        self.addAction(act_world)

    def _load_last_world(self) -> None:
        """启动时把上次打开的世界路径填进联动上下文（不自动打开）。"""
        try:
            saved = self._draft_store.load("last_world") or {}
        except Exception:                               # pragma: no cover
            return
        path = str(saved.get("path", "") or "")
        if not path:
            return
        if Path(path).is_dir():
            self.bridge_panel.log(
                f"上次打开的世界：{path}（菜单 文件 → 打开世界存档 可继续）")

    def _focus_give_panel(self) -> None:
        self._goto_command("give")
        panel = self._panels.get("give")
        if panel is None:                               # pragma: no cover
            return
        picker = getattr(panel, "item_select", None)
        if picker is not None:
            picker.setFocus()
        self.statusBar().showMessage("已切换到 /give，可直接搜索物品。", 2500)

    def _focus_command_search(self) -> None:
        search = getattr(self, "command_search", None)
        if search is not None:
            search.setFocus()
            search.selectAll()

    def _focus_nbt_editor(self) -> None:
        editor = getattr(self, "nbt_file_editor", None)
        if editor is None:
            return
        # 世界编辑模式下 NBT 页签属于中间栏，但 center_stack 还停在世界面板 → 
        # 文件加载了却看不见，这里先把中间栏切回命令页签集合
        self._ensure_side_panes()
        self._show_command_tabs()
        self.center_tabs.setCurrentWidget(editor)
        self._update_guidance()
        self._status("NBT 编辑器：可打开 .dat / .nbt / .snbt，"
                     "支持树形与文本两种编辑方式。", 4000)

    def _focus_world_editor(self) -> None:
        """切到世界与 NBT 编辑器面板。"""
        if not hasattr(self, "world_panel"):            # pragma: no cover
            return
        if getattr(self, "_editor_detached", False):
            # 编辑器已经独立成窗口：中间栏没有它，选代理页签只会显示空白
            window = getattr(self, "editor_window", None)
            if window is not None:
                window.show()
                window.raise_()
                window.activateWindow()
            self._status("世界编辑器在独立窗口里（Ctrl+Shift+D 可收回主窗口）。",
                         4000)
            self._update_guidance()
            return
        if self.workspace == "command":
            self.set_workspace("dual")
        elif self.workspace == "editor":
            if self.center_stack.indexOf(self.world_panel) >= 0:
                self.center_stack.setCurrentWidget(self.world_panel)
        # 双栏模式：选中中间栏的「世界编辑器」页签 → 中间栏显示世界面板
        proxy = getattr(self, "world_tab_proxy", None)
        if proxy is not None and self.workspace == "dual":
            if self.center_tabs.currentWidget() is not proxy:
                self.center_tabs.setCurrentWidget(proxy)
            if self.center_stack.indexOf(self.world_panel) >= 0:
                self.center_stack.setCurrentWidget(self.world_panel)
        self.world_panel.setFocus()
        self._update_guidance()
        self._status(
            "世界编辑器：可用「打开世界存档…」读取 .minecraft/saves/世界名；"
            "默认只读，解锁后才能编辑。", 6000)

    def _build_toolbar(self) -> None:
        """顶部导航条（2.1 重新设计）。

        旧版是一排 11 个纯文字按钮，主次不分、也看不出当前模式。新版分四段：
        品牌 + 首页 ｜ 模式切换 ｜ 查找命令 / 中文输入 ｜ 常用动作 + 更多菜单。
        原来的每个动作都还在（更多菜单 / 菜单栏 / 快捷键），只是不再挤在一起。
        """
        tb = QToolBar("主导航", self)
        tb.setMovable(False)
        tb.setFloatable(False)
        self.addToolBar(tb)

        self.navbar = NavBar(self)
        self.navbar.modeChanged.connect(self._on_nav_mode)
        self.navbar.actionTriggered.connect(self._on_nav_action)
        self.navbar.set_mode(self.workspace)

        self.command_search = CommandSearch(self)
        self.command_search.setFixedWidth(230)
        self.command_search.setToolTip(
            "按命令名 / 中文名 / 分类找命令（Ctrl+P）")
        self.command_search.commandChosen.connect(self._on_search_command)
        self.command_search.notFound.connect(self._on_search_not_found)
        self.navbar.add_middle_widget(self.command_search)

        self.nlp_input = NlpInput(self)
        self.nlp_input.setFixedWidth(250)
        self.nlp_input.setToolTip(
            "用中文描述直接生成命令，例如「给所有玩家 10 个钻石」")
        self.nlp_input.commandGenerated.connect(self._on_nlp_command)
        self.nlp_input.parseFailed.connect(self._on_nlp_failed)
        self.navbar.add_middle_widget(self.nlp_input)

        # 次要动作收进「更多 ▾」，保持导航条干净。
        # 注意：这里**不能**再 setShortcut —— 快捷键由 _build_shortcuts 里
        # 那几个 QAction 统一持有，同一个窗口里重复注册会变成
        # 「Ambiguous shortcut overload」而互相顶掉。
        more = self.navbar.more_menu
        for label, slot in (
                ("重新生成命令　Ctrl+R", self._on_generate),
                ("NBT → 输出", self._on_sync_nbt_to_output),
                ("目标选择器 → 输出", self._on_sync_target_to_output),
                ("输出 → 存档…　Ctrl+Shift+B", self.send_output_to_bridge)):
            act = QAction(label, self)
            act.triggered.connect(slot)
            more.addAction(act)
        more.addSeparator()
        for label, slot in (("折叠 / 展开输出区　Ctrl+Shift+O",
                             self._toggle_output_area),
                            ("折叠 / 展开底部面板", self._toggle_bottom_area),
                            ("折叠 / 展开命令列表", self._toggle_tree_area),
                            ("编辑器独立成窗口　Ctrl+Shift+D",
                             self.toggle_editor_window)):
            act = QAction(label, self)
            act.triggered.connect(slot)
            more.addAction(act)
        more.addSeparator()
        act_home = QAction("回到首页　Ctrl+Shift+H", self)
        act_home.triggered.connect(self.show_home)
        more.addAction(act_home)

        tb.addWidget(self.navbar)

    # ==================================================================
    # 壳层：导航 / 首页 / 命令面板 / 上下文引导
    # ==================================================================
    def _build_shell_shortcuts(self) -> None:
        # 注意：快捷键帮助用 Shift+F1，不用 F2 —— F2 在 Qt 里是
        # QAbstractItemView 的「就地编辑」键，窗口级 QAction 会把它吃掉，
        # 批量生成 / 世界表格 / NBT 树里按 F2 就编辑不了单元格了。
        for shortcut, slot in (("Ctrl+K", self.open_palette),
                               ("F1", self.open_welcome),
                               ("Shift+F1", self.open_shortcuts),
                               ("Ctrl+Shift+H", self.show_home)):
            act = QAction(self)
            act.setShortcut(shortcut)
            act.triggered.connect(slot)
            self.addAction(act)

    def _on_nav_mode(self, key: str) -> None:
        self.set_workspace(key)

    def _on_nav_action(self, action: str) -> None:
        handlers = {
            "home": self.show_home,
            "palette": self.open_palette,
            "open-world": self._on_open_world,
            "open-nbt": self._open_nbt_from_home,
            "welcome": self.open_welcome,
            "shortcuts": self.open_shortcuts,
            "settings": self._on_open_settings,
            "about": self._on_about,
        }
        handler = handlers.get(action)
        if handler is not None:
            handler()

    def _open_nbt_from_home(self) -> None:
        self._focus_nbt_editor()
        self._on_open_nbt_file()

    # ---- 首页 ----
    def show_home(self) -> None:
        """回到首页（挑任务 / 看最近 / 看上手进度）。"""
        self._ensure_side_panes()
        if hasattr(self, "center_stack"):
            if self.center_stack.indexOf(self.center_tabs) >= 0:
                self.center_stack.setCurrentWidget(self.center_tabs)
            self.center_tabs.setCurrentWidget(self.home_page)
        self._refresh_home()
        self._update_guidance()

    def _refresh_home(self) -> None:
        page = getattr(self, "home_page", None)
        if page is None:
            return
        page.set_checklist(self._checklist_done())
        commands: list[str] = []
        try:
            commands = [row.command for row in HistoryStore().list(limit=6)]
        except Exception:
            commands = []
        page.set_recent_commands(commands)
        entries: list[dict] = []
        try:
            entries = self.world_cache.recent(limit=6)
        except Exception:
            entries = []
        page.set_recent_worlds(entries)

    def _checklist_done(self) -> set[str]:
        return set(self._onboarding)

    def _mark_onboarding(self, key: str) -> None:
        """勾掉上手清单的一步（并落盘，重启后仍然打勾）。"""
        if key in self._onboarding:
            return
        self._onboarding.add(key)
        try:
            self._draft_store.save("onboarding",
                                   {"done": sorted(self._onboarding)})
        except Exception:
            pass
        if getattr(self, "home_page", None) is not None \
                and self.center_tabs.currentWidget() is self.home_page:
            self._refresh_home()

    def _on_home_navigate(self, route: str) -> None:
        """首页卡片 / 清单按钮的路由。"""
        if route == "palette":
            self.open_palette()
        elif route == "cmd:give":
            self._focus_give_panel()
        elif route == "world":
            self._focus_world_editor()
            if self.world_panel.session is None:
                self._on_open_world()
        elif route == "nbt-open":
            self._open_nbt_from_home()
        elif route == "datapack":
            self._ensure_side_panes()
            self._show_command_tabs()
            self.center_tabs.setCurrentWidget(self.datapack_panel)
        elif route == "nlp":
            self._ensure_side_panes()
            self._show_command_tabs()
            self.nlp_input.setFocus()
            self.nlp_input.selectAll()
            self._status("用中文描述你的需求，回车生成命令。", 4000)
        elif route == "copy-current":
            self._ensure_side_panes()
            self._show_command_tabs()
            self._on_copy_output()
        self._update_guidance()

    def _show_command_tabs(self) -> None:
        """确保中间栏显示的是命令面板集合（而不是世界编辑器）。"""
        if hasattr(self, "center_stack") \
                and self.center_stack.indexOf(self.center_tabs) >= 0:
            self.center_stack.setCurrentWidget(self.center_tabs)

    # ---- 命令面板（Ctrl+K）----
    def _palette_entries(self) -> list[PaletteEntry]:
        entries: list[PaletteEntry] = []
        for cls in iter_builders():
            name = cls.command_name
            label = cls.display_name or f"/{name}"
            entries.append(PaletteEntry(
                title=f"/{name}",
                subtitle=label,
                keywords=f"{cls.category or ''} {label}",
                category="命令",
                callback=(lambda n=name: self._goto_command(n))))
        for index in range(self.center_tabs.count()):
            widget = self.center_tabs.widget(index)
            if widget is getattr(self, "world_tab_proxy", None):
                entries.append(PaletteEntry(
                    title="世界编辑器",
                    subtitle="世界总览 / 玩家 / 实体 / 容器 / 区域 / 数据包…",
                    keywords="世界 存档 level.dat 玩家 实体 容器 地图 区域",
                    category="页签",
                    callback=self._focus_world_editor))
                continue
            name = self.center_tabs.tabText(index)
            entries.append(PaletteEntry(
                title=name,
                subtitle="中间栏页签",
                keywords=f"页签 {name}",
                category="页签",
                callback=(lambda w=widget: self._goto_center_tab(w))))
        for index in range(self.world_panel.tabs.count()):
            name = self.world_panel.tabs.tabText(index)
            entries.append(PaletteEntry(
                title=f"世界编辑器 → {name}",
                subtitle="打开存档后可直接跳到该页签",
                keywords=f"存档 世界 {name}",
                category="世界",
                callback=(lambda i=index: self._goto_world_tab(i))))
        actions = (
            ("打开世界存档…", "打开 .minecraft/saves/世界名（Ctrl+Shift+K）",
             "存档 世界 打开", self._on_open_world),
            ("打开 NBT / .dat 文件…", "编辑 level.dat、playerdata、.nbt、.snbt",
             "文件 nbt dat 打开", self._open_nbt_from_home),
            ("生成 / 重新生成命令", "把当前面板的命令送到输出区（Ctrl+R）",
             "generate 重新生成 输出", self._on_generate),
            ("复制输出命令", "复制到剪贴板，粘到游戏聊天栏（Ctrl+Shift+C）",
             "copy 复制 剪贴板", self._on_copy_output),
            ("把输出写进存档…", "预览差异后写入世界（Ctrl+Shift+B）",
             "存档 写入 桥接", self.send_output_to_bridge),
            ("回到首页", "任务卡片 / 最近 / 上手清单（Ctrl+Shift+H）",
             "home 首页 开始", self.show_home),
            ("空/解锁存档编辑", "只读 ↔ 可编辑（写入前自动备份）",
             "解锁 只读 编辑", self.world_panel.toggle_lock),
            ("预览存档差异", "先看差异再决定是否写盘",
             "差异 diff 预览", self.world_panel.preview_diff),
            ("检查存档健康", "损坏区块 / 缺失数据包 / UUID 冲突",
             "健康 检查 health", self.world_panel.run_health_check),
            ("深色主题", "切换成深色", "主题 深色 dark",
             lambda: self._set_theme("dark")),
            ("浅色主题", "切换成浅色", "主题 浅色 light",
             lambda: self._set_theme("light")),
            ("设置…", "专有名词翻译、NBT 显示选项", "设置 翻译",
             self._on_open_settings),
            ("快捷键一览", "全部快捷键（F2）", "快捷键 帮助",
             self.open_shortcuts),
            ("快速上手向导", "三步生成第一条命令（F1）", "帮助 上手 向导",
             self.open_welcome),
        )
        for title, subtitle, keywords, callback in actions:
            entries.append(PaletteEntry(title=title, subtitle=subtitle,
                                        keywords=keywords, category="动作",
                                        callback=callback))
        return entries

    def open_palette(self) -> None:
        if self._palette is None:
            self._palette = CommandPalette(self)
            self._palette.entryTriggered.connect(
                lambda _e: self._update_guidance())
        self._palette.open_palette(self._palette_entries())

    def open_welcome(self) -> None:
        if self._welcome is None:
            self._welcome = WelcomeDialog(
                self, home_enabled=self._start_on_home_pref())
            self._welcome.navigateRequested.connect(self._on_home_navigate)
            self._welcome.homePreferenceChanged.connect(self._set_start_on_home)
        self._welcome.show()
        self._welcome.raise_()

    # ---- 「启动时显示首页」偏好（默认开）----
    def _start_on_home_pref(self) -> bool:
        try:
            saved = self._draft_store.load("startup") or {}
        except Exception:                               # pragma: no cover
            return True
        return bool(saved.get("home", True))

    def _set_start_on_home(self, enabled: bool) -> None:
        try:
            self._draft_store.save("startup", {"home": bool(enabled)})
        except Exception:                               # pragma: no cover
            pass
        self._status("启动时会显示首页。" if enabled
                     else "启动时会回到上次的工作区与页签。", 3000)

    def open_shortcuts(self) -> None:
        dialog = ShortcutsDialog(self)
        # 反复按快捷键不要在主窗口下堆积隐藏对话框
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.exec()

    def _ensure_side_panes(self) -> None:
        """壳层导航前先把工作区切到「看得见目标面板」的模式。

        否则在「世界编辑」模式下按 Ctrl+K 选 /give，会显示命令面板但左侧
        命令树、右侧输出区都还是隐藏的，导航条也还高亮着「世界编辑」。
        """
        if getattr(self, "workspace", "") == "editor":
            self.set_workspace("dual")

    def _goto_command(self, name: str) -> None:
        """跳到某条命令：切回命令面板 + 选中命令树节点 + 刷新引导。"""
        self._ensure_side_panes()
        self._show_command_tabs()
        if self.center_tabs.currentWidget() is not self.panel_stack:
            self.center_tabs.setCurrentWidget(self.panel_stack)
        self._select_command_in_tree(name)
        panel = self._panels.get(name)
        if panel is not None:
            self.panel_stack.setCurrentWidget(panel)
        self._update_guidance()

    def _goto_center_tab(self, widget) -> None:
        self._show_command_tabs()
        index = self.center_tabs.indexOf(widget)
        if index >= 0:
            self.center_tabs.setCurrentIndex(index)
        self._update_guidance()

    def _goto_world_tab(self, index: int) -> None:
        self._focus_world_editor()
        if 0 <= index < self.world_panel.tabs.count():
            self.world_panel.tabs.setCurrentIndex(index)
        self._update_guidance()

    # ---- 搜索 / 中文输入失败反馈 ----
    def _on_search_not_found(self, text: str) -> None:
        hint = f"没有找到和「{text}」匹配的命令"
        self.statusBar().showMessage(
            f"{hint} —— 试试中文名（方块 / 实体），或按 Ctrl+K 搜索全部功能。",
            5000)
        self.guide_strip.set_hint(
            f"{hint}。可以按 Ctrl+K 打开命令面板搜「物品」「存档」「时间」这类词。",
            [("打开命令面板 (Ctrl+K)", self.open_palette)])

    def _on_nlp_failed(self, text: str) -> None:
        self.statusBar().showMessage(
            f"没能理解「{text}」——已给出可用句式提示，换个说法再试。", 5000)

    # ---- 上下文引导条 ----
    def _update_guidance(self) -> None:
        """按当前状态刷新底部引导条：永远告诉用户下一步点哪里。"""
        strip = getattr(self, "guide_strip", None)
        if strip is None:
            return
        try:
            self._update_guidance_impl(strip)
        except Exception:                                # pragma: no cover
            strip.set_hint("", [])

    def _update_guidance_impl(self, strip: GuideStrip) -> None:
        world_panel = getattr(self, "world_panel", None)
        center = self.center_stack.currentWidget() if hasattr(
            self, "center_stack") else None
        # 1) 世界 / 存档编辑器优先
        if self.workspace == "editor" or center is world_panel:
            self._guide_world(strip)
            return
        # 2) 中间栏的独立页签
        current = self.center_tabs.currentWidget()
        if current is self.home_page:
            strip.set_steps([])
            strip.set_hint(
                "挑一个任务卡片开始；也可以按 Ctrl+K 直接搜命令或功能。",
                [("搜索命令 (Ctrl+K)", self.open_palette),
                 ("看快速上手 (F1)", self.open_welcome)])
            return
        if current is self.nbt_file_editor:
            # 路径在 doc 上（NbtFileEditor 自己没有 path 属性）
            path = getattr(getattr(self.nbt_file_editor, "doc", None),
                           "path", None)
            dirty = bool(getattr(getattr(self.nbt_file_editor, "doc", None),
                                 "dirty", False))
            if not path:
                strip.set_steps([])
                strip.set_hint(
                    "NBT 编辑器：打开 .dat / .nbt / .snbt 文件，"
                    "或把文件直接拖进窗口。",
                    [("打开 NBT 文件…", self._on_open_nbt_file),
                     ("新建文档", self._on_new_nbt_file)])
            else:
                strip.set_steps([])
                mark = "有未保存的修改 —— " if dirty else ""
                strip.set_hint(
                    f"正在编辑：{Path(path).name}　{mark}改完 Ctrl+S 保存"
                    "（按原压缩格式写回，不会把 level.dat 压坏）。",
                    [("保存 (Ctrl+S)", self._on_save_nbt_file_direct),
                     ("另存为…", self._on_save_nbt_file)])
            return
        if current is self.target_editor:
            strip.set_steps([])
            strip.set_hint(
                "目标选择器：拼好条件后点「目标选择器 → 输出」"
                "或「更多 ▾ → 目标选择器 → 输出」写进输出区。",
                [("送到输出区", self._on_sync_target_to_output)])
            return
        if current is self.datapack_panel:
            strip.set_steps([])
            strip.set_hint(
                "数据包工程：新建 / 载入目录 → 编辑函数 → 「发送到输出区」；"
                "页签标题带 * 表示还有没写盘的改动，记得「保存项目」。",
                self._panel_actions(self.datapack_panel,
                                    (("保存项目", "_on_save_project"),)))
            return
        if current is self.batch_panel:
            strip.set_steps(["选命令", "填多行参数", "全部 → 输出"])
            strip.set_hint(
                "批量生成：选一条命令 → 每行一条参数（点「添加行」加行）→ "
                "点右侧「全部 → 输出」。",
                self._panel_actions(self.batch_panel,
                                    (("填充示例", "_on_fill_sample"),
                                     ("全部 → 输出", "_on_send_all_to_output"))))
            return
        if current is self.workbench_panel:
            strip.set_steps([])
            strip.set_hint(
                "工作台：从模板或底部「片段库」拖入步骤，用「缩进 →」/"
                "「← 反缩进」搭 execute 链，再导出 .mcfunction。",
                self._panel_actions(self.workbench_panel,
                                    (("送到输出区", "_on_send_to_output"),)))
            return
        if current is self.reference_panel:
            strip.set_steps([])
            strip.set_hint(
                "ID 速查：搜中文名（僵尸 / 钻石剑）或 ID；"
                "双击结果会把示例命令送进输出区。",
                [])
            return
        # 3) 命令面板
        panel = self.panel_stack.currentWidget()
        if panel is getattr(self, "_placeholder", None):
            strip.set_steps([])
            strip.set_hint(
                "这条命令的图形面板还没做，可以改用「批量生成」"
                "（任意命令都能生成）或「ID 速查」。",
                [("去批量生成", lambda: self.center_tabs.setCurrentWidget(
                    self.batch_panel)),
                 ("用命令面板搜别的命令 (Ctrl+K)", self.open_palette)])
            return
        output = self.output_edit.toPlainText().strip()
        has_command = bool(getattr(self, "_current_command_type", ""))
        is_demo = getattr(panel, "is_demo", None)
        if callable(is_demo) and is_demo():
            strip.set_steps(["改参数", "生成命令", "复制到游戏"], current=0)
            strip.set_hint(
                f"面板里填好的是示例：{panel.current_command()}　"
                "改动任意一项就会变成你的命令，并实时出现在右侧输出区。",
                [("搜索别的命令 (Ctrl+K)", self.open_palette),
                 ("去 /give 造物品", lambda: self._goto_command("give"))])
            return
        if output:
            strip.set_steps(["选命令", "填参数", "复制到游戏"], current=2,
                            done_until=2)
            # 不把「N 字符」写进文案：输出区每敲一个字都会触发刷新，
            # 文案一变就要重建按钮（字符数在输出区标题行已有）
            strip.set_hint(
                "命令已就绪：点「复制命令」粘到游戏聊天栏，"
                "或「→ 存档」预览差异后写进世界。",
                [("复制命令 (Ctrl+Shift+C)", self._on_copy_output),
                 ("→ 存档…", self.send_output_to_bridge)])
        elif has_command:
            strip.set_steps(["选命令", "填参数", "复制到游戏"], current=1)
            strip.set_hint(
                "在面板里填好参数，命令会实时出现在右侧「命令输出」；"
                "也可以随时按 Ctrl+R 重新生成。",
                [("重新生成 (Ctrl+R)", self._on_generate),
                 ("查看输出区", self._focus_output)])
        else:
            strip.set_steps(["选命令", "填参数", "复制到游戏"], current=0)
            strip.set_hint(
                "先在左侧命令树选一条命令（或按 Ctrl+K 搜），再填参数。",
                [("搜索命令 (Ctrl+K)", self.open_palette),
                 ("推荐：/give 造物品", lambda: self._goto_command("give"))])

    def _panel_actions(self, panel,
                       specs) -> list[tuple[str, object]]:
        """按属性名取面板方法，拼成引导条按钮；方法不存在就跳过。

        引导条直接写 `panel._on_xxx` 时，面板里改个私有方法名就会让整条
        引导变成空白（异常被兜底吞掉），所以统一走这里做存在性检查。
        """
        out: list[tuple[str, object]] = []
        for label, attr in specs:
            callback = getattr(panel, attr, None)
            if callable(callback):
                out.append((label, callback))
        return out

    def _guide_world(self, strip: GuideStrip) -> None:
        panel = self.world_panel
        session = panel.session
        world = panel.world() if session is not None else None
        if world is None:
            strip.set_steps(["打开存档", "只读浏览", "解锁后写入"], current=0)
            strip.set_hint(
                "还没有打开存档。选择 .minecraft/saves/世界名 后即可浏览；"
                "存档默认只读，写入前会自动备份并可回滚。",
                [("打开世界存档…", self._on_open_world),
                 ("看最近打开的存档", self._open_recent_world)])
            return
        if session is not None and session.read_only:
            strip.set_steps(["打开存档", "只读浏览", "解锁后写入"], current=1)
            strip.set_hint(
                f"已打开「{world.name}」（只读）。想改内容先解锁编辑，"
                "所有写入都会先备份到 backups/<时间戳>/。",
                [("解锁编辑", panel.toggle_lock),
                 ("检查存档健康", panel.run_health_check)])
            return
        # 用 session.unsaved_changes()：它包含「撤销回到写盘前状态」这种情况，
        # 只看 world.dirty 会和面板上的「应用修改」按钮状态自相矛盾
        if session is not None and session.unsaved_changes():
            strip.set_steps(["打开存档", "只读浏览", "解锁后写入"], current=2)
            strip.set_hint(
                "有未应用的修改：先「预览差异」核对，再「应用修改」写盘"
                "（会自动备份，可撤销）。",
                [("预览差异", panel.preview_diff),
                 ("应用修改", panel.apply_changes)])
            return
        strip.set_steps(["打开存档", "只读浏览", "解锁后写入"], current=2,
                        done_until=2)
        strip.set_hint(
            f"已打开「{world.name}」且可编辑。可以直接改表格后「应用修改」，"
            "或回到命令面板用「→ 存档」把命令写进去。",
            [("回到命令生成", self._show_command_tabs),
             ("创建快照", getattr(panel, "snapshot", None))])

    def _focus_output(self) -> None:
        """展开并聚焦输出区。"""
        sizes = self.main_splitter.sizes()
        if len(sizes) >= 3 and sizes[2] <= 20:
            self._toggle_output_area()
        self.right_tabs.setCurrentWidget(self.output_edit)
        self.output_edit.setFocus()

    def _open_recent_world(self) -> None:
        self.world_panel._open_last_world()

    # ==================================================================
    # 中央布局
    # ==================================================================
    def _build_central(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.main_splitter = splitter
        splitter.setChildrenCollapsible(True)
        splitter.setHandleWidth(4)

        # ---- 左：命令树 ----
        left = QWidget()
        left.setMinimumWidth(150)
        self.left_pane = left
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 4, 8)
        left_layout.setSpacing(6)

        tree_header = QHBoxLayout()
        tree_header.setSpacing(6)
        tree_title = QLabel("命令")
        tree_title.setStyleSheet(muted_style(tree_title))
        tree_header.addWidget(tree_title)
        tree_header.addStretch(1)
        self.btn_collapse_tree = QToolButton()
        self.btn_collapse_tree.setText("◀")
        self.btn_collapse_tree.setToolTip("折叠/展开命令列表")
        self.btn_collapse_tree.setAutoRaise(True)
        self.btn_collapse_tree.clicked.connect(self._toggle_tree_area)
        tree_header.addWidget(self.btn_collapse_tree)
        left_layout.addLayout(tree_header)

        # 命令树筛选：44 条命令里找一条不该靠翻
        self.tree_filter = QLineEdit()
        self.tree_filter.setPlaceholderText("筛选命令（中文/英文）")
        self.tree_filter.setClearButtonEnabled(True)
        self.tree_filter.setToolTip(
            "只保留匹配的命令；清空即恢复全部。也可以按 Ctrl+K 搜功能")
        self.tree_filter.textChanged.connect(self._filter_command_tree)
        left_layout.addWidget(self.tree_filter)

        self.command_tree = QTreeWidget()
        self.command_tree.setHeaderHidden(True)
        self.command_tree.setIndentation(14)
        self.command_tree.setUniformRowHeights(True)
        self.command_tree.currentItemChanged.connect(self._on_command_selected)
        left_layout.addWidget(self.command_tree, 1)

        self._populate_command_list()
        splitter.addWidget(left)

        # ---- 中：编辑区 ----
        center = QWidget()
        center.setMinimumWidth(420)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(4, 8, 4, 8)
        center_layout.setSpacing(6)

        self.center_tabs = QTabWidget()
        self.center_tabs.setDocumentMode(True)
        self.center_tabs.setUsesScrollButtons(True)
        self.center_tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.panel_stack = QStackedWidget()
        self._build_command_panels()

        # ---- 首页（首屏不再是无路可走的空白编辑器）----
        self.home_page = HomePage()
        self.home_page.navigateRequested.connect(self._on_home_navigate)
        self.home_page.commandActivated.connect(self._on_preset_activated)
        self.home_page.worldActivated.connect(self.open_world_async)
        self.center_tabs.addTab(self.home_page, "首页")

        self.center_tabs.addTab(self.panel_stack, "命令面板")

        # 「世界编辑器」占位页签：在双栏模式下点它就把中间栏切到世界面板
        # （面板本身在 center_stack 里，不能同时属于两个父容器）
        self.world_tab_proxy = QWidget()
        self.center_tabs.addTab(self.world_tab_proxy, "世界编辑器")
        self.center_tabs.currentChanged.connect(self._on_center_tab_changed)

        self.nbt_file_editor = NbtFileEditor()
        self.nbt_editor = self.nbt_file_editor.editor
        self.nbt_editor.changed.connect(
            lambda: self._status("NBT 内容已更新。", 1500))
        self.nbt_file_editor.fileChanged.connect(self._on_nbt_file_changed)
        self.nbt_file_editor.statusMessage.connect(self._status)
        self.nbt_file_editor.giveRequested.connect(self._on_nbt_give_request)
        self.nbt_file_editor.summonRequested.connect(
            self._on_nbt_summon_request)
        self.nbt_file_editor.settingsRequested.connect(self._on_open_settings)
        self.nbt_file_editor.commandsReady.connect(self._on_nbt_commands)
        self.center_tabs.addTab(self.nbt_file_editor, "NBT 编辑器")

        self.target_editor = TargetSelectorEditor()
        self.target_editor.changed.connect(
            lambda: self.statusBar().showMessage("目标选择器已更新。", 1500))
        self.center_tabs.addTab(self.target_editor, "目标选择器")

        self.workbench_panel = WorkbenchPanel()
        self.workbench_panel.commandActivated.connect(self._on_panel_activated)
        self.center_tabs.addTab(self.workbench_panel, "工作台")

        self.batch_panel = BatchPanel()
        self.batch_panel.commandActivated.connect(self._on_panel_activated)
        self.center_tabs.addTab(self.batch_panel, "批量生成")

        self.datapack_panel = DatapackPanel()
        self.datapack_panel.functionActivated.connect(self._on_panel_activated)
        self.datapack_panel.nbtLoaded.connect(self._on_datapack_nbt_loaded)
        self.datapack_panel.dirtyChanged.connect(self._on_datapack_dirty)
        self.center_tabs.addTab(self.datapack_panel, "数据包项目")

        self.reference_panel = ReferencePanel()
        self.reference_panel.commandActivated.connect(self._on_panel_activated)
        self.center_tabs.addTab(self.reference_panel, "ID 速查")

        # 页签 → 稳定 id（会话草稿按 id 恢复：标题会被文件名/NBT 状态改写）
        self._CENTER_TAB_IDS = {
            self.home_page: "home",
            self.panel_stack: "commands",
            self.world_tab_proxy: "world",
            self.nbt_file_editor: "nbt",
            self.target_editor: "target",
            self.workbench_panel: "workbench",
            self.batch_panel: "batch",
            self.datapack_panel: "datapack",
            self.reference_panel: "reference",
        }

        center_layout.addWidget(self.center_tabs, 1)
        splitter.addWidget(center)

        # ---- 右：输出 + 联动总线 ----
        # 复制 / 收藏 / 折叠都放在标题行里，省下一整行按钮的高度
        right = QWidget()
        right.setMinimumWidth(220)
        self.right_pane = right
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 8, 8, 8)
        right_layout.setSpacing(6)

        out_header = QHBoxLayout()
        out_header.setSpacing(6)
        out_title = QLabel("命令输出")
        out_title.setStyleSheet(muted_style(out_title))
        out_header.addWidget(out_title)

        self.output_status = QLabel("")
        self.output_status.setStyleSheet(hint_style(self.output_status))
        out_header.addWidget(self.output_status)
        out_header.addStretch(1)

        self.btn_collapse_output = QToolButton()
        self.btn_collapse_output.setText("▶")
        self.btn_collapse_output.setToolTip("折叠/展开输出区（Ctrl+Shift+O）")
        self.btn_collapse_output.setAutoRaise(True)
        self.btn_collapse_output.clicked.connect(self._toggle_output_area)
        out_header.addWidget(self.btn_collapse_output)
        right_layout.addLayout(out_header)

        # 动作分两行：右栏最小只有 220px，四个按钮挤一行会把主按钮压成「制命」
        out_actions = QVBoxLayout()
        out_actions.setSpacing(4)
        self.btn_copy_output = QPushButton("复制命令")
        self.btn_copy_output.setObjectName("Primary")
        self.btn_copy_output.setToolTip(
            "复制输出区命令到剪贴板，到游戏里按 T 粘贴（Ctrl+Shift+C）")
        self.btn_copy_output.clicked.connect(self._on_copy_output)
        out_actions.addWidget(self.btn_copy_output)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self.btn_to_bridge = QToolButton()
        self.btn_to_bridge.setText("→ 存档")
        self.btn_to_bridge.setToolTip(
            "把输出区命令送进联动总线，可预览差异后写入世界存档"
            "（Ctrl+Shift+B）")
        self.btn_to_bridge.setAutoRaise(True)
        self.btn_to_bridge.clicked.connect(self.send_output_to_bridge)
        row2.addWidget(self.btn_to_bridge)

        self.btn_favorite = QToolButton()
        self.btn_favorite.setText("收藏")
        self.btn_favorite.setToolTip("把输出区命令加入收藏（Ctrl+Shift+F）")
        self.btn_favorite.setAutoRaise(True)
        self.btn_favorite.clicked.connect(self._on_favorite)
        row2.addWidget(self.btn_favorite)

        self.btn_generate = QToolButton()
        self.btn_generate.setText("重新生成")
        self.btn_generate.setToolTip(
            "按当前面板的配置重新生成命令（Ctrl+R）")
        self.btn_generate.setAutoRaise(True)
        self.btn_generate.clicked.connect(self._on_generate)
        row2.addWidget(self.btn_generate)
        row2.addStretch(1)
        out_actions.addLayout(row2)
        right_layout.addLayout(out_actions)

        self.right_tabs = QTabWidget()
        self.right_tabs.setDocumentMode(True)
        self.output_edit = CommandOutputEdit()
        self.output_edit.textChanged.connect(self._on_output_text_changed)
        self.output_edit.textDropped.connect(
            lambda _t: self._status("已拖入内容到输出区。", 2000))
        self.output_edit.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.output_edit.customContextMenuRequested.connect(
            self._on_output_context_menu)
        self.right_tabs.addTab(self.output_edit, "命令输出")

        self.bridge_panel = BridgeDrawer()
        self.bridge_panel.applyRequested.connect(self._on_bridge_apply)
        self.bridge_panel.commandsRequested.connect(self._on_bridge_commands)
        self.bridge_panel.undoRequested.connect(self._on_bridge_undo)
        self.bridge_panel.linkToggled.connect(self._on_link_toggled)
        self.bridge_panel.statusMessage.connect(self._status)
        self.right_tabs.addTab(self.bridge_panel, "联动总线")
        self.right_tabs.currentChanged.connect(self._on_right_tab_changed)
        self.bridge_tabs = self.right_tabs
        right_layout.addWidget(self.right_tabs, 1)
        splitter.addWidget(right)

        self._restore_pane_sizes()
        splitter.splitterMoved.connect(self._on_splitter_moved)

        # ---- 编辑器面板（世界与 NBT 编辑器）----
        self.world_panel = WorldPanel()
        self.world_panel.giveRequested.connect(self._on_world_give_request)
        self.world_panel.summonRequested.connect(self._on_nbt_summon_request)
        self.world_panel.commandsReady.connect(self._on_nbt_commands)
        self.world_panel.bridgeRequested.connect(self._on_bridge_operation)
        self.world_panel.statusMessage.connect(self._status)
        self.world_panel.worldChanged.connect(self._on_world_changed)
        self.world_panel.openRequested.connect(self.open_world_async)
        # 大存档在后台线程读取（界面显示进度并可取消）
        self.world_panel.async_open = True

        self.center_stack = QStackedWidget()
        self.center_stack.addWidget(self.center_tabs)
        self.center_stack.addWidget(self.world_panel)
        center_layout.removeWidget(self.center_tabs)
        center_layout.addWidget(self.center_stack, 1)

        # ---- 底部 tabs ----
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.setDocumentMode(True)

        self.history_panel = HistoryPanel()
        self.history_panel.commandActivated.connect(self._on_preset_activated)
        self.bottom_tabs.addTab(self.history_panel, "历史记录")

        self.preset_panel = PresetPanel()
        self.preset_panel.commandActivated.connect(self._on_preset_activated)
        self.bottom_tabs.addTab(self.preset_panel, "收藏 / 预设")

        self.fragment_panel = FragmentPanel()
        self.fragment_panel.commandActivated.connect(self._on_preset_activated)
        self.bottom_tabs.addTab(self.fragment_panel, "片段库")

        self.validation_tree = QTreeWidget()
        self.validation_tree.setHeaderLabels(["级别", "消息"])
        self.validation_tree.setRootIsDecorated(False)
        self.validation_tree.itemDoubleClicked.connect(
            self._on_validation_double_clicked)
        self.bottom_tabs.addTab(self.validation_tree, "校验信息")

        self.btn_collapse_bottom = QToolButton()
        self.btn_collapse_bottom.setText("▼")
        self.btn_collapse_bottom.setToolTip("折叠/展开底部面板")
        self.btn_collapse_bottom.setAutoRaise(True)
        self.btn_collapse_bottom.clicked.connect(self._toggle_bottom_area)
        self.bottom_tabs.setCornerWidget(
            self.btn_collapse_bottom, Qt.Corner.TopRightCorner)
        self.bottom_tabs.setMinimumHeight(0)
        self.bottom_tabs.setMaximumHeight(self._bottom_height)

        container = QWidget()
        c_layout = QVBoxLayout(container)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(0)
        c_layout.addWidget(splitter, 1)

        # ---- 上下文引导条：永远告诉用户下一步点哪里 ----
        self.guide_strip = GuideStrip()
        c_layout.addWidget(self.guide_strip, 0)

        c_layout.addWidget(self.bottom_tabs, 0)
        self.setCentralWidget(container)

    def _on_center_tab_changed(self, _index: int) -> None:
        """中间栏选中「世界编辑器」占位页签时，把 center_stack 切到世界面板。"""
        if not hasattr(self, "center_stack") or not hasattr(
                self, "world_tab_proxy"):
            return
        current = self.center_tabs.currentWidget()
        is_world_tab = current is self.world_tab_proxy
        in_stack = self.center_stack.indexOf(self.world_panel) >= 0
        if is_world_tab and in_stack and self.workspace != "editor":
            self.center_stack.setCurrentWidget(self.world_panel)
        elif not is_world_tab and self.workspace == "dual" \
                and self.center_stack.currentWidget() is self.world_panel:
            self.center_stack.setCurrentWidget(self.center_tabs)
        self._update_guidance()

    # ==================================================================
    # 面板尺寸 / 折叠
    # ==================================================================
    def _status(self, text: str, timeout: int = 2000) -> None:
        self.statusBar().showMessage(text, timeout)

    def _restore_pane_sizes(self) -> None:
        saved = self._draft_store.load("layout") or {}
        self._bottom_height = max(0, int(saved.get("bottom", 150) or 0))
        if saved.get("default"):
            # 构建期 splitter 还没有真实宽度，等窗口显示后再按比例铺开
            self._layout_pending = True
            return
        sizes = saved.get("sizes")
        if isinstance(sizes, list) and len(sizes) == 3:
            left, center, right = (int(v) for v in sizes)
            self.left_width = max(0, left)
            self.right_width = max(0, right)
            self.main_splitter.setSizes(
                [self.left_width, max(320, center), self.right_width])
        else:
            self._layout_pending = True

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """首次显示时套用默认三栏比例（没有保存过布局的情况）。

        `resize()` 在 `show()` 之前调用时不会触发布局，于是首启会停在
        「各栏最小宽度」的样子 —— 右栏只有 220px，按钮全被压扁。
        """
        super().showEvent(event)
        if getattr(self, "_layout_pending", False):
            self._layout_pending = False
            self._apply_proportional_layout()
            self._update_collapse_buttons()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        super().resizeEvent(event)
        if getattr(self, "_layout_pending", False) and self.isVisible():
            self._layout_pending = False
            self._apply_proportional_layout()
            self._update_collapse_buttons()
        if getattr(self, "_toast", None) is not None \
                and self._toast.isVisible():
            self._place_toast()

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        sizes = self.main_splitter.sizes()
        if len(sizes) < 3:
            return
        if sizes[0] > 20:
            self.left_width = sizes[0]
        if sizes[2] > 20:
            self.right_width = sizes[2]
        self._save_layout_sizes()

    def _save_layout_sizes(self) -> None:
        try:
            sizes = self.main_splitter.sizes()
            total = max(1, sum(sizes))
            payload = {
                "sizes": [int(v) for v in sizes],
                "ratios": [round(v / total, 4) for v in sizes],
                "bottom": self._bottom_height,
            }
            self._draft_store.save("layout", payload)
        except Exception:
            pass

    def _toggle_tree_area(self) -> None:
        if not hasattr(self, "main_splitter"):
            return
        sizes = self.main_splitter.sizes()
        if len(sizes) < 3:
            return
        if sizes[0] > 20:
            self.left_width = sizes[0]
            self.main_splitter.setSizes([0, sizes[1] + sizes[0], sizes[2]])
        else:
            total = max(600, self.main_splitter.width())
            restore = self.left_width or max(
                190, int(total * self.DEFAULT_RATIOS["left"]))
            self.main_splitter.setSizes(
                [restore, max(320, sizes[1] - restore), sizes[2]])
        self._update_collapse_buttons()
        self._save_layout_sizes()

    def _toggle_bottom_area(self) -> None:
        if self._bottom_height > 0:
            self._bottom_height = 0
            self.bottom_tabs.setMaximumHeight(0)
            self.btn_collapse_bottom.setText("▲")
            self.btn_collapse_bottom.setToolTip("展开底部面板")
        else:
            self._bottom_height = max(120, int(self.DEFAULT_RATIOS["bottom"]))
            self.bottom_tabs.setMaximumHeight(self._bottom_height)
            self.btn_collapse_bottom.setText("▼")
            self.btn_collapse_bottom.setToolTip("折叠底部面板")
        self._save_layout_sizes()

    def _populate_command_list(self) -> None:
        self.command_tree.clear()
        self._recent_item = None

        by_cat: dict[str, list] = {}
        for cls in iter_builders():
            by_cat.setdefault(cls.category or "其他", []).append(cls)

        recent = self._recent_commands(limit=5)
        if recent:
            recent_item = QTreeWidgetItem(["最近使用"])
            self.command_tree.addTopLevelItem(recent_item)
            self._fill_recent(recent_item, recent)
            recent_item.setExpanded(True)
            self._recent_item = recent_item

        ordered_cats = [c for c in CATEGORY_ORDER if c in by_cat]
        ordered_cats += [c for c in by_cat if c not in ordered_cats]

        for cat in ordered_cats:
            cat_item = QTreeWidgetItem([cat])
            self.command_tree.addTopLevelItem(cat_item)
            for cls in by_cat[cat]:
                child = QTreeWidgetItem([cls.display_name])
                child.setData(0, Qt.ItemDataRole.UserRole, cls.command_name)
                # 窄栏里名称会被截断，tooltip 给全名 + 分类
                child.setToolTip(0, f"{cls.display_name}（{cat}）")
                cat_item.addChild(child)
            cat_item.setExpanded(cat == "核心命令")
        filter_text = self.tree_filter.text() if hasattr(
            self, "tree_filter") else ""
        self._filter_command_tree(filter_text)

    def _filter_command_tree(self, text: str) -> None:
        """按关键字过滤命令树（命令名 / 中文名 / 分类，中英文都行）。"""
        needle = (text or "").strip().lower()
        tree = getattr(self, "command_tree", None)
        if tree is None:                                 # pragma: no cover
            return
        for index in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(index)
            if top is getattr(self, "_recent_item", None):
                # 「最近使用」是历史分组，不参与过滤：一搜索就先收起来
                top.setHidden(bool(needle) or top.childCount() == 0)
                continue
            category = top.text(0).lower()
            visible = 0
            for child in range(top.childCount()):
                item = top.child(child)
                key = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
                haystack = f"{item.text(0)} {key} {category}".lower()
                match = (not needle) or needle in haystack
                item.setHidden(not match)
                if match:
                    visible += 1
            has_children = top.childCount() > 0
            top.setHidden(has_children and visible == 0)
            if needle and visible:
                top.setExpanded(True)

    def _fill_recent(self, parent: QTreeWidgetItem, recent: list[str]) -> None:
        for cmd in recent:
            cls = get_builder(cmd)
            if cls is None:
                continue
            child = QTreeWidgetItem([cls.display_name])
            child.setData(0, Qt.ItemDataRole.UserRole, cls.command_name)
            parent.addChild(child)

    def _refresh_recent(self) -> None:
        """历史变化后只重建「最近使用」分组，不动其余节点与选中状态。"""
        recent = self._recent_commands(limit=5)
        item = getattr(self, "_recent_item", None)
        if item is None:
            if not recent:
                return
            item = QTreeWidgetItem(["最近使用"])
            self.command_tree.insertTopLevelItem(0, item)
            self._recent_item = item
        item.takeChildren()
        self._fill_recent(item, recent)
        item.setExpanded(True)
        item.setHidden(not recent)
        # 正在筛选命令时，新建/更新的「最近使用」分组也要跟着筛选状态隐藏，
        # 否则会突然冒出一个含不匹配命令的分组
        if hasattr(self, "tree_filter") and self.tree_filter.text().strip():
            item.setHidden(True)

    def _recent_commands(self, limit: int = 5) -> list[str]:
        try:
            rows = HistoryStore().list(limit=80)
        except Exception:
            return []
        seen, out = set(), []
        for r in rows:
            cmd = r.command.strip().lstrip("/")
            if not cmd:
                continue
            first = cmd.split(None, 1)[0]
            if first in all_builders() and first not in seen:
                seen.add(first)
                out.append(first)
            if len(out) >= limit:
                break
        return out

    def _build_command_panels(self) -> None:
        self.summon_panel = SummonPanel()
        self.summon_panel.commandChanged.connect(self._on_command_generated)
        self.summon_panel.commandCopied.connect(self._on_panel_copied)
        self.summon_panel.commandSent.connect(self._on_panel_sent)
        self._panels["summon"] = self.summon_panel
        self.panel_stack.addWidget(self.summon_panel)

        self.give_panel = GivePanel()
        self.give_panel.commandChanged.connect(self._on_command_generated)
        self.give_panel.commandCopied.connect(self._on_panel_copied)
        self.give_panel.commandSent.connect(self._on_panel_sent)
        self._panels["give"] = self.give_panel
        self.panel_stack.addWidget(self.give_panel)

        self.execute_panel = ExecutePanel()
        self.execute_panel.commandChanged.connect(self._on_command_generated)
        self.execute_panel.commandCopied.connect(self._on_panel_copied)
        self.execute_panel.commandSent.connect(self._on_panel_sent)
        self._panels["execute"] = self.execute_panel
        self.panel_stack.addWidget(self.execute_panel)

        placeholder = QLabel("该命令面板将在后续阶段实现。")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet(muted_style(placeholder))
        self._placeholder = placeholder
        self.panel_stack.addWidget(placeholder)

        for cls in iter_builders():
            name = cls.command_name
            if name in self._panels:
                continue
            if not issubclass(cls, SchemaBuilder):
                continue
            try:
                panel = SchemaPanel(cls())
            except Exception:
                continue
            panel.commandChanged.connect(self._on_command_generated)
            for signal_name, slot in (("commandCopied", self._on_panel_copied),
                                      ("commandSent", self._on_panel_sent)):
                signal = getattr(panel, signal_name, None)
                if signal is not None:
                    signal.connect(slot)
            self._panels[name] = panel
            self.panel_stack.addWidget(panel)

    def _build_statusbar(self) -> None:
        self.setStatusBar(QStatusBar(self))
        # 状态栏右侧：当前世界 / 玩家 / 维度 / 坐标 / 保存状态
        self.ctx_world = QLabel("世界：—")
        self.ctx_player = QLabel("玩家：—")
        self.ctx_dimension = QLabel("维度：—")
        self.ctx_pos = QLabel("坐标：—")
        self.ctx_save = QLabel("只读")
        for widget in (self.ctx_world, self.ctx_player, self.ctx_dimension,
                       self.ctx_pos, self.ctx_save):
            widget.setStyleSheet(hint_style(widget))
            self.statusBar().addPermanentWidget(widget)
        self.statusBar().showMessage(
            f"就绪 — {branding.APP_NAME} · Minecraft Java "
            f"{branding.TARGET_MC_VERSION}")

    def _refresh_status_context(self) -> None:
        """刷新状态栏的联动上下文。"""
        if not hasattr(self, "ctx_world"):              # pragma: no cover
            return
        panel = getattr(self, "world_panel", None)
        session = panel.session if panel is not None else None
        world = session.world if session is not None else None
        if world is None:
            self.ctx_world.setText("世界：—")
            self.ctx_player.setText("玩家：—")
            self.ctx_dimension.setText("维度：—")
            self.ctx_pos.setText("坐标：—")
            self.ctx_save.setText("只读")
            return
        self.ctx_world.setText(f"世界：{world.name}")
        player = panel.current_player() if panel is not None else None
        if player is not None:
            self.ctx_player.setText(f"玩家：{player.display()}")
            self.ctx_dimension.setText(f"维度：{player.dimension or '—'}")
            self.ctx_pos.setText(
                "坐标：" + " ".join(f"{v:.1f}" for v in player.pos))
        else:
            self.ctx_player.setText("玩家：—")
            self.ctx_dimension.setText("维度：—")
            self.ctx_pos.setText(
                "坐标：" + " ".join(str(v) for v in world.overview.spawn))
        state = "只读" if session.read_only else "可编辑"
        if world.dirty:
            state += " · 有未应用的修改"
        self.ctx_save.setText(state)

    # ==================================================================
    # 演示数据
    # ==================================================================
    def _load_demo_nbt(self) -> None:
        from app.core.snbt import compound, string, byte, boolean, list_node
        demo = compound(
            CustomName=string('{"text":"演示僵尸","color":"red"}'),
            IsBaby=byte(0),
            CanPickUpLoot=boolean(True),
            Tags=list_node([string("demo"), string("stage7")]),
            Attributes=list_node([
                compound(Name=string("minecraft:generic.max_health"),
                         Base=byte(20))]),
        )
        self.nbt_editor.set_root(demo)

    def _load_demo_target(self) -> None:
        from app.core.workbench import TargetSelector
        self.target_editor.set_selector(TargetSelector(
            base="@e",
            filters={"type": "minecraft:zombie", "distance": "..10",
                     "sort": "nearest", "limit": "1"}))

    # ==================================================================
    # 事件处理
    # ==================================================================
    def _on_command_selected(self, current: QTreeWidgetItem, _prev) -> None:
        if current is None:
            return
        key = current.data(0, Qt.ItemDataRole.UserRole)
        if not key:
            return
        self._current_command_type = key
        self.statusBar().showMessage(f"已选择命令：/{key}")
        panel = self._panels.get(key)
        if panel is not None:
            self.panel_stack.setCurrentWidget(panel)
            # 选了命令就一定要看得见命令面板：否则在「世界编辑器」占位页签里
            # 点左侧命令树会像「点了没反应」（旧版就是这个 bug）
            self._show_command_tabs()
            if self.center_tabs.currentWidget() is not self.panel_stack:
                self.center_tabs.setCurrentWidget(self.panel_stack)
            # 输出区同步成这条命令的当前内容，避免「面板有命令、输出区空着」
            command = getattr(panel, "current_command", None)
            is_demo = getattr(panel, "is_demo", None)
            if callable(command) and not (callable(is_demo) and is_demo()):
                text = (command() or "").strip()
                if text and text != self.output_edit.toPlainText().strip():
                    self.output_edit.setPlainText(text)
        else:
            self.panel_stack.setCurrentWidget(self._placeholder)
            self._placeholder.setText(
                f"/{key} 的图形面板还没实现。\n"
                "可以改用「批量生成」页签（任意命令都能填表生成），"
                "或按 Ctrl+K 搜其它命令。")
            self._show_command_tabs()
            if self.center_tabs.currentWidget() is not self.panel_stack:
                self.center_tabs.setCurrentWidget(self.panel_stack)
        self._update_guidance()

    def _on_search_command(self, command_name: str) -> None:
        self._select_command_in_tree(command_name)

    def _select_command_in_tree(self, command_name: str) -> None:
        for i in range(self.command_tree.topLevelItemCount()):
            top = self.command_tree.topLevelItem(i)
            for j in range(top.childCount()):
                child = top.child(j)
                if child.data(0, Qt.ItemDataRole.UserRole) == command_name:
                    self.command_tree.setCurrentItem(child)
                    top.setExpanded(True)
                    return

    def _on_nlp_command(self, cmd: str) -> None:
        self.output_edit.setPlainText(cmd)
        self.statusBar().showMessage(f"语义化生成：{cmd}", 3000)

    def _on_command_generated(self, command: str) -> None:
        self.output_edit.setPlainText(command)
        self._mark_onboarding("generate")

    def _on_panel_copied(self, command: str) -> None:
        """命令面板内部「复制命令」按钮的反馈（面板里就地就有了）。"""
        self.history_panel.add(command, self._current_command_type)
        self._refresh_recent()
        self._mark_onboarding("copy")
        self._set_output_unread(False)
        self._status("已从命令面板复制到剪贴板。", 3000)

    def _on_panel_sent(self, command: str) -> None:
        """命令面板里的「发送到输出区」：真的把命令写回输出区。

        （以前这个信号被接到了「已复制」，于是状态栏说复制成功、
        上手清单打勾，而剪贴板根本没变。）
        """
        self.output_edit.setPlainText(command)
        self._set_output_unread(True)
        self._mark_onboarding("generate")
        self._show_toast("✔ 已发送到右侧「命令输出」")
        self._update_guidance()

    def _on_panel_activated(self, text: str) -> None:
        if not (text or "").strip():
            # 空串会把用户输出区清空 —— 绝不能悄悄这么做
            self._status("没有可发送的内容：先在对应面板里生成点东西。", 4000)
            return
        self.output_edit.setPlainText(text)
        self._mark_onboarding("generate")
        # 跨页签动作（批量生成 / 工作台 / 数据包 → 输出）的可见反馈：
        # 旧版只写文本、状态栏闪 2 秒，用户不知道去哪看结果
        self._set_output_unread(True)
        self._status("已发送到右侧「命令输出」，可以复制或写进存档。", 4000)
        self._update_guidance()

    def _on_output_text_changed(self) -> None:
        text = self.output_edit.toPlainText()
        cmd = text.strip()
        if not cmd:
            if hasattr(self, "output_status"):
                self.output_status.setText("")
            self._set_output_unread(False)
            # 必须先刷新引导：否则把输出清空后，引导条还挂着「命令已就绪」
            self._update_guidance()
            return
        self._mark_onboarding("generate")
        self._pending_history = (cmd, self._current_command_type)
        self._history_timer.start()

        first = cmd.lstrip("/").split(None, 1)[0]
        if hasattr(self, "output_status"):
            self.output_status.setText(f"{len(text)} 字符 · /{first}")
        self._update_guidance()

    def _on_datapack_dirty(self, dirty: bool) -> None:
        """数据包工程有未保存改动时，页签标题加 *（旧版完全看不出来）。"""
        index = self.center_tabs.indexOf(self.datapack_panel)
        if index < 0:                                    # pragma: no cover
            return
        self.center_tabs.setTabText(index,
                                    "数据包项目 *" if dirty else "数据包项目")
        if dirty:
            self.center_tabs.setTabToolTip(index, "有未保存的函数改动")
        else:
            self.center_tabs.setTabToolTip(index, "")

    def _on_right_tab_changed(self, index: int) -> None:
        """切到「命令输出」页签即清掉未读标记。"""
        widget = self.right_tabs.widget(index) if index >= 0 else None
        if widget is getattr(self, "output_edit", None):
            self._set_output_unread(False)
        self._update_guidance()

    def _set_output_unread(self, unread: bool) -> None:
        """输出页签的未读标记（结果在别的页签时也能看见）。"""
        if not hasattr(self, "right_tabs"):
            return
        index = self.right_tabs.indexOf(self.output_edit)
        if index < 0:
            return
        label = "命令输出" + ("　●" if unread else "")
        if self.right_tabs.tabText(index) != label:
            self.right_tabs.setTabText(index, label)
        if unread:
            self.right_tabs.setTabToolTip(index, "输出区有新内容")
        else:
            self.right_tabs.setTabToolTip(index, "")

    def _flush_history(self) -> None:
        if self._pending_history is None:
            return
        cmd, ctype = self._pending_history
        self._pending_history = None
        self.history_panel.add(cmd, ctype)
        self._refresh_recent()

    def _on_preset_activated(self, command: str) -> None:
        self.output_edit.setPlainText(command)
        self.statusBar().showMessage("已从历史 / 预设 / 片段填入输出区。", 2000)

    def _on_sync_nbt_to_output(self) -> None:
        self.output_edit.setPlainText(self.nbt_editor.to_snbt(pretty=False))
        self.statusBar().showMessage("NBT 已同步到输出区。", 2000)

    def _on_sync_target_to_output(self) -> None:
        self.output_edit.setPlainText(self.target_editor.to_string())
        self.statusBar().showMessage("目标选择器已同步到输出区。", 2000)

    def _on_generate(self) -> None:
        cur = self.panel_stack.currentWidget()
        if not hasattr(cur, "current_command"):
            self._status(
                "当前页签不支持「重新生成」，请在命令面板"
                "（summon/give/execute/其他）里使用。", 4000)
            return
        self._show_command_tabs()
        if self.center_tabs.currentWidget() is not self.panel_stack:
            self.center_tabs.setCurrentWidget(self.panel_stack)
        command = cur.current_command()
        self.output_edit.setPlainText(command)
        self._mark_onboarding("generate")
        self._set_output_unread(True)
        self._update_guidance()
        self._show_toast("✔ 已生成命令，可以点「复制命令」或「→ 存档」")
        self._status("已重新生成命令。", 2000)

    def _on_copy_output(self) -> None:
        text = self.output_edit.toPlainText().strip()
        if not text:
            self._status("输出区是空的：先在命令面板填参数，命令会自动生成。", 4000)
            self.guide_strip.set_hint(
                "还没有可复制的命令。先在左侧选一条命令（比如 /give）并填好参数。",
                [("打开 /give", lambda: self._goto_command("give")),
                 ("搜索命令 (Ctrl+K)", self.open_palette)])
            return
        QApplication.clipboard().setText(text)
        self.history_panel.add(text, self._current_command_type)
        self._refresh_recent()
        self._mark_onboarding("copy")
        self._set_output_unread(False)
        self._update_guidance()
        self._show_toast("✔ 已复制到剪贴板 —— 到游戏里按 T 粘贴即可")
        self._status("已复制到剪贴板。", 3000)

    # ---- 轻量浮层提示（比状态栏更难错过）----
    def _show_toast(self, text: str, timeout: int = 2600) -> None:
        if not hasattr(self, "guide_strip"):            # pragma: no cover
            return
        label = getattr(self, "_toast", None)
        if label is None:
            label = QLabel(self)
            label.setObjectName("Toast")
            label.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            label.hide()
            self._toast = label
            self._toast_timer = QTimer(self)
            self._toast_timer.setSingleShot(True)
            self._toast_timer.timeout.connect(label.hide)
        label.setText(text)
        label.adjustSize()
        self._place_toast()
        label.show()
        label.raise_()
        self._toast_timer.start(timeout)

    def _place_toast(self) -> None:
        label = getattr(self, "_toast", None)
        if label is None:                               # pragma: no cover
            return
        bar = self.statusBar()
        top = bar.mapTo(self, bar.rect().topLeft()).y() if bar is not None \
            else self.height()
        label.move(max(12, (self.width() - label.width()) // 2),
                   max(12, top - label.height() - 12))

    def _on_favorite(self) -> None:
        command = self.output_edit.toPlainText().strip()
        if not command:
            QMessageBox.information(self, "提示", "输出区为空，无法收藏。")
            return
        store = PresetStore()
        dlg = SavePresetDialog(command, self._current_command_type,
                               store.categories(), self)
        if dlg.exec():
            name, category, cmd, ctype = dlg.value()
            store.add(name, category, cmd, ctype)
            self.preset_panel.reload()
            self.statusBar().showMessage(f"已收藏：{name}", 2000)

    # ---- 布局 / 输出折叠 / 主题 ----
    # 用「比例」而不是固定像素，窗口很小时也不会把某一栏挤成 0
    DEFAULT_RATIOS = {"left": 0.17, "right": 0.29, "bottom": 150}

    def _reset_layout(self) -> None:
        try:
            self._draft_store.save("layout", {"default": True})
        except Exception:
            pass
        self.left_width = 0
        self.right_width = 0
        self._bottom_height = self.DEFAULT_RATIOS["bottom"]
        if hasattr(self, "main_splitter") and hasattr(self, "bottom_tabs"):
            self.bottom_tabs.setMaximumHeight(self._bottom_height)
            self.bottom_tabs.setVisible(True)
            if self.isVisible():
                self._apply_proportional_layout()
            else:
                self._layout_pending = True
            self._update_collapse_buttons()
        self._status("已恢复默认布局。", 2500)

    def _apply_proportional_layout(self) -> None:
        """按默认比例分配三栏宽度（受各栏最小宽度约束）。"""
        total = max(600, self.main_splitter.width())
        left = max(190, int(total * self.DEFAULT_RATIOS["left"]))
        right = max(240, int(total * self.DEFAULT_RATIOS["right"]))
        center = max(320, total - left - right)
        self.main_splitter.setSizes([left, center, right])

    def _update_collapse_buttons(self) -> None:
        sizes = self.main_splitter.sizes()
        if len(sizes) >= 3:
            self.btn_collapse_tree.setText("▶" if sizes[0] <= 20 else "◀")
            self.btn_collapse_tree.setToolTip(
                "展开命令列表" if sizes[0] <= 20 else "折叠命令列表")
            self.btn_collapse_output.setText("◀" if sizes[2] <= 20 else "▶")
            self.btn_collapse_output.setToolTip(
                "展开输出区（Ctrl+Shift+O）" if sizes[2] <= 20
                else "折叠输出区（Ctrl+Shift+O）")

    def _apply_layout(self, key: str) -> None:
        if not hasattr(self, "main_splitter"):
            return
        total = max(600, self.main_splitter.width())
        if key == "focus":
            self.main_splitter.setSizes([0, total, 0])
            if self._bottom_height > 0:
                self._toggle_bottom_area()
        elif key == "output":
            right = max(240, int(total * self.DEFAULT_RATIOS["right"]))
            self.main_splitter.setSizes([0, max(320, total - right), right])
            if self._bottom_height <= 0:
                self._toggle_bottom_area()
        else:
            self.left_width = 0
            self.right_width = 0
            self._apply_proportional_layout()
            if self._bottom_height <= 0:
                self._toggle_bottom_area()
        self._update_collapse_buttons()
        self._save_layout_sizes()

    def _toggle_output_area(self) -> None:
        if not hasattr(self, "main_splitter"):
            return
        sizes = self.main_splitter.sizes()
        if len(sizes) < 3:
            return
        if sizes[2] > 20:
            self.right_width = sizes[2]
            self.main_splitter.setSizes([sizes[0], sizes[1] + sizes[2], 0])
        else:
            total = max(600, self.main_splitter.width())
            restore = self.right_width or max(
                240, int(total * self.DEFAULT_RATIOS["right"]))
            self.main_splitter.setSizes(
                [sizes[0], max(320, sizes[1] - restore), restore])
        self._update_collapse_buttons()
        self._save_layout_sizes()

    def _set_theme(self, key: str) -> None:
        apply_theme(QApplication.instance(), key)
        try:
            self._draft_store.save("theme", {"name": key})
        except Exception:
            pass

    # ---- 校验 ----
    def _switch_panel_index(self, index: int) -> None:
        if not (0 <= index < self.panel_stack.count()):
            return
        self.panel_stack.setCurrentIndex(index)
        # 同步命令树选中项与"当前命令类型"，避免快捷键切换后状态分裂
        widget = self.panel_stack.widget(index)
        for name, panel in self._panels.items():
            if panel is widget:
                self._select_command_in_tree(name)
                break

    def _on_validate_current(self) -> None:
        cur = self.panel_stack.currentWidget()
        builder = getattr(cur, "builder", None)
        self.validation_tree.clear()
        if builder is None:
            item = QTreeWidgetItem(["info", "当前面板不支持校验。"])
            self.validation_tree.addTopLevelItem(item)
            self.bottom_tabs.setCurrentWidget(self.validation_tree)
            return
        issues = builder.validate(BuildContext())
        if not issues:
            item = QTreeWidgetItem(["ok", "校验通过。"])
            self.validation_tree.addTopLevelItem(item)
        else:
            counts: dict[str, int] = {}
            for issue in issues:
                level = getattr(issue, "level", "error")
                counts[level] = counts.get(level, 0) + 1
                item = QTreeWidgetItem([level, str(issue)])
                self.validation_tree.addTopLevelItem(item)
            if counts:
                summary = "、".join(
                    f"{level} {count}" for level, count in
                    sorted(counts.items(),
                           key=lambda kv: ("error", "warn", "info").index(
                               kv[0]) if kv[0] in ("error", "warn", "info") else 9))
                self.statusBar().showMessage(f"校验结果：{summary}", 4000)
        self.bottom_tabs.setCurrentWidget(self.validation_tree)

    def _on_validation_double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        if item is None:
            return
        message = item.text(1)
        QApplication.clipboard().setText(message)
        self.statusBar().showMessage(f"已复制校验信息：{message}", 4000)

    # ---- 反向解析 ----
    def _on_output_context_menu(self, pos) -> None:
        menu = self.output_edit.createStandardContextMenu()
        menu.addSeparator()
        act_parse = menu.addAction("解析到当前面板")
        act_parse.setEnabled(self._can_parse_output())
        act_parse_all = menu.addAction("解析并刷新（即使当前面板不匹配）")
        act_parse_all.setEnabled(bool(self.output_edit.toPlainText().strip()))
        chosen = menu.exec(self.output_edit.viewport().mapToGlobal(pos))
        if chosen == act_parse:
            self._on_parse_output(force=False)
        elif chosen == act_parse_all:
            self._on_parse_output(force=True)

    def _can_parse_output(self) -> bool:
        cmd = self.output_edit.toPlainText().strip()
        if not cmd:
            return False
        panel = self._panels.get(self._current_command_type)
        if panel is None:
            return False
        builder = getattr(panel, "builder", None)
        if builder is None:
            return False
        supports = getattr(builder, "supports_parse", None)
        if callable(supports):
            return bool(supports())
        return type(builder).load_from is not CommandBuilder.load_from

    def _on_parse_output(self, force: bool = False) -> None:
        cmd = self.output_edit.toPlainText().strip()
        if not cmd:
            self.statusBar().showMessage("输出区为空。", 2000)
            return

        first = cmd.lstrip("/").split(None, 1)[0] if cmd else ""
        if not first:
            QMessageBox.information(self, "提示", "请先在左侧选择一个命令。")
            return

        if not force and self._current_command_type \
                and first != self._current_command_type:
            QMessageBox.information(
                self, "提示",
                f"当前选中 /{self._current_command_type}，"
                f"而输出区是 /{first}。\n请先选择 /{first}，"
                "或使用「解析并刷新」强制推断。")
            return

        panel = self._panels.get(first)
        if panel is None:
            QMessageBox.information(self, "提示",
                f"未找到 /{first} 的面板。请先在左侧选择对应命令。")
            return

        builder = getattr(panel, "builder", None)
        if builder is None:
            QMessageBox.information(self, "提示", "当前面板不支持反向解析。")
            return

        try:
            builder.load_from(cmd)
        except NotImplementedError:
            QMessageBox.information(self, "提示", "当前命令暂不支持反向解析。")
            return
        except Exception as e:
            QMessageBox.warning(self, "解析失败", str(e))
            return

        if hasattr(panel, "reload_from_builder"):
            try:
                panel.reload_from_builder()
            except Exception as e:
                QMessageBox.warning(self, "回填失败", str(e))
                return

        self._select_command_in_tree(first)
        try:
            self.panel_stack.setCurrentWidget(panel)
        except Exception:
            pass

        self.statusBar().showMessage(f"已解析 /{first} 命令并回填到面板。", 3000)

    # ---- 剪贴板 ----
    def _on_clipboard_changed(self) -> None:
        """检测到剪贴板里是已知命令时给出回填提示（复制非命令文本时保持安静）。"""
        try:
            text = QApplication.clipboard().text().strip()
        except Exception:
            return
        if not text or "\n" in text:
            return
        cmd = text[1:] if text.startswith("/") else text
        if not cmd:
            return
        first = cmd.split(None, 1)[0]
        if not first or first not in all_builders():
            return
        if self._last_clip_cmd == text:
            return
        self._last_clip_cmd = text
        self.statusBar().showMessage(
            f"检测到 /{first} 命令，右键输出区「解析到当前面板」可回填。", 6000)

    # ---- 数据 ----
    def _on_import_data(self) -> None:
        dlg = DataImportDialog(self)
        if dlg.exec():
            data_store().reload()
            self._refresh_all_pickers()
            backup = getattr(dlg, "imported_backup", None)
            extra = f"\n\n原文件已备份为：{backup.name}" if backup else ""
            QMessageBox.information(
                self, "完成", "用户数据已导入，选择器已刷新。" + extra)

    def _on_check_update(self) -> None:
        if self._updater is not None and self._updater.isRunning():
            QMessageBox.information(self, "提示", "更新正在进行中…")
            return

        progress = QProgressDialog("准备检查更新…", None, 0, 0, self)
        progress.setWindowTitle("检查更新")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)

        self._updater = UpdaterThread(self)
        self._updater.progress.connect(progress.setLabelText)

        def on_ok(count: int, msg: str) -> None:
            progress.close()
            data_store().reload()
            self._refresh_all_pickers()
            QMessageBox.information(self, "更新完成", msg)

        def on_fail(msg: str) -> None:
            progress.close()
            QMessageBox.warning(self, "更新失败", msg)

        self._updater.finished_ok.connect(on_ok)
        self._updater.failed.connect(on_fail)
        self._updater.finished.connect(lambda: setattr(self, "_updater", None))
        self._updater.start()

    def _on_reload_data(self) -> None:
        data_store().reload()
        self._refresh_all_pickers()
        self.statusBar().showMessage("数据已重新加载。", 2000)

    def _refresh_all_pickers(self) -> None:
        try:
            recommender().reload()
        except Exception:
            pass
        try:
            from app.core.ids import clear_cache
            clear_cache()
        except Exception:
            pass
        try:
            from app.core.item_components import clear_cache as clear_components
            clear_components()
        except Exception:
            pass
        entities = data_store().entities()
        items = data_store().items()
        for panel_name, setter in (("summon", entities), ("give", items)):
            p = self._panels.get(panel_name)
            if p is None:
                continue
            reloader = getattr(p, "reload_data", None)
            if callable(reloader):
                try:
                    reloader()
                    continue
                except Exception:
                    pass
            picker = getattr(
                p,
                "entity_picker" if panel_name == "summon" else "item_picker",
                None)
            if picker is not None:
                try:
                    picker.set_entries(setter)
                except Exception:
                    pass
        ref = getattr(self, "reference_panel", None)
        if ref is not None:
            try:
                ref.reload()
            except Exception:
                pass

    # ---- NBT 文件 ----
    def _on_open_nbt_file(self) -> None:
        self.center_tabs.setCurrentWidget(self.nbt_file_editor)
        self.nbt_file_editor.open_dialog()

    def _on_save_nbt_file(self) -> None:
        self.center_tabs.setCurrentWidget(self.nbt_file_editor)
        self.nbt_file_editor.save_as()

    def _on_save_nbt_file_direct(self) -> None:
        self.center_tabs.setCurrentWidget(self.nbt_file_editor)
        self.nbt_file_editor.save()

    def _on_new_nbt_file(self) -> None:
        self.center_tabs.setCurrentWidget(self.nbt_file_editor)
        self.nbt_file_editor.new_document()

    def _on_nbt_file_changed(self, title: str) -> None:
        index = self.center_tabs.indexOf(self.nbt_file_editor)
        if index >= 0:
            self.center_tabs.setTabText(index, f"NBT 编辑器 · {title}")
        self._current_nbt_path = self.nbt_file_editor.doc.path
        self._mark_onboarding("open")
        self._update_window_title()
        self._update_guidance()

    def _update_window_title(self) -> None:
        dirty = self.nbt_file_editor.doc.dirty
        path = self.nbt_file_editor.doc.path or self._current_nbt_path
        suffix = ""
        panel = getattr(self, "world_panel", None)
        world = panel.session.world if panel is not None \
            and panel.session is not None else None
        if world is not None and world.is_open():
            mark = "（有未应用的修改）" if world.dirty else ""
            suffix = f"{world.name}{mark}"
        elif path:
            suffix = f"{Path(path).name}{'（未保存）' if dirty else ''}"
        self.setWindowTitle(branding.window_title(suffix))

    def _on_datapack_nbt_loaded(self, root) -> None:
        self.nbt_file_editor.set_root(root)
        self.center_tabs.setCurrentWidget(self.nbt_file_editor)
        self._status("已从数据包载入 NBT。", 3000)

    # ---- NBT 编辑器：定位 / 筛选 / 查找 ----

    def _nbt_tree_editor(self):
        return self.nbt_file_editor.editor

    def _on_nbt_goto_path(self) -> None:
        """用 `Data.Player.Inventory[0].id` 这样的路径直接定位标签。"""
        editor = self._nbt_tree_editor()
        self.center_tabs.setCurrentWidget(self.nbt_file_editor)
        current = editor.current_path()
        path, ok = QInputDialog.getText(
            self, "定位到路径",
            "NBT 路径（列表元素用 [0] 表示）：", text=current)
        if not ok:
            return
        path = path.strip()
        if not path:
            return
        if editor.select_by_path(path):
            self._status(f"已定位到 {path}", 3000)
        else:
            self._status(f"找不到路径：{path}", 5000)

    def _on_nbt_filter(self) -> None:
        self.center_tabs.setCurrentWidget(self.nbt_file_editor)
        self._nbt_tree_editor().focus_filter()

    def _on_nbt_find(self) -> None:
        self.center_tabs.setCurrentWidget(self.nbt_file_editor)
        self._nbt_tree_editor().show_find_bar()

    def _on_nbt_expand_all(self) -> None:
        self._nbt_tree_editor().expand_all()

    def _on_nbt_collapse_all(self) -> None:
        self._nbt_tree_editor().collapse_all()

    def _on_nbt_copy_snbt(self) -> None:
        editor = self._nbt_tree_editor()
        text = editor.copy_selected_snbt()
        self._status("已复制 SNBT 到剪贴板。" if text else "请先在树里选中一个标签。",
                     3000)

    def _on_nbt_copy_command(self) -> None:
        editor = self._nbt_tree_editor()
        if editor.selected_node() is None:
            self._status("请先在树里选中一个标签。", 3000)
            return
        editor.copy_as_command()
        self._status("已复制 /data 命令到剪贴板。", 3000)

    # ---- NBT → 命令面板 / 设置 ----

    def _on_open_settings(self) -> None:
        """打开设置对话框（专有名词翻译、NBT 显示）。"""
        from app.ui.dialogs import SettingsDialog
        dialog = SettingsDialog(self)
        dialog.exec()
        # 数据重载后缓存要跟着失效
        try:
            from app.core.nbt_bridge import clear_registry_cache
            clear_registry_cache()
        except Exception:                                   # pragma: no cover
            pass

    def _on_open_world(self) -> None:
        """文件 → 打开世界存档。"""
        if self.workspace == "command":
            self.set_workspace("dual")
        elif self.workspace == "editor" \
                and self.center_stack.indexOf(self.world_panel) >= 0:
            self.center_stack.setCurrentWidget(self.world_panel)
        self.world_panel.open_dialog()

    # ---- 异步打开大存档 ----

    def _ensure_world_loader(self) -> WorldLoader:
        loader = getattr(self, "_world_loader", None)
        if loader is None:
            loader = WorldLoader(self)
            loader.progress.connect(self._on_world_load_progress)
            loader.loaded.connect(self._on_world_loaded)
            loader.failed.connect(self._on_world_load_failed)
            self._world_loader = loader
        return loader

    def open_world_async(self, path: str | Path) -> None:
        """后台线程打开世界，界面显示进度并可取消。"""
        loader = self._ensure_world_loader()
        self._world_progress = QProgressDialog(
            "正在读取世界存档…", "取消", 0, 0, self)
        self._world_progress.setWindowTitle("打开世界存档")
        self._world_progress.setWindowModality(
            Qt.WindowModality.WindowModal)
        self._world_progress.setMinimumDuration(0)
        self._world_progress.canceled.connect(loader.cancel)
        self._world_progress.show()
        loader.load(path)

    def _on_world_load_progress(self, done: int, total: int,
                                text: str) -> None:
        dialog = getattr(self, "_world_progress", None)
        if dialog is None or dialog.wasCanceled():
            return
        if total > 0:
            dialog.setRange(0, total)
            dialog.setValue(min(done, total))
        dialog.setLabelText(text or "正在读取世界存档…")

    def _on_world_loaded(self, world) -> None:
        dialog = getattr(self, "_world_progress", None)
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
            self._world_progress = None
        if world is None:                               # pragma: no cover
            return
        self.world_panel._adopt_world(world)
        self.world_panel.tabs.setCurrentIndex(0)
        if self.workspace == "command":
            self.set_workspace("dual")
        elif self.workspace == "editor" \
                and self.center_stack.indexOf(self.world_panel) >= 0:
            self.center_stack.setCurrentWidget(self.world_panel)
        self._update_window_title()
        self._mark_onboarding("open")
        self._show_toast(
            f"✔ 已打开存档「{getattr(world, 'name', '')}」——默认只读，"
            "解锁后才能写入")
        self._update_guidance()

    def _on_world_load_failed(self, message: str) -> None:
        dialog = getattr(self, "_world_progress", None)
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
            self._world_progress = None
        QMessageBox.warning(self, "打开世界失败", message)

    def _on_check_world(self) -> None:
        panel = getattr(self, "world_panel", None)
        if panel is None or panel.session is None:      # pragma: no cover
            QMessageBox.information(self, "提示", "请先打开一个世界存档。")
            return
        if self.workspace == "command":
            self.set_workspace("dual")
        panel.run_health_check()
        panel.preview_diff()

    def _on_nbt_item_overview(self) -> None:
        self.center_tabs.setCurrentWidget(self.nbt_file_editor)
        self.nbt_file_editor.show_item_overview()

    def _on_nbt_structure(self) -> None:
        self.center_tabs.setCurrentWidget(self.nbt_file_editor)
        self.nbt_file_editor.show_structure()

    def _on_nbt_commands(self, text: str) -> None:
        """物品一览的「全部 → 输出」。"""
        lines = [line for line in str(text).splitlines() if line.strip()]
        if not lines:
            return
        self.output_edit.setPlainText("\n".join(lines))
        self._current_command_type = "give"
        self._status(f"已把 {len(lines)} 条 /give 命令送到输出区。", 4000)

    def _on_nbt_give_request(self, stack) -> None:
        """存档里的物品 → `/give` 面板。"""
        panel = self._panels.get("give")
        if panel is None:                                   # pragma: no cover
            return
        panel.receive_item(stack)
        self.panel_stack.setCurrentWidget(panel)
        self.center_tabs.setCurrentWidget(self.panel_stack)
        try:
            display = stack.display()
        except Exception:                                   # pragma: no cover
            display = "物品"
        self._status(f"已把「{display}」载入 /give 面板，可继续调整后复制。",
                     5000)

    def receive_entity_to_summon(self, entity, pos: str = "") -> bool:
        """存档里的实体 → `/summon` 面板（供 NBT 编辑器调用）。"""
        panel = self._panels.get("summon")
        if panel is None or not hasattr(panel, "receive_entity"):
            return False
        if not panel.receive_entity(entity, pos):
            return False
        self.panel_stack.setCurrentWidget(panel)
        self.center_tabs.setCurrentWidget(self.panel_stack)
        self._status("已把实体数据载入 /summon 面板。", 5000)
        return True

    def _on_nbt_summon_request(self, entity) -> None:
        self.receive_entity_to_summon(entity)

    # ---- 场景 ----
    def _on_save_scene(self) -> None:
        name, ok = QInputDialog.getText(self, "保存场景", "场景名：")
        if not ok or not name.strip():
            return
        payload = {
            "output": self.output_edit.toPlainText(),
            "command_type": self._current_command_type,
            "center_tab": self.center_tabs.currentIndex(),
        }
        try:
            save_scene(name.strip(), payload)
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return
        self.statusBar().showMessage(f"场景已保存：{name}", 3000)

    def _on_load_scene(self) -> None:
        names = list_scenes()
        if not names:
            QMessageBox.information(self, "提示", "还没有保存的场景。")
            return
        name, ok = QInputDialog.getItem(self, "加载场景", "选择：", names, 0, False)
        if not ok:
            return
        data = load_scene(name)
        if not data:
            return
        self.output_edit.setPlainText(data.get("output", ""))
        ct = int(data.get("center_tab", 0))
        if 0 <= ct < self.center_tabs.count():
            self.center_tabs.setCurrentIndex(ct)
        self.statusBar().showMessage(f"已加载场景：{name}", 3000)

    def _on_show_execute_graph(self) -> None:
        panel = self._panels.get("execute")
        if panel is None:
            QMessageBox.information(self, "提示", "请先选择 /execute。")
            return
        dlg = QDialog(self)
        # WA_DeleteOnClose 会让 Qt 在关闭时销毁 C++ 对象；此处**不能**再
        # 手动 deleteLater()，否则 PySide 包装对象已失效，
        # 会抛 "Internal C++ object already deleted"
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dlg.setWindowTitle("Execute 节点图")
        dlg.resize(900, 400)
        layout = QVBoxLayout(dlg)
        view = ExecuteGraphView(dlg)
        view.set_nodes(panel.builder.nodes)
        layout.addWidget(view)
        dlg.exec()

    # ---- 草稿 ----
    def _panel_state(self) -> dict:
        """核心面板的编辑状态（关闭后可原样恢复）。"""
        out: dict[str, dict] = {}
        for name in ("summon", "give", "execute"):
            panel = self._panels.get(name)
            dumper = getattr(panel, "dump_state", None)
            if panel is None or not callable(dumper):
                continue
            try:
                data = dumper()
            except Exception:
                continue
            if isinstance(data, dict) and data:
                out[name] = data
        return out

    def _apply_panel_state(self, states: dict) -> None:
        if not isinstance(states, dict):
            return
        for name, data in states.items():
            panel = self._panels.get(name)
            restorer = getattr(panel, "restore_state", None)
            if panel is None or not callable(restorer):
                continue
            try:
                restorer(data)
            except Exception:
                continue

    def _center_tab_id(self, widget=None) -> str:
        """中间栏页签的**稳定 id**（标题会变：NBT 页签会带上文件名）。"""
        widget = widget if widget is not None \
            else self.center_tabs.currentWidget()
        for index in range(self.center_tabs.count()):
            if self.center_tabs.widget(index) is widget:
                return self._CENTER_TAB_IDS.get(
                    self.center_tabs.widget(index), "")
        return ""                                        # pragma: no cover

    def _center_tab_by_id(self, tab_id: str) -> int:
        if not tab_id:
            return -1
        for index in range(self.center_tabs.count()):
            if self._CENTER_TAB_IDS.get(self.center_tabs.widget(index)) \
                    == tab_id:
                return index
        return -1

    def _save_session_draft(self) -> None:
        try:
            payload = {
                "output": self.output_edit.toPlainText(),
                "current_command_type": self._current_command_type,
                "center_tab": self.center_tabs.currentIndex(),
                # 稳定 id 优先（标题会被 NBT 文件名改写），标题只作后备
                "center_tab_id": self._center_tab_id(),
                "center_tab_name": self.center_tabs.tabText(
                    self.center_tabs.currentIndex()).split(" · ")[0],
                "bottom_tab": self.bottom_tabs.currentIndex(),
                "workspace": self.workspace,
                "onboarding": sorted(self._onboarding),
                "panels": self._panel_state()}
            panel = getattr(self, "world_panel", None)
            if panel is not None and panel.session is not None \
                    and panel.session.path is not None:
                payload["world"] = str(panel.session.path)
            self._draft_store.save(DraftStore.SESSION_KEY, payload)
        except Exception:
            pass

    def _restore_session_draft(self) -> None:
        data = self._draft_store.load(DraftStore.SESSION_KEY)
        if not data:
            self._load_last_world()
            self._restore_workspace()
            self._maybe_start_with_home()
            return
        try:
            output = str(data.get("output", ""))
            if output.strip():
                self.output_edit.setPlainText(output)
                self._onboarding.add("generate")
            cmd_type = str(data.get("current_command_type", ""))
            if cmd_type:
                self._select_command_in_tree(cmd_type)
            # 1) 稳定 id；2) 页签标题；3) 老草稿的下标（2.0 的 0 = 命令面板，
            #    现在的 0 是首页，所以要 +1 才不会漂到首页）
            index = self._center_tab_by_id(str(data.get("center_tab_id", "")))
            if index < 0:
                name = str(data.get("center_tab_name", "") or "")
                if name:
                    for i in range(self.center_tabs.count()):
                        if self.center_tabs.tabText(i).split(" · ")[0] == name:
                            index = i
                            break
            if index < 0 and "center_tab_id" not in data \
                    and "center_tab_name" not in data:
                legacy = int(data.get("center_tab", -1))
                if 0 <= legacy < self.center_tabs.count() - 1:
                    index = legacy + 1
            if index < 0:
                index = int(data.get("center_tab", -1))
            if 0 <= index < self.center_tabs.count():
                self.center_tabs.setCurrentIndex(index)
            bt = int(data.get("bottom_tab", -1))
            if 0 <= bt < self.bottom_tabs.count():
                self.bottom_tabs.setCurrentIndex(bt)
            self._apply_panel_state(data.get("panels") or {})
            saved_done = data.get("onboarding")
            if isinstance(saved_done, list):
                self._onboarding |= {str(k) for k in saved_done}
            self.statusBar().showMessage("已恢复上次会话草稿。", 4000)
        except Exception:
            pass
        self._restore_workspace(data)
        self._load_last_world()
        self._maybe_start_with_home()

    def _maybe_start_with_home(self) -> None:
        """启动时按偏好落在首页（默认开，可在向导里关掉）。"""
        if not self._start_on_home_pref():
            return
        if self.workspace == "editor":
            # 首页在中间栏里，而「世界编辑」模式会隐藏中间栏以外的内容、
            # 并把 center_stack 指向世界面板 —— 先切到双栏才看得见首页
            self.set_workspace("dual")
        if self.center_stack.indexOf(self.center_tabs) >= 0:
            self.center_stack.setCurrentWidget(self.center_tabs)
        self.center_tabs.setCurrentWidget(self.home_page)
        self._refresh_home()
        self._update_guidance()

    def _restore_workspace(self, data: dict | None = None) -> None:
        mode = ""
        if isinstance(data, dict):
            mode = str(data.get("workspace", "") or "")
        if not mode:
            try:
                saved = self._draft_store.load("workspace") or {}
                mode = str(saved.get("mode", "") or "")
            except Exception:                           # pragma: no cover
                mode = ""
        if mode in dict(WORKSPACES):
            self.set_workspace(mode)
        else:
            self.set_workspace("dual")

    def _on_migrate_command(self) -> None:
        initial = self.output_edit.toPlainText().strip()
        dlg = MigrateDialog(initial, self)
        if dlg.exec():
            new_cmd = dlg.migrated_command()
            if new_cmd:
                before = self.output_edit.toPlainText()
                self.output_edit.setPlainText(new_cmd)
                self.statusBar().showMessage("迁移完成，已写入输出区。", 3000)
                # 变化明显时给出差异对照，方便确认迁移结果
                if before.strip() and before.strip() != new_cmd.strip():
                    self._show_diff(before, new_cmd)

    def _show_diff(self, old: str, new: str) -> None:
        dlg = DiffDialog(old, new, self)
        dlg.exec()
        dlg.deleteLater()

    def _on_about(self) -> None:
        QMessageBox.about(self, f"关于 {branding.APP_NAME}",
                          branding.about_text())

    # ---- 关闭 ----
    def closeEvent(self, event) -> None:
        editor = getattr(self, "nbt_file_editor", None)
        if editor is not None and editor.doc.dirty:
            answer = QMessageBox.question(
                self, "NBT 尚未保存",
                f"{editor.doc.name} 有未保存的修改，是否保存后退出？",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save)
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Save and not editor.save():
                event.ignore()
                return
        if self._pending_history is not None:
            self._history_timer.stop()
            self._flush_history()
        try:
            self._save_layout_sizes()
        except Exception:
            pass
        try:
            self._save_session_draft()
        except Exception:
            pass
        self._draft_timer.stop()
        # 后台还在读存档时直接退出会让 QThreadPool 在任务运行中被销毁
        # （Qt 会警告甚至崩溃）——先取消再等一小会儿
        for loader in (getattr(self, "_world_loader", None),
                       getattr(getattr(self, "world_panel", None),
                               "_source_loader", None)):
            if loader is None:
                continue
            try:
                loader.cancel()
                loader.wait(3000)
            except Exception:                           # pragma: no cover
                pass
        try:
            Database.shutdown()
        except Exception:
            pass
        super().closeEvent(event)