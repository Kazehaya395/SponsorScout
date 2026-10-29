"""Small, process-local ingestion priority gate (no probes or startup work).

SQLite permits one writer. CPU-heavy Insights work must yield between batches
while a regular or explicitly requested assisted scan is ingesting jobs.
"""
from __future__ import annotations

import functools
import inspect
import threading
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

_lock = threading.Condition()
_active: Counter = Counter()
_maintenance: set[str] = set()


def _key(db_path) -> str:
    if not db_path:
        from sponsorscout.paths import DB_PATH
        db_path = DB_PATH
    return str(Path(db_path).expanduser().resolve())


def ingestion_active(db_path) -> bool:
    with _lock:
        return _active[_key(db_path)] > 0


@contextmanager
def ingestion_activity(db_path):
    key = _key(db_path)
    with _lock:
        if key in _maintenance:
            raise RuntimeError('Existing-data cleanup is in progress; retry ingestion after it finishes.')
        _active[key] += 1
    try:
        yield
    finally:
        with _lock:
            _active[key] -= 1
            if not _active[key]:
                del _active[key]
            _lock.notify_all()


@contextmanager
def maintenance_activity(db_path):
    """Exclusive local admission, not a long SQLite write transaction."""
    key = _key(db_path)
    with _lock:
        if _active[key] > 0:
            raise RuntimeError('Finish regular/assisted ingestion before running existing-data cleanup.')
        _maintenance.add(key)
        _active[key] += 1
    try:
        yield
    finally:
        with _lock:
            _active[key] -= 1
            if not _active[key]:
                del _active[key]
            _maintenance.discard(key)
            _lock.notify_all()


def ingestion_task(function):
    """Mark actual ingestion calls, preserving signatures/source introspection."""
    signature = inspect.signature(function)
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        bound = signature.bind_partial(*args, **kwargs)
        db_path = bound.arguments.get('db_path')
        if not db_path and bound.arguments.get('conn') is not None:
            try:
                db_path = bound.arguments['conn'].execute('PRAGMA database_list').fetchone()[2]
            except Exception:
                pass
        with ingestion_activity(db_path):
            return function(*args, **kwargs)
    return wrapped


def wait_for_ingestion_idle(db_path, cancel_event=None, on_wait=None) -> bool:
    """Yield without holding any SQLite transaction. Returns false on cancel."""
    notified = False
    while True:
        with _lock:
            busy = _active[_key(db_path)] > 0
            if not busy:
                return not (cancel_event is not None and cancel_event.is_set())
        if cancel_event is not None and cancel_event.is_set():
            return False
        if not notified and callable(on_wait):
            on_wait()
            notified = True
        with _lock:
            _lock.wait(timeout=0.1)
