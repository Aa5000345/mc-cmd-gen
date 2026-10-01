"""二进制 NBT 读取 → SnbtNode。

支持 gzip / zlib / 未压缩三种容器，以及 .snbt 文本文件。
"""
from __future__ import annotations

import gzip
import io
import zlib
from pathlib import Path

from app.core.snbt import SnbtKind, SnbtNode, SnbtParseError, SnbtParser

try:
    import nbtlib
    from nbtlib import (
        Byte, Short, Int, Long, Float, Double, String,
        ByteArray, IntArray, LongArray, List as NbtList,
        Compound as NbtCompound,
    )
    _HAS_NBTLIB = True
except Exception:                                   # pragma: no cover
    _HAS_NBTLIB = False


class NbtFormatError(Exception):
    """NBT 读取失败。"""


# ---------------------------------------------------------------------------
# 容器格式探测 / 解压
# ---------------------------------------------------------------------------

def _detect_compression(data: bytes) -> str:
    """返回 gzip / zlib / none。

    未压缩的 NBT 首字节一定是 TAG_Compound(0x0A)，因此不能只认 0x0A 0x00 这一对
    字节——root_name 非空时第二字节不是 0x00。
    """
    if len(data) >= 2 and data[0] == 0x1F and data[1] == 0x8B:
        return "gzip"
    if len(data) >= 2 and data[0] == 0x78 and data[1] in (0x01, 0x5E, 0x9C, 0xDA):
        return "zlib"
    if data and data[0] == 0x0A:
        return "none"
    raise NbtFormatError(
        f"不是有效的 NBT 数据（前 8 字节：{data[:8].hex(' ')}）。"
        "支持 gzip / zlib / 未压缩三种格式；文本 NBT 请用 .snbt 扩展名。")


def _decompress(data: bytes, kind: str) -> bytes:
    if kind == "gzip":
        try:
            return gzip.decompress(data)
        except Exception as e:
            raise NbtFormatError(f"gzip 解压失败：{e}") from e
    if kind == "zlib":
        try:
            return zlib.decompress(data)
        except Exception as e:
            raise NbtFormatError(f"zlib 解压失败：{e}") from e
    if kind == "none":
        return data
    raise NbtFormatError(f"不支持的压缩格式：{kind}")


# ---------------------------------------------------------------------------
# tag → SnbtNode
# ---------------------------------------------------------------------------

def tag_to_node(tag, key: str | None = None) -> SnbtNode:
    """把 nbtlib tag 递归转换为 SnbtNode。

    tag 已经是 SnbtNode 时原样返回（`_root_from_file` 会为「根无名 + 根下
    只有一个子标签」这种退化表示手工造一个包装节点）。
    """
    if isinstance(tag, SnbtNode):
        return tag
    if isinstance(tag, NbtCompound):
        node = SnbtNode.new_compound(key=key)
        for k, v in tag.items():
            node.add_child(tag_to_node(v, key=str(k)))
        return node

    if isinstance(tag, NbtList):
        node = SnbtNode.new_list(key=key)
        for item in tag:
            node.add_child(tag_to_node(item, key=None))
        return node

    if isinstance(tag, ByteArray):
        node = SnbtNode(kind=SnbtKind.BYTE_ARRAY, key=key)
        for v in tag:
            node.add_child(SnbtNode.new_scalar(SnbtKind.BYTE, int(v)))
        return node

    if isinstance(tag, IntArray):
        node = SnbtNode(kind=SnbtKind.INT_ARRAY, key=key)
        for v in tag:
            node.add_child(SnbtNode.new_scalar(SnbtKind.INT, int(v)))
        return node

    if isinstance(tag, LongArray):
        node = SnbtNode(kind=SnbtKind.LONG_ARRAY, key=key)
        for v in tag:
            node.add_child(SnbtNode.new_scalar(SnbtKind.LONG, int(v)))
        return node

    if isinstance(tag, Byte):
        return SnbtNode.new_scalar(SnbtKind.BYTE, int(tag), key)
    if isinstance(tag, Short):
        return SnbtNode.new_scalar(SnbtKind.SHORT, int(tag), key)
    if isinstance(tag, Int):
        return SnbtNode.new_scalar(SnbtKind.INT, int(tag), key)
    if isinstance(tag, Long):
        return SnbtNode.new_scalar(SnbtKind.LONG, int(tag), key)
    if isinstance(tag, Float):
        return SnbtNode.new_scalar(SnbtKind.FLOAT, float(tag), key)
    if isinstance(tag, Double):
        return SnbtNode.new_scalar(SnbtKind.DOUBLE, float(tag), key)
    if isinstance(tag, String):
        return SnbtNode.new_scalar(SnbtKind.STRING, str(tag), key)

    return SnbtNode.new_scalar(SnbtKind.STRING, str(tag), key)


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def detect_compression(data: bytes) -> str:
    """返回 gzip / zlib / none（供界面显示源文件格式）。"""
    return _detect_compression(data)


def parse_nbt_bytes(data: bytes, with_meta: bool = False,
                    source: str = ""):
    """从字节解析 NBT（自动识别容器格式）。

    with_meta=True 时返回 (node, 元信息 dict)：保留根标签名与压缩方式，
    这样「打开 → 直接保存」不会悄悄改变 level.dat 等文件的格式。
    """
    if not _HAS_NBTLIB:
        raise NbtFormatError("未安装 nbtlib，请先执行 pip install nbtlib")
    if not data:
        raise NbtFormatError("数据为空。")

    kind = _detect_compression(data)
    raw = _decompress(data, kind)

    try:
        nbt_file = nbtlib.File.parse(io.BytesIO(raw), byteorder="big")
    except Exception as e:
        raise NbtFormatError(f"NBT 解析失败：{e}") from e

    if isinstance(nbt_file, dict):
        # 以字节里读到的根标签名为准；nbtlib 的 root_name 不可靠
        root, root_name = _root_from_file(nbt_file, raw)
    elif root is None:                                      # pragma: no cover
        root = nbt_file
    node = tag_to_node(root, key=None)
    if not with_meta:
        return node
    return node, {
        "compression": kind,
        "root_name": root_name,
        "source": source,
        "size": len(data),
    }


def _peek_root_name(raw: bytes) -> str | None:
    """直接从字节里读出根标签名。

    nbtlib 的 `File.root_name` 并不可靠（有名字的根也会报 ''），而「根标签
    有没有名字」决定了 File 到底代表哪一个 Compound，所以这里自己读一次：
    NBT 的头部固定是 `[tag:1][名字长度:2][名字:utf-8]`。
    """
    if len(raw) < 3:
        return None
    length = int.from_bytes(raw[1:3], "big")
    if length > len(raw) - 3:
        return None
    try:
        return raw[3:3 + length].decode("utf-8")
    except UnicodeDecodeError:                              # pragma: no cover
        return None


def _root_from_file(nbt_file, raw: bytes = b""):
    """从 nbtlib.File 里取出「根标签」与「根标签名」。

    nbtlib 的 `File` **总是**表示根 Compound：它的键就是根下的标签，
    而根标签名放在 `File.root_name` 里（这个属性并不可靠，所以从字节读）。
    但根标签**没有名字**时，nbtlib 的表示会退化成下面两种之一，必须还原：

    * `{"": 根 Compound}` —— 键就是根 Compound 本身；
    * `{子标签名: 值}`（根下只有一个子标签）—— 键其实是**子标签**，
      以前正是这里把第一项当成了根，于是同级的 `Data` 被整个丢掉。

    判断依据是**字节里的根标签名**：有名字 → File 就是根；没名字再按上面两种拆。
    """
    if not isinstance(nbt_file, dict):                      # pragma: no cover
        return nbt_file, ""

    root_name = _peek_root_name(raw)
    if root_name:
        return nbt_file, root_name
    if "" in nbt_file:
        return nbt_file[""], ""
    # 根无名 + 根下只有一个子标签：把那一项还原成子标签
    if len(nbt_file) == 1:
        key = next(iter(nbt_file))
        wrapper = SnbtNode.new_compound()
        wrapper.add_child(tag_to_node(nbt_file[key], key=str(key)))
        return wrapper, ""
    return nbt_file, ""


def parse_snbt_text(text: str, source: str = "<text>") -> SnbtNode:
    """解析 SNBT 文本。"""
    try:
        return SnbtParser(text).parse()
    except SnbtParseError as e:
        raise NbtFormatError(f"{source} 解析失败：{e}") from e


def load_nbt_file(path: str | Path, with_meta: bool = False):
    """读取二进制 NBT 文件；扩展名为 .snbt 时按文本解析。"""
    path = Path(path)
    if not path.exists():
        raise NbtFormatError(f"文件不存在：{path}")

    try:
        data = path.read_bytes()
    except PermissionError as e:
        raise NbtFormatError(f"文件被其它程序占用，无法读取：{path}") from e
    except OSError as e:
        raise NbtFormatError(f"文件读取失败：{e}") from e

    if path.suffix.lower() == ".snbt":
        node = parse_snbt_text(data.decode("utf-8", errors="replace"), str(path))
        if not with_meta:
            return node
        return node, {"compression": "none", "root_name": "",
                      "source": str(path), "size": len(data)}
    return parse_nbt_bytes(data, with_meta=with_meta, source=str(path))


__all__ = [
    "load_nbt_file", "parse_nbt_bytes", "parse_snbt_text", "tag_to_node",
    "detect_compression", "NbtFormatError",
]