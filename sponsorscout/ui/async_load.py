"""Small coalescing UI data loader; no QWidget/SQL work on the wrong thread.

One read worker and one action worker per process, created lazily. Slow chart
reads cannot hold up a queue action. Latest request wins; obsolete SQLite reads
are interrupted, and results from a closed or superseded view are discarded.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import logging
import threading
import time
from PySide6.QtCore import QObject, Signal, Slot, Qt
from sponsorscout.core.db_tasks import DatabaseReadTask

logger = logging.getLogger(__name__)
_pools = {}
_pool_lock = threading.Lock()


def _pool(read_only):
    name = 'view' if read_only else 'action'
    with _pool_lock:
        if name not in _pools:
            _pools[name] = ThreadPoolExecutor(max_workers=1, thread_name_prefix='UI-' + name)
        return _pools[name]


class AsyncLoader(QObject):
    _ready = Signal(int, object, object)

    def __init__(self, parent=None, *, read_only=True, name='view'):
        super().__init__(parent)
        self.read_only, self.name = read_only, name
        self._generation = 0
        self._active = None
        self._pending = None
        self._backlog = []
        self._closed = False
        self._ready.connect(self._deliver, Qt.QueuedConnection)
        if parent is not None:
            parent.destroyed.connect(self.close)

    @property
    def busy(self):
        return self._active is not None or self._pending is not None or bool(self._backlog)

    def invalidate(self):
        self._generation += 1
        self._pending = None
        self._backlog.clear()
        if self._active is not None:
            future, task, *_ = self._active
            if task is not None:
                task.cancel()
            future.cancel()

    @Slot()
    def close(self, *_args):
        self._closed = True
        self.invalidate()

    def request(self, load, apply, error=None):
        if self._closed:
            return
        self._generation += 1
        request = (self._generation, load, apply, error)
        if self._active is not None:
            if not self.read_only:
                self._backlog.append(request)  # writes are never coalesced away
                return
            self._pending = request
            future, task, *_ = self._active
            if task is not None:
                task.cancel()
            future.cancel()
        else:
            self._start(request)

    def _start(self, request):
        generation, load, apply, error = request
        task = DatabaseReadTask() if self.read_only else None

        def run():
            started = time.perf_counter()
            try:
                if task is not None:
                    with task:
                        return load()
                return load()
            finally:
                logger.debug('UI snapshot %s: %.1f ms (worker)', self.name,
                             (time.perf_counter() - started) * 1000)

        future = _pool(self.read_only).submit(run)
        self._active = (future, task, generation, apply, error)

        def done(result):
            try:
                value, failure = result.result(), None
            except BaseException as exc:
                value, failure = None, exc
            try:
                self._ready.emit(generation, value, failure)
            except RuntimeError:
                pass  # parent/view already destroyed; never touch its widgets.
        future.add_done_callback(done)

    @Slot(int, object, object)
    def _deliver(self, generation, result, failure):
        active = self._active
        if active is None or active[2] != generation:
            return
        self._active = None
        if not self._closed and (not self.read_only or generation == self._generation):
            started = time.perf_counter()
            try:
                if failure is None:
                    active[3](result)
                elif active[4] is not None:
                    active[4](failure)
                else:
                    logger.warning('UI snapshot %s failed: %s', self.name, failure)
            finally:
                elapsed = time.perf_counter() - started
                if elapsed > .25:
                    logger.warning('UI apply %s took %.0f ms', self.name, elapsed * 1000)
        if not self._closed and self._active is None:
            if self._backlog:
                self._start(self._backlog.pop(0))
            elif self._pending is not None:
                pending, self._pending = self._pending, None
                self._start(pending)
