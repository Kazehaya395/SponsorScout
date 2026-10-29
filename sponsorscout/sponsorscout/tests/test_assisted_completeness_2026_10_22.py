"""F-22 / F-25 pins (2026-10-22): assisted ingestion must be honest and generic.

Regression pinned: a live assisted run on robertwalters.de harvested 10 of 169
jobs yet reported "Successfully harvested and ingested 10 jobs" as a clean
success, and the pagination fallback keyed its paging scheme off the literal
host name ("robertwalters"), which guide §15.9 forbids.

These pins prove:
1. the query-parameter paging step is host-name free and preserves whatever
   scheme the board already uses;
2. the generic locale-segment 404 recovery works for any host;
3. no host-name branching remains in the assisted-ingestion core or the UI
   URL rewrite;
4. button labels carry no bare '&' (Qt mnemonic -> stray underline, F-24);
5. the completion report the UI reads actually exists.
"""
import pathlib

from sponsorscout.core import blocked_portals as bp

CORE = pathlib.Path(bp.__file__)
UI = CORE.parents[1] / "ui" / "tabs" / "assisted_ingestion.py"


def test_next_page_url_increments_the_boards_own_scheme():
    assert bp.next_page_url("https://x/jobs?p=3", 9) == "https://x/jobs?p=4"
    assert bp.next_page_url("https://x/jobs?page=3", 9) == "https://x/jobs?page=4"
    # no scheme of its own -> probe p, 0-indexed (page 1 -> p=1)
    assert bp.next_page_url("https://x/jobs", 1) == "https://x/jobs?p=1"
    # garbage values fall back instead of raising
    assert bp.next_page_url("https://x/jobs?p=abc", 4) == "https://x/jobs?p=4"


def test_next_page_url_is_host_name_free():
    src = CORE.read_text(encoding="utf-8")
    assert "robertwalters" not in src.lower(), (
        "no host-name rules in the assisted-ingestion core (guide §15.9)")


def test_strip_locale_segment_is_generic():
    assert bp.strip_locale_segment("https://h/en/jobs.html") == "https://h/jobs.html"
    assert bp.strip_locale_segment("https://h/de/jobs") == "https://h/jobs"
    assert bp.strip_locale_segment("https://h/en-GB/jobs") == "https://h/jobs"
    assert bp.strip_locale_segment("https://h/jobs.html") is None
    assert bp.strip_locale_segment("https://h/energy/jobs") is None  # not a locale


def test_ui_dropped_the_host_specific_url_rewrite():
    src = UI.read_text(encoding="utf-8")
    assert "robertwalters.ie/en/jobs.html" not in src, (
        "the UI must not rewrite one host's path (guide §15.9)")


def test_ui_labels_carry_no_bare_mnemonic_ampersand():
    src = UI.read_text(encoding="utf-8")
    assert "Browser & Ingest" not in src, "bare & renders as a stray underline (F-24)"
    assert '"Load & Ingest"' not in src, "bare & renders as a stray underline (F-24)"


def test_completion_report_is_exposed_for_the_ui():
    assert isinstance(bp.LAST_INGESTION_REPORT, dict)
    src = UI.read_text(encoding="utf-8")
    assert "LAST_INGESTION_REPORT" in src, (
        "the completion dialog must read the honest report (F-22)")
    assert "Pages read: %d. Pagination stopped because: %s" in src


def test_denial_page_titles_count_as_a_bot_challenge():
    src = CORE.read_text(encoding="utf-8")
    assert "access to this page has been denied" in src, (
        "PerimeterX-style denial titles must be recognised as a challenge")
    assert "blocked by bot protection:" in src, (
        "the §14b marker prefix must be logged so the post-scan prompt sees it")


def test_has_jobs_never_trusts_a_lone_navigation_anchor():
    src = CORE.read_text(encoding="utf-8")
    assert "_jobish_anchor_count(page) >= 3" in src, (
        "a denial/consent shell with one anchor must not read as a listing")


def test_jobish_anchor_count_tolerates_a_dead_or_shell_page():
    class _Dead:
        def query_selector_all(self, sel):
            raise RuntimeError("page closed")

    class _Shell:
        def query_selector_all(self, sel):
            return [object()]

    assert bp._jobish_anchor_count(_Dead()) == 0
    assert bp._jobish_anchor_count(_Shell()) == 1
