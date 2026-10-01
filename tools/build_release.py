"""打包 Windows 发行版（PyInstaller onedir + zip）。

用法：
    python tools/build_release.py              # 打包并压缩成 dist/MCStudio-<版本>-win64.zip
    python tools/build_release.py --console     # 保留控制台窗口（排查启动崩溃用）
    python tools/build_release.py --no-zip      # 只打包，不压缩

产物：
    dist/MCStudio/MCStudio.exe                ← 可直接运行的目录版
    dist/MCStudio-<版本>-win64.zip            ← 发行版附件（含 README）

为什么 onedir 而不是 onefile：PySide6 + Qt 插件解包到临时目录再启动，
onefile 首次启动要几秒到十几秒，onedir 是双击即开；压缩包里已经是绿色版。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import branding  # noqa: E402  （版本号的唯一事实来源）


def build(*, console: bool) -> Path:
    dist = ROOT / "dist"
    work = ROOT / "build" / "pyinstaller"
    for folder in (dist / "MCStudio", work):
        shutil.rmtree(folder, ignore_errors=True)

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", "MCStudio",
        "--distpath", str(dist),
        "--workpath", str(work),
        "--specpath", str(work),
        # 数据文件：data.py / nlp/engine.py 都用 __file__ 相对定位，
        # 所以这里必须保持同样的相对目录结构
        "--add-data", f"{ROOT / 'app' / 'data' / 'builtin'};app/data/builtin",
        "--add-data", f"{ROOT / 'app' / 'core' / 'nlp' / 'patterns.json'};app/core/nlp",
        # PySide6 的 Qt 插件（平台 / 样式 / 图片格式）按需收集
        "--collect-submodules", "nbtlib",
        "--hidden-import", "PySide6.QtSvg",
    ]
    args.append("--console" if console else "--windowed")
    args.append(str(ROOT / "main.py"))

    print("[build]", " ".join(args[1:6]), "…", flush=True)
    subprocess.run(args, cwd=ROOT, check=True)

    exe = dist / "MCStudio" / "MCStudio.exe"
    if not exe.is_file():                             # pragma: no cover
        raise SystemExit(f"打包结束但没找到 {exe}")
    warn = work / "MCStudio" / "warn-MCStudio.txt"
    if warn.is_file():
        misses = [line for line in warn.read_text(
            encoding="utf-8", errors="replace").splitlines()
            if line.startswith("missing module named app")]
        if misses:
            print("[build] ⚠ 缺少 app 子模块：")
            for line in misses[:10]:
                print("   ", line)
    return dist / "MCStudio"


def make_zip(folder: Path) -> Path:
    target = folder.parent / f"{folder.name}-{branding.VERSION}-win64.zip"
    readme = ROOT / "README.md"
    print(f"[zip] {target.name}", flush=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zf.write(path, Path("MCStudio") / path.relative_to(folder))
        if readme.is_file():
            zf.write(readme, "MCStudio/README.md")
    size_mb = target.stat().st_size / 1024 / 1024
    print(f"[zip] 完成：{target}（{size_mb:.1f} MB）")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 MCStudio Windows 发行版")
    parser.add_argument("--console", action="store_true",
                        help="保留控制台窗口（排查启动问题用）")
    parser.add_argument("--no-zip", action="store_true", help="只打包不压缩")
    ns = parser.parse_args()

    print(f"MCStudio {branding.VERSION} · 目标 Minecraft "
          f"{branding.TARGET_MC_VERSION}")
    folder = build(console=ns.console)
    if not ns.no_zip:
        make_zip(folder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
