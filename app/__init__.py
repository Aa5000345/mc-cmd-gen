"""MCStudio（MC 工坊）— Minecraft Java 命令与世界编辑器。

原项目名 `MCCmdGen` 仍作为兼容别名保留：包的导入路径不变，CLI 入口
（`mcgen.py` / `mcstudio.py`）继续可用，数据目录迁移策略见
`app/branding.py` 与 `app/migration.py`。
"""
from app.branding import APP_NAME, APP_NAME_CN, VERSION

__version__ = VERSION

#: 兼容别名：部分脚本会写 `app.APP_VERSION`
APP_VERSION = VERSION
#: 旧名（兼容读取与展示）
LEGACY_NAME = "MCCmdGen"

__all__ = ["__version__", "APP_VERSION", "APP_NAME", "APP_NAME_CN",
           "LEGACY_NAME", "VERSION"]
