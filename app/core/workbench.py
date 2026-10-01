"""命令编排脚本 + 目标选择器模型。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .datapack import has_placeholder, render_placeholders


# =========================================================================
# 1. 目标选择器
# =========================================================================

BASES: list[tuple[str, str]] = [
    ("@a", "@a — 所有玩家"),
    ("@e", "@e — 所有实体"),
    ("@p", "@p — 最近的玩家"),
    ("@r", "@r — 随机玩家"),
    ("@s", "@s — 执行者自己"),
    ("@n", "@n — 最近的实体"),
]


FILTER_DEFS: list[dict] = [
    {"key": "type", "label": "实体类型", "kind": "text",
     "hint": "minecraft:zombie，! 前缀排除"},
    {"key": "name", "label": "名称", "kind": "text",
     "hint": "字符串或 JSON 文本组件"},
    {"key": "tag", "label": "标签", "kind": "text",
     "hint": "foo，!bar 表示排除"},
    {"key": "team", "label": "队伍", "kind": "text",
     "hint": "队伍名，! 前缀排除"},
    {"key": "gamemode", "label": "游戏模式", "kind": "enum",
     "options": ["survival", "creative", "adventure", "spectator",
                 "!survival", "!creative", "!adventure", "!spectator"]},
    {"key": "level", "label": "经验等级", "kind": "text",
     "hint": "5 或 5..10 或 ..10"},
    {"key": "distance", "label": "距离", "kind": "text",
     "hint": "10 或 5..10 或 ..10"},
    {"key": "x", "label": "X 坐标", "kind": "text", "hint": "支持 ~ 相对坐标"},
    {"key": "y", "label": "Y 坐标", "kind": "text", "hint": "支持 ~ 相对坐标"},
    {"key": "z", "label": "Z 坐标", "kind": "text", "hint": "支持 ~ 相对坐标"},
    {"key": "dx", "label": "dX 体积", "kind": "text"},
    {"key": "dy", "label": "dY 体积", "kind": "text"},
    {"key": "dz", "label": "dZ 体积", "kind": "text"},
    {"key": "x_rotation", "label": "X 旋转", "kind": "text", "hint": "-90..90"},
    {"key": "y_rotation", "label": "Y 旋转", "kind": "text", "hint": "-180..180"},
    {"key": "scores", "label": "计分板", "kind": "nbt",
     "hint": "{obj=5,obj2=5..10}"},
    {"key": "nbt", "label": "NBT", "kind": "nbt", "hint": "{IsBaby:0b}"},
    {"key": "predicate", "label": "谓词", "kind": "text",
     "hint": "命名空间:谓词"},
    {"key": "advancements", "label": "进度", "kind": "nbt",
     "hint": "{adv={criteria=true}}"},
    {"key": "sort", "label": "排序", "kind": "enum",
     "options": ["nearest", "furthest", "random", "arbitrary"]},
    {"key": "limit", "label": "数量限制", "kind": "int"},
]

FILTER_DEF_MAP = {d["key"]: d for d in FILTER_DEFS}


def _split_top_level(body: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
            continue
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


@dataclass
class TargetSelector:
    base: str = "@p"
    filters: dict[str, str] = field(default_factory=dict)

    def to_string(self) -> str:
        if not self.filters:
            return self.base
        parts = [f"{k}={v}" for k, v in self.filters.items()]
        return f"{self.base}[{','.join(parts)}]"

    @classmethod
    def parse(cls, text: str) -> "TargetSelector":
        text = text.strip()
        if not text:
            raise ValueError("空的目标选择器")
        if not text.startswith("@"):
            raise ValueError("目标选择器必须以 @ 开头")
        if "[" not in text:
            return cls(base=text, filters={})
        idx = text.find("[")
        base = text[:idx].strip()
        rest = text[idx:]
        if not rest.endswith("]"):
            raise ValueError("目标选择器缺少右方括号 ']'")
        body = rest[1:-1]
        filters: dict[str, str] = {}
        for item in _split_top_level(body):
            item = item.strip()
            if not item or "=" not in item:
                continue
            key, value = item.split("=", 1)
            filters[key.strip()] = value.strip()
        return cls(base=base, filters=filters)

    def clone(self) -> "TargetSelector":
        return TargetSelector(base=self.base, filters=dict(self.filters))


# =========================================================================
# 2. 命令编排脚本
# =========================================================================

STEP_KINDS = [
    ("command", "命令"),
    ("comment", "注释"),
    ("if", "如果 (if)"),
    ("unless", "除非 (unless)"),
    ("repeat", "重复 (repeat)"),
    ("function", "函数调用 (function)"),
    ("delay", "延迟 (delay)"),
]

STEP_KIND_LABELS = dict(STEP_KINDS)


@dataclass
class Step:
    kind: str = "command"
    command: str = ""
    condition: str = ""
    function: str = ""
    repeat_count: int = 1
    delay_time: str = "1s"
    comment: str = ""
    children: list["Step"] = field(default_factory=list)

    def summary(self) -> str:
        k = self.kind
        if k == "command": return self.command.strip() or "(空命令)"
        if k == "comment": return "# " + (self.comment.strip() or "")
        if k == "function": return f"function {self.function or '?'}"
        if k == "delay": return f"delay {self.delay_time}"
        if k in ("if", "unless"): return f"{k} {self.condition or '?'}"
        if k == "repeat": return f"repeat {self.repeat_count} 次"
        return k

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "command": self.command,
            "condition": self.condition, "function": self.function,
            "repeat_count": self.repeat_count, "delay_time": self.delay_time,
            "comment": self.comment,
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        return cls(
            kind=str(d.get("kind", "command")),
            command=str(d.get("command", "")),
            condition=str(d.get("condition", "")),
            function=str(d.get("function", "")),
            repeat_count=int(d.get("repeat_count", 1) or 1),
            delay_time=str(d.get("delay_time", "1s")),
            comment=str(d.get("comment", "")),
            children=[Step.from_dict(c) for c in (d.get("children") or [])],
        )

    def clone(self) -> "Step":
        return Step(
            kind=self.kind, command=self.command, condition=self.condition,
            function=self.function, repeat_count=self.repeat_count,
            delay_time=self.delay_time, comment=self.comment,
            children=[c.clone() for c in self.children],
        )


@dataclass
class CommandScript:
    name: str = "untitled"
    description: str = ""
    steps: list[Step] = field(default_factory=list)

    def to_mcfunction(self, values: dict[str, str] | None = None) -> str:
        values = values or {}
        lines: list[str] = []
        if self.description.strip():
            for line in self.description.splitlines():
                lines.append(f"# {line}")
        for s in self.steps:
            lines.extend(_render_step(s, values))
        return "\n".join(lines)

    def has_placeholder(self) -> bool:
        return has_placeholder(self._all_text())

    def _all_text(self) -> str:
        buf: list[str] = []
        for s in self.steps:
            buf.extend(_step_text(s))
        return "\n".join(buf)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1, "name": self.name,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CommandScript":
        return cls(
            name=str(d.get("name", "untitled")),
            description=str(d.get("description", "")),
            steps=[Step.from_dict(s) for s in (d.get("steps") or [])],
        )


def _step_text(step: Step) -> list[str]:
    lines: list[str] = []
    if step.kind == "command":
        if step.command.strip(): lines.append(step.command)
    elif step.kind == "comment":
        if step.comment.strip(): lines.append(step.comment)
    elif step.kind == "function":
        if step.function.strip(): lines.append(step.function)
    elif step.kind == "delay":
        lines.append(step.delay_time)
    elif step.kind in ("if", "unless"):
        if step.condition.strip(): lines.append(step.condition)
    for c in step.children:
        lines.extend(_step_text(c))
    return lines


def _render_step(step: Step, values: dict[str, str]) -> list[str]:
    k = step.kind
    lines: list[str] = []

    if k == "command":
        cmd = step.command.strip()
        if cmd:
            lines.append(render_placeholders(cmd, values))

    elif k == "comment":
        txt = step.comment.strip()
        if txt:
            lines.append("# " + render_placeholders(txt, values))

    elif k == "function":
        fn = step.function.strip()
        if fn:
            lines.append(f"function {render_placeholders(fn, values)}")

    elif k == "delay":
        t = step.delay_time.strip() or "1s"
        safe = _safe_id(step.delay_time)
        lines.append(f"schedule function mccmdgen:_delay_{safe} {t} replace")

    elif k in ("if", "unless"):
        cond = step.condition.strip()
        if not cond:
            return lines
        for child in step.children:
            for cl in _render_step(child, values):
                if cl.startswith("#"):
                    lines.append(cl)
                else:
                    lines.append(f"execute {k} {cond} run {cl}")

    elif k == "repeat":
        n = max(1, int(step.repeat_count or 1))
        for _ in range(n):
            for child in step.children:
                lines.extend(_render_step(child, values))

    return lines


def _safe_id(s: str) -> str:
    s = (s or "").strip().lower()
    return re.sub(r"[^a-z0-9_]", "_", s) or "1s"


__all__ = [
    "BASES", "FILTER_DEFS", "FILTER_DEF_MAP", "TargetSelector",
    "STEP_KINDS", "STEP_KIND_LABELS", "Step", "CommandScript",
]