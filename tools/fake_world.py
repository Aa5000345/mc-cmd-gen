"""为测试与冒烟脚本生成一个可用的假世界存档（不依赖真实游戏）。

用法（Python）：

    from tools.fake_world import build_fake_world
    path = build_fake_world("build/fakeworld")

生成内容：
    level.dat（gzip，无名根，Data 下带完整世界字段）
    level.dat_old
    playerdata/<uuid>.dat（背包 / 末影箱 / 生命 / 经验 / 属性 / 效果）
    stats/<uuid>.json, advancements/<uuid>.json
    data/map_0.dat
    region/r.0.0.mca（真的写一个区块，用来验证区域索引）
    datapacks/example/{pack.mcmeta,data/example/function/hello.mcfunction}
"""
from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.nbt_binary import node_to_nbt_bytes, save_nbt_file  # noqa: E402
from app.core.snbt import SnbtParser                              # noqa: E402

PLAYER_UUID = "11111111-2222-3333-4444-555555555555"
PLAYER_UUID_COMPACT = PLAYER_UUID.replace("-", "")

#: 区域文件扇区大小
SECTOR_SIZE = 4096

#: UUID 的 IntArray 写法（四段 32 位整数，等价于 PLAYER_UUID）
UUID_INT_ARRAY_TEXT = "[I; 0x11111111, 0x22223333, 0x44445555, 0x55555555]"

LEVEL_SNBT = """{
  Data: {
    version: 19133,
    DataVersion: 3955,
    Version: {Id: 3955, Name: "1.21.1", Snapshot: 0b},
    LevelName: "测试世界",
    LastPlayed: 1735689600000L,
    GameType: 1,
    hardcore: 0b,
    Difficulty: 2b,
    DifficultyLocked: 0b,
    allowCommands: 1b,
    initialized: 1b,
    RandomSeed: 1234567890123L,
    Time: 24000L,
    DayTime: 6000L,
    raining: 0b,
    rainTime: 12000,
    thundering: 0b,
    thunderTime: 6000,
    clearWeatherTime: 0,
    SpawnX: 8, SpawnY: 65, SpawnZ: 8,
    WorldBorder: {
      CenterX: 0.0d, CenterZ: 0.0d, Size: 59999968.0d,
      DamageAmount: 0.2d, DamageBuffer: 5.0d,
      WarningTime: 15, WarningBlocks: 5,
      SafeZone: 5.0d, SizeLerpTime: 0L, SizeLerpTarget: 59999968.0d
    },
    GameRules: {
      doDaylightCycle: "true",
      doWeatherCycle: "true",
      doMobSpawning: "true",
      keepInventory: "false",
      randomTickSpeed: "3",
      spawnRadius: "10"
    },
    DataPacks: {Enabled: ["vanilla", "file/example"], Disabled: []},
    Player: {
      UUID: [I; 0x11111111, 0x22223333, 0x44445555, 0x55555555],
      Health: 20.0f, foodLevel: 20, XpLevel: 5, XpTotal: 120,
      playerGameType: 1, Dimension: "minecraft:overworld",
      Pos: [0.5d, 65.0d, 0.5d],
      Rotation: [0.0f, 0.0f],
      Inventory: [
        {Slot: 0b, id: "minecraft:diamond_sword", count: 1,
         components: {"minecraft:custom_name": '{"text":"神剑"}',
                      "minecraft:enchantments": {levels: {"minecraft:sharpness": 5}}}},
        {Slot: 1b, id: "minecraft:stone", count: 64}
      ],
      EnderItems: [{Slot: 0b, id: "minecraft:elytra", count: 1}],
      Attributes: [
        {Name: "minecraft:generic.max_health", Base: 20.0d},
        {Name: "minecraft:generic.movement_speed", Base: 0.1d}
      ],
      active_effects: [
        {id: "minecraft:night_vision", amplifier: 0b, duration: 1200, ambient: 0b}
      ]
    }
  },
  fml: {LoadingModList: [{ModId: "create", ModSize: 4096}]}
}"""


def build_fake_world(root: str | Path = "build/fakeworld",
                     *, with_region: bool = True) -> Path:
    root = Path(root)
    if root.exists():
        import shutil
        shutil.rmtree(root)
    (root / "playerdata").mkdir(parents=True, exist_ok=True)
    (root / "stats").mkdir(parents=True, exist_ok=True)
    (root / "advancements").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "region").mkdir(parents=True, exist_ok=True)
    (root / "datapacks" / "example" / "data" / "example" / "function") \
        .mkdir(parents=True, exist_ok=True)

    level = SnbtParser(LEVEL_SNBT).parse()
    save_nbt_file(level, root / "level.dat", compress="gzip", root_name="")
    save_nbt_file(level, root / "level.dat_old", compress="gzip", root_name="")

    # 玩家独立文件（去掉 level.dat 里的 UUID 写法差异）
    player = SnbtParser(LEVEL_SNBT).parse().get_child("Data").get_child("Player")
    player.add_child(SnbtParser('{Score: 0}').parse().children[0])
    save_nbt_file(player, root / "playerdata" / f"{PLAYER_UUID_COMPACT}.dat",
                  compress="gzip", root_name="")

    (root / "stats" / f"{PLAYER_UUID_COMPACT}.json").write_text(json.dumps({
        "stats": {
            "minecraft:custom": {"minecraft:play_time": 12345,
                                 "minecraft:deaths": 3},
            "minecraft:mined": {"minecraft:stone": 512},
        },
        "DataVersion": 3955,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "advancements" / f"{PLAYER_UUID_COMPACT}.json").write_text(
        json.dumps({
            "minecraft:story/mine_stone": {
                "criteria": {"get_stone": "2025-01-01 00:00:00 +0000"},
                "done": True},
            "minecraft:story/smelt_iron": {
                "criteria": {}, "done": False},
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    map_nbt = SnbtParser("""{
      data: {scale: 2b, locked: 1b, tracking: 1b, unlimitedTracking: 0b,
             dimension: "minecraft:overworld", xCenter: 32, zCenter: -16,
             colors: [B; 1, 2, 3, 4], banners: [], frames: []}
    }""").parse()
    save_nbt_file(map_nbt, root / "data" / "map_0.dat", compress="gzip",
                  root_name="")

    (root / "datapacks" / "example" / "pack.mcmeta").write_text(json.dumps({
        "pack": {"pack_format": 48, "description": "示例数据包"},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "datapacks" / "example" / "data" / "example" / "function"
     / "hello.mcfunction").write_text("say hello from example\n",
                                      encoding="utf-8")

    (root / "levelname.txt").write_text("测试世界", encoding="utf-8")
    if with_region:
        _write_region(root / "region" / "r.0.0.mca")
    return root


def _write_region(path: Path, *, sectors: int = 2) -> None:
    """写一个结构合法的区域文件（1 个区块，占 `sectors` 个扇区）。

    区块内容是**真的 NBT**（能解析的那种），并且留出多余扇区空间，
    这样「区域只读索引」「区块懒加载」「原地等长替换」都能在测试里跑通。
    """
    chunk = SnbtParser(
        '{DataVersion:3955,xPos:0,zPos:0,yPos:-4,'
        'Status:"minecraft:full",'
        'Entities:[{id:"minecraft:zombie",Health:20.0f,Pos:[0.5d,64.0d,0.5d],'
        'Tags:["fake-world"]}],'
        'block_entities:[{id:"minecraft:chest",x:1,y:64,z:1,'
        'Items:[{Slot:0b,id:"minecraft:diamond",count:3}]}],'
        'sections:[]}').parse()
    raw = node_to_nbt_bytes(chunk, compress="none", root_name="")
    payload = zlib.compress(raw)

    header = bytearray(8192)
    # 位置表第 0 项：3 字节起始扇区（2）+ 1 字节扇区数
    struct.pack_into(">I", header, 0, (2 << 8) | sectors)
    # 时间戳表第 0 项（偏移 4096）
    struct.pack_into(">I", header, 4096, 1735689600)
    body = bytearray(sectors * SECTOR_SIZE)
    # 区块数据：长度（含 1 字节压缩类型）+ 压缩类型（2 = zlib）+ 载荷
    struct.pack_into(">IB", body, 0, len(payload) + 1, 2)
    body[5:5 + len(payload)] = payload
    path.write_bytes(bytes(header) + bytes(body))


if __name__ == "__main__":
    target = build_fake_world(sys.argv[1] if len(sys.argv) > 1
                              else "build/fakeworld")
    print(f"已生成测试存档：{target}")
