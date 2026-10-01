"""SNBT 数据模型 + 解析器 + 序列化器 + 语法高亮，四合一。"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Optional

# =========================================================================
# 1. 数据模型
# =========================================================================

class SnbtKind(str, Enum):
    COMPOUND = "compound"
    LIST = "list"
    STRING = "string"
    INT = "int"
    LONG = "long"
    BYTE = "byte"
    SHORT = "short"
    FLOAT = "float"
    DOUBLE = "double"
    BOOL = "bool"
    INT_ARRAY = "int_array"
    LONG_ARRAY = "long_array"
    BYTE_ARRAY = "byte_array"


@dataclass
class SnbtNode:
    kind: SnbtKind = SnbtKind.COMPOUND
    key: Optional[str] = None
    value: Any = None
    children: list["SnbtNode"] = field(default_factory=list)

    # ---- 工厂 ----
    @staticmethod
    def new_compound(key: Optional[str] = None) -> "SnbtNode":
        return SnbtNode(SnbtKind.COMPOUND, key=key)

    @staticmethod
    def new_list(key: Optional[str] = None) -> "SnbtNode":
        return SnbtNode(SnbtKind.LIST, key=key)

    @staticmethod
    def new_scalar(kind: SnbtKind, value: Any, key: Optional[str] = None) -> "SnbtNode":
        if kind in (SnbtKind.COMPOUND, SnbtKind.LIST,
                    SnbtKind.INT_ARRAY, SnbtKind.LONG_ARRAY, SnbtKind.BYTE_ARRAY):
            raise ValueError(f"{kind} 不是标量类型")
        return SnbtNode(kind, key=key, value=value)

    # ---- 查询 ----
    @property
    def is_container(self) -> bool:
        return self.kind in (SnbtKind.COMPOUND, SnbtKind.LIST,
                             SnbtKind.INT_ARRAY, SnbtKind.LONG_ARRAY,
                             SnbtKind.BYTE_ARRAY)

    @property
    def is_array(self) -> bool:
        return self.kind in (SnbtKind.INT_ARRAY, SnbtKind.LONG_ARRAY,
                             SnbtKind.BYTE_ARRAY)

    def get_child(self, key: str) -> Optional["SnbtNode"]:
        if self.kind != SnbtKind.COMPOUND:
            return None
        for c in self.children:
            if c.key == key:
                return c
        return None

    def get_children(self, key: str) -> list["SnbtNode"]:
        return [c for c in self.children if c.key == key]

    def iter_descendants(self) -> Iterator["SnbtNode"]:
        for c in self.children:
            yield c
            yield from c.iter_descendants()

    # ---- 修改 ----
    def add_child(self, node: "SnbtNode", index: Optional[int] = None) -> None:
        if not self.is_container:
            raise TypeError(f"{self.kind} 不能添加子节点")
        if index is None:
            self.children.append(node)
        else:
            self.children.insert(index, node)

    def remove_child(self, node: "SnbtNode") -> None:
        try:
            self.children.remove(node)
        except ValueError:
            pass

    def index_of(self, node: "SnbtNode") -> int:
        try:
            return self.children.index(node)
        except ValueError:
            return -1

    def clone(self) -> "SnbtNode":
        return SnbtNode(self.kind, self.key, self.value,
                        [c.clone() for c in self.children])

    def display_label(self) -> str:
        if self.kind == SnbtKind.COMPOUND:
            inner = f"{{{len(self.children)} 项}}"
        elif self.kind == SnbtKind.LIST:
            inner = f"[{len(self.children)} 项]"
        elif self.is_array:
            inner = f"[{self.kind.value.split('_')[0].upper()}; {len(self.children)} 项]"
        elif self.kind == SnbtKind.STRING:
            s = str(self.value)
            inner = f'"{s[:32]}{"…" if len(s) > 32 else ""}"'
        else:
            suffix = {SnbtKind.BYTE: "b", SnbtKind.SHORT: "s",
                      SnbtKind.LONG: "L", SnbtKind.FLOAT: "f",
                      SnbtKind.DOUBLE: "d", SnbtKind.BOOL: "b"}.get(self.kind, "")
            inner = f"{self.value}{suffix}"
        return f"{self.key}: {inner}" if self.key is not None else inner


# 工厂函数
def compound(key=None, **kwargs) -> SnbtNode:
    node = SnbtNode.new_compound(key=key)
    for k, v in kwargs.items():
        v.key = k
        node.add_child(v)
    return node


def list_node(items=None, key=None) -> SnbtNode:
    node = SnbtNode.new_list(key=key)
    for it in (items or []):
        it.key = None
        node.add_child(it)
    return node


def string(v, key=None): return SnbtNode.new_scalar(SnbtKind.STRING, v, key)
def integer(v, key=None): return SnbtNode.new_scalar(SnbtKind.INT, int(v), key)
def long_(v, key=None): return SnbtNode.new_scalar(SnbtKind.LONG, int(v), key)
def byte(v, key=None): return SnbtNode.new_scalar(SnbtKind.BYTE, int(v), key)
def short(v, key=None): return SnbtNode.new_scalar(SnbtKind.SHORT, int(v), key)
def float_(v, key=None): return SnbtNode.new_scalar(SnbtKind.FLOAT, float(v), key)
def double(v, key=None): return SnbtNode.new_scalar(SnbtKind.DOUBLE, float(v), key)
def boolean(v, key=None): return SnbtNode.new_scalar(SnbtKind.BOOL, bool(v), key)


# =========================================================================
# 2. 解析器
# =========================================================================

class SnbtParseError(Exception):
    def __init__(self, message: str, pos: int, text: str):
        self.message, self.pos, self.text = message, pos, text
        line = text.count("\n", 0, pos) + 1
        col = pos - text.rfind("\n", 0, pos)
        self.line, self.col = line, col
        super().__init__(f"{message}（第 {line} 行，第 {col} 列）")


_UNQUOTED_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.+\-]*")
# 十进制定点 / 科学计数法；十六进制单独处理（`0x7Fb` 有歧义）
_NUMBER_RE = re.compile(
    r"^(?P<num>[+\-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+\-]?\d+)?)"
    r"(?P<suffix>[bBsSlLfFdD])?$"
)

#: 十六进制 token：`0x…` 后面可能跟类型后缀（b/s/l）
_HEX_TOKEN_RE = re.compile(
    r"^(?P<sign>[+\-]?)0[xX](?P<digits>[0-9a-fA-F]+?)(?P<suffix>[bBsSlL])?$")
_HEX_SUFFIX_LIMITS = {"b": (8, SnbtKind.BYTE), "s": (16, SnbtKind.SHORT),
                      "l": (64, SnbtKind.LONG)}


def _parse_hex_token(tok: str):
    """解析十六进制 token；返回 (SnbtKind, value) 或 None。

    `0x7Fb` 的最后一个 `b` 究竟是十六进制位还是 Byte 后缀，靠取值范围判断：
    `0x7F` 放得进 int8 → 127（Byte）；`0x1000L` 的 L 不是十六进制位，只能
    是 Long 后缀。正数用无符号范围，负数用有符号范围。
    """
    text = str(tok or "")
    if "0x" not in text.lower():                        # pragma: no cover
        return None
    match = _HEX_TOKEN_RE.match(text)
    if not match:
        return None
    digits = match.group("digits")
    suffix = (match.group("suffix") or "").lower()
    sign = -1 if match.group("sign") == "-" else 1
    if suffix in _HEX_SUFFIX_LIMITS and digits:
        bits, kind = _HEX_SUFFIX_LIMITS[suffix]
        value = int(digits, 16) if sign > 0 else -int(digits, 16)
        if _hex_fits(value, bits):
            # Byte / Short 按 NBT 的有符号语义折算
            # （0xFFb 与 -1b、0xFFFFs 与 -1s 是同一个数）
            if kind == SnbtKind.BYTE:
                return kind, ((value + 128) % 256) - 128
            if kind == SnbtKind.SHORT:
                return kind, ((value + 32768) % 65536) - 32768
            return kind, value
        # 后缀不成立（例如 0x1FFb）：整个 token 当十六进制整数
        whole = int(digits + suffix, 16)
        if sign < 0:
            whole = -whole
        return SnbtKind.INT, ((whole + 2 ** 31) % 2 ** 32) - 2 ** 31
    value = int(digits, 16)
    if sign < 0:
        value = -value
    return SnbtKind.INT, ((value + 2 ** 31) % 2 ** 32) - 2 ** 31


def _hex_fits(value: int, bits: int) -> bool:
    """十六进制取值能否放进指定位宽（有符号 / 无符号都算放得下）。"""
    if value < 0:
        return value >= -(1 << (bits - 1))
    return value <= (1 << bits) - 1


class SnbtParser:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.length = len(text)

    def parse(self) -> SnbtNode:
        self._skip_ws()
        if self.pos >= self.length:
            raise self._error("空的 SNBT 文本")
        node = self._parse_value(key=None)
        self._skip_ws()
        if self.pos < self.length:
            raise self._error("解析结束后仍有剩余字符")
        return node

    def _peek(self): return self.text[self.pos] if self.pos < self.length else None
    def _advance(self):
        ch = self.text[self.pos]; self.pos += 1; return ch
    def _skip_ws(self):
        while self.pos < self.length and self.text[self.pos] in " \t\r\n":
            self.pos += 1
    def _error(self, msg, pos=None):
        return SnbtParseError(msg, pos if pos is not None else self.pos, self.text)

    def _parse_value(self, key):
        self._skip_ws()
        ch = self._peek()
        if ch is None:
            raise self._error("意外的文本结尾")
        if ch == "{": return self._parse_compound(key)
        if ch == "[": return self._parse_bracket(key)
        if ch in ('"', "'"): return self._parse_quoted_string(key)
        return self._parse_unquoted(key)

    def _parse_compound(self, key):
        node = SnbtNode.new_compound(key=key)
        self._advance()
        self._skip_ws()
        if self._peek() == "}":
            self._advance(); return node
        while True:
            self._skip_ws()
            child_key = self._parse_key()
            self._skip_ws()
            if self._peek() != ":":
                raise self._error("compound 中缺少 ':'")
            self._advance(); self._skip_ws()
            node.add_child(self._parse_value(key=child_key))
            self._skip_ws()
            ch = self._peek()
            if ch == ",":
                self._advance(); self._skip_ws()
                if self._peek() == "}":
                    self._advance(); return node
                continue
            if ch == "}":
                self._advance(); return node
            raise self._error("compound 中应为 ',' 或 '}'")

    def _parse_key(self):
        self._skip_ws()
        ch = self._peek()
        if ch is None:
            raise self._error("期望键")
        if ch in ('"', "'"):
            return str(self._parse_quoted_string(key=None).value)
        m = _UNQUOTED_KEY_RE.match(self.text, self.pos)
        if not m:
            start = self.pos
            while self.pos < self.length and self.text[self.pos] not in " \t\r\n:{}[],":
                self.pos += 1
            raw = self.text[start:self.pos]
            if not raw:
                raise self._error("非法键")
            return raw
        self.pos = m.end()
        return m.group(0)

    def _parse_quoted_string(self, key):
        quote = self._advance()
        buf: list[str] = []
        while True:
            if self.pos >= self.length:
                raise self._error("字符串未闭合")
            ch = self._advance()
            if ch == "\\":
                if self.pos >= self.length:
                    raise self._error("转义字符未完成")
                buf.append(self._decode_escape(self._advance()))
            elif ch == quote:
                break
            else:
                buf.append(ch)
        return SnbtNode.new_scalar(SnbtKind.STRING, "".join(buf), key)

    @staticmethod
    def _decode_escape(ch):
        return {"n": "\n", "t": "\t", "r": "\r", "\\": "\\",
                '"': '"', "'": "'", "0": "\0"}.get(ch, ch)

    def _parse_bracket(self, key):
        self._advance()
        self._skip_ws()
        ch = self._peek()
        if ch in ("I", "L", "B") and self.pos + 1 < self.length \
                and self.text[self.pos + 1] == ";":
            arr_kind = {"I": SnbtKind.INT_ARRAY,
                        "L": SnbtKind.LONG_ARRAY,
                        "B": SnbtKind.BYTE_ARRAY}[ch]
            self.pos += 2
            return self._parse_array_body(key, arr_kind)

        node = SnbtNode.new_list(key=key)
        self._skip_ws()
        if self._peek() == "]":
            self._advance(); return node
        while True:
            node.add_child(self._parse_value(key=None))
            self._skip_ws()
            ch = self._peek()
            if ch == ",":
                self._advance(); self._skip_ws()
                if self._peek() == "]":
                    self._advance(); return node
                continue
            if ch == "]":
                self._advance(); return node
            raise self._error("list 中应为 ',' 或 ']'")

    def _parse_array_body(self, key, arr_kind):
        node = SnbtNode(kind=arr_kind, key=key)
        self._skip_ws()
        if self._peek() == "]":
            self._advance(); return node
        elem_kind = {SnbtKind.INT_ARRAY: SnbtKind.INT,
                     SnbtKind.LONG_ARRAY: SnbtKind.LONG,
                     SnbtKind.BYTE_ARRAY: SnbtKind.BYTE}[arr_kind]
        while True:
            self._skip_ws()
            tok = self._read_token()
            parsed = self._try_parse_number(tok)
            if parsed is None:
                raise self._error(f"数组元素 '{tok}' 不是数字")
            _, value = parsed
            node.add_child(SnbtNode.new_scalar(elem_kind, value, key=None))
            self._skip_ws()
            ch = self._peek()
            if ch == ",":
                self._advance(); self._skip_ws()
                if self._peek() == "]":
                    self._advance(); return node
                continue
            if ch == "]":
                self._advance(); return node
            raise self._error("数组元素间应为 ',' 或 ']'")

    def _parse_unquoted(self, key):
        tok = self._read_token()
        if not tok:
            raise self._error("期望值")
        num = self._try_parse_number(tok)
        if num is not None:
            kind, value = num
            return SnbtNode.new_scalar(kind, value, key)
        low = tok.lower()
        if low == "true":  return SnbtNode.new_scalar(SnbtKind.BOOL, True, key)
        if low == "false": return SnbtNode.new_scalar(SnbtKind.BOOL, False, key)
        return SnbtNode.new_scalar(SnbtKind.STRING, tok, key)

    def _read_token(self):
        self._skip_ws()
        start = self.pos
        while self.pos < self.length and self.text[self.pos] not in " \t\r\n,}]":
            self.pos += 1
        return self.text[start:self.pos]

    @staticmethod
    def _try_parse_number(tok):
        # 先单独处理 0x 十六进制：`0x7Fb` 里的 b 既可能是十六进制位也可能是
        # 类型后缀，所以不能用统一正则，必须按后缀的取值上限来切分。
        hex_result = _parse_hex_token(tok)
        if hex_result is not None:
            return hex_result
        m = _NUMBER_RE.match(tok)
        if not m:
            return None
        num_s = m.group("num")
        suffix = (m.group("suffix") or "").lower()
        if num_s is None:
            return None
        try:
            if suffix == "b":
                if num_s in ("0", "0.0"): return SnbtKind.BOOL, False
                if num_s in ("1", "1.0"): return SnbtKind.BOOL, True
                return SnbtKind.BYTE, int(float(num_s))
            if suffix == "s": return SnbtKind.SHORT, int(float(num_s))
            if suffix == "l": return SnbtKind.LONG, int(float(num_s))
            if suffix == "f": return SnbtKind.FLOAT, float(num_s)
            if suffix == "d": return SnbtKind.DOUBLE, float(num_s)
            if "." in num_s or "e" in num_s.lower():
                return SnbtKind.DOUBLE, float(num_s)
            return SnbtKind.INT, int(num_s)
        except ValueError:
            return None


# =========================================================================
# 3. 序列化器
# =========================================================================

_BARE_STRING_RE = re.compile(r"^[A-Za-z0-9._+\-]+$")
_RESERVED = {"true", "false", "null"}


def serialize(node: SnbtNode, pretty: bool = False) -> str:
    return _serialize(node, pretty=pretty, indent=0)


def _serialize(node, pretty, indent):
    k = node.kind
    if k == SnbtKind.COMPOUND: return _ser_compound(node, pretty, indent)
    if k == SnbtKind.LIST: return _ser_list(node, pretty, indent)
    if k in (SnbtKind.INT_ARRAY, SnbtKind.LONG_ARRAY, SnbtKind.BYTE_ARRAY):
        return _ser_array(node, pretty, indent)
    return _ser_scalar(node)


def _quote_if_needed(s):
    if s == "": return '""'
    if not _BARE_STRING_RE.match(s): return '"' + _escape(s) + '"'
    if s in _RESERVED: return '"' + s + '"'
    return s


def _escape(s):
    return (s.replace("\\", "\\\\").replace('"', '\\"')
             .replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r"))


def _fmt_float(v):
    if v != v or v in (float("inf"), float("-inf")):
        return "0.0"
    if v == int(v): return f"{v:.1f}"
    return repr(v)


def _ser_scalar(node):
    k = node.kind
    if k == SnbtKind.STRING: return _quote_if_needed(str(node.value))
    if k == SnbtKind.BOOL: return "1b" if node.value else "0b"
    if k == SnbtKind.BYTE: return f"{int(node.value)}b"
    if k == SnbtKind.SHORT: return f"{int(node.value)}s"
    if k == SnbtKind.LONG: return f"{int(node.value)}L"
    if k == SnbtKind.FLOAT: return f"{_fmt_float(float(node.value))}f"
    if k == SnbtKind.DOUBLE: return _fmt_float(float(node.value))
    if k == SnbtKind.INT: return str(int(node.value))
    return str(node.value)


def _ser_compound(node, pretty, indent):
    if not node.children: return "{}"
    if not pretty:
        return "{" + ",".join(
            f"{_quote_if_needed(c.key or '')}:{_serialize(c, False, indent)}"
            for c in node.children
        ) + "}"
    pad, close = "  " * (indent + 1), "  " * indent
    return "{\n" + ",\n".join(
        f"{pad}{_quote_if_needed(c.key or '')}: {_serialize(c, True, indent+1)}"
        for c in node.children
    ) + "\n" + close + "}"


def _ser_list(node, pretty, indent):
    if not node.children: return "[]"
    if not pretty:
        return "[" + ",".join(_serialize(c, False, indent) for c in node.children) + "]"
    pad, close = "  " * (indent + 1), "  " * indent
    return "[\n" + ",\n".join(
        f"{pad}{_serialize(c, True, indent+1)}" for c in node.children
    ) + "\n" + close + "]"


def _ser_array(node, pretty, indent):
    prefix = {SnbtKind.INT_ARRAY: "I;", SnbtKind.LONG_ARRAY: "L;",
              SnbtKind.BYTE_ARRAY: "B;"}[node.kind]
    if not node.children: return f"[{prefix}]"
    if not pretty:
        return f"[{prefix}" + ",".join(_ser_scalar(c) for c in node.children) + "]"
    pad, close = "  " * (indent + 1), "  " * indent
    return f"[{prefix}\n" + ",\n".join(
        f"{pad}{_ser_scalar(c)}" for c in node.children
    ) + "\n" + close + "]"


# =========================================================================
# 4. 语法高亮（延迟导入 Qt，保持 core 无 GUI 依赖）
# =========================================================================

def make_snbt_highlighter(document):
    """在 UI 层调用：返回一个已绑定的 QSyntaxHighlighter。"""
    from PySide6.QtCore import QRegularExpression
    from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

    class _Highlighter(QSyntaxHighlighter):
        def __init__(self, doc):
            super().__init__(doc)
            self._rules = self._build()

        @staticmethod
        def _fmt(color, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            f.setFontItalic(italic)
            return f

        def _build(self):
            return [
                (QRegularExpression(r'[A-Za-z_][A-Za-z0-9_.\-]*(?=\s*:)'),
                 self._fmt("#79C0FF", bold=True)),
                (QRegularExpression(r"\[[ILB];"), self._fmt("#FF7B72", bold=True)),
                (QRegularExpression(r'"(?:\\.|[^"\\])*"'), self._fmt("#A5D6A7")),
                (QRegularExpression(r"'(?:\\.|[^'\\])*'"), self._fmt("#A5D6A7")),
                (QRegularExpression(r"\b\d+\.\d+(?:[eE][+\-]?\d+)?[fFdD]?"),
                 self._fmt("#F2CC60")),
                (QRegularExpression(r"\b\d+[bBsSlL]?\b"), self._fmt("#F2CC60")),
                (QRegularExpression(r"\b(?:true|false)\b"),
                 self._fmt("#D2A8FF", bold=True)),
                (QRegularExpression(r"[{}\[\],:]"), self._fmt("#C9D1D9")),
            ]

        def highlightBlock(self, text):
            for pat, fmt in self._rules:
                it = pat.globalMatch(text)
                while it.hasNext():
                    m = it.next()
                    self.setFormat(m.capturedStart(), m.capturedLength(), fmt)

    return _Highlighter(document)


__all__ = [
    "SnbtNode", "SnbtKind", "SnbtParser", "SnbtParseError", "serialize",
    "compound", "list_node", "string", "integer", "long_", "byte", "short",
    "float_", "double", "boolean", "make_snbt_highlighter",
]