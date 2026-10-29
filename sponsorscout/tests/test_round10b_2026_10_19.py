"""Round 10, part 2 (2026-10-19): the four items left open after part 1.

1. Unifiedpost (banqup.com, one posting, scanner got 0). The direct-listing
   anchor extractor never ran: its JS had an unescaped "/" inside a regex
   group, so the browser rejected the whole script and the extractor returned
   []. Its visibility check also required a non-zero width, and the posting
   link is a zero-width overlay. Heading-style links were not accepted.
2. LCL (career.lcl.be, one posting). The posting link is a bare slug under an
   <h3>; the card's WordPress data-id produced a 404 URL (/vacancies/jobs/4735).
3. Work in Austria "dispatcher" (job 3798). The slug rescue refused any
   one-word slug, so "Open job" stayed unrescued and was quarantined.
4. Nexthink country display. The career CSV had only Target Country (the
   seed's Germany) next to city-level locations of other countries. It now also
   carries Job Country, derived from the posting's own location, as the
   database does.
"""
import csv
import re
from pathlib import Path

import pytest

from sponsorscout.scanning.career import career_scanner as cs

REPO = Path(__file__).resolve().parents[1]
SRC = (REPO / "scanning" / "career" / "career_scanner.py").read_text(encoding="utf-8")


def _scanner():
    return cs.CareerPortalScanner(input_csv="/dev/null", output_csv="/tmp/_t.csv",
                                  skip_preflight=True)


# ── 1+2: direct-listing anchor JS ────────────────────────────────────────────

def test_anchor_js_has_no_unescaped_slash_inside_regex_group():
    # An unescaped "/" inside (?:...) ends a JS regex literal early.
    start = SRC.index("def _direct_listing_anchor_rows(target):")
    block = SRC[start:SRC.index("def _local_job_signal_re", start)]
    assert "(?:/|$)" not in block
    assert "(?:\\/|$)" in block


def test_anchor_js_parses_in_the_browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        p = sync_api.sync_playwright().start()
        browser = p.chromium.launch(args=["--no-sandbox"])
    except Exception as exc:  # no browser installed on this machine
        pytest.skip(f"chromium unavailable: {exc}")
    html_banqup = """<html><body>
      <h2><a href="/contact-us">Contact us</a></h2>
      <div class="card"><h3><a href="/en/prevention-advisor-environmental-coordinator/"
          aria-label="Prevention Advisor &amp; Environmental Coordinator"
          style="display:block;width:0;height:139px"></a>
          Prevention Advisor &amp; Environmental Coordinator</h3></div>
      <a href="/careers/712631" aria-label="Customer Success Trainee"
         class="peer" style="display:block;width:0;height:139px"></a>
      <a href="/privacy-policy">Privacy</a>
    </body></html>"""
    try:
        page = browser.new_page()
        page.route("https://employer.test/**",
                   lambda route: route.fulfill(status=200, content_type="text/html",
                                               body=html_banqup))
        page.goto("https://employer.test/vacancies/", wait_until="load")
        rows = cs.CareerPortalScanner._direct_listing_anchor_rows(page)
    finally:
        browser.close()
        p.stop()
    urls = {r["job_url"] for r in rows}
    assert "https://employer.test/careers/712631" in urls
    assert "https://employer.test/en/prevention-advisor-environmental-coordinator/" in urls
    assert not any("contact" in u or "privacy" in u for u in urls)  # exclusions kept
    methods = {r["job_url"]: r["extraction_method"] for r in rows}
    assert methods["https://employer.test/careers/712631"] == "direct_listing_anchor"
    assert methods["https://employer.test/en/prevention-advisor-environmental-coordinator/"] \
        == "direct_listing_heading"


def test_heading_slug_link_is_accepted_only_via_heading_method():
    s = _scanner()
    slug = "https://career.lcl.be/en/prevention-advisor-environmental-coordinator"
    assert s.is_valid_job_url(slug) is False            # generic URL rules unchanged
    assert "_heading_link = method == \"direct_listing_heading\"" in SRC


def test_card_resolver_prefers_heading_link_over_data_id():
    # The fallback that turned data-id 4735 into /vacancies/jobs/4735 must come
    # after a real heading link.
    h = SRC.index("const headingA = scope.querySelector")
    d = SRC.index("const dataInfo =")
    assert h < d


# ── 3: one-word slug after a posting id ──────────────────────────────────────

def test_one_word_slug_after_posting_id_is_the_title():
    # The scan calls the class method (the module-level helper is a different one).
    slug = cs.CareerPortalScanner._title_from_url_slug
    assert slug("https://www.workinaustria.com/en/employees/jobs/jobdetail/3798/dispatcher") \
        .lower() == "dispatcher"
    assert slug("https://www.workinaustria.com/en/employees/jobs/jobdetail/5806/franchisee-vienna") \
        == "Franchisee Vienna"


def test_one_word_slug_without_id_is_still_rejected():
    slug = cs.CareerPortalScanner._title_from_url_slug
    assert slug("https://careers.example.com/about") == ""
    assert slug("https://careers.example.com/careers/contact") == ""
    assert slug("https://careers.example.com/jobs/dispatcher") == ""


def test_dispatcher_title_passes_the_validator():
    s = _scanner()
    url = "https://www.workinaustria.com/en/employees/jobs/jobdetail/3798/dispatcher"
    assert s.is_valid_job_title("Dispatcher", "Work in Austria", url)
    assert s.is_valid_job_title("dispatcher", "Work in Austria", url)


# ── 4: Job Country column ────────────────────────────────────────────────────

@pytest.mark.parametrize("loc,expected", [
    ("Nashville", "United States"),
    ("Madrid", "Spain"),
    ("Bengaluru", "India"),
    ("Tokyo", "Japan"),
    ("Unknown", "Unknown"),
    ("Global", "Unknown"),
])
def test_job_country_comes_from_the_posting_location(loc, expected):
    assert cs._job_country_from_record({"Job Location": loc}) == expected


def test_job_country_never_uses_the_seed_target_country():
    rec = {"Job Location": "Unknown", "Raw Location": "", "Target Country": "Germany"}
    assert cs._job_country_from_record(rec) == "Unknown"


def test_job_country_column_is_in_the_output_schema_and_filled():
    assert '"Job Location", "Job Country", "Raw Location"' in SRC
    # Direct/recruiter finalization is now shared, not two copied loops.
    assert SRC.count('_r["Job Country"] = _job_country_from_record(_r)') >= 1
    assert SRC.count('rec["Job Country"] = _job_country_from_record(rec)') >= 1
    assert 'finalize_job_csvs(' in SRC


def test_seed_target_country_is_not_changed_by_this_round():
    with open(REPO / "data" / "seeds" / "company_Career_seed.csv", encoding="utf-8-sig",
              newline="") as fh:
        rows = {r["name"]: r for r in csv.DictReader(fh)}
    assert rows["Nexthink (Germany)"]["target_country"] == "Germany"


# ── franchisee titles still hold (positive controls) ─────────────────────────

def test_franchisee_titles_with_place_still_title_not_location():
    s = _scanner()
    assert s.is_valid_job_title("Franchisee Vienna", "Work in Austria",
                                "https://www.workinaustria.com/en/employees/jobs/jobdetail/5806/franchisee-vienna")
    assert re.search(r"franchisee\|franchisor\|franchise partner", SRC)


# ── Robert Walters Germany: bot-protection block is reported, not silent ─────

class _FakeChallengePage:
    def __init__(self, title, body, html=""):
        self._t, self._b, self._h = title, body, html

    def title(self):
        return self._t

    def inner_text(self, sel):
        return self._b

    def content(self):
        return self._h


RW_BODY = ("Please verify you are a human\n\nAccess to this page has been denied because "
           "we believe you are using automation tools to browse the website.")


def test_bot_challenge_page_is_detected_with_vendor():
    s = _scanner()
    reason = s._bot_challenge_reason(_FakeChallengePage(
        "Access to this page has been denied", RW_BODY,
        '<meta name="description" content="px-captcha">'))
    assert reason.startswith("PerimeterX challenge page")
    assert "no listing could be read" in reason


def test_real_job_list_is_not_flagged_as_challenge():
    s = _scanner()
    page = _FakeChallengePage("Jobs | Robert Walters", "You have 165 job results\nSenior Accountant\nBerlin")
    assert s._bot_challenge_reason(page) == ""


def test_browser_path_raises_on_challenge_instead_of_empty():
    # The seed-load branch must consult the detector before reading cards.
    # 2026-10-28: the wording is decided by listing_block_message(), which only
    # claims a bot-protection wall when the response actually carried challenge
    # evidence. A bare 403 on the listing is reported as an access failure.
    i = SRC.index("_challenge = self._bot_challenge_reason(page)")
    window = SRC[i:i + 300]
    assert "raise RuntimeError(listing_block_message(_challenge))" in window


def test_static_403_is_access_denial_not_proven_bot_protection(monkeypatch):
    import urllib.error
    from sponsorscout.scanning.career import career_scanner as mod

    def boom(*a, **k):
        raise urllib.error.HTTPError("https://www.robertwalters.de/en/jobs.html", 403,
                                     "Forbidden", {}, None)
    monkeypatch.setattr("urllib.request.urlopen", boom)
    jobs, diag = mod.fetch_static_jobs("https://www.robertwalters.de/en/jobs.html",
                                       timeout_sec=3)
    assert jobs == []
    assert "HTTP 403" in diag and "access blocked" in diag
    assert diag.block_reason=="HTTP 403 access blocked (human verification not confirmed)"
    assert "bot protection likely" not in diag


# ── Non-English role titles (Elior Italia, run 20261019T195804) ───────────────

@pytest.mark.parametrize("title", [
    "responsable ordonnancement hf", "second de cuisine hf", "Chef de Partie",
    "Ingénieur Maintenance", "Cuoco Senior", "Addetto Vendite",
])
def test_non_english_role_titles_pass_the_validator(title):
    s = _scanner()
    url = "https://www.eliorgroup.com/fr/carriere/offres-d-emploi/example-posting"
    assert s.is_valid_job_title(title, "Elior Italia", url), title


def test_non_english_role_words_are_in_the_role_pattern():
    for word in ("responsable", "second", "cuisinier", "cuoco", "addetto"):
        assert cs.ProductionScannerConfig.ROLE_WORD_PATTERN.search(word), word
