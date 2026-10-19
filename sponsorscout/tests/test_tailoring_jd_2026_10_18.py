"""JD loading: browser-notice removal, JSON-LD JobPosting, rendered-page fallback (2026-10-18)."""
import json

import pytest

tailoring = pytest.importorskip("sponsorscout.ui.tabs.tailoring")

LONG = "Real job text about finance systems and reporting. " * 12

STORED_NEXTHINK = (
    "Google Chrome\nMicrosoft Edge\nApple Safari\nMozilla Firefox\n"
    "Senior Finance Systems Analyst (Netsuite)\nFull-time\n\nJob Description\nWe are looking for..."
)

FETCHED_SMARTRECRUITERS = (
    "# Sorry, Internet Explorer 11 is no longer supported by SmartRecruiters\n\n"
    "Please update to one of the following browsers:\n\n"
    "- Google Chrome\n- Microsoft Edge\n- Apple Safari\n- Mozilla Firefox\n\n"
    "You can find details about supported web browsers here.\n\n"
    "# Senior Finance Systems Analyst (Netsuite)\n\n- Madrid, Spain\n"
)


def _page(body: str, ld=None) -> str:
    scripts = ""
    if ld is not None:
        scripts = '<script type="application/ld+json">' + json.dumps(ld) + "</script>"
    return f"<html><head>{scripts}</head><body>{body}</body></html>"


def _jobposting(description: str) -> dict:
    return {"@context": "https://schema.org", "@type": "JobPosting",
            "title": "Laboratory Officer", "description": description}


class _Resp:
    def __init__(self, status, text):
        self.status_code = status
        self.headers = {"Content-Type": "text/html"}
        self.text = text


def test_stored_browser_list_is_removed():
    out = tailoring.strip_browser_notice(STORED_NEXTHINK)
    assert out.startswith("Senior Finance Systems Analyst (Netsuite)")


def test_ie_notice_block_is_removed():
    out = tailoring.strip_browser_notice(FETCHED_SMARTRECRUITERS)
    assert "Internet Explorer" not in out and "Google Chrome" not in out
    assert "Senior Finance Systems Analyst (Netsuite)" in out and "Madrid, Spain" in out


def test_normal_text_mentioning_browsers_is_kept():
    text = "We need Chrome experience and Firefox knowledge.\nEdge cases matter.\nPython or Java"
    assert tailoring.strip_browser_notice(text) == text


def test_jobposting_description_is_read_from_jsonld():
    desc = "<p>Laboratory Officer role. " + ("Support lab operations daily. " * 20) + "</p>"
    markup = _page("<div id='root'></div>", ld=_jobposting(desc))
    text = tailoring.jobposting_text(markup)
    assert "Support lab operations daily" in text and "<p>" not in text


def test_jsonld_preferred_over_empty_visible_page():
    markup = _page("<div id='root'></div>", ld=_jobposting("Job text. " * 60))
    assert "Job text." in tailoring.best_jd_text(markup)


def test_thin_static_page_uses_jsonld_without_browser(monkeypatch):
    markup = _page("<div id='root'></div>", ld=_jobposting("Real job text. " * 40))
    monkeypatch.setattr(tailoring.requests, "get", lambda *a, **k: _Resp(200, markup))
    monkeypatch.setattr(tailoring, "_read_rendered_html", lambda url: pytest.fail("no browser needed"))
    assert "Real job text." in tailoring.fetch_jd_from_url("https://example.com/job/1")


def test_thin_html_falls_back_to_rendered_page(monkeypatch):
    monkeypatch.setattr(tailoring.requests, "get",
                        lambda *a, **k: _Resp(200, "<html><body><div id='root'></div></body></html>"))
    monkeypatch.setattr(tailoring, "_read_rendered_html",
                        lambda url: _page("<p>" + LONG + "</p>"))
    assert "Real job text" in tailoring.fetch_jd_from_url("https://example.com/job/2")


def test_http_403_falls_back_to_rendered_page(monkeypatch):
    monkeypatch.setattr(tailoring.requests, "get", lambda *a, **k: _Resp(403, "Forbidden"))
    monkeypatch.setattr(tailoring, "_read_rendered_html", lambda url: _page("<p>" + LONG + "</p>"))
    assert "Real job text" in tailoring.fetch_jd_from_url("https://example.com/job/3")


def test_http_403_with_no_rendered_text_reports_status(monkeypatch):
    monkeypatch.setattr(tailoring.requests, "get", lambda *a, **k: _Resp(403, "Forbidden"))
    monkeypatch.setattr(tailoring, "_read_rendered_html", lambda url: "")
    with pytest.raises(tailoring.JDFetchError, match="403"):
        tailoring.fetch_jd_from_url("https://example.com/job/4")
