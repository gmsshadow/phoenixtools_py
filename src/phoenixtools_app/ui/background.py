from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot


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


class _JobCallbacks(QObject):
    """
    Receiver that lives in the *parent's* (main UI) thread. Because the worker emits from a
    different thread, connecting to these slots uses a queued connection, so the user callbacks
    always run on the main thread (creating widgets / repainting off-thread is illegal in Qt).
    """

    def __init__(
        self,
        parent: QObject,
        on_progress: Callable[[str], None],
        on_done: Callable[[str], None],
        on_failed: Callable[[str], None],
    ) -> None:
        super().__init__(parent)
        self._on_progress = on_progress
        self._on_done = on_done
        self._on_failed = on_failed

    @Slot(str)
    def progress(self, msg: str) -> None:
        self._on_progress(msg)

    @Slot(str)
    def done(self, summary: str) -> None:
        self._on_done(summary)

    @Slot(str)
    def failed(self, err: str) -> None:
        self._on_failed(err)


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

    # Callbacks must run on the main thread. _JobCallbacks is parented to `parent`
    # (main thread), so these signal->slot connections are auto/queued, not direct.
    callbacks = _JobCallbacks(parent, on_progress, on_done, on_failed)

    thread.started.connect(worker.run)
    worker.progress.connect(callbacks.progress)
    worker.done.connect(callbacks.done)
    worker.failed.connect(callbacks.failed)
    worker.done.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(callbacks.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread, worker
