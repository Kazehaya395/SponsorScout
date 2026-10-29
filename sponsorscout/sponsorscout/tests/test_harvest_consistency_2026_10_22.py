"""F-27..F-30 pins (2026-10-22): the anti-bot / assisted harvest must be
*complete*, *clean* and *consistent* across both entry points.

Evidence (user screenshots, round 5):
* F-27 ``uploads/image-4/5/6.png`` — Robert Walters DE: 169 results on the
  board, 100 ingested, "Pages read: 12", stop reason "no new listings on 2
  consecutive pages - pagination control not recognised". The board's pager is a
  numbered window (1..10) plus an SVG arrow, so on page 10 no element's text is
  "11"; the generic synthesised ``p`` step was refused and the harvest stopped
  two loads early, silently short.
* F-28 ``uploads/image-2/3.png`` — Robert Walters IE: board says "36 job
  results", Search tab showed 38 rows, one of them the footer call-to-action
  "Create job alert".
* F-29 ``uploads/image-7.png`` — the Assisted tab's Action column clipped its
  own button ("oad and Inge").
* F-30 ``uploads/image-1.png`` — the post-scan assisted dialog in the Tools tab
  reported a bare job count with no pages/stop reason, and Scan History stayed
  stale (the ingestion never refreshed anything).

Pins below are offline: fake page objects for the pager walker, the real Qt
widgets for the column, monkeypatched seams for the Tools-tab batch worker, and
a full end-to-end run against a synthetic board lives in
``/home/user/live_verify/live_verify_synth.py``.
"""
from __future__ import annotations

import contextlib
import re
import os
import pathlib
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sponsorscout.core import blocked_portals as bp  # noqa: E402

CORE_SRC = pathlib.Path(bp.__file__).read_text(encoding="utf-8")
DB_SRC = (pathlib.Path(bp.__file__).resolve().parents[1]
            / "db" / "database.py").read_text(encoding="utf-8")
TOOLS_SRC = (pathlib.Path(bp.__file__).resolve().parents[1]
             / "ui" / "tabs" / "tools.py").read_text(encoding="utf-8")
ASSISTED_SRC = (pathlib.Path(bp.__file__).resolve().parents[1]
                / "ui" / "tabs" / "assisted_ingestion.py").read_text(encoding="utf-8")


class _FakePage:
    """Just enough of the Playwright page API for the pager walker."""

    def __init__(self, hrefs):
        self._hrefs = list(hrefs)

    def eval_on_selector_all(self, selector, expression):
        return list(self._hrefs)


class _DeadPage:
    def eval_on_selector_all(self, selector, expression):
        raise RuntimeError("Target page, context or browser has been closed")


# --------------------------------------------------------------------------- #
# F-27: board-owned paging hrefs
# --------------------------------------------------------------------------- #
def test_walker_returns_forward_board_hrefs_in_ascending_order():
    page = _FakePage([
        "?page=11", "?page=2", "?page=12", "?page=10",     # same listing, mixed
        "/en/job/vacancy-1",                               # a job, not a page
        "https://other.example/en/jobs.html?page=99",      # another host
        "javascript:void(0)", "#", "mailto:x@y.z",         # not navigable
        "?sort=date",                                      # no paging parameter
    ])
    got = bp.paging_href_candidates(page, "http://b/en/jobs.html?page=10", set())
    assert got == ["http://b/en/jobs.html?page=11",
                   "http://b/en/jobs.html?page=12"]


def test_walker_never_re_proposes_a_page_it_already_tried():
    page = _FakePage(["?page=11", "?page=12"])
    got = bp.paging_href_candidates(
        page, "http://b/en/jobs.html?page=10", {"http://b/en/jobs.html?page=11"})
    assert got == ["http://b/en/jobs.html?page=12"]


def test_walker_understands_the_legacy_p_scheme_and_an_svg_only_arrow():
    """The reported board: window slots 1..10 plus an arrow with no text."""
    hrefs = ["?p=%d" % k for k in range(1, 11)] + ["?p=11"]   # arrow href
    got = bp.paging_href_candidates(
        _FakePage(hrefs), "http://b/en/jobs.html?p=10", set())
    assert got == ["http://b/en/jobs.html?p=11"]


def test_walker_is_quiet_on_a_dead_page_instead_of_raising():
    assert bp.paging_href_candidates(_DeadPage(), "http://b/en/jobs.html?p=3", set()) == []


def test_loop_prefers_board_hrefs_before_synthesising_a_parameter():
    """Order matters: real hrefs are authoritative, guesses are a fallback."""
    assert "Check 3.5" in CORE_SRC
    assert CORE_SRC.index("Check 3.5") < CORE_SRC.index("Check 4:")
    assert "tried_urls" in CORE_SRC


def test_loop_reports_a_re_served_listing_honestly():
    """Pre-fix this read as 'pagination control not recognised' after burning
    two loads; now the clamp is named for what it is."""
    assert "page_seen_raw == r for r in recent_raws" in CORE_SRC
    assert "pages reachable" in CORE_SRC


def test_an_empty_page_retries_with_a_board_href_before_giving_up():
    assert "retrying with the board's own href" in CORE_SRC


# --------------------------------------------------------------------------- #
# F-28: footer / call-to-action noise must never become a job row
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("title", [
    "Create job alert", "Create a job alert", "Job alerts",
    "Register your CV", "Submit your CV", "Refer a friend",
    "Salary Calculator", "Careers advice", "Read more", "More information",
    "Looking to hire?", "Looking for a job?", "Save job", "Search jobs",
    "View all jobs", "Quick links", "Subscribe to our newsletter",
    "Load more", "Show more", "Cookie Policy", "Key contact",
])
def test_cta_and_footer_chrome_is_noise(title):
    assert bp.is_noise_cta_title(title) is True


@pytest.mark.parametrize("title", [
    "Senior Python Engineer", "Senior Platform Engineer 12",
    "HR Advisor - Dublin", "Contact Centre Advisor", "Newsletter Editor",
    "Recruitment Consultant", "Product Designer (m/f/d)",
    "Java Developer - Save-the-Children programme",
])
def test_real_vacancy_titles_are_not_noise(title):
    assert bp.is_noise_cta_title(title) is False


def test_noise_filter_is_wired_into_every_harvest_path():
    assert CORE_SRC.count("is_noise_cta_title(title") >= 3


def test_the_reported_noise_title_is_filtered_end_to_end():
    """uploads/image-3.png: 'Create job alert' appeared as a Search row."""
    assert bp.is_noise_cta_title("Create job alert") is True


# --------------------------------------------------------------------------- #
# F-29: the Action column must fit its own button label
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    yield app


@pytest.fixture(scope="module")
def assisted_tab(qapp, tmp_path_factory):
    from sponsorscout.db import database as db
    from sponsorscout.ui.tabs.assisted_ingestion import AssistedIngestionTab

    dbpath = str(tmp_path_factory.mktemp("f29") / "assisted.db")
    db.initialize(dbpath)
    return AssistedIngestionTab(dbpath)


def test_action_column_is_wide_enough_for_the_button_label(assisted_tab):
    from PySide6.QtGui import QFontMetrics

    table = assisted_tab.portals_table
    assert table.rowCount() > 0
    fm = QFontMetrics(table.font())
    need = fm.horizontalAdvance("Load and Ingest") + 12
    assert table.columnWidth(2) >= need, (
        "Action column %d px clips the %d px button label"
        % (table.columnWidth(2), need))


def test_every_action_button_can_render_its_full_label(assisted_tab):
    from PySide6.QtGui import QFontMetrics

    table = assisted_tab.portals_table
    for row in range(table.rowCount()):
        btn = table.cellWidget(row, 2)
        assert btn is not None, "row %d has no action button" % row
        fm = QFontMetrics(btn.font())
        assert btn.minimumWidth() >= fm.horizontalAdvance(btn.text()), (
            "row %d button clips '%s'" % (row, btn.text()))


def test_action_column_width_is_derived_from_font_metrics_not_a_constant():
    assert "QFontMetrics" in ASSISTED_SRC
    assert "horizontalAdvance" in ASSISTED_SRC


# --------------------------------------------------------------------------- #
# F-30: the Tools-tab assisted flow must match the Assisted tab's honesty
# --------------------------------------------------------------------------- #
def test_tools_flow_runs_off_the_ui_thread():
    assert "class _AssistedBatchWorker(QThread)" in TOOLS_SRC
    assert "_AssistedBatchWorker(items, self.db_path, self)" in TOOLS_SRC


def test_tools_flow_reports_pages_and_stop_reason_like_the_assisted_tab():
    assert "LAST_INGESTION_REPORT" in TOOLS_SRC
    assert "Pages read: %d. Pagination stopped because: %s" in TOOLS_SRC


def test_tools_flow_refreshes_history_and_search_after_ingesting():
    assert "self.data_changed.emit()" in TOOLS_SRC
    assert "self.refresh()" in TOOLS_SRC


def test_the_old_bare_count_dialog_is_gone():
    """uploads/image-1.png wording: a count and nothing else, synchronously."""
    assert 'total_ingested += run_assisted_ingestion(b["url"], b["seed_name"], conn)' not in TOOLS_SRC


def test_batch_worker_aggregates_the_honesty_fields(qapp, monkeypatch, tmp_path):
    from sponsorscout.ui.tabs import tools as tools_mod

    monkeypatch.setattr("sponsorscout.db.database.get_connection",
                        lambda p: contextlib.nullcontext(None))
    reports = iter([
        {"pages_visited": 17, "stop_reason": "no next-page control recognised on page 17"},
        {"pages_visited": 5,
         "stop_reason": "board re-served the same listing after a paging step (page 5); "
                        "no further pages reachable"},
    ])

    def fake_ingest(url, name, conn, **kw):
        bp.LAST_INGESTION_REPORT = next(reports)
        return 3

    monkeypatch.setattr(bp, "run_assisted_ingestion", fake_ingest)

    worker = tools_mod._AssistedBatchWorker(
        [("http://a/en/jobs.html", "A"), ("http://b/en/jobs.html", "B")],
        str(tmp_path / "tools.db"))
    out = []
    worker.done.connect(lambda *args: out.append(args))
    worker.run()                      # call the body directly: no thread needed

    assert len(out) == 1
    ingested, pages, reasons, error = out[0]
    assert ingested == 6
    assert pages == 22
    assert len(reasons) == 2 and error == ""


def test_batch_worker_survives_a_failing_portal_and_says_so(qapp, monkeypatch, tmp_path):
    from sponsorscout.ui.tabs import tools as tools_mod

    monkeypatch.setattr("sponsorscout.db.database.get_connection",
                        lambda p: contextlib.nullcontext(None))
    calls = {"n": 0}

    def flaky_ingest(url, name, conn, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("net::ERR_CONNECTION_REFUSED")
        bp.LAST_INGESTION_REPORT = {"pages_visited": 2, "stop_reason": "last page reached"}
        return 4

    monkeypatch.setattr(bp, "run_assisted_ingestion", flaky_ingest)

    worker = tools_mod._AssistedBatchWorker(
        [("http://a/jobs", "A"), ("http://b/jobs", "B")], str(tmp_path / "tools.db"))
    out = []
    worker.done.connect(lambda *args: out.append(args))
    worker.run()

    ingested, pages, reasons, error = out[0]
    assert ingested == 4 and pages == 2          # the failed portal contributes nothing
    assert "ERR_CONNECTION_REFUSED" in error


def test_a_failed_portal_cannot_inherit_the_previous_report(qapp, monkeypatch, tmp_path):
    """F-30: the module-level report is cleared per portal, so a crash never
    reports another portal's page count or stop reason as its own."""
    from sponsorscout.ui.tabs import tools as tools_mod

    bp.LAST_INGESTION_REPORT = {"pages_visited": 999, "stop_reason": "leftover from a previous run"}
    monkeypatch.setattr("sponsorscout.db.database.get_connection",
                        lambda p: contextlib.nullcontext(None))

    def always_fails(url, name, conn, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(bp, "run_assisted_ingestion", always_fails)
    worker = tools_mod._AssistedBatchWorker([("http://a/jobs", "A")], str(tmp_path / "t.db"))
    out = []
    worker.done.connect(lambda *args: out.append(args))
    worker.run()
    ingested, pages, reasons, error = out[0]
    assert (ingested, pages, reasons) == (0, 0, [])
    assert "boom" in error
    bp.LAST_INGESTION_REPORT = {}


# --------------------------------------------------------------------------- #
# F-27b: a refused advance must be retried, not reported as the end
# --------------------------------------------------------------------------- #
def test_a_re_served_listing_triggers_a_url_based_retry_before_giving_up():
    assert "re-served the previous listing" in CORE_SRC   # logged, then retried
    assert "attempt %d/3" in CORE_SRC                      # bounded retry
    assert "mis_advances >= 3" in CORE_SRC


def test_the_dom_click_checks_are_skipped_after_they_have_just_failed():
    """Checks 1-3 produced a phantom advance; repeating them wastes loads."""
    assert CORE_SRC.count("and not skip_dom") >= 2
    assert "None if skip_dom else page.evaluate" in CORE_SRC


def test_a_wasted_load_does_not_consume_a_page_number():
    assert "if not re_served:" in CORE_SRC
    assert CORE_SRC.index("if not re_served:") < CORE_SRC.rindex("page_num += 1")


def test_clamp_oscillation_is_detected_as_re_serve_not_as_new_pages():
    """A,B,A,B... clamp states must collapse into the mis-advance counter, so the
    reported page number stays at the true last page (round-8 cosmetic fix)."""
    assert "recent_raws.append(page_seen_raw)" in CORE_SRC
    assert "del recent_raws[:-4]" in CORE_SRC


def test_the_true_end_message_quotes_the_last_complete_page_not_the_counter():
    """Round-8: '(page 20)' read like a cap; page_num is the next-page counter."""
    assert "(last complete page %d)" in CORE_SRC
    assert "last_good_page = page_num" in CORE_SRC


def test_the_synthesised_step_is_still_allowed_after_a_failed_advance():
    """page_jobs is empty on a re-served page, so the >=5 guard must be lifted."""
    assert "(len(page_jobs) >= 5 or skip_dom)" in CORE_SRC


# --------------------------------------------------------------------------- #
# F-31: a bot wall or render failure MID-harvest must be classified, waited
#       out, or reported honestly - never "pagination control not recognised"
# --------------------------------------------------------------------------- #
CHALLENGE = ("<html><head><title>Access to this page has been denied</title></head>"
             "<body><h1>Press &amp; Hold</h1><p>PerimeterX px-000</p></body></html>")
LISTING = ("<html><head><title>Search jobs</title></head><body>"
           "<p>You have 170 job results</p>"
           "<a href='/j/1'>Finance Manager</a><a href='/j/2'>Tax Manager</a>"
           "<a href='/j/3'>Payroll Specialist</a></body></html>")
BLANK = "<html><head><title>502 Bad Gateway</title></head><body>oops</body></html>"


class _ScriptedPage:
    """Each helper evaluation (content+title) consumes one scripted state."""

    def __init__(self, states):
        self.states = list(states)
        self.goto_calls = []
        self.reloads = 0

    def content(self):
        return self.states[0][0]

    def title(self):
        c, t = self.states[0]
        if len(self.states) > 1:
            self.states.pop(0)
        return t

    def is_closed(self):
        return False

    def eval_on_selector_all(self, selector, expression):
        return []

    def reload(self, wait_until=None, timeout=None):
        self.reloads += 1

    def goto(self, url, wait_until=None, timeout=None):
        self.goto_calls.append(url)


def test_challenge_and_listing_detection_are_factored_and_correct():
    assert bp._page_is_challenge(_ScriptedPage([(CHALLENGE, "x")])) is True
    assert bp._page_is_challenge(_ScriptedPage([(LISTING, "x")])) is False
    assert bp._page_has_listing(_ScriptedPage([(LISTING, "x")])) is True
    assert bp._page_has_listing(_ScriptedPage([(BLANK, "x")])) is False


def test_a_mid_run_challenge_that_a_human_clears_resumes_the_harvest():
    page = _ScriptedPage([(CHALLENGE, "d"), (CHALLENGE, "d")] + [(LISTING, "s")] * 8)
    assert bp._recover_blank_page(page, "http://b/en/jobs.html?page=8", 8,
                                  wait_s=6.0) == "cleared"
    assert page.goto_calls == ["http://b/en/jobs.html?page=8"]


def test_a_mid_run_challenge_nobody_clears_is_named_as_such():
    page = _ScriptedPage([(CHALLENGE, "d")] * 12)
    assert bp._recover_blank_page(page, "http://b/x?page=8", 8, wait_s=1.5) == "challenge"
    assert page.goto_calls == []


def test_a_failed_render_gets_one_reload_and_then_resumes():
    page = _ScriptedPage([(BLANK, "b")] + [(LISTING, "s")] * 6)
    assert bp._recover_blank_page(page, "http://b/x?page=8", 8, wait_s=1.5) == "cleared"
    assert page.reloads == 1


def test_a_page_that_stays_blank_is_reported_as_blank_not_as_pagination():
    page = _ScriptedPage([(BLANK, "b")] * 10)
    assert bp._recover_blank_page(page, "http://b/x?page=8", 8, wait_s=1.5) == "blank"


def test_recovery_runs_before_the_misleading_pagination_stop():
    assert "bot protection re-engaged on page" in CORE_SRC
    assert "or undetected bot wall" in CORE_SRC
    # the *stop* (rindex: the helper's docstring quotes the old message) must sit
    # after the recovery call in the loop body
    assert (CORE_SRC.index("outcome = _recover_blank_page(page, current_page_url")
            < CORE_SRC.rindex("no next-page control recognised"))


def test_skip_dom_is_assigned_before_the_scroll_guard_uses_it():
    """Regression pin: rev-5 first draft read skip_dom before assigning it."""
    assert (CORE_SRC.index("skip_dom = force_url_advance")
            < CORE_SRC.index("if not skip_dom:"))


def test_lazy_pagers_get_a_scroll_before_the_dom_checks():
    """F-32: a pager that renders on scroll must not read as 'no pagination'."""
    assert "window.scrollTo(0, document.body.scrollHeight)" in CORE_SRC
    assert CORE_SRC.index("document.body.scrollHeight") < CORE_SRC.index("# Check 1:")


def test_the_clearance_wait_is_configurable_so_tests_never_block():
    assert "clear_wait_s" in CORE_SRC


# --------------------------------------------------------------------------- #
# F-33: a job-shaped TITLE on a non-job URL is still noise; locale variants of
#       one job are one job
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("url", [
    "https://b/en/job-alerts", "https://b/en/job-alerts?ref=board",
    "https://b/en/register-cv/", "https://b/de/salary-calculator.html",
    "https://b/en/refer-a-friend", "https://b/en/careers-advice/x",
    "https://b/en/offices", "https://b/en/my-account/profile",
    "https://b/en/login", "https://b/en/newsletter",
])
def test_chrome_urls_are_rejected_whatever_the_title_says(url):
    assert bp.is_chrome_url(url) is True


@pytest.mark.parametrize("url", [
    "https://b/en/jobs/senior-python-engineer.html",
    "https://b/en/job/vacancy-12",
    "https://b/jobs/4d-entwickler-100-remote",
    "https://b/en/contact-us-page?x=1",      # a real page that merely starts alike
    "https://b/en/officer-vacancies/lead",    # 'offices' must not match mid-word
])
def test_real_detail_urls_are_not_chrome(url):
    assert bp.is_chrome_url(url) is False


def test_locale_variants_of_one_job_share_a_dedup_key():
    a = bp.normalize_job_url("https://b/en/jobs/slug.html")
    assert a == bp.normalize_job_url("https://b/de/jobs/slug.html")
    assert a == bp.normalize_job_url("https://b/en-GB/jobs/slug.html#frag")
    assert a != bp.normalize_job_url("https://b/en/jobs/other.html")
    assert a != bp.normalize_job_url("https://b/en/jobs/slug.html?p=2")


def test_listing_self_links_are_chrome_but_job_id_queries_are_jobs():
    assert bp.is_listing_self_link("https://b/en/jobs.html?page=3", "/en/jobs.html") is True
    assert bp.is_listing_self_link("https://b/en/jobs.html", "/en/jobs.html") is True
    assert bp.is_listing_self_link("https://b/en/jobs.html?jobId=4711", "/en/jobs.html") is False
    assert bp.is_listing_self_link("https://b/en/jobs/slug.html", "/en/jobs.html") is False


def test_url_guards_are_wired_into_both_harvest_branches():
    assert CORE_SRC.count("is_chrome_url(full_url)") >= 2
    assert CORE_SRC.count("is_listing_self_link(full_url, listing_path)") >= 2
    assert "seen_norm.add(norm_url)" in CORE_SRC
    assert "is_chrome_url(job_url)" in CORE_SRC


# --------------------------------------------------------------------------- #
# Round-8 question: is there a 20-page cap? No. The only page-count limit is the
# max_pages parameter (default 60); every other stop is condition-driven.
# --------------------------------------------------------------------------- #
def test_the_only_page_count_cap_is_the_max_pages_parameter():
    assert "max_pages: int = 60" in CORE_SRC
    assert "while page_num <= max_pages and not page.is_closed():" in CORE_SRC
    # the loop advances by condition, never by a hard-coded page number
    assert "page_num <= 20" not in CORE_SRC
    assert "page_num >= 20" not in CORE_SRC
    assert "page_num == 20" not in CORE_SRC


def test_the_ui_worker_uses_the_same_sixty_page_parameter():
    assert "max_pages=60" in ASSISTED_SRC
    assert "max_pages=self.max_pages" in ASSISTED_SRC


# ---------------------------------------------------------------------------
# Round 9 (F-34): the rev-6 RW NL run reported "Pages read: 32 ... stopped on
# page 21" for a 20-page board. The harvest was complete (200 jobs >= the
# board's live 197); the two scary numbers were counter artifacts:
#   * pages_visited counted every non-blank browser load, including clamp /
#     redirect phantoms past the true end;
#   * the no-next-control stop branch quoted the raw NEXT-page counter and,
#     unlike the re-serve branch, never named the last complete page.
# ---------------------------------------------------------------------------

def test_pages_read_means_complete_pages_not_browser_loads():
    """F-34: the user-facing count is the last complete page; raw loads are
    reported separately as `loads` and never inflate the dialog number."""
    assert "pages_visited = last_good_page" in CORE_SRC
    # the assignment must sit AFTER the paging loop, not inside it
    assert (CORE_SRC.rindex("while page_num <= max_pages")
            < CORE_SRC.index("pages_visited = last_good_page"))
    assert "loads=loads," in CORE_SRC           # report carries the raw count
    assert "browser load(s) incl. retries" in CORE_SRC


def test_the_per_load_pages_visited_increment_is_gone():
    """Round-9 regression: `pages_visited += 1` inside the loop is what made a
    20-page board read as 32 pages."""
    assert "pages_visited += 1" not in CORE_SRC


def test_every_honest_stop_branch_names_the_last_complete_page():
    """F-34: re-serve, challenge wall, render failure, consecutive-empty and
    no-next-control stops all quote the last page that delivered jobs."""
    assert CORE_SRC.count("last complete page %d") >= 5   # all five branches
    assert CORE_SRC.count("(last complete page %d)") >= 4  # parenthesised form
    for frag in ("no next-page control recognised on page %d ",
                 "pagination control not recognised ",
                 "or undetected bot wall; last complete page %d",
                 "clearance observed within the wait window "):
        assert frag in CORE_SRC


# ---------------------------------------------------------------------------
# Round 10 (F-35 / F-36): the user's rev-8 RW NL run stopped with "no
# next-page control recognised on page 47 (last complete page 46)" while the
# board may hold more pages, and assisted runs left no CSV trail behind
# (regular scans write a job list + scan log + quarantine/errors set).
# ---------------------------------------------------------------------------

def test_false_end_probe_is_budgeted_and_gated_on_a_full_last_page():
    """F-35: before accepting a missing next control as the end, the walker
    probes the synthesised next URL - at most twice per run, and only when the
    last complete page was a FULL page (a partial last page is a true end)."""
    assert "probes_left = 4" in CORE_SRC   # F-37: budget raised 2 -> 4
    assert "end-of-board probe" in CORE_SRC
    assert "last_good_count >= page_size_max - 1" in CORE_SRC  # trap-row tolerant
    assert "db_path: str | None = None" in CORE_SRC
    assert "out-of-range probe" in CORE_SRC


def test_assisted_run_writes_the_regular_scan_csv_set():
    """F-36: job list, recruiter view, quarantine, errors and scan log - the
    same five files a regular scan run leaves, utf-8-sig for Excel."""
    for frag in ("ensure_scan_output_dir", "_career_jobs.csv",
                 "_recruiter.csv", "_quarantine.csv", "_errors.csv",
                 "_scan_log.csv"):
        assert frag in CORE_SRC
    assert CORE_SRC.count('encoding="utf-8-sig"') >= 4


def test_assisted_run_counts_new_vs_already_present_rows():
    """F-36: re-runs must not report re-seen jobs as new ingests (the user's
    DB showed jobs_found=450 / duplicates=0 for a 250-new + 200-reseen run)."""
    assert 'SELECT 1 FROM jobs WHERE url = ?' in CORE_SRC
    assert "dup_count += 1" in CORE_SRC
    assert "new_count += 1" in CORE_SRC
    assert "duplicates=dup_count" in CORE_SRC


def test_assisted_run_metadata_uses_the_real_run_clock():
    """F-36: started_at/finished_at/duration must span the harvest (the
    user's DB showed started_at == finished_at and duration_sec 0)."""
    assert "run_started = _dt.now(_tz.utc)" in CORE_SRC
    assert "run_id = run_started.strftime" in CORE_SRC
    assert "duration_s = (datetime.now(timezone.utc) - run_started).total_seconds()" in CORE_SRC


def test_dialog_distinguishes_new_rows_from_refreshed_ones():
    """F-36: the completion dialog names duplicates instead of letting a
    re-run read as a fresh ingest of everything."""
    assert "were already in the database - they were refreshed, not duplicated" in ASSISTED_SRC
    assert "so 0 new rows were added" in ASSISTED_SRC


def test_phrase_only_shells_cannot_loop_the_recoverer():
    """F-35b: round-10 phase E exposed an infinite recover loop: an
    out-of-range shell saying "You have 0 job results" matches the
    listing-phrase test, so every "cleared" outcome re-entered the blank
    branch. The cleared-continue must therefore sit under the recovery cap."""
    assert (CORE_SRC.index('recover_cleared += 1')
            < CORE_SRC.index('if recover_cleared <= 2:'))
    assert (CORE_SRC.index('if recover_cleared <= 2:')
            < CORE_SRC.index('stayed empty after'))


def test_blank_page_recovery_is_bounded():
    """F-35b: at most two "cleared" recoveries per page; a page that stays
    empty after that ends the harvest honestly instead of looping."""
    assert "recover_cleared = 0" in CORE_SRC
    assert "recover_cleared <= 2" in CORE_SRC
    assert "stayed empty after" in CORE_SRC


def test_the_reserve_stop_also_probes_before_accepting_a_full_page_end():
    """F-35c (round 11): the user's 490-job run stopped in the mis-advance
    (re-serve) branch on a board believed to hold more. That branch now spends
    the same budgeted probe as the no-next branch before accepting the end."""
    assert CORE_SRC.count("end-of-board probe") >= 2
    i = CORE_SRC.index("if mis_advances >= 3:")
    j = CORE_SRC.index("board re-served the same listing after %d paging ")
    assert i < j
    assert "mis_advances = 0" in CORE_SRC[i:j]


def test_probe_budget_is_shared_between_both_stop_branches():
    """F-35/F-35c: two probe loads per run in total, never per branch - a
    clamping board must not be probed into a load spiral."""
    assert CORE_SRC.count("probes_left -= 1") == 2
    assert "probes_left = 4" in CORE_SRC   # F-37: budget raised 2 -> 4


def test_refused_advances_get_escalating_polite_cooldowns_before_probes():
    """F-37/F-38 (rounds 12-13): the live RW NL pager runs to 60 pages while
    runs stopped at 49, then 47, on three refused advances - a bot edge
    rate-limit window that can outlast a single 15 s pause. Escalating
    cool-downs (15/45/90 s) + reload + full advance-chain retry must precede
    probe spending and the honest stop."""
    i = CORE_SRC.index("if mis_advances >= 3:")
    j = CORE_SRC.index("F-35c: same false-end logic as the no-next branch")
    seg = CORE_SRC[i:j]
    assert "cooldowns_used < len(_COOLDOWN_STEPS_S)" in seg
    assert "_COOLDOWN_STEPS_S[cooldowns_used]" in seg
    assert "mis_advances = 0" in seg
    assert "wait_for_timeout(int(delay * 1000))" in seg


def test_cooldown_escalation_is_bounded_and_polite():
    """F-38 (round 13): the escalation is bounded - exactly three steps,
    150 s of pure waiting in total, no request burst. Pure waiting is what a
    rate-limit window needs; hammering is what trips it."""
    m = re.search(r"_COOLDOWN_STEPS_S = \(([^)]*)\)", CORE_SRC)
    assert m, "cool-down step table missing"
    steps = [float(x) for x in m.group(1).replace(",", " ").split()]
    assert steps == [15.0, 45.0, 90.0]
    assert sum(steps) == 150.0
    assert CORE_SRC.count("cooldowns_used += 1") == 1


def test_run_row_timestamps_carry_the_real_calendar_day():
    """F-39 (round 13): the scan-run row's Started/Finished were formatted
    with "%Y-%m-%m" - month twice, no day - so a 2026-10-23 run reported
    2026-10-10 in the exported scan log (your round-13 CSVs). The day must be
    in the format, on the same UTC clock as run_id and the event timeline."""
    assert 'strftime("%Y-%m-%m' not in CORE_SRC
    i = CORE_SRC.index("started_s = run_started.strftime")
    seg = CORE_SRC[i:i + 400]
    assert 'started_s = run_started.strftime("%Y-%m-%d %H:%M:%S")' in seg
    assert 'finished_s = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")' in seg


def test_probe_budget_is_four_shared_loads():
    """F-37: four probe loads per run in total (was 2), still shared between
    both stop branches - bounded, polite, never a spiral."""
    assert "probes_left = 4" in CORE_SRC
    assert CORE_SRC.count("probes_left -= 1") == 2


def test_denied_board_hrefs_stay_retryable():
    """F-37b (round 12): a re-served landing is a denied advance, not a visited
    page - keeping it in tried_urls permanently blocked the signed pager's own
    arrow after a bot-edge soft denial, ending a 60-page board at 49."""
    i = CORE_SRC.index("if re_served:")
    j = CORE_SRC.index("if mis_advances >= 3:")
    seg = CORE_SRC[i:j]
    assert "tried_urls.discard(current_page_url)" in seg
    assert "tried_urls.add(current_page_url)" not in seg


def test_denied_advance_target_is_re_requested_before_synthesising():
    """F-37d (round 12): when the denied landing URL IS the advance target
    (paging value == counter), it must be re-requested politely before the
    retry chain drifts to synthesised stale-token URLs - a signed board clamps
    those to its window, while a bot edge's soft denial releases on the next
    same-href request. The F-37c global re-base (last_good_url baseline) broke
    the window-boundary rescue phases E/F rely on and must stay reverted."""
    assert CORE_SRC.count("_paging_value(current_page_url) == page_num") == 1
    i = CORE_SRC.index("_paging_value(current_page_url) == page_num")
    seg = CORE_SRC[i:i + 900]
    assert "page.goto(current_page_url" in seg
    assert "def _paging_value(" in CORE_SRC
    assert "last_good_url if re_served else current_page_url" not in CORE_SRC
    assert "last_good_url = current_page_url" not in CORE_SRC
