"""命令面板集合。

SummonPanel / GivePanel / ExecutePanel / SchemaPanel / BatchPanel
DatapackPanel / HistoryPanel / PresetPanel / FragmentPanel / WorkbenchPanel
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer, QMimeData, QSize
from PySide6.QtGui import QFont, QDrag, QColor
from PySide6.QtWidgets import QStyle
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout, QLabel,
    QPushButton, QToolButton, QGroupBox, QSplitter, QListWidget,
    QListWidgetItem, QTreeWidget, QTreeWidgetItem, QPlainTextEdit, QLineEdit,
    QSpinBox, QCheckBox, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMenu, QMessageBox, QInputDialog,
    QFileDialog, QApplication, QDialog, QDialogButtonBox, QScrollArea,
    QStackedWidget, QTabWidget,
)

from app.core import (
    BuildContext, SchemaBuilder, ArgSpec,
    iter_builders, get_builder, load_schemas,
)
from app.core.builders import (
    SummonBuilder, GiveBuilder, ExecuteBuilder, ExecuteNode, default_params,
    NODE_TYPES, CONDITION_TYPES, STORE_TARGETS,
)
from app.core.item_components import (
    ComponentSpec, MODE_REMOVE, MODE_SET, check_component_value,
    component_defs, component_preview, clear_cache as clear_component_cache,
    normalize_id, parse_components, short_id,
)
from app.core.data import store as data_store
from app.core.datapack import (
    export_mcfunction, export_mcfunction_multi,
    DatapackProject, FunctionEntry, validate_namespace,
    has_placeholder, _sanitize_function_name,
)
from app.storage import (
    HistoryStore, PresetStore, FragmentStore, Fragment,
)
from app.core.workbench import (
    Step, CommandScript, STEP_KINDS, STEP_KIND_LABELS,
)
from app.core.snbt import SnbtParser, SnbtParseError
from app.core.nbt_binary import load_nbt_file, NbtFormatError
from app.ui.mime import (
    MIME_FRAGMENT, MIME_COMMAND, MIME_NBT, read_mime,
)
from app.ui.widgets import (
    CoordInput, IdPicker, NbtTreeEditor, RecommendSidebar,
    TargetSelectorDialog, ComponentEditDialog,
    ComponentPickerDialog, EnchantmentsEditDialog, ItemSelect, ValueEditDialog,
)
from app.ui.dialogs import (
    SavePresetDialog, PlaceholderDialog,
)
from app.ui.dialogs.diff_dialog import DiffDialog
from app.ui.theme import colors as theme_colors


FRAGMENT_MIME = MIME_FRAGMENT

# /give 是手写 Builder，为了让「批量生成」也能用它，这里给一份等价 schema。
GIVE_BATCH_SCHEMA: dict = {
    "command": "give",
    "display": "/give — 给予物品",
    "category": "核心命令",
    "args": [
        {"key": "targets", "label": "目标", "kind": "target",
         "default": "@a", "required": True,
         "hint": "可以是 @p / @a / @e[type=…] 等选择器或玩家名"},
        {"key": "item", "label": "物品 ID", "kind": "item",
         "default": "minecraft:diamond_sword", "required": True,
         "hint": "可省略 minecraft: 前缀"},
        {"key": "count", "label": "数量", "kind": "int", "default": "",
         "optional": True, "hint": "留空表示 1 个"},
        {"key": "components", "label": "物品组件", "kind": "text", "default": "",
         "optional": True,
         "hint": '例如 [minecraft:custom_name="神剑",minecraft:unbreakable=1b]'},
    ],
}


# =========================================================================
# 通用工具
# =========================================================================

def _notify(widget: QWidget, text: str, timeout: int = 2000) -> None:
    """统一的状态栏通知。"""
    w = widget.window()
    if hasattr(w, "statusBar"):
        try:
            w.statusBar().showMessage(text, timeout)
        except Exception:
            pass


class StatusBadge(QLabel):
    """面板顶部状态徽章：一条横条同时说明当前配置与校验状态。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(26)
        self.setMaximumHeight(28)
        self._apply("#1F6FEB", "#FFFFFF")

    def _apply(self, bg: str, fg: str) -> None:
        self.setStyleSheet(
            f"padding: 3px 10px; border-radius: 4px; font-size: 12px; "
            f"background: {bg}; color: {fg};")

    def update_state(self, text: str, level: str = "ok") -> None:
        colors = {
            "ok": ("#1F6FEB", "#FFFFFF"),
            "warn": ("#9E6A03", "#FFFFFF"),
            "error": ("#B62324", "#FFFFFF"),
        }
        bg, fg = colors.get(level, colors["ok"])
        self._apply(bg, fg)
        self.setText(text)
        self.setToolTip(text)


class EmptyState(QWidget):
    """通用空状态引导页。"""

    def __init__(self, text: str, buttons: list[tuple[str, object]],
                 parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.addStretch(1)

        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet(_muted(label, size=13))
        v.addWidget(label)

        row = QHBoxLayout()
        row.addStretch(1)
        for text_, cb in buttons:
            btn = QPushButton(text_)
            btn.clicked.connect(cb)
            row.addWidget(btn)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)


class PanelActionBar(QWidget):
    """面板底部的「主行动」条：复制命令 / 发送到输出区 / 清空。

    为什么要有它：旧版面板里把参数填完之后，唯一能「拿走结果」的按钮在
    右侧输出区的标题行，文案还只写「复制」；用户根本不知道下一步在哪。
    现在每条命令面板的底部都有明确的终点动作，并且实时显示当前命令长度。
    """

    copied = Signal(str)
    sent = Signal(str)

    def __init__(self, panel, parent=None) -> None:
        super().__init__(parent)
        self.panel = panel
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(6)

        self.hint = QLabel("命令会实时同步到右侧「命令输出」")
        self.hint.setStyleSheet(_muted(self.hint, size=12))
        row.addWidget(self.hint)
        row.addStretch(1)

        self.btn_clear = QPushButton("重置为示例")
        self.btn_clear.setObjectName("Secondary")
        self.btn_clear.setToolTip("把参数恢复成内置示例（不是清空，示例会立刻预览）")
        self.btn_clear.clicked.connect(self._on_clear)
        # 没有初始值的面板（Schema 表单）不显示「清空参数」这种空按钮
        self.btn_clear.setVisible(callable(getattr(panel, "_load_demo", None)))
        row.addWidget(self.btn_clear)

        self.btn_send = QPushButton("发送到输出区")
        self.btn_send.setObjectName("Secondary")
        self.btn_send.setToolTip("把当前命令送进右侧输出区（通常已经自动同步）")
        self.btn_send.clicked.connect(self._on_send)
        row.addWidget(self.btn_send)

        self.btn_copy = QPushButton("复制命令")
        self.btn_copy.setObjectName("Primary")
        self.btn_copy.setToolTip("复制到剪贴板，到游戏聊天栏粘贴")
        self.btn_copy.clicked.connect(self._on_copy)
        row.addWidget(self.btn_copy)

        # 面板命令一变就刷新「当前命令 N 字符」与按钮可用态
        changed = getattr(panel, "commandChanged", None)
        if changed is not None:
            changed.connect(lambda _text: self.refresh())
        self.refresh()

    # ------------------------------------------------------------------
    def _command(self) -> str:
        getter = getattr(self.panel, "current_command", None)
        if not callable(getter):
            return ""
        try:
            return (getter() or "").strip()
        except Exception:                               # pragma: no cover
            return ""

    def refresh(self) -> None:
        command = self._command()
        has = bool(command)
        self.btn_copy.setEnabled(has)
        self.btn_send.setEnabled(has)
        self.hint.setText(
            f"当前命令 · {len(command)} 字符　命令会实时同步到右侧「命令输出」"
            if has else "还没有可生成的命令：先在上面的表单里选一项")

    def _on_copy(self) -> None:
        command = self._command()
        if not command:
            self.hint.setText("还没有可复制的命令。")
            return
        QApplication.clipboard().setText(command)
        self.hint.setText(f"✔ 已复制 {len(command)} 个字符 —— 到游戏里按 T 粘贴")
        self.copied.emit(command)

    def _on_send(self) -> None:
        command = self._command()
        if not command:
            return
        self.sent.emit(command)

    def _on_clear(self) -> None:
        reset = getattr(self.panel, "_load_demo", None)
        if callable(reset):
            reset()
            self.refresh()
            self.hint.setText("已恢复成初始示例。")


def _muted(widget, size: int | None = None) -> str:
    """主题感知的「次要文字」样式。

    旧代码到处硬编码 `#8B949E`：深色下没问题，浅色下对比度只有 ~2.9:1，
    小字几乎看不清。统一走 theme 的语义色后，两套主题都可读。
    """
    qss = f"color: {theme_colors(widget)['muted']};"
    return qss + (f" font-size: {size}px;" if size else "")


def _eval_issue_level(errors) -> str:
    """只在出现 error / warn 时才报警，纯 info 提示保持绿色徽章。"""
    levels = [getattr(e, "level", "error") for e in errors]
    if "error" in levels:
        return "error"
    if "warn" in levels:
        return "warn"
    return "ok"


def _field_label(text: str) -> QLabel:
    """表单左侧的字段名：统一宽度，让多行表单对齐。"""
    label = QLabel(text)
    label.setStyleSheet(_muted(label))
    label.setMinimumWidth(52)
    return label


def _section_label(text: str) -> QLabel:
    """小标题：比正文更淡、更小，用来分组而不抢注意力。"""
    label = QLabel(text)
    label.setStyleSheet(_muted(label, size=12))
    return label


def _single_line_label(text: str = "") -> QLabel:
    """单行省略标签：内容过长时显示 …，完整内容放 tooltip。"""
    label = QLabel(text)
    label.setStyleSheet(_muted(label, size=12))
    label.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse)
    label._full_text = text  # type: ignore[attr-defined]
    return label


def _set_single_line_text(label: QLabel, text: str) -> None:
    full = text or ""
    label._full_text = full  # type: ignore[attr-defined]
    label.setToolTip(full)
    metrics = label.fontMetrics()
    available = max(120, label.width() - 8)
    label.setText(metrics.elidedText(full, Qt.TextElideMode.ElideRight,
                                     available))


# =========================================================================
# SummonPanel
# =========================================================================

class SummonPanel(QWidget):
    commandChanged = Signal(str)
    #: 面板内「复制命令」按钮成功复制（主窗口据此给可见反馈）
    commandCopied = Signal(str)
    #: 面板内「发送到输出区」按钮（与复制区分开，否则状态栏会说错话）
    commandSent = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.builder = SummonBuilder()
        #: None = 跟随宽度自动收起；True/False = 用户手动指定
        self._recommend_choice: bool | None = None
        self._build_ui()
        self._load_demo()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        self.badge = StatusBadge()
        v.addWidget(self.badge)

        tip = _single_line_label("配置实体、坐标与 NBT，右侧实时预览生成的命令。")
        tip_row = QHBoxLayout()
        tip_row.setSpacing(6)
        tip_row.addWidget(tip, 1)
        # 窄窗口里第三列会把实体列表和坐标挤变形 —— 给一个手动开关
        self.btn_recommend = QToolButton()
        self.btn_recommend.setText("NBT 推荐")
        self.btn_recommend.setCheckable(True)
        self.btn_recommend.setChecked(True)
        self.btn_recommend.setAutoRaise(True)
        self.btn_recommend.setToolTip(
            "显示 / 隐藏右侧「NBT 推荐」栏（窄窗口会自动收起，"
            "手动点过以后就听你的）")
        self.btn_recommend.toggled.connect(self._on_recommend_toggled)
        tip_row.addWidget(self.btn_recommend)
        v.addLayout(tip_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)

        left = QWidget()
        left.setMinimumWidth(200)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)

        ent_box = QGroupBox("实体")
        ent_layout = QVBoxLayout(ent_box)
        self.entity_picker = IdPicker(data_store().entities(), "实体 ID")
        self.entity_picker.idChanged.connect(self._on_entity_changed)
        ent_layout.addWidget(self.entity_picker)
        lv.addWidget(ent_box, 1)

        pos_box = QGroupBox("坐标")
        pos_layout = QVBoxLayout(pos_box)
        self.coord = CoordInput()
        self.coord.changed.connect(self._refresh_command)
        pos_layout.addWidget(self.coord)
        lv.addWidget(pos_box, 0)

        right = QWidget()
        right.setMinimumWidth(280)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)

        nbt_box = QGroupBox("NBT（可留空）")
        nbt_layout = QVBoxLayout(nbt_box)
        self.nbt_enable = QCheckBox("附加 NBT")
        self.nbt_enable.stateChanged.connect(self._on_nbt_toggle)
        nbt_layout.addWidget(self.nbt_enable)
        self.nbt_editor = NbtTreeEditor()
        self.nbt_editor.changed.connect(self._on_nbt_changed)
        self.nbt_editor.setEnabled(False)
        nbt_layout.addWidget(self.nbt_editor, 1)
        rv.addWidget(nbt_box, 1)

        self.recommend = RecommendSidebar()
        self.recommend.set_title("NBT 推荐")
        self.recommend.setMinimumWidth(200)
        self.recommend.applyRequested.connect(self._on_recommend_apply)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.addWidget(self.recommend)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([300, 460, 220])
        v.addWidget(splitter, 1)

        self.action_bar = PanelActionBar(self)
        self.action_bar.copied.connect(self.commandCopied.emit)
        self.action_bar.sent.connect(self.commandSent.emit)
        v.addWidget(self.action_bar)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(f"color: {theme_colors(self)['error']};")
        self.error_label.setVisible(False)
        v.addWidget(self.error_label, 0)

    def _on_recommend_toggled(self, visible: bool) -> None:
        self._recommend_choice = bool(visible)
        self.recommend.setVisible(bool(visible))

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        super().resizeEvent(event)
        if self._recommend_choice is not None:
            return
        # 自动模式：窄了就收起推荐栏（否则坐标区会被挤成几十像素）。
        # 用 isVisibleTo(self) 而不是 isVisible()：面板还没 show 时后者恒为
        # False，会漏掉「显式隐藏」这一步，show 之后又冒出来。
        desired = self.width() >= 980
        if self.recommend.isVisibleTo(self) != desired:
            self.btn_recommend.blockSignals(True)
            self.btn_recommend.setChecked(desired)
            self.btn_recommend.blockSignals(False)
            self.recommend.setVisible(desired)

    def _load_demo(self) -> None:
        self.entity_picker.set_id("minecraft:zombie")
        self.builder.entity = "minecraft:zombie"
        self.coord.set_values(False, "~", "~", "~")
        self.recommend.set_context("entity", "minecraft:zombie")
        self._refresh_command()
        self._demo_command = self.current_command()

    def is_demo(self) -> bool:
        """当前内容是否还是初始示例（用户一个字都没改）。

        首用者最容易被「面板里已经有一条命令」误导成「这是我生成的」，
        主窗口据此把示例与真实结果区分开（见 main_window._update_guidance）。
        """
        demo = getattr(self, "_demo_command", None)
        if not demo:
            return False
        return self.current_command().strip() == demo.strip()

    def _on_entity_changed(self, value: str) -> None:
        self.builder.entity = value
        self.recommend.set_context("entity", value)
        self._refresh_command()

    def _on_nbt_toggle(self, state: int) -> None:
        on = self.nbt_enable.isChecked()
        self.nbt_editor.setEnabled(on)
        if on:
            # 勾选后立即把编辑器内容同步进 builder（首次勾选时补一个空 compound），
            # 否则预览会继续用旧的 NBT 文本。
            if not self.builder.nbt.strip():
                from app.core.snbt import SnbtNode
                self.nbt_editor.set_root(SnbtNode.new_compound())
            self.builder.nbt = self.nbt_editor.to_snbt(pretty=False)
        else:
            self.builder.nbt = ""
        self._refresh_command()

    def _on_nbt_changed(self) -> None:
        if self.nbt_enable.isChecked():
            self.builder.nbt = self.nbt_editor.to_snbt(pretty=False)
        else:
            self.builder.nbt = ""
        self._refresh_command()

    def _refresh_command(self) -> None:
        enabled, x, y, z = self.coord.values()
        self.builder.use_pos = enabled
        self.builder.x, self.builder.y, self.builder.z = x, y, z
        errors = self.builder.validate(BuildContext())
        text = "；".join(str(e) for e in errors)
        self.error_label.setText(text)
        self.error_label.setVisible(bool(text))
        self.badge.update_state(
            f"/summon　{self.builder.status_summary()}",
            _eval_issue_level(errors))
        self.commandChanged.emit(self.builder.build(BuildContext()))

    def reload_from_builder(self) -> None:
        self.entity_picker.set_id(self.builder.entity)
        self.coord.set_values(self.builder.use_pos,
                              self.builder.x, self.builder.y, self.builder.z)
        has_nbt = bool(self.builder.nbt.strip())
        self.nbt_enable.blockSignals(True)
        self.nbt_enable.setChecked(has_nbt)
        self.nbt_enable.blockSignals(False)
        self.nbt_editor.setEnabled(has_nbt)
        if has_nbt:
            try:
                self.nbt_editor.set_snbt(self.builder.nbt)
            except Exception:
                pass
        self.recommend.set_context("entity", self.builder.entity)
        self._refresh_command()

    def _on_recommend_apply(self, entry: dict) -> None:
        nbt = str(entry.get("nbt", "")).strip()
        if not nbt:
            return
        default = entry.get("default", "")
        snbt = f"{{{nbt}:{default}}}" if default else f"{{{nbt}:0b}}"

        if not self.nbt_enable.isChecked():
            self.nbt_enable.setChecked(True)

        if not self.nbt_editor.apply_snbt_merge(snbt):
            QMessageBox.warning(self, "推荐解析失败", "SNBT 无法解析。")
            return

        self._msg(f"已应用推荐：{nbt}")

    def _msg(self, text: str) -> None:
        _notify(self, text)

    def current_command(self) -> str:
        return self.builder.build(BuildContext())

    # ---- 从存档 NBT 接收数据 ----

    def receive_entity(self, entity, pos: str = "") -> bool:
        """把存档里识别出的实体填进面板（`/summon <实体> <坐标> <NBT>`）。"""
        if entity is None:
            return False
        entity_id = str(getattr(entity, "entity_id", "") or "").strip()
        if not entity_id:
            return False
        self.builder.entity = entity_id
        nbt = str(getattr(entity, "nbt", "") or "").strip()
        if nbt:
            self.builder.nbt = nbt
        if pos.strip():
            parts = pos.split()
            if len(parts) == 3:
                self.builder.use_pos = True
                self.builder.x, self.builder.y, self.builder.z = parts
        self.reload_from_builder()
        try:
            from app.core.nbt_bridge import entity_display
            name = entity_display(entity_id)
        except Exception:                                   # pragma: no cover
            name = ""
        self._msg(f"已载入{'实体 ' + name if name else '实体数据'}"
                  f"（{entity_id}）")
        return True

    # ---- 会话草稿 ----

    def dump_state(self) -> dict:
        return {
            "entity": self.builder.entity,
            "use_pos": bool(self.builder.use_pos),
            "x": self.builder.x, "y": self.builder.y, "z": self.builder.z,
            "nbt": self.builder.nbt,
        }

    def restore_state(self, data: dict | None) -> bool:
        if not isinstance(data, dict) or not data:
            return False
        self.builder.entity = str(data.get("entity") or "minecraft:zombie")
        self.builder.use_pos = bool(data.get("use_pos", False))
        self.builder.x = str(data.get("x", "~"))
        self.builder.y = str(data.get("y", "~"))
        self.builder.z = str(data.get("z", "~"))
        self.builder.nbt = str(data.get("nbt", ""))
        self.reload_from_builder()
        return True


# =========================================================================
# GivePanel
# =========================================================================

class GivePanel(QWidget):
    """`/give` 编辑面板。

    设计要点：
        * 物品 ID 自动补 minecraft: 前缀，支持直接手输与浏览；
        * 组件用表格管理，前两列（组件 / 预览）常驻可见，值/模式按需编辑；
        * 「快速处方」一键套用常用组件组合，「补全建议」按物品推荐组件；
        * 顶部提示行给出可执行的修正建议（不可堆叠、组件用错、语法错误等）。
    """

    commandChanged = Signal(str)
    #: 面板内「复制命令」按钮成功复制（主窗口据此给可见反馈）
    commandCopied = Signal(str)
    #: 面板内「发送到输出区」按钮（与复制区分开，否则状态栏会说错话）
    commandSent = Signal(str)

    TARGET_PRESETS = ("@p", "@a", "@r", "@s", "@e", "@e[type=player]")
    COUNT_PRESETS = (1, 2, 4, 8, 16, 32, 64, 100, 6400)
    # 组件表只留三列：组件 / 值预览 / 操作（窄面板下也能完整显示）
    COL_KIND, COL_PREVIEW, COL_ACTION = range(3)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.builder = GiveBuilder()
        self._comp_defs: dict[str, dict] = component_defs()
        self._presets: list[dict] = [
            p for p in data_store().item_presets() if isinstance(p, dict)
        ]
        self._last_item = ""
        self._hint_texts: list[str] = []
        self._loading = False

        self._item_timer = QTimer(self)
        self._item_timer.setSingleShot(True)
        self._item_timer.setInterval(220)
        self._item_timer.timeout.connect(self._on_item_settled)

        self._build_ui()
        self._load_demo()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        self.badge = StatusBadge()
        v.addWidget(self.badge)

        # 提示行：单行 + 省略号，完整内容放 tooltip，避免换行把编辑区挤扁
        hints_row = QHBoxLayout()
        hints_row.setSpacing(6)
        self.hint_label = _single_line_label("")
        hints_row.addWidget(self.hint_label, 1)
        self.hint_more = QToolButton()
        self.hint_more.setText("全部提示")
        self.hint_more.setAutoRaise(True)
        self.hint_more.setVisible(False)
        self.hint_more.clicked.connect(self._show_all_hints)
        hints_row.addWidget(self.hint_more)
        v.addLayout(hints_row)

        basics = QGroupBox("基础参数")
        grid = QGridLayout(basics)
        grid.setContentsMargins(10, 4, 10, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(3, 1)

        grid.addWidget(_field_label("目标"), 0, 0)
        self.target_line = QLineEdit("@p")
        self.target_line.setPlaceholderText("@p / @a / @e[type=zombie] …")
        self.target_line.textChanged.connect(self._on_target_changed)
        grid.addWidget(self.target_line, 0, 1, 1, 3)
        self.target_combo = QComboBox()
        self.target_combo.addItem("常用…")
        for value in self.TARGET_PRESETS:
            self.target_combo.addItem(value, value)
        self.target_combo.setToolTip("常用目标选择器")
        self.target_combo.activated.connect(self._on_target_preset)
        grid.addWidget(self.target_combo, 0, 4)
        btn_target = QPushButton("编辑…")
        btn_target.setToolTip("打开图形化目标选择器")
        btn_target.clicked.connect(self._open_target_dialog)
        grid.addWidget(btn_target, 0, 5)

        grid.addWidget(_field_label("物品"), 1, 0)
        self.item_select = ItemSelect(data_store().items())
        self.item_select.idChanged.connect(self._on_item_changed)
        grid.addWidget(self.item_select, 1, 1, 1, 5)

        grid.addWidget(_field_label("数量"), 2, 0)
        self.count_enable = QCheckBox("指定")
        self.count_enable.stateChanged.connect(self._on_count_toggle)
        grid.addWidget(self.count_enable, 2, 1)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 6400)
        self.count_spin.setValue(1)
        self.count_spin.setEnabled(False)
        self.count_spin.setToolTip("不勾选「指定」时命令里不写数量，等同于 1 个")
        self.count_spin.valueChanged.connect(self._on_count_changed)
        grid.addWidget(self.count_spin, 2, 2)
        self.count_combo = QComboBox()
        self.count_combo.addItem("快捷数量…")
        for value in self.COUNT_PRESETS:
            self.count_combo.addItem(str(value), value)
        self.count_combo.activated.connect(self._on_count_preset)
        grid.addWidget(self.count_combo, 2, 3)
        btn_clear = QPushButton("清空组件")
        btn_clear.setToolTip("移除全部物品组件")
        btn_clear.clicked.connect(self._on_clear_components)
        grid.addWidget(btn_clear, 2, 4, 1, 2)

        v.addWidget(basics, 0)

        comp_box = QGroupBox("物品组件")
        comp_layout = QVBoxLayout(comp_box)
        comp_layout.setContentsMargins(10, 4, 10, 8)
        comp_layout.setSpacing(6)

        comp_top = QHBoxLayout()
        comp_top.setSpacing(6)
        btn_add = QPushButton("添加组件…")
        btn_add.clicked.connect(self._on_add_component)
        comp_top.addWidget(btn_add)

        btn_preset = QPushButton("快速处方 ▾")
        btn_preset.setToolTip("一次套用一组常用组件（会替换物品与现有组件）")
        btn_preset.clicked.connect(self._show_presets_menu)
        comp_top.addWidget(btn_preset)

        self.btn_apply_suggest = QPushButton("套用建议")
        self.btn_apply_suggest.setToolTip("把右侧「补全建议」里的组件全部加进来")
        self.btn_apply_suggest.clicked.connect(self._on_apply_suggestions)
        comp_top.addWidget(self.btn_apply_suggest)

        comp_top.addStretch(1)
        self.comp_summary = QLabel("")
        self.comp_summary.setStyleSheet(_muted(self.comp_summary, size=12))
        comp_top.addWidget(self.comp_summary)
        btn_batch = QPushButton("批量填充…")
        btn_batch.setToolTip("把当前物品与组件带到「批量生成」页签做多行生成")
        btn_batch.clicked.connect(self._on_send_to_batch)
        comp_top.addWidget(btn_batch)
        comp_layout.addLayout(comp_top)

        self.comp_table = QTableWidget(0, 3)
        self.comp_table.setHorizontalHeaderLabels(
            ["组件", "值预览", "操作"])
        header = self.comp_table.horizontalHeader()
        header.setMinimumSectionSize(72)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(
            self.COL_KIND, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(
            self.COL_PREVIEW, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(
            self.COL_ACTION, QHeaderView.ResizeMode.Fixed)
        self.comp_table.setColumnWidth(self.COL_KIND, 210)
        self.comp_table.setColumnWidth(self.COL_ACTION, 104)
        self.comp_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.comp_table.verticalHeader().setDefaultSectionSize(32)
        self.comp_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.comp_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.comp_table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.comp_table.customContextMenuRequested.connect(
            self._on_table_context_menu)
        self.comp_table.itemDoubleClicked.connect(
            lambda item: self._edit_component(item.row()))
        self.comp_table.verticalHeader().setVisible(False)
        self.comp_table.setAlternatingRowColors(True)
        self.comp_table.setMinimumHeight(200)

        inner = QSplitter(Qt.Orientation.Horizontal)
        inner.setChildrenCollapsible(True)
        inner.setHandleWidth(4)
        inner.addWidget(self.comp_table)

        self.side_panel = QWidget()
        side = self.side_panel
        side.setMinimumWidth(190)
        side_v = QVBoxLayout(side)
        side_v.setContentsMargins(0, 0, 0, 0)
        side_v.setSpacing(4)
        side_head = QHBoxLayout()
        side_head.setSpacing(4)
        side_title = _section_label("补全建议")
        side_head.addWidget(side_title)
        side_head.addStretch(1)
        self.suggest_detail = QLabel("")
        self.suggest_detail.setStyleSheet(_muted(self.suggest_detail, size=11))
        side_head.addWidget(self.suggest_detail)
        side_v.addLayout(side_head)

        self.suggest = QListWidget()
        self.suggest.setAlternatingRowColors(True)
        self.suggest.setToolTip("双击直接加入组件（已存在则更新取值）")
        self.suggest.setMinimumHeight(140)
        self.suggest.itemDoubleClicked.connect(self._on_suggestion_picked)
        side_v.addWidget(self.suggest, 1)

        self.recommend = RecommendSidebar()
        self.recommend.set_title("物品推荐")
        self.recommend.setVisible(False)
        self.recommend.applyRequested.connect(self._on_recommend_apply)
        side_v.addWidget(self.recommend)

        inner.addWidget(side)
        inner.setSizes([620, 260])
        inner.setStretchFactor(0, 1)
        inner.setStretchFactor(1, 0)
        self.comp_splitter = inner
        comp_layout.addWidget(inner, 1)
        v.addWidget(comp_box, 1)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {theme_colors(self)['error']};")
        self.error_label.setVisible(False)
        v.addWidget(self.error_label, 0)

        self.action_bar = PanelActionBar(self)
        self.action_bar.copied.connect(self.commandCopied.emit)
        self.action_bar.sent.connect(self.commandSent.emit)
        v.addWidget(self.action_bar)

    def _show_all_hints(self) -> None:
        QMessageBox.information(self, "校验提示",
                                "\n".join(self._hint_texts) or "没有提示。")

    # ------------------------------------------------------------------
    # 初始化 / 上下文
    # ------------------------------------------------------------------

    def _load_demo(self) -> None:
        self.target_line.setText(self.builder.target or "@p")
        self.item_select.set_id(self.builder.item)
        self._reload_recent_items()
        self._refresh_table()
        self._on_item_settled(emit=False)
        self._refresh_command()
        self._demo_command = self.current_command()

    def is_demo(self) -> bool:
        """当前内容是否还是初始示例（用户一个字都没改）。"""
        demo = getattr(self, "_demo_command", None)
        if not demo:
            return False
        return self.current_command().strip() == demo.strip()

    def _reload_recent_items(self) -> None:
        try:
            recent = HistoryStore().recent_values("give", 2, limit=8)
        except Exception:
            recent = []
        if recent:
            self.item_select.set_recent(recent)

    def _on_target_preset(self, index: int) -> None:
        value = self.target_combo.itemData(index)
        if value:
            self.target_line.setText(str(value))

    def _on_count_preset(self, index: int) -> None:
        value = self.count_combo.itemData(index)
        if value is None:
            return
        self.count_enable.setChecked(True)
        self.count_spin.setValue(int(value))

    def _open_target_dialog(self) -> None:
        dlg = TargetSelectorDialog(self.target_line.text(), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.target_line.setText(dlg.value())

    # ------------------------------------------------------------------
    # 状态同步
    # ------------------------------------------------------------------

    def _on_target_changed(self, text: str) -> None:
        self.builder.target = text.strip() or "@p"
        self._refresh_command()

    def _on_item_changed(self, value: str) -> None:
        self.builder.item = value.strip()
        self._item_timer.start()
        self._refresh_command()

    def _on_item_settled(self, emit: bool = True) -> None:
        """物品变化稳定后刷新建议（避免每敲一个字符都重建列表）。"""
        item = self.builder.item.strip()
        if item == self._last_item:
            return
        self._last_item = item
        if hasattr(self, "recommend"):
            self.recommend.set_context("item", item)
        self._refresh_suggestions()
        if emit:
            self._refresh_command()

    def _on_count_toggle(self, _state: int) -> None:
        on = self.count_enable.isChecked()
        self.count_spin.setEnabled(on)
        self.builder.use_count = on
        self._refresh_command()

    def _on_count_changed(self, value: int) -> None:
        self.builder.count = int(value)
        if not self.count_enable.isChecked():
            self.count_enable.setChecked(True)
        self._refresh_command()

    # ------------------------------------------------------------------
    # 组件增删改
    # ------------------------------------------------------------------

    def _on_add_component(self) -> None:
        dlg = ComponentPickerDialog(self, item=self.builder.item)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        for cid in dlg.selected_ids():
            defn = self._comp_defs.get(cid, {})
            self.builder.add_component(
                cid, value=str(defn.get("default", "")), mode=MODE_SET)
        self._refresh_table()
        self._refresh_command()

    def _on_clear_components(self) -> None:
        if not self.builder.components:
            return
        self.builder.components = []
        self._refresh_table()
        self._refresh_command()

    def _on_send_to_batch(self) -> None:
        """把当前物品/组件填到批量生成页签（按 /give 的参数列）。"""
        window = self.window()
        panel = getattr(window, "batch_panel", None)
        if panel is None:
            _notify(self, "当前窗口没有批量生成页签。", 2500)
            return
        try:
            index = panel.command_combo.findData("give")
            if index < 0:
                _notify(self, "批量生成里没有 /give 命令。", 3000)
                return
            # 先切到 /give，参数列才是 give 的（否则会被上一条命令的列过滤掉）
            panel.command_combo.setCurrentIndex(index)
            panel._on_command_changed(index)
            keys = {spec.key for spec in getattr(panel, "_args", [])}
            values = {
                "targets": self.builder.target,
                "item": normalize_id(self.builder.item),
                "count": str(max(1, int(self.builder.count)))
                if self.builder.use_count else "",
                "components": self.builder.values.get("components", ""),
            }
            values = {k: v for k, v in values.items() if k in keys}
            if not values or panel._add_row_from_values(values) < 0:
                _notify(self, "/give 没有可填充的参数列。", 3000)
                return
        except Exception as exc:
            _notify(self, f"填充失败：{exc}", 4000)
            return
        tabs = getattr(window, "center_tabs", None)
        if tabs is not None:
            tabs.setCurrentWidget(panel)
        _notify(self, "已把 /give 内容填入批量生成。", 3000)

    def _show_presets_menu(self) -> None:
        if not self._presets:
            _notify(self, "没有可用的快速处方。", 2500)
            return
        menu = QMenu(self)
        by_cat: dict[str, list[dict]] = {}
        for preset in self._presets:
            by_cat.setdefault(str(preset.get("category", "其他")), []).append(
                preset)
        if len(by_cat) == 1:
            for preset in next(iter(by_cat.values())):
                act = menu.addAction(str(preset.get("name", "未命名")))
                act.setData(preset)
        else:
            for cat in sorted(by_cat):
                sub = menu.addMenu(cat)
                for preset in by_cat[cat]:
                    act = sub.addAction(str(preset.get("name", "未命名")))
                    act.setData(preset)
        chosen = menu.exec(self.mapToGlobal(self.rect().topLeft()))
        if chosen is None:
            return
        preset = chosen.data()
        if isinstance(preset, dict):
            self._apply_preset(preset)

    def _apply_preset(self, preset: dict) -> None:
        specs = parse_components(str(preset.get("components", "")))
        if not specs:
            QMessageBox.information(self, "提示", "该处方没有可用的组件。")
            return
        existing = len(self.builder.components)
        if existing:
            answer = QMessageBox.question(
                self, "替换组件？",
                f"当前已有 {existing} 个组件，套用「{preset.get('name', '')}」"
                "会替换它们。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        item = str(preset.get("item", "")).strip()
        if item:
            self.item_select.set_id(item)
            self.builder.item = item
            self._last_item = item
        raw_count = preset.get("count")
        if raw_count is not None:
            try:
                value = max(1, int(raw_count))
                self.count_enable.setChecked(True)
                self.count_spin.setValue(value)
            except (TypeError, ValueError):
                pass
        self.builder.components = list(specs)
        self._refresh_table()
        self._on_item_settled(emit=False)
        self._refresh_command()
        _notify(self, f"已套用处方的 {len(specs)} 个组件。", 2500)

    def _icon_button(self, name: str, tip: str, slot) -> QToolButton:
        """小图标按钮：优先用系统标准图标，取不到就退回文字。"""
        btn = QToolButton()
        btn.setAutoRaise(True)
        btn.setToolTip(tip)
        icon = None
        try:
            standard = getattr(QStyle.StandardPixmap, name, None)
            if standard is not None:
                icon = self.style().standardIcon(standard)
        except Exception:
            icon = None
        if icon is not None and not icon.isNull():
            btn.setIcon(icon)
            btn.setIconSize(QSize(14, 14))
            btn.setFixedSize(24, 24)
        else:
            fallback = {"SP_ArrowUp": "↑", "SP_ArrowDown": "↓",
                        "SP_TrashIcon": "✕", "SP_FileDialogDetailedView": "…"}
            btn.setText(fallback.get(name, "•"))
            btn.setFixedSize(24, 24)
        btn.clicked.connect(slot)
        return btn

    def _refresh_table(self, select: int = -1) -> None:
        specs = self.builder.components
        table = self.comp_table
        table.setRowCount(0)
        for idx, spec in enumerate(specs):
            table.insertRow(idx)
            definition = self._comp_defs.get(spec.id, {})
            display = str(definition.get("display") or short_id(spec.id))

            kind_item = QTableWidgetItem(f"{display}  ·  {short_id(spec.id)}")
            kind_item.setToolTip(f"{spec.id}\n{spec.to_text()}")
            if spec.removes:
                font = kind_item.font()
                font.setStrikeOut(True)
                kind_item.setFont(font)
            table.setItem(idx, self.COL_KIND, kind_item)

            preview_text = component_preview(spec, self._comp_defs)
            preview = QTableWidgetItem(preview_text)
            preview.setToolTip(f"{display}\n{spec.to_text()}")
            if spec.removes:
                preview.setForeground(QColor(theme_colors(self)["muted"]))
            table.setItem(idx, self.COL_PREVIEW, preview)

            actions = QWidget()
            ah = QHBoxLayout(actions)
            ah.setContentsMargins(0, 0, 0, 0)
            ah.setSpacing(0)
            btn_up = self._icon_button(
                "SP_ArrowUp", "上移",
                lambda _=False, i=idx: self._move_component(i, -1))
            btn_up.setEnabled(idx > 0)
            ah.addWidget(btn_up)
            btn_down = self._icon_button(
                "SP_ArrowDown", "下移",
                lambda _=False, i=idx: self._move_component(i, 1))
            btn_down.setEnabled(idx < len(specs) - 1)
            ah.addWidget(btn_down)
            btn_edit = self._icon_button(
                "SP_FileDialogDetailedView", "编辑组件值（双击行同效）",
                lambda _=False, i=idx: self._edit_component(i))
            ah.addWidget(btn_edit)
            btn_del = self._icon_button(
                "SP_TrashIcon", "删除该组件",
                lambda _=False, i=idx: self._delete_component(i))
            ah.addWidget(btn_del)
            table.setCellWidget(idx, self.COL_ACTION, actions)

        if 0 <= select < table.rowCount():
            table.selectRow(select)
        self._refresh_component_summary()
        self._fit_component_columns()

    def _refresh_component_summary(self) -> None:
        specs = self.builder.components
        if not specs:
            self.comp_summary.setText("尚未添加组件")
            return
        sets = sum(1 for c in specs if not c.removes)
        removes = len(specs) - sets
        text = f"共 {len(specs)} 个：设置 {sets}"
        if removes:
            text += f" · 移除 {removes}"
        self.comp_summary.setText(text)

    def _prepare_edit(self, index: int) -> ComponentSpec | None:
        if not (0 <= index < len(self.builder.components)):
            return None
        return self.builder.components[index]

    def _edit_component(self, index: int) -> None:
        spec = self._prepare_edit(index)
        if spec is None:
            return
        definition = self._comp_defs.get(spec.id)
        if definition is None:
            definition = {"id": spec.id, "display": short_id(spec.id),
                          "kind": "text", "hint": "自由编辑组件值（SNBT 或纯文本）"}

        kind = str(definition.get("kind", "text"))
        if spec.id in ("minecraft:enchantments",
                       "minecraft:stored_enchantments"):
            # 附魔用结构化编辑器，避免手写 SNBT 写错键名/等级
            dlg = EnchantmentsEditDialog(
                spec.value, self,
                title=f"编辑附魔 — {definition.get('display', spec.id)}")
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            spec.value = dlg.value()
            spec.mode = MODE_SET
        elif kind in ("bool", "enum"):
            dlg = ComponentEditDialog(definition, spec.value, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            spec.value = dlg.value()
            spec.mode = MODE_SET
        else:
            dlg = ValueEditDialog(
                f"编辑组件 — {definition.get('display', spec.id)}",
                spec.value,
                hint=str(definition.get("hint", "")),
                validator=lambda text: check_component_value(definition, text),
                parent=self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            spec.value = dlg.value()
            spec.mode = MODE_SET
        self._refresh_table(select=index)
        self._refresh_command()

    def _delete_component(self, index: int) -> None:
        if not (0 <= index < len(self.builder.components)):
            return
        del self.builder.components[index]
        self._refresh_table(select=min(index, len(self.builder.components) - 1))
        self._refresh_command()

    def _move_component(self, index: int, delta: int) -> None:
        new_index = self.builder.move_component(index, delta)
        self._refresh_table(select=new_index)
        self._refresh_command()

    def _toggle_component_mode(self, index: int) -> None:
        spec = self._prepare_edit(index)
        if spec is None:
            return
        spec.mode = MODE_SET if spec.removes else MODE_REMOVE
        if spec.removes:
            spec.value = ""
        elif not spec.value:
            spec.value = str(self._comp_defs.get(spec.id, {}).get("default", ""))
        self._refresh_table(select=index)
        self._refresh_command()

    def _duplicate_component(self, index: int) -> None:
        spec = self._prepare_edit(index)
        if spec is None:
            return
        self.builder.components.insert(index + 1, spec.clone())
        self._refresh_table(select=index + 1)
        self._refresh_command()

    def _copy_component_text(self, index: int) -> None:
        spec = self._prepare_edit(index)
        if spec is None:
            return
        text = spec.to_text()
        QApplication.clipboard().setText(text)
        _notify(self, f"已复制组件：{text}", 2500)

    def _on_table_context_menu(self, pos) -> None:
        row = self.comp_table.rowAt(pos.y())
        menu = QMenu(self)
        act_edit = menu.addAction("编辑组件…")
        act_mode = menu.addAction("切换为「移除」/「设置」")
        act_dup = menu.addAction("复制一份")
        act_up = menu.addAction("上移")
        act_down = menu.addAction("下移")
        menu.addSeparator()
        act_copy = menu.addAction("复制组件文本")
        act_del = menu.addAction("删除")
        for act in (act_edit, act_mode, act_dup, act_up, act_down,
                    act_copy, act_del):
            act.setEnabled(row >= 0)
        chosen = menu.exec(self.comp_table.viewport().mapToGlobal(pos))
        if chosen is None or row < 0:
            return
        if chosen == act_edit:
            self._edit_component(row)
        elif chosen == act_mode:
            self._toggle_component_mode(row)
        elif chosen == act_dup:
            self._duplicate_component(row)
        elif chosen == act_up:
            self._move_component(row, -1)
        elif chosen == act_down:
            self._move_component(row, 1)
        elif chosen == act_copy:
            self._copy_component_text(row)
        elif chosen == act_del:
            self._delete_component(row)

    # ------------------------------------------------------------------
    # 建议
    # ------------------------------------------------------------------

    def _refresh_suggestions(self) -> None:
        entries = self._suggested_components(self.builder.item)
        self.suggest.clear()
        added = {c.id for c in self.builder.components}
        for entry in entries:
            cid = str(entry.get("id", ""))
            defn = self._comp_defs.get(cid, {})
            text = str(defn.get("display", short_id(cid)))
            if cid in added:
                text += "   （已添加）"
            elif defn.get("default"):
                text += f"   = {defn['default']}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, cid)
            item.setToolTip(str(defn.get("hint", cid)))
            self.suggest.addItem(item)
        self.suggest_detail.setText(
            f"共 {len(entries)} 条建议；双击加入组件。")

    def _suggested_components(self, item: str) -> list[dict]:
        iid = normalize_id(item)
        iid = iid if item.strip() else ""
        tags: set[str] = set()
        for entry in data_store().items():
            if str(entry.get("id", "")) == iid:
                tags = {str(t) for t in entry.get("tags", []) if t}
                tags.add(str(entry.get("category", "")))
                break
        if short_id(iid).endswith(("_sword", "_axe", "_pickaxe", "_shovel",
                                   "_hoe", "_bow", "crossbow", "trident")):
            tags |= {"weapon", "tool"}
        if short_id(iid).endswith(("_helmet", "_chestplate", "_leggings",
                                   "_boots")):
            tags.add("armor")

        out: list[dict] = []
        for cid in ("minecraft:custom_name", "minecraft:lore",
                    "minecraft:unbreakable"):
            if cid in self._comp_defs:
                out.append(self._comp_defs[cid])
        if tags:
            for defn in self._comp_defs.values():
                applies = defn.get("applies") or []
                if applies and set(applies) & tags:
                    out.append(defn)
        for cid in ("minecraft:enchantments", "minecraft:attribute_modifiers",
                    "minecraft:custom_data"):
            defn = self._comp_defs.get(cid)
            if defn is not None:
                out.append(defn)
        seen: set[str] = set()
        unique: list[dict] = []
        for defn in out:
            cid = str(defn.get("id", ""))
            if cid and cid not in seen:
                seen.add(cid)
                unique.append(defn)
        return unique[:20]

    def _on_suggestion_picked(self, item: QListWidgetItem) -> None:
        cid = item.data(Qt.ItemDataRole.UserRole)
        if not cid:
            return
        defn = self._comp_defs.get(str(cid), {})
        self.builder.add_component(
            str(cid), value=str(defn.get("default", "")), mode=MODE_SET)
        self._refresh_table()
        self._refresh_suggestions()
        self._refresh_command()

    def _on_apply_suggestions(self) -> None:
        entries = self._suggested_components(self.builder.item)
        existing = {c.id for c in self.builder.components}
        added = 0
        for defn in entries:
            cid = str(defn.get("id", ""))
            if not cid or cid in existing:
                continue
            self.builder.add_component(
                cid, value=str(defn.get("default", "")), mode=MODE_SET)
            existing.add(cid)
            added += 1
        if not added:
            _notify(self, "建议里的组件都已经添加过了。", 2500)
            return
        self._refresh_table()
        self._refresh_suggestions()
        self._refresh_command()
        _notify(self, f"已加入 {added} 个建议组件。", 2500)

    # ------------------------------------------------------------------
    # 推荐侧栏
    # ------------------------------------------------------------------

    def _on_recommend_apply(self, entry: dict) -> None:
        cid = str(entry.get("component", "")).strip()
        if not cid:
            return
        value = str(entry.get("default", ""))
        self.builder.add_component(normalize_id(cid), value=value, mode=MODE_SET)
        self._refresh_table()
        self._refresh_suggestions()
        self._refresh_command()

    # ------------------------------------------------------------------
    # 刷新 / 反向解析
    # ------------------------------------------------------------------

    def _tips(self) -> list[str]:
        """提示文案 + 错误信息，按重要性排序。"""
        items: list[str] = []
        for issue in self.builder.validate(BuildContext()):
            if getattr(issue, "level", "error") == "error":
                items.append(f"✗ {issue}")
        for text in self.builder.hints:
            if text not in items:
                items.append(f"· {text}")
        if not items:
            items.append("· 没有发现问题，可以直接复制使用")
        return items

    def _refresh_hints(self, errors=None) -> None:
        items = self._tips()
        self._hint_texts = items
        text = "　".join(items[:2])
        if len(items) > 2:
            text += f"　（还有 {len(items) - 2} 条）"
        _set_single_line_text(self.hint_label, text)
        self.hint_more.setVisible(len(items) > 2)
        self.hint_more.setToolTip("\n".join(items))
        # 单独的红色错误行只在有 error 时出现，平时不占高度
        errors = [i for i in items if i.startswith("✗")]
        self.error_label.setText("；".join(e.lstrip("✗ ") for e in errors))
        self.error_label.setVisible(bool(errors))

    def _refresh_command(self) -> None:
        if self._loading:
            return
        errors = self.builder.validate(BuildContext())
        self._refresh_hints(errors)
        self.badge.update_state(
            f"/give　{self.builder.status_summary()}",
            _eval_issue_level(errors))
        self.commandChanged.emit(self.builder.build(BuildContext()))

    def _fit_component_columns(self) -> None:
        """按可用宽度分配组件表列宽，保证「组件」列能看到完整中文名。"""
        table = getattr(self, "comp_table", None)
        if table is None:
            return
        available = table.viewport().width()
        if available < 160:
            return
        action = 104
        kind = max(150, min(260, int((available - action) * 0.56)))
        table.setColumnWidth(self.COL_KIND, kind)
        table.setColumnWidth(self.COL_ACTION, action)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        super().resizeEvent(event)
        self._fit_component_columns()
        if getattr(self, "_hint_texts", None):
            text = "　".join(self._hint_texts[:2])
            if len(self._hint_texts) > 2:
                text += f"　（还有 {len(self._hint_texts) - 2} 条）"
            label = getattr(self, "hint_label", None)
            if label is not None:
                _set_single_line_text(label, text)

    def reload_from_builder(self) -> None:
        self._loading = True
        try:
            self.target_line.setText(self.builder.target or "@p")
            self.item_select.set_id(self.builder.item)
            self.count_enable.blockSignals(True)
            self.count_enable.setChecked(bool(self.builder.use_count))
            self.count_enable.blockSignals(False)
            self.count_spin.setEnabled(bool(self.builder.use_count))
            self.count_spin.blockSignals(True)
            self.count_spin.setValue(max(1, int(self.builder.count or 1)))
            self.count_spin.blockSignals(False)
            self._refresh_table()
            self._last_item = self.builder.item
            if hasattr(self, "recommend"):
                self.recommend.set_context("item", self.builder.item)
            self._refresh_suggestions()
        finally:
            self._loading = False
        self._refresh_command()

    def current_command(self) -> str:
        return self.builder.build(BuildContext())

    # ---- 会话草稿 ----

    def dump_state(self) -> dict:
        return self.builder.to_dict()

    def restore_state(self, data: dict | None) -> bool:
        if not isinstance(data, dict) or not data:
            return False
        self._loading = True
        try:
            self.builder.load_state(data)
            self.target_line.setText(self.builder.target or "@p")
            self.item_select.set_id(self.builder.item)
            self.count_enable.setChecked(bool(self.builder.use_count))
            self.count_spin.setEnabled(bool(self.builder.use_count))
            self.count_spin.setValue(max(1, int(self.builder.count or 1)))
            self._last_item = self.builder.item
            self._refresh_table()
            if hasattr(self, "recommend"):
                self.recommend.set_context("item", self.builder.item)
            self._refresh_suggestions()
        finally:
            self._loading = False
        self._refresh_command()
        return True

    # ---- 从存档 NBT 接收物品 ----

    def _msg(self, text: str) -> None:
        _notify(self, text, 3000)

    def receive_item(self, stack, target: str = "") -> bool:
        """把存档里识别出的物品（含全部组件）填进面板。"""
        if stack is None:
            return False
        from app.core.item_components import short_id
        from app.core.nbt_bridge import component_value_snbt
        self.builder.item = str(getattr(stack, "clean_id", lambda: "")())
        count = max(1, int(getattr(stack, "count", 1) or 1))
        self.builder.use_count = True
        self.builder.count = count
        self.builder.components = []
        for cid, value in dict(getattr(stack, "components", {}) or {}).items():
            # 面板里统一用短 ID（custom_name），与选择器、表格显示一致
            self.builder.add_component(short_id(cid).split(":", 1)[-1],
                                       component_value_snbt(value))
        if target.strip():
            self.builder.target = target.strip()
        self.reload_from_builder()
        try:
            display = stack.display()
        except Exception:                                   # pragma: no cover
            display = self.builder.item
        self._msg(f"已载入物品 {display} ×{count}"
                  f"（{len(self.builder.components)} 个组件）")
        return True

    def reload_data(self) -> None:
        clear_component_cache()
        self._comp_defs = component_defs()
        self._presets = [p for p in data_store().item_presets()
                         if isinstance(p, dict)]
        self.item_select.reload(data_store().items())
        self._refresh_suggestions()
        self._refresh_command()


# =========================================================================
# ExecutePanel
# =========================================================================

class ExecutePanel(QWidget):
    commandChanged = Signal(str)
    #: 面板内「复制命令」按钮成功复制（主窗口据此给可见反馈）
    commandCopied = Signal(str)
    #: 面板内「发送到输出区」按钮（与复制区分开，否则状态栏会说错话）
    commandSent = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.builder = ExecuteBuilder()
        self._current_index: int = -1
        self._editor: QWidget | None = None
        self.setAcceptDrops(True)
        self._build_ui()
        self._load_demo()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        self.badge = StatusBadge()
        v.addWidget(self.badge)

        tip = QLabel("从上到下依次叠加子命令，最后以 run 命令结束。")
        tip.setStyleSheet(_muted(tip))
        v.addWidget(tip)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(4)

        tools = QHBoxLayout()
        tools.setSpacing(4)
        btn_add = QPushButton("添加 ▾")
        btn_add.clicked.connect(self._on_add_node)
        btn_del = QPushButton("删除")
        btn_del.clicked.connect(self._on_delete_node)
        btn_up = QPushButton("↑")
        btn_up.setFixedWidth(32)
        btn_up.clicked.connect(lambda: self._move(-1))
        btn_down = QPushButton("↓")
        btn_down.setFixedWidth(32)
        btn_down.clicked.connect(lambda: self._move(1))
        tools.addWidget(btn_add)
        tools.addWidget(btn_del)
        tools.addStretch(1)
        tools.addWidget(btn_up)
        tools.addWidget(btn_down)
        lv.addLayout(tools)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.currentRowChanged.connect(self._on_row_changed)
        lv.addWidget(self.list, 1)
        splitter.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.editor_host = QWidget()
        self.editor_host_layout = QVBoxLayout(self.editor_host)
        self.editor_host_layout.setContentsMargins(6, 6, 6, 6)
        self.editor_host_layout.addStretch(1)
        scroll.setWidget(self.editor_host)
        rv.addWidget(scroll, 1)
        splitter.addWidget(right)

        splitter.setSizes([280, 480])
        v.addWidget(splitter, 1)

        run_box = QGroupBox("run 后的命令（不含前导 /）")
        run_layout = QHBoxLayout(run_box)
        self.run_line = QLineEdit("say hello")
        self.run_line.textChanged.connect(self._on_run_changed)
        run_layout.addWidget(self.run_line, 1)
        v.addWidget(run_box)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(f"color: {theme_colors(self)['error']};")
        v.addWidget(self.error_label)

        self.action_bar = PanelActionBar(self)
        self.action_bar.copied.connect(self.commandCopied.emit)
        self.action_bar.sent.connect(self.commandSent.emit)
        v.addWidget(self.action_bar)

    def _load_demo(self) -> None:
        self.builder.nodes = [
            ExecuteNode("at", default_params("at")),
            ExecuteNode("if", default_params("if")),
        ]
        self.builder.nodes[0].params["target"] = "@p"
        cond = self.builder.nodes[1].params["condition"]
        cond.clear()
        cond.update({"type": "block", "x": "~", "y": "~-1", "z": "~",
                     "block": "minecraft:stone"})
        self.run_line.setText("say hello")
        self._refresh_list()
        if self.builder.nodes:
            self.list.setCurrentRow(0)
        self._refresh_command()
        self._demo_command = self.current_command()

    def is_demo(self) -> bool:
        """当前内容是否还是初始示例（用户一个字都没改）。"""
        demo = getattr(self, "_demo_command", None)
        if not demo:
            return False
        return self.current_command().strip() == demo.strip()

    def _refresh_list(self) -> None:
        self.list.blockSignals(True)
        prev = self._current_index
        self.list.clear()
        for i, n in enumerate(self.builder.nodes):
            self.list.addItem(QListWidgetItem(f"{i + 1}. {n.summary()}"))
        self.list.blockSignals(False)
        if 0 <= prev < len(self.builder.nodes):
            self.list.setCurrentRow(prev)
        elif self.builder.nodes:
            self.list.setCurrentRow(0)
        else:
            self._current_index = -1
            self._rebuild_editor()

    def _on_row_changed(self, row: int) -> None:
        self._current_index = row
        self._rebuild_editor()

    def _on_add_node(self) -> None:
        menu = QMenu(self)
        for key, label in NODE_TYPES:
            act = menu.addAction(label)
            act.setData(key)
        chosen = menu.exec(self.mapToGlobal(self.rect().topLeft()))
        if not chosen:
            return
        t = chosen.data()
        self.builder.nodes.append(ExecuteNode(t, default_params(t)))
        self._refresh_list()
        self.list.setCurrentRow(len(self.builder.nodes) - 1)
        self._refresh_command()

    def _on_delete_node(self) -> None:
        i = self._current_index
        if i < 0 or i >= len(self.builder.nodes):
            return
        del self.builder.nodes[i]
        self._current_index = min(i, len(self.builder.nodes) - 1)
        self._refresh_list()
        self._refresh_command()

    def _move(self, delta: int) -> None:
        i = self._current_index
        j = i + delta
        if i < 0 or j < 0 or j >= len(self.builder.nodes):
            return
        self.builder.nodes[i], self.builder.nodes[j] = (
            self.builder.nodes[j], self.builder.nodes[i])
        self._current_index = j
        self._refresh_list()
        self.list.setCurrentRow(j)
        self._refresh_command()

    def _rebuild_editor(self) -> None:
        if self._editor is not None:
            self._editor.setParent(None)
            self._editor.deleteLater()
            self._editor = None
        if self._current_index < 0 or self._current_index >= len(self.builder.nodes):
            label = QLabel("请从左侧选择或添加一个子命令。")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet(_muted(label))
            self.editor_host_layout.insertWidget(0, label)
            self._editor = label
            return
        node = self.builder.nodes[self._current_index]
        editor = self._make_editor(node)
        self.editor_host_layout.insertWidget(0, editor)
        self._editor = editor

    def _make_editor(self, node: ExecuteNode) -> QWidget:
        wrap = QGroupBox(f"{node.type}  子命令")
        fl = QFormLayout(wrap)
        fl.setContentsMargins(8, 8, 8, 8)

        t, p = node.type, node.params

        def on_change() -> None:
            self._refresh_list_keep()
            self._refresh_command()

        def line_widget(key, default="~"):
            le = QLineEdit(str(p.get(key, default)))
            le.textChanged.connect(
                lambda v, k=key: (p.__setitem__(k, v), on_change()))
            return le

        def target_row(value, setter):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            le = QLineEdit(value)
            le.textChanged.connect(lambda v: (setter(v), on_change()))
            h.addWidget(le, 1)
            btn = QPushButton("编辑…")
            btn.setFixedWidth(72)
            btn.clicked.connect(lambda: self._open_target(le))
            h.addWidget(btn)
            return w

        if t in ("as", "at"):
            fl.addRow("目标", target_row(p.get("target", "@s"),
                       lambda v: p.__setitem__("target", v)))
        elif t == "positioned":
            mode = QComboBox()
            mode.addItem("坐标", "coord")
            mode.addItem("使用目标", "as")
            idx = mode.findData(p.get("mode", "coord"))
            if idx >= 0:
                mode.setCurrentIndex(idx)
            mode.currentIndexChanged.connect(
                lambda i: (p.__setitem__("mode", mode.itemData(i)), on_change()))
            fl.addRow("模式", mode)
            fl.addRow("X", line_widget("x"))
            fl.addRow("Y", line_widget("y"))
            fl.addRow("Z", line_widget("z"))
            fl.addRow("目标（as 模式）", target_row(
                p.get("target", "@s"),
                lambda v: p.__setitem__("target", v)))
        elif t == "rotated":
            mode = QComboBox()
            mode.addItem("坐标", "coord")
            mode.addItem("使用目标", "as")
            idx = mode.findData(p.get("mode", "coord"))
            if idx >= 0:
                mode.setCurrentIndex(idx)
            mode.currentIndexChanged.connect(
                lambda i: (p.__setitem__("mode", mode.itemData(i)), on_change()))
            fl.addRow("模式", mode)
            fl.addRow("Yaw", line_widget("yaw"))
            fl.addRow("Pitch", line_widget("pitch"))
            fl.addRow("目标（as 模式）", target_row(
                p.get("target", "@s"),
                lambda v: p.__setitem__("target", v)))
        elif t == "facing":
            mode = QComboBox()
            mode.addItem("坐标", "coord")
            mode.addItem("面向实体", "entity")
            idx = mode.findData(p.get("mode", "coord"))
            if idx >= 0:
                mode.setCurrentIndex(idx)
            mode.currentIndexChanged.connect(
                lambda i: (p.__setitem__("mode", mode.itemData(i)), on_change()))
            fl.addRow("模式", mode)
            fl.addRow("X", line_widget("x"))
            fl.addRow("Y", line_widget("y"))
            fl.addRow("Z", line_widget("z"))
            fl.addRow("目标（entity 模式）", target_row(
                p.get("target", "@s"),
                lambda v: p.__setitem__("target", v)))
            anchor = QComboBox()
            anchor.addItems(["eyes", "feet"])
            anchor.setCurrentText(p.get("anchor", "eyes"))
            anchor.currentTextChanged.connect(
                lambda v: (p.__setitem__("anchor", v), on_change()))
            fl.addRow("锚点", anchor)
        elif t == "align":
            axes = QLineEdit(p.get("axes", "xyz"))
            axes.setPlaceholderText("例如 x / y / z / xy / xyz")
            axes.textChanged.connect(
                lambda v: (p.__setitem__("axes", v), on_change()))
            fl.addRow("轴", axes)
        elif t == "anchored":
            anchor = QComboBox()
            anchor.addItems(["eyes", "feet"])
            anchor.setCurrentText(p.get("anchor", "eyes"))
            anchor.currentTextChanged.connect(
                lambda v: (p.__setitem__("anchor", v), on_change()))
            fl.addRow("锚点", anchor)
        elif t == "in":
            le = QLineEdit(p.get("dimension", "minecraft:overworld"))
            le.textChanged.connect(
                lambda v: (p.__setitem__("dimension", v), on_change()))
            fl.addRow("维度", le)
        elif t == "on":
            combo = QComboBox()
            combo.setEditable(True)
            for r in ("attacker", "controller", "leasher", "origin",
                      "owner", "passengers", "target", "vehicle"):
                combo.addItem(r)
            combo.setCurrentText(p.get("relation", "attacker"))
            combo.currentTextChanged.connect(
                lambda v: (p.__setitem__("relation", v), on_change()))
            fl.addRow("关系", combo)
        elif t in ("if", "unless"):
            self._build_condition_editor(
                fl, node, on_change, target_row, line_widget)
        elif t == "store":
            self._build_store_editor(
                fl, node, on_change, target_row, line_widget)
        return wrap

    def _open_target(self, line: QLineEdit) -> None:
        dlg = TargetSelectorDialog(line.text(), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            line.setText(dlg.value())

    def _build_condition_editor(self, fl, node, on_change, target_row,
                                 line_widget):
        cond = node.params.setdefault("condition", {"type": "entity"})

        type_combo = QComboBox()
        for key, label in CONDITION_TYPES:
            type_combo.addItem(label, key)
        idx = type_combo.findData(cond.get("type", "entity"))
        if idx >= 0:
            type_combo.setCurrentIndex(idx)
        fl.addRow("条件类型", type_combo)

        fields_widget = QWidget()
        fields_layout = QFormLayout(fields_widget)
        fields_layout.setContentsMargins(0, 0, 0, 0)
        fl.addRow(fields_widget)

        def clear_fields():
            while fields_layout.count():
                item = fields_layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()

        def add_line(key, label, default=""):
            le = QLineEdit(str(cond.get(key, default)))
            le.textChanged.connect(
                lambda v, k=key: (cond.__setitem__(k, v), on_change()))
            fields_layout.addRow(label, le)

        def rebuild():
            clear_fields()
            t = cond.get("type", "entity")
            if t == "entity":
                fields_layout.addRow("目标", target_row(
                    cond.get("target", "@s"),
                    lambda v: cond.__setitem__("target", v)))
            elif t == "block":
                for k, lbl in (("x", "X"), ("y", "Y"), ("z", "Z"),
                                ("block", "方块 ID")):
                    add_line(k, lbl, "~" if k != "block" else "minecraft:stone")
            elif t == "blocks":
                for k, lbl in (("sx", "起点X"), ("sy", "起点Y"), ("sz", "起点Z"),
                                ("ex", "终点X"), ("ey", "终点Y"), ("ez", "终点Z"),
                                ("dx", "目标X"), ("dy", "目标Y"), ("dz", "目标Z")):
                    add_line(k, lbl, "~")
                combo = QComboBox()
                combo.addItems(["all", "masked"])
                combo.setCurrentText(cond.get("mode", "all"))
                combo.currentTextChanged.connect(
                    lambda v: (cond.__setitem__("mode", v), on_change()))
                fields_layout.addRow("模式", combo)
            elif t == "data_block":
                for k, lbl in (("x", "X"), ("y", "Y"), ("z", "Z"),
                                ("path", "NBT 路径")):
                    add_line(k, lbl, "~" if k != "path" else "")
            elif t == "data_entity":
                fields_layout.addRow("目标", target_row(
                    cond.get("target", "@s"),
                    lambda v: cond.__setitem__("target", v)))
                add_line("path", "NBT 路径", "")
            elif t == "predicate":
                add_line("id", "谓词 ID", "minecraft:example")
            elif t == "score":
                fields_layout.addRow("目标", target_row(
                    cond.get("target", "@s"),
                    lambda v: cond.__setitem__("target", v)))
                add_line("objective", "目标计分板", "objective")
                op = QComboBox()
                for o in ("<", "<=", "=", ">=", ">", "><"):
                    op.addItem(o)
                op.setCurrentText(cond.get("op", "="))
                op.currentTextChanged.connect(
                    lambda v: (cond.__setitem__("op", v), on_change()))
                fields_layout.addRow("运算符", op)
                fields_layout.addRow("来源", target_row(
                    cond.get("source", "@s"),
                    lambda v: cond.__setitem__("source", v)))
                add_line("source_objective", "来源计分板", "objective")
            elif t == "function":
                add_line("name", "函数 ID", "minecraft:example")
            elif t == "items_block":
                for k, lbl in (("x", "X"), ("y", "Y"), ("z", "Z"),
                                ("slot", "槽位"), ("data", "物品条件 SNBT")):
                    add_line(k, lbl,
                             "container.0" if k == "slot"
                             else ("{}" if k == "data" else "~"))
            elif t == "items_entity":
                fields_layout.addRow("目标", target_row(
                    cond.get("target", "@s"),
                    lambda v: cond.__setitem__("target", v)))
                add_line("slot", "槽位", "container.0")
                add_line("data", "物品条件 SNBT", "{}")

        def on_type_changed(index: int) -> None:
            new_type = type_combo.itemData(index)
            cond.clear()
            cond["type"] = new_type
            if new_type == "entity":
                cond["target"] = "@s"
            elif new_type == "block":
                cond.update({"x": "~", "y": "~", "z": "~",
                             "block": "minecraft:stone"})
            elif new_type == "blocks":
                cond.update(dict.fromkeys(
                    ("sx", "sy", "sz", "ex", "ey", "ez",
                     "dx", "dy", "dz"), "~"))
                cond["mode"] = "all"
            elif new_type == "data_block":
                cond.update({"x": "~", "y": "~", "z": "~", "path": ""})
            elif new_type == "data_entity":
                cond.update({"target": "@s", "path": ""})
            elif new_type == "predicate":
                cond["id"] = "minecraft:example"
            elif new_type == "score":
                cond.update({"target": "@s", "objective": "objective",
                             "op": "=", "source": "@s",
                             "source_objective": "objective"})
            elif new_type == "function":
                cond["name"] = "minecraft:example"
            elif new_type == "items_block":
                cond.update({"x": "~", "y": "~", "z": "~",
                             "slot": "container.0", "data": "{}"})
            elif new_type == "items_entity":
                cond.update({"target": "@s", "slot": "container.0",
                             "data": "{}"})
            rebuild()
            on_change()

        type_combo.currentIndexChanged.connect(on_type_changed)
        rebuild()

    def _build_store_editor(self, fl, node, on_change, target_row,
                             line_widget):
        p = node.params
        result_combo = QComboBox()
        result_combo.addItems(["result", "success"])
        result_combo.setCurrentText(p.get("result", "result"))
        result_combo.currentTextChanged.connect(
            lambda v: (p.__setitem__("result", v), on_change()))
        fl.addRow("结果类型", result_combo)

        target_combo = QComboBox()
        for key, label in STORE_TARGETS:
            target_combo.addItem(label, key)
        idx = target_combo.findData(p.get("target_type", "score"))
        if idx >= 0:
            target_combo.setCurrentIndex(idx)
        fl.addRow("存储目标", target_combo)

        fields_widget = QWidget()
        fields_layout = QFormLayout(fields_widget)
        fields_layout.setContentsMargins(0, 0, 0, 0)
        fl.addRow(fields_widget)

        def clear_fields():
            while fields_layout.count():
                item = fields_layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()

        def add_line(key, label, default=""):
            le = QLineEdit(str(p.get(key, default)))
            le.textChanged.connect(
                lambda v, k=key: (p.__setitem__(k, v), on_change()))
            fields_layout.addRow(label, le)

        def rebuild():
            clear_fields()
            tt = p.get("target_type", "score")
            if tt == "score":
                fields_layout.addRow("目标", target_row(
                    p.get("target", "@s"),
                    lambda v: p.__setitem__("target", v)))
                add_line("objective", "计分板", "objective")
            elif tt == "entity":
                fields_layout.addRow("目标", target_row(
                    p.get("target", "@s"),
                    lambda v: p.__setitem__("target", v)))
                add_line("path", "NBT 路径", "foo")
                add_line("data_type", "数据类型", "int")
                add_line("scale", "缩放", "1.0")
            elif tt == "block":
                for k, lbl in (("x", "X"), ("y", "Y"), ("z", "Z"),
                                ("path", "NBT 路径"),
                                ("data_type", "数据类型"),
                                ("scale", "缩放")):
                    add_line(k, lbl,
                             "foo" if k == "path"
                             else ("int" if k == "data_type"
                             else ("1.0" if k == "scale" else "~")))
            elif tt == "bossbar":
                add_line("id", "Boss 栏 ID", "minecraft:custom:name")
                combo = QComboBox()
                combo.addItems(["value", "max"])
                combo.setCurrentText(p.get("value", "value"))
                combo.currentTextChanged.connect(
                    lambda v: (p.__setitem__("value", v), on_change()))
                fields_layout.addRow("字段", combo)

        def on_target_changed(index: int) -> None:
            new_tt = target_combo.itemData(index)
            p["target_type"] = new_tt
            p.setdefault("target", "@s")
            p.setdefault("objective", "objective")
            p.setdefault("path", "foo")
            p.setdefault("data_type", "int")
            p.setdefault("scale", "1.0")
            p.setdefault("id", "minecraft:custom:name")
            p.setdefault("value", "value")
            rebuild()
            on_change()

        target_combo.currentIndexChanged.connect(on_target_changed)
        rebuild()

    def _refresh_list_keep(self) -> None:
        cur = self._current_index
        self.list.blockSignals(True)
        self.list.clear()
        for i, n in enumerate(self.builder.nodes):
            self.list.addItem(QListWidgetItem(f"{i + 1}. {n.summary()}"))
        self.list.blockSignals(False)
        if 0 <= cur < self.list.count():
            self.list.setCurrentRow(cur)

    def _on_run_changed(self, text: str) -> None:
        self.builder.run_command = text
        self._refresh_command()

    def _refresh_command(self) -> None:
        errors = self.builder.validate(BuildContext())
        text = "；".join(str(e) for e in errors)
        self.error_label.setText(text)
        self.error_label.setVisible(bool(text))
        self.badge.update_state(
            f"/execute　{self.builder.status_summary()}",
            _eval_issue_level(errors))
        self.commandChanged.emit(self.builder.build(BuildContext()))

    def reload_from_builder(self) -> None:
        self.run_line.blockSignals(True)
        self.run_line.setText(self.builder.run_command or "say hello")
        self.run_line.blockSignals(False)
        self._current_index = 0 if self.builder.nodes else -1
        self.list.blockSignals(True)
        self.list.clear()
        for i, n in enumerate(self.builder.nodes):
            self.list.addItem(QListWidgetItem(f"{i + 1}. {n.summary()}"))
        self.list.blockSignals(False)
        if self.builder.nodes:
            self.list.setCurrentRow(0)
        else:
            self._rebuild_editor()
        self._refresh_command()

    # ---- 拖拽 ----
    def dragEnterEvent(self, event) -> None:
        md = event.mimeData()
        if md.hasFormat(FRAGMENT_MIME) or md.hasFormat(MIME_NBT):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        md = event.mimeData()
        if md.hasFormat(FRAGMENT_MIME) or md.hasFormat(MIME_NBT):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        # NBT 拖入 → 作为 if data entity 条件
        if event.mimeData().hasFormat(MIME_NBT):
            payload = read_mime(event.mimeData(), MIME_NBT)
            if payload:
                node = ExecuteNode("if", {
                    "condition": {"type": "data_entity", "target": "@s",
                                  "path": payload.get("snbt", "{}")}})
                self.builder.nodes.append(node)
                self._refresh_list()
                self._refresh_command()
                event.acceptProposedAction()
                return

        if not event.mimeData().hasFormat(FRAGMENT_MIME):
            super().dropEvent(event)
            return

        try:
            data = json.loads(
                bytes(event.mimeData().data(FRAGMENT_MIME)).decode("utf-8"))
        except Exception:
            return

        kind = str(data.get("kind", ""))
        payload = data.get("payload") or {}

        node: ExecuteNode | None = None
        if kind == "execute_node":
            try:
                node = ExecuteNode(
                    type=str(payload.get("type", "as")),
                    params=dict(payload.get("params") or {}))
            except Exception:
                node = None
        elif kind == "command":
            text = str(payload.get("text", "")).strip()
            if text:
                self.run_line.setText(text)
                event.acceptProposedAction()
                return
        elif kind == "nbt":
            snbt = str(payload.get("snbt", "")).strip()
            if snbt:
                node = ExecuteNode("if", {
                    "condition": {"type": "data_entity", "target": "@s",
                                  "path": snbt}})

        if node is None:
            event.ignore()
            return

        insert_at = len(self.builder.nodes)
        if 0 <= self._current_index < len(self.builder.nodes):
            insert_at = self._current_index + 1
        self.builder.nodes.insert(insert_at, node)
        self._refresh_list()
        self.list.setCurrentRow(insert_at)
        self._refresh_command()
        event.acceptProposedAction()

    def current_command(self) -> str:
        return self.builder.build(BuildContext())

    # ---- 会话草稿 ----

    def dump_state(self) -> dict:
        return {
            "nodes": [{"type": n.type, "params": n.params}
                      for n in self.builder.nodes],
            "run_command": self.builder.run_command,
        }

    def restore_state(self, data: dict | None) -> bool:
        if not isinstance(data, dict) or not data:
            return False
        nodes: list[ExecuteNode] = []
        for raw in data.get("nodes") or []:
            if not isinstance(raw, dict):
                continue
            params = raw.get("params")
            nodes.append(ExecuteNode(str(raw.get("type", "as")),
                                     dict(params) if isinstance(params, dict)
                                     else {}))
        self.builder.nodes = nodes
        self.builder.run_command = str(data.get("run_command") or "say hi")
        self.reload_from_builder()
        return True


# =========================================================================
# SchemaPanel
# =========================================================================

class _NbtEditDialog(QDialog):
    def __init__(self, initial: str, parent=None, title: str = "编辑 NBT"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 520)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        self.editor = NbtTreeEditor()
        if initial.strip():
            self.editor.set_snbt(initial)
        v.addWidget(self.editor, 1)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def value(self) -> str:
        return self.editor.to_snbt(pretty=False)


class _IdPickerDialog(QDialog):
    def __init__(self, entries, initial: str, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(540, 580)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        self.picker = IdPicker(entries, title)
        self.picker.set_id(initial)
        v.addWidget(self.picker, 1)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def value(self) -> str:
        return self.picker.get_id()


class SchemaPanel(QWidget):
    commandChanged = Signal(str)
    #: 面板内「复制命令」按钮成功复制（主窗口据此给可见反馈）
    commandCopied = Signal(str)
    #: 面板内「发送到输出区」按钮（与复制区分开，否则状态栏会说错话）
    commandSent = Signal(str)

    def __init__(self, builder: SchemaBuilder, parent=None):
        super().__init__(parent)
        self.builder = builder
        self._updating = False
        self._visiting = False
        self._editors: dict[str, tuple[QWidget, object, object]] = {}
        self._build_ui()
        self._refresh_command()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        self.badge = StatusBadge()
        v.addWidget(self.badge)

        tip = QLabel(
            f"按参数描述自动生成的表单 — /{self.builder.command_name}。"
            "带 * 的是必填项；带 ⚙ 的参数会随上面所选项自动显隐。"
            "命令实时预览到右侧输出区。")
        tip.setStyleSheet(_muted(tip))
        tip.setWordWrap(True)
        v.addWidget(tip)

        # 错误提示放在表单**上方**：旧版放在 addStretch 之后的最底部，
        # 用户填完看不到「哪里错了」，也不知道是自己填错还是命令不支持
        self.error_label = QLabel("")
        self.error_label.setStyleSheet(f"color: {theme_colors(self)['error']};")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        v.addWidget(self.error_label)

        box = QGroupBox(
            self.builder.display_name or f"/{self.builder.command_name}")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form.setContentsMargins(8, 8, 8, 8)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)

        for spec in self.builder.args():
            editor, getter, setter = self._make_editor(spec)
            label = spec.label or spec.key
            if spec.required:
                label += " *"
            if spec.when:
                label += " ⚙"
            label_widget = QLabel(label)
            self._editors[spec.key] = (editor, getter, setter, label_widget)
            form.addRow(label_widget, editor)
        if not self.builder.args():
            form.addRow(QLabel("该命令无参数。"))
        v.addWidget(box)
        v.addStretch(1)

        self.action_bar = PanelActionBar(self)
        self.action_bar.copied.connect(self.commandCopied.emit)
        self.action_bar.sent.connect(self.commandSent.emit)
        v.addWidget(self.action_bar)

        self._apply_visibility()

    def _apply_visibility(self) -> None:
        """按 when 条件显隐参数行（带 ⚙ 的参数只在特定取值下出现）。"""
        if not self._editors or self._visiting:
            return
        self._visiting = True
        try:
            # 先用当前控件值同步一次 builder 取值，条件才基于最新 action 判断
            self._sync_builder()
            for spec in self.builder.args():
                entry = self._editors.get(spec.key)
                if entry is None or not spec.when:
                    continue
                editor, _getter, _setter, label_widget = entry
                visible = spec.visible(self.builder.values)
                editor.setVisible(visible)
                label_widget.setVisible(visible)
        finally:
            self._visiting = False

    def _make_editor(self, spec: ArgSpec):
        from PySide6.QtGui import QIntValidator, QDoubleValidator
        kind = spec.kind
        default = spec.default or ""

        # ✅ P2-1：坐标三段输入
        if kind == "coord":
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)

            parts = (default or "~ ~ ~").split()
            while len(parts) < 3:
                parts.append("~")

            ex = QLineEdit(parts[0])
            ey = QLineEdit(parts[1])
            ez = QLineEdit(parts[2])
            for w, lbl in ((ex, "X"), (ey, "Y"), (ez, "Z")):
                w.setFixedWidth(72)
                w.textChanged.connect(self._on_field_changed)
                h.addWidget(QLabel(lbl))
                h.addWidget(w)
            h.addStretch(1)

            def get_coord() -> str:
                return (f"{ex.text().strip() or '~'} "
                        f"{ey.text().strip() or '~'} "
                        f"{ez.text().strip() or '~'}")

            def set_coord(v: str) -> None:
                ps = (v or "~ ~ ~").split()
                while len(ps) < 3:
                    ps.append("~")
                ex.setText(ps[0])
                ey.setText(ps[1])
                ez.setText(ps[2])

            return row, get_coord, set_coord

        if kind == "enum":
            combo = QComboBox()
            combo.setEditable(True)
            for opt in spec.options:
                combo.addItem(opt)
            idx = combo.findText(default)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            elif default:
                combo.setEditText(default)
            combo.currentTextChanged.connect(self._on_field_changed)

            def get_enum(): return combo.currentText().strip()

            def set_enum(v: str):
                i2 = combo.findText(v or "")
                if i2 >= 0:
                    combo.setCurrentIndex(i2)
                else:
                    combo.setEditText(v or "")

            return combo, get_enum, set_enum

        if kind == "nbt":
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            le = QLineEdit(default)
            le.setPlaceholderText(spec.hint or 'SNBT，例如 {"text":"hi"}')
            le.textChanged.connect(self._on_field_changed)
            h.addWidget(le, 1)
            btn = QPushButton("编辑…")
            btn.setFixedWidth(72)
            btn.clicked.connect(lambda: self._open_nbt(spec, le))
            h.addWidget(btn)

            def get_nbt(): return le.text().strip()

            def set_nbt(v: str): le.setText(v or "")

            return row, get_nbt, set_nbt

        if kind in ("entity", "item", "block", "target"):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            le = QLineEdit(default)
            if spec.hint:
                le.setPlaceholderText(spec.hint)
            le.textChanged.connect(self._on_field_changed)
            h.addWidget(le, 1)
            btn = QPushButton("浏览…")
            btn.setFixedWidth(72)
            btn.clicked.connect(lambda: self._open_picker(spec, le))
            h.addWidget(btn)

            def get_pick(): return le.text().strip()

            def set_pick(v: str): le.setText(v or "")

            return row, get_pick, set_pick

        le = QLineEdit(default)
        if kind == "int":
            le.setValidator(QIntValidator())
            if spec.hint:
                le.setPlaceholderText(spec.hint)
        elif kind == "float":
            le.setValidator(QDoubleValidator())
            if spec.hint:
                le.setPlaceholderText(spec.hint)
        elif spec.hint:
            le.setPlaceholderText(spec.hint)
        le.textChanged.connect(self._on_field_changed)

        def get_text(): return le.text().strip()

        def set_text(v: str): le.setText(v or "")

        return le, get_text, set_text

    def _on_field_changed(self, *args) -> None:
        if self._updating:
            return
        self._apply_visibility()
        self._refresh_command()

    def _sync_builder(self) -> None:
        for spec in self.builder.args():
            entry = self._editors.get(spec.key)
            if entry is None:
                continue
            getter = entry[1]
            try:
                self.builder.values[spec.key] = getter()
            except Exception:
                pass

    def _refresh_command(self) -> None:
        errors = self.builder.validate(BuildContext())
        text = "；".join(str(e) for e in errors)
        self.error_label.setText(text)
        self.error_label.setVisible(bool(text))
        self.badge.update_state(
            f"/{self.builder.command_name}　{self.builder.status_summary()}",
            _eval_issue_level(errors))
        self.commandChanged.emit(self.builder.build(BuildContext()))

    def reload_from_builder(self) -> None:
        self._updating = True
        try:
            for spec in self.builder.args():
                entry = self._editors.get(spec.key)
                if entry is None:
                    continue
                setter = entry[2]
                setter(self.builder.values.get(spec.key, ""))
        finally:
            self._updating = False
        self._apply_visibility()
        self._refresh_command()

    def current_command(self) -> str:
        self._sync_builder()
        return self.builder.build(BuildContext())

    def _open_nbt(self, spec, line) -> None:
        dlg = _NbtEditDialog(line.text(), self,
                              f"编辑 {spec.label or spec.key}")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            line.setText(dlg.value())

    def _open_picker(self, spec, line) -> None:
        kind = spec.kind
        if kind == "target":
            dlg = TargetSelectorDialog(line.text(), self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                line.setText(dlg.value())
            return
        if kind == "entity":
            entries = data_store().entities()
            title = "选择实体 ID"
        elif kind == "item":
            entries = data_store().items()
            title = "选择物品 ID"
        else:
            QMessageBox.information(self, "提示",
                "该字段暂未内置选择器，请直接手动输入 ID（如 minecraft:stone）。")
            return
        if not entries:
            QMessageBox.information(self, "提示", "内置数据为空。")
            return
        dlg = _IdPickerDialog(entries, line.text(), title, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            line.setText(dlg.value())


# =========================================================================
# BatchPanel
# =========================================================================

class BatchPanel(QWidget):
    commandActivated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._schemas = self._collect_schemas()
        self._args: list[ArgSpec] = []
        self._current_command = ""
        self._updating = False
        self._rows: list[dict[str, str]] = []
        self._drafts: dict[str, list[dict[str, str]]] = {}

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(300)
        self._preview_timer.timeout.connect(self._refresh_preview_now)

        self._build_ui()
        self._reload_command_list()

    @staticmethod
    def _collect_schemas() -> dict[str, dict]:
        load_schemas()
        result: dict[str, dict] = {}
        for cls in iter_builders():
            if not issubclass(cls, SchemaBuilder) or cls is SchemaBuilder:
                continue
            schema = getattr(cls, "schema", None)
            if isinstance(schema, dict) and schema.get("command"):
                result[cls.command_name] = schema
        # /give 是手写 Builder，这里补一份 schema，让批量生成也能用它
        if "give" not in result:
            result["give"] = GIVE_BATCH_SCHEMA
        return result

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QLabel("目标命令"))
        self.command_combo = QComboBox()
        self.command_combo.setMinimumWidth(260)
        self.command_combo.currentIndexChanged.connect(
            self._on_command_changed)
        top.addWidget(self.command_combo, 1)
        v.addLayout(top)

        tip = QLabel("每行填写一组参数，实时生成命令列表。"
                     "支持 CSV 导入与剪贴板粘贴（首行可作表头）。")
        tip.setStyleSheet(_muted(tip))
        tip.setWordWrap(True)
        v.addWidget(tip)

        tools = QHBoxLayout()
        tools.setSpacing(4)
        for label, cb in (("添加行", self._on_add_row),
                          ("删除行", self._on_delete_row),
                          ("清空", self._on_clear),
                          ("导入 CSV…", self._on_import_csv),
                          ("粘贴表格", self._on_paste_table),
                          ("填充示例", self._on_fill_sample)):
            b = QPushButton(label)
            b.clicked.connect(cb)
            tools.addWidget(b)
        tools.addStretch(1)
        for label, cb in (("导出 CSV…", self._on_export_csv),
                          ("导出 .mcfunction…", self._on_export_mcf),
                          ("全部 → 输出", self._on_send_all_to_output)):
            b = QPushButton(label)
            b.clicked.connect(cb)
            tools.addWidget(b)
        v.addLayout(tools)

        # ✅ F-05：筛选行 + 单元格取值辅助
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("筛选"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("例如 amplifier >= 2")
        self.filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_edit, 1)

        self.value_combo = QComboBox()
        self.value_combo.setMinimumWidth(180)
        self.value_combo.setToolTip("把历史记录里用过的取值填到当前单元格")
        self.value_combo.addItem("历史取值…", "")
        self.value_combo.activated.connect(self._on_pick_history_value)
        filter_row.addWidget(self.value_combo)

        self.btn_pick = QPushButton("从列表选择…")
        self.btn_pick.setToolTip("为当前单元格打开实体 / 物品 / 方块选择器")
        self.btn_pick.clicked.connect(self._on_pick_for_cell)
        filter_row.addWidget(self.btn_pick)
        v.addLayout(filter_row)

        self._stack = QStackedWidget()
        self._empty_page = EmptyState(
            "批量生成：先在上方选一条命令，表格会自动列出它的参数列，"
            "一行一条命令。\n\n"
            "填好后点右侧「全部 → 输出」，命令会整批送进输出区，"
            "也可以导出 CSV 或 .mcfunction。",
            [("用 /give 试试", self._select_give_command),
             ("用 /setblock 试试", self._select_first_command)],
        )

        normal_page = QWidget()
        nv = QVBoxLayout(normal_page)
        nv.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Vertical)

        table_box = QGroupBox("参数表格")
        tb = QVBoxLayout(table_box)
        tb.setContentsMargins(6, 6, 6, 6)

        self.table = QTableWidget(0, 0)
        self.table.setAlternatingRowColors(True)
        # 排序会打乱"行号 ↔ 数据行"的对应关系（编辑时行会跳走），
        # 因此关闭自动排序，导入 CSV 时按原顺序展示。
        self.table.setSortingEnabled(False)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)
        self.table.itemChanged.connect(self._on_table_item_changed)
        tb.addWidget(self.table, 1)
        splitter.addWidget(table_box)

        preview_box = QGroupBox("预览")
        pv = QVBoxLayout(preview_box)
        pv.setContentsMargins(6, 6, 6, 6)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont("Consolas", 10))
        pv.addWidget(self.preview, 1)
        self.count_label = QLabel("共 0 条命令")
        self.count_label.setStyleSheet(_muted(self.count_label))
        pv.addWidget(self.count_label, 0)
        splitter.addWidget(preview_box)
        splitter.setSizes([380, 260])

        nv.addWidget(splitter, 1)

        self._stack.addWidget(self._empty_page)
        self._stack.addWidget(normal_page)
        self._normal_page = normal_page
        v.addWidget(self._stack, 1)

    def _select_first_command(self) -> None:
        """空状态按钮：优先选 /setblock，找不到就选第一条真实命令。"""
        idx = self.command_combo.findData("setblock")
        self.command_combo.setCurrentIndex(idx if idx >= 0 else 1)

    def _select_give_command(self) -> None:
        """空状态按钮：选 /give（最常用的一条批量命令）。"""
        idx = self.command_combo.findData("give")
        if idx >= 0:
            self.command_combo.setCurrentIndex(idx)

    # ---- 取值辅助（批量面板增强） ----

    def _current_cell(self):
        """返回当前选中的 (行, 列, 参数规格)；没有合法单元格时返回 None。"""
        item = self.table.currentItem()
        if item is None:
            return None
        row, col = item.row(), item.column()
        if not (0 <= col < len(self._args)):
            return None
        return row, col, self._args[col]

    def _on_pick_history_value(self, _index: int) -> None:
        value = self.value_combo.currentData() or ""
        self.value_combo.setCurrentIndex(0)
        if not value:
            return
        cell = self._current_cell()
        if cell is None:
            _notify(self, "请先在表格里选中一个单元格。")
            return
        row, col, _spec = cell
        item = self.table.item(row, col)
        if item is not None:
            item.setText(value)

    def _on_pick_for_cell(self) -> None:
        cell = self._current_cell()
        if cell is None:
            _notify(self, "请先在表格里选中一个单元格。")
            return
        row, col, spec = cell
        from app.core.ids import ALIASES
        kind = spec.kind
        entries: list[dict] = []
        title = ""
        if kind == "entity":
            entries, title = data_store().entities(), "选择实体 ID"
        elif kind == "item":
            entries, title = data_store().items(), "选择物品 ID"
        elif kind == "block":
            entries, title = data_store().blocks(), "选择方块 ID"
        elif kind == "target":
            item = self.table.item(row, col)
            dlg = TargetSelectorDialog(item.text() if item else "@p", self)
            if dlg.exec() == QDialog.DialogCode.Accepted and item is not None:
                item.setText(dlg.value())
            return
        elif kind == "enum" and spec.options:
            entries = [{"id": opt, "display": opt, "category": "可选值"}
                       for opt in spec.options]
            title = f"选择 {spec.label or spec.key}"
        else:
            # 其他类型：给出中文别名速查，避免用户无从下手
            rows = [{"id": v, "display": k, "category": "中文别名"}
                    for k, v in sorted(ALIASES.items())][:200]
            entries, title = rows, "中文别名速查（仅供参考）"

        if not entries:
            QMessageBox.information(self, "提示", "内置数据为空。")
            return
        item = self.table.item(row, col)
        dlg = _IdPickerDialog(entries, item.text() if item else "", title, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and item is not None:
            item.setText(dlg.value())

    def _refresh_value_combo(self) -> None:
        """把历史里用过的取值填进下拉框（数据来源于 SQLite 历史）。"""
        self.value_combo.blockSignals(True)
        try:
            self.value_combo.clear()
            self.value_combo.addItem("历史取值…", "")
            if not self._current_command:
                return
            store = HistoryStore()
            seen: set[str] = set()
            for field_index, spec in enumerate(self._args, start=1):
                if spec.kind not in ("text", "item", "block", "entity", "target"):
                    continue
                try:
                    values = store.recent_values(self._current_command, field_index)
                except Exception:
                    continue
                for value in values:
                    if value and value not in seen:
                        seen.add(value)
                        self.value_combo.addItem(value, value)
                    if self.value_combo.count() > 40:
                        return
        finally:
            self.value_combo.blockSignals(False)

    def _on_fill_sample(self) -> None:
        """填一行"有内容的示例值"，方便直接改成自己的数据。"""
        if not self._args:
            return
        row = self._example_row()
        self._rows = [row] + [{a.key: a.default for a in self._args}
                              for _ in range(2)]
        self._drafts.pop(self._current_command, None)
        self._rebuild_table()
        self._mark_rows_explicit()

    def _example_row(self) -> dict[str, str]:
        """按参数类型给出一组可用的示例取值。"""
        st = data_store()
        sample: dict[str, str] = {}
        for a in self._args:
            if a.default:
                sample[a.key] = a.default
                continue
            if a.kind == "target":
                sample[a.key] = "@a"
            elif a.kind == "entity":
                entries = st.entities()
                sample[a.key] = entries[0]["id"] if entries else "minecraft:zombie"
            elif a.kind == "item":
                entries = st.items()
                sample[a.key] = entries[0]["id"] if entries else "minecraft:stone"
            elif a.kind == "block":
                entries = st.blocks()
                sample[a.key] = entries[0]["id"] if entries else "minecraft:stone"
            elif a.kind == "enum" and a.options:
                sample[a.key] = a.options[0]
            elif a.kind == "coord":
                sample[a.key] = "~ ~ ~"
            elif a.kind == "nbt":
                sample[a.key] = "{}"
            elif a.kind == "int":
                sample[a.key] = "1"
            elif a.kind == "float":
                sample[a.key] = "1.0"
            else:
                sample[a.key] = "example"
        return sample

    def _on_send_all_to_output(self) -> None:
        commands = [c for c in self._build_all()
                    if c and not c.startswith("# 错误")]
        if not commands:
            _notify(self, "没有可发送的命令。")
            return
        self.commandActivated.emit("\n".join(commands))

    def _reload_command_list(self) -> None:
        self.command_combo.blockSignals(True)
        self.command_combo.clear()
        items = sorted(self._schemas.values(),
                       key=lambda s: (s.get("category", "其他"),
                                       s.get("command", "")))
        for schema in items:
            label = schema.get("display") or f"/{schema['command']}"
            self.command_combo.addItem(label, schema["command"])
        self.command_combo.blockSignals(False)
        # 不自动选中第一条：让「请先选择命令」的引导页成为首屏。
        # （旧版构造时就选第一条，用户一进来看到的是 3 行空表 +
        #   「共 0 条命令」，既不知道要做什么，也永远看不到空状态说明。）
        self.command_combo.setPlaceholderText("请选择命令…")
        self.command_combo.setCurrentIndex(-1)
        self._on_command_changed(-1)

    def _on_command_changed(self, index: int) -> None:
        cmd = self.command_combo.itemData(index) if index >= 0 else ""
        if not cmd:
            # 占位项 / 无选中 → 引导页（参数表格先不建，避免出现空表）
            if self._current_command and self._has_user_data():
                self._drafts[self._current_command] = self._collect_rows()
            self._current_command = ""
            self._args = []
            self._rows = []
            self._rebuild_table()
            self._stack.setCurrentWidget(self._empty_page)
            return
        if cmd == self._current_command and self._args:
            return
        # 切换命令前先保留已填内容，避免用户数据被静默清空
        if self._current_command and self._has_user_data():
            self._drafts[self._current_command] = self._collect_rows()
        self._current_command = cmd
        schema = self._schemas.get(cmd) or {}
        self._args = [ArgSpec.from_dict(a) for a in (schema.get("args") or [])]
        restored = self._drafts.get(cmd)
        if restored:
            self._rows = [dict(r) for r in restored]
        else:
            self._rows = [{a.key: a.default for a in self._args} for _ in range(3)]
        self._rebuild_table()
        self._stack.setCurrentWidget(self._normal_page)
        self._refresh_value_combo()

    def _has_user_data(self) -> bool:
        """表格里是否存在与默认值不同的内容。"""
        defaults = {a.key: str(a.default) for a in self._args}
        for row in self._collect_rows():
            for k, v in row.items():
                if str(v).strip() and str(v) != defaults.get(k, ""):
                    return True
        return False

    def _save_drafts(self) -> None:
        if self._current_command:
            self._drafts[self._current_command] = self._collect_rows()

    def _rebuild_table(self) -> None:
        self._updating = True
        try:
            self.table.clear()
            self.table.setColumnCount(len(self._args))
            self.table.setHorizontalHeaderLabels([
                (a.label or a.key) + (" *" if a.required else "")
                for a in self._args])
            self.table.setRowCount(len(self._rows))
            for r, row in enumerate(self._rows):
                for c, a in enumerate(self._args):
                    it = QTableWidgetItem(str(row.get(a.key, "")))
                    if a.hint:
                        it.setToolTip(a.hint)
                    self.table.setItem(r, c, it)
            for c in range(len(self._args)):
                self.table.setColumnWidth(c, 140)
        finally:
            self._updating = False
        self._refresh_preview()

    def _collect_rows(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for r in range(self.table.rowCount()):
            d: dict[str, str] = {}
            for c, a in enumerate(self._args):
                it = self.table.item(r, c)
                d[a.key] = (it.text() if it is not None else "")
            rows.append(d)
        return rows

    def _on_table_item_changed(self, item) -> None:
        if self._updating:
            return
        self._update_cell_hint(item)
        self._refresh_preview()

    def _update_cell_hint(self, item) -> None:
        """按当前单元格类型更新取值辅助按钮的可用性与提示。"""
        if item is None:
            return
        col = item.column()
        if not (0 <= col < len(self._args)):
            return
        spec = self._args[col]
        kind = spec.kind
        supported = kind in ("entity", "item", "block", "target", "enum")
        self.btn_pick.setEnabled(supported)
        self.btn_pick.setText({
            "entity": "选实体…", "item": "选物品…", "block": "选方块…",
            "target": "选目标…", "enum": "选取值…",
        }.get(kind, "从列表选择…"))
        self.btn_pick.setToolTip(
            spec.hint or spec.label or spec.key)

    def _on_add_row(self) -> None:
        if not self._args:
            return
        self._rows = self._collect_rows()
        self._rows.append({a.key: a.default for a in self._args})
        self._rebuild_table()

    def _add_row_from_values(self, values: dict[str, str]) -> int:
        """用一组给定取值新增一行（供 /give 面板「批量填充」调用）。"""
        if not self._args:
            return -1
        self._rows = self._collect_rows()
        row = {a.key: str(values.get(a.key, a.default)) for a in self._args}
        self._rows.append(row)
        self._rebuild_table()
        index = len(self._rows) - 1
        for col in range(self.table.columnCount()):
            item = self.table.item(index, col)
            if item is not None:
                item.setData(Qt.ItemDataRole.UserRole + 1, True)
        self.table.selectRow(index)
        self._refresh_preview_now()
        return index

    def _on_delete_row(self) -> None:
        rows = sorted(
            {i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        cur = self._collect_rows()
        for r in rows:
            if 0 <= r < len(cur):
                del cur[r]
        self._rows = cur
        self._rebuild_table()

    def _on_clear(self) -> None:
        if not self._args:
            return
        if self._has_user_data():
            answer = QMessageBox.question(
                self, "确认清空表格",
                "会把当前表格里填好的内容全部恢复成默认值。\n\n"
                "清空后无法撤销（可以重新用「填充示例」或导入 CSV）。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._rows = [{a.key: a.default for a in self._args} for _ in range(3)]
        self._drafts.pop(self._current_command, None)
        self._rebuild_table()
        _notify(self, "已清空表格（恢复默认值）。")

    def _refresh_preview(self) -> None:
        self._preview_timer.start()

    def _refresh_preview_now(self) -> None:
        commands = self._build_all()
        self.preview.setPlainText("\n".join(commands))
        valid = sum(1 for c in commands if c and not c.startswith("# 错误"))
        hint = ""
        if not commands and self._args:
            hint = "　（当前表格都是默认值，点「填充示例」开始编辑）"
        self.count_label.setText(
            f"共 {len(commands)} 条命令，其中有效 {valid} 条。{hint}")

    def _build_all(self) -> list[str]:
        if not self._current_command:
            return []
        cls = get_builder(self._current_command)
        if cls is None:
            return ["# 错误：未找到对应的 Builder"]
        self._rows = self._collect_rows()
        defaults = {a.key: str(a.default) for a in self._args}
        result: list[str] = []
        if not self._args:
            # 无参数命令（如 /seed）：表格为空也要能输出
            try:
                return [cls().build()]
            except Exception as e:
                return [f"# 错误: {e}"]
        for r, row in enumerate(self._rows):
            # 整行空白 → 跳过；整行默认值且未被显式标记 → 跳过（避免刷屏）
            values = {k: str(v).strip() for k, v in row.items()}
            if not any(values.values()):
                continue
            looks_default = all(values.get(k, "") == defaults.get(k, "")
                                for k in values)
            if looks_default and not self._is_explicit_row(r):
                continue
            try:
                b = cls()
                for k, v in row.items():
                    if hasattr(b, "values"):
                        b.values[k] = v
                result.append(b.build())
            except Exception as e:
                result.append(f"# 错误: {e}")
        return result

    def _is_explicit_row(self, row: int) -> bool:
        """该行是否由「填充示例」或导入产生（这类行即使等于默认值也要输出）。"""
        item = self.table.item(row, 0)
        return bool(item is not None
                    and item.data(Qt.ItemDataRole.UserRole + 1))

    def _mark_rows_explicit(self) -> None:
        for r in range(self.table.rowCount()):
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                if item is not None:
                    item.setData(Qt.ItemDataRole.UserRole + 1, True)

    # ---- F-05 筛选 ----
    def _apply_filter(self) -> None:
        expr = self.filter_edit.text().strip()
        if not expr or not self._args:
            for r in range(self.table.rowCount()):
                self.table.setRowHidden(r, False)
            return

        m = re.match(r"^(\w+)\s*(>=|<=|==|>|<)\s*(.+)$", expr)
        if not m:
            for r in range(self.table.rowCount()):
                self.table.setRowHidden(r, False)
            return

        key, op, value = m.group(1), m.group(2), m.group(3).strip()
        col = next((i for i, a in enumerate(self._args) if a.key == key), -1)
        if col < 0:
            # 列名不存在：恢复全部显示，避免留下上一次筛选的陈旧隐藏行
            for r in range(self.table.rowCount()):
                self.table.setRowHidden(r, False)
            return

        for r in range(self.table.rowCount()):
            item = self.table.item(r, col)
            text = item.text().strip() if item is not None else ""
            try:
                left = float(text)
                right = float(value)
                ok = {
                    ">=": left >= right, "<=": left <= right,
                    "==": left == right, ">": left > right, "<": left < right,
                }[op]
            except ValueError:
                ok = {">=": text >= value, "<=": text <= value,
                      "==": text == value, ">": text > value,
                      "<": text < value}[op]
            self.table.setRowHidden(r, not ok)

    def _on_import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 CSV", "",
            "CSV 文件 (*.csv *.tsv *.txt);;所有文件 (*)")
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
        except Exception as e:
            QMessageBox.warning(self, "读取失败", str(e))
            return
        self._import_text(text)

    def _on_paste_table(self) -> None:
        try:
            text = QApplication.clipboard().text()
        except Exception:
            text = ""
        if not text.strip():
            QMessageBox.information(self, "提示", "剪贴板为空。")
            return
        self._import_text(text)

    def _import_text(self, text: str) -> None:
        if not self._args:
            QMessageBox.information(self, "提示", "请先选择一个命令。")
            return
        dialect = csv.excel_tab if "\t" in text else csv.excel
        try:
            rows = list(csv.reader(io.StringIO(text), dialect=dialect))
        except Exception as e:
            QMessageBox.warning(self, "解析失败", str(e))
            return
        rows = [r for r in rows if any(c.strip() for c in r)]
        if not rows:
            QMessageBox.information(self, "提示", "没有可导入的数据。")
            return
        header_map = self._match_header(rows[0])
        if header_map is not None:
            data_rows = rows[1:]
        else:
            header_map = {i: a.key for i, a in enumerate(self._args)
                          if i < len(rows[0])}
            data_rows = rows
        new_rows = []
        for row in data_rows:
            d = {a.key: "" for a in self._args}
            for i, val in enumerate(row):
                key = header_map.get(i)
                if key and key in d:
                    d[key] = val.strip()
            new_rows.append(d)
        if not new_rows:
            QMessageBox.information(self, "提示", "没有数据行。")
            return
        self._rows = new_rows
        self._rebuild_table()
        # 导入的行即使等于默认值也应输出
        self._mark_rows_explicit()

    def _match_header(self, header: list[str]):
        keys_lower = {a.key.lower(): a.key for a in self._args}
        labels_lower = {(a.label or "").lower(): a.key
                        for a in self._args if a.label}
        result: dict[int, str] = {}
        matched = 0
        for i, h in enumerate(header):
            h_low = h.strip().lower()
            if not h_low:
                continue
            if h_low in keys_lower:
                result[i] = keys_lower[h_low]
                matched += 1
            elif h_low in labels_lower:
                result[i] = labels_lower[h_low]
                matched += 1
        if matched >= max(1, len(self._args) // 2):
            return result
        return None

    def _on_export_csv(self) -> None:
        if not self._args:
            QMessageBox.information(self, "提示", "没有数据。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", f"{self._current_command or 'batch'}.csv",
            "CSV 文件 (*.csv)")
        if not path:
            return

        # ✅ F-05：编码选择
        ans = QMessageBox.question(
            self, "编码选择",
            "是否使用带 BOM 的 UTF-8（兼容 Excel）？\n"
            "选择“No”则使用严格 UTF-8。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        enc = "utf-8-sig" if ans == QMessageBox.StandardButton.Yes else "utf-8"

        # 对话框期间用户可能改动过表格，这里在真正写盘前再取一次快照
        rows = self._collect_rows()
        self._drafts[self._current_command] = rows
        try:
            with open(path, "w", encoding=enc, newline="") as f:
                w = csv.writer(f)
                w.writerow([a.key for a in self._args])
                for row in rows:
                    w.writerow([row.get(a.key, "") for a in self._args])
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出成功", f"已导出 {len(rows)} 行。")

    def _on_export_mcf(self) -> None:
        commands = [c for c in self._build_all()
                    if c and not c.startswith("# 错误")]
        if not commands:
            QMessageBox.information(self, "提示", "没有可导出的命令。")
            return
        path = QFileDialog.getExistingDirectory(self, "选择数据包导出目录")
        if not path:
            return
        name = self._current_command or "batch"
        try:
            f = export_mcfunction(
                Path(path), commands, namespace="mccmdgen", name=name,
                description=f"MCCmdGen batch export: {name}",
                header=f"批量生成：共 {len(commands)} 条 /{name} 命令")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出成功", f"已生成：\n{f}")


# =========================================================================
# DatapackPanel
# =========================================================================

class DatapackPanel(QWidget):
    functionActivated = Signal(str)
    nbtLoaded = Signal(object)
    #: 未保存的改动状态变化（主窗口据此在页签标题上加 *）
    dirtyChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: DatapackProject | None = None
        self._current_function: FunctionEntry | None = None
        self._updating = False
        #: 有没有还没写盘的函数改动（标题上会显示 *）
        self._dirty = False
        self._build_ui()

    # ---- 脏标记 ----
    def _mark_dirty(self, dirty: bool) -> None:
        if self._dirty == bool(dirty):
            return
        self._dirty = bool(dirty)
        self.dirtyChanged.emit(self._dirty)

    def _confirm_discard(self, action: str) -> bool:
        """载入 / 新建前确认：未保存的函数改动会丢。"""
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self, "有未保存的修改",
            f"当前工程的函数改动还没写盘，{action}会丢掉它们。\n\n"
            "建议先点「保存项目」。仍要继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        box = QGroupBox("数据包配置")
        box_layout = QHBoxLayout(box)
        box_layout.setContentsMargins(8, 8, 8, 8)
        box_layout.setSpacing(4)

        box_layout.addWidget(QLabel("目录"))
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("点击右侧「选择…」按钮")
        box_layout.addWidget(self.path_edit, 2)

        btn_browse = QPushButton("选择…")
        btn_browse.clicked.connect(self._on_browse)
        box_layout.addWidget(btn_browse)

        box_layout.addWidget(QLabel("命名空间"))
        self.ns_edit = QLineEdit("mccmdgen")
        self.ns_edit.setFixedWidth(120)
        box_layout.addWidget(self.ns_edit)

        box_layout.addWidget(QLabel("描述"))
        self.desc_edit = QLineEdit("Generated by MCCmdGen")
        box_layout.addWidget(self.desc_edit, 2)
        v.addWidget(box)

        tools = QHBoxLayout()
        for label, cb in (("新建项目", self._on_new_project),
                          ("载入目录…", self._on_load_project),
                          ("保存项目", self._on_save_project)):
            b = QPushButton(label)
            b.clicked.connect(cb)
            tools.addWidget(b)
        tools.addStretch(1)
        for label, cb in (("+ 新建函数", self._on_add_function),
                          ("删除函数", self._on_delete_function)):
            b = QPushButton(label)
            b.clicked.connect(cb)
            tools.addWidget(b)
        v.addLayout(tools)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_tabs = QTabWidget()
        left_tabs.addTab(self._build_function_list(), "函数")
        left_tabs.addTab(self._build_file_browser(), "文件")
        splitter.addWidget(left_tabs)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)
        rv.addWidget(QLabel("函数内容（每行一条命令，不含前导 /）"))
        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("Consolas", 10))
        self.editor.textChanged.connect(self._on_editor_changed)
        rv.addWidget(self.editor, 1)
        row = QHBoxLayout()
        btn_send = QPushButton("发送到输出区")
        btn_send.clicked.connect(self._on_send_to_output)
        row.addWidget(btn_send)
        row.addStretch(1)
        rv.addLayout(row)
        splitter.addWidget(right)
        splitter.setSizes([260, 620])
        v.addWidget(splitter, 1)

    def _build_function_list(self) -> QWidget:
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("函数"))
        self.fn_list = QListWidget()
        self.fn_list.setAlternatingRowColors(True)
        self.fn_list.currentRowChanged.connect(self._on_function_selected)
        lv.addWidget(self.fn_list, 1)
        return left

    def _build_file_browser(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)

        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderHidden(True)
        self.file_tree.itemDoubleClicked.connect(self._on_file_double_clicked)
        v.addWidget(self.file_tree, 1)

        btn = QPushButton("刷新文件树")
        btn.clicked.connect(self._reload_file_tree)
        v.addWidget(btn)
        return page

    def _reload_file_tree(self) -> None:
        self.file_tree.clear()
        if self.project is None:
            return
        root = self.project.root
        if not root.exists():
            return

        def add_dir(parent, path):
            try:
                children = sorted(path.iterdir())
            except OSError:
                return
            for p in children:
                if p.name.startswith("."):
                    continue
                if p.is_dir():
                    child = QTreeWidgetItem([p.name])
                    if parent is None:
                        self.file_tree.addTopLevelItem(child)
                    else:
                        parent.addChild(child)
                    add_dir(child, p)
                elif p.suffix.lower() in (".nbt", ".snbt"):
                    child = QTreeWidgetItem([p.name])
                    child.setData(0, Qt.ItemDataRole.UserRole, str(p))
                    if parent is None:
                        self.file_tree.addTopLevelItem(child)
                    else:
                        parent.addChild(child)

        add_dir(None, root)

    def _on_file_double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        path_str = item.data(0, Qt.ItemDataRole.UserRole)
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() == ".nbt":
            try:
                root = load_nbt_file(path)
            except NbtFormatError as e:
                QMessageBox.warning(self, "打开失败", str(e))
                return
        else:
            try:
                root = SnbtParser(
                    path.read_text(encoding="utf-8")).parse()
            except (SnbtParseError, OSError) as e:
                QMessageBox.warning(self, "打开失败", str(e))
                return
        self.nbtLoaded.emit(root)

    def _on_browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择数据包目录")
        if path:
            self.path_edit.setText(path)

    def _reset_editor(self) -> None:
        """清空当前函数引用与编辑器，避免把旧内容写进新项目/新函数。"""
        self._current_function = None
        self.editor.blockSignals(True)
        self.editor.clear()
        self.editor.blockSignals(False)

    def _on_new_project(self) -> None:
        if not self._confirm_discard("新建项目"):
            return
        path = self.path_edit.text().strip()
        if not path:
            path = QFileDialog.getExistingDirectory(self, "选择数据包目录")
            if not path:
                return
            self.path_edit.setText(path)
        ns = validate_namespace(self.ns_edit.text())
        self.project = DatapackProject(
            root=Path(path), namespace=ns,
            description=self.desc_edit.text().strip() or "Generated by MCCmdGen")
        self.project.add_function("main", ["say hello from MCCmdGen"])
        self._reset_editor()
        self._refresh_functions()
        self._reload_file_tree()
        self._mark_dirty(True)

    def _on_load_project(self) -> None:
        if not self._confirm_discard("载入目录"):
            return
        path = self.path_edit.text().strip()
        if not path:
            path = QFileDialog.getExistingDirectory(self, "选择数据包目录")
            if not path:
                return
            self.path_edit.setText(path)
        ns = validate_namespace(self.ns_edit.text())
        project = DatapackProject(
            root=Path(path), namespace=ns,
            description=self.desc_edit.text().strip() or "Generated by MCCmdGen")
        project.load_from_disk()
        self.project = project
        self._reset_editor()
        self._refresh_functions()
        self._reload_file_tree()
        self._mark_dirty(False)
        _notify(self, f"已载入 {len(project.functions)} 个函数。")

    def _on_save_project(self) -> None:
        if self.project is None:
            QMessageBox.information(self, "提示", "请先新建或载入项目。")
            return
        self.project.namespace = validate_namespace(self.ns_edit.text())
        self.project.description = (
            self.desc_edit.text().strip() or "Generated by MCCmdGen")
        try:
            written = self.project.save()
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return
        QMessageBox.information(
            self, "保存成功",
            f"已写出 {len(written)} 个 .mcfunction 文件。\n"
            f"目录：{self.project.root}")
        self._mark_dirty(False)
        self._reload_file_tree()

    def _refresh_functions(self, select_row: int = 0) -> None:
        """重建函数列表；select_row<0 表示不选中任何行。"""
        self._updating = True
        try:
            self.fn_list.clear()
            if self.project is None:
                self._current_function = None
                return
            for fn in self.project.functions:
                item = QListWidgetItem(f"{self.project.namespace}:{fn.name}")
                item.setData(Qt.ItemDataRole.UserRole, fn.name)
                self.fn_list.addItem(item)
            if 0 <= select_row < self.fn_list.count():
                self.fn_list.setCurrentRow(select_row)
            else:
                # 选中行在刷新前就失效时，主动清空编辑器，避免显示已删除的函数
                self._current_function = None
                self.editor.blockSignals(True)
                self.editor.clear()
                self.editor.blockSignals(False)
        finally:
            self._updating = False

    def _on_function_selected(self, row: int) -> None:
        if self._updating:
            return
        if self.project is None or row < 0 or row >= len(self.project.functions):
            self._current_function = None
            self.editor.blockSignals(True)
            self.editor.clear()
            self.editor.blockSignals(False)
            return
        fn = self.project.functions[row]
        self._current_function = fn
        self.editor.blockSignals(True)
        self.editor.setPlainText("\n".join(fn.commands))
        self.editor.blockSignals(False)

    def _on_editor_changed(self) -> None:
        if self._current_function is None:
            return
        self._current_function.commands = self.editor.toPlainText().splitlines()
        self._mark_dirty(True)

    def _on_add_function(self) -> None:
        if self.project is None:
            QMessageBox.information(self, "提示", "请先新建或载入项目。")
            return
        name, ok = QInputDialog.getText(
            self, "新建函数", "函数名（可含子目录，例如 sub/init）：")
        if not ok or not name.strip():
            return
        fn = self.project.add_function(name.strip())
        try:
            row = self.project.functions.index(fn)
        except ValueError:
            row = self.fn_list.count() - 1
        self._refresh_functions(select_row=row)

    def _on_delete_function(self) -> None:
        if self.project is None:
            return
        row = self.fn_list.currentRow()
        if row < 0 or row >= len(self.project.functions):
            return
        fn = self.project.functions[row]
        target = self._function_file(fn.name)
        extra = (f"\n\n磁盘文件也会一起删除：\n{target}" if target is not None
                 else "")
        if QMessageBox.question(
                self, "删除函数",
                f"删除函数 {fn.name} 吗？{extra}\n\n"
                "（旧版本只从列表里移除，磁盘上的 .mcfunction 会残留，"
                "游戏照样会加载它。）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) \
                != QMessageBox.StandardButton.Yes:
            return
        self.project.remove_function(fn.name)
        removed = self._delete_function_file(fn.name)
        # 删除后把选中行收敛到有效范围；越界时清空编辑器
        next_row = min(row, len(self.project.functions) - 1)
        self._refresh_functions(select_row=next_row)
        self._reload_file_tree()
        if removed:
            _notify(self, f"已删除函数与文件 {fn.name}.mcfunction")
        else:
            _notify(self, f"已从工程里删除函数 {fn.name}（未找到对应文件）")

    def _function_file(self, name: str) -> Path | None:
        """函数名 → 磁盘上的 .mcfunction 路径（找不到返回 None）。"""
        project = self.project
        root = getattr(project, "root", None)
        if root is None or not name:
            return None
        safe = _sanitize_function_name(name)
        candidates = []
        try:
            candidates.append(project.functions_dir() / f"{safe}.mcfunction")
        except Exception:                               # pragma: no cover
            pass
        for namespace in {getattr(project, "namespace", "minecraft"),
                          "minecraft"}:
            for folder in ("function", "functions"):
                candidates.append(
                    Path(root) / "data" / namespace / folder
                    / f"{safe}.mcfunction")
        candidates.append(Path(root) / "functions" / f"{safe}.mcfunction")
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def _delete_function_file(self, name: str) -> bool:
        path = self._function_file(name)
        if path is None:
            return False
        try:
            path.unlink()
        except OSError:
            return False
        # 顺手清掉空的父目录，避免数据包里堆一堆空文件夹
        parent = path.parent
        while parent.name not in ("data", "function", "functions", ""):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
        return True

    def _on_send_to_output(self) -> None:
        if self._current_function is None:
            return
        self.functionActivated.emit("\n".join(self._current_function.commands))


# =========================================================================
# HistoryPanel
# =========================================================================

class HistoryPanel(QWidget):
    commandActivated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.store = HistoryStore()
        self._stack = QStackedWidget()
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)

        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索历史…")
        self.search.textChanged.connect(self.reload)
        row.addWidget(self.search, 1)

        btn_export = QPushButton("导出 .mcfunction…")
        btn_export.clicked.connect(self._on_export_all)
        row.addWidget(btn_export)

        btn_clear = QPushButton("清空历史")
        btn_clear.clicked.connect(self._on_clear)
        row.addWidget(btn_clear)
        v.addLayout(row)

        self._empty_page = EmptyState(
            "还没有记录。生成或复制命令后会自动出现在这里。",
            [],
        )

        normal_page = QWidget()
        nv = QVBoxLayout(normal_page)
        nv.setContentsMargins(0, 0, 0, 0)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.itemDoubleClicked.connect(self._on_activated)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_context_menu)
        nv.addWidget(self.list, 1)

        hint = QLabel("双击填入输出区；右键可多选导出")
        hint.setStyleSheet(_muted(hint))
        nv.addWidget(hint, 0)

        self._normal_page = normal_page
        self._stack.addWidget(self._empty_page)
        self._stack.addWidget(normal_page)
        v.addWidget(self._stack, 1)

    def reload(self) -> None:
        keyword = self.search.text()
        entries = self.store.list(keyword=keyword, limit=300)
        self.list.clear()
        for e in entries:
            item = QListWidgetItem(f"[{e.created_at}] {e.command}")
            item.setData(Qt.ItemDataRole.UserRole, e.command)
            item.setData(Qt.ItemDataRole.UserRole + 1, e.id)
            self.list.addItem(item)
        self._stack.setCurrentWidget(
            self._normal_page if self.list.count() else self._empty_page)

    def add(self, command: str, command_type: str = "") -> None:
        if self.store.add(command, command_type) is not None:
            self.reload()

    def _on_activated(self, item: QListWidgetItem) -> None:
        cmd = item.data(Qt.ItemDataRole.UserRole) or ""
        if cmd:
            self.commandActivated.emit(cmd)

    def _on_context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        act_use = menu.addAction("填入输出区")
        act_copy = menu.addAction("复制到剪贴板")
        menu.addSeparator()
        act_diff = menu.addAction("与当前输出对比")
        menu.addSeparator()
        act_export_sel = menu.addAction("导出选中为 .mcfunction…")
        act_export_all = menu.addAction("导出全部为 .mcfunction…")
        menu.addSeparator()
        act_del = menu.addAction("删除该条")
        chosen = menu.exec(self.list.viewport().mapToGlobal(pos))
        if chosen == act_use:
            self._on_activated(item)
        elif chosen == act_copy:
            QApplication.clipboard().setText(
                item.data(Qt.ItemDataRole.UserRole) or "")
        elif chosen == act_diff:
            current = ""
            w = self.window()
            if hasattr(w, "output_edit"):
                current = w.output_edit.toPlainText()
            old = item.data(Qt.ItemDataRole.UserRole) or ""
            DiffDialog(old, current, self).exec()
        elif chosen == act_export_sel:
            self._export_selected()
        elif chosen == act_export_all:
            self._on_export_all()
        elif chosen == act_del:
            entry_id = item.data(Qt.ItemDataRole.UserRole + 1)
            if entry_id is not None:
                self.store.delete(int(entry_id))
                self.reload()

    def _export_selected(self) -> None:
        items = self.list.selectedItems()
        if not items:
            QMessageBox.information(self, "提示", "未选中任何历史条目。")
            return
        commands = [it.data(Qt.ItemDataRole.UserRole) or "" for it in items]
        self._do_export([c for c in commands if c.strip()])

    def _on_export_all(self) -> None:
        commands = []
        for i in range(self.list.count()):
            c = self.list.item(i).data(Qt.ItemDataRole.UserRole) or ""
            if c.strip():
                commands.append(c)
        if not commands:
            QMessageBox.information(self, "提示", "没有可导出的历史记录。")
            return
        self._do_export(commands)

    def _do_export(self, commands: list[str]) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择数据包导出目录")
        if not path:
            return
        try:
            f = export_mcfunction(
                Path(path), commands, namespace="mccmdgen", name="history",
                description="MCCmdGen history export",
                header="导出自 MCCmdGen 历史记录")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(
            self, "导出成功",
            f"已生成：\n{f}\n\n以及 {Path(path) / 'pack.mcmeta'}")

    def _on_clear(self) -> None:
        if QMessageBox.question(
                self, "清空历史记录",
                "确定清空全部历史记录吗？\n\n"
                "清空后无法撤销（收藏与片段不受影响）。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) \
                == QMessageBox.StandardButton.Yes:
            self.store.clear()
            self.reload()
            _notify(self, "已清空历史记录。")


# =========================================================================
# PresetPanel
# =========================================================================

class PresetPanel(QWidget):
    commandActivated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.store = PresetStore()
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索预设…")
        self.search.textChanged.connect(self.reload)
        row.addWidget(self.search, 1)
        for label, cb in (("新建…", self._on_new),
                          ("导出 .mcfunction…", self._on_export_mcfunction),
                          ("导出 JSON…", self._on_export),
                          ("导入…", self._on_import)):
            b = QPushButton(label)
            b.clicked.connect(cb)
            row.addWidget(b)
        v.addLayout(row)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["名称 / 分类", "命令"])
        self.tree.setColumnWidth(0, 220)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        v.addWidget(self.tree, 1)

        hint = QLabel("双击应用（含 {占位符} 时会弹出填写表单）")
        hint.setStyleSheet(_muted(hint))
        v.addWidget(hint, 0)

    def reload(self) -> None:
        presets = self.store.list(keyword=self.search.text())
        by_cat: dict[str, list] = {}
        for p in presets:
            by_cat.setdefault(p.category, []).append(p)
        self.tree.clear()
        for cat in sorted(by_cat.keys()):
            cat_item = QTreeWidgetItem([cat, ""])
            cat_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(cat_item)
            for p in by_cat[cat]:
                child = QTreeWidgetItem([p.name, p.command])
                child.setData(0, Qt.ItemDataRole.UserRole, p.id)
                child.setData(1, Qt.ItemDataRole.UserRole, p.command)
                cat_item.addChild(child)
            cat_item.setExpanded(True)

    def _on_double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        cmd = item.data(1, Qt.ItemDataRole.UserRole)
        if not cmd:
            return
        if has_placeholder(cmd):
            dlg = PlaceholderDialog(cmd, self, title="填写预设参数")
            if dlg.exec() != PlaceholderDialog.DialogCode.Accepted:
                return
            cmd = dlg.result_command()
        self.commandActivated.emit(cmd)

    def _on_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        preset_id = item.data(0, Qt.ItemDataRole.UserRole)
        if preset_id is None:
            return
        menu = QMenu(self)
        act_use = menu.addAction("填入输出区")
        act_copy = menu.addAction("复制到剪贴板")
        if has_placeholder(item.data(1, Qt.ItemDataRole.UserRole) or ""):
            act_fill = menu.addAction("填写占位符后复制…")
        else:
            act_fill = None
        menu.addSeparator()
        act_frag = menu.addAction("另存为片段…")
        menu.addSeparator()
        act_edit = menu.addAction("编辑…")
        act_del = menu.addAction("删除")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen == act_use:
            self._on_double_clicked(item, 0)
        elif chosen == act_copy:
            QApplication.clipboard().setText(
                item.data(1, Qt.ItemDataRole.UserRole) or "")
        elif act_fill is not None and chosen == act_fill:
            self._fill_placeholders(item)
        elif chosen == act_frag:
            self._save_as_fragment(item)
        elif chosen == act_edit:
            self._edit_preset(preset_id,
                              item.data(1, Qt.ItemDataRole.UserRole) or "")
        elif chosen == act_del:
            if QMessageBox.question(
                    self, "删除预设",
                    "确定删除该预设吗？（不可撤销）",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) \
                    == QMessageBox.StandardButton.Yes:
                self.store.delete(preset_id)
                self.reload()

    def _fill_placeholders(self, item: QTreeWidgetItem) -> None:
        """填写 {占位符} 后复制，不改变收藏本身。"""
        cmd = item.data(1, Qt.ItemDataRole.UserRole) or ""
        if not has_placeholder(cmd):
            QApplication.clipboard().setText(cmd)
            _notify(self, "该预设没有占位符，已直接复制。")
            return
        dlg = PlaceholderDialog(cmd, self, title="填写占位符")
        if dlg.exec() != PlaceholderDialog.DialogCode.Accepted:
            return
        result = dlg.result_command()
        QApplication.clipboard().setText(result)
        _notify(self, "已把填写后的命令复制到剪贴板。")

    def _save_as_fragment(self, item: QTreeWidgetItem) -> None:
        """把收藏转存为片段，方便在工作台/命令面板里拖拽复用。"""
        preset_id = item.data(0, Qt.ItemDataRole.UserRole)
        preset = next((p for p in self.store.list() if p.id == preset_id), None)
        if preset is None:
            return
        try:
            store = FragmentStore()
        except Exception as e:                          # pragma: no cover
            QMessageBox.warning(self, "失败", str(e))
            return
        name, ok = QInputDialog.getText(
            self, "另存为片段", "片段名称：", text=preset.name)
        if not ok or not name.strip():
            return
        store.add(name.strip(), "command",
                  {"command": preset.command}, preset.category)
        _notify(self, f"已保存片段：{name.strip()}")

    def _on_new(self) -> None:
        dlg = SavePresetDialog("", "", self.store.categories(), self)
        if dlg.exec():
            name, category, command, ctype = dlg.value()
            self.store.add(name, category, command, ctype)
            self.reload()

    def _edit_preset(self, preset_id: int, command: str) -> None:
        preset = next((p for p in self.store.list() if p.id == preset_id), None)
        if preset is None:
            return
        dlg = SavePresetDialog(preset.command, preset.command_type,
                                self.store.categories(), self)
        dlg.name_edit.setText(preset.name)
        idx = dlg.category_combo.findText(preset.category)
        if idx >= 0:
            dlg.category_combo.setCurrentIndex(idx)
        else:
            dlg.category_combo.setEditText(preset.category)
        if dlg.exec():
            name, category, command, ctype = dlg.value()
            self.store.update(preset_id, name, category, command, ctype)
            self.reload()

    def _on_export_mcfunction(self) -> None:
        presets = self.store.list(keyword=self.search.text())
        if not presets:
            QMessageBox.information(self, "提示", "当前没有可导出的预设。")
            return
        functions: dict[str, list[str]] = {}
        for p in presets:
            safe_cat = _sanitize(p.category) or "default"
            safe_name = _sanitize(p.name) or f"preset_{p.id}"
            key = f"{safe_cat}/{safe_name}"
            while key in functions:
                key += f"_{p.id}"
            functions[key] = [p.command]
        path = QFileDialog.getExistingDirectory(self, "选择数据包导出目录")
        if not path:
            return
        try:
            written = export_mcfunction_multi(
                Path(path), functions, namespace="mccmdgen",
                description="MCCmdGen preset export")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(
            self, "导出成功",
            f"已生成 {len(written)} 个 .mcfunction 文件。\n目录：{Path(path)}")

    def _on_export(self) -> None:
        if not self.store.list():
            QMessageBox.information(self, "提示", "当前没有可导出的预设。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出预设", "presets.json", "JSON 文件 (*.json)")
        if not path:
            return
        try:
            n = self.store.export_json(Path(path))
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出成功", f"已导出 {n} 条预设。")

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入预设", "", "JSON 文件 (*.json);;所有文件 (*)")
        if not path:
            return
        try:
            added, skipped, notes = self.store.import_json(
                Path(path), merge=True)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))
            return
        self.reload()
        msg = f"成功导入 {added} 条，跳过 {skipped} 条（重名或格式问题）。"
        if notes:
            msg += "\n\n提示：\n" + "\n".join(f"· {n}" for n in notes)
        QMessageBox.information(self, "导入完成", msg)


def _sanitize(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9_\-]+", "_", s)
    return s.strip("_")


# =========================================================================
# FragmentPanel
# =========================================================================

KIND_LABELS = {
    "execute_node": "Execute 节点", "command": "命令", "nbt": "SNBT",
    "target": "目标选择器", "block": "方块 ID", "item": "物品 ID",
    "component": "物品组件",
}
KIND_OPTIONS = list(KIND_LABELS.keys())


class _DraggableTree(QTreeWidget):
    """显式重写 startDrag，保证片段拖拽到 ExecutePanel 一定生效。"""

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner
        self.setDragEnabled(True)

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if item is not None:
            self._owner._start_drag(item)


class _FragmentEditDialog(QDialog):
    def __init__(self, fragment: Fragment | None, categories: list[str],
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑片段" if fragment else "新建片段")
        self.resize(560, 420)
        self._fragment = fragment

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        form = QFormLayout()

        self.name_edit = QLineEdit(fragment.name if fragment else "")
        self.name_edit.setPlaceholderText("例如：僵尸常用 NBT")
        form.addRow("名称", self.name_edit)

        self.kind_combo = QComboBox()
        for k in KIND_OPTIONS:
            self.kind_combo.addItem(KIND_LABELS[k], k)
        if fragment:
            idx = self.kind_combo.findData(fragment.kind)
            if idx >= 0:
                self.kind_combo.setCurrentIndex(idx)
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        form.addRow("类型", self.kind_combo)

        self.category_combo = QComboBox()
        self.category_combo.setEditable(True)
        base = ["默认", "实体", "方块", "选择器", "execute", "NBT", "物品组件"]
        for c in (categories or []):
            if c not in base:
                base.append(c)
        for c in base:
            self.category_combo.addItem(c)
        if fragment:
            idx = self.category_combo.findText(fragment.category)
            if idx >= 0:
                self.category_combo.setCurrentIndex(idx)
            else:
                self.category_combo.setEditText(fragment.category)
        form.addRow("分类", self.category_combo)
        v.addLayout(form)

        v.addWidget(QLabel("内容（JSON 或按提示填写）"))
        self.payload_edit = QPlainTextEdit()
        self.payload_edit.setPlaceholderText(
            '按类型填写：\n'
            '  execute_node : {"type": "as", "params": {"target": "@s"}}\n'
            '  command      : {"text": "say hello"}\n'
            '  nbt          : {"snbt": "{IsBaby:1b}"}\n'
            '  target       : {"text": "@e[type=minecraft:zombie]"}\n'
            '  block/item   : {"id": "minecraft:stone"}\n'
            '  component    : {"id": "minecraft:enchantments", "value": "{...}"}')
        if fragment:
            self.payload_edit.setPlainText(
                json.dumps(fragment.payload, ensure_ascii=False, indent=2))
        else:
            self.payload_edit.setPlainText('{"text": "say hello"}')
        v.addWidget(self.payload_edit, 1)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(f"color: {theme_colors(self)['error']};")
        v.addWidget(self.error_label)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _on_kind_changed(self, index: int) -> None:
        kind = self.kind_combo.itemData(index)
        defaults = {
            "execute_node": '{"type": "as", "params": {"target": "@s"}}',
            "command": '{"text": "say hello"}',
            "nbt": '{"snbt": "{}"}',
            "target": '{"text": "@e[type=minecraft:zombie]"}',
            "block": '{"id": "minecraft:stone"}',
            "item": '{"id": "minecraft:diamond_sword"}',
            "component": '{"id": "minecraft:unbreakable", "value": "1b"}',
        }
        if not self._fragment:
            self.payload_edit.setPlainText(defaults.get(kind, "{}"))

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            self.error_label.setText("名称不能为空。")
            return
        try:
            payload = json.loads(self.payload_edit.toPlainText() or "{}")
        except json.JSONDecodeError as e:
            self.error_label.setText(
                f"JSON 解析失败：第 {e.lineno} 行：{e.msg}")
            return
        if not isinstance(payload, dict):
            self.error_label.setText("内容必须是 JSON 对象。")
            return
        self.error_label.setText("")
        self.accept()

    def value(self) -> tuple[str, str, str, dict]:
        return (
            self.name_edit.text().strip(),
            self.kind_combo.currentData() or "command",
            self.category_combo.currentText().strip() or "默认",
            json.loads(self.payload_edit.toPlainText() or "{}"),
        )


class FragmentPanel(QWidget):
    commandActivated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.store = FragmentStore()
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)

        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索片段…")
        self.search.textChanged.connect(self.reload)
        row.addWidget(self.search, 1)

        self.kind_filter = QComboBox()
        self.kind_filter.addItem("全部类型", "")
        for k in KIND_OPTIONS:
            self.kind_filter.addItem(KIND_LABELS[k], k)
        self.kind_filter.currentIndexChanged.connect(self.reload)
        row.addWidget(self.kind_filter)

        for label, cb in (("新建…", self._on_new),
                          ("导入…", self._on_import),
                          ("导出…", self._on_export)):
            b = QPushButton(label)
            b.clicked.connect(cb)
            row.addWidget(b)
        v.addLayout(row)

        self._stack = QStackedWidget()
        self._empty_page = EmptyState(
            "还没有片段。点 [新建…] 保存第一个 execute 节点，或从文件 [导入…]。",
            [("新建片段", self._on_new), ("导入片段", self._on_import)],
        )

        normal_page = QWidget()
        nv = QVBoxLayout(normal_page)
        nv.setContentsMargins(0, 0, 0, 0)

        self.tree = _DraggableTree(self)
        self.tree.setHeaderLabels(["名称", "类型", "分类"])
        self.tree.setColumnWidth(0, 220)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        nv.addWidget(self.tree, 1)

        hint = QLabel("双击 → 填入输出区；拖到 ExecutePanel 会作为节点插入。")
        hint.setStyleSheet(_muted(hint))
        nv.addWidget(hint, 0)

        self._normal_page = normal_page
        self._stack.addWidget(self._empty_page)
        self._stack.addWidget(normal_page)
        v.addWidget(self._stack, 1)

    def reload(self) -> None:
        keyword = self.search.text()
        kind = self.kind_filter.currentData() or ""
        fragments = self.store.list(keyword=keyword, kind=kind)
        by_cat: dict[str, list[Fragment]] = {}
        for f in fragments:
            by_cat.setdefault(f.category, []).append(f)
        self.tree.clear()
        for cat in sorted(by_cat.keys()):
            cat_item = QTreeWidgetItem([cat, "", ""])
            cat_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(cat_item)
            for f in by_cat[cat]:
                child = QTreeWidgetItem([
                    f.name,
                    KIND_LABELS.get(f.kind, f.kind),
                    f.category])
                child.setData(0, Qt.ItemDataRole.UserRole, f.id)
                child.setData(1, Qt.ItemDataRole.UserRole, f)
                cat_item.addChild(child)
            cat_item.setExpanded(True)
        self._stack.setCurrentWidget(
            self._normal_page if self.tree.topLevelItemCount()
            else self._empty_page)

    def _start_drag(self, item: QTreeWidgetItem) -> None:
        frag = item.data(1, Qt.ItemDataRole.UserRole)
        if not isinstance(frag, Fragment):
            return
        payload = {
            "id": frag.id,
            "name": frag.name,
            "kind": frag.kind,
            "category": frag.category,
            "payload": frag.payload,
        }
        mime = QMimeData()
        mime.setData(FRAGMENT_MIME,
                     json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        mime.setText(_fragment_to_command(frag))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def _on_double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        frag = item.data(1, Qt.ItemDataRole.UserRole)
        if not isinstance(frag, Fragment):
            return
        text = _fragment_to_command(frag)
        if text:
            self.commandActivated.emit(text)

    def _on_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        frag = item.data(1, Qt.ItemDataRole.UserRole)
        if not isinstance(frag, Fragment):
            return
        menu = QMenu(self)
        act_use = menu.addAction("填入输出区")
        act_copy = menu.addAction("复制命令/内容")
        act_save = menu.addAction("另存为文件…")
        menu.addSeparator()
        act_fav = menu.addAction("加入收藏")
        menu.addSeparator()
        act_edit = menu.addAction("编辑…")
        act_del = menu.addAction("删除")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen == act_use:
            self._on_double_clicked(item, 0)
        elif chosen == act_copy:
            QApplication.clipboard().setText(_fragment_to_command(frag))
        elif chosen == act_save:
            self._save_to_file(frag)
        elif chosen == act_fav:
            self._add_to_presets(frag)
        elif chosen == act_edit:
            self._edit(frag)
        elif chosen == act_del:
            if QMessageBox.question(
                    self, "删除片段",
                    "确定删除该片段吗？（不可撤销）",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) \
                    == QMessageBox.StandardButton.Yes:
                self.store.delete(frag.id)
                self.reload()

    def _save_to_file(self, frag: Fragment) -> None:
        """把片段内容导出成文件（命令 → .mcfunction，SNBT → .snbt）。"""
        text = _fragment_to_command(frag)
        if not text:
            _notify(self, "片段内容为空。")
            return
        if frag.kind == "nbt":
            default_name = f"{_sanitize(frag.name) or 'fragment'}.snbt"
            filters = "SNBT 文本 (*.snbt);;所有文件 (*)"
        else:
            default_name = f"{_sanitize(frag.name) or 'fragment'}.mcfunction"
            filters = "mcfunction (*.mcfunction);;所有文件 (*)"
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为文件", default_name, filters)
        if not path:
            return
        try:
            Path(path).write_text(text + "\n", encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return
        _notify(self, f"已保存：{Path(path).name}")

    def _add_to_presets(self, frag: Fragment) -> None:
        """把片段登记为收藏，方便在收藏面板里统一管理。"""
        text = _fragment_to_command(frag)
        if not text:
            _notify(self, "片段内容为空。")
            return
        try:
            store = PresetStore()
        except Exception as e:                          # pragma: no cover
            QMessageBox.warning(self, "失败", str(e))
            return
        store.add(frag.name, frag.category, text, frag.kind)
        _notify(self, f"已加入收藏：{frag.name}（分类 {frag.category}）")

    def _on_new(self) -> None:
        dlg = _FragmentEditDialog(None, self.store.categories(), self)
        if dlg.exec():
            name, kind, category, payload = dlg.value()
            self.store.add(name, kind, payload, category)
            self.reload()

    def _edit(self, frag: Fragment) -> None:
        dlg = _FragmentEditDialog(frag, self.store.categories(), self)
        if dlg.exec():
            name, kind, category, payload = dlg.value()
            self.store.update(frag.id, name, kind, payload, category)
            self.reload()

    def _on_export(self) -> None:
        if not self.store.list():
            QMessageBox.information(self, "提示", "当前没有可导出的片段。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出片段", "fragments.json", "JSON 文件 (*.json)")
        if not path:
            return
        try:
            n = self.store.export_json(Path(path))
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出成功", f"已导出 {n} 条片段。")

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入片段", "", "JSON 文件 (*.json);;所有文件 (*)")
        if not path:
            return
        try:
            added, skipped = self.store.import_json(Path(path), merge=True)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))
            return
        self.reload()
        QMessageBox.information(self, "导入完成",
            f"成功导入 {added} 条，跳过 {skipped} 条。")


def _payload_to_fragment(payload: dict) -> "Fragment | None":
    """把拖放载荷还原成 Fragment（便于统一走 _fragment_to_command）。"""
    body = payload.get("payload")
    if not isinstance(body, dict):
        # 兼容直接给出命令文本的简化载荷
        text = str(payload.get("text", "") or payload.get("command", "")).strip()
        if not text:
            return None
        body = {"text": text}
    return Fragment(
        id=int(payload.get("id", 0) or 0),
        name=str(payload.get("name", "")),
        kind=str(payload.get("kind", "command") or "command"),
        category=str(payload.get("category", "")),
        payload=body,
        created_at="",
        updated_at="",
    )


def _fragment_to_command(frag: Fragment) -> str:
    kind = frag.kind
    p = frag.payload or {}
    if kind == "command":
        return str(p.get("text", "")).strip()
    if kind == "nbt":
        return str(p.get("snbt", "")).strip()
    if kind == "target":
        return str(p.get("text", "")).strip()
    if kind in ("block", "item"):
        return str(p.get("id", "")).strip()
    if kind == "component":
        cid = str(p.get("id", "")).strip()
        val = str(p.get("value", "")).strip()
        if not cid:
            return ""
        return f"{cid}={val}" if val else cid
    if kind == "execute_node":
        try:
            node = ExecuteNode(type=str(p.get("type", "as")),
                               params=dict(p.get("params") or {}))
            return node.build()
        except Exception:
            return ""
    return ""


# =========================================================================
# WorkbenchPanel
# =========================================================================

class WorkbenchPanel(QWidget):
    commandActivated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.script = CommandScript(name="untitled")
        self._current_step: Step | None = None
        self._updating = False
        self._templates: list[dict] = []
        self.setAcceptDrops(True)
        self._build_ui()
        self._load_templates()
        self._refresh_tree()
        self._refresh_preview()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QLabel("脚本名"))
        self.name_edit = QLineEdit("untitled")
        self.name_edit.textChanged.connect(
            lambda t: setattr(self.script, "name", t.strip() or "untitled"))
        top.addWidget(self.name_edit, 1)
        top.addWidget(QLabel("说明"))
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("可选，会写入 mcfunction 头部注释")
        self.desc_edit.textChanged.connect(
            lambda t: setattr(self.script, "description", t))
        top.addWidget(self.desc_edit, 2)
        v.addLayout(top)

        tools = QHBoxLayout()
        tools.setSpacing(4)
        btn_add = QPushButton("添加 ▾")
        btn_add.clicked.connect(self._on_add)
        btn_del = QPushButton("删除")
        btn_del.clicked.connect(self._on_delete)
        btn_up = QPushButton("↑")
        btn_up.setFixedWidth(32)
        btn_up.clicked.connect(lambda: self._move(-1))
        btn_down = QPushButton("↓")
        btn_down.setFixedWidth(32)
        btn_down.clicked.connect(lambda: self._move(1))
        btn_indent = QPushButton("缩进 →")
        btn_indent.clicked.connect(self._indent)
        btn_outdent = QPushButton("← 反缩进")
        btn_outdent.clicked.connect(self._outdent)
        for b in (btn_add, btn_del, btn_up, btn_down, btn_indent, btn_outdent):
            tools.addWidget(b)
        tools.addStretch(1)
        btn_clear = QPushButton("清空")
        btn_clear.clicked.connect(self._on_clear)
        tools.addWidget(btn_clear)
        v.addLayout(tools)

        self._stack = QStackedWidget()
        self._empty_page = EmptyState(
            "还没有步骤。点击 [添加 ▾] 从命令/if/repeat 开始，"
            "或从片段库拖入一个节点。",
            [("添加步骤", self._on_add)],
        )

        normal_page = QWidget()
        nv = QVBoxLayout(normal_page)
        nv.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["步骤", "类型"])
        self.tree.setColumnWidth(0, 320)
        self.tree.setAlternatingRowColors(True)
        self.tree.currentItemChanged.connect(self._on_selection_changed)
        lv.addWidget(self.tree, 1)

        tpl_box = QGroupBox("模板")
        tpl_layout = QVBoxLayout(tpl_box)
        self.template_list = QListWidget()
        self.template_list.setMaximumHeight(120)
        self.template_list.itemDoubleClicked.connect(
            self._on_template_double_clicked)
        tpl_layout.addWidget(self.template_list)
        lv.addWidget(tpl_box, 0)

        splitter.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)

        self.prop_box = QGroupBox("属性")
        self.prop_stack = QStackedWidget()
        prop_layout = QVBoxLayout(self.prop_box)
        prop_layout.setContentsMargins(8, 8, 8, 8)
        prop_layout.addWidget(self.prop_stack)
        rv.addWidget(self.prop_box, 0)

        preview_box = QGroupBox("预览")
        pv = QVBoxLayout(preview_box)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont("Consolas", 10))
        pv.addWidget(self.preview, 1)
        rv.addWidget(preview_box, 1)
        splitter.addWidget(right)
        splitter.setSizes([520, 520])

        nv.addWidget(splitter, 1)
        self._normal_page = normal_page
        self._stack.addWidget(self._empty_page)
        self._stack.addWidget(normal_page)
        v.addWidget(self._stack, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        for label, cb in (("导出 .mcfunction…", self._on_export_mcf),
                          ("保存脚本…", self._on_export_json),
                          ("加载脚本…", self._on_import_json),
                          ("发送到输出区", self._on_send_to_output)):
            b = QPushButton(label)
            b.clicked.connect(cb)
            bottom.addWidget(b)
        v.addLayout(bottom)

    def _load_templates(self) -> None:
        data = data_store().load_json("command_templates.json") or {}
        self._templates = data.get("templates", [])
        self.template_list.clear()
        for t in self._templates:
            item = QListWidgetItem(f"{t['name']}  [{t.get('category','')}]")
            item.setData(Qt.ItemDataRole.UserRole, t)
            self.template_list.addItem(item)

    def _on_template_double_clicked(self, item: QListWidgetItem) -> None:
        t = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(t, dict):
            return
        for cmd in t.get("commands", []):
            self.script.steps.append(Step(kind="command", command=cmd))
        self._refresh_tree()

    def _refresh_tree(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        self._populate(None, self.script.steps)
        self.tree.blockSignals(False)
        self._refresh_preview()
        self._stack.setCurrentWidget(
            self._normal_page if self.script.steps else self._empty_page)

    def _populate(self, parent_item, steps) -> None:
        for s in steps:
            item = QTreeWidgetItem()
            item.setText(0, s.summary())
            item.setText(1, STEP_KIND_LABELS.get(s.kind, s.kind))
            item.setData(0, Qt.ItemDataRole.UserRole, s)
            if parent_item is None:
                self.tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            item.setExpanded(True)
            if s.children:
                self._populate(item, s.children)

    def _refresh_preview(self) -> None:
        self.preview.setPlainText(self.script.to_mcfunction())

    def _on_selection_changed(self, current, _prev) -> None:
        if current is None:
            self._current_step = None
            self._clear_prop()
            return
        self._current_step = current.data(0, Qt.ItemDataRole.UserRole)
        self._rebuild_prop(self._current_step)

    def _clear_prop(self) -> None:
        while self.prop_stack.count():
            w = self.prop_stack.widget(0)
            self.prop_stack.removeWidget(w)
            w.deleteLater()

    def _rebuild_prop(self, step: Step) -> None:
        self._clear_prop()
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)

        def on_change() -> None:
            if self._updating:
                return
            self._refresh_tree_keep_selection()
            self._refresh_preview()

        k = step.kind
        if k == "command":
            le = QLineEdit(step.command)
            le.setPlaceholderText(
                "例如 say hello 或 summon minecraft:pig ~ ~ ~")
            le.textChanged.connect(
                lambda v: (setattr(step, "command", v), on_change()))
            form.addRow("命令", le)
        elif k == "comment":
            le = QLineEdit(step.comment)
            le.textChanged.connect(
                lambda v: (setattr(step, "comment", v), on_change()))
            form.addRow("注释", le)
        elif k == "function":
            le = QLineEdit(step.function)
            le.setPlaceholderText("minecraft:example 或 ns:path/to/func")
            le.textChanged.connect(
                lambda v: (setattr(step, "function", v), on_change()))
            form.addRow("函数 ID", le)
        elif k == "delay":
            le = QLineEdit(step.delay_time)
            le.setPlaceholderText("1s / 5t / 1d")
            le.textChanged.connect(
                lambda v: (setattr(step, "delay_time", v), on_change()))
            form.addRow("延迟", le)
        elif k in ("if", "unless"):
            le = QLineEdit(step.condition)
            le.setPlaceholderText("例如 block ~ ~-1 ~ minecraft:stone")
            le.textChanged.connect(
                lambda v: (setattr(step, "condition", v), on_change()))
            form.addRow("条件（execute 子命令后的一段）", le)
        elif k == "repeat":
            sp = QSpinBox()
            sp.setRange(1, 9999)
            sp.setValue(max(1, int(step.repeat_count)))
            sp.valueChanged.connect(
                lambda v: (setattr(step, "repeat_count", int(v)), on_change()))
            form.addRow("重复次数", sp)
        else:
            form.addRow(QLabel(f"该类型 ({k}) 无可编辑属性。"))
        self.prop_stack.addWidget(page)

    def _refresh_tree_keep_selection(self) -> None:
        path = self._current_path()
        self._refresh_tree()
        self._select_by_path(path)

    def _current_path(self) -> list[int]:
        item = self.tree.currentItem()
        path: list[int] = []
        while item is not None:
            parent = item.parent()
            if parent is None:
                path.insert(0, self.tree.indexOfTopLevelItem(item))
                break
            path.insert(0, parent.indexOfChild(item))
            item = parent
        return path

    def _select_by_path(self, path: list[int]) -> None:
        if not path:
            return
        item = self.tree.topLevelItem(path[0])
        for idx in path[1:]:
            if item is None:
                return
            item = item.child(idx)
        if item is not None:
            self.tree.setCurrentItem(item)

    def _on_add(self) -> None:
        menu = QMenu(self)
        for key, label in STEP_KINDS:
            act = menu.addAction(label)
            act.setData(key)
        chosen = menu.exec(self.mapToGlobal(self.rect().topLeft()))
        if not chosen:
            return
        kind = chosen.data()
        step = Step(kind=kind)
        if kind == "command":
            step.command = "say hello"
        elif kind == "comment":
            step.comment = "说明"
        elif kind == "function":
            step.function = "minecraft:example"

        cur_item = self.tree.currentItem()
        cur_step = (cur_item.data(0, Qt.ItemDataRole.UserRole)
                    if cur_item is not None else None)
        if not isinstance(cur_step, Step):
            # 未选中或选中的是分类占位项时，追加到顶层
            self.script.steps.append(step)
        elif cur_step.kind in ("if", "unless", "repeat"):
            cur_step.children.append(step)
        else:
            parent_item = cur_item.parent()
            parent_step = (parent_item.data(0, Qt.ItemDataRole.UserRole)
                           if parent_item is not None else None)
            if isinstance(parent_step, Step):
                parent_step.children.append(step)
            else:
                self.script.steps.append(step)
        self._refresh_tree()

    def _on_delete(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        step = item.data(0, Qt.ItemDataRole.UserRole)
        parent_item = item.parent()
        if parent_item is None:
            try:
                self.script.steps.remove(step)
            except ValueError:
                pass
        else:
            parent_step = parent_item.data(0, Qt.ItemDataRole.UserRole)
            try:
                parent_step.children.remove(step)
            except ValueError:
                pass
        self._current_step = None
        self._refresh_tree()

    def _move(self, delta: int) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        step = item.data(0, Qt.ItemDataRole.UserRole)
        parent_item = item.parent()
        lst = (self.script.steps if parent_item is None
               else parent_item.data(0, Qt.ItemDataRole.UserRole).children)
        try:
            i = lst.index(step)
        except ValueError:
            return
        j = i + delta
        if j < 0 or j >= len(lst):
            return
        lst[i], lst[j] = lst[j], lst[i]
        self._refresh_tree()
        self._select_step(step)

    def _indent(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        step = item.data(0, Qt.ItemDataRole.UserRole)
        parent_item = item.parent()
        lst = (self.script.steps if parent_item is None
               else parent_item.data(0, Qt.ItemDataRole.UserRole).children)
        try:
            i = lst.index(step)
        except ValueError:
            return
        if i == 0:
            QMessageBox.information(
                self, "提示", "前面没有可作为父级的兄弟节点。")
            return
        prev = lst[i - 1]
        if prev.kind not in ("if", "unless", "repeat"):
            QMessageBox.information(
                self, "提示", "只能缩进到 if / unless / repeat 节点下面。")
            return
        lst.pop(i)
        prev.children.append(step)
        self._refresh_tree()
        self._select_step(step)

    def _outdent(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        parent_item = item.parent()
        if parent_item is None:
            return
        step = item.data(0, Qt.ItemDataRole.UserRole)
        parent_step = parent_item.data(0, Qt.ItemDataRole.UserRole)
        try:
            parent_step.children.remove(step)
        except ValueError:
            return
        grand_item = parent_item.parent()
        if grand_item is None:
            self.script.steps.append(step)
        else:
            grand_item.data(0, Qt.ItemDataRole.UserRole).children.append(step)
        self._refresh_tree()
        self._select_step(step)

    def _select_step(self, step: Step) -> None:
        items = self.tree.findItems(
            "", Qt.MatchFlag.MatchContains | Qt.MatchFlag.MatchRecursive, 0)
        for it in items:
            if it.data(0, Qt.ItemDataRole.UserRole) is step:
                self.tree.setCurrentItem(it)
                self.tree.scrollToItem(it)
                return

    def _on_clear(self) -> None:
        if QMessageBox.question(
                self, "清空脚本",
                "确定清空当前脚本吗？（不可撤销）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) \
                == QMessageBox.StandardButton.Yes:
            self.script.steps.clear()
            self._current_step = None
            self._refresh_tree()
            self._clear_prop()

    # ✅ P1-2 保守方案：工作台不处理 {xxx} 占位符
    def _collect_values_if_needed(self) -> dict[str, str] | None:
        return {}

    def _on_export_mcf(self) -> None:
        if not self.script.steps:
            QMessageBox.information(self, "提示", "脚本为空。")
            return
        values = self._collect_values_if_needed()
        if values is None:
            return
        text = self.script.to_mcfunction(values)
        path = QFileDialog.getExistingDirectory(self, "选择数据包导出目录")
        if not path:
            return
        name = self.script.name.strip() or "main"
        try:
            f = export_mcfunction(
                Path(path), text.splitlines(),
                namespace="mccmdgen", name=name,
                description=f"MCCmdGen workbench: {name}")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出成功", f"已生成：\n{f}")

    def _on_export_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "保存脚本",
            f"{self.script.name or 'script'}.mcscript.json",
            "MCCmdGen 脚本 (*.json);;所有文件 (*)")
        if not path:
            return
        try:
            Path(path).write_text(
                json.dumps(self.script.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return
        # 旧版写盘成功却一句话都不说（同面板的「导出 .mcfunction」有提示）
        _notify(self, f"已保存脚本到 {path}")

    def _on_import_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "加载脚本", "",
            "MCCmdGen 脚本 (*.json);;所有文件 (*)")
        if not path:
            return
        if self.script.steps:
            answer = QMessageBox.question(
                self, "加载脚本",
                f"当前脚本（{len(self.script.steps)} 步）还没有保存的内容会被替换。\n\n"
                "仍要加载吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self.script = CommandScript.from_dict(data)
        except Exception as e:
            QMessageBox.warning(self, "加载失败", str(e))
            return
        self.name_edit.blockSignals(True)
        self.name_edit.setText(self.script.name)
        self.name_edit.blockSignals(False)
        self.desc_edit.blockSignals(True)
        self.desc_edit.setText(self.script.description)
        self.desc_edit.blockSignals(False)
        self._refresh_tree()
        self._clear_prop()
        _notify(self, f"已加载脚本（{len(self.script.steps)} 步）。")

    def _on_send_to_output(self) -> None:
        if not self.script.steps:
            # 空脚本直接 emit 空串会把用户输出区清空（还报「已发送」）
            QMessageBox.information(
                self, "工作台是空的",
                "还没有任何步骤：先点「添加 ▾」插入一条子命令，"
                "或从底部「片段库」把片段拖进来。")
            return
        values = self._collect_values_if_needed()
        if values is None:
            return
        text = self.script.to_mcfunction(values)
        if not text.strip():
            _notify(self, "当前脚本没有可发送的内容。")
            return
        self.commandActivated.emit(text)

    # ---- 拖入 MIME_COMMAND / MIME_FRAGMENT ----
    def _drop_payload(self, mime) -> dict | None:
        """支持从命令面板拖来的命令，以及从片段库拖来的片段。"""
        payload = read_mime(mime, MIME_COMMAND)
        if payload is not None:
            text = str(payload.get("text", "")).strip()
            return {"text": text} if text else None
        payload = read_mime(mime, MIME_FRAGMENT)
        if payload is not None:
            # 片段载荷按 kind 使用不同键（text / snbt / id …），
            # 统一交给 _fragment_to_command 归一化，避免读错键。
            frag = _payload_to_fragment(payload)
            if frag is not None:
                text = _fragment_to_command(frag).strip()
                if text:
                    return {"text": text}
        return None

    def _accepts(self, mime) -> bool:
        return mime.hasFormat(MIME_COMMAND) or mime.hasFormat(MIME_FRAGMENT)

    def dragEnterEvent(self, event) -> None:
        if self._accepts(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self._accepts(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        payload = self._drop_payload(event.mimeData())
        if payload is None:
            super().dropEvent(event)
            return
        text = str(payload.get("text", "")).strip()
        if text:
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                kind = "comment" if line.startswith("#") else "command"
                step = Step(kind=kind, command=line, comment=line.lstrip("# "))
                self.script.steps.append(step)
            self._refresh_tree()
        event.acceptProposedAction()


# =========================================================================
# ReferencePanel —— ID 速查 / 中文名反查（新增功能）
# =========================================================================

class ReferencePanel(QWidget):
    """实体 / 物品 / 方块 / 组件 ID 速查表。

    支持中文名、拼音首字母、ID 片段模糊搜索；双击或点按钮可把 ID 发到输出区，
    也可以直接把整条示例命令发到输出区。
    """

    commandActivated = Signal(str)

    KINDS = [
        ("entity", "实体", "summon"),
        ("item", "物品", "give"),
        ("block", "方块", "setblock"),
        ("component", "物品组件", "give"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[dict] = []
        self._current: dict | None = None
        self._build_ui()
        self.reload()

    # ---- UI ----

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "搜索 ID / 中文名，例如 僵尸、zombie、diamond、照明")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        top.addWidget(self.search, 1)

        self.kind_combo = QComboBox()
        for key, label, _cmd in self.KINDS:
            self.kind_combo.addItem(f"{label}", key)
        self.kind_combo.addItem("全部", "any")
        self.kind_combo.currentIndexChanged.connect(self._reload_tree)
        top.addWidget(self.kind_combo)
        v.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["名称", "ID"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.currentItemChanged.connect(self._on_selected)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.setColumnWidth(0, 200)
        splitter.addWidget(self.tree)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("选中左侧条目查看详情…")
        rv.addWidget(self.detail, 1)

        row = QHBoxLayout()
        self.btn_copy = QPushButton("复制 ID")
        self.btn_copy.clicked.connect(self._copy_id)
        self.btn_example = QPushButton("示例命令 → 输出")
        self.btn_example.clicked.connect(self._send_example)
        row.addWidget(self.btn_copy)
        row.addWidget(self.btn_example)
        row.addStretch(1)
        rv.addLayout(row)
        self.btn_copy.setToolTip("复制选中条目的 ID")
        self.btn_example.setToolTip("把第一条示例命令送进输出区")
        # 没选中任何条目时不要装作能用（点了没反应最让人困惑）
        self.btn_copy.setEnabled(False)
        self.btn_example.setEnabled(False)

        splitter.addWidget(right)
        splitter.setSizes([460, 380])
        v.addWidget(splitter, 1)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet(_muted(self.count_label))
        v.addWidget(self.count_label)

    # ---- 数据 ----

    def reload(self) -> None:
        """从数据层重新载入全部条目（导入用户数据后调用）。"""
        self._entries = []
        try:
            self._entries.extend(self._collect("entity", data_store().entities()))
            self._entries.extend(self._collect("item", data_store().items()))
            self._entries.extend(self._collect("block", data_store().blocks()))
            self._entries.extend(
                self._collect("component", data_store().item_components()))
        except Exception:
            pass
        self._reload_tree()

    @staticmethod
    def _collect(kind: str, entries) -> list[dict]:
        out = []
        for e in entries or []:
            if not isinstance(e, dict):
                continue
            eid = str(e.get("id", "")).strip()
            if not eid:
                continue
            out.append({
                "kind": kind,
                "id": eid,
                "display": str(e.get("display", "")).strip(),
                "category": str(e.get("category", "")).strip() or "未分类",
                "raw": e,
            })
        return out

    def _kind_label(self, kind: str) -> str:
        return {"entity": "实体", "item": "物品",
                "block": "方块", "component": "物品组件"}.get(kind, kind)

    def _reload_tree(self, *_args) -> None:
        kind = self.kind_combo.currentData() or "any"
        self.tree.clear()
        by_cat: dict[tuple[str, str], list[dict]] = {}
        for e in self._entries:
            if kind != "any" and e["kind"] != kind:
                continue
            by_cat.setdefault((e["kind"], e["category"]), []).append(e)

        for (k, cat) in sorted(by_cat, key=lambda x: (x[0], x[1])):
            top = QTreeWidgetItem([f"【{self._kind_label(k)}】{cat}", ""])
            self.tree.addTopLevelItem(top)
            for e in sorted(by_cat[(k, cat)], key=lambda x: x["display"] or x["id"]):
                name = e["display"] or e["id"].split(":")[-1]
                child = QTreeWidgetItem([name, e["id"]])
                child.setData(0, Qt.ItemDataRole.UserRole, e)
                top.addChild(child)
        self.tree.expandToDepth(0)
        self._apply_filter()

    def _apply_filter(self) -> None:
        text = self.search.text().strip().lower()
        total = 0
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            visible = 0
            for j in range(top.childCount()):
                child = top.child(j)
                entry = child.data(0, Qt.ItemDataRole.UserRole) or {}
                hay = f"{child.text(0)} {child.text(1)} {entry.get('category', '')}".lower()
                hit = (not text) or (text in hay)
                child.setHidden(not hit)
                if hit:
                    visible += 1
            top.setHidden(visible == 0)
            total += visible
        if total == 0:
            if not self._entries:
                self.count_label.setText(
                    "没有可显示的数据 —— 可能是内置数据未加载；"
                    "菜单「数据 → 重新加载数据」可以重试。")
            elif text:
                self.count_label.setText(
                    f"没有匹配项（关键词：{self.search.text().strip()}）"
                    "　试试中文名，例如「僵尸」「钻石剑」")
            else:
                self.count_label.setText("共 0 条")
        else:
            self.count_label.setText(f"共 {total} 条")

    def _on_selected(self, current: QTreeWidgetItem, _prev) -> None:
        entry = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        self._current = entry if isinstance(entry, dict) else None
        has = self._current is not None
        self.btn_copy.setEnabled(has)
        self.btn_example.setEnabled(has)
        if not self._current:
            self.detail.setPlainText("")
            return
        e = self._current
        lines = [f"ID: {e['id']}", f"中文名: {e['display'] or '—'}",
                 f"类型: {self._kind_label(e['kind'])}", f"分类: {e['category']}",
                 "", "示例命令："]
        lines.extend(self._examples(e))
        lines.append("")
        lines.append("原始数据：")
        lines.append(json.dumps(e["raw"], ensure_ascii=False, indent=2))
        self.detail.setPlainText("\n".join(lines))

    # ---- 示例命令 ----

    def _examples(self, e: dict) -> list[str]:
        eid = e["id"]
        if e["kind"] == "entity":
            return [f"summon {eid} ~ ~ ~",
                    f"execute as @e[type={eid},limit=1] run say hi",
                    f"kill @e[type={eid}]"]
        if e["kind"] == "item":
            return [f"give @p {eid} 1",
                    f"give @p {eid} 1 [minecraft:custom_name=\"\\\"示例\\\"\"]"]
        if e["kind"] == "block":
            return [f"setblock ~ ~ ~ {eid}",
                    f"fill ~ ~ ~ ~5 ~5 ~5 {eid}"]
        return [f"give @p minecraft:diamond_sword[{eid}={e['raw'].get('default', '')}]"]

    def _copy_id(self) -> None:
        if not self._current:
            return
        QApplication.clipboard().setText(self._current["id"])
        self._msg(f"已复制 {self._current['id']}")

    def _send_example(self) -> None:
        if not self._current:
            return
        cmd = self._examples(self._current)[0]
        self.commandActivated.emit(cmd)

    def _on_double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(entry, dict):
            self.commandActivated.emit(self._examples(entry)[0])

    def _msg(self, text: str) -> None:
        _notify(self, text)


__all__ = [
    "SummonPanel", "GivePanel", "ExecutePanel", "SchemaPanel",
    "BatchPanel", "DatapackPanel", "HistoryPanel", "PresetPanel",
    "FragmentPanel", "WorkbenchPanel", "ReferencePanel", "FRAGMENT_MIME",
    "StatusBadge", "EmptyState",
]