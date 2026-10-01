"""导入健壮性：所有模块都能导入，且不会出现同名文件遮蔽目录的回归。"""
from __future__ import annotations

import importlib
import pathlib
import unittest

from tests import TEST_ROOT  # noqa: F401  （导入即设置环境变量）


class TestImports(unittest.TestCase):
    def test_all_modules_import(self) -> None:
        root = pathlib.Path(TEST_ROOT)
        modules = set()
        for path in sorted((root / "app").rglob("*.py")):
            parts = list(path.relative_to(root).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            modules.add(".".join(parts))
        failures = []
        for name in sorted(modules):
            try:
                importlib.import_module(name)
            except Exception as e:                     # pragma: no cover
                failures.append(f"{name}: {type(e).__name__}: {e}")
        self.assertEqual(failures, [], f"以下模块导入失败：{failures}")

    def test_no_shadowed_package_dirs(self) -> None:
        """同名 .py 与目录共存会让子模块不可导入。"""
        root = pathlib.Path(TEST_ROOT) / "app"
        conflicts = []
        for path in root.rglob("*.py"):
            if path.stem == "__init__":
                continue
            twin = path.with_suffix("")
            if twin.is_dir():
                conflicts.append(str(path.relative_to(TEST_ROOT)))
        self.assertEqual(conflicts, [], f"存在同名冲突：{conflicts}")

    def test_package_dirs_have_init(self) -> None:
        root = pathlib.Path(TEST_ROOT) / "app"
        missing = []
        for d in root.rglob("*"):
            if not d.is_dir() or d.name == "__pycache__":
                continue
            if d.name == "builtin":          # 纯数据目录
                continue
            if not (d / "__init__.py").exists():
                missing.append(str(d.relative_to(TEST_ROOT)))
        self.assertEqual(missing, [], f"缺少 __init__.py：{missing}")

    def test_critical_submodules_importable(self) -> None:
        for name in ("app.core.builders.rules",
                     "app.ui.dialogs.diff_dialog",
                     "app.ui.dialogs.shortcuts_dialog",
                     "app.ui.widgets.command_search",
                     "app.ui.widgets.nlp_input",
                     "app.ui.widgets.output_edit",
                     "app.ui.panels",
                     "app.ui.main_window"):
            with self.subTest(module=name):
                self.assertIsNotNone(importlib.import_module(name))


if __name__ == "__main__":
    unittest.main()
