"""一次性脚本：修正 command_schemas.json 中与 1.21.1 语法不符、或会生成非法命令的参数定义。

历史脚本，schema 数据本身仍然有效、无需重建；重建注册表数据请用
tools/fetch_registry.py + tools/build_registry.py。

用法：python tools/fix_schemas.py
"""
from __future__ import annotations

import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "app" / "data" / "builtin" / "command_schemas.json"


def arg(key: str, **kw) -> dict:
    d = {"key": key}
    d.update(kw)
    return d


def find(data: dict, name: str) -> dict | None:
    for c in data["commands"]:
        if c.get("command") == name:
            return c
    return None


def main() -> int:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    changed: list[str] = []

    # ---- /time：value/amount 只在特定 action 下存在 ----
    if (c := find(data, "time")):
        c["args"] = [
            arg("action", label="操作", kind="enum",
                options=["set", "add", "query"], default="set", required=True),
            arg("value", label="时间点（set / query）", kind="text", default="",
                optional=True, when={"action": ["set", "query"]},
                hint="day / noon / night / midnight"),
            arg("amount", label="数量（add 时）", kind="int", default="1000",
                when={"action": ["add"]}),
        ]
        changed.append("time")

    # ---- /weather：duration 可选 ----
    if (c := find(data, "weather")):
        c["args"] = [
            arg("type", label="天气", kind="enum",
                options=["clear", "rain", "thunder"], default="clear", required=True),
            arg("duration", label="时长（秒，可选）", kind="int", default="",
                optional=True),
        ]
        changed.append("weather")

    # ---- /bossbar：按 action 拆分参数 ----
    if (c := find(data, "bossbar")):
        c["args"] = [
            arg("action", label="操作", kind="enum",
                options=["add", "remove", "get", "set", "list"], default="add",
                required=True),
            arg("id", label="ID", kind="text", default="minecraft:custom:name",
                required=True),
            arg("name", label="显示名（add 时）", kind="nbt",
                default="{\"text\":\"Boss\"}", when={"action": ["add"]}),
            arg("field", label="字段（set 时）", kind="enum",
                options=["name", "color", "style", "value", "max", "visible", "players"],
                default="value", when={"action": ["set"]}),
            arg("value", label="值（set 时）", kind="text", default="",
                when={"action": ["set"]}),
        ]
        changed.append("bossbar")

    # ---- /me：纯文本而非 NBT ----
    if (c := find(data, "me")):
        c["args"] = [
            arg("action", label="动作文本", kind="text", default="waves",
                required=True, hint="纯文本，不需要 JSON"),
        ]
        changed.append("me")

    # ---- /tp：destination 可能是 "~ ~ ~" / "1 2 3" / "facing 0 0" ----
    if (c := find(data, "tp")):
        c["args"] = [
            arg("targets", label="目标", kind="target", default="@p", required=True),
            arg("destination", label="目的地 / 朝向", kind="text", default="~ ~ ~",
                required=True, repeat=3,
                hint="坐标（3 段）或 facing <yaw> <pitch>"),
        ]
        changed.append("tp")

    # ---- /rotate：朝向是两段 ----
    if (c := find(data, "rotate")):
        c["args"] = [
            arg("target", label="目标", kind="target", default="@s", required=True),
            arg("rotation", label="朝向（yaw pitch）", kind="text", default="~ ~",
                required=True, repeat=2),
        ]
        changed.append("rotate")

    # ---- /forceload：坐标只在 add/remove 下出现 ----
    if (c := find(data, "forceload")):
        c["args"] = [
            arg("action", label="操作", kind="enum",
                options=["add", "remove", "query"], default="query", required=True),
            arg("from", label="起点", kind="coord", default="~ ~ ~",
                when={"action": ["add", "remove"]}),
            arg("to", label="终点（可省略）", kind="coord", default="",
                when={"action": ["add", "remove"]}),
        ]
        changed.append("forceload")

    # ---- /item：按 action 拆分 ----
    if (c := find(data, "item")):
        c["args"] = [
            arg("action", label="操作", kind="enum",
                options=["replace", "modify", "copy"], default="replace",
                required=True),
            arg("target", label="目标（坐标或选择器）", kind="text", default="~ ~ ~",
                required=True, repeat=3),
            arg("slot", label="槽位", kind="text", default="container.0",
                when={"action": ["replace", "modify", "copy"]}),
            arg("item", label="物品 ID", kind="item", default="minecraft:stone",
                when={"action": ["replace"]}),
            arg("count", label="数量", kind="int", default="1",
                when={"action": ["replace"]}),
            arg("modifier", label="修饰器（modify 时）", kind="text", default="",
                when={"action": ["modify"]}),
        ]
        changed.append("item")

    # ---- /playsound：1.21 参数顺序为 sound → source → targets ----
    if (c := find(data, "playsound")):
        c["args"] = [
            arg("sound", label="音效 ID", kind="text",
                default="minecraft:entity.player.levelup", required=True),
            arg("source", label="音源", kind="enum",
                options=["master", "music", "record", "weather", "block",
                         "hostile", "neutral", "player", "ambient", "voice"],
                default="master"),
            arg("target", label="目标", kind="target", default="@a"),
            arg("pos", label="位置", kind="coord", default="~ ~ ~"),
            arg("volume", label="音量", kind="float", default="1.0"),
            arg("pitch", label="音调", kind="float", default="1.0"),
        ]
        changed.append("playsound")

    # ---- /gamemode：1.21 顺序为 mode → target ----
    if (c := find(data, "gamemode")):
        c["args"] = [
            arg("mode", label="模式", kind="enum",
                options=["survival", "creative", "adventure", "spectator"],
                default="creative", required=True),
            arg("target", label="目标", kind="target", default="@s"),
        ]
        changed.append("gamemode")

    # ---- /xp：1.21 语法是 /experience <add|set|query> <targets> <amount> [points|levels] ----
    if (c := find(data, "xp")):
        c["args"] = [
            arg("action", label="操作", kind="enum",
                options=["add", "set", "query"], default="add", required=True),
            arg("target", label="目标", kind="target", default="@s", required=True),
            arg("amount", label="数量", kind="int", default="10",
                when={"action": ["add", "set"]}),
            arg("unit", label="单位", kind="enum",
                options=["points", "levels"], default="points",
                when={"action": ["add", "set"]}),
        ]
        changed.append("xp")

    # ---- /loot：source 只在特定 source_type 下出现 ----
    if (c := find(data, "loot")):
        c["args"] = [
            arg("action", label="操作", kind="enum",
                options=["give", "insert", "spawn", "replace", "fish"],
                default="give", required=True),
            arg("target", label="目标", kind="text", default="@p", required=True),
            arg("source_type", label="来源类型", kind="enum",
                options=["loot", "kill", "mine", "fish"], default="loot"),
            arg("source", label="来源（表 ID 或实体）", kind="text",
                default="minecraft:chests/simple_dungeon",
                when={"source_type": ["loot", "kill"]}),
        ]
        changed.append("loot")

    # ---- /schedule：clear 只需要函数名 ----
    if (c := find(data, "schedule")):
        c["args"] = [
            arg("action", label="操作", kind="enum",
                options=["function", "clear"], default="function", required=True),
            arg("name", label="函数 ID", kind="text", default="minecraft:example",
                required=True),
            arg("time", label="时间（如 1d / 1s）", kind="text", default="1s",
                when={"action": ["function"]}),
            arg("mode", label="模式", kind="enum",
                options=["replace", "append"], default="replace",
                when={"action": ["function"]}),
        ]
        changed.append("schedule")

    # ---- /title：times/reset 的语义不同，value 可选 ----
    if (c := find(data, "title")):
        c["args"] = [
            arg("target", label="目标", kind="target", default="@a", required=True),
            arg("action", label="类型", kind="enum",
                options=["title", "subtitle", "actionbar", "times", "reset"],
                default="title", required=True),
            arg("value", label="文本 / 时长", kind="nbt",
                default="{\"text\":\"Hello\"}",
                when={"action": ["title", "subtitle", "actionbar", "times"]}),
            arg("value2", label="第二时长（times 时）", kind="text", default="",
                when={"action": ["times"]}),
            arg("value3", label="第三时长（times 时）", kind="text", default="",
                when={"action": ["times"]}),
        ]
        changed.append("title")

    # ---- /worldborder：value 只在需要时出现 ----
    if (c := find(data, "worldborder")):
        c["args"] = [
            arg("action", label="操作", kind="enum",
                options=["add", "set", "center", "damage", "get", "warning"],
                default="get", required=True),
            arg("value", label="值", kind="text", default="",
                when={"action": ["add", "set", "center", "damage", "warning"]}),
        ]
        changed.append("worldborder")

    # ---- /difficulty / /gamemode 等没有变化，保持原样 ----

    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    print(f"已更新 {len(changed)} 条命令 schema：{', '.join(changed)}")
    print(f"输出：{PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
