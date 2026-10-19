"""Regression tests: run-on JSON-LD JD text and Workday "N Locations" detail lookup."""
import json

from sponsorscout.scanning.ats.ats_scanner import ATSScanner
from sponsorscout.ui.tabs.tailoring import _split_runon, jobposting_text


def test_split_runon_breaks_lowercase_capital_joins():
    out = _split_runon("design complexesDesign and build.")
    assert "complexes\nDesign" in out


def test_split_runon_keeps_short_camel_words_and_acronyms():
    out = _split_runon("We use iPhone and McDonald tooling.")
    assert "iPhone" in out and "McDonald" in out and "\n" not in out


def test_jobposting_text_splits_single_paragraph_description():
    ld = {"@type": "JobPosting",
          "description": "Overview We design complexesDesign tools.Your role As a mechanic"}
    markup = '<script type="application/ld+json">' + json.dumps(ld) + "</script>"
    text = jobposting_text(markup)
    assert "complexes\nDesign" in text
    assert "tools.\nYour" in text


def _scanner(detail_calls):
    s = ATSScanner.__new__(ATSScanner)
    s.run_id = "T"
    s._post_json = lambda api, body: {"total": 2, "jobPostings": [
        {"title": "A", "externalPath": "/job/x/A_R1", "locationsText": "10 Locations",
         "bulletFields": ["R1"]},
        {"title": "B", "externalPath": "/job/x/B_R2", "locationsText": "Bengaluru, IND",
         "bulletFields": ["R2"]}]}
    s._seed_slug = lambda t, i: ""

    def get_json(url):
        detail_calls.append(url)
        return {"jobPostingInfo": {"location": "Munich, DEU",
                                   "additionalLocations": ["Berlin, DEU"],
                                   "jobDescription": "<p>x</p>"}}
    s._get_json = get_json
    return s


def test_workday_n_locations_resolved_from_detail_even_without_budget(monkeypatch):
    monkeypatch.setenv("SPONSORSCOUT_WORKDAY_DETAILS", "0")
    calls = []
    target = {"url": "https://autodesk.wd1.myworkdayjobs.com/Ext", "name": "Autodesk",
              "company": "Autodesk", "source_type": "ats", "ats_type": "workday",
              "type": "workday", "industry": ""}
    rows = _scanner(calls).scan_workday(target)
    by_title = {r["Job Title"]: r for r in rows}
    assert by_title["A"]["Job Location"] == "Munich, DEU"
    assert by_title["B"]["Job Location"] == "Bengaluru, IND"
    # Only the placeholder row needs a detail GET; the plain row has no budget left.
    assert len(calls) == 1
