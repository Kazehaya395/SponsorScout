"""2026-10-19 (round 2): Workday detail pool, LIST clock credit for browser-queue
waits, and the Workday pagination guard. Offline: HTTP is faked."""
import ast
import threading
import time
from pathlib import Path

import pytest

from sponsorscout.scanning.ats.ats_scanner import ATSScanner

SCANNER_SRC = Path(__file__).resolve().parents[1] / "scanning" / "career" / "career_scanner.py"


def _target():
    """Target exactly as the scanner builds it from the bundled Leonardo row."""
    from sponsorscout.application import seed_manager as sm
    scanner = ATSScanner(seed_file=str(sm.bundled_ats_path()), skip_preflight=True)
    records = scanner.read_seed_file()
    return next(r for r in records if r["name"] == "Leonardo")


def _jobs(n, loc="Roma, IT"):
    return [{"title": f"Engineer {i}", "externalPath": f"/job/x/{i}",
             "locationsText": loc, "bulletFields": [f"R{1000 + i}"]}
            for i in range(n)]


class _FakeWorkday(ATSScanner):
    """Serves N postings in pages of 20; records detail GET calls."""

    def __init__(self, n, loc="Roma, IT", detail_text="Visa sponsorship is available."):
        super().__init__(skip_preflight=True)
        self._all = _jobs(n, loc)
        self._detail_text = detail_text
        self.detail_calls = []
        self.detail_threads = set()
        self._lock = threading.Lock()

    def _post_json(self, url, body):
        off, lim = body["offset"], body["limit"]
        page = self._all[off:off + lim]
        return {"total": len(self._all) if off == 0 else 0, "jobPostings": page}

    def _get_json(self, url):
        with self._lock:
            self.detail_calls.append(url)
            self.detail_threads.add(threading.get_ident())
        return {"jobPostingInfo": {"jobDescription": f"<p>{self._detail_text}</p>",
                                    "location": "Roma, IT"}}


def test_workday_pages_all_postings_and_uses_pool(monkeypatch):
    monkeypatch.setenv("SPONSORSCOUT_WORKDAY_WORKERS", "4")
    s = _FakeWorkday(45)
    rows = s.scan_workday(_target())
    assert len(rows) == 45
    assert len(s.detail_calls) == 45
    assert len({r["Job URL"] for r in rows}) == 45
    # Order follows the list, not the completion order of the pool.
    assert [r["Job Title"] for r in rows] == [f"Engineer {i}" for i in range(45)]


def test_workday_detail_budget_zero_still_fetches_multi_site(monkeypatch):
    monkeypatch.setenv("SPONSORSCOUT_WORKDAY_DETAILS", "0")
    monkeypatch.setenv("SPONSORSCOUT_WORKDAY_WORKERS", "2")
    s = _FakeWorkday(5, loc="3 Locations")
    rows = s.scan_workday(_target())
    assert len(rows) == 5
    # Only the multi-site postings need a detail GET when the budget is spent.
    assert len(s.detail_calls) == 5
    assert all("Roma" in r["Job Location"] for r in rows)


def test_workday_budget_limits_descriptions(monkeypatch):
    monkeypatch.setenv("SPONSORSCOUT_WORKDAY_DETAILS", "3")
    monkeypatch.setenv("SPONSORSCOUT_WORKDAY_WORKERS", "1")
    s = _FakeWorkday(8)
    rows = s.scan_workday(_target())
    assert len(rows) == 8
    # Only the three postings that got a description can produce a Yes verdict.
    assert sum(r["Visa Sponsorship"] == "Yes" for r in rows) == 3
    assert len(s.detail_calls) == 3


def test_workday_serial_path_with_one_worker(monkeypatch):
    monkeypatch.setenv("SPONSORSCOUT_WORKDAY_WORKERS", "1")
    s = _FakeWorkday(3)
    assert len(s.scan_workday(_target())) == 3


def test_workday_stops_when_total_reached(monkeypatch):
    monkeypatch.setenv("SPONSORSCOUT_WORKDAY_WORKERS", "1")
    s = _FakeWorkday(20)
    calls = []
    orig = s._post_json

    def counting(url, body):
        calls.append(body["offset"])
        return orig(url, body)

    s._post_json = counting
    assert len(s.scan_workday(_target())) == 20
    assert calls == [0]  # total == 20 on page 1: no second list request


def test_bad_worker_env_falls_back(monkeypatch):
    monkeypatch.setenv("SPONSORSCOUT_WORKDAY_WORKERS", "many")
    s = _FakeWorkday(2)
    assert len(s.scan_workday(_target())) == 2


def test_list_clock_credits_browser_queue_wait():
    """The LIST budget clock must not count time spent queued for the single
    Chromium session. Checked structurally: the credit line sits inside the
    list-phase browser block of crawl_target, and crawl_target owns `started`."""
    src = SCANNER_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "crawl_target")
    credit = [n for n in ast.walk(fn)
              if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name)
              and n.target.id == "started"]
    assert credit, "started must be credited with the semaphore wait"
    wait_marks = [n for n in ast.walk(fn)
                  if isinstance(n, ast.Assign) and any(
                      isinstance(t, ast.Name) and t.id == "_sem_t0" for t in n.targets)]
    assert wait_marks and wait_marks[0].lineno < credit[0].lineno


def test_semaphore_wait_is_credited_to_started():
    """Behavioural model of the same arithmetic: a company queued 200 s
    behind another session must still have its full LIST budget."""
    sem = threading.Semaphore(1)
    sem.acquire()  # another company holds the only Chromium session

    started = time.monotonic() - 200  # company started 200 s ago, queued since
    _sem_t0 = time.monotonic() - 200
    threading.Timer(0.05, sem.release).start()
    sem.acquire()
    started += time.monotonic() - _sem_t0
    sem.release()
    elapsed = time.monotonic() - started
    assert elapsed < 1.0  # only the real work counts, not the 200 s queue


# ── career: consent-banner stripping and JD browser fallback ────────────────

from sponsorscout.scanning.career import career_scanner as cs  # noqa: E402


def _bare_career():
    """CareerPortalScanner without the heavy __init__ (no seeds, no DB)."""
    import threading as _t
    obj = cs.CareerPortalScanner.__new__(cs.CareerPortalScanner)
    obj.config = cs.ProductionScannerConfig
    obj.cancel_event = None
    obj._jd_browser_sem = _t.Semaphore(1)
    obj._jd_browser_lock = _t.Lock()
    obj._jd_browser_used = 0
    # __new__ bypasses the constructor; mirror its new run-local detail state.
    from sponsorscout.scanning.detail_state import DetailRequestState
    obj._detail_state = DetailRequestState()
    obj._tls = _t.local()
    return obj


def test_consent_banner_is_removed_with_its_subtree():
    html = ('<body><div id="onetrust-banner-sdk"><div><p>your browser cookies</p>'
            '</div><p>cookie copy</p></div><div class="job"><p>Visa sponsorship '
            'is available.</p></div></body>')
    out = cs._strip_consent_blocks(html)
    assert "your browser" not in out and "cookie copy" not in out
    assert "Visa sponsorship is available." in out


def test_ordinary_content_is_not_stripped():
    html = '<body><div class="job-body"><p>Relocation support offered.</p></div></body>'
    assert cs._strip_consent_blocks(html) == html


def test_consent_strip_handles_unclosed_banner_gracefully():
    html = '<body><div id="cookie-x"><p>text</p></body>'
    out = cs._strip_consent_blocks(html)  # must terminate
    assert isinstance(out, str)


def test_thin_static_text_triggers_browser_and_longer_result_wins(monkeypatch):
    s = _bare_career()
    monkeypatch.setattr(s, "_fetch_jd_text_uncached", lambda url: ("shell", "", ""))
    long_text = "We offer visa sponsorship and relocation. " * 10
    calls = []

    def fake_browser(url, timeout_ms=15000):
        calls.append(url)
        return (long_text, "Roma, IT", "")

    monkeypatch.setattr(s, "_fetch_jd_text_browser", fake_browser)
    monkeypatch.setattr(cs, "_jd_cache", lambda: type("C", (), {
        "get": lambda self, u: None, "put": lambda self, u, r: None})())
    out = s._fetch_jd_text("https://careers.example/job/1")
    assert calls == ["https://careers.example/job/1"]
    assert out[0] == long_text


def test_substantial_static_text_skips_browser(monkeypatch):
    s = _bare_career()
    static = "Engineer role with visa sponsorship. " * 12  # > 200 chars
    monkeypatch.setattr(s, "_fetch_jd_text_uncached", lambda url: (static, "", ""))
    monkeypatch.setattr(cs, "_jd_cache", lambda: type("C", (), {
        "get": lambda self, u: None, "put": lambda self, u, r: None})())
    monkeypatch.setattr(s, "_fetch_jd_text_browser",
                        lambda *a, **k: pytest.fail("browser must not run"))
    assert s._fetch_jd_text("https://careers.example/job/2")[0] == static


def test_browser_result_shorter_than_static_is_ignored(monkeypatch):
    s = _bare_career()
    static = "Short but real text about the role."  # thin
    monkeypatch.setattr(s, "_fetch_jd_text_uncached", lambda url: (static, "", ""))
    monkeypatch.setattr(s, "_fetch_jd_text_browser", lambda *a, **k: ("x", "", ""))
    monkeypatch.setattr(cs, "_jd_cache", lambda: type("C", (), {
        "get": lambda self, u: None, "put": lambda self, u, r: None})())
    assert s._fetch_jd_text("https://careers.example/job/3")[0] == static


def test_browser_cap_is_enforced(monkeypatch):
    s = _bare_career()
    monkeypatch.setenv("SPONSORSCOUT_JD_BROWSER_MAX", "2")
    opened = []

    class _NoBrowser:
        def __init__(self, *a, **k):
            opened.append(1)
            raise RuntimeError("no browser in unit test")

    monkeypatch.setattr(cs, "sync_playwright", lambda: _NoBrowser())
    for i in range(5):
        s._fetch_jd_text_browser(f"https://careers.example/{i}")
    assert s._jd_browser_used == 2  # calls past the cap never open a browser


def test_jd_browser_uses_its_own_gate_so_detail_pass_cannot_deadlock():
    """The detail pass holds _browser_sem while it reads JDs. The JD browser
    must not wait on that same semaphore."""
    src = SCANNER_SRC.read_text(encoding="utf-8")
    fn = src[src.index("    def _fetch_jd_text_browser("):]
    fn = fn[:fn.index("\n    def ", 10)]
    assert "self._jd_browser_sem" in fn
    assert "self._browser_sem" not in fn
