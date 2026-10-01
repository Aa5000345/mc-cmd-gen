"""一次性脚本：把内置数据修正到 Minecraft Java 1.21.1 语法。

历史脚本，保留是为了在旧数据文件上打补丁。**日常重建数据请用**
`tools/fetch_registry.py` + `tools/build_registry.py`（会从官方注册表重新生成
items / blocks / entities / enchantments，并保留人工润色的中文名与标签）。

用法：python tools/fix_data.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILTIN = ROOT / "app" / "data" / "builtin"

ATTRIBUTE_MAP = {
    "minecraft:generic.max_health": "minecraft:max_health",
    "minecraft:generic.movement_speed": "minecraft:movement_speed",
    "minecraft:generic.attack_damage": "minecraft:attack_damage",
    "minecraft:generic.armor": "minecraft:armor",
    "minecraft:generic.armor_toughness": "minecraft:armor_toughness",
    "minecraft:generic.attack_speed": "minecraft:attack_speed",
    "minecraft:generic.knockback_resistance": "minecraft:knockback_resistance",
    "minecraft:generic.luck": "minecraft:luck",
    "minecraft:generic.follow_range": "minecraft:follow_range",
}


def fix_templates() -> list[str]:
    path = BUILTIN / "templates.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    notes: list[str] = []
    mapping = {
        "entity.attributes.full": (
            "{\"attributes\":["
            "{\"id\":\"minecraft:max_health\",\"base\":20.0},"
            "{\"id\":\"minecraft:attack_damage\",\"base\":3.0},"
            "{\"id\":\"minecraft:movement_speed\",\"base\":0.25}]}",
            "实体属性三元组：1.21 属性名去 generic. 前缀"),
        "entity.attributes.max_health": (
            "{\"attributes\":[{\"id\":\"minecraft:max_health\",\"base\":20.0}]}",
            "最大生命：1.21 属性名去 generic. 前缀"),
        "entity.attributes.speed": (
            "{\"attributes\":[{\"id\":\"minecraft:movement_speed\",\"base\":0.3}]}",
            "移动速度：1.21 属性名去 generic. 前缀"),
        "entity.hand_items.mainhand": (
            "{\"HandItems\":[{\"id\":\"minecraft:diamond_sword\",\"count\":1},{}],"
            "\"equipment\":{\"mainhand\":{\"id\":\"minecraft:diamond_sword\",\"count\":1}}}",
            "主手物品：1.20.5+ 用 equipment 组件，保留 HandItems 兼容旧版本"),
        "entity.armor_items.full": (
            "{\"equipment\":{"
            "\"head\":{\"id\":\"minecraft:diamond_helmet\",\"count\":1},"
            "\"chest\":{\"id\":\"minecraft:diamond_chestplate\",\"count\":1},"
            "\"legs\":{\"id\":\"minecraft:diamond_leggings\",\"count\":1},"
            "\"feet\":{\"id\":\"minecraft:diamond_boots\",\"count\":1}}}",
            "全套盔甲：1.20.5+ 用 equipment 组件"),
        "block.container.chest": (
            "{\"Items\":["
            "{\"Slot\":0,\"id\":\"minecraft:diamond\",\"count\":64},"
            "{\"Slot\":1,\"id\":\"minecraft:gold_ingot\",\"count\":16},"
            "{\"Slot\":2,\"id\":\"minecraft:apple\",\"count\":32}]}",
            "箱子物品：1.20.5+ 槽位是小写 slot，物品字段为 id/count"),
        "item.attribute_modifiers.attack": (
            "{\"minecraft:attribute_modifiers\":[{\"type\":\"minecraft:attack_damage\","
            "\"amount\":5.0,\"operation\":\"add_value\",\"slot\":\"mainhand\","
            "\"id\":\"example:attack_bonus\"}]}",
            "attribute_modifiers：属性名去 generic. 前缀"),
        "villager.offers.librarian": (
            "{\"Offers\":{\"Recipes\":[{\"buy\":{\"id\":\"minecraft:emerald\",\"count\":1},"
            "\"sell\":{\"id\":\"minecraft:enchanted_book\",\"count\":1},"
            "\"maxUses\":12,\"rewardExp\":1b}]}}",
            "村民交易：物品结构为 id/count"),
        "entity.tags": ("{\"Tags\":[\"demo\",\"stage7\"]}", "实体标签，保持 1.21 语法"),
    }

    for tpl in data.get("templates", []):
        tid = tpl.get("id")
        if tid in mapping:
            snbt, note = mapping[tid]
            tpl["snbt"] = snbt
            notes.append(f"{tid}: {note}")

    # 追加一批 1.21.1 新增组件模板
    extra = [
        {"id": "item.food.component", "name": "物品组件 — 食物属性",
         "category": "物品组件",
         "snbt": "{\"minecraft:food\":{\"nutrition\":4,\"saturation\":2.0}}"},
        {"id": "item.trim.component", "name": "物品组件 — 盔甲纹饰",
         "category": "物品组件",
         "snbt": "{\"minecraft:trim\":{\"material\":\"minecraft:gold\","
                 "\"pattern\":\"minecraft:silence\"}}"},
        {"id": "item.tool.component", "name": "物品组件 — 工具挖掘速度",
         "category": "物品组件",
         "snbt": "{\"minecraft:tool\":{\"rules\":[{\"blocks\":\"#minecraft:mineable/pickaxe\","
                 "\"speed\":8.0}],\"default_mining_speed\":1.0,\"damage_per_block\":1}}"},
        {"id": "item.custom_data.component", "name": "物品组件 — 自定义数据",
         "category": "物品组件",
         "snbt": "{\"minecraft:custom_data\":{\"myplugin\":{\"level\":3}}}"},
        {"id": "entity.equipment.full", "name": "实体装备 — equipment 组件",
         "category": "实体",
         "snbt": "{\"equipment\":{"
                 "\"head\":{\"id\":\"minecraft:golden_helmet\",\"count\":1},"
                 "\"mainhand\":{\"id\":\"minecraft:golden_sword\",\"count\":1}}}"},
        {"id": "block.container.new_slot", "name": "方块容器 — 1.21 物品结构",
         "category": "方块",
         "snbt": "{\"Items\":[{\"Slot\":0,\"id\":\"minecraft:stone\",\"count\":1,"
                 "\"components\":{\"minecraft:custom_name\":\"\\\"石头\\\"\"}}]}"},
    ]
    have = {t.get("id") for t in data.get("templates", [])}
    for t in extra:
        if t["id"] not in have:
            data["templates"].append(t)
            notes.append(f"新增模板 {t['id']}")

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return notes


def fix_item_components() -> list[str]:
    path = BUILTIN / "item_components.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    notes: list[str] = []
    fixes = {
        "minecraft:dyed_color": {
            "kind": "nbt", "default": "{\"rgb\":16711680}",
            "hint": "{\"rgb\":<十进制 RGB>}，1.21 为 compound"},
        "minecraft:custom_name": {
            "kind": "nbt", "default": "\"重命名物品\"",
            "hint": "JSON 文本组件或纯字符串"},
        "minecraft:lore": {"kind": "nbt", "default": "[\"第一行\",\"第二行\"]"},
        "minecraft:unbreakable": {"kind": "bool", "default": "1b"},
        "minecraft:enchantments": {
            "kind": "nbt", "default": "{\"levels\":{\"minecraft:sharpness\":5}}",
            "hint": "1.21 为 {levels:{<附魔>:<等级>}}"},
        "minecraft:stored_enchantments": {
            "kind": "nbt", "default": "{\"levels\":{\"minecraft:sharpness\":5}}"},
        "minecraft:attribute_modifiers": {
            "kind": "nbt",
            "default": "[{\"type\":\"minecraft:attack_damage\",\"amount\":5.0,"
                       "\"operation\":\"add_value\",\"slot\":\"mainhand\","
                       "\"id\":\"example:bonus\"}]"},
        "minecraft:max_stack_size": {"kind": "int", "default": "64"},
        "minecraft:rarity": {"kind": "enum", "default": "common"},
        "minecraft:food": {
            "kind": "nbt", "default": "{\"nutrition\":4,\"saturation\":2.0}"},
        "minecraft:tool": {
            "kind": "nbt",
            "default": "{\"rules\":[{\"blocks\":\"#minecraft:mineable/pickaxe\","
                       "\"speed\":8.0}],\"default_mining_speed\":1.0,"
                       "\"damage_per_block\":1}"},
        "minecraft:custom_data": {
            "kind": "nbt", "default": "{\"myplugin\":{\"level\":3}}"},
        "minecraft:fire_resistant": {"kind": "bool", "default": "1b"},
        "minecraft:glider": {"kind": "bool", "default": "1b"},
    }
    for comp in data.get("components", []):
        cid = comp.get("id")
        if cid in fixes:
            comp.update(fixes[cid])
            notes.append(f"{cid} 已按 1.21.1 修正")

    # 追加 1.21.1 常用组件
    extra = [
        {"id": "minecraft:max_damage", "display": "最大耐久", "kind": "int",
         "default": "2031"},
        {"id": "minecraft:tooltip_style", "display": "提示框样式", "kind": "text",
         "default": "minecraft:default"},
        {"id": "minecraft:item_name", "display": "物品名（优先于 custom_name）",
         "kind": "nbt", "default": "\"物品名\""},
        {"id": "minecraft:enchantment_glint_override", "display": "附魔光效覆盖",
         "kind": "bool", "default": "0b"},
        {"id": "minecraft:container", "display": "容器内容（潜影盒等）",
         "kind": "nbt",
         "default": "[{\"slot\":0,\"item\":{\"id\":\"minecraft:stone\",\"count\":1}}]"},
        {"id": "minecraft:recipes", "display": "合成配方解锁", "kind": "nbt",
         "default": "[\"minecraft:stick\"]"},
    ]
    have = {c.get("id") for c in data.get("components", [])}
    for c in extra:
        if c["id"] not in have:
            data["components"].append(c)
            notes.append(f"新增组件 {c['id']}")

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return notes


def fix_schemas_attributes() -> list[str]:
    path = BUILTIN / "command_schemas.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    notes: list[str] = []
    for cmd in data.get("commands", []):
        for a in cmd.get("args", []):
            if a.get("kind") == "text" and isinstance(a.get("default"), str):
                new = ATTRIBUTE_MAP.get(a["default"])
                if new:
                    notes.append(f"/{cmd['command']} {a['key']}: {a['default']} → {new}")
                    a["default"] = new
            if a.get("key") == "attribute" and a.get("default") in ATTRIBUTE_MAP:
                new = ATTRIBUTE_MAP[a["default"]]
                notes.append(f"/{cmd['command']} attribute: → {new}")
                a["default"] = new
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return notes


def add_blocks() -> list[str]:
    """新增 blocks.json（供方块选择器与批量面板使用）。"""
    path = BUILTIN / "blocks.json"
    if path.exists():
        return ["blocks.json 已存在，跳过"]
    blocks = [
        ("minecraft:stone", "石头", "建筑方块"),
        ("minecraft:cobblestone", "圆石", "建筑方块"),
        ("minecraft:stone_bricks", "石砖", "建筑方块"),
        ("minecraft:deepslate_bricks", "深板岩砖", "建筑方块"),
        ("minecraft:oak_planks", "橡木木板", "建筑方块"),
        ("minecraft:spruce_planks", "云杉木板", "建筑方块"),
        ("minecraft:oak_log", "橡木原木", "建筑方块"),
        ("minecraft:glass", "玻璃", "建筑方块"),
        ("minecraft:glass_pane", "玻璃板", "建筑方块"),
        ("minecraft:white_concrete", "白色混凝土", "建筑方块"),
        ("minecraft:red_concrete", "红色混凝土", "建筑方块"),
        ("minecraft:blue_concrete", "蓝色混凝土", "建筑方块"),
        ("minecraft:terracotta", "陶瓦", "建筑方块"),
        ("minecraft:quartz_block", "石英块", "建筑方块"),
        ("minecraft:prismarine", "海晶石", "建筑方块"),
        ("minecraft:sea_lantern", "海晶灯", "光源"),
        ("minecraft:glowstone", "荧石", "光源"),
        ("minecraft:torch", "火把", "光源"),
        ("minecraft:lantern", "灯笼", "光源"),
        ("minecraft:soul_lantern", "灵魂灯笼", "光源"),
        ("minecraft:redstone_lamp", "红石灯", "光源"),
        ("minecraft:shroomlight", "菌光体", "光源"),
        ("minecraft:water", "水", "流体"),
        ("minecraft:lava", "岩浆", "流体"),
        ("minecraft:chest", "箱子", "功能方块"),
        ("minecraft:barrel", "木桶", "功能方块"),
        ("minecraft:hopper", "漏斗", "功能方块"),
        ("minecraft:furnace", "熔炉", "功能方块"),
        ("minecraft:crafting_table", "工作台", "功能方块"),
        ("minecraft:anvil", "铁砧", "功能方块"),
        ("minecraft:enchanting_table", "附魔台", "功能方块"),
        ("minecraft:beacon", "信标", "功能方块"),
        ("minecraft:command_block", "命令方块", "功能方块"),
        ("minecraft:chain_command_block", "连锁命令方块", "功能方块"),
        ("minecraft:repeating_command_block", "循环命令方块", "功能方块"),
        ("minecraft:structure_block", "结构方块", "功能方块"),
        ("minecraft:barrier", "屏障", "功能方块"),
        ("minecraft:light", "光源方块", "功能方块"),
        ("minecraft:spawner", "刷怪笼", "功能方块"),
        ("minecraft:obsidian", "黑曜石", "装饰方块"),
        ("minecraft:crying_obsidian", "哭泣的黑曜石", "装饰方块"),
        ("minecraft:bedrock", "基岩", "装饰方块"),
        ("minecraft:diamond_block", "钻石块", "矿物方块"),
        ("minecraft:gold_block", "金块", "矿物方块"),
        ("minecraft:iron_block", "铁块", "矿物方块"),
        ("minecraft:emerald_block", "绿宝石块", "矿物方块"),
        ("minecraft:netherite_block", "下界合金块", "矿物方块"),
        ("minecraft:redstone_block", "红石块", "矿物方块"),
        ("minecraft:lapis_block", "青金石块", "矿物方块"),
        ("minecraft:coal_block", "煤炭块", "矿物方块"),
        ("minecraft:tnt", "TNT", "红石"),
        ("minecraft:redstone_wire", "红石线", "红石"),
        ("minecraft:repeater", "中继器", "红石"),
        ("minecraft:comparator", "比较器", "红石"),
        ("minecraft:piston", "活塞", "红石"),
        ("minecraft:sticky_piston", "黏性活塞", "红石"),
        ("minecraft:observer", "侦测器", "红石"),
        ("minecraft:lever", "拉杆", "红石"),
        ("minecraft:stone_button", "石按钮", "红石"),
        ("minecraft:oak_pressure_plate", "橡木压力板", "红石"),
    ]
    payload = {"version": "1.21.1",
               "blocks": [{"id": i, "display": d, "category": c} for i, d, c in blocks]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return [f"新增 blocks.json（{len(blocks)} 个方块）"]


def main() -> int:
    all_notes = []
    for fn in (fix_schemas_attributes, fix_templates, fix_item_components, add_blocks):
        notes = fn()
        all_notes.extend(notes)
        print(f"--- {fn.__name__}: {len(notes)} 项 ---")
        for n in notes:
            print("   ", n)
    print(f"\n共 {len(all_notes)} 项数据修正完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
