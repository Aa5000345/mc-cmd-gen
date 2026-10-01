"""区域文件写入（实验功能，全流程安全护栏）。

为什么单独一个模块
------------------
`region/*.mca` 是「按扇区分配」的格式：一个区块的数据块占 2~n 个扇区，
位置表记录起始扇区与扇区数。写入有两个真实风险：

1. **越界**：新数据需要的扇区数超过文件末尾能提供的空间；
2. **半写坏档**：写到一半失败，位置表和内容对不上。

所以本模块只做**最保守**的那一类写：

* 只支持「等长或更短」的替换（新数据占用的扇区数 ≤ 原扇区数），
  不够就**拒绝**，绝不移动其它区块、绝不动位置表的其它项；
* 永远先写 `backup`（调用方负责）；
* 提供 `dry_run()`：只算差异与可行性，不碰磁盘；
* 真正落盘用「临时文件 + `os.replace`」原子替换；
* 写完后重新读一遍位置表做自检（`verify_region`）。

大多数需求（改方块、改容器）用 `/setblock`、`/fill`、
`/data modify block …` 让游戏执行更安全 —— 本模块是给「确实要离线改存档」
的场景兜底的。
"""
from __future__ import annotations

import gzip
import os
import shutil
import struct
import tempfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from app.core.nbt_binary import node_to_nbt_bytes, parse_nbt_bytes
from app.core.snbt import SnbtNode
from app.core.world import (
    SECTOR_SIZE, read_region_chunk_data,
    read_region_index,
)

#: 压缩方式编号（区域文件头）
COMPRESSION_CODES = {"gzip": 1, "zlib": 2, "none": 3}


class RegionWriteError(Exception):
    """区域写入失败（不可写 / 越界 / 校验不通过）。"""


@dataclass
class RegionWritePlan:
    """一次区域写入的可行性分析（dry-run 结果）。"""

    path: Path
    x: int
    z: int
    ok: bool = False
    reason: str = ""
    old_sectors: int = 0
    new_sectors: int = 0
    old_bytes: int = 0
    new_bytes: int = 0
    compression: str = ""
    diff: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.ok:
            return f"区块 ({self.x},{self.z}) 不可写：{self.reason}"
        return (f"区块 ({self.x},{self.z}) 可写："
                f"{self.old_bytes} → {self.new_bytes} 字节，"
                f"占用扇区 {self.old_sectors} → {self.new_sectors}"
                f"（{self.compression}）")


def sectorize(data: bytes) -> tuple[bytes, int]:
    """把数据补齐到 4096 的整数倍，返回 (补齐后的字节, 扇区数)。"""
    sectors = max(1, (len(data) + SECTOR_SIZE - 1) // SECTOR_SIZE)
    padded = data + b"\x00" * (sectors * SECTOR_SIZE - len(data))
    return padded, sectors


def build_chunk_payload(node: SnbtNode, *,
                        compression: str = "zlib") -> bytes:
    """区块数据块 = 4 字节长度 + 1 字节压缩类型 + 压缩后的 NBT。"""
    code = COMPRESSION_CODES.get(compression)
    if code is None:
        raise RegionWriteError(f"不支持的压缩方式：{compression}")
    raw = node_to_nbt_bytes(node, compress="none", root_name="")
    if code == 1:
        body = gzip.compress(raw)
    elif code == 2:
        body = zlib.compress(raw)
    else:
        body = raw
    return struct.pack(">IB", len(body) + 1, code) + body


def plan_chunk_write(path: str | Path, x: int, z: int, node: SnbtNode,
                     *, compression: str = "zlib") -> RegionWritePlan:
    """分析「把 node 写进区块 (x,z)」是否可行（不写盘）。"""
    path = Path(path)
    plan = RegionWritePlan(path=path, x=int(x), z=int(z),
                           compression=compression)
    info = read_region_index(path)
    if info.error:
        plan.reason = info.error
        return plan
    match = [chunk for chunk in info.chunks if chunk.x == x and chunk.z == z]
    if not match:
        plan.reason = "这个区块在文件里不存在（本工具不会新建区块）"
        return plan
    chunk = match[0]
    plan.old_sectors = chunk.sectors
    try:
        plan.old_bytes = _chunk_payload_size(path, chunk.offset)
    except RegionWriteError as e:
        plan.reason = str(e)
        return plan

    payload = build_chunk_payload(node, compression=compression)
    plan.new_bytes = len(payload)
    padded, sectors = sectorize(payload)
    plan.new_sectors = sectors

    if sectors > chunk.sectors:
        plan.reason = (f"新区块需要 {sectors} 个扇区，"
                       f"原位置只有 {chunk.sectors} 个；"
                       "本工具不支持移动其它区块，请改用 /setblock 或 /data")
        return plan
    if chunk.offset * SECTOR_SIZE + sectors * SECTOR_SIZE > info.size_bytes:
        plan.reason = "写入会超出文件末尾"
        return plan

    plan.ok = True
    plan.reason = ""
    old_node = None
    try:
        raw = read_region_chunk_data(path, x, z)
        if raw is not None:
            old_node, _meta = parse_nbt_bytes(raw, with_meta=True)
    except Exception:                                   # pragma: no cover
        old_node = None
    if old_node is not None:
        from app.core.world_session import diff_roots
        plan.diff = [entry.summary()
                     for entry in diff_roots(old_node, node,
                                             f"区块({x},{z})")[:200]]
    _ = padded
    return plan


def write_chunk(path: str | Path, x: int, z: int, node: SnbtNode,
                *, compression: str = "zlib",
                allow_shrink: bool = True) -> RegionWritePlan:
    """把区块写回区域文件（原子替换 + 写后自检）。

    只做「等长或更短」的原地替换；返回实际执行的 plan。
    """
    path = Path(path)
    plan = plan_chunk_write(path, x, z, node, compression=compression)
    if not plan.ok:
        raise RegionWriteError(plan.reason)
    info = read_region_index(path)
    chunk = next(c for c in info.chunks if c.x == x and c.z == z)

    payload = build_chunk_payload(node, compression=compression)
    padded, sectors = sectorize(payload)
    if sectors > chunk.sectors:                         # pragma: no cover
        raise RegionWriteError("扇区数在计划之后发生变化，已放弃写入")
    if not allow_shrink and sectors < chunk.sectors:    # pragma: no cover
        raise RegionWriteError("不允许缩短扇区占用")

    original = path.read_bytes()
    buffer = bytearray(original)
    start = chunk.offset * SECTOR_SIZE
    buffer[start:start + len(padded)] = padded
    # 多余出来的扇区清零（保守：不清空位置表，只清内容）
    spare_start = start + len(padded)
    spare_end = start + chunk.sectors * SECTOR_SIZE
    if spare_end > spare_start:
        buffer[spare_start:spare_end] = b"\x00" * (spare_end - spare_start)

    _atomic_write(bytes(buffer), path)
    verify_region(path)
    return plan


def _chunk_payload_size(path: Path, offset: int) -> int:
    """读一个区块数据块的长度（含 5 字节块头）。"""
    try:
        with path.open("rb") as handle:
            handle.seek(offset * SECTOR_SIZE)
            header = handle.read(5)
    except OSError as e:                                # pragma: no cover
        raise RegionWriteError(f"读取区块头失败：{e}") from e
    if len(header) < 5:
        raise RegionWriteError("区块头不完整")
    length = int.from_bytes(header[:4], "big")
    return length + 4


def verify_region(path: str | Path) -> list[str]:
    """写后自检：位置表 + 每个区块的块头是否自洽；返回问题列表。"""
    path = Path(path)
    problems: list[str] = []
    info = read_region_index(path)
    if info.error:
        return [info.error]
    try:
        size = path.stat().st_size
    except OSError as e:                                # pragma: no cover
        return [str(e)]
    for chunk in info.chunks:
        if chunk.offset * SECTOR_SIZE + chunk.sectors * SECTOR_SIZE > size:
            problems.append(
                f"({chunk.x},{chunk.z}) 超出文件末尾")
            continue
        try:
            length = _chunk_payload_size(path, chunk.offset)
        except RegionWriteError as e:
            problems.append(f"({chunk.x},{chunk.z}) {e}")
            continue
        if length > chunk.sectors * SECTOR_SIZE:
            problems.append(
                f"({chunk.x},{chunk.z}) 数据块长度 {length} "
                f"超过 {chunk.sectors} 个扇区")
    return problems


def _atomic_write(data: bytes, destination: Path) -> None:
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


def backup_region(path: str | Path, folder: str | Path) -> Path:
    """把区域文件复制到备份目录（写前必做）。"""
    source = Path(path)
    target = Path(folder) / "region" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


# ---------------------------------------------------------------------------
# 工作区预设（一键场景 / 规则组合的保存与复用）
# ---------------------------------------------------------------------------

#: 内置预设
BUILTIN_PRESETS: tuple[dict, ...] = (
    {"id": "creative_sandbox", "name": "创造沙盒",
     "note": "白天 + 无天气 + 不刷怪 + 保留物品",
     "payload": {"permanent_day": True, "no_weather": True,
                 "peaceful": True, "gamerules": {
                     "doMobSpawning": "false", "keepInventory": "true",
                     "doDaylightCycle": "false", "doWeatherCycle": "false"}}},
    {"id": "hardcore_survival", "name": "硬核生存",
     "note": "困难 + 昼夜与天气循环 + 死亡掉落",
     "payload": {"difficulty": "hard", "gamerules": {
         "keepInventory": "false", "doDaylightCycle": "true",
         "doWeatherCycle": "true", "naturalRegeneration": "false"}}},
    {"id": "build_showcase", "name": "建筑展示",
     "note": "永远白天 + 晴天 + 不刷怪 + 允许命令",
     "payload": {"permanent_day": True, "no_weather": True,
                 "allow_commands": True, "gamerules": {
                     "doMobSpawning": "false", "doWeatherCycle": "false",
                     "doFireTick": "false", "mobGriefing": "false"}}},
    {"id": "pvp_arena", "name": "PvP 竞技场",
     "note": "困难 + 锁定边界 + 保留物品 + 关闭自然回血",
     "payload": {"difficulty": "hard", "worldborder": {"size": 500.0,
                                                        "damage": 0.5},
                 "gamerules": {"keepInventory": "true",
                               "naturalRegeneration": "false",
                               "doDaylightCycle": "false"}}},
)


def _presets_dir() -> Path:
    from app.storage import user_data_dir
    path = user_data_dir() / "presets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_presets() -> list[dict]:
    """内置 + 用户预设（用户同名覆盖内置）。"""
    import json
    out: list[dict] = [dict(item, builtin=True)
                       for item in BUILTIN_PRESETS]
    for path in sorted(_presets_dir().glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):                   # pragma: no cover
            continue
        if not isinstance(payload, dict):
            continue
        payload.setdefault("id", path.stem)
        payload["builtin"] = False
        for index, existing in enumerate(out):
            if existing["id"] == payload["id"]:
                out[index] = payload
                break
        else:
            out.append(payload)
    return out


def get_preset(key: str) -> dict | None:
    wanted = str(key or "").strip().lower()
    for preset in list_presets():
        if wanted in (str(preset.get("id", "")).lower(),
                      str(preset.get("name", "")).lower()):
            return preset
    return None


def save_preset(preset: dict) -> Path:
    """保存用户预设（JSON 落在 user_data/presets/）。"""
    import json
    from app.core.blueprint import _safe_name
    key = str(preset.get("id") or preset.get("name") or "preset")
    path = _presets_dir() / f"{_safe_name(key)}.json"
    payload = {k: v for k, v in preset.items() if k != "builtin"}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def preset_to_operation(preset: dict):
    """把一个工作区预设变成 `scene` 桥接操作。"""
    from app.core.bridge import BridgeOperation, operation_runner
    payload = dict(preset.get("payload") or {})
    if "difficulty" in payload and "peaceful" not in payload:
        payload["peaceful"] = payload["difficulty"] == "peaceful"
    border = payload.get("worldborder") or {}
    if border:
        payload.setdefault("border_size", border.get("size"))
        payload.setdefault("border_damage", border.get("damage"))
    operation = BridgeOperation(
        kind="scene", label=f"套用预设「{preset.get('name', '未命名')}」",
        source="editor", target="level", payload=payload)
    operation.runner = operation_runner(operation)
    operation.notes.append(str(preset.get("note", "")))
    return operation


def preset_summary(preset: dict) -> str:
    payload = preset.get("payload") or {}
    parts: list[str] = []
    for key, label in (("permanent_day", "永久白天"), ("no_weather", "禁止天气"),
                       ("peaceful", "和平"), ("allow_commands", "允许命令")):
        if payload.get(key):
            parts.append(label)
    if payload.get("difficulty"):
        parts.append(f"难度 {payload['difficulty']}")
    border = payload.get("worldborder") or {}
    if border:
        parts.append(f"边界 {border.get('size', '—')}")
    rules = payload.get("gamerules") or {}
    if rules:
        parts.append(f"{len(rules)} 条规则")
    return "、".join(parts) or "（无可应用的设置）"


__all__ = [
    "RegionWriteError", "RegionWritePlan", "COMPRESSION_CODES",
    "sectorize", "build_chunk_payload", "plan_chunk_write", "write_chunk",
    "verify_region", "backup_region",
    "BUILTIN_PRESETS", "list_presets", "get_preset", "save_preset",
    "preset_to_operation", "preset_summary",
]
