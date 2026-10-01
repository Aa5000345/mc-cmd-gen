"""看一个 NBT 文件里到底有什么（不依赖 GUI）。

用法：
    python tools/inspect_nbt.py <文件> [--depth 3] [--json]

会打印压缩格式、根标签名、根下每个标签的结构与条目数，
用来确认「某个标签是不是真的不在文件里」。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows 默认控制台是 cp936，直接 print 中文会变成乱码，
# 而管道读取方（测试、编辑器）按 UTF-8 解码。统一强制 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):                # pragma: no cover
        pass

from app.core.nbt_binary import NbtFormatError, load_nbt_file  # noqa: E402
from app.core.snbt import SnbtKind, SnbtNode                    # noqa: E402

TYPE_NAMES = {
    SnbtKind.BYTE: "Byte", SnbtKind.SHORT: "Short", SnbtKind.INT: "Int",
    SnbtKind.LONG: "Long", SnbtKind.FLOAT: "Float", SnbtKind.DOUBLE: "Double",
    SnbtKind.STRING: "String", SnbtKind.BOOL: "Bool",
    SnbtKind.LIST: "List", SnbtKind.COMPOUND: "Compound",
    SnbtKind.BYTE_ARRAY: "ByteArray", SnbtKind.INT_ARRAY: "IntArray",
    SnbtKind.LONG_ARRAY: "LongArray",
}


def scalar_text(node: SnbtNode, limit: int = 40) -> str:
    if node.kind == SnbtKind.STRING:
        text = str(node.value)
        return text if len(text) <= limit else text[:limit] + "…"
    if node.kind == SnbtKind.BOOL:
        return "1b" if node.value else "0b"
    return str(node.value)


def describe(node: SnbtNode) -> str:
    kind = TYPE_NAMES.get(node.kind, str(node.kind))
    if node.is_container:
        return f"{kind}（{len(node.children)} 项）"
    return f"{kind} = {scalar_text(node)}"


def walk(node: SnbtNode, prefix: str, depth: int, max_depth: int,
         out: list[str]) -> None:
    if depth > max_depth:
        return
    pad = "  " * depth
    for index, child in enumerate(node.children):
        key = child.key if child.key is not None else f"[{index}]"
        out.append(f"{pad}{key}  <{describe(child)}>")
        if child.is_container and depth < max_depth:
            walk(child, f"{prefix}.{key}", depth + 1, max_depth, out)


def structure(node: SnbtNode, depth: int, max_depth: int) -> list[str]:
    out: list[str] = []
    for index, child in enumerate(node.children):
        key = child.key if child.key is not None else f"[{index}]"
        out.append(f"{key}  <{describe(child)}>")
        if child.is_container and depth < max_depth:
            walk(child, key, 1, max_depth, out)
    return out


def to_json(node: SnbtNode, depth: int, max_depth: int) -> dict:
    def node_json(item: SnbtNode, level: int) -> dict:
        data = {"type": TYPE_NAMES.get(item.kind, str(item.kind))}
        if item.is_container:
            data["count"] = len(item.children)
            if level < max_depth:
                data["children"] = [
                    {"key": c.key, **node_json(c, level + 1)}
                    for c in item.children
                ]
        else:
            data["value"] = scalar_text(item, 200)
        return data

    return {"root": node_json(node, depth)}


def main() -> int:
    parser = argparse.ArgumentParser(description="查看 NBT 文件里到底有什么")
    parser.add_argument("path", help="NBT / .dat / .snbt 文件")
    parser.add_argument("--depth", type=int, default=2,
                        help="展开层数（默认 2）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    target = Path(args.path)
    if not target.exists():
        print(f"文件不存在：{target}", file=sys.stderr)
        return 2
    try:
        node, meta = load_nbt_file(target, with_meta=True)
    except NbtFormatError as exc:
        print(f"不是合法的 NBT：{exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({
            "file": str(target),
            "size": target.stat().st_size,
            "compression": meta.get("compression"),
            "root_name": meta.get("root_name", ""),
            **to_json(node, 0, args.depth),
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"文件　　：{target}")
    print(f"大小　　：{target.stat().st_size} 字节")
    print(f"压缩格式：{meta.get('compression')}")
    print(f"根标签名：{meta.get('root_name')!r}"
          f"{'（空，世界存档通常是这样）' if not meta.get('root_name') else ''}")
    print(f"根类型　：{TYPE_NAMES.get(node.kind, node.kind)}"
          f"，共 {len(node.children)} 个直接子标签")
    print("-" * 60)
    for line in structure(node, 0, args.depth):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
