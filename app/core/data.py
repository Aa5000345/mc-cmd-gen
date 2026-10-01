"""数据层：JSON 加载器 + 联网更新器 + 规则推荐引擎（带二级缓存）。"""
from __future__ import annotations
import json
import urllib.request
from pathlib import Path
from typing import Any

from app.storage import user_data_dir, app_data_dir


_BUILTIN_DIR = Path(__file__).parent.parent / "data" / "builtin"


class DataStore:
    """内置 JSON + 用户覆盖 JSON 的合并加载器。"""

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    def _load_one(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def load_json(self, name: str) -> dict:
        if name in self._cache:
            return self._cache[name]
        builtin = self._load_one(_BUILTIN_DIR / name)
        user = self._load_one(user_data_dir() / name)
        merged = self._merge(name, builtin, user)
        self._cache[name] = merged
        return merged

    @staticmethod
    def _merge(name: str, builtin: dict, user: dict) -> dict:
        if not user:
            return builtin or {}
        key = {"entities.json": "entities",
               "items.json": "items",
               "blocks.json": "blocks",
               "item_components.json": "components",
               "item_presets.json": "presets",
               "enchantments.json": "enchantments"}.get(name)
        if key is None:
            return {**builtin, **user}
        base_list = list(builtin.get(key, []) or [])
        user_list = list(user.get(key, []) or [])
        index = {it.get("id"): i for i, it in enumerate(base_list)
                 if isinstance(it, dict)}
        for it in user_list:
            if not isinstance(it, dict):
                continue
            iid = it.get("id")
            if iid in index:
                base_list[index[iid]] = it
            else:
                base_list.append(it)
        return {**builtin, **user, key: base_list}

    def reload(self) -> None:
        self._cache.clear()

    def entities(self) -> list[dict]:
        return list(self.load_json("entities.json").get("entities", []))

    def items(self) -> list[dict]:
        return list(self.load_json("items.json").get("items", []))

    def blocks(self) -> list[dict]:
        return list(self.load_json("blocks.json").get("blocks", []))

    def item_components(self) -> list[dict]:
        return list(self.load_json("item_components.json").get("components", []))

    def item_component_groups(self) -> list[list[str]]:
        """组件分组顺序：[[组 key, 中文名], ...]。"""
        raw = self.load_json("item_components.json").get("groups", []) or []
        return [[str(pair[0]), str(pair[1])] for pair in raw
                if isinstance(pair, (list, tuple)) and len(pair) == 2]

    def item_presets(self) -> list[dict]:
        return list(self.load_json("item_presets.json").get("presets", []))

    def enchantments(self) -> list[dict]:
        return list(self.load_json("enchantments.json").get("enchantments", []))

    def all_ids(self, kind: str = "any") -> list[dict]:
        """按类别取条目；kind ∈ any/entity/item/block。"""
        if kind == "entity":
            return self.entities()
        if kind == "item":
            return self.items()
        if kind == "block":
            return self.blocks()
        return [*self.entities(), *self.items(), *self.blocks()]


_store: DataStore | None = None


def store() -> DataStore:
    global _store
    if _store is None:
        _store = DataStore()
    return _store


# ---------- 联网更新 ----------

SUPPORTED_FILES = ("entities.json", "items.json", "item_components.json",
                    "item_presets.json", "enchantments.json")


def source_config_path() -> Path:
    return app_data_dir() / "update_source.txt"


def run_update(progress_cb=None) -> tuple[int, str]:
    cfg = source_config_path()
    if not cfg.exists():
        return 0, (f"未配置更新源。请创建：\n{cfg}\n"
                   "内容为数据源基础 URL，例如：https://example.com/mcdata")
    try:
        base_url = cfg.read_text(encoding="utf-8").strip().splitlines()[0].strip().rstrip("/")
    except Exception as e:
        return 0, f"读取更新源配置失败：{e}"
    if not base_url:
        return 0, "更新源 URL 为空。"

    count = 0
    for fname in SUPPORTED_FILES:
        url = f"{base_url}/{fname}"
        if progress_cb:
            progress_cb(f"下载 {url} …")
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            (user_data_dir() / fname).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            count += 1
        except Exception as e:
            if progress_cb:
                progress_cb(f"跳过 {fname}：{e}")
            continue
    if count == 0:
        return 0, "未获取到任何可用的数据文件。"
    return count, f"成功更新 {count} 个数据文件。"


# ---------- 规则推荐（带二级缓存） ----------

class Recommender:
    def __init__(self) -> None:
        self._data: dict | None = None
        self._cache: dict[tuple[str, str, int], list[dict]] = {}

    def _load(self) -> dict:
        if self._data is None:
            data = store().load_json("recommendations.json") or {}
            self._data = data if isinstance(data, dict) else {}
        return self._data

    def reload(self) -> None:
        self._data = None
        self._cache.clear()

    def recommend_for_entity(self, eid, limit=15):
        return self._query("entities", eid, "nbt", limit)

    def recommend_for_item(self, iid, limit=15):
        return self._query("items", iid, "component", limit)

    def recommend_for_command(self, cmd, limit=15):
        return self._query("commands", cmd, "snippet", limit)

    def _query(self, section, key, field, limit):
        cache_key = (section, (key or "").strip(), int(limit))
        if cache_key in self._cache:
            return list(self._cache[cache_key])

        data = self._load()
        bucket = data.get(section) or {}
        key = (key or "").strip()
        if not key or not isinstance(bucket, dict):
            self._cache[cache_key] = []
            return []

        entries = list(bucket.get(key) or [])
        if not entries and ":" in key:
            ns = key.split(":", 1)[0]
            for k, v in bucket.items():
                if k == f"{ns}:*":
                    entries = list(v)
                    break
        if not entries and ":" in key:
            short = key.split(":", 1)[1]
            for k, v in bucket.items():
                if k.endswith(f":*{short}") or k == f"*:{short}":
                    entries = list(v)
                    break

        result = _normalize(entries, field, limit)
        self._cache[cache_key] = result
        return list(result)


def _normalize(entries, field, limit):
    out, seen = [], set()
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        k = str(e.get(field) or e.get("nbt") or e.get("component")
                or e.get("snippet") or "").strip()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(dict(e))
        if len(out) >= limit:
            break
    return out


_singleton: Recommender | None = None


def recommender() -> Recommender:
    global _singleton
    if _singleton is None:
        _singleton = Recommender()
    return _singleton


__all__ = ["DataStore", "store", "Recommender", "recommender",
           "run_update", "source_config_path", "SUPPORTED_FILES"]