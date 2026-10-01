"""项目品牌常量（改名后的唯一事实来源）。

改名背景
--------
这个工具最初只做「命令生成」，现在覆盖 **命令生成 + NBT 编辑 + 世界存档编辑
+ 玩家数据编辑 + 数据包/地图数据**，所以名字里必须同时体现「Minecraft Java
命令」与「世界 / NBT 编辑器」两个定位。

候选名称（详见 README「项目改名」一节）：

1. **MCStudio（MC 工坊）** —— 本次采用
2. MCForge（MC 锻造台）
3. MCWorldsmith（MC 世界工匠）
4. CraftConsole（MC 世界控制台）
5. MCBlockBench（MC 方块工作台）

兼容策略
--------
* Python 包名仍是 `app`，环境变量 `MCCMDGEN_DATA_DIR` 与命令行
  `mcgen.py` / `mc-cmd-gen` 全部保留，旧脚本不会失效；
* 旧数据目录 `MCCmdGen` 会被自动迁移到新目录（见 app/migration.py）；
* 导出的数据包 / 配方包继续接受旧的 `generator: "MCCmdGen"` 标识，
  生成的 `.mcfunction` 注释头里也会同时写出新旧两个名字。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 品牌
# ---------------------------------------------------------------------------

#: 显示名（英文 / 国际）
APP_NAME = "MCStudio"

#: 显示名（中文全称，用于窗口标题 / 关于页 / README 标题）
APP_NAME_FULL = "MCStudio · Minecraft Java 命令与世界编辑器"

#: 简短中文名
APP_NAME_CN = "MC 工坊"

#: 目录名 / pip 分发名用的 slug
APP_SLUG = "mcstudio"

#: 命令行工具名（新旧并存）
CLI_NAME = "mcstudio"
CLI_NAME_LEGACY = "mc-cmd-gen"

#: QApplication 用
APP_ID = "MCStudio"
ORG_NAME = "MCStudio"

VERSION = "2.1.0"

#: 目标 Minecraft 版本
TARGET_MC_VERSION = "1.21.1"

#: 生成物里写入的 generator 标识（新）
GENERATOR = "MCStudio"
#: 仍然接受的历史 generator 标识（旧数据不失效）
GENERATOR_ALIASES = ("MCStudio", "MCCmdGen", "mc-command-generator")

#: 项目主页 / 仓库（仅在关于页展示）
HOMEPAGE = "https://github.com/local/mcstudio"


def window_title(suffix: str = "") -> str:
    """主窗口标题；suffix 用于追加当前文件名。"""
    base = f"{APP_NAME} — Minecraft Java {TARGET_MC_VERSION} 命令与世界编辑器"
    return f"{base} — {suffix}" if suffix else base


def about_text() -> str:
    return (
        f"{APP_NAME}（{APP_NAME_CN}）\n"
        f"{APP_NAME_FULL}\n"
        f"版本：{VERSION}　目标版本：Minecraft Java {TARGET_MC_VERSION}\n"
        "技术栈：Python + PySide6\n\n"
        "能力范围：命令生成（/summon /give /execute …）、NBT 文件编辑、\n"
        "世界存档编辑（level.dat / playerdata / 实体 / 容器 / 地图 / 区域索引）、\n"
        "数据包与地图数据导出。\n\n"
        "历史 / 收藏 / 片段 / 草稿 / 窗口布局保存在应用数据目录的 data.db。\n"
        f"旧名 MCCmdGen 的数据会在首次启动时自动迁移（原目录保留不删除）。"
    )


# ---------------------------------------------------------------------------
# 数据目录
# ---------------------------------------------------------------------------

#: 新的应用数据目录名
APP_DIR_NAME = APP_SLUG

#: 旧的应用数据目录名（只读兼容 / 自动迁移来源）
LEGACY_APP_DIR_NAMES: tuple[str, ...] = ("MCCmdGen",)

#: 显式指定数据目录的环境变量（新名优先，旧名保留兼容）
DATA_DIR_ENV = "MCSTUDIO_DATA_DIR"
LEGACY_DATA_DIR_ENVS: tuple[str, ...] = ("MCCMDGEN_DATA_DIR",)

#: 设为 1 / true 时不自动迁移旧数据目录
NO_MIGRATE_ENV = "MCSTUDIO_NO_MIGRATE"


def data_dir_override() -> Path | None:
    """读取环境变量指定的数据目录（测试与便携版用）。"""
    for name in (DATA_DIR_ENV, *LEGACY_DATA_DIR_ENVS):
        raw = os.environ.get(name, "").strip()
        if raw:
            return Path(raw).expanduser()
    return None


def skip_migration() -> bool:
    return os.environ.get(NO_MIGRATE_ENV, "").strip().lower() in (
        "1", "true", "yes", "on")


def platform_data_dir(name: str) -> Path:
    """按平台约定给出数据目录（不创建、不迁移）。"""
    if sys.platform.startswith("win"):
        base = (os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
                or (Path.home() / "AppData" / "Local"))
        return Path(base) / name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / name
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / name


def legacy_data_dirs() -> list[Path]:
    """所有历史数据目录（存在与否都返回，调用方自己判断）。"""
    return [platform_data_dir(n) for n in LEGACY_APP_DIR_NAMES]


# ---------------------------------------------------------------------------
# 改名影响说明（GUI「关于」与 CLI `--rename-info` 共用）
# ---------------------------------------------------------------------------

RENAME_CANDIDATES: tuple[tuple[str, str, str], ...] = (
    ("MCStudio", "MC 工坊",
     "命令 + 世界/NBT 一体的「工坊」，短、好记、跨语言无歧义。★ 本次采用"),
    ("MCForge", "MC 锻造台",
     "强调「打造命令与存档」，但与 Forge / NeoForge 模组加载器撞名，检索困难。"),
    ("MCWorldsmith", "MC 世界工匠",
     "最直白地表达「世界编辑器」，但偏长，且不像命令生成器。"),
    ("CraftConsole", "世界控制台",
     "强调控制台式命令 + 存档控制，但不如 MC 前缀直观。"),
    ("MCBlockBench", "方块工作台",
     "突出方块/NBT 编辑，但 Blockbench 已是一个知名 3D 建模软件。"),
)

RENAME_IMPACT: tuple[tuple[str, str], ...] = (
    ("窗口标题 / 关于页 / 状态栏", f"统一显示 {APP_NAME}（{APP_NAME_CN}）"),
    ("pyproject.toml", f"name = \"{APP_SLUG}\"，脚本入口 {CLI_NAME}（保留 {CLI_NAME_LEGACY}）"),
    ("README.md", "标题、目录结构、命令行示例、测试说明全部更新，并新增改名章节"),
    ("CLI 帮助", f"`python mcgen.py --version` 显示 {APP_NAME}；`mcgen.py` 与 "
                f"`{CLI_NAME}.py` 等价"),
    ("安装包名", f"{APP_SLUG}（旧的 mc-command-generator 仍可 `pip install -e .`）"),
    ("应用数据目录",
     f"新：%LOCALAPPDATA%\\{APP_DIR_NAME}\\　旧：%LOCALAPPDATA%\\MCCmdGen\\（自动迁移，原目录保留）"),
    ("数据包 generator 标识", f"写 \"{GENERATOR}\"，读取时兼容 \"MCCmdGen\""),
)


def rename_report() -> str:
    """人类可读的改名说明（CLI 与文档共用）。"""
    lines = [f"{APP_NAME}（{APP_NAME_CN}）— 项目改名说明", "=" * 60, "", "候选名称："]
    for index, (name, cn, note) in enumerate(RENAME_CANDIDATES, 1):
        lines.append(f"  {index}. {name}（{cn}）：{note}")
    lines += ["", "改名影响与处理："]
    for item, detail in RENAME_IMPACT:
        lines.append(f"  · {item}：{detail}")
    lines += [
        "",
        "兼容与迁移：",
        "  · Python 导入路径不变（app.*），现有脚本与测试无需改动；",
        "  · 保留 mcgen.py 兼容入口，旧命令行用法继续可用；",
        "  · 首次启动自动把 MCCmdGen 数据目录里的 data.db / user_data / scenes "
        "/ update_source.txt 复制到新目录（原目录保留，可手动删除）；",
        "  · 旧的导出文件（generator 为 MCCmdGen）仍可导入；",
        "  · 设 MCSTUDIO_NO_MIGRATE=1 可关闭自动迁移。",
    ]
    return "\n".join(lines)


__all__ = [
    "APP_NAME", "APP_NAME_FULL", "APP_NAME_CN", "APP_SLUG", "APP_ID",
    "ORG_NAME", "VERSION", "TARGET_MC_VERSION", "CLI_NAME", "CLI_NAME_LEGACY",
    "GENERATOR", "GENERATOR_ALIASES", "HOMEPAGE", "APP_DIR_NAME",
    "LEGACY_APP_DIR_NAMES", "DATA_DIR_ENV", "LEGACY_DATA_DIR_ENVS",
    "NO_MIGRATE_ENV", "RENAME_CANDIDATES", "RENAME_IMPACT",
    "window_title", "about_text", "rename_report", "data_dir_override",
    "skip_migration", "platform_data_dir", "legacy_data_dirs",
]
