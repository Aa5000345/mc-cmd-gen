"""可扩展校验规则（按正则匹配已生成的命令文本）。

用户可在应用数据目录放 validation_rules.json 覆盖内置规则。
"""
from __future__ import annotations

import json
import re

from app.storage import user_data_dir


DEFAULT_RULES = [
    {"id": "missing_block",
     "pattern": r"^/?setblock\s+\S+\s+\S+\s+\S+\s*$",
     "level": "error", "message": "缺少方块 ID"},
    {"id": "missing_item",
     "pattern": r"^/?give\s+\S+\s*$",
     "level": "error", "message": "缺少物品 ID"},
    {"id": "missing_run",
     "pattern": r"^/?execute\b.*\brun\s*$",
     "level": "error", "message": "run 后命令为空"},
    {"id": "bad_coord",
     "pattern": r"(?<![\w.])[+-]?\d+\.\d+\.\d+(?![\w.])",
     "level": "warn", "message": "坐标不应含多个小数点"},
    {"id": "old_enchant",
     "pattern": r"\{Enchantments:",
     "level": "warn",
     "message": "1.21.1 请使用 minecraft:enchantments 组件"},
    {"id": "old_custom_name",
     "pattern": r"display\s*:\s*\{[^}]*Name",
     "level": "warn",
     "message": "display.Name 已改为 minecraft:custom_name"},
    {"id": "old_hand_items",
     "pattern": r"\b(HandItems|ArmorItems|Inventory)\s*:",
     "level": "warn",
     "message": "1.20.5 起 HandItems/ArmorItems 已改为 equipment 组件（实体用 equipment）"},
    {"id": "old_generic_attr",
     "pattern": r"minecraft:generic\.",
     "level": "warn",
     "message": "1.21 起属性名去掉了 generic. 前缀，例如 minecraft:max_health"},
    {"id": "tp_alias",
     "pattern": r"^/?tp\b",
     "level": "info",
     "message": "/tp 仍可用，但建议改用 /teleport"},
    {"id": "chat_via_say",
     "pattern": r"^/?say\b",
     "level": "info",
     "message": "需要富文本格式时建议改用 /tellraw"},
    {"id": "dimension_short",
     "pattern": r"\bin\s+(overworld|the_nether|the_end)\b",
     "level": "warn",
     "message": "维度名建议补全为 minecraft:overworld 形式"},
]


def load_rules() -> list[dict]:
    """读取校验规则：用户目录的 validation_rules.json 优先，否则用内置规则。"""
    user_path = user_data_dir() / "validation_rules.json"
    if user_path.exists():
        try:
            data = json.loads(user_path.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return [r for r in data if isinstance(r, dict) and "pattern" in r]
        except Exception:
            pass
    return list(DEFAULT_RULES)


def validate_text(text: str) -> list[tuple[str, str]]:
    """返回 [(level, message), ...]。level ∈ error/warn/info。"""
    out: list[tuple[str, str]] = []
    if not text or not text.strip():
        return out
    for rule in load_rules():
        try:
            if re.search(rule["pattern"], text):
                out.append((str(rule.get("level", "info")),
                            str(rule.get("message", rule.get("id", "校验提示")))))
        except re.error:
            continue
    return out


__all__ = ["DEFAULT_RULES", "load_rules", "validate_text"]
