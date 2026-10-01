"""NBT 编辑对话框：值编辑、数组编辑（含 hex dump）、查找替换、批量添加。

对标 NBTExplorer 的 Edit Value / Edit Byte Array 等窗口：
    * 按标签类型给出合适的输入控件：整数用带范围的输入框（可十六进制），
      小数用 double 输入框，字符串用多行编辑器，布尔用复选框；
    * 错误即时反馈在窗口底部，确定按钮在校验通过前不可用；
    * 数组支持「列表」「十六进制」「字符串」三种视图，并实时显示 hex dump
      与元素个数 / 字节数；
    * 追加元素对话框可以一次填多个值（`,` 或空白分隔）。
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

from app.core.snbt import SnbtKind, SnbtNode
from app.ui.widgets.nbt_tags import (
    ARRAY_KINDS, CREATABLE, LIMITS, ValueParseError, array_element_kind,
    array_hexdump, array_length, array_to_text, bytes_to_hexdump,
    default_value, element_kind, format_value, parse_array_text,
    parse_element, parse_float, parse_int, parse_value, type_label,
    type_label_kind,
)

#: 整数类型的输入范围。用「显示范围」而不是有符号范围，这样既能输入
#: `255` 也能输入 `-1`（同一个字节），写回时按有符号折算。
INT_RANGES: dict[SnbtKind, tuple[int, int]] = {
    SnbtKind.BYTE: (-128, 255),
    SnbtKind.SHORT: (-32768, 65535),
    SnbtKind.INT: (-(2 ** 31), 2 ** 32 - 1),
    SnbtKind.LONG: (-(2 ** 63), 2 ** 64 - 1),
}

#: Qt 的 QSpinBox 只能装 32 位整数；Long 用带校验的文本框代替微调框，
#: 这样还能直接写 `0x…` 与 `1L`。
SPIN_RANGES: dict[SnbtKind, tuple[int, int]] = {
    SnbtKind.BYTE: (-128, 255),
    SnbtKind.SHORT: (-32768, 65535),
    SnbtKind.INT: (-(2 ** 31), 2 ** 31 - 1),
}

FLOAT_RANGES: dict[SnbtKind, tuple[float, float]] = {
    SnbtKind.FLOAT: (-3.4e38, 3.4e38),
    SnbtKind.DOUBLE: (-1.7976931348623157e308, 1.7976931348623157e308),
}


def _mono(widget) -> None:
    font = QFont("Consolas")
    font.setStyleHint(QFont.StyleHint.Monospace)
    widget.setFont(font)


def _fix_width(widget, width: int = 150) -> None:
    widget.setFixedWidth(width)
    widget.setMinimumWidth(min(width, 90))


# ---------------------------------------------------------------------------
# 单值：文本 ↔ 数值 的规范化
# ---------------------------------------------------------------------------

def canonical_int(value: int, kind: SnbtKind) -> int:
    """把「显示范围」的输入折算成该类型实际写入的数值。

    NBT 的 Byte/Short/Int/Long 都是有符号的：写 `255` 与写 `-1` 落到同一个
    字节上，这里统一折成有符号值，避免同一颗字节出现两种内部表示。
    """
    bits = {SnbtKind.BYTE: 8, SnbtKind.SHORT: 16, SnbtKind.INT: 32,
            SnbtKind.LONG: 64}.get(kind)
    if bits and value >= (1 << (bits - 1)):
        return value - (1 << bits)
    return value


def int_to_text(value: int, kind: SnbtKind) -> str:
    return str(canonical_int(int(value), kind))


def create_inline_editor(node: SnbtNode, parent: QWidget | None = None):
    """给标量节点造一个「就地编辑」控件。

    Byte / Short / Int 用带范围的微调框（不容易输错），Long 与小数用文本框
    （Long 超出 Qt 微调框的 32 位范围，文本框还能直接写 `0x…`）。
    """
    kind = node.kind
    if kind == SnbtKind.BOOL:
        box = QCheckBox(parent)
        box.setChecked(bool(node.value))
        return box
    if kind in SPIN_RANGES:
        low, high = SPIN_RANGES[kind]
        spin = QSpinBox(parent)
        spin.setRange(low, high)
        spin.setAccelerated(True)
        spin.setAlignment(Qt.AlignmentFlag.AlignRight
                          | Qt.AlignmentFlag.AlignVCenter)
        spin.setGroupSeparatorShown(kind == SnbtKind.INT)
        spin.setValue(canonical_int(int(node.value or 0), kind))
        spin.setToolTip(f"{type_label(node)} 取值范围 "
                        f"{INT_RANGES[kind][0]} ~ {INT_RANGES[kind][1]}")
        return spin
    if kind in FLOAT_RANGES:
        spin = QDoubleSpinBox(parent)
        low, high = FLOAT_RANGES[kind]
        spin.setDecimals(6 if kind == SnbtKind.FLOAT else 8)
        spin.setRange(max(low, -1e15), min(high, 1e15))
        spin.setAlignment(Qt.AlignmentFlag.AlignRight
                          | Qt.AlignmentFlag.AlignVCenter)
        spin.setValue(float(node.value or 0.0))
        spin.setToolTip(f"{type_label(node)} 小数")
        return spin
    edit = QLineEdit(parent)
    if kind == SnbtKind.STRING:
        edit.setText(str(node.value or ""))
        edit.setToolTip("字符串取值（不会自动加引号）")
    else:
        edit.setText(format_value(node))
        edit.setToolTip(
            f"{type_label(node)}：可用 0x 前缀写十六进制，"
            "1b / 1s / 1L / 1f / 1d 后缀可省略")
    return edit


def read_inline_editor(node: SnbtNode, widget):
    """从就地编辑控件读出合法取值；不合法时抛 ValueParseError。"""
    kind = node.kind
    if kind == SnbtKind.BOOL:
        return bool(widget.isChecked())
    if kind in SPIN_RANGES:
        return canonical_int(int(widget.value()), kind)
    if kind in FLOAT_RANGES:
        return float(widget.value())
    if kind == SnbtKind.STRING:
        return widget.text()
    return parse_element(widget.text(), kind)


# ---------------------------------------------------------------------------
# 值编辑对话框
# ---------------------------------------------------------------------------

class ValueEditDialog(QDialog):
    """编辑单个标签的值（也能重命名 key）。"""

    def __init__(self, node: SnbtNode, parent=None, hex_mode: bool = False):
        super().__init__(parent)
        self.node = node
        self._hex = hex_mode
        self.setWindowTitle(f"编辑 {type_label(node)} — {node.key or '(根)'}")
        self.resize(620, 420)
        self._result: object = None
        self._key_result: str | None = None
        self._buttons: QDialogButtonBox | None = None
        self._check: QCheckBox | None = None

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        info = QLabel(f"类型：<b>{type_label(node)}</b>"
                      f"　路径：<code>{node.key or '(根)'}</code>")
        info.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(info)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("名称"))
        self.key_edit = QLineEdit(node.key or "")
        self.key_edit.setEnabled(node.key is not None)
        if node.key is None:
            self.key_edit.setPlaceholderText("（根标签没有名称）")
        key_row.addWidget(self.key_edit, 1)
        v.addLayout(key_row)

        self.input: QWidget | None = None
        self.text: QPlainTextEdit | None = None
        self._value_kind = node.kind

        if node.kind == SnbtKind.STRING:
            v.addWidget(QLabel("值"))
            self.text = QPlainTextEdit(str(node.value))
            _mono(self.text)
            v.addWidget(self.text, 1)
        elif node.is_container:
            v.addWidget(QLabel(
                f"这是 {type_label(node)}，共 {len(node.children)} 项；"
                "请通过添加/删除子节点来修改内容。"))
        elif node.kind in INT_RANGES:
            v.addWidget(QLabel(
                f"值（范围 {LIMITS.get(node.kind)[0]} ~ "
                f"{LIMITS.get(node.kind)[1]}，可用 0x 前缀或 1b/1s/1L 后缀）"))
            low, high = INT_RANGES[node.kind]
            self._raw_int = high > 2 ** 31
            self.input = QLineEdit(int_to_text(int(node.value or 0), node.kind))
            _mono(self.input)
            self.input.selectAll()
            v.addWidget(self.input)
        elif node.kind in FLOAT_RANGES:
            v.addWidget(QLabel("值（小数，可写 1.5e-3 这样的科学计数法）"))
            self.input = QLineEdit(format_value(node))
            _mono(self.input)
            self.input.selectAll()
            v.addWidget(self.input)
        elif node.kind == SnbtKind.BOOL:
            v.addWidget(QLabel("值"))
            self._check = QCheckBox("为 1b（不勾选为 0b）")
            self._check.setChecked(bool(node.value))
            v.addWidget(self._check)
        else:
            v.addWidget(QLabel("值"))
            self.input = QLineEdit(format_value(node))
            _mono(self.input)
            self.input.selectAll()
            v.addWidget(self.input)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: #8B949E;")
        v.addWidget(self.message)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        self._buttons = buttons
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("确定")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        if not node.is_container:
            ok.setEnabled(False)
            if self.input is not None:
                self.input.textChanged.connect(self._validate)
            elif self.text is not None:
                self.text.textChanged.connect(self._validate)
            elif self._check is not None:
                self._check.toggled.connect(self._validate)
            self._validate()
        v.addWidget(buttons)

    # ---- 取值 ----

    def _current_text(self) -> str:
        if self.input is not None:
            return self.input.text()
        if self.text is not None:
            return self.text.toPlainText()
        return ""

    def _current_value(self):
        """当前控件里的值（容器返回 None）。"""
        if self.node.is_container:
            return None
        if self.node.kind == SnbtKind.BOOL:
            return bool(self._check is not None and self._check.isChecked())
        if self.node.kind == SnbtKind.STRING:
            return self.text.toPlainText()
        if self.node.kind in INT_RANGES:
            # 折成有符号：写 0xFF 与写 -1 落到同一颗字节上
            return canonical_int(parse_int(self._current_text(), self.node.kind),
                                 self.node.kind)
        if self.node.kind in FLOAT_RANGES:
            return parse_float(self._current_text(), self.node.kind)
        return parse_value(self.node, self._current_text())

    def _validate(self, *_args) -> None:
        ok = (self._buttons.button(QDialogButtonBox.StandardButton.Ok)
              if self._buttons else None)
        if self.node.is_container:
            if ok:
                ok.setEnabled(True)
            self.message.setStyleSheet("color: #8B949E;")
            self.message.setText("容器类型的名称可以修改。")
            return
        try:
            self._result = self._current_value()
        except ValueParseError as exc:
            self._result = None
            self.message.setStyleSheet("color: #FF7B72;")
            self.message.setText(str(exc))
            if ok:
                ok.setEnabled(False)
            return
        self.message.setStyleSheet("color: #8B949E;")
        note = ""
        if self.node.kind in INT_RANGES:
            raw = parse_int(self._current_text(), self.node.kind) \
                if self.input is not None else self._result
            folded = canonical_int(int(raw), self.node.kind)
            if folded != raw:
                note = f"（十六进制写法，实际存为 {folded}）"
        self.message.setText(f"确定后写入：{_pretty(self._result)}{note}")
        if ok:
            ok.setEnabled(True)

    def accept(self) -> None:  # noqa: D102 (Qt 命名)
        if not self.node.is_container:
            try:
                self._result = self._current_value()
            except ValueParseError as exc:
                self.message.setStyleSheet("color: #FF7B72;")
                self.message.setText(str(exc))
                return
        self._key_result = self.key_edit.text()
        super().accept()

    def apply_to(self, node: SnbtNode) -> None:
        """把编辑结果写回节点。"""
        if self._key_result is not None and node.key is not None:
            node.key = self._key_result
        if not node.is_container and self._result is not None:
            node.value = self._result


def _pretty(value: object) -> str:
    if isinstance(value, bool):
        return "1b" if value else "0b"
    return str(value)


# ---------------------------------------------------------------------------
# 数组编辑对话框
# ---------------------------------------------------------------------------

class ArrayEditDialog(QDialog):
    """数组编辑：列表 / 十六进制 / 字符串 三种视图。"""

    MODE_LIST = "list"
    MODE_HEX = "hex"
    MODE_TEXT = "text"

    def __init__(self, node: SnbtNode, parent=None, hex_mode: bool = False):
        super().__init__(parent)
        self.node = node
        self.setWindowTitle(f"编辑 {type_label(node)}（{array_length(node)} 项）")
        self.resize(820, 580)
        self._values: list[int] = [int(c.value) for c in node.children
                                   if c.value is not None]
        self._buttons: QDialogButtonBox | None = None

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        head = QHBoxLayout()
        self.count_label = QLabel("")
        head.addWidget(self.count_label)
        head.addStretch(1)
        for label, mode, tip in (
                ("列表", self.MODE_LIST, "十进制，逗号或空格分隔"),
                ("十六进制", self.MODE_HEX,
                 "字节两位十六进制；Int/Long 支持 0x 前缀"),
                ("字节转字符串", self.MODE_TEXT, "按 UTF-8 解释这段字节（只读）")):
            btn = QRadioButton(label)
            btn.setToolTip(tip)
            btn.setProperty("mode", mode)
            btn.toggled.connect(self._on_mode_changed)
            head.addWidget(btn)
            if mode == (self.MODE_HEX if hex_mode else self.MODE_LIST):
                btn.setChecked(True)
        v.addLayout(head)

        self.editor = QPlainTextEdit()
        _mono(self.editor)
        self.editor.setTabChangesFocus(True)
        self.editor.textChanged.connect(self._validate)
        v.addWidget(self.editor, 1)

        self.hexdump = QPlainTextEdit()
        _mono(self.hexdump)
        self.hexdump.setReadOnly(True)
        self.hexdump.setVisible(False)
        v.addWidget(self.hexdump, 1)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.btn_append = QPushButton("追加 0 …")
        self.btn_append.setToolTip("在末尾追加一个 0（也可以直接在文本里编辑）")
        self.btn_append.clicked.connect(self._append_zero)
        tools.addWidget(self.btn_append)
        self.btn_dim = QPushButton("检查范围")
        self.btn_dim.setToolTip("逐项检查是否超出该类型的取值范围")
        self.btn_dim.clicked.connect(self._report_range)
        tools.addWidget(self.btn_dim)
        tools.addStretch(1)
        self.size_label = QLabel("")
        self.size_label.setStyleSheet("color: #8B949E;")
        tools.addWidget(self.size_label)
        v.addLayout(tools)

        self.message = QLabel("")
        self.message.setStyleSheet("color: #8B949E;")
        self.message.setWordWrap(True)
        v.addWidget(self.message)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        self._buttons = buttons
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

        self._fill_editor()

    # ---- 模式 ----

    def _mode(self) -> str:
        for btn in self.findChildren(QRadioButton):
            if btn.isChecked():
                return str(btn.property("mode"))
        return self.MODE_LIST

    def _on_mode_changed(self, checked: bool) -> None:
        if not checked:
            return
        self._fill_editor()

    def _fill_editor(self) -> None:
        if not hasattr(self, "editor"):
            return  # 构造过程中单选按钮先触发，控件还没建好
        mode = self._mode()
        is_bytes = self.node.kind == SnbtKind.BYTE_ARRAY
        if mode == self.MODE_HEX:
            self.editor.setPlainText(array_to_text(self.node, hex_mode=True))
            self.editor.setReadOnly(not is_bytes)
            self.editor.setVisible(True)
            self.hexdump.setVisible(is_bytes)
            if is_bytes:
                self.hexdump.setPlainText(array_hexdump(self.node, limit=64))
        elif mode == self.MODE_TEXT:
            self.editor.setPlainText(self._as_text())
            self.editor.setReadOnly(True)
            self.editor.setVisible(True)
            self.hexdump.setVisible(False)
        else:
            self.editor.setPlainText(array_to_text(self.node, hex_mode=False))
            self.editor.setReadOnly(False)
            self.editor.setVisible(True)
            self.hexdump.setVisible(False)
        self._validate()

    def _as_text(self) -> str:
        data = bytes(v & 0xFF for v in self._values)
        return data.decode("utf-8", errors="replace")

    # ---- 工具按钮 ----

    def _append_zero(self) -> None:
        if self._mode() == self.MODE_TEXT:
            return
        self.editor.moveCursor(self.editor.textCursor().MoveOperation.End)
        text = self.editor.toPlainText().rstrip()
        self.editor.setPlainText(f"{text}, 0" if text else "0")

    def _report_range(self) -> None:
        try:
            values = parse_array_text(self.editor.toPlainText())
        except ValueParseError as exc:
            self._fail(str(exc))
            return
        bad = self._out_of_range(values)
        if bad:
            self._fail(f"有 {len(bad)} 项超出范围：{bad[:6]}")
            return
        self.message.setStyleSheet("color: #8B949E;")
        self.message.setText(f"{len(values)} 项全部在范围内。")

    def _out_of_range(self, values: list[int]) -> list[int]:
        if self.node.kind == SnbtKind.BYTE_ARRAY:
            return [v for v in values if not -128 <= v <= 255]
        if self.node.kind == SnbtKind.INT_ARRAY:
            return [v for v in values if not -(2 ** 31) <= v <= 2 ** 32 - 1]
        return [v for v in values if not -(2 ** 63) <= v <= 2 ** 64 - 1]

    def _fail(self, text: str) -> None:
        self.message.setStyleSheet("color: #FF7B72;")
        self.message.setText(text)
        ok = (self._buttons.button(QDialogButtonBox.StandardButton.Ok)
              if self._buttons else None)
        if ok:
            ok.setEnabled(False)

    # ---- 校验 ----

    def _validate(self, _text: str = "") -> None:
        ok = (self._buttons.button(QDialogButtonBox.StandardButton.Ok)
              if self._buttons else None)
        mode = self._mode()
        self.message.setStyleSheet("color: #8B949E;")
        if mode == self.MODE_TEXT:
            self.message.setText("只读预览（按 UTF-8 解释为文本）")
            if ok:
                ok.setEnabled(True)
            self._refresh_labels(self._values)
            return
        try:
            values = parse_array_text(self.editor.toPlainText())
        except ValueParseError as exc:
            self._fail(str(exc))
            return
        self._values = values
        bad = self._out_of_range(values)
        if bad:
            kind = array_element_kind(self.node.kind)
            self._fail(f"{type_label_kind(kind)} 超出范围：{bad[:6]}"
                       f"（字节可写 0x80~0xFF 表示同一颗字节）")
            self._refresh_labels(values)
            return
        if self.node.kind == SnbtKind.BYTE_ARRAY:
            self.hexdump.setPlainText(
                bytes_to_hexdump(bytes(v & 0xFF for v in values), limit=64))
        self.message.setText(f"将写入 {len(values)} 项。")
        if ok:
            ok.setEnabled(True)
        self._refresh_labels(values)

    def _refresh_labels(self, values: list[int]) -> None:
        kind = array_element_kind(self.node.kind)
        self.count_label.setText(
            f"类型 <b>{type_label(self.node)}</b>　元素 {type_label_kind(kind)}"
            f"　共 {len(values)} 项")
        size = {SnbtKind.BYTE_ARRAY: 1, SnbtKind.INT_ARRAY: 4,
                SnbtKind.LONG_ARRAY: 8}.get(self.node.kind, 1)
        self.size_label.setText(f"预计占 {len(values) * size} 字节")

    def accept(self) -> None:  # noqa: D102
        mode = self._mode()
        if mode != self.MODE_TEXT:
            try:
                self._values = parse_array_text(self.editor.toPlainText())
            except ValueParseError as exc:
                self._fail(str(exc))
                return
        elif not self._values:
            # 文本模式下允许把字符串直接写回字节数组
            self._values = list(self._as_text().encode("utf-8"))
        super().accept()

    def apply_to(self, node: SnbtNode) -> None:
        element = array_element_kind(node.kind)
        node.children = []
        for value in self._values:
            node.add_child(SnbtNode.new_scalar(
                element, canonical_int(int(value), element)))


# ---------------------------------------------------------------------------
# 追加元素对话框（List / 数组都能用）
# ---------------------------------------------------------------------------# ---------------------------------------------------------------------------
# 追加元素对话框（List / 数组都能用）
# ---------------------------------------------------------------------------
class AddElementDialog(QDialog):
    """一次给 List 或数组追加一个/多个元素。

    * 列表：可以选元素类型（空 List 必须选；非空 List 默认跟随首元素）；
    * 数组：元素类型固定，只填值；
    * 输入框接受 `1, 2, 3` 或 `1 2 3` 或换行分隔的多个值。
    """

    def __init__(self, parent_node: SnbtNode, parent=None):
        super().__init__(parent)
        self.parent_node = parent_node
        self.setWindowTitle("添加元素")
        self.resize(520, 300)
        self._buttons: QDialogButtonBox | None = None

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        if parent_node.kind in ARRAY_KINDS:
            fixed = array_element_kind(parent_node.kind)
            self._fixed_kind = fixed
            v.addWidget(QLabel(
                f"{type_label(parent_node)} 的元素类型固定为 "
                f"<b>{type_label_kind(fixed)}</b>"))
        else:
            self._fixed_kind = None
            row = QHBoxLayout()
            row.addWidget(QLabel("元素类型"))
            self.kind_combo = QComboBox()
            for label, kind in CREATABLE:
                if kind in (SnbtKind.LIST, SnbtKind.COMPOUND, *ARRAY_KINDS):
                    continue
                self.kind_combo.addItem(label, kind)
            guess = element_kind(parent_node)
            if guess is not None:
                idx = self.kind_combo.findData(guess)
                if idx >= 0:
                    self.kind_combo.setCurrentIndex(idx)
                self.kind_combo.setEnabled(False)
                self.kind_combo.setToolTip("List 的元素类型跟随第一个元素")
            row.addWidget(self.kind_combo, 1)
            v.addLayout(row)

        v.addWidget(QLabel("取值（可用逗号、空格或换行分隔多个）"))
        self.editor = QPlainTextEdit()
        _mono(self.editor)
        self.editor.setPlainText(default_text(self._element_kind()))
        self.editor.selectAll()
        self.editor.textChanged.connect(self._validate)
        v.addWidget(self.editor, 1)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: #8B949E;")
        v.addWidget(self.message)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        self._buttons = buttons
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("添加")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)
        self._validate()

    def _element_kind(self) -> SnbtKind:
        if self._fixed_kind is not None:
            return self._fixed_kind
        return self.kind_combo.currentData()

    def values(self) -> list:
        kind = self._element_kind()
        out: list = []
        for token in _split_tokens(self.editor.toPlainText(), kind):
            out.append(parse_element(token, kind))
        return out

    def _validate(self, *_args) -> None:
        ok = (self._buttons.button(QDialogButtonBox.StandardButton.Ok)
              if self._buttons else None)
        try:
            values = self.values()
        except ValueParseError as exc:
            self.message.setStyleSheet("color: #FF7B72;")
            self.message.setText(str(exc))
            if ok:
                ok.setEnabled(False)
            return
        if not values:
            self.message.setStyleSheet("color: #FF7B72;")
            self.message.setText("至少要填一个值")
            if ok:
                ok.setEnabled(False)
            return
        self.message.setStyleSheet("color: #8B949E;")
        preview = ", ".join(str(v) for v in values[:8])
        self.message.setText(f"将添加 {len(values)} 项：{preview}"
                             f"{' …' if len(values) > 8 else ''}")
        if ok:
            ok.setEnabled(True)


def default_text(kind: SnbtKind) -> str:
    if kind == SnbtKind.STRING:
        return ""
    return str(default_value(kind))


def _split_tokens(text: str, kind: SnbtKind | None = None) -> list[str]:
    """按逗号 / 换行切分多个值。

    数值与布尔还会把单个空格当分隔符（`1 2 3`）；字符串类型的空格是内容的一部分，
    所以只按逗号与换行切，`hello world` 仍然是一个元素。
    """
    body = text.strip()
    if not body:
        return []
    numeric = kind is None or kind != SnbtKind.STRING
    parts = re.split(r"[,\n\r;]+|\s+" if numeric else r"[,\n\r]+", body)
    return [p.strip() for p in parts if p.strip() != ""]


class FindReplaceBar(QWidget):
    """查找 / 替换工具条（NBTExplorer 的 Find / Replace）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("查找"))
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("按名称或取值查找…")
        row.addWidget(self.find_edit, 1)

        self.btn_prev = QPushButton("上一个")
        self.btn_prev.setFixedWidth(70)
        row.addWidget(self.btn_prev)
        self.btn_next = QPushButton("下一个")
        self.btn_next.setFixedWidth(70)
        row.addWidget(self.btn_next)

        self.match_keys = QCheckBox("名称")
        self.match_keys.setChecked(True)
        row.addWidget(self.match_keys)
        self.match_values = QCheckBox("取值")
        self.match_values.setChecked(True)
        row.addWidget(self.match_values)
        self.match_case = QCheckBox("区分大小写")
        row.addWidget(self.match_case)
        self.match_exact = QCheckBox("整个标签")
        row.addWidget(self.match_exact)
        row.addStretch(1)
        self.result = QLabel("")
        self.result.setStyleSheet("color: #8B949E;")
        row.addWidget(self.result)
        v.addLayout(row)

        replace_row = QHBoxLayout()
        replace_row.setSpacing(6)
        replace_row.addWidget(QLabel("替换"))
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("替换为…（只作用于 String 标签）")
        replace_row.addWidget(self.replace_edit, 1)
        self.btn_replace = QPushButton("替换")
        self.btn_replace.setFixedWidth(70)
        replace_row.addWidget(self.btn_replace)
        self.btn_replace_all = QPushButton("全部替换")
        self.btn_replace_all.setFixedWidth(90)
        replace_row.addWidget(self.btn_replace_all)
        self.case_only = QCheckBox("只改大小写 / 只替换完整取值")
        self.case_only.setToolTip(
            "勾选后仅在取值与查找内容完全相同时替换，避免误伤长文本")
        replace_row.addWidget(self.case_only)
        replace_row.addStretch(1)
        v.addLayout(replace_row)


__all__ = [
    "ValueEditDialog", "ArrayEditDialog", "AddElementDialog", "FindReplaceBar",
    "INT_RANGES", "SPIN_RANGES", "FLOAT_RANGES", "canonical_int", "int_to_text",
    "create_inline_editor", "read_inline_editor", "default_text",
]
