"""世界存档的异步加载（QThreadPool + QRunnable）。

大存档（几百 MB 的 region、上千个 playerdata）如果同步打开会卡住界面，
所以：

* `WorldLoader` 在后台线程里 `open_world()`（纯文件 IO + NBT 解析，不碰 Qt）；
* 通过信号把进度、成功、失败传回 GUI 线程；
* `cancel()` 让后续的中间结果被丢弃（不阻断已经在跑的 IO，但不会再更新界面）。

注意：后台线程只负责**读**。所有写盘都必须在 GUI 线程里走 WorldSession 事务。
"""
from __future__ import annotations

import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from app.core.world import WorldSave, open_world


class _LoaderSignals(QObject):
    progress = Signal(int, int, str)        # 当前, 总数, 说明
    finished = Signal(object)               # WorldSave
    failed = Signal(str)


class _LoadTask(QRunnable):
    def __init__(self, path: Path, signals: _LoaderSignals,
                 read_only: bool = True, token: int = 0) -> None:
        super().__init__()
        self.path = Path(path)
        self.signals = signals
        self.read_only = read_only
        self.token = token
        self.setAutoDelete(True)

    def run(self) -> None:                              # pragma: no cover
        try:
            world = open_world(self.path, read_only=self.read_only,
                               progress=self._progress)
            self.signals.finished.emit(world)
        except Exception as e:
            self.signals.failed.emit(f"{type(e).__name__}: {e}\n"
                                     + traceback.format_exc(limit=3))

    def _progress(self, done: int, total: int, text: str) -> None:
        self.signals.progress.emit(int(done), int(total), str(text))


class WorldLoader(QObject):
    """一次只跑一个加载任务；新请求会作废旧结果。"""

    progress = Signal(int, int, str)
    loaded = Signal(object)                 # WorldSave
    failed = Signal(str)
    started = Signal(str)                   # 版本化路径

    def __init__(self, parent=None, *, threads: int = 2) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(1, threads))
        self._signals = _LoaderSignals(self)
        self._signals.progress.connect(self._on_progress)
        self._signals.finished.connect(self._on_finished)
        self._signals.failed.connect(self._on_failed)
        self._token = 0
        self._running = False

    # ---- 状态 ----

    @property
    def busy(self) -> bool:
        return self._running

    def cancel(self) -> None:
        """作废当前结果（后台任务会跑完，但结果不会再送到界面）。"""
        self._token += 1
        self._running = False

    # ---- 启动 ----

    def load(self, path: str | Path, *, read_only: bool = True) -> None:
        self._token += 1
        self._running = True
        self.started.emit(str(path))
        task = _LoadTask(Path(path), self._signals, read_only=read_only,
                         token=self._token)
        self._pool.start(task)

    def wait(self, timeout_ms: int = 30000) -> bool:
        """等后台任务结束（测试与「退出前收尾」用）。"""
        return self._pool.waitForDone(int(timeout_ms))

    # ---- 回调 ----

    def _on_progress(self, done: int, total: int, text: str) -> None:
        if not self._running:
            return
        self.progress.emit(done, total, text)

    def _on_finished(self, world: WorldSave) -> None:
        if not self._running:
            return
        self._running = False
        self.loaded.emit(world)

    def _on_failed(self, message: str) -> None:
        self._running = False
        self.failed.emit(message)


__all__ = ["WorldLoader"]
