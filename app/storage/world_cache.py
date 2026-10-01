"""世界索引缓存（SQLite）。

大存档每次打开都要遍历 region / playerdata / data 目录，几百毫秒到几秒。
这里把「上次扫描结果」按 (路径, level.dat 的 mtime+size) 缓存起来：

* 命中且签名一致 → 直接用缓存渲染总览，跳过重量级扫描；
* 签名变化 → 重新扫描并覆盖；
* 缓存里只存**摘要**（JSON），不存 NBT 本体，几十 KB 量级。

数据库沿用应用数据目录的 data.db（新增三张表，旧库自动建表，不影响旧数据）。
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from app.storage import Database

_SCHEMA = """
CREATE TABLE IF NOT EXISTS worlds (
    path TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    data_version INTEGER,
    version_label TEXT NOT NULL DEFAULT '',
    last_played TEXT NOT NULL DEFAULT '',
    players INTEGER NOT NULL DEFAULT 0,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    signature TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_worlds_updated ON worlds(updated_at DESC);

CREATE TABLE IF NOT EXISTS world_players (
    path TEXT NOT NULL,
    uuid TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    game_type TEXT NOT NULL DEFAULT '',
    dimension TEXT NOT NULL DEFAULT '',
    health REAL NOT NULL DEFAULT 0,
    xp_level INTEGER NOT NULL DEFAULT 0,
    item_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (path, uuid)
);

CREATE TABLE IF NOT EXISTS world_regions (
    path TEXT NOT NULL,
    file TEXT NOT NULL,
    dimension TEXT NOT NULL DEFAULT '',
    chunks INTEGER NOT NULL DEFAULT 0,
    present INTEGER NOT NULL DEFAULT 0,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (path, file)
);
"""


def signature_of(world_path: Path) -> str:
    """用 level.dat 的 mtime + size 做缓存签名（秒级够用）。"""
    level = Path(world_path) / "level.dat"
    try:
        stat = level.stat()
    except OSError:
        return ""
    return f"{int(stat.st_mtime)}:{stat.st_size}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class WorldIndexCache:
    """世界摘要缓存。构造失败（无数据库）时全部方法退化为 no-op。"""

    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database.instance()
        try:
            self.db.conn.executescript(_SCHEMA)
            self.db.conn.commit()
        except Exception:                               # pragma: no cover
            pass

    # ---- 世界摘要 ----

    def load(self, world_path: str | Path) -> dict[str, Any] | None:
        key = str(Path(world_path))
        try:
            cur = self.db.conn.execute(
                "SELECT signature, payload, updated_at FROM worlds "
                "WHERE path=?", (key,))
            row = cur.fetchone()
        except Exception:                               # pragma: no cover
            return None
        if row is None:
            return None
        payload = _loads(row["payload"])
        if not isinstance(payload, dict):               # pragma: no cover
            return None
        payload["_cached_at"] = str(row["updated_at"])
        payload["_stale"] = str(row["signature"]) != signature_of(key)
        return payload

    def store(self, world, *, payload: dict[str, Any] | None = None) -> None:
        """写入一个已经打开的世界（WorldSave）。"""
        path = str(Path(world.path))
        overview = world.overview
        data = payload if payload is not None else _jsonable(overview)
        try:
            self.db.conn.execute(
                "INSERT INTO worlds(path, name, data_version, version_label,"
                " last_played, players, size_bytes, signature, payload,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?,?, datetime('now'))"
                " ON CONFLICT(path) DO UPDATE SET name=excluded.name,"
                " data_version=excluded.data_version,"
                " version_label=excluded.version_label,"
                " last_played=excluded.last_played, players=excluded.players,"
                " size_bytes=excluded.size_bytes, signature=excluded.signature,"
                " payload=excluded.payload, updated_at=datetime('now')",
                (path, overview.name, overview.data_version or 0,
                 overview.version_label, overview.last_played,
                 len(overview.players), overview.size_bytes,
                 signature_of(path),
                 json.dumps(data, ensure_ascii=False)))
            self.db.conn.commit()
        except Exception:                               # pragma: no cover
            return
        self.store_players(world)
        self.store_regions(world)

    def store_players(self, world) -> None:
        path = str(Path(world.path))
        try:
            for uuid, player in world.players.items():
                self.db.conn.execute(
                    "INSERT INTO world_players(path, uuid, name, game_type,"
                    " dimension, health, xp_level, item_count, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?, datetime('now'))"
                    " ON CONFLICT(path, uuid) DO UPDATE SET name=excluded.name,"
                    " game_type=excluded.game_type,"
                    " dimension=excluded.dimension, health=excluded.health,"
                    " xp_level=excluded.xp_level,"
                    " item_count=excluded.item_count,"
                    " updated_at=datetime('now')",
                    (path, uuid, player.name, player.game_type,
                     player.dimension, float(player.health), player.xp_level,
                     player.item_count))
            self.db.conn.commit()
        except Exception:                               # pragma: no cover
            return

    def store_regions(self, world) -> None:
        path = str(Path(world.path))
        try:
            for region in world.regions.values():
                self.db.conn.execute(
                    "INSERT INTO world_regions(path, file, dimension, chunks,"
                    " present, size_bytes, updated_at)"
                    " VALUES (?,?,?,?,?,?, datetime('now'))"
                    " ON CONFLICT(path, file) DO UPDATE SET"
                    " dimension=excluded.dimension, chunks=excluded.chunks,"
                    " present=excluded.present, size_bytes=excluded.size_bytes,"
                    " updated_at=datetime('now')",
                    (path, region.name, region.dimension, len(region.chunks),
                     region.present, region.size_bytes))
            self.db.conn.commit()
        except Exception:                               # pragma: no cover
            return

    # ---- 查询 ----

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        try:
            cur = self.db.conn.execute(
                "SELECT path, name, data_version, version_label, last_played,"
                " players, size_bytes, updated_at FROM worlds"
                " ORDER BY updated_at DESC LIMIT ?", (int(limit),))
            rows = cur.fetchall()
        except Exception:                               # pragma: no cover
            return []
        return [dict(row) for row in rows]

    def players(self, world_path: str | Path) -> list[dict[str, Any]]:
        try:
            cur = self.db.conn.execute(
                "SELECT uuid, name, game_type, dimension, health, xp_level,"
                " item_count FROM world_players WHERE path=?"
                " ORDER BY uuid", (str(Path(world_path)),))
            rows = cur.fetchall()
        except Exception:                               # pragma: no cover
            return []
        return [dict(row) for row in rows]

    def regions(self, world_path: str | Path) -> list[dict[str, Any]]:
        try:
            cur = self.db.conn.execute(
                "SELECT file, dimension, chunks, present, size_bytes"
                " FROM world_regions WHERE path=? ORDER BY file",
                (str(Path(world_path)),))
            rows = cur.fetchall()
        except Exception:                               # pragma: no cover
            return []
        return [dict(row) for row in rows]

    def forget(self, world_path: str | Path) -> None:
        key = str(Path(world_path))
        try:
            for table in ("worlds", "world_players", "world_regions"):
                self.db.conn.execute(f"DELETE FROM {table} WHERE path=?", (key,))
            self.db.conn.commit()
        except Exception:                               # pragma: no cover
            pass

    def clear(self) -> None:
        try:
            for table in ("worlds", "world_players", "world_regions"):
                self.db.conn.execute(f"DELETE FROM {table}")
            self.db.conn.commit()
        except Exception:                               # pragma: no cover
            pass


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def cached_overview_line(entry: dict[str, Any]) -> str:
    """缓存项 → 一行摘要（最近打开列表用）。"""
    mark = "（缓存已过期，打开会重新扫描）" if entry.get("_stale") else ""
    parts = [
        str(entry.get("name") or Path(str(entry.get("path", ""))).name),
        f"DataVersion {entry.get('data_version')}",
        f"{entry.get('players')} 名玩家",
        str(entry.get("_cached_at", ""))[:19],
    ]
    if mark:
        parts.append(mark)
    return " · ".join(part for part in parts if part)


__all__ = ["WorldIndexCache", "signature_of", "cached_overview_line"]
