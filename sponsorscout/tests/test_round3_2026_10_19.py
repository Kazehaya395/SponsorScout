"""2026-10-19 (round 3): title/location conflict guard (ATS + career parity),
Workday CXS reads for career pages, and detector parity for the
"sponsor visas, however not able to sponsor every candidate" wording.
Offline: the Workday read is exercised through a fake fetch."""
import pytest

from sponsorscout.scanning.ats import ats_scanner as ascan
from sponsorscout.scanning.career import career_scanner as cs


# ── title / location conflict ────────────────────────────────────────────────

@pytest.mark.parametrize("title, location, conflict", [
    ("Applied Scientist (Internship in Paris or London)", "Seoul", True),
    ("Applied Scientist (Internship in Paris or London)", "Paris, France", False),
    ("Applied Scientist (Internship in Paris or London)", "France", False),
    ("Applied Scientist (Internship in Paris or London)", "London, UK", False),
    ("Engineer", "Seoul", False),                       # no places in title
    ("Engineer in Berlin or Munich", "Berlin, DE", False),
    ("Engineer in Berlin or Munich", "Madrid, ES", True),
    ("Engineer in Berlin or Munich", "Unknown", False),  # no evidence
    ("Engineer in Berlin or Munich", "Atlantis", False),  # unproven country
])
def test_title_place_conflict_rule(title, location, conflict):
    assert bool(ascan.title_place_conflict(title, location)) is conflict
    assert bool(cs.title_place_conflict(title, location)) is conflict


def test_ats_make_row_quarantines_the_conflicting_row():
    scanner = ascan.ATSScanner(skip_preflight=True)
    target = {"name": "Mistral AI", "url": "https://jobs.ashbyhq.com/mistral",
              "scope_policy": "global", "target_country": "Global",
              "source_type": "direct_employer", "industry": "AI",
              "seed_name": "Mistral AI", "ats_type": "ashby",
              "sponsorship_history": 80, "english_friendly": 85,
              "remote_score": 60}
    row = scanner.make_row(
        target, "Applied Scientist (Internship in Paris or London)",
        "https://jobs.ashbyhq.com/mistral/123", "Seoul", "Seoul", "Full-time",
        "We offer visa sponsorship.", "ashby", location_source="api")
    assert row["Record Status"] == "quarantine"
    assert row["Quarantine Reason"] == "location_conflicts_with_title"


def test_career_scope_branch_uses_the_conflict_reason():
    src = cs.__file__
    text = open(src, encoding="utf-8").read()
    assert "title_place_conflict(clean_title, out_location)" in text
    assert '"location_conflicts_with_title" if _conflict' in text


# ── Workday CXS reads for career pages ──────────────────────────────────────

def test_workday_cxs_mapping():
    f = cs.workday_cxs_detail_url
    assert f("https://embl.wd103.myworkdayjobs.com/EMBL/job/Hinxton/Title_JR1") == \
        "https://embl.wd103.myworkdayjobs.com/wday/cxs/embl/EMBL/job/Hinxton/Title_JR1"
    assert f("https://example.com/job/1") == ""           # not Workday
    assert f("https://x.wd3.myworkdayjobs.com/EMBL") == ""  # no /job/ segment
    assert f("") == ""


class _FakeCxs:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if isinstance(self.payload, Exception):
            raise self.payload
        import json
        return json.dumps(self.payload)


def _bare():
    from sponsorscout.scanning.detail_state import DetailRequestState
    obj = cs.CareerPortalScanner.__new__(cs.CareerPortalScanner)
    obj.config = cs.ProductionScannerConfig
    obj._detail_state = DetailRequestState()
    return obj


def test_workday_jd_reads_cxs_and_keeps_line_breaks():
    s = _bare()
    fake = _FakeCxs({"jobPostingInfo": {
        "location": "Hinxton, Cambridgeshire",
        "jobDescription": "<p>We offer visa sponsorship.</p><ul><li>Python</li>"
                          "<li>Relocation support</li></ul>"}})
    s._cxs_fetch = fake
    desc, loc, org = s._workday_jd("https://embl.wd103.myworkdayjobs.com/EMBL/job/H/T_1")
    assert "visa sponsorship" in desc and loc == "Hinxton, Cambridgeshire"
    assert "Python" in desc.split("\n")[1:] or "\nPython" in desc or "Python\n" in desc
    assert fake.urls and "/wday/cxs/embl/EMBL/job/H/T_1" in fake.urls[0]


def test_workday_jd_none_for_non_workday_url():
    s = _bare()
    s._cxs_fetch = _FakeCxs({})
    assert s._workday_jd("https://careers.example/job/1") is None


def test_workday_jd_failure_returns_empty_not_shell():
    s = _bare()
    s._cxs_fetch = _FakeCxs(OSError("boom"))
    assert s._workday_jd("https://x.wd3.myworkdayjobs.com/S/job/a/b") == ("", "", "")


def test_workday_jd_empty_description_returns_empty():
    s = _bare()
    s._cxs_fetch = _FakeCxs({"jobPostingInfo": {"jobDescription": "  "}})
    assert s._workday_jd("https://x.wd3.myworkdayjobs.com/S/job/a/b") == ("", "", "")


# ── detector parity: "sponsor visas, however not able to sponsor every candidate"

RESTRICTED = "We sponsor visas, however we are not able to sponsor every candidate."


def test_restricted_sponsorship_is_possible_in_both_scanners():
    assert ascan.apply_support_fp_guards("Yes", "Unknown", RESTRICTED, "Engineer")[0] == "Possible"
    assert cs.apply_support_fp_guards("Yes", "Unknown", RESTRICTED, "Engineer")[0] == "Possible"


def test_plain_offer_stays_yes():
    text = "We offer visa sponsorship for qualified candidates."
    assert ascan.apply_support_fp_guards("Yes", "Unknown", text, "Engineer")[0] == "Yes"
