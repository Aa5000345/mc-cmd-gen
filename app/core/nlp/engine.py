"""离线语义化命令块：自然语言 → Minecraft 命令。

流程：
    1. 按 patterns.json 的句式匹配；
    2. target/entity/item/block/effect 等字段走 app.core.ids 做中文 → ID 解析；
    3. 组装模板并压缩多余空格。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.ids import resolve

_PATTERNS_PATH = Path(__file__).parent / "patterns.json"

_TARGET_MAP = {
    "所有玩家": "@a", "全体玩家": "@a", "全部玩家": "@a", "所有人": "@a",
    "最近的玩家": "@p", "最近玩家": "@p", "最近的": "@p",
    "自己": "@s", "我自己": "@s", "我": "@s",
    "所有生物": "@e", "所有实体": "@e", "全部实体": "@e",
    "最近的实体": "@n", "随机玩家": "@r",
}

# 字段 → 解析类型（只放一定是命名空间 ID 的字段）
_FIELD_KIND = {
    "entity": "entity",
    "item": "item",
    "block": "block",
    "effect": "effect",
    "name": "particle",
    "sound": "sound",
    "enchantment": "enchantment",
}

# 语义化字段的专用纠正表（这些不是命名空间 ID）
_VALUE_MAPS: dict[str, dict[str, str]] = {
    "gamemode": {"生存": "survival", "创造": "creative", "冒险": "adventure",
                 "旁观": "spectator", "旁观者": "spectator"},
    "weather": {"晴天": "clear", "下雨": "rain", "雨": "rain",
                "雷暴": "thunder", "雷雨": "thunder", "打雷": "thunder"},
    "difficulty": {"和平": "peaceful", "简单": "easy", "普通": "normal",
                   "困难": "hard"},
    "time": {"白天": "day", "正午": "noon", "夜晚": "night", "午夜": "midnight"},
    "unit": {"点经验": "points", "经验点": "points", "经验": "points",
             "级经验": "levels", "经验等级": "levels"},
    "dimension": {"主世界": "minecraft:overworld", "下界": "minecraft:the_nether",
                  "地狱": "minecraft:the_nether", "末地": "minecraft:the_end"},
}

# 多张纠正表共用的字段名（同一个 key 在不同句式里含义不同）
_FIELD_VALUE_MAPS = {
    "mode": "gamemode",
    "type": "weather",
    "value": "time",
    "level": "difficulty",
    "difficulty": "difficulty",
    "dimension": "dimension",
    "unit": "unit",
}

_WS_RE = re.compile(r"\s{2,}")

# ---------------------------------------------------------------------------
# 中文数字 → 阿拉伯数字（"五" / "十二" / "五十" / "三"）
# ---------------------------------------------------------------------------

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}
_CN_NUM_RE = re.compile(r"[零〇一二两三四五六七八九十百千万]+")
_NUMERIC_FIELDS = {"count", "duration", "amplifier", "amount", "level",
                   "x", "y", "z", "x1", "y1", "z1", "x2", "y2", "z2", "pos"}


def cn_to_int(text: str) -> int | None:
    """把中文数字串转成整数；无法解析返回 None。"""
    s = (text or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total, section, number = 0, 0, 0
    for ch in s:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if unit == 10000:
                section = (section + number) * unit
                total += section
                section = 0
            else:
                section += (number or 1) * unit
            number = 0
        else:
            return None
    return total + section + number


def _convert_numbers(value: str) -> str:
    """把值里的中文数字段替换成阿拉伯数字。"""
    def repl(m: re.Match) -> str:
        n = cn_to_int(m.group(0))
        return str(n) if n is not None else m.group(0)
    return _CN_NUM_RE.sub(repl, value)


def _load_patterns() -> list[dict]:
    if not _PATTERNS_PATH.exists():
        return []
    try:
        data = json.loads(_PATTERNS_PATH.read_text(encoding="utf-8"))
        return data.get("patterns", [])
    except Exception:
        return []


def _normalize_target(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("@"):
        return value
    return _TARGET_MAP.get(value, value)


def _resolve_field(field: str, value: str) -> str:
    value = (value or "").strip()
    if not value:
        return value
    # 1) 语义化取值表（游戏模式 / 天气 / 难度 / 时间点 / 单位 / 维度）
    map_key = _FIELD_VALUE_MAPS.get(field)
    if map_key:
        hit = _VALUE_MAPS[map_key].get(value)
        if hit:
            return hit
    # 2) 目标选择器
    if field in ("target", "dest", "vehicle"):
        return _normalize_target(value)
    # 3) 数字字段：中文数字转阿拉伯数字
    if field in _NUMERIC_FIELDS:
        return _convert_numbers(value)
    # 4) 命名空间 ID 字段
    kind = _FIELD_KIND.get(field)
    if kind is None:
        return value
    resolved = resolve(value, kind=kind)
    return resolved or value


def parse_with_pattern(text: str) -> tuple[str, dict] | None:
    """返回 (命令, 命中的模式信息)；无法识别返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    # 中文数字先转成阿拉伯数字，句式里的 \d+ 才能匹配"十秒""三个"
    candidates = [text]
    converted = _convert_numbers(text)
    if converted != text:
        candidates.append(converted)
    for candidate in candidates:
        hit = _match_patterns(candidate)
        if hit is not None:
            return hit
    return None


def _match_patterns(text: str) -> tuple[str, dict] | None:
    for p in _load_patterns():
        try:
            m = re.match(p["regex"], text)
        except (re.error, KeyError):
            continue
        if not m:
            continue
        # 可选组为 None 时给空串，模板里的占位符才不会 KeyError
        values = {k: ("" if v is None else v) for k, v in m.groupdict().items()}
        resolved = {k: _resolve_field(k, v) if v else ""
                    for k, v in values.items()}
        try:
            command = p["template"].format(**resolved)
        except (KeyError, IndexError):
            continue
        command = _WS_RE.sub(" ", command).strip()
        # 模板里用全角逗号做"可选参数占位符"，值缺失时把它清理掉
        command = command.replace("， ", " ").replace(" ，", " ").replace("，", " ")
        command = _WS_RE.sub(" ", command).strip()
        return command, {"id": p.get("id", ""), "desc": p.get("desc", ""),
                         "values": resolved}
    return None


def parse(text: str) -> str | None:
    """把自然语言转换成命令；无法识别时返回 None。"""
    hit = parse_with_pattern(text)
    return hit[0] if hit else None


def available_patterns() -> list[dict]:
    """列出所有句式，供 UI 做示例提示。"""
    out = []
    for p in _load_patterns():
        out.append({"id": p.get("id", ""), "desc": p.get("desc", ""),
                    "template": p.get("template", "")})
    return out


def examples(limit: int = 6) -> list[str]:
    """给 UI 用的输入示例。"""
    samples = [
        "给所有玩家10个钻石", "给Steve添加夜视效果60秒",
        "在10 64 10生成一只僵尸", "把Steve传送到10 64 10",
        "杀死所有僵尸", "用石头填充0 0 0到5 5 5",
        "把Steve设置成创造模式", "设置成下雨", "把时间设置成夜晚",
        "给所有玩家50点经验", "对所有人说你好", "在~ ~1 ~生成火焰粒子",
    ]
    return samples[:limit]


__all__ = ["parse", "parse_with_pattern", "available_patterns", "examples"]
