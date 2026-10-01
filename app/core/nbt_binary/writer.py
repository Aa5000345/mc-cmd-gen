"""SnbtNode → 二进制 NBT 写入。

修正点：
* nbtlib 2.x 的 ``File.write(fileobj, byteorder)`` 需要文件对象（原来是写 bytes，必然报错）；
* 列表元素类型会按第一个元素统一，其余元素做安全降级，避免写到一半抛异常；
* 支持选择根标签名与压缩方式（gzip / zlib / none）。
"""
from __future__ import annotations

import gzip
import io
import zlib
from pathlib import Path

from app.core.snbt import SnbtKind, SnbtNode

try:
    from nbtlib import (
        Byte, Short, Int, Long, Float, Double, String,
        ByteArray, IntArray, LongArray, List as NbtList,
        Compound as NbtCompound,
    )
    _HAS_NBTLIB = True
except Exception:                                   # pragma: no cover
    _HAS_NBTLIB = False


class NbtWriteError(Exception):
    """NBT 写入失败。"""


# ---------------------------------------------------------------------------
# SnbtNode → tag
# ---------------------------------------------------------------------------

_INT_KINDS = (SnbtKind.BYTE, SnbtKind.SHORT, SnbtKind.INT,
              SnbtKind.LONG, SnbtKind.BOOL)
_FLOAT_KINDS = (SnbtKind.FLOAT, SnbtKind.DOUBLE)


def node_to_tag(node: SnbtNode):
    """把 SnbtNode 转换为 nbtlib tag。"""
    k = node.kind

    if k == SnbtKind.COMPOUND:
        tag = NbtCompound()
        for c in node.children:
            if c.key is None:
                continue
            tag[str(c.key)] = node_to_tag(c)
        return tag

    if k == SnbtKind.LIST:
        if not node.children:
            return NbtList[String]([])
        items = [node_to_tag(c) for c in node.children]
        elem_cls = _common_list_type(items)
        if elem_cls is None:
            # 类型无法统一（如 compound 与 int 混排）→ 全部转字符串，保证可写
            return NbtList[String]([_stringify(t) for t in items])
        try:
            # 统一元素类型：Byte/Int/Float 混合时向上转换（如 Int → Double）
            return NbtList[elem_cls]([_coerce(t, elem_cls) for t in items])
        except Exception as e:
            raise NbtWriteError(f"列表元素类型不一致，无法写入：{e}") from e

    if k == SnbtKind.INT_ARRAY:
        return IntArray([_as_signed(_as_int(c), 32) for c in node.children])
    if k == SnbtKind.LONG_ARRAY:
        return LongArray([_as_signed(_as_int(c), 64) for c in node.children])
    if k == SnbtKind.BYTE_ARRAY:
        return ByteArray([_as_byte(_as_int(c)) for c in node.children])

    if k == SnbtKind.STRING:
        return String(str(node.value))
    if k == SnbtKind.BOOL:
        return Byte(1 if node.value else 0)
    if k == SnbtKind.BYTE:
        return Byte(_as_byte(_as_int(node)))
    if k == SnbtKind.SHORT:
        return Short(_as_int(node))
    if k == SnbtKind.INT:
        return Int(_as_int(node))
    if k == SnbtKind.LONG:
        return Long(_as_int(node))
    if k == SnbtKind.FLOAT:
        return Float(float(node.value))
    if k == SnbtKind.DOUBLE:
        return Double(float(node.value))

    return String(str(node.value))


def _as_int(node: SnbtNode) -> int:
    try:
        return int(node.value)
    except (TypeError, ValueError) as e:
        raise NbtWriteError(f"'{node.value}' 不是合法整数") from e


def _as_byte(value: int) -> int:
    """把整数折进 NBT 的 int8 范围。

    字节数组在十六进制视图里很自然会被写成 00~FF，但 NBT 存的是**有符号**字节
    （255 与 -1 是同一颗字节）。这里统一折算，避免 nbtlib 抛 OverflowError。
    """
    return ((int(value) + 128) % 256) - 128


def _as_signed(value: int, bits: int) -> int:
    """把整数折进指定位宽的有符号范围（用于 IntArray / LongArray）。"""
    return ((int(value) + (1 << (bits - 1))) % (1 << bits)) - (1 << (bits - 1))


def _common_list_type(items: list):
    """返回列表可用的统一元素类型；无法统一时返回 None。"""
    classes = {type(t) for t in items}
    if len(classes) == 1:
        return classes.pop()
    if classes <= {Byte, Short, Int, Long}:
        return Long
    if classes <= {Byte, Short, Int, Long, Float, Double}:
        return Double
    if classes == {NbtList}:
        return NbtList
    return None


def _coerce(tag, elem_cls):
    """把 tag 转换成列表要求的元素类型（必要时向上转换数值类型）。"""
    if isinstance(tag, elem_cls):
        return tag
    if elem_cls in (Byte, Short, Int, Long):
        return elem_cls(int(tag))
    if elem_cls in (Float, Double):
        return elem_cls(float(tag))
    if elem_cls is String:
        return String(_stringify(tag))
    return elem_cls(tag)


def _stringify(tag) -> str:
    if isinstance(tag, String):
        return str(tag)
    if isinstance(tag, NbtCompound):
        from app.core.snbt import serialize
        from app.core.nbt_binary.reader import tag_to_node
        return serialize(tag_to_node(tag), pretty=False)
    return str(tag)


# ---------------------------------------------------------------------------
# 压缩
# ---------------------------------------------------------------------------

def _compress(raw: bytes, kind: str) -> bytes:
    if kind == "gzip":
        return gzip.compress(raw)
    if kind == "zlib":
        return zlib.compress(raw)
    if kind == "none":
        return raw
    raise NbtWriteError(f"不支持的压缩格式：{kind}")


def node_to_nbt_bytes(root: SnbtNode, compress: str = "gzip",
                      root_name: str = "") -> bytes:
    """把 SnbtNode 序列化成 NBT 字节（含容器压缩）。

    根标签名要**原样**写进文件头：`nbtlib.File({name: tag})` 不管名字是什么
    都会写成「无名根 + 一个同名子标签」，读回来就多一层、或者把根下的其它
    标签挤掉，所以这里自己写文件头，再让 Compound 序列化内容。

        无名根（世界存档）：`0A 00 00` + 根 Compound 的内容
        有名根（`root_name="hello"`）：`0A 00 05 hello` + 根 Compound 的内容
    """
    if not _HAS_NBTLIB:
        raise NbtWriteError("未安装 nbtlib，请先执行 pip install nbtlib")

    if root.kind != SnbtKind.COMPOUND:
        # 顶层不是 Compound（例如直接打开一段 SNBT）：包一层，名字用给定的根名
        wrapper = SnbtNode.new_compound()
        child = root.clone()
        child.key = root_name or "value"
        wrapper.add_child(child)
        root = wrapper
        root_name = ""

    tag = node_to_tag(root)
    name_bytes = str(root_name or "").encode("utf-8")
    if len(name_bytes) > 0xFFFF:
        raise NbtWriteError("根标签名过长（NBT 的名字最长 65535 字节）")

    try:
        body = io.BytesIO()
        tag.write(body, byteorder="big")
        header = bytes([0x0A]) + len(name_bytes).to_bytes(2, "big") + name_bytes
        raw = header + body.getvalue()
    except NbtWriteError:
        raise
    except Exception as e:
        raise NbtWriteError(f"NBT 序列化失败：{e}") from e

    return _compress(raw, compress)


def save_nbt_file(root: SnbtNode, path: str | Path,
                  compress: str = "gzip",
                  root_name: str = "") -> Path:
    """把 SnbtNode 写入二进制 NBT 文件。

    path 以 .snbt 结尾时写文本 SNBT（UTF-8，不压缩）。
    compress 默认 gzip；需要保持源文件格式时由调用方显式传入
    （见 app/core/nbt_binary/document.py 的 NbtDocument）。
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise NbtWriteError(f"无法创建目录：{e}") from e

    if path.suffix.lower() == ".snbt":
        from app.core.snbt import serialize
        try:
            path.write_text(serialize(root, pretty=True), encoding="utf-8")
        except OSError as e:
            raise NbtWriteError(f"文件写入失败：{e}") from e
        return path

    data = node_to_nbt_bytes(root, compress=compress, root_name=root_name)
    try:
        path.write_bytes(data)
    except OSError as e:
        raise NbtWriteError(f"文件写入失败：{e}") from e
    return path


__all__ = [
    "save_nbt_file", "node_to_nbt_bytes", "node_to_tag", "NbtWriteError",
]
