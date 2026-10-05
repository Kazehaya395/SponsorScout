"""Scan Pause/Stop control semantics (the in-memory pause primitive).

Regression: the desktop exposed a single button labelled "Pause" whose handler
called ``ScanCoordinator.stop()`` — a full cooperative *cancel*.  "Resuming"
therefore meant a DB checkpoint lookup (``scan_runs`` + both seed CSVs, read on
the GUI thread while the scan still held the database) followed by a brand-new
run and thread, which is exactly why Pause/Resume felt laggy and could freeze
the window.  Career pagination had no cancel check at all, so Stop there was
ignored until the per-company budget (15 min) expired.

These tests pin the model that replaced it:

* ``check_control`` is the single gate every scanner loop calls: it blocks
  while the pause event is set (workers suspend in place, browsers stay open,
  same ``run_id``) and always lets a cancel win, so Stop pressed while paused
  still ends the scan instead of deadlocking behind the pause.
* ``ScanCoordinator.pause()/resume()`` are pure flips of one flag — no DB
  access, no new thread — reported through ``state_changed`` so the Tools tab
  never has to poll the thread to know what the buttons do.
"""
import os
import threading
import time

import pytest

from sponsorscout.scanning.common import check_control


# ── check_control: the gate every scanner loop calls ─────────────────────────

def test_check_control_is_a_noop_without_events():
    """CLI/tests pass no events at all — the gate must never block them."""
    started = time.monotonic()
    assert check_control(None, None) is False
    assert check_control(threading.Event(), None) is False
    assert time.monotonic() - started < 1.0


def test_check_control_reports_cancel():
    cancel = threading.Event()
    assert check_control(cancel, None) is False
    cancel.set()
    assert check_control(cancel, None) is True


def test_check_control_suspends_until_the_pause_clears():
    cancel, pause = threading.Event(), threading.Event()
    pause.set()
    resumed, result = threading.Event(), []

    def worker():
        result.append(check_control(cancel, pause, poll_sec=0.01))
        resumed.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    # While paused the gate must NOT return: suspending in place (keeping the
    # run identity) is the whole point of the in-memory pause.
    assert not resumed.wait(0.3), "check_control returned while paused"
    pause.clear()
    assert resumed.wait(5.0), "check_control did not continue after a resume"
    assert result == [False]
    thread.join(timeout=5.0)


def test_check_control_stop_wins_over_a_pause():
    """Stop pressed while paused must end the scan, not queue behind it."""
    cancel, pause = threading.Event(), threading.Event()
    pause.set()
    cancel.set()
    started = time.monotonic()
    assert check_control(cancel, pause, poll_sec=0.01) is True
    assert time.monotonic() - started < 1.0


def test_sleep_interruptible_wakes_on_cancel():
    """Backoff sleeps must not stall Stop: wake within ~0.3 s, report abort."""
    from sponsorscout.scanning.common import ScanCancelled, sleep_interruptible

    cancel = threading.Event()
    started = time.monotonic()

    def _stop_soon():
        time.sleep(0.1)
        cancel.set()

    threading.Thread(target=_stop_soon, daemon=True).start()
    assert sleep_interruptible(20, cancel, None) is False
    assert time.monotonic() - started < 2.0


def test_sleep_interruptible_sleeps_through_when_idle():
    """No events set: the full (short) sleep elapses and returns True."""
    from sponsorscout.scanning.common import sleep_interruptible

    started = time.monotonic()
    assert sleep_interruptible(0.15, threading.Event(), None) is True
    assert 0.1 <= time.monotonic() - started < 2.0


def test_check_cancelled_raises_only_when_set():
    """check_cancelled is a no-op for None/unset, raises ScanCancelled on Stop."""
    from sponsorscout.scanning.common import ScanCancelled, check_cancelled

    check_cancelled(None)
    check_cancelled(threading.Event())
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ScanCancelled):
        check_cancelled(cancel)



# ── ScanCoordinator: pause/resume/stop state machine ─────────────────────────

class _LiveThread:
    """Stands in for the scan thread: ``is_running()`` reads ``is_alive()``.

    Using it keeps these tests hermetic — no network, no database and no real
    campaign — while still exercising the real transitions.
    """

    def is_alive(self):
        return True


def _coordinator():
    pytest.importorskip("PySide6.QtCore")
    from sponsorscout.application.scan_coordinator import ScanCoordinator
    return ScanCoordinator(db_path=":memory:")


def test_coordinator_controls_are_noops_when_idle():
    coord = _coordinator()
    states = []
    coord.state_changed.connect(states.append)

    coord.pause()        # nothing is running → nothing to suspend
    assert coord.is_paused() is False
    coord.resume()       # nothing is paused → nothing to continue
    assert coord.is_paused() is False
    coord.stop()         # not running → no "stopping" flash for the UI

    assert states == []
    assert coord.state() == "idle"


def test_coordinator_pause_resume_keeps_one_run():
    coord = _coordinator()
    states = []
    coord.state_changed.connect(states.append)
    coord._thread = _LiveThread()  # a live campaign, without starting one

    coord.pause()
    assert coord.is_paused() is True
    assert coord.state() == "paused"
    coord.pause()  # idempotent: a second click cannot double-suspend
    assert coord.is_paused() is True

    coord.resume()
    assert coord.is_paused() is False
    assert coord.state() == "running"
    coord.resume()  # idempotent as well
    assert coord.state() == "running"

    assert states == ["paused", "running"]


def test_coordinator_stop_clears_a_pending_pause():
    """Stop is not allowed to deadlock behind a pause the user just set."""
    coord = _coordinator()
    states = []
    coord.state_changed.connect(states.append)
    coord._thread = _LiveThread()

    coord.pause()
    coord.stop()

    assert coord.is_paused() is False   # Stop wins over the pause
    assert coord.state() == "stopping"
    assert states == ["paused", "stopping"]


# ── Tools tab: buttons mirror the coordinator instead of polling it ──────────

def _qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:  # pragma: no cover - PySide6 missing
        pytest.skip("PySide6 unavailable")
    app = QApplication.instance() or QApplication([])
    assert app is not None
    return app


# Tabs built by these tests stay referenced for the whole session: their
# workers (checkpoint probe, run-history read) hand results back through queued
# signals, and a queued delivery whose QWidget receiver was already garbage
# collected faults inside Qt on the next processEvents() call.
_TABS: list = []


def _make_tab(db_path):
    _qapp()
    from sponsorscout.i18n import set_locale
    from sponsorscout.ui.tabs.tools import ToolsTab
    set_locale("en")
    tab = ToolsTab(db_path)
    _TABS.append(tab)
    return tab


def _pump(app, seconds: float) -> None:
    """Run the Qt event loop for a while: worker signals are queued."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def _pump_until(app, predicate, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_pause_button_follows_the_coordinator_state(db_path):
    """Pause flips the button to "Resume" in place; Stop freezes both."""
    tab = _make_tab(db_path)
    tab._set_scan_running_ui("starting")
    assert tab.pause_btn.isEnabled()
    assert tab.pause_btn.text() == "Pause"
    assert tab.stop_btn.isEnabled()

    tab.coordinator._thread = _LiveThread()  # simulate a live campaign
    tab.coordinator.pause()
    # The in-memory pause only relabels the button — nothing is stopped and
    # no lookup/new run happens, which is what made the old Pause feel laggy.
    assert tab.pause_btn.text() == "Resume"
    assert tab.scan_status.text() == "Paused"
    assert tab.stop_btn.isEnabled()

    tab.coordinator.resume()
    assert tab.pause_btn.text() == "Pause"
    assert tab.scan_status.text() == "Running…"

    tab.coordinator.stop()
    # A click that already landed must be visible: no second click can re-arm
    # anything, and the worker's own "idle"/finished signal lands later.
    assert not tab.pause_btn.isEnabled()
    assert not tab.stop_btn.isEnabled()
    assert tab.scan_status.text() == "Stopping…"


def test_history_refresh_ignores_superseded_replies(db_path, monkeypatch):
    """Only the newest history read may repaint the runs table."""
    tab = _make_tab(db_path)
    # Keep the test hermetic: the real method starts a checkpoint lookup
    # thread, which is not what this test is about.
    monkeypatch.setattr(tab, "_refresh_resume_button", lambda: None)
    row = ("r1", "full", "2026-01-01T10:00:00", "", "completed", "", 3, 0, 0,
           7, 1, 2, 1, 1)

    tab._on_refresh_done((tab._refresh_seq + 1, [row]))  # stale → ignored
    assert tab.runs_table.rowCount() == 0

    tab._on_refresh_done((tab._refresh_seq, [row]))      # newest → applied
    assert tab.runs_table.rowCount() == 1
    assert tab.runs_table.item(0, 0).text() == "r1"


def test_pause_resume_stop_end_to_end(db_path, monkeypatch):
    """The reported bug, end to end: Pause/Resume must never restart the run.

    Regression: the "Pause" handler called ``coordinator.stop()``, so every
    Pause/Resume pair relaunched the pipeline — a new ``run_id``, a new thread
    and a blocking checkpoint lookup — and the Career pagination loop had no
    cancel check at all, so Stop there looked dead for minutes.  A stubbed
    ``pipeline.run_scan`` keeps this hermetic (no network, no browsers) while
    the real coordinator and ToolsTab do the work.
    """
    app = _qapp()
    from PySide6.QtWidgets import QMessageBox
    from sponsorscout.scanning import pipeline

    runs = []

    def fake_run_scan(method="full", db_path=None, cancel_event=None,
                      pause_event=None, progress=None, resume_from=None,
                      **kwargs):
        run_id = "RUN1" if resume_from is None else "RUN2"
        runs.append(run_id)
        done = 0
        for i in range(1, 101):
            if check_control(cancel_event, pause_event):
                return {"run_id": run_id, "method": method,
                        "status": "cancelled", "cancelled": True,
                        "ingested": done, "duplicates": 0, "log_rows": 0,
                        "artifacts": {}, "errors": []}
            done += 1
            if progress:
                progress(f"   OK company_{i}: wrote=1")
            time.sleep(0.03)
        return {"run_id": run_id, "method": method, "status": "completed",
                "cancelled": False, "ingested": done, "duplicates": 0,
                "log_rows": 0, "artifacts": {}, "errors": []}

    monkeypatch.setattr(pipeline, "run_scan", fake_run_scan)
    tab = _make_tab(db_path)
    tab.show()

    tab.start_scan()
    assert _pump_until(
        app, lambda: "company_1" in tab.scan_log.toPlainText()), "scan never started"
    assert runs == ["RUN1"]
    assert tab.pause_btn.text() == "Pause" and tab.stop_btn.isEnabled()

    # ── Pause: suspended in place, no new run, log stops growing ────────────
    tab.toggle_pause()
    assert _pump_until(app, lambda: tab.coordinator.is_paused())
    assert tab.pause_btn.text() == "Resume"
    assert tab.scan_status.text() == "Paused"
    assert runs == ["RUN1"], "pausing started a new scan"
    frozen = tab.scan_log.toPlainText()
    _pump(app, 0.4)
    assert tab.scan_log.toPlainText() == frozen, "log kept streaming while paused"

    # ── Resume: instant, same run, no checkpoint lookup ─────────────────────
    started = time.monotonic()
    tab.toggle_pause()
    assert not tab.coordinator.is_paused()
    assert time.monotonic() - started < 0.3, "resume blocked the GUI thread"
    assert tab.pause_btn.text() == "Pause"
    assert tab.scan_status.text() == "Running…"
    assert _pump_until(
        app, lambda: len(tab.scan_log.toPlainText()) > len(frozen))
    assert runs == ["RUN1"], "resuming started a new scan"

    # ── Stop: confirmation, real cancel, everything on one run ──────────────
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    tab.confirm_stop()
    assert tab.scan_status.text() == "Stopping…"
    assert not tab.pause_btn.isEnabled() and not tab.stop_btn.isEnabled()
    assert _pump_until(app, lambda: not tab.coordinator.is_running())
    assert _pump_until(app, lambda: tab.scan_status.text() == "Stopped.")
    assert runs == ["RUN1"], "Pause/Resume/Stop restarted the scan"
    assert not tab.pause_btn.isEnabled() and not tab.stop_btn.isEnabled()
    # Let the async run-history / resume probes land before teardown.
    _pump(app, 0.3)
