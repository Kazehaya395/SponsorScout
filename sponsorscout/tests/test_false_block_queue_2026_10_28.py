"""Issue 2026-10-28: the Assisted queue must list proven LISTING walls only.

Real evidence, not invented fixtures: the messages below are the error rows of
the user's full career scan `20261027T231035` (26,566 jobs kept). That run
queued 20 portals for human browser verification; 9 of them had readable
listings and 3,469 saved job rows, because the generic `blocked access: N
unresolved response(s)` summary - built from the per-job-detail outcome cache -
was accepted as a portal-level bot block, and its wording ("no listing could be
read") was produced by the same `challenge_reason()` that job-detail reads use.

Contract pinned here:
  * a job-detail wall says "job detail text", never "the job list";
  * a bare 401/403/429 is an access failure, not a bot-protection wall;
  * a listing that saved rows is only queued when a real listing wall was seen;
  * a listing that saved nothing is queued whatever the access failure was.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sponsorscout.core.blocked_portals import (  # noqa: E402
    _is_block_message, blocked_portals_from_errors_csv, blocked_portals_from_summary,
    listing_yield_by_url)
from sponsorscout.scanning.response_evidence import (  # noqa: E402
    challenge_reason, is_confirmed_wall, listing_block_message)

# --- verbatim error texts from run 20261027T231035 (career phase) ----------
DETAIL_ONLY_SUMMARY = (
    "blocked access: 15 unresolved response(s); Cloudflare challenge page served "
    "instead of the job list; no listing could be read at "
    "https://www.harnham.com/job/e1aeb1da-8723-4ba1-d23d-08d5948a7341-data-scientis")
UNCONFIRMED_403_ON_LISTING = (
    "blocked access: 1 unresolved response(s); HTTP 403 access blocked "
    "(human verification not confirmed) at https://www.gamesjobsdirect.com/results")
PROVEN_LISTING_WALL_WITH_ROWS = (
    "blocked by bot protection: Imperva challenge page served instead of the job "
    "list; no listing could be read")
PROVEN_ZERO_ROW_WALL = (
    "blocked by bot protection: Akamai challenge page served instead of the job "
    "list; no listing could be read")
NOT_A_BLOCK = "seed page did not load: https://careers.quinyx.com/jobs"
DNS_FAILURE = "net::ERR_NAME_NOT_RESOLVED"

WALL = ('<title>Just a moment...</title><h1>Verify you are human</h1>'
        '<div id="cf-challenge-running">'
        '<iframe src="https://challenges.cloudflare.com/local-fixture"></iframe></div>')


# --- wording follows the scope of the request that actually failed ---------

def test_detail_wall_no_longer_claims_the_listing_is_unreadable():
    reason = challenge_reason(WALL, status=200, subject="detail")
    assert "job detail text" in reason and "no job description could be read" in reason
    assert "no listing could be read" not in reason
    assert "job list" not in reason


def test_listing_wall_keeps_the_listing_wording():
    reason = challenge_reason(WALL, status=200)
    assert reason.startswith("Cloudflare challenge page served instead of the job list")
    assert "no listing could be read" in reason


def test_bare_403_is_reported_as_an_access_failure_not_a_bot_wall():
    reason = challenge_reason("<title>Forbidden</title><p>nope</p>", status=403)
    assert is_confirmed_wall(reason) is False
    message = listing_block_message(reason)
    assert message.startswith("listing access blocked:")
    assert "bot protection" not in message
    assert "no verification challenge observed" in message


def test_confirmed_wall_keeps_the_queue_marker():
    message = listing_block_message(challenge_reason(WALL, status=200))
    assert message.startswith("blocked by bot protection:")


# --- the queue predicate is scope- and outcome-aware ----------------------

@pytest.mark.parametrize("message,jobs_found,queued", [
    (DETAIL_ONLY_SUMMARY, 1210, False),
    (DETAIL_ONLY_SUMMARY, 0, True),          # nothing readable -> still worth a look
    (UNCONFIRMED_403_ON_LISTING, 0, True),
    (PROVEN_LISTING_WALL_WITH_ROWS, 9, True),
    (PROVEN_ZERO_ROW_WALL, 0, True),
    (NOT_A_BLOCK, 0, False),
    (DNS_FAILURE, 0, False),
    ("", 0, False),
])
def test_is_block_message_scope_and_outcome(message, jobs_found, queued):
    assert _is_block_message(message, jobs_found) is queued


# --- replay of the real run's artifacts ------------------------------------

def _write(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


ERROR_FIELDS = ["Run ID", "Timestamp", "Seed Name", "Phase", "Error Type", "Message", "Seed URL"]
LOG_FIELDS = ["Run ID", "Seed Name", "Company", "Source Type", "Target Country", "Status",
              "Provider", "Jobs Found", "Quarantined", "Duplicates", "Rejected Scope", "Error",
              "Diagnostics", "Duration Sec", "Seed URL", "Attempts", "Jobs Retained", "Jobs Merged"]

# Seed name, seed URL, error message, jobs found - straight from the run.
ROWS = [
    ("Harnham Germany", "https://www.harnham.com/job-search/", DETAIL_ONLY_SUMMARY, 1210),
    ("Work in Finland", "https://www.workinfinland.com/en/open-jobs/",
     DETAIL_ONLY_SUMMARY.replace("15", "4"), 1203),
    ("HelloFresh", "https://careers.hellofresh.com/global/en/search-results",
     DETAIL_ONLY_SUMMARY.replace("15", "1"), 382),
    ("Doctolib", "https://careers.doctolib.com/jobs/", UNCONFIRMED_403_ON_LISTING.replace(
         "gamesjobsdirect.com/results", "doctolib.com/jobs/team-lead-sales-enablement"), 181),
    ("English Jobs Austria (Analyst)", "https://englishjobsearch.at/jobs/Analyst",
     DETAIL_ONLY_SUMMARY.replace("15", "3"), 53),
    ("BAE Systems", "https://jobsearch.baesystems.com/search-and-apply",
     PROVEN_LISTING_WALL_WITH_ROWS, 9),
    ("gamesjobsdirect", "https://www.gamesjobsdirect.com/results", UNCONFIRMED_403_ON_LISTING, 0),
    ("NCS", "https://www.ncs.co/careers/", PROVEN_ZERO_ROW_WALL, 0),
    ("Quinyx", "https://careers.quinyx.com/jobs", NOT_A_BLOCK, 0),
]


def test_readable_listings_leave_the_queue_and_walls_stay(tmp_path):
    errors = tmp_path / "career_errors.csv"
    log = tmp_path / "career_scan_log.csv"
    _write(errors, [dict(zip(ERROR_FIELDS[1:], (
        "2026-10-27T23:31:21+0530", name, "target", "AccessBlocked", message, url)))
        for name, url, message, _ in ROWS], ERROR_FIELDS)
    _write(log, [dict(zip(LOG_FIELDS[1:], (
        name, name, "direct_employer", "Global", "partial" if found else "error", "auto",
        str(found), "0", "0", "0", message, message, "10.0", url, "1", str(found), "0")))
        for name, url, message, found in ROWS], LOG_FIELDS)
    summary = {"artifacts": {"career": {"errors": str(errors), "scan_log": str(log)},
                             "ats": {}}}

    queued = blocked_portals_from_summary(summary)
    names = {row["seed_name"] for row in queued}
    # the 4 listings that saved rows and only had detail walls are gone; Quinyx never
    # had a wall; the two real listing walls and the unreadable board stay queued.
    assert names == {"BAE Systems", "gamesjobsdirect", "NCS"}
    assert not {"Harnham Germany", "Work in Finland", "HelloFresh", "Doctolib",
                "English Jobs Austria (Analyst)", "Quinyx"} & names


def test_queued_row_says_how_much_was_already_saved(tmp_path):
    errors = tmp_path / "career_errors.csv"
    log = tmp_path / "career_scan_log.csv"
    _write(errors, [{"Timestamp": "t", "Seed Name": "BAE Systems", "Phase": "target",
                     "Error Type": "AccessBlocked",
                     "Message": PROVEN_LISTING_WALL_WITH_ROWS,
                     "Seed URL": "https://jobsearch.baesystems.com/search-and-apply"}],
             ERROR_FIELDS)
    _write(log, [{"Seed Name": "BAE Systems", "Company": "BAE Systems", "Source Type": "direct",
                  "Target Country": "Global", "Status": "partial", "Provider": "auto",
                  "Jobs Found": "9", "Quarantined": "0", "Duplicates": "0",
                  "Rejected Scope": "0", "Error": PROVEN_LISTING_WALL_WITH_ROWS,
                  "Diagnostics": "", "Duration Sec": "10.0",
                  "Seed URL": "https://jobsearch.baesystems.com/search-and-apply",
                  "Attempts": "1", "Jobs Retained": "9", "Jobs Merged": "0"}], LOG_FIELDS)
    [row] = blocked_portals_from_summary(
        {"artifacts": {"career": {"errors": str(errors), "scan_log": str(log)}}})
    assert "9 job row(s) were already saved from this listing" in row["reason"]


def test_listing_yield_reader_takes_the_highest_count_per_url(tmp_path):
    log = tmp_path / "scan_log.csv"
    _write(log, [{"Seed Name": "A", "Jobs Found": "5", "Seed URL": "https://x.example/jobs?a=1"},
                 {"Seed Name": "A", "Jobs Found": "12", "Seed URL": "https://x.example/jobs?a=1"},
                 {"Seed Name": "B", "Jobs Found": "", "Seed URL": "https://x.example/jobs?b=1"}],
             LOG_FIELDS)
    yields = listing_yield_by_url(str(log))
    assert yields == {"https://x.example/jobs?a=1": 12, "https://x.example/jobs?b=1": 0}
    assert listing_yield_by_url(str(tmp_path / "missing.csv")) == {}


def test_parser_accepts_the_yields_map_without_a_scan_log(tmp_path):
    errors = tmp_path / "errors.csv"
    _write(errors, [{"Timestamp": "t", "Seed Name": "X", "Phase": "target",
                     "Error Type": "AccessBlocked", "Message": DETAIL_ONLY_SUMMARY,
                     "Seed URL": "https://harnham.com/job-search/"}], ERROR_FIELDS)
    assert blocked_portals_from_errors_csv(str(errors), phase="career") == []
    found = blocked_portals_from_errors_csv(
        str(errors), phase="career", yields={"https://harnham.com/job-search/": 0})
    assert [row["seed_name"] for row in found] == ["X"]
