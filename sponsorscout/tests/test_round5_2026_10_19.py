"""Round 5 (2026-10-19): Elior static list junk and seed scope.

Elior Italia's static list read site navigation ("A job for you at Elior",
"5 reasons to join Elior", the French language switch) as job postings, and
its seed listed every country while the scope said Italy.
"""
import csv
from pathlib import Path

from sponsorscout.scanning.career.career_scanner import _StaticAnchorParser

REPO = Path(__file__).resolve().parents[1]


def _anchors(html):
    p = _StaticAnchorParser()
    p.feed(html)
    return [(href, text) for href, text, _ in p.items]


def test_nav_header_footer_anchors_are_not_job_candidates():
    html = (
        "<header><a href='/fr/carriere/offres-d-emploi'>Français (FR)</a></header>"
        "<nav><a href='/careers/5-reasons-to-join-our-group'>5 reasons to join</a>"
        "<a href='/careers/a-job-for-you-at-elior'>A job for you</a></nav>"
        "<main><a href='/career/job-offers/cook-83'>Cook</a></main>"
        "<footer><a href='/careers/apprenticeship'>Apprenticeship</a></footer>"
    )
    hrefs = [h for h, _ in _anchors(html)]
    assert hrefs == ["/career/job-offers/cook-83"]


def test_nav_region_closes_so_later_main_links_are_kept():
    html = (
        "<nav><ul><li><a href='/x'>menu</a></li></ul></nav>"
        "<div><a href='/career/job-offers/driver-7'>driver</a></div>"
        "<header><span>h</span></header>"
        "<a href='/career/job-offers/cook-83'>Cook</a>"
    )
    hrefs = [h for h, _ in _anchors(html)]
    assert hrefs == ["/career/job-offers/driver-7", "/career/job-offers/cook-83"]


def test_nested_nav_tags_need_all_closes_before_links_resume():
    html = (
        "<nav><header><a href='/menu'>m</a></header>"
        "<a href='/still-nav'>n</a></nav>"
        "<a href='/career/job-offers/ok-1'>ok</a>"
    )
    hrefs = [h for h, _ in _anchors(html)]
    assert hrefs == ["/career/job-offers/ok-1"]


def test_self_closing_anchor_in_nav_is_skipped():
    html = "<nav><a href='/nav-only' /></nav><a href='/career/job-offers/x-1'>x</a>"
    hrefs = [h for h, _ in _anchors(html)]
    assert hrefs == ["/career/job-offers/x-1"]


def _career_seed_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def test_elior_italia_seed_filters_to_italy_in_repo_and_focus_seed():
    checked = 0
    for path in (
        REPO / "data" / "seeds" / "company_Career_seed.csv",
        REPO.parents[1] / "test_seeds" / "test_career_focus.csv",
    ):
        if not path.exists():
            continue
        checked += 1
        rows = [r for r in _career_seed_rows(path)
                if any((v or "").strip() == "Elior Italia" for v in r.values())]
        assert rows, path
        for r in rows:
            url = next(v for v in r.values() if v and "eliorgroup.com" in v)
            assert "field_country_target_id=402" in url, (path, url)
            assert "field_country_target_id=All" not in url
    assert checked >= 1


def test_seed_revision_r3_marks_elior_change():
    from sponsorscout.application import seed_manager as sm
    assert sm.SEED_REVISION == "2026-10-19-r4"  # bumped r3->r4 in round 10; r3 change still applies
