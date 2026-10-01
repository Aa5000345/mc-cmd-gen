"""测试包：把应用数据目录重定向到临时目录，避免污染真实用户数据。

必须在任何 app.* 模块导入之前设置环境变量。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Qt 离屏，避免 CI/无显示环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMP = Path(tempfile.mkdtemp(prefix="mccmdgen-test-"))
os.environ["LOCALAPPDATA"] = str(_TMP)
os.environ["APPDATA"] = str(_TMP)
os.environ["XDG_DATA_HOME"] = str(_TMP)

# 让测试能直接 import app / main
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 各测试模块统一从这里取根目录（避免"仅用于副作用的导入"）
TEST_ROOT = ROOT

__all__ = ["TEST_ROOT", "ROOT", "_TMP"]

