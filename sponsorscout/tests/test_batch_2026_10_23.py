"""Regression suite for the 2026-10-23 7-issue batch:

1. Assisted Ingestion >50-60 pages (max_pages=500, /vacatures/ support, 1-indexed
   data-page self-click guard, sustained probe/cooldown replenishment, card JD save).
2. Regular scan anti-bot hang protection (prompt bot-wall detection & clean skip).
3. Instant scan startup (skip_preflight=True, zero dummy Chromium launch, fast seed stamp).
4. Decoupled JD Insights & single consolidated Refresh Insights button + clean skill/domain/category taxonomy.
5. Lightweight scan JD extraction + non-destructive SQLite full-JD persistence.
6. Hardware-aware CPU/RAM scaling (strict <=4c/<=8GB vs desktop auto-boost + parallel ATS pool).
"""

from __future__ import annotations

import csv
import inspect
from pathlib import Path

import pytest

from sponsorscout.application import seed_manager
from sponsorscout.core import blocked_portals as bp
from sponsorscout.core import persistence
from sponsorscout.db import database
from sponsorscout.insights import queries, terms
from sponsorscout.scanning import common, pipeline
from sponsorscout.scanning.ats import ats_scanner as ats_mod
from sponsorscout.scanning.career import career_scanner as career_mod


REPO = Path(__file__).resolve().parents[1]


# ── Issue 1: Assisted Ingestion >50-60 pages ────────────────────────────────
def test_assisted_ingestion_defaults_to_500_pages_and_dutch_plural_vacatures():
    sig = inspect.signature(bp.run_assisted_ingestion)
    assert sig.parameters["max_pages"].default == 500

    ui_src = (REPO / "ui" / "tabs" / "assisted_ingestion.py").read_text(encoding="utf-8")
    assert "max_pages: int = 500" in ui_src
    assert "max_pages=500" in ui_src
    # Actual queued targets replaced the hardcoded example portal list.
    assert "https://www.robertwalters.nl/vacatures.html" not in ui_src
    assert 'db.list_blocked_portals' in ui_src

    # Universal multilingual plural job-detail paths (/vacatures/, /stellenangebote/, etc.)
    bp_src = Path(bp.__file__).read_text(encoding="utf-8")
    assert "/vacatures/" in bp_src
    assert "robertwalters" not in bp_src.lower()


def test_assisted_next_page_url_and_1_indexed_data_page_guard():
    # URL pagination parameters used across large European portals
    assert bp.next_page_url("https://portal.example/vacatures.html?page=59", 59) == (
        "https://portal.example/vacatures.html?page=60"
    )
    assert bp.next_page_url("https://portal.example/vacatures.html?pagina=50", 50) == (
        "https://portal.example/vacatures.html?pagina=51"
    )

    # Guard against 1-indexed data-page self-click on page 50 -> 51 boundary
    bp_src = Path(bp.__file__).read_text(encoding="utf-8")
    assert "zeroIndexedDataPage" in bp_src
    assert "has_zero_dp" in bp_src
    assert "fresh_streak >= 10" in bp_src


# ── Issue 2: Anti-bot prompt detection without hanging ──────────────────────
class _FakeResponse:
    def __init__(self, status: int):
        self.status = status


class _FakePage:
    def __init__(self, title: str, html: str, body: str = "", url: str = "https://careers.example/jobs", status: int = 200):
        self._title = title
        self._html = html
        self._body = body or html
        self.url = url
        self._last_nav_status = status

    def title(self):
        return self._title

    def inner_text(self, _sel: str = "body"):
        return self._body

    def content(self):
        return self._html


def test_bot_challenge_reason_catches_antibot_walls_and_ignores_real_listings():
    scanner = career_mod.CareerPortalScanner(
        input_csv=str(seed_manager.bundled_career_path()),
        output_csv="/tmp/unused_career_out.csv",
    )

    # Bare HTTP access denial is not proof of a human-verification challenge.
    p_403 = _FakePage(
        "Access Denied",
        "<html><body><h1>403 Forbidden</h1><p>Automated requests blocked by WAF.</p></body></html>",
        status=403,
    )
    reason = scanner._bot_challenge_reason(p_403)
    assert "HTTP 403 access blocked" in reason and "human verification not confirmed" in reason

    # GeeTest / FriendlyCaptcha challenge with no job links -> prompt bot-block verdict
    p_captcha = _FakePage(
        "Security Check",
        "<html><body><div class='frc-captcha' data-sitekey='xyz'>Verify you are human</div></body></html>",
    )
    assert "challenge page served instead of the job list" in scanner._bot_challenge_reason(p_captcha)

    # Real job board that happens to load Cloudflare analytics/scripts -> NOT blocked
    links = "".join(f'<a href="/jobs/engineer-{i}">Software Engineer {i}</a>' for i in range(6))
    p_real = _FakePage(
        "Careers - Example Corp",
        f"<html><head><script src='https://challenges.cloudflare.com/turnstile/v0/api.js'></script></head><body>{links}</body></html>",
        body="Showing 6 open positions: Software Engineer 1, Software Engineer 2 — Apply now",
    )
    assert scanner._bot_challenge_reason(p_real) == ""


# ── Issue 3: Instant scan startup (no preflight / sniffing / test browser) ──
def test_scanners_default_skip_preflight_and_pipeline_skips_browser_launch():
    ats_sig = inspect.signature(ats_mod.ATSScanner.__init__)
    car_sig = inspect.signature(career_mod.CareerPortalScanner.__init__)
    assert ats_sig.parameters["skip_preflight"].default is True
    assert car_sig.parameters["skip_preflight"].default is True

    pipe_src = Path(pipeline.__file__).read_text(encoding="utf-8")
    assert "_ensure_playwright_browsers" not in pipe_src
    assert "_playwright_import_error" in pipe_src


def test_seed_manager_stamp_short_circuits_unchanged_seeds(tmp_path, monkeypatch):
    monkeypatch.setattr(seed_manager, "SEEDS_DIR", tmp_path)
    first = seed_manager.merge_bundled_seeds()
    marker = tmp_path / ".seed_merged_revision"
    assert marker.exists()

    # Second call short-circuits without re-reading CSVs when stamp matches
    called = []
    orig_read = seed_manager.read_seed_rows
    monkeypatch.setattr(seed_manager, "read_seed_rows", lambda *a, **k: called.append(1) or orig_read(*a, **k))
    second = seed_manager.merge_bundled_seeds()
    assert second == {"ats": 0, "career": 0}
    assert called == []


# ── Issue 4 & 5: Lightweight scan JD storage + decoupled SQLite Insights ────
def test_skills_dictionary_purged_of_targeting_noise_and_false_positives():
    skill_path = REPO / "insights" / "skills_dictionary.csv"
    with open(skill_path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    groups = {r["group"] for r in rows}
    assert "Location Targeting" not in groups
    assert "Job Portal / Source Targeting" not in groups
    assert "Work Arrangement & Mobility" not in groups

    # Generic recruitment prose must not trigger Go or Swift false positives
    prose = "Go to our portal for a swift hiring process in Amsterdam, Netherlands on LinkedIn Jobs."
    extracted = terms.extract_skills(prose)
    assert "Go / Golang" not in extracted
    assert "Swift" not in extracted
    assert not any(s.startswith("Location ") or s.startswith("Job portal:") for s in extracted)

    # Real Go + Swift + AWS skills match once each (no duplicate AWS / Amazon Web Services)
    real_jd = "Backend & Mobile role using Golang, SwiftUI, AWS, Kubernetes, PostgreSQL, and Docker."
    real_skills = terms.extract_skills(real_jd)
    assert "Go / Golang" in real_skills
    assert "Swift" in real_skills
    assert "AWS" in real_skills
    assert "Amazon Web Services" not in real_skills


def test_weighted_category_and_full_jd_domain_classification():
    # Specialized engineering titles are no longer shadowed by generic "engineer"
    assert terms.classify_category("Senior Cloud & DevOps Engineer") == "Cloud & DevOps"
    assert terms.classify_category("Staff Data Engineer") == "Data & Analytics"
    assert terms.classify_category("Lead Cybersecurity Engineer") == "Cybersecurity"
    assert terms.classify_category("QA Test Automation Engineer") == "Quality Assurance & Testing"
    assert terms.classify_category("Senior Python Software Engineer") == "Engineering & IT"

    # Short token "IT" no longer false-matches substring "it" inside "Hospitality"
    assert terms.classify_domain("IT") != "Travel, Hospitality & Food"

    # Missing seed industry falls back to full-JD domain cues when JD text is provided
    dom = terms.classify_domain(
        "",
        title="Backend Developer",
        text="Join our regulated fintech and open banking payment processing platform supporting SEPA and PSD2.",
    )
    assert dom == "Financial Services & Fintech"


def test_lightweight_scan_saves_full_jd_and_decoupled_insights_backfills(tmp_path):
    db_file = str(tmp_path / "scan_jd.db")
    database.initialize(db_file)

    full_jd = (
        "We are a B2B SaaS cloud-native platform hiring a Specialist to build "
        "distributed backend services in Python, FastAPI, PostgreSQL, Docker, and Kubernetes "
        "with strong Stakeholder Management and Mentoring. Visa sponsorship and relocation provided."
    )
    raw_row = {
        "Canonical Job ID": "job-101",
        "Job Title": "Platform Specialist",
        "Hiring Company": "CloudNova",
        "Company Name": "CloudNova",
        "Seed Name": "CloudNova",
        "Job Location": "Amsterdam, Netherlands",
        "Job URL": "https://careers.cloudnova.example/jobs/101",
        "Provider": "greenhouse",
        "Visa Sponsorship": "Yes",
        "Relocation Support": "Yes",
        "EU Blue Card": "Unknown",
        "Support Confidence": "0.9",
        "Support Evidence": "Visa sponsorship and relocation provided.",
        "Experience Level": "Senior",
    }

    # 1. During scan mapping: NO inline skill/term extraction runs, but full JD is attached via jd_lookup
    mapped = pipeline._row_to_job(raw_row, run_id="RUN1", jd_lookup=lambda _u: full_jd)
    assert mapped is not None
    assert "terms" not in mapped
    assert mapped["description"] == full_jd
    assert mapped["category"] == "Other"

    with database.get_connection(db_file) as conn:
        persistence.upsert_job(conn, mapped, commit=True)
        # Re-upserting with empty description must NOT erase the stored full JD
        shorter = dict(mapped, description="")
        persistence.upsert_job(conn, shorter, commit=True)
        stored_desc = conn.execute("SELECT description FROM jobs WHERE url=?", (mapped["url"],)).fetchone()[0]
        assert stored_desc == full_jd

    # Before Insights refresh, 0 skill rows exist because scan stayed lightweight
    cov_before = queries.get_insights_coverage(db_file)
    assert cov_before["total_jobs"] == 1
    assert cov_before["jobs_with_skills"] == 0

    # 2. Separate Insights extraction reads full JD from SQLite and populates category, domain & skills
    updated = queries.backfill_existing_job_terms(db_file, force_all=False)
    assert updated == 1

    cov_after = queries.get_insights_coverage(db_file)
    assert cov_after["jobs_with_skills"] == 1
    seg = queries.get_segregated_skills(db_file)
    tech_names = [t for t, _ in seg["technical"]]
    soft_names = [t for t, _ in seg["soft"]]
    assert "Python" in tech_names
    assert "PostgreSQL" in tech_names
    assert "Kubernetes" in tech_names
    assert any(s in soft_names for s in ("Stakeholder Management", "Stakeholder management", "Mentoring"))


def test_insights_tab_consolidates_into_single_refresh_button(tmp_path):
    insights_src = (REPO / "ui" / "tabs" / "insights.py").read_text(encoding="utf-8")
    assert "Re-Analyse Database Skills" not in insights_src
    assert 'QPushButton(_("Refresh Insights"))' in insights_src
    assert "def refresh_from_sqlite" in insights_src


# ── Issue 6: Hardware-aware CPU & RAM scaling ───────────────────────────────
def test_hardware_scaling_strict_low_spec_vs_capable_desktop_autoboost(monkeypatch):
    gib = 1024 ** 3
    monkeypatch.delenv("SPONSORSCOUT_MAX_WORKERS", raising=False)
    monkeypatch.delenv("SPONSORSCOUT_HTTP_WORKERS", raising=False)
    common.set_high_performance(False)

    # Dual-core / 8 GB laptop -> strict conservative caps
    monkeypatch.setattr(common, "host_workers_limits", lambda: (2, 8 * gib, 4 * gib))
    assert common.is_strict_low_spec() is True
    assert common.recommended_workers("browser") == 1
    assert common.recommended_workers("company") == 2
    assert common.recommended_workers("http") <= 3

    # Quad-core / 8 GB laptop -> strict conservative caps
    monkeypatch.setattr(common, "host_workers_limits", lambda: (4, 8 * gib, 5 * gib))
    assert common.is_strict_low_spec() is True
    assert common.recommended_workers("browser") <= 2
    assert common.recommended_workers("company") <= 4

    # 12-core / 32 GB desktop (24 GB free) -> automatic multi-thread & parallel Chrome boost
    monkeypatch.setattr(common, "host_workers_limits", lambda: (12, 32 * gib, 24 * gib))
    assert common.is_strict_low_spec() is False
    b_norm = common.recommended_workers("browser")
    c_norm = common.recommended_workers("company")
    h_norm = common.recommended_workers("http")
    assert 3 <= b_norm <= 6
    assert 12 <= c_norm <= 16
    assert 16 <= h_norm <= 24

    # High-Performance (Turbo) mode boosts ceilings further while keeping OS headroom
    common.set_high_performance(True)
    try:
        assert common.recommended_workers("browser") >= b_norm
        assert common.recommended_workers("company") >= c_norm
        assert common.recommended_workers("http") >= h_norm
    finally:
        common.set_high_performance(False)


# ── Follow-up: direct scan startup, detail stalls and assisted false blocks ──
@pytest.mark.parametrize("kind", ["ats", "career"])
def test_seed_read_filters_first_and_never_upgrades_or_audits(kind, tmp_path, monkeypatch, capsys):
    path = tmp_path / "seed.csv"
    cols = ["name", "careers_url", "provider", "scope_policy", "sponsorship_history", "enabled"]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        writer.writerows([
            {"name": "# group"},
            {"name": "Unselected broken row", "careers_url": "not a URL", "scope_policy": "bad"},
            {"name": "Chosen", "careers_url": "https://careers.example/original", "provider": "auto",
             "sponsorship_history": "not-a-number"},
            {"name": "Disabled", "careers_url": "https://disabled.example/jobs", "enabled": "false"},
        ])
    module = ats_mod if kind == "ats" else career_mod
    monkeypatch.setattr(module, "SEED_UPGRADE", {"Chosen": {"careers_url": "https://wrong.example/rewritten"}})
    scanner = (module.ATSScanner(seed_file=str(path), only_companies=["Chosen"])
               if kind == "ats" else module.CareerPortalScanner(input_csv=str(path), only_companies=["Chosen"]))
    monkeypatch.setattr(scanner, "_audit_seed_contradictions", lambda *_: pytest.fail("no seed audit during a scan"))
    rows = scanner.read_seed_file()
    assert len(rows) == 1
    assert rows[0].get("url", rows[0].get("careers_url")) == "https://careers.example/original"
    assert rows[0]["sponsorship_history"] is None
    assert "Unselected broken row" in scanner._other_company_names("Chosen")
    assert "[upgrade]" not in capsys.readouterr().out
    scanner.only_companies = []
    assert scanner.read_seed_file() == []  # explicit empty selection never scans everything


def test_career_constructor_does_not_sweep_the_seed(tmp_path, monkeypatch):
    import builtins
    path = tmp_path / "seed.csv"
    path.write_text("name,careers_url\nA,https://a.example/jobs\n", encoding="utf-8-sig")
    real_open = builtins.open
    reads = []

    def observed_open(file, *args, **kwargs):
        if str(file) == str(path):
            reads.append(file)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", observed_open)
    scanner = career_mod.CareerPortalScanner(input_csv=str(path))
    assert reads == []
    scanner.read_seed_file()
    scanner._other_company_names("A")
    assert len(reads) == 1


def test_pipeline_scan_button_never_merges_or_validates_the_seed(tmp_path, monkeypatch):
    import threading
    from sponsorscout import paths
    ats = tmp_path / "ats.csv"
    career = tmp_path / "career.csv"
    ats.write_text("name,careers_url\nUnwanted,invalid\n", encoding="utf-8-sig")
    career.write_text("name,careers_url\nChosen,https://a.example/jobs\nBroken,invalid\n", encoding="utf-8-sig")
    monkeypatch.setattr(seed_manager, "user_ats_path", lambda: ats)
    monkeypatch.setattr(seed_manager, "user_career_path", lambda: career)
    monkeypatch.setattr(seed_manager, "merge_bundled_seeds", lambda **kw: pytest.fail("no seed merge on Scan"))
    output = tmp_path / "scan_outputs"
    output.mkdir()
    monkeypatch.setattr(paths, "ensure_scan_output_dir", lambda: output)
    monkeypatch.setattr(pipeline, "lower_process_priority", lambda: True)
    seen = []

    class Career:
        def __init__(self, **kw):
            seen.append(kw)
        def execute_crawler(self):
            return None

    monkeypatch.setattr(pipeline.career_module, "CareerPortalScanner", Career)
    monkeypatch.setattr(pipeline.ats_module, "ATSScanner", lambda **kw: pytest.fail("disabled phase"))
    summary = pipeline.run_scan(db_path=str(tmp_path / "jobs.db"), run_ats=False,
                                only_career=["Chosen"], cancel_event=threading.Event())
    assert summary["status"] == "completed"
    assert seen[0]["only_companies"] == ["Chosen"]
    assert seen[0]["skip_preflight"] is True
    seen.clear()
    pipeline.run_scan(db_path=str(tmp_path / "empty.db"), run_ats=False, only_career=[])
    assert seen == []


def _no_disk_jd_cache(monkeypatch):
    class Cache:
        def get(self, url):
            return None
        def put(self, url, value):
            pass
    monkeypatch.setattr(career_mod, "_jd_cache", lambda: Cache())


def test_http_detail_call_never_launches_a_browser(monkeypatch):
    scanner = career_mod.CareerPortalScanner()
    _no_disk_jd_cache(monkeypatch)
    monkeypatch.setattr(scanner, "_fetch_jd_text_uncached", lambda u: ("JS shell", "", ""))
    monkeypatch.setattr(scanner, "_fetch_jd_text_browser", lambda *a, **kw: pytest.fail("HTTP-only must stay HTTP-only"))
    assert scanner._fetch_jd_text("https://example.test/jobs/1", allow_browser=False) == ("JS shell", "", "")


def test_api_jd_is_reused_even_when_sponsorship_is_unknown(monkeypatch):
    scanner = career_mod.CareerPortalScanner(detail_scan=True)
    url = "https://example.test/jobs/1"
    jd = "You will build distributed services and work with product teams. " * 10
    scanner.store_captured_jd(url, jd)
    monkeypatch.setattr(scanner, "_fetch_jd_text_uncached", lambda *a: pytest.fail("API JD must not be fetched twice"))
    monkeypatch.setattr(scanner, "_fetch_jd_text_browser", lambda *a: pytest.fail("no cold Chromium for an API JD"))
    assert scanner._fetch_jd_text(url)[0] == jd.strip()
    monkeypatch.setattr(scanner, "_enrich_support_from_detail", lambda *a, **kw: pytest.fail("complete JD is already enriched"))
    scanner._run_detail_pipeline(None, "Company", {"id": {
        "Job URL": url, "Visa Sponsorship": "Unknown", "_jd_complete": True,
    }})


def test_detail_http_403_opens_breaker_after_two_requests(monkeypatch):
    import urllib.error
    scanner = career_mod.CareerPortalScanner()
    _no_disk_jd_cache(monkeypatch)
    requests = []

    def forbidden(req, **kw):
        requests.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(career_mod.urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(scanner, "_fetch_jd_text_browser", lambda *a, **kw: pytest.fail("never browser-retry a blocked HTTP URL"))
    rows = [{"Job URL": f"https://blocked.example/jobs/{i}", "Job Title": f"Engineer {i}",
             "Visa Sponsorship": "Unknown", "Relocation Support": "Unknown"} for i in range(30)]
    scanner._enrich_support_from_detail(rows, max_workers=4, use_browser=True)
    assert len(requests) == 2
    assert all(r["Visa Sponsorship"] == "Unknown" for r in rows)
    assert not any(scanner.get_captured_jd(r["Job URL"]) for r in rows)


def test_browser_detail_403_is_bounded_and_not_treated_as_a_jd():
    scanner = career_mod.CareerPortalScanner(detail_scan=True)
    class Page:
        def __init__(self):
            self.urls = []
        def goto(self, url, **kw):
            self.urls.append(url)
            return _FakeResponse(403)
        def wait_for_timeout(self, ms):
            pass
    page = Page()
    rows = {str(i): {"Job URL": f"https://blocked.example/jobs/{i}", "Visa Sponsorship": "Unknown"}
            for i in range(20)}
    scanner._detail_scan_for_company(page, "Company", rows)
    assert len(page.urls) == 2
    assert all(r["Visa Sponsorship"] == "Unknown" for r in rows.values())


def test_full_html_and_child_frames_reveal_challenges_beyond_large_heads():
    from types import SimpleNamespace
    scanner = career_mod.CareerPortalScanner()
    html = "<head><style>" + "x" * 140000 + "</style></head><body><div id='px-captcha'></div></body>"
    page = _FakePage("Careers", html, body="Please verify you are a human")
    assert scanner._bot_challenge_reason(page).startswith("PerimeterX challenge page")
    assert career_mod._raw_html_is_bot_challenge(html)
    frame_page = _FakePage("Careers", "<body></body>", body=" ")
    frame_page.frames = [SimpleNamespace(url="https://geo.captcha-delivery.com/captcha/?id=x")]
    assert scanner._bot_challenge_reason(frame_page).startswith("DataDome challenge page")


@pytest.mark.parametrize("sdk", ["perimeterx px-captcha", "turnstile challenges.cloudflare.com", "_Incapsula_Resource"])
def test_assisted_passive_sdk_is_not_a_mid_run_challenge(sdk):
    cards = "".join(f"<a href='/vacatures/engineer-{i}.html'>Engineer {i}</a>" for i in range(10))
    html = f"<head><script>{sdk}</script></head><body><p>We hebben <b>602</b> vacatures gevonden</p>{cards}</body>"
    page = _FakePage("Vacatures", html)
    assert bp._page_is_challenge(page) is False
    assert bp._page_has_listing(page) is True
    assert career_mod._raw_html_is_bot_challenge(html) is False
    # Even on a shell, a passive SDK alone is not evidence of a challenge.
    assert bp._page_is_challenge(_FakePage("Careers", f"<script>{sdk}</script><body>Loading...</body>")) is False


def test_real_assisted_challenge_prompts_and_does_not_reload_cleared_listing(monkeypatch):
    page = _FakePage("Access denied", "<div id='px-captcha'>Press &amp; Hold</div>")
    page.goto = lambda *a, **kw: pytest.fail("must not reload an already-cleared listing URL")
    foreground = []
    page.bring_to_front = lambda: foreground.append(True)
    monkeypatch.setattr(bp, "_wait_for_clearance", lambda *a: True)
    messages = []
    assert bp._recover_blank_page(page, page.url, 43, progress_cb=messages.append) == "cleared"
    assert foreground == [True]
    assert "Action needed" in messages[0]


def test_paging_owns_its_paths_and_rejects_cross_origin_or_blog_links():
    class Page:
        def eval_on_selector_all(self, *a):
            return [
                {"h": "/careers/page/60", "rel": "next"},
                {"h": "/careers/page/61", "rel": ""},
                {"h": "/blog/page/90", "rel": ""},
                {"h": "https://unrelated.example/page/99", "rel": "next"},
            ]
    assert bp.paging_href_candidates(Page(), "https://board.example/careers/page/59", set()) == [
        "https://board.example/careers/page/60", "https://board.example/careers/page/61",
    ]
    assert bp.next_page_url("https://board.example/careers/page/59", 59) == "https://board.example/careers/page/60"
    assert bp.next_page_url("https://board.example/jobs?P=59", 59) == "https://board.example/jobs?P=60"
    assert bp.next_page_url("https://board.example/jobs?offset=590", 59) is None


def test_card_cta_uses_heading_and_save_link_is_never_selected():
    scanner = career_mod.CareerPortalScanner()
    class Element:
        def __init__(self, text, href=""):
            self.text, self.href = text, href
        def inner_text(self):
            return self.text
        def get_attribute(self, key):
            return self.href if key == "href" else None
    save = Element("Vacature opslaan", "/vacatures/software-engineer/save-job")
    detail = Element("Vacature bekijken", "/vacatures/software-engineer-123.html")
    heading = Element("Senior Software Engineer")
    class Card:
        def query_selector_all(self, selector):
            return [heading] if selector.startswith("h1,") else [save, detail]
    card = Card()
    assert bp._card_detail_anchor(card) is detail
    assert bp._card_title(card, "Vacature bekijken", detail.href, scanner) == "Senior Software Engineer"


def test_ajax_wait_does_not_accept_changed_url_with_stale_cards(monkeypatch):
    from types import SimpleNamespace
    clock = [0.0]
    monkeypatch.setattr(bp, "time", SimpleNamespace(
        monotonic=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s)))
    class Page:
        url = "https://board.example/jobs?page=27"
        reads = 0
        def evaluate(self, script):
            self.reads += 1
            return "old" if self.reads < 4 else "fresh"
        def is_closed(self):
            return False
        def wait_for_load_state(self, *a, **kw):
            pass
    page = Page()
    assert bp._wait_for_page_advance(page, "https://board.example/jobs?page=26", "old")
    assert page.reads == 4
    assert clock[0] >= 3.0  # poll for the real swap, then keep the polite dwell


def test_company_start_changes_activity_without_inflating_percent():
    got = []
    wrapped = pipeline._ScanProgress(0, 1).wrap(got.append)
    wrapped("[1/1] Example -> Example [direct_employer, Global]")
    ticks = [line for line in got if line.startswith("PROGRESS:")]
    assert ticks == ["PROGRESS: 0/1:career:Scanning Example -> Example [direct_employer, Global]"]
    wrapped("OK Example: wrote=1")
    assert got[-1] == "PROGRESS: 1/1:career:Career 1/1"


def test_short_tail_of_progress_flushes_while_scan_is_blocking(tmp_path, monkeypatch):
    import threading
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from sponsorscout.application.scan_coordinator import ScanCoordinator
    started = threading.Event()
    released = threading.Event()
    delivered = threading.Event()
    messages = []
    def fake_run(**kw):
        kw["progress"]("ordinary short line")
        started.set()
        released.wait(3)
        return {"status": "completed"}
    monkeypatch.setattr(pipeline, "run_scan", fake_run)
    coordinator = ScanCoordinator(str(tmp_path / "jobs.db"))
    def receive(message):
        messages.append(message)
        delivered.set()
    coordinator.progress.connect(receive, Qt.ConnectionType.DirectConnection)
    try:
        assert coordinator.start()
        assert started.wait(2)
        assert delivered.wait(0.8), "a short startup tail remained buffered behind the blocked crawler"
        assert messages == ["ordinary short line"]
    finally:
        released.set()
        coordinator._thread.join(timeout=3)
    assert not coordinator.is_running()


@pytest.fixture()
def local_chromium():
    """Opt-in offline browser regressions; never part of a desktop Scan run.

    Set SPONSORSCOUT_TEST_BROWSER=1 with an installed Playwright Chromium.
    All pages are set_content/local HTTP; customer portals are never contacted.
    """
    import os
    if os.environ.get("SPONSORSCOUT_TEST_BROWSER") != "1":
        pytest.skip("opt-in local Chromium regression")
    pw_api = pytest.importorskip("playwright.sync_api")
    with pw_api.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            yield browser
        finally:
            browser.close()


def test_pagination_javascript_scopes_numbers_and_generates_trusted_input(local_chromium, monkeypatch):
    from types import SimpleNamespace
    import time as real_time
    monkeypatch.setattr(bp, "time", SimpleNamespace(monotonic=real_time.monotonic,
                                                    sleep=lambda s: real_time.sleep(min(s, 0.002))))
    page = local_chromium.new_page()
    try:
        page.set_content("""<aside><a id='facet' href='?facet=12'>12</a></aside>
            <nav class='pagination'><li class='active'><button data-page='11'>11</button></li>
              <li><button id='p12' data-page='12'>12</button></li></nav>
            <script>window.events=[]; document.addEventListener('click', e => window.events.push([e.target.id,e.isTrusted]));</script>""")
        assert page.evaluate(bp._PAGINATION_TARGET_JS, 12) is True
        assert page.locator("[data-ss-next]").get_attribute("id") == "p12"
        assert bp._trusted_click_tagged(page)
        assert page.evaluate("window.events") == [["p12", True]]

        page.set_content("""<nav class='pagination'><li class='active'><button data-page='49'>50</button></li>
            <li><button id='p51' data-page='50'>51</button></li><li><button data-page='51'>52</button></li></nav>""")
        assert page.evaluate(bp._PAGINATION_TARGET_JS, 51) is True
        assert page.locator("[data-ss-next]").get_attribute("id") == "p51"

        page.set_content("""<div class='pagination'><ul><li><button>1</button></li>
            <li class='active'><button data-page='10'>10</button></li></ul>
            <button id='chevron'><svg width='10' height='10'><path d='M1 1L8 5L1 9'/></svg></button></div>""")
        assert page.evaluate(bp._PAGINATION_TARGET_JS, 11) is True
        assert page.locator("[data-ss-next]").get_attribute("id") == "chevron"

        page.set_content("""<nav class='pagination'><li class='active'><button>61</button></li>
            <li class='disabled'><button aria-label='Volgende' disabled>›</button></li></nav>""")
        assert bp._pagination_end_reached(page)
        assert page.evaluate(bp._PAGINATION_TARGET_JS, 62) is False
    finally:
        page.close()


def test_assisted_harvest_reads_all_61_pages_despite_a_reserve_and_human_challenge(
        local_chromium, tmp_path, monkeypatch):
    import contextlib
    import http.server
    import threading
    import time as real_time
    from collections import Counter
    from types import SimpleNamespace
    from urllib.parse import parse_qs, urlparse
    from sponsorscout import paths
    import playwright.sync_api as pw_api

    visits = Counter()
    class Board(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            if urlparse(self.path).path.startswith('/vacatures/software-engineer-'):
                import json
                payload={'@type':'JobPosting','url':'http://'+self.headers['Host']+self.path,'title':'Software Engineer','description':('Responsibilities: Build Python services, maintain tests and collaborate with the team. '*15),'jobLocation':{'address':{'addressCountry':'NL'}}}
                body=('<title>Software Engineer</title><script type="application/ld+json">'+json.dumps(payload)+'</script>').encode()
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
            query = parse_qs(urlparse(self.path).query)
            number = int(query.get("page", ["1"])[0])
            visits[number] += 1
            if number == 43 and visits[number] == 1:
                document = """<title>Human verification</title><div id='px-captcha'>
                    Please verify you are a human<button id='human' onclick='location.reload()'>Human clearance</button></div>"""
            else:
                # One transient stale-card response at the exact reported boundary.
                card_page = 26 if number == 27 and visits[number] == 1 else number
                first = (card_page - 1) * 10 + 1
                last = min(card_page * 10, 602)
                cards = "".join(f"""<article class='job-card'><a href='/vacatures/save-job/{i}'>Vacature opslaan</a>
                    <h2>Senior Software Engineer {i}</h2><p>Location: Amsterdam, Netherlands</p>
                    <p>Build Python services and maintain distributed systems.</p>
                    <a href='/vacatures/software-engineer-{i}.html'>Vacature bekijken</a></article>"""
                    for i in range(first, last + 1))
                low = (number - 1) // 10 * 10 + 1
                links = "".join(f"<li class='{'active' if n == number else ''}'><a data-page='{n}' href='?page={n}'>{n}</a></li>"
                                for n in range(low, min(low + 10, 62)))
                next_button = ("<button aria-label='Volgende' disabled>›</button>" if number >= 61 else
                               f"<button aria-label='Volgende' onclick=\"location.href='?page={number + 1}'\"><svg width='10' height='10'><path d='M1 1L8 5L1 9'/></svg></button>")
                document = f"""<title>Vacatures</title><script>window.securitySDK='perimeterx px-captcha turnstile';</script>
                    <p>We hebben <b>602</b> vacatures gevonden</p>{cards}
                    <nav class='pagination'><ul>{links}</ul>{next_button}</nav>"""
            body = ("<!doctype html><html><head><meta charset='utf-8'></head><body>" + document + "</body></html>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Board)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    pages = []
    waits = []
    original_context = local_chromium.new_context
    class BrowserProxy:
        def new_context(self, **kwargs):
            ctx = original_context(**kwargs)
            original_page = ctx.new_page
            def new_page():
                page = original_page()
                original_wait = page.wait_for_timeout
                def quick_wait(ms):
                    waits.append(ms)
                    original_wait(min(ms, 5))
                page.wait_for_timeout = quick_wait
                pages.append(page)
                return page
            ctx.new_page = new_page
            return ctx
        def close(self):
            pass  # local_chromium fixture owns the real browser lifecycle
    proxy = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw: BrowserProxy()))
    monkeypatch.setattr(pw_api, "sync_playwright", lambda: contextlib.nullcontext(proxy))
    # Disable production dwell ONLY for this local fixture; production remains
    # deliberately polite. Real monotonic time is retained for AJAX deadlines.
    monkeypatch.setattr(bp, "time", SimpleNamespace(monotonic=real_time.monotonic,
                                                    sleep=lambda s: real_time.sleep(min(s, 0.002))))
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(paths, "ensure_scan_output_dir", lambda: outputs)
    db_file = str(tmp_path / "synthetic.db")
    database.initialize(db_file)
    messages = []
    from sponsorscout.core import session_details
    monkeypatch.setattr(session_details, 'time', SimpleNamespace(monotonic=real_time.monotonic,sleep=lambda s: real_time.sleep(min(s,0.002))))
    def progress(message):
        messages.append(message)
        if "Action needed" in message:
            # A synthetic human action on our own fixture, not automatic
            # solving/bypassing of a third-party challenge.
            pages[0].locator("#human").click()
    try:
        with database.get_connection(db_file) as conn:
            count = bp.run_assisted_ingestion(
                f"http://127.0.0.1:{server.server_port}/vacatures.html?page=1", "Local Board", conn,
                db_path=db_file, progress_callback=progress, clear_wait_s=3.0)
            assert count == 602
            assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 602
            assert conn.execute("SELECT COUNT(*) FROM jobs WHERE title LIKE '%bekijken%' OR url LIKE '%save-job%'").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM jobs WHERE description IS NOT NULL AND description <> ''").fetchone()[0] == 602
        assert bp.LAST_INGESTION_REPORT["pages_visited"] == 61
        assert bp.LAST_INGESTION_REPORT["harvested"] == 602
        assert "last page reached" in bp.LAST_INGESTION_REPORT["stop_reason"]
        assert visits[27] == 2  # retry the denied page; never skip it or wait 180 s
        assert visits[43] == 2  # synthetic human clears it; no redundant reload over it
        assert any("Action needed" in m for m in messages)
        assert any("page 61" in m for m in messages)
        assert sum(ms >= 6000 for ms in waits) == 3  # breathers on pages 20, 40, 60
        for artifact in outputs.glob("*.csv"):
            assert artifact.read_bytes().startswith(b"\xef\xbb\xbf")
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)


def test_api_security_denial_is_not_retried_before_real_crawl_fallback(monkeypatch):
    import urllib.error
    scanner = career_mod.CareerPortalScanner()
    calls = []
    def forbidden(req, **kw):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
    monkeypatch.setattr(career_mod.urllib.request, "urlopen", forbidden)
    with pytest.raises(urllib.error.HTTPError):
        scanner._http_fetch_with_retry("https://api.example.test/list", {})
    assert len(calls) == 1


def test_live_ingester_can_stop_and_join_without_shadowing_thread_internals(tmp_path):
    ingester = pipeline._LiveIngester(str(tmp_path / "jobs.db"), "test", [], set(), interval=0.01)
    ingester.start()
    ingester.stop()
    ingester.join(timeout=1)
    assert not ingester.is_alive()


def test_regular_api_scan_writes_full_jds_without_preflight_or_refetch(tmp_path, monkeypatch):
    seed = tmp_path / "seed.csv"
    seed.write_text("name,careers_url,provider,board_slug\nExample,https://boards.greenhouse.io/example,greenhouse,example\n",
                    encoding="utf-8-sig")
    output = tmp_path / "career_jobs.csv"
    scanner = career_mod.CareerPortalScanner(input_csv=str(seed), output_csv=str(output), detail_scan=True)
    jd = "Develop distributed systems with a product team and maintain software quality. " * 8
    jobs = [{"job_url": f"https://boards.greenhouse.io/example/jobs/{1000+i}",
             "job_title": f"Software Engineer {i}", "location_hint": "Amsterdam, Netherlands",
             "jd_text": jd, "extraction_method": "greenhouse_api"} for i in range(30)]
    monkeypatch.setattr(scanner, "_fetch_provider_jobs", lambda row: (jobs, "synthetic provider API"))
    for name in ("_preflight_connectivity", "_audit_seed_contradictions", "_enrich_support_from_detail",
                 "_fetch_jd_text_browser", "_get_or_launch_worker_browser"):
        monkeypatch.setattr(scanner, name, lambda *a, **kw: pytest.fail("unexpected startup check, refetch or browser"))
    scanner.execute_crawler()
    with output.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 30
    assert all(r["Visa Sponsorship"] == "Unknown" for r in rows)
    assert all(scanner.get_captured_jd(r["Job URL"]) == jd.strip() for r in rows)
    assert output.read_bytes().startswith(b"\xef\xbb\xbf")
