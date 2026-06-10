from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal


class _JobWorker(QObject):
    progress = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, fn: Callable[[Callable[[str], None]], str]) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            summary = self._fn(self.progress.emit)
            self.done.emit(summary)
        except Exception as e:
            self.failed.emit(str(e))


def run_job(
    parent: QObject,
    fn: Callable[[Callable[[str], None]], str],
    *,
    on_progress: Callable[[str], None],
    on_done: Callable[[str], None],
    on_failed: Callable[[str], None],
) -> tuple[QThread, _JobWorker]:
    """
    Run `fn(progress_cb) -> summary` on a worker thread so the UI keeps repainting.
    `fn` must open its own DB engine/session (SQLite connections are not thread-safe).
    Caller must keep a reference to the returned (thread, worker) until finished.
    """
    thread = QThread(parent)
    worker = _JobWorker(fn)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.progress.connect(on_progress)
    worker.done.connect(on_done)
    worker.failed.connect(on_failed)
    worker.done.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread, worker
