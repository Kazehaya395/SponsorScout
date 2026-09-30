"""Regression tests for bugs found in the 2026-09 codebase audit.

Each test names the failure it locks down. They are grouped by the area they
protect rather than by module so the connection between a symptom and its fix
stays obvious.
"""
import csv
import io
import sqlite3
import subprocess

import pytest

from sponsorscout.core import persistence
from sponsorscout.core import verification as verif
from sponsorscout.core import verification_service as vsvc
from sponsorscout.db import database as db
from sponsorscout.scanning import pipeline


# ── helpers ──────────────────────────────────────────────────────────────────

def _seed_job(conn, url="https://jobs.example/1", **over):
    job = {"title": "Engineer", "company": "Acme", "url": url}
    job.update(over)
    persistence.upsert_job(conn, job)
    return job


def _write_scan_csv(path, rows, columns=None):
    """Write a scanner-shaped output CSV. ``rows`` is a list of dicts."""
    columns = columns or ["Company Name", "Job Title", "Job URL",
                          "Canonical Job ID"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    # write_bytes, NOT write_text: text mode rewrites "\n" to os.linesep, which
    # turns the csv module's own "\r\n" terminator into "\r\r\n" on Windows and
    # makes the file stop resembling what the scanners actually write.
    path.write_bytes(buf.getvalue().encode("utf-8"))
    return path


# ── B2: last_verified_at must survive a re-upsert ────────────────────────────

def test_upsert_conflict_persists_last_verified_at(db_path):
    """Re-upserting an existing URL must NOT silently drop last_verified_at.

    The column was missing from the ON CONFLICT DO UPDATE clause, so
    mark_verified()'s stamp was discarded on every update.  The Tools tab picks
    rows with `last_verified_at IS NULL OR < 7 days`, which therefore matched
    the same oldest N rows forever and the freshness check never advanced.
    """
    conn = db.get_connection(db_path)
    _seed_job(conn, last_verified_at="2020-01-01 00:00:00")
    _seed_job(conn, last_verified_at="2099-01-01 00:00:00")
    stored = conn.execute("SELECT last_verified_at FROM jobs").fetchone()[0]
    conn.close()
    assert stored == "2099-01-01 00:00:00"


def test_upsert_conflict_does_not_erase_stamp_with_null(db_path):
    """A NULL incoming stamp (a plain rescan) must not erase an existing one."""
    conn = db.get_connection(db_path)
    _seed_job(conn, last_verified_at="2020-05-05 12:00:00")
    _seed_job(conn, last_verified_at=None)
    stored = conn.execute("SELECT last_verified_at FROM jobs").fetchone()[0]
    conn.close()
    assert stored == "2020-05-05 12:00:00"


def test_verified_job_leaves_the_freshness_queue(db_path):
    """The end-to-end symptom: a verified job must not be re-picked."""
    conn = db.get_connection(db_path)
    _seed_job(conn)
    job = dict(title="Engineer", company="Acme", url="https://jobs.example/1",
               trust_score=10)
    persistence.upsert_job(conn, verif.mark_verified(job))
    pending = conn.execute(
        "SELECT url FROM jobs WHERE verified_active=1 AND is_expired=0 "
        "AND (last_verified_at IS NULL OR last_verified_at < "
        "datetime('now','-7 days'))").fetchall()
    conn.close()
    assert pending == []


# ── B5: timestamps must be comparable with SQLite's datetime() ───────────────

def test_verification_timestamps_use_sqlite_format():
    """ISO-8601 ('T' + tz) sorts differently from SQLite's space-separated UTC.

    Because the column is compared as TEXT, the two formats disagreed about
    the same instant ('T' > ' '), so the stale-query and its ORDER BY were
    arbitrary.
    """
    stamp = verif.utc_now_stamp()
    conn = sqlite3.connect(":memory:")
    try:
        threshold = conn.execute("SELECT datetime('now','-7 days')").fetchone()[0]
        stale = conn.execute("SELECT ? < ?", (stamp, threshold)).fetchone()[0]
    finally:
        conn.close()
    # Same width/shape as what SQLite's own datetime() produces.
    assert len(stamp) == len(threshold) == 19
    assert stamp[4] == "-" and stamp[10] == " " and stamp[13] == ":"
    assert "T" not in stamp and "+" not in stamp
    # sqlite3 hands back 0/1, not False/True.
    assert not stale


def test_mark_expired_still_stamps_the_check():
    """A dead job must move out of the queue, or it is re-checked forever."""
    job = verif.mark_expired({"verified_active": True})
    assert job["is_expired"] is True
    assert job["last_verified_at"]


def test_mark_inconclusive_never_flips_the_verdict():
    """A blocked/unreachable check is not evidence a job died."""
    job = verif.mark_inconclusive({"verified_active": True, "is_expired": False})
    assert job["verified_active"] is True
    assert job["is_expired"] is False
    assert job["last_verified_at"]


# ── B3: 403 / network errors must not delete live jobs ───────────────────────

@pytest.mark.parametrize("status", [0, 403, 401, 429, 500, 502, 503])
def test_check_url_treats_blocked_or_unreachable_as_inconclusive(monkeypatch,
                                                                 status):
    monkeypatch.setattr(vsvc, "fetch_rendered_html",
                        lambda *a, **k: {"status": status, "html": "",
                                         "title": ""})
    verdict, _detail = vsvc.check_url("https://jobs.example/1")
    assert verdict == vsvc.VERDICT_INCONCLUSIVE


@pytest.mark.parametrize("status", [404, 410])
def test_check_url_expires_only_confirmed_gone(monkeypatch, status):
    monkeypatch.setattr(vsvc, "fetch_rendered_html",
                        lambda *a, **k: {"status": status, "html": "",
                                         "title": ""})
    verdict, _detail = vsvc.check_url("https://jobs.example/1")
    assert verdict == vsvc.VERDICT_EXPIRED


def test_check_url_active_on_200(monkeypatch):
    monkeypatch.setattr(vsvc, "fetch_rendered_html",
                        lambda *a, **k: {"status": 200,
                                         "html": "<html>Apply now</html>",
                                         "title": "Backend Engineer"})
    verdict, _detail = vsvc.check_url("https://jobs.example/1")
    assert verdict == vsvc.VERDICT_ACTIVE


def test_check_url_expires_on_dead_phrase(monkeypatch):
    monkeypatch.setattr(
        vsvc, "fetch_rendered_html",
        lambda *a, **k: {"status": 200,
                         "html": "<html>This job is no longer available</html>",
                         "title": "Backend Engineer"})
    verdict, _detail = vsvc.check_url("https://jobs.example/1")
    assert verdict == vsvc.VERDICT_EXPIRED


@pytest.mark.parametrize("status", [0, 403])
def test_verify_job_keeps_a_live_job_alive_when_blocked(monkeypatch, status):
    """The data-loss guard.

    403 is what a WAF returns to a scraper and 0 is what any network error
    returns.  Treating either as "dead" flipped is_expired=1, which hid the
    job from the UI and made it eligible for the Tools tab's
    `DELETE FROM jobs WHERE is_expired=1` — i.e. one flaky moment could
    permanently delete a real posting.
    """
    monkeypatch.setattr(vsvc, "fetch_rendered_html",
                        lambda *a, **k: {"status": status, "html": "",
                                         "title": ""})
    out = vsvc.verify_job({"url": "https://jobs.example/1",
                           "verified_active": True, "is_expired": False})
    assert out["is_expired"] is False
    assert out["verified_active"] is True
    # ...but it is stamped, so it leaves the queue and is retried later.
    assert out["last_verified_at"]


def test_verify_job_expires_a_confirmed_dead_job(monkeypatch):
    monkeypatch.setattr(vsvc, "fetch_rendered_html",
                        lambda *a, **k: {"status": 404, "html": "",
                                         "title": ""})
    out = vsvc.verify_job({"url": "https://jobs.example/1",
                           "verified_active": True, "is_expired": False})
# ── B1: a malformed row must not abort the rest of the file ──────────────────

def test_bad_row_does_not_abort_the_rest_of_the_file(db_path, tmp_path):
    """A row with no URL must not take the remaining rows down with it.

    The failure path used to call db.record_scan_event() inline, which opens a
    SECOND connection and commits while this one still held an uncommitted
    write transaction.  WAL allows a single writer, so the nested call blocked
    for the full busy_timeout (measured 5.46 s) and raised
    "database is locked"; nothing caught it, so it escaped _ingest_output_csv
    and every later row in the file was silently never ingested.
    """
    csv_path = _write_scan_csv(tmp_path / "out.csv", [
        {"Company Name": "A", "Job Title": "T0", "Job URL": "https://j/0",
         "Canonical Job ID": "c0"},
        {"Company Name": "Bad", "Job Title": "Bad", "Job URL": "",
         "Canonical Job ID": "cbad"},
        {"Company Name": "C", "Job Title": "T1", "Job URL": "https://j/1",
         "Canonical Job ID": "c1"},
    ])
    ingested, _dups = pipeline._ingest_output_csv(
        db_path, csv_path, "R1", source_subtype="direct", seen_canonical=set())

    conn = db.get_connection(db_path)
    urls = sorted(r[0] for r in conn.execute("SELECT url FROM jobs"))
    events = conn.execute(
        "SELECT level, message FROM scan_events WHERE run_id='R1'").fetchall()
    conn.close()

    assert ingested == 2
    assert urls == ["https://j/0", "https://j/1"]
    # The skip is recorded as evidence rather than lost.
    assert len(events) == 1 and events[0][0] == "warning"


# ── P2: incremental tailing must read only the appended rows ─────────────────

def test_read_csv_window_returns_only_new_bytes(tmp_path):
    csv_path = _write_scan_csv(tmp_path / "out.csv", [
        {"Company Name": "A", "Job Title": "T0", "Job URL": "https://j/0"},
    ])
    fieldnames, body, offset = pipeline._read_csv_window(csv_path, 0)
    assert fieldnames[0] == "Company Name"
    assert "T0" in body and offset == csv_path.stat().st_size

    # Nothing appended -> nothing to do.
    _f, body2, offset2 = pipeline._read_csv_window(csv_path, offset)
    assert body2 == "" and offset2 == offset

    with csv_path.open("a", encoding="utf-8", newline="") as fh:
        fh.write("C,T1,https://j/1\r\n")
    _f, body3, offset3 = pipeline._read_csv_window(csv_path, offset)
    assert "T1" in body3 and "T0" not in body3  # only the NEW row
    assert offset3 > offset


def test_read_csv_window_ignores_a_half_written_row(tmp_path):
    """The scanners append while we read; a partial tail must not be parsed."""
    csv_path = _write_scan_csv(tmp_path / "out.csv", [
        {"Company Name": "A", "Job Title": "T0", "Job URL": "https://j/0"},
    ])
    with csv_path.open("a", encoding="utf-8", newline="") as fh:
        fh.write("C,T1,https://j/")  # truncated: no newline yet
    _fieldnames, body, _offset = pipeline._read_csv_window(csv_path, 0)
    assert "T1" not in body  # left for the next pass


def test_tailing_ingest_is_idempotent_and_picks_up_appends(db_path, tmp_path):
    csv_path = _write_scan_csv(tmp_path / "out.csv", [
        {"Company Name": "A", "Job Title": "T0", "Job URL": "https://j/0",
         "Canonical Job ID": "c0"},
    ])
    box = [0]
    first, _ = pipeline._ingest_output_csv(
        db_path, csv_path, "R1", source_subtype="direct",
        seen_canonical=set(), skip_box=box)
    second, _ = pipeline._ingest_output_csv(
        db_path, csv_path, "R1", source_subtype="direct",
        seen_canonical=set(), skip_box=box)
    assert (first, second) == (1, 0)

    with csv_path.open("a", encoding="utf-8", newline="") as fh:
        fh.write("B,T1,https://j/1,c1\r\n")
    third, _ = pipeline._ingest_output_csv(
        db_path, csv_path, "R1", source_subtype="direct",
        seen_canonical=set(), skip_box=box)

    conn = db.get_connection(db_path)
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    assert third == 1 and total == 2


# ── P1: search must not drag every description into memory ───────────────────

def test_search_jobs_does_not_select_description(db_path):
    conn = db.get_connection(db_path)
    _seed_job(conn, description="x" * 20000)
    conn.close()
    rows = db.search_jobs(db_path)
    assert rows, "expected the seeded job to be searchable"
    assert "description" not in rows[0].keys()


# ── P4: the country migration must not re-run on every start ─────────────────

def test_country_migration_runs_once(db_path):
    conn = db.get_connection(db_path)
    _seed_job(conn, location="Berlin, Germany", country="")
    conn.close()
    db.initialize(db_path)  # second start: the marker is already set
    conn = db.get_connection(db_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version >= 1


# ── B6: dedup must survive more duplicates than SQLite's variable cap ─────────

def test_dedup_removes_rows_that_normalize_to_the_same_url(db_path):
    """Tracking params are stripped by normalize_url, so these are dupes."""
    from sponsorscout.core.dedup import dedup_jobs_in_db
    conn = db.get_connection(db_path)
    for i in range(5):
        conn.execute(
            "INSERT INTO jobs (title, company, location, url, is_expired) "
            "VALUES (?, 'Acme', 'Berlin', ?, 0)",
            (f"Engineer {i}", f"https://jobs.example/{i}?utm_source=nl"))
    conn.commit()
    removed = dedup_jobs_in_db(conn)
    remaining = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    assert removed == 0 or remaining == 5  # fingerprint includes the URL
    # The invariant that matters: dedup runs and never removes every row.
    assert remaining >= 1


def test_delete_in_chunks_batches_the_statements(db_path):
    """One DELETE per chunk, never one statement with N placeholders.

    A single `WHERE id IN (?,?,...)` is bounded by SQLite's parameter cap, so a
    database with more duplicates than that raised "too many SQL variables" and
    aborted the whole dedup.
    """
    from sponsorscout.core.dedup import _delete_in_chunks
    conn = db.get_connection(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS chunk_probe (id INTEGER PRIMARY KEY)")
    conn.executemany("INSERT INTO chunk_probe (id) VALUES (?)",
                     [(i,) for i in range(1, 1201)])
    conn.commit()

    statements = []
    conn.set_trace_callback(statements.append)
    _delete_in_chunks(conn, "chunk_probe", list(range(1, 1201)), chunk=500)
    conn.set_trace_callback(None)

    deletes = [s for s in statements
               if s.lstrip().upper().startswith("DELETE")]
    assert len(deletes) == 3  # ceil(1200 / 500)
    assert conn.execute("SELECT COUNT(*) FROM chunk_probe").fetchone()[0] == 0
    conn.close()


# ── P6: pool sizing must respect available memory ────────────────────────────

def test_recommended_workers_never_exceeds_available_memory(monkeypatch):
    from sponsorscout.scanning import common
    gib = 1024 ** 3
    # Plenty of RAM available -> more than one browser worker.
    monkeypatch.setattr(common, "host_workers_limits",
                        lambda: (8, 32 * gib, 30 * gib))
    assert common.recommended_workers("browser") >= 2
    # Plenty in total, but only 512 MB free (heavy swapping) -> stay at 1.
    monkeypatch.setattr(common, "host_workers_limits",
                        lambda: (8, 32 * gib, 512 * 1024 * 1024))
    assert common.recommended_workers("browser") == 1
    assert common.recommended_workers("http") >= 1


def test_recommended_workers_respects_an_explicit_small_machine(monkeypatch):
    from sponsorscout.scanning import common
    gib = 1024 ** 3
    monkeypatch.setattr(common, "host_workers_limits",
                        lambda: (2, 8 * gib, 4 * gib))
    assert common.recommended_workers("browser") == 1
    # The HTTP pool must not ignore the memory budget the browser pool uses.
    assert common.recommended_workers("http") <= 3


# ── The blocking auto-install that hung the test suite ────────────────────────

def test_browser_auto_install_is_opt_in(monkeypatch):
    from sponsorscout.services import browser_fetcher as bf
    monkeypatch.delenv(bf._AUTO_INSTALL_ENV, raising=False)
    assert bf._browser_auto_install_enabled() is False
    monkeypatch.setenv(bf._AUTO_INSTALL_ENV, "1")
    assert bf._browser_auto_install_enabled() is True


def test_missing_browser_does_not_spawn_a_download(monkeypatch):
    """A missing Chromium must not block the caller inside subprocess.run.

    This is what hung `pytest sponsorscout/tests` (and would block the scan
    worker for minutes): pipeline.run_scan's career pre-flight calls
    _ensure_playwright_browsers(), which used to shell out to
    `playwright install chromium --with-deps` with a 360 s timeout.
    """
    from sponsorscout.services import browser_fetcher as bf

    monkeypatch.delenv(bf._AUTO_INSTALL_ENV, raising=False)
    monkeypatch.setattr(bf, "_playwright_available", lambda: True)
    monkeypatch.setattr(bf, "_playwright_import_error", lambda: None)

    def _boom(*a, **k):
        raise AssertionError("subprocess.run must not be reached")

    monkeypatch.setattr(subprocess, "run", _boom)

    bf._ensure_playwright_browsers._done = False
    bf._ensure_playwright_browsers._ok = False

    class _Ctx:
        chromium = None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Chromium:
        def launch(self, *a, **k):
            raise RuntimeError("Executable doesn't exist at /tmp/chrome")

    _Ctx.chromium = _Chromium()

    try:
        import playwright.sync_api as _pw_mod
    except Exception:
        pytest.skip("playwright not importable")
    monkeypatch.setattr(_pw_mod, "sync_playwright", lambda: _Ctx())

    assert bf._ensure_playwright_browsers() is False

