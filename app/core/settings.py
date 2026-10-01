"""应用设置与「专有名词翻译」。

界面上的技术名词（NBT 类型、物品分类、常用术语）都可以翻译成中文，
既内置了一套默认翻译，也允许用户在「设置 → 专有名词翻译」里逐条覆盖。

设置存在应用数据目录的 `user_data/settings.json`，格式：

```json
{
  "terms": {
    "nbt_types": {"Byte": "字节", "Compound": "复合标签"},
    "categories": {"Boss": "Boss"},
    "words": {"String": "字符串"},
    "use_translations": true,
    "scope": "all"
  },
  "nbt": {"show_icons": true, "item_summary": true}
}
```
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from app.storage import user_data_dir

SETTINGS_FILE = "settings.json"

# ---------------------------------------------------------------------------
# 默认翻译
# ---------------------------------------------------------------------------

#: NBT 标签类型（键与 app/ui/widgets/nbt_tags.py 的 TYPE_LABELS 一致）
DEFAULT_NBT_TYPES: dict[str, str] = {
    "Byte": "字节",
    "Short": "短整型",
    "Int": "整型",
    "Long": "长整型",
    "Float": "单精度浮点",
    "Double": "双精度浮点",
    "String": "字符串",
    "Bool": "布尔",
    "List": "列表",
    "Compound": "复合标签",
    "ByteArray": "字节数组",
    "IntArray": "整型数组",
    "LongArray": "长整型数组",
}

#: 注册表里的分类（物品 / 实体 / 方块共用一套）
DEFAULT_CATEGORIES: dict[str, str] = {
    "Boss": "Boss",
    "building": "建筑",
    "decoration": "装饰",
    "equipment": "装备",
    "food": "食物",
    "material": "材料",
    "misc": "杂项",
    "redstone": "红石",
    "tool": "工具",
    "transportation": "交通",
    "combat": "战斗",
    "brewing": "酿造",
    "spawn_egg": "刷怪蛋",
    "hostile": "敌对",
    "passive": "被动",
    "neutral": "中立",
    "mob": "生物",
    "projectile": "投掷物",
    "vehicle": "载具",
    "ambient": "环境",
    "water": "水生",
    "光源": "光源",
    "建筑": "建筑",
    "交通": "交通",
    "装饰": "装饰",
    "装备": "装备",
    "材料": "材料",
    "工具": "工具",
    "食物": "食物",
    "红石": "红石",
    "杂项": "杂项",
    "战斗": "战斗",
}

#: 常用术语 / 字段名（覆盖 NBT 键名、命令参数等零散名词）
DEFAULT_WORDS: dict[str, str] = {
    "String": "字符串",
    "Compound": "复合标签",
    "List": "列表",
    "Byte": "字节",
    "Short": "短整型",
    "Int": "整型",
    "Long": "长整型",
    "Float": "单精度浮点",
    "Double": "双精度浮点",
    "Array": "数组",
    "Slot": "槽位",
    "Count": "数量",
    "id": "标识",
    "Inventory": "物品栏",
    "EnderItems": "末影箱",
    "ArmorItems": "装备栏",
    "Items": "物品列表",
    "Pos": "坐标",
    "Health": "生命值",
    "Attributes": "属性",
    "CustomName": "自定义名称",
    "components": "物品组件",
    # Forge / NeoForge 的 mod 清单（level.dat 里的 fml 段）
    "fml": "模组加载器",
    "LoadingModList": "已加载模组列表",
    "ModFileList": "模组文件列表",
    "ModId": "模组 ID",
    "ModSize": "模组大小(字节)",
    "ModLocation": "模组位置",
    "ModName": "模组名称",
    "ModVersion": "模组版本",
    "ModLoader": "模组加载器",
    "ModSide": "模组侧",
    "MCVersion": "游戏版本",
    "DataVersion": "数据版本",
}

TERM_GROUPS: tuple[tuple[str, str, str], ...] = (
    ("nbt_types", "NBT 类型", "树形视图「类型」列与详情面板里的标签类型"),
    ("categories", "注册表分类", "ID 速查与物品浏览里的分类名"),
    ("words", "其它术语", "其余零散的技术名词"),
)

DEFAULT_SETTINGS: dict[str, Any] = {
    "terms": {
        "use_translations": True,
        # all = 全部翻译；types = 只翻译 NBT 类型；none = 全部显示英文原名
        "scope": "all",
        "nbt_types": {},
        "categories": {},
        "words": {},
    },
    "nbt": {
        "show_icons": True,
        "item_summary": True,
    },
}

# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------

_cache: dict[str, Any] | None = None
_listeners: list[Callable[[dict], None]] = []
_path_override: Path | None = None


def settings_path() -> Path:
    if _path_override is not None:
        return _path_override
    return user_data_dir() / SETTINGS_FILE


def _merge_defaults(raw: dict) -> dict:
    """把读到的设置与默认值合并（缺键补默认，未知键保留）。"""
    out = json.loads(json.dumps(DEFAULT_SETTINGS))       # 深拷贝
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key].update(value)
        else:
            out[key] = value
    return out


def load(force: bool = False) -> dict:
    """读取设置（带缓存）。"""
    global _cache
    if _cache is not None and not force:
        return _cache
    raw: dict = {}
    path = settings_path()
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                raw = data
        except Exception:
            raw = {}
    _cache = _merge_defaults(raw)
    return _cache


def save(data: dict | None = None) -> bool:
    """写回设置；data 为空时写当前缓存。"""
    global _cache
    if data is not None:
        _cache = _merge_defaults(data)
    current = _cache if _cache is not None else load()
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(current, handle, ensure_ascii=False, indent=2)
    except Exception:
        return False
    notify()
    return True


def reset() -> None:
    """恢复默认设置（只清缓存与文件内容，不动其它数据）。"""
    global _cache
    _cache = json.loads(json.dumps(DEFAULT_SETTINGS))
    save()


def set_value(path: str, value: Any) -> None:
    """按 `terms.scope` 这样的点分路径写一个值。"""
    data = load()
    node = data
    keys = path.split(".")
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value
    save(data)


# ---------------------------------------------------------------------------
# 订阅（设置变了要刷新界面）
# ---------------------------------------------------------------------------

def subscribe(callback: Callable[[dict], None]) -> Callable[[], None]:
    """注册设置变更回调，返回取消订阅的函数。"""
    if callback not in _listeners:
        _listeners.append(callback)

    def unsubscribe() -> None:
        if callback in _listeners:
            _listeners.remove(callback)

    return unsubscribe


def notify() -> None:
    data = load()
    for callback in list(_listeners):
        try:
            callback(data)
        except Exception:
            continue


# ---------------------------------------------------------------------------
# 翻译查询
# ---------------------------------------------------------------------------

def use_translations() -> bool:
    return bool(load().get("terms", {}).get("use_translations", True))


def scope() -> str:
    """all / types / none。"""
    value = str(load().get("terms", {}).get("scope", "all") or "all")
    return value if value in ("all", "types", "none") else "all"


def _lookup(group: str, key: str) -> str:
    terms = load().get("terms", {})
    custom = terms.get(group) or {}
    if key in custom and str(custom[key]).strip():
        return str(custom[key]).strip()
    defaults = {"nbt_types": DEFAULT_NBT_TYPES,
                "categories": DEFAULT_CATEGORIES,
                "words": DEFAULT_WORDS}.get(group, {})
    return str(defaults.get(key, "")) or key


def translate_nbt_type(label: str) -> str:
    """NBT 类型名 → 显示名（例如 String → 字符串）。"""
    scope_now = scope()
    if scope_now == "none":
        return label
    if not use_translations():
        return label
    # 兼容 TAG_String 这种写法
    bare = label[4:] if label.startswith("TAG_") else label
    return _lookup("nbt_types", bare)


def translate_category(label: str) -> str:
    if scope() != "all" or not use_translations():
        return label
    return _lookup("categories", label)


def translate_word(label: str) -> str:
    if scope() != "all" or not use_translations():
        return label
    return _lookup("words", label)


def type_label_with_source(label: str) -> tuple[str, str]:
    """返回 (显示名, 原文)；原文用于 tooltip，方便对照。"""
    translated = translate_nbt_type(label)
    return translated, label


def nbt_show_icons() -> bool:
    return bool(load().get("nbt", {}).get("show_icons", True))


def nbt_item_summary() -> bool:
    return bool(load().get("nbt", {}).get("item_summary", True))


def effective_terms(group: str) -> dict[str, str]:
    """某组「当前生效」的完整翻译表（默认 + 用户覆盖），供设置界面展示。"""
    defaults = {"nbt_types": DEFAULT_NBT_TYPES,
                "categories": DEFAULT_CATEGORIES,
                "words": DEFAULT_WORDS}.get(group, {})
    custom = (load().get("terms", {}).get(group) or {})
    merged = dict(defaults)
    for key, value in custom.items():
        if str(value).strip():
            merged[key] = str(value).strip()
    return merged


__all__ = [
    "SETTINGS_FILE", "DEFAULT_NBT_TYPES", "DEFAULT_CATEGORIES",
    "DEFAULT_WORDS", "TERM_GROUPS", "DEFAULT_SETTINGS",
    "settings_path", "load", "save", "reset", "set_value",
    "subscribe", "notify", "use_translations", "scope",
    "translate_nbt_type", "translate_category", "translate_word",
    "type_label_with_source", "nbt_show_icons", "nbt_item_summary",
    "effective_terms",
]
