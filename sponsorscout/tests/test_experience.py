"""FIX P0-30: experience extraction engine + persistence round-trip."""
import sqlite3

from sponsorscout.scanning.career import career_scanner as career_mod

# Minimal ``jobs`` replica: only the columns ``upsert_job`` writes as NOT-NULL
# free text plus the FIX P0-30 experience columns under test.
_JOBS_DDL = """CREATE TABLE jobs (
        id INTEGER PRIMARY KEY, external_id TEXT, title TEXT, company TEXT,
        country TEXT, location TEXT, url TEXT UNIQUE, ats_source TEXT,
        source_type TEXT, source_subtype TEXT, source_name TEXT,
        description TEXT, trust_score INTEGER, freshness_score INTEGER,
        sponsorship_score INTEGER, match_score INTEGER,
        verified_active INTEGER, is_expired INTEGER, last_verified_at TEXT,
        first_seen_at TEXT DEFAULT '', last_seen_at TEXT DEFAULT '',
        created_at TEXT DEFAULT '', updated_at TEXT DEFAULT '',
        remote_type TEXT, eu_blue_card INTEGER DEFAULT 0,
        has_relocation INTEGER DEFAULT 0, experience_level TEXT DEFAULT '',
        experience_required TEXT DEFAULT '', experience_min_years REAL,
        experience_source TEXT DEFAULT '', industry TEXT DEFAULT '',
        ai_score INTEGER DEFAULT 0, visa_sponsorship TEXT,
        relocation_support TEXT, eu_blue_card_verdict TEXT,
        relocation_required TEXT, support_confidence REAL,
        support_evidence TEXT, support_evidence_url TEXT,
        support_evidence_type TEXT, blue_card_evidence TEXT,
        canonical_job_id TEXT, run_id TEXT, raw_location TEXT DEFAULT '',
        country_source TEXT DEFAULT 'auto')"""


def _make_jobs_db(tmp_path) -> str:
    """Create a throwaway DB with the replica schema; return its path."""
    db_path = str(tmp_path / "t.db")
    conn = sqlite3.connect(db_path)
    conn.execute(_JOBS_DDL)
    conn.commit()
    conn.close()
    return db_path


def test_years_range_extracted():
    exp = career_mod.extract_experience(
        "You have 5-8 years of professional experience in backend.", "")
    assert exp["required"] == "5-8 years"
    assert exp["min_years"] == 5
    assert exp["max_years"] == 8
    assert exp["level"] == "Senior"  # midpoint 6.5
    assert exp["source"] == "detail_text"


def test_months_and_plus():
    exp = career_mod.extract_experience("at least 6 months of experience", "")
    assert exp["min_years"] == 0.5
    assert "month" in exp["required"]


def test_disqualifiers_ignored():
    # Ages, founding dates and visa durations must never become experience.
    for text in ("You must be at least 18 years old.",
                 "We were founded 25 years ago.",
                 "visa valid for 3 years"):
        exp = career_mod.extract_experience(text, "")
        assert exp["required"] == "Unknown", text


def test_none_required():
    exp = career_mod.extract_experience("No prior experience required.", "")
    assert exp["required"] == "None required"
    assert exp["min_years"] == 0


def test_title_inference_fallback():
    exp = career_mod.extract_experience("", "Senior Backend Engineer")
    assert exp["level"] == "Senior"
    assert exp["source"] == "title_inference"
    assert exp["required"] == "Unknown"


def test_apply_never_downgrades():
    rec = {"Experience Source": "api_field",
           "Experience Required": "3+ years",
           "Experience Min Years": 3,
           "Experience Level": "Senior"}
    hit = career_mod.apply_experience_to_record(
        rec, jd_text="no experience needed", title="Junior Dev")
    # Weaker source (detail text) cannot overwrite api_field.
    assert rec["Experience Required"] == "3+ years"
    assert rec["Experience Level"] == "Senior"


def test_level_hint_beats_inference():
    rec = {}
    career_mod.apply_experience_to_record(
        rec, jd_text="some experience preferred", title="Dev",
        level_hint="Mid-Senior level")
    assert rec["Experience Level"] == "Senior"
    assert rec["Experience Source"] == "api_field"


def test_row_to_job_maps_experience_columns():
    from sponsorscout.scanning.pipeline import _row_to_job
    from sponsorscout.tests.test_pipeline import _career_row
    row = _career_row(**{"Experience Required": "5-8 years",
                         "Experience Min Years": 5,
                         "Experience Level": "Senior",
                         "Experience Source": "api_description"})
    job = _row_to_job(row, run_id="R1")
    assert job["experience_level"] == "Senior"
    assert job["experience_required"] == "5-8 years"
    assert job["experience_min_years"] == 5.0
    assert job["experience_source"] == "api_description"


def test_persistence_roundtrip(tmp_path):
    from sponsorscout.core import persistence
    from sponsorscout.db import database
    db_path = _make_jobs_db(tmp_path)
    job = {
        "url": "https://x/j1", "title": "Dev", "company": "Acme",
        "external_id": "acme-1", "experience_level": "Senior",
        "experience_required": "5-8 years", "experience_min_years": 5.0,
        "experience_source": "api_description",
    }
    conn = database.get_connection(db_path)
    try:
        persistence.upsert_job(conn, job)
    finally:
        conn.close()
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT experience_required, experience_min_years,"
                       " experience_source, experience_level FROM jobs").fetchone()
    conn.close()
    assert row == ("5-8 years", 5.0, "api_description", "Senior")


def test_persistence_never_downgrades_experience(tmp_path):
    """Re-ingesting a row with no experience data must keep the stored value."""
    from sponsorscout.core import persistence
    from sponsorscout.db import database
    db_path = _make_jobs_db(tmp_path)
    conn = database.get_connection(db_path)
    try:
        persistence.upsert_job(conn, {"url": "https://x/j1", "title": "Dev",
                                      "company": "Acme",
                                      "experience_required": "5-8 years",
                                      "experience_min_years": 5.0,
                                      "experience_source": "api_description",
                                      "experience_level": "Senior"})
        # Second sighting of the same URL carries no experience evidence.
        persistence.upsert_job(conn, {"url": "https://x/j1", "title": "Dev",
                                      "company": "Acme"})
    finally:
        conn.close()
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT experience_required, experience_min_years,"
                       " experience_source FROM jobs").fetchone()
    conn.close()
    assert row == ("5-8 years", 5.0, "api_description")


def test_scanner_vocabulary_is_canonicalised_on_ingest():
    """Scanner words must land as the app vocabulary (db.EXPERIENCE_LEVELS).

    The scanners emit employer-facing words (Internship/Junior/Executive) but
    the jobs table and its filter use Intern/Entry/.../Exec.  Mapping happens
    once, in ``_row_to_job``, so the Experience filter can never miss a row.
    """
    from sponsorscout.db.database import EXPERIENCE_LEVELS
    from sponsorscout.scanning.pipeline import _row_to_job
    from sponsorscout.tests.test_pipeline import _career_row
    canon = set(EXPERIENCE_LEVELS) - {"All", "Any (incl. unknown)"}
    for scanner_word, expected in (("Internship", "Intern"),
                                   ("Junior", "Entry"),
                                   ("Mid", "Mid"),
                                   ("Senior", "Senior"),
                                   ("Lead", "Lead"),
                                   ("Executive", "Exec")):
        job = _row_to_job(
            _career_row(**{"Experience Level": scanner_word}), run_id="R1")
        assert job["experience_level"] == expected, scanner_word
        assert job["experience_level"] in canon, scanner_word


def test_unclassifiable_experience_stored_blank():
    """'Unknown' becomes '' for both the level (enum) and the text column.

    The required-text column is free-form (it holds "5-8 years", "None
    required", ...), so only the literal placeholder is scrubbed.
    """
    from sponsorscout.scanning.pipeline import _row_to_job
    from sponsorscout.tests.test_pipeline import _career_row
    for raw in ("", "Unknown"):
        job = _row_to_job(_career_row(**{
            "Experience Level": raw,
            "Experience Required": raw,
            "Experience Min Years": "",
            "Experience Source": "title_inference"}), run_id="R1")
        assert job["experience_level"] == "", raw
        assert job["experience_required"] == "", raw
        assert job["experience_min_years"] is None, raw


def test_out_of_vocabulary_level_dropped_but_text_kept():
    """An unknown level word is dropped; free-text detail is preserved."""
    from sponsorscout.scanning.pipeline import _row_to_job
    from sponsorscout.tests.test_pipeline import _career_row
    job = _row_to_job(_career_row(**{"Experience Level": "Wizard",
                                     "Experience Required": "Wizard"}),
                      run_id="R1")
    assert job["experience_level"] == ""
    assert job["experience_required"] == "Wizard"


def test_experience_none_required_is_kept():
    """'None required' is a real fact, unlike 'Unknown' — never dropped."""
    from sponsorscout.scanning.pipeline import _row_to_job
    from sponsorscout.tests.test_pipeline import _career_row
    job = _row_to_job(_career_row(**{"Experience Required": "None required",
                                     "Experience Min Years": 0}), run_id="R1")
    assert job["experience_required"] == "None required"
    assert job["experience_min_years"] == 0.0


def test_junk_min_years_becomes_none():
    """A non-numeric 'Experience Min Years' must not raise on ingest."""
    from sponsorscout.scanning.pipeline import _row_to_job
    from sponsorscout.tests.test_pipeline import _career_row
    job = _row_to_job(_career_row(**{"Experience Min Years": "n/a"}),
                      run_id="R1")
    assert job["experience_min_years"] is None
