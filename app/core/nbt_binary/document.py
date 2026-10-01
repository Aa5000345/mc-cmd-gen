"""NBT 文档模型：把「一棵 NBT + 它来自哪个文件 + 格式」绑在一起。

这样「打开 → 保存」默认按原格式写回，不会把 level.dat（未压缩）悄悄变成
gzip，也不会丢掉根标签名。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core.nbt_binary.reader import (
    NbtFormatError, detect_compression, load_nbt_file,
)
from app.core.nbt_binary.writer import NbtWriteError, save_nbt_file
from app.core.snbt import SnbtNode

# 常见的 NBT 文件后缀（打开对话框与拖放都认这些）
SUFFIXES: tuple[str, ...] = (
    ".dat", ".nbt", ".snbt", ".dat_old", ".mcstructure", ".schem",
    ".schematic", ".nbt.gz",
)

COMPRESSION_LABELS = {
    "gzip": "gzip 压缩",
    "zlib": "zlib 压缩",
    "none": "未压缩",
}


@dataclass
class NbtDocument:
    """一个打开的 NBT 文件。"""

    root: SnbtNode = field(default_factory=SnbtNode.new_compound)
    path: Path | None = None
    root_name: str = ""
    #: 展示用名字。绑定「来自存档但尚未写回」的树时用它显示 level.dat 之类的
    #: 来源名，同时 path 保持为空 —— 这样「保存」绝不可能是直接覆盖存档。
    display_name: str = ""
    # 新文档默认 gzip（原版 .dat/.nbt 的常见格式）；打开已有文件时按源格式覆盖，
    # 这样「打开 → 保存」不会悄悄改变格式。
    compression: str = "gzip"
    dirty: bool = False
    source_size: int = 0

    # ---- 属性 ----

    @property
    def name(self) -> str:
        if self.path:
            return self.path.name
        return self.display_name or "未命名"

    @property
    def suffix(self) -> str:
        return (self.path.suffix.lower() if self.path else "")

    @property
    def is_text(self) -> bool:
        return self.suffix == ".snbt"

    @property
    def compression_label(self) -> str:
        return COMPRESSION_LABELS.get(self.compression, self.compression)

    def display_title(self) -> str:
        mark = " *" if self.dirty else ""
        if self.path is None:
            return f"{self.display_name or '未命名'}{mark}"
        return f"{self.path.name}{mark}"

    # ---- 读写 ----

    @classmethod
    def load(cls, path: str | Path) -> "NbtDocument":
        node, meta = load_nbt_file(path, with_meta=True)
        return cls(root=node,
                   path=Path(path),
                   root_name=str(meta.get("root_name", "")),
                   compression=str(meta.get("compression", "none")),
                   source_size=int(meta.get("size", 0) or 0))

    @classmethod
    def from_snbt(cls, text: str, name: str = "未命名") -> "NbtDocument":
        from app.core.snbt import SnbtParser
        return cls(root=SnbtParser(text).parse(),
                   path=None, root_name="", compression="none")

    def save(self, path: str | Path | None = None,
             compression: str | None = None) -> Path:
        """写回文件；默认沿用原路径与原始格式。"""
        target = Path(path) if path else self.path
        if target is None:
            raise NbtWriteError("尚未指定保存路径")
        kind = compression
        if kind is None:
            kind = "none" if target.suffix.lower() == ".snbt" else self.compression
        save_nbt_file(self.root, target, compress=kind,
                      root_name=self.root_name)
        self.path = target
        self.compression = "none" if target.suffix.lower() == ".snbt" else kind
        self.dirty = False
        try:
            self.source_size = target.stat().st_size
        except OSError:
            self.source_size = 0
        return target

    def info_line(self) -> str:
        parts = [self.name]
        if self.path is not None:
            parts.append(self.compression_label)
            if self.root_name:
                parts.append(f'根标签 "{self.root_name}"')
            if self.source_size:
                size = self.source_size
                for unit in ("B", "KB", "MB"):
                    if size < 1024 or unit == "MB":
                        parts.append(f"{size:.1f} {unit}" if unit != "B"
                                     else f"{size} B")
                        break
                    size /= 1024.0
        else:
            parts.append("尚未保存")
        if self.dirty:
            parts.append("有未保存的修改")
        return " · ".join(parts)

    def clone_meta(self) -> dict:
        """复制「来源信息」（压缩格式 / 根标签名 / 体积 / 展示名）。"""
        return {"root_name": self.root_name, "compression": self.compression,
                "source_size": self.source_size,
                "display_name": self.display_name}


def guess_compression(data: bytes) -> str:
    """给原始字节猜压缩方式（供外部工具使用）。"""
    try:
        return detect_compression(data)
    except NbtFormatError:
        return "none"


__all__ = ["NbtDocument", "SUFFIXES", "COMPRESSION_LABELS",
           "guess_compression"]
