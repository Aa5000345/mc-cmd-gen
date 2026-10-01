"""世界编辑会话：只读门禁 + 事务化写入 + 撤销栈。

为什么要有这一层
----------------
直接改 NBT 再写盘很危险：写坏了存档就打不开了。这里把所有写入都收进
`WorldTransaction`：

    备份 → 修改内存模型 → 校验 → dry-run 差异 → 确认 → 原子写入 → 可回滚

* 默认 **只读**：`WorldSession.read_only` 为 True 时任何写入都会被拒绝，
  必须 `unlock()`（GUI 里对应「解锁编辑」按钮 + 二次确认）；
* `dry_run()` 只做差异计算，不碰磁盘，用于「安全审阅模式」；
* `commit()` 真正写盘前会自动备份到 `backups/<时间戳>/`；
* 撤销栈保存的是 **节点级快照**（写时复制：只克隆被改动的子树），
  限制 100 步，大对象不会把内存吃满。

不依赖 Qt。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.snbt import SnbtNode, serialize
from app.core.world import (
    WorldSave, open_world,
)

#: 撤销栈上限
UNDO_LIMIT = 100


# ---------------------------------------------------------------------------
# 差异
# ---------------------------------------------------------------------------

@dataclass
class DiffEntry:
    """一条字段级差异。"""

    target: str                 # level / 玩家 UUID / map:0
    path: str                   # Data.DayTime
    before: str = ""
    after: str = ""
    kind: str = "change"        # change / add / remove

    def summary(self) -> str:
        mark = {"change": "~", "add": "+", "remove": "-"}.get(self.kind, "~")
        prefix = f"{self.target}:" if self.target != "level" else ""
        before = self.before if self.before else "(无)"
        after = self.after if self.after else "(无)"
        return f"{mark} {prefix}{self.path}: {before} → {after}"


@dataclass
class WorldDiff:
    """一次操作前后的完整差异。"""

    entries: list[DiffEntry] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    label: str = ""

    def __bool__(self) -> bool:
        return bool(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def text(self, limit: int = 200) -> str:
        if not self.entries:
            return "（没有差异）"
        lines = [entry.summary() for entry in self.entries[:limit]]
        if len(self.entries) > limit:
            lines.append(f"… 共 {len(self.entries)} 处差异")
        return "\n".join(lines)

    def as_dicts(self) -> list[dict[str, str]]:
        return [{"target": e.target, "path": e.path, "before": e.before,
                 "after": e.after, "kind": e.kind} for e in self.entries]


def _node_text(node: SnbtNode | None) -> str:
    if node is None:
        return ""
    if node.is_container:
        text = serialize(node, pretty=False)
        return text if len(text) <= 120 else text[:117] + "…"
    return serialize(node, pretty=False)


def diff_roots(before: SnbtNode | None, after: SnbtNode | None,
               target: str = "level", limit: int = 2000) -> list[DiffEntry]:
    """比较两棵 NBT（结构 + 取值），返回字段级差异。"""
    out: list[DiffEntry] = []
    _diff_nodes(before, after, "", target, out, limit)
    return out


def _diff_nodes(before: SnbtNode | None, after: SnbtNode | None,
                path: str, target: str, out: list[DiffEntry],
                limit: int) -> None:
    if len(out) >= limit:                               # pragma: no cover
        return
    if before is None and after is None:                # pragma: no cover
        return
    if before is None:
        out.append(DiffEntry(target, path or "(根)", "", _node_text(after),
                             "add"))
        return
    if after is None:
        out.append(DiffEntry(target, path or "(根)", _node_text(before), "",
                             "remove"))
        return

    before_map = {str(c.key): c for c in before.children if c.key is not None}
    after_map = {str(c.key): c for c in after.children if c.key is not None}
    positional = (not before_map and not after_map
                  and len(before.children) == len(after.children))

    if positional:
        for index, (left, right) in enumerate(zip(before.children,
                             after.children, strict=False)):
            child_path = _join(path, index)
            if left.kind != right.kind or left.is_container or right.is_container:
                _diff_nodes(left, right, child_path, target, out, limit)
            elif left.value != right.value:
                out.append(DiffEntry(target, child_path,
                                     _node_text(left), _node_text(right)))
        if len(before.children) != len(after.children):
            out.append(DiffEntry(
                target, path or "(根)",
                f"{len(before.children)} 项", f"{len(after.children)} 项"))
        return

    for key in list(before_map) + [k for k in after_map
                                   if k not in before_map]:
        left = before_map.get(key)
        right = after_map.get(key)
        child_path = _join(path, key)
        if left is None:
            out.append(DiffEntry(target, child_path, "", _node_text(right),
                                 "add"))
            continue
        if right is None:
            out.append(DiffEntry(target, child_path, _node_text(left), "",
                                 "remove"))
            continue
        if left.is_container != right.is_container:
            out.append(DiffEntry(target, child_path, _node_text(left),
                                 _node_text(right)))
            continue
        if left.is_container:
            _diff_nodes(left, right, child_path, target, out, limit)
        elif left.kind != right.kind or left.value != right.value:
            out.append(DiffEntry(target, child_path, _node_text(left),
                                 _node_text(right)))
    if len(before.children) != len(after.children) and not before_map:
        out.append(DiffEntry(target, path or "(根)",
                             f"{len(before.children)} 项",
                             f"{len(after.children)} 项"))    # pragma: no cover


def _join(path: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]" if path else f"[{key}]"
    return f"{path}.{key}" if path else str(key)


# ---------------------------------------------------------------------------
# 事务
# ---------------------------------------------------------------------------

@dataclass
class TransactionResult:
    """事务执行结果。"""

    ok: bool = False
    applied: bool = False
    diff: WorldDiff = field(default_factory=WorldDiff)
    backup: Path | None = None
    written: list[Path] = field(default_factory=list)
    message: str = ""

    def summary(self) -> str:
        head = "已应用" if self.applied else ("预览" if self.ok else "未执行")
        parts = [f"{head}：{len(self.diff)} 处差异"]
        if self.backup is not None:
            parts.append(f"备份 {self.backup.name}")
        if self.written:
            parts.append("写入 " + "、".join(p.name for p in self.written))
        if self.message:
            parts.append(self.message)
        return " · ".join(parts)


class WorldTransaction:
    """一次「改存档」的事务。

    典型用法：

        with session.transaction("把时间设成白天") as tx:
            session.world.set_time(day_time=1000)
            tx.commit()
    """

    def __init__(self, session: "WorldSession", label: str = "",
                 *, source: str = "", target: str = "",
                 snapshot: dict[str, SnbtNode] | None = None) -> None:
        self.session = session
        self.label = label
        self.source = source
        self.target = target
        #: 显式传入的「改动前」快照（编辑器已经在内存里改完了，需要拿
        #: 写盘基准来算差异），不传则在 __enter__ 时现场抓一份。
        self.snapshot: dict[str, SnbtNode] | None = snapshot
        self.diff = WorldDiff(label=label)
        self.backup: Path | None = None
        self.written: list[Path] = []
        self._entered = False
        self._closed = False

    # ---- 上下文管理 ----

    def __enter__(self) -> "WorldTransaction":
        if self.snapshot is None:
            self.snapshot = self.session.capture()
        self._entered = True
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            # 出错就回滚内存模型，绝不留半改状态
            if self.snapshot is not None:
                self.session.restore(self.snapshot)
            self._closed = True
            return False
        if not self._closed:
            # 没有显式 commit：只做 dry-run 预览，不落盘
            self.preview()
            # 预览完要把内存模型还原（不能留下未提交的改动）
            if self.snapshot is not None:
                self.session.restore(self.snapshot)
        return False

    # ---- 预览 / 提交 ----

    def preview(self) -> WorldDiff:
        """计算差异（不写盘）。"""
        if self.snapshot is None:
            self.snapshot = self.session.capture()
        self._entered = True
        self.diff = self.session.diff_against(self.snapshot, self.label)
        return self.diff

    def dry_run(self) -> TransactionResult:
        diff = self.preview()
        return TransactionResult(ok=True, applied=False, diff=diff,
                                 message="dry-run：没有写入任何文件")

    def commit(self, *, backup: bool = True) -> TransactionResult:
        """备份 → 校验 → 原子写入 → 记入撤销栈。"""
        try:
            self.session.world.require_writable()
        except Exception as e:
            # 只读 / 存档结构不可用：返回失败结果而不是把异常抛进 Qt 槽
            # （抛出去只会打印 traceback，界面上什么都不显示）
            self._closed = True
            return TransactionResult(
                ok=False, applied=False, message=f"当前不可写入：{e}")
        diff = self.preview()
        if not diff:
            self._closed = True
            return TransactionResult(ok=True, applied=False, diff=diff,
                                     message="没有变化，跳过写入")
        problems = self.session.validate()
        if problems:
            self.session.restore(self.snapshot)         # type: ignore[arg-type]
            self._closed = True
            return TransactionResult(
                ok=False, applied=False, diff=diff,
                message="校验失败：" + "；".join(problems))
        if backup:
            try:
                self.backup = self.session.world.create_backup(
                    label=self.label or "编辑")
            except Exception as e:                      # pragma: no cover
                self.session.restore(self.snapshot)     # type: ignore[arg-type]
                self._closed = True
                return TransactionResult(
                    ok=False, diff=diff, message=f"备份失败，已取消：{e}")
        try:
            self.written = self.session.world.save()
        except Exception as e:
            # 写入失败：尽量回滚内存模型
            self.session.restore(self.snapshot)         # type: ignore[arg-type]
            self._closed = True
            return TransactionResult(ok=False, applied=False, diff=diff,
                                     backup=self.backup,
                                     message=f"写入失败：{e}")
        # 写盘成功后重算派生摘要（玩家生命 / 世界规则 / 地图尺寸…），
        # 否则界面上的「摘要」还是改动前的旧值
        try:
            self.session.world.sync_view()
        except Exception:                               # pragma: no cover
            pass
        self.session.push_undo(self.snapshot or {}, self.label, self.backup)
        self.session.log(self.label or "编辑", diff, source=self.source,
                         target=self.target, backup=self.backup, applied=True)
        # 写盘成功：内存 == 磁盘，刷新基准（之后「应用修改」的差异从这里算）
        self.session.set_baseline()
        self._closed = True
        return TransactionResult(ok=True, applied=True, diff=diff,
                                 backup=self.backup, written=self.written,
                                 message="已写入并记录撤销点")


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------

@dataclass
class EditRecord:
    """桥接日志的一条记录。"""

    at: str
    action: str
    detail: str = ""
    source: str = ""
    target: str = ""
    applied: bool = False
    diff: list[dict[str, str]] = field(default_factory=list)
    backup: str = ""
    undoable: bool = False

    def summary(self) -> str:
        mark = "✔" if self.applied else "…"
        text = f"{mark} [{self.at}] {self.action}"
        if self.detail:
            text += f"：{self.detail}"
        if self.source or self.target:
            text += f"（{self.source or '—'} → {self.target or '—'}）"
        if self.diff:
            text += f" · {len(self.diff)} 处差异"
        if self.backup:
            text += f" · 备份 {self.backup}"
        return text


class WorldSession:
    """世界编辑会话：包装 WorldSave，加上只读门禁 / 撤销 / 日志 / 缓存。"""

    def __init__(self, path: str | Path | None = None,
                 world: WorldSave | None = None) -> None:
        self.world: WorldSave | None = world
        self.path = Path(path) if path else (world.path if world else None)
        self.undo_stack: list[tuple[dict[str, SnbtNode], str]] = []
        self.redo_stack: list[tuple[dict[str, SnbtNode], str]] = []
        self.log_records: list[EditRecord] = []
        self.observers: list[Any] = []
        self._pending: list[str] = []
        #: 「上次写盘时」的内存快照 —— 「应用修改」靠它算出真实的待写入差异。
        #: 旧实现拿当前快照跟自己对 diff，永远得到「没有变化」，按钮等于废的。
        self.baseline: dict[str, SnbtNode] | None = None
        #: 撤销 / 重做之后内存可能已经不等于磁盘（保守标记为「可能有改动」）
        self.baseline_stale = False
        #: 与 undo_stack 一一对应：那次写入前的备份目录（可为 None）
        self.undo_backups: list[Path | None] = []

    # ---- 打开 / 关闭 ----

    @classmethod
    def open(cls, path: str | Path, *, read_only: bool = True,
             progress=None, **kwargs) -> "WorldSession":
        world = open_world(path, read_only=read_only, progress=progress,
                           **kwargs)
        return cls(path=path, world=world)

    @property
    def read_only(self) -> bool:
        return self.world is None or self.world.read_only

    def unlock(self) -> None:
        if self.world is not None:
            self.world.unlock()

    def lock(self) -> None:
        if self.world is not None:
            self.world.lock()

    def is_open(self) -> bool:
        return self.world is not None and self.world.is_open()

    @property
    def name(self) -> str:
        return self.world.name if self.world else "（未打开世界）"

    # ---- 订阅 ----

    def subscribe(self, callback) -> None:
        if callback not in self.observers:
            self.observers.append(callback)

    def _notify(self) -> None:
        for callback in list(self.observers):
            try:
                callback(self)
            except Exception:                           # pragma: no cover
                continue

    # ---- 基准快照 / 待写入差异 ----

    def set_baseline(self, snapshot: dict[str, SnbtNode] | None = None) -> None:
        """记录「磁盘上现在的样子」（不传则以当前内存模型为准）。"""
        if self.world is None:
            self.baseline = None
            self.baseline_stale = False
            return
        self.baseline = snapshot if snapshot is not None else self.capture()
        self.baseline_stale = False

    def unsaved_changes(self) -> bool:
        """是否可能存在未写回磁盘的改动（便宜判断，用于按钮启用态）。"""
        if self.world is None:
            return False
        return bool(self.world.dirty or self.baseline_stale)

    def pending_diff(self, label: str = "待应用修改") -> WorldDiff | None:
        """内存模型 vs 上次写盘（真实差异）。

        返回 `None` 表示**没有基准、无法判断** —— 调用方必须把它和
        「真的没有差异」区分开，否则会把待写入的改动判成干净。
        """
        if self.world is None:
            return WorldDiff(label=label)
        if self.baseline is None:
            return None
        return self.diff_against(self.baseline, label)

    # ---- 快照 / 撤销 ----

    def capture(self, *, deep: bool = True) -> dict[str, SnbtNode]:
        """抓取当前内存模型的快照。

        `deep=True`（默认）深克隆：快照与内存模型完全脱钩，之后怎么改都
        不会污染快照 —— 撤销与差异计算都依赖这一点。
        `deep=False` 只克隆根、共享子节点，快得多，仅用于「读一眼就丢」的
        场景（例如统计体积）。
        """
        out: dict[str, SnbtNode] = {}
        world = self.world
        if world is None:
            return out

        def snap(node: SnbtNode | None) -> SnbtNode | None:
            if node is None:
                return None
            return node.clone() if deep else _shallow_clone(node)

        level = snap(world.level_root)
        if level is not None:
            out["level"] = level
        for uuid, player in world.players.items():
            # 内嵌在 level.dat 里的玩家不单独快照：level 快照已经包含它，
            # 单独再存一份会出现两份互相打架的副本。
            if player.source == "level.dat":
                continue
            node = snap(player.root)
            if node is not None:
                out[f"player:{uuid}"] = node
        for index, info in world.maps.items():
            node = snap(info.root)
            if node is not None:
                out[f"map:{index}"] = node
        return out

    def restore(self, snapshot: dict[str, SnbtNode]) -> None:
        """把内存模型换成快照内容。

        **这里是克隆而不是直接接管节点**：`restore` 的入参常常来自
        `undo_stack` 或 `baseline`（它们必须原样保留），如果直接把同一个
        节点对象装进活树，之后用户的编辑就会改到快照本体上 ——
        表现为「撤销点被污染」以及「写盘基准和内存变成同一份，差异恒为 0、
        改动永远不落盘」。深拷贝一次（用户手动操作才触发）换正确性。
        """
        world = self.world
        if world is None:
            return

        def take(node: SnbtNode | None) -> SnbtNode | None:
            if node is None:
                return None
            clone = getattr(node, "clone", None)
            return clone() if callable(clone) else node

        if "level" in snapshot:
            level_node = take(snapshot["level"])
            world.level_root = level_node
            if world.level is not None:
                world.level.root = level_node
        for key, node in snapshot.items():
            if key.startswith("player:"):
                uuid = key.split(":", 1)[1]
                player = world.players.get(uuid)
                if player is not None:
                    player.root = take(node)
            elif key.startswith("map:"):
                try:
                    index = int(key.split(":", 1)[1])
                except ValueError:                      # pragma: no cover
                    continue
                info = world.maps.get(index)
                if info is not None:
                    info.root = take(node)
        # 内嵌玩家跟着 level 快照一起换（保持单一真相）
        for player in world.players.values():
            if player.source == "level.dat":
                player.root = world.embedded_player_node()
        self._notify()

    def restore_and_sync(self, snapshot: dict[str, SnbtNode]) -> None:
        """恢复内存模型并重算派生摘要（撤销 / 重做 / 回滚用）。"""
        self.restore(snapshot)
        if self.world is not None:
            try:
                self.world.sync_view()
            except Exception:                           # pragma: no cover
                pass

    def push_undo(self, snapshot: dict[str, SnbtNode], label: str = "",
                  backup: Path | None = None) -> None:
        self.undo_stack.append((snapshot, label))
        # 撤销栈旁边记一份「这次写入前的备份目录」：有它才能把磁盘一起回退
        self.undo_backups.append(backup)
        if len(self.undo_stack) > UNDO_LIMIT:
            self.undo_stack.pop(0)
            if self.undo_backups:
                self.undo_backups.pop(0)
        self.redo_stack.clear()

    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        current = self.capture()
        snapshot, label = self.undo_stack.pop()
        if self.undo_backups:
            self.undo_backups.pop()
        self.redo_stack.append((current, label))
        self.restore_and_sync(snapshot)
        # 内存可能已经和磁盘不一致（旧版这里什么都不做，界面于是显示「干净」）
        self.baseline_stale = True
        self._log(EditRecord(
            at=_now(), action="撤销", detail=label or "上一步操作",
            applied=True))
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        current = self.capture()
        snapshot, label = self.redo_stack.pop()
        self.undo_stack.append((current, label))
        self.undo_backups.append(None)      # 重做没有对应的「写入前备份」
        self.restore_and_sync(snapshot)
        self.baseline_stale = True
        self._log(EditRecord(at=_now(), action="重做",
                             detail=label or "上一步操作", applied=True))
        return True

    # ---- 「撤销」的磁盘版 ----

    def last_undo_backup(self) -> Path | None:
        """最近一次可撤销写入的备份目录（没有就返回 None）。"""
        if not self.undo_stack or not self.undo_backups:
            return None
        backup = self.undo_backups[-1]
        if backup is None:
            return None
        path = Path(backup)
        return path if path.is_dir() else None

    def undo_with_rollback(self) -> tuple[bool, str]:
        """撤销并把**磁盘一起**回退到那次写入前的备份。

        纯内存的 `undo()` 只改内存：如果那一步已经写盘，磁盘上还是改过的内容，
        用户很容易以为「撤销完了」。这里用提交时留下的备份目录真正回滚文件。
        """
        if not self.undo_stack:
            return False, "没有可撤销的操作。"
        backup = self.last_undo_backup()
        snapshot, label = self.undo_stack.pop()
        if self.undo_backups:
            self.undo_backups.pop()
        self.restore_and_sync(snapshot)
        self.baseline_stale = True
        if backup is None:
            self._log(EditRecord(at=_now(), action="撤销（仅内存）",
                                 detail=label or "上一步操作", applied=True))
            return True, ("已回退内存。这次改动没有留下备份（可能是重做或"
                          "内存里的未写盘改动），磁盘没有变化。")
        try:
            restored = self.world.restore_backup(backup.name)
        except Exception as e:                          # pragma: no cover
            self._log(EditRecord(at=_now(), action="撤销（回滚失败）",
                                 detail=f"{label}：{e}", applied=True))
            return True, (f"内存已回退，但从 backups/{backup.name} 回滚磁盘失败：{e}")
        # 回滚会重建 world 模型，旧快照/基准全部失效
        self.reload()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.undo_backups.clear()
        self.set_baseline()
        self._log(EditRecord(
            at=_now(), action="撤销并回滚磁盘",
            detail=f"{label or '上一步操作'} ← backups/{backup.name}；"
                   f"恢复 {len(restored)} 个文件",
            applied=True))
        return True, (f"已把内存与磁盘一起回退到 backups/{backup.name}"
                      f"（恢复 {len(restored)} 个文件，撤销栈已清空）。")

    # ---- 日志 ----

    def _log(self, record: EditRecord) -> None:
        self.log_records.insert(0, record)
        del self.log_records[500:]
        self._notify()

    def log(self, action: str, diff: WorldDiff | None = None,
            *, source: str = "", target: str = "", backup: Path | None = None,
            applied: bool = False, detail: str = "") -> EditRecord:
        record = EditRecord(
            at=_now(), action=action, detail=detail, source=source,
            target=target, applied=applied,
            diff=diff.as_dicts() if diff else [],
            backup=backup.name if backup else "",
            undoable=applied)
        self._log(record)
        return record

    def clear_log(self) -> None:
        self.log_records.clear()
        self._notify()

    # ---- 事务 ----

    def transaction(self, label: str = "", *, source: str = "",
                    target: str = "",
                    snapshot: dict[str, SnbtNode] | None = None) -> WorldTransaction:
        return WorldTransaction(self, label, source=source, target=target,
                                snapshot=snapshot)

    def diff_against(self, snapshot: dict[str, SnbtNode] | None,
                     label: str = "") -> WorldDiff:
        """当前内存模型 vs 快照。"""
        world = self.world
        diff = WorldDiff(label=label)
        if world is None or snapshot is None:
            return diff
        if "level" in snapshot and world.level_root is not None:
            diff.entries.extend(diff_roots(snapshot["level"], world.level_root,
                                           "level"))
        for uuid, player in world.players.items():
            key = f"player:{uuid}"
            if key in snapshot and player.root is not None:
                diff.entries.extend(diff_roots(snapshot[key], player.root,
                                               f"player:{uuid}"))
        for index, info in world.maps.items():
            key = f"map:{index}"
            if key in snapshot and info.root is not None:
                diff.entries.extend(diff_roots(snapshot[key], info.root,
                                               f"map:{index}"))
        if diff.entries:
            diff.files = [p.name for p in world._dirty_files()]
        return diff

    def preview(self, mutate, label: str = "") -> WorldDiff:
        """执行一段只改内存的修改并返回差异，然后回滚（纯预览）。

        `mutate` 是接收 session 的可调用对象。
        """
        snapshot = self.capture()
        try:
            mutate(self)
        finally:
            pass
        diff = self.diff_against(snapshot, label)
        self.restore(snapshot)
        return diff

    # ---- 校验 ----

    def validate(self) -> list[str]:
        """写盘前校验：只读门禁、DataVersion、必需的根结构。"""
        problems: list[str] = []
        world = self.world
        if world is None:
            return ["没有打开世界"]
        if world.read_only:
            problems.append("存档处于只读模式")
        if world.level_root is None:
            problems.append("level.dat 结构不可用")
        else:
            if world.level_root.get_child("Data") is None:
                problems.append("level.dat 缺少 Data 标签")
            data = world.level_root.get_child("Data")
            if data is not None:
                version = data.get_child("DataVersion")
                if version is None:
                    problems.append("缺少 DataVersion（写回后游戏可能拒绝读取）")
        compat = world.overview.compatibility
        if compat.level == "read_only":
            problems.append(compat.message)
        return problems

    # ---- 备份 / 回滚 ----

    def list_backups(self) -> list[dict[str, Any]]:
        return self.world.list_backups() if self.world else []

    def restore_backup(self, name: str) -> list[Path]:
        if self.world is None:
            raise RuntimeError("没有打开世界")
        snapshot = self.capture()
        restored = self.world.restore_backup(name)
        self.log("回滚备份", detail=name, applied=True)
        self.push_undo(snapshot, f"回滚 {name}")
        self.reload()
        # 回滚会重建 world.players / maps，撤销栈里的旧快照指向的是已经被
        # 替换掉的节点对象 —— 留着它做「撤销」只会恢复一部分，所以清空。
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.undo_backups.clear()
        self.set_baseline()
        return restored

    def reload(self, **kwargs) -> None:
        """重新从磁盘读取存档（回滚后刷新视图）。"""
        if self.path is None:
            return
        read_only = self.read_only
        world = open_world(self.path, read_only=read_only, **kwargs)
        self.world = world
        self.set_baseline()
        self._notify()

    # ---- 摘要 ----

    def summary_line(self) -> str:
        if self.world is None:
            return "未打开世界"
        state = "只读" if self.world.read_only else "可编辑"
        dirty = " · 有未保存修改" if self.world.dirty else ""
        return (f"{self.name} · {state} · "
                f"{len(self.world.players)} 名玩家 · "
                f"{len(self.world.regions)} 个区域文件{dirty}")


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _shallow_clone(node: SnbtNode) -> SnbtNode:
    """只克隆一层：根是新对象，子节点与源共享（省内存，勿在之后修改）。"""
    clone = SnbtNode(kind=node.kind, key=node.key)
    clone.value = node.value
    clone.children = list(node.children)
    return clone


__all__ = [
    "UNDO_LIMIT", "DiffEntry", "WorldDiff", "TransactionResult",
    "WorldTransaction", "EditRecord", "WorldSession", "diff_roots",
]
