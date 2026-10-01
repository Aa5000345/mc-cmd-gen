"""MCStudio — 应用入口。

兼容性说明：旧的入口 `python main.py` 与打包脚本全部继续有效；
包名仍是 `app`，命令行工具仍是 `mcgen.py`（等价于 `mcstudio.py`）。
"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app import branding
from app.ui.main_window import MainWindow


def main() -> int:
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
