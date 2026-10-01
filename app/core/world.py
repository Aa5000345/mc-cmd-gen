"""Minecraft Java 世界存档的读取 / 编辑核心层。

支持范围（1.21.1，兼容更早版本只读）
------------------------------------
* 打开 `.minecraft/saves/世界名`、启动器版本隔离目录（`versions/<v>/saves/…`）
  以及任意直接指向存档目录的路径；
* 自动识别 `level.dat` / `level.dat_old` / `playerdata/` / `stats/` /
  `advancements/` / `data/`（含 `map_*.dat`）/ `region/` / `entities/` /
  `poi/` / `DIM1/` / `DIM-1/` / `datapacks/`；
* 世界总览（名称 / DataVersion / 版本 / 种子 / 难度 / 允许命令 / 游戏规则 /
  时间 / 天气 / 出生点 / 世界边界 / 上次游玩 / 数据包 / 玩家列表）；
* `level.dat`、`playerdata/*.dat` 的结构化编辑（字段路径 + 类型安全赋值）；
* 容器 / 方块实体 / 实体 / 地图物品的读取与编辑；
* 区域文件只读索引（`region/*.mca` 的区块位置表，不整块读入）。

安全约定（非常重要）
--------------------
1. 打开存档默认 **只读**，必须显式 `unlock()` 才能进入可编辑状态；
2. 任何写入都走 `WorldTransaction`：备份 → 内存模型 → 校验 → dry-run 差异
   → 原子替换 → 可回滚；
3. `level.dat` 写回时保留原始压缩方式与根标签名；
4. 备份落在 `backups/<时间戳>/`，不覆盖用户存档的历史备份。

本模块不依赖 Qt，可被 CLI / 测试直接使用。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
import gzip
import tempfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

from app.core.nbt_binary import (
    NbtDocument, NbtFormatError, NbtWriteError, save_nbt_file,
)
from app.core.snbt import SnbtKind, SnbtNode, serialize

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 目标 Minecraft 版本
TARGET_VERSION = "1.21.1"

#: 已知 DataVersion 区间（用于兼容性判断）
#: 1.21.1 → 3955；1.21 → 3953；1.20.6 → 3839；1.20.4 → 3700；
#: 1.20.1 → 3465；1.19.4 → 3337；1.18.2 → 2975；1.16.5 → 2586
KNOWN_DATA_VERSIONS: dict[int, str] = {
    3955: "1.21.1",
    3953: "1.21",
    3839: "1.20.6",
    3837: "1.20.5",
    3700: "1.20.4",
    3578: "1.20.2",
    3465: "1.20.1",
    3463: "1.20",
    3337: "1.19.4",
    3218: "1.19.3",
    3120: "1.19.1",
    3105: "1.19",
    3086: "1.18.2",
    2975: "1.18.2",
    2865: "1.18.1",
    2860: "1.18",
    2730: "1.17.1",
    2724: "1.17",
    2586: "1.16.5",
    2584: "1.16.4",
    2566: "1.16.2",
    2230: "1.15.2",
    1976: "1.14.4",
    1631: "1.13.2",
    1519: "1.13",
}

#: 1.21.1 可写的 DataVersion 窗口（更早的只给只读建议）
SUPPORTED_DATA_VERSION_MIN = 3465      # 1.20.1
SUPPORTED_DATA_VERSION_MAX = 3955      # 1.21.1

#: 直接在存档根下、需要展示的条目
SAVE_ENTRIES: tuple[str, ...] = (
    "level.dat", "level.dat_old", "playerdata", "stats", "advancements",
    "data", "region", "entities", "poi", "DIM1", "DIM-1", "datapacks",
    "session.lock", "icon.png", "levelname.txt",
)

#: 维度目录 → 显示名
DIMENSIONS: dict[str, str] = {
    "": "minecraft:overworld",
    "DIM1": "minecraft:the_end",
    "DIM-1": "minecraft:the_nether",
}

#: 难度
DIFFICULTIES = {0: "peaceful", 1: "easy", 2: "normal", 3: "hard"}

#: level.dat 里可以直接编辑、带中文说明的字段
#: (键, 显示名, 类型, 取值范围说明)
LEVEL_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("LevelName", "存档名称", "String", "任意文本"),
    ("GameType", "默认游戏模式", "Int", "0 生存 / 1 创造 / 2 冒险 / 3 旁观"),
    ("Difficulty", "难度", "Byte", "0 和平 / 1 简单 / 2 普通 / 3 困难"),
    ("DifficultyLocked", "锁定难度", "Byte", "0 / 1"),
    ("allowCommands", "允许命令", "Byte", "0 / 1"),
    ("hardcore", "极限模式", "Byte", "0 / 1"),
    ("DayTime", "白天时间", "Long", "0 起，24000 一整天（会推进日夜）"),
    ("Time", "世界时间", "Long", "只增不减的世界刻数"),
    ("raining", "正在下雨", "Byte", "0 / 1"),
    ("rainTime", "剩余下雨刻数", "Int", "≥0"),
    ("thundering", "正在雷暴", "Byte", "0 / 1"),
    ("thunderTime", "剩余雷暴刻数", "Int", "≥0"),
    ("clearWeatherTime", "晴天剩余刻数", "Int", "≥0"),
    ("SpawnX", "出生点 X", "Int", "整数"),
    ("SpawnY", "出生点 Y", "Int", "整数"),
    ("SpawnZ", "出生点 Z", "Int", "整数"),
    ("LastPlayed", "上次游玩", "Long", "毫秒时间戳"),
    ("DataVersion", "数据版本", "Int", f"{SUPPORTED_DATA_VERSION_MIN}~"
                                       f"{SUPPORTED_DATA_VERSION_MAX}"),
    ("version", "存档版本号", "Int", "只读参考"),
    ("initialized", "已初始化", "Byte", "0 / 1"),
    ("WasModded", "由模组加载过", "Byte", "只读参考"),
)

#: 常用游戏规则（level.dat → Data.GameRules，1.21 起值是字符串）
COMMON_GAMERULES: tuple[tuple[str, str, str], ...] = (
    ("doDaylightCycle", "昼夜循环", "true / false"),
    ("doWeatherCycle", "天气循环", "true / false"),
    ("doMobSpawning", "刷怪", "true / false"),
    ("keepInventory", "死亡保留物品", "true / false"),
    ("mobGriefing", "生物破坏方块", "true / false"),
    ("doFireTick", "火焰蔓延", "true / false"),
    ("fallDamage", "摔落伤害", "true / false"),
    ("naturalRegeneration", "自然回血", "true / false"),
    ("showDeathMessages", "死亡消息", "true / false"),
    ("randomTickSpeed", "随机刻速度", "整数（默认 3）"),
    ("spawnRadius", "出生点半径", "整数（默认 10）"),
    ("maxCommandChainLength", "命令链最大长度", "整数（默认 65536）"),
)

#: 玩家数据总览字段 (路径, 显示名, 类型)
PLAYER_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("Health", "生命值", "Float"),
    ("foodLevel", "饥饿值", "Int"),
    ("foodSaturationLevel", "饱和度", "Float"),
    ("XpLevel", "经验等级", "Int"),
    ("XpTotal", "总经验", "Int"),
    ("XpP", "经验进度", "Float"),
    ("Score", "分数", "Int"),
    ("playerGameType", "游戏模式", "Int"),
    ("Air", "氧气", "Short"),
    ("Fire", "着火刻数", "Short"),
    ("SelectedItemSlot", "选中槽位", "Int"),
    ("UUID", "UUID", "IntArray"),
    ("Pos", "坐标", "List"),
    ("Rotation", "朝向", "List"),
    ("Dimension", "维度", "String"),
    ("SpawnX", "重生点 X", "Int"),
    ("SpawnY", "重生点 Y", "Int"),
    ("SpawnZ", "重生点 Z", "Int"),
    ("SleepTimer", "睡眠计时", "Short"),
    (" DeathTime", "死亡计时", "Short"),
)

#: 游戏模式
GAME_MODES = {0: "survival", 1: "creative", 2: "adventure", 3: "spectator"}

#: 容器 / 方块实体里常见的物品容器字段
ENTITY_CONTAINER_KEYS = ("Items", "Inventory", "ItemsList", "EnderItems",
                         "HandItems", "ArmorItems", "SaddleItem", "Item",
                         "contents")

#: 区域文件头（8KiB 位置表 + 8KiB 时间戳表）
REGION_HEADER_SIZE = 8192
SECTOR_SIZE = 4096

#: Forge / NeoForge 的模组清单标签
MOD_TAGS = ("fml", "neoforge", "forge", "ForgeCaps", "neoforge:attachments")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def probe_compression(path: Path) -> str:
    """只读文件头几个字节判断压缩方式（大文件也能秒回）。"""
    try:
        with Path(path).open("rb") as handle:
            head = handle.read(4)
    except OSError:
        return "none"
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    if len(head) >= 2 and head[0] == 0x78:
        return "zlib"
    return "none"


# ---------------------------------------------------------------------------
# 区域文件里的区块（懒加载）
# ---------------------------------------------------------------------------

def read_region_chunk_data(path: str | Path, x: int, z: int,
                           max_bytes: int = 16 * 1024 * 1024) -> bytes | None:
    """读取并解压区域文件里某个区块的原始 NBT 字节（按需读，不整块载入）。"""
    path = Path(path)
    match = re.match(r"r\.(-?\d+)\.(-?\d+)\.mca", path.name)
    if not match:
        return None
    region_x, region_z = int(match.group(1)), int(match.group(2))
    local_x = x - region_x * 32
    local_z = z - region_z * 32
    if not (0 <= local_x < 32 and 0 <= local_z < 32):
        return None
    index = local_x + local_z * 32
    try:
        with path.open("rb") as handle:
            handle.seek(index * 4)
            entry = handle.read(4)
            if len(entry) < 4:
                return None
            offset = int.from_bytes(entry[:3], "big")
            sectors = entry[3]
            if offset == 0 or sectors == 0:
                return None
            handle.seek(offset * SECTOR_SIZE)
            header = handle.read(5)
            if len(header) < 5:
                return None
            length = int.from_bytes(header[:4], "big")
            compression = header[4]
            if length <= 1 or length > max_bytes:
                return None
            payload = handle.read(length - 1)
    except OSError:
        return None
    try:
        if compression == 1:                            # gzip
            return gzip.decompress(payload)
        if compression == 2:                            # zlib
            return zlib.decompress(payload)
        if compression == 3:                            # 未压缩
            return payload
    except (OSError, zlib.error):                       # pragma: no cover
        return None
    # compression == 4 是 LZ4（1.20.5+ 少见），目前不支持
    return None


def read_region_chunk(path: str | Path, x: int, z: int) -> SnbtNode | None:
    """把一个区块解析成 NBT 节点（失败返回 None）。"""
    from app.core.nbt_binary import parse_nbt_bytes
    raw = read_region_chunk_data(path, x, z)
    if raw is None:
        return None
    try:
        node, _meta = parse_nbt_bytes(raw, with_meta=True)
    except Exception:
        return None
    return node


def iter_region_entities(world: "WorldSave", limit_chunks: int = 4096):
    """遍历区域文件里的实体（懒加载：逐区块读、逐区块解析）。

    区块里的实体存在根下的 `Entities` 列表（1.17+ 也可能是 `entities`）。
    生成器逐个产出 `EntityData`，调用方可以随时中断。
    """
    from app.core.nbt_bridge import looks_like_entity, parse_entity
    remaining = int(limit_chunks)
    for region in world.regions.values():
        for chunk in region.chunks:
            if not chunk.exists():
                continue
            if remaining <= 0:
                return
            remaining -= 1
            node = read_region_chunk(region.path, chunk.x, chunk.z)
            if node is None:
                continue
            for key in ("Entities", "entities"):
                container = node.get_child(key)
                if container is None or container.kind != SnbtKind.LIST:
                    continue
                for entry in container.children:
                    if not looks_like_entity(entry):
                        continue
                    try:
                        data = parse_entity(
                            entry,
                            f"{region.name} 区块({chunk.x},{chunk.z})")
                    except Exception:                   # pragma: no cover
                        continue
                    # 附加来源信息（EntityData 是 dataclass，可动态补字段）
                    data.source_dimension = region.dimension   # type: ignore[attr-defined]
                    data.source_chunk = (chunk.x, chunk.z)      # type: ignore[attr-defined]
                    yield data
                break


def scan_world_entities(world: "WorldSave", limit_chunks: int = 2048,
                        limit_entities: int = 5000) -> list[Any]:
    """扫描区域文件里的实体（带数量上限，避免巨存档扫太久）。"""
    out: list[Any] = []
    for entity in iter_region_entities(world, limit_chunks=limit_chunks):
        out.append(entity)
        if len(out) >= limit_entities:
            break
    return out


def count_bytes_human(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{value:.1f} GB"                            # pragma: no cover


def human_time(millis: int | float | None) -> str:
    """毫秒时间戳 → 人类可读（无效值返回空串）。"""
    try:
        value = float(millis)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    try:
        return datetime.fromtimestamp(value / 1000.0).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):        # pragma: no cover
        return ""


def game_time_label(day_time: int) -> str:
    """白天时间 → 时段说明。"""
    ticks = int(day_time) % 24000
    if ticks < 6000:
        return "白天"
    if ticks < 12000:
        return "中午"
    if ticks < 13800:
        return "日落"
    if ticks < 18000:
        return "夜晚"
    return "午夜" if ticks < 22800 else "日出前"


def data_version_label(version: int | None) -> str:
    if version is None:
        return "未知"
    exact = KNOWN_DATA_VERSIONS.get(int(version))
    if exact:
        return exact
    # 找最近的已知识别
    if not KNOWN_DATA_VERSIONS:                         # pragma: no cover
        return "未知"
    nearest = min(KNOWN_DATA_VERSIONS, key=lambda v: abs(v - int(version)))
    return f"约 {KNOWN_DATA_VERSIONS[nearest]}"


def normalize_uuid(raw: Any) -> str:
    """把 UUID 的多种写法归一成带横线的字符串。"""
    if raw is None:
        return ""
    if isinstance(raw, SnbtNode):
        if raw.kind == SnbtKind.INT_ARRAY:
            values = []
            for child in raw.children:
                try:
                    values.append(int(child.value) & 0xFFFFFFFF)
                except (TypeError, ValueError):
                    return ""
            if len(values) == 4:
                # IntArray 写法：四段拼起来就是完整的 128 位 UUID
                hexed = "".join(f"{value:08x}" for value in values)
                return (f"{hexed[0:8]}-{hexed[8:12]}-{hexed[12:16]}-"
                        f"{hexed[16:20]}-{hexed[20:32]}").lower()
            return ""
        text = str(raw.value or "")
    else:
        text = str(raw)
    text = text.strip()
    if not text:
        return ""
    hex_only = re.sub(r"[^0-9a-fA-F]", "", text)
    if len(hex_only) == 32:
        return (f"{hex_only[0:8]}-{hex_only[8:12]}-{hex_only[12:16]}-"
                f"{hex_only[16:20]}-{hex_only[20:32]}").lower()
    return text.lower()


def find_child(node: SnbtNode | None, key: str) -> SnbtNode | None:
    return node.get_child(key) if node is not None else None


def node_of(node: SnbtNode | None, *keys: str) -> SnbtNode | None:
    """按路径逐层找子节点（大小写敏感优先）。"""
    current = node
    for key in keys:
        if current is None:
            return None
        current = current.get_child(key)
    return current


def scalar(node: SnbtNode | None, default: Any = None) -> Any:
    if node is None or node.is_container:
        return default
    if node.kind == SnbtKind.STRING:
        return str(node.value)
    return node.value


def number(node: SnbtNode | None, default: float = 0.0) -> float:
    try:
        return float(scalar(node, default))             # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def int_number(node: SnbtNode | None, default: int = 0) -> int:
    try:
        return int(number(node, default))
    except (TypeError, ValueError):                     # pragma: no cover
        return default


# ---------------------------------------------------------------------------
# 字段路径（Data.Player.Inventory[0].id）
# ---------------------------------------------------------------------------

def parse_field_path(path: str) -> list[str | int]:
    """`Data.Player.Inventory[0].id` → ['Data','Player','Inventory',0,'id']。"""
    tokens: list[str | int] = []
    buffer = ""
    index = 0
    text = str(path or "").strip().lstrip(".")
    while index < len(text):
        char = text[index]
        if char == ".":
            if buffer:
                tokens.append(buffer)
                buffer = ""
            index += 1
            continue
        if char == "[":
            if buffer:
                tokens.append(buffer)
                buffer = ""
            end = text.find("]", index)
            if end < 0:
                raise ValueError(f"路径缺少 ']'：{path}")
            inner = text[index + 1:end].strip()
            if not inner:
                raise ValueError(f"空的列表下标：{path}")
            try:
                tokens.append(int(inner))
            except ValueError:
                # `[{}]` 形式的查询不在这里支持，交给 /data 命令生成
                raise ValueError(f"下标必须是整数：{inner}") from None
            index = end + 1
            continue
        buffer += char
        index += 1
    if buffer:
        tokens.append(buffer)
    if not tokens:
        raise ValueError("路径为空")
    return tokens


def resolve_path(root: SnbtNode, path: str) -> SnbtNode | None:
    """按字段路径取节点；找不到返回 None。"""
    tokens = parse_field_path(path)
    current: SnbtNode | None = root
    for token in tokens:
        if current is None:
            return None
        if isinstance(token, int):
            if token < 0 or token >= len(current.children):
                return None
            current = current.children[token]
        else:
            current = current.get_child(token)
    return current


def path_exists(root: SnbtNode, path: str) -> bool:
    try:
        return resolve_path(root, path) is not None
    except ValueError:
        return False


def _coerce_scalar(kind: SnbtKind, value: Any) -> Any:
    """按目标类型折算取值（越界会抛 ValueError）。"""
    if kind == SnbtKind.STRING:
        return str(value)
    if kind == SnbtKind.BOOL:
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("true", "1", "yes", "on"):
                return True
            if low in ("false", "0", "no", "off"):
                return False
            raise ValueError(f"不是布尔值：{value!r}")
        return bool(value)
    if kind in (SnbtKind.FLOAT, SnbtKind.DOUBLE):
        return float(value)
    # 整数族
    if isinstance(value, str):
        text = value.strip().lower()
        for suffix in ("b", "s", "l", "i"):
            if text.endswith(suffix):
                text = text[:-1]
                break
        number_value = int(text, 0) if text.startswith("0x") else int(float(text))
    else:
        number_value = int(value)
    limits = {
        SnbtKind.BYTE: (-128, 255),
        SnbtKind.SHORT: (-32768, 65535),
        SnbtKind.INT: (-2147483648, 2147483647),
        SnbtKind.LONG: (-(2 ** 63), 2 ** 63 - 1),
    }
    low, high = limits.get(kind, (-(2 ** 63), 2 ** 63 - 1))
    if not low <= number_value <= high:
        raise ValueError(f"{number_value} 超出 {kind.value} 取值范围")
    return number_value


def set_value_at(root: SnbtNode, path: str, value: Any,
                 kind: SnbtKind | None = None, create: bool = False,
                 list_appendix: bool = False) -> SnbtNode:
    """把 `value` 写到 `path` 指向的节点；节点不存在时按需创建。

    返回被写入（或新建）的节点。类型默认沿用原有节点的类型。
    """
    tokens = parse_field_path(path)
    parent = root
    for token in tokens[:-1]:
        if isinstance(token, int):
            if token >= len(parent.children):
                raise KeyError(f"下标越界：{path}")
            parent = parent.children[token]
        else:
            child = parent.get_child(token)
            if child is None:
                if not create:
                    raise KeyError(f"找不到路径：{path}")
                child = SnbtNode.new_compound(key=token)
                parent.add_child(child)
            parent = child

    last = tokens[-1]
    if isinstance(last, int):
        if last >= len(parent.children):
            if not list_appendix:
                raise KeyError(f"下标越界：{path}")
            node = SnbtNode.new_scalar(kind or SnbtKind.INT, value)
            parent.add_child(node)
            return node
        node = parent.children[last]
    else:
        node = parent.get_child(str(last))
        if node is None:
            if not create:
                raise KeyError(f"找不到路径：{path}")
            node = SnbtNode.new_scalar(kind or SnbtKind.STRING,
                                       _coerce_scalar(kind or SnbtKind.STRING,
                                                      value),
                                       key=str(last))
            parent.add_child(node)
            return node

    if node.is_container:
        # 容器赋值：按取值重建（Pos / Rotation / 列表 / 复合标签都用这条路）
        target_kind = kind or node.kind
        replacement = _node_from_value(target_kind, value, key=node.key)
        if replacement is None:
            raise ValueError(f"{path} 是容器，不能直接赋标量值")
        parent = root
        for token in tokens[:-1]:
            parent = (parent.children[token] if isinstance(token, int)
                      else parent.get_child(token))
        if isinstance(tokens[-1], int):
            parent.children[tokens[-1]] = replacement
        else:
            index = parent.children.index(node)
            parent.children[index] = replacement
        return replacement
    target_kind = kind or node.kind
    node.kind = target_kind
    node.value = _coerce_scalar(target_kind, value)
    return node


def _node_from_value(kind: SnbtKind, value: Any,
                     key: str | None = None) -> SnbtNode | None:
    """把 Python 取值 + 目标类型变成 NBT 节点（容器也支持）。"""
    if kind == SnbtKind.LIST:
        node = SnbtNode.new_list(key=key)
        items = list(value) if isinstance(value, (list, tuple)) else [value]
        for item in items:
            if isinstance(item, SnbtNode):
                node.add_child(item.clone())
                continue
            child_kind = _guess_kind(item)
            child = _node_from_value(child_kind, item)
            if child is not None:
                node.add_child(child)
        return node
    if kind == SnbtKind.COMPOUND:
        node = SnbtNode.new_compound(key=key)
        if isinstance(value, dict):
            for item_key, item in value.items():
                child = _node_from_value(_guess_kind(item), item,
                                         key=str(item_key))
                if child is not None:
                    node.add_child(child)
        elif isinstance(value, SnbtNode):               # pragma: no cover
            return value.clone()
        return node
    if kind in (SnbtKind.BYTE_ARRAY, SnbtKind.INT_ARRAY, SnbtKind.LONG_ARRAY):
        node = SnbtNode(kind=kind, key=key)
        items = list(value) if isinstance(value, (list, tuple)) else [value]
        for item in items:
            node.add_child(SnbtNode.new_scalar(SnbtKind.INT,
                                               int(_coerce_scalar(
                                                   SnbtKind.INT, item))))
        return node
    if kind == SnbtKind.STRING:
        return SnbtNode.new_scalar(SnbtKind.STRING, str(value), key=key)
    if kind in (SnbtKind.FLOAT, SnbtKind.DOUBLE):
        return SnbtNode.new_scalar(kind, float(value), key=key)
    if kind == SnbtKind.BOOL:
        return SnbtNode.new_scalar(SnbtKind.BOOL, bool(value), key=key)
    if kind in (SnbtKind.BYTE, SnbtKind.SHORT, SnbtKind.INT, SnbtKind.LONG):
        return SnbtNode.new_scalar(kind, _coerce_scalar(kind, value), key=key)
    return None                                                # pragma: no cover


# ---------------------------------------------------------------------------
# 世界总览 / 玩家 / 实体 / 容器 数据模型
# ---------------------------------------------------------------------------

@dataclass
class WorldCompatibility:
    """DataVersion 与目标版本的兼容性结论。"""

    data_version: int | None = None
    detected: str = ""
    target: str = TARGET_VERSION
    level: str = "unknown"          # ok / read_only / unknown
    message: str = ""

    @property
    def writable(self) -> bool:
        return self.level == "ok"


@dataclass
class PlayerRef:
    """一个玩家存档文件的摘要。"""

    uuid: str
    path: Path
    name: str = ""
    kind: str = "playerdata"        # playerdata / stats / advancements

    def display(self) -> str:
        return self.name or self.uuid


@dataclass
class WorldOverview:
    """世界总览（GUI「世界总览」页签直接用这个）。"""

    path: Path
    name: str = ""
    folder: str = ""
    data_version: int | None = None
    version_label: str = ""
    version_id: int | None = None
    version_name: str = ""
    seed: int | None = None
    difficulty: str = ""
    difficulty_locked: bool = False
    allow_commands: bool = False
    hardcore: bool = False
    game_type: str = ""
    day_time: int = 0
    time: int = 0
    weather: str = "clear"
    rain_time: int = 0
    thunder_time: int = 0
    spawn: tuple[int, int, int] = (0, 0, 0)
    world_border: dict[str, Any] = field(default_factory=dict)
    last_played: str = ""
    gamerules: dict[str, str] = field(default_factory=dict)
    datapacks: list[str] = field(default_factory=list)
    players: list[PlayerRef] = field(default_factory=list)
    singleplayer_uuid: str = ""
    mods: list[dict[str, str]] = field(default_factory=list)
    compatibility: WorldCompatibility = field(
        default_factory=WorldCompatibility)
    entries: dict[str, str] = field(default_factory=dict)
    size_bytes: int = 0
    total_files: int = 0
    warnings: list[str] = field(default_factory=list)

    # ---- 展示 ----

    def weather_text(self) -> str:
        return {"clear": "晴天", "rain": "下雨", "thunder": "雷暴"}.get(
            self.weather, self.weather)

    def difficulty_text(self) -> str:
        return {"peaceful": "和平", "easy": "简单", "normal": "普通",
                "hard": "困难"}.get(self.difficulty, self.difficulty or "—")

    def game_type_text(self) -> str:
        return {"survival": "生存", "creative": "创造", "adventure": "冒险",
                "spectator": "旁观"}.get(self.game_type, self.game_type or "—")

    def summary_rows(self) -> list[tuple[str, str]]:
        """(标签, 取值) 列表，GUI 表格与 CLI 共用。"""
        border = self.world_border or {}
        return [
            ("世界名", self.name or self.folder),
            ("存档目录", str(self.path)),
            ("DataVersion", f"{self.data_version}（{self.version_label}）"
             if self.data_version is not None else "未知"),
            ("存档版本号", str(self.version_id) if self.version_id is not None
             else "—"),
            ("版本名", self.version_name or "—"),
            ("种子", str(self.seed) if self.seed is not None else "—"),
            ("难度", self.difficulty_text()
             + ("（已锁定）" if self.difficulty_locked else "")),
            ("允许命令", "是" if self.allow_commands else "否"),
            ("极限模式", "是" if self.hardcore else "否"),
            ("默认游戏模式", self.game_type_text()),
            ("时间", f"DayTime {self.day_time}（{game_time_label(self.day_time)}）"
                     f" · Time {self.time}"),
            ("天气", f"{self.weather_text()}（雨 {self.rain_time} / "
                     f"雷 {self.thunder_time}）"),
            ("出生点", f"{self.spawn[0]} {self.spawn[1]} {self.spawn[2]}"),
            ("世界边界", (f"中心 {border.get('center_x', 0):.0f} "
                          f"{border.get('center_z', 0):.0f} · "
                          f"边长 {border.get('size', 0):.0f} · "
                          f"伤害 {border.get('damage', 0):.0f}"
                          if border else "—")),
            ("上次游玩", self.last_played or "—"),
            ("数据包", f"{len(self.datapacks)} 个："
                       + ("、".join(self.datapacks[:6]) or "无")
             + ("…" if len(self.datapacks) > 6 else "")),
            ("玩家", f"{len(self.players)} 个："
                     + ("、".join(p.display() for p in self.players[:8]) or "无")),
            ("模组标签", f"{len(self.mods)} 条（只读展示）"
             if self.mods else "无"),
            ("游戏规则", f"{len(self.gamerules)} 条"),
            ("目录体积", f"{count_bytes_human(self.size_bytes)} · "
                         f"{self.total_files} 个文件"),
        ]


@dataclass
class PlayerData:
    """一个玩家的数据（playerdata/<uuid>.dat）。"""

    uuid: str = ""
    path: Path | None = None
    root: SnbtNode | None = None
    name: str = ""
    health: float = 0.0
    max_health: float = 20.0
    food_level: int = 20
    xp_level: int = 0
    xp_total: int = 0
    game_type: str = ""
    dimension: str = ""
    pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float] = (0.0, 0.0)
    effects: list[dict[str, Any]] = field(default_factory=list)
    attributes: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    advancements: list[str] = field(default_factory=list)
    inventory_count: int = 0
    inventory_total: int = 0
    ender_count: int = 0
    item_count: int = 0
    #: 数据来源：`playerdata`（独立文件）或 `level.dat`（单机主人的内嵌
    #: Player 节点）。内嵌玩家没有独立文件 —— 它的改动随 level.dat 一起
    #: 写回，并且**不参与独立快照**（level 快照已经包含它），
    #: 否则会出现两份互相打架的副本。
    source: str = "playerdata"
    dirty: bool = False

    def display(self) -> str:
        return self.name or self.uuid or "玩家"

    def summary(self) -> str:
        return (f"{self.display()} · 生命 {self.health:g}/{self.max_health:g}"
                f" · 饥饿 {self.food_level} · 等级 {self.xp_level}"
                f" · {self.game_type or '—'}")

    def pos_text(self) -> str:
        return " ".join(f"{value:.2f}" for value in self.pos)


@dataclass
class ContainerInfo:
    """一个容器 / 方块实体的可编辑视图。"""

    path: str                     # BNBT 路径（Data.Items / Data.Player.Inventory …）
    file: Path | None = None      # 所在文件
    label: str = ""
    kind: str = "list"            # list / dict
    block_id: str = ""
    position: tuple[int, int, int] | None = None
    item_count: int = 0
    total_count: int = 0
    size: int = 0

    def summary(self) -> str:
        where = ""
        if self.position is not None:
            where = f" @ {self.position[0]} {self.position[1]} {self.position[2]}"
        return (f"{self.label or self.path}{where} · "
                f"{self.item_count} 格 / {self.total_count} 个物品")


@dataclass
class MapInfo:
    """`data/map_*.dat` 的摘要。"""

    index: int
    path: Path
    dimension: str = ""
    scale: int = 1
    locked: bool = False
    tracking: bool = False
    unlimited_tracking: bool = False
    x_center: int = 0
    z_center: int = 0
    banners: int = 0
    frames: int = 0
    colors_bytes: int = 0
    dirty: bool = False
    root: SnbtNode | None = None

    @property
    def map_id(self) -> str:
        return f"map_{self.index}"

    def summary(self) -> str:
        return (f"地图 #{self.index} · {self.dimension or '—'} · "
                f"比例 1:{1 << max(0, self.scale)} · "
                f"{'已锁定' if self.locked else '可更新'}"
                f"{' · 追踪' if self.tracking else ''}"
                f" · 中心 {self.x_center},{self.z_center}")


@dataclass
class RegionChunk:
    """区域文件里的一个区块索引项。"""

    x: int
    z: int
    offset: int
    sectors: int
    timestamp: int

    @property
    def index(self) -> int:
        return (self.x & 31) + (self.z & 31) * 32

    def exists(self) -> bool:
        return self.sectors > 0 and self.offset > 0

    def summary(self) -> str:
        when = (datetime.fromtimestamp(self.timestamp)
                .strftime("%Y-%m-%d %H:%M:%S") if self.timestamp else "—")
        return (f"区块 ({self.x}, {self.z}) · 扇区 {self.sectors} · "
                f"偏移 {self.offset} · {when}")


@dataclass
class RegionFile:
    """区域文件只读索引（只读头部 8KiB，不整块读入）。"""

    path: Path
    dimension: str = ""
    chunks: list[RegionChunk] = field(default_factory=list)
    size_bytes: int = 0
    error: str = ""

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def present(self) -> int:
        return sum(1 for c in self.chunks if c.exists())

    def summary(self) -> str:
        return (f"{self.path.name} · {self.present}/{len(self.chunks)} 区块 · "
                f"{count_bytes_human(self.size_bytes)}"
                + (f" · {self.error}" if self.error else ""))


# ---------------------------------------------------------------------------
# 世界存档
# ---------------------------------------------------------------------------

def looks_like_save_dir(path: Path) -> bool:
    """判断目录是否像一个世界存档。"""
    try:
        if not path.is_dir():
            return False
    except OSError:                                     # pragma: no cover
        return False
    if (path / "level.dat").is_file():
        return True
    # 极少数情况下只有 level.dat_old / 只有区域目录
    if (path / "level.dat_old").is_file():
        return True
    return (path / "region").is_dir() and not (path / "level.dat").exists()


def find_save_dirs(root: Path, limit: int = 200) -> list[Path]:
    """在 `.minecraft` / `saves` / 版本隔离目录下找存档。"""
    root = Path(root)
    candidates: list[Path] = []
    if looks_like_save_dir(root):
        return [root]

    def push(path: Path) -> None:
        if len(candidates) >= limit:
            return
        try:
            resolved = path.resolve()
        except OSError:                                 # pragma: no cover
            return
        if resolved not in candidates:
            candidates.append(resolved)

    # 常见层级：root/saves/*、root/versions/*/saves/*
    for level in (1, 2, 3):
        pattern = "/".join(["*"] * (level - 1))
        glob = f"{pattern}/saves/*" if pattern else "saves/*"
        for path in sorted(root.glob(glob)):
            if looks_like_save_dir(path):
                push(path)
        if candidates:
            break
    if not candidates:
        for path in sorted(root.glob("*/level.dat")):
            push(path.parent)
    return candidates


class WorldSave:
    """一个打开的世界存档（默认只读）。"""

    def __init__(self, path: str | Path, *, read_only: bool = True) -> None:
        self.path = Path(path)
        self.read_only = read_only
        self.overview = WorldOverview(path=self.path)
        self.level: NbtDocument | None = None
        self.level_root: SnbtNode | None = None
        self.level_old: NbtDocument | None = None
        self.players: dict[str, PlayerData] = {}
        self.maps: dict[int, MapInfo] = {}
        self.regions: dict[str, RegionFile] = {}
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self._level_dirty = False

    # ---- 基本信息 ----

    @property
    def name(self) -> str:
        return self.overview.name or self.path.name

    def is_open(self) -> bool:
        return self.level is not None

    def unlock(self) -> None:
        """解除只读（用户显式确认后才可写）。"""
        self.read_only = False

    def lock(self) -> None:
        self.read_only = True

    @property
    def dirty(self) -> bool:
        return (self._level_dirty
                or any(p.dirty for p in self.players.values())
                or any(m.dirty for m in self.maps.values()))

    # ---- 打开 ----

    def load(self, *, with_players: bool = True, with_maps: bool = True,
             with_regions: bool = True,
             progress=None) -> "WorldSave":
        """读取存档。`progress` 是可选的 (当前, 总数, 说明) 回调。"""
        self.errors.clear()
        self.warnings.clear()
        self._scan_entries()
        self._load_level()
        if with_players:
            self._load_players(progress=progress)
        if with_maps:
            self._load_maps(progress=progress)
        if with_regions:
            self._index_regions(progress=progress)
        self._refresh_overview()
        return self

    def _notify(self, progress, done: int, total: int, text: str) -> None:
        if progress is None:
            return
        try:
            progress(done, total, text)
        except Exception:                               # pragma: no cover
            pass

    # ---- 目录扫描 ----

    def _scan_entries(self) -> None:
        entries: dict[str, str] = {}
        total_files = 0
        total_size = 0
        try:
            listing = sorted(self.path.iterdir(), key=lambda p: p.name)
        except OSError as e:
            self.errors.append(f"无法读取存档目录：{e}")
            return
        for item in listing:
            try:
                if item.is_dir():
                    count, size = _walk_size(item)
                    entries[item.name] = f"{count} 个文件 · {count_bytes_human(size)}"
                    total_files += count
                    total_size += size
                else:
                    size = item.stat().st_size
                    entries[item.name] = count_bytes_human(size)
                    total_files += 1
                    total_size += size
            except OSError:                             # pragma: no cover
                entries[item.name] = "无法读取"
        # DIM1 / DIM-1 里的区域目录也列出来
        for dim in ("DIM1", "DIM-1"):
            nested = self.path / dim / "region"
            if nested.is_dir():
                count, size = _walk_size(nested)
                entries[f"{dim}/region"] = (f"{count} 个文件 · "
                                            f"{count_bytes_human(size)}")
        known = [name for name in SAVE_ENTRIES if name in entries]
        for name in ("level.dat", "region", "playerdata"):
            if name not in entries:
                self.warnings.append(f"存档缺少 {name}")
        ordered: dict[str, str] = {name: entries[name] for name in known}
        for name in sorted(entries):
            if name not in ordered:
                ordered[name] = entries[name]
        self.overview.entries = ordered
        self.overview.total_files = total_files
        self.overview.size_bytes = total_size
        self.overview.folder = self.path.name

    # ---- level.dat ----

    def level_file(self) -> Path:
        return self.path / "level.dat"

    def _load_level(self) -> None:
        level = self.level_file()
        if not level.is_file():
            self.errors.append("找不到 level.dat —— 这不是一个完整的存档目录")
            return
        try:
            self.level = NbtDocument.load(level)
        except (NbtFormatError, NbtWriteError, OSError) as e:
            self.errors.append(f"level.dat 读取失败：{e}")
            return
        self.level_root = self.level.root
        data = self.level_root.get_child("Data")
        if data is None:
            self.errors.append("level.dat 缺少 Data 标签，无法安全编辑")
            self.level_root = None
            return
        old = self.path / "level.dat_old"
        if old.is_file():
            try:
                self.level_old = NbtDocument.load(old)
            except Exception as e:                      # pragma: no cover
                self.warnings.append(f"level.dat_old 读取失败：{e}")

    @property
    def data(self) -> SnbtNode | None:
        if self.level_root is None:
            return None
        return self.level_root.get_child("Data")

    def embedded_player_node(self) -> SnbtNode | None:
        """`Data.Player`（单机主人的内嵌玩家节点）。"""
        data = self.data
        return data.get_child("Player") if data is not None else None

    def player_root(self, uuid: str) -> SnbtNode | None:
        """玩家节点：内嵌玩家始终从 level.dat 现取，保证只有一份真相。"""
        player = self.players.get(normalize_uuid(uuid))
        if player is None:
            return None
        if player.source == "level.dat":
            node = self.embedded_player_node()
            if node is not None:
                player.root = node
            return player.root
        return player.root

    def get_field(self, key: str) -> SnbtNode | None:
        return find_child(self.data, key)

    def field_value(self, key: str, default: Any = None) -> Any:
        return scalar(self.get_field(key), default)

    # ---- 玩家 ----

    def player_files(self) -> list[Path]:
        folder = self.path / "playerdata"
        if not folder.is_dir():
            return []
        return sorted(folder.glob("*.dat"))

    def _load_players(self, progress=None) -> None:
        files = self.player_files()
        total = len(files) or 1
        # 单机存档：level.dat 里的 Player 就是主人。
        # 独立文件优先级更高（内容更新），所以它最后写入会覆盖同名 UUID。
        single = self.data.get_child("Player") if self.data is not None else None
        single_uuid = ""
        if single is not None:
            single_uuid = normalize_uuid(single.get_child("UUID"))
            if single_uuid:
                data = PlayerData(uuid=single_uuid, root=single, name="",
                                  source="level.dat")
                self._fill_player(data, single)
                self.players[single_uuid] = data
                self.overview.singleplayer_uuid = single_uuid
        for index, path in enumerate(files, 1):
            uuid = path.stem.replace("-", "").lower()
            display_uuid = normalize_uuid(path.stem)
            self._notify(progress, index, total, f"读取玩家 {path.name}")
            try:
                doc = NbtDocument.load(path)
            except Exception as e:
                self.warnings.append(f"{path.name} 读取失败：{e}")
                continue
            data = PlayerData(uuid=display_uuid or uuid, path=path,
                              root=doc.root)
            self._fill_player(data, doc.root)
            if data.uuid in self.players:
                # level.dat 里的内嵌 Player 与 playerdata 是同一个玩家：
                # 独立文件内容更新，保留它，并明确记一条提示（健康检查会用到）
                self.notes: list[str] = getattr(self, "notes", [])
                self.notes.append(
                    f"{display_uuid} 同时存在于 level.dat(Player) 与 "
                    f"{path.name}，以独立文件为准")
            self.players[data.uuid] = data
        self._load_player_sidecars(progress)

    def _load_player_sidecars(self, progress=None) -> None:
        """stats / advancements：只读取摘要，用于总览与跨存档复制。"""
        for uuid, player in self.players.items():
            compact = uuid.replace("-", "").lower()
            stats_file = self.path / "stats" / f"{compact}.json"
            if stats_file.is_file():
                try:
                    payload = json.loads(stats_file.read_text(encoding="utf-8"))
                    stats = payload.get("stats", payload)
                    flat: dict[str, int] = {}
                    for group, values in (stats or {}).items():
                        if isinstance(values, dict):
                            for key, value in values.items():
                                try:
                                    flat[f"{group}.{key}"] = int(value)
                                except (TypeError, ValueError):
                                    continue
                    player.stats = flat
                except Exception as e:
                    self.warnings.append(f"{stats_file.name} 读取失败：{e}")
            adv_file = self.path / "advancements" / f"{compact}.json"
            if adv_file.is_file():
                try:
                    payload = json.loads(adv_file.read_text(encoding="utf-8"))
                    done = [name for name, entry in (payload or {}).items()
                            if isinstance(entry, dict) and entry.get("done")]
                    player.advancements = sorted(done)
                except Exception as e:
                    self.warnings.append(f"{adv_file.name} 读取失败：{e}")

    def _fill_player(self, data: PlayerData, root: SnbtNode) -> None:
        from app.core.nbt_bridge import collect_items
        data.root = root
        data.health = number(root.get_child("Health"), 20.0)
        data.food_level = int_number(root.get_child("foodLevel"), 20)
        data.xp_level = int_number(root.get_child("XpLevel"), 0)
        data.xp_total = int_number(root.get_child("XpTotal"), 0)
        game_type = int_number(root.get_child("playerGameType"),
                               int_number(self.get_field("GameType"), 0))
        data.game_type = GAME_MODES.get(game_type, str(game_type))
        data.dimension = str(scalar(root.get_child("Dimension"), "") or
                             (self.data.get_child("dimension")
                              .value if self.data is not None
                              and self.data.get_child("dimension") else ""))
        pos = root.get_child("Pos")
        if pos is not None and pos.kind == SnbtKind.LIST and len(pos.children) >= 3:
            data.pos = tuple(                       # type: ignore[assignment]
                number(child, 0.0) for child in pos.children[:3])
        rotation = root.get_child("Rotation")
        if rotation is not None and rotation.kind == SnbtKind.LIST \
                and len(rotation.children) >= 2:
            data.rotation = tuple(                  # type: ignore[assignment]
                number(child, 0.0) for child in rotation.children[:2])
        data.attributes = _read_attributes(root)
        max_health = next((a["base"] for a in data.attributes
                           if a.get("name") in ("minecraft:generic.max_health",
                                                "minecraft:max_health")), None)
        data.max_health = float(max_health) if max_health else 20.0
        data.effects = _read_effects(root)

        inventory = root.get_child("Inventory")
        ender = root.get_child("EnderItems")
        data.inventory_count = len(inventory.children) if inventory is not None \
            else 0
        data.ender_count = len(ender.children) if ender is not None else 0
        items = collect_items(root)
        data.item_count = len(items)
        data.inventory_total = sum(max(0, int(i.count)) for i in items)

    # ---- 地图 ----

    def map_files(self) -> list[Path]:
        folder = self.path / "data"
        if not folder.is_dir():
            return []
        return sorted(folder.glob("map_*.dat"),
                      key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))

    def _load_maps(self, progress=None) -> None:
        files = self.map_files()
        total = len(files) or 1
        for index, path in enumerate(files, 1):
            self._notify(progress, index, total, f"读取地图 {path.name}")
            number_match = re.search(r"map_(-?\d+)", path.stem)
            map_index = int(number_match.group(1)) if number_match else 0
            info = MapInfo(index=map_index, path=path)
            try:
                doc = NbtDocument.load(path)
                info.root = doc.root
                # 原版 map_*.dat 是 {data:{…}}；少数工具直接写在根下
                node = doc.root.get_child("data") \
                    or doc.root.get_child("Data") or doc.root
                info.dimension = str(scalar(node.get_child("dimension"),
                                            "") or "")
                info.scale = int_number(node.get_child("scale"), 1)
                info.locked = bool(int_number(node.get_child("locked"), 0))
                info.tracking = bool(int_number(node.get_child("tracking"),
                                                0))
                info.unlimited_tracking = bool(
                    int_number(node.get_child("unlimitedTracking"), 0))
                info.x_center = int_number(node.get_child("xCenter"), 0)
                info.z_center = int_number(node.get_child("zCenter"), 0)
                banners = node.get_child("banners")
                frames = node.get_child("frames")
                info.banners = len(banners.children) if banners is not None \
                    and banners.kind == SnbtKind.LIST else 0
                info.frames = len(frames.children) if frames is not None \
                    and frames.kind == SnbtKind.LIST else 0
                colors = node.get_child("colors")
                info.colors_bytes = len(colors.children) if colors is not None \
                    and colors.kind == SnbtKind.BYTE_ARRAY else 0
            except Exception as e:
                self.warnings.append(f"{path.name} 读取失败：{e}")
            self.maps[map_index] = info

    # ---- 区域索引 ----

    def region_files(self) -> list[tuple[str, Path]]:
        out: list[tuple[str, Path]] = []
        for dim, label in (("", "minecraft:overworld"),
                           ("DIM-1", "minecraft:the_nether"),
                           ("DIM1", "minecraft:the_end")):
            folder = self.path / dim / "region" if dim else self.path / "region"
            if folder.is_dir():
                for path in sorted(folder.glob("*.mca")):
                    out.append((label, path))
        return out

    def _index_regions(self, progress=None) -> None:
        for index, (dimension, path) in enumerate(self.region_files(), 1):
            self._notify(progress, index, 0, f"索引区域 {path.name}")
            self.regions[str(path)] = read_region_index(path, dimension=dimension)

    # ---- 总览 ----

    def _refresh_overview(self) -> None:
        data = self.data
        info = self.overview
        info.path = self.path
        info.folder = self.path.name
        if data is None:
            info.warnings = list(self.warnings)
            return
        info.name = str(scalar(data.get_child("LevelName"), "") or self.path.name)
        info.data_version = int_number(data.get_child("DataVersion"), -1)
        if info.data_version is not None and info.data_version < 0:
            info.data_version = None
        info.version_label = data_version_label(info.data_version)
        version = data.get_child("Version")
        if version is not None:
            info.version_id = int_number(version.get_child("Id"), 0) or None
            info.version_name = str(scalar(version.get_child("Name"), "") or "")
        seed = data.get_child("RandomSeed")
        if seed is not None and not seed.is_container:
            try:
                info.seed = int(seed.value)
            except (TypeError, ValueError):             # pragma: no cover
                info.seed = None
        info.difficulty = DIFFICULTIES.get(
            int_number(data.get_child("Difficulty"), 2), "normal")
        info.difficulty_locked = bool(int_number(
            data.get_child("DifficultyLocked"), 0))
        info.allow_commands = bool(int_number(data.get_child("allowCommands"), 0))
        info.hardcore = bool(int_number(data.get_child("hardcore"), 0))
        info.game_type = GAME_MODES.get(
            int_number(data.get_child("GameType"), 0), "survival")
        info.day_time = int_number(data.get_child("DayTime"), 0)
        info.time = int_number(data.get_child("Time"), 0)
        info.rain_time = int_number(data.get_child("rainTime"), 0)
        info.thunder_time = int_number(data.get_child("thunderTime"), 0)
        if bool(int_number(data.get_child("thundering"), 0)):
            info.weather = "thunder"
        elif bool(int_number(data.get_child("raining"), 0)):
            info.weather = "rain"
        else:
            info.weather = "clear"
        info.spawn = (int_number(data.get_child("SpawnX"), 0),
                      int_number(data.get_child("SpawnY"), 64),
                      int_number(data.get_child("SpawnZ"), 0))
        info.last_played = human_time(scalar(data.get_child("LastPlayed"), 0))
        info.world_border = _read_world_border(data)
        rules = data.get_child("GameRules")
        if rules is not None and rules.kind == SnbtKind.COMPOUND:
            info.gamerules = {str(child.key): str(child.value)
                              for child in rules.children if child.key}
        # 单机存档里玩家自己的 playerGameType 才是「当前游戏模式」，
        # Data.GameType 只是新玩家进入世界时的默认值
        if info.singleplayer_uuid:
            player = self.players.get(info.singleplayer_uuid)
            if player is not None and player.game_type:
                info.game_type = player.game_type
        packs = data.get_child("DataPacks")
        names: list[str] = []
        if packs is not None:
            for key in ("Enabled", "Disabled"):
                node = packs.get_child(key)
                if node is not None and node.kind == SnbtKind.LIST:
                    names.extend(str(child.value) for child in node.children
                                 if not child.is_container)
        info.datapacks = names
        info.players = [
            PlayerRef(uuid=player.uuid,
                      path=player.path or self.level_file(),
                      name=player.name,
                      kind=getattr(player, "kind", "playerdata"))
            for player in self.players.values()
        ]
        info.mods = read_mod_list(self.level_root)
        info.compatibility = check_compatibility(info.data_version)
        info.warnings = list(self.warnings) + list(self.errors)

    # ---- 编辑 ----

    def require_writable(self) -> None:
        if self.read_only:
            raise PermissionError("存档处于只读模式：请先显式解锁后再编辑")
        if self.level_root is None:
            raise RuntimeError("没有可编辑的 level.dat")

    def set_level_field(self, key: str, value: Any,
                        kind: SnbtKind | None = None) -> None:
        """写 level.dat 的 Data.<key>；类型默认沿用原节点。"""
        self.require_writable()
        data = self.data
        if data is None:                                # pragma: no cover
            raise RuntimeError("level.dat 缺少 Data 标签")
        node = data.get_child(key)
        target_kind = kind
        if target_kind is None and node is not None and not node.is_container:
            target_kind = node.kind
        if target_kind is None:
            target_kind = _guess_kind(value)
        if node is None:
            set_value_at(self.level_root, f"Data.{key}", value,
                         kind=target_kind, create=True)
        else:
            set_value_at(self.level_root, f"Data.{key}", value, kind=target_kind)
        self._level_dirty = True

    def set_gamerule(self, name: str, value: Any) -> None:
        self.require_writable()
        data = self.data
        if data is None:                                # pragma: no cover
            raise RuntimeError("level.dat 缺少 Data 标签")
        rules = data.get_child("GameRules")
        if rules is None or rules.kind != SnbtKind.COMPOUND:
            rules = SnbtNode.new_compound(key="GameRules")
            data.add_child(rules)
        target = rules.get_child(name)
        text = _gamerule_text(value)
        if target is None:
            rules.add_child(SnbtNode.new_scalar(SnbtKind.STRING, text,
                                                key=name))
        else:
            target.kind = SnbtKind.STRING
            target.value = text
        self._level_dirty = True

    def set_world_border(self, **kwargs: Any) -> None:
        self.require_writable()
        data = self.data
        if data is None:                                # pragma: no cover
            raise RuntimeError("level.dat 缺少 Data 标签")
        border = data.get_child("WorldBorder")
        if border is None:
            border = SnbtNode.new_compound(key="WorldBorder")
            data.add_child(border)
        mapping = {"center_x": "CenterX", "center_z": "CenterZ",
                   "size": "Size", "damage": "DamageAmount",
                   "safe_zone": "SafeZone", "warning_time": "WarningTime",
                   "warning_blocks": "WarningBlocks", "size_lerp_time":
                   "SizeLerpTime", "size_lerp_target": "SizeLerpTarget"}
        for key, target_key in mapping.items():
            if key not in kwargs or kwargs[key] is None:
                continue
            raw = kwargs[key]
            value = float(raw) if not isinstance(raw, bool) else float(raw)
            node = border.get_child(target_key)
            if node is None:
                node = SnbtNode.new_scalar(SnbtKind.DOUBLE, value,
                                           key=target_key)
                border.add_child(node)
            else:
                node.kind = SnbtKind.DOUBLE
                node.value = value
        self._level_dirty = True

    def set_spawn(self, x: int, y: int, z: int) -> None:
        for key, value in (("SpawnX", x), ("SpawnY", y), ("SpawnZ", z)):
            self.set_level_field(key, int(value), kind=SnbtKind.INT)

    def set_time(self, day_time: int | None = None,
                 time: int | None = None) -> None:
        if day_time is not None:
            self.set_level_field("DayTime", int(day_time), kind=SnbtKind.LONG)
        if time is not None:
            self.set_level_field("Time", int(time), kind=SnbtKind.LONG)

    def set_weather(self, weather: str, duration: int = 6000) -> None:
        """weather 取 clear / rain / thunder。"""
        value = str(weather or "").strip().lower()
        if value not in ("clear", "rain", "thunder"):
            raise ValueError(f"未知天气：{weather}")
        self.set_level_field("raining", 1 if value == "rain" else 0,
                             kind=SnbtKind.BYTE)
        self.set_level_field("thundering", 1 if value == "thunder" else 0,
                             kind=SnbtKind.BYTE)
        if value == "clear":
            self.set_level_field("clearWeatherTime", int(duration),
                                 kind=SnbtKind.INT)
            self.set_level_field("rainTime", 0, kind=SnbtKind.INT)
            self.set_level_field("thunderTime", 0, kind=SnbtKind.INT)
        elif value == "rain":
            self.set_level_field("rainTime", int(duration), kind=SnbtKind.INT)
            self.set_level_field("clearWeatherTime", 0, kind=SnbtKind.INT)
        else:
            self.set_level_field("thunderTime", int(duration), kind=SnbtKind.INT)
            self.set_level_field("rainTime", int(duration), kind=SnbtKind.INT)
            self.set_level_field("clearWeatherTime", 0, kind=SnbtKind.INT)

    def set_difficulty(self, difficulty: Any, locked: bool | None = None) -> None:
        if isinstance(difficulty, str):
            reverse = {name: value for value, name in DIFFICULTIES.items()}
            if difficulty.lower() not in reverse:
                raise ValueError(f"未知难度：{difficulty}")
            value = reverse[difficulty.lower()]
        else:
            value = int(difficulty)
            if value not in DIFFICULTIES:
                raise ValueError(f"难度取值应为 0~3：{difficulty}")
        self.set_level_field("Difficulty", value, kind=SnbtKind.BYTE)
        if locked is not None:
            self.set_level_field("DifficultyLocked", 1 if locked else 0,
                                 kind=SnbtKind.BYTE)

    def set_allow_commands(self, enabled: bool) -> None:
        self.set_level_field("allowCommands", 1 if enabled else 0,
                             kind=SnbtKind.BYTE)

    def set_level_name(self, name: str) -> None:
        self.set_level_field("LevelName", str(name), kind=SnbtKind.STRING)

    def set_game_type(self, mode: Any) -> None:
        if isinstance(mode, str):
            reverse = {name: value for value, name in GAME_MODES.items()}
            key = mode.strip().lower()
            if key not in reverse:
                raise ValueError(f"未知游戏模式：{mode}")
            value = reverse[key]
        else:
            value = int(mode)
        self.set_level_field("GameType", value, kind=SnbtKind.INT)

    def mark_level_dirty(self) -> None:
        self._level_dirty = True

    # ---- 玩家编辑 ----

    def set_player_field(self, uuid: str, path: str, value: Any,
                         kind: SnbtKind | None = None) -> None:
        self.require_writable()
        player = self.players.get(normalize_uuid(uuid))
        if player is None:
            raise KeyError(f"找不到玩家：{uuid}")
        root = self.player_root(player.uuid)
        if root is None:                                # pragma: no cover
            raise KeyError(f"玩家 {uuid} 没有可编辑的节点")
        set_value_at(root, path, value, kind=kind, create=True)
        player.dirty = True
        if player.source == "level.dat":
            self.mark_level_dirty()

    def set_player_gamemode(self, uuid: str, mode: Any) -> None:
        if isinstance(mode, str):
            reverse = {name: value for value, name in GAME_MODES.items()}
            key = mode.strip().lower()
            if key not in reverse:
                raise ValueError(f"未知游戏模式：{mode}")
            value = reverse[key]
        else:
            value = int(mode)
        self.set_player_field(uuid, "playerGameType", value, SnbtKind.INT)

    def set_player_pos(self, uuid: str, x: float, y: float, z: float) -> None:
        self.set_player_field(uuid, "Pos", [float(x), float(y), float(z)],
                              SnbtKind.LIST)

    def set_player_health(self, uuid: str, health: float) -> None:
        self.set_player_field(uuid, "Health", float(health), SnbtKind.FLOAT)

    def set_player_xp(self, uuid: str, level: int | None = None,
                      total: int | None = None) -> None:
        if level is not None:
            self.set_player_field(uuid, "XpLevel", int(level), SnbtKind.INT)
        if total is not None:
            self.set_player_field(uuid, "XpTotal", int(total), SnbtKind.INT)

    # ---- 存档内的通用写入 ----

    def _document_for(self, target: str) -> tuple[SnbtNode, callable]:
        """按 `level` / 玩家 UUID / `map:<n>` 找到根节点与 dirty 标记函数。"""
        key = str(target or "level").strip().lower()
        if key in ("level", "level.dat", "world", ""):
            if self.level_root is None:
                raise RuntimeError("没有可编辑的 level.dat")
            return self.level_root, self.mark_level_dirty
        if key.startswith("map:"):
            try:
                index = int(key.split(":", 1)[1])
            except ValueError:                          # pragma: no cover
                raise KeyError(f"地图编号无效：{target}") from None
            info = self.maps.get(index)
            if info is None or info.root is None:
                raise KeyError(f"找不到地图：{target}")

            def mark_map() -> None:
                info.dirty = True
            return info.root, mark_map
        uuid = normalize_uuid(key)
        player = self.players.get(uuid)
        if player is None:
            raise KeyError(f"找不到玩家或来源：{target}")
        root = self.player_root(uuid)
        if root is None:                                # pragma: no cover
            raise KeyError(f"玩家 {uuid} 没有可编辑的节点")

        def mark_player() -> None:
            player.dirty = True
            if player.source == "level.dat":
                self.mark_level_dirty()
        return root, mark_player

    def set_node_value(self, target: str, path: str, value: Any,
                       kind: SnbtKind | None = None,
                       create: bool = False) -> None:
        """在 level / 玩家 / 地图 的任意路径写入一个值。"""
        self.require_writable()
        root, mark = self._document_for(target)
        set_value_at(root, path, value, kind=kind, create=create)
        mark()

    def get_node_value(self, target: str, path: str) -> Any:
        root, _mark = self._document_for(target)
        node = resolve_path(root, path)
        if node is None:
            return None
        return serialize(node, pretty=False) if node.is_container else node.value

    def grow_items(self, target: str, path: str, item_id: str,
                   count: int, *, mode: str = "first_empty",
                   slot: int | None = None, components: str = "") -> int:
        """把物品写进某个容器；返回实际写入的数量。

        mode:

        * ``first_empty`` —— 优先合并同类，再填首个空槽（默认）
        * ``merge``       —— 只合并同类堆叠，不新增槽位
        * ``overwrite``   —— 覆盖指定槽位
        * ``slot``        —— 写进指定槽位（空则新建，非空则覆盖）

        components 是 SNBT 组件片段，例如
        ``custom_name="神剑",enchantments={levels:{"minecraft:sharpness":5}}``。
        """
        from app.core.nbt_bridge import item_stack_size, normalize_item_id
        self.require_writable()
        if mode not in ("first_empty", "merge", "overwrite", "slot"):
            raise ValueError(f"未知写入模式：{mode}")
        root, mark = self._document_for(target)
        container = resolve_path(root, path)
        if container is None:
            raise KeyError(f"找不到容器：{path}")
        if container.kind not in (SnbtKind.LIST, SnbtKind.COMPOUND):
            raise ValueError(f"{path} 不是容器（List / Compound）")

        clean = normalize_item_id(item_id)
        stack_size = max(1, item_stack_size(clean))
        remaining = max(0, int(count))
        if remaining <= 0:
            return 0
        template = _parse_component_fragment(components)
        entries = list(enumerate(container.children))
        written = 0

        def stack_pair(node: SnbtNode) -> tuple[str, int]:
            if node is None or node.kind != SnbtKind.COMPOUND:
                return "", 0
            return (str(scalar(node.get_child("id"), "") or ""),
                    max(0, int_number(node.get_child("count"), 1)))

        # 1) 合并同类堆叠
        if mode in ("first_empty", "merge"):
            for _index, node in entries:
                if remaining <= 0:
                    break
                item, amount = stack_pair(node)
                if normalize_item_id(item) != clean or amount >= stack_size:
                    continue
                add = min(stack_size - amount, remaining)
                counter = node.get_child("count")
                if counter is None or counter.is_container:  # pragma: no cover
                    continue
                counter.value = amount + add
                remaining -= add
                written += add
            if remaining <= 0:
                mark()
                return written

        # 2) 指定槽位（overwrite / slot）
        if mode in ("overwrite", "slot") or slot is not None:
            target_slot = int(slot) if slot is not None else \
                _find_empty_slot(entries)
            if target_slot is None:
                raise ValueError("容器已满，找不到可写槽位")
            amount = min(stack_size, remaining)
            existing = _node_at_slot(entries, target_slot)
            fresh = _make_item_stack(clean, amount, target_slot, template)
            if existing is None:
                _insert_slot(container, fresh, target_slot)
            else:
                _replace_node(container, existing, fresh)
            written += amount
            remaining -= amount
            if mode == "overwrite" or remaining <= 0:
                mark()
                return written

        # 3) 填空槽（only first_empty / slot 会走到这里；merge 不新增槽位）
        if mode in ("merge", "overwrite"):
            if remaining > 0:
                self.warnings.append(
                    f"{path} 没有可合并的同类堆叠，仍有 {remaining} 个 "
                    f"{clean} 未写入")
            mark()
            return written
        guard = 0
        while remaining > 0 and guard < 4096:
            guard += 1
            entries = list(enumerate(container.children))
            target_slot = _find_empty_slot(entries)
            if target_slot is None:
                self.warnings.append(
                    f"{path} 已满，仍有 {remaining} 个 {clean} 未写入")
                break
            amount = min(stack_size, remaining)
            _insert_slot(container,
                         _make_item_stack(clean, amount, target_slot, template),
                         target_slot)
            written += amount
            remaining -= amount
        mark()
        return written

    def containers(self, target: str = "level") -> list[ContainerInfo]:
        """列出某个来源里所有容器 / 方块实体（只读索引）。"""
        from app.core.nbt_bridge import (
            is_item_container, looks_like_item,
        )
        root, _mark = self._document_for(target)
        out: list[ContainerInfo] = []
        for path, node in _iter_paths(root):
            label = path.split(".")[-1].split("[")[0] or "(根)"
            if is_item_container(node):
                items = [child for child in node.children
                         if looks_like_item(child)]
                total = 0
                for item in items:
                    total += max(1, int_number(item.get_child("count"), 1))
                out.append(ContainerInfo(
                    path=path, label=label,
                    kind="list" if node.kind == SnbtKind.LIST else "dict",
                    item_count=len(items), total_count=total,
                    size=len(node.children)))
                continue
            # 方块实体：xxx 里带 id + Items
            if node.kind == SnbtKind.COMPOUND:
                block_id = str(scalar(node.get_child("id"), "") or "")
                if block_id and any(node.get_child(key) is not None
                                    for key in ENTITY_CONTAINER_KEYS):
                    items_node = next(
                        (node.get_child(key) for key in ENTITY_CONTAINER_KEYS
                         if node.get_child(key) is not None), None)
                    if items_node is None or items_node.kind not in (
                            SnbtKind.LIST, SnbtKind.COMPOUND):
                        continue
                    items = [child for child in items_node.children
                             if looks_like_item(child)]
                    total = sum(max(1, int_number(c.get_child("count"), 1))
                                for c in items)
                    out.append(ContainerInfo(
                        path=f"{path}.{items_node.key}", label=block_id,
                        block_id=block_id,
                        position=_read_block_pos(node),
                        item_count=len(items), total_count=total,
                        size=len(items_node.children)))
        out.sort(key=lambda c: c.path)
        return out

    # ---- 备份 / 事务 ----

    def backup_dir(self, timestamp: str | None = None) -> Path:
        """备份目录。time 为空时按当前时间生成，已存在则自动加后缀。"""
        stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
        folder = self.path / "backups" / stamp
        suffix = 1
        while folder.exists():
            folder = self.path / "backups" / f"{stamp}-{suffix}"
            suffix += 1
        return folder

    def create_backup(self, files: Iterable[Path] | None = None,
                      label: str = "") -> Path | None:
        """把指定文件（默认全部待写文件）复制到 backups/<时间戳>/。"""
        targets = [Path(p) for p in (files or self._dirty_files())]
        targets = [p for p in targets if p.is_file()]
        if not targets:
            return None
        folder = self.backup_dir()
        folder.mkdir(parents=True, exist_ok=True)
        manifest = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "world": self.name,
            "label": label,
            "files": [],
        }
        for path in targets:
            try:
                relative = path.relative_to(self.path)
            except ValueError:                          # pragma: no cover
                relative = Path(path.name)
            destination = folder / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            manifest["files"].append({
                "path": str(relative),
                "size": path.stat().st_size,
            })
        (folder / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return folder

    def list_backups(self) -> list[dict[str, Any]]:
        folder = self.path / "backups"
        out: list[dict[str, Any]] = []
        if not folder.is_dir():
            return out
        for item in sorted(folder.iterdir(), reverse=True):
            if not item.is_dir():
                continue
            manifest_file = item / "manifest.json"
            payload: dict[str, Any] = {}
            if manifest_file.is_file():
                try:
                    payload = json.loads(manifest_file.read_text("utf-8"))
                except Exception:                       # pragma: no cover
                    payload = {}
            out.append({
                "name": item.name,
                "path": item,
                "created_at": payload.get("created_at", ""),
                "label": payload.get("label", ""),
                "files": payload.get("files", []),
            })
        return out

    def restore_backup(self, name: str) -> list[Path]:
        """把某个备份里的文件复制回存档（会先给当前文件再备份一次）。"""
        folder = self.path / "backups" / name
        if not folder.is_dir():
            raise FileNotFoundError(f"找不到备份：{name}")
        manifest_file = folder / "manifest.json"
        if not manifest_file.is_file():
            raise FileNotFoundError(f"备份缺少 manifest.json：{name}")
        payload = json.loads(manifest_file.read_text("utf-8"))
        restored: list[Path] = []
        for entry in payload.get("files", []):
            relative = Path(str(entry.get("path", "")))
            source = folder / relative
            if not source.is_file():
                continue
            destination = self.path / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            _atomic_replace(source.read_bytes(), destination)
            restored.append(destination)
        return restored

    def _dirty_files(self) -> list[Path]:
        files: list[Path] = []
        # level.dat 里的 Player 是「单机主人」，改动它等于改 level.dat
        if self._level_dirty or any(p.dirty and p.path is None
                                    for p in self.players.values()):
            files.append(self.level_file())
        files.extend(p.path for p in self.players.values()
                     if p.dirty and p.path is not None)
        files.extend(m.path for m in self.maps.values() if m.dirty)
        if not files:
            files = [self.level_file()]
        return files

    def save(self, *, unlock: bool = False) -> list[Path]:
        """把内存模型写回磁盘（调用方必须先经过事务；这里只做写入）。"""
        if self.read_only and not unlock:
            raise PermissionError("存档只读，拒绝写入")
        written: list[Path] = []
        embedded_player_dirty = any(p.dirty and p.path is None
                                    for p in self.players.values())
        if (self._level_dirty or embedded_player_dirty) \
                and self.level is not None:
            self.level.root = self.level_root or self.level.root
            save_nbt_file(self.level_root, self.level_file(),
                          compress=self.level.compression,
                          root_name=self.level.root_name)
            self._level_dirty = False
            for player in self.players.values():
                if player.path is None:
                    player.dirty = False
            written.append(self.level_file())
        for player in self.players.values():
            if not player.dirty or player.path is None or player.root is None:
                continue
            doc = NbtDocument.load(player.path)
            _atomic_replace(node_to_bytes(player.root, doc), player.path)
            player.dirty = False
            written.append(player.path)
        for info in self.maps.values():
            if not info.dirty or info.root is None:
                continue
            doc = NbtDocument.load(info.path)
            _atomic_replace(node_to_bytes(info.root, doc), info.path)
            info.dirty = False
            written.append(info.path)
        if written:
            self.refresh()
        return written

    def refresh(self) -> None:
        """重新扫描目录信息（写完文件后调用）。"""
        self._scan_entries()
        self._refresh_overview()

    def sync_view(self) -> None:
        """内存模型被替换（撤销 / 回滚）后，重新计算所有派生摘要。

        只重算派生字段，不重新读盘，所以很快。
        """
        for player in self.players.values():
            root = player.root
            if player.source == "level.dat":
                root = self.embedded_player_node() or root
            if root is not None:
                self._fill_player(player, root)
        for info in self.maps.values():
            if info.root is None:
                continue
            root = info.root
            info.dimension = str(scalar(root.get_child("dimension"), "") or "")
            info.scale = int_number(root.get_child("scale"), 1)
            info.locked = bool(int_number(root.get_child("locked"), 0))
            info.tracking = bool(int_number(root.get_child("tracking"), 0))
            info.x_center = int_number(root.get_child("xCenter"), 0)
            info.z_center = int_number(root.get_child("zCenter"), 0)
            colors = root.get_child("colors")
            info.colors_bytes = len(colors.children) if colors is not None \
                and colors.kind == SnbtKind.BYTE_ARRAY else 0
        self._refresh_overview()


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _walk_size(folder: Path) -> tuple[int, int]:
    count = 0
    size = 0
    try:
        for root, _dirs, files in os.walk(folder):
            for name in files:
                try:
                    size += (Path(root) / name).stat().st_size
                    count += 1
                except OSError:                         # pragma: no cover
                    continue
    except OSError:                                     # pragma: no cover
        pass
    return count, size


def _guess_kind(value: Any) -> SnbtKind:
    if isinstance(value, bool):
        return SnbtKind.BOOL
    if isinstance(value, int):
        return SnbtKind.INT
    if isinstance(value, float):
        return SnbtKind.DOUBLE
    if isinstance(value, (list, tuple)):
        return SnbtKind.LIST
    if isinstance(value, dict):
        return SnbtKind.COMPOUND
    return SnbtKind.STRING


def _gamerule_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _read_world_border(data: SnbtNode) -> dict[str, Any]:
    border = data.get_child("WorldBorder")
    if border is None:
        return {}
    out: dict[str, Any] = {}
    for key, target in (("CenterX", "center_x"), ("CenterZ", "center_z"),
                        ("Size", "size"), ("DamageAmount", "damage"),
                        ("DamageBuffer", "damage_buffer"),
                        ("WarningTime", "warning_time"),
                        ("WarningBlocks", "warning_blocks"),
                        ("SafeZone", "safe_zone"),
                        ("SizeLerpTime", "size_lerp_time"),
                        ("SizeLerpTarget", "size_lerp_target")):
        node = border.get_child(key)
        if node is not None and not node.is_container:
            out[target] = node.value
    return out


def _read_attributes(root: SnbtNode) -> list[dict[str, Any]]:
    node = root.get_child("Attributes")
    out: list[dict[str, Any]] = []
    if node is None or node.kind != SnbtKind.LIST:
        return out
    for entry in node.children:
        if entry.kind != SnbtKind.COMPOUND:             # pragma: no cover
            continue
        out.append({
            "name": str(scalar(entry.get_child("Name"), "") or
                        scalar(entry.get_child("id"), "") or ""),
            "base": number(entry.get_child("Base"), 0.0),
            "modifiers": len((entry.get_child("Modifiers") or
                              SnbtNode.new_list()).children),
        })
    return out


def _read_effects(root: SnbtNode) -> list[dict[str, Any]]:
    node = root.get_child("active_effects") or root.get_child("ActiveEffects")
    out: list[dict[str, Any]] = []
    if node is None or node.kind != SnbtKind.LIST:
        return out
    for entry in node.children:
        if entry.kind != SnbtKind.COMPOUND:
            continue
        out.append({
            "id": str(scalar(entry.get_child("id"), "") or ""),
            "amplifier": int_number(entry.get_child("amplifier"), 0),
            "duration": int_number(entry.get_child("duration"), 0),
            "ambient": bool(int_number(entry.get_child("ambient"), 0)),
            "show_particles": bool(int_number(
                entry.get_child("show_particles"), 1)),
        })
    return out


def _read_block_pos(node: SnbtNode) -> tuple[int, int, int] | None:
    for key in ("x", "y", "z"):
        if node.get_child(key) is None:
            return None
    return (int_number(node.get_child("x")), int_number(node.get_child("y")),
            int_number(node.get_child("z")))


def _iter_paths(root: SnbtNode) -> Iterator[tuple[str, SnbtNode]]:
    """深度优先遍历，产生 (字段路径, 节点)。"""
    stack: list[tuple[str, SnbtNode]] = [("", root)]
    while stack:
        path, node = stack.pop()
        yield path, node
        children = list(node.children)
        for index in range(len(children) - 1, -1, -1):
            child = children[index]
            if child.key is not None and str(child.key):
                child_path = f"{path}.{child.key}" if path else str(child.key)
            else:
                child_path = f"{path}[{index}]" if path else f"[{index}]"
            stack.append((child_path, child))


def _parse_component_fragment(text: str) -> SnbtNode | None:
    """把 `custom_name="神剑",damage=5` 这样的片段解析成 Compound 模板。

    物品组件用的是 `id=值` 语法（不是 SNBT 的 `id:值`），所以走
    `item_components.parse_components` 做切分，再把每个值解析成 NBT 节点。
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    from app.core.item_components import MODE_REMOVE, parse_components
    from app.core.snbt import SnbtParser
    specs = parse_components(raw)
    if not specs:
        raise ValueError(f"物品组件片段无法解析：{raw!r}")
    node = SnbtNode.new_compound()
    for spec in specs:
        if spec.removes or spec.mode == MODE_REMOVE:
            # 新建物品时「移除组件」没有意义（本来就没有），跳过
            continue
        value_text = str(spec.value or "").strip()
        if not value_text:
            value_text = "{}"
        try:
            parsed = SnbtParser(value_text).parse()
        except Exception as e:
            raise ValueError(
                f"组件 {spec.id} 的取值无法解析：{e}") from e
        parsed.key = spec.id
        node.add_child(parsed)
    return node if node.children else None


def _make_item_stack(item_id: str, count: int, slot: int,
                     template: SnbtNode | None) -> SnbtNode:
    """构造一个 1.20.5+ 写法的物品节点：{id, count, Slot, components}。"""
    node = SnbtNode.new_compound()
    node.add_child(SnbtNode.new_scalar(SnbtKind.STRING, item_id, key="id"))
    node.add_child(SnbtNode.new_scalar(SnbtKind.INT, int(count), key="count"))
    if slot is not None:
        node.add_child(SnbtNode.new_scalar(SnbtKind.BYTE, int(slot),
                                           key="Slot"))
    if template is not None and template.children:
        components = SnbtNode.new_compound(key="components")
        for child in template.children:
            clone = child.clone()
            clone.key = child.key
            components.add_child(clone)
        node.add_child(components)
    return node


def _insert_slot(container: SnbtNode, node: SnbtNode, slot: int) -> None:
    """按槽位号插入（List 直接追加，Compound 用槽位号做键）。"""
    if container.kind == SnbtKind.LIST:
        container.add_child(node)
    else:
        node.key = str(int(slot))
        existing = container.get_child(node.key)
        if existing is not None:                        # pragma: no cover
            _replace_node(container, existing, node)
        else:
            container.add_child(node)


def _find_empty_slot(entries: list[tuple[int, SnbtNode]]) -> int | None:
    """找第一个没被占用的槽位号。"""
    used: set[int] = set()
    for index, node in entries:
        if node.kind != SnbtKind.COMPOUND:
            used.add(index)
            continue
        slot = node.get_child("Slot")
        if slot is not None and not slot.is_container:
            used.add(int_number(slot, index))
        else:
            used.add(index)
    for candidate in range(0, 4096):
        if candidate not in used:
            return candidate
    return None


def _node_at_slot(entries: list[tuple[int, SnbtNode]],
                  slot: int) -> SnbtNode | None:
    for index, node in entries:
        if node.kind != SnbtKind.COMPOUND:
            continue
        raw = node.get_child("Slot")
        value = int_number(raw, index) if raw is not None else index
        if value == slot:
            return node
    return None


def _replace_node(container: SnbtNode, old: SnbtNode,
                  new: SnbtNode) -> None:
    index = container.index_of(old)
    if index < 0:                                       # pragma: no cover
        container.add_child(new)
        return
    # 保留旧节点的 key（list 元素没有 key）
    new.key = old.key or new.key
    container.children[index] = new


def node_to_bytes(root: SnbtNode, doc: NbtDocument) -> bytes:
    """按文档原有压缩格式把根节点序列化成字节（供原子替换）。"""
    from app.core.nbt_binary.writer import _compress, node_to_nbt_bytes
    raw = node_to_nbt_bytes(root, compress="none", root_name=doc.root_name)
    if doc.compression in ("none", "", None):
        return raw
    return _compress(raw, doc.compression)


def _atomic_replace(data: bytes, destination: Path) -> None:
    """先写临时文件再 os.replace，避免半写坏档。"""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=destination.name + ".", suffix=".tmp",
        dir=str(destination.parent))
    try:
        with os.fdopen(handle, "wb") as sink:
            sink.write(data)
            sink.flush()
            os.fsync(sink.fileno())
        os.replace(temp_name, destination)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:                                 # pragma: no cover
            pass
        raise


# ---------------------------------------------------------------------------
# 区域文件只读索引
# ---------------------------------------------------------------------------

def read_region_index(path: str | Path,
                      dimension: str = "") -> RegionFile:
    """只读区域文件的 8KiB 位置表，产生区块索引（内存映射友好）。"""
    path = Path(path)
    info = RegionFile(path=path, dimension=dimension)
    try:
        info.size_bytes = path.stat().st_size
    except OSError as e:
        info.error = str(e)
        return info
    match = re.match(r"r\.(-?\d+)\.(-?\d+)\.mca", path.name)
    if not match:
        info.error = "文件名不是 r.<x>.<z>.mca"
        return info
    region_x, region_z = int(match.group(1)), int(match.group(2))
    try:
        with path.open("rb") as handle:
            positions = handle.read(REGION_HEADER_SIZE // 2)
            timestamps = handle.read(REGION_HEADER_SIZE // 2)
    except OSError as e:                                # pragma: no cover
        info.error = str(e)
        return info
    if len(positions) < REGION_HEADER_SIZE // 2 \
            or len(timestamps) < REGION_HEADER_SIZE // 2:
        info.error = "文件头不完整"
        return info
    for index in range(1024):
        # 位置表每项 4 字节：3 字节起始扇区（大端）+ 1 字节扇区数
        base = index * 4
        offset_raw = int.from_bytes(positions[base:base + 3], "big")
        sectors_raw = positions[base + 3]
        if offset_raw == 0 and sectors_raw == 0:
            continue
        local_x = index % 32
        local_z = index // 32
        timestamp = struct.unpack_from(">I", timestamps, index * 4)[0]
        info.chunks.append(RegionChunk(
            x=region_x * 32 + local_x,
            z=region_z * 32 + local_z,
            offset=offset_raw,
            sectors=sectors_raw,
            timestamp=timestamp))
    return info


# ---------------------------------------------------------------------------
# 兼容性 / 模组
# ---------------------------------------------------------------------------

def check_compatibility(data_version: int | None,
                        target: str = TARGET_VERSION) -> WorldCompatibility:
    """判断 DataVersion 是否在可写窗口内。"""
    result = WorldCompatibility(data_version=data_version, target=target)
    if data_version is None:
        result.level = "unknown"
        result.message = "读不到 DataVersion，无法判断版本兼容性；建议只在游戏中操作。"
        return result
    result.detected = data_version_label(data_version)
    if data_version > SUPPORTED_DATA_VERSION_MAX:
        result.level = "read_only"
        result.message = (f"存档 DataVersion {data_version}"
                          f"（{result.detected}）高于本工具支持的 {target}"
                          f"（{SUPPORTED_DATA_VERSION_MAX}），"
                          "只做只读浏览；写入请用对应版本的游戏或工具。")
    elif data_version < SUPPORTED_DATA_VERSION_MIN:
        result.level = "read_only"
        result.message = (f"存档 DataVersion {data_version}"
                          f"（{result.detected}）早于 1.20.1，"
                          "结构差异较大，只做只读浏览，"
                          "如需写入请先让游戏升级存档。")
    else:
        result.level = "ok"
        result.message = (f"DataVersion {data_version}（约 {result.detected}）"
                          f"与目标 {target} 兼容，可安全编辑。")
    return result


def read_mod_list(root: SnbtNode | None) -> list[dict[str, str]]:
    """读取 Forge / NeoForge 的模组清单（只读展示，写入时安全跳过）。"""
    if root is None:
        return []
    out: list[dict[str, str]] = []
    for tag in MOD_TAGS:
        node = root.get_child(tag)
        if node is None or node.kind != SnbtKind.COMPOUND:
            continue
        loader = tag
        mods = node.get_child("LoadingModList")
        if mods is None or mods.kind != SnbtKind.LIST:
            mods = node.get_child("ModFileList")
        if mods is None or mods.kind != SnbtKind.LIST:
            continue
        for entry in mods.children:
            if entry.kind != SnbtKind.COMPOUND:
                continue
            out.append({
                "loader": loader,
                "mod_id": str(scalar(entry.get_child("ModId"), "") or
                              scalar(entry.get_child("modId"), "") or ""),
                "name": str(scalar(entry.get_child("ModName"), "") or ""),
                "version": str(scalar(entry.get_child("ModVersion"), "") or ""),
                "size": str(scalar(entry.get_child("ModSize"), "") or ""),
            })
    return out


# ---------------------------------------------------------------------------
# 世界健康检查
# ---------------------------------------------------------------------------

@dataclass
class HealthIssue:
    level: str          # error / warn / info
    title: str
    detail: str = ""
    path: str = ""

    def __str__(self) -> str:
        text = self.title + (f"：{self.detail}" if self.detail else "")
        return f"[{self.level}] {text}" + (f"（{self.path}）" if self.path else "")


def health_check(world: WorldSave) -> list[HealthIssue]:
    """世界健康检查：损坏区块 / 缺失数据包 / 版本不匹配 / 超大 NBT / UUID 冲突。"""
    issues: list[HealthIssue] = []
    if not world.is_open():
        issues.append(HealthIssue("error", "无法打开存档",
                                  "；".join(world.errors) or "未知原因"))
        return issues

    for message in world.errors:
        issues.append(HealthIssue("error", "读取错误", message))
    for message in world.warnings:
        issues.append(HealthIssue("warn", "读取警告", message))

    compat = world.overview.compatibility
    if compat.level == "read_only":
        issues.append(HealthIssue("warn", "版本不匹配", compat.message))
    elif compat.level == "unknown":
        issues.append(HealthIssue("info", "版本未知", compat.message))

    # 损坏 / 越界的区块索引
    for region in world.regions.values():
        if region.error:
            issues.append(HealthIssue("error", "区域文件异常", region.error,
                                      str(region.path)))
            continue
        size_limit = region.size_bytes
        for chunk in region.chunks:
            if chunk.offset * SECTOR_SIZE + chunk.sectors * SECTOR_SIZE \
                    > size_limit:
                issues.append(HealthIssue(
                    "error", "区块索引越界",
                    f"({chunk.x},{chunk.z}) 偏移 {chunk.offset} 扇区 "
                    f"{chunk.sectors} 超出文件大小", str(region.path)))
        if region.present == 0 and region.chunks:
            issues.append(HealthIssue("warn", "区域文件无有效区块",
                                      region.name, str(region.path)))

    # 缺失的数据包
    folder = world.path / "datapacks"
    enabled = world.overview.datapacks
    vanilla = ("vanilla", "minecraft", "bukkit", "paper", "spigot")
    present: set[str] = set()
    if folder.is_dir():
        present = {item.name for item in folder.iterdir()}
    for pack in enabled:
        if pack.startswith(vanilla):
            continue
        name = pack[5:] if pack.startswith("file/") else pack
        if name in present or f"{name}.zip" in present:
            continue
        level = "warn" if folder.is_dir() else "info"
        detail = (f"level.dat 里启用了 {pack}，但 datapacks/ 下找不到"
                  if folder.is_dir() else
                  f"level.dat 里启用了 {pack}，但存档没有 datapacks/ 目录")
        issues.append(HealthIssue(level, "数据包缺失", detail, str(folder)))

    # 超大 NBT
    if world.level is not None and world.level.source_size > 8 * 1024 * 1024:
        issues.append(HealthIssue(
            "warn", "level.dat 过大",
            f"{count_bytes_human(world.level.source_size)}，"
            "可能是大量模组数据（fml / neoforge），修改前务必备份",
            str(world.level_file())))

    # UUID 冲突 / playerdata 与 level.dat 不一致
    seen: dict[str, list[str]] = {}
    for uuid, player in world.players.items():
        seen.setdefault(uuid, []).append(str(player.path or "level.dat"))
    for uuid, places in seen.items():
        if len(places) > 1:
            issues.append(HealthIssue("warn", "UUID 重复",
                                      f"{uuid} 出现在 {len(places)} 处："
                                      + "、".join(places)))
    for player in world.players.values():
        if player.health <= 0 and player.path is not None:
            issues.append(HealthIssue("info", "玩家生命值异常",
                                      f"{player.display()} Health={player.health}",
                                      str(player.path)))
        if not player.root:                             # pragma: no cover
            continue
        if player.root.get_child("Health") is None:
            issues.append(HealthIssue("info", "玩家数据缺少 Health",
                                      player.display(),
                                      str(player.path or "")))

    # 区域文件缺失
    if not world.regions:
        issues.append(HealthIssue("warn", "没有区域文件",
                                  "region/ 下没有 .mca，世界可能是空的",
                                  str(world.path / "region")))
    if not issues:
        issues.append(HealthIssue("info", "检查通过", "没有发现明显问题"))
    return issues


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def open_world(path: str | Path, *, read_only: bool = True,
               with_players: bool = True, with_maps: bool = True,
               with_regions: bool = True, progress=None) -> WorldSave:
    """打开一个世界存档（不抛异常，错误写进 world.errors）。"""
    world = WorldSave(path, read_only=read_only)
    world.load(with_players=with_players, with_maps=with_maps,
               with_regions=with_regions, progress=progress)
    return world


def snapshot_world(world: WorldSave, label: str = "打开快照") -> Path | None:
    """打开世界时自动做一次快照（时间线功能）。"""
    if world.level is None:
        return None
    files = [world.level_file()]
    if (world.path / "level.dat_old").is_file():
        files.append(world.path / "level.dat_old")
    return world.create_backup(files, label=label)


def hash_file(path: Path, chunk: int = 1 << 20) -> str:
    """文件内容的短哈希（时间线去重用）。"""
    import hashlib
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            while True:
                block = handle.read(chunk)
                if not block:
                    break
                digest.update(block)
    except OSError:                                     # pragma: no cover
        return ""
    return digest.hexdigest()[:16]


__all__ = [
    "TARGET_VERSION", "KNOWN_DATA_VERSIONS", "SUPPORTED_DATA_VERSION_MIN",
    "SUPPORTED_DATA_VERSION_MAX", "SAVE_ENTRIES", "DIMENSIONS", "DIFFICULTIES",
    "LEVEL_FIELDS", "COMMON_GAMERULES", "PLAYER_FIELDS", "GAME_MODES",
    "REGION_HEADER_SIZE", "SECTOR_SIZE", "MOD_TAGS",
    "WorldCompatibility", "WorldOverview", "PlayerRef", "PlayerData",
    "ContainerInfo", "MapInfo", "RegionChunk", "RegionFile", "HealthIssue",
    "WorldSave", "open_world", "snapshot_world", "health_check",
    "read_region_index", "read_mod_list", "check_compatibility",
    "parse_field_path", "resolve_path", "path_exists", "set_value_at",
    "find_save_dirs", "looks_like_save_dir", "normalize_uuid",
    "data_version_label", "human_time", "count_bytes_human",
    "game_time_label", "hash_file", "node_to_bytes", "probe_compression",
]
