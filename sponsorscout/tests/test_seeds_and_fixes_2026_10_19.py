"""Regression tests for the 2026-10-19 round: seed replacement, verdict guards,
Workday spaced locations, Workable JD, JD text cleanup and the JD cache."""
import json
import time
from pathlib import Path

import pytest

from sponsorscout.application import seed_manager as sm
from sponsorscout.scanning.ats.ats_scanner import apply_support_fp_guards as ats_guard
from sponsorscout.scanning.career import career_scanner as cs
from sponsorscout.scanning.common import workday_spaced_location
from sponsorscout.scanning.jd_support import normalize_jd_text


# ── seeds ────────────────────────────────────────────────────────────────────

def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_bundled_seeds_load_and_validate():
    for path in (sm.bundled_ats_path(), sm.bundled_career_path()):
        ok, errs = sm.validate_file(path)
        assert ok, errs


def test_bundled_moss_is_on_the_live_ashby_board():
    rows = {r["name"]: r for r in sm.read_seed_rows(sm.bundled_ats_path())["rows"]}
    assert rows["Moss"]["provider"] == "ashby"
    assert rows["Moss"]["careers_url"] == "https://jobs.ashbyhq.com/moss"


def test_spendesk_uses_the_teamtailor_careers_site():
    rows = {r["name"]: r for r in sm.read_seed_rows(sm.bundled_ats_path())["rows"]}
    assert rows["Spendesk"]["careers_url"] == "https://career.spendesk.com/jobs"
    assert rows["Spendesk"]["provider"] == "teamtailor"


def test_revision_sync_replaces_old_urls_keeps_user_rows(tmp_path, monkeypatch):
    seeds = tmp_path / "seeds"
    monkeypatch.setattr(sm, "SEEDS_DIR", seeds)
    monkeypatch.setattr(sm, "user_ats_path", lambda: seeds / "company_ATS_seed.csv")
    monkeypatch.setattr(sm, "user_career_path", lambda: seeds / "company_Career_seed.csv")
    monkeypatch.setattr(sm, "ensure_user_seeds", lambda force=False: None)
    bundled = sm.read_seed_rows(sm.bundled_ats_path())
    bundled_text = sm.bundled_ats_path().read_text(encoding="utf-8-sig")
    old_moss = bundled_text.replace("https://jobs.ashbyhq.com/moss,ashby,moss",
                                    "https://moss.jobs.personio.de,personio,moss")
    _write(seeds / "company_ATS_seed.csv",
           old_moss + "MyCo,https://example.com/jobs,workable,myco,direct_employer,Germany,"
                      "job_location,Tech,50,50,50,\"my own\"\n")
    _write(seeds / "company_Career_seed.csv",
           sm.bundled_career_path().read_text(encoding="utf-8-sig"))

    sm._sync_to_revision(log_fn=lambda *_: None)
    rows = sm.read_seed_rows(seeds / "company_ATS_seed.csv")["rows"]
    by_name = {r["name"]: r for r in rows if not sm.is_comment_row(r)}
    assert by_name["Moss"]["careers_url"] == "https://jobs.ashbyhq.com/moss"
    assert by_name["MyCo"]["careers_url"] == "https://example.com/jobs"
    assert len([r for r in rows if not sm.is_comment_row(r)]) == len(bundled["rows"]) + 1
    assert (seeds / ".seed_revision").read_text(encoding="utf-8") == sm.SEED_REVISION
    assert any((seeds / "backup").rglob("company_ATS_seed.csv"))
    # Second call is a no-op.
    assert sm._sync_to_revision(log_fn=lambda *_: None) == {"ats": 0, "career": 0}


def test_revision_sync_keeps_both_pam_panorama_rows(tmp_path, monkeypatch):
    seeds = tmp_path / "seeds"
    monkeypatch.setattr(sm, "SEEDS_DIR", seeds)
    monkeypatch.setattr(sm, "user_ats_path", lambda: seeds / "company_ATS_seed.csv")
    monkeypatch.setattr(sm, "user_career_path", lambda: seeds / "company_Career_seed.csv")
    monkeypatch.setattr(sm, "ensure_user_seeds", lambda force=False: None)
    _write(seeds / "company_ATS_seed.csv", sm.bundled_ats_path().read_text(encoding="utf-8-sig"))
    _write(seeds / "company_Career_seed.csv", sm.bundled_career_path().read_text(encoding="utf-8-sig"))
    sm._sync_to_revision(log_fn=lambda *_: None)
    pam = [r["careers_url"] for r in sm.read_seed_rows(seeds / "company_Career_seed.csv")["rows"]
           if r["name"] == "Pam Panorama"]
    assert len(pam) == 2


# ── location ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("IT - Venegono Superiore", "Venegono Superiore, IT"),
    ("GB - Yeovil - Lysander Rd", "Yeovil, GB"),
    ("PL - Świdnik S.A.", "Świdnik, PL"),
    ("NLD - Alphen Aan Den Rijn", "Alphen Aan Den Rijn, NLD"),
])
def test_workday_spaced_codes_become_city_country(raw, expected):
    assert workday_spaced_location(raw) == expected


def test_workday_spaced_street_gives_country_only():
    assert workday_spaced_location("GB - 12 High Street") == "GB"


def test_workday_spaced_helper_ignores_plain_text():
    assert workday_spaced_location("Hinxton, Cambridgeshire") is None


def test_career_sanitiser_accepts_leonardo_codes():
    scanner = cs.CareerPortalScanner.__new__(cs.CareerPortalScanner)
    assert scanner._sanitize_job_location("IT - Venegono Superiore", "Leonardo") == "Venegono Superiore, IT"


# ── verdict guards ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Provided advice about visas or passports, verified that all travel arrangements are confirmed.",
    "- Handle immigration requests, work permits, and related documentation; Benefits vary by country.",
    "Location & work authorization: This internship is available only in France. Please apply.",
])
def test_duties_and_restrictions_are_not_yes(text):
    assert ats_guard("Yes", "Unknown", text, "Role")[0] == "Unknown"
    assert cs.apply_support_fp_guards("Yes", "Unknown", text, "Role")[0] == "Unknown"


@pytest.mark.parametrize("text", [
    "We offer visa sponsorship and relocation support for this role.",
    "This internship is available only in France. We offer visa sponsorship for candidates who relocate.",
])
def test_real_offers_stay_yes(text):
    assert ats_guard("Yes", "Unknown", text, "Role")[0] == "Yes"
    assert cs.apply_support_fp_guards("Yes", "Unknown", text, "Role")[0] == "Yes"


# ── JD text ─────────────────────────────────────────────────────────────────

def test_leading_block_separator_is_dropped():
    assert normalize_jd_text("<p>Global Corporate Controller.</p><p>Full-time.</p>").startswith(
        "Global Corporate Controller")


def test_workable_jd_uses_job_api(monkeypatch):
    scanner = cs.CareerPortalScanner.__new__(cs.CareerPortalScanner)
    payload = {"description": "<p>Consultant role.</p>", "requirements": "<ul><li>Degree</li></ul>",
               "benefits": "", "location": {"city": "Munich", "region": "Bavaria", "country": "Germany"}}

    class _Resp:
        def __init__(self, body): self._b = body.encode()
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(cs.urllib.request, "urlopen", lambda req, timeout=None: _Resp(json.dumps(payload)))
    desc, loc, _org = scanner._workable_jd("https://apply.workable.com/avomind/j/38165AC2D5")
    assert "Consultant role." in desc and "Degree" in desc
    assert loc == "Munich, Bavaria, Germany"


def test_workable_match_ignores_other_hosts():
    scanner = cs.CareerPortalScanner.__new__(cs.CareerPortalScanner)
    assert scanner._workable_jd("https://example.com/jobs/1") is None


# ── JD cache ────────────────────────────────────────────────────────────────

def test_jd_cache_round_trip_and_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_JD_CACHE_FLUSH_EVERY", 1)
    cache = cs._JDCache.__new__(cs._JDCache)
    import threading
    cache._lock = threading.Lock()
    cache._data = {}
    cache._dirty = 0
    cache.hits = 0
    cache.path = str(tmp_path / "jd_cache.json")
    cache.put("https://x.test/job/1", ("desc text", "Rome, IT", "Org"))
    cache.put("https://x.test/job/empty", ("", "", ""))  # never cached
    assert cache.get("https://x.test/job/1") == ("desc text", "Rome, IT", "Org")
    assert cache.get("https://x.test/job/empty") is None
    saved = json.loads(Path(cache.path).read_text(encoding="utf-8"))
    assert "https://x.test/job/1" in saved
    cache._data["https://x.test/job/1"]["ts"] = time.time() - cs._JD_CACHE_TTL_SEC - 5
    assert cache.get("https://x.test/job/1") is None
