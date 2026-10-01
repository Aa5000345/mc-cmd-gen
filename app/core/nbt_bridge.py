"""存档 NBT ↔ 命令的桥梁。

把存档里的数据识别成「能直接用的东西」：

* **物品堆**（`{id:"minecraft:stone", count:64, components:{…}}`，以及旧写法
  `{id:…, Count:64b, Slot:0b, tag:{…}}`）→ `/give` 面板 / `/give` 命令；
* **物品容器**（`Inventory` / `Items` / `EnderItems` / `ArmorItems` …）→ 图形化
  的物品浏览列表；
* **实体数据** → `/summon` 命令（带 NBT）或 `/data` 命令。

这一层刻意不依赖 Qt，方便单元测试；界面只负责调用。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterator, Optional

from app.core.data import store as data_store
from app.core.snbt import SnbtKind, SnbtNode

#: 常见物品容器字段名（识别不到时按结构判断）
CONTAINER_KEYS = (
    "Inventory", "Items", "EnderItems", "ArmorItems", "HandItems",
    "SaddleItem", "Item", "item", "contents", "ItemsList",
)

#: 单个物品里与「数量 / 位置」有关的字段，转命令时不再重复写进组件
BOOKKEEPING_KEYS = ("Slot", "slot", "Count", "count", "id", "tag",
                    "components", "Damage")

#: 旧版 NBT 物品的 `tag` 里有这些字段时，1.21 起已经变成物品组件
LEGACY_TAG_COMPONENTS = {
    "display": None, "Enchantments": "minecraft:enchantments",
    "Unbreakable": "minecraft:unbreakable", "Damage": "minecraft:damage",
    "AttributeModifiers": "minecraft:attribute_modifiers",
    "CustomModelData": "minecraft:custom_model_data",
    "CanDestroy": "minecraft:can_break", "CanPlaceOn": "minecraft:can_place_on",
    "HideFlags": None, "RepairCost": "minecraft:repair_cost",
}

#: SNBT 里要加引号的字符串判定（与 core/snbt.py 保持一致的思路）
_BARE_OK = set("abcdefghijklmnopqrstuvwxyz"
               "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-")
_RESERVED = {"true", "false", "null"}


class ItemParseError(ValueError):
    """节点不是可识别的物品 / 数据。"""


# ---------------------------------------------------------------------------
# NBT 取值小工具
# ---------------------------------------------------------------------------

def child(node: Optional[SnbtNode], *keys: str) -> Optional[SnbtNode]:
    """按顺序找第一个存在的子标签（兼容大小写不同的写法）。"""
    if node is None or node.kind != SnbtKind.COMPOUND:
        return None
    for key in keys:
        found = node.get_child(key)
        if found is not None:
            return found
    lowered = {str(c.key).lower(): c for c in node.children if c.key}
    for key in keys:
        found = lowered.get(key.lower())
        if found is not None:
            return found
    return None


def child_text(node: Optional[SnbtNode], *keys: str) -> str:
    found = child(node, *keys)
    if found is None or found.is_container:
        return ""
    return str(found.value)


def child_int(node: Optional[SnbtNode], *keys: str, default: int = 0) -> int:
    found = child(node, *keys)
    if found is None or found.is_container:
        return default
    try:
        return int(found.value)
    except (TypeError, ValueError):
        return default


def build_path(parent_path: str, node: SnbtNode, index: int) -> str:
    """给节点拼出 `Data.Player.Inventory[0]` 这样的路径。"""
    if node.key is not None and str(node.key):
        return f"{parent_path}.{node.key}" if parent_path else str(node.key)
    return f"{parent_path}[{index}]" if parent_path else f"[{index}]"


def iter_nodes(root: SnbtNode) -> Iterator[tuple[str, SnbtNode]]:
    """深度优先遍历，返回 (路径, 节点)。"""
    stack: list[tuple[str, SnbtNode]] = [("", root)]
    while stack:
        path, node = stack.pop()
        yield path, node
        for index, item in enumerate(reversed(node.children)):
            position = len(node.children) - 1 - index
            stack.append((build_path(path, item, position), item))


# ---------------------------------------------------------------------------
# 物品
# ---------------------------------------------------------------------------

@dataclass
class ItemStack:
    """存档里的一个物品堆。"""

    item_id: str
    count: int = 1
    slot: Optional[int] = None
    components: dict[str, object] = field(default_factory=dict)
    path: str = ""
    source: str = ""
    damage: Optional[int] = None
    display_name: str = ""
    lore: list[str] = field(default_factory=list)
    enchantments: list[tuple[str, int]] = field(default_factory=list)

    # ---- 展示 ----

    def clean_id(self) -> str:
        return normalize_item_id(self.item_id)

    def display(self) -> str:
        """中文名（没有就用 ID）。"""
        if self.display_name:
            return self.display_name
        return item_display(self.item_id) or self.item_id

    def summary(self) -> str:
        """一行摘要：`钻石剑 ×1 · 锋利 V · 耐久 III`。"""
        parts = [f"{self.display()} ×{self.count}"]
        for eid, level in self.enchantments[:4]:
            parts.append(f"{enchant_display(eid)} {roman(level)}")
        if len(self.enchantments) > 4:
            parts.append(f"另 {len(self.enchantments) - 4} 项附魔")
        if self.components:
            parts.append(f"{len(self.components)} 个组件")
        if self.slot is not None:
            parts.append(f"槽位 {self.slot}")
        return " · ".join(parts)

    def component_count(self) -> int:
        return len(self.components)

    # ---- 转换 ----

    def components_snbt(self) -> str:
        """`[component=value,…]` 形式的组件文本（省略 `minecraft:` 前缀）。"""
        if not self.components:
            return ""
        chunks = []
        for cid, value in self.components.items():
            short = cid.split(":", 1)[-1]
            text = component_value_snbt(value)
            chunks.append(f"{short}={text}" if text else short)
        return "[" + ",".join(chunks) + "]"

    def give_argument(self) -> str:
        """完整物品参数：`minecraft:stone[components…]`，数量另写。"""
        return f"{self.clean_id()}{self.components_snbt()}"

    def to_give_steps(self) -> list[tuple[str, str]]:
        """拆成 `/give` 面板用的 (组件 id, 组件值) 列表。"""
        return [(cid, component_value_snbt(value))
                for cid, value in self.components.items()]

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "count": self.count,
            "slot": self.slot,
            "components": self.components,
            "path": self.path,
            "source": self.source,
            "display_name": self.display_name,
            "lore": list(self.lore),
            "enchantments": [[e, lv] for e, lv in self.enchantments],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ItemStack":
        stack = cls(item_id=str(data.get("item_id", "")),
                    count=int(data.get("count", 1) or 1),
                    slot=data.get("slot"),
                    path=str(data.get("path", "")),
                    source=str(data.get("source", "")),
                    display_name=str(data.get("display_name", "")))
        stack.components = dict(data.get("components") or {})
        stack.lore = [str(x) for x in (data.get("lore") or [])]
        stack.enchantments = [(str(e), int(lv))
                              for e, lv in (data.get("enchantments") or [])]
        try:
            stack.slot = int(stack.slot) if stack.slot is not None else None
        except (TypeError, ValueError):
            stack.slot = None
        return stack


def normalize_item_id(item_id: str) -> str:
    """补上 `minecraft:` 前缀。"""
    text = str(item_id or "").strip()
    if not text:
        return "minecraft:air"
    return text if ":" in text else f"minecraft:{text}"


def item_display(item_id: str) -> str:
    """查注册表拿中文名（查不到返回空串）。"""
    key = normalize_item_id(item_id)
    cached = _item_index().get(key)
    return str(cached.get("display", "")) if cached else ""


def lookup_item(item_id: str) -> dict:
    return dict(_item_index().get(normalize_item_id(item_id), {}))


def item_stack_size(item_id: str) -> int:
    entry = _item_index().get(normalize_item_id(item_id)) or {}
    try:
        return int(entry.get("stack_size", 64) or 64)
    except (TypeError, ValueError):
        return 64


_item_cache: dict[str, dict] | None = None


def _item_index() -> dict[str, dict]:
    global _item_cache
    if _item_cache is None:
        _item_cache = {}
        try:
            for entry in data_store().items():
                if isinstance(entry, dict) and entry.get("id"):
                    _item_cache[str(entry["id"])] = entry
        except Exception:                                  # pragma: no cover
            _item_cache = {}
    return _item_cache


def clear_registry_cache() -> None:
    """内置数据重载后调用。"""
    global _item_cache, _enchant_index
    _item_cache = None
    _enchant_index = None


_enchant_index: dict[str, dict] | None = None


def _enchantments() -> dict[str, dict]:
    global _enchant_index
    if _enchant_index is None:
        _enchant_index = {}
        try:
            for entry in data_store().enchantments():
                if isinstance(entry, dict) and entry.get("id"):
                    _enchant_index[str(entry["id"])] = entry
        except Exception:                                  # pragma: no cover
            _enchant_index = {}
    return _enchant_index


def enchant_display(enchant_id: str) -> str:
    key = normalize_item_id(enchant_id)
    entry = _enchantments().get(key) or {}
    return str(entry.get("display", "")) or str(enchant_id).split(":")[-1]


_ROMAN = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
          (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),
          (5, "V"), (4, "IV"), (1, "I"))


def roman(number: int) -> str:
    """附魔等级 → 罗马数字（超出范围就退回阿拉伯数字）。"""
    try:
        value = int(number)
    except (TypeError, ValueError):
        return str(number)
    if not 1 <= value <= 3999:
        return str(value)
    out = []
    for amount, symbol in _ROMAN:
        while value >= amount:
            out.append(symbol)
            value -= amount
    return "".join(out)


# ---------------------------------------------------------------------------
# SNBT 值格式化（物品组件用）
# ---------------------------------------------------------------------------

def quote_snbt(text: str) -> str:
    """需要时给字符串加引号（和 core/snbt.py 的规则一致）。"""
    if text == "":
        return '""'
    if all(ch in _BARE_OK for ch in text) and text not in _RESERVED:
        return text
    escaped = (text.replace("\\", "\\\\").replace('"', '\\"')
               .replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r"))
    return f'"{escaped}"'


def component_value_snbt(value: object) -> str:
    """把组件取值转成 SNBT 文本。

    存档里的组件取值大多是「已经是 SNBT/JSON 的字符串」（例如
    `custom_name` 存的是 `{"text":"…"}`），这种要保持原样；纯文本才加引号。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1b" if value else "0b"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, SnbtNode):
        from app.core.snbt import serialize
        return serialize(value, pretty=False)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = str(value)
    stripped = text.strip()
    if not stripped:
        return '""'
    # 已经是复合 / 列表 / 带引号的字面量就原样使用
    if stripped[0] in "{[":
        return stripped
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        return stripped
    # 数字 / 带后缀的数值也别加引号
    if _looks_like_number(stripped):
        return stripped
    return quote_snbt(text)


def _looks_like_number(text: str) -> bool:
    body = text[:-1] if text[-1:] in "bBsSlLfFdD" else text
    try:
        float(body)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# 识别
# ---------------------------------------------------------------------------

def looks_like_item(node: Optional[SnbtNode]) -> bool:
    """`{id:"minecraft:xxx", …}` 就是物品。"""
    if node is None or node.kind != SnbtKind.COMPOUND:
        return False
    item_id = child_text(node, "id", "Id", "ID")
    if not item_id or ":" not in item_id:
        return False
    # 物品一定有数量 / 组件 / 槽位之一，或者 ID 能在注册表里查到
    if child(node, "count", "Count", "components", "tag", "Slot") is not None:
        return True
    return normalize_item_id(item_id) in _item_index()


def looks_like_item_list(node: Optional[SnbtNode]) -> bool:
    if node is None or node.kind != SnbtKind.LIST or not node.children:
        return False
    if not all(child.kind == SnbtKind.COMPOUND for child in node.children):
        return False
    return any(looks_like_item(child) for child in node.children)


def is_item_container(node: Optional[SnbtNode]) -> bool:
    """List 里全是物品，或 Compound 的每个值都是物品。"""
    if node is None:
        return False
    if looks_like_item_list(node):
        return True
    if node.kind == SnbtKind.COMPOUND and node.children:
        return all(looks_like_item(c) for c in node.children)
    return False


def container_kind(node: SnbtNode) -> str:
    if looks_like_item_list(node):
        return "list"
    return "dict"


# ---------------------------------------------------------------------------
# 解析物品
# ---------------------------------------------------------------------------

def parse_item(node: SnbtNode, path: str = "",
               source: str = "") -> ItemStack:
    """把一个物品节点解析成 ItemStack。"""
    if not looks_like_item(node):
        raise ItemParseError("节点不是物品（缺少 id:命名空间:路径）")
    stack = ItemStack(item_id=child_text(node, "id", "Id", "ID"),
                      path=path, source=source)
    stack.count = max(1, child_int(node, "count", "Count", default=1))
    slot_node = child(node, "Slot", "slot")
    if slot_node is not None and not slot_node.is_container:
        try:
            stack.slot = int(slot_node.value)
        except (TypeError, ValueError):
            stack.slot = None

    components: dict[str, object] = {}
    # 1.20.5+ 写法：components:{…}
    comp_node = child(node, "components", "Components")
    if comp_node is not None and comp_node.kind == SnbtKind.COMPOUND:
        for entry in comp_node.children:
            components[_component_id(entry.key)] = _component_value(entry)
    # 旧写法：tag:{…}
    tag = child(node, "tag", "Tag")
    if tag is not None and tag.kind == SnbtKind.COMPOUND:
        for entry in tag.children:
            key = str(entry.key or "")
            if key in LEGACY_TAG_COMPONENTS:
                mapped = LEGACY_TAG_COMPONENTS[key]
                if mapped is None:
                    continue            # 1.21 起已移除的字段（display/HideFlags）
                components[_component_id(mapped)] = _component_value(entry)
            else:
                components[_component_id(key)] = _component_value(entry)
        # 旧写法把名字放在 tag.display.Name 里，1.21 起是 custom_name 组件
        legacy_name = _legacy_display_name(tag)
        if legacy_name and "minecraft:custom_name" not in components:
            components["minecraft:custom_name"] = legacy_name
    damage = child_int(node, "Damage", default=-1)
    if damage >= 0 and "minecraft:damage" not in components:
        components["minecraft:damage"] = damage
        stack.damage = damage

    stack.components = components
    _read_display(stack, node)
    return stack


def _component_id(key: Optional[str]) -> str:
    name = str(key or "")
    return name if ":" in name else f"minecraft:{name}"


def _legacy_display_name(tag: SnbtNode) -> str:
    """旧写法 `tag.display.Name` → 名字文本（没有就返回空串）。"""
    display = child(tag, "display", "Display")
    if display is None:
        return ""
    if display.kind == SnbtKind.COMPOUND:
        name_node = child(display, "Name", "name")
        if name_node is None:
            return ""
        if name_node.is_container:
            from app.core.snbt import serialize
            return serialize(name_node, pretty=False)
        return str(name_node.value)
    if display.is_container:                                # pragma: no cover
        return ""
    return str(display.value)


def _component_value(node: SnbtNode) -> object:
    """组件取值：标量直接取值，容器转成 SNBT 文本（保持存档原样）。"""
    if node.is_container:
        from app.core.snbt import serialize
        return serialize(node, pretty=False)
    if node.kind == SnbtKind.STRING:
        return str(node.value)
    return node.value


def _read_display(stack: ItemStack, node: SnbtNode) -> None:
    """从组件里读出中文名 / 附魔 / 描述，供图形化显示用。"""
    name = stack.components.get("minecraft:custom_name")
    if name:
        stack.display_name = _text_from_jsonish(name) or stack.display_name
    lore = stack.components.get("minecraft:lore")
    if lore:
        if isinstance(lore, str):
            try:
                parsed = json.loads(lore)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, list):
                stack.lore = [_text_from_jsonish(x) for x in parsed]
        elif isinstance(lore, list):
            stack.lore = [_text_from_jsonish(x) for x in lore]
    ench = stack.components.get("minecraft:enchantments")
    stack.enchantments = _parse_enchantments(ench)


def _parse_enchantments(value: object) -> list[tuple[str, int]]:
    """支持三种写法：

    * 1.21 组件：`{"levels":{"minecraft:sharpness":5}}`；
    * SNBT 字符串：`{levels:{…}}`；
    * 旧写法：`[{id:"minecraft:sharpness",lvl:2s}]`。
    """
    data = value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") or text.startswith("["):
            from app.core.snbt import SnbtParser, SnbtParseError
            try:
                data = SnbtParser(text).parse()
            except SnbtParseError:
                return []
    if isinstance(data, SnbtNode) and data.kind == SnbtKind.LIST:
        return _legacy_enchantments(data)
    if isinstance(data, SnbtNode):
        levels = child(data, "levels")
        data = {str(c.key): (c.value if not c.is_container else None)
                for c in (levels.children if levels is not None else [])}
    if not isinstance(data, dict):
        return []
    levels = data.get("levels", data)
    if isinstance(levels, SnbtNode):                        # pragma: no cover
        levels = {str(c.key): c.value for c in levels.children}
    if not isinstance(levels, dict):
        return []
    out: list[tuple[str, int]] = []
    for key, level in levels.items():
        try:
            out.append((str(key), int(level)))
        except (TypeError, ValueError):
            continue
    return out


def _legacy_enchantments(node: SnbtNode) -> list[tuple[str, int]]:
    """`[{id:…,lvl:2s}]` 形式的旧附魔列表。"""
    out: list[tuple[str, int]] = []
    for entry in node.children:
        if entry.kind != SnbtKind.COMPOUND:
            continue
        enchant_id = child_text(entry, "id", "Id", "name", "Name")
        level = child_int(entry, "lvl", "level", "Level", default=1)
        if enchant_id:
            out.append((enchant_id, level))
    return out


def _text_from_jsonish(value: object) -> str:
    """把 `{"text":"名字"}` / `'{"text":"…"}'` / 纯文本 都读成人能看的文字。"""
    data = value
    if isinstance(value, SnbtNode):
        from app.core.snbt import serialize
        data = serialize(value, pretty=False)
    if isinstance(data, str):
        text = data.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                data = json.loads(text)
            except (ValueError, TypeError):
                return text
        else:
            return text
    if isinstance(data, dict):
        if "text" in data:
            return str(data.get("text", ""))
        if "translate" in data:
            return str(data.get("translate", ""))
        extra = data.get("extra")
        if isinstance(extra, list):
            return "".join(_text_from_jsonish(x) for x in extra)
        return ""
    if isinstance(data, list):
        return "".join(_text_from_jsonish(x) for x in data)
    return str(data)


# ---------------------------------------------------------------------------
# 搜索容器与物品
# ---------------------------------------------------------------------------

@dataclass
class ItemContainer:
    """一个物品容器（背包 / 末影箱 / 箱子…）。"""

    path: str
    node: SnbtNode
    label: str = ""
    kind: str = "list"

    def stacks(self) -> list[ItemStack]:
        out: list[ItemStack] = []
        if self.kind == "list":
            for index, child_node in enumerate(self.node.children):
                if not looks_like_item(child_node):
                    continue
                try:
                    out.append(parse_item(
                        child_node, f"{self.path}[{index}]", self.label))
                except ItemParseError:                      # pragma: no cover
                    continue
        else:
            for entry in self.node.children:
                if not looks_like_item(entry):
                    continue
                try:
                    out.append(parse_item(
                        entry, build_path(self.path, entry, 0), self.label))
                except ItemParseError:                      # pragma: no cover
                    continue
        return out


def find_item_containers(root: SnbtNode) -> list[ItemContainer]:
    """找出整棵树里所有的物品容器（按路径排序，稳定好读）。"""
    out: list[ItemContainer] = []
    for path, node in iter_nodes(root):
        if not is_item_container(node):
            continue
        key = path.split(".")[-1].split("[")[0]
        label = key or "(根)"
        out.append(ItemContainer(path=path, node=node, label=label,
                                 kind=container_kind(node)))
    return out


def collect_items(root: SnbtNode, source: str = "") -> list[ItemStack]:
    """收集整棵树里的所有物品堆（含所在路径）。"""
    items: list[ItemStack] = []
    for container in find_item_containers(root):
        items.extend(container.stacks())
    if source:
        for stack in items:
            stack.source = source
    return items


def aggregate_items(items: list[ItemStack]) -> list[ItemStack]:
    """按物品 ID + 组件合并，数量相加（槽位清空，便于整体拿走）。"""
    merged: dict[tuple, ItemStack] = {}
    for stack in items:
        key = (stack.clean_id(), json.dumps(stack.components, sort_keys=True,
                                           default=str))
        existing = merged.get(key)
        if existing is None:
            clone = ItemStack(item_id=stack.item_id, count=stack.count,
                              slot=None, components=dict(stack.components),
                              path=stack.path, source=stack.source,
                              display_name=stack.display_name,
                              lore=list(stack.lore),
                              enchantments=list(stack.enchantments))
            merged[key] = clone
        else:
            existing.count += stack.count
    out = list(merged.values())
    out.sort(key=lambda s: (-s.count, s.display()))
    return out


def total_count(items: list[ItemStack]) -> int:
    return sum(max(0, int(s.count)) for s in items)


def unique_kinds(items: list[ItemStack]) -> int:
    return len({s.clean_id() for s in items})


# ---------------------------------------------------------------------------
# 转命令
# ---------------------------------------------------------------------------

def stack_to_command(stack: ItemStack, target: str = "@p") -> str:
    """物品 → `/give` 命令（保留存档里的全部组件）。"""
    argument = stack.give_argument()
    count = max(1, int(stack.count))
    return f"give {target or '@p'} {argument} {count}"


def stack_to_snbt(stack: ItemStack) -> str:
    """物品 → 存档里的 SNBT（可用于 `/data` 命令）。"""
    node = SnbtNode.new_compound()
    node.add_child(SnbtNode.new_scalar(SnbtKind.STRING, stack.clean_id(),
                                       key="id"))
    node.add_child(SnbtNode.new_scalar(SnbtKind.INT, max(1, int(stack.count)),
                                       key="count"))
    if stack.slot is not None:
        node.add_child(SnbtNode.new_scalar(SnbtKind.BYTE, int(stack.slot),
                                           key="Slot"))
    if stack.components:
        from app.core.snbt import SnbtParser
        components = SnbtNode.new_compound(key="components")
        for cid, value in stack.components.items():
            text = component_value_snbt(value)
            try:
                components.add_child(SnbtParser(f"{{{quote_snbt(cid)}:{text}}}")
                                     .parse().children[0])
            except Exception:                              # pragma: no cover
                components.add_child(SnbtNode.new_scalar(
                    SnbtKind.STRING, str(value), key=cid))
        node.add_child(components)
    from app.core.snbt import serialize
    return serialize(node, pretty=False)


def stack_to_data_command(stack: ItemStack, root: str = "",
                          slots: bool = True) -> str:
    """物品 → `/data` 命令：把这一堆写进某个容器的槽位。

    `root` 是容器路径（例如 `Data.Player.Inventory`）；给空则按物品自身路径猜。
    """
    base = root.strip().rstrip(".")
    if not base and stack.path:
        import re
        base = re.sub(r"\[[^\]]*\]$", "", stack.path)     # 去掉 [0]
        base = re.sub(r"\.[^.]+$", "", base) or stack.path
    if not base:
        return stack_to_command(stack)
    snbt = stack_to_snbt(stack)
    if slots and stack.slot is not None:
        return (f"data modify {base} "
                f"[{{Slot:{int(stack.slot)}b}}] set value {snbt}")
    return f"data modify {base} set value [{snbt}]"


def items_to_give_commands(items: list[ItemStack],
                           target: str = "@p") -> list[str]:
    return [stack_to_command(s, target) for s in items]


def stack_to_editor_state(stack: ItemStack, target: Optional[str] = None
                          ) -> dict:
    """物品 → `/give` 面板的状态（物品 / 数量 / 组件）。"""
    state: dict = {
        "item": stack.clean_id(),
        "use_count": True,
        "count": max(1, int(stack.count)),
        "components": [{"id": cid, "value": component_value_snbt(value),
                        "removes": False}
                       for cid, value in stack.components.items()],
    }
    if target:
        state["target"] = target
    return state


def apply_to_give_panel(panel, stack: ItemStack,
                        target: Optional[str] = None) -> bool:
    """把物品写进 `/give` 面板（duck typing，方便测试与别处复用）。

    优先直接设置面板状态；面板不认识组件时退回「把命令文本填进输出区」。
    """
    if panel is None:
        return False
    if hasattr(panel, "receive_item"):
        try:
            panel.receive_item(stack, target)
            return True
        except Exception:                                  # pragma: no cover
            pass
    state = stack_to_editor_state(stack, target)
    builder = getattr(panel, "builder", None)
    if builder is None:
        return False
    try:
        builder.item = state["item"]
        builder.use_count = state["use_count"]
        builder.count = state["count"]
        builder.components = []
        for spec in state["components"]:
            builder.add_component(spec["id"], spec["value"])
        if target:
            builder.target = target
        if hasattr(panel, "reload_from_builder"):
            panel.reload_from_builder()
        return True
    except Exception:                                      # pragma: no cover
        return False


@dataclass
class EntityData:
    """存档里的一个实体。"""

    entity_id: str
    nbt: str = ""
    path: str = ""
    display_name: str = ""
    tags: list[str] = field(default_factory=list)
    health: Optional[float] = None
    custom_name: str = ""
    items: list[ItemStack] = field(default_factory=list)

    def display(self) -> str:
        if self.custom_name:
            return self.custom_name
        return entity_display(self.entity_id) or self.entity_id

    def summary(self) -> str:
        parts = [self.display()]
        if self.health is not None:
            parts.append(f"生命 {self.health:g}")
        if self.tags:
            parts.append("标签 " + "/".join(self.tags[:3]))
        if self.items:
            parts.append(f"{len(self.items)} 件物品")
        return " · ".join(parts)

    def summon_command(self, pos: str = "~ ~ ~") -> str:
        base = f"summon {normalize_item_id(self.entity_id)} {pos}".strip()
        return f"{base} {self.nbt}" if self.nbt else base


def entity_display(entity_id: str) -> str:
    key = normalize_item_id(entity_id)
    entry = _entity_index().get(key) or {}
    return str(entry.get("display", ""))


_entity_cache: dict[str, dict] | None = None


def _entity_index() -> dict[str, dict]:
    global _entity_cache
    if _entity_cache is None:
        _entity_cache = {}
        try:
            for entry in data_store().entities():
                if isinstance(entry, dict) and entry.get("id"):
                    _entity_cache[str(entry["id"])] = entry
        except Exception:                                  # pragma: no cover
            _entity_cache = {}
    return _entity_cache


def looks_like_entity(node: Optional[SnbtNode]) -> bool:
    """Compound 且 id 能在实体注册表里查到。"""
    if node is None or node.kind != SnbtKind.COMPOUND:
        return False
    entity_id = child_text(node, "id", "Id", "ID")
    return bool(entity_id) and normalize_item_id(entity_id) in _entity_index()


def parse_entity(node: SnbtNode, path: str = "") -> EntityData:
    """把实体节点解析成 EntityData（NBT 里去掉 id 字段）。"""
    if not looks_like_entity(node):
        raise ItemParseError("节点不是实体（id 不在实体注册表里）")
    from app.core.snbt import SnbtNode as Node, serialize
    entity_id = child_text(node, "id", "Id", "ID")
    payload = Node.new_compound()
    for entry in node.children:
        if str(entry.key) in ("id", "Id", "ID"):
            continue
        payload.add_child(entry.clone())
    data = EntityData(entity_id=entity_id, nbt=serialize(payload, pretty=False),
                      path=path)
    health = child(node, "Health")
    if health is not None and not health.is_container:
        try:
            data.health = float(health.value)
        except (TypeError, ValueError):
            data.health = None
    name = child(node, "CustomName")
    if name is not None:
        data.custom_name = _text_from_jsonish(
            name.value if not name.is_container else name)
    tags = child(node, "Tags")
    if tags is not None and tags.kind == SnbtKind.LIST:
        data.tags = [str(c.value) for c in tags.children
                     if not c.is_container]
    data.items = collect_items(payload, source=f"实体 {data.display()}")
    return data


def find_entities(root: SnbtNode) -> list[EntityData]:
    """找出整棵树里的实体（每个 `id` 命中实体注册表的 Compound）。"""
    out: list[EntityData] = []
    for path, node in iter_nodes(root):
        if not looks_like_entity(node):
            continue
        out.append(parse_entity(node, path))
    return out


def entity_to_summon_command(data: EntityData, pos: str = "~ ~ ~") -> str:
    return data.summon_command(pos)


__all__ = [
    "ItemParseError", "ItemStack", "ItemContainer", "EntityData",
    "CONTAINER_KEYS", "child", "child_text", "child_int", "build_path",
    "iter_nodes", "normalize_item_id", "item_display", "lookup_item",
    "item_stack_size", "clear_registry_cache", "enchant_display", "roman",
    "quote_snbt", "component_value_snbt", "looks_like_item",
    "looks_like_item_list", "is_item_container", "container_kind",
    "parse_item", "find_item_containers", "collect_items", "aggregate_items",
    "total_count", "unique_kinds", "stack_to_command", "stack_to_data_command",
    "items_to_give_commands", "stack_to_editor_state", "apply_to_give_panel",
    "entity_display", "looks_like_entity", "parse_entity", "find_entities",
    "entity_to_summon_command",
]
