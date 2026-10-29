"""Cancellable, thread-owned connections for background UI read snapshots.

No startup work/probes. Ordinary ingestion connections are unchanged. A UI
reader never enables WAL, migrates data, or waits seconds for a writer.
"""
from __future__ import annotations
from contextvars import ContextVar
from pathlib import Path
import sqlite3
import threading

_current = ContextVar('sponsorscout_ui_database_task', default=None)


def current_database_task():
    return _current.get()


class DatabaseReadTask:
    def __init__(self):
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._connections = []

    def cancel(self):
        self.cancelled.set()
        with self._lock:
            for conn in self._connections:
                try:
                    conn.interrupt()  # SQLite explicitly permits cross-thread interrupt.
                except sqlite3.Error:
                    pass

    def connect(self, path):
        if self.cancelled.is_set():
            raise InterruptedError('Superseded UI read')
        uri = Path(path).expanduser().absolute().as_uri() + '?mode=ro'
        conn = sqlite3.connect(uri, uri=True, timeout=0.25)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA busy_timeout=250')
        # One connection per query is deliberate: 37 read helpers in
        # db/database.py close the connection they were handed, so a memoised
        # snapshot connection would be closed under the next reader and its
        # `except Exception: return []` would surface as an empty dropdown.
        # 2 MB was chosen for a 4 MB database. A full scan's jobs table is
        # 60 MB+, so every aggregate re-read its pages from disk; -16 MB plus
        # mmap covers the tables the Insights panels actually touch.
        conn.execute('PRAGMA cache_size=-16000')
        conn.execute('PRAGMA mmap_size=268435456')
        conn.set_progress_handler(lambda: int(self.cancelled.is_set()), 1000)
        with self._lock:
            self._connections.append(conn)
        if self.cancelled.is_set():
            conn.interrupt()
        return conn

    def __enter__(self):
        self._token = _current.set(self)
        return self

    def __exit__(self, *exc):
        # sqlite3's `with conn` commits/rolls back but does NOT close it.
        # Close every snapshot connection on its creating worker thread.
        with self._lock:
            connections, self._connections = self._connections, []
        for conn in connections:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        _current.reset(self._token)
