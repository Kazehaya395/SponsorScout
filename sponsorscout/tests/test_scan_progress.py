"""Scan progress tests (visual progress bar protocol).

The bar is driven by machine-readable ``PROGRESS: done/total:phase:label``
lines the pipeline emits after each finished company; the coordinator
parses them into a typed signal.  These tests pin the contract so the bar
can never desync from the actual scan (wrong phase, overflow, stalls).
"""
import threading

from sponsorscout.application.scan_coordinator import (
    PROGRESS_PREFIX,
    _parse_progress_tick,
)
from sponsorscout.scanning.pipeline import _ScanProgress


def test_tick_parser_accepts_valid_lines():
    assert _parse_progress_tick("PROGRESS: 12/208:career:Career 12/162") == (
        12, 208, "career", "Career 12/162")
    assert _parse_progress_tick("PROGRESS: 46/208:ats:ATS 46/46") == (
        46, 208, "ats", "ATS 46/46")
    assert _parse_progress_tick("  PROGRESS: 1/10:ats:x") == (1, 10, "ats", "x")


def test_tick_parser_rejects_log_and_bad_lines():
    assert _parse_progress_tick("[1/46] Foo (greenhouse)") is None
    assert _parse_progress_tick("   OK: wrote=5, quarantined=0") is None
    assert _parse_progress_tick("Ingested live so far: 5 jobs") is None
    assert _parse_progress_tick("PROGRESS: bad") is None
    assert _parse_progress_tick("PROGRESS: 0/0:ats:") is None
    # Overflow is clamped, never rejected (bar must reach exactly 100%).
    assert _parse_progress_tick("PROGRESS: 99/5:career:y") == (5, 5, "career", "y")


def test_scan_progress_counts_both_phases():
    got = []
    wrapped = _ScanProgress(2, 3).wrap(got.append)
    wrapped("Scan x started")
    wrapped("   OK: wrote=5, quarantined=0, dups=1")
    wrapped("   EMPTY Foo: wrote=0, quarantined=0, dups=0, scope_reject=0")
    wrapped("   OK Bar: wrote=3, quarantined=1, dups=0, scope_reject=0")
    wrapped("   PARTIAL Baz: wrote=1, quarantined=0, dups=0, scope_reject=0")
    ticks = [str(m) for m in got if str(m).startswith(PROGRESS_PREFIX)]
    assert ticks == [
        "PROGRESS: 1/5:ats:ATS 1/2",
        "PROGRESS: 2/5:career:Career 1/3",
        "PROGRESS: 3/5:career:Career 2/3",
        "PROGRESS: 4/5:career:Career 3/3",
    ]


def test_scan_progress_thread_safe_full_sequence():
    got = []
    lock = threading.Lock()

    def _sink(msg):
        with lock:
            got.append(str(msg))

    wrapped = _ScanProgress(0, 200).wrap(_sink)
    threads = [threading.Thread(
        target=lambda: [wrapped(
            "   OK C: wrote=1, quarantined=0, dups=0, scope_reject=0")
            for _ in range(25)]) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    import re
    dones = sorted(int(re.search(r" (\d+)/", m).group(1))
                   for m in got if m.startswith(PROGRESS_PREFIX))
    assert dones == list(range(1, 201))


def test_scan_progress_never_counts_own_ticks_and_preserves_flush():
    class _P:
        def __init__(self):
            self.seen = []

        def __call__(self, msg):
            self.seen.append(msg)

        def flush(self):
            self.seen.append("FLUSHED")

    inner = _P()
    wrapped = _ScanProgress(1, 1).wrap(inner)
    wrapped.flush()
    assert "FLUSHED" in inner.seen
    # Re-feeding a tick line passes it through but must not spawn a new
    # tick (no feedback loop / counter corruption).
    before = [m for m in inner.seen if str(m).startswith(PROGRESS_PREFIX)]
    wrapped("PROGRESS: 1/2:ats:ATS 1/1")
    after = [m for m in inner.seen if str(m).startswith(PROGRESS_PREFIX)]
    assert after == before + ["PROGRESS: 1/2:ats:ATS 1/1"]
    # Zero totals: plain log passes through, no tick emitted.
    seen = []
    _ScanProgress(0, 0).wrap(seen.append)(
        "   OK: wrote=1, quarantined=0, dups=0")
    assert not [m for m in seen if str(m).startswith(PROGRESS_PREFIX)]
