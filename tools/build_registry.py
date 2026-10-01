#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把官方注册表数据整理成内置 JSON（items / blocks / entities / enchantments）。

数据来源（只在需要更新时联网）：
    * PrismarineJS/minecraft-data  —— 1.21.1 的物品 / 方块 / 实体 / 附魔注册表
    * Misode/mcmeta 1.21.1-assets  —— 官方 zh_cn.json 语言文件（中文名）

用法：
    # 1) 先把原始数据下到 build/mcdata/
    python tools/fetch_registry.py
    # 2) 生成内置数据
    python tools/build_registry.py

生成结果会保留原有的手工中文名 / category / tags / note（按 id 合并覆盖），
所以重复执行不会把人工润色冲掉。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILTIN = ROOT / "app" / "data" / "builtin"
RAW = ROOT / "build" / "mcdata"

VERSION = "1.21.1"

# --------------------------------------------------------------------------
# 分类规则
# --------------------------------------------------------------------------

ARMOR_PIECES = {
    "helmet": "头盔", "chestplate": "胸甲", "leggings": "护腿",
    "boots": "靴子", "horse_armor": "马铠", "wolf_armor": "狼铠",
}
TOOL_HEADS = ("pickaxe", "axe", "shovel", "hoe", "shears", "fishing_rod",
              "flint_and_steel", "brush", "mace", "shield")
WEAPON_HEADS = ("sword", "bow", "crossbow", "trident", "arrow",
                "spectral_arrow", "tipped_arrow", "arrow")
# 方块材料的粗分类（用于「方块」大类下的二级分类）
BLOCK_MATERIAL_ZH = {
    "mineable/pickaxe": "石材",
    "mineable/axe": "木质",
    "mineable/shovel": "土质",
    "mineable/hoe": "植物",
    "default": "其他",
    "stone": "石材",
    "wood": "木质",
    "dirt": "土质",
    "plant": "植物",
    "gourd": "植物",
    "glass": "玻璃",
    "wool": "羊毛",
    "cloth": "布料",
    "metal": "金属",
    "sand": "沙质",
    "leaves": "树叶",
    "web": "其他",
    "cake": "食物",
    "cactus": "植物",
    "portal": "传送门",
    "coral": "珊瑚",
    "decoration": "装饰",
    "amethyst": "紫水晶",
    "sculk": "幽匿",
    "froglight": "蛙明灯",
    "shulker_box": "潜影盒",
    "mud": "泥巴",
    "sculk_vein": "幽匿",
    "moss": "苔藓",
    "explosive": "爆炸物",
    "tnt": "爆炸物",
    "ice": "冰",
    "snow": "雪",
    "powder_snow": "雪",
    "air": "空气",
    "fire": "火",
    "water": "液体",
    "lava": "液体",
    "anvil": "金属",
    "barrier": "装饰",
    "bamboo": "植物",
    "bamboo_sapling": "植物",
    "cave_vines": "植物",
    "heavy_metal": "金属",
    "light": "光源",
    "piston": "机械",
    "portion": "药水",
    "pufferfish": "其他",
    "replaceable_plant": "植物",
    "sculk_replaceable": "幽匿",
    "sculk_replaceable_world_gen": "幽匿",
    "top_snow": "雪",
    "vine": "植物",
}


def item_category(name: str, stack: int, tags: set[str]) -> str:
    short = name
    if short.endswith("_spawn_egg"):
        return "刷怪蛋"
    if short.endswith("_boat") or short.endswith("_raft"):
        return "交通"
    if short.endswith("_minecart") or short == "minecart":
        return "交通"
    if "_bucket" in short or short == "bucket":
        return "桶"
    if short.startswith("music_disc_"):
        return "唱片"
    if short.endswith("_banner") or short == "banner":
        return "装饰"
    if short.endswith("_bed") or short == "bed":
        return "装饰"
    if short.endswith("_shulker_box") or short == "shulker_box":
        return "容器"
    if short.endswith("_potion") or short == "potion":
        return "药水"
    if short.endswith("_arrow") or short == "arrow":
        return "武器"
    for piece, _zh in ARMOR_PIECES.items():
        if short.endswith(piece):
            return "盔甲"
    if short.endswith("_horse_armor") or short == "horse_armor" \
            or short.endswith("_wolf_armor"):
        return "盔甲"
    if short.endswith("_sword") or short in ("bow", "crossbow", "trident",
                                             "mace", "shield"):
        return "武器"
    if short.endswith(("_pickaxe", "_axe", "_shovel", "_hoe")) \
            or short in TOOL_HEADS:
        return "工具"
    if short.endswith("_helmet") or short.endswith("_chestplate") \
            or short.endswith("_leggings") or short.endswith("_boots"):
        return "盔甲"
    if short in ("apple", "bread", "cookie", "cake", "pumpkin_pie",
                 "melon_slice", "sweet_berries", "glow_berries",
                 "honey_bottle", "milk_bucket", "dried_kelp",
                 "rotten_flesh", "spider_eye", "poisonous_potato",
                 "chorus_fruit", "enchanted_golden_apple", "golden_apple",
                 "golden_carrot", "beef", "porkchop", "chicken", "mutton",
                 "rabbit", "cod", "salmon", "tropical_fish", "pufferfish",
                 "potato", "carrot", "beetroot", "kelp", "suspicious_stew",
                 "mushroom_stew", "rabbit_stew", "beetroot_soup", "cookie",
                 "glow_berries", "honey_bottle") \
            or short.endswith(("_stew", "_soup", "_raw", "_cooked")):
        return "食物"
    if "redstone" in short or short in ("repeater", "comparator", "piston",
                                        "sticky_piston", "observer",
                                        "dispenser", "dropper", "hopper",
                                        "lever", "tripwire_hook",
                                        "daylight_detector", "target",
                                        "lightning_rod", "sculk_sensor",
                                        "calibrated_sculk_sensor",
                                        "crafter", "lectern", "note_block"):
        return "红石"
    if short.endswith("_sign") or short.endswith("_hanging_sign"):
        return "装饰"
    if short.endswith(("_door", "_trapdoor", "_fence", "_fence_gate",
                       "_slab", "_stairs", "_wall", "_button",
                       "_pressure_plate")):
        return "建材"
    if short.endswith(("_planks", "_log", "_wood", "_sapling", "_leaves",
                       "_slab")) or short.startswith(("stripped_",
                                                      "oak_", "spruce_",
                                                      "birch_", "jungle_",
                                                      "acacia_", "dark_oak_",
                                                      "mangrove_", "cherry_",
                                                      "bamboo_", "crimson_",
                                                      "warped_", "pale_oak_")):
        if "_log" in short or "_wood" in short or "_planks" in short \
                or "_sapling" in short or "_leaves" in short:
            return "方块"
    if short.endswith(("_helmet", "_chestplate", "_leggings", "_boots")):
        return "盔甲"
    if short.endswith("_block") or short.endswith("_ore") \
            or short.endswith("_ingot") or short.endswith("_nugget"):
        return "材料" if short.endswith(("_ingot", "_nugget")) else "方块"
    return "材料" if not tags else "其他"


def entity_category(entry: dict) -> str:
    etype = str(entry.get("type", ""))
    cat = str(entry.get("category", ""))
    name = str(entry.get("name", ""))
    if etype == "projectile" or name in ("arrow", "spectral_arrow",
                                         "trident", "fireball",
                                         "small_fireball", "dragon_fireball",
                                         "wither_skull", "snowball", "egg",
                                         "ender_pearl", "eye_of_ender",
                                         "potion", "experience_bottle",
                                         "firework_rocket", "llama_spit",
                                         "shulker_bullet", "fishing_bobber",
                                         "wind_charge", "breeze_wind_charge"):
        return "弹射物"
    if etype in ("vehicle", "boat", "minecart"):
        return "载具"
    if name in ("ender_dragon", "wither", "warden", "elder_guardian"):
        return "Boss"
    if "Hostile" in cat or "hostile" in cat:
        return "敌对生物"
    if "Passive" in cat or "passive" in cat:
        return "被动生物"
    if "Neutral" in cat or "neutral" in cat:
        return "中立生物"
    if etype in ("living", "mob"):
        return "生物"
    if etype in ("object", "other", "block", "environment"):
        return "其他实体"
    return "其他实体"


# --------------------------------------------------------------------------
# 标签（供物品组件适用性提示）
# --------------------------------------------------------------------------

def build_tags(name: str, category: str) -> list[str]:
    tags: set[str] = set()
    short = name
    if short.endswith(("_sword", "bow", "crossbow", "trident", "mace", "_axe")):
        tags |= {"weapon", "tool"}
    if short.endswith(("_pickaxe", "_shovel", "_hoe", "shears",
                       "fishing_rod", "flint_and_steel", "brush")):
        tags.add("tool")
    if short.endswith(("_sword", "_pickaxe", "_axe", "_shovel", "_hoe",
                       "_helmet", "_chestplate", "_leggings", "_boots",
                       "shield", "elytra", "bow", "crossbow", "trident",
                       "fishing_rod", "shears", "flint_and_steel", "mace")):
        tags.add("durable")
    if short.endswith("_helmet"):
        tags |= {"armor", "armor_head", "head"}
    if short.endswith("_chestplate"):
        tags |= {"armor", "armor_chest"}
    if short.endswith("_leggings"):
        tags |= {"armor", "armor_legs"}
    if short.endswith("_boots"):
        tags |= {"armor", "armor_feet"}
    if short == "player_head":
        tags.add("head")
    if "potion" in short:
        tags.add("potion")
    if short.endswith("_banner") or short == "shield":
        tags.add("shield" if short == "shield" else "banner")
    if "leather" in short:
        tags |= {"leather", "dyeable"}
    if short.endswith("_shulker_box") or short in (
            "shulker_box", "chest", "barrel", "hopper", "dispenser",
            "dropper", "furnace", "blast_furnace", "smoker", "crafter",
            "brewing_stand", "trapped_chest", "ender_chest"):
        tags.add("container")
    if short == "bundle":
        tags.add("bundle")
    if short == "crossbow":
        tags.add("crossbow")
    if short.endswith("_book") or short == "book":
        tags.add("book")
    if short.endswith("_map") or short in ("map", "filled_map"):
        tags.add("map")
    if short in ("potion", "splash_potion", "lingering_potion"):
        tags.add("potion")
    if category == "食物":
        tags.add("food")
    return sorted(tags)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def existing_entries(fname: str, key: str) -> dict[str, dict]:
    path = BUILTIN / fname
    if not path.exists():
        return {}
    try:
        data = load_json(path)
    except Exception:
        return {}
    return {str(e.get("id")): e for e in data.get(key, []) if e.get("id")}


def zh_name(lang: dict, short: str, kind: str, fallback: str) -> str:
    for prefix in (f"{kind}.minecraft.{short}", f"item.minecraft.{short}",
                   f"block.minecraft.{short}", f"entity.minecraft.{short}"):
        value = lang.get(prefix)
        if value:
            return str(value)
    return fallback


def write_json(path: Path, payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    # 每个条目一行，避免 1300 条把文件撑得太长
    text = re.sub(r"\n\s+(\{[^{}]*\})\s*(?=,?\n)", r"\n    \1", text)
    path.write_text(text + "\n", encoding="utf-8")
    print(f"  {path.name:26} {path.stat().st_size / 1024:7.1f} KB")


def build_items(lang: dict) -> None:
    raw = load_json(RAW / "items.json")
    foods = {f["name"]: f for f in load_json(RAW / "foods.json")}
    blocks = {b["name"]: b for b in load_json(RAW / "blocks.json")}
    old = existing_entries("items.json", "items")

    entries: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        short = str(item.get("name", ""))
        if not short or short in seen:
            continue
        seen.add(short)
        stack = int(item.get("stackSize", 64) or 64)
        display = zh_name(lang, short, "item", str(item.get("displayName", short)))
        block = blocks.get(short)
        category = item_category(short, stack, set())
        if block and category == "材料":
            category = "方块"
        entry: dict = {"id": f"minecraft:{short}", "display": display,
                       "category": category}
        if stack != 64:
            entry["stack_size"] = stack
        tags = build_tags(short, category)
        old_entry = old.get(f"minecraft:{short}")
        if old_entry:
            # 保留人工润色
            entry["display"] = str(old_entry.get("display") or display)
            entry["category"] = str(old_entry.get("category") or category)
            if old_entry.get("tags"):
                tags = sorted(set(tags) | {str(t) for t in old_entry["tags"]})
            for key in ("note", "hint"):
                if old_entry.get(key):
                    entry[key] = old_entry[key]
        if short == "written_book":
            entry["stack_size"] = 16
        if tags:
            entry["tags"] = tags
        if short in foods:
            entry["food"] = {
                "nutrition": int(foods[short].get("foodPoints", 0)),
                "saturation": float(foods[short].get("saturation", 0.0)),
            }
        entries.append(entry)

    entries.sort(key=lambda e: (e["category"], e["id"]))
    write_json(BUILTIN / "items.json", {
        "version": VERSION,
        "note": "1.21.1 全部可获得物品（含可放置的方块物品），共 %d 条。"
                "category 供选择器分组，tags 供物品组件适用性提示，"
                "stack_size 仅在不是 64 时出现。"
                % len(entries),
        "source": "PrismarineJS/minecraft-data 1.21.1 + 官方 zh_cn 语言文件",
        "items": entries,
    })
    print(f"     items: {len(entries)}")


def build_blocks(lang: dict) -> None:
    raw = load_json(RAW / "blocks.json")
    old = existing_entries("blocks.json", "blocks")
    entries: list[dict] = []
    seen: set[str] = set()
    for block in raw:
        short = str(block.get("name", ""))
        if not short or short in seen or short == "air":
            continue
        seen.add(short)
        material = str(block.get("material") or "default")
        category = BLOCK_MATERIAL_ZH.get(material, "其他")
        display = zh_name(lang, short, "block",
                          str(block.get("displayName", short)))
        entry: dict = {"id": f"minecraft:{short}", "display": display,
                       "category": category}
        hardness = block.get("hardness")
        if isinstance(hardness, (int, float)) and hardness > 0:
            entry["hardness"] = round(float(hardness), 2)
        if block.get("emitLight"):
            entry["light"] = int(block["emitLight"])
        old_entry = old.get(f"minecraft:{short}")
        if old_entry:
            entry["display"] = str(old_entry.get("display") or display)
            entry["category"] = str(old_entry.get("category") or category)
            if old_entry.get("note"):
                entry["note"] = old_entry["note"]
        entries.append(entry)

    entries.sort(key=lambda e: (e["category"], e["id"]))
    write_json(BUILTIN / "blocks.json", {
        "version": VERSION,
        "note": "1.21.1 全部方块，共 %d 条。category 来自方块挖掘材料类别，"
                "hardness/light 仅在有意义时出现。" % len(entries),
        "source": "PrismarineJS/minecraft-data 1.21.1 + 官方 zh_cn 语言文件",
        "blocks": entries,
    })
    print(f"     blocks: {len(entries)}")


def build_entities(lang: dict) -> None:
    raw = load_json(RAW / "entities.json")
    old = existing_entries("entities.json", "entities")
    entries: list[dict] = []
    seen: set[str] = set()
    for entity in raw:
        short = str(entity.get("name", ""))
        if not short or short in seen:
            continue
        seen.add(short)
        display = zh_name(lang, short, "entity",
                          str(entity.get("displayName", short)))
        category = entity_category(entity)
        entry: dict = {"id": f"minecraft:{short}", "display": display,
                       "category": category}
        etype = str(entity.get("type", ""))
        if etype:
            entry["type"] = etype
        old_entry = old.get(f"minecraft:{short}")
        if old_entry:
            entry["display"] = str(old_entry.get("display") or display)
            entry["category"] = str(old_entry.get("category") or category)
            if old_entry.get("note"):
                entry["note"] = old_entry["note"]
        entries.append(entry)

    entries.sort(key=lambda e: (e["category"], e["id"]))
    write_json(BUILTIN / "entities.json", {
        "version": VERSION,
        "note": "1.21.1 全部实体类型，共 %d 条。" % len(entries),
        "source": "PrismarineJS/minecraft-data 1.20.5+（1.21.1 实体表）"
                  " + 官方 zh_cn 语言文件",
        "entities": entries,
    })
    print(f"     entities: {len(entries)}")


def build_enchantments(lang: dict) -> None:
    old = existing_entries("enchantments.json", "enchantments")
    try:
        raw = load_json(RAW / "enchantments.json")
    except Exception:
        raw = []
    entries: dict[str, dict] = {}
    for ench in raw:
        short = str(ench.get("name", ""))
        if not short:
            continue
        entries[short] = {
            "id": f"minecraft:{short}",
            "display": zh_name(lang, short, "enchantment",
                               str(ench.get("displayName", short))),
            "max": int(ench.get("maxLevel", 1) or 1),
        }
    # 语言文件里的附魔（补上注册表没覆盖的）
    for key, value in lang.items():
        if not key.startswith("enchantment.minecraft."):
            continue
        short = key.split(".", 2)[2]
        entries.setdefault(short, {
            "id": f"minecraft:{short}", "display": str(value), "max": 1})

    out: list[dict] = []
    for short, entry in sorted(entries.items(), key=lambda kv: kv[1]["display"]):
        old_entry = old.get(entry["id"], {})
        if old_entry.get("category"):
            entry["category"] = old_entry["category"]
        if old_entry.get("max"):
            entry["max"] = int(old_entry["max"])
        if old_entry.get("display") and not zh_name(
                lang, short, "enchantment", ""):
            entry["display"] = old_entry["display"]
        out.append(entry)
    # 手工补充但注册表/语言文件都没有的（理论上不会有，兜底保留）
    known = {e["id"] for e in out}
    for eid, old_entry in old.items():
        if eid not in known and old_entry.get("display"):
            out.append({"id": eid, "display": old_entry["display"],
                        "category": old_entry.get("category", ""),
                        "max": int(old_entry.get("max", 1) or 1)})

    write_json(BUILTIN / "enchantments.json", {
        "version": VERSION,
        "note": "附魔 ID → 中文名与最大等级，共 %d 条。" % len(out),
        "enchantments": out,
    })
    print(f"     enchantments: {len(out)}")


def main() -> int:
    if not RAW.exists() or not (RAW / "items.json").exists():
        print(f"缺少原始数据：{RAW}\n请先运行 python tools/fetch_registry.py")
        return 2
    lang = load_json(RAW / "zh_cn.json")
    print("生成内置数据：")
    build_items(lang)
    build_blocks(lang)
    build_entities(lang)
    build_enchantments(lang)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
