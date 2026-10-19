"""2026-10-19 (round 4): empty ATS boards must say why.

Teamtailor used to swallow every error as "no jobs"; the browser fallback
returned [] when Playwright was missing; the scan log had no reason for a
zero-row board. Akur8 was tagged Workable but lives on Ashby. Offline."""
import urllib.error

import pytest

from sponsorscout.application import seed_manager as sm
from sponsorscout.scanning.ats import ats_scanner as ascan


def _scanner():
    return ascan.ATSScanner(skip_preflight=True)


def _target(url="https://career.spendesk.com/jobs"):
    return {"name": "Spendesk", "url": url, "ats_type": "teamtailor",
            "source_type": "direct_employer", "scope_policy": "global",
            "target_country": "Global"}


def _http_error(code):
    return urllib.error.HTTPError("https://x/jobs.json", code, "err", {}, None)


def test_teamtailor_404_is_a_reasoned_empty_board():
    s = _scanner()
    s._target_notes = []
    s._fetch = lambda url, **k: (_ for _ in ()).throw(_http_error(404))
    assert s.scan_teamtailor(_target()) == []
    assert any("no /jobs.json feed" in n for n in s._target_notes)


@pytest.mark.parametrize("code", [403, 429, 503])
def test_teamtailor_real_failures_propagate(code):
    s = _scanner()
    s._target_notes = []

    def boom(url, **k):
        raise _http_error(code)

    s._fetch = boom
    with pytest.raises(urllib.error.HTTPError) as exc:
        s.scan_teamtailor(_target())
    assert exc.value.code == code


def test_teamtailor_network_failure_propagates():
    s = _scanner()
    s._target_notes = []

    def boom(url, **k):
        raise urllib.error.URLError("connection reset by peer")

    s._fetch = boom
    with pytest.raises(urllib.error.URLError):
        s.scan_teamtailor(_target())


def test_browser_fallback_raises_when_playwright_missing(monkeypatch):
    s = _scanner()
    monkeypatch.setattr(ascan, "sync_playwright", None)
    monkeypatch.setattr(ascan, "_playwright_unavailable_reason", lambda: "no module")
    with pytest.raises(RuntimeError) as exc:
        s.browser_fallback({"name": "X", "url": "https://x.example/careers",
                            "ats_type": "auto"})
    assert "browser fallback unavailable: no module" in str(exc.value)


def test_note_is_collected_only_while_a_target_runs():
    s = _scanner()
    s._note("ignored before any target")  # no list yet: must not raise
    s._target_notes = []
    s._note("teamtailor: no feed")
    assert s._target_notes == ["teamtailor: no feed"]


def test_moss_has_no_stale_personio_note():
    assert "Moss" not in ascan.KNOWN_BOARD_ISSUES
    assert "Ecosia" in ascan.KNOWN_BOARD_ISSUES


def test_bundled_akur8_is_on_ashby_and_revision_bumped():
    rows = {r["name"]: r for r in sm.read_seed_rows(sm.bundled_ats_path())["rows"]}
    assert rows["Akur8"]["provider"] == "ashby"
    assert rows["Akur8"]["careers_url"] == "https://jobs.ashbyhq.com/akur8"
    assert sm.SEED_REVISION == "2026-10-19-r4"
