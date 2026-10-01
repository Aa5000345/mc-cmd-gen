"""中文（及常用别名）→ Minecraft 命名空间 ID 解析。

数据来源优先级：
    1. 手工别名表 _ALIASES（覆盖简写、俗称、英文名）
    2. 内置数据文件的 display 字段（entities.json / items.json / blocks.json）
    3. 已经是合法 ID 的原样返回
"""
from __future__ import annotations

import re
from functools import lru_cache

# ---------------------------------------------------------------------------
# 手工别名：中文/俗称/英文 → 完整 ID
# ---------------------------------------------------------------------------

_ALIASES: dict[str, str] = {
    # 常见物品
    "钻石": "minecraft:diamond", "钻石块": "minecraft:diamond_block",
    "钻石剑": "minecraft:diamond_sword", "钻石镐": "minecraft:diamond_pickaxe",
    "钻石斧": "minecraft:diamond_axe", "钻石铲": "minecraft:diamond_shovel",
    "钻石锄": "minecraft:diamond_hoe", "钻石头盔": "minecraft:diamond_helmet",
    "钻石胸甲": "minecraft:diamond_chestplate", "钻石护腿": "minecraft:diamond_leggings",
    "钻石靴": "minecraft:diamond_boots",
    "铁": "minecraft:iron_ingot", "铁锭": "minecraft:iron_ingot",
    "金": "minecraft:gold_ingot", "金锭": "minecraft:gold_ingot",
    "铁剑": "minecraft:iron_sword", "铁镐": "minecraft:iron_pickaxe",
    "金苹果": "minecraft:golden_apple", "附魔金苹果": "minecraft:enchanted_golden_apple",
    "苹果": "minecraft:apple", "面包": "minecraft:bread",
    "熟牛排": "minecraft:cooked_beef", "生牛肉": "minecraft:beef",
    "熟鸡肉": "minecraft:cooked_chicken", "胡萝卜": "minecraft:carrot",
    "马铃薯": "minecraft:potato", "小麦": "minecraft:wheat",
    "木棍": "minecraft:stick", "棍子": "minecraft:stick",
    "石头": "minecraft:stone", "圆石": "minecraft:cobblestone",
    "泥土": "minecraft:dirt", "草方块": "minecraft:grass_block",
    "沙子": "minecraft:sand", "玻璃": "minecraft:glass",
    "木板": "minecraft:oak_planks", "原木": "minecraft:oak_log",
    "黑曜石": "minecraft:obsidian", "基岩": "minecraft:bedrock",
    "火把": "minecraft:torch", "灯笼": "minecraft:lantern",
    "箱子": "minecraft:chest", "木桶": "minecraft:barrel",
    "工作台": "minecraft:crafting_table", "熔炉": "minecraft:furnace",
    "附魔台": "minecraft:enchanting_table", "铁砧": "minecraft:anvil",
    "信标": "minecraft:beacon", "命令方块": "minecraft:command_block",
    "刷怪笼": "minecraft:spawner", "TNT": "minecraft:tnt",
    "弓": "minecraft:bow", "箭": "minecraft:arrow",
    "盾牌": "minecraft:shield", "鞘翅": "minecraft:elytra",
    "下界之星": "minecraft:nether_star", "下界合金锭": "minecraft:netherite_ingot",
    "末影珍珠": "minecraft:ender_pearl", "末影之眼": "minecraft:ender_eye",
    "经验瓶": "minecraft:experience_bottle",
    "书": "minecraft:book", "附魔书": "minecraft:enchanted_book",
    "纸": "minecraft:paper", "皮革": "minecraft:leather",
    "煤炭": "minecraft:coal", "木炭": "minecraft:charcoal",
    "红石": "minecraft:redstone", "青金石": "minecraft:lapis_lazuli",
    "绿宝石": "minecraft:emerald", "石英": "minecraft:quartz",
    "荧石粉": "minecraft:glowstone_dust", "火药": "minecraft:gunpowder",
    "骨头": "minecraft:bone", "线": "minecraft:string",
    "蜘蛛眼": "minecraft:spider_eye", "腐肉": "minecraft:rotten_flesh",
    "生鱼": "minecraft:cod", "熟鱼": "minecraft:cooked_cod",
    # 常见实体
    "僵尸": "minecraft:zombie", "尸壳": "minecraft:husk",
    "溺尸": "minecraft:drowned", "骷髅": "minecraft:skeleton",
    "骷髅马": "minecraft:skeleton_horse", "凋灵骷髅": "minecraft:wither_skeleton",
    "流浪者": "minecraft:stray", "苦力怕": "minecraft:creeper",
    "爬行者": "minecraft:creeper", "蜘蛛": "minecraft:spider",
    "洞穴蜘蛛": "minecraft:cave_spider", "末影人": "minecraft:enderman",
    "女巫": "minecraft:witch", "史莱姆": "minecraft:slime",
    "岩浆怪": "minecraft:magma_cube", "烈焰人": "minecraft:blaze",
    "恶魂": "minecraft:ghast", "幻翼": "minecraft:phantom",
    "掠夺者": "minecraft:pillager", "卫道士": "minecraft:vindicator",
    "唤魔者": "minecraft:evoker", "劫掠兽": "minecraft:ravager",
    "监守者": "minecraft:warden", "凋灵": "minecraft:wither",
    "末影龙": "minecraft:ender_dragon", "猪": "minecraft:pig",
    "牛": "minecraft:cow", "羊": "minecraft:sheep", "鸡": "minecraft:chicken",
    "兔子": "minecraft:rabbit", "马": "minecraft:horse", "驴": "minecraft:donkey",
    "狼": "minecraft:wolf", "狗": "minecraft:wolf", "猫": "minecraft:cat",
    "豹猫": "minecraft:ocelot", "狐狸": "minecraft:fox",
    "蜜蜂": "minecraft:bee", "北极熊": "minecraft:polar_bear",
    "熊猫": "minecraft:panda", "鹦鹉": "minecraft:parrot",
    "羊驼": "minecraft:llama", "海龟": "minecraft:turtle",
    "美西螈": "minecraft:axolotl", "青蛙": "minecraft:frog",
    "山羊": "minecraft:goat", "村民": "minecraft:villager",
    "流浪商人": "minecraft:wandering_trader", "铁傀儡": "minecraft:iron_golem",
    "雪傀儡": "minecraft:snow_golem", "盔甲架": "minecraft:armor_stand",
    "物品展示框": "minecraft:item_frame", "船": "minecraft:oak_boat",
    "矿车": "minecraft:minecart", "漏斗矿车": "minecraft:hopper_minecart",
    "TNT矿车": "minecraft:tnt_minecart", "经验球": "minecraft:experience_orb",
    # 状态效果
    "速度": "minecraft:speed", "迅捷": "minecraft:speed",
    "缓慢": "minecraft:slowness", "迟缓": "minecraft:slowness",
    "急迫": "minecraft:haste", "挖掘疲劳": "minecraft:mining_fatigue",
    "力量": "minecraft:strength", "瞬间治疗": "minecraft:instant_health",
    "瞬间伤害": "minecraft:instant_damage", "跳跃提升": "minecraft:jump_boost",
    "反胃": "minecraft:nausea", "生命恢复": "minecraft:regeneration",
    "抗性提升": "minecraft:resistance", "防火": "minecraft:fire_resistance",
    "水下呼吸": "minecraft:water_breathing", "隐身": "minecraft:invisibility",
    "失明": "minecraft:blindness", "夜视": "minecraft:night_vision",
    "饥饿": "minecraft:hunger", "虚弱": "minecraft:weakness",
    "中毒": "minecraft:poison", "凋零": "minecraft:wither",
    "生命提升": "minecraft:health_boost", "伤害吸收": "minecraft:absorption",
    "饱和": "minecraft:saturation", "发光": "minecraft:glowing",
    "漂浮": "minecraft:levitation", "幸运": "minecraft:luck",
    "不祥之兆": "minecraft:bad_omen", "村庄英雄": "minecraft:hero_of_the_village",
    # 维度 / 天气 / 难度 / 游戏模式
    "主世界": "minecraft:overworld", "下界": "minecraft:the_nether",
    "地狱": "minecraft:the_nether", "末地": "minecraft:the_end",
    "晴天": "clear", "下雨": "rain", "雨": "rain", "雷暴": "thunder",
    "雷雨": "thunder", "打雷": "thunder",
    "和平": "peaceful", "简单": "easy", "普通": "normal", "困难": "hard",
    "生存": "survival", "创造": "creative", "冒险": "adventure",
    "旁观": "spectator", "旁观者": "spectator",
    # 时间点
    "白天": "day", "正午": "noon", "夜晚": "night", "午夜": "midnight",
    # 附魔
    "锋利": "minecraft:sharpness", "亡灵杀手": "minecraft:smite",
    "节肢杀手": "minecraft:bane_of_arthropods", "击退": "minecraft:knockback",
    "火焰附加": "minecraft:fire_aspect", "抢夺": "minecraft:looting",
    "横扫之刃": "minecraft:sweeping_edge", "保护": "minecraft:protection",
    "火焰保护": "minecraft:fire_protection", "摔落保护": "minecraft:feather_falling",
    "爆炸保护": "minecraft:blast_protection", "弹射物保护": "minecraft:projectile_protection",
    "水下呼吸附魔": "minecraft:respiration", "水下速掘": "minecraft:aqua_affinity",
    "荆棘": "minecraft:thorns", "深海探索者": "minecraft:depth_strider",
    "冰霜行者": "minecraft:frost_walker", "绑定诅咒": "minecraft:binding_curse",
    "灵魂疾行": "minecraft:soul_speed", "迅捷潜行": "minecraft:swift_sneak",
    "效率": "minecraft:efficiency", "精准采集": "minecraft:silk_touch",
    "耐久": "minecraft:unbreaking", "时运": "minecraft:fortune",
    "力量附魔": "minecraft:power", "冲击": "minecraft:punch",
    "火矢": "minecraft:flame", "无限": "minecraft:infinity",
    "海之眷顾": "minecraft:luck_of_the_sea", "诱饵": "minecraft:lure",
    "忠诚": "minecraft:loyalty", "穿刺": "minecraft:impaling",
    "激流": "minecraft:riptide", "引雷": "minecraft:channeling",
    "多重射击": "minecraft:multishot", "快速装填": "minecraft:quick_charge",
    "穿透": "minecraft:piercing", "经验修补": "minecraft:mending",
    "消失诅咒": "minecraft:vanishing_curse",
    # 常用粒子
    "火焰": "minecraft:flame", "烟雾": "minecraft:smoke",
    "爆炸": "minecraft:explosion", "爱心": "minecraft:heart",
    "音符": "minecraft:note", "末影": "minecraft:portal",
    "闪电": "minecraft:electric_spark", "泡沫": "minecraft:bubble",
    "不死图腾": "minecraft:totem_of_undying", "云": "minecraft:cloud",
    # 常用音效
    "升级": "minecraft:entity.player.levelup", "受伤": "minecraft:entity.player.hurt",
    "爆炸声": "minecraft:entity.generic.explode", "点击": "minecraft:ui.button.click",
    "经验声": "minecraft:entity.experience_orb.pickup",
    "末影人传送": "minecraft:entity.enderman.teleport",
}

# 中文 → 英文实体的补充映射（用于 NLP 句式里出现"生成一只XX"）
_ENTITY_HINT = ("zombie", "skeleton", "creeper", "pig", "cow", "sheep",
                "chicken", "spider", "witch", "slime", "villager", "wolf",
                "cat", "horse", "bee", "panda", "fox", "rabbit", "blaze",
                "ghast", "warden", "enderman")

_ID_RE = re.compile(r"^[a-z0-9_.\-]+:[a-z0-9_./\-]+$")
_PLAIN_ID_RE = re.compile(r"^[a-z0-9_]+$")


@lru_cache(maxsize=1)
def _display_index() -> dict[str, str]:
    """从内置数据构建 display → id 索引。

    中文名会大量重名（「钻石矿石」既有石头版也有深板岩版，「唱片」有 20 多种），
    所以：
        * 先登记 blocks 再登记 items —— 查「煤炭矿石」时应给方块 ID 而不是物品；
        * 同一中文名保留「最短 ID」作为代表（`diamond_ore` 比
          `deepslate_diamond_ore` 更通用），其余靠别名表精确指向。
    """
    index: dict[str, str] = {}
    try:
        from app.core.data import store
    except Exception:                                  # pragma: no cover
        return index

    st = store()
    for getter in (st.blocks, st.entities, st.items):
        try:
            entries = getter()
        except Exception:
            continue
        for e in entries or []:
            if not isinstance(e, dict):
                continue
            eid = str(e.get("id", "")).strip()
            disp = str(e.get("display", "")).strip()
            if not eid or not disp:
                continue
            current = index.get(disp)
            if current is None or (len(eid), eid) < (len(current), current):
                index[disp] = eid
    return index


def clear_cache() -> None:
    _display_index.cache_clear()
    _candidates.cache_clear()


def resolve(name: str, default_namespace: str = "minecraft",
            kind: str = "any") -> str | None:
    """把中文名 / 别名 / 短 ID 解析成 命名空间:路径。无法识别返回 None。"""
    raw = (name or "").strip()
    if not raw:
        return None

    # 已经是完整 ID
    if _ID_RE.match(raw):
        return raw

    # 去掉可能的引号与空白
    cleaned = raw.strip('"\'` ')

    # 1) 精确匹配：手工别名 → 内置数据 display
    if cleaned in _ALIASES:
        return _ALIASES[cleaned]
    hit = _display_index().get(cleaned)
    if hit:
        return hit

    # 2) 包含匹配：在「别名」和「内置 display」里挑最贴合的一个。
    #    只看最长子串是不够的 —— 「深板岩钻石矿石」里既有「钻石」也有
    #    「深板岩钻石矿石」，必须比较「结尾对齐 + 覆盖率」，否则会退化。
    best: tuple[int, float, int, str] | None = None
    for candidate, target in _candidates():
        if candidate not in cleaned:
            continue
        suffix = cleaned.endswith(candidate)
        coverage = len(candidate) / max(1, len(cleaned))
        score = (1 if suffix else 0, coverage, len(candidate), target)
        if best is None or score[:3] > best[:3]:
            best = score
    if best is not None:
        return best[3]

    # 3) 英文短 ID（zombie → minecraft:zombie）
    low = cleaned.lower().replace(" ", "_")
    if _PLAIN_ID_RE.match(low):
        return f"{default_namespace}:{low}"

    return None


@lru_cache(maxsize=1)
def _candidates() -> tuple[tuple[str, str], ...]:
    """包含匹配的候选表：别名 + 内置中文名，按名字长度降序（便于快速命中）。"""
    merged: dict[str, str] = {}
    for disp, eid in _display_index().items():
        merged.setdefault(disp, eid)
    for alias, eid in _ALIASES.items():
        merged.setdefault(alias, eid)
    # 单字只用于整句就是那个字的场景（如「马」），否则「一匹白马」会误判
    return tuple(sorted(((name, eid) for name, eid in merged.items()
                         if len(name) >= 2),
                        key=lambda kv: len(kv[0]), reverse=True))


def resolve_or_raw(name: str, default_namespace: str = "minecraft") -> str:
    """解析失败时返回原值（保留用户手输的 ID）。"""
    return resolve(name, default_namespace) or (name or "").strip()


def is_id(text: str) -> bool:
    return bool(_ID_RE.match((text or "").strip()))


def alias_count() -> int:
    return len(_ALIASES)


# 对外暴露的只读别名表（供 UI 展示/统计）
ALIASES = _ALIASES

__all__ = ["resolve", "resolve_or_raw", "is_id", "clear_cache", "alias_count",
           "ALIASES"]
