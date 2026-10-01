"""创新功能：蓝图 / 结构模板 + 物品组件配方工坊。

蓝图（Blueprint）
-----------------
从存档的一个选区导出一份「结构模板」，并同时给出可执行的命令：

* `.nbt` / `.mcstructure` 结构文件（供 `/place template` 或结构方块使用）；
* `.schem`（Sponge Schematic v2，用 gzip 容器，常见于 WorldEdit / 投影模组）；
* `/fill` `/setblock` `/place` 命令；
* 数据包函数。

结构模板的规范（`minecraft:structure`）：

```snbt
{
  DataVersion: 3955,
  size: [I; dx, dy, dz],
  palette: [{Name: "minecraft:stone"}, …],
  blocks: [{pos: [I; x, y, z], state: 0}, …],
  entities: []
}
```

配方工坊（Recipe Workshop）
---------------------------
把「一整套物品组件」保存成预设，然后批量应用到玩家 / 容器：

```json
{"id": "sword.sharp5", "name": "锋利 V 神剑", "item": "minecraft:diamond_sword",
 "count": 1, "components": [{"id": "minecraft:enchantments",
                             "value": "{levels:{\"minecraft:sharpness\":5}}"}]}
```

预设存在应用数据目录的 `user_data/blueprints/` 与 `user_data/recipes/` 下，
纯 JSON，方便手工编辑与分享。不依赖 Qt。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from app.core.nbt_bridge import (
    ItemStack, component_value_snbt, normalize_item_id,
    stack_to_command,
)
from app.core.nbt_binary import load_nbt_file, save_nbt_file
from app.core.snbt import SnbtKind, SnbtNode, SnbtParser, serialize
from app.storage import user_data_dir

#: 蓝图 / 配方目录
BLUEPRINT_DIR = "blueprints"
RECIPE_DIR = "recipes"

#: 目标 DataVersion
TARGET_DATA_VERSION = 3955

#: 结构模板支持的最大体积（避免误操作导出整张地图）
MAX_VOLUME = 32 * 32 * 32


def blueprints_dir() -> Path:
    path = user_data_dir() / BLUEPRINT_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def recipes_dir() -> Path:
    path = user_data_dir() / RECIPE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# 蓝图
# ---------------------------------------------------------------------------

@dataclass
class BlueprintBlock:
    """结构模板里的一个方块。"""

    pos: tuple[int, int, int]
    block: str
    nbt: SnbtNode | None = None

    def key(self) -> str:
        """调色板键：只用方块 ID（NBT 走 block_entities，不进调色板）。

        这跟原版结构模板一致：同一个方块 ID 无论带不带 NBT 都只有一种
        「状态」，方块实体数据单独存。之前把 SNBT 拼进键里会让调色板
        查找永远落空（key 是元组、索引是字符串）。
        """
        return normalize_item_id(self.block)


@dataclass
class Blueprint:
    """一份结构模板。"""

    name: str = "未命名结构"
    size: tuple[int, int, int] = (1, 1, 1)
    blocks: list[BlueprintBlock] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    source: str = ""
    created_at: str = ""

    # ---- 统计 ----

    @property
    def volume(self) -> int:
        return self.size[0] * self.size[1] * self.size[2]

    def block_count(self) -> int:
        return len(self.blocks)

    def palette(self) -> list[str]:
        seen: list[str] = []
        for block in self.blocks:
            key = block.key()
            if key not in seen:
                seen.append(key)
        return seen

    def block_entities(self) -> list[BlueprintBlock]:
        """带 NBT 的方块（箱子 / 熔炉 / 命令方块…）。"""
        return [block for block in self.blocks if block.nbt is not None]

    def bounds(self) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        if not self.blocks:
            return (0, 0, 0), tuple(self.size)          # type: ignore[return-value]
        xs = [b.pos[0] for b in self.blocks]
        ys = [b.pos[1] for b in self.blocks]
        zs = [b.pos[2] for b in self.blocks]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def summary(self) -> str:
        return (f"{self.name} · {self.size[0]}×{self.size[1]}×{self.size[2]}"
                f" · {self.block_count()} 个方块 · {len(self.palette())} 种调色板"
                f" · {len(self.entities)} 个实体")

    # ---- 生成 SNBT / 文件 ----

    def to_structure_node(self) -> SnbtNode:
        """生成 `minecraft:structure` 规范的 NBT。"""
        palette = self.palette()
        # 注意：不要在这里用 `index` 当变量名，否则会和下面的循环变量串味
        palette_index = {key: position for position, key in enumerate(palette)}

        node = SnbtNode.new_compound()
        node.add_child(SnbtNode.new_scalar(SnbtKind.INT, TARGET_DATA_VERSION,
                                           key="DataVersion"))
        size = SnbtNode(kind=SnbtKind.INT_ARRAY, key="size")
        for value in self.size:
            size.add_child(SnbtNode.new_scalar(SnbtKind.INT, int(value)))
        node.add_child(size)

        palette_node = SnbtNode.new_list(key="palette")
        for key in palette:
            entry = SnbtNode.new_compound()
            entry.add_child(SnbtNode.new_scalar(SnbtKind.STRING, key,
                                                key="Name"))
            palette_node.add_child(entry)
        node.add_child(palette_node)

        blocks_node = SnbtNode.new_list(key="blocks")
        for block in self.blocks:
            entry = SnbtNode.new_compound()
            pos = SnbtNode(kind=SnbtKind.INT_ARRAY, key="pos")
            for value in block.pos:
                pos.add_child(SnbtNode.new_scalar(SnbtKind.INT, int(value)))
            entry.add_child(pos)
            entry.add_child(SnbtNode.new_scalar(
                SnbtKind.INT, palette_index.get(block.key(), 0), key="state"))
            blocks_node.add_child(entry)
        node.add_child(blocks_node)

        # 原版结构模板把方块实体单独放在 block_entities 里
        entities_node = SnbtNode.new_list(key="block_entities")
        for block in self.blocks:
            if block.nbt is None:
                continue
            entry = SnbtNode.new_compound()
            for axis, value in zip(("x", "y", "z"), block.pos, strict=False):
                entry.add_child(SnbtNode.new_scalar(SnbtKind.INT, int(value),
                                                    key=axis))
            entry.add_child(SnbtNode.new_scalar(
                SnbtKind.STRING, block.key(), key="id"))
            for child in block.nbt.children:
                entry.add_child(child.clone())
            entities_node.add_child(entry)
        node.add_child(entities_node)

        # 实体（生物）列表
        mobs_node = SnbtNode.new_list(key="entities")
        for text in self.entities:
            try:
                parsed = SnbtParser(text).parse()
            except Exception:                           # pragma: no cover
                continue
            mobs_node.add_child(parsed)
        node.add_child(mobs_node)
        return node

    def to_snbt(self, pretty: bool = False) -> str:
        return serialize(self.to_structure_node(), pretty=pretty)

    def save_nbt(self, path: str | Path | None = None,
                 compress: str = "gzip") -> Path:
        target = Path(path) if path else (
            blueprints_dir() / f"{_safe_name(self.name)}.nbt")
        save_nbt_file(self.to_structure_node(), target, compress=compress,
                      root_name="")
        return target

    def save_schem(self, path: str | Path | None = None) -> Path:
        """导出 Sponge Schematic v2（gzip 容器，WorldEdit / 投影模组可读）。"""
        target = Path(path) if path else (
            blueprints_dir() / f"{_safe_name(self.name)}.schem")
        root = self.to_structure_node()
        # Sponge 用 Width/Height/Length + Palette/PaletteMax + BlockData 的写法，
        # 这里做一次最小映射（方块实体进 BlockEntities）
        palette = self.palette()
        palette_index = {key: position for position, key in enumerate(palette)}
        width, height, length = self.size
        volume = max(1, width * height * length)
        node = SnbtNode.new_compound()
        node.add_child(SnbtNode.new_scalar(SnbtKind.INT, TARGET_DATA_VERSION,
                                           key="DataVersion"))
        node.add_child(SnbtNode(kind=SnbtKind.SHORT, value=width,
                                key="Width"))
        node.add_child(SnbtNode(kind=SnbtKind.SHORT, value=height,
                                key="Height"))
        node.add_child(SnbtNode(kind=SnbtKind.SHORT, value=length,
                                key="Length"))
        node.add_child(SnbtNode(kind=SnbtKind.INT, value=len(palette),
                                key="PaletteMax"))
        palette_node = SnbtNode.new_compound(key="Palette")
        for position, key in enumerate(palette):
            palette_node.add_child(SnbtNode.new_scalar(SnbtKind.INT, position,
                                                       key=key))
        node.add_child(palette_node)

        # Sponge 的 BlockData 是「按 (y, z, x) 铺平」的完整体积数组
        # （offset = (y * Length + z) * Width + x），读取端按同一公式还原。
        # 注意 0 号调色板项在写入端表示空气，所以这里把 palette[0] 之后
        # 再用 1 偏移会破坏兼容性，保持原样（与 WorldEdit 一致）。
        values = [0] * volume
        for block in self.blocks:
            x, y, z = block.pos
            offset = (y * length + z) * width + x
            if 0 <= offset < volume:
                values[offset] = palette_index.get(block.key(), 0)
        data = SnbtNode(kind=SnbtKind.BYTE_ARRAY, key="BlockData")
        for value in values:
            data.add_child(SnbtNode.new_scalar(SnbtKind.BYTE, value))
        node.add_child(data)

        # 带 NBT 的方块（箱子 / 熔炉…）按 Sponge 规范放进 BlockEntities
        entities = SnbtNode.new_list(key="BlockEntities")
        for block in self.blocks:
            if block.nbt is None:
                continue
            entry = SnbtNode.new_compound()
            for axis, value in zip(("x", "y", "z"), block.pos, strict=False):
                entry.add_child(SnbtNode.new_scalar(SnbtKind.INT, int(value),
                                                    key=axis))
            entry.add_child(SnbtNode.new_scalar(
                SnbtKind.STRING, block.key(), key="Id"))
            for child in block.nbt.children:
                entry.add_child(child.clone())
            entities.add_child(entry)
        node.add_child(entities)
        _ = root
        save_nbt_file(node, target, compress="gzip", root_name="")
        return target

    # ---- 生成命令 ----

    def to_commands(self, origin: tuple[int, int, int] = (0, 0, 0),
                    *, mode: str = "setblock", limit: int = 4096) -> list[str]:
        """生成可执行命令（setblock / fill / place）。"""
        if mode == "place":
            return [f"place template mcstudio:{_safe_name(self.name)} "
                    f"{origin[0]} {origin[1]} {origin[2]}"]
        commands: list[str] = []
        if mode == "fill":
            (x0, y0, z0), (x1, y1, z1) = self.bounds()
            commands.append(
                f"fill {origin[0] + x0} {origin[1] + y0} {origin[2] + z0} "
                f"{origin[0] + x1} {origin[1] + y1} {origin[2] + z1} "
                f"{normalize_item_id(self.blocks[0].block) if self.blocks else 'minecraft:air'} replace")
            return commands
        for block in self.blocks[:limit]:
            x = origin[0] + block.pos[0]
            y = origin[1] + block.pos[1]
            z = origin[2] + block.pos[2]
            nbt = f" {serialize(block.nbt, pretty=False)}" if block.nbt else ""
            commands.append(
                f"setblock {x} {y} {z} {normalize_item_id(block.block)} "
                f"replace{nbt}".rstrip())
        if len(self.blocks) > limit:
            commands.append(f"# 还有 {len(self.blocks) - limit} 个方块未生成"
                            "（可提高 limit 或用 forceload + 分批执行）")
        for text in self.entities:
            commands.append(f"# 实体：{text[:120]}")
        return commands

    def to_mcfunction(self) -> str:
        header = [
            f"# {self.name}",
            f"# 由 MCStudio 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"# 尺寸 {self.size[0]}×{self.size[1]}×{self.size[2]}"
            f" · {self.block_count()} 个方块 · 调色板 {len(self.palette())} 种",
            f"# 来源：{self.source or '存档选区'}",
        ]
        return "\n".join(header + self.to_commands())


def _safe_name(name: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fa5.\-]+", "_", str(name or "")).strip("_")
    return text or "blueprint"


def export_selection(source: Any, name: str = "选区",
                     *, max_volume: int = MAX_VOLUME) -> Blueprint:
    """从一个「方块来源」导出蓝图。

    `source` 支持两种形态：

    * `dict[(x, y, z)] = 方块 id 或 (id, nbt)`；
    * 一块区域里的 `block_entities` 列表（会自动补上空气）。
    """
    blueprint = Blueprint(name=name, created_at=datetime.now().isoformat(
        timespec="seconds"))
    if isinstance(source, dict):
        for key, value in source.items():
            if not (isinstance(key, tuple) and len(key) == 3):
                continue
            x, y, z = (int(v) for v in key)
            if isinstance(value, tuple) and len(value) == 2:
                block_id, nbt = value
            else:
                block_id, nbt = value, None
            blueprint.blocks.append(BlueprintBlock(
                (x, y, z), normalize_item_id(str(block_id)),
                nbt if isinstance(nbt, SnbtNode) else None))
        if blueprint.blocks:
            xs = [b.pos[0] for b in blueprint.blocks]
            ys = [b.pos[1] for b in blueprint.blocks]
            zs = [b.pos[2] for b in blueprint.blocks]
            min_x, min_y, min_z = min(xs), min(ys), min(zs)
            for block in blueprint.blocks:
                block.pos = (block.pos[0] - min_x, block.pos[1] - min_y,
                             block.pos[2] - min_z)
            blueprint.size = (max(xs) - min_x + 1, max(ys) - min_y + 1,
                              max(zs) - min_z + 1)
    if blueprint.volume > max_volume:
        raise ValueError(
            f"选区体积 {blueprint.volume} 超过上限 {max_volume}；"
            "请缩小范围或调高 max_volume")
    return blueprint


def blueprint_from_block_entities(container: SnbtNode,
                                  name: str = "容器选区") -> Blueprint:
    """从 `block_entities`（或带 Items 的方块实体列表）导出蓝图。"""
    blocks: dict[tuple[int, int, int], tuple[str, SnbtNode | None]] = {}
    for entry in container.children:
        if entry.kind != SnbtKind.COMPOUND:
            continue
        try:
            x = int(entry.get_child("x").value)         # type: ignore[union-attr]
            y = int(entry.get_child("y").value)         # type: ignore[union-attr]
            z = int(entry.get_child("z").value)         # type: ignore[union-attr]
        except (AttributeError, TypeError, ValueError):
            continue
        block_id = str(entry.get_child("id").value) if entry.get_child("id") \
            else "minecraft:chest"
        payload = entry.clone()
        for key in ("x", "y", "z", "id"):
            child = payload.get_child(key)
            if child is not None:
                payload.remove_child(child)
        blocks[(x, y, z)] = (normalize_item_id(block_id), payload)
    return export_selection(blocks, name=name)


def load_blueprint(path: str | Path) -> Blueprint:
    """读回一份蓝图（.nbt / .schem / .snbt）。"""
    path = Path(path)
    if path.suffix.lower() == ".snbt":
        root = SnbtParser(path.read_text(encoding="utf-8")).parse()
    else:
        root, _meta = load_nbt_file(path, with_meta=True)
    blueprint = Blueprint(name=path.stem, source=str(path),
                          created_at=datetime.now().isoformat(
                              timespec="seconds"))
    size = root.get_child("size")
    if size is not None:
        blueprint.size = tuple(int(c.value) for c in size.children[:3])  # type: ignore
    elif root.get_child("Width") is not None:
        blueprint.size = (int(_num(root.get_child("Width"), 1)),
                          int(_num(root.get_child("Height"), 1)),
                          int(_num(root.get_child("Length"), 1)))
    palette = root.get_child("palette") or root.get_child("Palette")
    names: list[str] = []
    if palette is not None:
        if palette.kind == SnbtKind.LIST:
            for entry in palette.children:
                names.append(str(_scalar(entry.get_child("Name"), "minecraft:air")))
        else:
            pairs = sorted(
                ((int(_num(child, 0)), str(child.key))
                 for child in palette.children), key=lambda pair: pair[0])
            names = [name for _index, name in pairs]
    blocks = root.get_child("blocks")
    if blocks is not None and blocks.kind == SnbtKind.LIST:
        for entry in blocks.children:
            pos = entry.get_child("pos")
            if pos is None or len(pos.children) < 3:
                continue
            coordinates = tuple(int(c.value) for c in pos.children[:3])
            state = int(_num(entry.get_child("state"), 0))
            block_id = names[state] if 0 <= state < len(names) \
                else "minecraft:air"
            # 兼容旧写法：NBT 直接挂在方块项的 `nbt` 上
            nbt = entry.get_child("nbt")
            blueprint.blocks.append(BlueprintBlock(coordinates, block_id, nbt))
    else:
        data = root.get_child("BlockData")
        if data is not None and blueprint.volume > 1:
            width, height, length = blueprint.size
            values = [int(c.value) & 0xFF for c in data.children]
            # Sponge 的 BlockData 按 (y, z, x) 铺平：
            #   offset = (y * Length + z) * Width + x
            # 所以还原时先按 Width*Length 切出 y，再按 Width 切出 z。
            plane = max(1, width * length)
            for offset, value in enumerate(values):
                if value == 0 or offset >= width * height * length:
                    continue
                y, rest = divmod(offset, plane)
                z, x = divmod(rest, width)
                block_id = names[value] if value < len(names) \
                    else "minecraft:air"
                blueprint.blocks.append(
                    BlueprintBlock((x, y, z), block_id, None))

    # 方块实体：原版结构模板放在 block_entities 里，按坐标合并回方块
    block_entities = root.get_child("block_entities") \
        or root.get_child("BlockEntities")
    if block_entities is not None and block_entities.kind == SnbtKind.LIST:
        by_pos = {block.pos: block for block in blueprint.blocks}
        for entry in block_entities.children:
            if entry.kind != SnbtKind.COMPOUND:
                continue
            try:
                coords = tuple(int(_num(entry.get_child(axis), 0))
                               for axis in ("x", "y", "z"))
            except (TypeError, ValueError):             # pragma: no cover
                continue
            block_id = str(_scalar(entry.get_child("id"), "") or
                           _scalar(entry.get_child("Id"), "") or
                           "minecraft:chest")
            payload = SnbtNode.new_compound()
            for child in entry.children:
                if str(child.key) in ("x", "y", "z", "id", "Id", "keepPacked"):
                    continue
                payload.add_child(child.clone())
            existing = by_pos.get(coords)
            if existing is not None:
                existing.nbt = payload
                if existing.block in ("minecraft:air", ""):  # pragma: no cover
                    existing.block = normalize_item_id(block_id)
            else:
                block = BlueprintBlock(coords, normalize_item_id(block_id),
                                       payload)
                blueprint.blocks.append(block)
                by_pos[coords] = block
        if blueprint.blocks and blueprint.size == (1, 1, 1):
            xs = [b.pos[0] for b in blueprint.blocks]
            ys = [b.pos[1] for b in blueprint.blocks]
            zs = [b.pos[2] for b in blueprint.blocks]
            # 结构文件里没有 size 时，按实际占据的范围推算（从 0 开始）
            blueprint.size = (max(0, max(xs)) + 1, max(0, max(ys)) + 1,
                              max(0, max(zs)) + 1)

    entities = root.get_child("entities")
    if entities is not None and entities.kind == SnbtKind.LIST:
        blueprint.entities = [serialize(child, pretty=False)
                              for child in entities.children]
    return blueprint


def list_blueprints() -> list[Path]:
    folder = blueprints_dir()
    return sorted(list(folder.glob("*.nbt")) + list(folder.glob("*.schem"))
                  + list(folder.glob("*.snbt")))


def save_named_blueprint(blueprint: "Blueprint", *,
                         fmt: str = "both") -> list[str]:
    """按指定格式写蓝图，返回写出的文件名列表。

    `fmt` 取 `both` / `nbt` / `schem`；写出的位置是
    `user_data/blueprints/`。
    """
    written: list[str] = []
    if fmt in ("both", "nbt"):
        written.append(blueprint.save_nbt().name)
    if fmt in ("both", "schem"):
        written.append(blueprint.save_schem().name)
    return written


# ---------------------------------------------------------------------------
# 物品 / NBT 配方工坊
# ---------------------------------------------------------------------------

@dataclass
class ItemRecipe:
    """一整套物品组件预设（可批量应用到玩家 / 容器）。"""

    id: str = ""
    name: str = "未命名配方"
    item: str = "minecraft:stone"
    count: int = 1
    components: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""
    builtin: bool = False

    def component_text(self) -> str:
        """`[id=value,id2=value2]` 形式。"""
        if not self.components:
            return ""
        chunks = []
        for entry in self.components:
            cid = str(entry.get("id", ""))
            value = component_value_snbt(entry.get("value", ""))
            short = cid.split(":", 1)[-1]
            chunks.append(f"{short}={value}" if value else short)
        return "[" + ",".join(chunks) + "]"

    def to_give_command(self, target: str = "@p") -> str:
        argument = f"{normalize_item_id(self.item)}{self.component_text()}"
        return f"give {target} {argument} {max(1, int(self.count))}"

    def apply_to_stack(self, stack: ItemStack) -> ItemStack:
        """把配方套用到已有的物品堆（保留槽位与来源）。"""
        clone = ItemStack(item_id=normalize_item_id(self.item),
                          count=max(1, int(self.count)), slot=stack.slot,
                          path=stack.path, source=stack.source)
        clone.components = {str(e.get("id")): e.get("value", "")
                            for e in self.components}
        return clone

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "item": self.item,
                "count": self.count, "components": self.components,
                "note": self.note}

    @classmethod
    def from_dict(cls, data: dict) -> "ItemRecipe":
        return cls(id=str(data.get("id", "")),
                   name=str(data.get("name", "未命名配方")),
                   item=normalize_item_id(str(data.get("item", "minecraft:stone"))),
                   count=max(1, int(data.get("count", 1) or 1)),
                   components=list(data.get("components") or []),
                   note=str(data.get("note", "")))

    def save(self) -> Path:
        key = self.id or _safe_name(self.name)
        path = recipes_dir() / f"{key}.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False,
                                   indent=2), encoding="utf-8")
        return path


def list_recipes() -> list[ItemRecipe]:
    """所有配方（内置物品处方 + 用户保存的）。"""
    out: list[ItemRecipe] = []
    seen: set[str] = set()
    # 1) 内置物品处方（app/data/builtin/item_presets.json）
    try:
        from app.core.item_components import parse_components
        from app.core.data import store as data_store
        for preset in data_store().item_presets():
            if not isinstance(preset, dict):
                continue
            key = str(preset.get("id", "")).strip()
            if not key or key in seen:
                continue
            specs = parse_components(str(preset.get("components", "")))
            recipe = ItemRecipe(
                id=key, name=str(preset.get("name", key)),
                item=normalize_item_id(str(preset.get("item", "minecraft:stone"))),
                count=max(1, int(preset.get("count", 1) or 1)),
                components=[{"id": spec.id, "value": spec.value}
                            for spec in specs],
                note=str(preset.get("category", "")), builtin=True)
            seen.add(key)
            out.append(recipe)
    except Exception:                                   # pragma: no cover
        pass
    # 2) 用户配方（覆盖同名内置）
    folder = recipes_dir()
    for path in sorted(folder.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):                   # pragma: no cover
            continue
        if not isinstance(payload, dict):
            continue
        recipe = ItemRecipe.from_dict(payload)
        if not recipe.id:
            recipe.id = path.stem
        for index, existing in enumerate(out):
            if existing.id == recipe.id:
                out[index] = recipe
                break
        else:
            out.append(recipe)
    return out


def get_recipe(key: str) -> ItemRecipe | None:
    wanted = str(key or "").strip().lower()
    for recipe in list_recipes():
        if wanted in (recipe.id.lower(), recipe.name.lower()):
            return recipe
    return None


def apply_recipe_to_container(world: Any, target: str, path: str,
                              recipe: ItemRecipe, *,
                              mode: str = "first_empty",
                              slot: int | None = None) -> int:
    """把配方批量应用到容器（返回写入数量）。"""
    return world.grow_items(target, path, recipe.item, recipe.count,
                            mode=mode, slot=slot,
                            components=_components_fragment(recipe))


def _components_fragment(recipe: ItemRecipe) -> str:
    """把配方组件拼成 `grow_items` 需要的片段文本。"""
    if not recipe.components:
        return ""
    chunks = []
    for entry in recipe.components:
        cid = str(entry.get("id", "")).split(":", 1)[-1]
        value = component_value_snbt(entry.get("value", ""))
        chunks.append(f"{cid}={value}" if value else cid)
    return ",".join(chunks)


def stacks_to_give_commands(stacks: Iterable[ItemStack],
                            target: str = "@p") -> list[str]:
    return [stack_to_command(stack, target) for stack in stacks]


def _num(node: SnbtNode | None, default: float = 0) -> float:
    if node is None or node.is_container:
        return default
    try:
        return float(node.value)
    except (TypeError, ValueError):                     # pragma: no cover
        return default


def _scalar(node: SnbtNode | None, default: Any = None) -> Any:
    if node is None or node.is_container:
        return default
    return node.value


__all__ = [
    "BLUEPRINT_DIR", "RECIPE_DIR", "MAX_VOLUME", "TARGET_DATA_VERSION",
    "BlueprintBlock", "Blueprint", "ItemRecipe",
    "blueprints_dir", "recipes_dir", "export_selection",
    "blueprint_from_block_entities", "load_blueprint", "list_blueprints",
    "list_recipes", "get_recipe", "apply_recipe_to_container",
    "stacks_to_give_commands",
]
