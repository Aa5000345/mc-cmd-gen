"""区域多区块批量替换。

典型需求：「把这个存档里所有区块的 `LightPopulated` 清掉」、
「所有区块的 DataVersion 跟着升到 1.21.1」。

做法刻意保守：

1. **先全量 dry-run**：每个候选区块都算一遍可行性，列出可写 / 被拒的原因；
2. 明确告诉用户「会动几个文件、写几个区块、几个会被跳过、为什么」；
3. 用户确认后，**每个区域文件只备份一次**（不是每个区块备份一次）；
4. 逐个区块走 `write_chunk`（等长替换 + 原子替换 + 写后自检）；
5. 抛出任何异常都记为失败，继续处理其它区块，最后给完整报告。

不做的事：不新建区块、不移动区块、不扩容扇区、不碰位置表的其它项。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QVBoxLayout,
)

from app.core.region_io import (
    backup_region, plan_chunk_write, verify_region, write_chunk,
)
from app.core.snbt import SnbtKind, SnbtNode, SnbtParser
from app.ui.theme import (
    FONTS, MARGINS, SPACING, muted_style, polish_form,
)


class _Cancelled(Exception):
    """用户在进度提示里点了「取消」——用异常从进度回调里跳出循环。"""


class _BatchWorker(QThread):
    """把 dry-run / 执行放到后台线程，界面才不会卡成「未响应」。

    进度用信号回主线程（Qt 跨线程信号是队列投递，安全）；
    取消通过 `cancel()` 置位，工作线程在下一个进度回调里抛出 `_Cancelled`。
    """

    progress = Signal(int, int, str)
    planned = Signal(object)          # ChunkBatchPlan
    executed = Signal(int, list, object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, world, *, mode: str, kwargs: dict | None = None,
                 plan=None, parent=None) -> None:
        super().__init__(parent)
        self._world = world
        self._mode = mode
        self._kwargs = kwargs or {}
        self._plan = plan
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def _tick(self, done: int, total: int, text: str) -> None:
        self.progress.emit(done, total, text)
        if self._cancel:
            raise _Cancelled

    def run(self) -> None:                            # noqa: D401 (Qt 命名)
        try:
            if self._mode == "plan":
                plan = plan_batch(self._world, progress=self._tick,
                                  **self._kwargs)
                self.planned.emit(plan)
                return
            done, failures, folder = execute_batch(
                self._world, self._plan, progress=self._tick)
            self.executed.emit(done, failures, folder)
        except _Cancelled:
            self.cancelled.emit()
        except Exception as e:                        # pragma: no cover
            self.failed.emit(str(e))

#: 支持的批量操作：(key, 标签, 说明)
BATCH_OPERATIONS: tuple[tuple[str, str, str], ...] = (
    ("set", "设置字段", "把某个路径写成固定取值（不存在则创建）"),
    ("remove", "删除字段", "删掉某个路径（区块空间会变小，更容易通过）"),
    ("replace_text", "文本替换", "在某个字符串字段里替换子串"),
    ("bump_data_version", "统一 DataVersion",
     "把 DataVersion 改成目标版本（升级存档结构时用）"),
)


@dataclass
class ChunkBatchPlan:
    """一次批量替换的完整计划。"""

    operation: str
    path: str = ""
    value: str = ""
    search: str = ""
    replace: str = ""
    data_version: int = 3955
    #: (path, chunk_x, chunk_z) → 计划 / 原因
    writable: list[tuple[str, int, int]] = field(default_factory=list)
    skipped: list[tuple[str, int, int, str]] = field(default_factory=list)
    changed: int = 0
    files: set[str] = field(default_factory=set)

    @property
    def total(self) -> int:
        return len(self.writable) + len(self.skipped)

    def summary(self) -> str:
        return (f"共 {self.total} 个区块：可写 {len(self.writable)}、"
                f"跳过 {len(self.skipped)}、涉及 {len(self.files)} 个区域文件")

    def report(self, limit: int = 40) -> str:
        lines = [self.summary(), ""]
        if self.writable:
            lines.append("■ 可写区块：")
            for path, x, z in self.writable[:limit]:
                lines.append(f"    {Path(path).name} ({x},{z})")
            if len(self.writable) > limit:
                lines.append(f"    … 另有 {len(self.writable) - limit} 个")
        if self.skipped:
            lines.append("")
            lines.append("■ 被跳过的区块：")
            for path, x, z, reason in self.skipped[:limit]:
                lines.append(f"    {Path(path).name} ({x},{z})：{reason}")
            if len(self.skipped) > limit:
                lines.append(f"    … 另有 {len(self.skipped) - limit} 个")
        return "\n".join(lines)


def apply_to_node(node: SnbtNode, operation: str, *, path: str = "",
                  value: str = "", search: str = "", replace: str = "",
                  data_version: int = 3955) -> bool:
    """在区块 NBT 上执行一次批量操作；返回是否真的改了内容。"""
    from app.core.world import parse_field_path, resolve_path, set_value_at

    if operation == "bump_data_version":
        current = node.get_child("DataVersion")
        if current is not None and int(current.value) == int(data_version):
            return False
        if current is None:
            node.add_child(SnbtNode.new_scalar(SnbtKind.INT, int(data_version),
                                               key="DataVersion"))
        else:
            current.kind = SnbtKind.INT
            current.value = int(data_version)
        return True

    if not path.strip():
        raise ValueError("缺少字段路径")
    tokens = parse_field_path(path)
    target = resolve_path(node, path)

    if operation == "remove":
        if target is None:
            return False
        parent = node
        for token in tokens[:-1]:
            parent = (parent.children[token] if isinstance(token, int)
                      else parent.get_child(token))
        if parent is None:                              # pragma: no cover
            return False
        last = tokens[-1]
        if isinstance(last, int):
            if last >= len(parent.children):            # pragma: no cover
                return False
            del parent.children[last]
            return True
        parent.remove_child(target)
        return True

    if operation == "replace_text":
        if target is None or target.is_container:
            return False
        if target.kind != SnbtKind.STRING:
            raise ValueError(f"{path} 不是字符串，无法做文本替换")
        text = str(target.value)
        if search not in text:
            return False
        replaced = text.replace(search, replace)
        if replaced == text:
            return False                    # 替换后没变（search == replace）
        target.value = replaced
        return True

    # set：先把取值当 SNBT 解析一遍，拿到正确的类型与值再写
    kind = target.kind if target is not None and not target.is_container \
        else None
    text = str(value).strip()
    parsed = None
    try:
        parsed = SnbtParser(text).parse()
    except Exception:
        parsed = None
    if parsed is not None and not parsed.is_container:
        # SNBT 的 `0b` / `1b` 会被解析成 Bool，但 NBT 里存的是 Byte；
        # 统一折算成 0/1，否则「写 0b」和「写 0」会被认为内容不同，
        # dry-run 就永远显示「有变化」。
        if parsed.kind == SnbtKind.BOOL:
            new_kind, new_value = SnbtKind.BYTE, (
                1 if parsed.value else 0)
        else:
            new_kind, new_value = parsed.kind, parsed.value
        if target is not None and not target.is_container \
                and target.kind == new_kind \
                and _same_value(target.value, new_value):
            return False                    # 已经是这个值，不算变化
        # 用户可能写 `0` 而字段是 Byte：类型不同但数值一样时也别白写一遍
        if target is not None and not target.is_container \
                and _same_value(target.value, new_value) \
                and target.kind in (SnbtKind.BYTE, SnbtKind.SHORT,
                                    SnbtKind.INT, SnbtKind.LONG,
                                    SnbtKind.BOOL) \
                and new_kind in (SnbtKind.BYTE, SnbtKind.SHORT,
                                 SnbtKind.INT, SnbtKind.LONG, SnbtKind.BOOL):
            return False
        set_value_at(node, path, new_value, kind=new_kind, create=True)
        return True
    if parsed is not None and parsed.is_container:
        from app.core.bridge import _write_path
        _write_path(world_like(node), path, parsed)
        return True
    # 解析不了就按字符串写（例如带空格的名字）
    set_value_at(node, path, text, kind=kind or SnbtKind.STRING, create=True)
    return True


def world_like(node: SnbtNode):
    """给 `_write_path` 用的最小「根节点」包装。

    `_write_path` 需要一个能按路径下钻的根；这里直接复用 `SnbtNode`
    自己（它有 `get_child` / `add_child`）。
    """
    return node


def _same_value(left, right) -> bool:
    """比较两个 NBT 标量取值是否等价（1 与 1.0、0 与 False 视为相同）。"""
    if left is right:
        return True
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) == bool(right)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    return str(left) == str(right)


def plan_batch(world, *, operation: str, path: str = "", value: str = "",
               search: str = "", replace: str = "", data_version: int = 3955,
               progress: Callable[[int, int, str], None] | None = None
               ) -> ChunkBatchPlan:
    """对存档里所有区块做 dry-run。"""
    from app.core.world import read_region_chunk

    plan = ChunkBatchPlan(operation=operation, path=path, value=value,
                          search=search, replace=replace,
                          data_version=data_version)
    candidates: list[tuple[str, int, int]] = []
    for region in world.regions.values():
        for chunk in region.chunks:
            if chunk.exists():
                candidates.append((str(region.path), chunk.x, chunk.z))

    total = len(candidates) or 1
    for index, (region_path, chunk_x, chunk_z) in enumerate(candidates, 1):
        if progress is not None:
            progress(index, total,
                     f"检查 {Path(region_path).name} ({chunk_x},{chunk_z})")
        node = read_region_chunk(region_path, chunk_x, chunk_z)
        if node is None:
            plan.skipped.append((region_path, chunk_x, chunk_z, "读不出区块"))
            continue
        try:
            changed = apply_to_node(node, operation, path=path, value=value,
                                    search=search, replace=replace,
                                    data_version=data_version)
        except Exception as e:
            plan.skipped.append((region_path, chunk_x, chunk_z,
                                 f"无法应用：{e}"))
            continue
        if not changed:
            plan.skipped.append((region_path, chunk_x, chunk_z, "没有变化"))
            continue
        chunk_plan = plan_chunk_write(region_path, chunk_x, chunk_z, node)
        if not chunk_plan.ok:
            plan.skipped.append((region_path, chunk_x, chunk_z,
                                 chunk_plan.reason))
            continue
        plan.writable.append((region_path, chunk_x, chunk_z))
        plan.files.add(region_path)
        plan.changed += len(chunk_plan.diff)
    return plan


def execute_batch(world, plan: ChunkBatchPlan, *,
                  progress: Callable[[int, int, str], None] | None = None
                  ) -> tuple[int, list[str], Path | None]:
    """执行批量替换：每个文件备份一次，逐区块原子替换 + 自检。"""
    from app.core.world import read_region_chunk

    folder = world.backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    backed: set[str] = set()
    failures: list[str] = []
    done = 0

    total = len(plan.writable) or 1
    for index, (region_path, chunk_x, chunk_z) in enumerate(plan.writable, 1):
        if progress is not None:
            progress(index, total,
                     f"写入 {Path(region_path).name} ({chunk_x},{chunk_z})")
        node = read_region_chunk(region_path, chunk_x, chunk_z)
        if node is None:                                # pragma: no cover
            failures.append(f"({chunk_x},{chunk_z}) 读取失败")
            continue
        try:
            apply_to_node(node, plan.operation, path=plan.path,
                          value=plan.value, search=plan.search,
                          replace=plan.replace,
                          data_version=plan.data_version)
        except Exception as e:
            failures.append(f"({chunk_x},{chunk_z}) 应用失败：{e}")
            continue
        try:
            if region_path not in backed:
                backup_region(region_path, folder)
                backed.add(region_path)
            write_chunk(region_path, chunk_x, chunk_z, node)
            problems = verify_region(region_path)
        except Exception as e:
            failures.append(f"({chunk_x},{chunk_z}) 写入失败：{e}")
            continue
        if problems:
            failures.append(f"({chunk_x},{chunk_z}) 自检："
                            + "；".join(problems[:2]))
            continue
        done += 1
    return done, failures, (folder if backed else None)


class RegionBatchDialog(QDialog):
    """区域多区块批量替换（两步：dry-run → 执行）。"""

    def __init__(self, parent=None, *, world=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("区域多区块批量替换")
        self.resize(760, 620)
        self._world = world
        self._plan: ChunkBatchPlan | None = None
        #: 正在跑 dry-run / 执行（用于防重入 + 禁用按钮）
        self._busy = False
        self._worker: _BatchWorker | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(*MARGINS["page"])
        outer.setSpacing(SPACING["sm"])

        hint = QLabel(
            "一次对所有区域文件的区块应用同一条修改。流程固定为："
            "① dry-run 逐个检查 → ② 看清「可写 / 跳过 + 原因」→ ③ 确认后"
            "每个文件备份一次、逐区块原子替换并自检。"
            "不会新建区块、不移动区块、不扩容扇区。")
        hint.setWordWrap(True)
        hint.setStyleSheet(muted_style(self))
        outer.addWidget(hint)

        form = QFormLayout()
        polish_form(form)
        self.operation = QComboBox()
        for key, label, _note in BATCH_OPERATIONS:
            self.operation.addItem(label, key)
        self.operation.currentIndexChanged.connect(self._on_operation_changed)
        form.addRow("操作", self.operation)

        self.path_edit = QLineEdit("DataVersion")
        self.path_edit.setPlaceholderText("字段路径，例如 DataVersion / Status")
        form.addRow("路径", self.path_edit)

        self.value_edit = QLineEdit("3955")
        self.value_edit.setPlaceholderText("SNBT 取值，例如 3955 / \"minecraft:full\"")
        form.addRow("取值", self.value_edit)

        text_row = QHBoxLayout()
        text_row.setSpacing(SPACING["xs"])
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("要查找的子串")
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("替换成")
        text_row.addWidget(self.search_edit, 1)
        text_row.addWidget(QLabel("→"))
        text_row.addWidget(self.replace_edit, 1)
        form.addRow("文本替换", text_row)

        self.version_spin = QSpinBox()
        self.version_spin.setRange(0, 99999)
        self.version_spin.setValue(3955)
        form.addRow("目标 DataVersion", self.version_spin)
        outer.addLayout(form)
        self._executed = 0
        self._failures: list[str] = []

        row = QHBoxLayout()
        row.setSpacing(SPACING["xs"])
        self.btn_plan = QPushButton("① 全量 dry-run")
        self.btn_plan.clicked.connect(self.run_plan)
        row.addWidget(self.btn_plan)
        self.btn_execute = QPushButton("② 执行（先备份）")
        self.btn_execute.setEnabled(False)
        self.btn_execute.clicked.connect(self._on_execute)
        row.addWidget(self.btn_execute)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setToolTip("中止后台的 dry-run / 写入（已写入的部分不会回滚）")
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        row.addWidget(self.btn_cancel)
        row.addStretch(1)
        self.count_label = QLabel("还没有计划")
        self.count_label.setStyleSheet(muted_style(self))
        row.addWidget(self.count_label)
        outer.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(10)
        self.progress.setVisible(False)
        outer.addWidget(self.progress)

        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setFont(QFont("Consolas", FONTS["mono"]))
        self.report.setPlaceholderText("dry-run 结果会显示在这里")
        outer.addWidget(self.report, 1)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close,
                               parent=self)
        box.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        box.rejected.connect(self.reject)
        outer.addWidget(box)

        # 字段显隐最后由这里统一刷一遍（此时所有控件都已建好）
        self._on_operation_changed(0)

    # ---- 交互 ----

    def _on_operation_changed(self, _index: int) -> None:
        key = str(self.operation.currentData())
        is_text = key == "replace_text"
        is_version = key == "bump_data_version"
        self.search_edit.setEnabled(is_text)
        self.replace_edit.setEnabled(is_text)
        self.value_edit.setEnabled(not (is_text or is_version))
        self.path_edit.setEnabled(not is_version)
        self.version_spin.setEnabled(is_version)
        self.btn_execute.setEnabled(False)
        self._plan = None
        note = next(note for k, _l, note in BATCH_OPERATIONS if k == key)
        self.count_label.setText(note)

    def _current_kwargs(self) -> dict:
        return {
            "operation": str(self.operation.currentData()),
            "path": self.path_edit.text().strip(),
            "value": self.value_edit.text().strip(),
            "search": self.search_edit.text(),
            "replace": self.replace_edit.text(),
            "data_version": int(self.version_spin.value()),
        }

    def run_plan(self) -> None:
        if self._busy:
            return                                   # 防重入
        if self._world is None or not self._world.is_open():
            self.report.setPlainText("没有打开世界存档。")
            return
        kwargs = self._current_kwargs()
        if kwargs["operation"] not in ("bump_data_version",) \
                and not kwargs["path"]:
            self.report.setPlainText("请先填字段路径。")
            return

        worker = self._start_worker("plan", kwargs=kwargs)
        worker.planned.connect(self._on_planned)

    def _on_planned(self, plan) -> None:
        self._plan = plan
        self.report.setPlainText(plan.report(limit=60))
        self.count_label.setText(plan.summary())
        self.btn_execute.setEnabled(bool(plan.writable))

    def _on_execute(self) -> None:
        if self._busy:
            return                                   # 防重入：旧版执行期间还能再点
        if self._plan is None or not self._plan.writable:
            return
        answer = QMessageBox.question(
            self, "确认批量替换",
            f"{self._plan.summary()}\n\n将写入 {len(self._plan.writable)} 个区块，"
            f"涉及 {len(self._plan.files)} 个区域文件（每个文件只备份一次）。"
            "\n\n确认继续？",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Ok:
            return

        worker = self._start_worker("execute", plan=self._plan)
        worker.executed.connect(self._on_executed)

    def _on_executed(self, done: int, failures: list,
                     folder) -> None:
        self._executed = done
        self._failures = list(failures)
        lines = [f"■ 已写入 {done} 个区块"]
        if folder is not None:
            lines.append(f"■ 备份目录：backups/{folder.name}")
        if failures:
            lines.append(f"■ {len(failures)} 个失败：")
            lines.extend(f"    {item}" for item in failures[:30])
        self.report.setPlainText("\n".join(lines))
        self.count_label.setText(f"完成：写入 {done}、失败 {len(failures)}")
        self.btn_execute.setEnabled(False)

    # ---- 后台任务：进度条 + 取消 ----

    def _start_worker(self, mode: str, *, kwargs: dict | None = None,
                      plan=None) -> _BatchWorker:
        worker = _BatchWorker(self._world, mode=mode, kwargs=kwargs,
                              plan=plan, parent=self)
        worker.progress.connect(self._on_progress)
        worker.failed.connect(self._on_worker_failed)
        worker.cancelled.connect(self._on_worker_cancelled)
        worker.finished.connect(lambda: self._end_busy(worker))

        self._busy = True
        self._worker = worker
        self.btn_plan.setEnabled(False)
        self.btn_execute.setEnabled(False)
        self.progress.setRange(0, 0)                  # 未知总量：先走「不确定」态
        self.progress.setVisible(True)
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.setText("取消")
        label = ("正在 dry-run 全部区块…" if mode == "plan"
                 else "正在写入区块（每个区域文件先备份一次）…")
        self.count_label.setText(label)
        self.report.setPlainText(label + "\n\n界面没有卡住：任务在后台线程跑，"
                                 "随时可以点「取消」。")
        worker.start()
        return worker

    def _on_progress(self, done: int, total: int, text: str) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(min(done, max(1, total)))
        self.count_label.setText(f"{done}/{total} · {text}")

    def _on_worker_failed(self, message: str) -> None:
        self.report.setPlainText(f"失败：{message}")
        self.count_label.setText("失败")

    def _on_worker_cancelled(self) -> None:
        if self._worker is not None and self._worker._mode == "execute":
            self.report.setPlainText(
                "已取消：**已经写入的区块不会回滚**，"
                "请用「差异」页签或 backups/ 里的备份检查。")
            self.count_label.setText("已取消（部分区块可能已写入）")
        else:
            self.report.setPlainText("已取消 dry-run（没有写入任何文件）。")
            self.count_label.setText("已取消")

    def _on_cancel_clicked(self) -> None:
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            self.btn_cancel.setEnabled(False)
            self.btn_cancel.setText("正在取消…")

    def _end_busy(self, worker=None) -> None:
        self._busy = False
        self._worker = None
        self.btn_plan.setEnabled(True)
        self.progress.setVisible(False)
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.setText("取消")
        if worker is not None:
            worker.deleteLater()

    # ---- 关闭时的收尾 ----

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """后台还在跑时不要直接销毁对话框。

        `_BatchWorker` 是 QThread：如果它还在运行时父对象被 delete，
        Qt 会报「QThread: Destroyed while thread is still running」并可能崩溃。
        这里的做法：请求取消 → 等它收尾（最多 5 秒）→ 再允许关闭。
        """
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            self.btn_cancel.setEnabled(False)
            self.btn_cancel.setText("正在取消…")
            if not worker.wait(5000):                    # pragma: no cover
                QMessageBox.information(
                    self, "还在收尾",
                    "后台任务还在取消中，请稍等几秒再关闭。")
                event.ignore()
                return
            self._end_busy(worker)
        super().closeEvent(event)

    # ---- 结果 ----

    def executed(self) -> int:
        return self._executed

    def failures(self) -> list[str]:
        return list(self._failures)


__all__ = [
    "BATCH_OPERATIONS", "ChunkBatchPlan", "apply_to_node", "plan_batch",
    "execute_batch", "RegionBatchDialog",
]
