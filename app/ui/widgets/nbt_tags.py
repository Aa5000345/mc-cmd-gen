"""NBT 标签类型层：类型判定、文本格式化与解析、节点路径。

这一层刻意不依赖 Qt，方便单元测试；界面只负责调用。
对标 NBTExplorer 的「类型列 + 值列」表现：
    * 每个标签都有明确的类型名（Byte / Short / Int / Long / Float /
      Double / String / List / Compound / ByteArray / IntArray / LongArray）；
    * 数组可以按「十进制」或「十六进制」显示；
    * 值解析宽容（`1b` / `1` / `0x1F` 都认），编辑不易出错。
"""
from __future__ import annotations

import re
from typing import Iterable, Iterator, Optional

from app.core.snbt import SnbtKind, SnbtNode

# ---------------------------------------------------------------------------
# 类型名（列名用）
# ---------------------------------------------------------------------------

TYPE_LABELS: dict[SnbtKind, str] = {
    SnbtKind.BYTE: "Byte",
    SnbtKind.SHORT: "Short",
    SnbtKind.INT: "Int",
    SnbtKind.LONG: "Long",
    SnbtKind.FLOAT: "Float",
    SnbtKind.DOUBLE: "Double",
    SnbtKind.STRING: "String",
    SnbtKind.BOOL: "Bool",
    SnbtKind.LIST: "List",
    SnbtKind.COMPOUND: "Compound",
    SnbtKind.BYTE_ARRAY: "ByteArray",
    SnbtKind.INT_ARRAY: "IntArray",
    SnbtKind.LONG_ARRAY: "LongArray",
}

#: 数组类型（整数元素）
ARRAY_KINDS = (SnbtKind.BYTE_ARRAY, SnbtKind.INT_ARRAY, SnbtKind.LONG_ARRAY)
#: Compound 在「值」列里最多预览几个字段
COMPOUND_PREVIEW_ITEMS = 4
#: 字段多到这个数以上就不再逐个预览（改成 `{ 130 项 }`，避免拖慢界面）
COMPOUND_PREVIEW_MAX_CHILDREN = 8
#: 数值标量
NUMBER_KINDS = (SnbtKind.BYTE, SnbtKind.SHORT, SnbtKind.INT, SnbtKind.LONG,
                SnbtKind.FLOAT, SnbtKind.DOUBLE)

SUFFIXES = {
    SnbtKind.BYTE: "b", SnbtKind.SHORT: "s", SnbtKind.LONG: "L",
    SnbtKind.FLOAT: "f", SnbtKind.DOUBLE: "d",
}

COMPOUND_KEYS = ("compound", "Compound", "{}", "Compound {}")
LIST_KEYS = ("list", "List", "[]", "List []")
KIND_ALIASES: dict[str, SnbtKind] = {
    "byte": SnbtKind.BYTE, "b": SnbtKind.BYTE,
    "short": SnbtKind.SHORT, "s": SnbtKind.SHORT,
    "int": SnbtKind.INT, "i": SnbtKind.INT,
    "long": SnbtKind.LONG, "l": SnbtKind.LONG,
    "float": SnbtKind.FLOAT, "f": SnbtKind.FLOAT,
    "double": SnbtKind.DOUBLE, "d": SnbtKind.DOUBLE,
    "string": SnbtKind.STRING, "str": SnbtKind.STRING,
    "bool": SnbtKind.BOOL, "boolean": SnbtKind.BOOL,
    "list": SnbtKind.LIST,
    "compound": SnbtKind.COMPOUND,
    "bytearray": SnbtKind.BYTE_ARRAY, "byte_array": SnbtKind.BYTE_ARRAY,
    "intarray": SnbtKind.INT_ARRAY, "int_array": SnbtKind.INT_ARRAY,
    "longarray": SnbtKind.LONG_ARRAY, "long_array": SnbtKind.LONG_ARRAY,
}

# 新建节点时的可选项（顺序即菜单顺序）
CREATABLE: tuple[tuple[str, SnbtKind], ...] = (
    ("Byte", SnbtKind.BYTE),
    ("Short", SnbtKind.SHORT),
    ("Int", SnbtKind.INT),
    ("Long", SnbtKind.LONG),
    ("Float", SnbtKind.FLOAT),
    ("Double", SnbtKind.DOUBLE),
    ("String", SnbtKind.STRING),
    ("List", SnbtKind.LIST),
    ("Compound", SnbtKind.COMPOUND),
    ("ByteArray", SnbtKind.BYTE_ARRAY),
    ("IntArray", SnbtKind.INT_ARRAY),
    ("LongArray", SnbtKind.LONG_ARRAY),
)

# 默认值（NBTExplorer 同样是 0 / 空串）
DEFAULTS = {
    SnbtKind.BYTE: 0, SnbtKind.SHORT: 0, SnbtKind.INT: 0,
    SnbtKind.LONG: 0, SnbtKind.FLOAT: 0.0, SnbtKind.DOUBLE: 0.0,
    SnbtKind.STRING: "", SnbtKind.BOOL: False,
}

# 数值范围（用于编辑校验）
LIMITS = {
    SnbtKind.BYTE: (-128, 255),
    SnbtKind.SHORT: (-32768, 65535),
    SnbtKind.INT: (-2147483648, 4294967295),
    SnbtKind.LONG: (-(2 ** 63), 2 ** 64 - 1),
}


def type_label(node: SnbtNode) -> str:
    return TYPE_LABELS.get(node.kind, str(node.kind))


def is_array(node: SnbtNode) -> bool:
    return node.kind in ARRAY_KINDS


def array_length(node: SnbtNode) -> int:
    """数组元素个数（忽略脏数据）。"""
    return len([c for c in node.children if c.value is not None])


def array_values(node: SnbtNode) -> list[int]:
    """数组元素的整数列表。"""
    return [int(c.value) for c in node.children if c.value is not None]


def element_kind(node: SnbtNode) -> SnbtKind | None:
    """容器的「元素类型」。

    * 数组 (ByteArray/IntArray/LongArray) → 元素是 Byte / Int / Long；
    * List → 与第一个子元素同类型（空 List 返回 None，表示任意类型）；
    * Compound → 每个子标签各自带名字，不限定类型（返回 None）。
    """
    if node.kind in ARRAY_KINDS:
        return array_element_kind(node.kind)
    if node.kind == SnbtKind.LIST:
        return node.children[0].kind if node.children else None
    return None


def can_hold(parent: SnbtNode, child: SnbtNode) -> bool:
    """`child` 能否放进 `parent`（NBT 的类型约束，供拖放与新建校验）。"""
    if not parent.is_container:
        return False
    if parent is child:
        return False
    if parent.kind == SnbtKind.COMPOUND:
        return True
    if parent.kind == SnbtKind.LIST:
        expected = element_kind(parent)
        return expected is None or expected == child.kind
    if parent.kind in ARRAY_KINDS:
        return child.kind == array_element_kind(parent.kind)
    return False


def is_descendant(node: SnbtNode, root: SnbtNode) -> bool:
    """`node` 是否就是 `root` 或挂在 `root` 之下（按对象身份判断）。

    拖放时用来判断「是否把标签拖进了自己的子树」：从 `root` 往下找 `node`。
    注意不能按值比较 —— 内容相同的两个节点会被误判成同一个。
    """
    stack = [root]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if current is node:
            return True
        if id(current) in seen:
            continue
        seen.add(id(current))
        stack.extend(current.children)
    return False


def container_summary(node: SnbtNode) -> str:
    """容器的一行说明（详情面板用）。"""
    k = node.kind
    if k == SnbtKind.COMPOUND:
        return f"{len(node.children)} 个子标签"
    if k == SnbtKind.LIST:
        expected = element_kind(node)
        label = type_label_kind(expected) if expected is not None else "任意"
        return f"{len(node.children)} 项 · 元素类型 {label}"
    if k in ARRAY_KINDS:
        return f"{array_length(node)} 项 · {type_label_kind(array_element_kind(k))}"
    return ""


def is_numeric(node: SnbtNode) -> bool:
    return node.kind in NUMBER_KINDS or node.kind == SnbtKind.BOOL


def default_value(kind: SnbtKind):
    return DEFAULTS.get(kind, "")


# ---------------------------------------------------------------------------
# 值 → 文本
# ---------------------------------------------------------------------------

def format_key(key: Optional[str]) -> str:
    """键名在「值」列里的写法：带点或空格的键名加引号。"""
    text = str(key or "")
    if text and all(ch.isalnum() or ch in "_+-" for ch in text):
        return text
    return '"' + text.replace('"', '\\"') + '"'


def format_scalar_inline(node: SnbtNode) -> str:
    """单行展示的标量（字符串加引号，与 SNBT 一致）。"""
    if node.kind == SnbtKind.STRING:
        text = str(node.value)
        if text == "":
            return '""'
        if all(ch.isalnum() or ch in "._+-" for ch in text) \
                and text not in ("true", "false", "null"):
            return text
        escaped = (text.replace("\\", "\\\\").replace('"', '\\"')
                   .replace("\n", "\\n").replace("\t", "\\t")
                   .replace("\r", "\\r"))
        return f'"{escaped}"'
    return format_value(node, max_items=0)


#: Compound 预览优先展示的字段（物品、实体、方块这类结构一眼就能看懂）
PRIORITY_PREVIEW_KEYS = (
    "id", "Id", "ID", "ModId", "type", "Type", "Name", "name",
    "display", "text", "translate", "count", "Count", "ModSize",
)


def _preview_compound(node: SnbtNode, hex_mode: bool, depth: int,
                      limit: int) -> str:
    """Compound 的单行预览：`{Slot:0b, id:"minecraft:stone", …}`。

    数据本身是完整的，但 `{ 2 项 }` 这种写法得点开才知道里面是什么，
    所以在「值」列直接把字段列出来（NBTExplorer 也是这个思路）。
    优先字段（id / ModId / type …）排在前面，其余保持原顺序。
    """
    children = node.children
    priority = {key: index for index, key in enumerate(PRIORITY_PREVIEW_KEYS)}
    order = sorted(range(len(children)),
                   key=lambda i: priority.get(str(children[i].key or ""),
                                              len(priority)))
    parts: list[str] = []
    for index in order[:limit]:
        child = children[index]
        if child.is_container:
            text = format_value(child, hex_mode, max_items=0,
                                depth=max(0, depth - 1))
        else:
            text = format_scalar_inline(child)
        parts.append(f"{format_key(child.key)}: {text}")
    if len(children) > len(parts):
        parts.append("…")
    return "{" + ", ".join(parts) + "}"


def format_value(node: SnbtNode, hex_mode: bool = False,
                 max_items: int = 0, depth: int = 2,
                 preview: bool = True) -> str:
    """把节点渲染成「值」列里的字符串。

    * `max_items > 0`：列表只预览前若干项（如 `[1, 2, 3, …]`）；
    * `depth`：Compound / 嵌套列表的展开层数（0 表示不再往里展开）；
    * `preview=False`：用一个简短的占位（`{ 3 项 }`）代替字段预览 ——
      查找 / 筛选用它，免得把字段名也当成「取值」匹配到。
    """
    k = node.kind
    if k == SnbtKind.COMPOUND:
        count = len(node.children)
        if not count:
            return "{}"
        if preview and max_items == 0 and depth > 0 \
                and count <= COMPOUND_PREVIEW_MAX_CHILDREN:
            preview_text = _preview_compound(node, hex_mode, depth - 1,
                                             COMPOUND_PREVIEW_ITEMS)
            omitted = count > COMPOUND_PREVIEW_ITEMS
            return (f"{preview_text}（{count} 项）" if omitted
                    else preview_text)
        return f"{{ {count} 项 }}"
    if k == SnbtKind.LIST:
        if not node.children:
            return "[ 空 ]"
        total = len(node.children)
        # 默认只预览前几项：大列表逐个渲染会明显拖慢界面
        limit = max_items if max_items else COMPOUND_PREVIEW_ITEMS
        shown = node.children[:limit]
        body = ", ".join(
            format_value(c, hex_mode, max_items=0,
                         depth=depth - 1 if depth > 0 else 0, preview=preview)
            for c in shown)
        more = ", …" if total > len(shown) else ""
        text = f"[ {body}{more} ]"
        return f"{text}（{total} 项）" if total > len(shown) else text
    if k in ARRAY_KINDS:
        return format_array(node, hex_mode=hex_mode, max_items=max_items)
    if k == SnbtKind.STRING:
        return str(node.value)
    if k == SnbtKind.BOOL:
        return "1b" if node.value else "0b"
    suffix = SUFFIXES.get(k, "")
    return f"{node.value}{suffix}"


def format_array(node: SnbtNode, hex_mode: bool = False,
                 max_items: int = 12) -> str:
    items = [int(c.value) for c in node.children if c.value is not None]
    total = len(items)
    shown = items if not max_items else items[:max_items]
    if hex_mode:
        # 字节按 NBTExplorer 的习惯显示两位十六进制（01 FF），
        # 更大的整数用 0x 前缀避免歧义
        if node.kind == SnbtKind.BYTE_ARRAY:
            body = ", ".join(f"{v & 0xFF:02X}" for v in shown)
        else:
            body = ", ".join(f"0x{v:X}" for v in shown)
    else:
        body = ", ".join(str(v) for v in shown)
    more = ", …" if total > len(shown) else ""
    return f"[{array_prefix(node.kind)}; {body}{more}]（{total} 项）"


# ---------------------------------------------------------------------------
# 文本 → 值
# ---------------------------------------------------------------------------

class ValueParseError(ValueError):
    """值文本无法解析成该标签类型的取值。"""


def _strip_suffix(text: str) -> tuple[str, str]:
    """去掉 SNBT 的类型后缀（`1b` / `1L` / `1.5f`）。

    注意不能把十六进制字面量的末位当成后缀：`0xFF` 里的 `F` 是数字。
    """
    body = text.strip()
    suffix = ""
    if len(body) >= 2 and body[-1] in "bBsSlLfFdD":
        head = body[:-1]
        is_hex_literal = head[:2].lower() == "0x" and all(
            ch in "0123456789abcdefABCDEF" for ch in head[2:])
        if not is_hex_literal and head and (head[-1].isdigit() or head[-1] == "."):
            suffix = body[-1].lower()
            body = head
    return body, suffix


def parse_int(text: str, kind: SnbtKind) -> int:
    """解析整数：支持 `1b`、`1`、`0x1F`、`-3`。"""
    body, _suffix = _strip_suffix(str(text))
    if not body:
        raise ValueParseError("不能为空")
    sign = -1 if body.startswith("-") else 1
    digits = body[1:] if body[:1] in "+-" else body
    base = 16 if digits[:2].lower() == "0x" else 10
    if base == 16:
        digits = digits[2:]
    if not digits:
        raise ValueParseError(f"“{text}” 不是合法整数")
    try:
        value = sign * int(digits, base)
    except ValueError as exc:
        raise ValueParseError(f"“{text}” 不是合法整数（可用 0x 前缀写十六进制）") \
            from exc
    low, high = LIMITS.get(kind, (-(2 ** 63), 2 ** 64 - 1))
    if not (low <= value <= high):
        raise ValueParseError(f"{value} 超出 {type_label_kind(kind)} 的范围 "
                              f"[{low}, {high}]")
    return value


def type_label_kind(kind: SnbtKind) -> str:
    return TYPE_LABELS.get(kind, str(kind))


def parse_float(text: str, kind: SnbtKind) -> float:
    body, _suffix = _strip_suffix(str(text))
    if not body:
        raise ValueParseError("不能为空")
    try:
        return float(body)
    except ValueError as exc:
        raise ValueParseError(f"“{text}” 不是合法小数") from exc


def parse_bool(text: str) -> bool:
    body = str(text).strip().lower()
    if body in ("1b", "1", "true", "t", "yes", "y"):
        return True
    if body in ("0b", "0", "false", "f", "no", "n"):
        return False
    try:
        return parse_int(body, SnbtKind.BYTE) != 0
    except ValueParseError as exc:
        raise ValueParseError(f"“{text}” 不是合法布尔值（用 1b / 0b）") from exc


def parse_value(node: SnbtNode, text: str):
    """按节点类型解析文本，返回可直接赋给 node.value 的值。"""
    k = node.kind
    if k in (SnbtKind.BYTE, SnbtKind.SHORT, SnbtKind.INT, SnbtKind.LONG):
        return parse_int(text, k)
    if k in (SnbtKind.FLOAT, SnbtKind.DOUBLE):
        return parse_float(text, k)
    if k == SnbtKind.BOOL:
        return parse_bool(text)
    if k == SnbtKind.STRING:
        return str(text)
    raise ValueParseError("该类型不支持直接编辑单个值")


# ---------------------------------------------------------------------------
# 数组文本
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"[-+]?(0[xX][0-9a-fA-F]+|\d+)")


def parse_array_text(text: str) -> list[int]:
    """解析数组内容：接受 `[B;1,2]` / `[I; 1 2 3]` / `1,2,3` / 空白分隔。"""
    body = str(text).strip()
    if body.startswith("[") and body.endswith("]"):
        body = body[1:-1]
    body = re.sub(r"^[BILbil]\s*;", "", body.strip())
    values: list[int] = []
    for token in re.split(r"[,\s]+", body.strip()):
        token = token.strip()
        if not token:
            continue
        match = _NUM_RE.fullmatch(token)
        if not match:
            raise ValueParseError(f"“{token}” 不是合法整数")
        raw = match.group(0)
        sign = -1 if raw.startswith("-") else 1
        digits = raw.lstrip("+-")
        base = 16 if digits[:2].lower() == "0x" else 10
        if base == 16:
            digits = digits[2:]
        try:
            values.append(sign * int(digits, base))
        except ValueError as exc:
            raise ValueParseError(f"“{token}” 不是合法整数") from exc
    return values


def array_element_kind(kind: SnbtKind) -> SnbtKind:
    return {SnbtKind.BYTE_ARRAY: SnbtKind.BYTE,
            SnbtKind.INT_ARRAY: SnbtKind.INT,
            SnbtKind.LONG_ARRAY: SnbtKind.LONG}.get(kind, SnbtKind.INT)


def array_prefix(kind: SnbtKind) -> str:
    return {SnbtKind.BYTE_ARRAY: "B", SnbtKind.INT_ARRAY: "I",
            SnbtKind.LONG_ARRAY: "L"}.get(kind, "I")


def array_to_text(node: SnbtNode, hex_mode: bool = False,
                  wrap: int = 16) -> str:
    """数组的完整文本（用于编辑对话框）。"""
    values = [int(c.value) for c in node.children if c.value is not None]
    if hex_mode:
        if node.kind == SnbtKind.BYTE_ARRAY:
            tokens = [f"{v & 0xFF:02X}" for v in values]
        else:
            tokens = [f"0x{v:X}" for v in values]
    else:
        tokens = [str(v) for v in values]
    if wrap <= 0 or not tokens:
        return ", ".join(tokens)
    lines = [", ".join(tokens[i:i + wrap]) for i in range(0, len(tokens), wrap)]
    return ",\n".join(lines)


def bytes_to_hexdump(data: bytes, width: int = 16,
                     limit: int = 0) -> str:
    """经典 hex dump：偏移 + 十六进制 + ASCII。

    limit > 0 时最多输出这么多行，超出部分给出省略提示。
    """
    lines: list[str] = []
    total = (len(data) + width - 1) // width
    for index, offset in enumerate(range(0, len(data), width)):
        if limit and index >= limit:
            lines.append(f"…… 其余 {total - limit} 行已省略")
            break
        chunk = data[offset:offset + width]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        hex_part = hex_part.ljust(width * 3 - 1)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset:08X}  {hex_part}  |{ascii_part}|")
    return "\n".join(lines)


def array_hexdump(node: SnbtNode, limit: int = 0) -> str:
    """数组 → hex dump。

    只有 ByteArray 的内存布局与 hex dump 一一对应；IntArray / LongArray
    是 4/8 字节大端整数，转成字节后 dump 出来才是它们真正写进文件的样子，
    所以这里统一按大端展开。
    """
    values = array_values(node)
    if node.kind == SnbtKind.BYTE_ARRAY:
        data = bytes(v & 0xFF for v in values)
    elif node.kind == SnbtKind.LONG_ARRAY:
        data = b"".join((v & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "big")
                        for v in values)
    else:
        data = b"".join((v & 0xFFFFFFFF).to_bytes(4, "big") for v in values)
    return bytes_to_hexdump(data, limit=limit)


def array_preview(node: SnbtNode, max_items: int = 64,
                  hex_mode: bool = False) -> str:
    """详情面板用的数组预览：一行若干项，超出部分省略。"""
    values = array_values(node)
    tokens = ([f"{v & 0xFF:02X}" for v in values] if hex_mode
              and node.kind == SnbtKind.BYTE_ARRAY
              else [f"0x{v:X}" for v in values] if hex_mode
              else [str(v) for v in values])
    shown = tokens[:max_items]
    per_line = 12
    lines = [", ".join(shown[i:i + per_line])
             for i in range(0, len(shown), per_line)]
    if len(tokens) > len(shown):
        lines.append(f"…… 其余 {len(tokens) - len(shown)} 项已省略")
    return "\n".join(lines)


def format_element(node: SnbtNode) -> str:
    """单个元素的取值文本（不带数组括号）。"""
    if node.kind == SnbtKind.STRING:
        return str(node.value)
    return format_value(node)


def parse_element(text: str, kind: SnbtKind):
    """把一段文本解析成指定类型的元素值（供单元素编辑）。"""
    if kind == SnbtKind.STRING:
        return str(text)
    if kind == SnbtKind.BOOL:
        return parse_bool(text)
    if kind in (SnbtKind.BYTE, SnbtKind.SHORT, SnbtKind.INT, SnbtKind.LONG):
        return parse_int(text, kind)
    if kind in (SnbtKind.FLOAT, SnbtKind.DOUBLE):
        return parse_float(text, kind)
    raise ValueParseError("该类型不支持按元素编辑")


def parse_data_path(path: str) -> list[tuple[str, int]]:
    """解析 `Data.Player.Inventory[0].id` 形式的路径。

    返回 [(键名, 该键之后的下标列表)]，例如
    `Data.Player.Inventory[0].id` → [("Data", []), ("Player", []),
    ("Inventory", [0]), ("id", [])]。

    支持带引号的键名（`"my.key"`），也容忍多余的空格。
    """
    tokens: list[tuple[str, list[int]]] = []
    buffer: list[str] = []
    index = 0
    length = len(path)

    def flush() -> None:
        token = "".join(buffer).strip()
        buffer.clear()
        if token:
            tokens.append((token, []))

    while index < length:
        ch = path[index]
        if ch == '"':
            end = path.find('"', index + 1)
            if end < 0:
                buffer.append(path[index + 1:])
                index = length
                break
            buffer.append(path[index + 1:end])
            index = end + 1
            continue
        if ch == ".":
            flush()
            index += 1
            continue
        if ch == "[":
            end = path.find("]", index + 1)
            if end < 0:
                buffer.append(path[index:])
                index = length
                break
            raw = path[index + 1:end].strip()
            flush()
            if tokens:
                try:
                    tokens[-1][1].append(int(raw, 0))
                except ValueError:
                    tokens[-1][1].append(0)
            index = end + 1
            continue
        buffer.append(ch)
        index += 1
    flush()
    return tokens


def path_segments(node: SnbtNode, root: SnbtNode | None = None) -> str:
    """与 `node_path` 相同，但键名含特殊字符时加引号，便于再解析回去。"""
    if node is root:
        return "(根)"
    trail = _trail(root, node) if root is not None else None
    if not trail:
        return node.key or "(根)"
    parts: list[str] = []
    for index, current in enumerate(trail):
        parent = trail[index - 1] if index > 0 else None
        if current.key and index > 0:
            # 带引号的键名本身就起分隔作用，不用再加点
            quoted = quote_key(str(current.key))
            parts.append(quoted if quoted.startswith('"') else f".{quoted}")
        elif current.key:
            parts.append(quote_key(str(current.key)))
        elif parent is not None:
            try:
                position = parent.children.index(current)
            except ValueError:
                position = 0
            parts.append(f"[{position}]")
    return "".join(parts) or "(根)"


def quote_key(key: str) -> str:
    """路径里的键名：含 `.` `[` 等特殊字符时加引号。"""
    if key and all(ch.isalnum() or ch in "_+-" for ch in key):
        return key
    return '"' + key.replace('"', '\\"') + '"'


# ---------------------------------------------------------------------------
# 查找 / 筛选
# ---------------------------------------------------------------------------

def _contains_ignore_case(text: str, needle: str) -> bool:
    return needle.lower() in text.lower()


def match_segments(text: str, needle: str, case_sensitive: bool = False,
                   exact: bool = False) -> list[tuple[int, int]]:
    """`needle` 在 `text` 里的命中区间（供高亮）；exact 时要求整串相等。"""
    if not needle:
        return []
    if exact:
        hit = text == needle if case_sensitive else text.lower() == needle.lower()
        return [(0, len(text))] if hit else []
    haystack = text if case_sensitive else text.lower()
    target = needle if case_sensitive else needle.lower()
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        found = haystack.find(target, start)
        if found < 0:
            return spans
        spans.append((found, found + len(target)))
        start = found + max(1, len(target))


def node_field_text(node: SnbtNode, field: str) -> str:
    """某个字段的可搜索文本。

    取值的搜索用简短占位（`{ 3 项 }`）而不是字段预览：否则搜 `ModId`
    会因为预览里含这个字段名把父标签也当成「取值命中」。
    """
    if field == "key":
        return str(node.key or "")
    if field == "value":
        if node.is_container:
            return format_value(node, preview=False)
        return format_value(node)
    if field == "type":
        return type_label(node)
    return ""


def filter_nodes(root: SnbtNode, needle: str = "", *, fields=("key",),
                 case_sensitive: bool = False, exact: bool = False,
                 types: Iterable[SnbtKind] | None = None) -> set[int]:
    """返回「应当保留」的节点 id 集合。

    命中的节点连同它所有的祖先都会保留（这样树才能展开到命中处）。
    needle 为空时只看类型过滤；类型与关键字都为空则返回空集合（表示不筛选）。
    """
    wanted = set(types) if types else None
    if not needle and wanted is None:
        return set()
    types_match = (lambda n: wanted is not None and n.kind in wanted)
    text_fields = [f for f in fields if f != "type"] or ["key"]

    keep: set[int] = set()

    def hit(node: SnbtNode) -> bool:
        if types_match(node):
            return True
        if not needle:
            return False
        return any(_field_hit(node, needle, field, case_sensitive, exact)
                   for field in text_fields)

    def walk(node: SnbtNode, ancestors: list[int]) -> bool:
        matched = hit(node)
        descendants = [walk(child, ancestors + [id(node)])
                       for child in node.children]
        if matched or any(descendants):
            keep.add(id(node))
            keep.update(ancestors)
            return True
        return False

    walk(root, [])
    return keep


def _field_hit(node: SnbtNode, needle: str, field: str,
               case_sensitive: bool, exact: bool) -> bool:
    text = node_field_text(node, field)
    if not text:
        return False
    if exact:
        return text == needle if case_sensitive else text.lower() == needle.lower()
    return _contains_ignore_case(text, needle) if not case_sensitive \
        else needle in text


def find_nodes(root: SnbtNode, text: str, in_values: bool = True,
               in_keys: bool = True, case_sensitive: bool = False,
               exact: bool = False) -> list[SnbtNode]:
    """按关键字查找节点（供「查找下一个」使用）。

    取值按「简短取值文本」判断：容器用 `{ 3 项 }` / `[ a, b ]` 这种占位，
    标量用真实取值，数组还会把元素算进去（搜 `255` 能找到含该字节的数组）。
    """
    needle = text if case_sensitive else text.lower()
    out: list[SnbtNode] = []
    if not needle:
        return out
    for node in iter_nodes(root):
        haystacks: list[str] = []
        if in_keys and node.key:
            haystacks.append(str(node.key))
        if in_values:
            haystacks.append(format_value(node, preview=False))
        for hay in haystacks:
            probe = hay if case_sensitive else hay.lower()
            hit = probe == needle if exact else needle in probe
            if hit:
                out.append(node)
                break
    return out


# ---------------------------------------------------------------------------
# 节点路径
# ---------------------------------------------------------------------------

def node_path(node: SnbtNode, root: SnbtNode | None = None) -> str:
    """从根到节点的路径，列表元素用下标：`Data.Player.Inventory[0].id`。"""
    if node is root:
        return "(根)"
    trail = _trail(root, node) if root is not None else None
    if not trail:
        return node.key or "(根)"
    parts: list[str] = []
    for index, current in enumerate(trail):
        parent = trail[index - 1] if index > 0 else None
        if current.key:
            parts.append(current.key if not parts else f".{current.key}")
        elif parent is not None:
            try:
                position = parent.children.index(current)
            except ValueError:
                position = 0
            parts.append(f"[{position}]")
    return "".join(parts) or "(根)"


def _trail(root: SnbtNode, target: SnbtNode) -> list[SnbtNode]:
    """返回 [根, ..., 目标]；找不到返回空列表。"""
    if root is target:
        return [root]
    for child in root.children:
        found = _trail(child, target)
        if found:
            return [root, *found]
    return []


def node_trail(root: SnbtNode, target: SnbtNode) -> list[SnbtNode]:
    """从根到目标的节点链（含两端）；找不到返回空列表。"""
    return _trail(root, target)


def node_path_of(root: SnbtNode, node: SnbtNode) -> str:
    """`node` 相对 `root` 的路径（不依赖树视图）。"""
    trail = _trail(root, node)
    if not trail:
        return ""
    if len(trail) == 1:
        return ""
    parts: list[str] = []
    for index in range(1, len(trail)):
        current = trail[index]
        parent = trail[index - 1]
        if current.key is not None and parent.kind == SnbtKind.COMPOUND:
            quoted = quote_key(str(current.key))
            parts.append(quoted if not parts or quoted.startswith('"')
                         else f".{quoted}")
        else:
            parts.append(f"[{parent.index_of(current)}]")
    return "".join(parts)


def iter_nodes(root: SnbtNode) -> Iterator[SnbtNode]:
    """深度优先遍历（含根）。"""
    yield root
    for child in root.children:
        yield from iter_nodes(child)


def collect_strings(node: SnbtNode) -> list[SnbtNode]:
    """所有 String 节点（用于「替换全部」）。"""
    return [n for n in iter_nodes(node) if n.kind == SnbtKind.STRING]


def count_nodes(root: SnbtNode) -> int:
    return sum(1 for _ in iter_nodes(root))


def summarize(root: SnbtNode) -> str:
    """一行统计：`Compound · 42 个标签 · 最深 5 层`。"""
    depth = _max_depth(root, 1)
    return f"{type_label(root)} · {count_nodes(root) - 1} 个标签 · 最深 {depth} 层"


def _max_depth(node: SnbtNode, level: int) -> int:
    if not node.children:
        return level
    return max(_max_depth(c, level + 1) for c in node.children)


__all__ = [
    "TYPE_LABELS", "ARRAY_KINDS", "NUMBER_KINDS", "SUFFIXES", "CREATABLE",
    "DEFAULTS", "LIMITS", "KIND_ALIASES", "ValueParseError",
    "type_label", "type_label_kind", "is_array", "is_numeric",
    "default_value", "format_value", "format_array", "format_key",
    "format_scalar_inline", "COMPOUND_PREVIEW_ITEMS",
    "COMPOUND_PREVIEW_MAX_CHILDREN", "PRIORITY_PREVIEW_KEYS",
    "parse_value", "parse_int", "parse_float", "parse_bool",
    "parse_array_text", "array_element_kind", "array_prefix", "array_to_text",
    "bytes_to_hexdump", "node_path", "iter_nodes", "find_nodes",
    "collect_strings", "count_nodes", "summarize",
    # 类型约束与容器信息
    "array_length", "array_values", "element_kind", "can_hold", "is_descendant",
    "container_summary", "array_hexdump", "array_preview",
    "format_element", "parse_element", "parse_data_path", "path_segments",
    "quote_key", "match_segments", "node_field_text", "filter_nodes",
    "node_trail", "node_path_of",
]
