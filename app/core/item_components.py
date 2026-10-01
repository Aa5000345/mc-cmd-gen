"""物品组件（item components）引擎 —— /give 的解析、生成与校验。

1.21 起物品 NBT 被「物品组件」取代：`/give @p minecraft:stone[minecraft:custom_name="石头"]`。
本模块把这一刻度的语法集中到一处，避免 Builder / GUI / CLI 各写一份：

    解析：parse_components("[a=1,b={c:[1,2]}]") → [ComponentSpec, ...]
    生成：serialize_components(specs)          → "[a=1,b={c:[1,2]}]"
    校验：validate_components(specs, item=...) → [ValidationIssue, ...]

关键细节（这些是手写正则最容易踩的坑）：
    * 值里可以出现逗号、空格、引号和嵌套括号，必须按顶层逗号切分；
    * 移除组件写作 `!minecraft:lore`（前缀 `!`），此时不应有 `=`；
    * 组件 ID 必须是 命名空间:路径，裸 ID（如 `lore`）要补 `minecraft:`；
    * 物品 ID 同理（`/give @p stone` 在 1.21.1 会补成 minecraft:stone）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

MODE_SET = "set"
MODE_REMOVE = "remove"

# 组件 ID：允许 minecraft:xxx 与自定义命名空间 a_b:xxx
ID_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")

# 整数值的 SNBT 类型后缀
_INT_SUFFIXES = ("b", "s", "l")

# 常见堆叠上限（未列出的物品按 64 处理）
_STACK_OVERRIDES: dict[str, int] = {
    "minecraft:potion": 1,
    "minecraft:splash_potion": 1,
    "minecraft:lingering_potion": 1,
    "minecraft:written_book": 16,
    "minecraft:writable_book": 16,
    "minecraft:enchanted_book": 1,
    "minecraft:knowledge_book": 1,
    "minecraft:water_bucket": 1,
    "minecraft:lava_bucket": 1,
    "minecraft:milk_bucket": 1,
    "minecraft:powder_snow_bucket": 1,
    "minecraft:axolotl_bucket": 1,
    "minecraft:cod_bucket": 1,
    "minecraft:salmon_bucket": 1,
    "minecraft:pufferfish_bucket": 1,
    "minecraft:tropical_fish_bucket": 1,
    "minecraft:tadpole_bucket": 1,
    "minecraft:cake": 1,
    "minecraft:minecart": 1,
    "minecraft:chest_minecart": 1,
    "minecraft:furnace_minecart": 1,
    "minecraft:hopper_minecart": 1,
    "minecraft:tnt_minecart": 1,
    "minecraft:command_block_minecart": 1,
    "minecraft:spawner_minecart": 1,
    "minecraft:oak_boat": 1,
    "minecraft:oak_chest_boat": 1,
    "minecraft:saddle": 1,
    "minecraft:elytra": 1,
    "minecraft:shield": 1,
    "minecraft:totem_of_undying": 1,
    "minecraft:firework_rocket": 64,
    "minecraft:iron_ingot": 64,
}

# 单堆叠物品的判定关键字（不在表里也能识别）
_SINGLE_HINTS = ("_boat", "_minecart", "bucket", "helmet", "chestplate",
                 "leggings", "boots", "shield", "elytra", "_sword", "_pickaxe",
                 "_axe", "_shovel", "_hoe", "_bow", "crossbow", "trident",
                 "fishing_rod", "_horse_armor", "totem", "bed")

# 可以附魔的判定：默认几乎所有物品都行，这里只挑出明显不行的
_ENCHANTABLE_BLACKLIST = ("minecraft:stick", "minecraft:diamond")

# 附魔 ID 缓存（用于附魔组件里的键名提示）
_KNOWN_ENCHANTMENTS = (
    "minecraft:sharpness", "minecraft:smite", "minecraft:bane_of_arthropods",
    "minecraft:knockback", "minecraft:fire_aspect", "minecraft:looting",
    "minecraft:sweeping_edge", "minecraft:efficiency",
    "minecraft:silk_touch", "minecraft:unbreaking", "minecraft:fortune",
    "minecraft:power", "minecraft:punch", "minecraft:flame",
    "minecraft:infinity", "minecraft:luck_of_the_sea", "minecraft:lure",
    "minecraft:loyalty", "minecraft:impaling", "minecraft:riptide",
    "minecraft:channeling", "minecraft:multishot", "minecraft:quick_charge",
    "minecraft:piercing", "minecraft:protection", "minecraft:fire_protection",
    "minecraft:feather_falling", "minecraft:blast_protection",
    "minecraft:projectile_protection", "minecraft:respiration",
    "minecraft:aqua_affinity", "minecraft:thorns", "minecraft:depth_strider",
    "minecraft:frost_walker", "minecraft:binding_curse",
    "minecraft:soul_speed", "minecraft:swift_sneak",
    "minecraft:vanishing_curse", "minecraft:mending",
)

# 需要"先有物品 ID"才有意义的组件（用于输入顺序提示）
COMPONENT_GROUPS: list[tuple[str, str]] = [
    ("name", "名称与描述"),
    ("enchant", "附魔"),
    ("combat", "战斗与耐久"),
    ("attribute", "属性与修饰"),
    ("appearance", "外观与染色"),
    ("food", "食物与药水"),
    ("container", "容器与收纳"),
    ("misc", "其他"),
]


# =========================================================================
# 数据模型
# =========================================================================

@dataclass
class ComponentSpec:
    """一条物品组件：`id=value` 或 `!id`（移除）。"""

    id: str = ""
    value: str = ""
    mode: str = MODE_SET

    def __post_init__(self) -> None:
        self.id = normalize_id(self.id or "")
        if not self.id:
            self.id = "minecraft:custom_data"
        self.mode = MODE_REMOVE if str(self.mode) == MODE_REMOVE else MODE_SET
        if self.mode == MODE_REMOVE:
            self.value = ""

    # ---- 序列化 ----
    def to_text(self) -> str:
        if self.mode == MODE_REMOVE:
            return f"!{self.id}"
        if not self.value.strip():
            return self.id
        return f"{self.id}={self.value.strip()}"

    def to_dict(self) -> dict:
        return {"id": self.id, "mode": self.mode, "value": self.value}

    @classmethod
    def from_dict(cls, data: dict | "ComponentSpec") -> "ComponentSpec":
        if isinstance(data, ComponentSpec):
            return data.clone()
        if not isinstance(data, dict):
            raise ValueError("组件必须是对象")
        cid = str(data.get("id", "")).strip()
        if not cid:
            raise ValueError("组件缺少 id 字段")
        return cls(id=cid,
                   value=str(data.get("value", "")),
                   mode=str(data.get("mode", MODE_SET)))

    def clone(self) -> "ComponentSpec":
        return ComponentSpec(self.id, self.value, self.mode)

    @property
    def removes(self) -> bool:
        return self.mode == MODE_REMOVE


# =========================================================================
# ID 规范化
# =========================================================================

def normalize_id(raw: str) -> str:
    """补全命名空间：`stone` → `minecraft:stone`；`foo:bar` 原样返回。"""
    text = (raw or "").strip().strip(",").strip()
    if not text:
        return ""
    if ":" in text:
        ns, path = text.split(":", 1)
        ns = ns.strip()
        path = path.strip()
        if not ns or not path:
            return text
        return f"{ns}:{path}"
    return f"minecraft:{text}"


def is_namespaced(raw: str) -> bool:
    return bool(ID_RE.match((raw or "").strip()))


def short_id(raw: str) -> str:
    """去掉 minecraft: 前缀，便于显示。"""
    text = (raw or "").strip()
    return text.split(":", 1)[1] if text.startswith("minecraft:") else text


def max_stack_size(item: str) -> int:
    """单堆叠上限（粗粒度，用于给出「数量过大」提示）。"""
    iid = normalize_id(item)
    if iid in _STACK_OVERRIDES:
        return _STACK_OVERRIDES[iid]
    short = short_id(iid)
    if short.startswith("potion") or short.endswith("_potion"):
        return 1
    if any(h in short for h in _SINGLE_HINTS):
        return 1
    return 64


def enchantable(item: str) -> bool:
    iid = normalize_id(item)
    if iid in _ENCHANTABLE_BLACKLIST:
        return False
    short = short_id(iid)
    return not short.endswith("_block") and "book" not in short


def known_enchantments() -> tuple[str, ...]:
    return _KNOWN_ENCHANTMENTS


# =========================================================================
# 切分与解析
# =========================================================================

def find_component_start(command: str) -> int:
    """定位命令中的顶层 `[`（组件块起点），找不到返回 -1。

    必须跳过目标选择器自带的方括号：`give @e[nbt={a:1},limit=1] stone[d=1]`
    里第一个 `[` 属于选择器，组件块是第二个。
    """
    body = (command or "").lstrip()
    if not body:
        return -1
    # 目标选择器 / NBT 自带的方括号属于「参数」而不是组件块，先整体跳过。
    # 只有紧跟其后的 `[` 才是选择器参数（裸 @a 与 @a{...} 都要让位）。
    if body[0] == "@" and "[" in body:
        head_end = _selector_head_end(body)
        if head_end > 0:
            return _first_top_bracket(body[head_end:], head_end)
    return _first_top_bracket(body, 0)


def _selector_head_end(body: str) -> int:
    """返回目标选择器自身参数段的结束下标（不含），没有参数时返回 -1。"""
    i = 0
    n = len(body)
    while i < n and not body[i].isspace():
        if body[i] == "[":
            depth = 0
            while i < n:
                if body[i] == "[":
                    depth += 1
                elif body[i] == "]":
                    depth -= 1
                    if depth == 0:
                        return i + 1
                i += 1
            return -1
        if body[i] == "{":
            depth = 0
            while i < n:
                if body[i] == "{":
                    depth += 1
                elif body[i] == "}":
                    depth -= 1
                    if depth == 0:
                        return i + 1
                i += 1
            return -1
        i += 1
    return -1


def _first_top_bracket(text: str, offset: int = 0) -> int:
    depth = 0
    for i, ch in enumerate(text):
        if ch == "[":
            if depth == 0:
                return offset + i
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
    return -1


def wrap_bare_value(text: str) -> str:
    """把裸文字（含中文/空格）包成 SNBT 字符串，已经是 SNBT 的原样返回。

    `重命名物品` → `"重命名物品"`；`{a:1}` / `"x"` / `1` 原样返回。
    """
    body = (text or "").strip()
    if not body:
        return body
    if body[0] in "\"{[(" or body[0].isdigit() or body[0] in "+-.":
        return body
    if body.lower() in ("true", "false"):
        return body
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_./:-]*$", body):
        return '"' + body.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return body


def split_top_level(body: str) -> list[str]:
    """按顶层逗号切分，尊重引号、嵌套括号与转义。"""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    escaped = False
    for ch in body:
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
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return parts


def parse_components(block: str) -> list[ComponentSpec]:
    """解析组件块（可带或不带最外层方括号）。

    非法片段（如空串）直接跳过；返回顺序与输入一致。
    """
    body = (block or "").strip()
    if body.startswith("[") and body.endswith("]"):
        body = body[1:-1]
    out: list[ComponentSpec] = []
    for raw in split_top_level(body):
        item = raw.strip()
        if not item:
            continue
        if item.startswith("!"):
            cid = item[1:].strip()
            if cid:
                out.append(ComponentSpec(id=cid, mode=MODE_REMOVE))
            continue
        if "=" in item:
            cid, value = item.split("=", 1)
            cid = cid.strip()
            if not cid:
                continue
            out.append(ComponentSpec(id=cid, value=value.strip(), mode=MODE_SET))
            continue
        out.append(ComponentSpec(id=item, mode=MODE_SET))
    return out


def serialize_components(specs: Iterable[ComponentSpec | dict]) -> str:
    """生成 `[a=1,b]`；空列表返回空串。"""
    texts: list[str] = []
    for spec in specs:
        if isinstance(spec, dict):
            spec = ComponentSpec.from_dict(spec)
        text = spec.to_text()
        if text:
            texts.append(text)
    return "[" + ",".join(texts) + "]" if texts else ""


def dedupe_components(specs: Iterable[ComponentSpec]) -> list[ComponentSpec]:
    """同 ID 去重（后者覆盖前者，保留首次出现的位置）。"""
    order: list[str] = []
    table: dict[str, ComponentSpec] = {}
    for spec in specs:
        if spec.id not in table:
            order.append(spec.id)
        table[spec.id] = spec
    return [table[i] for i in order]


# =========================================================================
# 值校验
# =========================================================================

def _parse_snbt(value: str):
    from app.core.snbt import SnbtParser
    return SnbtParser(value).parse()


def _bool_value_ok(value: str) -> bool:
    v = value.strip().lower()
    return v in ("1b", "0b", "true", "false")


def check_component_value(definition: dict, value: str) -> list[tuple[str, str]]:
    """按组件定义检查值，返回 [(level, message), ...]。"""
    out: list[tuple[str, str]] = []
    body = (value or "").strip()
    if not body:
        out.append(("warn", "值为空：建议填写具体值，或改用「移除」模式"))
        return out

    kind = str(definition.get("kind", "text"))
    if kind == "bool":
        if not _bool_value_ok(body):
            out.append(("warn", "布尔组件建议写 1b / 0b（true / false 也可）"))
        return out
    if kind == "int":
        text = body.lower()
        for suffix in _INT_SUFFIXES:
            if text.endswith(suffix):
                text = text[:-1]
                break
        try:
            int(text)
        except ValueError:
            out.append(("error", f"应为整数（可用 1b/2s/3l 后缀），当前为 “{body}”"))
        return out
    if kind == "enum":
        options = [str(o) for o in definition.get("options", [])]
        if options and short_id(body) not in [short_id(o) for o in options]:
            out.append(("warn", f"取值不在建议列表：{', '.join(options)}"))
        return out
    if kind == "nbt":
        try:
            _parse_snbt(body)
        except Exception as exc:  # SnbtParseError 及少数类型错误
            out.append(("error", f"SNBT 语法错误：{_short_error(exc)}"))
            return out
        out.extend(_check_structural(definition, body))
        return out
    return out


def _short_error(exc: Exception) -> str:
    text = str(exc).strip().replace("\n", " ")
    return text[:120] if text else exc.__class__.__name__


def _check_structural(definition: dict, body: str) -> list[tuple[str, str]]:
    """针对几个高频组件的结构提示。"""
    out: list[tuple[str, str]] = []
    cid = str(definition.get("id", ""))
    if cid in ("minecraft:enchantments", "minecraft:stored_enchantments"):
        if body.startswith("{"):
            try:
                node = _parse_snbt(body)
            except Exception:
                return out
            keys = {c.key for c in node.children if c.key}
            if "levels" not in keys:
                if keys & {"minecraft:sharpness", "sharpness"}:
                    out.append(("warn", "1.21 起应写作 "
                                        "{\"levels\":{\"minecraft:sharpness\":5}}"))
                else:
                    out.append(("warn", "缺少 levels 字段："
                                        "{\"levels\":{\"<附魔>\":<等级>}}"))
        elif body.startswith("["):
            out.append(("warn", "1.21 起附魔组件是 {levels:{…}} 而非列表"))
    if cid == "minecraft:dyed_color" and body.startswith("[") \
            and not body.startswith("[I;"):
        out.append(("warn", "1.21 起染色组件为 {\"rgb\":<十进制整数>}"))
    if cid == "minecraft:attribute_modifiers":
        if body.startswith("{"):
            out.append(("warn", "属性修饰符应为列表：[{type:…,amount:…}]"))
        else:
            out.extend(_check_modifier_ids(body))
    if cid == "minecraft:container" and body.startswith("{"):
        out.append(("warn", "容器内容应为列表：[{slot:0,item:{id:…,count:…}}]"))
    if cid in ("minecraft:enchantments", "minecraft:stored_enchantments"):
        out.extend(_check_enchantment_names(body))
    return out


def _check_modifier_ids(body: str) -> list[tuple[str, str]]:
    """属性修饰符：1.21 用 id 字段（旧写法是 name）。"""
    if not body.startswith("["):
        return []
    try:
        node = _parse_snbt(body)
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    for child in node.children:
        if child.kind.value != "compound":
            continue
        keys = {c.key for c in child.children if c.key}
        if "name" in keys and "id" not in keys:
            out.append(("warn", "1.21 起修饰符用 id 字段（不再是 name），"
                                "例如 id:\"mcgen:bonus\""))
            break
    return out


def _check_enchantment_names(body: str) -> list[tuple[str, str]]:
    """附魔键名是否认识（拼错时给提示，不认识 ≠ 一定错，可能是模组附魔）。"""
    out: list[tuple[str, str]] = []
    if not body.startswith("{"):
        return out
    try:
        node = _parse_snbt(body)
    except Exception:
        return out
    levels = node.get_child("levels")
    if levels is None:
        return out
    known = enchantment_names()
    known_short = {short_id(k) for k in known}
    unknown = [str(c.key) for c in levels.children
               if c.key and short_id(str(c.key)) not in known_short]
    for key in unknown[:3]:
        out.append(("warn", f"附魔 “{key}” 不在内置附魔表中，"
                            "请确认拼写（自定义附魔可忽略）"))
    return out


def validate_components(
        specs: Iterable[ComponentSpec | dict],
        item: str = "",
        defs: dict[str, dict] | None = None,
) -> list[tuple[str, str]]:
    """整体校验，返回 [(level, message), ...]。"""
    table = defs if defs is not None else component_defs()

    out: list[tuple[str, str]] = []
    seen: dict[str, str] = {}

    for raw in specs:
        spec = raw if isinstance(raw, ComponentSpec) else ComponentSpec.from_dict(raw)
        cid = spec.id
        short = short_id(cid)

        if not is_namespaced(cid):
            out.append(("error", f"组件 ID “{cid}” 不合法（应为 命名空间:路径）"))
            continue

        if cid in seen:
            if seen[cid] == spec.mode:
                out.append(("warn", f"组件 {short} 重复出现，只会生效最后一个"))
            else:
                out.append(("warn", f"组件 {short} 同时设置了「设置」和「移除」，"
                                    "语义混乱"))
        seen[cid] = spec.mode

        definition = table.get(cid)
        if definition is None and table:
            out.append(("warn", f"未知组件 {short}：不在内置组件表中，"
                                "请确认版本或拼写"))
            continue

        if definition is None:
            if not spec.removes:
                out.extend(check_component_value({}, spec.value))
            continue

        if spec.removes:
            if not definition.get("removable", True):
                out.append(("warn", f"{short} 是物品固有组件，"
                                    "移除后通常无实际效果"))
            continue

        out.extend(check_component_value(definition, spec.value))

        applies = definition.get("applies")
        if applies and item:
            tags = _item_categories().get(normalize_id(item))
            if tags and not (set(applies) & tags):
                out.append(("warn", f"{short} 通常不适用于 "
                                    f"{short_id(item)}（该物品标签："
                                    f"{'、'.join(sorted(tags)) or '无'}）"))

    return out


def _item_categories() -> dict[str, set[str]]:
    """物品 ID → 标签集合（items.json 的 tags + category + 名称推断）。"""
    global _ITEM_TAG_CACHE
    if _ITEM_TAG_CACHE is not None:
        return _ITEM_TAG_CACHE
    from app.core.data import store as _store
    out: dict[str, set[str]] = {}
    for entry in _store().items() or []:
        if not isinstance(entry, dict):
            continue
        iid = normalize_id(str(entry.get("id", "")))
        if not iid:
            continue
        tags = {str(t) for t in entry.get("tags", []) if t}
        cat = str(entry.get("category", ""))
        if cat:
            tags.add(cat)
        short = short_id(iid)
        if short.endswith(("_sword", "_axe", "_pickaxe", "_shovel", "_hoe",
                           "_bow", "crossbow", "trident", "fishing_rod",
                           "_mace")):
            tags.add("tool")
        if short.endswith(("_sword", "_axe", "bow", "crossbow", "trident")):
            tags.add("weapon")
        if short.endswith(("_helmet", "_chestplate", "_leggings", "_boots")):
            tags.add("armor")
            tags.add("durable")
        if short.endswith("_leggings"):
            tags.add("armor_legs")
        if short.endswith("_boots"):
            tags.add("armor_feet")
        if short.endswith("_helmet"):
            tags.add("armor_head")
            tags.add("head")
        if "potion" in short:
            tags.add("potion")
        if "book" in short:
            tags.add("book")
        if short.endswith(("_block", "_planks", "_wool", "_concrete")):
            tags.add("block")
        out[iid] = tags
    _ITEM_TAG_CACHE = out
    return out


# =========================================================================
# 组件定义表（来自 app/data/builtin/item_components.json）
# =========================================================================

_DEFS_CACHE: dict[str, dict] | None = None
_DEF_LIST_CACHE: list[dict] | None = None
_ITEM_TAG_CACHE: dict[str, set[str]] | None = None


def component_list(reload: bool = False) -> list[dict]:
    global _DEF_LIST_CACHE
    if _DEF_LIST_CACHE is None or reload:
        from app.core.data import store
        _DEF_LIST_CACHE = [d for d in store().item_components()
                           if isinstance(d, dict) and d.get("id")]
    return list(_DEF_LIST_CACHE)


def component_defs(reload: bool = False) -> dict[str, dict]:
    global _DEFS_CACHE
    if _DEFS_CACHE is None or reload:
        table: dict[str, dict] = {}
        for entry in component_list(reload=reload):
            cid = str(entry.get("id", "")).strip()
            if not cid:
                continue
            entry = dict(entry)
            entry.setdefault("kind", "text")
            entry.setdefault("display", short_id(cid))
            table[cid] = entry
        _DEFS_CACHE = table
    return _DEFS_CACHE


def clear_cache() -> None:
    global _DEFS_CACHE, _DEF_LIST_CACHE, _ITEM_TAG_CACHE
    _DEFS_CACHE = None
    _DEF_LIST_CACHE = None
    _ITEM_TAG_CACHE = None


def grouped_components() -> list[tuple[str, list[dict]]]:
    """按 group 字段分组，供选择器展示（顺序取自数据文件的 groups）。"""
    from app.core.data import store
    labels = [tuple(pair) for pair in store().item_component_groups()] \
        or list(COMPONENT_GROUPS)
    known = {key for key, _label in labels}
    buckets: dict[str, list[dict]] = {}
    for entry in component_list():
        group = str(entry.get("group", "misc")) or "misc"
        buckets.setdefault(group, []).append(entry)
    out: list[tuple[str, list[dict]]] = []
    for key, label in labels:
        if key in buckets:
            out.append((label, sorted(buckets[key],
                                      key=lambda d: str(d.get("display", "")))))
    for key in sorted(buckets):
        if key not in known:
            out.append((key, sorted(buckets[key],
                                    key=lambda d: str(d.get("display", "")))))
    return out


def component_preview(spec: ComponentSpec | dict,
                      defs: dict[str, dict] | None = None) -> str:
    """给表格用的一行简短预览，例如 `附魔 · 锋利 V`。"""
    if isinstance(spec, dict):
        spec = ComponentSpec.from_dict(spec)
    table = defs if defs is not None else component_defs()
    definition = table.get(spec.id, {})
    display = str(definition.get("display", short_id(spec.id)))
    if spec.removes:
        return f"移除 {display}"
    value = spec.value.strip()
    if not value:
        return display
    pretty = _preview_value(spec.id, value, definition)
    return f"{display} · {pretty}"


def _preview_value(cid: str, value: str, definition: dict) -> str:
    short = short_id(cid)
    if definition.get("kind") == "bool":
        return "是" if value.strip().lower() in ("1b", "true", "1") else "否"
    if short in ("enchantments", "stored_enchantments"):
        try:
            node = _parse_snbt(value)
            levels = node.get_child("levels")
            if levels is not None:
                names = enchantment_names()
                pairs = [f"{names.get(c.key or '', short_id(c.key or ''))} {c.value}"
                         for c in levels.children][:3]
                if pairs:
                    return "、".join(pairs)
        except Exception:
            pass
    if short == "custom_name" or short == "item_name":
        return value.strip('"')
    if short == "lore":
        return value
    return value if len(value) <= 48 else value[:45] + "…"


_ENCHANT_CACHE: dict[str, str] | None = None


def enchantment_names() -> dict[str, str]:
    """附魔 ID → 中文名（数据来自 enchantments.json）。"""
    global _ENCHANT_CACHE
    if _ENCHANT_CACHE is None:
        from app.core.data import store
        table: dict[str, str] = {}
        for entry in store().enchantments():
            if not isinstance(entry, dict):
                continue
            eid = str(entry.get("id", "")).strip()
            if eid:
                table[eid] = str(entry.get("display", "") or short_id(eid))
        _ENCHANT_CACHE = table
    return _ENCHANT_CACHE


def enchantment_ids() -> list[str]:
    return list(enchantment_names().keys())


def known_component_ids() -> list[str]:
    return list(component_defs().keys())


__all__ = [
    "ComponentSpec", "MODE_SET", "MODE_REMOVE",
    "parse_components", "serialize_components", "dedupe_components",
    "validate_components", "check_component_value",
    "find_component_start", "split_top_level", "wrap_bare_value",
    "normalize_id", "is_namespaced", "short_id", "max_stack_size",
    "enchantable", "known_enchantments", "known_component_ids",
    "component_defs", "component_list", "component_preview",
    "grouped_components", "clear_cache",
    "COMPONENT_GROUPS", "ID_RE",
]
