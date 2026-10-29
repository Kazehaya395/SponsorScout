"""Round 10c (2026-10-19): silent ATS failures now report their cause.

1. Personio failed and Teamtailor failed too: the Teamtailor error replaced
   the Personio cause. Both causes are now in the message.
2. Personio failed and Teamtailor had no feed (404): the run returned an
   empty board with no error. It now raises the Personio cause.
3. Browser fallback failed: it recorded an error and returned [], which read
   as an empty company. It now raises with the cause.
"""
import pytest

from sponsorscout.scanning.ats import ats_scanner as ascan


def _scanner():
    return ascan.ATSScanner(skip_preflight=True)


TARGET = {"name": "Spendesk", "url": "https://career.spendesk.com/",
          "ats_type": "personio", "board_slug": "spendesk"}


def test_both_feeds_failing_reports_both_causes(monkeypatch):
    s = _scanner()

    def personio_down(url, *a, **k):
        raise ConnectionError("personio unreachable")
    monkeypatch.setattr(s, "_fetch", personio_down)

    def teamtailor_down(target):
        raise PermissionError("teamtailor 403")
    monkeypatch.setattr(s, "scan_teamtailor", teamtailor_down)

    with pytest.raises(RuntimeError) as info:
        s.scan_personio(TARGET)
    msg = str(info.value)
    assert "personio feed failed (ConnectionError: personio unreachable)" in msg
    assert "teamtailor feed failed (PermissionError: teamtailor 403)" in msg


def test_personio_failure_with_no_teamtailor_feed_is_not_silent(monkeypatch):
    s = _scanner()

    def personio_down(url, *a, **k):
        raise ConnectionError("personio 404")
    monkeypatch.setattr(s, "_fetch", personio_down)
    monkeypatch.setattr(s, "scan_teamtailor", lambda target: [])  # no feed, no jobs

    with pytest.raises(ConnectionError, match="personio 404"):
        s.scan_personio(TARGET)


def test_teamtailor_rows_still_win_when_personio_is_missing(monkeypatch):
    s = _scanner()

    def personio_down(url, *a, **k):
        raise ConnectionError("personio 404")
    monkeypatch.setattr(s, "_fetch", personio_down)
    sentinel = [{"title": "Accountant"}]
    monkeypatch.setattr(s, "scan_teamtailor", lambda target: sentinel)
    assert s.scan_personio(TARGET) == sentinel


def test_browser_fallback_failure_raises_with_cause(monkeypatch):
    s = _scanner()

    def chromium_crash():
        raise OSError("chromium crashed")
    monkeypatch.setattr(ascan, "sync_playwright", chromium_crash)
    target = {"name": "Akur8", "url": "https://jobs.example.test/", "ats_type": "auto"}
    with pytest.raises(RuntimeError, match="browser fallback failed \\(OSError: chromium crashed\\)"):
        s.browser_fallback(target)


# ── career-portal fixes (same round) ─────────────────────────────────────

from sponsorscout.scanning.career import career_scanner as cs  # noqa: E402


def _career():
    return cs.CareerPortalScanner(input_csv="/dev/null", output_csv="/tmp/_r10c.csv",
                                  skip_preflight=True)


@pytest.mark.parametrize("title,url", [
    ("Photographer", "https://www.agentur-loop.com/careers/photographer"),
])
def test_loop_photographer_is_a_valid_title(title, url):
    assert _career().is_valid_job_title(title, "LOOP", url)


def test_loop_proactive_application_stays_accepted_per_evergreen_contract():
    assert _career().is_valid_job_title(
        "Proactive Application", "LOOP",
        "https://www.agentur-loop.com/careers/proactive-application")


def test_detail_page_h2_title_is_used_when_h1_is_portal_name(monkeypatch):
    class Resp:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n=None): return self.body.encode()
    html = ("<html><head><title>Jobdetail</title></head><body>"
            "<h1>Jobdetail</h1><h2>Role Overview</h2><h2>CFO</h2></body></html>")
    monkeypatch.setattr(cs.urllib.request, "urlopen", lambda req, timeout=None: Resp(html))
    got = _career()._title_from_detail_page(
        "https://www.workinaustria.com/en/employees/jobs/jobdetail/8250/cfo", "Work in Austria")
    assert got == "CFO"


def test_detail_page_with_only_section_headings_returns_nothing(monkeypatch):
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n=None):
            return b"<h1>Jobdetail</h1><h2>Role Overview</h2><h2>Responsibilities</h2>"
    monkeypatch.setattr(cs.urllib.request, "urlopen", lambda req, timeout=None: Resp())
    assert _career()._title_from_detail_page(
        "https://www.workinaustria.com/en/employees/jobs/jobdetail/8250/cfo",
        "Work in Austria") == ""
