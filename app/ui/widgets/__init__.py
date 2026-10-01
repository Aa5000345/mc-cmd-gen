"""通用控件集：坐标、ID 选择、NBT 编辑器、目标选择器、组件编辑、
推荐侧栏、模板浏览器、目标选择器对话框。"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import (
    Qt, Signal, QTimer, QPoint, QMimeData, QStringListModel, QSize,
)
from PySide6.QtGui import QFont, QCursor, QDrag, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QToolButton,
    QComboBox, QLineEdit, QLabel, QScrollArea, QCheckBox, QFrame,
    QPushButton, QDialog, QDialogButtonBox, QPlainTextEdit,
    QListWidget, QListWidgetItem, QTreeWidget, QTreeWidgetItem,
    QMenu, QMessageBox, QAbstractItemView, QSplitter, QCompleter,
    QSpinBox, QTableWidget, QTableWidgetItem, QHeaderView, QStyle,
    QApplication, QFileDialog,
)

from app.core.snbt import (
    SnbtNode, SnbtKind, SnbtParser, SnbtParseError, serialize,
    make_snbt_highlighter,
)
from app.core.nbt_binary import NbtDocument, NbtFormatError, NbtWriteError
from app.core.data import store as data_store
from app.core.data import recommender
from app.core.workbench import TargetSelector, BASES, FILTER_DEFS
from app.ui.mime import MIME_NBT, make_mime
from app.ui.theme import colors as theme_colors, hint_style, muted_style


# =========================================================================
# 坐标输入
# =========================================================================

class CoordInput(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)

        self.enable = QCheckBox("使用坐标")
        self.enable.setToolTip("勾选后命令里才会写坐标（否则用 ~ ~ ~ 的默认值）")
        self.enable.stateChanged.connect(self._on_toggle)
        h.addWidget(self.enable)
        h.addSpacing(4)

        self.x = QLineEdit("~")
        self.y = QLineEdit("~")
        self.z = QLineEdit("~")
        for label, w in (("X", self.x), ("Y", self.y), ("Z", self.z)):
            tag = QLabel(label)
            tag.setStyleSheet(muted_style(tag))
            h.addWidget(tag)
            w.setMinimumWidth(58)
            w.setMaximumWidth(90)
            w.setToolTip("支持 ~ 与 ^ 相对坐标，也可写小数")
            w.setEnabled(False)
            w.textChanged.connect(lambda *_: self.changed.emit())
            h.addWidget(w, 1)

    def _on_toggle(self, state: int) -> None:
        on = self.enable.isChecked()
        for w in (self.x, self.y, self.z):
            w.setEnabled(on)
        self.changed.emit()

    def is_enabled(self) -> bool:
        return self.enable.isChecked()

    def values(self) -> tuple[bool, str, str, str]:
        return (self.enable.isChecked(),
                self.x.text().strip() or "~",
                self.y.text().strip() or "~",
                self.z.text().strip() or "~")

    def set_values(self, enabled: bool, x: str, y: str, z: str) -> None:
        widgets = (self.enable, self.x, self.y, self.z)
        for w in widgets:
            w.blockSignals(True)
        try:
            self.enable.setChecked(enabled)
            self.x.setText(x)
            self.y.setText(y)
            self.z.setText(z)
            for w in (self.x, self.y, self.z):
                w.setEnabled(enabled)
        finally:
            for w in widgets:
                w.blockSignals(False)


# =========================================================================
# ID 选择器
# =========================================================================

class IdPicker(QWidget):
    idChanged = Signal(str)

    def __init__(self, entries: Iterable[dict] | None = None,
                 title: str = "ID", parent=None):
        super().__init__(parent)
        self._all: list[dict] = list(entries or [])
        self._title = title
        self._build_ui()
        self._reload_categories()
        self._apply_filter()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        top = QHBoxLayout()
        top.addWidget(QLabel(self._title))
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索 ID 或中文名…")
        self.search.textChanged.connect(self._apply_filter)
        top.addWidget(self.search, 1)
        v.addLayout(top)

        cat_row = QHBoxLayout()
        cat_row.addWidget(QLabel("分类"))
        self.category = QComboBox()
        self.category.currentIndexChanged.connect(self._apply_filter)
        cat_row.addWidget(self.category, 1)
        v.addLayout(cat_row)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.currentItemChanged.connect(self._on_selected)
        v.addWidget(self.list, 1)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("已选"))
        self.current = QLineEdit()
        self.current.setPlaceholderText("minecraft:...")
        self.current.textChanged.connect(self._on_text_changed)
        bottom.addWidget(self.current, 1)
        v.addLayout(bottom)

    def _reload_categories(self) -> None:
        cats = ["全部"]
        seen = set()
        for e in self._all:
            c = e.get("category", "其他")
            if c not in seen:
                seen.add(c)
                cats.append(c)
        self.category.blockSignals(True)
        self.category.clear()
        for c in cats:
            self.category.addItem(c)
        self.category.blockSignals(False)

    def _apply_filter(self) -> None:
        keyword = self.search.text().strip().lower()
        cat = self.category.currentText() if self.category.count() else "全部"
        self.list.blockSignals(True)
        self.list.clear()
        shown = 0
        for e in self._all:
            if cat and cat != "全部" and e.get("category") != cat:
                continue
            hay = (e.get("id", "") + " " + e.get("display", "")).lower()
            if keyword and keyword not in hay:
                continue
            display = f"{e.get('id','')}  —  {e.get('display','')}"
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, e.get("id", ""))
            self.list.addItem(item)
            shown += 1
        if shown == 0:
            # 静默的空列表让人以为「程序坏了」：给一行不可选的说明
            empty = QListWidgetItem(
                "没有匹配项 —— 可以直接在上面手打 ID，"
                "或者试试中文名（如「僵尸」「钻石剑」）"
                if keyword else "这里还没有数据")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(empty)
        self.list.blockSignals(False)

    def _on_selected(self, current: QListWidgetItem, _prev) -> None:
        if current is None:
            return
        if not (current.flags() & Qt.ItemFlag.ItemIsSelectable):
            return                      # 占位提示行：不要动用户手打的 ID
        val = current.data(Qt.ItemDataRole.UserRole) or ""
        self.current.blockSignals(True)
        self.current.setText(val)
        self.current.blockSignals(False)
        self.idChanged.emit(val)

    def _on_text_changed(self, text: str) -> None:
        self.idChanged.emit(text.strip())

    def set_id(self, value: str) -> None:
        self.current.blockSignals(True)
        self.current.setText(value or "")
        self.current.blockSignals(False)

    def get_id(self) -> str:
        return self.current.text().strip()

    def set_entries(self, entries: Iterable[dict]) -> None:
        self._all = list(entries)
        self._reload_categories()
        self._apply_filter()

    # ✅ F-03：参数级历史推荐
    def set_recent(self, values: list[str]) -> None:
        """为输入框绑定最近使用值自动补全。"""
        if not values:
            return
        model = QStringListModel(values, self)
        completer = QCompleter(model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.current.setCompleter(completer)
        self.search.setCompleter(completer)


# =========================================================================
# NBT 树形 + 文本编辑器
# =========================================================================


class NbtFileEditor(QWidget):
    """完整的 NBT 文件编辑器：文件工具条 + 树形/文本编辑。

    支持 .dat / .nbt / .snbt（以及 .dat_old、.schem 等常见后缀），
    打开时记录源文件的压缩方式与根标签名，保存默认按原格式写回。
    工具条上还有「最近打开」「定位到路径」，编辑区本身是 NbtTreeEditor。
    """

    dirtyChanged = Signal(bool)
    fileChanged = Signal(str)
    statusMessage = Signal(str)
    #: 请求把识别出的物品送进 `/give` 面板（由主窗口处理）
    giveRequested = Signal(object)
    #: 请求把识别出的实体送进 `/summon` 面板（由主窗口处理）
    summonRequested = Signal(object)
    #: 请求打开设置（专有名词翻译等）
    settingsRequested = Signal()
    #: 物品一览里「全部 → 输出」的命令
    commandsReady = Signal(str)

    RECENT_KEY = "nbt_recent_files"
    RECENT_MAX = 12

    def __init__(self, parent=None, *, embedded: bool = False):
        """`embedded=True` 表示这棵文档属于世界编辑器：

        不能新建 / 打开 / 直接保存（存档写入必须走 WorldSession 的事务），
        但「另存为」「物品一览」「结构」「定位」全部保留。
        """
        super().__init__(parent)
        self.embedded = bool(embedded)
        self.doc = NbtDocument()
        self.setAcceptDrops(not self.embedded)
        self._build_ui()
        if self.embedded:
            self._enter_embedded_mode()
        self._reload_recent()
        self.refresh_title()

    # ---- 嵌入式（世界编辑器）模式 ----

    def _enter_embedded_mode(self) -> None:
        self.btn_new.setVisible(False)
        self.btn_open.setVisible(False)
        self.btn_recent.setVisible(False)
        self.btn_save.setText("另存为…")
        self.btn_save.setToolTip(
            "把当前文档另存成 .dat / .nbt / .snbt（不会直接改写存档；\n"
            "存档写入请用编辑器面板的「应用修改」）")
        self.btn_save.clicked.disconnect()
        self.btn_save.clicked.connect(self.save_as)
        self.btn_reload.setToolTip("从存档目录重新读取（会丢弃未应用的修改）")

    def bind_document(self, root: SnbtNode, *, source: str = "",
                      compression: str = "gzip", root_name: str = "",
                      source_size: int = 0) -> None:
        """绑定一棵「来自存档」的树（不落盘、路径为空 → 保存不会覆盖存档）。"""
        self.doc = NbtDocument(root=root, path=None, root_name=root_name,
                               compression=compression,
                               source_size=source_size,
                               display_name=source or "存档数据")
        self.editor.set_source_label(source)
        self.editor.set_root(root)
        self.editor.set_root_name(root_name)
        self.editor.clear_history()
        self.root_name.blockSignals(True)
        self.root_name.setText(root_name)
        self.root_name.blockSignals(False)
        index = self.compression.findData(compression)
        self.compression.setCurrentIndex(index if index >= 0 else 0)
        self._mark_dirty(False)
        self.refresh_title()

    # ---- 界面 ----

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.btn_new = QPushButton("新建")
        self.btn_new.setToolTip("新建一个空 NBT 文档（未保存，Ctrl+Shift+M）")
        self.btn_new.clicked.connect(self.new_document)
        self.btn_open = QPushButton("打开…")
        self.btn_open.setToolTip("打开 .dat / .nbt / .snbt 文件（Ctrl+O）")
        self.btn_open.clicked.connect(self.open_dialog)
        self.btn_save = QPushButton("保存")
        self.btn_save.setToolTip("按原格式写回原文件（Ctrl+S）")
        self.btn_save.clicked.connect(self.save)
        self.btn_save_as = QPushButton("另存为…")
        self.btn_save_as.setToolTip("另存为新文件，可选文本 SNBT（Ctrl+Shift+S）")
        self.btn_save_as.clicked.connect(self.save_as)
        self.btn_reload = QPushButton("重新载入")
        self.btn_reload.setToolTip("丢弃当前修改，重新从磁盘读取（F5）")
        self.btn_reload.clicked.connect(self.reload)
        for btn, icon_name in ((self.btn_new, "SP_FileIcon"),
                               (self.btn_open, "SP_DirOpenIcon"),
                               (self.btn_save, "SP_DialogSaveButton"),
                               (self.btn_save_as, "SP_DriveFDIcon"),
                               (self.btn_reload, "SP_BrowserReload")):
            icon = self.style().standardIcon(
                getattr(QStyle.StandardPixmap, icon_name))
            if not icon.isNull():
                btn.setIcon(icon)
            bar.addWidget(btn)

        self.btn_recent = QToolButton()
        self.btn_recent.setText("最近打开 ▾")
        self.btn_recent.setToolTip("最近打开过的 NBT 文件")
        self.btn_recent.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self.recent_menu = QMenu(self.btn_recent)
        self.btn_recent.setMenu(self.recent_menu)
        bar.addWidget(self.btn_recent)

        bar.addSpacing(8)
        self.btn_overview = QPushButton("存档物品一览…")
        self.btn_overview.setToolTip(
            "把存档里的背包 / 箱子内容列成表，可一键送进 /give 面板")
        self.btn_overview.clicked.connect(self.show_item_overview)
        bar.addWidget(self.btn_overview)
        self.btn_structure = QPushButton("结构…")
        self.btn_structure.setToolTip(
            "看一眼这个文件里到底有哪些标签（确认 Data 之类是否在文件里）")
        self.btn_structure.clicked.connect(self.show_structure)
        bar.addWidget(self.btn_structure)
        self.btn_settings = QPushButton("设置…")
        self.btn_settings.setToolTip("专有名词翻译与 NBT 显示选项")
        self.btn_settings.clicked.connect(self.settingsRequested.emit)
        bar.addWidget(self.btn_settings)

        bar.addWidget(QLabel("写出格式"))
        self.compression = QComboBox()
        self.compression.addItem("跟随源文件", "")
        for key, label in (("none", "未压缩（原版 .dat）"),
                           ("gzip", "gzip 压缩"),
                           ("zlib", "zlib 压缩")):
            self.compression.addItem(label, key)
        self.compression.setToolTip(
            "保存时使用的容器格式；默认跟随打开时的格式")
        bar.addWidget(self.compression)

        bar.addWidget(QLabel("根标签名"))
        self.root_name = QLineEdit()
        self.root_name.setPlaceholderText("（通常留空）")
        self.root_name.setFixedWidth(120)
        self.root_name.setToolTip(
            "level.dat / playerdata 等文件的根标签名通常为空，保持默认即可")
        self.root_name.textChanged.connect(self._on_root_name_changed)
        bar.addWidget(self.root_name)
        bar.addStretch(1)
        v.addLayout(bar)

        self.editor = NbtTreeEditor()
        self.editor.changed.connect(self._on_edited)
        self.editor.requestNewFile.connect(self.new_document)
        self.editor.statusMessage.connect(self.statusMessage.emit)
        self.editor.giveRequested.connect(self.giveRequested.emit)
        self.editor.summonRequested.connect(self.summonRequested.emit)
        self.editor.settingsRequested.connect(self.settingsRequested.emit)
        self.editor.overviewRequested.connect(self.show_item_overview)
        v.addWidget(self.editor, 1)

        self.hint = QLabel("")
        self.hint.setStyleSheet(muted_style(self.hint))
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)

        for keys, slot in (("Ctrl+O", self.open_dialog),
                           ("Ctrl+S", self.save),
                           ("Ctrl+Shift+S", self.save_as),
                           ("F5", self.reload)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)

    # ---- 拖放 ----

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self.load_path(path)
                event.acceptProposedAction()
                return

    # ---- 最近打开 ----

    def _reload_recent(self) -> None:
        self.recent_menu.clear()
        paths = self._recent_paths()
        if not paths:
            action = self.recent_menu.addAction("（还没有打开过文件）")
            action.setEnabled(False)
            return
        for path in paths:
            action = self.recent_menu.addAction(Path(path).name)
            action.setToolTip(path)
            action.triggered.connect(
                lambda _=False, p=path: self.load_path(p))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("清空列表", self._clear_recent)

    def _recent_paths(self) -> list[str]:
        try:
            from app.storage import DraftStore
            data = DraftStore().load(self.RECENT_KEY) or {}
            return [str(p) for p in data.get("paths", [])][:self.RECENT_MAX]
        except Exception:
            return []

    def _remember_recent(self, path) -> None:
        try:
            from app.storage import DraftStore
            target = str(Path(path).resolve())
            paths = [p for p in self._recent_paths() if p != target]
            paths.insert(0, target)
            DraftStore().save(self.RECENT_KEY,
                              {"paths": paths[:self.RECENT_MAX]})
        except Exception:
            pass
        self._reload_recent()

    def _clear_recent(self) -> None:
        try:
            from app.storage import DraftStore
            DraftStore().save(self.RECENT_KEY, {"paths": []})
        except Exception:
            pass
        self._reload_recent()

    # ---- 文档操作 ----

    def new_document(self) -> None:
        if not self._confirm_discard():
            return
        self.doc = NbtDocument()
        self.editor.set_root(self.doc.root)
        self.editor.set_root_name("")
        self.editor.clear_history()
        self.root_name.blockSignals(True)
        self.root_name.setText("")
        self.root_name.blockSignals(False)
        self.compression.setCurrentIndex(0)
        self.refresh_title()
        self.statusMessage.emit("已新建空 NBT 文档。")

    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 NBT 文件", "",
            "NBT 文件 (*.dat *.dat_old *.nbt *.snbt *.schem *.schematic);;"
            "所有文件 (*)")
        if path:
            self.load_path(path)

    def load_path(self, path: str) -> bool:
        if not self._confirm_discard():
            return False
        try:
            doc = NbtDocument.load(path)
        except NbtFormatError as exc:
            QMessageBox.warning(self, "打开失败", str(exc))
            return False
        except Exception as exc:                       # pragma: no cover
            QMessageBox.warning(self, "打开失败", f"{type(exc).__name__}: {exc}")
            return False
        self.doc = doc
        self.editor.set_source_label(doc.name)
        self.editor.set_root(doc.root)
        self.editor.set_root_name(doc.root_name)
        self.editor.clear_history()
        self.root_name.blockSignals(True)
        self.root_name.setText(doc.root_name)
        self.root_name.blockSignals(False)
        self.compression.setCurrentIndex(0)
        if doc.is_text:
            # SNBT 文本文件没有「压缩格式」和「根标签名」的概念
            self.editor._set_mode("text")
        else:
            self.editor._set_mode("tree")
        self.refresh_title()
        self._remember_recent(path)
        self.statusMessage.emit(
            f"已打开 {doc.name}（{doc.compression_label}）。")
        return True

    def reload(self) -> bool:
        """丢弃当前修改，重新从磁盘读。"""
        if self.doc.path is None:
            return False
        path = str(self.doc.path)
        self.doc.dirty = False                 # 已经是「用户明确要求重载」
        self.doc = NbtDocument()
        return self.load_path(path)

    def save(self) -> bool:
        if self.doc.path is None:
            return self.save_as()
        return self._write(self.doc.path)

    def save_as(self) -> bool:
        default = (str(self.doc.path) if self.doc.path else "untitled.dat")
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为 NBT 文件", default,
            "NBT 文件 (*.dat *.nbt *.schem *.schematic);;"
            "文本 SNBT (*.snbt);;所有文件 (*)")
        if not path:
            return False
        return self._write(path)

    def _write(self, path) -> bool:
        self._sync_root_name()
        self.doc.root = self.editor.root()
        choice = self.compression.currentData() or None
        try:
            target = self.doc.save(path, compression=choice)
        except NbtWriteError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return False
        except Exception as exc:                       # pragma: no cover
            QMessageBox.warning(self, "保存失败", f"{type(exc).__name__}: {exc}")
            return False
        self.editor.set_root_name(self.doc.root_name)
        self.refresh_title()
        self._remember_recent(target)
        self.statusMessage.emit(f"已保存 {target}（{self.doc.compression_label}）。")
        return True

    def _sync_root_name(self) -> None:
        self.doc.root_name = self.root_name.text().strip()

    def _on_root_name_changed(self, text: str) -> None:
        if self.doc.root_name != text.strip():
            self.doc.root_name = text.strip()
            self.editor.set_root_name(text.strip())
            self._mark_dirty(True)

    def _on_edited(self) -> None:
        self._mark_dirty(True)

    def _mark_dirty(self, dirty: bool) -> None:
        if self.doc.dirty != dirty:
            self.doc.dirty = dirty
            self.dirtyChanged.emit(dirty)
        self.refresh_title()

    def refresh_title(self) -> None:
        self.hint.setText(self.doc.info_line())
        self.btn_save.setEnabled(True)
        self.btn_reload.setEnabled(self.doc.path is not None)
        items = len(getattr(self.editor, "_item_paths", ()) or ())
        self.btn_overview.setEnabled(items > 0)
        self.btn_overview.setText(f"存档物品一览（{items}）…" if items
                                  else "存档物品一览…")
        self.btn_overview.setToolTip(
            f"存档里识别到 {items} 件物品，点这里列成表并可送进 /give 面板"
            if items else
            "当前文档里没有识别到物品（需要是存档里的背包 / 箱子数据）")
        self.fileChanged.emit(self.doc.display_title())

    # ---- 存档物品一览 ----

    def show_structure(self) -> None:
        """文件结构总览：确认某个标签是不是真的不在文件里。"""
        from app.ui.dialogs import StructureDialog
        dialog = StructureDialog(self.doc, self)
        dialog.exec()


    def show_item_overview(self) -> None:
        """把识别到的物品列成图形化表格。"""
        from app.ui.dialogs import ItemOverviewDialog
        items = len(getattr(self.editor, "_item_paths", ()) or ())
        if not items:
            QMessageBox.information(
                self, "没有识别到物品",
                "当前文档里没有找到物品数据。\n\n"
                "物品一览会读取存档里的 `Inventory` / `Items` / `EnderItems` /\n"
                "`ArmorItems` 这类背包数据（例如 level.dat 或 playerdata）。")
            return
        dialog = ItemOverviewDialog(self.editor.root(), self,
                                    source=self.doc.name)
        dialog.giveRequested.connect(self.giveRequested.emit)
        accepted = dialog.exec() == ItemOverviewDialog.DialogCode.Accepted
        if accepted and dialog.outputLines:
            self.statusMessage.emit(
                f"已生成 {len(dialog.outputLines)} 条 /give 命令。")
            self.commandsReady.emit("\n".join(dialog.outputLines))

    def current_snbt(self) -> str:
        return self.editor.to_snbt(pretty=False)

    # 兼容主窗口原来的接口
    def set_root(self, node: SnbtNode) -> None:
        self.doc = NbtDocument(root=node)
        self.editor.set_root(node)
        self.refresh_title()

    def root(self) -> SnbtNode:
        return self.editor.root()

    def to_snbt(self, pretty: bool = True) -> str:
        return self.editor.to_snbt(pretty=pretty)

    def confirm_discard(self) -> bool:
        """给主窗口用：有未保存修改时先问一句。"""
        return self._confirm_discard()

    def _confirm_discard(self) -> bool:
        if not self.doc.dirty:
            return True
        answer = QMessageBox.question(
            self, "尚未保存",
            f"{self.doc.name} 有未保存的修改，是否先保存？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return True


# =========================================================================
# 目标选择器编辑器
# =========================================================================

class NbtEditDialog(QDialog):
    def __init__(self, initial: str = "", parent=None,
                 title: str = "编辑 NBT"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 540)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.editor = NbtTreeEditor()
        if initial.strip():
            self.editor.set_snbt(initial.strip())
        layout.addWidget(self.editor, 1)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def value(self) -> str:
        return self.editor.to_snbt(pretty=False)


class FilterRow(QWidget):
    changed = Signal()

    def __init__(self, definition: dict, parent=None):
        super().__init__(parent)
        self.defn = definition
        self.key = definition["key"]
        self.kind = definition["kind"]
        self._updating = False

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(6)

        self.enabled = QCheckBox()
        self.enabled.setFixedWidth(20)
        self.enabled.stateChanged.connect(self._on_toggle)
        row.addWidget(self.enabled)

        label = QLabel(definition["label"])
        label.setFixedWidth(96)
        label.setToolTip(definition.get("hint", ""))
        row.addWidget(label)

        if self.kind == "enum":
            combo = QComboBox()
            combo.setEditable(True)
            for opt in definition.get("options", []):
                combo.addItem(opt)
            combo.currentTextChanged.connect(self._on_value_changed)
            self.editor = combo
            row.addWidget(combo, 1)
        elif self.kind in ("nbt", "scores"):
            line = QLineEdit()
            line.setPlaceholderText(definition.get("hint", ""))
            line.textChanged.connect(self._on_value_changed)
            self.editor = line
            row.addWidget(line, 1)
            btn = QPushButton("编辑…")
            btn.setFixedWidth(72)
            btn.clicked.connect(self._open_nbt_dialog)
            row.addWidget(btn)
        else:
            line = QLineEdit()
            line.setPlaceholderText(definition.get("hint", ""))
            line.textChanged.connect(self._on_value_changed)
            self.editor = line
            row.addWidget(line, 1)

        self.editor.setEnabled(False)

    def _on_toggle(self, state: int) -> None:
        self.editor.setEnabled(state == Qt.CheckState.Checked.value)
        self._on_value_changed()

    def _on_value_changed(self, *args) -> None:
        if self._updating:
            return
        self.changed.emit()

    def _open_nbt_dialog(self) -> None:
        dlg = NbtEditDialog(self._current_text(), self,
                            f"编辑 {self.defn['label']}")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._set_text(dlg.value())

    def _current_text(self) -> str:
        if isinstance(self.editor, QComboBox):
            return self.editor.currentText().strip()
        if isinstance(self.editor, QLineEdit):
            return self.editor.text().strip()
        return ""

    def _set_text(self, value: str) -> None:
        self._updating = True
        try:
            if isinstance(self.editor, QComboBox):
                idx = self.editor.findText(value)
                if idx >= 0:
                    self.editor.setCurrentIndex(idx)
                else:
                    self.editor.setEditText(value)
            elif isinstance(self.editor, QLineEdit):
                self.editor.setText(value)
        finally:
            self._updating = False

    def set_value(self, value: Optional[str]) -> None:
        self._updating = True
        try:
            if value is None:
                self.enabled.setChecked(False)
                self.editor.setEnabled(False)
                self._set_text("")
            else:
                self.enabled.setChecked(True)
                self.editor.setEnabled(True)
                self._set_text(value)
        finally:
            self._updating = False

    def get_value(self) -> Optional[str]:
        if not self.enabled.isChecked():
            return None
        text = self._current_text()
        return text if text else None


class TargetSelectorEditor(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False
        self._rows: dict[str, FilterRow] = {}
        self._text_timer: Optional[QTimer] = None
        self._build_ui()
        self._refresh_preview()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        top = QHBoxLayout()
        self.btn_form = QToolButton()
        self.btn_form.setText("图形模式")
        self.btn_form.setCheckable(True)
        self.btn_form.setChecked(True)
        self.btn_form.clicked.connect(lambda: self._set_mode("form"))

        self.btn_text = QToolButton()
        self.btn_text.setText("文本模式")
        self.btn_text.setCheckable(True)
        self.btn_text.clicked.connect(lambda: self._set_mode("text"))

        top.addWidget(self.btn_form)
        top.addWidget(self.btn_text)
        top.addStretch(1)

        self.preview = QLabel("")
        self.preview.setFont(QFont("Consolas", 10))
        self.preview.setStyleSheet(muted_style(self.preview))
        self.preview.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(self.preview)
        layout.addLayout(top)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_form_page())
        self.stack.addWidget(self._build_text_page())
        layout.addWidget(self.stack, 1)

    def _build_form_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        base_row = QHBoxLayout()
        label = QLabel("基础选择器")
        label.setFixedWidth(96)
        base_row.addWidget(label)
        self.base_combo = QComboBox()
        for base, text in BASES:
            self.base_combo.addItem(text, base)
        self.base_combo.setCurrentIndex(2)
        self.base_combo.currentIndexChanged.connect(self._on_form_changed)
        base_row.addWidget(self.base_combo, 1)
        v.addLayout(base_row)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        v.addWidget(line)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(2, 2, 2, 2)
        inner_layout.setSpacing(0)

        for defn in FILTER_DEFS:
            row = FilterRow(defn)
            row.changed.connect(self._on_form_changed)
            self._rows[defn["key"]] = row
            inner_layout.addWidget(row)
        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        v.addWidget(scroll, 1)
        return page

    def _build_text_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.text = QPlainTextEdit()
        self.text.setFont(QFont("Consolas", 10))
        self.text.setPlaceholderText("@e[type=minecraft:zombie,distance=..10]")
        self.text.textChanged.connect(self._on_text_changed)
        v.addWidget(self.text, 1)
        self.error_label = QLabel("")
        self.error_label.setStyleSheet(
            f"color: {theme_colors(self)['error']}; padding: 2px;")
        v.addWidget(self.error_label, 0)
        return page

    def _set_mode(self, mode: str) -> None:
        if mode == "form":
            if self.stack.currentIndex() == 1:
                self._apply_text_now()
            self.btn_form.setChecked(True)
            self.btn_text.setChecked(False)
            self.stack.setCurrentIndex(0)
        else:
            self.btn_form.setChecked(False)
            self.btn_text.setChecked(True)
            self.stack.setCurrentIndex(1)
            self._refresh_text()

    def _on_form_changed(self) -> None:
        if self._updating:
            return
        selector = self._collect_from_form()
        self._refresh_preview(selector)
        if self.stack.currentIndex() != 1:
            self._updating = True
            try:
                self.text.setPlainText(selector.to_string())
                self.error_label.setText("")
            finally:
                self._updating = False
        self.changed.emit()

    def _collect_from_form(self) -> TargetSelector:
        base = self.base_combo.currentData() or "@p"
        filters: dict[str, str] = {}
        for key, row in self._rows.items():
            v = row.get_value()
            if v is not None:
                filters[key] = v
        return TargetSelector(base=base, filters=filters)

    def _on_text_changed(self) -> None:
        if self._updating:
            return
        if self._text_timer is None:
            self._text_timer = QTimer(self)
            self._text_timer.setSingleShot(True)
            self._text_timer.setInterval(400)
            self._text_timer.timeout.connect(self._apply_text_now)
        self._text_timer.start()

    def _apply_text_now(self) -> None:
        if self._text_timer is not None:
            self._text_timer.stop()
        text = self.text.toPlainText().strip()
        if not text:
            self.error_label.setText("")
            return
        try:
            selector = TargetSelector.parse(text)
        except ValueError as e:
            self.error_label.setText(str(e))
            return
        self.error_label.setText("")
        self._updating = True
        try:
            self._populate_form(selector)
            self._refresh_preview(selector)
        finally:
            self._updating = False
        self.changed.emit()

    def _populate_form(self, selector: TargetSelector) -> None:
        idx = self.base_combo.findData(selector.base)
        if idx >= 0:
            self.base_combo.setCurrentIndex(idx)
        else:
            self.base_combo.addItem(selector.base, selector.base)
            self.base_combo.setCurrentIndex(self.base_combo.count() - 1)
        for key, row in self._rows.items():
            row.set_value(selector.filters.get(key))

    def _refresh_text(self) -> None:
        self._updating = True
        try:
            self.text.setPlainText(self._collect_from_form().to_string())
            self.error_label.setText("")
        finally:
            self._updating = False

    def _refresh_preview(self,
                          selector: Optional[TargetSelector] = None) -> None:
        if selector is None:
            selector = self._collect_from_form()
        self.preview.setText(selector.to_string())

    def set_selector(self, selector: TargetSelector) -> None:
        self._updating = True
        try:
            self._populate_form(selector)
            self.text.setPlainText(selector.to_string())
            self._refresh_preview(selector)
        finally:
            self._updating = False

    def selector(self) -> TargetSelector:
        if self.stack.currentIndex() == 1:
            text = self.text.toPlainText().strip()
            if text:
                try:
                    return TargetSelector.parse(text)
                except ValueError:
                    pass
        return self._collect_from_form()

    def to_string(self) -> str:
        return self.selector().to_string()


class TargetSelectorDialog(QDialog):
    def __init__(self, initial: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑目标选择器")
        self.resize(780, 560)

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)

        self.editor = TargetSelectorEditor()
        try:
            self.editor.set_selector(
                TargetSelector.parse(initial) if initial.strip()
                else TargetSelector())
        except ValueError:
            pass
        v.addWidget(self.editor, 1)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def value(self) -> str:
        return self.editor.to_string()


# =========================================================================
# 物品组件编辑
# =========================================================================

class ComponentEditDialog(QDialog):
    def __init__(self, definition: dict, initial_value: str = "",
                 parent=None):
        super().__init__(parent)
        self.defn = definition
        self.setWindowTitle(
            f"编辑组件 — {definition.get('display', definition.get('id', ''))}")
        self.resize(720, 520)

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        kind = definition.get("kind", "text")
        hint = definition.get("hint", "")
        if hint:
            hl = QLabel(hint)
            hl.setStyleSheet(muted_style(hl))
            v.addWidget(hl)

        self._kind = kind
        self._nbt_editor: Optional[NbtTreeEditor] = None

        if kind == "bool":
            self._bool = QCheckBox("启用")
            self._bool.setChecked(
                initial_value.strip().lower() in ("1b", "true", "1"))
            v.addWidget(self._bool)
        elif kind == "enum":
            self._combo = QComboBox()
            for opt in definition.get("options", []):
                self._combo.addItem(opt)
            idx = self._combo.findText(initial_value.strip())
            if idx >= 0:
                self._combo.setCurrentIndex(idx)
            elif initial_value.strip():
                self._combo.addItem(initial_value.strip())
                self._combo.setCurrentIndex(self._combo.count() - 1)
            v.addWidget(self._combo)
        elif kind == "int":
            self._line = QLineEdit(
                initial_value.strip() or definition.get("default", "0"))
            v.addWidget(self._line)
        elif kind == "nbt":
            self._nbt_editor = NbtTreeEditor()
            init = initial_value.strip() or definition.get("default", "{}")
            if not self._nbt_editor.set_snbt(init):
                self._nbt_editor.set_snbt("{}")
            v.addWidget(self._nbt_editor, 1)
        else:
            self._line = QLineEdit(
                initial_value.strip() or definition.get("default", ""))
            v.addWidget(self._line)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _on_accept(self) -> None:
        if self._kind == "int":
            try:
                int(self._line.text().strip())
            except ValueError:
                QMessageBox.warning(self, "输入有误", "请输入整数。")
                return
        self.accept()

    def value(self) -> str:
        if self._kind == "bool":
            return "1b" if self._bool.isChecked() else "0b"
        if self._kind == "enum":
            return self._combo.currentText().strip()
        if self._kind == "nbt" and self._nbt_editor is not None:
            return self._nbt_editor.to_snbt(pretty=False)
        return self._line.text().strip()


# =========================================================================
# 推荐侧栏
# =========================================================================

class RecommendSidebar(QWidget):
    applyRequested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._kind = ""
        self._context_id = ""
        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(4)

        top = QHBoxLayout()
        self.title = QLabel("推荐")
        self.title.setStyleSheet("font-weight: bold;")
        top.addWidget(self.title, 1)

        btn_refresh = QToolButton()
        btn_refresh.setText("↻")
        btn_refresh.setToolTip("重新加载推荐数据")
        btn_refresh.clicked.connect(self._on_reload)
        top.addWidget(btn_refresh)
        v.addLayout(top)

        self.subtitle = QLabel("")
        self.subtitle.setStyleSheet(hint_style(self.subtitle))
        self.subtitle.setWordWrap(True)
        v.addWidget(self.subtitle)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.itemDoubleClicked.connect(self._on_apply_item)
        v.addWidget(self.list, 1)

        self.btn_apply = QPushButton("应用选中")
        self.btn_apply.clicked.connect(self._on_apply_current)
        v.addWidget(self.btn_apply)

        self.hint = QLabel("双击条目也可直接应用。")
        self.hint.setStyleSheet(hint_style(self.hint))
        v.addWidget(self.hint)

    def set_context(self, kind: str, context_id: str) -> None:
        self._kind = (kind or "").strip()
        self._context_id = (context_id or "").strip()
        self._refresh()

    def set_title(self, text: str) -> None:
        self.title.setText(text or "推荐")

    def _refresh(self) -> None:
        self.list.clear()
        if not self._kind or not self._context_id:
            self.subtitle.setText("请先在左侧选择实体 / 物品 / 命令。")
            return
        rec = recommender()
        if self._kind == "entity":
            items = rec.recommend_for_entity(self._context_id)
            self.subtitle.setText(f"实体：{self._context_id}")
        elif self._kind == "item":
            items = rec.recommend_for_item(self._context_id)
            self.subtitle.setText(f"物品：{self._context_id}")
        elif self._kind == "command":
            items = rec.recommend_for_command(self._context_id)
            self.subtitle.setText(f"命令：/{self._context_id}")
        else:
            items = []
            self.subtitle.setText("")

        if not items:
            item = QListWidgetItem("（暂无内置推荐）")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
            return

        # ✅ P1-5：按 field_key 分支，避免 and/or 优先级陷阱
        for entry in items:
            field_key = ""
            if entry.get("nbt"):
                field_key = "nbt"
            elif entry.get("component"):
                field_key = "component"
            elif entry.get("snippet"):
                field_key = "snippet"

            field = entry.get(field_key, "")
            default = entry.get("default", "")
            desc = entry.get("desc", "")

            text = str(field)
            if default and field_key in ("nbt", "component"):
                text += f"  =  {default}"
            if desc:
                text += f"   — {desc}"

            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(desc or str(field))
            self.list.addItem(item)

    def _on_reload(self) -> None:
        recommender().reload()
        self._refresh()

    def _on_apply_current(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self._on_apply_item(item)

    def _on_apply_item(self, item: QListWidgetItem) -> None:
        entry = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, dict):
            self.applyRequested.emit(dict(entry))


# =========================================================================
# 模板浏览器
# =========================================================================

class TemplateBrowser(QDialog):
    def __init__(self, parent=None, title: str = "模板库"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 560)

        self._templates = self._load_templates()
        self._selected_snbt = ""

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QLabel("搜索"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("按名称 / 分类 / ID 搜索…")
        self.search.textChanged.connect(self._apply_filter)
        top.addWidget(self.search, 1)
        v.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["名称", "ID"])
        self.tree.setColumnWidth(0, 280)
        self.tree.setAlternatingRowColors(True)
        self.tree.currentItemChanged.connect(self._on_selected)
        self.tree.itemDoubleClicked.connect(lambda *_: self.accept())
        splitter.addWidget(self.tree)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)
        rv.addWidget(QLabel("SNBT 预览"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        rv.addWidget(self.preview, 1)
        splitter.addWidget(right)
        splitter.setSizes([420, 380])
        v.addWidget(splitter, 1)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("插入")
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self._reload_tree()

    @staticmethod
    def _load_templates() -> list[dict]:
        data = data_store().load_json("templates.json") or {}
        items = data.get("templates") if isinstance(data, dict) else None
        return [it for it in (items or []) if isinstance(it, dict)]

    def _reload_tree(self) -> None:
        self.tree.clear()
        by_cat: dict[str, list[dict]] = {}
        for t in self._templates:
            by_cat.setdefault(t.get("category", "其他"), []).append(t)
        for cat in sorted(by_cat.keys()):
            cat_item = QTreeWidgetItem([cat, ""])
            cat_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(cat_item)
            for t in by_cat[cat]:
                child = QTreeWidgetItem([str(t.get("name", "")),
                                          str(t.get("id", ""))])
                child.setData(0, Qt.ItemDataRole.UserRole, t.get("snbt", ""))
                cat_item.addChild(child)
            cat_item.setExpanded(True)

    def _apply_filter(self) -> None:
        keyword = self.search.text().strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            cat_item = self.tree.topLevelItem(i)
            visible = 0
            for j in range(cat_item.childCount()):
                child = cat_item.child(j)
                hay = (child.text(0) + " " + child.text(1)).lower()
                match = not keyword or keyword in hay
                child.setHidden(not match)
                if match:
                    visible += 1
            cat_item.setHidden(visible == 0)

    def _on_selected(self, current: QTreeWidgetItem, _prev) -> None:
        if current is None:
            self.preview.setPlainText("")
            self._selected_snbt = ""
            return
        snbt = current.data(0, Qt.ItemDataRole.UserRole) or ""
        self._selected_snbt = str(snbt)
        self.preview.setPlainText(self._selected_snbt)

    def _on_accept(self) -> None:
        if not self._selected_snbt.strip():
            QMessageBox.information(self, "提示", "请先选择一个模板。")
            return
        self.accept()

    def selected_snbt(self) -> str:
        return self._selected_snbt


# =========================================================================
# 物品组件：值编辑 / 选择器 / 物品选择
# =========================================================================

class ValueEditDialog(QDialog):
    """组件值的多行编辑器（带 SNBT 校验与错误提示）。

    组件值常常是长 SNBT，单行 QLineEdit 很难维护，这里用等宽多行输入。
    """

    def __init__(self, title: str, initial: str = "", hint: str = "",
                 validator=None, parent=None, allow_empty: bool = True):
        super().__init__(parent)
        self.setWindowTitle(title or "编辑值")
        self.resize(660, 460)
        self._validator = validator
        self._allow_empty = allow_empty
        self._value = initial

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        if hint:
            label = QLabel(hint)
            label.setWordWrap(True)
            label.setStyleSheet(muted_style(label))
            v.addWidget(label)

        self.editor = QPlainTextEdit()
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.editor.setFont(font)
        self.editor.setPlainText(initial)
        self.editor.textChanged.connect(self._on_changed)
        v.addWidget(self.editor, 1)

        row = QHBoxLayout()
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setStyleSheet(muted_style(self.message))
        row.addWidget(self.message, 1)
        btn_check = QPushButton("校验")
        btn_check.clicked.connect(lambda: self._on_changed())
        row.addWidget(btn_check)
        v.addLayout(row)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self._on_changed()

    def _issues(self) -> list[tuple[str, str]]:
        if self._validator is None:
            return []
        try:
            return list(self._validator(self.editor.toPlainText()))
        except Exception as exc:  # 校验器自身出错不应阻塞编辑
            return [("error", f"校验失败：{exc}")]

    def _on_changed(self) -> None:
        issues = self._issues()
        if not issues:
            self.message.setStyleSheet("color: #3FB950;")
            self.message.setText("看起来没问题。" if self.editor.toPlainText().strip()
                                 else "（值为空：生成时只写组件 ID）")
        else:
            errors = [m for level, m in issues if level == "error"]
            palette = theme_colors(self)
            self.message.setStyleSheet(
                f"color: {palette['error']};" if errors
                else f"color: {palette['warn']};")
            self.message.setText("；".join(m for _l, m in issues))

    def _on_accept(self) -> None:
        text = self.editor.toPlainText().strip()
        if not text and not self._allow_empty:
            QMessageBox.warning(self, "输入有误", "值不能为空。")
            return
        if any(level == "error" for level, _m in self._issues()):
            QMessageBox.warning(self, "输入有误",
                                "值里还有错误，请先修正（可点「校验」查看）。")
            return
        self._value = text
        self.accept()

    def value(self) -> str:
        return self._value


class EnchantmentsEditDialog(QDialog):
    """附魔组件编辑器：{levels:{附魔:等级}} 的图形化编辑。

    直接编辑 SNBT 很容易写错键名或等级，这里给出「附魔（中文名） + 等级」
    的列表编辑，并实时显示生成的组件值。
    """

    def __init__(self, initial: str = "", parent=None,
                 title: str = "编辑附魔"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(660, 440)
        self._rows: list[tuple[str, int]] = []
        self._mount = _parse_levels(initial)

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        pick = QHBoxLayout()
        pick.addWidget(QLabel("附魔"))
        from app.core.item_components import enchantment_names
        self._names = enchantment_names()
        self.combo = QComboBox()
        self.combo.setEditable(True)
        for eid, display in sorted(self._names.items(),
                                   key=lambda kv: kv[1]):
            self.combo.addItem(f"{display}（{eid}）", eid)
        self.combo.setMinimumWidth(260)
        pick.addWidget(self.combo, 1)
        pick.addWidget(QLabel("等级"))
        self.level = QSpinBox()
        self.level.setRange(1, 255)
        self.level.setValue(1)
        pick.addWidget(self.level)
        btn_add = QPushButton("添加 / 更新")
        btn_add.clicked.connect(self._on_add)
        pick.addWidget(btn_add)
        v.addLayout(pick)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["附魔", "等级", "操作"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        v.addWidget(self.table, 1)

        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet(muted_style(self.preview))
        v.addWidget(self.preview)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        for eid, lvl in self._mount:
            self._append(eid, lvl)
        self._refresh()

    def _append(self, eid: str, level: int) -> None:
        self._rows.append((eid, int(level)))
        row = self.table.rowCount()
        self.table.insertRow(row)
        display = self._names.get(eid, "")
        item = QTableWidgetItem(f"{display}（{eid}）" if display else eid)
        item.setData(Qt.ItemDataRole.UserRole, eid)
        self.table.setItem(row, 0, item)
        lvl = QTableWidgetItem(str(level))
        lvl.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, 1, lvl)
        holder = QWidget()
        h = QHBoxLayout(holder)
        h.setContentsMargins(2, 2, 2, 2)
        h.setSpacing(2)
        btn_up = QPushButton("＋")
        btn_up.setFixedWidth(28)
        btn_up.setToolTip("提高等级")
        btn_up.clicked.connect(lambda _=False, i=row: self._bump(i, 1))
        h.addWidget(btn_up)
        btn_down = QPushButton("－")
        btn_down.setFixedWidth(28)
        btn_down.setToolTip("降低等级")
        btn_down.clicked.connect(lambda _=False, i=row: self._bump(i, -1))
        h.addWidget(btn_down)
        btn_del = QPushButton("删除")
        btn_del.setFixedWidth(52)
        btn_del.clicked.connect(lambda _=False, i=row: self._delete(i))
        h.addWidget(btn_del)
        self.table.setCellWidget(row, 2, holder)

    def _bump(self, row: int, delta: int) -> None:
        if not (0 <= row < len(self._rows)):
            return
        eid, level = self._rows[row]
        self._rows[row] = (eid, max(1, level + delta))
        self.table.item(row, 1).setText(str(self._rows[row][1]))
        self._refresh()

    def _delete(self, row: int) -> None:
        if not (0 <= row < len(self._rows)):
            return
        del self._rows[row]
        self.table.removeRow(row)
        self._rebuild_callbacks()
        self._refresh()

    def _rebuild_callbacks(self) -> None:
        for row in range(self.table.rowCount()):
            holder = self.table.cellWidget(row, 2)
            if holder is None:
                continue
            layout = holder.layout()
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            for label, delta, tip in (("＋", 1, "提高等级"), ("－", -1, "降低等级")):
                btn = QPushButton(label)
                btn.setFixedWidth(28)
                btn.setToolTip(tip)
                btn.clicked.connect(
                    lambda _=False, i=row, d=delta: self._bump(i, d))
                layout.addWidget(btn)
            btn_del = QPushButton("删除")
            btn_del.setFixedWidth(52)
            btn_del.clicked.connect(lambda _=False, i=row: self._delete(i))
            layout.addWidget(btn_del)

    def _on_add(self) -> None:
        eid = self.combo.currentData()
        text = self.combo.currentText().strip()
        if not eid and text:
            # 手动输入的附魔 ID：补 minecraft: 前缀
            eid = text if ":" in text else f"minecraft:{text}"
        if not eid:
            return
        for i, (existing, _lvl) in enumerate(self._rows):
            if existing == eid:
                self._rows[i] = (eid, int(self.level.value()))
                self.table.item(i, 1).setText(str(self.level.value()))
                self._refresh()
                return
        self._append(str(eid), int(self.level.value()))
        self._refresh()

    def _refresh(self) -> None:
        if not self._rows:
            self.preview.setText("（没有附魔：确定后会生成空组件）")
            return
        self.preview.setText("生成：{" + ", ".join(
            f"{eid}:{level}" for eid, level in self._rows) + "}")

    def value(self) -> str:
        body = ",".join(f'"{eid}":{level}' for eid, level in self._rows)
        return '{"levels":{' + body + "}}"


def _parse_levels(text: str) -> list[tuple[str, int]]:
    """解析 `{附魔:等级}` / `{levels:{附魔:等级}}` 两种写法。"""
    body = (text or "").strip()
    if not body.startswith("{"):
        return []
    try:
        node = SnbtParser(body).parse()
    except Exception:
        return []
    container = node.get_child("levels") or node
    out: list[tuple[str, int]] = []
    for child in container.children:
        if child.key is None:
            continue
        try:
            level = int(float(child.value))
        except (TypeError, ValueError):
            level = 1
        out.append((str(child.key), max(1, level)))
    return out


class ComponentPickerDialog(QDialog):
    """物品组件选择器：搜索 + 分组，支持一次加入多个组件。"""

    def __init__(self, parent=None, item: str = ""):
        super().__init__(parent)
        self.setWindowTitle("添加物品组件")
        self.resize(720, 560)
        self._selected: list[str] = []

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QLabel("搜索"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("按中文名 / 组件 ID 搜索，例如「附魔」")
        self.search.textChanged.connect(self._apply_filter)
        top.addWidget(self.search, 1)
        v.addLayout(top)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["组件", "ID", "说明"])
        self.tree.setColumnWidth(0, 160)
        self.tree.setColumnWidth(1, 260)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        v.addWidget(self.tree, 1)

        self.preview = QLabel("双击或选中后点「添加」即可加入组件。")
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet(muted_style(self.preview))
        v.addWidget(self.preview)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("添加")
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self._populate(item=item)

    def _populate(self, item: str = "") -> None:
        from app.core.item_components import grouped_components
        from app.core.data import store as _store
        tags: set[str] = set()
        for entry in _store().items():
            if str(entry.get("id", "")) == item:
                tags = {str(t) for t in entry.get("tags", []) if t}
                break
        self._item_tags = tags
        self.tree.clear()
        for label, entries in grouped_components():
            node = QTreeWidgetItem([label, "", ""])
            node.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(node)
            for entry in entries:
                cid = str(entry.get("id", ""))
                applies = entry.get("applies") or []
                mark = "适用" if (not applies or not tags
                                  or set(applies) & tags) else "不适用"
                child = QTreeWidgetItem([
                    str(entry.get("display", cid)),
                    cid,
                    f"{entry.get('hint', '')}  [{mark}]".strip()])
                child.setData(0, Qt.ItemDataRole.UserRole, cid)
                child.setToolTip(0, f"{entry.get('hint', '')}")
                node.addChild(child)
            node.setExpanded(True)

    def _apply_filter(self) -> None:
        keyword = self.search.text().strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            node = self.tree.topLevelItem(i)
            visible = 0
            for j in range(node.childCount()):
                child = node.child(j)
                hay = (child.text(0) + " " + child.text(1)
                       + " " + child.text(2)).lower()
                match = not keyword or keyword in hay
                child.setHidden(not match)
                if match:
                    visible += 1
            node.setHidden(visible == 0)

    def _on_double_clicked(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        cid = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if cid:
            self._selected = [str(cid)]
            self.accept()
            return
        self._on_accept()

    def _on_selection_changed(self) -> None:
        from app.core.item_components import component_defs
        table = component_defs()
        rows: list[str] = []
        for node in self.tree.selectedItems():
            cid = node.data(0, Qt.ItemDataRole.UserRole)
            if not cid:
                continue
            defn = table.get(str(cid), {})
            piece = f"{defn.get('display', cid)}：默认 {defn.get('default', '（空）')}"
            if defn.get("hint"):
                piece += f"（{defn['hint']}）"
            rows.append(piece)
        self.preview.setText("\n".join(rows) if rows
                             else "双击或选中后点「添加」即可加入组件。")

    def _on_accept(self) -> None:
        ids: list[str] = []
        for node in self.tree.selectedItems():
            cid = node.data(0, Qt.ItemDataRole.UserRole)
            if cid:
                ids.append(str(cid))
        if not ids:
            QMessageBox.information(self, "提示", "请先选择一个或多个组件。")
            return
        self._selected = ids
        self.accept()

    def selected_ids(self) -> list[str]:
        return list(self._selected)


class ItemPickerDialog(QDialog):
    """物品选择对话框：常用物品 + 分类 + 搜索，也允许直接手写 ID。"""

    def __init__(self, entries, initial: str = "", recent=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择物品")
        self.resize(760, 560)
        self._initial = initial or ""

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        if recent:
            row = QHBoxLayout()
            row.addWidget(QLabel("最近"))
            for value in list(recent)[:6]:
                btn = QPushButton(_short_label(value))
                btn.setToolTip(value)
                btn.clicked.connect(
                    lambda _=False, val=value: self._choose(val))
                row.addWidget(btn)
            row.addStretch(1)
            v.addLayout(row)

        self.picker = IdPicker(entries, "物品 ID")
        self.picker.set_id(self._initial)
        self.picker.list.itemDoubleClicked.connect(
            lambda *_: self._choose(self.picker.get_id()))
        v.addWidget(self.picker, 1)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("使用该物品")
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _choose(self, value: str) -> None:
        if value:
            self.picker.set_id(value)
            self.accept()

    def _on_accept(self) -> None:
        if not self.picker.get_id().strip():
            QMessageBox.information(self, "提示", "请先选择或填写物品 ID。")
            return
        self.accept()

    def value(self) -> str:
        return self.picker.get_id().strip()


def _short_label(value: str) -> str:
    text = str(value or "")
    if text.startswith("minecraft:"):
        text = text.split(":", 1)[1]
    return text if len(text) <= 14 else text[:12] + "…"


class ItemSelect(QWidget):
    """单行物品选择器：下拉（带搜索/最近）+ 浏览按钮。"""

    idChanged = Signal(str)

    def __init__(self, entries=None, parent=None):
        super().__init__(parent)
        self._entries = list(entries or [])
        self._recent: list[str] = []
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)

        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.combo.setMinimumWidth(240)
        self.combo.lineEdit().setPlaceholderText(
            "minecraft:…（可直接输入，会自动补 minecraft: 前缀）")
        self.combo.lineEdit().textChanged.connect(self._on_text)
        self.combo.activated.connect(self._on_picked)
        h.addWidget(self.combo, 1)

        self.btn_browse = QPushButton("浏览…")
        self.btn_browse.setFixedWidth(72)
        self.btn_browse.clicked.connect(self.browse)
        h.addWidget(self.btn_browse)

        self.reload(entries)

    # ---- 数据 ----

    def reload(self, entries=None) -> None:
        if entries is not None:
            self._entries = list(entries)
        current = self.get_id()
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("", "")
        for entry in self._entries:
            iid = str(entry.get("id", ""))
            if not iid:
                continue
            # 文本＝ID，中文名放 tooltip，避免「显示名 ↔ ID」互相污染
            self.combo.addItem(iid, iid)
            index = self.combo.count() - 1
            display = str(entry.get("display", ""))
            if display:
                self.combo.setItemData(index, f"{iid} — {display}",
                                       Qt.ItemDataRole.ToolTipRole)
                self.combo.setItemData(index, f"{display}  ({iid})",
                                       Qt.ItemDataRole.AccessibleTextRole)
        self.combo.blockSignals(False)
        self.set_id(current)

    def set_recent(self, values) -> None:
        """把最近使用过的物品名做成补全候选。"""
        model = QStringListModel([str(v) for v in (values or [])], self)
        completer = QCompleter(model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.combo.setCompleter(completer)
        self._recent = [str(v) for v in (values or [])]

    # ---- 取值 ----

    def get_id(self) -> str:
        return self.combo.lineEdit().text().strip()

    def set_id(self, value: str) -> None:
        """静默设置（不触发 idChanged），便于外部回填面板。"""
        text = str(value or "").strip()
        idx = self.combo.findData(text)
        line = self.combo.lineEdit()
        line.blockSignals(True)
        self.combo.blockSignals(True)
        if idx >= 0:
            self.combo.setCurrentIndex(idx)
            line.setText(text)
        else:
            self.combo.setCurrentIndex(0)
            line.setText(text)
        self.combo.blockSignals(False)
        line.blockSignals(False)

    def _on_text(self, text: str) -> None:
        self.idChanged.emit(text.strip())

    def _on_picked(self, index: int) -> None:
        data = self.combo.itemData(index)
        if not data:
            return
        # 手输时 Qt 会保留旧 currentIndex，这里显式把输入框同步成选中的 ID
        line = self.combo.lineEdit()
        line.blockSignals(True)
        line.setText(str(data))
        line.blockSignals(False)
        self.idChanged.emit(str(data))

    def browse(self) -> None:
        dlg = ItemPickerDialog(self._entries, self.get_id(), parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.set_id(dlg.value())
            self.idChanged.emit(self.get_id())


# ---------------------------------------------------------------------------
# 子模块再导出（原本平级的 widgets.py 已并入本包）
# ---------------------------------------------------------------------------

from app.ui.widgets.nbt_tags import (            # noqa: E402
    ValueParseError as NbtValueError,
)
from app.ui.widgets.nbt_dialogs import (         # noqa: E402
    ArrayEditDialog, FindReplaceBar, ValueEditDialog as NbtValueEditDialog,
)
from app.ui.widgets.nbt_tree import NbtTreeEditor  # noqa: E402

from app.ui.widgets.command_search import CommandSearch    # noqa: E402
from app.ui.widgets.nlp_input import NlpInput              # noqa: E402
from app.ui.widgets.output_edit import CommandOutputEdit   # noqa: E402

__all__ = [
    "CoordInput", "IdPicker", "NbtTreeEditor", "NbtEditDialog",
    "NbtFileEditor", "TargetSelectorEditor", "TargetSelectorDialog",
    "ComponentEditDialog", "RecommendSidebar", "TemplateBrowser",
    "ValueEditDialog", "ComponentPickerDialog", "ItemPickerDialog",
    "ItemSelect", "EnchantmentsEditDialog",
    "NbtValueEditDialog", "ArrayEditDialog", "FindReplaceBar",
    "NbtValueError",
    "CommandSearch", "NlpInput", "CommandOutputEdit",
]