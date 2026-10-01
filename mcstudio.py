#!/usr/bin/env python3
"""MCStudio 命令行入口（新名字）。

与 `mcgen.py` **完全等价** —— 两者只是名字不同，方便：

* 老脚本继续用 `python mcgen.py …`
* 新脚本用 `python mcstudio.py …` 或安装后的 `mcstudio` / `mc-cmd-gen`

用法示例：
    python mcstudio.py list
    python mcstudio.py give --item diamond_sword -c 'minecraft:custom_name="神剑"'
    python mcstudio.py world info "…/.minecraft/saves/世界"
    python mcstudio.py world edit "…/saves/世界" DayTime=1000 --dry-run
    python mcstudio.py world health "…/saves/世界"
    python mcstudio.py world commands "…/saves/世界" -o items.mcfunction
    python mcstudio.py world nlp "…/saves/世界" "把时间设置为夜晚"
    python mcstudio.py rename-info
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcgen import build_parser, main  # noqa: E402  （同一个实现）

__all__ = ["main", "build_parser"]


if __name__ == "__main__":
    raise SystemExit(main())
