#!/usr/bin/env python3
"""MCStudio（MC 工坊）命令行工具 —— 命令生成 + 世界 / NBT 编辑。

不启动 GUI 也能生成 / 迁移 / 查询命令、查看世界存档信息、按字段编辑
level.dat（默认 dry-run），方便脚本化与批量处理。

用法示例：
    python mcgen.py list
    python mcgen.py list --category 世界
    python mcgen.py gen setblock pos="~ ~ ~" block=minecraft:stone
    python mcgen.py gen time action=add amount=1000
    python mcgen.py give --target @a --item diamond_sword -c 'minecraft:custom_name="神剑"'
    python mcgen.py give --preset sword.sharp5
    python mcgen.py give --from "give @p stone" -c "minecraft:max_stack_size=99"
    python mcgen.py give-presets --list-presets
    python mcgen.py give-presets --search 附魔
    python mcgen.py nlp "给所有玩家10个钻石"
    python mcgen.py id 僵尸 钻石剑
    python mcgen.py migrate "give @p diamond_sword 1 {display:{Name:\"Excalibur\"}}"
    python mcgen.py validate "tp @p 1 2 3"
    python mcgen.py batch examples/batch.csv
    python mcgen.py world info  "…/saves/世界"
    python mcgen.py world edit  "…/saves/世界" DayTime=1000 --dry-run
    python mcgen.py world backups "…/saves/世界"
    python mcgen.py rename-info

`mcstudio.py` 是同一入口的新名字，两者完全等价。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _builders():
    from app.core import all_builders, get_builder, iter_builders
    return all_builders(), get_builder, iter_builders


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    _all, _get, iter_builders = _builders()
    rows = []
    for cls in iter_builders():
        if args.category and (cls.category or "其他") != args.category:
            continue
        rows.append((cls.command_name, cls.category or "其他", cls.display_name))
    rows.sort(key=lambda r: (r[1], r[0]))
    width = max((len(r[0]) for r in rows), default=8)
    for name, cat, display in rows:
        print(f"/{name:<{width}}  [{cat}]  {display}")
    print(f"\n共 {len(rows)} 条命令")
    if args.json:
        import json
        print(json.dumps(
            [{"command": n, "category": c, "display": d} for n, c, d in rows],
            ensure_ascii=False, indent=2))
    return 0


def _read_value(raw: str) -> str:
    """`@file` 形式从文件读取，其余原样返回（方便传递长 SNBT）。"""
    if raw.startswith("@"):
        path = Path(raw[1:])
        if not path.exists():
            raise FileNotFoundError(f"文件不存在：{path}")
        return path.read_text(encoding="utf-8").strip()
    return raw


def cmd_gen(args: argparse.Namespace) -> int:
    _all, get_builder, _iter = _builders()
    cls = get_builder(args.command.lstrip("/"))
    if cls is None:
        print(f"未知命令：/{args.command}", file=sys.stderr)
        return 2
    inst = cls()
    if not hasattr(inst, "values"):
        print(f"/{args.command} 不支持参数化生成", file=sys.stderr)
        return 2
    for pair in args.arg or []:
        if "=" not in pair:
            print(f"参数格式应为 key=value：{pair}", file=sys.stderr)
            return 2
        key, value = pair.split("=", 1)
        key = key.strip()
        if key not in inst.values:
            print(f"/{args.command} 没有参数 '{key}'；可用："
                  f"{', '.join(inst.values)}", file=sys.stderr)
            return 2
        try:
            inst.values[key] = _read_value(value)
        except OSError as e:
            print(str(e), file=sys.stderr)
            return 2
    command = inst.build()
    print(command)
    issues = inst.validate()
    if args.validate and issues:
        for issue in issues:
            level = getattr(issue, "level", "error")
            print(f"  [{level}] {issue}", file=sys.stderr)
        if any(getattr(i, "level", "error") == "error" for i in issues):
            return 1
    return 0


def cmd_give(args: argparse.Namespace) -> int:
    """`/give` 专用入口：组件可重复、可移除、可套用处方。"""
    from app.core.builders import GiveBuilder, parse_give_command
    from app.core.item_components import (
        MODE_REMOVE, MODE_SET, parse_components, short_id,
    )

    builder = GiveBuilder()
    notes: list[str] = []

    if args.from_command:
        try:
            state = parse_give_command(args.from_command)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        builder.load_state(state)

    if args.preset:
        chosen = _find_preset(args.preset)
        if chosen is None:
            print(f"找不到处方：{args.preset}（用 `give --list-presets` 查看）",
                  file=sys.stderr)
            return 2
        item = str(chosen.get("item", "")).strip()
        if item:
            builder.item = item
        count = chosen.get("count")
        if count is not None:
            try:
                builder.count = max(1, int(count))
                builder.use_count = True
            except (TypeError, ValueError):
                pass
        builder.components = parse_components(str(chosen.get("components", "")))
        notes.append(f"应用处方：{chosen.get('name', args.preset)}")

    if args.target:
        builder.target = args.target
    if args.item:
        builder.item = args.item
    if args.count is not None:
        if args.count < 1:
            print("数量至少为 1", file=sys.stderr)
            return 2
        builder.count = args.count
        builder.use_count = True

    for raw in args.component or []:
        if "=" not in raw:
            print(f"组件格式应为 id=value：{raw}", file=sys.stderr)
            return 2
        cid, value = raw.split("=", 1)
        try:
            value = _read_value(value)
        except OSError as e:
            print(str(e), file=sys.stderr)
            return 2
        builder.add_component(cid.strip(), value=value.strip(), mode=MODE_SET)

    for cid in args.remove or []:
        builder.add_component(cid.strip(), mode=MODE_REMOVE)

    if args.clear_components:
        builder.components = []

    command = builder.build()
    print(command)

    if args.show_components:
        for spec in builder.components:
            mark = "移除" if spec.removes else "设置"
            print(f"  {mark}  {short_id(spec.id):<28} {spec.value}",
                  file=sys.stderr)

    for note in notes:
        print(f"# {note}", file=sys.stderr)

    if args.validate or args.explain:
        # builder.validate() 已经包含组件值校验，这里不再重复
        levels = [(getattr(issue, "level", "error"), str(issue))
                  for issue in builder.validate()]
        if args.explain:
            for level, message in levels:
                print(f"  [{level}] {message}", file=sys.stderr)
            if not levels:
                print("  [ok] 没有发现问题", file=sys.stderr)
        if args.validate and any(level == "error" for level, _m in levels):
            return 1
    return 0


def _find_preset(key: str) -> dict | None:
    from app.core.data import store
    wanted = key.strip().lower()
    for preset in store().item_presets():
        if not isinstance(preset, dict):
            continue
        if wanted in (str(preset.get("id", "")).lower(),
                      str(preset.get("name", "")).lower()):
            return preset
    return None


def cmd_give_presets(args: argparse.Namespace) -> int:
    """列出物品处方 / 物品组件 ID。"""
    from app.core.data import store
    from app.core.item_components import component_list, short_id

    if args.list_presets:
        presets = [p for p in store().item_presets() if isinstance(p, dict)]
        if args.json:
            print(json.dumps(presets, ensure_ascii=False, indent=2))
            return 0
        width = max((len(str(p.get("id", ""))) for p in presets), default=8)
        for preset in presets:
            print(f"{str(preset.get('id', '')):<{width}}  "
                  f"[{preset.get('category', '')}] "
                  f"{preset.get('name', '')}  →  "
                  f"{preset.get('item', '')}")
        print(f"\n共 {len(presets)} 个处方")
        return 0

    entries = component_list()
    keyword = (args.search or "").strip().lower()
    rows = [e for e in entries
            if not keyword
            or keyword in str(e.get("id", "")).lower()
            or keyword in str(e.get("display", "")).lower()]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    width = max((len(short_id(str(e.get("id", "")))) for e in rows),
                default=10)
    for entry in rows:
        print(f"{short_id(str(entry.get('id', ''))):<{width}}  "
              f"{entry.get('display', '')}  |  "
              f"{str(entry.get('default', ''))[:48]}")
    print(f"\n共 {len(rows)} 个物品组件")
    return 0


def cmd_nlp(args: argparse.Namespace) -> int:
    from app.core.nlp.engine import parse, parse_with_pattern
    text = " ".join(args.text)
    hit = parse_with_pattern(text)
    if hit is None:
        print("无法识别的句式。可用示例：", file=sys.stderr)
        from app.core.nlp.engine import examples
        for s in examples(12):
            print(f"  {s}", file=sys.stderr)
        return 2
    print(parse(text) or hit[0])
    if args.verbose:
        print(f"  命中句式：{hit[1]['id']}（{hit[1]['desc']}）", file=sys.stderr)
        print(f"  解析字段：{hit[1]['values']}", file=sys.stderr)
    return 0


def cmd_id(args: argparse.Namespace) -> int:
    from app.core.ids import resolve
    rc = 0
    for name in args.names:
        hit = resolve(name)
        if hit:
            print(f"{name}\t{hit}")
        else:
            print(f"{name}\t(未识别)", file=sys.stderr)
            rc = 1
    return rc


def cmd_migrate(args: argparse.Namespace) -> int:
    from app.core.datapack import migrate_command
    text = " ".join(args.command) if args.command else sys.stdin.read().strip()
    if not text:
        print("没有输入命令。", file=sys.stderr)
        return 2
    lines = [ln for ln in text.splitlines() if ln.strip()]
    changed = 0
    for line in lines:
        result = migrate_command(line)
        print(result.migrated)
        if args.verbose:
            print(f"  # 检测版本 {result.detected}", file=sys.stderr)
            for a in result.applied:
                print(f"  # 应用：{a}", file=sys.stderr)
            for w in result.warnings:
                print(f"  # 提示：{w}", file=sys.stderr)
        changed += 1 if result.changed else 0
    if args.verbose:
        print(f"# 共 {len(lines)} 条，其中 {changed} 条被修改", file=sys.stderr)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    from app.core.rules import validate_text
    text = " ".join(args.command) if args.command else sys.stdin.read().strip()
    if not text:
        return 2
    rc = 0
    for line in (ln for ln in text.splitlines() if ln.strip()):
        hits = validate_text(line)
        if not hits:
            print(f"OK   {line}")
            continue
        for level, msg in hits:
            print(f"{level.upper():5} {line}\n      → {msg}")
            if level == "error":
                rc = 1
    return rc


def cmd_batch(args: argparse.Namespace) -> int:
    _all, get_builder, _iter = _builders()
    path = Path(args.file)
    if not path.exists():
        print(f"文件不存在：{path}", file=sys.stderr)
        return 2
    with path.open("r", encoding=args.encoding, newline="") as f:
        reader = csv.DictReader(f)
        command = (args.command or "").strip()
        if not command and reader.fieldnames:
            command = reader.fieldnames[0] if reader.fieldnames[0] != "command" else ""
        out = []
        for row in reader:
            cmd_name = command or str(row.get("command", "")).strip()
            if not cmd_name:
                continue
            cls = get_builder(cmd_name.lstrip("/"))
            if cls is None:
                out.append(f"# 未知命令：{cmd_name}")
                continue
            inst = cls()
            if hasattr(inst, "values"):
                for key, value in row.items():
                    if key in inst.values and value is not None:
                        inst.values[key] = str(value)
            out.append(inst.build())
    text = "\n".join(out)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"已写出 {len(out)} 条命令到 {args.output}")
    else:
        print(text)
    return 0


# ---------------------------------------------------------------------------
# 世界存档相关（world / rename-info）
# ---------------------------------------------------------------------------

def _open_world_for_cli(target: str, *, read_only: bool = True):
    from app.core.world import open_world
    world = open_world(target, read_only=read_only)
    if world.errors:
        for message in world.errors:
            print(f"# 错误：{message}", file=sys.stderr)
    if not world.is_open():
        print(f"无法打开世界存档：{target}", file=sys.stderr)
        return None
    world.unlock() if not read_only else None
    return world


def cmd_world_info(args: argparse.Namespace) -> int:
    """打印世界总览（世界名 / 版本 / 规则 / 玩家 / 区域 / 地图）。"""
    world = _open_world_for_cli(args.path)
    if world is None:
        return 1
    overview = world.overview
    if args.json:
        payload = {
            "path": str(world.path),
            "name": overview.name,
            "data_version": overview.data_version,
            "version_label": overview.version_label,
            "seed": overview.seed,
            "difficulty": overview.difficulty,
            "allow_commands": overview.allow_commands,
            "hardcore": overview.hardcore,
            "game_type": overview.game_type,
            "day_time": overview.day_time,
            "weather": overview.weather,
            "spawn": list(overview.spawn),
            "world_border": overview.world_border,
            "last_played": overview.last_played,
            "gamerules": overview.gamerules,
            "datapacks": overview.datapacks,
            "players": [p.uuid for p in overview.players],
            "mods": overview.mods,
            "compatibility": overview.compatibility.message,
            "entries": overview.entries,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    width = max(len(k) for k, _v in overview.summary_rows())
    for key, value in overview.summary_rows():
        print(f"{key:<{width}}  {value}")
    if overview.gamerules:
        print("\n游戏规则：")
        for name in sorted(overview.gamerules):
            print(f"  {name} = {overview.gamerules[name]}")
    if world.players:
        print("\n玩家：")
        for uuid in sorted(world.players):
            print(f"  {world.players[uuid].summary()}")
    if world.regions:
        print("\n区域文件：")
        for region in world.regions.values():
            print(f"  {region.summary()}")
    if world.maps:
        print("\n地图：")
        for info in sorted(world.maps.values(), key=lambda m: m.index):
            print(f"  {info.summary()}")
    for warning in overview.warnings:
        print(f"# 警告：{warning}", file=sys.stderr)
    return 0


def cmd_world_edit(args: argparse.Namespace) -> int:
    """按字段编辑 level.dat；默认 dry-run，加 --apply 才写盘。

    无论哪种模式，修改都只发生在内存里：
    `--apply` 才会走事务写盘（备份 + 原子替换），否则算完差异就丢弃。
    """
    from app.core.snbt import SnbtKind
    from app.core.world import health_check, resolve_path
    from app.core.world_session import WorldSession

    session = WorldSession.open(args.path, read_only=False)
    world = session.world
    if world is None or not world.is_open():
        for message in (world.errors if world else ["无法打开"]):
            print(f"错误：{message}", file=sys.stderr)
        return 1
    session.unlock()

    operations: list[tuple[str, str]] = []
    for pair in args.field or []:
        if "=" not in pair:
            print(f"字段格式应为 路径=取值：{pair}", file=sys.stderr)
            return 2
        path, value = pair.split("=", 1)
        operations.append((path.strip(), value.strip()))

    if not operations:
        print("没有指定任何字段。示例：\n"
              "  mcgen.py world edit <存档> DayTime=1000\n"
              "  mcgen.py world edit <存档> GameRules.keepInventory=true\n"
              "  mcgen.py world edit <存档> SpawnX=0 SpawnY=64 SpawnZ=0",
              file=sys.stderr)
        return 2

    snapshot = session.capture()
    for path, value in operations:
        target = path if path.startswith("Data") else f"Data.{path}"
        node = resolve_path(world.level_root, target) \
            if world.level_root else None
        kind = node.kind if node is not None and not node.is_container else None
        if kind is None:
            kind = SnbtKind.STRING
        try:
            world.set_node_value("level", target, value, kind=kind,
                                 create=args.create)
        except Exception as e:
            session.restore(snapshot)
            print(f"无法设置 {path}：{e}", file=sys.stderr)
            return 1

    diff = session.diff_against(snapshot, "world edit")
    if not diff:
        session.restore(snapshot)
        print("# 没有变化。")
        return 0
    print(f"# 待写入 {len(diff)} 处差异")
    print(diff.text(limit=100))
    problems = session.validate()
    if problems:
        session.restore(snapshot)
        for problem in problems:
            print(f"# 校验失败：{problem}", file=sys.stderr)
        return 1
    if not args.apply:
        session.restore(snapshot)
        print("# dry-run：没有写入任何文件（加 --apply 才会写盘）")
        return 0

    # 统一走事务：备份 → 校验 → 原子写入 → 记撤销点 → 刷新写盘基准。
    # 旧实现自己 create_backup + world.save()，绕过了事务，于是没有撤销点、
    # 不写会话日志，同一个进程里再复用这个 session 时「有没有未写盘的改动」
    # 也会判错。
    result = session.transaction("world edit", snapshot=snapshot).commit(
        backup=not args.no_backup)
    if not result.ok:
        print(f"# 写入失败：{result.message}", file=sys.stderr)
        return 1
    if not result.applied:
        print("# 没有变化。")
        return 0
    if result.backup is not None:
        print(f"# 已备份到 backups/{result.backup.name}")
    elif args.no_backup:
        print("# 按 --no-backup 跳过备份")
    print(f"# 已写入 {len(result.written)} 个文件："
          + "、".join(p.name for p in result.written))
    if args.health:
        reopened = _open_world_for_cli(args.path)
        if reopened is not None:
            for issue in health_check(reopened):
                print(f"# [{issue.level}] {issue.title}：{issue.detail}")
    return 0


def cmd_world_health(args: argparse.Namespace) -> int:
    """世界健康检查。"""
    from app.core.world import health_check
    world = _open_world_for_cli(args.path)
    if world is None:
        return 1
    issues = health_check(world)
    if args.json:
        print(json.dumps([{"level": i.level, "title": i.title,
                           "detail": i.detail, "path": i.path}
                          for i in issues], ensure_ascii=False, indent=2))
    else:
        for issue in issues:
            print(issue)
    return 1 if any(i.level == "error" for i in issues) else 0


def cmd_world_backups(args: argparse.Namespace) -> int:
    """列出 / 回滚备份（世界时间线）。"""
    from app.core.world_session import WorldSession
    session = WorldSession.open(args.path, read_only=not args.restore)
    world = session.world
    if world is None or not world.is_open():
        print("无法打开世界存档。", file=sys.stderr)
        return 1
    if not args.restore:
        entries = session.list_backups()
        if not entries:
            print("还没有备份。")
            return 0
        for entry in entries:
            print(f"{entry['name']}  {entry.get('created_at', '')}  "
                  f"{entry.get('label', '')}  "
                  f"{len(entry.get('files', []))} 个文件")
        return 0
    session.unlock()
    try:
        restored = session.restore_backup(args.restore)
    except Exception as e:
        print(f"回滚失败：{e}", file=sys.stderr)
        return 1
    print("已回滚：" + "、".join(p.name for p in restored))
    return 0


def cmd_world_commands(args: argparse.Namespace) -> int:
    """把存档里的东西变成命令（物品 → /give，实体 → /summon，规则 → /gamerule）。"""
    from app.core import bridge
    from app.core.nbt_bridge import stack_to_command
    world = _open_world_for_cli(args.path)
    if world is None:
        return 1
    lines: list[str] = []
    if args.what in ("all", "items"):
        for uuid in sorted(world.players):
            root = world.player_root(uuid)
            if root is None:
                continue
            from app.core.nbt_bridge import collect_items
            for stack in collect_items(root):
                lines.append(f"# {world.players[uuid].display()} · {stack.path}")
                lines.append(stack_to_command(stack, args.target))
    if args.what in ("all", "entities"):
        from app.core.world import scan_world_entities
        for entity in scan_world_entities(world, limit_entities=args.limit):
            body = f" {entity.nbt}" if entity.nbt else ""
            lines.append(f"summon {entity.entity_id} ~ ~ ~{body}")
    if args.what in ("all", "rules"):
        lines.extend(bridge.world_rules_to_commands(world).commands)
    if args.what in ("all", "scene"):
        lines.extend([
            "time set 6000", "gamerule doDaylightCycle false",
            "weather clear 1000000", "gamerule doWeatherCycle false",
            "difficulty peaceful",
        ])
    text = "\n".join(lines)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"已写出 {len(lines)} 行到 {args.output}")
    else:
        print(text)
    return 0


def cmd_world_nlp(args: argparse.Namespace) -> int:
    """中文自然语言 → 世界操作（先 diff，加 --apply 才写）。"""
    from app.core import bridge
    from app.core.world_session import WorldSession
    text = " ".join(args.text)
    session = WorldSession.open(args.path, read_only=not args.apply)
    if session.world is None or not session.world.is_open():
        print("无法打开世界存档。", file=sys.stderr)
        return 1
    operations = bridge.parse_cn_world_request(text, session=session)
    if not operations:
        print("无法识别的世界编辑请求。可用示例：", file=sys.stderr)
        for sample in ("把时间设置为夜晚", "天气改成下雨", "难度改成困难",
                       "永久白天", "禁止下雨", "在出生点生成 5 只僵尸",
                       "把 Steve 的背包塞满钻石剑"):
            print(f"  {sample}", file=sys.stderr)
        return 2
    print(f"# 解析出 {len(operations)} 个操作：")
    for operation in operations:
        print(f"#   {operation.summary()}")
    if not args.apply:
        session.unlock()
        snapshot = session.capture()
        for operation in operations:
            runner = operation.runner or bridge.operation_runner(operation)
            try:
                runner(session)
            except Exception as e:
                session.restore(snapshot)
                print(f"# 无法应用：{e}", file=sys.stderr)
                return 1
        diff = session.diff_against(snapshot, text)
        print(diff.text(limit=100))
        session.restore(snapshot)
        print("# dry-run：没有写入任何文件（加 --apply 才会写盘）")
        return 0
    session.unlock()
    for operation in operations:
        result = bridge.apply_operation(session, operation)
        print(f"# {'✔' if result.ok else '✘'} {operation.title}："
              f"{result.message}")
        if not result.ok:
            return 1
    return 0


def cmd_world_convert(args: argparse.Namespace) -> int:
    """世界快照（时间线）：给当前 level.dat 拍一张快照。"""
    from app.core.world import snapshot_world
    world = _open_world_for_cli(args.path)
    if world is None:
        return 1
    folder = snapshot_world(world, label=args.label or "CLI 快照")
    if folder is None:
        print("无法创建快照。", file=sys.stderr)
        return 1
    print(f"已创建快照：{folder}")
    return 0


def cmd_world_presets(args: argparse.Namespace) -> int:
    """列出 / 套用 / 保存工作区预设。"""
    from app.core.region_io import (
        get_preset, list_presets, preset_summary, preset_to_operation,
    )
    from app.core.world_session import WorldSession

    if args.save:
        if args.path is None:
            print("保存预设需要同时给出存档路径（用 --from 更好）。",
                  file=sys.stderr)
            return 2
        session = WorldSession.open(args.path)
        world = session.world
        if world is None or not world.is_open():
            print("无法打开世界存档。", file=sys.stderr)
            return 1
        overview = world.overview
        payload = {
            "permanent_day": overview.gamerules.get("doDaylightCycle")
            == "false",
            "no_weather": overview.gamerules.get("doWeatherCycle") == "false",
            "peaceful": overview.difficulty == "peaceful",
            "allow_commands": overview.allow_commands,
            "difficulty": overview.difficulty,
            "gamerules": dict(overview.gamerules),
        }
        if overview.world_border:
            payload["worldborder"] = {
                "size": overview.world_border.get("size"),
                "damage": overview.world_border.get("damage")}
        from app.core.region_io import save_preset
        path = save_preset({"id": args.save, "name": args.save,
                            "note": f"由 {world.name} 保存", "payload": payload})
        print(f"已保存预设到 {path}")
        return 0

    if args.use:
        preset = get_preset(args.use)
        if preset is None:
            print(f"找不到预设：{args.use}", file=sys.stderr)
            return 2
        print(f"# 预设 {preset.get('id')}：{preset.get('name')}")
        print(f"# 内容：{preset_summary(preset)}")
        if args.path is None:
            print("# 未指定存档路径，只列出内容（加存档路径 + --apply 才会写）")
            return 0
        session = WorldSession.open(args.path, read_only=False)
        if session.world is None or not session.world.is_open():
            print("无法打开世界存档。", file=sys.stderr)
            return 1
        session.unlock()
        operation = preset_to_operation(preset)
        snapshot = session.capture()
        try:
            operation.runner(session)
        except Exception as e:
            session.restore(snapshot)
            print(f"无法应用：{e}", file=sys.stderr)
            return 1
        diff = session.diff_against(snapshot, operation.title)
        print(diff.text(limit=100))
        if not args.apply:
            session.restore(snapshot)
            print("# dry-run：没有写入任何文件（加 --apply 才会写盘）")
            return 0
        backup = session.world.create_backup(label=f"预设 {preset.get('id')}")
        written = session.world.save()
        # 关键：原生 save() 不会刷新会话的「写盘基准」，同一个进程里再问
        # 「有没有未写盘的改动」就会判错（GUI + CLI 混跑时尤其明显）
        session.set_baseline()
        session.log(f"预设 {preset.get('id')}", source="cli",
                    backup=backup, applied=True)
        print(f"# 已备份 {backup.name if backup else '(无需备份)'}；"
              f"写入 {len(written)} 个文件")
        return 0

    presets = list_presets()
    if args.json:
        print(json.dumps(presets, ensure_ascii=False, indent=2))
        return 0
    width = max((len(str(p.get("id", ""))) for p in presets), default=8)
    for preset in presets:
        source = "内置" if preset.get("builtin") else "用户"
        print(f"{str(preset.get('id', '')):<{width}}  [{source}]  "
              f"{preset.get('name', '')}  →  {preset_summary(preset)}")
    print(f"\n共 {len(presets)} 个预设")
    return 0


def cmd_world_region(args: argparse.Namespace) -> int:
    """区域区块只读索引 / dry-run / 写入（实验功能）。"""
    from app.core.region_io import (
        backup_region, plan_chunk_write, verify_region, write_chunk,
    )
    from app.core.snbt import SnbtParser
    from app.core.world import read_region_chunk

    path = Path(args.file)
    if not path.is_file():
        print(f"文件不存在：{path}", file=sys.stderr)
        return 2

    if args.list or (args.x is None and args.z is None):
        from app.core.world import read_region_index
        region = read_region_index(path)
        if region.error:
            print(f"读取失败：{region.error}", file=sys.stderr)
            return 1
        print(f"# {region.name} · {region.dimension or '—'} · "
              f"{region.present}/{len(region.chunks)} 区块")
        for chunk in region.chunks[:args.limit]:
            when = (world_mod_human(chunk.timestamp) if chunk.timestamp
                    else "—")
            print(f"{chunk.x:>8} {chunk.z:>8}  偏移 {chunk.offset:>6}  "
                  f"扇区 {chunk.sectors:>3}  {when}")
        if len(region.chunks) > args.limit:
            print(f"# … 另有 {len(region.chunks) - args.limit} 个区块")
        return 0

    node = read_region_chunk(path, args.x, args.z)
    if node is None and not args.snbt:
        print(f"区块 ({args.x},{args.z}) 不存在或读不出来。", file=sys.stderr)
        return 1

    if args.show:
        from app.core.snbt import serialize
        print(serialize(node, pretty=True) if node is not None
              else "（读不到区块）")
        return 0

    if args.snbt:
        try:
            if args.snbt.startswith("@"):
                text = Path(args.snbt[1:]).read_text(encoding="utf-8")
            else:
                text = args.snbt
            node = SnbtParser(text).parse()
        except Exception as e:
            print(f"NBT 解析失败：{e}", file=sys.stderr)
            return 1

    plan = plan_chunk_write(path, args.x, args.z, node,
                            compression=args.compression)
    print(f"# {plan.summary()}")
    if plan.diff:
        print("# 差异：")
        for entry in plan.diff[:args.limit]:
            print(f"#   {entry}")
    if not plan.ok:
        return 1
    if not args.apply:
        print("# dry-run：没有写入（加 --apply 才会写盘，写前会自动备份）")
        return 0

    folder = path.parent.parent / "backups" / (
        __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S"))
    folder.mkdir(parents=True, exist_ok=True)
    backup = backup_region(path, folder)
    write_chunk(path, args.x, args.z, node, compression=args.compression)
    problems = verify_region(path)
    print(f"# 已写入并自检：{'通过' if not problems else '；'.join(problems)}")
    print(f"# 备份：{backup}")
    return 0 if not problems else 1


def world_mod_human(timestamp: int) -> str:
    from app.core.world import human_time
    return human_time(timestamp * 1000)


def cmd_pack(args: argparse.Namespace) -> int:
    """数据包工程：列出 / 新建 / 导入 / 加函数 / 加标签 / 保存 / 自检。"""
    from app.core.datapack_project import (
        AuthoringPack, advancement_template, function_template, import_pack,
        loot_table_template, predicate_template,
    )
    from app.storage import user_data_dir

    root = Path(args.root) if args.root else (
        user_data_dir() / "datapacks" / (args.namespace or "mcstudio"))

    if args.import_from:
        try:
            pack = import_pack(Path(args.import_from), root)
        except Exception as e:
            print(f"导入失败：{e}", file=sys.stderr)
            return 1
        print(f"# 已导入到 {pack.root}")
    elif root.exists() and (root / "pack.mcmeta").is_file():
        pack = AuthoringPack.load(root)
    else:
        pack = AuthoringPack(root=root, namespace=args.namespace or "mcstudio")

    if args.function:
        commands: list[str] = []
        for item in args.function:
            if item.startswith("@"):
                text = Path(item[1:]).read_text(encoding="utf-8")
                commands.extend(text.splitlines())
            else:
                commands.append(item)
        if not commands:
            commands = function_template("main").splitlines()
        pack.add_from_commands(args.name or "main", commands)
    if args.advancement:
        pack.add_asset("advancement", args.advancement,
                       advancement_template(args.advancement))
    if args.loot_table:
        pack.add_asset("loot_table", args.loot_table, loot_table_template())
    if args.predicate:
        pack.add_asset("predicate", args.predicate, predicate_template())
    if args.tag_load:
        for name in args.tag_load:
            pack.add_to_tag("minecraft:load", f"{pack.namespace}:{name}")
    if args.tag_tick:
        for name in args.tag_tick:
            pack.add_to_tag("minecraft:tick", f"{pack.namespace}:{name}")

    if args.list:
        for kind in ("function", "advancement", "loot_table", "predicate"):
            assets = pack.by_kind(kind)
            print(f"{kind}（{len(assets)}）")
            for asset in assets:
                print(f"  {asset.name}{asset.suffix}  —  {asset.summary()}")
        for tag, entries in sorted(pack.tags.items()):
            print(f"tag {tag}（{len(entries)}）：{', '.join(entries)}")
        print(f"\n# {pack.summary()}")
        print(f"# 目录：{pack.root}")
        return 0

    if args.check:
        problems = pack.validate()
        for item in problems:
            print(f"# 问题：{item}", file=sys.stderr)
        print(f"# {pack.summary()}")
        print("# 自检：" + ("通过" if not problems else f"{len(problems)} 个问题"))
        return 1 if problems else 0

    if args.save:
        written = pack.save()
        print(f"# 已写入 {len(written)} 个文件到 {pack.root}")
        for path in written[:40]:
            print(f"  {path}")
        return 0

    print(f"# {pack.summary()}")
    print(f"# 目录：{pack.root}")
    print("# 提示：加 --save 才会写盘；--function/--advancement 用来加内容")
    return 0


def cmd_paint(args: argparse.Namespace) -> int:
    """用命令行编排一小块方块（与 GUI 画布同一套 BlockGrid）。"""
    from app.core.snbt import SnbtNode, SnbtKind, serialize
    from app.ui.world_creative import BlockGrid

    grid = BlockGrid()
    layer = args.y
    block = args.block
    if args.rect:
        try:
            x0, z0, x1, z1 = (int(float(part))
                              for part in args.rect.split(","))
        except ValueError:
            print("--rect 需要 x0,z0,x1,z1 四个数字", file=sys.stderr)
            return 2
        for x in range(min(x0, x1), max(x0, x1) + 1):
            for z in range(min(z0, z1), max(z0, z1) + 1):
                grid.set(x, layer, z, block)
    for point in args.point or []:
        try:
            x, z = (int(float(part)) for part in point.split(","))
        except ValueError:
            print(f"--point 需要 x,z 两个数字：{point}", file=sys.stderr)
            return 2
        grid.set(x, layer, z, block)

    if not len(grid):
        print("没有编排任何格子（用 --rect 或 --point 指定）", file=sys.stderr)
        return 2

    mode = args.mode
    if mode == "snbt":
        payload = SnbtNode.new_list(key="blocks")
        for (x, y, z), value in sorted(grid.cells.items()):
            entry = SnbtNode.new_compound()
            entry.add_child(SnbtNode.new_scalar(SnbtKind.INT, x, key="x"))
            entry.add_child(SnbtNode.new_scalar(SnbtKind.INT, y, key="y"))
            entry.add_child(SnbtNode.new_scalar(SnbtKind.INT, z, key="z"))
            entry.add_child(SnbtNode.new_scalar(SnbtKind.STRING, value,
                                                key="id"))
            payload.add_child(entry)
        text = serialize(payload, pretty=False)
    else:
        text = "\n".join(grid.to_commands(mode=mode))
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"# 已写出 {len(grid)} 格到 {args.output}")
    else:
        print(text)
    return 0


def cmd_region_batch(args: argparse.Namespace) -> int:
    """区域多区块批量替换（默认 dry-run）。"""
    from app.core.world import open_world
    from app.ui.world_region_batch import execute_batch, plan_batch

    world = open_world(args.path, read_only=False)
    if not world.is_open():
        for message in world.errors:
            print(f"错误：{message}", file=sys.stderr)
        return 1
    kwargs = {
        "operation": args.op,
        "path": args.field or "",
        "value": args.value or "",
        "search": args.search or "",
        "replace": args.replace or "",
        "data_version": int(args.data_version),
    }
    plan = plan_batch(world, **kwargs)
    print(plan.report(limit=args.limit))
    if not plan.writable:
        print("# 没有可写区块，未做任何改动。")
        return 0
    if not args.apply:
        print("# dry-run：没有写入（加 --apply 才会写盘，写前会备份）")
        return 0
    done, failures, folder = execute_batch(world, plan)
    print(f"# 已写入 {done} 个区块；备份 {folder.name if folder else '(无)'}")
    for item in failures[:20]:
        print(f"# 失败：{item}", file=sys.stderr)
    return 1 if failures else 0


def cmd_rename_info(args: argparse.Namespace) -> int:
    """打印项目改名说明（候选名称、影响、迁移方式）。"""
    from app import branding
    _ = args
    print(branding.rename_report())
    return 0



# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    from app import branding
    p = argparse.ArgumentParser(
        prog=branding.CLI_NAME,
        description=(f"{branding.APP_NAME}（{branding.APP_NAME_CN}）"
                     f" — Minecraft Java {branding.TARGET_MC_VERSION} "
                     "命令生成与世界 / NBT 编辑器（命令行版）"),
        epilog="旧命令名 mcgen 与新命令名 mcstudio 完全等价；"
               "`--version` 看版本，`rename-info` 看改名说明。")
    p.add_argument("--version", action="version",
                   version=f"{branding.APP_NAME} {branding.VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list", help="列出全部命令")
    sp.add_argument("--category", help="只显示某个分类")
    sp.add_argument("--json", action="store_true", help="同时输出 JSON")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("gen", help="按参数生成命令")
    sp.add_argument("command", help="命令名，如 setblock")
    sp.add_argument("arg", nargs="*", help="key=value 形式的参数")
    sp.add_argument("--validate", action="store_true", help="同时做校验")
    sp.set_defaults(func=cmd_gen)

    sp = sub.add_parser("give", help="生成 /give（组件可重复、可移除，支持处方）")
    sp.add_argument("--target", help="目标选择器，默认 @p")
    sp.add_argument("--item", help="物品 ID，可省略 minecraft: 前缀")
    sp.add_argument("--count", type=int, help="数量")
    sp.add_argument("--component", "-c", action="append", metavar="ID=VALUE",
                    help="添加组件，可重复；值支持 @文件")
    sp.add_argument("--remove", "-r", action="append", metavar="ID",
                    help="移除组件（生成 !ID），可重复")
    sp.add_argument("--preset", "-p", help="套用物品处方（见 --list-presets）")
    sp.add_argument("--from", dest="from_command",
                    help="在已有 /give 命令基础上修改")
    sp.add_argument("--clear-components", action="store_true",
                    help="先清空组件")
    sp.add_argument("--show-components", action="store_true",
                    help="在 stderr 列出组件明细")
    sp.add_argument("--validate", action="store_true", help="有 error 时返回 1")
    sp.add_argument("--explain", action="store_true",
                    help="打印全部校验/提示信息")
    sp.set_defaults(func=cmd_give)

    sp = sub.add_parser("give-presets", help="列出物品处方或物品组件 ID")
    sp.add_argument("--list-presets", action="store_true",
                    help="列出处方（默认列出物品组件）")
    sp.add_argument("--search", help="按 ID / 中文名过滤")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_give_presets)

    sp = sub.add_parser("nlp", help="中文句子 → 命令")
    sp.add_argument("text", nargs="+", help="中文描述")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.set_defaults(func=cmd_nlp)

    sp = sub.add_parser("id", help="中文名 → 命名空间 ID")
    sp.add_argument("names", nargs="+")
    sp.set_defaults(func=cmd_id)

    sp = sub.add_parser("migrate", help="旧版本命令迁移到 1.21.1")
    sp.add_argument("command", nargs="*", help="命令；省略则读标准输入")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.set_defaults(func=cmd_migrate)

    sp = sub.add_parser("validate", help="用校验规则检查命令")
    sp.add_argument("command", nargs="*")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("batch", help="CSV → 命令列表")
    sp.add_argument("file")
    sp.add_argument("--command", help="CSV 未包含命令名列时指定命令名")
    sp.add_argument("--encoding", default="utf-8-sig")
    sp.add_argument("-o", "--output", help="输出文件（默认打印到屏幕）")
    sp.set_defaults(func=cmd_batch)

    # ---- 世界存档 ----
    sp = sub.add_parser("world", help="世界存档：查看 / 编辑 / 体检 / 备份 / 转命令")
    world_sub = sp.add_subparsers(dest="world_cmd", required=True)

    w = world_sub.add_parser("info", help="打印世界总览")
    w.add_argument("path", help="存档目录（含 level.dat）")
    w.add_argument("--json", action="store_true")
    w.set_defaults(func=cmd_world_info)

    w = world_sub.add_parser("edit", help="按字段编辑 level.dat（默认 dry-run）")
    w.add_argument("path")
    w.add_argument("field", nargs="*", metavar="路径=取值",
                   help="例如 DayTime=1000 / GameRules.keepInventory=true")
    w.add_argument("--apply", action="store_true", help="真正写盘（会先备份）")
    w.add_argument("--no-backup", action="store_true", help="跳过备份（不建议）")
    w.add_argument("--create", action="store_true",
                   help="路径不存在时创建中间 Compound")
    w.add_argument("--health", action="store_true", help="写完后体检")
    w.set_defaults(func=cmd_world_edit)

    w = world_sub.add_parser("health", help="世界健康检查")
    w.add_argument("path")
    w.add_argument("--json", action="store_true")
    w.set_defaults(func=cmd_world_health)

    w = world_sub.add_parser("backups", help="列出备份 / 回滚（世界时间线）")
    w.add_argument("path")
    w.add_argument("--restore", metavar="备份名", help="回滚到指定备份")
    w.set_defaults(func=cmd_world_backups)

    w = world_sub.add_parser("commands", help="存档内容 → 命令")
    w.add_argument("path")
    w.add_argument("--what", choices=["all", "items", "entities", "rules",
                                      "scene"], default="all")
    w.add_argument("--target", default="@p", help="/give 的目标选择器")
    w.add_argument("--limit", type=int, default=500, help="实体数量上限")
    w.add_argument("-o", "--output", help="输出 .mcfunction")
    w.set_defaults(func=cmd_world_commands)

    w = world_sub.add_parser("nlp", help="中文自然语言 → 世界操作（默认 dry-run）")
    w.add_argument("path")
    w.add_argument("text", nargs="+")
    w.add_argument("--apply", action="store_true")
    w.set_defaults(func=cmd_world_nlp)

    w = world_sub.add_parser("snapshot", help="给当前存档拍一张快照")
    w.add_argument("path")
    w.add_argument("--label", default="")
    w.set_defaults(func=cmd_world_convert)

    w = world_sub.add_parser("presets", help="工作区预设：列出 / 套用 / 保存")
    w.add_argument("path", nargs="?", help="存档目录（套用 / 保存时需要）")
    w.add_argument("--use", metavar="预设", help="套用指定预设（默认 dry-run）")
    w.add_argument("--save", metavar="预设名", help="把当前世界设置存为预设")
    w.add_argument("--apply", action="store_true", help="真正写盘（会先备份）")
    w.add_argument("--json", action="store_true")
    w.set_defaults(func=cmd_world_presets)

    w = world_sub.add_parser(
        "region", help="区域文件：区块索引 / 读取 / 写入（实验功能）")
    w.add_argument("file", help="region/*.mca 文件")
    w.add_argument("--x", type=int, help="区块 X")
    w.add_argument("--z", type=int, help="区块 Z")
    w.add_argument("--list", action="store_true", help="只列区块索引（默认）")
    w.add_argument("--show", action="store_true", help="打印该区块的 NBT")
    w.add_argument("--snbt", help="要写入的 NBT 文本（支持 @文件）")
    w.add_argument("--compression", default="zlib",
                   choices=["zlib", "gzip", "none"])
    w.add_argument("--limit", type=int, default=200, help="最多列出多少条")
    w.add_argument("--apply", action="store_true",
                   help="真正写盘（写前自动备份 + 写后自检）")
    w.set_defaults(func=cmd_world_region)

    sp = sub.add_parser("rename-info", help="查看项目改名说明与迁移方式")
    sp.set_defaults(func=cmd_rename_info)

    # ---- 数据包工程 ----
    sp = sub.add_parser("pack", help="数据包工程：函数 / 进度 / 战利品表 / 标签")
    sp.add_argument("--root", help="工程目录（默认 user_data/datapacks/<命名空间>）")
    sp.add_argument("--namespace", default="mcstudio", help="命名空间")
    sp.add_argument("--import-from", metavar="目录",
                    help="把已有数据包复制进工程目录再处理")
    sp.add_argument("--function", action="append", metavar="命令或@文件",
                    help="往函数里加命令（可重复；也可以给 @文件）")
    sp.add_argument("--name", help="配合 --function 使用的函数名，默认 main")
    sp.add_argument("--advancement", metavar="名字", help="新建进度（模板）")
    sp.add_argument("--loot-table", dest="loot_table", metavar="名字",
                    help="新建战利品表（模板）")
    sp.add_argument("--predicate", metavar="名字", help="新建谓词（模板）")
    sp.add_argument("--tag-load", action="append", metavar="函数名",
                    help="把函数挂进 minecraft:load")
    sp.add_argument("--tag-tick", action="append", metavar="函数名",
                    help="把函数挂进 minecraft:tick")
    sp.add_argument("--list", action="store_true", help="列出工程内容")
    sp.add_argument("--check", action="store_true", help="自检（有问题返回 1）")
    sp.add_argument("--save", action="store_true", help="写盘")
    sp.set_defaults(func=cmd_pack)

    # ---- 方块编排（命令行版画布） ----
    sp = sub.add_parser("paint", help="编排一小块方块并生成命令 / SNBT")
    sp.add_argument("--rect", metavar="x0,z0,x1,z1", help="矩形区域")
    sp.add_argument("--point", action="append", metavar="x,z",
                    help="单个格子（可重复）")
    sp.add_argument("--y", type=int, default=64, help="Y 层，默认 64")
    sp.add_argument("--block", default="minecraft:stone", help="方块 ID")
    sp.add_argument("--mode", default="setblock",
                    choices=["setblock", "fill", "snbt"],
                    help="输出形式（fill 会做矩形分解）")
    sp.add_argument("-o", "--output", help="输出文件")
    sp.set_defaults(func=cmd_paint)

    # ---- 区域批量替换 ----
    sp = sub.add_parser("region-batch",
                        help="对存档所有区域区块应用同一条修改（默认 dry-run）")
    sp.add_argument("path", help="存档目录")
    sp.add_argument("--op", default="set",
                    choices=["set", "remove", "replace_text",
                             "bump_data_version"])
    sp.add_argument("--field", help="字段路径，例如 LightPopulated")
    sp.add_argument("--value", help="SNBT 取值，例如 0b / 3955 / \"minecraft:full\"")
    sp.add_argument("--search", help="replace_text 的查找串")
    sp.add_argument("--replace", help="replace_text 的替换串")
    sp.add_argument("--data-version", type=int, default=3955,
                    help="bump_data_version 的目标版本")
    sp.add_argument("--limit", type=int, default=40, help="报告里最多列多少条")
    sp.add_argument("--apply", action="store_true",
                    help="真正写盘（每个区域文件备份一次 + 写后自检）")
    sp.set_defaults(func=cmd_region_batch)

    return p


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 cp936，中文会乱码；管道读取方按 UTF-8 解码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):            # pragma: no cover
            pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
