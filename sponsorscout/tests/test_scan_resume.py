"""Scan resume tests (Stop-as-checkpoint: no lost progress).

Stopping a scan keeps every finished company in the DB (live ingestion +
scan_log rows); resuming scans only the remainder under a fresh run_id and
re-ingestion upserts idempotently on the canonical URL key.
"""
import pytest

from sponsorscout.application import seed_manager
from sponsorscout.db import database as db
from sponsorscout.scanning.pipeline import _ScanProgress


@pytest.fixture()
def resume_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "resume.db")
    db.initialize(db_path)
    ats = tmp_path / "ats.csv"
    career = tmp_path / "career.csv"
    ats.write_text(
        "name,careers_url,provider,board_slug,source_type,target_country,"
        "scope_policy,industry,notes\n"
        "A1,https://a1.example.com,greenhouse,,direct,Global,global,,,\n"
        "A2,https://a2.example.com,ashby,,direct,Global,global,,,\n",
        encoding="utf-8")
    career.write_text(
        "name,careers_url,provider,board_slug,source_type,target_country,"
        "scope_policy,industry,notes\n"
        "C1,https://c1.example.com,auto,,direct,Global,global,,,\n"
        "C2,https://c2.example.com,auto,,direct,Global,global,,,\n"
        "C3,https://c3.example.com,auto,,direct,Global,global,,,\n",
        encoding="utf-8")
    monkeypatch.setattr(seed_manager, "user_ats_path", lambda: ats)
    monkeypatch.setattr(seed_manager, "user_career_path", lambda: career)
    return db_path


def _log(db_path, run_id, scanner, company):
    db.record_scan_log_rows(db_path, run_id, scanner, [{
        "seed_name": company, "company": company, "source_type": "direct",
        "target_country": "Global", "status": "ok", "provider": "x",
        "jobs_found": 1, "quarantined": 0, "duplicates": 0,
        "rejected_scope": 0, "error": "", "diagnostics": "",
        "duration_sec": 1.0, "seed_url": "https://example.com",
    }])


def test_completed_companies_split_by_phase(resume_db):
    _log(resume_db, "R1", "ats", "A1")
    _log(resume_db, "R1", "career", "C1")
    done = db.get_completed_scan_companies(resume_db, "R1")
    assert done == {"ats": {"a1"}, "career": {"c1"}}


def test_resumable_checkpoint_lists_only_remaining(resume_db):
    db.start_scan_run(resume_db, "R1", "full", 2, 3)
    _log(resume_db, "R1", "ats", "A1")
    _log(resume_db, "R1", "ats", "A2")  # ATS phase fully done
    _log(resume_db, "R1", "career", "C1")
    db.finish_scan_run(resume_db, "R1", targets_ok=3, status="cancelled")
    cp = db.get_resumable_scan(resume_db)
    assert cp is not None and cp["run_id"] == "R1"
    assert cp["remaining_ats"] == []
    assert sorted(cp["remaining_career"]) == ["C2", "C3"]
    assert (cp["done_ats"], cp["done_career"]) == (2, 1)
    assert (cp["total_ats"], cp["total_career"]) == (2, 3)


def test_no_resume_when_nothing_stopped_or_all_done(resume_db):
    assert db.get_resumable_scan(resume_db) is None
    db.start_scan_run(resume_db, "R2", "full", 1, 1)
    db.finish_scan_run(resume_db, "R2", targets_ok=0, status="completed")
    assert db.get_resumable_scan(resume_db) is None


def test_seed_added_after_stop_joins_remaining(resume_db, tmp_path):
    db.start_scan_run(resume_db, "R1", "full", 2, 3)
    _log(resume_db, "R1", "ats", "A1")
    _log(resume_db, "R1", "ats", "A2")
    db.finish_scan_run(resume_db, "R1", targets_ok=2, status="cancelled")
    # User adds a company to the career seed after stopping.
    with open(seed_manager.user_career_path(), "a", encoding="utf-8") as f:
        f.write("C4,https://c4.example.com,auto,,direct,Global,global,,,\n")
    cp = db.get_resumable_scan(resume_db)
    assert sorted(cp["remaining_career"]) == ["C1", "C2", "C3", "C4"]
    assert cp["added_since_stop"] == 1


def test_resume_link_parsers():
    assert db.parse_resume_link("") == ""
    assert db.parse_resume_link("ATS: TimeoutError: x") == ""
    assert db.parse_resume_link("resumed_from:R1") == "R1"
    assert db.parse_resume_link(
        "Career: ValueError: boom; resumed_from:R2") == "R2"
    assert db.parse_resumed_by("") == ""
    assert db.parse_resumed_by("resumed_by:C9") == "C9"
    assert db.parse_resumed_by(
        "ATS: TimeoutError: x; resumed_by:C9") == "C9"


def test_parent_promoted_when_child_covers_remainder(resume_db):
    db.start_scan_run(resume_db, "R1", "full", 2, 3)
    _log(resume_db, "R1", "ats", "A1")
    _log(resume_db, "R1", "ats", "A2")
    _log(resume_db, "R1", "career", "C1")
    db.finish_scan_run(resume_db, "R1", targets_ok=3, status="cancelled")
    # Resume child finishes the rest.
    db.start_scan_run(resume_db, "C9", "full", 0, 2)
    _log(resume_db, "C9", "career", "C2")
    _log(resume_db, "C9", "career", "C3")
    db.finish_scan_run(resume_db, "C9", targets_ok=2, status="completed",
                       error="resumed_from:R1")
    assert db.mark_scan_resumed(resume_db, "R1", "C9") is True
    parent = db.get_scan_run(resume_db, "R1")
    assert parent[4] == "resumed"  # status column
    assert db.parse_resumed_by(parent[5]) == "C9"
    # Fully covered parent is no longer resumable.
    assert db.get_resumable_scan(resume_db) is None


def test_parent_kept_when_child_stopped_early(resume_db):
    db.start_scan_run(resume_db, "R1", "full", 2, 3)
    _log(resume_db, "R1", "ats", "A1")
    _log(resume_db, "R1", "ats", "A2")
    db.finish_scan_run(resume_db, "R1", targets_ok=2, status="cancelled")
    # Child stopped after one company — remainder still open.
    db.start_scan_run(resume_db, "C9", "full", 0, 3)
    _log(resume_db, "C9", "career", "C1")
    db.finish_scan_run(resume_db, "C9", targets_ok=1, status="cancelled",
                       error="resumed_from:R1")
    assert db.mark_scan_resumed(resume_db, "R1", "C9") is False
    assert db.get_scan_run(resume_db, "R1")[4] == "cancelled"
    # Newest stopped run with remainder (the child) is what resumes next.
    cp = db.get_resumable_scan(resume_db)
    assert cp is not None and cp["run_id"] == "C9"


def test_completed_parent_never_promoted(resume_db):
    db.start_scan_run(resume_db, "R1", "full", 1, 0)
    _log(resume_db, "R1", "ats", "A1")
    db.finish_scan_run(resume_db, "R1", targets_ok=1, status="completed")
    assert db.mark_scan_resumed(resume_db, "R1", "C9") is False
    assert db.get_scan_run(resume_db, "R1")[4] == "completed"


def test_progress_offset_continues_from_checkpoint():
    # 2/5 done before the stop; the resume run scans 3 more with the
    # overall total so the bar goes 2/5 -> 5/5, never restarting at 0.
    got = []
    wrapped = _ScanProgress(1, 2, base_ats=2, base_career=0).wrap(got.append)
    wrapped("   OK: wrote=1, quarantined=0, dups=0")
    wrapped("   OK C2: wrote=1, quarantined=0, dups=0, scope_reject=0")
    wrapped("   OK C3: wrote=1, quarantined=0, dups=0, scope_reject=0")
    ticks = [str(m) for m in got if str(m).startswith("PROGRESS:")]
    assert ticks[0] == "PROGRESS: 3/5:ats:ATS 3/3"
    assert ticks[-1] == "PROGRESS: 5/5:career:Career 2/2"


def test_progress_without_resume_unchanged():
    got = []
    _ScanProgress(2, 1).wrap(got.append)(
        "   OK: wrote=1, quarantined=0, dups=0")
    assert got == ["   OK: wrote=1, quarantined=0, dups=0",
                   "PROGRESS: 1/3:ats:ATS 1/2"]
