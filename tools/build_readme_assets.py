"""生成 README 用的图（banner / 架构图 / 功能截图 / 快速上手 GIF）。

    python tools/build_readme_assets.py            # 全部生成到 docs/images/
    python tools/build_readme_assets.py --only gif # 只重做某一部分

产物（都在 docs/images/，方便 GitHub 直接渲染）：
    banner.png            顶部横幅
    architecture.png      三栏 + 联动总线架构图
    home.png / palette.png / give.png / world-empty.png / world-overview.png
    nbt-editor.png / batch.png / reference.png / dark-home.png / guide-strip.png
    quickstart.gif        「首页 → Ctrl+K → /give → 复制」动图

所有画面都是**离屏渲染**真实界面（不是手绘 mock），所以文档里的图和实际
程序永远一致；改完界面重跑本脚本即可。
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "docs" / "images"
DATA = ROOT / "build" / "readme-data"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["MCSTUDIO_DATA_DIR"] = str(DATA)
os.environ["MCSTUDIO_NO_MIGRATE"] = "1"

from PIL import Image, ImageDraw, ImageFont      # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase     # noqa: E402
from PySide6.QtWidgets import QApplication       # noqa: E402

from app import branding                          # noqa: E402
from app.ui.main_window import MainWindow         # noqa: E402

# ---- 配色（与深色主题一致，截图和配图放在一起不违和）----
BG = (13, 17, 23)
PANEL = (22, 27, 34)
PANEL2 = (28, 36, 48)
BORDER = (48, 54, 61)
TEXT = (201, 209, 217)
MUTED = (139, 148, 158)
ACCENT = (31, 111, 235)
GREEN = (35, 134, 54)
ORANGE = (210, 153, 34)

FONT_CANDIDATES = (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
                   r"C:\Windows\Fonts\simhei.ttf")
MONO_CANDIDATES = (r"C:\Windows\Fonts\consola.ttf",)
#: 离屏渲染时 Qt 找不到系统字体目录，✔ ▶ ⚙ 这类符号会变成方框；
#: 手动把符号字体也塞进字体库，截图里的界面才和真实桌面一致
SYMBOL_CANDIDATES = (r"C:\Windows\Fonts\seguisym.ttf",
                     r"C:\Windows\Fonts\segoeui.ttf",
                     r"C:\Windows\Fonts\symbol.ttf")

SHOT_WIDTH = 1200         # README 里图片的显示宽度
GIF_WIDTH = 900


def font(size: int, bold: bool = False):
    path = (r"C:\Windows\Fonts\msyhbd.ttc" if bold else None)
    for candidate in ([path] if path else []) + list(FONT_CANDIDATES):
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:                              # pragma: no cover
            continue
    return ImageFont.load_default()                    # pragma: no cover


def mono(size: int):
    for candidate in MONO_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:                              # pragma: no cover
            continue
    return font(size)                                  # pragma: no cover


# ---------------------------------------------------------------------------
# 1) 横幅 + 架构图（Pillow 直接画）
# ---------------------------------------------------------------------------

def make_banner() -> None:
    width, height = 1280, 340
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    for y in range(height):                            # 顶部渐亮
        alpha = 1 - y / height
        draw.line([(0, y), (width, y)],
                  fill=(int(BG[0] + 16 * alpha), int(BG[1] + 20 * alpha),
                        int(BG[2] + 34 * alpha)))
    draw.rectangle([0, 0, width, 4], fill=ACCENT)

    draw.text((64, 74), branding.APP_NAME, font=font(64, bold=True), fill=TEXT)
    draw.text((66, 150), "MC 工坊 · Minecraft Java 1.21.1 命令生成器 + 世界 / NBT 编辑器",
              font=font(26), fill=MUTED)

    chips = ("44 条命令", "NBT 编辑器", "世界存档编辑", "数据包工程", "中文写命令",
             "深色 / 浅色")
    x, y = 66, 214
    for text in chips:
        w = draw.textlength(text, font=font(19)) + 30
        draw.rounded_rectangle([x, y, x + w, y + 40], radius=20,
                               fill=PANEL2, outline=BORDER)
        draw.text((x + 15, y + 9), text, font=font(19), fill=TEXT)
        x += w + 12

    draw.text((width - 250, height - 46), f"v{branding.VERSION}",
              font=font(22, bold=True), fill=ACCENT)
    img.save(OUT / "banner.png")
    print("saved banner.png", flush=True)


def _box(draw, xy, title, lines, *, fill=PANEL, outline=BORDER,
         title_font=None, body_font=None, accent=None):
    draw.rounded_rectangle(xy, radius=10, fill=fill, outline=outline, width=2)
    x0, y0, x1, _y1 = xy
    draw.text((x0 + 16, y0 + 12), title,
              font=title_font or font(22, bold=True),
              fill=accent or TEXT)
    yy = y0 + 48
    for line in lines:
        draw.text((x0 + 16, yy), line, font=body_font or font(17), fill=MUTED)
        yy += 26


def make_architecture() -> None:
    width, height = 1280, 560
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    draw.text((40, 30), "MCStudio 架构：三栏工作区 + 联动总线 + 事务化写入",
              font=font(28, bold=True), fill=TEXT)
    draw.text((40, 70), "左边生成命令、中间改存档、右边看结果；两侧靠结构化对象互推，"
                        "所有写盘都先备份、可撤销。",
              font=font(18), fill=MUTED)

    top = 118
    _box(draw, (40, top, 420, top + 150), "命令生成器",
         ["命令树 / 筛选", "44 条命令图形面板", "NBT 与物品组件编辑", "收藏 · 片段 · 批量"],
         accent=(121, 192, 255))
    _box(draw, (450, top, 830, top + 150), "编辑器（世界 / NBT）",
         ["世界总览 · 玩家 · 实体 · 容器", "地图 · 区域 · 数据包 · 蓝图",
          "中文世界编辑 · 差异 · 健康检查"],
         accent=(126, 231, 135))
    _box(draw, (860, top, 1240, top + 150), "输出 / 联动总线",
         ["命令输出（复制 / 收藏）", "结构化对象互推", "差异预览 + 备份 + 撤销"],
         accent=(255, 166, 87))

    bar = (40, top + 186, 1240, top + 246)
    draw.rounded_rectangle(bar, radius=10, fill=(18, 35, 58), outline=ACCENT,
                           width=2)
    draw.text((64, top + 200), "联动总线 · 统一上下文",
              font=font(22, bold=True), fill=(121, 192, 255))
    draw.text((360, top + 206),
              "current_world / player / dimension / pos / selected_item / entity / block / nbt_path",
              font=mono(16), fill=TEXT)

    for x in (230, 640, 1050):                          # 三栏 → 总线
        draw.line([(x, top + 150), (x, top + 186)], fill=BORDER, width=3)
        draw.polygon([(x - 6, top + 180), (x + 6, top + 180), (x, top + 190)],
                     fill=ACCENT)

    core_y = top + 282
    draw.text((40, core_y - 6), "核心层（与界面解耦，CLI 与 GUI 共用）",
              font=font(20, bold=True), fill=TEXT)
    modules = (("core.builders", "44 条命令 → SNBT"),
               ("core.world", "level.dat / 区域索引"),
               ("core.nbt_binary", "NBT 二进制读写"),
               ("core.bridge", "命令 → 存档（事务）"),
               ("storage", "SQLite 历史 / 收藏 / 片段"))
    x = 40
    for name, desc in modules:
        w = 236
        draw.rounded_rectangle([x, core_y + 30, x + w, core_y + 122], radius=8,
                               fill=PANEL, outline=BORDER)
        draw.text((x + 14, core_y + 42), name, font=mono(17), fill=(121, 192, 255))
        draw.text((x + 14, core_y + 72), desc, font=font(16), fill=MUTED)
        x += w + 12

    draw.text((40, height - 44),
              "写入链路：备份 → 内存模型 → 校验 → 差异预览 → 确认 → 原子替换 → 可回滚",
              font=font(18), fill=GREEN)
    img.save(OUT / "architecture.png")
    print("saved architecture.png", flush=True)


# ---------------------------------------------------------------------------
# 2) 真实界面截图（离屏）
# ---------------------------------------------------------------------------

def load_fonts(app=None) -> None:
    for candidate in FONT_CANDIDATES + MONO_CANDIDATES + SYMBOL_CANDIDATES:
        for path in glob.glob(candidate):
            QFontDatabase.addApplicationFont(path)
    if app is None:
        return
    # 显式给出回退链：中文字体缺 ▾ ✔ ▶ 这类符号时用 Segoe UI Symbol 兜底，
    # 否则离屏截图里这些字符会渲染成方框（真实桌面上 Qt 会自己回退）
    base = QFont("Microsoft YaHei UI")
    base.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei",
                      "Segoe UI Symbol", "Segoe UI", "Consolas"])
    base.setPointSize(9)
    app.setFont(base)


def pump(app, n: int = 20) -> None:
    for _ in range(n):
        app.processEvents()
        time.sleep(0.004)


def grab(window) -> Image.Image:
    path = ROOT / "build" / "_grab.png"
    window.grab().save(str(path))
    image = Image.open(path).convert("RGB")
    path.unlink(missing_ok=True)
    return image


def fit(image: Image.Image, width: int) -> Image.Image:
    if image.width == width:
        return image
    height = round(image.height * width / image.width)
    return image.resize((width, height), Image.LANCZOS)


def save(image: Image.Image, name: str, width: int = SHOT_WIDTH) -> None:
    fit(image, width).save(OUT / name, optimize=True)
    print(f"saved {name}", flush=True)


def caption(image: Image.Image, text: str) -> Image.Image:
    """在图片左下角加一条说明，方便动图里讲清每一步。"""
    canvas = image.copy()
    draw = ImageDraw.Draw(canvas, "RGBA")
    pad, size = 18, 26
    label = font(size, bold=True)
    w = draw.textlength(text, font=label) + pad * 2
    y0 = canvas.height - 62
    draw.rounded_rectangle([24, y0, 24 + w, y0 + 44], radius=10,
                           fill=(31, 111, 235, 235))
    draw.text((24 + pad, y0 + 9), text, font=label, fill=(255, 255, 255))
    return canvas


def build_shots() -> int:
    if DATA.exists():
        shutil.rmtree(DATA, ignore_errors=True)
    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts(app)
    window = MainWindow()
    window.resize(1600, 950)
    window.show()
    pump(app, 40)

    # 首页（首启）
    window.show_home()
    pump(app, 25)
    save(grab(window), "home.png")

    # 命令面板（Ctrl+K）
    window.open_palette()
    pump(app, 30)
    save(grab(window._palette), "palette.png", width=760)
    window._palette.search.setText("存档")
    pump(app, 20)
    save(grab(window._palette), "palette-search.png", width=760)
    window._palette.close()
    pump(app, 10)

    # /give + 引导条 + 复制提示
    window._goto_command("give")
    pump(app, 25)
    save(grab(window), "give.png")
    window.give_panel.target_line.setText("@a")
    pump(app, 20)
    window._on_copy_output()
    pump(app, 6)
    save(grab(window), "toast.png")

    # 引导条特写（窗口底部那条）
    strip = grab(window).crop((0, 740, 1600, 820))
    save(strip, "guide-strip.png", width=1200)

    # 单条命令的图形面板（/execute）
    window._goto_command("execute")
    pump(app, 25)
    save(grab(window), "execute.png")

    # 批量生成（首屏引导页）
    window._ensure_side_panes()
    window.center_tabs.setCurrentWidget(window.batch_panel)
    pump(app, 25)
    save(grab(window), "batch.png")

    # ID 速查
    window.center_tabs.setCurrentWidget(window.reference_panel)
    pump(app, 25)
    save(grab(window), "reference.png")

    # NBT 编辑器
    window._focus_nbt_editor()
    pump(app, 25)
    save(grab(window), "nbt-editor.png")

    # 世界编辑器：空态
    window.set_workspace("editor")
    pump(app, 25)
    save(grab(window), "world-empty.png")

    # 世界编辑器：打开存档（只读，设置灰显）
    world = ROOT / "build" / "test-worlds" / "open"
    if not world.exists():
        from tools.fake_world import build_fake_world
        world = build_fake_world(ROOT / "build" / "readme-world")
    window.world_panel.async_open = False
    window.world_panel.open_path(world)
    pump(app, 40)
    window.world_panel.tabs.setCurrentIndex(0)
    pump(app, 20)
    save(grab(window), "world-overview.png")

    # 数据包工程
    index = window.world_panel._tab_index("数据包工程")
    window.world_panel.tabs.setCurrentIndex(index)
    pump(app, 25)
    save(grab(window), "world-datapack.png")

    # 深色主题首页
    window._set_theme("dark")
    window.show_home()
    pump(app, 30)
    save(grab(window), "dark-home.png")

    window.close()
    return 0


# ---------------------------------------------------------------------------
# 3) 快速上手动图
# ---------------------------------------------------------------------------

def build_gif() -> int:
    if DATA.exists():
        shutil.rmtree(DATA, ignore_errors=True)
    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts(app)
    window = MainWindow()
    window.resize(1500, 900)
    window.show()
    window._set_theme("dark")
    pump(app, 40)

    frames: list[Image.Image] = []

    def snap(text: str) -> None:
        pump(app, 22)
        frames.append(caption(fit(grab(window), GIF_WIDTH), text))

    window.show_home()
    snap("① 首页挑一个任务")

    window.open_palette()
    pump(app, 25)
    frames.append(caption(fit(grab(window._palette), GIF_WIDTH),
                          "② Ctrl+K 搜命令 / 功能"))
    window._palette.search.setText("give")
    pump(app, 20)
    frames.append(caption(fit(grab(window._palette), GIF_WIDTH),
                          "③ 支持中文与英文关键词"))
    window._palette.close()
    window._goto_command("give")
    snap("④ 选好命令，左边填参数")

    window.give_panel.target_line.setText("@a")
    snap("⑤ 命令实时出现在右侧输出区")

    window._on_copy_output()
    pump(app, 8)
    frames.append(caption(fit(grab(window), GIF_WIDTH),
                          "⑥ 点「复制命令」，到游戏里粘贴"))

    window.close()

    gif = OUT / "quickstart.gif"
    frames[0].save(gif, save_all=True, append_images=frames[1:],
                   duration=[1400, 1100, 1100, 1200, 1200, 2200], loop=0,
                   optimize=True)
    print(f"saved quickstart.gif ({gif.stat().st_size / 1024:.0f} KB)",
          flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 README 配图")
    parser.add_argument("--only", choices=("art", "shots", "gif"),
                        help="只生成某一部分")
    ns = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    if ns.only in (None, "art"):
        make_banner()
        make_architecture()
    if ns.only in (None, "shots"):
        build_shots()
    if ns.only in (None, "gif"):
        build_gif()
    total = sum(p.stat().st_size for p in OUT.iterdir() if p.is_file())
    print(f"docs/images 共 {len(list(OUT.iterdir()))} 个文件，"
          f"{total / 1024 / 1024:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
