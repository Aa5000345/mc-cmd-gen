"""命令 Builder 集合：/summon、/give、/execute。

Schema 驱动的 Builder（SchemaBuilder / register_schema）定义在 app/core/schema.py。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.base import (
    BuildContext, CommandBuilder, ValidationIssue, register,
)
from app.core.item_components import (
    MODE_SET, ComponentSpec, component_defs,
    dedupe_components, find_component_start, is_namespaced, max_stack_size,
    normalize_id, parse_components, serialize_components, short_id,
    validate_components,
)
from app.core.rules import validate_text
from app.core.schema import split_command


# =========================================================================
# 通用工具
# =========================================================================

def _split_top_level(body: str) -> list[str]:
    """按顶层逗号拆分，跳过引号与嵌套括号。"""
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


# =========================================================================
# /summon
# =========================================================================

_SUMMON_RE = re.compile(
    r"^/?summon\s+(?P<entity>\S+)"
    r"(?:\s+(?P<pos>[^\s{]+)(?:\s+(?P<pos2>[^\s{]+)(?:\s+(?P<pos3>[^\s{]+))?)?)?"
    r"(?:\s+(?P<nbt>\{.*\}))?\s*$",
    re.DOTALL,
)


@register
class SummonBuilder(CommandBuilder):
    command_name = "summon"
    display_name = "/summon — 生成实体"
    category = "核心命令"
    version = "1.21.1"

    def __init__(self) -> None:
        self.entity: str = "minecraft:zombie"
        self.use_pos: bool = False
        self.x: str = "~"
        self.y: str = "~"
        self.z: str = "~"
        self.nbt: str = ""

    def build(self, ctx: BuildContext | None = None) -> str:
        parts = ["summon", self.entity or "minecraft:pig"]
        if self.use_pos:
            parts += [self.x or "~", self.y or "~", self.z or "~"]
        if self.nbt.strip():
            parts.append(self.nbt.strip())
        return " ".join(parts)

    def validate(self, ctx: BuildContext | None = None) -> list[ValidationIssue]:
        errors: list[ValidationIssue] = []
        if not self.entity or ":" not in self.entity:
            errors.append(ValidationIssue("实体 ID 应为 命名空间:路径 的形式", "error"))
        if self.use_pos:
            for lbl, v in (("X", self.x), ("Y", self.y), ("Z", self.z)):
                if not v.strip():
                    errors.append(ValidationIssue(f"{lbl} 坐标不能为空", "error"))
        if self.nbt.strip() and not self.nbt.strip().startswith("{"):
            errors.append(ValidationIssue("NBT 应以 '{' 开头", "error"))
        for level, msg in validate_text(self.build()):
            errors.append(ValidationIssue(msg, level))
        return errors

    def load_from(self, command: str) -> None:
        m = _SUMMON_RE.match(command.strip())
        if not m:
            raise ValueError("无法解析的 /summon 命令")
        self.entity = m.group("entity") or "minecraft:pig"
        p1, p2, p3 = m.group("pos"), m.group("pos2"), m.group("pos3")
        if p1 and p2 and p3:
            self.use_pos = True
            self.x, self.y, self.z = p1, p2, p3
        else:
            self.use_pos = False
            self.x = self.y = self.z = "~"
        self.nbt = m.group("nbt") or ""


# =========================================================================
# /give
# =========================================================================

class _ValuesDict(dict):
    """`give.values`：让 CLI 的 `values[key] = value` 能触发一次状态同步。

    没有这个信号就无法区分「values 是状态的镜像」和「用户刚改了 values」。
    """

    def __init__(self, owner: "GiveBuilder", *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._owner = owner

    def __setitem__(self, key, value) -> None:
        super().__setitem__(key, value)
        self._owner._values_active = True


@register
class GiveBuilder(CommandBuilder):
    """`/give <目标> <物品> [数量] [组件]`。

    生成与解析都交给 app/core/item_components.py，这里只负责状态与校验，
    以保证「生成 → 反向解析 → 再生成」严格闭环。
    """

    command_name = "give"
    display_name = "/give — 给予物品"
    category = "核心命令"
    version = "1.21.1"

    def __init__(self) -> None:
        self.target: str = "@p"
        self.item: str = "minecraft:diamond_sword"
        self.use_count: bool = False
        self.count: int = 1
        self.components: list[ComponentSpec] = []
        self._values_active: bool = False
        # 兼容 CLI 的 key=value 入口（见 mcgen.py gen）
        self.values: dict[str, str] = _ValuesDict(self, {
            "target": self.target,
            "item": self.item,
            "count": "1",
            "components": "",
        })
        self._values_active = False

    # ---- 组件操作 ----

    def component(self, cid: str) -> ComponentSpec | None:
        key = normalize_id(cid)
        for spec in self.components:
            if spec.id == key:
                return spec
        return None

    def component_index(self, cid: str) -> int:
        key = normalize_id(cid)
        for i, spec in enumerate(self.components):
            if spec.id == key:
                return i
        return -1

    def add_component(self, cid: str, value: str = "",
                      mode: str = MODE_SET) -> ComponentSpec:
        """新增组件；同 ID 已存在时原地更新，避免生成重复组件。"""
        spec = ComponentSpec(id=cid, value=value, mode=mode)
        idx = self.component_index(spec.id)
        if idx >= 0:
            self.components[idx] = spec
        else:
            self.components.append(spec)
        return spec

    def remove_component(self, cid: str) -> bool:
        idx = self.component_index(cid)
        if idx < 0:
            return False
        del self.components[idx]
        return True

    def move_component(self, index: int, delta: int) -> int:
        """在列表中移动组件，返回新下标。"""
        if not self.components:
            return -1
        target = index + delta
        if not (0 <= index < len(self.components)) \
                or not (0 <= target < len(self.components)):
            return index
        specs = self.components
        specs[index], specs[target] = specs[target], specs[index]
        return target

    # ---- 序列化状态（草稿 / 片段）----

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "item": self.item,
            "use_count": bool(self.use_count),
            "count": int(self.count),
            "components": [c.to_dict() for c in self.components],
        }

    def load_state(self, data: dict | None) -> None:
        if not isinstance(data, dict):
            return
        self.target = str(data.get("target") or "@p")
        self.item = str(data.get("item") or "minecraft:stone")
        self.use_count = bool(data.get("use_count", False))
        try:
            self.count = max(1, int(data.get("count", 1)))
        except (TypeError, ValueError):
            self.count = 1
        specs: list[ComponentSpec] = []
        for raw in data.get("components") or []:
            try:
                specs.append(ComponentSpec.from_dict(raw))
            except Exception:
                continue
        self.components = dedupe_components(specs)
        self.sync_values()

    # ---- 生成 ----

    def sync_values(self) -> None:
        """把当前状态写回 CLI 用的 values（只在用户没直接改 values 时使用）。"""
        if isinstance(self.values, _ValuesDict):
            self._values_active = False
        else:  # pragma: no cover - 兼容被整体替换成普通 dict 的情况
            self.values = _ValuesDict(self, self.values)
        self.values["target"] = self.target
        self.values["item"] = self.item
        self.values["count"] = str(self.count)
        self.values["components"] = serialize_components(self.components)
        self._values_active = False

    def _apply_values(self) -> None:
        """读取 CLI 传入的 values。

        只在 values 被显式改写过后生效（`_values_active`），否则 values 只是
        状态的镜像，会在 load_from / 组件编辑后把新状态覆盖掉。
        """
        if not getattr(self, "_values_active", False):
            self.sync_values()
            return
        self._values_active = False
        # 别名：批量生成 / CSV 导入常用 targets，与 Minecraft 原生命名一致
        target = self.values.get("targets") or self.values.get("target", "")
        item = self.values.get("item", "")
        if item and item != "minecraft:diamond_sword":
            self.item = item
        if target:
            self.target = target
        raw_count = self.values.get("count", "")
        if raw_count:
            try:
                self.count = max(1, int(raw_count))
                self.use_count = True
            except ValueError:
                pass
        raw_components = self.values.get("components", "")
        if raw_components:
            self.components = dedupe_components(
                parse_components(raw_components))
        self.sync_values()

    def build(self, ctx: BuildContext | None = None) -> str:
        self._apply_values()
        parts = ["give", self.target.strip() or "@p",
                 normalize_id(self.item) or "minecraft:stone"]
        if self.use_count:
            parts.append(str(max(1, int(self.count))))
        block = serialize_components(self.components)
        if block:
            parts.append(block)
        return " ".join(parts)

    def status_summary(self) -> str:
        item = short_id(normalize_id(self.item)) or "?"
        bits = [item]
        if self.use_count:
            bits.append(f"×{max(1, int(self.count))}")
        if self.components:
            sets = sum(1 for c in self.components if not c.removes)
            removes = len(self.components) - sets
            detail = f"{sets} 个组件"
            if removes:
                detail += f"（移除 {removes}）"
            bits.append(detail)
        return " · ".join(bits)

    # ---- 校验 ----

    def validate(self, ctx: BuildContext | None = None) -> list[ValidationIssue]:
        self._apply_values()
        errors: list[ValidationIssue] = []

        target = self.target.strip()
        if not target:
            errors.append(ValidationIssue("目标选择器不能为空", "error"))
        elif " " in target:
            errors.append(ValidationIssue("目标选择器不能包含空格", "error"))

        raw_item = self.item.strip()
        if not raw_item:
            errors.append(ValidationIssue("物品 ID 不能为空", "error"))
        else:
            item = normalize_id(raw_item)
            if not is_namespaced(item):
                errors.append(ValidationIssue(
                    f"物品 ID “{raw_item}” 不合法（应为 命名空间:路径）", "error"))
            elif ":" not in raw_item:
                errors.append(ValidationIssue(
                    f"物品 ID 已自动补全为 {item}", "info"))

        if self.use_count:
            if int(self.count) < 1:
                errors.append(ValidationIssue("数量应 >= 1", "error"))
            limit = max_stack_size(self.item) * 100
            if int(self.count) > 6400:
                errors.append(ValidationIssue(
                    "单次给予数量超过 6400（100 组）会被服务器拒绝", "error"))
            elif max_stack_size(self.item) == 1 and int(self.count) > 1:
                errors.append(ValidationIssue(
                    f"{short_id(normalize_id(self.item))} 不可堆叠，"
                    f"数量 {int(self.count)} 会占用多个背包格", "warn"))
            elif int(self.count) > limit:
                errors.append(ValidationIssue(
                    f"数量 {int(self.count)} 超过 {limit} 上限", "warn"))
            elif int(self.count) > max_stack_size(self.item):
                errors.append(ValidationIssue(
                    f"数量超过单堆叠上限（{max_stack_size(self.item)}），"
                    "会拆成多组", "info"))

        for level, msg in validate_components(
                self.components, item=self.item, defs=component_defs()):
            errors.append(ValidationIssue(msg, level))

        for level, msg in validate_text(self.build()):
            errors.append(ValidationIssue(msg, level))
        return errors

    @property
    def hints(self) -> list[str]:
        """给 GUI 的提示行：只保留可执行的建议。"""
        out: list[str] = []
        if ":" not in self.item.strip():
            out.append(f"物品 ID 将补全为 {normalize_id(self.item)}")
        if self.use_count and max_stack_size(self.item) == 1 \
                and int(self.count) > 1 and self.count <= 6400:
            out.append(f"{short_id(normalize_id(self.item))} 不可堆叠，"
                       f"会占用 {int(self.count)} 个背包格")
        for level, msg in validate_components(
                self.components, item=self.item, defs=component_defs()):
            if level in ("warn", "error"):
                out.append(msg)
        return out

    # ---- 反向解析 ----

    def load_from(self, command: str) -> None:
        state = parse_give_command(command)
        self.target = state["target"]
        self.item = state["item"]
        self.use_count = state["use_count"]
        self.count = state["count"]
        self.components = state["components"]
        self.sync_values()


def _head_tokens(head: str) -> list[str]:
    """切分「目标 + 物品 + 数量」段，保留选择器里的 `[...]` 与 `{...}`。

    不能用 split_command：`@e[nbt={a:1},limit=1]` 里的空格会被当成分隔符。
    """
    tokens: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    escaped = False
    for ch in head:
        if quote:
            buf.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
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
        if ch.isspace() and depth == 0:
            if buf:
                tokens.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        tokens.append("".join(buf))
    return tokens


def parse_give_command(command: str) -> dict:
    """把 `/give` 命令解析成 Builder 状态字典。

    比单纯的正则更宽容：
        * 允许物品 ID 与组件块之间没有空格（`stone[a=1]`）；
        * 允许裸物品 ID（`stone`）与裸组件 ID（`[lore=...]`）；
        * 值里含空格/逗号/引号也能正确切分。
    """
    text = (command or "").strip()
    if text.startswith("/"):
        text = text[1:]
    text = text.strip()
    if not re.match(r"^give\b", text):
        raise ValueError("不是 /give 命令")
    body = text[4:].strip()
    if not body:
        raise ValueError("give 命令缺少参数")
    if "{" in body.split("[", 1)[0]:
        raise ValueError("1.21.1 的 /give 不再接受物品 NBT（{…}），"
                         "请改用物品组件：[minecraft:custom_name=…]"
                         "（可用「迁移命令」自动转换）")

    start = find_component_start(body)
    head = body[:start] if start >= 0 else body
    block = body[start:] if start >= 0 else ""

    tokens = _head_tokens(head)
    use_count = False
    count = 1
    if tokens and tokens[-1].isdigit():
        use_count = True
        count = max(1, int(tokens[-1]))
        tokens = tokens[:-1]

    # `/give @p stone[d=1] 3`：数量写在组件块之后，这里把尾部的数量摘出来
    if block:
        tail = re.match(r"(?P<block>\[.*\])\s+(?P<count>\d+)\s*$",
                        block, re.DOTALL)
        if tail:
            if not use_count:
                use_count = True
                count = max(1, int(tail.group("count")))
            block = tail.group("block")

    if len(tokens) < 2:
        raise ValueError("give 命令缺少物品 ID（应为 /give <目标> <物品>）")

    return {
        "target": tokens[0].strip(),
        "item": normalize_id(tokens[1]),
        "use_count": use_count,
        "count": count,
        "components": dedupe_components(parse_components(block)) if block else [],
    }


# =========================================================================
# /execute
# =========================================================================

NODE_TYPES: list[tuple[str, str]] = [
    ("as", "as — 以目标身份执行"),
    ("at", "at — 以目标位置/朝向执行"),
    ("positioned", "positioned — 设置位置"),
    ("rotated", "rotated — 设置朝向"),
    ("facing", "facing — 面向"),
    ("align", "align — 对齐坐标到方块"),
    ("anchored", "anchored — 设置锚点"),
    ("in", "in — 切换维度"),
    ("on", "on — 关系者"),
    ("if", "if — 条件为真"),
    ("unless", "unless — 条件为假"),
    ("store", "store — 存储结果"),
]

CONDITION_TYPES: list[tuple[str, str]] = [
    ("entity", "entity — 存在实体"),
    ("block", "block — 指定位置方块"),
    ("blocks", "blocks — 区域方块"),
    ("data_block", "data block — 方块 NBT"),
    ("data_entity", "data entity — 实体 NBT"),
    ("predicate", "predicate — 谓词"),
    ("score", "score — 计分板分数"),
    ("function", "function — 函数成功"),
    ("items_block", "items block — 方块容器物品"),
    ("items_entity", "items entity — 实体容器物品"),
]

STORE_TARGETS: list[tuple[str, str]] = [
    ("score", "score — 计分板"),
    ("entity", "entity — 实体 NBT"),
    ("block", "block — 方块 NBT"),
    ("bossbar", "bossbar — Boss 栏"),
]


@dataclass
class ExecuteNode:
    type: str = "as"
    params: dict = field(default_factory=dict)

    def build(self) -> str:
        return _build_node(self)

    def summary(self) -> str:
        s = self.build()
        return s if len(s) <= 90 else s[:87] + "…"


def default_params(type_: str) -> dict:
    if type_ in ("as", "at"):
        return {"target": "@s"}
    if type_ == "positioned":
        return {"mode": "coord", "x": "~", "y": "~", "z": "~", "target": "@s"}
    if type_ == "rotated":
        return {"mode": "coord", "yaw": "~", "pitch": "~", "target": "@s"}
    if type_ == "facing":
        return {"mode": "coord", "x": "~", "y": "~", "z": "~",
                "target": "@s", "anchor": "eyes"}
    if type_ == "align":
        return {"axes": "xyz"}
    if type_ == "anchored":
        return {"anchor": "eyes"}
    if type_ == "in":
        return {"dimension": "minecraft:overworld"}
    if type_ == "on":
        return {"relation": "attacker"}
    if type_ in ("if", "unless"):
        return {"condition": {"type": "entity", "target": "@s"}}
    if type_ == "store":
        return {"result": "result", "target_type": "score",
                "target": "@s", "objective": "objective"}
    return {}


def _build_node(node: ExecuteNode) -> str:
    t, p = node.type, node.params
    if t in ("as", "at"):
        return f"{t} {p.get('target', '@s')}"
    if t == "positioned":
        if p.get("mode") == "as":
            return f"positioned as {p.get('target', '@s')}"
        return f"positioned {p.get('x', '~')} {p.get('y', '~')} {p.get('z', '~')}"
    if t == "rotated":
        if p.get("mode") == "as":
            return f"rotated as {p.get('target', '@s')}"
        return f"rotated {p.get('yaw', '~')} {p.get('pitch', '~')}"
    if t == "facing":
        if p.get("mode") == "entity":
            return f"facing entity {p.get('target', '@s')} {p.get('anchor', 'eyes')}"
        return f"facing {p.get('x', '~')} {p.get('y', '~')} {p.get('z', '~')}"
    if t == "align":
        return f"align {p.get('axes', 'xyz')}"
    if t == "anchored":
        return f"anchored {p.get('anchor', 'eyes')}"
    if t == "in":
        return f"in {p.get('dimension', 'minecraft:overworld')}"
    if t == "on":
        return f"on {p.get('relation', 'attacker')}"
    if t in ("if", "unless"):
        return f"{t} {_build_condition(p.get('condition', {}))}"
    if t == "store":
        return _build_store(p)
    return ""


def _build_condition(c: dict) -> str:
    t = c.get("type", "entity")
    if t == "entity":
        return f"entity {c.get('target', '@s')}"
    if t == "block":
        return (f"block {c.get('x', '~')} {c.get('y', '~')} {c.get('z', '~')} "
                f"{c.get('block', 'minecraft:stone')}")
    if t == "blocks":
        return (f"blocks {c.get('sx', '~')} {c.get('sy', '~')} {c.get('sz', '~')} "
                f"{c.get('ex', '~')} {c.get('ey', '~')} {c.get('ez', '~')} "
                f"{c.get('dx', '~')} {c.get('dy', '~')} {c.get('dz', '~')} "
                f"{c.get('mode', 'all')}")
    if t == "data_block":
        return (f"data block {c.get('x', '~')} {c.get('y', '~')} {c.get('z', '~')} "
                f"{c.get('path', '')}")
    if t == "data_entity":
        return f"data entity {c.get('target', '@s')} {c.get('path', '')}"
    if t == "predicate":
        return f"predicate {c.get('id', 'minecraft:example')}"
    if t == "score":
        return (f"score {c.get('target', '@s')} {c.get('objective', 'obj')} "
                f"{c.get('op', '=')} {c.get('source', '@s')} "
                f"{c.get('source_objective', 'obj')}")
    if t == "function":
        return f"function {c.get('name', 'minecraft:example')}"
    if t == "items_block":
        return (f"items block {c.get('x', '~')} {c.get('y', '~')} {c.get('z', '~')} "
                f"{c.get('slot', 'container.0')} {c.get('data', '{}')}")
    if t == "items_entity":
        return (f"items entity {c.get('target', '@s')} "
                f"{c.get('slot', 'container.0')} {c.get('data', '{}')}")
    return "entity @s"


def _build_store(p: dict) -> str:
    result = p.get("result", "result")
    tt = p.get("target_type", "score")
    if tt == "score":
        return (f"store {result} score {p.get('target', '@s')} "
                f"{p.get('objective', 'objective')}")
    if tt == "entity":
        return (f"store {result} entity {p.get('target', '@s')} "
                f"{p.get('path', '')} {p.get('data_type', 'int')} "
                f"{p.get('scale', '1.0')}")
    if tt == "block":
        return (f"store {result} block {p.get('x', '~')} {p.get('y', '~')} "
                f"{p.get('z', '~')} {p.get('path', '')} "
                f"{p.get('data_type', 'int')} {p.get('scale', '1.0')}")
    if tt == "bossbar":
        return (f"store {result} bossbar {p.get('id', 'minecraft:custom:name')} "
                f"{p.get('value', 'value')}")
    return ""


# ---- 反向解析 ----

def _tokenize(text: str) -> list[str]:
    return split_command(text)


def _parse_condition(tokens: list[str], i: int) -> tuple[dict | None, int]:
    if i >= len(tokens):
        return None, 0
    t = tokens[i]

    if t == "entity" and i + 1 < len(tokens):
        return {"type": "entity", "target": tokens[i + 1]}, 2
    if t == "block" and i + 4 < len(tokens):
        return {"type": "block",
                "x": tokens[i + 1], "y": tokens[i + 2], "z": tokens[i + 3],
                "block": tokens[i + 4]}, 5
    if t == "blocks" and i + 9 < len(tokens):
        params: dict = {"type": "blocks"}
        names = ["sx", "sy", "sz", "ex", "ey", "ez", "dx", "dy", "dz"]
        for k, name in enumerate(names):
            params[name] = tokens[i + 1 + k]
        if i + 10 < len(tokens) and tokens[i + 10] in ("all", "masked"):
            params["mode"] = tokens[i + 10]
            return params, 11
        params["mode"] = "all"
        return params, 10
    if t == "data" and i + 1 < len(tokens):
        sub = tokens[i + 1]
        if sub == "block" and i + 5 < len(tokens):
            return {"type": "data_block",
                    "x": tokens[i + 2], "y": tokens[i + 3], "z": tokens[i + 4],
                    "path": tokens[i + 5]}, 6
        if sub == "entity" and i + 3 < len(tokens):
            return {"type": "data_entity",
                    "target": tokens[i + 2], "path": tokens[i + 3]}, 4
        return None, 0
    if t == "predicate" and i + 1 < len(tokens):
        return {"type": "predicate", "id": tokens[i + 1]}, 2
    if t == "score" and i + 5 < len(tokens):
        return {"type": "score",
                "target": tokens[i + 1], "objective": tokens[i + 2],
                "op": tokens[i + 3],
                "source": tokens[i + 4], "source_objective": tokens[i + 5]}, 6
    if t == "function" and i + 1 < len(tokens):
        return {"type": "function", "name": tokens[i + 1]}, 2
    if t == "items" and i + 1 < len(tokens):
        sub = tokens[i + 1]
        if sub == "block" and i + 5 < len(tokens):
            parts, depth, j = [], 0, i + 6
            while j < len(tokens):
                parts.append(tokens[j])
                depth += tokens[j].count("{") - tokens[j].count("}")
                j += 1
                if depth <= 0:
                    break
            return {"type": "items_block",
                    "x": tokens[i + 2], "y": tokens[i + 3], "z": tokens[i + 4],
                    "slot": tokens[i + 5], "data": " ".join(parts) or "{}"}, 6 + len(parts)
        if sub == "entity" and i + 3 < len(tokens):
            parts, depth, j = [], 0, i + 4
            while j < len(tokens):
                parts.append(tokens[j])
                depth += tokens[j].count("{") - tokens[j].count("}")
                j += 1
                if depth <= 0:
                    break
            return {"type": "items_entity",
                    "target": tokens[i + 2], "slot": tokens[i + 3],
                    "data": " ".join(parts) or "{}"}, 4 + len(parts)
        return None, 0
    return None, 0


def _parse_store(tokens: list[str], i: int) -> tuple[dict | None, int]:
    if i + 1 >= len(tokens):
        return None, 0
    result = tokens[i]
    if result not in ("result", "success"):
        return None, 0
    t = tokens[i + 1]
    if t == "score" and i + 3 < len(tokens):
        return {"result": result, "target_type": "score",
                "target": tokens[i + 2], "objective": tokens[i + 3]}, 4
    if t == "entity" and i + 5 < len(tokens):
        return {"result": result, "target_type": "entity",
                "target": tokens[i + 2], "path": tokens[i + 3],
                "data_type": tokens[i + 4], "scale": tokens[i + 5]}, 6
    if t == "block" and i + 7 < len(tokens):
        return {"result": result, "target_type": "block",
                "x": tokens[i + 2], "y": tokens[i + 3], "z": tokens[i + 4],
                "path": tokens[i + 5],
                "data_type": tokens[i + 6], "scale": tokens[i + 7]}, 8
    if t == "bossbar" and i + 3 < len(tokens):
        return {"result": result, "target_type": "bossbar",
                "id": tokens[i + 2], "value": tokens[i + 3]}, 4
    return None, 0


def _parse_execute_node(tokens: list[str], i: int) -> tuple[ExecuteNode | None, int]:
    if i >= len(tokens):
        return None, 0
    tok = tokens[i]

    if tok in ("as", "at") and i + 1 < len(tokens):
        return ExecuteNode(tok, {"target": tokens[i + 1]}), 2

    if tok == "positioned" and i + 1 < len(tokens):
        if tokens[i + 1] == "as" and i + 2 < len(tokens):
            return ExecuteNode("positioned", {"mode": "as", "target": tokens[i + 2]}), 3
        if i + 3 < len(tokens):
            return ExecuteNode("positioned", {
                "mode": "coord", "x": tokens[i + 1], "y": tokens[i + 2], "z": tokens[i + 3]}), 4
        return None, 0

    if tok == "rotated" and i + 1 < len(tokens):
        if tokens[i + 1] == "as" and i + 2 < len(tokens):
            return ExecuteNode("rotated", {"mode": "as", "target": tokens[i + 2]}), 3
        if i + 2 < len(tokens):
            return ExecuteNode("rotated", {
                "mode": "coord", "yaw": tokens[i + 1], "pitch": tokens[i + 2]}), 3
        return None, 0

    if tok == "facing" and i + 1 < len(tokens):
        if tokens[i + 1] == "entity" and i + 2 < len(tokens):
            target = tokens[i + 2]
            if i + 3 < len(tokens):
                return ExecuteNode("facing", {
                    "mode": "entity", "target": target, "anchor": tokens[i + 3]}), 4
            return ExecuteNode("facing", {
                "mode": "entity", "target": target, "anchor": "eyes"}), 3
        if i + 3 < len(tokens):
            return ExecuteNode("facing", {
                "mode": "coord",
                "x": tokens[i + 1], "y": tokens[i + 2], "z": tokens[i + 3]}), 4
        return None, 0

    if tok == "align" and i + 1 < len(tokens):
        return ExecuteNode("align", {"axes": tokens[i + 1]}), 2
    if tok == "anchored" and i + 1 < len(tokens):
        return ExecuteNode("anchored", {"anchor": tokens[i + 1]}), 2
    if tok == "in" and i + 1 < len(tokens):
        return ExecuteNode("in", {"dimension": tokens[i + 1]}), 2
    if tok == "on" and i + 1 < len(tokens):
        return ExecuteNode("on", {"relation": tokens[i + 1]}), 2

    if tok in ("if", "unless"):
        cond, consumed = _parse_condition(tokens, i + 1)
        if cond is None:
            return None, 0
        return ExecuteNode(tok, {"condition": cond}), consumed + 1

    if tok == "store":
        params, consumed = _parse_store(tokens, i + 1)
        if params is None:
            return None, 0
        return ExecuteNode("store", params), consumed + 1

    return None, 0


@register
class ExecuteBuilder(CommandBuilder):
    command_name = "execute"
    display_name = "/execute — 条件执行"
    category = "核心命令"
    version = "1.21.1"

    def __init__(self) -> None:
        self.nodes: list[ExecuteNode] = []
        self.run_command: str = "say hello"

    def build(self, ctx: BuildContext | None = None) -> str:
        parts = ["execute"]
        for n in self.nodes:
            sub = n.build().strip()
            if sub:
                parts.append(sub)
        parts.append("run")
        parts.append(self.run_command.strip() or "say hi")
        return " ".join(parts)

    def validate(self, ctx: BuildContext | None = None) -> list[ValidationIssue]:
        errors: list[ValidationIssue] = []
        if not self.run_command.strip():
            errors.append(ValidationIssue("run 后的命令不能为空", "error"))
        for i, n in enumerate(self.nodes, 1):
            if n.type in ("if", "unless") and not n.params.get("condition"):
                errors.append(ValidationIssue(f"第 {i} 个节点条件未配置", "error"))
        if not self.nodes:
            errors.append(ValidationIssue("没有任何子命令节点，等价于直接 run", "info"))
        for level, msg in validate_text(self.build()):
            errors.append(ValidationIssue(msg, level))
        return errors

    def load_from(self, command: str) -> None:
        text = command.strip()
        if text.startswith("/"):
            text = text[1:]
        if not text.startswith("execute"):
            raise ValueError("不是 /execute 命令")
        rest = text[len("execute"):].strip()
        if not rest:
            raise ValueError("execute 命令为空")
        tokens = _tokenize(rest)

        self.nodes = []
        self.run_command = "say hi"

        i = 0
        while i < len(tokens):
            if tokens[i] == "run":
                self.run_command = " ".join(tokens[i + 1:]).strip() or "say hi"
                return
            node, consumed = _parse_execute_node(tokens, i)
            if node is None or consumed <= 0:
                raise ValueError(f"无法解析的 execute 片段：{' '.join(tokens[i:])}")
            self.nodes.append(node)
            i += consumed
        raise ValueError("execute 命令缺少 run 子句")


# =========================================================================
# 触发 schema 命令注册（在 core/__init__ 调用 load_schemas 之前需要先加载 builders）
# =========================================================================

__all__ = [
    "SummonBuilder", "GiveBuilder", "ExecuteBuilder", "ExecuteNode",
    "NODE_TYPES", "CONDITION_TYPES", "STORE_TARGETS",
    "default_params", "parse_give_command",
]