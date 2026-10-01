"""Schema 驱动的 Builder：声明式参数 → 表单 → 命令。

纯数据模块，不导入 core.builders / core.__init__，避免循环依赖。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.base import (
    BuildContext, CommandBuilder, ValidationIssue, get_builder, register,
)


# ---------- 分词 ----------

def split_command(text: str) -> list[str]:
    """按顶层空白拆分命令，跳过引号与嵌套括号内部。"""
    tokens: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    for ch in text:
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
        if ch in " \t" and depth == 0:
            if buf:
                tokens.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        tokens.append("".join(buf))
    return tokens


def count_brackets(tok: str) -> int:
    """token 内的括号净深度（忽略引号内容）。"""
    depth = 0
    quote = None
    for ch in tok:
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            continue
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
    return depth


_NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


def _matches_kind(kind: str, token: str) -> bool:
    """token 是否符合参数类型；用于可选参数的反向解析推断。"""
    if not token:
        return False
    if kind in ("int", "float"):
        return bool(_NUMERIC_RE.match(token))
    if kind == "coord":
        return _looks_like_coord(token)
    if kind == "nbt":
        return token[0] in "{["
    if kind == "target":
        return token[0] == "@"
    return True


def _looks_like_coord(token: str) -> bool:
    """是否是坐标段：数字 / ~ / ^ 开头。"""
    if not token:
        return False
    return token[0].isdigit() or token[0] in "+-.~^"

# ---------- 参数规格 ----------

@dataclass
class ArgSpec:
    key: str
    label: str = ""
    kind: str = "text"
    default: str = ""
    options: list[str] = field(default_factory=list)
    required: bool = False
    hint: str = ""
    # 新增：可选参数 + 条件可见性
    optional: bool = False
    when: dict[str, list[str]] = field(default_factory=dict)
    repeat: int = 1

    @classmethod
    def from_dict(cls, d: dict) -> "ArgSpec":
        when = d.get("when") or {}
        if not isinstance(when, dict):
            when = {}
        clean_when: dict[str, list[str]] = {}
        for k, v in when.items():
            if isinstance(v, str):
                clean_when[str(k)] = [v]
            elif isinstance(v, (list, tuple)):
                clean_when[str(k)] = [str(x) for x in v]
        return cls(
            key=str(d.get("key", "")),
            label=str(d.get("label", d.get("key", ""))),
            kind=str(d.get("kind", "text")),
            default=str(d.get("default", "")),
            options=list(d.get("options") or []),
            required=bool(d.get("required", False)),
            hint=str(d.get("hint", "")),
            optional=bool(d.get("optional", False)),
            when=clean_when,
            repeat=max(1, int(d.get("repeat", 1) or 1)),
        )

    def visible(self, values: dict[str, str]) -> bool:
        """根据 when 条件判断该参数在当前取值下是否应输出。"""
        for key, allowed in self.when.items():
            if str(values.get(key, "")).strip() not in allowed:
                return False
        return True

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"key": self.key, "label": self.label,
                             "kind": self.kind, "default": self.default}
        if self.options:
            d["options"] = list(self.options)
        if self.required:
            d["required"] = True
        if self.hint:
            d["hint"] = self.hint
        if self.optional:
            d["optional"] = True
        if self.when:
            d["when"] = {k: list(v) for k, v in self.when.items()}
        if self.repeat != 1:
            d["repeat"] = self.repeat
        return d


# ---------- SchemaBuilder ----------

class SchemaBuilder(CommandBuilder):
    schema: dict = {}

    def __init__(self) -> None:
        self._args = [ArgSpec.from_dict(a) for a in (self.schema.get("args") or [])]
        self.values = {a.key: str(a.default) for a in self._args}

    # ---- 查询 ----

    def args(self) -> list[ArgSpec]:
        return list(self._args)

    def spec(self, key: str) -> ArgSpec | None:
        for a in self._args:
            if a.key == key:
                return a
        return None

    def visible_args(self) -> list[ArgSpec]:
        return [a for a in self._args if a.visible(self.values)]

    # ---- 生成 ----

    def build(self, ctx: BuildContext | None = None) -> str:
        parts = [self.command_name]
        for a in self._args:
            if not a.visible(self.values):
                continue
            v = str(self.values.get(a.key, "")).strip()
            if not v:
                continue
            # repeat 只影响反向解析时要吃掉几个 token（如 /tp 的目标朝向），
            # 生成时仍输出一个值，避免重复拼出 ~ ~ ~ ~ ~ ~。
            parts.append(v)
        return " ".join(parts)

    def missing_required(self) -> list[ArgSpec]:
        out = []
        for a in self._args:
            if not a.required or not a.visible(self.values):
                continue
            if not str(self.values.get(a.key, "")).strip():
                out.append(a)
        return out

    def validate(self, ctx: BuildContext | None = None) -> list[ValidationIssue]:
        result: list[ValidationIssue] = [
            ValidationIssue(f"{a.label or a.key}不能为空", "error")
            for a in self.missing_required()
        ]
        from app.core.rules import validate_text
        for level, msg in validate_text(self.build()):
            result.append(ValidationIssue(msg, level))
        return result

    # ---- 反向解析 ----

    def load_from(self, command: str) -> None:
        self.values = {a.key: str(a.default) for a in self._args}

        text = command.strip()
        if text.startswith("/"):
            text = text[1:]
        tokens = split_command(text)
        if not tokens or tokens[0] != self.command_name:
            raise ValueError(f"不是 /{self.command_name} 命令")

        cursor = 1
        for a in self._args:
            if cursor >= len(tokens):
                break
            if not a.visible(self.values):
                # 条件不满足的参数不占用 token，默认值保留
                continue
            if a.optional and str(self.values.get(a.key, "")).strip() == "" \
                    and not _matches_kind(a.kind, tokens[cursor]):
                # 可选参数缺省：把 token 让给后面的参数（/time add 1000）
                continue
            if a.kind == "coord":
                # 坐标可能是 1~3 段（如 /forceload add <x> <z>），
                # 按实际可用的坐标 token 数量消费，避免吃掉后续参数。
                got: list[str] = []
                while cursor < len(tokens) and len(got) < 3 \
                        and _looks_like_coord(tokens[cursor]):
                    got.append(tokens[cursor])
                    cursor += 1
                if got:
                    self.values[a.key] = " ".join(got)
            elif a.kind == "nbt" and tokens[cursor].startswith(("{", "[")):
                parts: list[str] = []
                depth = 0
                while cursor < len(tokens):
                    tok = tokens[cursor]
                    parts.append(tok)
                    cursor += 1
                    depth += count_brackets(tok)
                    if depth <= 0:
                        break
                self.values[a.key] = " ".join(parts)
            elif a.repeat > 1:
                got = []
                for _ in range(a.repeat):
                    if cursor >= len(tokens):
                        break
                    got.append(tokens[cursor])
                    cursor += 1
                if got:
                    self.values[a.key] = " ".join(got)
            else:
                self.values[a.key] = tokens[cursor]
                cursor += 1

        return tokens[cursor:]


# ---------- 注册 ----

def register_schema(schema: dict) -> type[SchemaBuilder] | None:
    cmd = str(schema.get("command", "")).strip()
    if not cmd:
        raise ValueError("schema 缺少 command 字段")
    if get_builder(cmd) is not None:
        return None
    cls_name = "".join(p.capitalize() for p in cmd.split("_")) + "Builder"
    cls = type(cls_name, (SchemaBuilder,), {
        "__doc__": f"schema 驱动 /{cmd}",
        "command_name": cmd,
        "display_name": schema.get("display", f"/{cmd}"),
        "category": schema.get("category", "其他"),
        "schema": schema,
    })
    register(cls)
    return cls


def load_schemas(path: str | Path | None = None) -> int:
    if path is None:
        path = Path(__file__).resolve().parents[1] / "data" / "builtin" / "command_schemas.json"
    path = Path(path)
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    commands = data.get("commands") if isinstance(data, dict) else data
    if not isinstance(commands, list):
        return 0
    count = 0
    for schema in commands:
        if isinstance(schema, dict):
            try:
                if register_schema(schema) is not None:
                    count += 1
            except Exception:
                continue
    return count


__all__ = [
    "ArgSpec", "SchemaBuilder", "register_schema", "load_schemas",
    "split_command", "count_brackets",
]
