"""世界 / NBT 编辑器面板（主界面右侧工作区）。

页签组织
--------
    世界总览      只读摘要 + 可写设置（时间 / 天气 / 难度 / 出生点 / 边界 /
                  允许命令 / 存档名）+ 游戏规则
    玩家          playerdata 列表 + 字段编辑 + 背包卡片
    实体          实体列表（读 / 复制 / /summon / 送联动总线）
    容器          箱子 / 桶 / 潜影盒 / 熔炉等
    NBT 树        完整 NBT 树只读视图 + 路径定位 / 复制 / 送总线
    地图          data/map_*.dat 的比例 / 锁定 / 追踪 / 中心 / 颜色
    区域          region/*.mca 只读区块索引（懒加载）
    数据包        datapacks 内容 + 导出函数
    蓝图 / 配方    结构模板导出 + 物品组件配方工坊
    中文世界编辑   自然语言 → 操作 → 差异 → 应用
    差异          事务差异 + 备份列表 + 回滚
    跨存档        从另一个存档搬运玩家数据 / 进度 / 地图
    健康检查      损坏区块 / 缺失数据包 / 版本 / UUID 冲突

排版
----
所有间距 / 边距 / 字号 / 语义色都来自 `app.ui.theme` 的 token，
再用 `app.ui.world_common` 的 `SectionBox + FilterBar + make_table` 组装，
所以同一页里疏密一致、浅色主题下灰字可读、每个表格都能 `Ctrl+F` 过滤。

只读门禁
--------
打开存档默认只读：除「解锁编辑」外所有写入入口禁用；解锁本身要二次确认。
所有写入都通过 `WorldSession` 事务：备份 → 内存模型 → 校验 → dry-run 差异
→ 确认 → 原子写入 → 可回滚。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QMenu,
    QMessageBox, QProgressBar, QPushButton, QStackedWidget, QTabWidget,
    QToolButton, QVBoxLayout, QWidget,
)

from app.core import world as world_mod
from app.core.world_session import WorldSession
from app.storage.world_cache import WorldIndexCache
from app.ui.panels import EmptyState
from app.ui.theme import (
    MARGINS, SPACING, hint_style, muted_style,
)
from app.ui.world_common import (
    FilterBar, SectionBox, StateBanner, export_table_csv, make_table,
)
from app.ui.world_dialogs import (  # noqa: F401  （对外再导出，便于测试与外部脚本）
    BlueprintExportDialog, CrossWorldCopyDialog, GrowItemsDialog,
    NlpWorldDialog, SceneDialog,
)
from app.ui.world_tabs_data import (
    BlueprintTabMixin, ContainerTabMixin, CrossWorldTabMixin,
    DiffTabMixin, EntityTabMixin, HealthTabMixin,
    MapTabMixin, NbtTabMixin, NlpTabMixin, RegionTabMixin,
)
from app.ui.world_creative import CreativeTabMixin
from app.ui.world_datapack import DatapackTabMixin as ProjectTabMixin
from app.ui.world_tabs_data import DatapackTabMixin as SavePackTabMixin
from app.ui.world_tabs_world import OverviewTabMixin, PlayerTabMixin

#: 编辑器内部页签顺序（README 与测试都依赖它）
EDITOR_TABS = (
    "世界总览", "玩家", "实体", "容器", "NBT 树", "地图", "区域",
    "数据包", "数据包工程", "蓝图 / 配方", "可视化编排", "中文世界编辑",
    "差异", "跨存档", "健康检查",
)


class WorldPanel(OverviewTabMixin, PlayerTabMixin, EntityTabMixin,
                  ContainerTabMixin, NbtTabMixin, MapTabMixin,
                  RegionTabMixin, SavePackTabMixin, ProjectTabMixin,
                  BlueprintTabMixin, CreativeTabMixin, NlpTabMixin,
                  DiffTabMixin, CrossWorldTabMixin, HealthTabMixin, QWidget):
    """世界与 NBT 编辑器面板。"""

    #: 请求把物品送进命令面板的 /give
    giveRequested = Signal(object)
    #: 请求把实体送进命令面板的 /summon
    summonRequested = Signal(object)
    #: 请求把一批命令送到输出区
    commandsReady = Signal(str)
    #: 请求把一段结构化载荷送到命令面板（联动总线）
    bridgeRequested = Signal(object)
    #: 状态栏文字
    statusMessage = Signal(str)
    #: 存档打开 / 关闭 / 内容变化（主窗口据此刷新标题与联动上下文）
    worldChanged = Signal(object)
    #: 请求在主窗口的后台线程里打开世界（大存档不卡界面）
    openRequested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.session: WorldSession | None = None
        self.cache = WorldIndexCache()
        #: True 时 open_path 只发 openRequested，由主窗口异步加载
        self.async_open = False
        self._current_player = ""
        self._current_container = ""
        self._blueprint = None
        #: 各页签构建时登记的「会改存档」按钮（只读门禁用）
        self._extra_write_widgets: list[QWidget] = []
        self._build_ui()

    # ==================================================================
    # 构建界面
    # ==================================================================
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*MARGINS["panel"])
        outer.setSpacing(SPACING["sm"])

        outer.addLayout(self._build_toolbar())
        self.banner = StateBanner()
        outer.addWidget(self.banner)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setMaximumHeight(12)
        self.progress.setTextVisible(False)
        outer.addWidget(self.progress)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideRight)
        for name, builder in (
            ("世界总览", self._build_overview_tab),
            ("玩家", self._build_player_tab),
            ("实体", self._build_entity_tab),
            ("容器", self._build_container_tab),
            ("NBT 树", self._build_nbt_tab),
            ("地图", self._build_map_tab),
            ("区域", self._build_region_tab),
            ("数据包", self._build_datapack_tab),
            ("数据包工程", self._build_datapack_project_tab),
            ("蓝图 / 配方", self._build_blueprint_tab),
            ("可视化编排", self._build_creative_tab),
            ("中文世界编辑", self._build_nlp_tab),
            ("差异", self._build_diff_tab),
            ("跨存档", self._build_crossworld_tab),
            ("健康检查", self._build_health_tab),
        ):
            self.tabs.addTab(builder(), name)
        # 未打开世界时先给个空态提示
        self.empty_hint = QLabel(
            "还没有打开世界。点左上角「打开世界存档…」选择 "
            ".minecraft/saves/世界名（存档默认只读，解锁后才能写入）。")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setStyleSheet(hint_style(self))

        # 没打开世界时，15 个页签切过去全是空表格 —— 用一个整块的引导页
        # 盖住内容区（页签本身还在，代理/测试仍可正常使用）
        self.tab_stack = QStackedWidget()
        self.tab_empty = EmptyState(
            "还没有打开世界存档\n\n"
            "选择 .minecraft/saves/世界名。存档默认只读，"
            "解锁后才能写入 —— 每次写入都会先备份到 backups/<时间戳>/，"
            "随时可以回滚。",
            [("打开世界存档…", self.open_dialog),
             ("看最近打开的世界", self._open_last_world)])
        self.tab_stack.addWidget(self.tab_empty)
        self.tab_stack.addWidget(self.tabs)
        self.tab_stack.setCurrentWidget(self.tab_empty)
        outer.addWidget(self.tab_stack, 1)
        outer.addWidget(self.empty_hint)

        # 页签建好后把名字填进「全部页签」下拉
        self.tab_jump.clear()
        self.tab_jump.addItem("全部页签 ▾", -1)
        for index in range(self.tabs.count()):
            self.tab_jump.addItem(self.tabs.tabText(index), index)
        self.tabs.currentChanged.connect(self._sync_tab_jump)

    def _on_tab_jump(self, position: int) -> None:
        index = self.tab_jump.itemData(position)
        if index is None or int(index) < 0:
            return
        self.tabs.setCurrentIndex(int(index))

    def _sync_tab_jump(self, index: int) -> None:
        """页签手动切换时，让下拉跟着回到「全部页签」占位项。"""
        if not hasattr(self, "tab_jump"):               # pragma: no cover
            return
        self.tab_jump.setCurrentIndex(0)
        self.tab_jump.setToolTip(
            f"当前页签：{self.tabs.tabText(index)}　—　在这里跳到任意页签")

    def _build_toolbar(self) -> QVBoxLayout:
        """两行工具条：上行「打开 / 状态」，下行「编辑动作」。"""
        wrapper = QVBoxLayout()
        wrapper.setSpacing(SPACING["xs"])

        # ---- 第一行：文件与状态 ----
        top = QHBoxLayout()
        top.setSpacing(SPACING["sm"])
        self.btn_open = QPushButton("打开世界存档…")
        self.btn_open.setToolTip(
            "打开 .minecraft/saves/世界名（也支持启动器版本隔离目录）")
        self.btn_open.clicked.connect(self.open_dialog)
        top.addWidget(self.btn_open)

        self.btn_recent = QToolButton()
        self.btn_recent.setText("最近打开")
        self.btn_recent.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_recent.setToolTip("从世界索引缓存里选（显示 DataVersion）")
        self._recent_menu = QMenu(self.btn_recent)
        self.btn_recent.setMenu(self._recent_menu)
        self._rebuild_recent_menu()
        top.addWidget(self.btn_recent)

        self.btn_reload = QPushButton("重新载入")
        self.btn_reload.setToolTip("丢掉内存里的修改，重新从磁盘读取")
        self.btn_reload.clicked.connect(self.reload)
        top.addWidget(self.btn_reload)

        self.btn_health = QPushButton("检查存档")
        self.btn_health.setToolTip(
            "世界健康检查：损坏区块 / 缺失数据包 / 版本 / UUID 冲突")
        self.btn_health.clicked.connect(self.run_health_check)
        top.addWidget(self.btn_health)

        top.addStretch(1)
        # 15 个页签在窄中栏里必然溢出（右侧的差异 / 跨存档 / 健康检查会被
        # 滚动箭头藏起来）—— 给一个「全部页签」下拉，键盘也能跳
        self.tab_jump = QComboBox()
        self.tab_jump.setToolTip("跳到任意编辑器页签（页签太多时可以在这里找）")
        self.tab_jump.setMinimumWidth(140)
        self.tab_jump.activated.connect(self._on_tab_jump)
        top.addWidget(self.tab_jump)
        self.btn_export_all = QToolButton()
        self.btn_export_all.setText("导出当前表")
        self.btn_export_all.setAutoRaise(True)
        self.btn_export_all.setToolTip("把当前页签的表格导出成 CSV")
        self.btn_export_all.clicked.connect(self._export_current_table)
        top.addWidget(self.btn_export_all)
        wrapper.addLayout(top)

        # ---- 第二行：编辑动作 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(SPACING["sm"])
        self.btn_lock = QPushButton("解锁编辑")
        self.btn_lock.setToolTip(
            "存档默认只读。解锁后才能写入 —— 每次写入都会先备份到 backups/")
        self.btn_lock.clicked.connect(self.toggle_lock)
        bottom.addWidget(self.btn_lock)

        self.btn_apply = QPushButton("应用修改")
        self.btn_apply.setToolTip("把内存里的修改事务化写盘（备份 + 原子替换）")
        self.btn_apply.clicked.connect(self.apply_changes)
        self.btn_apply.setEnabled(False)
        bottom.addWidget(self.btn_apply)

        self.btn_undo = QPushButton("撤销")
        self.btn_undo.setToolTip(
            "只回退内存里的改动；如果这一步已经写盘，磁盘不会变 —— "
            "想连磁盘一起回退用右边的「撤销并回滚磁盘」")
        self.btn_undo.clicked.connect(self.undo)
        bottom.addWidget(self.btn_undo)

        self.btn_undo_disk = QPushButton("撤销并回滚磁盘")
        self.btn_undo_disk.setToolTip(
            "用这次写入前的备份把磁盘也一起回退（会清空撤销栈）")
        self.btn_undo_disk.clicked.connect(self.undo_with_rollback)
        bottom.addWidget(self.btn_undo_disk)
        self.btn_redo = QPushButton("重做")
        self.btn_redo.clicked.connect(self.redo)
        bottom.addWidget(self.btn_redo)

        self.btn_diff = QPushButton("预览差异")
        self.btn_diff.setToolTip("先看差异再决定是否写盘")
        self.btn_diff.clicked.connect(self.preview_diff)
        bottom.addWidget(self.btn_diff)

        bottom.addStretch(1)
        self.state_label = QLabel("未打开世界")
        self.state_label.setStyleSheet(muted_style(self))
        bottom.addWidget(self.state_label)
        wrapper.addLayout(bottom)
        return wrapper

    # ==================================================================
    # 通用设施（各 mixin 依赖）
    # ==================================================================
    def world(self) -> world_mod.WorldSave | None:
        """当前打开的 WorldSave（没打开返回 None）。"""
        if self.session is None:
            return None
        return self.session.world

    def _refresh_status_context(self) -> None:
        """刷新自身状态（`_refresh_session_state` 的语义化别名）。

        各页签的「选中项变化」只关心刷新自己，不必知道主窗口状态栏的事。
        """
        self._refresh_session_state()

    def _tab_index(self, name: str) -> int:
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) == name:
                return index
        return 0                                              # pragma: no cover

    def _guard_write(self) -> bool:
        """写入前的统一门禁：世界打开 + 已解锁。"""
        if self.session is None or self.session.world is None:
            QMessageBox.information(self, "提示", "请先打开一个世界存档。")
            return False
        if self.session.read_only:
            QMessageBox.information(
                self, "只读模式",
                "存档处于只读模式，请先点「解锁编辑」。")
            return False
        return True

    def _run_bridge(self, operation) -> None:
        """统一走联动总线：preview → 确认 → apply（带备份 + 撤销）。"""
        if not self._guard_write():
            return
        from app.core import bridge as bridge_mod
        preview = bridge_mod.preview_operation(self.session, operation)
        if not preview.ok:
            QMessageBox.warning(self, "无法应用", preview.message)
            return
        if len(preview.diff) == 0:
            QMessageBox.information(self, "没有变化", "该操作不会改变任何数据。")
            return
        self.diff_view.setPlainText(preview.diff.text(limit=200))
        self.diff_summary.setText(f"{len(preview.diff)} 处差异")
        answer = QMessageBox.question(
            self, f"确认：{operation.title}",
            f"{operation.summary()}\n\n差异预览（{len(preview.diff)} 处）：\n"
            f"{preview.diff.text(limit=25)}\n\n"
            "确认后将自动备份并写入存档。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Ok:
            return
        result = bridge_mod.apply_operation(self.session, operation)
        if not result.ok:
            QMessageBox.warning(self, "写入失败", result.message)
            return
        self.diff_view.setPlainText(
            result.diff.text(limit=200) if result.diff else "（无差异）")
        self.refresh_all()
        # 写入结果必须看得见：旧版只把差异写进「差异」页签却不切过去
        index = self._tab_index("差异")
        if index >= 0:
            self.tabs.setCurrentIndex(index)
        summary = result.summary() if hasattr(result, "summary") else ""
        self.statusMessage.emit(summary or result.message)
        if result.backup is not None:
            self.statusMessage.emit(f"备份目录：backups/{result.backup.name}")

    def _targets(self) -> list[str]:
        world = self.world()
        if world is None:
            return []
        out = ["level"]
        out.extend(sorted(world.players))
        out.extend(f"map:{index}" for index in sorted(world.maps))
        return out

    def _document_roots(self) -> list[tuple[str, object]]:
        world = self.world()
        out: list[tuple[str, object]] = []
        if world is None or not world.is_open():
            return out
        if world.level_root is not None:
            out.append(("level", world.level_root))
        for uuid in sorted(world.players):
            root = world.player_root(uuid)
            if root is not None:
                out.append((uuid, root))
        return out

    def _export_table(self, table, name: str) -> None:
        """把某个表格导出成 CSV（只导出可见行）。"""
        if table is None or not table.rowCount():
            self.statusMessage.emit("这个表格还没有内容。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, f"导出{name}", f"{name}.csv", "CSV 文件 (*.csv);;所有文件 (*)")
        if not path:
            return
        try:
            count = export_table_csv(table, path)
        except OSError as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        self.statusMessage.emit(f"已导出 {count} 行到 {path}")

    def _export_current_table(self) -> None:
        """导出当前页签的主表格。"""
        name = self.tabs.tabText(self.tabs.currentIndex())
        candidate = {
            "世界总览": getattr(self, "overview_table", None),
            "玩家": getattr(self, "player_table", None),
            "实体": getattr(self, "entity_table", None),
            "容器": getattr(self, "container_table", None),
            "地图": getattr(self, "map_table", None),
            "区域": getattr(self, "region_table", None),
            "数据包": getattr(self, "datapack_table", None),
            "差异": getattr(self, "backup_table", None),
            "健康检查": getattr(self, "health_table", None),
        }.get(name)
        self._export_table(candidate, name)

    # ==================================================================
    # 打开 / 状态
    # ==================================================================
    def open_dialog(self) -> None:
        start = ""
        if self.session is not None and self.session.path is not None:
            start = str(self.session.path.parent)
        path = QFileDialog.getExistingDirectory(
            self, "选择世界存档文件夹（含 level.dat）", start)
        if path:
            self.open_path(path)

    def open_path(self, path: str | Path) -> bool:
        """打开世界。

        默认同步（小存档 / 测试）；主窗口会把 `async_open` 设为 True，
        这时只发信号，由主窗口在后台线程加载并显示进度。
        """
        if self.async_open:
            self.openRequested.emit(str(path))
            return True
        from app.core.world import open_world
        try:
            world = open_world(path)
        except Exception as e:                              # pragma: no cover
            QMessageBox.warning(self, "打开失败", str(e))
            return False
        self._adopt_world(world)
        return True

    def _adopt_world(self, world) -> None:
        self.session = WorldSession(path=world.path, world=world)
        self.session.subscribe(lambda _s: self._refresh_session_state())
        # 记下「磁盘上现在的样子」：之后「应用修改」靠它算出真实差异
        self.session.set_baseline()
        self._current_player = ""
        self._current_container = ""
        self._blueprint = None
        self.tabs.setCurrentIndex(0)
        self.refresh_all()
        try:
            self.cache.store(world)
            self._rebuild_recent_menu()
        except Exception:                                   # pragma: no cover
            pass
        self.statusMessage.emit(
            f"已打开世界「{world.name}」（{world.path}）"
            " —— 默认只读，需要编辑请点「解锁编辑」。")
        self.worldChanged.emit(world)
        for issue in world.errors:
            self.statusMessage.emit(f"读取问题：{issue}")

    def reload(self) -> None:
        if self.session is None or self.session.path is None:
            return
        if self.session.unsaved_changes():
            answer = QMessageBox.question(
                self, "有未应用的修改",
                "内存里有还没写盘的修改，重新载入会丢掉它们"
                "（磁盘上的文件不受影响）。\n\n仍要重新载入吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        path = self.session.path
        was_read_only = self.session.read_only
        self.session = None
        if self.async_open:
            self.openRequested.emit(str(path))
            return
        from app.core.world import open_world
        world = open_world(path, read_only=was_read_only)
        self._adopt_world(world)

    def toggle_lock(self) -> None:
        if self.session is None or self.session.world is None:
            QMessageBox.information(self, "提示", "请先打开一个世界存档。")
            return
        if self.session.read_only:
            world = self.session.world
            compatibility = world.overview.compatibility
            lines = [
                f"即将解锁「{world.name}」进行编辑。",
                "",
                f"目录：{world.path}",
                f"DataVersion：{world.overview.data_version}"
                f"（{world.overview.version_label}）",
                compatibility.message,
                "",
                "写入前会自动备份到 backups/<时间戳>/，所有改动都能撤销。",
                "仍然建议先关掉游戏，避免游戏把内存里的旧数据写回去。",
            ]
            answer = QMessageBox.warning(
                self, "确认解锁编辑", "\n".join(lines),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.session.unlock()
            self.statusMessage.emit("已解锁编辑：写入会先备份再原子替换。")
        else:
            self.session.lock()
            self.statusMessage.emit("已切回只读模式。")
        self._refresh_session_state()

    def apply_changes(self) -> None:
        """把内存模型里的改动事务化写盘（备份 → 校验 → 原子写入 → 撤销点）。"""
        if self.session is None or self.session.world is None:
            QMessageBox.information(self, "提示", "请先打开一个世界存档。")
            return
        if self.session.read_only:
            QMessageBox.information(self, "只读", "请先点「解锁编辑」。")
            return
        diff = self.session.pending_diff("待应用修改")
        if diff is None:
            # 没有写盘基准就分不清「改了什么」，绝不能顺手把当前状态当基准
            QMessageBox.information(
                self, "需要重新载入",
                "这个会话没有记录写盘基准，无法安全地计算差异。\n\n"
                "请先用「重新载入」重新打开存档（会丢掉未应用的修改），"
                "再修改并应用。")
            return
        if not diff:
            # 内存和磁盘一致（例如撤销回到了写盘时的状态）：顺手刷新基准，
            # 免得「应用修改」按钮一直亮着却什么也不做
            self.session.set_baseline()
            self._refresh_session_state()
            QMessageBox.information(self, "没有变化", "内存模型与磁盘一致。")
            return
        answer = QMessageBox.question(
            self, "确认写入存档",
            f"即将写入 {len(diff)} 处修改：\n\n{diff.text(limit=40)}\n\n"
            "写入前会自动备份到 backups/<时间戳>/，写入后可撤销。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Ok:
            return
        # 用「上次写盘时的基准快照」建事务：这样才能算对差异、
        # 也才会真的走备份 + 校验 + 原子写入 + 记撤销点。
        # 传去的是浅拷贝：事务回滚时 restore() 会克隆节点，不会污染基准。
        transaction = self.session.transaction(
            "应用修改", snapshot=dict(self.session.baseline or {}))
        result = transaction.commit()
        if not result.ok:
            backup = f"\n\n已备份到：backups/{result.backup.name}" \
                if result.backup is not None else ""
            QMessageBox.warning(self, "写入失败", result.message + backup)
            return
        if not result.applied:
            QMessageBox.information(self, "没有变化", result.message)
            return
        self.refresh_all()
        backup = f"\n\n备份目录：backups/{result.backup.name}" \
            if result.backup is not None else ""
        QMessageBox.information(
            self, "已写入",
            "已写入：" + "、".join(p.name for p in result.written)
            + backup
            + "\n\n可以用「撤销」回到上一步，或从「差异」页签回滚备份。")

    def undo(self) -> None:
        if self.session is None:
            return
        if self.session.undo():
            self.refresh_all()
            self.statusMessage.emit(
                "已撤销上一步（内存）。如果这一步之前已经写盘，"
                "请点「撤销并回滚磁盘」把文件也退回去。")
        else:
            self.statusMessage.emit("没有可撤销的操作。")

    def undo_with_rollback(self) -> None:
        """撤销并把磁盘一起回退（用提交时留下的备份）。"""
        if self.session is None:
            QMessageBox.information(self, "提示", "请先打开一个世界存档。")
            return
        backup = self.session.last_undo_backup()
        if backup is None:
            QMessageBox.information(
                self, "没有可回滚的备份",
                "最近这一步没有留下备份（可能还在内存里没写盘）。\n\n"
                "先点「撤销」退掉内存里的改动即可；"
                "已经写盘的历史改动可以在「差异」页签里选备份回滚。")
            return
        answer = QMessageBox.question(
            self, "撤销并回滚磁盘",
            f"将把内存与磁盘一起回退到：backups/{backup.name}\n\n"
            "回滚会用备份文件覆盖存档（回滚前会再备份一次当前文件），"
            "并清空撤销栈。确认继续？",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Ok:
            return
        ok, message = self.session.undo_with_rollback()
        self.refresh_all()
        self.statusMessage.emit(message)
        (QMessageBox.information if ok else QMessageBox.warning)(
            self, "撤销并回滚磁盘" if ok else "回滚失败", message)

    def redo(self) -> None:
        if self.session is None:
            return
        if self.session.redo():
            self.refresh_all()
            self.statusMessage.emit(
                "已重做（内存）。磁盘内容需要再点「应用修改」才会同步。")
        else:
            self.statusMessage.emit("没有可重做的操作。")

    # ==================================================================
    # 刷新
    # ==================================================================
    def refresh_all(self) -> None:
        self.refresh_overview()
        self.refresh_players()
        self.refresh_entities()
        self.refresh_containers()
        self.refresh_maps()
        self.refresh_regions()
        self.refresh_datapacks()
        self.refresh_blueprints()
        self.refresh_recipes()
        self.refresh_creative()
        self.refresh_backups()
        self.refresh_nbt_sources()
        self._refresh_session_state()

    def _refresh_session_state(self) -> None:
        writable = bool(self.session and not self.session.read_only)
        world = self.world()
        self.btn_lock.setText("切回只读" if writable else "解锁编辑")
        self._apply_write_gate(writable, has_world=world is not None)
        # 没打开世界时「全部页签」跳转没有意义（停在引导页），导出也只有空表
        if hasattr(self, "tab_jump"):
            self.tab_jump.setEnabled(world is not None)
        if hasattr(self, "btn_export_all"):
            self.btn_export_all.setEnabled(world is not None)
        if world is None:
            self.state_label.setText("未打开世界")
            self.banner.update_state()
            self.empty_hint.setVisible(True)
            if hasattr(self, "tab_stack"):
                self.tab_stack.setCurrentWidget(self.tab_empty)
            self.btn_apply.setEnabled(False)
            self.btn_undo.setEnabled(False)
            self.btn_undo_disk.setEnabled(False)
            self.btn_redo.setEnabled(False)
            return

        self.empty_hint.setVisible(False)
        if hasattr(self, "tab_stack"):
            self.tab_stack.setCurrentWidget(self.tabs)
        # 「有未应用的修改」不能只看 world.dirty：旧实现里 save() 会把 dirty
        # 清零，于是「撤销」之后磁盘还是改过的、界面却显示干净。
        dirty = bool(self.session is not None and self.session.unsaved_changes())
        self.btn_apply.setEnabled(writable and dirty)
        self.btn_undo.setEnabled(bool(self.session and self.session.can_undo()))
        self.btn_undo_disk.setEnabled(
            bool(self.session and self.session.last_undo_backup() is not None))
        self.btn_redo.setEnabled(bool(self.session and self.session.can_redo()))

        overview = world.overview
        state = "可编辑" if writable else "只读"
        self.state_label.setText(
            f"{world.name} · {state}{' · 有未应用的修改' if dirty else ''}")
        compatibility = overview.compatibility
        detail = (f"DataVersion {overview.data_version}"
                  f"（{overview.version_label}） · "
                  f"{len(world.players)} 名玩家 · "
                  f"{len(world.regions)} 个区域文件 · "
                  f"{len(world.maps)} 张地图")
        self.banner.update_state(
            name=world.name, detail=detail, readonly=not writable,
            dirty=dirty,
            level={"ok": "ok", "read_only": "warn", "unknown": "error"}.get(
                compatibility.level, "ok"))

    def _write_widgets(self) -> list[QWidget]:
        """只读模式下必须禁用的控件（写盘入口）。

        原则：**会改存档的**才禁用；只读也安全的（创建快照 / 导出蓝图 /
        导出 CSV）不禁用 —— 用户想「先备份再解锁」是完全合理的顺序。
        旧清单里那个 `"btn_map_toggle_lock" if False else ...` 是死代码，
        地图 / 区域 / 中文编辑这些真正会写的入口反而漏在门禁之外。
        """
        names = (
            "btn_set_time", "btn_set_weather", "btn_set_difficulty",
            "btn_set_spawn", "btn_set_border", "btn_set_commands",
            "btn_scene", "btn_preset", "btn_inv_grow", "btn_recipe_apply",
            "btn_restore", "btn_cross_apply", "btn_nlp_apply",
            # 设置类输入框：只读时整组一起禁用，别让用户改了却点不动「应用」
            "level_name_edit", "time_spin", "weather_combo",
            "weather_duration", "difficulty_combo", "difficulty_lock",
            "spawn_x", "spawn_y", "spawn_z", "border_size", "border_damage",
            "allow_commands",
        )
        out = []
        for name in names:
            widget = getattr(self, name, None)
            if widget is not None:
                out.append(widget)
        if hasattr(self, "rules_table"):
            out.append(self.rules_table)
        # 地图 / 区域这些页签里的写入按钮是循环里临时创建的，
        # 构建时登记到 _extra_write_widgets，这里统一收口
        out.extend(getattr(self, "_extra_write_widgets", []))
        return out

    def _apply_write_gate(self, writable: bool,
                          has_world: bool | None = None) -> None:
        """启用/禁用写入入口，并给灰按钮补上「为什么灰」。"""
        if has_world is None:                            # pragma: no cover
            has_world = self.world() is not None
        reason = ("还没有打开世界存档：先点「打开世界存档…」"
                  if not has_world else "只读模式：先点「解锁编辑」")
        for widget in self._write_widgets():
            widget.setEnabled(writable)
            original = widget.property("_ro_original_tip")
            if original is None:
                original = widget.toolTip()
                widget.setProperty("_ro_original_tip", original)
            if writable:
                widget.setToolTip(original)
            else:
                prefix = f"{original}　—　" if original else ""
                widget.setToolTip(prefix + reason)

    # ==================================================================
    # 最近打开
    # ==================================================================
    def _rebuild_recent_menu(self) -> None:
        menu = self._recent_menu
        if menu is None:                                    # pragma: no cover
            return
        menu.clear()
        try:
            entries = self.cache.recent(limit=15)
        except Exception:                                   # pragma: no cover
            entries = []
        if not entries:
            action = menu.addAction("（还没有打开过世界）")
            action.setEnabled(False)
        for entry in entries:
            path = str(entry.get("path", ""))
            stale = "（缓存过期）" if entry.get("_stale") else ""
            action = menu.addAction(
                f"{entry.get('name') or Path(path).name}  ·  "
                f"DV {entry.get('data_version')}  ·  "
                f"{entry.get('players')} 玩家{stale}")
            action.setToolTip(path)
            action.triggered.connect(
                lambda _=False, p=path: self.open_path(p))
        menu.addSeparator()
        restore = menu.addAction("恢复最近一次会话的世界…")
        restore.triggered.connect(self._open_last_world)
        clear = menu.addAction("清除最近列表")
        clear.triggered.connect(self._clear_recent)

    def _open_last_world(self) -> None:
        from app.storage import DraftStore
        saved = DraftStore().load("last_world") or {}
        path = str(saved.get("path", "") or "")
        if path and Path(path).is_dir():
            self.open_path(path)
        else:
            self.statusMessage.emit("没有可恢复的世界路径。")

    def _clear_recent(self) -> None:
        try:
            self.cache.clear()
        except Exception:                                   # pragma: no cover
            pass
        self._rebuild_recent_menu()
        self.statusMessage.emit("已清除世界索引缓存。")


__all__ = [
    "WorldPanel", "EDITOR_TABS", "SceneDialog", "GrowItemsDialog",
    "BlueprintExportDialog", "CrossWorldCopyDialog", "NlpWorldDialog",
    "make_table", "FilterBar", "SectionBox", "export_table_csv",
]
