#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载生成内置数据所需的官方原始数据（写入 build/mcdata/）。

数据源：
    * PrismarineJS/minecraft-data —— 1.21.1 的物品 / 方块 / 实体 / 附魔 / 食物
    * Misode/mcmeta 1.21.1-assets —— 官方 zh_cn.json（中文名）
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "mcdata"

MC_DATA = "https://raw.githubusercontent.com/PrismarineJS/minecraft-data/master/data/pc/1.21.1/"
FILES = (
    "items.json", "blocks.json", "foods.json", "materials.json",
    "enchantments.json",
)
ENTITIES_FALLBACKS = (
    "https://raw.githubusercontent.com/PrismarineJS/minecraft-data/master/data/pc/1.20.5/entities.json",
    "https://raw.githubusercontent.com/PrismarineJS/minecraft-data/master/data/pc/1.21/entities.json",
)
LANG_URLS = (
    "https://raw.githubusercontent.com/Misode/mcmeta/1.21.1-assets/assets/minecraft/lang/zh_cn.json",
    "https://raw.githubusercontent.com/InventivetalentDev/minecraft-assets/1.21.1/assets/minecraft/lang/zh_cn.json",
)


def fetch(url: str):
    with urllib.request.urlopen(url, timeout=40) as response:
        return json.loads(response.read().decode("utf-8"))


def save(name: str, data) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False),
                            encoding="utf-8")
    size = (OUT / name).stat().st_size / 1024
    count = len(data) if hasattr(data, "__len__") else "-"
    print(f"  {name:22} {size:8.1f} KB  {count}")


def main() -> int:
    print(f"下载到 {OUT}")
    ok = 0
    for name in FILES:
        try:
            save(name, fetch(MC_DATA + name))
            ok += 1
        except Exception as exc:
            print(f"  {name:22} 失败：{exc}")

    for url in ENTITIES_FALLBACKS:
        try:
            save("entities.json", fetch(url))
            print(f"    （来源 {url}）")
            ok += 1
            break
        except Exception as exc:
            print(f"  entities.json 尝试失败：{exc}")

    for url in LANG_URLS:
        try:
            save("zh_cn.json", fetch(url))
            print(f"    （来源 {url}）")
            ok += 1
            break
        except Exception as exc:
            print(f"  zh_cn.json 尝试失败：{exc}")

    print(f"完成，成功 {ok} 个文件。下一步：python tools/build_registry.py")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
