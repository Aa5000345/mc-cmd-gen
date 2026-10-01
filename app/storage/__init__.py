"""SQLite 存储层：数据库 + 历史 + 预设（含签名/依赖）+ 片段 + 草稿。"""
from __future__ import annotations
import atexit
import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from app import branding

# 是否已登记退出钩子
_ATEXIT_REGISTERED = False


# ---------- 路径 ----------
#
# 改名（MCCmdGen → MCStudio）后仍然要兼容旧数据：
#   * 环境变量 MCSTUDIO_DATA_DIR（新）/ MCCMDGEN_DATA_DIR（旧）都可显式指定目录；
#   * 新目录 .../mcstudio 优先；如果新目录还没建立、而旧目录 .../MCCmdGen 有数据，
#     就继续用旧目录（此时会由 app.migration 在启动时把数据复制到新目录）；
#   * 迁移是「复制 + 保留旧目录」，用户随时可以回退。

APP_DIR_NAME = branding.APP_DIR_NAME
LEGACY_APP_DIR_NAMES = branding.LEGACY_APP_DIR_NAMES

#: 兼容旧代码：历史上唯一的目录名
LEGACY_APP_DIR_NAME = LEGACY_APP_DIR_NAMES[0]


def _legacy_dir(base: Path | None = None) -> Path:
    """旧数据目录（始终存在，不创建）。"""
    if base is not None:
        return Path(base) / LEGACY_APP_DIR_NAME
    return branding.platform_data_dir(LEGACY_APP_DIR_NAME)


def _override_dir() -> Path | None:
    """环境变量显式指定目录。"""
    return branding.data_dir_override()


def _fallback_dir(base: Path | None = None) -> Path:
    """不依赖 Qt 的数据目录（与 README 描述一致）。"""
    if base is not None:
        return Path(base) / APP_DIR_NAME
    return branding.platform_data_dir(APP_DIR_NAME)


def _prefer_legacy(base: Path | None = None) -> bool:
    """新目录不存在、旧目录有内容时继续用旧目录（改名的平滑过渡）。"""
    try:
        if _fallback_dir(base).exists():
            return False
    except OSError:                                         # pragma: no cover
        return False
    try:
        old = _legacy_dir(base)
        return old.is_dir() and any(old.iterdir())
    except OSError:                                         # pragma: no cover
        return False


def _app_dir() -> Path:
    """应用数据目录。

    优先走系统约定路径（与 README 一致、结果确定），只有在环境变量
    缺失等情况下才回退到 QStandardPaths，避免出现
    “开不开 QApplication 会得到两个不同目录”的问题。
    """
    override = _override_dir()
    if override is not None:
        path = override
    else:
        try:
            path = _legacy_dir() if _prefer_legacy() else _fallback_dir()
        except Exception:                                   # pragma: no cover
            path = None
        if path is None:                                    # pragma: no cover
            path = _qt_dir()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = _qt_dir()
        path.mkdir(parents=True, exist_ok=True)
    return path


def migrate_legacy_data(force: bool = False):
    """把旧数据目录的内容迁移到新目录；返回 MigrationReport。

    延迟导入 `app.migration`，避免 storage 在导入期就依赖品牌模块。
    """
    from app.migration import migrate_data_dir
    return migrate_data_dir(force=force)


def _qt_dir() -> Path:
    """回退方案：QStandardPaths 的本地应用数据目录。"""
    try:
        from PySide6.QtCore import QStandardPaths
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation)
        if base:
            candidate = Path(base)
            if not _has_app_component(candidate):
                candidate = candidate / APP_DIR_NAME
            return candidate
    except Exception:
        pass
    return Path.home() / f".{APP_DIR_NAME.lower()}"


def _has_app_component(path: Path) -> bool:
    """路径中是否已经带有 mcstudio / MCCmdGen 段（避免出现重复目录）。"""
    names = {APP_DIR_NAME.lower()} | {n.lower() for n in LEGACY_APP_DIR_NAMES}
    return any(part.lower() in names for part in path.parts[-3:])


def app_data_dir() -> Path:
    return _app_dir()


def user_data_dir() -> Path:
    path = _app_dir() / "user_data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return _app_dir() / "data.db"


# ---------- 数据库 ----------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    command_type TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_history_created_at ON history(created_at DESC);

CREATE TABLE IF NOT EXISTS presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '默认',
    command TEXT NOT NULL,
    command_type TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_presets_category ON presets(category);

CREATE TABLE IF NOT EXISTS fragments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'command',
    category TEXT NOT NULL DEFAULT '默认',
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fragments_category ON fragments(category);
CREATE INDEX IF NOT EXISTS idx_fragments_kind ON fragments(kind);

CREATE TABLE IF NOT EXISTS drafts (
    key TEXT PRIMARY KEY,
    payload TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Database:
    _instance: "Database | None" = None
    _lock = threading.Lock()

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or db_path()
        self._closed = False
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.executescript(
                "PRAGMA journal_mode=WAL;"
                "PRAGMA synchronous=NORMAL;"
                "PRAGMA temp_store=MEMORY;"
            )
        except Exception:
            pass
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @classmethod
    def instance(cls) -> "Database":
        if cls._instance is None:
            cls.register_atexit()
            with cls._lock:
                if cls._instance is None:
                    # 改名兼容：第一次访问数据前，把旧目录数据复制到新目录
                    try:
                        migrate_legacy_data()
                    except Exception:                       # pragma: no cover
                        pass
                    cls._instance = Database()
        return cls._instance

    @classmethod
    def shutdown(cls) -> None:
        """进程退出前关闭连接（WAL 落盘），可重复调用。"""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.close()
                cls._instance = None

    @classmethod
    def register_atexit(cls) -> None:
        """登记退出钩子，避免 WAL 文件残留。"""
        global _ATEXIT_REGISTERED
        with cls._lock:
            if _ATEXIT_REGISTERED:
                return
            _ATEXIT_REGISTERED = True
        atexit.register(cls.shutdown)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._closed:
            # 曾经被 shutdown() 关过（例如测试里先后建了两个主窗口），
            # 这里透明重连，避免调用方拿到已关闭的连接。
            self._reopen()
        return self._conn

    def _reopen(self) -> None:
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._closed = False

    def close(self) -> None:
        try:
            self._conn.close()
        finally:
            self._closed = True


# ---------- 历史 ----------

@dataclass
class HistoryEntry:
    id: int
    command: str
    command_type: str
    created_at: str


class HistoryStore:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database.instance()

    def add(self, command: str, command_type: str = "",
            dedupe_window: int = 5) -> int | None:
        command = command.strip()
        if not command:
            return None
        cur = self.db.conn.execute(
            "SELECT command FROM history ORDER BY id DESC LIMIT ?",
            (dedupe_window,))
        for row in cur.fetchall():
            if row["command"] == command:
                return None
        cur = self.db.conn.execute(
            "INSERT INTO history(command, command_type) VALUES (?, ?)",
            (command, command_type))
        self.db.conn.commit()
        return cur.lastrowid

    def list(self, keyword: str = "", limit: int = 300) -> list[HistoryEntry]:
        if keyword.strip():
            like = f"%{keyword.strip()}%"
            cur = self.db.conn.execute(
                "SELECT id, command, command_type, created_at FROM history "
                "WHERE command LIKE ? ORDER BY id DESC LIMIT ?", (like, limit))
        else:
            cur = self.db.conn.execute(
                "SELECT id, command, command_type, created_at FROM history "
                "ORDER BY id DESC LIMIT ?", (limit,))
        return [HistoryEntry(int(r["id"]), str(r["command"]),
                             str(r["command_type"]), str(r["created_at"]))
                for r in cur.fetchall()]

    def delete(self, entry_id: int) -> None:
        self.db.conn.execute("DELETE FROM history WHERE id=?", (entry_id,))
        self.db.conn.commit()

    def clear(self) -> None:
        self.db.conn.execute("DELETE FROM history")
        self.db.conn.commit()

    def count(self) -> int:
        cur = self.db.conn.execute("SELECT COUNT(*) AS c FROM history")
        return int(cur.fetchone()["c"])

    def recent_values(self, command_type: str, field_index: int,
                      limit: int = 8) -> list[str]:
        """从历史命令中提取第 field_index 个 token（0 为命令名）。"""
        cur = self.db.conn.execute(
            "SELECT command FROM history WHERE command_type=? "
            "ORDER BY id DESC LIMIT 200", (command_type,))
        seen, out = set(), []
        for row in cur.fetchall():
            tokens = row["command"].strip().lstrip("/").split()
            if len(tokens) <= field_index:
                continue
            val = tokens[field_index]
            if val in seen:
                continue
            seen.add(val)
            out.append(val)
            if len(out) >= limit:
                break
        return out


# ---------- 预设 ----------

EXPORT_FORMAT_VERSION = 2
TARGET_MC_VERSION = "1.21.1"


@dataclass
class Preset:
    id: int
    name: str
    category: str
    command: str
    command_type: str
    created_at: str
    updated_at: str


class PresetStore:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database.instance()

    def add(self, name, category, command, command_type="") -> int:
        cur = self.db.conn.execute(
            "INSERT INTO presets(name, category, command, command_type) VALUES (?,?,?,?)",
            (name.strip() or "未命名", category.strip() or "默认",
             command.strip(), command_type))
        self.db.conn.commit()
        return cur.lastrowid

    def update(self, pid, name, category, command, command_type="") -> None:
        self.db.conn.execute(
            "UPDATE presets SET name=?, category=?, command=?, command_type=?, "
            "updated_at=datetime('now') WHERE id=?",
            (name.strip() or "未命名", category.strip() or "默认",
             command.strip(), command_type, pid))
        self.db.conn.commit()

    def delete(self, pid: int) -> None:
        self.db.conn.execute("DELETE FROM presets WHERE id=?", (pid,))
        self.db.conn.commit()

    def list(self, keyword: str = "") -> list[Preset]:
        if keyword.strip():
            like = f"%{keyword.strip()}%"
            cur = self.db.conn.execute(
                "SELECT * FROM presets WHERE name LIKE ? OR command LIKE ? "
                "ORDER BY category, name", (like, like))
        else:
            cur = self.db.conn.execute("SELECT * FROM presets ORDER BY category, name")
        return [Preset(**dict(r)) for r in cur.fetchall()]

    def categories(self) -> list[str]:
        cur = self.db.conn.execute(
            "SELECT DISTINCT category FROM presets ORDER BY category")
        return [r["category"] for r in cur.fetchall()]

    def export_json(self, path, items: Iterable[Preset] | None = None,
                    app_version: str | None = None) -> int:
        from app.core.datapack import extract_placeholders
        if app_version is None:
            try:
                from app import __version__ as app_version  # type: ignore[no-redef]
            except Exception:                          # pragma: no cover
                app_version = "unknown"
        items = list(items if items is not None else self.list())
        placeholders: set[str] = set()
        for p in items:
            placeholders.update(extract_placeholders(p.command))

        presets_payload = [
            {"name": p.name, "category": p.category,
             "command": p.command, "command_type": p.command_type}
            for p in items
        ]

        body = json.dumps(presets_payload, ensure_ascii=False, sort_keys=True)
        checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()

        payload = {
            "manifest": {
                "format": EXPORT_FORMAT_VERSION,
                "generator": branding.GENERATOR,
                "generator_version": app_version,
                "minecraft_version": TARGET_MC_VERSION,
                "exported_at": datetime.now().isoformat(timespec="seconds"),
                "count": len(items),
                "placeholders": sorted(placeholders),
                "checksum": checksum,
                "dependencies": {
                    "entities.json": TARGET_MC_VERSION,
                    "items.json": TARGET_MC_VERSION,
                    "item_components.json": TARGET_MC_VERSION,
                },
            },
            "presets": presets_payload,
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        return len(items)

    def import_json(self, path, merge: bool = True):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        notes: list[str] = []
        if isinstance(data, dict) and "manifest" in data:
            m = data.get("manifest") or {}
            mc_ver = str(m.get("minecraft_version", "")).strip()
            fmt = m.get("format", 1)
            gen = m.get("generator", "未知")
            if mc_ver and mc_ver != TARGET_MC_VERSION:
                notes.append(f"该配方包来自 Minecraft {mc_ver}（目标 {TARGET_MC_VERSION}）。"
                             "建议先使用「迁移命令」功能。")
            if isinstance(fmt, int) and fmt > EXPORT_FORMAT_VERSION:
                notes.append(f"格式 v{fmt} 高于本工具支持的 v{EXPORT_FORMAT_VERSION}。")
            if gen and gen not in branding.GENERATOR_ALIASES:
                notes.append(f"由 {gen} 生成，字段可能不完全兼容。")

            deps = m.get("dependencies") or {}
            for fname, ver in deps.items():
                if ver != TARGET_MC_VERSION:
                    notes.append(f"依赖 {fname} 版本为 {ver}，当前为 {TARGET_MC_VERSION}。")

            entries = data.get("presets") or []

            expected = m.get("checksum")
            if expected:
                body = json.dumps(entries, ensure_ascii=False, sort_keys=True)
                actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
                if actual != expected:
                    notes.append("警告：checksum 不匹配，文件可能被修改。")
        elif isinstance(data, dict):
            entries = data.get("presets") or []
        else:
            entries = data
        if not isinstance(entries, list):
            raise ValueError("不是合法的预设文件")

        existing = set()
        if merge:
            existing = {(p.name, p.category, p.command) for p in self.list()}
        added = skipped = 0
        for e in entries:
            if not isinstance(e, dict):
                skipped += 1
                continue
            name = str(e.get("name", "")).strip()
            category = str(e.get("category", "默认")).strip() or "默认"
            command = str(e.get("command", "")).strip()
            ctype = str(e.get("command_type", "")).strip()
            if not name or not command or (name, category, command) in existing:
                skipped += 1
                continue
            self.add(name, category, command, ctype)
            added += 1
        return added, skipped, notes


# ---------- 片段 ----------

@dataclass
class Fragment:
    id: int
    name: str
    kind: str
    category: str
    payload: dict
    created_at: str
    updated_at: str


class FragmentStore:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database.instance()

    def add(self, name, kind, payload, category="默认") -> int:
        cur = self.db.conn.execute(
            "INSERT INTO fragments(name, kind, category, payload) VALUES (?,?,?,?)",
            (name.strip() or "未命名片段", kind or "command",
             category or "默认", json.dumps(payload or {}, ensure_ascii=False)))
        self.db.conn.commit()
        return cur.lastrowid

    def update(self, fid, name, kind, payload, category="默认") -> None:
        self.db.conn.execute(
            "UPDATE fragments SET name=?, kind=?, category=?, payload=?, "
            "updated_at=datetime('now') WHERE id=?",
            (name.strip() or "未命名片段", kind or "command",
             category or "默认", json.dumps(payload or {}, ensure_ascii=False), fid))
        self.db.conn.commit()

    def delete(self, fid: int) -> None:
        self.db.conn.execute("DELETE FROM fragments WHERE id=?", (fid,))
        self.db.conn.commit()

    def list(self, keyword: str = "", kind: str = "") -> list[Fragment]:
        sql = "SELECT * FROM fragments"
        clauses, params = [], []
        if keyword.strip():
            clauses.append("(name LIKE ? OR category LIKE ?)")
            like = f"%{keyword.strip()}%"
            params.extend([like, like])
        if kind.strip():
            clauses.append("kind = ?")
            params.append(kind.strip())
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY category, name"
        cur = self.db.conn.execute(sql, params)
        out = []
        for r in cur.fetchall():
            try:
                payload = json.loads(r["payload"])
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            out.append(Fragment(int(r["id"]), str(r["name"]), str(r["kind"]),
                                str(r["category"]), payload,
                                str(r["created_at"]), str(r["updated_at"])))
        return out

    def get(self, fid: int) -> Fragment | None:
        cur = self.db.conn.execute("SELECT * FROM fragments WHERE id=?", (fid,))
        r = cur.fetchone()
        if not r:
            return None
        try:
            payload = json.loads(r["payload"])
        except Exception:
            payload = {}
        return Fragment(int(r["id"]), str(r["name"]), str(r["kind"]),
                        str(r["category"]), payload,
                        str(r["created_at"]), str(r["updated_at"]))

    def categories(self) -> list[str]:
        cur = self.db.conn.execute(
            "SELECT DISTINCT category FROM fragments ORDER BY category")
        return [r["category"] for r in cur.fetchall()]

    def export_json(self, path, items=None) -> int:
        items = items if items is not None else self.list()
        payload = {"version": 1, "fragments": [
            {"name": f.name, "kind": f.kind, "category": f.category, "payload": f.payload}
            for f in items]}
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        return len(payload["fragments"])

    def import_json(self, path, merge: bool = True):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = data.get("fragments") if isinstance(data, dict) else data
        if not isinstance(entries, list):
            raise ValueError("不是合法的片段文件")
        existing = set()
        if merge:
            existing = {(f.name, f.category, f.kind) for f in self.list()}
        added = skipped = 0
        for e in entries:
            if not isinstance(e, dict):
                skipped += 1
                continue
            name = str(e.get("name", "")).strip()
            kind = str(e.get("kind", "command")).strip() or "command"
            category = str(e.get("category", "默认")).strip() or "默认"
            payload = e.get("payload") or {}
            if not name or not isinstance(payload, dict) \
                    or (name, category, kind) in existing:
                skipped += 1
                continue
            self.add(name, kind, payload, category)
            added += 1
        return added, skipped


# ---------- 草稿 ----------

class DraftStore:
    SESSION_KEY = "session"

    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database.instance()

    def save(self, key: str, payload: dict) -> None:
        if not key:
            return
        self.db.conn.execute(
            "INSERT INTO drafts(key, payload, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, "
            "updated_at=datetime('now')",
            (key, json.dumps(payload or {}, ensure_ascii=False)))
        self.db.conn.commit()

    def load(self, key: str) -> dict | None:
        cur = self.db.conn.execute("SELECT payload FROM drafts WHERE key=?", (key,))
        r = cur.fetchone()
        if not r:
            return None
        try:
            data = json.loads(r["payload"])
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def delete(self, key: str) -> None:
        self.db.conn.execute("DELETE FROM drafts WHERE key=?", (key,))
        self.db.conn.commit()


__all__ = [
    "Database", "HistoryStore", "HistoryEntry",
    "PresetStore", "Preset", "FragmentStore", "Fragment",
    "DraftStore", "app_data_dir", "user_data_dir", "db_path",
    "TARGET_MC_VERSION", "EXPORT_FORMAT_VERSION", "APP_DIR_NAME",
    "LEGACY_APP_DIR_NAMES", "migrate_legacy_data",
]