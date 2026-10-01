"""UI 冒烟：把主窗口里能点的东西都点一遍，捕获 Python 异常与 Qt 警告。

用途：找「点了才崩」的问题。Qt 槽里的异常默认不会让脚本退出，只会打到
stderr，所以这里主动接管 sys.excepthook + 收集 stderr 文本。

    python build/smoke_ui.py            # 干净数据目录 + 假存档
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "build" / "smoke-data"
if DATA.exists():
    shutil.rmtree(DATA, ignore_errors=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["MCSTUDIO_DATA_DIR"] = str(DATA)
os.environ["MCSTUDIO_NO_MIGRATE"] = "1"

# Windows 控制台默认 GBK，带 emoji 的 print 会 UnicodeEncodeError
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                     # pragma: no cover
        pass

from PySide6.QtCore import Qt, QTimer                    # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog       # noqa: E402

from app.ui.main_window import MainWindow                 # noqa: E402

FAILURES: list[str] = []
STDERR = io.StringIO()


def close_modal_dialogs() -> None:
    """离屏冒烟里没有真人点按钮：任何模态窗口直接关掉。

    `exec()` 会阻塞在嵌套事件循环里，所以靠 QTimer 在事件循环里回调。
    """
    widget = QApplication.activeModalWidget()
    if widget is None:
        return
    try:
        if isinstance(widget, QDialog):
            widget.reject()
        else:                                         # pragma: no cover
            widget.close()
    except Exception:
        try:
            widget.close()
        except Exception:
            pass


def check(label: str, fn) -> None:
    """跑一步；异常（含 Qt 槽里逃出来的）都记下来并继续。"""
    try:
        fn()
    except Exception:
        FAILURES.append(f"{label}\n{traceback.format_exc()}")
    for _ in range(8):
        QApplication.processEvents()
        close_modal_dialogs()


def build_world(name: str) -> Path:
    from tools.fake_world import build_fake_world
    root = ROOT / "build" / "smoke-worlds" / name
    if root.exists():
        shutil.rmtree(root)
    return build_fake_world(root)


def main() -> int:
    app = QApplication(sys.argv)
    # 槽里的异常走我们的钩子，别只打到控制台
    sys.excepthook = lambda *exc: FAILURES.append(
        "未捕获异常\n" + "".join(traceback.format_exception(*exc)))
    # exec() 期间也要能关掉模态窗口
    modal_timer = QTimer()
    modal_timer.setInterval(50)
    modal_timer.timeout.connect(close_modal_dialogs)
    modal_timer.start()

    window = MainWindow()
    window.resize(1500, 900)
    window.show()
    for _ in range(20):
        app.processEvents()

    # 1) 左侧命令树逐条选中（会切换所有命令面板）
    tree = window.command_tree
    items = []
    for i in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(i)
        for j in range(top.childCount()):
            items.append(top.child(j))
    for item in items:
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if key:
            check(f"选择命令 /{key}", lambda k=key: window._goto_command(k))

    # 2) 命令树筛选 + 清空
    check("筛选命令", lambda: window.tree_filter.setText("give"))
    check("清空筛选", lambda: window.tree_filter.clear())

    # 3) 中间栏 / 右侧 / 底部页签全切一遍
    for i in range(window.center_tabs.count()):
        check(f"中心页签 {window.center_tabs.tabText(i)}",
              lambda i=i: window.center_tabs.setCurrentIndex(i))
    for i in range(window.right_tabs.count()):
        check(f"右侧页签 {window.right_tabs.tabText(i)}",
              lambda i=i: window.right_tabs.setCurrentIndex(i))
    for i in range(window.bottom_tabs.count()):
        check(f"底部页签 {window.bottom_tabs.tabText(i)}",
              lambda i=i: window.bottom_tabs.setCurrentIndex(i))

    # 4) 工作区模式三种来回切
    for key in ("command", "dual", "editor", "dual"):
        check(f"工作区 {key}", lambda k=key: window.set_workspace(k))

    # 5) 壳层：首页 / 命令面板 / 向导 / 快捷键 / Execute 节点图
    check("回到首页", window.show_home)
    check("打开命令面板", window.open_palette)
    check("命令面板搜索", lambda: window._palette.search.setText("存档"))
    check("命令面板关闭", lambda: window._palette.close())
    check("打开向导", window.open_welcome)
    check("关闭向导", lambda: window._welcome.close())
    check("快捷键对话框", window.open_shortcuts)
    check("快捷键对话框（再来一次）", window.open_shortcuts)
    check("Execute 节点图", window._on_show_execute_graph)
    # 反复开关：WA_DeleteOnClose 的对话框如果在关闭后再 deleteLater()，
    # 第二次就会抛 "Internal C++ object already deleted"，这里专门复现
    check("Execute 节点图（再来一次）", window._on_show_execute_graph)
    check("Execute 节点图（第三次）", window._on_show_execute_graph)

    # 6) 生成 / 复制 / 收藏 / 校验 / 解析 / 折叠
    check("选择 /give", lambda: window._goto_command("give"))
    check("重新生成", window._on_generate)
    check("复制输出", window._on_copy_output)
    check("校验当前命令", window._on_validate_current)
    check("解析输出", lambda: window._on_parse_output(force=True))
    for name in ("_toggle_tree_area", "_toggle_bottom_area",
                 "_toggle_output_area", "_toggle_tree_area",
                 "_toggle_output_area"):
        check(name, getattr(window, name))

    # 7) 世界编辑器：打开存档 + 15 个页签 + 解锁 + 撤销 + 区域批量
    world = build_world("smoke")
    window.world_panel.async_open = False
    check("打开世界", lambda: window.world_panel.open_path(world))
    for i in range(window.world_panel.tabs.count()):
        check(f"世界页签 {window.world_panel.tabs.tabText(i)}",
              lambda i=i: window.world_panel.tabs.setCurrentIndex(i))
    check("切到世界编辑器", window._focus_world_editor)
    check("只读时点应用修改", window.world_panel.apply_changes)
    check("只读时点刷新区域", window.world_panel.refresh_regions)
    check("解锁编辑", lambda: window.world_panel.session.unlock())
    check("解锁后刷新状态", window.world_panel._refresh_session_state)
    check("世界设置写时间", lambda: window.world_panel.world()
          .set_node_value("level", "Data.DayTime", "13000",
                          kind=window.world_panel.world()
                          .level_root.get_child("Data")
                          .get_child("DayTime").kind))
    check("撤销（内存）", window.world_panel.undo)
    check("重做", window.world_panel.redo)
    check("预览差异", window.world_panel.preview_diff)
    check("区域批量对话框（dry-run）", lambda: _region_batch(window))
    check("检查存档健康", window.world_panel.run_health_check)
    check("世界规则刷新", window.world_panel.refresh_all)

    # 8) NBT 编辑器：新建 / 保存结构视图 / 各种菜单动作
    check("切到 NBT 编辑器", window._focus_nbt_editor)
    check("新建 NBT 文档", window._on_new_nbt_file)
    check("NBT 结构总览", window._on_nbt_structure)
    check("NBT 展开全部", window._on_nbt_expand_all)
    check("NBT 折叠全部", window._on_nbt_collapse_all)
    check("NBT 复制 SNBT", window._on_nbt_copy_snbt)
    check("NBT 复制 /data 命令", window._on_nbt_copy_command)

    # 9) 会话草稿 + 布局 + 主题 + 收尾
    check("保存草稿", window._save_session_draft)
    check("恢复草稿", window._restore_session_draft)
    check("保存布局", window._save_layout_sizes)
    check("恢复默认布局", window._reset_layout)
    for theme in ("dark", "light", "system"):
        check(f"主题 {theme}", lambda t=theme: window._set_theme(t))
    check("关闭窗口", window.close)

    # Qt 槽里的异常有时只走 stderr
    captured = STDERR.getvalue()
    for line in captured.splitlines():
        if "Traceback" in line or "Internal C++ object" in line:
            FAILURES.append(f"stderr 里出现异常痕迹：{line}")

    if FAILURES:
        print(f"[FAIL] 发现 {len(FAILURES)} 个问题：\n")
        for item in FAILURES:
            print("-" * 70)
            print(item)
        return 1
    print(f"[OK] UI 冒烟通过：{len(items)} 条命令 / "
          f"{window.center_tabs.count()} 个中心页签 / "
          f"{window.world_panel.tabs.count()} 个世界页签 全部点过，无异常")
    return 0


def _region_batch(window) -> None:
    from app.ui.world_region_batch import RegionBatchDialog
    dialog = RegionBatchDialog(window, world=window.world_panel.world())
    dialog.run_plan()
    deadline = 0
    while dialog._busy and deadline < 2000:
        QApplication.processEvents()
        deadline += 1
    dialog.close()
    dialog.deleteLater()


if __name__ == "__main__":
    raise SystemExit(main())
