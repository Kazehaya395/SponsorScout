"""Regression tests for the 20261018T183133 run: Workday JD text, Workday site
location codes, Broadcom seed slug, Moss Ashby seed, sponsorship filter."""
import csv
from pathlib import Path

from sponsorscout.scanning.ats.ats_scanner import ATSScanner
from sponsorscout.ui.tabs.tailoring import workday_cxs_url, _fix_mojibake

SEEDS = Path(__file__).resolve().parents[1] / "data" / "seeds" / "company_ATS_seed.csv"


def test_strip_html_keeps_block_boundaries():
    out = ATSScanner._strip_html("<p><b>Position Overview</b></p><ul><li>Design</li><li>Build</li></ul>")
    assert out.split("\n") == ["Position Overview", "Design", "Build"]


def test_workday_site_location_codes():
    s = ATSScanner()
    assert s.format_location("USA-Colorado-Fort Collins-4380 Ziegler Road") == "Fort Collins, USA"
    assert s.format_location("USA-TX-Austin - River Place B7") == "Austin, USA"
    assert s.format_location("USA-CA San Jose Innovation Drive") == "USA"
    assert s.format_location("IND-Hyderabad 115 IT Park Area") == "IND"
    assert s.format_location("Toronto, ON, CAN") == "Toronto, ON, CAN"


def test_workday_cxs_url_from_job_page():
    u = "https://autodesk.wd1.myworkdayjobs.com/Ext/job/Loc/Title_26WD1"
    assert workday_cxs_url(u) == \
        "https://autodesk.wd1.myworkdayjobs.com/wday/cxs/autodesk/Ext/job/Loc/Title_26WD1"
    assert workday_cxs_url("https://example.com/careers/job/1") == ""


def test_mojibake_repaired():
    assert _fix_mojibake("How Youâ€™d Spend") == "How You’d Spend"


def _seed_row(name):
    with open(SEEDS, encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh):
            if row and row[0] == name:
                return row
    raise AssertionError(name)


def test_broadcom_seed_has_tenant_and_site():
    assert _seed_row("Broadcom")[3] == "broadcom/External_Career"


def test_moss_seed_moved_to_ashby():
    row = _seed_row("Moss")
    assert row[1] == "https://jobs.ashbyhq.com/moss" and row[2] == "ashby"


def test_business_titles_with_country_suffix_are_kept():
    s = ATSScanner()
    u = "https://job-boards.greenhouse.io/anthropic/jobs/5446290008"
    assert s.valid_title("Data Center Capacity Delivery, Canada", u)
    assert s.valid_title("External Affairs, South Korea", u)


def test_real_location_stubs_still_rejected():
    s = ATSScanner()
    for stub in ("Pisa, Italy", "ITA, PI, Pisa", "Milano, MI", "Paris, France"):
        assert s._title_is_pure_location(stub), stub


def test_visa_exemption_counts_as_sponsorship_support():
    from sponsorscout.scanning.jd_support import VERDICT_YES, VERDICT_NO
    s = ATSScanner()
    assert s.classify_support(
        "Benefits for non-UK residents: Visa exemption, education grant.",
        "Engineer", "EMBL")[1] == VERDICT_YES
    assert s.classify_support(
        "We recruit internationally and successful candidates are offered visa exemptions.",
        "Engineer", "EMBL")[1] == VERDICT_YES
    assert s.classify_support(
        "We do not offer visa exemptions.", "Engineer", "EMBL")[1] == VERDICT_NO


def test_teamtailor_adapter_reads_json_feed(monkeypatch):
    import json as _json
    feed = {"items": [{
        "title": "Account Executive Iberia",
        "url": "https://career.spendesk.com/jobs/7168043-account-executive-iberia",
        "_jobposting": {
            "description": "<p>Drive revenue.</p><ul><li>Prospect</li></ul>",
            "jobLocation": [{"@type": "Place", "address": {
                "addressLocality": "Barcelona", "addressCountry": "ES"}}],
        }}]}
    s = ATSScanner()
    s.run_id = "T"
    monkeypatch.setattr(s, "_fetch", lambda url, **kw: _json.dumps(feed))
    t = {"url": "https://career.spendesk.com/jobs", "name": "Spendesk",
         "company": "Spendesk", "source_type": "direct_employer", "ats_type": "teamtailor",
         "type": "teamtailor", "industry": "", "board_slug": "spendesk",
         "target_country": "Global", "scope_policy": "global"}
    rows = s.scan_teamtailor(t)
    assert len(rows) == 1
    assert rows[0]["Job Title"] == "Account Executive Iberia"
    assert rows[0]["Job Location"] == "Barcelona, ES"
    assert rows[0]["Job URL"].endswith("7168043-account-executive-iberia")
