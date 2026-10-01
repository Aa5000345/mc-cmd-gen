"""MCStudio — 应用入口。

兼容性说明：旧的入口 `python main.py` 与打包脚本全部继续有效；
包名仍是 `app`，命令行工具仍是 `mcgen.py`（等价于 `mcstudio.py`）。

自检
----
打好的绿色版不方便用命令行排查「双击没反应」：
    set MCSTUDIO_SELFTEST=1
    MCStudio.exe
会依次构造 QApplication / 打开数据库 / 读内置数据 / 建主窗口，逐条打印结果，
失败时返回码非 0（打包缺文件、缺 Qt 插件、缺 sqlite3 都能立刻看出来）。
"""
import os
import sys
import traceback

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app import branding
from app.ui.main_window import MainWindow


def selftest() -> int:
    """打包自检：每一步都打印一行，返回 0 表示全部通过。"""
    checks: list[tuple[str, object]] = []

    def step(label: str, fn):
        try:
            value = fn()
        except Exception as exc:
            checks.append((label, exc))
            print(f"[FAIL] {label}: {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
            return None
        checks.append((label, value))
        print(f"[ OK ] {label}: {value}", flush=True)
        return value

    print(f"MCStudio {branding.VERSION} selftest "
          f"(frozen={getattr(sys, 'frozen', False)})", flush=True)
    for name in ("MCSTUDIO_DATA_DIR", *branding.LEGACY_DATA_DIR_ENVS,
                 "MCSTUDIO_NO_MIGRATE", "QT_QPA_PLATFORM"):
        print(f"       env {name}={os.environ.get(name)!r}", flush=True)
    step("override 解析", branding.data_dir_override)

    def _try_override_mkdir():
        path = branding.data_dir_override()
        if path is None:
            return "没有设置环境变量"
        path.mkdir(parents=True, exist_ok=True)
        return f"可写：{path}"

    step("override 可写", _try_override_mkdir)
    step("数据目录", lambda: __import__(
        "app.storage", fromlist=["app_data_dir"]).app_data_dir())
    step("内置物品数", lambda: len(
        __import__("app.core.data", fromlist=["store"]).store().items()))
    step("内置方块数", lambda: len(
        __import__("app.core.data", fromlist=["store"]).store().blocks()))
    step("命令注册表", lambda: len(
        __import__("app.core", fromlist=["all_builders"]).all_builders()))
    step("nbtlib", lambda: __import__("nbtlib").__version__)
    step("sqlite3", lambda: __import__("sqlite3").sqlite_version)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv)
    step("QApplication", lambda: app.platformName())
    step("主窗口", lambda: type(MainWindow()).__name__)

    failed = [label for label, value in checks
              if isinstance(value, Exception)]
    if failed:
        print(f"[FAIL] 共 {len(failed)} 项失败：{'、'.join(failed)}", flush=True)
        return 1
    print("[ OK ] 自检全部通过", flush=True)
    return 0


def main() -> int:
    if os.environ.get("MCSTUDIO_SELFTEST", "").strip() not in ("", "0"):
        return selftest()

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName(branding.APP_NAME)
    app.setApplicationDisplayName(branding.APP_NAME)
    app.setOrganizationName(branding.ORG_NAME)
    app.setApplicationVersion(branding.VERSION)

    window = MainWindow()
    window.show()
    try:
        return app.exec()
    finally:
        # 兜底：确保 SQLite 连接关闭、WAL 落盘（closeEvent 里也会做一次）
        try:
            from app.storage import Database
            Database.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
