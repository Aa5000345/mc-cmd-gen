"""离屏截图工具：把 MCStudio 主窗口的关键页面抓成 PNG，用于 UI 走查。

用法:
    python build/shot.py            # 干净首启 + 载入测试存档
    SHOT_TAG=after- python build/shot.py
"""
from __future__ import annotations

import glob
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "build" / "guidata"

# 干净数据目录：保证是「首次启动」的样子
if "--keep-data" not in sys.argv and DATA.exists():
    shutil.rmtree(DATA, ignore_errors=True)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["MCSTUDIO_DATA_DIR"] = str(DATA)
os.environ["MCSTUDIO_NO_MIGRATE"] = "1"

OUT = ROOT / "build" / "shots"
OUT.mkdir(parents=True, exist_ok=True)

from PySide6.QtGui import QFont, QFontDatabase     # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.main_window import MainWindow  # noqa: E402

TAG = os.environ.get("SHOT_TAG", "")


def load_fonts() -> None:
    """离屏模式下 Qt 找不到字体目录，手动加载系统中文 / 等宽 / 符号字体。"""
    for pat in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
                r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\consola.ttf",
                r"C:\Windows\Fonts\seguisym.ttf", r"C:\Windows\Fonts\segoeui.ttf"):
        for path in glob.glob(pat):
            QFontDatabase.addApplicationFont(path)
    # 显式回退链：中文字体缺 ▾ ✔ ▶ 时用 Segoe UI Symbol 兜底，否则截图里是方框
    app = QApplication.instance()
    if app is not None:
        base = QFont("Microsoft YaHei UI")
        base.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei",
                          "Segoe UI Symbol", "Segoe UI", "Consolas"])
        base.setPointSize(9)
        app.setFont(base)


def pump(app, n: int = 12) -> None:
    for _ in range(n):
        app.processEvents()
        time.sleep(0.005)


def shot(app, w, name: str, widget=None) -> None:
    pump(app, 25)
    path = OUT / f"{TAG}{name.replace('/', '／')}.png"
    ok = (widget or w).grab().save(str(path))
    print(("saved " if ok else "FAILED ") + path.name, flush=True)


def main() -> int:
    app = QApplication(sys.argv)
    load_fonts()
    w = MainWindow()
    w.resize(1600, 950)
    w.show()
    pump(app, 30)

    # ---- 首屏（干净数据目录 → 首页）----
    shot(app, w, "01-first-run-home")

    # ---- 命令面板（Ctrl+K）----
    w.open_palette()
    pump(app, 30)
    shot(app, w, "02-palette-all", w._palette)
    w._palette.search.setText("存档")
    pump(app, 20)
    shot(app, w, "03-palette-search", w._palette)
    w._palette.close()

    # ---- 命令面板 + 引导条 ----
    items = []
    tree = w.command_tree
    for i in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(i)
        for j in range(top.childCount()):
            items.append(top.child(j))
    for it in items:
        name = it.text(0).split()[0]
        if name in ("/give", "/summon", "/execute", "/setblock"):
            w._goto_command(name.lstrip("/"))
            pump(app, 25)
            shot(app, w, f"04-cmd-{name.strip('/')}")

    # 改动字段 → 引导条切到「命令已就绪」
    w._goto_command("give")
    w.give_panel.target_line.setText("@a")
    pump(app, 25)
    shot(app, w, "05-give-ready")
    w._on_copy_output()
    pump(app, 6)
    shot(app, w, "05b-toast-copied")

    # ---- 中间页签 ----
    for i in range(w.center_tabs.count()):
        w.center_stack.setCurrentWidget(w.center_tabs)
        w.center_tabs.setCurrentIndex(i)
        pump(app, 15)
        shot(app, w, f"06-tab-{i}-{w.center_tabs.tabText(i)}")

    # ---- 右侧 / 底部 ----
    for i in range(w.right_tabs.count()):
        w.right_tabs.setCurrentIndex(i)
        pump(app, 15)
        shot(app, w, f"07-right-{i}-{w.right_tabs.tabText(i)}")
    w.center_stack.setCurrentWidget(w.center_tabs)
    w.center_tabs.setCurrentIndex(w.center_tabs.indexOf(w.panel_stack))
    for i in range(w.bottom_tabs.count()):
        w.bottom_tabs.setCurrentIndex(i)
        pump(app, 15)
        shot(app, w, f"08-bottom-{i}-{w.bottom_tabs.tabText(i)}")

    # ---- 世界编辑器：空态 + 载入后 ----
    w.set_workspace("editor")
    pump(app, 20)
    shot(app, w, "09-world-empty")
    world = ROOT / "build" / "test-worlds" / "open"
    if world.exists():
        w.world_panel.async_open = False
        ok = w.world_panel.open_path(world)
        print("world open:", ok, flush=True)
        pump(app, 40)
        shot(app, w, "10-world-overview")
        tabs = w.world_panel.tabs
        for i in range(tabs.count()):
            tabs.setCurrentIndex(i)
            pump(app, 20)
            shot(app, w, f"11-worldtab-{i}-{tabs.tabText(i)}")

    # ---- 双栏模式下的世界编辑器（旧版这里点了没反应）----
    w.set_workspace("dual")
    w._focus_world_editor()
    pump(app, 25)
    shot(app, w, "12-dual-world")

    # ---- 引导条：世界只读 ----
    w.world_panel._refresh_session_state()
    pump(app, 20)
    shot(app, w, "13-guide-world-readonly")

    # ---- 深色主题 ----
    w._set_theme("dark")
    w.show_home()
    pump(app, 30)
    shot(app, w, "14-dark-home")
    w._goto_command("give")
    pump(app, 25)
    shot(app, w, "15-dark-give")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
