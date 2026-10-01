"""NBT 树形编辑器（对标 NBTExplorer 的交互）。

布局与交互对齐 NBTExplorer：
    * 三列树：名称 / 类型 / 取值 —— 类型一眼可见，取值直接显示内容；
    * 标量可以**就地编辑**（双击取值列出现输入框 / 复选框 / 数值框），
      容器与数组则打开对应的对话框；
    * 树下面是**详情面板**：显示完整路径、类型、元素类型、项数、hex dump，
      容器还能直接添加/删除子标签、把数组展开成子节点；
    * 顶部**筛选框**实时过滤树（按键名 / 取值，高亮命中片段），不会因为
      文件很大就找不到东西；
    * 完整的右键菜单与快捷键（新建 / 重命名 / 删除 / 复制 / 剪切 / 粘贴 /
      复制一份 / 上移下移 / 展开折叠 / 撤销重做 / 查找替换）；
    * 拖放会校验 NBT 类型（List 只能放同类型元素、数组只能放对应整数），
      非法拖放直接拒绝而不是把数据写坏；
    * 刷新后按节点路径恢复选中与展开状态，编辑大文件不会跳走。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (
    QItemSelectionModel, QModelIndex, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QIcon, QKeySequence, QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QSplitter, QStackedWidget, QStyle,
    QStyledItemDelegate, QToolButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from app.core import settings as app_settings
from app.core.nbt_bridge import (
    ItemStack, enchant_display, item_stack_size, lookup_item,
    normalize_item_id, parse_item, roman, stack_to_command, stack_to_data_command,
    stack_to_snbt,
)
from app.core.snbt import (
    SnbtKind, SnbtNode, SnbtParseError, SnbtParser, make_snbt_highlighter,
    serialize,
)
from app.ui.theme import nbt_palette, nbt_type_colors
from app.ui.widgets.nbt_dialogs import (
    AddElementDialog, ArrayEditDialog, FindReplaceBar, ValueEditDialog,
    canonical_int, create_inline_editor, read_inline_editor,
)
from app.ui.widgets.nbt_tags import (
    ARRAY_KINDS, CREATABLE, ValueParseError, array_hexdump, array_preview,
    array_values, can_hold, container_summary, default_value, element_kind,
    filter_nodes, find_nodes, format_value, is_descendant, match_segments,
    node_path, node_path_of, node_trail, parse_data_path, path_segments,
    summarize, type_label, type_label_kind,
)

COL_NAME, COL_TYPE, COL_VALUE = 0, 1, 2

#: 树里最多渲染多少个数组 / 大 List 的元素，避免打开巨型文件时卡死
MAX_ELEMENT_ROWS = 256
#: 物品容器（背包 / 末影箱）默认展开的最大行数
AUTO_EXPAND_ITEMS = 64


class EditorItem(QTreeWidgetItem):
    """树节点：同时记住对应的 NBT 节点与完整路径。"""

    def __init__(self, node: SnbtNode, path: str):
        super().__init__()
        self.node = node
        self.path = path
        #: 识别出的物品（父节点是物品容器时才有）
        self.stack: Optional[ItemStack] = None
        # 兼容旧写法：以前节点是放在 UserRole 里的
        self.setData(COL_NAME, Qt.ItemDataRole.UserRole, node)


# ---------------------------------------------------------------------------
# 就地编辑委托
# ---------------------------------------------------------------------------

def node_type_icon(kind: SnbtKind, color: str, size: int = 12):
    """按类型给一个着色的方形小图标（NBTExplorer 那套类型图标的意思）。"""
    from PySide6.QtGui import QPainter, QPixmap
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        qcolor = QColor(color)
        if kind == SnbtKind.COMPOUND:
            # 复合标签：实心方块
            painter.setBrush(qcolor)
            painter.drawRoundedRect(1, 1, size - 2, size - 2, 3, 3)
        elif kind == SnbtKind.LIST:
            # 列表：空心方块
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pen = painter.pen()
            pen.setColor(qcolor)
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRoundedRect(2, 2, size - 4, size - 4, 2, 2)
        elif kind in ARRAY_KINDS:
            # 数组：三道横线
            painter.setBrush(qcolor)
            for offset in (3, 6, 9):
                painter.drawRect(2, offset - 1, size - 4, 1)
            painter.drawRect(2, 2, 1, size - 4)
            painter.drawRect(size - 3, 2, 1, size - 4)
        elif kind == SnbtKind.STRING:
            # 字符串：一个小圆点
            painter.setBrush(qcolor)
            painter.drawEllipse(4, 4, size - 8, size - 8)
        else:
            # 数值：一个实心小方块
            painter.setBrush(qcolor)
            painter.drawRect(4, 4, size - 8, size - 8)
    finally:
        painter.end()
    return pixmap


class _ValueDelegate(QStyledItemDelegate):
    """取值列就地编辑 + 命中高亮的委托。

    * 双击 / 回车时按类型给出合适的控件，并做范围校验；
    * 容器与数组不就地编辑（交给对话框与详情面板）。
    """

    def createEditor(self, parent, option, index):  # noqa: N802
        node = _node_of(index)
        if node is None or node.is_container:
            return None
        return create_inline_editor(node, parent)

    def setEditorData(self, editor, index) -> None:  # noqa: N802
        node = _node_of(index)
        if node is None:
            return
        if node.kind == SnbtKind.BOOL:
            editor.setChecked(bool(node.value))
        elif isinstance(editor, QLineEdit):
            editor.setText(str(node.value) if node.kind == SnbtKind.STRING
                           else format_value(node))
        elif hasattr(editor, "setValue"):
            try:
                editor.setValue(node.value)
            except (TypeError, ValueError):
                pass

    def setModelData(self, editor, model, index) -> None:  # noqa: N802
        node = _node_of(index)
        if node is None:
            return
        try:
            value = read_inline_editor(node, editor)
        except (ValueParseError, TypeError, ValueError):
            return
        model.setData(index, format_value(SnbtNode(node.kind, value=value)),
                      Qt.ItemDataRole.EditRole)


def _node_of(index: QModelIndex) -> Optional[SnbtNode]:
    item = index.internalPointer()
    if isinstance(item, EditorItem):
        return item.node
    return None


class _HighlightDelegate(_ValueDelegate):
    """取值列委托 + 查找/筛选命中片段高亮。"""

    def __init__(self, tree: QTreeWidget):
        super().__init__(tree)
        self.tree = tree
        self.needle = ""
        self.case_sensitive = False
        self.exact = False

    def set_needle(self, needle: str, case_sensitive: bool = False,
                   exact: bool = False) -> None:
        self.needle = needle or ""
        self.case_sensitive = case_sensitive
        self.exact = exact
        self.tree.viewport().update()

    def paint(self, painter, option, index) -> None:  # noqa: N802
        super().paint(painter, option, index)
        if not self.needle or index.column() not in (COL_NAME, COL_VALUE):
            return
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return
        try:
            spans = match_segments(str(text), self.needle,
                                   self.case_sensitive, self.exact)
            if not spans:
                return
            self._paint_spans(painter, option, str(text), spans)
        except Exception:                                  # pragma: no cover
            # 高亮只是锦上添花，任何绘制问题都不该影响树的显示
            pass

    def _paint_spans(self, painter, option, text: str, spans) -> None:
        from PySide6.QtGui import QColor
        metrics = option.fontMetrics
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight,
                                    max(0, option.rect.width() - 8))
        painter.save()
        color = QColor(self.tree.palette().highlight().color())
        color.setAlpha(110)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        left = option.rect.left() + 4
        for start, end in spans:
            if start >= len(elided):
                break
            end = min(end, len(elided))
            x1 = left + metrics.horizontalAdvance(elided[:start])
            x2 = left + metrics.horizontalAdvance(elided[:end])
            painter.drawRect(x1, option.rect.top() + 2,
                             max(1, x2 - x1), option.rect.height() - 4)
        painter.restore()


# ---------------------------------------------------------------------------
# 树控件
# ---------------------------------------------------------------------------

class _TagTree(QTreeWidget):
    """树控件：接管拖放（让模型跟着动）与回车编辑。"""

    def __init__(self, owner: "NbtTreeEditor"):
        super().__init__(owner)
        self.owner = owner

    # ---- 拖放：自己改模型，避免 Qt 默认只挪 item 造成界面与数据不一致 ----

    def _internal_drop(self, event) -> bool:
        """是否是本控件内部的拖动。

        真实鼠标拖动时 source() 是 self；用脚本构造的 QDropEvent 没有 source，
        这种情况下再退一步看：InternalMove 模式 + 有可拖动的当前节点。
        """
        source = event.source()
        if source is self:
            return True
        if source is not None:
            return False
        return (self.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove
                and self.currentItem() is not None)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._internal_drop(event):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._internal_drop(event):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        if not self._internal_drop(event) \
                or not self.owner.move_dropped_item(event):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            item = self.currentItem()
            if item is not None:
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.owner.rename_selected()
                else:
                    self.owner.edit_value(item)
                return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# 编辑器
# ---------------------------------------------------------------------------

class NbtTreeEditor(QWidget):
    """树形 NBT 编辑器（树 + 详情面板 + 文本 SNBT 模式）。"""

    changed = Signal()
    statusMessage = Signal(str)
    requestNewFile = Signal()
    #: 请求把识别出的物品送进 `/give` 面板
    giveRequested = Signal(object)
    #: 请求把识别出的实体送进 `/summon` 面板
    summonRequested = Signal(object)
    #: 请求打开图形化的存档物品一览
    overviewRequested = Signal()
    #: 请求打开设置（专有名词翻译等）
    settingsRequested = Signal()

    MAX_HISTORY = 100
    HEX_SETTING_KEY = "nbt_hex_mode"
    #: 兼容旧名字
    changed_signal = changed

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root: SnbtNode = SnbtNode.new_compound()
        self._root_name = ""
        self._updating = False
        self._applying = False
        self._text_timer: Optional[QTimer] = None
        self._history: list[tuple[str, str]] = []
        self._redo: list[tuple[str, str]] = []
        self._clipboard: SnbtNode | None = None
        self._clipboard_source: SnbtNode | None = None
        self._hex_mode = False
        self._byte_text_mode = False
        self._details_visible = True
        self._find_index = -1
        self._filter_needle = ""
        self._settings_loaded = False
        self._compact = False
        self._compact_buttons: list[tuple[QToolButton, str, str]] = []
        self._auto_expand: set[str] = set()
        self._auto_expand_capped: set[str] = set()
        self._item_index: dict[str, frozenset] = {}
        self._item_paths: set[str] = set()
        self._item_cache: dict[str, Optional[ItemStack]] = {}
        self._item_counts: dict[str, int] = {}
        self._building_items = False
        self._show_icons = True
        self._item_summary = True
        self._source_label = ""
        self._filter_keep: set[int] | None = None
        self._filter_source: tuple | None = None
        self._expand_all_mode = False
        self._type_colors = nbt_type_colors(self)
        self._palette_colors = nbt_palette(self)
        self._build_ui()
        self._load_settings()
        self._settings_loaded = True
        self._refresh_all()
        self._apply_compact_mode()
        # 设置里改了翻译 / 图标，界面要立刻跟着变
        self._unsubscribe_settings = app_settings.subscribe(
            self._on_settings_changed)

    def _on_settings_changed(self, _data: dict) -> None:
        if getattr(self, "_applying", False):
            return
        self._refresh_theme_colors()
        self._refresh_tree()
        self._refresh_details()

    # ==================================================================
    # 对外接口
    # ==================================================================

    def set_root(self, node: SnbtNode, push_history: bool = False) -> None:
        if push_history:
            self._push_history()
        self._root = node
        self._filter_source = None      # 换了一棵树，筛选要重算
        self._filter_keep = None
        self._refresh_all()

    def root(self) -> SnbtNode:
        return self._root

    def set_root_name(self, name: str) -> None:
        """设置根标签名（level.dat 的根通常没有名字）。"""
        self._root_name = name or ""
        self._refresh_tree()

    def set_source_label(self, label: str) -> None:
        """记下数据来源（文件名），只用于 tooltip 与物品一览标题。"""
        self._source_label = label or ""

    def root_name(self) -> str:
        return self._root_name

    def set_snbt(self, text: str) -> bool:
        try:
            node = SnbtParser(text).parse()
        except SnbtParseError as exc:
            self._show_error(str(exc))
            return False
        self._root = node
        self._show_error("")
        self._refresh_all()
        self.changed.emit()
        return True

    def to_snbt(self, pretty: bool = True) -> str:
        return serialize(self._root, pretty=pretty)

    def selected_node(self) -> Optional[SnbtNode]:
        item = self.tree.currentItem()
        return item.node if isinstance(item, EditorItem) else None

    def selected_nodes(self) -> list[SnbtNode]:
        return [it.node for it in self.tree.selectedItems()
                if isinstance(it, EditorItem)]

    def current_path(self) -> str:
        item = self.tree.currentItem()
        if isinstance(item, EditorItem):
            return item.path
        return ""

    # ==================================================================
    # 界面
    # ==================================================================

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(4)
        self.btn_tree = QToolButton()
        self.btn_tree.setText("树形")
        self.btn_tree.setCheckable(True)
        self.btn_tree.setChecked(True)
        self.btn_tree.setToolTip("按 NBT 结构逐项编辑（推荐）")
        self.btn_tree.clicked.connect(lambda: self._set_mode("tree"))
        self.btn_text = QToolButton()
        self.btn_text.setText("文本 SNBT")
        self.btn_text.setCheckable(True)
        self.btn_text.setToolTip("直接编辑 SNBT 文本（带高亮与错误定位）")
        self.btn_text.clicked.connect(lambda: self._set_mode("text"))
        top.addWidget(self.btn_tree)
        top.addWidget(self.btn_text)
        top.addStretch(1)
        layout.addLayout(top)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_tree_page())
        self.stack.addWidget(self._build_text_page())
        layout.addWidget(self.stack, 1)

    # ---- 工具条 ----

    def _tool_button(self, icon_name: str, text: str, tip: str, slot,
                     checkable: bool = False) -> QToolButton:
        """工具条按钮：宽窗口显示「图标 + 文字」，窄窗口自动只留图标。"""
        btn = QToolButton()
        btn.setAutoRaise(True)
        btn.setToolTip(tip)
        btn.setCheckable(checkable)
        icon = None
        try:
            standard = getattr(QStyle.StandardPixmap, icon_name, None)
            if standard is not None:
                icon = self.style().standardIcon(standard)
        except Exception:                                  # pragma: no cover
            icon = None
        has_icon = icon is not None and not icon.isNull()
        if has_icon:
            from PySide6.QtCore import QSize
            btn.setIcon(icon)
            btn.setIconSize(QSize(14, 14))
        if text:
            btn.setText(text)
            btn.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextBesideIcon if has_icon
                else Qt.ToolButtonStyle.ToolButtonTextOnly)
        else:
            btn.setText("")
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        btn.clicked.connect(slot)
        return btn

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFixedHeight(18)
        return line

    def _tree_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(2)
        self.btn_undo = self._tool_button(
            "SP_ArrowBack", "撤销", "撤销 (Ctrl+Z)", self.undo)
        self.btn_redo = self._tool_button(
            "SP_ArrowForward", "重做", "重做 (Ctrl+Y)", self.redo)
        for btn in (self.btn_undo, self.btn_redo):
            row.addWidget(btn)
        row.addWidget(self._separator())
        self.btn_new = self._tool_button(
            "SP_FileDialogNewFolder", "新建", "新建标签（Ctrl+N / Insert）",
            self.new_child_dialog)
        row.addWidget(self.btn_new)
        self.btn_rename = self._tool_button(
            "SP_FileDialogInfoView", "重命名", "重命名标签 (F2 / Shift+回车)",
            self.rename_selected)
        row.addWidget(self.btn_rename)
        self.btn_duplicate = self._tool_button(
            "SP_FileDialogNewFolder", "复制一份", "复制一份 (Ctrl+D)",
            self.duplicate_selected)
        row.addWidget(self.btn_duplicate)
        self.btn_copy = self._tool_button(
            "SP_FileIcon", "复制", "复制标签 (Ctrl+C)", self.copy_selected)
        row.addWidget(self.btn_copy)
        self.btn_paste = self._tool_button(
            "SP_DialogApplyButton", "粘贴", "粘贴为子节点 (Ctrl+V)",
            self.paste_into_selected)
        row.addWidget(self.btn_paste)
        self.btn_delete = self._tool_button(
            "SP_TrashIcon", "删除", "删除标签 (Delete)", self.delete_selected)
        row.addWidget(self.btn_delete)
        row.addWidget(self._separator())
        self.btn_move_up = self._tool_button(
            "SP_ArrowUp", "", "上移一位 (Alt+↑)",
            lambda: self.move_selected(-1))
        row.addWidget(self.btn_move_up)
        self.btn_move_down = self._tool_button(
            "SP_ArrowDown", "", "下移一位 (Alt+↓)",
            lambda: self.move_selected(1))
        row.addWidget(self.btn_move_down)
        row.addWidget(self._separator())
        self.btn_expand = self._tool_button(
            "SP_TitleBarUnshadeButton", "展开", "全部展开 (Ctrl+Shift+E)",
            self.expand_all)
        row.addWidget(self.btn_expand)
        self.btn_collapse = self._tool_button(
            "SP_TitleBarShadeButton", "折叠", "全部折叠 (Ctrl+Shift+W)",
            self.collapse_all)
        row.addWidget(self.btn_collapse)
        row.addWidget(self._separator())
        self.btn_hex = self._tool_button(
            "SP_FileDialogListView", "字节视图",
            "数组按十六进制显示（字节视图）", self._toggle_hex_mode,
            checkable=True)
        self.btn_hex.setChecked(self._hex_mode)
        row.addWidget(self.btn_hex)
        self.btn_byte_text = self._tool_button(
            "SP_FileDialogContentsView", "字节转字符串",
            "在详情面板把 ByteArray 按 UTF-8 显示",
            self._toggle_byte_text, checkable=True)
        self.btn_byte_text.setChecked(self._byte_text_mode)
        row.addWidget(self.btn_byte_text)
        self.btn_details = self._tool_button(
            "SP_FileDialogDetailedView", "详情面板",
            "显示 / 隐藏树下面的详情面板（Ctrl+I）",
            self.toggle_details, checkable=True)
        self.btn_details.setChecked(self._details_visible)
        row.addWidget(self.btn_details)
        row.addStretch(1)
        self.btn_template = self._tool_button(
            "SP_DialogHelpButton", "插入模板…",
            "从内置模板库插入常用 NBT 结构", self._on_insert_template)
        row.addWidget(self.btn_template)
        # 窄窗口（例如 /summon 面板里的小编辑器）自动收成纯图标
        for btn, text in ((self.btn_undo, "撤销"), (self.btn_redo, "重做"),
                          (self.btn_new, "新建"), (self.btn_rename, "重命名"),
                          (self.btn_duplicate, "复制一份"),
                          (self.btn_copy, "复制"), (self.btn_paste, "粘贴"),
                          (self.btn_delete, "删除"),
                          (self.btn_expand, "展开"),
                          (self.btn_collapse, "折叠"),
                          (self.btn_hex, "字节视图"),
                          (self.btn_byte_text, "字节转字符串"),
                          (self.btn_details, "详情面板"),
                          (self.btn_template, "插入模板…")):
            self._compact_buttons.append((btn, text, btn.toolTip()))
        return row

    # ---- 窄窗口自适应 ----

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_compact_mode()

    def _apply_compact_mode(self) -> None:
        """宽度不够时把工具条按钮收成图标，避免按钮被挤出可视区域。"""
        compact = self.width() < 1080
        if compact == self._compact and self._compact_buttons:
            return
        self._compact = compact
        for btn, text, tip in self._compact_buttons:
            if compact:
                btn.setText("")
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            else:
                btn.setText(text)
                btn.setToolButtonStyle(
                    Qt.ToolButtonStyle.ToolButtonTextBesideIcon
                    if not btn.icon().isNull()
                    else Qt.ToolButtonStyle.ToolButtonTextOnly)
            btn.setToolTip(tip)
        if hasattr(self, "filter_types"):
            self.filter_types.setVisible(not compact)
            # 窄窗口里省略「筛选」两个字的标签
            for label in self.findChildren(QLabel):
                if label.text() == "筛选":
                    label.setVisible(not compact)
        if hasattr(self, "btn_add_child"):
            # 详情面板的动作按钮也收短一点
            for btn, full, short in (
                    (self.btn_add_child, "新建子标签…", "新建"),
                    (self.btn_add_item, "追加元素…", "追加元素"),
                    (self.btn_expand_items, "展开元素", "展开元素"),
                    (self.btn_edit_value, "编辑取值…", "编辑取值"),
                    (self.btn_delete_sel, "删除", "删除")):
                btn.setText(short if compact else full)

    # ---- 树页 ----

    def _build_tree_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        self._toolbar = self._tree_toolbar()
        v.addLayout(self._toolbar)

        self.filter_row = QWidget()
        frow = QHBoxLayout(self.filter_row)
        frow.setContentsMargins(0, 0, 0, 0)
        frow.setSpacing(6)
        frow.addWidget(QLabel("筛选"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(
            "输入关键字筛选标签（键名 + 取值），Ctrl+F 聚焦")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._on_filter_changed)
        frow.addWidget(self.filter_edit, 1)
        self.filter_keys = QCheckBox("名称")
        self.filter_keys.setToolTip("匹配标签名（默认开）")
        self.filter_keys.setChecked(True)
        self.filter_keys.toggled.connect(self._apply_filter)
        frow.addWidget(self.filter_keys)
        self.filter_values = QCheckBox("取值")
        self.filter_values.setToolTip("匹配取值内容，例如物品 id、名字（默认开）")
        self.filter_values.setChecked(True)
        self.filter_values.toggled.connect(self._apply_filter)
        frow.addWidget(self.filter_values)
        self.filter_case = QCheckBox("区分大小写")
        self.filter_case.toggled.connect(self._apply_filter)
        frow.addWidget(self.filter_case)
        self.filter_types = QComboBox()
        self.filter_types.addItem("全部类型", None)
        for label, kind in CREATABLE:
            translated = app_settings.translate_nbt_type(label)
            text = label if translated == label else f"{label}（{translated}）"
            self.filter_types.addItem(text, kind)
        self.filter_types.setToolTip("只看某个类型的标签")
        self.filter_types.currentIndexChanged.connect(self._apply_filter)
        frow.addWidget(self.filter_types)
        self.filter_count = QLabel("")
        self.filter_count.setStyleSheet(
            f"color: {self._palette_colors['muted']};")
        frow.addWidget(self.filter_count)
        v.addWidget(self.filter_row)

        self.main_split = QSplitter(Qt.Orientation.Vertical)
        self.tree = _TagTree(self)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["名称", "类型", "取值"])
        self.tree.setColumnWidth(COL_NAME, 260)
        # 类型列要能放下 Compound / ByteArray / LongArray，不然会折成两行
        self.tree.setColumnWidth(COL_TYPE, 108)
        header = self.tree.header()
        header.setMinimumSectionSize(60)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_VALUE, QHeaderView.ResizeMode.Stretch)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        # 就地编辑交给委托（标量）与对话框（容器 / 数组）
        self.tree.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.value_delegate = _HighlightDelegate(self.tree)
        self.tree.setItemDelegate(self.value_delegate)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tree.setToolTip(
            "双击取值列可直接编辑；把节点拖到别的 Compound / 同类型 List 上可移动")
        self.main_split.addWidget(self.tree)
        self._details_panel = self._build_details()
        self.main_split.addWidget(self._details_panel)
        self.main_split.setStretchFactor(0, 3)
        self.main_split.setStretchFactor(1, 2)
        self.main_split.setSizes([420, 240])
        v.addWidget(self.main_split, 1)

        self.find_bar = FindReplaceBar()
        self.find_bar.setVisible(False)
        self.find_bar.find_edit.returnPressed.connect(self.find_next)
        self.find_bar.btn_next.clicked.connect(self.find_next)
        self.find_bar.btn_prev.clicked.connect(
            lambda: self.find_next(forward=False))
        self.find_bar.btn_replace.clicked.connect(self.replace_current)
        self.find_bar.btn_replace_all.clicked.connect(self.replace_all)
        v.addWidget(self.find_bar)

        self.status = QLabel("")
        self.status.setStyleSheet(
            f"color: {self._palette_colors['muted']}; font-size: 12px;")
        self.status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.status)

        for keys, slot in (
                ("Ctrl+Z", self.undo),
                ("Ctrl+Shift+Z", self.redo),
                ("Ctrl+Y", self.redo),
                ("Ctrl+C", self.copy_selected),
                ("Ctrl+X", self.cut_selected),
                ("Ctrl+V", self.paste_into_selected),
                ("Ctrl+D", self.duplicate_selected),
                ("Ctrl+N", self.new_child_dialog),
                ("Insert", self.new_child_dialog),
                ("F2", self.rename_selected),
                ("Delete", self.delete_selected),
                ("Ctrl+F", self.focus_filter),
                ("Ctrl+I", self.toggle_details),
                ("Ctrl+Shift+F", self.show_find_bar),
                ("Ctrl+Shift+E", self.expand_all),
                ("Ctrl+Shift+W", self.collapse_all),
                ("Alt+Up", lambda: self.move_selected(-1)),
                ("Alt+Down", lambda: self.move_selected(1))):
            shortcut = QShortcut(QKeySequence(keys), self.tree)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(slot)
        # Ctrl+Shift+N 是主窗口「打开 NBT 文件」的快捷键，这里换一个，
        # 免得两处冲突：Ctrl+Shift+M 与主窗口的「新建 NBT 文档」一致。
        new_file = QShortcut(QKeySequence("Ctrl+Shift+M"), self.tree)
        new_file.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        new_file.activated.connect(self.requestNewFile.emit)
        return page

    # ---- 详情面板 ----

    def _build_details(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(6, 4, 6, 4)
        v.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.detail_type = QLabel("—")
        self.detail_type.setStyleSheet(
            "font-weight: bold; padding: 1px 6px; border-radius: 3px;")
        head.addWidget(self.detail_type)
        self.detail_meta = QLabel("")
        self.detail_meta.setStyleSheet(
            f"color: {self._palette_colors['muted']};")
        head.addWidget(self.detail_meta, 1)
        v.addLayout(head)

        self.detail_path = QLineEdit("")
        self.detail_path.setReadOnly(True)
        self.detail_path.setToolTip("选中标签的完整路径（可选中复制）")
        v.addWidget(self.detail_path)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.btn_add_child = QPushButton("新建子标签…")
        self.btn_add_child.clicked.connect(self.new_child_dialog)
        self.btn_add_item = QPushButton("追加元素…")
        self.btn_add_item.setToolTip("给 List / 数组追加一个或多个元素")
        self.btn_add_item.clicked.connect(self.append_element)
        self.btn_expand_items = QPushButton("展开元素")
        self.btn_expand_items.setToolTip(
            f"把这个容器的元素渲染成子节点（最多 {MAX_ELEMENT_ROWS} 个）")
        self.btn_expand_items.clicked.connect(self.expand_selected_elements)
        self.btn_edit_value = QPushButton("编辑取值…")
        self.btn_edit_value.clicked.connect(self.edit_selected_value)
        self.btn_delete_sel = QPushButton("删除")
        self.btn_delete_sel.clicked.connect(self.delete_selected)
        self.btn_overview = QPushButton("存档物品一览…")
        self.btn_overview.setToolTip(
            "把存档里的背包 / 箱子内容列成表，可整批送进 /give 面板")
        self.btn_overview.clicked.connect(self.overviewRequested.emit)
        for btn in (self.btn_add_child, self.btn_add_item,
                    self.btn_expand_items, self.btn_edit_value,
                    self.btn_delete_sel, self.btn_overview):
            actions.addWidget(btn)
        actions.addStretch(1)
        v.addLayout(actions)

        self.detail_stack = QStackedWidget()
        self.detail_stack.addWidget(self._build_container_view())
        self.detail_stack.addWidget(self._build_scalar_view())
        self.detail_stack.addWidget(self._build_item_view())
        self.detail_stack.addWidget(self._build_entity_view())
        v.addWidget(self.detail_stack, 1)
        self._build_detail_pages()
        return panel

    def _build_entity_view(self) -> QWidget:
        """识别出实体（生物 / 投掷物…）时的卡片。"""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.entity_icon = QLabel()
        self.entity_icon.setFixedSize(48, 48)
        self.entity_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.entity_icon.setStyleSheet(
            "border: 1px solid palette(mid); border-radius: 6px;"
            " font-size: 20px; font-weight: bold;")
        row.addWidget(self.entity_icon)
        info = QVBoxLayout()
        info.setSpacing(2)
        self.entity_title = QLabel("")
        font = self.entity_title.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        self.entity_title.setFont(font)
        info.addWidget(self.entity_title)
        self.entity_meta = QLabel("")
        self.entity_meta.setStyleSheet(
            f"color: {self._palette_colors['muted']}; font-size: 12px;")
        self.entity_meta.setWordWrap(True)
        info.addWidget(self.entity_meta)
        self.entity_id_label = QLabel("")
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.entity_id_label.setFont(mono)
        self.entity_id_label.setStyleSheet(
            f"color: {self._palette_colors['muted']};")
        info.addWidget(self.entity_id_label)
        row.addLayout(info, 1)
        v.addLayout(row)

        self.entity_nbt = QPlainTextEdit()
        self.entity_nbt.setReadOnly(True)
        self.entity_nbt.setFont(mono)
        self.entity_nbt.setMaximumHeight(96)
        v.addWidget(self.entity_nbt, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.btn_entity_summon = QPushButton("→ /summon 面板")
        self.btn_entity_summon.setToolTip(
            "把实体类型与这份 NBT 填进 /summon 面板")
        self.btn_entity_summon.clicked.connect(self._send_entity_to_summon)
        buttons.addWidget(self.btn_entity_summon)
        self.btn_entity_command = QPushButton("复制 /summon 命令")
        self.btn_entity_command.clicked.connect(self.copy_entity_command)
        buttons.addWidget(self.btn_entity_command)
        self.btn_entity_nbt_copy = QPushButton("复制实体 NBT")
        self.btn_entity_nbt_copy.clicked.connect(self.copy_entity_nbt)
        buttons.addWidget(self.btn_entity_nbt_copy)
        self.btn_entity_items = QPushButton("查看它的物品…")
        self.btn_entity_items.setToolTip("打开图形化的物品一览（含这个实体的装备）")
        self.btn_entity_items.clicked.connect(self.overviewRequested.emit)
        buttons.addWidget(self.btn_entity_items)
        buttons.addStretch(1)
        v.addLayout(buttons)
        return page

    def _build_item_view(self) -> QWidget:
        """识别出物品时的图形化卡片。"""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.item_icon = QLabel()
        self.item_icon.setFixedSize(48, 48)
        self.item_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.item_icon.setStyleSheet(
            "border: 1px solid palette(mid); border-radius: 6px;"
            " font-size: 20px; font-weight: bold;")
        row.addWidget(self.item_icon)

        info = QVBoxLayout()
        info.setSpacing(2)
        self.item_name = QLabel("")
        font = self.item_name.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        self.item_name.setFont(font)
        info.addWidget(self.item_name)
        self.item_meta = QLabel("")
        self.item_meta.setStyleSheet(
            f"color: {self._palette_colors['muted']}; font-size: 12px;")
        self.item_meta.setWordWrap(True)
        info.addWidget(self.item_meta)
        self.item_id = QLabel("")
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.item_id.setFont(mono)
        self.item_id.setStyleSheet(
            f"color: {self._palette_colors['muted']};")
        info.addWidget(self.item_id)
        row.addLayout(info, 1)
        v.addLayout(row)

        self.item_components = QPlainTextEdit()
        self.item_components.setReadOnly(True)
        self.item_components.setFont(mono)
        self.item_components.setMaximumHeight(96)
        self.item_components.setPlaceholderText("（没有组件）")
        v.addWidget(self.item_components, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.btn_item_give = QPushButton("→ /give 面板")
        self.btn_item_give.setToolTip(
            "把这件物品（含全部组件）填进 /give 编辑面板")
        self.btn_item_give.clicked.connect(self._send_item_to_give)
        buttons.addWidget(self.btn_item_give)
        self.btn_item_command = QPushButton("复制 /give 命令")
        self.btn_item_command.clicked.connect(self.copy_item_command)
        buttons.addWidget(self.btn_item_command)
        self.btn_item_snbt = QPushButton("复制物品 SNBT")
        self.btn_item_snbt.setToolTip("复制成存档里的 SNBT（可配合 /data 命令）")
        self.btn_item_snbt.clicked.connect(self.copy_item_snbt)
        buttons.addWidget(self.btn_item_snbt)
        self.btn_item_data = QPushButton("复制 /data 命令")
        self.btn_item_data.clicked.connect(self.copy_item_data_command)
        buttons.addWidget(self.btn_item_data)
        buttons.addStretch(1)
        v.addLayout(buttons)
        return page

    def _build_container_view(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.detail_preview = QPlainTextEdit()
        self.detail_preview.setReadOnly(True)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.detail_preview.setFont(font)
        self.detail_preview.setPlaceholderText(
            "选中 Compound / List / 数组后这里显示内容预览")
        v.addWidget(self.detail_preview, 1)
        self.detail_hint = QLabel("")
        self.detail_hint.setWordWrap(True)
        self.detail_hint.setStyleSheet(
            f"color: {self._palette_colors['muted']}; font-size: 12px;")
        v.addWidget(self.detail_hint)
        return page

    def _build_scalar_view(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.detail_value = QLineEdit()
        self.detail_value.setPlaceholderText("取值")
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.detail_value.setFont(font)
        self.detail_value.returnPressed.connect(self.apply_detail_value)
        self.detail_value.editingFinished.connect(self.apply_detail_value)
        v.addWidget(self.detail_value)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_apply_value = QPushButton("应用")
        self.btn_apply_value.setToolTip("把上面的取值写回标签（回车也可以）")
        self.btn_apply_value.clicked.connect(self.apply_detail_value)
        row.addWidget(self.btn_apply_value)
        self.btn_value_dialog = QPushButton("完整编辑…")
        self.btn_value_dialog.setToolTip("打开按类型定制的编辑窗口（字符串多行）")
        self.btn_value_dialog.clicked.connect(self.edit_selected_value)
        row.addWidget(self.btn_value_dialog)
        row.addStretch(1)
        self.detail_value_state = QLabel("")
        self.detail_value_state.setStyleSheet(
            f"color: {self._palette_colors['muted']}; font-size: 12px;")
        row.addWidget(self.detail_value_state)
        v.addLayout(row)
        v.addStretch(1)
        return page

    def _build_detail_pages(self) -> None:
        self.detail_container_page = self.detail_stack.widget(0)
        self.detail_scalar_page = self.detail_stack.widget(1)
        self.detail_item_page = self.detail_stack.widget(2)
        self.detail_entity_page = self.detail_stack.widget(3)
        self.detail_stack.setCurrentWidget(self.detail_container_page)
        self._details_ready = True

    # ---- 文本页 ----

    def _build_text_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        self.text = QPlainTextEdit()
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.text.setFont(font)
        try:
            metrics = self.text.fontMetrics()
            self.text.setTabStopDistance(4 * metrics.horizontalAdvance(" "))
        except Exception:                                  # pragma: no cover
            pass
        self._highlighter = make_snbt_highlighter(self.text.document())
        self.text.textChanged.connect(self._on_text_changed)
        v.addWidget(self.text, 1)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(
            f"color: {self._palette_colors['error']}; padding: 2px;")
        v.addWidget(self.error_label, 0)
        return page

    def _set_mode(self, mode: str) -> None:
        if mode == "text":
            self.btn_text.setChecked(True)
            self.btn_tree.setChecked(False)
            self._refresh_text()
            self.stack.setCurrentIndex(1)
        else:
            self.btn_tree.setChecked(True)
            self.btn_text.setChecked(False)
            self.stack.setCurrentIndex(0)
            self._refresh_tree()

    def show_find_bar(self) -> None:
        self.find_bar.setVisible(True)
        self.find_bar.find_edit.setFocus()
        self.find_bar.find_edit.selectAll()

    def focus_filter(self) -> None:
        self.filter_edit.setFocus()
        self.filter_edit.selectAll()

    # ==================================================================
    # 渲染
    # ==================================================================

    def _refresh_all(self) -> None:
        self.summary_text = summarize(self._root)
        self._refresh_theme_colors()
        self._refresh_tree()
        self._refresh_text()
        self._refresh_details()

    def _refresh_theme_colors(self) -> None:
        self._type_colors = nbt_type_colors(self)
        self._palette_colors = nbt_palette(self)
        self._show_icons = app_settings.nbt_show_icons()
        self._item_summary = app_settings.nbt_item_summary()
        if not self._item_summary:
            # 关闭「物品摘要」后不再识别物品
            self._item_index = {}
            self._item_paths = set()
            self._item_cache = {}

    @staticmethod
    def _step_for(node: SnbtNode, position: int) -> str:
        """子节点路径的连接符：Compound 用 `.键名`，List / 数组用 `[下标]`。

        根节点（``path`` 为空）的第一个键名不带点，这样路径与
        `Data.Player.Inventory[0]` 完全一致。
        """
        if node.kind == SnbtKind.COMPOUND:
            key = node.children[position].key
            return f".{key}" if key is not None else ""
        return f"[{position}]"

    def _join_step(self, parent_path: str, node: SnbtNode,
                   position: int) -> str:
        step = self._step_for(node, position)
        if step.startswith(".") and not parent_path:
            return step[1:]
        return step

    def _build_item_index(self) -> None:
        """预先找出「物品容器 → 子节点下标」以及物品节点所在的路径。

        只在开启「物品摘要」时构建；大文件里也只多做一次线性遍历。
        """
        index: dict[str, frozenset] = {}
        item_paths: set[str] = set()
        self._item_cache = {}
        self._item_counts = {}
        self._building_items = True
        try:
            self._build_item_index_inner(index, item_paths)
        finally:
            self._building_items = False
        self._item_index = index
        self._item_paths = item_paths
        # 背包类容器默认展开（最多 AUTO_EXPAND_ITEMS 行），打开存档就能看到物品
        for path, positions in index.items():
            if len(positions) <= AUTO_EXPAND_ITEMS:
                self._auto_expand.add(path)
                matched = self.item_by_path(path)
                if matched is not None:
                    matched.setExpanded(True)

    def _build_item_index_inner(self, index: dict, item_paths: set) -> None:
        from app.core.nbt_bridge import is_item_container, looks_like_item
        stack: list[tuple[str, SnbtNode]] = [("", self._root)]
        while stack:
            path, node = stack.pop()
            if node.children and is_item_container(node):
                positions = {position for position, child in
                             enumerate(node.children) if looks_like_item(child)}
                if positions:
                    index[path] = frozenset(positions)
                    for position in positions:
                        step = self._join_step(path, node, position)
                        item_paths.add(f"{path}{step}")
                    continue                 # 物品内部不用再往下找
            for position in range(len(node.children) - 1, -1, -1):
                child = node.children[position]
                step = self._join_step(path, node, position)
                stack.append((f"{path}{step}", child))

    def _item_stack_for(self, node: SnbtNode, path: str,
                        parent_path: str) -> Optional[ItemStack]:
        """如果这个子节点是物品，返回解析结果（带缓存）。"""
        if parent_path not in self._item_index:
            return None
        if path in self._item_cache:
            return self._item_cache[path]
        try:
            stack = parse_item(node, path)
        except Exception:
            stack = None
        self._item_cache[path] = stack
        return stack

    def _refresh_tree(self) -> None:
        """重建树，并尽量恢复选中项与展开状态。

        数组 / List 的元素是**按需渲染**的：只有已经展开的容器、或筛选命中
        的元素才会生成子节点，否则打开几万个元素的文件会先卡住界面。
        """
        keep = self._selected_paths()
        expanded = self._expanded_paths()
        if not self._building_items:
            self._build_item_index()
        previous = self._updating
        self._updating = True
        try:
            self.tree.clear()
            root_item = self._populate(None, self._root, index=None, path="",
                                       expanded=expanded)
            if expanded:
                self._restore_expanded(expanded)
            elif root_item is not None:
                root_item.setExpanded(True)
        finally:
            self._updating = previous
        self._restore_selection(keep)
        # 重新算一次签名：签名一致就说明命中集合没变，_apply_filter 不会再递归
        self._filter_source = self._filter_signature()
        self._apply_filter()
        self._update_status()
        self._refresh_details()

    def _filter_signature(self) -> tuple:
        """当前筛选条件的指纹（含数据本身），变了才需要重算命中集合。"""
        return (id(self._root), self.filter_edit.text().strip(),
                self.filter_keys.isChecked(), self.filter_values.isChecked(),
                self.filter_case.isChecked(), self.filter_types.currentData())

    def _populate(self, parent_item, node: SnbtNode, index,
                  path: str, expanded: set[str],
                  parent_path: str = "") -> EditorItem:
        item = EditorItem(node, path)
        stack = self._item_stack_for(node, path, parent_path) \
            if parent_item is not None else None
        item.stack = stack
        if parent_item is None:
            item.setText(COL_NAME, self._root_label())
        elif node.key is not None:
            item.setText(COL_NAME, node.key)
        else:
            item.setText(COL_NAME, f"[{index if index is not None else 0}]")
        if parent_item is None:
            font = item.font(COL_NAME)
            font.setBold(True)
            item.setFont(COL_NAME, font)

        type_text = display_type_label(node)
        item.setText(COL_TYPE, type_text)
        color = self._type_colors.get(node.kind.value, "#C9D1D9")
        if stack is not None:
            item.setText(COL_VALUE, stack.summary())
            item.setToolTip(COL_VALUE, _stack_tooltip(stack))
            item.setToolTip(COL_NAME, f"{path or self._root_label()}\n"
                                      f"{stack.summary()}")
            font = item.font(COL_NAME)
            font.setBold(True)
            item.setFont(COL_NAME, font)
        else:
            item.setText(COL_VALUE, format_value(node, self._hex_mode))
            item.setToolTip(COL_NAME, path or self._root_label())
            item.setToolTip(COL_VALUE, format_value(node, self._hex_mode))
        item.setForeground(COL_TYPE, QBrush(QColor(color)))
        item.setToolTip(COL_TYPE, f"{type_label(node)}"
                                  + (f"（{type_text}）" if type_text !=
                                     type_label(node) else ""))
        if self._show_icons:
            item.setIcon(COL_NAME, QIcon(node_type_icon(node.kind, color)))

        if parent_item is None:
            self.tree.addTopLevelItem(item)
        else:
            parent_item.addChild(item)

        if stack is not None:
            # 物品识别成功：这一行就是「一件物品」，不再往下拆组件
            return item

        if node.kind == SnbtKind.COMPOUND:
            for position, child in enumerate(node.children):
                step = self._step_for(node, position)
                if step.startswith(".") and not parent_path:
                    step = step[1:]
                child_path = _join_key(path, child.key)
                self._populate(item, child, position, child_path, expanded,
                               parent_path=parent_path + step)
        elif node.kind in (SnbtKind.LIST, *ARRAY_KINDS):
            keep = self._filter_keep
            if keep is not None:
                # 筛选时只渲染命中路径上的元素，这样超大数组也能搜到末尾
                children = [(i, c) for i, c in enumerate(node.children)
                            if id(c) in keep]
            elif path in self._auto_expand:
                # 要定位到这个容器里的某个元素：这次不截断，否则找不到目标
                children = list(enumerate(node.children))
            elif path in self._auto_expand_capped:
                self._auto_expand_capped.discard(path)
                children = list(enumerate(node.children))[:MAX_ELEMENT_ROWS]
            elif getattr(self, "_expand_all_mode", False) or path in expanded:
                children = list(enumerate(node.children))[:MAX_ELEMENT_ROWS]
            else:
                children = []
            for position, child in children:
                self._populate(item, child, position, f"{path}[{position}]",
                               expanded,
                               parent_path=parent_path)
        return item

    def _root_label(self) -> str:
        return f"根 ({self._root_name})" if self._root_name else "(根)"

    def _refresh_text(self) -> None:
        previous = self._updating
        self._updating = True
        try:
            self.text.setPlainText(serialize(self._root, pretty=True))
        finally:
            self._updating = previous

    # ---- 展开状态 / 选中状态 ----

    def _expanded_paths(self) -> set[str]:
        paths: set[str] = set()

        def walk(item) -> None:
            if item.isExpanded():
                paths.add(item.path)
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        return paths

    def _restore_expanded(self, paths: set[str]) -> None:
        def walk(item) -> None:
            item.setExpanded(item.path in paths)
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def _selected_paths(self) -> list[str]:
        return [it.path for it in self.tree.selectedItems()
                if isinstance(it, EditorItem)]

    def _restore_selection(self, paths: list[str]) -> None:
        if not paths:
            return
        selection = self.tree.selectionModel()
        found_current = False
        for path in paths:
            item = self.item_by_path(path)
            if item is None:
                continue
            item.setSelected(True)
            found_current = True
        if found_current and self.tree.currentItem() is None:
            first = self.item_by_path(paths[0])
            if first is not None:
                selection.setCurrentIndex(
                    self.tree.indexFromItem(first, COL_NAME),
                    QItemSelectionModel.SelectionFlag.NoUpdate)
        # 整棵重建后，未命中的选中项要清掉
        for item in list(self.tree.selectedItems()):
            if item.path not in paths:
                item.setSelected(False)

    def item_by_path(self, path: str):
        """按 `Data.Player.Inventory[0].id` 形式的路径找树节点。"""
        if not path:
            return None
        tokens = parse_data_path(path)
        if not tokens:
            return None
        item = self.tree.topLevelItem(0)
        if item is None:
            return None
        # 根节点的路径是 ""（或旧版的 "(根)"）
        if path in ("(根)", self._root_label()):
            return item
        for name, indices in tokens:
            if name:
                item = self._child_by_name(item, name)
                if item is None:
                    return None
            for position in indices:
                if position >= item.childCount():
                    return None
                item = item.child(position)
        return item

    @staticmethod
    def _child_by_name(item, name: str):
        for i in range(item.childCount()):
            child = item.child(i)
            if child.text(COL_NAME) == name:
                return child
        return None

    def node_by_path(self, path: str) -> Optional[SnbtNode]:
        """按路径在**模型**里找节点（不依赖树里是否渲染过）。"""
        if not path or path in ("(根)", self._root_label()):
            return self._root
        node = self._root
        for name, indices in parse_data_path(path):
            if name:
                if node.kind != SnbtKind.COMPOUND:
                    return None
                node = node.get_child(name)
                if node is None:
                    return None
            for position in indices:
                if not node.is_container or position >= len(node.children):
                    return None
                node = node.children[position]
        return node

    @staticmethod
    def _relative_path(parent: str, child: str) -> str:
        if not parent:
            return child
        if child.startswith(parent):
            rest = child[len(parent):]
            return rest[1:] if rest.startswith(".") else rest
        return child

    def select_by_path(self, path: str) -> bool:
        """按路径选中节点（兼容旧接口）。

        目标在折叠的容器里（例如 `Nums[1]`）时会自动把沿途的容器展开，
        必要时重建树 —— 定位过去就能直接编辑。
        """
        if not path or path in ("(根)", self._root_label()):
            item = self.tree.topLevelItem(0)
            if item is None:
                return False
            self._set_current(item)
            return True
        item = self.item_by_path(path)
        if item is None:
            node = self.node_by_path(path)
            if node is None:
                return False
            self._auto_expand_ancestors(node)
            self._refresh_tree()
            item = self.item_by_path(path)
        if item is None:
            return False
        self._set_current(item)
        self.tree.scrollToItem(item)
        return True

    def _auto_expand_ancestors(self, node: SnbtNode) -> None:
        """把从根到 `node` 的每一层容器都标成「要渲染子节点」。"""
        for current in node_trail(self._root, node):
            if current.kind in (SnbtKind.LIST, *ARRAY_KINDS):
                self._auto_expand.add(node_path_of(self._root, current))

    def _set_current(self, item, keep_selection: bool = False) -> None:
        """只改变当前项，不触发编辑；并同步状态栏与详情面板。"""
        if not keep_selection:
            self.tree.clearSelection()
            item.setSelected(True)
        self.tree.setCurrentItem(
            item, COL_NAME, QItemSelectionModel.SelectionFlag.NoUpdate)
        # NoUpdate 可能不触发 selectionChanged，这里手动同步一次
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        self._update_status()
        self._refresh_details()

    def _update_status(self) -> None:
        node = self.selected_node()
        summary = getattr(self, "summary_text", "") or summarize(self._root)
        if node is None:
            self.status.setText(f"{summary}　·　未选中标签")
            return
        count = len(self.selected_nodes())
        path = self.current_path()
        value = format_value(node, self._hex_mode)
        if len(value) > 80:
            value = value[:77] + "…"
        prefix = f"已选 {count} 项　·　" if count > 1 else ""
        self.status.setText(f"{prefix}{path}　·　{type_label(node)}　·　{value}"
                            f"　·　{summary}")
        self.statusMessage.emit(f"{type_label(node)} @ {path}")

    # ==================================================================
    # 详情面板
    # ==================================================================

    def _refresh_details(self) -> None:
        node = self.selected_node()
        if not getattr(self, "_details_ready", False):
            return
        if node is None:
            self.detail_type.setText("—")
            self.detail_type.setStyleSheet(
                "font-weight: bold; padding: 1px 6px; border-radius: 3px;")
            self.detail_meta.setText("在上面的树里选一个标签")
            self.detail_path.setText("")
            self.detail_preview.setPlainText("")
            self.detail_hint.setText("")
            self._set_detail_buttons(None)
            return

        self.detail_type.setText(type_label(node))
        color = self._type_colors.get(node.kind.value, "#C9D1D9")
        self.detail_type.setStyleSheet(
            f"font-weight: bold; padding: 1px 6px; border-radius: 3px;"
            f" color: {color}; border: 1px solid {color};")
        self.detail_path.setText(self.current_path() or self._root_label())

        stack = self._current_stack()
        entity = self._current_entity()
        if stack is not None:
            # 识别成物品 → 显示图形化卡片
            self.detail_meta.setText(stack.summary())
            self.detail_stack.setCurrentWidget(self.detail_item_page)
            self._fill_item_card(stack)
        elif entity is not None:
            # 识别成实体 → 显示实体卡片
            self.detail_meta.setText(entity.summary())
            self.detail_stack.setCurrentWidget(self.detail_entity_page)
            self._fill_entity_card(entity)
        elif node.is_container:
            meta = container_summary(node)
            if node.kind == SnbtKind.COMPOUND:
                meta += f" · 路径 {self.current_path() or '(根)'}"
            self.detail_meta.setText(meta)
            self.detail_stack.setCurrentWidget(self.detail_container_page)
            self.detail_preview.setPlainText(self._container_preview(node))
            self.detail_hint.setText(self._container_hint(node))
        else:
            self.detail_meta.setText(
                f"{type_label(node)} 标量 · 回车应用修改")
            self.detail_stack.setCurrentWidget(self.detail_scalar_page)
            self.detail_value.setText(
                str(node.value) if node.kind == SnbtKind.STRING
                else format_value(node))
            self.detail_value_state.setText("")
        self._set_detail_buttons(node)

    def _current_stack(self) -> Optional[ItemStack]:
        item = self.tree.currentItem()
        return item.stack if isinstance(item, EditorItem) else None

    def _current_entity(self):
        """当前选中的标签如果能识别成实体，返回 EntityData。"""
        node = self.selected_node()
        if node is None or node.kind != SnbtKind.COMPOUND:
            return None
        item = self.tree.currentItem()
        if not isinstance(item, EditorItem) or item.stack is not None:
            return None
        try:
            from app.core.nbt_bridge import looks_like_entity, parse_entity
        except Exception:                                   # pragma: no cover
            return None
        if not looks_like_entity(node):
            return None
        try:
            return parse_entity(node, item.path)
        except Exception:                                   # pragma: no cover
            return None

    def _fill_entity_card(self, entity) -> None:
        self.entity_icon.setText(entity.display()[:2] or "?")
        self.entity_title.setText(entity.summary())
        bits = []
        entry = {}
        try:
            from app.core.nbt_bridge import entity_display
            from app.core.data import store as data_store
            for item in data_store().entities():
                if item.get("id") == entity.entity_id:
                    entry = item
                    break
            entity_display(entity.entity_id)
        except Exception:                                   # pragma: no cover
            entry = {}
        if entry.get("category"):
            bits.append("分类 "
                        + app_settings.translate_category(entry["category"]))
        if entry.get("type"):
            bits.append("类型 "
                        + app_settings.translate_category(entry["type"]))
        if entity.tags:
            bits.append("标签 " + "/".join(entity.tags))
        if entity.items:
            bits.append(f"{len(entity.items)} 件装备 / 物品")
        if entity.path:
            bits.append(f"位置 {entity.path}")
        self.entity_meta.setText("　·　".join(bits))
        self.entity_id_label.setText(entity.entity_id)
        nbt = entity.nbt or ""
        self.entity_nbt.setPlainText(nbt if len(nbt) <= 1200
                                     else nbt[:1200] + " …（略）")
        self.entity_nbt.setToolTip(nbt)
        self.entity_id_label.setToolTip(entity.summon_command())

    def _send_entity_to_summon(self) -> None:
        entity = self._current_entity()
        if entity is None:
            return
        self.summonRequested.emit(entity)

    def copy_entity_command(self) -> None:
        entity = self._current_entity()
        if entity is None:
            return
        text = entity.summon_command()
        QApplication.clipboard().setText(text)
        self.statusMessage.emit(f"已复制：{text[:70]}")

    def copy_entity_nbt(self) -> None:
        entity = self._current_entity()
        if entity is None:
            return
        QApplication.clipboard().setText(entity.nbt)
        self.statusMessage.emit("已复制实体 NBT。")

    def _fill_item_card(self, stack: ItemStack) -> None:
        """把物品信息填进详情面板的图形化卡片。"""
        entry = lookup_item(stack.item_id)
        self.item_icon.setText(stack.display()[:2] or "?")
        self.item_name.setText(stack.summary())
        bits = [f"数量 {stack.count}"]
        if stack.slot is not None:
            bits.append(f"槽位 {stack.slot}")
        if entry.get("category"):
            bits.append(f"分类 {app_settings.translate_category(entry['category'])}")
        size = item_stack_size(stack.item_id)
        if size != 64:
            bits.append(f"堆叠上限 {size}")
        if stack.path:
            bits.append(f"位置 {stack.path}")
        if stack.lore:
            bits.append("描述 " + " / ".join(stack.lore))
        self.item_meta.setText("　·　".join(bits))
        self.item_id.setText(stack.clean_id())
        lines = []
        for enchant_id, level in stack.enchantments:
            lines.append(f"附魔　{enchant_display(enchant_id)} {roman(level)}"
                         f"　（{normalize_item_id(enchant_id)}）")
        for cid, value in stack.components.items():
            text = str(value)
            if len(text) > 120:
                text = text[:117] + "…"
            lines.append(f"{cid} = {text}")
        self.item_components.setPlainText("\n".join(lines))
        source = getattr(self, "_source_label", "") or ""
        self.item_id.setToolTip(
            f"{lookup_item(stack.item_id).get('display', stack.item_id)}\n"
            f"/give：{stack_to_command(stack, '@p')}" + (f"\n来源：{source}"
                                                        if source else ""))

    def _send_item_to_give(self) -> None:
        stack = self._current_stack()
        if stack is None:
            return
        self.giveRequested.emit(stack)

    def copy_item_command(self) -> None:
        stack = self._current_stack()
        if stack is None:
            return
        text = stack_to_command(stack, "@p")
        QApplication.clipboard().setText(text)
        self.statusMessage.emit(f"已复制：{text}")

    def copy_item_snbt(self) -> None:
        stack = self._current_stack()
        if stack is None:
            return
        text = stack_to_snbt(stack)
        QApplication.clipboard().setText(text)
        self.statusMessage.emit("已复制物品 SNBT。")

    def copy_item_data_command(self) -> None:
        stack = self._current_stack()
        if stack is None:
            return
        text = stack_to_data_command(stack)
        QApplication.clipboard().setText(text)
        self.statusMessage.emit(f"已复制：{text[:70]}")

    def _container_preview(self, node: SnbtNode) -> str:
        if node.kind == SnbtKind.COMPOUND:
            lines = []
            for child in node.children[:MAX_ELEMENT_ROWS]:
                lines.append(f"{child.key}: {format_value(child)}"
                             f"   «{type_label(child)}»")
            if len(node.children) > MAX_ELEMENT_ROWS:
                lines.append(f"…… 其余 {len(node.children) - MAX_ELEMENT_ROWS} "
                             "个子标签已省略")
            return "\n".join(lines)
        if node.kind == SnbtKind.LIST:
            if not node.children:
                return ""
            lines = [f"[{i}] {format_value(child)}   «{type_label(child)}»"
                     for i, child in enumerate(node.children[:MAX_ELEMENT_ROWS])]
            if len(node.children) > MAX_ELEMENT_ROWS:
                lines.append(f"…… 其余 {len(node.children) - MAX_ELEMENT_ROWS} "
                             "项已省略")
            return "\n".join(lines)
        if node.kind in ARRAY_KINDS:
            if self._byte_text_mode and node.kind == SnbtKind.BYTE_ARRAY:
                data = bytes(v & 0xFF for v in array_values(node))
                return data.decode("utf-8", errors="replace")
            body = array_preview(node, max_items=96, hex_mode=self._hex_mode)
            dump = array_hexdump(node, limit=24)
            return f"{body}\n\n--- 内存中的字节 ---\n{dump}"
        return ""

    def _container_hint(self, node: SnbtNode) -> str:
        special = _structure_hint(node, self._root_name)
        if special:
            return special
        if node.kind == SnbtKind.COMPOUND:
            return ("双击子标签的取值列可以直接改；用「新建子标签…」加字段，"
                    "右键还能复制/剪切/粘贴整个子树。")
        if node.kind == SnbtKind.LIST:
            expected = element_kind(node)
            name = type_label_kind(expected) if expected is not None else "任意"
            return (f"List 的元素类型是 {name}：只能放同类型元素。"
                    "用「追加元素…」加，拖放也能把同类型标签移到列表里。")
        return ("数组用「追加元素…」加项，或用「编辑取值…」整段编辑"
                "（支持十六进制与字节转字符串）。")

    def _set_detail_buttons(self, node: Optional[SnbtNode]) -> None:
        container = node is not None and node.is_container
        is_root = node is self._root
        stack = self._current_stack()
        has_item = stack is not None
        self.btn_add_child.setEnabled(
            container and node.kind == SnbtKind.COMPOUND and not has_item)
        self.btn_add_item.setEnabled(
            container and node.kind in (SnbtKind.LIST, *ARRAY_KINDS)
            and not has_item)
        self.btn_expand_items.setEnabled(
            container and node.kind in (SnbtKind.LIST, *ARRAY_KINDS)
            and bool(node.children))
        self.btn_edit_value.setEnabled(node is not None)
        self.btn_delete_sel.setEnabled(node is not None and not is_root)
        self.btn_overview.setEnabled(bool(self._item_paths))
        self.btn_overview.setToolTip(
            f"存档里有 {len(self._item_paths)} 件物品，点这里列成表"
            if self._item_paths else
            "当前文档里没有识别到物品（需要是存档里的背包 / 箱子数据）")
        # 「字节转字符串」只对 ByteArray 有意义
        self.btn_byte_text.setEnabled(
            node is not None and node.kind == SnbtKind.BYTE_ARRAY)
        # 「字节视图」只要文档里有数组就可用
        self.btn_hex.setEnabled(_has_array(self._root))

    # ==================================================================
    # 原地编辑
    # ==================================================================

    def _on_double_clicked(self, item, column: int) -> None:
        if not isinstance(item, EditorItem):
            return
        if column == COL_NAME:
            self.rename_node(item)
        elif column == COL_VALUE:
            self.edit_value(item)

    def edit_selected_value(self) -> None:
        item = self.tree.currentItem()
        if isinstance(item, EditorItem):
            self.edit_value(item)

    def edit_value(self, item) -> None:
        """编辑取值：标量就地编辑，数组 / 字符串 / 布尔走对话框。"""
        node = item.node if isinstance(item, EditorItem) else None
        if node is None:
            return
        if node.kind == SnbtKind.COMPOUND:
            QMessageBox.information(
                self, "提示",
                "Compound 的取值就是它的子标签：请用「新建」添加子标签。")
            return
        if node.kind == SnbtKind.LIST:
            QMessageBox.information(
                self, "提示",
                f"List 共 {len(node.children)} 项：请用「追加元素…」加，"
                "双击子项编辑元素值。")
            return
        if node.kind in ARRAY_KINDS:
            dialog = ArrayEditDialog(node, self, hex_mode=self._hex_mode)
            if dialog.exec() != ArrayEditDialog.DialogCode.Accepted:
                return
            self._push_history()
            dialog.apply_to(node)
            self._after_model_change(keep=node)
            return
        if node.kind in (SnbtKind.STRING, SnbtKind.BOOL):
            dialog = ValueEditDialog(node, self, hex_mode=self._hex_mode)
            if dialog.exec() != ValueEditDialog.DialogCode.Accepted:
                return
            self._push_history()
            dialog.apply_to(node)
            self._after_model_change(keep=node)
            return
        # 数值：就地编辑体验最好
        self._set_current(item)
        self.tree.editItem(item, COL_VALUE)

    def apply_detail_value(self) -> None:
        """把详情面板里的取值写回当前标量节点。"""
        if self._applying:
            return
        node = self.selected_node()
        if node is None or node.is_container:
            return
        text = self.detail_value.text()
        if node.kind == SnbtKind.STRING:
            new_value: object = text
        else:
            try:
                from app.ui.widgets.nbt_tags import parse_value
                new_value = parse_value(node, text)
            except ValueParseError as exc:
                self.detail_value_state.setStyleSheet(
                    f"color: {self._palette_colors['error']}; font-size: 12px;")
                self.detail_value_state.setText(str(exc))
                return
        if node.kind in ARRAY_KINDS:                       # pragma: no cover
            return
        if isinstance(new_value, int) and node.kind in (
                SnbtKind.BYTE, SnbtKind.SHORT, SnbtKind.INT, SnbtKind.LONG):
            new_value = canonical_int(new_value, node.kind)
        if new_value == node.value:
            self.detail_value_state.setStyleSheet(
                f"color: {self._palette_colors['muted']}; font-size: 12px;")
            self.detail_value_state.setText("没有改动")
            return
        self._push_history()
        node.value = new_value
        self._applying = True
        try:
            self.detail_value.setText(
                str(new_value) if node.kind == SnbtKind.STRING
                else format_value(node))
        finally:
            self._applying = False
        self.detail_value_state.setStyleSheet(
            f"color: {self._palette_colors['ok']}; font-size: 12px;")
        self.detail_value_state.setText("已应用")
        self._after_model_change(keep=node)

    def rename_selected(self) -> None:
        self.rename_node(self.tree.currentItem())

    def rename_node(self, item) -> None:
        node = item.node if isinstance(item, EditorItem) else None
        if node is None or node.key is None:
            return
        name, ok = self._ask_text("重命名标签", "名称：", str(node.key))
        if not ok:
            return
        new_name = name.strip()
        if not new_name or new_name == node.key:
            return
        parent = self._parent_node(node)
        if parent is not None and parent.kind == SnbtKind.COMPOUND \
                and parent.get_child(new_name) is not None:
            QMessageBox.warning(self, "重名",
                                f"同级已经有名为 {new_name} 的标签。")
            return
        self._push_history()
        node.key = new_name
        self._after_model_change(keep=node)

    def _ask_text(self, title: str, label: str, text: str) -> tuple[str, bool]:
        from PySide6.QtWidgets import QInputDialog
        return QInputDialog.getText(self, title, label, text=text)

    def _on_tree_item_changed(self, item, column: int) -> None:
        """处理树里被改过的单元格（就地编辑与程序化改动都会到这里）。"""
        if self._updating or not isinstance(item, EditorItem):
            return
        node = item.node
        if column == COL_NAME:
            if node.key is None or item is self.tree.topLevelItem(0):
                return
            new_key = item.text(COL_NAME).strip()
            if not new_key or new_key == node.key:
                return
            parent = self._parent_node(node)
            if parent is not None and parent.kind == SnbtKind.COMPOUND \
                    and parent.get_child(new_key) is not None:
                self._updating = True
                try:
                    item.setText(COL_NAME, str(node.key))
                finally:
                    self._updating = False
                self.statusMessage.emit(f"同级已经有名为 {new_key} 的标签。")
                return
            self._push_history()
            # 先算出改名后的新路径：父节点的路径没变，只换了最后一段
            parent_item = item.parent()
            base = parent_item.path if parent_item is not None else ""
            new_path = f"{base}.{new_key}" if base else new_key
            node.key = new_key
            # 路径全部按模型重算，保证子孙路径与「定位 / 状态栏」一致
            self._refresh_tree()
            self.select_by_path(new_path)
            self._mark_changed()
            return
        if column == COL_VALUE:
            if node.is_container:
                self._updating = True
                try:
                    item.setText(COL_VALUE,
                                 format_value(node, self._hex_mode))
                finally:
                    self._updating = False
                return
            from app.ui.widgets.nbt_tags import parse_value
            try:
                value = parse_value(node, item.text(COL_VALUE))
            except ValueParseError as exc:
                self._updating = True
                try:
                    item.setText(COL_VALUE,
                                 format_value(node, self._hex_mode))
                finally:
                    self._updating = False
                self.statusMessage.emit(str(exc))
                return
            if value == node.value:
                return
            self._push_history()
            node.value = value
            self._updating = True
            try:
                item.setText(COL_VALUE, format_value(node, self._hex_mode))
            finally:
                self._updating = False
            self._mark_changed()
            self._refresh_details()

    # ==================================================================
    # 节点操作
    # ==================================================================

    def _parent_node(self, node: SnbtNode) -> Optional[SnbtNode]:
        """按对象身份找父节点（不能用 `in`，SnbtNode 是值相等）。"""
        stack = [self._root]
        while stack:
            current = stack.pop()
            for child in current.children:
                if child is node:
                    return current
            stack.extend(current.children)
        return None

    def _unique_key(self, parent: SnbtNode, base: str) -> str:
        if parent.get_child(base) is None:
            return base
        index = 1
        while parent.get_child(f"{base}{index}") is not None:
            index += 1
        return f"{base}{index}"

    def new_child_dialog(self) -> None:
        """新建标签：先选类型（默认挂在当前节点下）。"""
        item = self.tree.currentItem()
        parent = item.node if isinstance(item, EditorItem) else self._root
        if parent is None or not parent.is_container:
            parent = self._root
        menu = QMenu(self)
        if parent.kind == SnbtKind.COMPOUND:
            for label, kind in CREATABLE:
                action = menu.addAction(label)
                action.setData(kind)
        else:
            expected = element_kind(parent)
            choices = CREATABLE if expected is None else [
                (label, kind) for label, kind in CREATABLE if kind == expected]
            for label, kind in choices:
                action = menu.addAction(f"元素：{label}")
                action.setData(kind)
        anchor = self.btn_new if hasattr(self, "btn_new") else self
        chosen = menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        if chosen is None:
            return
        if parent.kind == SnbtKind.COMPOUND:
            self.insert_node(parent, chosen.data())
        else:
            self.add_list_element(parent, chosen.data())

    def insert_node(self, parent: SnbtNode, kind: SnbtKind,
                    key: str | None = None) -> Optional[SnbtNode]:
        if parent.kind == SnbtKind.COMPOUND:
            node = self._make_node(kind)
            node.key = self._unique_key(parent, key or "NewTag")
        elif parent.kind in (SnbtKind.LIST, *ARRAY_KINDS):
            return self.add_list_element(parent, kind)
        else:
            QMessageBox.information(self, "提示",
                                    "只有 Compound / List / 数组能容纳子标签。")
            return None
        self._push_history()
        parent.add_child(node)
        self._after_model_change(keep=node)
        return node

    def _make_node(self, kind: SnbtKind) -> SnbtNode:
        if kind == SnbtKind.COMPOUND:
            return SnbtNode.new_compound()
        if kind == SnbtKind.LIST:
            return SnbtNode.new_list()
        if kind in ARRAY_KINDS:
            return SnbtNode(kind=kind)
        return SnbtNode.new_scalar(kind, default_value(kind))

    def add_list_element(self, parent: SnbtNode, kind: SnbtKind
                         ) -> Optional[SnbtNode]:
        """给 List / 数组追加一个元素（类型必须合法）。

        类型不匹配时只发一条状态信息、返回 None，不弹模态框 —— 这样脚本
        调用与离屏测试都不会被对话框卡住。
        """
        expected = element_kind(parent)
        if expected is not None and kind != expected:
            self.statusMessage.emit(
                f"{type_label(parent)} 只能放 {type_label_kind(expected)} 元素，"
                f"不能放 {type_label_kind(kind)}。")
            return None
        node = self._make_node(kind)
        node.key = None
        self._push_history()
        parent.add_child(node)
        self._after_model_change(keep=node)
        return node

    def append_element(self) -> None:
        """用对话框一次追加多个元素（List / 数组通用）。"""
        node = self.selected_node()
        if node is None or node.kind not in (SnbtKind.LIST, *ARRAY_KINDS):
            return
        dialog = AddElementDialog(node, self)
        if dialog.exec() != AddElementDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if not values:
            return
        kind = element_kind(node) or dialog._element_kind()
        self._push_history()
        for value in values:
            child = SnbtNode.new_scalar(
                kind, canonical_int(int(value), kind)
                if kind in (SnbtKind.BYTE, SnbtKind.SHORT, SnbtKind.INT,
                            SnbtKind.LONG) else value)
            child.key = None
            node.add_child(child)
        self._after_model_change(keep=node)

    def expand_selected_elements(self) -> None:
        """把容器的元素渲染成子节点（很大的容器只渲染前若干项）。"""
        item = self.tree.currentItem()
        node = item.node if isinstance(item, EditorItem) else None
        if node is None or node.kind not in (SnbtKind.LIST, *ARRAY_KINDS):
            return
        path = item.path
        if len(node.children) > MAX_ELEMENT_ROWS:
            self.statusMessage.emit(
                f"这个容器有 {len(node.children)} 项，树里只渲染前 "
                f"{MAX_ELEMENT_ROWS} 项；完整内容看详情面板或「编辑取值…」。")
        self._auto_expand_capped.add(path)    # 这次重建要把元素建出来
        self._refresh_tree()
        target = self.item_by_path(path)
        if target is not None:
            self._set_current(target)
            target.setExpanded(True)
            self.tree.scrollToItem(target)

    def _set_elements_rendered(self, visible: bool) -> None:
        """切换「是否把所有元素都渲染出来」的模式。"""
        previous = getattr(self, "_expand_all_mode", False)
        self._expand_all_mode = visible
        if previous != visible:
            self._refresh_tree()
        elif visible:
            self.tree.expandAll()
        else:
            self.collapse_all()

    def delete_selected(self) -> None:
        nodes = self.selected_nodes()
        if not nodes:
            return
        if any(node is self._root for node in nodes):
            QMessageBox.information(self, "提示", "根标签不能删除。")
            return
        removable = [n for n in nodes if self._parent_node(n) is not None]
        if not removable:
            return
        if len(removable) > 1:
            answer = QMessageBox.question(
                self, "删除多个标签",
                f"确定删除选中的 {len(removable)} 个标签？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._push_history()
        for node in removable:
            parent = self._parent_node(node)
            if parent is not None:
                parent.remove_child(node)
        self._after_model_change(keep=None)
        self.statusMessage.emit(f"已删除 {len(removable)} 个标签。")

    def duplicate_selected(self) -> None:
        nodes = [n for n in self.selected_nodes() if n is not self._root]
        if not nodes:
            return
        self._push_history()
        keep = None
        for node in nodes:
            parent = self._parent_node(node)
            if parent is None:
                continue
            clone = node.clone()
            if parent.kind == SnbtKind.COMPOUND and clone.key:
                clone.key = self._unique_key(parent, f"{clone.key}_copy")
            else:
                clone.key = None
            position = parent.index_of(node)
            parent.add_child(clone, position + 1 if position >= 0 else None)
            keep = clone
        self._after_model_change(keep=keep)

    def copy_selected(self) -> None:
        nodes = [n for n in self.selected_nodes() if n is not self._root]
        if not nodes:
            return
        self._clipboard = nodes[0].clone()
        self._clipboard_source = nodes[0]
        mime = _make_mime(nodes[0])
        QApplication.clipboard().setMimeData(mime)
        label = type_label(nodes[0])
        self.statusMessage.emit(
            f"已复制 {label} “{nodes[0].key or '(元素)'}”"
            f"{f' 等 {len(nodes)} 项' if len(nodes) > 1 else ''}")

    def cut_selected(self) -> None:
        nodes = [n for n in self.selected_nodes() if n is not self._root]
        if not nodes:
            return
        node = nodes[0]
        self._clipboard = node.clone()
        self._clipboard_source = node
        QApplication.clipboard().setMimeData(_make_mime(node))
        self.delete_selected()
        self.statusMessage.emit(f"已剪切“{node.key or '(元素)'}”，可粘贴到别处。")

    def paste_into_selected(self) -> None:
        """粘贴：优先用内部剪贴板里的节点，否则解析系统剪贴板的 SNBT 文本。"""
        item = self.tree.currentItem()
        parent = item.node if isinstance(item, EditorItem) else self._root
        if parent is None or not parent.is_container:
            parent = self._root

        nodes: list[SnbtNode] = []
        if self._clipboard is not None:
            nodes = [self._clipboard.clone()]
        else:
            text = QApplication.clipboard().text().strip()
            if text:
                try:
                    nodes = [SnbtParser(text).parse()]
                except SnbtParseError:
                    nodes = []
        if not nodes:
            QMessageBox.information(
                self, "粘贴失败",
                "剪贴板里没有可用的 SNBT 文本。\n"
                "可以先用「复制」或「复制为 SNBT 文本」把标签放进剪贴板。")
            return

        self._push_history()
        keep = None
        rejected = 0
        for node in nodes:
            if parent.kind == SnbtKind.COMPOUND:
                if node.key:
                    old = parent.get_child(node.key)
                    if old is not None:
                        parent.remove_child(old)
                else:
                    node.key = self._unique_key(parent, "NewTag")
            elif not can_hold(parent, node):
                rejected += 1
                continue
            else:
                node.key = None
            parent.add_child(node)
            keep = node
        if rejected:
            self.statusMessage.emit(
                f"有 {rejected} 个标签类型不匹配，没有粘贴到 "
                f"{type_label(parent)} 里。")
        self._after_model_change(keep=keep)

    def copy_as_snbt(self) -> None:
        node = self.selected_node()
        if node is None:
            return
        text = serialize(node, pretty=False)
        QApplication.clipboard().setText(text)
        self.statusMessage.emit(f"已复制 SNBT：{text[:60]}"
                                f"{'…' if len(text) > 60 else ''}")

    def copy_selected_snbt(self) -> str:
        """把选中节点复制成 SNBT 文本并返回（同时写入剪贴板）。"""
        node = self.selected_node()
        if node is None:
            return ""
        text = serialize(node, pretty=False)
        QApplication.clipboard().setText(text)
        return text

    def copy_as_command(self) -> None:
        """复制成 `/data modify … set value …` 形式的命令片段。"""
        node = self.selected_node()
        if node is None:
            return
        path = self.current_path() or ""
        snbt = serialize(node, pretty=False)
        command = f"/data modify entity @s {path} set value {snbt}"
        QApplication.clipboard().setText(command)
        self.statusMessage.emit(f"已复制命令：{command[:70]}"
                                f"{'…' if len(command) > 70 else ''}")

    def copy_path(self) -> None:
        node = self.selected_node()
        if node is None:
            return
        path = self.current_path() or path_segments(node, self._root)
        QApplication.clipboard().setText(path)
        self.statusMessage.emit(f"已复制路径：{path}")

    def _node_path(self, node: SnbtNode | None) -> str:
        """兼容旧接口：返回节点路径。"""
        if node is None:
            return ""
        for item in self._all_items():
            if item.node is node:
                return item.path
        return node_path(node, self._root)

    def _all_items(self):
        stack = [self.tree.topLevelItem(i)
                 for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            yield item
            for i in range(item.childCount()):
                stack.append(item.child(i))

    def move_selected(self, delta: int) -> None:
        node = self.selected_node()
        parent = self._parent_node(node) if node else None
        if node is None or parent is None:
            return
        position = parent.index_of(node)
        if position < 0:
            return
        target = position + delta
        if not 0 <= target < len(parent.children):
            return
        self._push_history()
        parent.children[position], parent.children[target] = (
            parent.children[target], parent.children[position])
        self._after_model_change(keep=node)

    #: 兼容旧名字
    _move_selected = move_selected

    def move_dropped_item(self, event) -> bool:
        """把拖动的节点移到放下位置（校验 NBT 类型与循环引用）。"""
        source_item = self.tree.currentItem()
        source = source_item.node if isinstance(source_item, EditorItem) else None
        if source is None or source is self._root:
            return False
        target_item = self.tree.itemAt(event.position().toPoint())
        drop = self.tree.dropIndicatorPosition()

        if target_item is None or drop == \
                QAbstractItemView.DropIndicatorPosition.OnViewport:
            return self._reparent(source, self._root,
                                  len(self._root.children), event)
        if drop == QAbstractItemView.DropIndicatorPosition.OnItem:
            new_parent = target_item.node
            if new_parent is None:                          # pragma: no cover
                return False
            insert_at = len(new_parent.children)
        else:
            parent_item = target_item.parent()
            if parent_item is None:
                new_parent = self._root
                insert_at = len(self._root.children)
            else:
                new_parent = parent_item.node
                insert_at = parent_item.indexOfChild(target_item)
            if drop == QAbstractItemView.DropIndicatorPosition.BelowItem:
                insert_at += 1
        return self._reparent(source, new_parent, insert_at, event)

    def _reparent(self, source: SnbtNode, new_parent: SnbtNode, insert_at: int,
                  event=None) -> bool:
        """把 source 挂到 new_parent 下；任何不合法的情况都拒绝并回滚。"""
        if new_parent is None or new_parent is source:
            self._reject_drop("不能把标签移动到它自己下面。", event)
            return False
        if not new_parent.is_container:
            self._reject_drop("目标不是容器，不能放子标签。", event)
            return False
        if is_descendant(new_parent, source):
            self._reject_drop("不能把标签移动到它自己的子节点里。", event)
            return False
        old_parent = self._parent_node(source)
        if old_parent is None:
            return False
        if old_parent is new_parent:
            old_index = old_parent.index_of(source)
            if insert_at in (old_index, old_index + 1):
                return False                      # 位置没变，不记历史
        elif not can_hold(new_parent, source):
            expected = element_kind(new_parent)
            name = type_label_kind(expected) if expected is not None else \
                type_label(new_parent)
            self._reject_drop(
                f"{type_label(new_parent)} 只能放 {name} 元素，"
                f"不能放 {type_label(source)}。", event)
            return False

        old_index = old_parent.index_of(source)

        self._push_history()
        old_parent.remove_child(source)
        if new_parent.kind == SnbtKind.COMPOUND and source.key is None:
            source.key = self._unique_key(new_parent, "NewTag")
        elif new_parent.kind != SnbtKind.COMPOUND:
            source.key = None
        if old_parent is new_parent and old_index < insert_at:
            insert_at -= 1
        new_parent.add_child(source, max(0, min(insert_at,
                                                len(new_parent.children))))
        self._after_model_change(keep=source)
        self.statusMessage.emit("已移动标签。")
        return True

    def _reject_drop(self, message: str, event=None) -> None:
        self.statusMessage.emit(message)
        if event is not None:
            event.ignore()

    def toggle_details(self, checked: bool | None = None) -> None:
        """显示 / 隐藏详情面板（小窗口里能把树留得更宽）。"""
        visible = (not self._details_visible if checked is None
                   else bool(checked))
        self._details_visible = visible
        if hasattr(self, "btn_details"):
            self.btn_details.setChecked(visible)
        panel = getattr(self, "_details_panel", None)
        if panel is not None:
            panel.setVisible(visible)
        if visible:
            sizes = self.main_split.sizes()
            if len(sizes) == 2 and sizes[1] < 60:
                self.main_split.setSizes([max(200, sizes[0] - 240), 240])
        if self._settings_loaded:
            self._save_settings()

    def _toggle_hex_mode(self, checked: bool) -> None:
        self._hex_mode = bool(checked)
        self._save_settings()
        self._updating = True
        try:
            self._refresh_tree()
        finally:
            self._updating = False
        self._refresh_details()

    def _toggle_byte_text(self, checked: bool) -> None:
        self._byte_text_mode = bool(checked)
        self._refresh_details()

    def expand_all(self) -> None:
        self._set_elements_rendered(True)
        self.tree.expandAll()

    def collapse_all(self) -> None:
        self._expand_all_mode = False
        self.tree.collapseAll()
        item = self.tree.topLevelItem(0)
        if item is not None:
            item.setExpanded(True)

    def _load_settings(self) -> None:
        try:
            from app.storage import DraftStore
            data = DraftStore().load(self.HEX_SETTING_KEY) or {}
            self._hex_mode = bool(data.get("hex", False))
            self._byte_text_mode = bool(data.get("byte_text", False))
            self._details_visible = bool(data.get("details", True))
        except Exception:
            self._hex_mode = False
            self._byte_text_mode = False
            self._details_visible = True
        if hasattr(self, "btn_hex"):
            self.btn_hex.setChecked(self._hex_mode)
            self.btn_byte_text.setChecked(self._byte_text_mode)
            self.btn_details.setChecked(self._details_visible)
        panel = getattr(self, "_details_panel", None)
        if panel is not None:
            panel.setVisible(self._details_visible)

    def _save_settings(self) -> None:
        try:
            from app.storage import DraftStore
            DraftStore().save(self.HEX_SETTING_KEY,
                              {"hex": self._hex_mode,
                               "byte_text": self._byte_text_mode,
                               "details": self._details_visible})
        except Exception:
            pass

    # ==================================================================
    # 筛选
    # ==================================================================

    def _on_filter_changed(self, _text: str) -> None:
        self._apply_filter()

    def _filter_active(self) -> bool:
        if not hasattr(self, "filter_edit"):
            return False
        return bool(self.filter_edit.text().strip()) \
            or self.filter_types.currentData() is not None

    def _apply_filter(self, *_args) -> None:
        """按关键字 / 类型筛选树。

        元素是按需渲染的，所以筛选第一次生效时（或筛选条件变了以后）要先按
        「命中集合」重建一次树，把命中的元素建出来；否则用户搜一个还没渲染的
        数组元素会看不到结果。
        """
        if not hasattr(self, "tree"):
            return
        needle = self.filter_edit.text().strip()
        self._filter_needle = needle
        only_type = self.filter_types.currentData()
        active = bool(needle) or only_type is not None
        # 筛选框里的关键字同时在树里高亮，命中位置一眼可见
        self.value_delegate.set_needle(needle, self.filter_case.isChecked())
        if not active:
            self.filter_count.setText("")
            if self._filter_keep is not None:
                self._filter_keep = None
                self._filter_source = None
                if self._settings_loaded:
                    self._refresh_tree()
                    return
            for item in self._all_items():
                item.setHidden(False)
            return

        fields = []
        if self.filter_keys.isChecked():
            fields.append("key")
        if self.filter_values.isChecked():
            fields.append("value")
        if not fields:
            fields = ["key", "value"]
        types = {only_type} if only_type is not None else None
        signature = self._filter_signature()
        if signature != self._filter_source:
            self._filter_source = signature
            self._filter_keep = filter_nodes(
                self._root, needle, fields=tuple(fields),
                case_sensitive=self.filter_case.isChecked(), types=types)
            if self._settings_loaded:
                self._refresh_tree()
                return

        keep = self._filter_keep or set()
        hidden = 0
        for item in self._all_items():
            visible = id(item.node) in keep
            item.setHidden(not visible)
            if not visible:
                hidden += 1
        # 命中的父节点自动展开，免得还要一层层点开
        for item in self._all_items():
            if not item.isHidden() and item.childCount():
                item.setExpanded(True)
        self.filter_count.setText(
            f"隐藏 {hidden} 项" if hidden else "全部匹配")

    # ==================================================================
    # 撤销 / 重做
    # ==================================================================

    def _snapshot(self) -> tuple[str, str]:
        return serialize(self._root, pretty=False), self.current_path()

    def _push_history(self) -> None:
        snapshot = self._snapshot()
        if self._history and self._history[-1][0] == snapshot[0]:
            return
        self._history.append(snapshot)
        del self._history[: max(0, len(self._history) - self.MAX_HISTORY)]
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._history)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._history:
            self.statusMessage.emit("没有可撤销的操作。")
            return False
        self._redo.append(self._snapshot())
        return self._restore_snapshot(self._history.pop())

    def redo(self) -> bool:
        if not self._redo:
            self.statusMessage.emit("没有可重做的操作。")
            return False
        self._history.append(self._snapshot())
        return self._restore_snapshot(self._redo.pop())

    def _restore_snapshot(self, snapshot: tuple[str, str]) -> bool:
        text, path = snapshot
        try:
            node = SnbtParser(text).parse()
        except SnbtParseError:                             # pragma: no cover
            return False
        self._root = node
        self._show_error("")
        self._after_model_change(keep=None)
        if path:
            self.select_by_path(path)
        return True

    def clear_history(self) -> None:
        """文件重新加载时清空撤销栈。"""
        self._history.clear()
        self._redo.clear()

    # ==================================================================
    # 查找 / 替换
    # ==================================================================

    def _matches(self) -> list[SnbtNode]:
        bar = self.find_bar
        return find_nodes(
            self._root, bar.find_edit.text(),
            in_values=bar.match_values.isChecked(),
            in_keys=bar.match_keys.isChecked(),
            case_sensitive=bar.match_case.isChecked(),
            exact=bar.match_exact.isChecked())

    def find_next(self, forward: bool = True) -> None:
        matches = self._matches()
        bar = self.find_bar
        if not matches:
            bar.result.setText("没有匹配项")
            self._find_index = -1
            return
        current = self.selected_node()
        index = -1
        for position, node in enumerate(matches):
            if node is current:
                index = position
                break
        if index >= 0:
            index = (index + (1 if forward else -1)) % len(matches)
        else:
            index = 0 if forward else len(matches) - 1
        self._find_index = index
        node = matches[index]
        if not self.select_by_node(node):
            bar.result.setText("匹配项在当前视图中不可见")
            return
        bar.result.setText(f"第 {index + 1} / {len(matches)} 个")
        self._highlight_needle(bar.find_edit.text(), node)

    def _highlight_needle(self, needle: str, node: SnbtNode) -> None:
        """把树里所有命中片段高亮出来。"""
        self.value_delegate.set_needle(
            needle, self.find_bar.match_case.isChecked(),
            self.find_bar.match_exact.isChecked())

    def select_by_node(self, node: SnbtNode) -> bool:
        for item in self._all_items():
            if item.node is node:
                if item.isHidden():
                    self.filter_edit.clear()
                self._set_current(item)
                self.tree.scrollToItem(item)
                return True
        return False

    def replace_current(self) -> None:
        node = self.selected_node()
        bar = self.find_bar
        if node is None or node.kind != SnbtKind.STRING:
            bar.result.setText("请先选中一个 String 标签")
            return
        needle = bar.find_edit.text()
        if not needle:
            return
        self._push_history()
        if bar.match_case.isChecked():
            node.value = str(node.value).replace(needle, bar.replace_edit.text())
        else:
            node.value = _replace_ignore_case(
                str(node.value), needle, bar.replace_edit.text())
        self._after_model_change(keep=node)
        bar.result.setText("已替换 1 处")

    def replace_all(self) -> None:
        bar = self.find_bar
        needle = bar.find_edit.text()
        if not needle:
            return
        replacement = bar.replace_edit.text()
        exact = bar.case_only.isChecked()
        self._push_history()
        count = 0
        for node in [n for n in _iter(self._root)
                     if n.kind == SnbtKind.STRING]:
            text = str(node.value)
            if exact:
                if text != needle:
                    continue
                node.value = replacement
                count += 1
                continue
            if bar.match_case.isChecked():
                if needle not in text:
                    continue
                node.value = text.replace(needle, replacement)
                count += text.count(needle)
            else:
                new_text, hits = _replace_ignore_case_count(
                    text, needle, replacement)
                if hits:
                    node.value = new_text
                    count += hits
        if not count:
            bar.result.setText("没有匹配项")
            return
        self._after_model_change(keep=None)
        bar.result.setText(f"共替换 {count} 处")

    # ==================================================================
    # 右键菜单与模板
    # ==================================================================

    def _on_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if isinstance(item, EditorItem):
            self._set_current(item)
        node = self.selected_node()
        parent = self._parent_node(node) if node else None
        selected = self.selected_nodes()
        many = len(selected) > 1
        menu = QMenu(self)
        menu.addAction("编辑取值…", self.edit_selected_value).setEnabled(
            node is not None and not node.is_container)
        menu.addAction("重命名…", self.rename_selected).setEnabled(
            node is not None and node.key is not None and not many)
        menu.addSeparator()
        act_new = menu.addAction("新建子标签 ▾")
        new_menu = QMenu(menu)
        target = node if node is not None and node.is_container else self._root
        if target.kind == SnbtKind.COMPOUND:
            for label, kind in CREATABLE:
                action = new_menu.addAction(label)
                action.triggered.connect(
                    lambda _=False, k=kind: self._insert_via_menu(k))
        else:
            expected = element_kind(target)
            choices = CREATABLE if expected is None else [
                (label, kind) for label, kind in CREATABLE if kind == expected]
            for label, kind in choices:
                action = new_menu.addAction(f"元素：{label}")
                action.triggered.connect(
                    lambda _=False, k=kind: self._insert_via_menu(k))
        act_new.setMenu(new_menu)
        menu.addAction("追加元素…", self.append_element).setEnabled(
            node is not None and node.kind in (SnbtKind.LIST, *ARRAY_KINDS))
        menu.addAction("复制一份", self.duplicate_selected).setEnabled(
            node is not None and node is not self._root)
        menu.addSeparator()
        menu.addAction("复制（可粘贴到别处）", self.copy_selected).setEnabled(
            node is not None and node is not self._root)
        menu.addAction("剪切", self.cut_selected).setEnabled(
            node is not None and node is not self._root)
        menu.addAction("粘贴为子节点", self.paste_into_selected)
        menu.addAction("复制为 SNBT 文本", self.copy_as_snbt)
        menu.addAction("复制为 /data 命令", self.copy_as_command)
        menu.addAction("复制路径", self.copy_path)
        if self._current_stack() is not None:
            menu.addSeparator()
            menu.addAction("→ 送进 /give 面板", self._send_item_to_give)
            menu.addAction("复制物品 /give 命令", self.copy_item_command)
            menu.addAction("复制物品 SNBT", self.copy_item_snbt)
            menu.addAction("复制物品 /data 命令", self.copy_item_data_command)
        entity = self._current_entity()
        if entity is not None:
            menu.addSeparator()
            menu.addAction("→ 送进 /summon 面板", self._send_entity_to_summon)
            menu.addAction("复制 /summon 命令", self.copy_entity_command)
            menu.addAction("复制实体 NBT", self.copy_entity_nbt)
        menu.addSeparator()
        if self._item_paths:
            menu.addAction("存档物品一览…", self.overviewRequested.emit)
        menu.addAction("设置：专有名词翻译…", self.settingsRequested.emit)
        menu.addSeparator()
        can_move = node is not None and parent is not None and not many \
            and parent.kind == SnbtKind.COMPOUND
        menu.addAction("上移", lambda: self.move_selected(-1)).setEnabled(can_move)
        menu.addAction("下移", lambda: self.move_selected(1)).setEnabled(can_move)
        menu.addSeparator()
        menu.addAction("删除", self.delete_selected).setEnabled(
            node is not None and node is not self._root)
        menu.addSeparator()
        act_undo = menu.addAction("撤销", self.undo)
        act_undo.setEnabled(self.can_undo())
        act_redo = menu.addAction("重做", self.redo)
        act_redo.setEnabled(self.can_redo())
        menu.addSeparator()
        menu.addAction("在树中查找…", self.show_find_bar)
        menu.addAction("筛选框", self.focus_filter)
        menu.addSeparator()
        menu.addAction("全部展开", self.expand_all)
        menu.addAction("全部折叠", self.collapse_all)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _insert_via_menu(self, kind: SnbtKind) -> None:
        item = self.tree.currentItem()
        parent = item.node if isinstance(item, EditorItem) else self._root
        if parent is None or not parent.is_container:
            parent = self._root
        if parent.kind == SnbtKind.COMPOUND:
            self.insert_node(parent, kind)
        else:
            self.add_list_element(parent, kind)

    def _on_insert_template(self) -> None:
        from app.ui.widgets import TemplateBrowser
        dialog = TemplateBrowser(self, title="插入模板")
        if dialog.exec() != TemplateBrowser.DialogCode.Accepted:
            return
        snbt = dialog.selected_snbt()
        if not snbt.strip():
            return
        if self.apply_snbt_merge(snbt):
            self.statusMessage.emit("模板已插入。")

    # ---- 文本模式 → 模型 ----

    def _on_text_changed(self) -> None:
        if self._updating:
            return
        if self._text_timer is None:
            self._text_timer = QTimer(self)
            self._text_timer.setSingleShot(True)
            self._text_timer.setInterval(500)
            self._text_timer.timeout.connect(self._apply_text_now)
        self._text_timer.start()

    def _apply_text_now(self) -> None:
        """把文本模式的内容解析回模型（停笔 0.5 秒后自动执行）。"""
        if self._text_timer is not None:
            self._text_timer.stop()
        text = self.text.toPlainText()
        if not text.strip():
            self._root = SnbtNode.new_compound()
            self._show_error("")
            self._after_model_change(keep=None)
            return
        try:
            node = SnbtParser(text).parse()
        except SnbtParseError as exc:
            self._show_error(str(exc))
            return
        self._show_error("")
        self._root = node
        self._after_model_change(keep=None)

    def apply_snbt_merge(self, snbt: str) -> bool:
        """把一段 SNBT 合并进当前根节点。"""
        try:
            node = SnbtParser(snbt).parse()
        except SnbtParseError as exc:
            self._show_error(str(exc))
            return False
        self._push_history()
        if self._root.kind == SnbtKind.COMPOUND \
                and node.kind == SnbtKind.COMPOUND:
            for child in node.children:
                old = self._root.get_child(child.key or "")
                if old is not None:
                    self._root.remove_child(old)
                self._root.add_child(child)
        else:
            self._root = node
        self._refresh_tree()
        self._refresh_text()
        self._mark_changed()
        return True

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)

    def _after_model_change(self, keep: SnbtNode | None = None) -> None:
        """模型改完后统一刷新（保持选中/展开），并通知外部。"""
        path = ""
        if keep is not None:
            for item in self._all_items():
                if item.node is keep:
                    path = item.path
                    break
            if not path:
                path = node_path(keep, self._root)
                if path == "(根)":
                    path = ""
        self._refresh_tree()
        if path:
            self.select_by_path(path)
        self._mark_changed()

    def _mark_changed(self) -> None:
        if self.stack.currentIndex() != 1:
            self._refresh_text()
        self.summary_text = summarize(self._root)
        self._filter_source = None      # 数据变了，命中集合要重算
        self._update_status()
        self._set_detail_buttons(self.selected_node())
        self.changed.emit()

    def startDrag(self, actions) -> None:  # noqa: N802
        """只允许拖动单个节点，避免多选拖放时丢数据。"""
        if len(self.tree.selectedItems()) > 1:
            return
        super().startDrag(actions)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _join_key(parent_path: str, key: str | None) -> str:
    name = str(key or "")
    if not parent_path:
        return name
    return f"{parent_path}.{name}"


def display_type_label(node: SnbtNode) -> str:
    """类型列的显示文本（按设置翻译，例如 String → 字符串）。"""
    return app_settings.translate_nbt_type(type_label(node))


def _structure_hint(node: SnbtNode, root_name: str = "") -> str:
    """已知「不是世界存档」的结构给一句说明，免得用户以为文件读错了。"""
    keys = {str(c.key or "") for c in node.children}
    name = root_name.strip()
    if name.lower() in ("fml", "neoforge") or {"LoadingModList", "ModFileList"} & keys:
        return ("这是 Forge / NeoForge 的模组清单（根标签 fml），不是世界存档："
                "每条只记录 ModId（模组 ID）与 ModSize（文件大小），"
                "里面没有可读的模组名称。\n"
                "世界存档请打开存档目录里的 level.dat（根下应有 Data 标签）"
                "或 playerdata/*.dat。")
    # 世界存档：level.dat 的数据都在 Data 里
    for candidate in (node, node.get_child("Data")):
        if candidate is None or candidate.kind != SnbtKind.COMPOUND:
            continue
        inner = {str(c.key or "") for c in candidate.children}
        if {"LevelName", "Player", "Version", "DataVersion"} & inner:
            return ("这是世界存档（level.dat）："
                    "`Data.Player.Inventory` 是玩家背包、"
                    "`Data.Player.EnderItems` 是末影箱，"
                    "物品可以直接送进 /give 面板。")
    return ""


def _stack_tooltip(stack: ItemStack) -> str:
    lines = [f"{stack.display()}（{stack.clean_id()}）× {stack.count}"]
    if stack.slot is not None:
        lines.append(f"槽位：{stack.slot}")
    if stack.path:
        lines.append(f"位置：{stack.path}")
    for enchant_id, level in stack.enchantments:
        from app.core.nbt_bridge import enchant_display, roman
        lines.append(f"附魔：{enchant_display(enchant_id)} {roman(level)}")
    if stack.lore:
        lines.append("描述：" + " / ".join(stack.lore))
    lines.append("按右键可复制为 /give 命令，或送进 /give 面板")
    return "\n".join(lines)


def _has_array(node: SnbtNode) -> bool:
    if node.kind in ARRAY_KINDS:
        return True
    return any(_has_array(child) for child in node.children)


def _make_mime(node: SnbtNode):
    from PySide6.QtCore import QMimeData
    mime = QMimeData()
    text = serialize(node, pretty=False)
    mime.setText(text)
    mime.setData("application/x-nbt-node", text.encode("utf-8"))
    return mime


def _iter(node: SnbtNode):
    yield node
    for child in node.children:
        yield from _iter(child)


def _replace_ignore_case(text: str, needle: str, replacement: str) -> str:
    return _replace_ignore_case_count(text, needle, replacement)[0]


def _replace_ignore_case_count(text: str, needle: str,
                               replacement: str) -> tuple[str, int]:
    lowered = text.lower()
    target = needle.lower()
    if target not in lowered:
        return text, 0
    out: list[str] = []
    start = 0
    hits = 0
    while True:
        found = lowered.find(target, start)
        if found < 0:
            out.append(text[start:])
            break
        out.append(text[start:found])
        out.append(replacement)
        start = found + len(needle)
        hits += 1
    return "".join(out), hits


__all__ = ["NbtTreeEditor", "EditorItem", "MAX_ELEMENT_ROWS",
           "COL_NAME", "COL_TYPE", "COL_VALUE"]
