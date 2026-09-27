"""Qt-side scan orchestration.

Thin wrapper around ``scanning.pipeline.run_scan``: runs the campaign in a
worker thread and streams progress to the UI through Qt signals (safe to
emit from a non-Qt thread — Qt queues cross-thread signal deliveries).
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from PySide6.QtCore import QObject, Signal


class ScanCoordinator(QObject):
    """Owns the background scan thread; the UI talks only to this object."""

    #: One chunk of scan log output (what the CLI scripts print).  Chunks are
    #: newline-joined batches, not single lines — see ``_PROGRESS_*`` below.
    progress = Signal(str)
    #: Structured scan progress for the visual progress bar: ``(done, total,
    #: phase, label)`` where phase is ``""``/``"ats"``/``"career"``.  Emitted
    #: from the worker thread alongside ``progress`` (same flush, no new
    #: threads/timers); the UI slot only touches a QProgressBar + one label.
    progress_tick = Signal(int, int, str, str)
    #: Emitted once when the scan thread ends; carries the pipeline summary.
    finished = Signal(dict)
    #: Control state: ``"idle" | "running" | "paused" | "stopping"``.
    #: pause()/resume()/stop() emit it immediately (UI thread), start() emits
    #: "running" and the worker emits "idle" when the campaign ends, so the
    #: Tools tab never has to poll the thread to know what the buttons do.
    state_changed = Signal(str)

    # Progress batching.  The scanners emit a line per company, per page and
    # per 100 detail checks; forwarding every line as its own queued Qt signal
    # floods the GUI thread (and the log widget) on long scans, which is what
    # made the window feel frozen.  Instead the worker joins lines into a
    # single chunk, rate-limited to PROGRESS_FLUSH_SEC / PROGRESS_FLUSH_LINES.
    PROGRESS_FLUSH_SEC = 0.15
    PROGRESS_FLUSH_LINES = 40

    def __init__(self, db_path: str | None = None):
        super().__init__()
        self.db_path = db_path
        self._cancel = threading.Event()
        # In-memory pause: set = suspend at the next control gate, clear =
        # continue.  Distinct from _cancel so Pause never ends the run, which
        # is what made the old single "Pause" button (a stop) so expensive to
        # undo — it forced a DB checkpoint lookup plus a brand-new thread.
        self._pause = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state = "idle"

    # ── Public API (main thread) ─────────────────────────────────────────────
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def state(self) -> str:
        return self._state

    def is_paused(self) -> bool:
        return self._pause.is_set()

    def _emit_state(self, state: str) -> None:
        self._state = state
        self.state_changed.emit(state)

    def pause(self) -> None:
        """Suspend the scan in place.

        Workers block at the next ``check_control`` gate; browsers stay open
        and the run keeps its identity, so :meth:`resume` continues instantly
        with no database round-trip.  No-op if no scan is running or it is
        already paused.
        """
        if not self.is_running() or self._pause.is_set():
            return
        self._pause.set()
        self._emit_state("paused")

    def resume(self) -> None:
        """Undo :meth:`pause`.  No-op when nothing is paused."""
        if not self._pause.is_set():
            return
        self._pause.clear()
        self._emit_state("running" if self.is_running() else "idle")

    def start(self, method: str = "full", resume_from: str | None = None,
              scan_scope: dict | None = None) -> bool:
        """Start a scan campaign. False if a scan is already running.

        The app uses a single scan mode (``"full"``): ATS boards + career
        pages + per-job detail-page enrichment, so every job is extracted
        with full detail.  ``"quick"`` remains available to the dev CLI
        (ATS/career crawl without the detail-page enrichment pass).

        ``resume_from``: run_id of a stopped run — only its unfinished
        companies are scanned (Stop-as-checkpoint); the progress bar is
        offset so it continues from the checkpoint.

        ``scan_scope``: optional custom-scan scope dict with optional keys
        ``run_ats`` (bool), ``run_career`` (bool), ``ats`` (list of ATS
        company names), ``career`` (list of career company names).
        ``None`` means a full scan of every seeded company.
        """
        if self.is_running():
            return False
        # A previous run may have left STOP set, or PAUSE stuck because the
        # user paused and then closed/stopped the campaign; a new campaign
        # must always begin with both control flags clear, or the very first
        # gate would abort/suspend the fresh scan.
        self._cancel.clear()
        self._pause.clear()
        self._emit_state("running")

        def worker():
            from sponsorscout.scanning import pipeline

            # Rate-limited progress pump (see constants above).
            pending: list[str] = []
            lock = threading.Lock()
            last = [0.0]

            def flush_progress():
                with lock:
                    chunk = "\n".join(pending)
                    pending.clear()
                    last[0] = time.monotonic()
                if chunk:
                    for _line in chunk.splitlines():
                        _tick = _parse_progress_tick(_line)
                        if _tick is not None:
                            _done, _total, _phase, _label = _tick
                            self.progress_tick.emit(_done, _total, _phase, _label)
                    self.progress.emit(chunk)

            def on_progress(message):
                with lock:
                    pending.append(str(message))
                    due = (len(pending) >= self.PROGRESS_FLUSH_LINES
                           or (time.monotonic() - last[0]) >= self.PROGRESS_FLUSH_SEC)
                if due:
                    flush_progress()

            try:
                scope = scan_scope if isinstance(scan_scope, dict) else None
                summary = pipeline.run_scan(
                    method=method,
                    db_path=self.db_path,
                    cancel_event=self._cancel,
                    pause_event=self._pause,
                    progress=on_progress,
                    resume_from=resume_from,
                    only_ats=scope.get("ats") if scope else None,
                    only_career=scope.get("career") if scope else None,
                    run_ats=bool(scope.get("run_ats", True)) if scope else True,
                    run_career=bool(scope.get("run_career", True))
                    if scope else True,
                )
            except Exception as exc:  # defensive: never kill the thread silently
                summary = {
                    "run_id": "", "method": method, "status": "error",
                    "cancelled": False, "ingested": 0, "duplicates": 0,
                    "log_rows": 0, "artifacts": {},
                    "errors": [f"{type(exc).__name__}: {exc}"],
                }
            flush_progress()
            # A campaign that is over is never "paused": clearing the flag
            # here stops a Pause pressed as the last company finished from
            # leaking into the next run's Pause/Resume button state.
            self._pause.clear()
            self._emit_state("idle")
            self.finished.emit(summary)

        self._thread = threading.Thread(target=worker, name="ScanWorker", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        """Cooperative stop: scanners check this between control gates.

        A pending pause is cleared first so Stop always wins — otherwise a
        Stop pressed while paused would queue behind the pause and look
        ignored.  The run keeps its DB checkpoint, so the separate Resume
        button can still continue it later (even after an app restart).
        """
        self._pause.clear()
        self._cancel.set()
        if self.is_running():
            self._emit_state("stopping")


#: Prefix for machine-readable progress lines.  The pipeline emits e.g.
#: ``PROGRESS: 12/208:career:About You`` alongside the human-readable log;
#: the UI strips these from the log widget and drives the QProgressBar.
PROGRESS_PREFIX = "PROGRESS:"


def _parse_progress_tick(line: str):
    """Parse a ``PROGRESS: done/total:phase:label`` line.

    Returns ``(done, total, phase, label)`` or ``None`` when the line is
    ordinary log output.  Pure string ops (no regex) — called per log line
    inside the already rate-limited progress flush, so cost is negligible.
    """
    try:
        text = str(line).strip()
    except Exception:
        return None
    if not text.startswith(PROGRESS_PREFIX):
        return None
    rest = text[len(PROGRESS_PREFIX):].strip()
    try:
        counts, _, tail = rest.partition(":")
        done_s, _, total_s = counts.partition("/")
        done, total = int(done_s.strip()), int(total_s.strip())
        phase, _, label = tail.partition(":")
        if total <= 0 or done < 0:
            return None
        return (min(done, total), total, phase.strip(), label.strip())
    except (ValueError, AttributeError):
        return None
