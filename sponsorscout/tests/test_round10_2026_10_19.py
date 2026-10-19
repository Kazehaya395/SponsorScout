"""Round 10 (2026-10-19): career list budget, franchisee titles, SmartRecruiters seed.

1. Work in Austria / Proximus exhausted the 300 s LIST budget. Default is now
   1500 s and the per-company cap 1800 s, both overridable by environment.
2. "Franchisee, Wien, Austria" was read as a pure location (no role word),
   so it was quarantined. "franchisee" / "franchisor" / "franchise partner"
   are now role words in both scanners.
3. SmartRecruiters is a directory of employers, not an ATS board. It moved
   from the ATS seed to the career seed; the seed revision was bumped so
   existing installs pick it up.

Robert Walters Germany is NOT covered here: a static-403 change was tried and
reverted, because the browser pass already runs and finds 0 cards.
"""
import csv
import importlib
import os
import re
from pathlib import Path

from sponsorscout.application import seed_manager
from sponsorscout.scanning.ats import ats_scanner as ats
from sponsorscout.scanning.career import career_scanner as cs

REPO = Path(__file__).resolve().parents[1]
SEEDS = REPO / "data" / "seeds"


def _reload_career(monkeypatch, **env):
    for k in ("SPONSORSCOUT_LIST_BUDGET", "SPONSORSCOUT_COMPANY_TIME_SEC"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(cs)


def test_list_budget_default_is_1500(monkeypatch):
    mod = _reload_career(monkeypatch)
    try:
        assert mod.ProductionScannerConfig.LIST_TIME_BUDGET_SEC == 1500
    finally:
        monkeypatch.undo()
        importlib.reload(cs)


def test_company_cap_default_is_1800(monkeypatch):
    mod = _reload_career(monkeypatch)
    try:
        assert mod.ProductionScannerConfig.MAX_COMPANY_TIME_SEC == 1800
    finally:
        monkeypatch.undo()
        importlib.reload(cs)


def test_budgets_are_env_overridable(monkeypatch):
    mod = _reload_career(monkeypatch, SPONSORSCOUT_LIST_BUDGET="600",
                         SPONSORSCOUT_COMPANY_TIME_SEC="2400")
    try:
        assert mod.ProductionScannerConfig.LIST_TIME_BUDGET_SEC == 600
        assert mod.ProductionScannerConfig.MAX_COMPANY_TIME_SEC == 2400
    finally:
        monkeypatch.undo()
        importlib.reload(cs)


def test_budget_floors_hold(monkeypatch):
    mod = _reload_career(monkeypatch, SPONSORSCOUT_LIST_BUDGET="1",
                         SPONSORSCOUT_COMPANY_TIME_SEC="1")
    try:
        assert mod.ProductionScannerConfig.LIST_TIME_BUDGET_SEC == 30
        assert mod.ProductionScannerConfig.MAX_COMPANY_TIME_SEC == 60
    finally:
        monkeypatch.undo()
        importlib.reload(cs)


def test_static_fetch_failure_message_is_not_altered_by_round10():
    # The browser-fallback edit was reverted; the message must be the original.
    src = (REPO / "scanning" / "career" / "career_scanner.py").read_text(encoding="utf-8")
    assert 'f"static: fetch failed ({type(exc).__name__})"' in src
    assert "using browser\"" not in src.split("static: fetch failed")[1][:120]


def test_franchisee_titles_are_not_pure_location():
    scanner = ats.__dict__
    cls = next(o for o in scanner.values()
               if isinstance(o, type) and hasattr(o, "_title_is_pure_location"))
    self = cls.__new__(cls)
    for title in ("Franchisee, Wien, Austria", "Franchisee, Vienna, Austria",
                  "Franchise Partner, Vienna, Austria", "Franchisor, Vienna, Austria"):
        assert self._title_is_pure_location(title) is False, title
    # Positive control: a place-only string is still a location.
    assert self._title_is_pure_location("Vienna, Austria") is True


def test_franchise_words_are_role_nouns_in_both_scanners():
    for word in ("Franchisee", "Franchisor", "Franchise Partner"):
        assert ats._has_role_noun(word), word
    src = (REPO / "scanning" / "career" / "career_scanner.py").read_text(encoding="utf-8")
    assert "franchisee|franchisor|franchise partner" in src


def test_smartrecruiters_moved_to_career_seed():
    def names(path):
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return [r["name"] for r in csv.DictReader(fh)]
    ats_names = names(SEEDS / "company_ATS_seed.csv")
    career_names = names(SEEDS / "company_Career_seed.csv")
    assert "SmartRecruiters" not in ats_names
    assert career_names.count("SmartRecruiters") == 1
    # Positive control: real ATS rows are untouched.
    assert "Smartly.io" in ats_names and "Nexthink" in ats_names


def test_seed_revision_bumped_for_seed_change():
    m = re.search(r'SEED_REVISION = "([^"]+)"', (REPO / "application" / "seed_manager.py").read_text(encoding="utf-8"))
    assert m and m.group(1) == "2026-10-19-r4"
    assert seed_manager.SEED_REVISION == "2026-10-19-r4"
