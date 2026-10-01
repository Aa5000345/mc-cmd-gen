"""二进制 NBT 读写（gzip / zlib / 未压缩 / 文本 SNBT）+ 文档模型。"""
from app.core.nbt_binary.reader import (
    NbtFormatError, detect_compression, load_nbt_file, parse_nbt_bytes,
    parse_snbt_text, tag_to_node,
)
from app.core.nbt_binary.writer import (
    NbtWriteError, node_to_nbt_bytes, node_to_tag, save_nbt_file,
)
from app.core.nbt_binary.document import (
    COMPRESSION_LABELS, SUFFIXES, NbtDocument, guess_compression,
)

__all__ = [
    "load_nbt_file", "save_nbt_file", "parse_nbt_bytes", "parse_snbt_text",
    "node_to_nbt_bytes", "node_to_tag", "tag_to_node", "detect_compression",
    "NbtFormatError", "NbtWriteError",
    "NbtDocument", "SUFFIXES", "COMPRESSION_LABELS", "guess_compression",
]
