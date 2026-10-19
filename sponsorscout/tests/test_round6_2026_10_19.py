"""Round 6 (2026-10-19): Work in Austria / Elior quarantine and nav evidence.

1. Posting titles whose URL slug equals the title were rejected by the P73
   taxonomy rule: Work in Austria /jobdetail/<id>/<slug> (13 rows quarantined)
   and Elior /job-offers/<slug> ("capo cuoco", "specialista gare e documenti").
2. Browser detail text (body innerText) carried the site menu, so the word
   "Work Permit" from the navigation became Support Evidence on 346 rows.
"""
import re
from pathlib import Path

import pytest

from sponsorscout.scanning.career import career_scanner as cs

SRC = Path(cs.__file__).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def scanner():
    return cs.CareerPortalScanner()


WIA = "https://www.workinaustria.com/en/employees/jobs/jobdetail/{}/{}"


@pytest.mark.parametrize("slug", [
    "accounting-staff", "dispatcher", "materials-handler", "technologist-m-f-d",
])
def test_work_in_austria_posting_titles_survive_taxonomy_rule(scanner, slug):
    title = slug.replace("-", " ")
    assert scanner.is_valid_job_title(title, "Work in Austria", WIA.format(3833, slug))


def test_work_in_austria_open_job_rescues_title_from_slug():
    url = WIA.format(3833, "accounting-staff")
    assert cs._title_from_url_slug(url) == "accounting staff"


def test_elior_posting_titles_survive_taxonomy_rule(scanner):
    for title, slug in [("capo cuoco", "capo-cuoco"),
                        ("specialista gare e documenti", "specialista-gare-e-documenti")]:
        assert scanner.is_valid_job_title(
            title, "Elior Italia", f"https://www.eliorgroup.com/career/job-offers/{slug}")


def test_category_pages_are_still_rejected(scanner):
    # P73 taxonomy must keep working on aggregator category URLs.
    assert not scanner.is_valid_job_title(
        "Visa Sponsorship", "jaabz", "https://www.jaabz.com/jobs/visasponsorship")
    assert not scanner.is_valid_job_title(
        "Blockchain", "jaabz", "https://www.jaabz.com/jobs/blockchain")


def test_posting_parent_set_is_narrow():
    # "jobs" and "job" host category pages elsewhere; they must not be posting parents.
    assert "jobs" not in cs._POSTING_PARENT_SEGS
    assert "job" not in cs._POSTING_PARENT_SEGS
    assert "job-offers" in cs._POSTING_PARENT_SEGS


def test_browser_detail_text_hides_navigation_before_reading_body():
    i = SRC.index("body innerText carried the site menu")
    block = SRC[i:i + 900]
    assert "querySelectorAll('nav,header,footer,aside')" in block
    assert "display='none'" in block
    # Navigation is restored after the text is read, so later reads are unchanged.
    assert "e.style.display=d" in block
    assert block.index("out.desc=") < block.index("e.style.display=d")


def test_titles_starting_with_a_cta_word_keep_their_role(scanner):
    # Proximus: "Email Deliverability Engineer" was quarantined as a CTA.
    u = "https://jobs.proximus.com/be/en/job/req19798/Email-Deliverability-Engineer"
    assert scanner.is_valid_job_title("Email Deliverability Engineer", "Proximus", u)


@pytest.mark.parametrize("label", ["Email us", "Save job", "Apply now", "Open job"])
def test_cta_labels_without_a_role_are_still_rejected(scanner, label):
    assert not scanner.is_valid_job_title(
        label, "Proximus", "https://jobs.proximus.com/be/en/job/req19798/x-y")
