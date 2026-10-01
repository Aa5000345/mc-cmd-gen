"""命令 ↔ 存档双向桥接。

两个方向
--------
**命令生成器 → 编辑器**（把命令「落到存档里」）
    /give → 玩家背包 / 末影箱 / 指定容器 / 指定槽位
    /summon → 实体数据（生成到地图实体 / 数据包函数）
    /setblock、/fill → 方块与区域编辑
    /data → 指定 NBT 路径
    /effect、/gamemode、/xp → 玩家数据
    /time、/weather、/worldborder、/gamerule → level.dat

**编辑器 → 命令生成器**（把存档里的东西「变成命令」）
    物品 → /give（保留数量、槽位、组件、附魔）
    实体 NBT → /summon
    容器 / 方块实体 → /setblock、/data
    NBT 路径 → /data modify … set value …
    玩家 → 目标选择器、/execute as/at
    坐标 → positioned、summon 坐标
    世界规则 → /time、/weather、/gamerule

设计约定
--------
* 传递的是**结构化对象**（`BridgeOperation`），不是纯文本；
* 每个操作都能 `preview()` 出差异（dry-run），`apply()` 才写盘，
  且写盘走 `WorldTransaction`（备份 + 原子写入 + 撤销）；
* 所有操作都记进桥接日志（`WorldSession.log_records`）。

不依赖 Qt，方便单元测试。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.core.nbt_bridge import (
    ItemStack, item_stack_size, normalize_item_id,
    stack_to_command, stack_to_data_command,
)
from app.core.snbt import SnbtKind, SnbtNode
from app.core.world import WorldSave, parse_field_path

# ---------------------------------------------------------------------------
# 结构化操作
# ---------------------------------------------------------------------------

#: 操作类型 → (中文名, 危险级别, 说明)
OPERATION_KINDS: dict[str, tuple[str, str, str]] = {
    "give": ("给予物品", "normal", "把物品写进玩家背包 / 容器"),
    "summon": ("生成实体", "normal", "把实体写进存档或导出数据包函数"),
    "setblock": ("放置方块", "normal", "修改指定坐标的方块实体"),
    "fill": ("区域填充", "danger", "批量修改一片区域的方块（实验）"),
    "data": ("写入 NBT", "danger", "按路径直接写 NBT（危险，会二次确认）"),
    "effect": ("添加状态效果", "normal", "给玩家加上 buff / debuff"),
    "gamemode": ("切换游戏模式", "normal", "修改玩家的游戏模式"),
    "xp": ("修改经验", "normal", "修改玩家等级 / 经验"),
    "time": ("设置时间", "normal", "修改世界时间"),
    "weather": ("设置天气", "normal", "修改世界天气"),
    "worldborder": ("世界边界", "danger", "修改世界边界（影响整张地图）"),
    "gamerule": ("游戏规则", "normal", "修改 level.dat 里的游戏规则"),
    "player_field": ("修改玩家字段", "normal", "直接写玩家 NBT 字段"),
    "delete_item": ("删除物品", "danger", "从容器里拿走物品"),
    "scene": ("世界场景", "danger", "一次性套用整套世界设置"),
}


@dataclass
class BridgeOperation:
    """一条待执行的桥接操作（结构化，可预览、可撤销、可记录）。"""

    kind: str
    #: 人类可读说明
    label: str = ""
    #: 操作来源：command / editor / nlp / scene / bridge
    source: str = "command"
    #: 目标：level / <uuid> / map:<n>
    target: str = "level"
    #: 结构化参数
    payload: dict[str, Any] = field(default_factory=dict)
    #: 该操作要执行的函数（接收 session，返回说明文本）
    runner: Callable[[Any], str] | None = None
    #: 生成的反向命令（编辑器 → 命令方向用）
    commands: list[str] = field(default_factory=list)
    #: 危险等级（normal / danger）
    risk: str = "normal"
    #: 附加提示
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        meta = OPERATION_KINDS.get(self.kind)
        if meta is not None:
            if not self.label:
                self.label = meta[0]
            if self.risk == "normal" and meta[1] == "danger":
                self.risk = meta[1]

    @property
    def title(self) -> str:
        return self.label or OPERATION_KINDS.get(self.kind, (self.kind,))[0]

    def describe(self) -> str:
        bits = [self.title]
        if self.payload:
            for key in ("item", "count", "entity", "pos", "path", "value",
                        "slot", "mode", "target", "selector", "player",
                        "dimension"):
                if key in self.payload and self.payload[key] not in (None, ""):
                    bits.append(f"{key}={self.payload[key]}")
        return " · ".join(str(bit) for bit in bits)

    def command_text(self) -> str:
        return "\n".join(self.commands)

    def summary(self) -> str:
        text = self.describe()
        if self.commands:
            text += f" → {self.commands[0]}"
            if len(self.commands) > 1:
                text += f"（共 {len(self.commands)} 条命令）"
        return text

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "label": self.title, "source": self.source,
                "target": self.target, "payload": self.payload,
                "commands": list(self.commands), "risk": self.risk,
                "notes": list(self.notes)}


# ---------------------------------------------------------------------------
# 命令 → 操作
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"\s+")


def split_command(command: str) -> list[str]:
    """把命令切成 token，保留 `{…}` / `[…]` / `"…"` 内部的空格。"""
    text = str(command or "").strip().lstrip("/")
    if not text:
        return []
    out: list[str] = []
    buffer: list[str] = []
    depth = 0
    quote = ""
    for char in text:
        if quote:
            buffer.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
            buffer.append(char)
            continue
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth = max(0, depth - 1)
        if char.isspace() and depth == 0:
            if buffer:
                out.append("".join(buffer))
                buffer = []
            continue
        buffer.append(char)
    if buffer:
        out.append("".join(buffer))
    return out


def command_to_operation(command: str, *,
                         session: Any = None) -> BridgeOperation | None:
    """把一条命令解析成桥接操作（不认识就返回 None）。"""
    tokens = split_command(command)
    if not tokens:
        return None
    name = tokens[0].lower()
    builder = _COMMAND_BUILDERS.get(name)
    if builder is None:
        return None
    try:
        return builder(tokens, session)
    except Exception as e:                              # pragma: no cover
        return BridgeOperation(kind="data", label=f"/{name} 解析失败",
                               source="command",
                               notes=[f"解析失败：{e}"])


def _parse_target_slot(text: str) -> int | None:
    match = re.search(r"\[.*?Slot\s*=\s*(-?\d+)", text or "")
    if match:
        return int(match.group(1))
    return None


def _give_operation(tokens: list[str], session: Any) -> BridgeOperation:
    """`/give @a minecraft:stone[components] 64` → 写进容器。"""
    target = tokens[1] if len(tokens) > 1 else "@p"
    item_token = tokens[2] if len(tokens) > 2 else "minecraft:stone"
    count = 1
    slot: int | None = None
    if len(tokens) > 3:
        raw = tokens[3]
        slot = _parse_target_slot(raw)
        if slot is None:
            raw = raw.split("[", 1)[0]
            try:
                count = max(1, int(float(raw)))
            except ValueError:
                count = 1
    item_id, components = split_item_argument(item_token)
    payload = {"item": item_id, "count": count, "components": components,
               "mode": "first_empty", "slot": slot, "target": target}
    # 目标选择器 → 具体玩家 + 默认容器
    payload["container"] = _default_container_for(target)
    payload["player"] = _target_to_uuid(target, session)
    return BridgeOperation(
        kind="give", source="command", target=payload["player"] or "level",
        payload=payload)


def split_item_argument(text: str) -> tuple[str, str]:
    """`minecraft:stone[damage=5,unbreakable={}]` → (id, 组件文本)。"""
    raw = str(text or "").strip()
    index = raw.find("[")
    if index < 0:
        return normalize_item_id(raw), ""
    item_id = raw[:index]
    body = raw[index:]
    return normalize_item_id(item_id), body


def _summon_operation(tokens: list[str], session: Any) -> BridgeOperation:
    entity = normalize_item_id(tokens[1] if len(tokens) > 1 else "minecraft:zombie")
    pos = "~ ~ ~"
    nbt = ""
    rest = tokens[2:]
    if len(rest) >= 3 and not rest[0].startswith("{"):
        pos = " ".join(rest[:3])
        rest = rest[3:]
    if rest:
        nbt = " ".join(rest)
    payload = {"entity": entity, "pos": pos, "nbt": nbt,
               "dimension": _dimension_of(session)}
    return BridgeOperation(kind="summon", source="command",
                           target="level", payload=payload)


def _setblock_operation(tokens: list[str], session: Any) -> BridgeOperation:
    pos = " ".join(tokens[1:4]) if len(tokens) >= 4 else "~ ~ ~"
    block = normalize_item_id(tokens[4]) if len(tokens) > 4 else "minecraft:stone"
    nbt = tokens[5] if len(tokens) > 5 and tokens[5].startswith("{") else ""
    return BridgeOperation(kind="setblock", source="command", target="level",
                           payload={"pos": pos, "block": block, "nbt": nbt,
                                    "dimension": _dimension_of(session)})


def _fill_operation(tokens: list[str], session: Any) -> BridgeOperation:
    start = " ".join(tokens[1:4]) if len(tokens) >= 4 else "~ ~ ~"
    end = " ".join(tokens[4:7]) if len(tokens) >= 7 else "~ ~ ~"
    block = normalize_item_id(tokens[7]) if len(tokens) > 7 else "minecraft:stone"
    mode = tokens[9] if len(tokens) > 9 else "replace"
    return BridgeOperation(
        kind="fill", source="command", target="level",
        payload={"start": start, "end": end, "block": block, "mode": mode,
                 "dimension": _dimension_of(session)},
        notes=["区域写入属于实验功能：优先导出 .mcfunction 让游戏自己执行"])


def _data_operation(tokens: list[str], session: Any) -> BridgeOperation:
    """`data <get|merge|modify|remove> <block|entity|storage> <目标> <路径> …`。

    注意 `data modify entity @s Health set value 20.0f` 里真正决定
    「写进哪里」的是 **entity** 这个来源与 **@s** 这个目标，
    `modify` 只是动作词。
    """
    rest = list(tokens[1:])
    action = ""
    if rest and rest[0].lower() in ("get", "merge", "modify", "remove"):
        action = rest.pop(0).lower()
    source = ""
    if rest and rest[0].lower() in ("block", "entity", "storage"):
        source = rest.pop(0).lower()
        # storage 的命令里没有独立目标，直接跟路径
        ref_target = "" if source == "storage" else (
            rest.pop(0) if rest else "")
    else:                                               # pragma: no cover
        ref_target = rest.pop(0) if rest else ""
    path = rest.pop(0) if rest else ""
    value = ""
    mode = "set"
    for marker in ("set", "merge", "append", "prepend", "insert",
                   "from", "string", "value"):
        if marker in rest:
            index = rest.index(marker)
            mode = marker
            tail = rest[index + 1:]
            if tail and tail[0] == "value":
                tail = tail[1:]
            value = " ".join(tail)
            break
    return BridgeOperation(kind="data", source="command",
                           target=_target_to_uuid(ref_target, session)
                           or "level",
                           payload={"action": action, "ref": source,
                                    "ref_target": ref_target,
                                    "path": path, "value": value,
                                    "mode": mode})


def _effect_operation(tokens: list[str], session: Any) -> BridgeOperation:
    # effect give <target> <effect> [seconds] [amplifier] [hideParticles]
    target = tokens[2] if len(tokens) > 2 else "@p"
    effect = normalize_item_id(tokens[3]) if len(tokens) > 3 else ""
    duration = 30
    amplifier = 0
    if len(tokens) > 4:
        try:
            duration = int(float(tokens[4]))
        except ValueError:
            duration = 30
    if len(tokens) > 5:
        try:
            amplifier = int(float(tokens[5]))
        except ValueError:
            amplifier = 0
    return BridgeOperation(
        kind="effect", source="command",
        target=_target_to_uuid(target, session) or "level",
        payload={"id": effect, "duration": duration, "amplifier": amplifier,
                 "player": target})


def _gamemode_operation(tokens: list[str], session: Any) -> BridgeOperation:
    mode = (tokens[1] if len(tokens) > 1 else "creative").lower()
    target = tokens[2] if len(tokens) > 2 else "@s"
    return BridgeOperation(
        kind="gamemode", source="command",
        target=_target_to_uuid(target, session) or "level",
        payload={"mode": mode, "player": target})


def _xp_operation(tokens: list[str], session: Any) -> BridgeOperation:
    # xp add <target> <amount> [points|levels] / xp set <target> <amount> …
    action = "add"
    target = "@s"
    amount = 0
    rest = tokens[1:]
    if rest and rest[0].lower() in ("add", "set", "query"):
        action = rest.pop(0).lower()
    if rest:
        target = rest.pop(0)
    if rest:
        try:
            amount = int(float(rest[0]))
        except ValueError:
            amount = 0
    return BridgeOperation(
        kind="xp", source="command",
        target=_target_to_uuid(target, session) or "level",
        payload={"amount": amount, "player": target, "action": action})


def _time_operation(tokens: list[str], session: Any) -> BridgeOperation:
    action = (tokens[1] if len(tokens) > 1 else "set").lower()
    value = 0
    if len(tokens) > 2:
        try:
            value = int(float(tokens[2]))
        except ValueError:
            value = {"day": 1000, "noon": 6000, "night": 13000,
                     "midnight": 18000}.get(tokens[2].lower(), 0)
    return BridgeOperation(kind="time", source="command", target="level",
                           payload={"action": action, "value": value})


def _weather_operation(tokens: list[str], session: Any) -> BridgeOperation:
    kind = (tokens[1] if len(tokens) > 1 else "clear").lower()
    duration = 6000
    if len(tokens) > 2:
        try:
            duration = int(float(tokens[2]))
        except ValueError:
            duration = 6000
    return BridgeOperation(kind="weather", source="command", target="level",
                           payload={"weather": kind, "duration": duration})


def _worldborder_operation(tokens: list[str], session: Any) -> BridgeOperation:
    action = (tokens[1] if len(tokens) > 1 else "get").lower()
    payload: dict[str, Any] = {"action": action}
    if action == "center" and len(tokens) > 3:
        payload["center_x"] = _to_float(tokens[2])
        payload["center_z"] = _to_float(tokens[3])
    elif action in ("set", "add") and len(tokens) > 2:
        payload["size"] = _to_float(tokens[2])
    elif action == "damage" and len(tokens) > 2:
        payload["damage"] = _to_float(tokens[2])
    return BridgeOperation(kind="worldborder", source="command",
                           target="level", payload=payload)


def _gamerule_operation(tokens: list[str], session: Any) -> BridgeOperation:
    rule = tokens[1] if len(tokens) > 1 else ""
    value = " ".join(tokens[2:]) if len(tokens) > 2 else "true"
    return BridgeOperation(kind="gamerule", source="command", target="level",
                           payload={"rule": rule, "value": value})


_COMMAND_BUILDERS: dict[str, Callable[[list[str], Any], BridgeOperation]] = {
    "give": _give_operation,
    "summon": _summon_operation,
    "setblock": _setblock_operation,
    "fill": _fill_operation,
    "data": _data_operation,
    "effect": _effect_operation,
    "gamemode": _gamemode_operation,
    "xp": _xp_operation,
    "experience": _xp_operation,
    "time": _time_operation,
    "weather": _weather_operation,
    "worldborder": _worldborder_operation,
    "gamerule": _gamerule_operation,
}


def _to_float(text: str, default: float = 0.0) -> float:
    try:
        return float(str(text).replace("~", "0") or 0)
    except ValueError:
        return default


def _dimension_of(session: Any) -> str:
    if session is None or getattr(session, "world", None) is None:
        return "minecraft:overworld"
    players = list(session.world.players.values())
    if players and players[0].dimension:
        return players[0].dimension
    return "minecraft:overworld"


def _default_container_for(target: str) -> str:
    return "EnderItems" if "ender" in (target or "").lower() else "Inventory"


def _target_to_uuid(target: str, session: Any) -> str:
    """把目标选择器映射到具体玩家（能对上就返回 UUID）。"""
    if session is None or getattr(session, "world", None) is None:
        return ""
    players = list(session.world.players.values())
    if not players:
        return ""
    text = str(target or "").strip().lower()
    if text in ("@s", "@p", "@r"):
        return players[0].uuid
    if text == "@a":
        return ""                                   # 多人：交给调用方展开
    name = text.lstrip("@")
    for player in players:
        if player.name and player.name.lower() == name:
            return player.uuid
    return ""


# ---------------------------------------------------------------------------
# 操作 → 执行
# ---------------------------------------------------------------------------

def apply_give(session: Any, payload: dict[str, Any]) -> str:
    """把 /give 的解析结果写进容器。"""
    world: WorldSave = session.world
    player_uuid = str(payload.get("player") or "")
    container = str(payload.get("container") or "Inventory")
    explicit_path = str(payload.get("path") or "")
    written_total = 0

    if explicit_path:
        targets = [(player_uuid or "level", explicit_path)]
    elif player_uuid:
        targets = [(player_uuid, container)]
    else:
        # @a：所有玩家
        targets = [(p.uuid, container) for p in world.players.values()]
    if not targets:
        raise KeyError("找不到可写入的玩家容器")

    for target, path in targets:
        written = world.grow_items(
            target, path, str(payload["item"]), int(payload.get("count", 1)),
            mode=str(payload.get("mode", "first_empty")),
            slot=payload.get("slot"),
            components=str(payload.get("components", "")))
        written_total += written
    mode = payload.get("mode", "first_empty")
    return (f"已写入 {written_total} 个 {payload['item']} "
            f"（{len(targets)} 个容器，模式 {mode}）")


def apply_summon(session: Any, payload: dict[str, Any]) -> str:
    """把实体写进存档的实体集合，或退化为数据包函数。"""
    world: WorldSave = session.world
    entity = normalize_item_id(str(payload.get("entity", "")))
    nbt = str(payload.get("nbt", "") or "").strip()
    pos = str(payload.get("pos", "~ ~ ~"))
    entities = world.data.get_child("Entities") if world.data else None
    if entities is None:
        raise KeyError("该存档的 level.dat 里没有 Entities 列表，"
                       "请改用「导出数据包函数」")
    from app.core.snbt import SnbtParser
    body = nbt if nbt.startswith("{") else (f"{{{nbt}}}" if nbt else "{}")
    node = SnbtParser(body).parse()
    if node.kind != SnbtKind.COMPOUND:                  # pragma: no cover
        raise ValueError("实体 NBT 必须是复合标签")
    id_child = node.get_child("id")
    if id_child is None:
        node.add_child(SnbtNode.new_scalar(SnbtKind.STRING, entity, key="id"))
    coords = _absolute_pos(pos, session)
    if coords is not None:
        pos_node = node.get_child("Pos")
        fresh = SnbtNode.new_list(key="Pos")
        for value in coords:
            fresh.add_child(SnbtNode.new_scalar(SnbtKind.DOUBLE, float(value)))
        if pos_node is None:
            node.add_child(fresh)
        else:
            node.children[node.children.index(pos_node)] = fresh
    entities.add_child(node)
    world.mark_level_dirty()
    return f"已把 {entity} 追加到 level.dat 的 Entities 列表"


def apply_setblock(session: Any, payload: dict[str, Any]) -> str:
    """方块实体写入：找到同坐标的方块实体就替换，否则新建。"""
    world: WorldSave = session.world
    pos = str(payload.get("pos", "~ ~ ~"))
    coords = _absolute_pos(pos, session)
    if coords is None:
        raise ValueError(f"坐标必须是绝对数值才能写进存档：{pos}")
    block = normalize_item_id(str(payload.get("block", "minecraft:stone")))
    block_entities = world.data.get_child("block_entities") \
        if world.data else None
    if block_entities is None:
        block_entities = SnbtNode.new_list(key="block_entities")
        world.data.add_child(block_entities)
    for child in list(block_entities.children):
        if _block_matches(child, coords):
            block_entities.remove_child(child)
    node = SnbtNode.new_compound()
    node.add_child(SnbtNode.new_scalar(SnbtKind.STRING, block, key="id"))
    for key, value in zip(("x", "y", "z"), coords, strict=False):
        node.add_child(SnbtNode.new_scalar(SnbtKind.INT, int(value), key=key))
    block_entities.add_child(node)
    world.mark_level_dirty()
    return f"已在 {coords[0]} {coords[1]} {coords[2]} 登记方块实体 {block}"


def apply_fill(session: Any, payload: dict[str, Any]) -> str:
    """区域填充：默认不改存档，而是导出函数让游戏执行。"""
    commands = fill_to_commands(payload)
    return ("区域填充属于实验功能：已生成 "
            f"{len(commands)} 条 /fill 命令，建议导出数据包执行\n"
            + "\n".join(commands))


def apply_data(session: Any, payload: dict[str, Any]) -> str:
    """按路径直接写 NBT。"""
    world: WorldSave = session.world
    path = str(payload.get("path", "")).strip()
    value = payload.get("value", "")
    ref = str(payload.get("ref", ""))
    target = _document_target(ref, payload, session)
    node = _snbt_value(value)
    _write_path(world, target, path, node)
    return f"已写入 {target}:{path}"


def apply_effect(session: Any, payload: dict[str, Any]) -> str:
    world: WorldSave = session.world
    uuid = str(payload.get("uuid") or "")
    if not uuid:
        raise KeyError("请先选择要施加效果的玩家")
    root = world.players[uuid].root
    effects = root.get_child("active_effects")
    if effects is None:
        effects = SnbtNode.new_list(key="active_effects")
        root.add_child(effects)
    from app.core.snbt import SnbtParser
    effect = normalize_item_id(str(payload.get("id", "")))
    node = SnbtParser("{}").parse()
    node.add_child(SnbtNode.new_scalar(SnbtKind.STRING, effect, key="id"))
    node.add_child(SnbtNode.new_scalar(
        SnbtKind.BYTE, int(payload.get("amplifier", 0)), key="amplifier"))
    node.add_child(SnbtNode.new_scalar(
        SnbtKind.INT, int(payload.get("duration", 30)) * 20, key="duration"))
    node.add_child(SnbtNode.new_scalar(SnbtKind.BYTE, 0, key="ambient"))
    node.add_child(SnbtNode.new_scalar(SnbtKind.BYTE, 1, key="show_particles"))
    effects.add_child(node)
    world.players[uuid].dirty = True
    return f"已给玩家加上效果 {effect}"


def apply_gamemode(session: Any, payload: dict[str, Any]) -> str:
    world: WorldSave = session.world
    mode = str(payload.get("mode", "creative"))
    uuid = str(payload.get("uuid") or "")
    if uuid:
        world.set_player_gamemode(uuid, mode)
        return f"已把玩家游戏模式设为 {mode}"
    for player in world.players.values():
        world.set_player_gamemode(player.uuid, mode)
    return f"已把 {len(world.players)} 名玩家的游戏模式设为 {mode}"


def apply_xp(session: Any, payload: dict[str, Any]) -> str:
    world: WorldSave = session.world
    amount = int(payload.get("amount", 0))
    uuid = str(payload.get("uuid") or "")
    if not uuid:
        raise KeyError("请先选择玩家")
    player = world.players[uuid]
    world.set_player_xp(uuid, level=max(0, player.xp_level + amount),
                        total=max(0, player.xp_total + amount))
    return f"已修改经验（{amount:+d} 级）"


def apply_time(session: Any, payload: dict[str, Any]) -> str:
    world: WorldSave = session.world
    action = str(payload.get("action", "set"))
    value = int(payload.get("value", 0))
    if action == "add":
        world.set_time(day_time=world.overview.day_time + value,
                       time=world.overview.time + value)
        return f"时间已 +{value}"
    world.set_time(day_time=value, time=max(world.overview.time, value))
    return f"时间已设为 {value}"


def apply_weather(session: Any, payload: dict[str, Any]) -> str:
    world: WorldSave = session.world
    weather = str(payload.get("weather", "clear"))
    world.set_weather(weather, int(payload.get("duration", 6000)))
    return f"天气已设为 {weather}"


def apply_worldborder(session: Any, payload: dict[str, Any]) -> str:
    world: WorldSave = session.world
    action = str(payload.get("action", "get"))
    if action in ("get", "query"):
        return "worldborder get 不需要写入"
    if action in ("set", "add"):
        size = float(payload.get("size", 0) or 0)
        if action == "add":
            size = float(world.overview.world_border.get("size", 0)) + size
        world.set_world_border(size=size)
        return f"世界边界边长设为 {size:.0f}"
    if action == "center":
        world.set_world_border(center_x=payload.get("center_x", 0),
                               center_z=payload.get("center_z", 0))
        return "世界边界中心已更新"
    if action == "damage":
        world.set_world_border(damage=payload.get("damage", 0))
        return "世界边界伤害已更新"
    if action == "warning":
        world.set_world_border(warning_blocks=payload.get("warning_blocks", 5))
        return "世界边界警告距离已更新"
    return f"未处理的 worldborder 动作：{action}"


def apply_gamerule(session: Any, payload: dict[str, Any]) -> str:
    world: WorldSave = session.world
    rule = str(payload.get("rule", "")).strip()
    if not rule:
        raise KeyError("缺少游戏规则名")
    world.set_gamerule(rule, payload.get("value", "true"))
    return f"游戏规则 {rule} = {payload.get('value')}"


def apply_scene(session: Any, payload: dict[str, Any]) -> str:
    """一键世界场景：永久白天 / 禁止下雨 / 和平 / 出生点 / 边界。"""
    world: WorldSave = session.world
    done: list[str] = []
    if payload.get("permanent_day", True):
        world.set_level_field("DayTime", 6000, SnbtKind.LONG)
        world.set_gamerule("doDaylightCycle", "false")
        done.append("锁定白天")
    if payload.get("no_weather", True):
        world.set_weather("clear", 1000000)
        world.set_gamerule("doWeatherCycle", "false")
        done.append("禁止天气变化")
    if payload.get("peaceful", True):
        world.set_difficulty("peaceful")
        done.append("难度=和平")
    if payload.get("spawn"):
        try:
            x, y, z = (int(float(part))
                       for part in str(payload["spawn"]).split()[:3])
            world.set_spawn(x, y, z)
            done.append(f"出生点 {x} {y} {z}")
        except (TypeError, ValueError):
            pass
    if payload.get("border_size"):
        world.set_world_border(size=float(payload["border_size"]))
        done.append(f"边界 {payload['border_size']}")
    if payload.get("clear_weather_timer", True):
        world.set_level_field("clearWeatherTime", 1000000, SnbtKind.INT)
        done.append("清空天气计时")
    return "、".join(done) or "没有需要应用的场景项"


#: 操作类型 → 执行函数
OPERATION_RUNNERS: dict[str, Callable[[Any, dict], str]] = {
    "give": apply_give,
    "summon": apply_summon,
    "setblock": apply_setblock,
    "fill": apply_fill,
    "data": apply_data,
    "effect": apply_effect,
    "gamemode": apply_gamemode,
    "xp": apply_xp,
    "time": apply_time,
    "weather": apply_weather,
    "worldborder": apply_worldborder,
    "gamerule": apply_gamerule,
    "scene": apply_scene,
}


def operation_runner(operation: BridgeOperation) -> Callable[[Any], str]:
    """给操作绑定执行函数。"""
    runner = OPERATION_RUNNERS.get(operation.kind)
    if runner is None:
        def missing(_session: Any) -> str:
            raise NotImplementedError(
                f"暂不支持把 {operation.kind} 写进存档")
        return missing

    def bound(session: Any) -> str:
        payload = dict(operation.payload)
        # 目标玩家：把选择器解析成 UUID（多人时按需展开）
        if operation.kind in ("effect", "xp") and not payload.get("uuid"):
            payload["uuid"] = _target_to_uuid(
                str(payload.get("player", "@p")), session)
        if operation.kind == "gamemode" and not payload.get("uuid"):
            payload["uuid"] = _target_to_uuid(
                str(payload.get("player", "@s")), session)
        return runner(session, payload)
    return bound


# ---------------------------------------------------------------------------
# 执行入口（预览 / 应用）
# ---------------------------------------------------------------------------

@dataclass
class BridgeResult:
    ok: bool = False
    operation: BridgeOperation | None = None
    diff: Any = None
    message: str = ""
    applied: bool = False
    backup: Path | None = None
    written: list[Path] = field(default_factory=list)

    def summary(self) -> str:
        return self.message or ("已应用" if self.applied else "未应用")

    @property
    def backup_name(self) -> str:
        return self.backup.name if self.backup else ""


def preview_operation(session: Any, operation: BridgeOperation) -> BridgeResult:
    """只改内存算差异，然后回滚（安全审阅模式）。"""
    from app.core.world_session import WorldDiff
    if session is None or getattr(session, "world", None) is None:
        return BridgeResult(ok=False, operation=operation,
                            message="没有打开世界")
    if session.read_only:
        return BridgeResult(ok=False, operation=operation,
                            message="存档只读：请先解锁编辑")
    runner = operation.runner or operation_runner(operation)
    snapshot = session.capture()
    try:
        runner(session)
    except Exception as e:
        session.restore(snapshot)
        return BridgeResult(ok=False, operation=operation,
                            message=f"无法应用：{e}")
    diff = session.diff_against(snapshot, operation.title)
    session.restore(snapshot)
    if not diff:
        diff = WorldDiff(label=operation.title)
    return BridgeResult(ok=True, operation=operation, diff=diff,
                        message=diff.text(limit=40))


def apply_operation(session: Any, operation: BridgeOperation,
                    *, backup: bool = True) -> BridgeResult:
    """真正写盘（走事务：备份 + 原子写入 + 撤销 + 日志）。"""
    if session is None or getattr(session, "world", None) is None:
        return BridgeResult(ok=False, operation=operation,
                            message="没有打开世界")
    if session.read_only:
        return BridgeResult(ok=False, operation=operation,
                            message="存档只读：请先解锁编辑")
    runner = operation.runner or operation_runner(operation)
    tx = session.transaction(operation.title, source=operation.source,
                             target=operation.target)
    detail = ""
    with tx:
        try:
            detail = runner(session)
        except Exception as e:
            return BridgeResult(ok=False, operation=operation,
                                message=f"执行失败：{e}")
        result = tx.commit(backup=backup)
    if not result.ok:
        return BridgeResult(ok=False, operation=operation, diff=result.diff,
                            message=result.message)
    message = detail or result.message
    if result.applied and result.backup is not None:
        message += f"（已备份到 backups/{result.backup.name}）"
    return BridgeResult(ok=True, operation=operation, diff=result.diff,
                        message=message, applied=result.applied,
                        backup=result.backup, written=result.written)


# ---------------------------------------------------------------------------
# 编辑器 → 命令
# ---------------------------------------------------------------------------

def item_to_give_operation(stack: ItemStack, target: str = "@p",
                           container: str = "Inventory") -> BridgeOperation:
    """存档物品 → /give 面板命令（保留数量、槽位、组件、附魔）。"""
    command = stack_to_command(stack, target)
    return BridgeOperation(
        kind="give", source="editor", target=container,
        payload={"item": stack.clean_id(), "count": stack.count,
                 "slot": stack.slot, "components": stack.components_snbt(),
                 "target": target, "player": target},
        commands=[command],
        notes=[f"来源：{stack.path or stack.source or '存档'}"],
        label=f"物品 → /give（{stack.display()} ×{stack.count}）")


def item_to_data_operation(stack: ItemStack) -> BridgeOperation:
    return BridgeOperation(
        kind="data", source="editor", target="level",
        payload={"path": stack.path, "value": ""},
        commands=[stack_to_data_command(stack)],
        label=f"物品 → /data（{stack.display()}）")


def entity_to_summon_operation(entity: Any, pos: str = "~ ~ ~") -> BridgeOperation:
    command = entity.summon_command(pos) if hasattr(entity, "summon_command") \
        else f"summon {entity.entity_id} {pos} {entity.nbt}".strip()
    return BridgeOperation(
        kind="summon", source="editor", target="level",
        payload={"entity": getattr(entity, "entity_id", ""),
                 "pos": pos, "nbt": getattr(entity, "nbt", "")},
        commands=[command],
        label=f"实体 → /summon（{getattr(entity, 'display', lambda: '')()}）")


def container_to_commands(container: Any, target: str = "@p",
                          world: Any = None,
                          document: str = "") -> BridgeOperation:
    """容器内容 → 一组 `/give` 命令。

    `container` 既可以是 `nbt_bridge.ItemContainer`（直接带 `stacks()`），
    也可以是世界编辑器里的 `world.ContainerInfo` 摘要 —— 后者需要
    `world` + `document`（level / 玩家 UUID）才能取到真实节点。
    """
    stacks: list[ItemStack] = []
    if hasattr(container, "stacks"):
        stacks = list(container.stacks())
    elif world is not None:
        path = str(getattr(container, "path", ""))
        source = document or "level"
        try:
            root, _mark = world._document_for(source)
            from app.core.world import resolve_path
            node = resolve_path(root, path)
        except Exception:                               # pragma: no cover
            node = None
        if node is not None:
            stacks = _stacks_from_node(node, path)
    commands = [stack_to_command(stack, target) for stack in stacks]
    return BridgeOperation(
        kind="give", source="editor", target="level",
        payload={"path": getattr(container, "path", ""),
                 "document": document or "level",
                 "count": len(stacks)},
        commands=commands,
        label=f"容器 → 命令（{getattr(container, 'path', '')}，"
              f"{len(stacks)} 件）")


def _stacks_from_node(node: SnbtNode, path: str = "") -> list[ItemStack]:
    """把一个物品容器节点转成 ItemStack 列表。

    先按 `is_item_container` 判断整体是不是容器；不成立时再退一步用
    `find_item_containers` 在子树里按路径找（兼容包了一层的写法）。
    """
    from app.core.nbt_bridge import (
        find_item_containers, is_item_container, parse_item,
    )
    out: list[ItemStack] = []
    children = node.children
    if is_item_container(node):
        for position, child in enumerate(children):
            try:
                out.append(parse_item(child, f"{path}[{position}]", "容器"))
            except Exception:                           # pragma: no cover
                continue
        return out
    for container in find_item_containers(node):
        if path and container.path not in (path, path.split(".", 1)[-1]):
            continue
        out.extend(container.stacks())
    return out


_NBT_COMMAND_PREFIX = {
    "level": "data modify storage mcstudio:level",
}


def nbt_path_to_command(path: str, value: str = "", *,
                        ref: str = "storage mcstudio:world",
                        mode: str = "set") -> str:
    """NBT 路径 → `/data modify … set value …`。"""
    clean = str(path or "").strip() or "Data"
    # `…Inventory[0].id` → `…Inventory[{Slot:0b}].id` 之类不做推断，
    # 直接按路径翻译成命令里的写法即可
    return f"data modify {ref} {clean} {mode} value {value}".strip()


def player_to_selector(player: Any) -> str:
    """玩家 → 目标选择器（有名字用名字，否则用 UUID 的 @e 写法）。"""
    name = getattr(player, "name", "") or ""
    if name:
        return f"@a[name={name}]"
    uuid = getattr(player, "uuid", "")
    return f"@e[type=player,uuid={uuid}]" if uuid else "@p"


def player_to_execute_commands(player: Any, command: str = "say hi") -> BridgeOperation:
    selector = player_to_selector(player)
    commands = [f"execute as {selector} at @s run {command}"]
    return BridgeOperation(
        kind="data", source="editor", target="level",
        payload={"selector": selector, "player": getattr(player, "name", "")},
        commands=commands,
        label=f"玩家 → /execute（{selector}）")


def coords_to_commands(x: float, y: float, z: float,
                       command: str = "setblock ~ ~ ~ minecraft:stone") -> BridgeOperation:
    located = f"execute positioned {x:.2f} {y:.2f} {z:.2f} run {command}"
    return BridgeOperation(
        kind="setblock", source="editor", target="level",
        payload={"pos": f"{x} {y} {z}", "block": "minecraft:stone"},
        commands=[located, f"summon minecraft:armor_stand {x} {y} {z}"],
        label=f"坐标 → 命令（{x:.2f} {y:.2f} {z:.2f}）")


def world_rules_to_commands(world: Any) -> BridgeOperation:
    """世界规则 → /time /weather /gamerule 一组命令。"""
    overview = world.overview
    commands = [
        f"time set {overview.day_time}",
        f"weather {'clear' if overview.weather == 'clear' else overview.weather}",
        f"difficulty {overview.difficulty or 'normal'}",
    ]
    for rule, value in sorted(overview.gamerules.items()):
        commands.append(f"gamerule {rule} {value}")
    border = overview.world_border or {}
    if border:
        commands.append(f"worldborder center {border.get('center_x', 0):.1f} "
                        f"{border.get('center_z', 0):.1f}")
        commands.append(f"worldborder set {border.get('size', 0):.1f}")
    return BridgeOperation(
        kind="gamerule", source="editor", target="level",
        payload={"rules": len(overview.gamerules)},
        commands=commands,
        label=f"世界规则 → 命令（{len(commands)} 条）")


def ops_to_commands(operations: list[BridgeOperation]) -> list[str]:
    out: list[str] = []
    for operation in operations:
        out.extend(operation.commands)
    return out


# ---------------------------------------------------------------------------
# 中文自然语言 → 世界操作
# ---------------------------------------------------------------------------

_NLP_RULES: tuple[tuple[str, str], ...] = (
    (r"(?:把|给)?\s*(?P<player>[\u4e00-\u9fa5A-Za-z0-9_]+)\s*(?:的)?"
     r"\s*(?:背包|物品栏)\s*(?:塞满|装满|放满|填满)\s*(?P<count>\d+)?\s*"
     r"(?P<item>[\u4e00-\u9fa5A-Za-z0-9_:]+)", "fill_inventory"),
    (r"(?:在)?\s*(?P<pos>出生点|[\d\-~ ]+)\s*(?:生成|刷|召唤)\s*"
     r"(?P<count>\d+)?\s*(?:只|个|头)?\s*(?P<entity>[\u4e00-\u9fa5A-Za-z0-9_:]+)",
     "spawn_entities"),
    (r"(?:把)?\s*时间\s*(?:设置?为?|改成|调到)\s*(?P<value>[\u4e00-\u9fa5\d:]+)",
     "set_time"),
    (r"(?:把)?\s*天气\s*(?:设置?为?|改成|调到)\s*(?P<weather>[\u4e00-\u9fa5]+)",
     "set_weather"),
    (r"(?:把)?\s*难度\s*(?:设置?为?|改成|调到)\s*(?P<difficulty>[\u4e00-\u9fa5]+)",
     "set_difficulty"),
    (r"(?:把)?\s*(?P<rule>do[A-Za-z]+|[a-zA-Z]+)\s*(?:游戏规则)?\s*"
     r"(?:设置?为?|改成|关闭|打开|开启)\s*(?P<value>true|false|[\d]+)",
     "set_gamerule"),
    (r"(?:永久|一直)\s*(?:白天|晴天)", "permanent_day"),
    (r"(?:禁止|不要)\s*(?:下雨|天气)", "no_weather"),
    (r"(?:把)?\s*(?:世界)?\s*边界\s*(?:设置?为?|改成)\s*(?P<size>\d+)",
     "set_border"),
)

#: 中文数字
_CN_NUMBERS = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100}

_CN_WEATHER = {"晴天": "clear", "晴": "clear", "下雨": "rain", "雨": "rain",
               "雷暴": "thunder", "雷": "thunder"}

_CN_TIME = {"白天": 1000, "中午": 6000, "正午": 6000, "黄昏": 12000,
            "日落": 12000, "夜晚": 13000, "晚上": 13000, "半夜": 18000,
            "午夜": 18000, "日出": 23000}

_CN_DIFFICULTY = {"和平": "peaceful", "简单": "easy", "普通": "normal",
                  "困难": "hard"}


def cn_number(text: str) -> int | None:
    """中文数字 → int（支持「十」「二十」「十个」这类写法）。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    raw = raw.rstrip("个只头把")
    if raw.isdigit():
        return int(raw)
    total = 0
    if "十" in raw:
        head, _, tail = raw.partition("十")
        tens = _CN_NUMBERS.get(head, 1 if not head else 0)
        ones = _CN_NUMBERS.get(tail, 0) if tail else 0
        total = tens * 10 + ones
    elif "百" in raw:
        head, _, tail = raw.partition("百")
        total = _CN_NUMBERS.get(head, 1) * 100 + (
            _CN_NUMBERS.get(tail, 0) if tail else 0)
    else:
        for char in raw:
            if char not in _CN_NUMBERS:
                return None
            total = total * 10 + _CN_NUMBERS[char] if total else _CN_NUMBERS[char]
    return total


def parse_cn_world_request(text: str, *,
                           session: Any = None) -> list[BridgeOperation]:
    """中文自然语言 → 一组世界操作（先 diff 再应用）。"""
    raw = str(text or "").strip()
    if not raw:
        return []
    from app.core.ids import resolve
    for pattern, rule in _NLP_RULES:
        match = re.search(pattern, raw)
        if not match:
            continue
        groups = match.groupdict()
        if rule == "fill_inventory":
            item_name = groups.get("item") or "钻石剑"
            item_id = resolve(item_name) or normalize_item_id(item_name)
            count = cn_number(groups.get("count") or "") or \
                item_stack_size(item_id) * 36
            player = groups.get("player") or ""
            uuid = _target_to_uuid(player, session)
            payload = {"item": item_id, "count": count,
                       "mode": "first_empty", "player": uuid,
                       "container": "Inventory", "target": player}
            return [BridgeOperation(
                kind="give", source="nlp", target=uuid or "level",
                payload=payload,
                label=f"把 {player or '玩家'} 的背包塞满 {item_name}",
                runner=operation_runner(BridgeOperation("give",
                                                        payload=payload)))]

        if rule == "spawn_entities":
            entity_name = groups.get("entity") or "僵尸"
            entity_id = resolve(entity_name) or normalize_item_id(entity_name)
            count = cn_number(groups.get("count") or "") or 1
            pos = groups.get("pos") or "出生点"
            if pos == "出生点":
                if session is not None and getattr(session, "world", None):
                    spawn = session.world.overview.spawn
                    pos = f"{spawn[0]} {spawn[1]} {spawn[2]}"
                else:
                    pos = "0 64 0"
            operations: list[BridgeOperation] = []
            base_x, base_y, base_z = _parse_pos(pos)
            for index in range(max(1, min(count, 200))):
                x = base_x + (index % 8)
                z = base_z + (index // 8)
                payload = {"entity": entity_id, "pos": f"{x} {base_y} {z}",
                           "nbt": ""}
                operations.append(BridgeOperation(
                    kind="summon", source="nlp", target="level", payload=payload,
                    label=f"在 {pos} 生成 {entity_name}"
                          + (f" ×{count}" if count > 1 else ""),
                    runner=operation_runner(
                        BridgeOperation("summon", payload=payload))))
            return operations

        if rule == "set_time":
            token = groups.get("value") or "白天"
            value = cn_number(token)
            if value is None:
                value = _CN_TIME.get(token, 1000)
            payload = {"action": "set", "value": value}
            return [BridgeOperation(
                kind="time", source="nlp", target="level", payload=payload,
                label=f"把时间设为 {token}（{value}）",
                runner=operation_runner(BridgeOperation("time",
                                                        payload=payload)))]

        if rule == "set_weather":
            token = groups.get("weather") or "晴天"
            weather = _CN_WEATHER.get(token, "clear")
            payload = {"weather": weather, "duration": 1000000}
            return [BridgeOperation(
                kind="weather", source="nlp", target="level", payload=payload,
                label=f"把天气设为 {token}",
                runner=operation_runner(BridgeOperation("weather",
                                                        payload=payload)))]

        if rule == "set_difficulty":
            token = groups.get("difficulty") or "普通"
            difficulty = _CN_DIFFICULTY.get(token, "normal")
            payload = {"difficulty": difficulty}
            return [BridgeOperation(
                kind="player_field", source="nlp", target="level",
                payload=payload, label=f"把难度设为 {token}",
                runner=_difficulty_runner(payload))]

        if rule == "set_gamerule":
            rule_name = groups.get("rule") or ""
            value = groups.get("value") or "true"
            payload = {"rule": rule_name, "value": value}
            return [BridgeOperation(
                kind="gamerule", source="nlp", target="level", payload=payload,
                label=f"游戏规则 {rule_name} = {value}",
                runner=operation_runner(BridgeOperation("gamerule",
                                                        payload=payload)))]

        if rule == "permanent_day":
            payload = {"permanent_day": True}
            return [BridgeOperation(
                kind="scene", source="nlp", target="level", payload=payload,
                label="永久白天",
                runner=operation_runner(BridgeOperation("scene",
                                                        payload=payload)))]

        if rule == "no_weather":
            payload = {"no_weather": True}
            return [BridgeOperation(
                kind="scene", source="nlp", target="level", payload=payload,
                label="禁止天气变化",
                runner=operation_runner(BridgeOperation("scene",
                                                        payload=payload)))]

        if rule == "set_border":
            size = cn_number(groups.get("size") or "") or 1000
            payload = {"action": "set", "size": size}
            return [BridgeOperation(
                kind="worldborder", source="nlp", target="level",
                payload=payload, label=f"把世界边界设为 {size}",
                runner=operation_runner(BridgeOperation("worldborder",
                                                        payload=payload)))]
    return []


def _difficulty_runner(payload: dict[str, Any]) -> Callable[[Any], str]:
    def run(session: Any) -> str:
        session.world.set_difficulty(payload.get("difficulty", "normal"))
        return f"难度已设为 {payload.get('difficulty')}"
    return run


def _parse_pos(text: str) -> tuple[float, float, float]:
    parts = [part for part in re.split(r"[ ,]+", str(text).strip()) if part]
    values: list[float] = []
    for part in parts[:3]:
        try:
            values.append(float(part.replace("~", "0")))
        except ValueError:
            values.append(0.0)
    while len(values) < 3:
        values.append(64.0 if len(values) == 1 else 0.0)
    return values[0], values[1], values[2]


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _absolute_pos(pos: str, session: Any) -> tuple[float, float, float] | None:
    """`~ ~1 ~` / `10 64 10` → 绝对坐标（相对坐标以玩家位置为基准）。"""
    parts = [part for part in str(pos).strip().split() if part]
    if len(parts) < 3:
        return None
    base = (0.0, 64.0, 0.0)
    if session is not None and getattr(session, "world", None) is not None:
        players = list(session.world.players.values())
        if players:
            base = tuple(float(v) for v in players[0].pos)     # type: ignore
    out: list[float] = []
    for index, part in enumerate(parts[:3]):
        if part.startswith("~"):
            offset = part[1:]
            try:
                delta = float(offset) if offset else 0.0
            except ValueError:
                delta = 0.0
            out.append(base[index] + delta)
            continue
        if part.startswith("^"):                        # 局部坐标不支持
            return None
        try:
            out.append(float(part))
        except ValueError:
            return None
    return out[0], out[1], out[2]


def _block_matches(node: SnbtNode, coords: tuple[float, float, float]) -> bool:
    try:
        values = tuple(float(node.get_child(key).value)      # type: ignore[union-attr]
                       for key in ("x", "y", "z"))
    except (AttributeError, TypeError, ValueError):
        return False
    return all(abs(a - b) < 1e-6 for a, b in zip(values, coords, strict=False))


def _snbt_value(text: Any) -> SnbtNode:
    """把命令里的 value 文本解析成 NBT 节点。"""
    from app.core.snbt import SnbtParser
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("value 为空")
    return SnbtParser(raw).parse()


def _document_target(ref: str, payload: dict[str, Any], session: Any) -> str:
    """`/data` 命令的第一个参数（block/entity/storage）→ 我们的写入目标。"""
    text = str(ref or "").strip().lower()
    explicit = str(payload.get("uuid") or "").strip()
    if explicit:
        return explicit
    if text in ("entity", "block"):
        # 选择器能对上玩家就用玩家，否则退回 level.dat
        return _target_to_uuid(str(payload.get("ref_target", "")),
                               session) or "level"
    if text in ("storage", "level", "world", ""):
        return "level"
    return _target_to_uuid(text, session) or "level"


def _write_path(world: WorldSave, target: str, path: str,
                value: SnbtNode) -> None:
    """按路径写 NBT（支持 `[0]` 下标与自动创建中间 Compound）。"""
    if target == "level":
        root = world.level_root
    else:
        player = world.players.get(target)
        root = player.root if player else None
    if root is None:
        raise KeyError(f"找不到写入目标：{target}")
    tokens = parse_field_path(path)
    parent = root
    for token in tokens[:-1]:
        if isinstance(token, int):
            if token >= len(parent.children):
                raise KeyError(f"下标越界：{path}")
            parent = parent.children[token]
        else:
            child = parent.get_child(token)
            if child is None:
                child = SnbtNode.new_compound(key=token)
                parent.add_child(child)
            parent = child
    last = tokens[-1]
    if isinstance(last, int):
        if last < len(parent.children):
            parent.children[last] = _with_key(value, None)
        else:
            parent.add_child(_with_key(value, None))
    else:
        existing = parent.get_child(str(last))
        clone = _with_key(value, str(last))
        if existing is None:
            parent.add_child(clone)
        else:
            index = parent.children.index(existing)
            parent.children[index] = clone
    if target == "level":
        world.mark_level_dirty()
    elif target in world.players:
        world.players[target].dirty = True


def _with_key(node: SnbtNode, key: str | None) -> SnbtNode:
    clone = node.clone()
    clone.key = key
    return clone


def fill_to_commands(payload: dict[str, Any]) -> list[str]:
    """把 fill 操作展开成命令（区域写入未成熟时的首选方案）。"""
    start = str(payload.get("start", "~ ~ ~"))
    end = str(payload.get("end", "~ ~ ~"))
    block = str(payload.get("block", "minecraft:stone"))
    mode = str(payload.get("mode", "replace"))
    return [f"fill {start} {end} {block} {mode}"]


def setblock_to_commands(payload: dict[str, Any]) -> list[str]:
    pos = str(payload.get("pos", "~ ~ ~"))
    block = str(payload.get("block", "minecraft:stone"))
    nbt = str(payload.get("nbt", "") or "")
    return [f"setblock {pos} {block} replace {nbt}".strip()]


def summon_to_commands(payload: dict[str, Any]) -> list[str]:
    entity = str(payload.get("entity", "minecraft:zombie"))
    pos = str(payload.get("pos", "~ ~ ~"))
    nbt = str(payload.get("nbt", "") or "")
    return [f"summon {entity} {pos} {nbt}".strip()]


__all__ = [
    "OPERATION_KINDS", "OPERATION_RUNNERS", "BridgeOperation", "BridgeResult",
    "split_command", "command_to_operation", "operation_runner",
    "preview_operation", "apply_operation",
    "item_to_give_operation", "item_to_data_operation",
    "entity_to_summon_operation", "container_to_commands",
    "nbt_path_to_command", "player_to_selector", "player_to_execute_commands",
    "coords_to_commands", "world_rules_to_commands", "ops_to_commands",
    "parse_cn_world_request", "cn_number", "split_item_argument",
    "fill_to_commands", "setblock_to_commands", "summon_to_commands",
    "apply_give", "apply_summon", "apply_time", "apply_weather",
    "apply_gamerule", "apply_scene",
]
