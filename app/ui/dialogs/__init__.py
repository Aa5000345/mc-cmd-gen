"""对话框集合：保存预设 / 填写占位符 / 命令迁移 / 导入用户数据。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QDialogButtonBox, QPlainTextEdit, QMessageBox,
    QListWidget, QListWidgetItem, QPushButton, QApplication,
)

from app.core.datapack import (
    extract_placeholders, render_placeholders,
    migrate_command, TARGET_VERSION,
)
from app.storage import user_data_dir


# =========================================================================
# 保存收藏
# =========================================================================

class SavePresetDialog(QDialog):
    def __init__(self, command: str, command_type: str = "",
                 categories: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("保存到收藏")
        self.resize(560, 380)

        v = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：一把满附魔下界合金剑")
        form.addRow("名称", self.name_edit)

        self.category_combo = QComboBox()
        self.category_combo.setEditable(True)
        base_cats = ["默认", "武器", "工具", "实用", "小游戏", "调试"]
        for c in (categories or []):
            if c not in base_cats:
                base_cats.append(c)
        for c in base_cats:
            self.category_combo.addItem(c)
        form.addRow("分类", self.category_combo)

        self.command_edit = QPlainTextEdit(command)
        self.command_edit.setMaximumHeight(140)
        form.addRow("命令", self.command_edit)

        v.addLayout(form)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self._command_type = command_type
        self.name_edit.setFocus()

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "提示", "请输入名称。")
            return
        if not self.command_edit.toPlainText().strip():
            QMessageBox.warning(self, "提示", "命令不能为空。")
            return
        self.accept()

    def value(self) -> tuple[str, str, str, str]:
        return (
            self.name_edit.text().strip(),
            self.category_combo.currentText().strip() or "默认",
            self.command_edit.toPlainText().strip(),
            self._command_type,
        )


# =========================================================================
# 占位符填写
# =========================================================================

class PlaceholderDialog(QDialog):
    def __init__(self, template: str, parent=None, title: str = "填写参数"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 480)

        self._template = template
        self._names = extract_placeholders(template)
        self._edits: dict[str, QLineEdit] = {}

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        tip = QLabel(
            "该预设包含占位符，请填写后将生成最终命令。\n"
            "未填写的占位符将保持原样。")
        tip.setStyleSheet("color: #8B949E;")
        v.addWidget(tip)

        form = QFormLayout()
        for name in self._names:
            le = QLineEdit()
            le.setPlaceholderText(f"例如 {self._default_for(name)}")
            le.textChanged.connect(self._refresh_preview)
            self._edits[name] = le
            form.addRow(f"{{{name}}}", le)
        v.addLayout(form)

        v.addWidget(QLabel("预览"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        v.addWidget(self.preview, 1)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self._refresh_preview()

    @staticmethod
    def _default_for(name: str) -> str:
        return {
            "target": "@p", "count": "1",
            "x": "~", "y": "~", "z": "~",
            "item": "minecraft:stone",
            "entity": "minecraft:zombie",
            "player": "@a", "n": "1",
        }.get(name, "")

    def _refresh_preview(self) -> None:
        values = {k: le.text().strip() for k, le in self._edits.items()}
        self.preview.setPlainText(render_placeholders(self._template, values))

    def values(self) -> dict[str, str]:
        return {k: le.text().strip() for k, le in self._edits.items()}

    def result_command(self) -> str:
        return render_placeholders(self._template, self.values())


# =========================================================================
# 命令迁移
# =========================================================================

class MigrateDialog(QDialog):
    def __init__(self, initial: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"命令迁移 → {TARGET_VERSION}")
        self.resize(900, 620)

        # ✅ P2-6 防抖：输入变化 300ms 后才刷新
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(300)
        self._refresh_timer.timeout.connect(self._refresh_now)

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        v.addWidget(QLabel("原始命令（1.20.4 或更早）"))

        self.input_edit = QPlainTextEdit()
        self.input_edit.setFont(QFont("Consolas", 10))
        self.input_edit.setPlaceholderText(
            '例如：/give @p diamond_sword'
            '{Enchantments:[{id:"minecraft:sharpness",lvl:5}]}')
        if initial.strip():
            self.input_edit.setPlainText(initial)
        self.input_edit.textChanged.connect(
            lambda: self._refresh_timer.start())
        v.addWidget(self.input_edit, 2)

        mid = QHBoxLayout()
        mid.addWidget(QLabel("迁移结果"))
        mid.addStretch(1)

        btn_paste = QPushButton("从剪贴板读取")
        btn_paste.clicked.connect(self._on_paste)
        mid.addWidget(btn_paste)

        btn_migrate = QPushButton("执行迁移")
        btn_migrate.clicked.connect(self._refresh_now)
        mid.addWidget(btn_migrate)
        v.addLayout(mid)

        self.output_edit = QPlainTextEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setFont(QFont("Consolas", 10))
        v.addWidget(self.output_edit, 2)

        v.addWidget(QLabel("应用的规则 / 警告"))
        self.report = QListWidget()
        self.report.setMaximumHeight(140)
        v.addWidget(self.report, 0)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("应用到输出区")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self._refresh_now()

    def _on_paste(self) -> None:
        try:
            text = QApplication.clipboard().text()
        except Exception:
            text = ""
        if text.strip():
            self.input_edit.setPlainText(text)

    def _refresh_now(self) -> None:
        text = self.input_edit.toPlainText()
        if not text.strip():
            self.output_edit.setPlainText("")
            self.report.clear()
            return
        result = migrate_command(text)
        self.output_edit.setPlainText(result.migrated)
        self.report.clear()
        for item in result.applied:
            self.report.addItem(QListWidgetItem("✔ " + item))
        for warn in result.warnings:
            self.report.addItem(QListWidgetItem("⚠ " + warn))
        if not result.applied and not result.warnings:
            self.report.addItem(QListWidgetItem("（无变更）"))

    def migrated_command(self) -> str:
        return self.output_edit.toPlainText().strip()


# =========================================================================
# 导入用户数据
# =========================================================================

DATA_KINDS = [
    ("entities", "实体（entities.json）"),
    ("items", "物品（items.json）"),
    ("item_components", "物品组件（item_components.json）"),
]


class DataImportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入用户数据")
        self.resize(640, 480)

        v = QVBoxLayout(self)
        v.addWidget(QLabel("选择数据类型，然后把对应的 JSON 内容粘贴到下方："))

        row = QHBoxLayout()
        row.addWidget(QLabel("类型"))
        self.kind_combo = QComboBox()
        for key, label in DATA_KINDS:
            self.kind_combo.addItem(label, key)
        row.addWidget(self.kind_combo, 1)

        btn_load = QPushButton("从文件载入…")
        btn_load.clicked.connect(self._on_load_file)
        row.addWidget(btn_load)
        v.addLayout(row)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            '{\n  "entities": [\n'
            '    {"id": "minecraft:custom", "display": "自定义", "category": "其他"}\n'
            '  ]\n}')
        v.addWidget(self.editor, 1)

        info = QLabel(
            f"导入后会保存到：{user_data_dir() / (self.kind_combo.currentData() + '.json')}\n"
            "整个文件会被替换（不是逐条合并）：以前在这个分类里导入过的自定义条目"
            "会一起消失，内置数据不受影响。\n"
            "写入前会自动把原文件备份成 *.bak。")
        info.setStyleSheet("color: #8B949E;")
        info.setWordWrap(True)
        v.addWidget(info)
        self.kind_combo.currentIndexChanged.connect(self._refresh_target_label)
        self._info = info

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("导入")
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _refresh_target_label(self) -> None:
        kind = self.kind_combo.currentData()
        self._info.setText(
            f"导入后会保存到：{user_data_dir() / (kind + '.json')}\n"
            "整个文件会被替换（不是逐条合并）：以前在这个分类里导入过的自定义条目"
            "会一起消失，内置数据不受影响。\n"
            "写入前会自动把原文件备份成 *.bak。")

    def _on_load_file(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 JSON 文件", "", "JSON 文件 (*.json);;所有文件 (*)")
        if not path:
            return
        try:
            self.editor.setPlainText(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            QMessageBox.warning(self, "读取失败", str(e))

    def _on_accept(self) -> None:
        text = self.editor.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "提示", "请粘贴或载入 JSON。")
            return
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            QMessageBox.warning(self, "JSON 解析失败",
                                f"第 {e.lineno} 行：{e.msg}")
            return
        kind = self.kind_combo.currentData()
        need = {"entities": "entities", "items": "items",
                "item_components": "components"}[kind]
        if need not in data:
            QMessageBox.warning(self, "结构不符",
                                f"缺少顶层字段 '{need}'。")
            return
        target = user_data_dir() / f"{kind}.json"
        existing = target.is_file()
        count = len(data.get(need) or [])
        answer = QMessageBox.question(
            self, "确认导入",
            f"将把 {count} 条数据写入：\n{target}\n\n"
            + ("**该文件已存在，会被整份覆盖**（旧的自定义条目会丢失，"
               "写入前自动备份为 .bak）。\n\n" if existing else "")
            + "继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        backup = None
        if existing:
            try:
                backup = target.with_suffix(target.suffix + ".bak")
                shutil.copy2(target, backup)
            except OSError as e:
                QMessageBox.warning(self, "备份失败，已取消", str(e))
                return
        try:
            target.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "写入失败", str(e))
            return
        self.imported_backup = backup
        self.accept()


# ---------------------------------------------------------------------------
# 子模块再导出（原本平级的 dialogs.py 已并入本包）
# ---------------------------------------------------------------------------

from app.ui.dialogs.diff_dialog import DiffDialog          # noqa: E402
from app.ui.dialogs.item_overview import ItemOverviewDialog  # noqa: E402
from app.ui.dialogs.settings_dialog import (                # noqa: E402
    SCOPE_CHOICES, SettingsDialog, TermTable,
)
from app.ui.dialogs.shortcuts_dialog import (              # noqa: E402
    SHORTCUTS, ShortcutsDialog,
)
from app.ui.dialogs.structure_dialog import StructureDialog  # noqa: E402

__all__ = [
    "SavePresetDialog", "PlaceholderDialog",
    "MigrateDialog", "DataImportDialog", "DATA_KINDS",
    "DiffDialog", "ShortcutsDialog", "SHORTCUTS",
    "SettingsDialog", "TermTable", "SCOPE_CHOICES",
    "ItemOverviewDialog", "StructureDialog",
]