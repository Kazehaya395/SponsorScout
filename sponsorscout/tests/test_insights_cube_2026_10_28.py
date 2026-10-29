"""The Insights cube must agree with the SQL it replaces, or stay out of the way.

Issue 2026-10-28 (performance): the five Insights panels each scanned the whole
`jobs` table on a cold 2 MB page cache, which is what made a chart click take
0.10-0.82 s. The cube answers the same aggregates from a pre-grouped table.
These tests hold the two properties that matter: identical numbers for every
panel and filter, and automatic fallback to the live SQL whenever the cube does
not cover the request (unsupported filter, never built, or stale after an
ingest). Synthetic SQLite only - no live portal, no user database.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sponsorscout.db import database as db  # noqa: E402
from sponsorscout.insights import cube, queries  # noqa: E402

KEYS = ["url", "title", "company", "country", "domain", "category", "source_type",
         "source_subtype", "run_id", "verified_active", "is_expired", "visa_sponsorship",
         "relocation_support", "sponsorship_score", "remote_type", "description"]

ROWS = [
    ("https://fixture.invalid/a1", "Software Engineer", "Acme", "Germany",
     "tech", "Software Engineering", "verified", "direct", "run-1", 1, 0, "Y", "Y", 90, "remote"),
    ("https://fixture.invalid/a2", "Data Analyst", "Acme", "Germany",
     "tech", "Software Engineering", "verified", "direct", "run-1", 1, 0, "N", "N", 20, "hybrid"),
    ("https://fixture.invalid/b1", "Risk Manager", "Globex", "Netherlands",
     "finance", "Risk & Compliance", "verified", "recruiter", "run-2", 1, 0, "Y", "", 70, ""),
    ("https://fixture.invalid/b2", "Controller", "Globex", "Netherlands",
     "", "", "verified", "recruiter", "run-2", 1, 0, "", "", 10, "onsite"),
    ("https://fixture.invalid/c1", "Sales Lead", "Initech", "Spain",
     "consulting", "Sales & Business Development", "verified", "direct", "run-2", 1, 0, "yes", "no", 55, ""),
    # expired and unverified rows must never reach either path
    ("https://fixture.invalid/d1", "Stale Job", "Initech", "Spain",
     "tech", "Software Engineering", "verified", "direct", "run-1", 1, 1, "Y", "Y", 99, "remote"),
    ("https://fixture.invalid/d2", "Unverified Job", "Umbrella", "Italy",
     "tech", "Software Engineering", "verified", "direct", "run-1", 0, 0, "Y", "Y", 99, "remote"),
]
TERMS = {"a1": ["Python", "SQL", "leadership"], "a2": ["Python", "Excel"],
         "b1": ["SQL", "risk"], "b2": ["Excel", "communication"],
         "c1": ["negotiation", "leadership"], "d1": ["Python"]}


@pytest.fixture
def board(tmp_path):
    path = tmp_path / "cube.db"
    db.initialize(str(path))
    conn = sqlite3.connect(str(path))
    for row in ROWS:
        conn.execute("INSERT INTO jobs (" + ",".join(KEYS) + ") VALUES ("
                     + ",".join("?" for _ in KEYS) + ")",
                     list(row) + ["Python SQL Excel leadership collaboration." * 30])
    for key, terms in TERMS.items():
        job_id = conn.execute("SELECT id FROM jobs WHERE url LIKE ?", (f"%{key}",)).fetchone()[0]
        for term in terms:
            conn.execute("INSERT INTO job_terms(job_id, term, kind) VALUES(?,?,'skill')",
                         (job_id, term))
    conn.execute("UPDATE jobs SET insights_version='test'")
    conn.commit()
    conn.close()
    return str(path)


FILTER_SETS = [
    {},
    {"category": "Software Engineering"},
    {"category": "Other"},
    {"domain": "Unknown"},
    {"domain": "tech"},
    {"country": "Germany"},
    {"country": "Netherlands", "domain": "finance"},
    {"category": "Does Not Exist"},
]


def _panels(path, *, run_id="", country=None, source_subtype=None, category="", domain=""):
    """Exactly the five calls the Insights tab makes (ui/tabs/insights.py:refresh)."""
    return (
        queries.get_insights_coverage(path, run_id, country, source_subtype, category, domain),
        queries.get_jobs_by_category(path, run_id, country, source_subtype, domain),
        queries.get_jobs_by_domain(path, run_id, country, source_subtype, category),
        queries.get_top_skills(path, 15, run_id, country, source_subtype, category, domain),
        queries.get_sponsorship_by_category(path, run_id, country, source_subtype, category, domain),
    )


def _sql_only(path, **filters):
    """Force the original SQL path by making the cube refuse the request."""
    real = cube.usable
    cube.usable = lambda **kwargs: False
    try:
        return _panels(path, **filters)
    finally:
        cube.usable = real


def test_cube_agrees_with_sql_for_every_panel_and_filter(board):
    assert cube.refresh(board, force=True) is True
    for filters in FILTER_SETS:
        with_cube = _panels(board, **filters)
        without = _sql_only(board, **filters)
        assert with_cube == without, f"cube and SQL disagree for {filters}"


def test_cube_reports_nonzero_numbers(board):
    assert cube.refresh(board, force=True) is True
    coverage = queries.get_insights_coverage(board)
    assert coverage["total_jobs"] == 5          # expired + unverified rows excluded
    assert coverage["categorized_jobs"] == 4
    assert coverage["jobs_with_skills"] == 5
    assert ("Software Engineering", 2) in queries.get_jobs_by_category(board)
    assert ("Unknown", 1) in queries.get_jobs_by_domain(board)
    [risk] = [row for row in queries.get_sponsorship_by_category(board)
              if row["category"] == "Risk & Compliance"]
    # visa_sponsorship NOT IN ('Y','N') is NULL-safe in SQL: an empty verdict is
    # counted as neither yes, no, nor unk. The cube has to reproduce that exactly.
    assert (risk["visa_yes"], risk["visa_no"], risk["visa_unk"], risk["total"]) == (1, 0, 0, 1)


def test_a_stale_cube_is_never_served(board):
    assert cube.refresh(board, force=True) is True
    conn = sqlite3.connect(board)
    conn.execute("INSERT INTO jobs (" + ",".join(KEYS) + ") VALUES ("
                 + ",".join("?" for _ in KEYS) + ")",
                 ["https://fixture.invalid/e1", "New Role", "Umbrella", "Italy", "tech",
                  "Software Engineering", "verified", "direct", "run-3", 1, 0, "Y", "Y", 80,
                  "remote", "Python leadership."])
    conn.commit()
    conn.close()
    # The live table moved; the aggregate did not. Reads must fall back to SQL
    # rather than answer from last snapshot's counts.
    assert queries.get_insights_coverage(board)["total_jobs"] == 6
    assert cube.refresh(board) is True
    assert queries.get_insights_coverage(board)["total_jobs"] == 6
    assert cube.coverage(board)["total_jobs"] == 6


def test_unsupported_scope_falls_back_to_sql(board):
    assert cube.refresh(board, force=True) is True
    assert cube.coverage(board, run_id="run-1") is None
    assert cube.jobs_by_category(board, source_subtype="direct") is None
    # The public query still answers correctly, straight from the jobs table.
    from_sql = _sql_only(board, run_id="run-1")
    assert _panels(board, run_id="run-1") == from_sql
    # run-1 holds a1/a2 active plus one expired and one unverified row.
    assert from_sql[0]["total_jobs"] == 2


def test_database_without_a_cube_still_answers(board):
    # Nothing was ever built here: every panel must still work.
    assert cube.coverage(board) is None
    assert queries.get_insights_coverage(board)["total_jobs"] == 5
    assert queries.get_jobs_by_category(board)
    assert queries.get_top_skills(board, 15)
    assert cube.refresh(board) is True
    assert cube.coverage(board)["total_jobs"] == 5
