"""2026-10-28 (issue 004): the scan log must not over-claim what it did.

Four things these tests pin, all of them follow-ups to the review of run
20261028T192816 (Changelogs/issue_2026_10_28_ats_scan_review/ATS_SCAN_REVIEW.md):

  P2  a job_location seed whose country filter is off says "unfiltered", not
      "verified";
  P3  retried requests, swallowed detail errors and the two different reasons a
      posting ends up "unattempted" are named on the seed's own line;
  P4  a Workday list entry with neither title nor externalPath is skipped and
      counted instead of quarantined as a row of empty fields;
  P1  a Personio board can now reach a description at all, through a fixture --
      the live posting-page markup is deliberately NOT claimed anywhere here.

Offline by construction: every response is a fixture string served by a patched
_fetch/_get_json/_post_json. No network call leaves the process.
"""
import urllib.error

import pytest

from sponsorscout.scanning.ats import ats_scanner as ascan

SCOPE = "https://acme.example.test/careers"


def _scanner(scope=SCOPE):
    s = ascan.ATSScanner(skip_preflight=True)
    s._target_notes = []
    s._tls.evidence_scope = scope
    return s


def _target(**over):
    row = {"name": "Acme", "url": SCOPE, "ats_type": "personio",
           "source_type": "direct_employer", "industry": "Tech",
           "scope_policy": "global", "target_country": "Global"}
    row.update(over)
    return row


def _transport(s, scope=SCOPE):
    return s._detail_state.transport_summary(scope)


# ── P2: Scope Confidence ─────────────────────────────────────────────────────

def test_job_location_seed_is_unfiltered_while_the_country_filter_is_off():
    s = _scanner()
    assert s.SCOPE_FILTER is False, "run 20261028T192816 measured this default"
    confidence = s._scope_confidence(
        _target(scope_policy="job_location", target_country="Netherlands"),
        "Bengaluru, IN")
    # A Bengaluru row under a Netherlands seed was labelled "verified" in that
    # run. Nothing was checked, so nothing may be claimed.
    assert confidence == "unfiltered"


def test_job_location_seed_still_verifies_when_the_filter_is_on(monkeypatch):
    monkeypatch.setattr(ascan.ATSScanner, "SCOPE_FILTER", True)
    s = _scanner()
    target = _target(scope_policy="job_location", target_country="Netherlands")
    assert s._scope_confidence(target, "Amsterdam, NH") == "verified"
    assert s._scope_confidence(
        target, "New York, NY") == "unverified_job_location"


def test_the_column_a_user_reads_is_the_one_that_changed():
    # _scope_confidence() is only useful if make_row() puts its answer in the
    # CSV column, and if nothing about the row itself moved: the Wolters Kluwer
    # case (India/USA/Italy locations under a Netherlands seed) must still be
    # kept, just no longer labelled as a verified check.
    s = _scanner()
    row = s.make_row(
        _target(scope_policy="job_location", target_country="Netherlands"),
        "Backend Engineer", "https://careers.example.test/jobs/1",
        "Bengaluru, IN", "Bengaluru, IN", "Full-time", "", "workday_api")
    assert row["Scope Confidence"] == "unfiltered"
    assert row["Record Status"] == "accepted"
    assert row["Quarantine Reason"] == ""


def test_seed_url_and_global_labels_are_unchanged():
    s = _scanner()
    assert s._scope_confidence(_target(), "Amsterdam") == "n/a"
    assert s._scope_confidence(
        _target(scope_policy="seed_url", target_country="Netherlands"),
        "Amsterdam") == "verified"
    assert s._scope_confidence(
        _target(scope_policy="seed_url", target_country="Netherlands"),
        "Lisbon") == "unverified_seed_url"


# ── P3: transport counters ──────────────────────────────────────────────────

def test_a_quiet_seed_prints_nothing_extra():
    assert _transport(_scanner()) == ""


def test_counters_accumulate_and_backoff_is_reported_in_seconds():
    s = _scanner()
    s._detail_state.note_transport(SCOPE, "retries")
    s._detail_state.note_transport(SCOPE, "retries")
    s._detail_state.note_transport(SCOPE, "backoff_seconds", 1.5)
    s._detail_state.note_transport(SCOPE, "backoff_seconds", 3.0)
    s._detail_state.note_transport(SCOPE, "throttled_429")
    line = _transport(s)
    assert line == "transport: retries=2 backoff=4.5s throttled_429=1"


def test_an_unattributed_thread_contributes_nothing():
    s = ascan.ATSScanner(skip_preflight=True)   # no _tls.evidence_scope at all
    s._note_transport_retry("network_errors", 1.5)
    assert s._detail_state.transport_summary("") == ""


def test_retrying_a_429_is_visible_but_keeps_the_seed_ok(monkeypatch):
    monkeypatch.setattr(ascan, "HTTP_BACKOFF_BASE_SEC", 0.0)
    calls = []

    def flaky(url, method, data, headers, timeout):
        calls.append(url)
        if len(calls) < 3:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
        return 200, b'{"ok": true}'

    monkeypatch.setattr(ascan, "_keepalive_request", flaky)
    s = _scanner()
    assert "ok" in s._fetch("https://api.smartrecruiters.com/v1/companies/x")
    assert "retries=2" in _transport(s) and "throttled_429=2" in _transport(s)
    # Retrying is not the same as an unfinished scan: two 429s that then
    # succeed must not turn a complete seed into a "partial" one.
    from sponsorscout.scanning.common import completion_status
    assert completion_status(10, "", [_transport(s)]) == "ok"


def test_a_definitive_404_is_not_counted_as_a_retry(monkeypatch):
    def missing(url, method, data, headers, timeout):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(ascan, "_keepalive_request", missing)
    s = _scanner()
    with pytest.raises(urllib.error.HTTPError):
        s._fetch("https://api.smartrecruiters.com/v1/companies/x")
    assert _transport(s) == ""


def test_smartrecruiters_separates_no_id_from_an_exhausted_budget(monkeypatch):
    monkeypatch.setenv("SPONSORSCOUT_SMARTRECRUITERS_DETAILS", "1")
    postings = [
        {"id": "a", "name": "Backend Engineer", "location": {"city": "Berlin"}},
        {"name": "Posting Without An Id", "location": {"city": "Berlin"}},
        {"id": "c", "name": "Data Engineer", "location": {"city": "Berlin"}},
    ]
    body = "<div>" + "role requirements and responsibilities " * 12 + "</div>"

    def fake_json(url):
        if "/postings?" in url:
            return {"content": postings, "totalFound": len(postings)}
        return {"jobAd": {"sections": {"jobDescription": {"text": body}}}}

    s = _scanner()
    s._get_json = fake_json
    rows = s.scan_smartrecruiters(_target(ats_type="smartrecruiters",
                                          board_slug="DeliveryHero"))
    assert len(rows) == 3, "the id-less posting still gets its listing-only row"
    line = _transport(s)
    assert "no_id=1" in line, line          # the feed gave no posting id
    assert "budget_skipped=1" in line, line  # budget spent on the first row
    assert "detail_errors" not in line


def test_a_detail_endpoint_that_always_fails_says_so(monkeypatch):
    monkeypatch.setenv("SPONSORSCOUT_SMARTRECRUITERS_DETAILS", "2")
    postings = [{"id": "a", "name": "A", "location": {}},
                {"id": "b", "name": "B", "location": {}}]

    def fake_json(url):
        if "/postings?" in url:
            return {"content": postings, "totalFound": 2}
        raise urllib.error.HTTPError(url, 500, "Boom", {}, None)

    s = _scanner()
    s._get_json = fake_json
    rows = s.scan_smartrecruiters(_target(ats_type="smartrecruiters",
                                          board_slug="DeliveryHero"))
    assert len(rows) == 2
    # Before this change both rows just had no description, which is what a
    # board with 945 readable postings also looks like.
    assert "detail_errors=2" in _transport(s)


# ── P4: a list entry with no identity is not a reviewable quarantine row ─────

def test_workday_entries_without_title_or_path_are_counted_not_quarantined():
    # scan_workday re-bases the evidence scope on the seed URL it was handed,
    # so its counters live under that key rather than under SCOPE.
    wd = "https://acme.wd3.myworkdayjobs.com/careers"

    def fake_post(url, body):
        return {"total": 2, "jobPostings": [
            # what Delivery Hero's board occasionally returns for a hidden
            # requisition: an entry with nothing in it at all
            {"title": "", "externalPath": "", "locationsText": None},
            {"title": "Security Engineer", "externalPath": "/job/R42",
             "locationsText": "Amsterdam", "bulletFields": ["R42"]},
        ]}

    s = _scanner()
    s._post_json = fake_post
    s._get_json = lambda url: {"jobPostingInfo": {
        "jobDescription": "<p>" + "duty " * 40 + "</p>", "location": "Amsterdam"}}
    rows = s.scan_workday(_target(ats_type="workday", url=wd))
    assert len(rows) == 1
    assert rows[0]["Record Status"] == "accepted"
    assert not any(r["Record Status"] == "quarantine" for r in rows)
    assert "empty_listing_item=1" in s._detail_state.transport_summary(wd)
    assert any("no title and no externalPath" in note
               for note in s._target_notes)


# ── P1: Personio detail pass, fixture only ───────────────────────────────────

_PERSONIO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<positionList>
  <position><id>1</id><name>Backend Engineer</name>
    <office>Amsterdam, NL</office><department>Platform</department>
    <employmentType>Full-time</employmentType>
    <jobUrl>https://acme.jobs.personio.de/job/1</jobUrl></position>
  <position><id>2</id><name>On Other Host</name>
    <office>Lisbon, PT</office>
    <jobUrl>https://careers.example.test/job/2</jobUrl></position>
  <position><id>3</id><name>Shell Page</name>
    <office>Berlin, DE</office>
    <jobUrl>https://acme.jobs.personio.de/job/3</jobUrl></position>
</positionList>"""

_PERSONIO_PAGE = """<html><body>
<nav><a href="/">Jobs home</a><a href="/imprint">Imprint</a></nav>
<div id="jobContent">
  <h2>Your tasks</h2>
  <p>Own the ingest pipeline end to end and work with stakeholders across EMEA.</p>
  <h2>Requirements</h2>
  <ul><li>Five years with Python</li><li>Fluent English</li></ul>
  <h2>Our offer</h2>
  <p>Visa sponsorship: We do sponsor visas for the right candidate.</p>
  <p>Relocation support: We help you move to Amsterdam.</p>
</div>
<footer>Company registration details</footer>
</body></html>"""


def _personio_scanner(monkeypatch, pages, budget=None):
    if budget is not None:
        monkeypatch.setenv("SPONSORSCOUT_PERSONIO_DETAILS", str(budget))
    fetched = []

    def fake_fetch(url, **kwargs):
        fetched.append(url)
        if url.endswith("/xml?language=en"):
            return _PERSONIO_XML
        return pages[url]

    s = _scanner()
    s._fetch = fake_fetch
    return s, fetched


def test_personio_detail_pass_reaches_the_row_and_the_coverage_line(monkeypatch):
    s, fetched = _personio_scanner(
        monkeypatch, {"https://acme.jobs.personio.de/job/1": _PERSONIO_PAGE,
                      "https://acme.jobs.personio.de/job/3":
                          "<html><body><nav>Jobs home</nav><footer>Imprint"
                          "</footer></body></html>"})
    rows = s.scan_personio(_target(url="https://acme.jobs.personio.de/search"))
    by_url = {row["Job URL"]: row for row in rows}
    jd = s.get_captured_jd("https://acme.jobs.personio.de/job/1")
    assert "Own the ingest pipeline" in jd
    assert "Five years with Python" in jd
    # page chrome must not be indexed as if it were a requirement
    assert "Jobs home" not in jd and "Imprint" not in jd and "Your tasks" in jd
    # the point of the pass: the verdicts that were Unknown by construction
    assert by_url["https://acme.jobs.personio.de/job/1"]["Visa Sponsorship"] == "Yes"
    assert by_url["https://acme.jobs.personio.de/job/3"]["Visa Sponsorship"] == "Unknown"
    assert by_url["https://acme.jobs.personio.de/job/3"]["Job Title"] == "Shell Page"
    coverage = s._detail_state.coverage(
        SCOPE, list(by_url), s.get_captured_jd)
    assert coverage["observed"] == 3
    assert coverage["readable"] == 1
    line = _transport(s)
    # the shell page says so, instead of being silent "unattempted"
    assert "no_container=1" in line, line
    # a posting the feed points somewhere else is left to the career scanner
    assert "skipped_external_url=1" in line, line
    assert "https://careers.example.test/job/2" not in fetched


def test_personio_budget_is_spent_only_on_a_usable_description(monkeypatch):
    pages = {"https://acme.jobs.personio.de/job/1":
                 "<html><body><main><p>" + "task " * 40 + "</p></main></body></html>",
             "https://acme.jobs.personio.de/job/3": _PERSONIO_PAGE}
    s, fetched = _personio_scanner(monkeypatch, pages, budget=1)
    rows = s.scan_personio(_target(url="https://acme.jobs.personio.de/search"))
    assert len(rows) == 3
    # position 1 succeeded and spent the budget; position 3 was never requested
    assert sum(1 for url in fetched if "/job/" in url) == 1
    assert "budget_skipped=1" in _transport(s)
    # the <main> fallback is what made position 1 readable without any hook
    assert "task task task" in s.get_captured_jd(
        "https://acme.jobs.personio.de/job/1")


def test_personio_row_survives_a_failing_detail_page(monkeypatch):
    def fake_fetch(url, **kwargs):
        if url.endswith("/xml?language=en"):
            return _PERSONIO_XML
        raise urllib.error.HTTPError(url, 503, "Boom", {}, None)

    s = _scanner()
    s._fetch = fake_fetch
    rows = s.scan_personio(_target(url="https://acme.jobs.personio.de/search"))
    assert len(rows) == 3, "an unreadable page must not lose the job"
    assert all(row["Job Title"] for row in rows)
    assert all(row["Visa Sponsorship"] == "Unknown" for row in rows)
    assert "detail_errors=2" in _transport(s)   # the external row is not fetched


def test_personio_feed_failure_still_falls_back_to_teamtailor(monkeypatch):
    def boom(url, **kwargs):
        raise urllib.error.HTTPError(url, 404, "No feed", {}, None)

    s = _scanner()
    s._fetch = boom
    s.scan_teamtailor = lambda target: []
    with pytest.raises(urllib.error.HTTPError):
        s.scan_personio(_target(url="https://acme.jobs.personio.de/search"))
