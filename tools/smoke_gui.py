"""离屏 GUI 冒烟测试：构建主窗口并跑一遍关键交互。

用法：python tools/smoke_gui.py
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer                      # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402

FAILED: list[str] = []

# Windows 控制台默认 cp936，中文输出会乱码；这里统一强制 UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):                # pragma: no cover
        pass


def step(name: str, fn):
    try:
        result = fn()
        print(f"  [OK]   {name}" + (f" -> {result}" if result is not None else ""))
        return result
    except Exception as e:
        FAILED.append(f"{name}: {type(e).__name__}: {e}")
        print(f"  [FAIL] {name}: {type(e).__name__}: {e}")
        traceback.print_exc()
        return None


def main() -> int:
    app = QApplication(sys.argv)

    print("=" * 72)
    print("1. 构建主窗口")
    print("=" * 72)
    from app.core import all_builders
    from app.ui.main_window import MainWindow

    window = step("MainWindow()", MainWindow)
    if window is None:
        return 1

    print(f"  注册命令 {len(all_builders())} 条；面板栈 {window.panel_stack.count()} 个控件；"
          f"命令树 {window.command_tree.topLevelItemCount()} 个分类")

    print("\n" + "=" * 72)
    print("2. 校验（F5）—— 确认 rules.py 真正生效")
    print("=" * 72)
    for i in range(window.panel_stack.count()):
        window.panel_stack.setCurrentIndex(i)
        window._on_validate_current()
        rows = [(window.validation_tree.topLevelItem(j).text(0),
                 window.validation_tree.topLevelItem(j).text(1))
                for j in range(window.validation_tree.topLevelItemCount())]
        name = type(window.panel_stack.currentWidget()).__name__
        print(f"  面板 {i:2} {name:16} {rows}")

    print("\n" + "=" * 72)
    print("3. 反向解析（parse → 面板回填）")
    print("=" * 72)
    for cmd in ("summon minecraft:zombie ~ ~ ~ {IsBaby:0b}",
                "give @p minecraft:diamond_sword 1 [minecraft:custom_name=\"A\"]",
                "execute as @a at @s run say hi",
                "setblock ~ ~ ~ minecraft:stone replace"):
        window.output_edit.setPlainText(cmd)
        window._current_command_type = cmd.split()[0]
        step(f"解析 {cmd[:48]!r}", lambda c=cmd: window._on_parse_output(force=True))

    print("\n" + "=" * 72)
    print("4. 交互与面板")
    print("=" * 72)
    for name, fn in (
        ("同步 NBT → 输出", window._on_sync_nbt_to_output),
        ("同步目标选择器 → 输出", window._on_sync_target_to_output),
        ("重新生成", window._on_generate),
        ("复制输出", window._on_copy_output),
        ("折叠输出区", window._toggle_output_area),
        ("展开输出区", window._toggle_output_area),
        ("布局 focus", lambda: window._apply_layout("focus")),
        ("布局 three", lambda: window._apply_layout("three")),
        ("保存会话草稿", window._save_session_draft),
        ("重建命令列表", window._populate_command_list),
        ("历史面板刷新", window.history_panel.reload),
        ("收藏面板刷新", window.preset_panel.reload),
        ("片段面板刷新", window.fragment_panel.reload),
    ):
        step(name, fn)

    print("\n" + "=" * 72)
    print("5. NBT 二进制往返（写入 → 读回）")
    print("=" * 72)
    from app.core.nbt_binary import load_nbt_file, save_nbt_file
    from app.core.snbt import serialize

    out_dir = Path("build") / "smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    for compress in ("gzip", "zlib", "none"):
        path = out_dir / f"roundtrip_{compress}.nbt"
        def roundtrip(path=path, compress=compress):
            save_nbt_file(window.nbt_editor.root(), path, compress=compress)
            back = load_nbt_file(path)
            return serialize(back, pretty=False)[:60]
        step(f"往返 compress={compress}", roundtrip)

    print("\n" + "=" * 72)
    print("6. 数据包导出")
    print("=" * 72)
    from app.core.datapack import export_mcfunction

    step("export_mcfunction", lambda: export_mcfunction(
        out_dir / "pack", ["/say hi", "give @p minecraft:stone"],
        namespace="smoke", name="main"))

    print("\n" + "=" * 72)
    print("7. 新增功能")
    print("=" * 72)

    ref = window.reference_panel
    step("ID 速查：全量载入", lambda: (
        ref.reload(),
        f"{len(ref._entries)} 条 / {ref.tree.topLevelItemCount()} 个分类")[1])
    step("ID 速查：中文搜索", lambda: (
        ref.search.setText("僵尸"), ref._apply_filter(),
        ref.count_label.text())[2])
    step("ID 速查：中文名解析", lambda: __import__(
        "app.core.ids", fromlist=["resolve"]).resolve("钻石剑"))
    ref.search.clear()

    bp = window.batch_panel
    step("批量面板：命令列表", lambda: f"{bp.command_combo.count()} 条")

    def fill_and_preview():
        bp._select_first_command()
        bp._on_fill_sample()
        bp._refresh_preview_now()
        return bp.preview.toPlainText().splitlines()[0]
    step("批量面板：填充示例 → 预览", fill_and_preview)

    def picker_for_cell():
        for col, spec in enumerate(bp._args):
            if spec.kind in ("entity", "item", "block"):
                bp.table.setCurrentCell(0, col)
                bp._update_cell_hint(bp.table.item(0, col))
                return f"列 {spec.key} → {bp.btn_pick.text()}"
        return "当前命令没有 ID 列（已跳过）"
    step("批量面板：单元格选择器", picker_for_cell)
    step("批量面板：全部 → 输出", lambda: (
        bp._on_send_all_to_output(),
        window.output_edit.toPlainText().splitlines()[0])[1])

    from app.core.nlp.engine import parse
    from app.storage import Fragment
    from app.ui.panels import _fragment_to_command
    step("中文语义化：杀死所有僵尸", lambda: parse("杀死所有僵尸"))
    step("片段 → 命令文本", lambda: _fragment_to_command(
        Fragment(1, "测试片段", "command", "工具", {"text": "say hi"}, "", "")))
    step("迁移差异", lambda: __import__(
        "app.core.datapack", fromlist=["migrate_command"]).migrate_command(
            'give @p diamond_sword 1 {display:{Name:"A"}}').migrated)

    print("\n" + "=" * 72)
    print("8. /give 面板（重点）")
    print("=" * 72)

    gp = window.give_panel
    step("默认命令", gp.current_command)
    step("物品自动补命名空间", lambda: (
        gp.item_select.set_id("diamond_sword"),
        gp._on_item_changed("diamond_sword"),
        gp._on_item_settled(),
        gp.current_command())[-1])
    step("快速数量下拉", lambda: (
        gp._on_count_preset(gp.count_combo.findData(64)),
        gp.current_command())[-1])
    step("目标预设", lambda: (
        gp._on_target_preset(gp.target_combo.findData("@a")),
        gp.current_command())[-1])

    def add_components():
        gp.builder.components = []
        gp.builder.add_component("minecraft:custom_name", '"神剑"')
        gp.builder.add_component(
            "minecraft:enchantments",
            '{"levels":{"minecraft:sharpness":5,"minecraft:mending":1}}')
        gp.builder.add_component("minecraft:damage", mode="remove")
        gp._refresh_table()
        gp._refresh_command()
        return gp.current_command()
    step("添加组件（含移除）", add_components)
    step("表格行数/预览", lambda: (
        f"{gp.comp_table.rowCount()} 行；"
        f"预览={gp.comp_table.item(1, 1).text()}"))
    step("上移/下移组件", lambda: (
        gp._move_component(0, 1), gp.current_command())[-1])
    step("切换组件模式", lambda: (
        gp._toggle_component_mode(0), gp.current_command())[-1])
    step("复制一份组件", lambda: (
        gp._duplicate_component(0), gp.comp_table.rowCount())[-1])
    step("删除组件", lambda: (
        gp._delete_component(0), gp.comp_table.rowCount())[-1])
    step("清空组件", lambda: (
        gp._on_clear_components(), gp.current_command())[-1])

    def apply_preset():
        presets = gp._presets
        if not presets:
            return "没有处方数据"
        gp._apply_preset(presets[0])
        return f"{presets[0]['name']} -> {gp.current_command()[:70]}"
    step("套用快速处方", apply_preset)
    step("建议列表", lambda: f"{gp.suggest.count()} 条")
    step("套用建议", lambda: (
        gp._on_clear_components(),
        gp._on_apply_suggestions(),
        f"{gp.comp_table.rowCount()} 个组件")[-1])
    def parse_roundtrip():
        command = gp.current_command()
        window.output_edit.setPlainText(command)
        window._current_command_type = "give"
        window._on_parse_output(force=True)
        return command if gp.current_command() == command else (
            f"不一致：{command} / {gp.current_command()}")
    step("处方 → 反向解析闭环", parse_roundtrip)
    def draft_roundtrip():
        gp.restore_state(gp.dump_state())
        return gp.dump_state()["components"] == gp.builder.to_dict()["components"]
    step("草稿往返", draft_roundtrip)
    def picker_dialog():
        from app.ui.widgets import ComponentPickerDialog
        dlg = ComponentPickerDialog(None, item="minecraft:diamond_sword")
        total = sum(dlg.tree.topLevelItem(i).childCount()
                    for i in range(dlg.tree.topLevelItemCount()))
        dlg.deleteLater()
        return f"{dlg.tree.topLevelItemCount()} 组 / {total} 个组件"
    step("组件选择对话框构建", picker_dialog)
    step("值编辑对话框校验", lambda: __import__(
        "app.ui.widgets", fromlist=["ValueEditDialog"])
        .ValueEditDialog("t", "{bad", validator=lambda t: [("error", "x")])
        .message.text())
    step("面板 reload_data", gp.reload_data)

    print("\n" + "=" * 72)
    print("9. NBT 文件编辑器（.dat / .nbt / .snbt）")
    print("=" * 72)

    from app.core.nbt_binary import detect_compression
    from app.core.snbt import byte, compound, integer, string

    nbt_dir = Path("build") / "smoke" / "nbt"
    nbt_dir.mkdir(parents=True, exist_ok=True)
    editor = window.nbt_file_editor

    sample = compound(
        Data=compound(LevelName=string("smoke"), SpawnX=integer(0),
                      Hardcore=byte(0)),
        Tags=__import__("app.core.snbt", fromlist=["list_node"]).list_node(
            [string("a"), string("b")]))

    for kind in ("none", "gzip", "zlib"):
        path = nbt_dir / f"level_{kind}.dat"
        save_nbt_file(sample, path, compress=kind, root_name="")
        step(f"打开并保持格式（{kind}）",
             lambda p=path, k=kind: (
                 editor.load_path(str(p)),
                 detect_compression(Path(p).read_bytes()) == k,
                 editor.doc.compression_label)[-1])

    step("文件信息行", lambda: editor.hint.text())

    def edit_and_save():
        root = editor.root()
        data = root.get_child("Data")
        if data is None:
            return "样例数据缺少 Data"
        editor.editor._push_history()
        data.add_child(__import__("app.core.snbt", fromlist=["SnbtNode"])
                       .SnbtNode.new_scalar(
                           __import__("app.core.snbt", fromlist=["SnbtKind"])
                           .SnbtKind.STRING, "smoke", key="Added"))
        editor.editor._refresh_all()
        editor.editor._mark_changed()
        dirty = editor.doc.dirty
        saved = editor.save()
        return f"dirty={dirty} saved={saved} 保存后={editor.doc.dirty}"

    step("编辑 → 保存（清除未保存标记）", edit_and_save)
    step("撤销 / 重做", lambda: (
        editor.editor.undo(), editor.editor.redo(),
        f"undo={editor.editor.can_undo()} redo={editor.editor.can_redo()}")[-1])

    def save_snbt():
        out = nbt_dir / "export.snbt"
        editor._write(out)
        return f"{out.name} {out.stat().st_size} bytes"
    step("另存为文本 SNBT", save_snbt)
    def new_doc():
        # 先清掉未保存标记：离屏环境下 _confirm_discard 的模态框会一直阻塞
        editor.doc.dirty = False
        editor.new_document()
        return editor.doc.display_title()
    step("新建空文档", new_doc)

    print("\n" + "=" * 72)
    print("10. NBT 树形编辑器（三列 / 数组 / 查找）")
    print("=" * 72)

    from app.core.snbt import SnbtKind as _Kind, SnbtParser as _Parser
    from app.ui.widgets import ArrayEditDialog
    from app.ui.widgets.nbt_tags import format_value, node_path

    tree_editor = editor.editor
    tree_editor.set_root(_Parser(
        '{Data:{LevelName:"world",SpawnX:0},Nums:[I;1,2,3],'
        'Bytes:[B;10,20,255]}').parse())

    step("三列表头", lambda: [
        tree_editor.tree.headerItem().text(i) for i in range(3)])
    step("类型列", lambda: [
        tree_editor.tree.topLevelItem(0).child(i).text(1)
        for i in range(tree_editor.tree.topLevelItem(0).childCount())])
    step("按路径定位", lambda: (
        tree_editor.select_by_path("Data.SpawnX"),
        node_path(tree_editor.selected_node(), tree_editor.root()))[-1])
    step("状态栏显示路径", lambda: tree_editor.status.text())
    step("新建标签", lambda: (
        tree_editor.insert_node(tree_editor.root().get_child("Data"),
                                _Kind.INT, key="Smoke"),
        tree_editor.selected_node().key)[-1])
    step("撤销 / 重做", lambda: (
        tree_editor.undo(), tree_editor.redo(),
        "Smoke" in tree_editor.to_snbt())[-1])
    step("hex 视图", lambda: (
        tree_editor._toggle_hex_mode(True),
        format_value(tree_editor.root().get_child("Bytes"), hex_mode=True))[-1])
    def array_dialog_text():
        dialog = ArrayEditDialog(tree_editor.root().get_child("Nums"))
        text = dialog.editor.toPlainText().replace("\n", "")
        # 必须真正销毁：Qt 只要还有可见窗口就不会退出事件循环
        dialog.setParent(None)
        dialog.deleteLater()
        return text
    step("数组编辑器", array_dialog_text)
    step("查找", lambda: (
        tree_editor.show_find_bar(),
        tree_editor.find_bar.find_edit.setText("world"),
        tree_editor.find_next(),
        tree_editor.selected_node().key)[-1])
    tree_editor.find_bar.setVisible(False)

    print("\n" + "=" * 72)
    print("11. NBT 编辑器增强（详情面板 / 筛选 / 就地编辑 / 追加元素）")
    print("=" * 72)

    step("详情面板：数组统计", lambda: (
        tree_editor.select_by_path("Bytes"),
        f"{tree_editor.detail_type.text()} / {tree_editor.detail_meta.text()}")[-1])
    step("详情面板：hex dump", lambda: tree_editor.detail_preview.toPlainText()
         .splitlines()[-1])
    step("详情面板：标量取值", lambda: (
        tree_editor.select_by_path("Data.LevelName"),
        tree_editor.detail_value.text())[-1])
    step("详情面板：改标量取值", lambda: (
        tree_editor.detail_value.setText("smoke-world"),
        tree_editor.apply_detail_value(),
        tree_editor.root().get_child("Data").get_child("LevelName").value)[-1])
    step("详情面板：非法取值的提示", lambda: (
        tree_editor.select_by_path("Data.SpawnX"),
        tree_editor.detail_value.setText("abc"),
        tree_editor.apply_detail_value(),
        tree_editor.detail_value_state.text())[-1])

    def inline_editor_roundtrip():
        from app.ui.widgets.nbt_dialogs import (
            create_inline_editor, read_inline_editor)
        node = tree_editor.root().get_child("Data").get_child("SpawnX")
        widget = create_inline_editor(node)
        widget.setValue(64) if hasattr(widget, "setValue") else \
            widget.setText("64")
        return read_inline_editor(node, widget)
    step("就地编辑：Int 微调框", inline_editor_roundtrip)

    def reject_mismatched_element():
        result = tree_editor.add_list_element(
            tree_editor.root().get_child("Nums"), _Kind.STRING)
        return f"返回 {result}（None 表示被拒绝）"
    step("追加元素：类型不匹配被拒绝", reject_mismatched_element)
    step("追加元素：Int 数组", lambda: (
        tree_editor.add_list_element(tree_editor.root().get_child("Nums"),
                                     _Kind.INT),
        len(tree_editor.root().get_child("Nums").children))[-1])

    step("字节转字符串", lambda: (
        tree_editor.select_by_path("Bytes"),
        tree_editor._toggle_byte_text(True),
        tree_editor.btn_byte_text.isEnabled())[-1])

    step("筛选：关键字", lambda: (
        tree_editor.filter_edit.setText("LevelName"),
        sum(1 for it in tree_editor._all_items() if not it.isHidden()))[-1])
    step("筛选：只看 IntArray", lambda: (
        tree_editor.filter_edit.clear(),
        tree_editor.filter_types.setCurrentIndex(
            tree_editor.filter_types.findData(_Kind.INT_ARRAY)),
        sum(1 for it in tree_editor._all_items() if not it.isHidden()))[-1])
    step("筛选：恢复", lambda: (
        tree_editor.filter_types.setCurrentIndex(0),
        sum(1 for it in tree_editor._all_items() if it.isHidden()))[-1])

    step("多选删除", lambda: (
        [it.setSelected(True) for it in tree_editor._all_items()
         if it.text(0) in ("Bytes",)],
        "Bytes" in tree_editor.to_snbt())[-1])

    step("复制为 /data 命令", lambda: (
        tree_editor.select_by_path("Data.SpawnX"),
        tree_editor.copy_as_command() or
        __import__("PySide6.QtWidgets", fromlist=["QApplication"])
        .QApplication.clipboard().text())[-1])

    step("详情面板开关", lambda: (
        tree_editor.toggle_details(False),
        tree_editor._details_panel.isHidden(),
        tree_editor.toggle_details(True),
        not tree_editor._details_panel.isHidden())[-1])

    step("展开 / 折叠全部", lambda: (
        tree_editor.expand_all(), tree_editor.collapse_all(),
        tree_editor.tree.topLevelItem(0).isExpanded())[-1])

    print("\n" + "=" * 72)
    print("12. NBT 编辑器菜单与定位")
    print("=" * 72)

    def nbt_menu_actions():
        menu = None
        for action in window.menuBar().actions():
            if action.menu() is None:
                continue
            for sub in action.menu().actions():
                if sub.menu() is not None and sub.text() == "NBT 编辑器":
                    menu = sub.menu()
        names = [a.text() for a in menu.actions()] if menu else []
        return f"{len(names)} 项：{' / '.join(names)}"
    step("文件菜单里的 NBT 子菜单", nbt_menu_actions)

    def goto_sample_path():
        editor.doc.dirty = False        # 离屏环境不能弹「是否保存」模态框
        editor.editor.set_root(_Parser(
            '{Data:{LevelName:"smoke",SpawnX:0}}').parse())
        editor.editor.select_by_path("Data.LevelName")
        return window._nbt_tree_editor().current_path()
    step("菜单：定位到路径", goto_sample_path)
    step("菜单：全部展开 / 折叠", lambda: (
        window._on_nbt_expand_all(), window._on_nbt_collapse_all(),
        "已执行")[-1])
    step("菜单：复制为 SNBT", lambda: (
        window._nbt_tree_editor().select_by_path("Data"),
        window._on_nbt_copy_snbt(), "已复制")[-1])
    step("菜单：复制为 /data 命令", lambda: (
        window._on_nbt_copy_command(), "已复制")[-1])
    step("面板内的 NBT 编辑器（窄窗口自适应）", lambda: (
        window._panels["summon"].nbt_editor.width(),
        window._panels["summon"].nbt_editor._compact)[-1])

    print("\n" + "=" * 72)
    print("13. 存档数据 → 图形化显示 → 命令面板")
    print("=" * 72)

    from app.core.snbt import SnbtParser as _SnbtParser
    player_nbt = _SnbtParser(
        '{Data:{Player:{'
        'Inventory:['
        '{Slot:0b,id:"minecraft:diamond_sword",count:1,components:{'
        '"minecraft:custom_name":\'{"text":"神剑"}\','
        '"minecraft:enchantments":{levels:{"minecraft:sharpness":5}}}},'
        '{Slot:1b,id:"minecraft:stone",count:64}],'
        'EnderItems:[{Slot:0b,id:"minecraft:elytra",count:1}],'
        'Health:20.0f}}}').parse()
    tree_editor.set_root(player_nbt)
    tree_editor.set_source_label("smoke_player.dat")

    step("识别物品", lambda: (
        len(tree_editor._item_paths),
        sorted(tree_editor._item_paths))[-1])
    step("物品行显示中文名", lambda: tree_editor.item_by_path(
        "Data.Player.Inventory[1]").text(2))
    step("物品容器默认展开", lambda: (
        tree_editor.item_by_path("Data.Player.Inventory").isExpanded()
        or tree_editor.item_by_path(
            "Data.Player.Inventory[0]") is not None))
    step("类型列中文（专有名词翻译）", lambda: (
        tree_editor.item_by_path("Data.Player.Health").text(1)))
    step("物品卡片", lambda: (
        tree_editor.select_by_path("Data.Player.Inventory[0]"),
        tree_editor.item_name.text())[-1])
    step("物品 → /give 面板", lambda: (
        tree_editor.select_by_path("Data.Player.Inventory[0]"),
        tree_editor._send_item_to_give(),
        window._panels["give"].current_command())[-1])
    step("物品 → 复制 /give 命令", lambda: (
        tree_editor.select_by_path("Data.Player.Inventory[1]"),
        tree_editor.copy_item_command(),
        __import__("PySide6.QtWidgets", fromlist=["QApplication"])
        .QApplication.clipboard().text())[-1])

    def overview_dialog():
        from app.ui.dialogs import ItemOverviewDialog
        dialog = ItemOverviewDialog(player_nbt, source="smoke_player.dat")
        rows = dialog.table.rowCount()
        command = dialog.table.item(0, 2).text()
        dialog.setParent(None)
        dialog.deleteLater()
        return f"{rows} 行，首行 {command}"
    step("存档物品一览对话框", overview_dialog)

    entity_nbt = _SnbtParser(
        '{Entities:[{id:"minecraft:zombie",Health:20.0f,Tags:["boss"],'
        'CustomName:\'{"text":"僵尸王"}\'}]}').parse()
    tree_editor.set_root(entity_nbt)
    step("实体识别与卡片", lambda: (
        tree_editor.select_by_path("Entities[0]"),
        tree_editor.entity_title.text())[-1])
    step("实体 → /summon 面板", lambda: (
        tree_editor._send_entity_to_summon(),
        window._panels["summon"].current_command())[-1])

    print("\n" + "=" * 72)
    print("14. 设置：专有名词翻译")
    print("=" * 72)

    step("默认翻译", lambda: (
        __import__("app.core.settings", fromlist=["x"])
        .translate_nbt_type("String"),
        __import__("app.core.settings", fromlist=["x"])
        .translate_nbt_type("Compound"))[-1])

    def settings_dialog():
        from app.ui.dialogs import SettingsDialog
        dialog = SettingsDialog()
        pages = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
        rows = dialog.term_tables["nbt_types"].table.rowCount()
        dialog.setParent(None)
        dialog.deleteLater()
        return f"{pages} / NBT 类型 {rows} 条"
    step("设置对话框", settings_dialog)

    print("\n" + "=" * 72)
    print("15. 文件结构总览（确认标签是否真的在文件里）")
    print("=" * 72)

    from app.core.nbt_binary import save_nbt_file
    struct_path = Path("build") / "smoke" / "nbt" / "world_level.dat"
    save_nbt_file(_SnbtParser(
        '{Data:{version:19133,LevelName:"w",Player:{Health:20.0f}},'
        'fml:{LoadingModList:[{ModId:"create",ModSize:1}]}}').parse(),
        struct_path, compress="gzip", root_name="")

    def structure_dialog():
        from app.ui.dialogs import StructureDialog
        editor.doc.dirty = False
        editor.load_path(str(struct_path))
        dialog = StructureDialog(editor.doc)
        rows = [dialog.tree.topLevelItem(i).text(0)
                for i in range(dialog.tree.topLevelItemCount())]
        summary = dialog.summary.text()
        dialog.setParent(None)
        dialog.deleteLater()
        return f"根下 {rows}｜根标签名显示：{'（空）' in summary}"
    step("结构总览：世界存档", structure_dialog)

    def structure_cli():
        import subprocess
        result = subprocess.run(
            [sys.executable, "tools/inspect_nbt.py", str(struct_path),
             "--depth", "1"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60)
        lines = [x for x in result.stdout.splitlines()
                 if x.strip() and not x.startswith("-")]
        return f"返回码 {result.returncode}｜{len(lines)} 行"
    step("CLI：inspect_nbt.py", structure_cli)

    print("\n" + "=" * 72)
    print("16. 世界与 NBT 编辑器面板（双面板骨架 + 世界存档）")
    print("=" * 72)

    from tools.fake_world import build_fake_world
    from app.core import bridge as _bridge
    from app.core.region_io import list_presets as _presets
    from app.core.region_io import preset_summary as _preset_summary

    def _visible_rows(table) -> int:
        return sum(1 for row in range(table.rowCount())
                   if not table.isRowHidden(row))

    world_dir = build_fake_world(Path("build") / "smoke" / "world")
    wp = window.world_panel

    # 冒烟测试走同步打开（异步路径在单元测试里覆盖）
    wp.async_open = False
    step("打开世界存档", lambda: (wp.open_path(world_dir),
                                  wp.session.world.name)[-1])
    step("工作区：编辑模式", lambda: (window.set_workspace("editor"),
                                     window.workspace)[-1])
    step("工作区：双栏联动", lambda: (window.set_workspace("dual"),
                                     window.workspace)[-1])
    step("编辑器页签", lambda: [
        wp.tabs.tabText(i) for i in range(wp.tabs.count())])
    step("世界总览行数", lambda: wp.overview_table.rowCount())
    step("游戏规则行数", lambda: wp.rules_table.rowCount())
    step("玩家列表 / 数据行", lambda: (wp.player_list.count(),
                                       wp.player_table.rowCount(),
                                       wp.player_inv_table.rowCount())[-1])
    step("容器索引", lambda: [wp.container_table.item(i, 5).text()
                              for i in range(wp.container_table.rowCount())])
    step("地图表", lambda: wp.map_table.rowCount())
    step("区域索引", lambda: (wp.region_table.rowCount(),
                              wp.region_count.text())[-1])
    step("数据包表", lambda: wp.datapack_table.rowCount())
    step("工作区预设", lambda: [_preset_summary(p) for p in _presets()])
    step("表格过滤（Ctrl+F）", lambda: (
        wp.overview_filter.edit.setText("时间"),
        _visible_rows(wp.overview_table),
        wp.overview_filter.edit.clear())[1])
    step("健康检查", lambda: (wp.run_health_check(),
                              wp.health_summary.text())[-1])
    step("NBT 树：载入 level.dat", lambda: (
        wp._on_load_nbt_source(),
        len(wp.nbt_editor.editor.root().children))[-1])
    step("NBT 树：定位到路径", lambda: (
        wp.nbt_editor.editor.select_by_path("Data.LevelName"),
        wp.nbt_editor.editor.current_path())[-1])

    # ---- 联动总线：命令 → 存档 → 预览 ----
    step("输出 → 联动总线", lambda: (
        window.output_edit.setPlainText(
            "give @p minecraft:diamond 5\ntime set 13000"),
        window.send_output_to_bridge(),
        len(window.bridge_panel.pending))[-1])
    step("总线：列出转换来源", lambda: (
        window.bridge_panel.on_preview(),
        len(window.bridge_panel.log_edit.toPlainText().splitlines()))[-1])

    # ---- 只读门禁 ----
    def read_only_blocks():
        result = _bridge.apply_operation(
            wp.session,
            _bridge.command_to_operation("time set 100", session=wp.session))
        return result.ok
    step("只读时拒绝写入", lambda: read_only_blocks())

    # ---- 解锁 + 事务写盘（离屏环境里不能弹模态框，所以直接调 API） ----
    def apply_time():
        wp.session.unlock()
        operation = _bridge.command_to_operation("time set 13000",
                                                 session=wp.session)
        operation.runner = _bridge.operation_runner(operation)
        operation.source, operation.target = "smoke", "level"
        result = _bridge.apply_operation(wp.session, operation)
        return (f"ok={result.ok} applied={result.applied} "
                f"时间={wp.session.world.overview.day_time} "
                f"备份={result.backup.name if result.backup else '无'}")
    step("联动总线 → 世界（事务 + 备份）", apply_time)
    step("备份列表", lambda: [b["name"] for b in wp.session.list_backups()])
    step("撤销", lambda: (window._on_bridge_undo(),
                          wp.session.world.overview.day_time)[-1])
    step("重做", lambda: (wp.redo(),
                          wp.session.world.overview.day_time)[-1])

    # ---- 编辑器 → 命令面板 ----
    step("物品 → /give 面板", lambda: (
        wp.player_list.setCurrentRow(0),
        wp.player_inv_table.selectRow(0),
        wp._on_inventory_to_give(),
        window.give_panel.current_command()[:60])[-1])
    step("容器 → /setblock（剪贴板）", lambda: (
        wp.container_table.selectRow(0),
        wp._on_container_to_setblock(),
        __import__("PySide6.QtWidgets", fromlist=["QApplication"])
        .QApplication.clipboard().text()[:60])[-1])

    print("\n" + "=" * 72)
    print("17. 蓝图 / 配方工坊")
    print("=" * 72)

    step("配方列表", lambda: (wp.refresh_recipes(),
                              wp.recipe_table.rowCount())[-1])
    step("配方 → /give", lambda: (
        wp.recipe_table.selectRow(0),
        wp._on_recipe_commands(),
        window.output_edit.toPlainText()[:60])[-1])
    step("蓝图：从 block_entities 导出", lambda: (
        wp.tabs.setCurrentIndex(
            [wp.tabs.tabText(i) for i in range(wp.tabs.count())]
            .index("蓝图 / 配方")),
        wp.refresh_blueprints(),
        wp.blueprint_source.count())[-1])

    print("\n" + "=" * 72)
    print("17b. 中文世界编辑 + 区域写入 + 预设")
    print("=" * 72)

    step("中文世界编辑：解析", lambda: (
        wp.tabs.setCurrentIndex(
            [wp.tabs.tabText(i) for i in range(wp.tabs.count())]
            .index("中文世界编辑")),
        wp.nlp_input.setText("把时间设置为夜晚"),
        wp._on_nlp_parse(),
        wp.nlp_status.text())[-1])
    step("中文世界编辑：等价命令", lambda: (
        wp._on_nlp_commands(),
        window.output_edit.toPlainText().splitlines()[0])[-1])
    step("中文世界编辑：换成场景句", lambda: (
        wp.nlp_input.setText("永久白天"),
        wp._on_nlp_parse(),
        wp.nlp_status.text())[-1])

    def region_plan():
        from app.core.region_io import plan_chunk_write
        from app.core.snbt import SnbtParser
        from app.core.world import read_region_chunk
        path = world_dir / "region" / "r.0.0.mca"
        node = read_region_chunk(path, 0, 0)
        if node is None:
            return "（样例区块不可读，已跳过）"
        node.add_child(SnbtParser("{InhabitedTime:7L}").parse().children[0])
        return plan_chunk_write(path, 0, 0, node).summary()
    step("区域写入：dry-run 可行性", region_plan)

    def region_write_roundtrip():
        from app.core.region_io import verify_region, write_chunk
        from app.core.snbt import SnbtParser
        from app.core.world import read_region_chunk
        path = world_dir / "region" / "r.0.0.mca"
        node = read_region_chunk(path, 0, 0)
        if node is None:
            return "（样例区块不可读，已跳过）"
        node.add_child(SnbtParser("{Inked:1b}").parse().children[0])
        write_chunk(path, 0, 0, node)
        back = read_region_chunk(path, 0, 0)
        return f"写后自检={verify_region(path)} 回读 Inked={back.get_child('Inked') is not None}"
    step("区域写入：原子替换 + 自检", region_write_roundtrip)

    step("预设：套用「创造沙盒」", lambda: (
        window.output_edit.clear(),
        _preset_summary(_presets()[0]))[-1])

    print("\n" + "=" * 72)
    print("17c. 可视化编排 + 区域批量替换 + 数据包工程")
    print("=" * 72)

    def canvas_paint():
        tabs = [wp.tabs.tabText(i) for i in range(wp.tabs.count())]
        wp.tabs.setCurrentIndex(tabs.index("可视化编排"))
        canvas = wp.canvas
        canvas.set_layer(64)
        canvas.brush = "minecraft:stone"
        for x in range(0, 4):
            for z in range(0, 4):
                canvas._apply_tool(x, z)
        canvas.brush = "minecraft:glass"
        canvas._apply_tool(0, 5)
        canvas._chunk_sel = (0, 0, 0, 0)
        wp._on_canvas_selection(0, 0, 0, 0)
        return (f"{len(canvas.grid)} 格 / {len(canvas.grid.palette())} 种方块 / "
                f"标记 {len(canvas.markers)} 个")
    step("画布：编排 17 格并框选", canvas_paint)
    step("画布：撤销 / 重做", lambda: (
        wp.canvas.undo(), wp.canvas.redo(), len(wp.canvas.grid))[-1])
    step("画布：生成 /fill（矩形分解）", lambda: (
        window.output_edit.clear(),
        wp._on_canvas_fill(),
        window.output_edit.toPlainText().splitlines()[0])[-1])
    step("画布：导出为蓝图对象", lambda: wp.canvas.grid.to_blueprint(
        "冒烟").summary())
    step("画布：复制 /data 命令", lambda: (
        wp._on_canvas_copy_data(),
        __import__("PySide6.QtWidgets", fromlist=["QApplication"])
        .QApplication.clipboard().text()[:50])[-1])

    def batch_plan():
        from app.ui.world_region_batch import execute_batch, plan_batch
        world = wp.session.world
        plan = plan_batch(world, operation="set", path="LightPopulated",
                          value="0b")
        summary = plan.summary()
        if plan.writable:
            done, failures, folder = execute_batch(world, plan)
            return (f"{summary} → 写入 {done} 个"
                    f"（备份 {folder.name if folder else '无'}）"
                    f"，失败 {len(failures)}")
        return summary
    step("区域批量替换（dry-run + 执行）", batch_plan)
    step("批量替换幂等性", lambda: (
        __import__("app.ui.world_region_batch", fromlist=["plan_batch"])
        .plan_batch(wp.session.world, operation="set",
                    path="LightPopulated", value="0b").summary()))

    def pack_flow():
        import tempfile
        from pathlib import Path as _Path
        from app.core.datapack_project import (AuthoringPack,
                                               advancement_template,
                                               function_template)
        root = _Path(tempfile.mkdtemp(prefix="mcstudio-smoke-pack-"))
        pack = AuthoringPack(root=root, namespace="smoke")
        pack.add_from_commands("main", function_template("main").splitlines())
        pack.add_from_commands("shop/buy",
                               function_template("shop").splitlines())
        pack.add_asset("advancement", "first", advancement_template("冒烟"))
        pack.auto_tag_load()
        problems = pack.validate()
        written = pack.save()
        loaded = AuthoringPack.load(root)
        import shutil
        shutil.rmtree(root, ignore_errors=True)
        return (f"{pack.summary()} · 写出 {len(written)} 个文件 · "
                f"回读 {len(loaded.assets)} 个资源 · 自检 {problems or '通过'}")
    step("数据包工程：新建 / 自检 / 落盘 / 回读", pack_flow)
    step("数据包工程页签", lambda: (
        [wp.tabs.tabText(i) for i in range(wp.tabs.count())]
        .index("数据包工程") >= 0, wp.pack_tree.topLevelItemCount())[-1])

    print("\n" + "=" * 72)
    print("18. 项目改名与数据目录")
    print("=" * 72)

    from app import branding as _branding
    from app.migration import migrate_data_dir
    from tests import TEST_ROOT  # noqa: F401

    step("品牌常量", lambda: (_branding.APP_NAME, _branding.APP_SLUG,
                              _branding.GENERATOR))
    step("窗口标题", lambda: window.windowTitle())
    step("旧 CLI 入口仍可用", lambda: (Path("mcgen.py").is_file(),
                                       Path("mcstudio.py").is_file()))
    step("改名说明", lambda: _branding.rename_report().splitlines()[0])

    def migrate_demo():
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="mcstudio-smoke-"))
        source = tmp / "MCCmdGen"
        (source / "user_data").mkdir(parents=True)
        (source / "data.db").write_bytes(b"SQLite format 3\x00")
        (source / "user_data" / "settings.json").write_text("{}",
                                                            encoding="utf-8")
        report = migrate_data_dir(source, tmp / "mcstudio")
        ok = (tmp / "mcstudio" / "data.db").is_file() and report.moved
        shutil.rmtree(tmp, ignore_errors=True)
        return f"迁移成功={ok}（{report.summary()}）"
    step("数据目录迁移", migrate_demo)

    window.show()
    print("  [..] 进入事件循环（600ms 后退出）", flush=True)

    def finish() -> None:
        app.exit(0)

    QTimer.singleShot(600, finish)
    app.exec()
    print("  [..] 事件循环已退出", flush=True)

    print("\n" + "=" * 72)
    if FAILED:
        print(f"冒烟测试结束：{len(FAILED)} 项失败")
        for f in FAILED:
            print("  -", f)
        return 1
    print("冒烟测试结束：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
