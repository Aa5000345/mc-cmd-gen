"""设置对话框：专有名词翻译 / NBT 显示选项 / 数据目录。

「专有名词翻译」页签列出所有内置术语（NBT 类型、注册表分类、其它术语），
每一行都可以改成自己喜欢的中文；改动立即生效并保存。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from app.core import settings as app_settings
from app.storage import app_data_dir, user_data_dir
from app.ui.theme import muted_style

SCOPE_CHOICES = (
    ("all", "全部翻译（NBT 类型 + 分类 + 术语）"),
    ("types", "只翻译 NBT 类型（树形视图的类型列）"),
    ("none", "全部显示英文原名"),
)


class TermTable(QWidget):
    """一张「原名 → 译文」的编辑表。"""

    def __init__(self, group: str, parent=None):
        super().__init__(parent)
        self.group = group
        self._loading = False
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("筛选"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("按原名或译文筛选…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        row.addWidget(self.filter_edit, 1)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(muted_style(self.count_label))
        row.addWidget(self.count_label)
        btn_reset = QPushButton("恢复本页默认")
        btn_reset.setToolTip("把这一页的译文恢复成内置默认值")
        btn_reset.clicked.connect(self.reset_group)
        row.addWidget(btn_reset)
        v.addLayout(row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["原名", "译文（可编辑）", "说明"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 190)
        self.table.setColumnWidth(1, 190)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.itemChanged.connect(self._on_item_changed)
        v.addWidget(self.table, 1)

        hint = QLabel("双击「译文」列即可修改；留空表示用内置默认值。"
                      "改动即时写盘生效，不需要另存。")
        hint.setStyleSheet(muted_style(hint))
        v.addWidget(hint)
        self.saved_label = QLabel("")
        self.saved_label.setStyleSheet("color: #3FB950; font-size: 12px;")
        v.addWidget(self.saved_label)
        self._saved_timer = QTimer(self)
        self._saved_timer.setSingleShot(True)
        self._saved_timer.timeout.connect(lambda: self.saved_label.setText(""))
        self.reload()

    # ---- 数据 ----

    def reload(self) -> None:
        self._loading = True
        try:
            terms = app_settings.effective_terms(self.group)
            custom = (app_settings.load().get("terms", {}).get(self.group) or {})
            self.table.setRowCount(0)
            for key in sorted(terms, key=lambda k: (k.lower(), k)):
                row = self.table.rowCount()
                self.table.insertRow(row)
                item = QTableWidgetItem(key)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, 0, item)
                value = QTableWidgetItem(str(terms[key]))
                self.table.setItem(row, 1, value)
                explain = QTableWidgetItem(
                    "已自定义" if key in custom else "内置默认")
                explain.setFlags(explain.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, 2, explain)
            self._refresh_count()
        finally:
            self._loading = False

    def _refresh_count(self) -> None:
        total = self.table.rowCount()
        shown = sum(1 for r in range(total) if not self.table.isRowHidden(r))
        self.count_label.setText(f"{shown} / {total} 条")

    def _apply_filter(self, _text: str = "") -> None:
        needle = self.filter_edit.text().strip().lower()
        for row in range(self.table.rowCount()):
            if not needle:
                self.table.setRowHidden(row, False)
                continue
            texts = [self.table.item(row, col).text().lower()
                     for col in (0, 1) if self.table.item(row, col)]
            self.table.setRowHidden(row, not any(needle in t for t in texts))
        self._refresh_count()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 1:
            return
        key_item = self.table.item(item.row(), 0)
        if key_item is None:
            return
        key = key_item.text()
        text = item.text().strip()
        defaults = {"nbt_types": app_settings.DEFAULT_NBT_TYPES,
                    "categories": app_settings.DEFAULT_CATEGORIES,
                    "words": app_settings.DEFAULT_WORDS}.get(self.group, {})
        data = app_settings.load()
        custom = data.setdefault("terms", {}).setdefault(self.group, {})
        if not text or text == str(defaults.get(key, "")):
            custom.pop(key, None)
        else:
            custom[key] = text
        app_settings.save(data)
        self._loading = True
        try:
            self.table.item(item.row(), 2).setText(
                "已自定义" if key in custom else "内置默认")
        finally:
            self._loading = False
        # 这里是「改完即写盘」：必须告诉用户已经保存了，
        # 否则他会一直找「保存」按钮，或者以为改了没生效
        self._flash_saved(f"已保存：{key}")
        self._apply_filter()

    def _flash_saved(self, text: str) -> None:
        label = getattr(self, "saved_label", None)
        if label is None:
            return
        label.setText(f"✔ {text}（立即生效，无需另存）")
        self._saved_timer.start(2600)

    def reset_group(self) -> None:
        data = app_settings.load()
        data.setdefault("terms", {})[self.group] = {}
        app_settings.save(data)
        self.reload()
        self._apply_filter()


class SettingsDialog(QDialog):
    """应用设置。"""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(860, 620)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_terms_page(), "专有名词翻译")
        self.tabs.addTab(self._build_display_page(), "NBT 显示")
        self.tabs.addTab(self._build_about_page(), "数据目录")
        v.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).setText(
            "全部恢复默认")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults) \
            .clicked.connect(self._reset_all)
        v.addWidget(buttons)

    # ---- 专有名词翻译 ----

    def _build_terms_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        box = QGroupBox("翻译范围")
        form = QFormLayout(box)
        form.setContentsMargins(10, 6, 10, 8)
        self.scope_combo = QComboBox()
        for key, label in SCOPE_CHOICES:
            self.scope_combo.addItem(label, key)
        current = app_settings.scope()
        index = self.scope_combo.findData(current)
        if index >= 0:
            self.scope_combo.setCurrentIndex(index)
        self.scope_combo.setToolTip(
            "控制「类型」「分类」「术语」是否显示成中文；命令本身永远用英文 ID")
        self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        form.addRow("显示方式", self.scope_combo)

        self.enable_check = QCheckBox("启用专有名词翻译")
        self.enable_check.setChecked(app_settings.use_translations())
        self.enable_check.toggled.connect(self._on_enable_toggled)
        form.addRow("总开关", self.enable_check)
        v.addWidget(box)

        self.term_tabs = QTabWidget()
        self.term_tables: dict[str, TermTable] = {}
        for group, title, tip in app_settings.TERM_GROUPS:
            table = TermTable(group)
            table.setToolTip(tip)
            self.term_tables[group] = table
            self.term_tabs.addTab(table, title)
        v.addWidget(self.term_tabs, 1)
        return page

    def _on_scope_changed(self, _index: int) -> None:
        app_settings.set_value("terms.scope", self.scope_combo.currentData())
        self.changed.emit()

    def _on_enable_toggled(self, checked: bool) -> None:
        app_settings.set_value("terms.use_translations", bool(checked))
        self.changed.emit()

    # ---- NBT 显示 ----

    def _build_display_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        box = QGroupBox("树形视图")
        form = QFormLayout(box)
        form.setContentsMargins(10, 6, 10, 8)
        self.icons_check = QCheckBox("在名称前显示类型图标")
        self.icons_check.setChecked(app_settings.nbt_show_icons())
        self.icons_check.toggled.connect(
            lambda on: self._set_nbt_value("show_icons", on))
        form.addRow("类型图标", self.icons_check)

        self.summary_check = QCheckBox("识别物品并显示中文名（背包 / 末影箱等）")
        self.summary_check.setToolTip(
            "打开存档后，物品所在的标签会显示成「钻石剑 ×1 · 锋利 V」")
        self.summary_check.setChecked(app_settings.nbt_item_summary())
        self.summary_check.toggled.connect(
            lambda on: self._set_nbt_value("item_summary", on))
        form.addRow("物品摘要", self.summary_check)
        v.addWidget(box)

        tip = QLabel(
            "这些选项只影响显示，不会改变存档内容或生成的命令。\n"
            "「物品摘要」需要打开的是真实存档（level.dat / playerdata）。")
        tip.setWordWrap(True)
        tip.setStyleSheet(muted_style(tip))
        v.addWidget(tip)
        v.addStretch(1)
        return page

    def _set_nbt_value(self, key: str, value: bool) -> None:
        app_settings.set_value(f"nbt.{key}", bool(value))
        self.changed.emit()

    # ---- 数据目录 ----

    def _build_about_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        box = QGroupBox("位置")
        form = QFormLayout(box)
        form.setContentsMargins(10, 6, 10, 8)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        for label, path in (("应用数据目录", app_data_dir()),
                            ("用户数据目录", user_data_dir()),
                            ("设置文件", app_settings.settings_path())):
            line = QLineEdit(str(path))
            line.setReadOnly(True)
            line.setFont(font)
            form.addRow(label, line)
        v.addWidget(box)

        tip = QLabel(
            "把 `items.json` / `entities.json` / `blocks.json` 等放进用户数据目录\n"
            "即可覆盖或扩充内置注册表（格式与内置文件一致）。")
        tip.setWordWrap(True)
        tip.setStyleSheet(muted_style(tip))
        v.addWidget(tip)
        v.addStretch(1)
        return page

    # ---- 全局恢复 ----

    def _reset_all(self) -> None:
        answer = QMessageBox.question(
            self, "恢复默认设置",
            "把全部设置与专有名词翻译恢复成默认值？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        app_settings.reset()
        self.scope_combo.setCurrentIndex(
            max(0, self.scope_combo.findData(app_settings.scope())))
        self.enable_check.setChecked(app_settings.use_translations())
        self.icons_check.setChecked(app_settings.nbt_show_icons())
        self.summary_check.setChecked(app_settings.nbt_item_summary())
        for table in self.term_tables.values():
            table.reload()
        self.changed.emit()


__all__ = ["SettingsDialog", "TermTable", "SCOPE_CHOICES"]
