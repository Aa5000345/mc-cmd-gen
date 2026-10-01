"""旧数据目录（MCCmdGen）→ 新数据目录（mcstudio）的一次性迁移。

设计原则
--------
* **只复制、不删除**：旧目录原样保留，出问题可以随时回退；
* **幂等**：已经有新目录数据时逐个文件跳过（`data.db` 只在目标不存在时复制，
  避免把新写入的历史覆盖回旧版本）；
* **不依赖 Qt**：可在 CLI / 测试里直接调用；
* **失败不阻塞启动**：任何异常都记录进报告，主程序继续用旧目录。

迁移清单（旧目录根下）：

    data.db, data.db-wal, data.db-shm        历史 / 收藏 / 片段 / 草稿 / 布局
    user_data/**                             设置、自定义校验规则、用户数据
    scenes/**                                场景
    update_source.txt                        联网更新源
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.branding import (
    APP_DIR_NAME, legacy_data_dirs, platform_data_dir, skip_migration,
)

#: 记录迁移结果的标记文件（放在新目录里）
MIGRATION_MARKER = "migration.json"

#: 迁移的顶层条目
MIGRATE_ENTRIES: tuple[str, ...] = (
    "data.db", "data.db-wal", "data.db-shm",
    "user_data", "scenes", "update_source.txt",
)


@dataclass
class MigrationReport:
    """迁移结果。"""

    source: Path | None = None
    target: Path | None = None
    copied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    already: bool = False

    @property
    def moved(self) -> bool:
        """是否真的迁移了数据。"""
        return bool(self.copied)

    def summary(self) -> str:
        if self.already:
            return "数据目录已迁移过，无需重复处理。"
        if not self.source:
            return "没有发现旧数据目录，无需迁移。"
        if self.errors:
            return (f"迁移部分失败：复制 {len(self.copied)} 项、"
                    f"跳过 {len(self.skipped)} 项、错误 {len(self.errors)} 项")
        if not self.copied:
            return f"旧数据目录无需迁移（跳过 {len(self.skipped)} 项）。"
        return (f"已从 {self.source.name} 迁移 {len(self.copied)} 项到 "
                f"{self.target.name}（旧目录保留）。")

    def details(self) -> list[str]:
        out = [f"复制：{name}" for name in self.copied]
        out += [f"跳过：{name}" for name in self.skipped]
        out += [f"错误：{name}" for name in self.errors]
        return out


def new_data_dir() -> Path:
    return platform_data_dir(APP_DIR_NAME)


def find_legacy_dir() -> Path | None:
    """返回第一个「存在且有内容」的旧数据目录。"""
    for path in legacy_data_dirs():
        try:
            if path.is_dir() and any(path.iterdir()):
                return path
        except OSError:
            continue
    return None


def _has_payload(path: Path) -> bool:
    try:
        return path.is_dir() and any(path.iterdir())
    except OSError:
        return False


def migrate_data_dir(source: Path | None = None,
                     target: Path | None = None,
                     force: bool = False) -> MigrationReport:
    """把旧数据目录复制到新数据目录（幂等）。"""
    report = MigrationReport()
    if not force and skip_migration():
        report.skipped.append("(已通过 MCSTUDIO_NO_MIGRATE 关闭自动迁移)")
        return report

    source = Path(source) if source else find_legacy_dir()
    target = Path(target) if target else new_data_dir()
    if source is None or not _has_payload(source):
        return report
    if source.resolve() == target.resolve():                # pragma: no cover
        return report

    report.source, report.target = source, target
    marker = target / MIGRATION_MARKER
    if marker.exists() and not force:
        report.already = True
        return report

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        report.errors.append(f"无法创建新数据目录：{e}")
        return report

    for name in MIGRATE_ENTRIES:
        src = source / name
        if not src.exists():
            continue
        dst = target / name
        try:
            if src.is_dir():
                # 目录逐文件复制：已存在的新文件不覆盖（保留新数据）
                copied = _merge_tree(src, dst)
                (report.copied if copied else report.skipped).append(f"{name}/")
            else:
                if dst.exists():
                    report.skipped.append(name)
                    continue
                shutil.copy2(src, dst)
                report.copied.append(name)
        except Exception as e:                              # pragma: no cover
            report.errors.append(f"{name}: {e}")

    try:
        marker.write_text(json.dumps({
            "migrated_at": datetime.now().isoformat(timespec="seconds"),
            "source": str(source),
            "target": str(target),
            "copied": report.copied,
            "skipped": report.skipped,
            "errors": report.errors,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:                                        # pragma: no cover
        pass
    return report


def _merge_tree(src: Path, dst: Path) -> bool:
    """把 src 目录合并进 dst（不覆盖已存在文件），返回是否复制过内容。"""
    copied = False
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target_item = dst / item.name
        if item.is_dir():
            copied = _merge_tree(item, target_item) or copied
        elif not target_item.exists():
            shutil.copy2(item, target_item)
            copied = True
    return copied


__all__ = [
    "MigrationReport", "MIGRATION_MARKER", "MIGRATE_ENTRIES",
    "migrate_data_dir", "find_legacy_dir", "new_data_dir",
]
