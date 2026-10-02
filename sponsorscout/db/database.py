from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from sponsorscout.paths import DB_PATH, ensure_user_data_dir

logger = logging.getLogger(__name__)


def _configure_connection(conn, db_path=DB_PATH):
    """Apply standard PRAGMA settings to a fresh sqlite3 connection.

    Centralised so both the raw ``get_connection`` accessor and any future
    context managers configure connections identically.
    """
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # B3 fix: WAL allows concurrent readers but only one writer. Without a
    # busy_timeout, parallel scanners would fail with "database is locked"
    # the moment two threads tried to commit at the same time.
    conn.execute("PRAGMA busy_timeout=5000")
    # Performance: NORMAL is safe with WAL; reduces fsync overhead.
    conn.execute("PRAGMA synchronous=NORMAL")
    # Performance: 8 MiB page cache (SQLite default is 2 MiB). The
    # Dashboard's COUNT/GROUP BY refreshes and full-table LIKE scans reuse
    # cached pages instead of re-reading them from disk on every query.
    conn.execute("PRAGMA cache_size=-8000")
    return conn


def _regexp_like(pattern, value):
    """SQLite REGEXP operator backed by Python's re.
    Returns 1 if value matches pattern, else 0. Invalid patterns match nothing
    (the caller validates and falls back to LIKE on invalid input).

    Case-insensitive on purpose: the non-regex path (LIKE on lower(...)) is
    case-insensitive too, so enabling the Regex checkbox must never narrow the
    result set (pattern 'berlin' must still match 'Berlin'; otherwise jobs
    would be missed by the very filter meant to find them).  Patterns that need
    case sensitivity can use explicit inline flags, e.g. '(?-i:Berlin)'.
    """
    import re
    if value is None:
        return 0
    try:
        return 1 if re.search(pattern, str(value), re.IGNORECASE) else 0
    except re.error:
        return 0


_user_dir_ready = False


def get_connection(db_path=DB_PATH):
    global _user_dir_ready
    db_path = Path(db_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # ensure_user_data_dir() is per-process work; running it on EVERY
    # connection open added filesystem syscalls to every query. Tests that
    # redirect the data dir re-import this module, which resets the flag.
    if not _user_dir_ready:
        ensure_user_data_dir()
        _user_dir_ready = True
    conn = _configure_connection(sqlite3.connect(str(db_path), timeout=30.0))
    conn.create_function("REGEXP", 2, _regexp_like)
    return conn


def _apply_migrations(conn):
    """Apply schema migrations safely (idempotent via column existence checks)."""
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    migrations = [
        ("remote_type", "ALTER TABLE jobs ADD COLUMN remote_type TEXT DEFAULT 'onsite'"),
        ("eu_blue_card", "ALTER TABLE jobs ADD COLUMN eu_blue_card INTEGER DEFAULT 0"),
        ("has_relocation", "ALTER TABLE jobs ADD COLUMN has_relocation INTEGER DEFAULT 0"),
        # BUGFIX: support the new "Experience" filter (v0.1.1).
        ("experience_level", "ALTER TABLE jobs ADD COLUMN experience_level TEXT DEFAULT ''"),
        # FIX P0-30: experience requirement columns from the scanners.
        ("experience_required", "ALTER TABLE jobs ADD COLUMN experience_required TEXT DEFAULT ''"),
        ("experience_min_years", "ALTER TABLE jobs ADD COLUMN experience_min_years REAL"),
        ("experience_source", "ALTER TABLE jobs ADD COLUMN experience_source TEXT DEFAULT ''"),
        # BUGFIX: support Phase 3 source subtype migration
        ("source_subtype", "ALTER TABLE jobs ADD COLUMN source_subtype TEXT DEFAULT 'direct'"),
        # NEW: industry tag sourced from company registry (v0.2.0)
        ("industry", "ALTER TABLE jobs ADD COLUMN industry TEXT DEFAULT ''"),
        # AI domain detection score (v0.2.1)
        ("ai_score", "ALTER TABLE jobs ADD COLUMN ai_score INTEGER DEFAULT 0"),
        # ── Scan evidence columns (PySide6 restart migration) ────────────────
        ("visa_sponsorship", "ALTER TABLE jobs ADD COLUMN visa_sponsorship TEXT DEFAULT ''"),
        ("relocation_support", "ALTER TABLE jobs ADD COLUMN relocation_support TEXT DEFAULT ''"),
        ("eu_blue_card_verdict", "ALTER TABLE jobs ADD COLUMN eu_blue_card_verdict TEXT DEFAULT ''"),
        ("relocation_required", "ALTER TABLE jobs ADD COLUMN relocation_required TEXT DEFAULT ''"),
        ("support_confidence", "ALTER TABLE jobs ADD COLUMN support_confidence REAL DEFAULT 0"),
        ("support_evidence", "ALTER TABLE jobs ADD COLUMN support_evidence TEXT DEFAULT ''"),
        ("support_evidence_url", "ALTER TABLE jobs ADD COLUMN support_evidence_url TEXT DEFAULT ''"),
        ("support_evidence_type", "ALTER TABLE jobs ADD COLUMN support_evidence_type TEXT DEFAULT ''"),
        ("blue_card_evidence", "ALTER TABLE jobs ADD COLUMN blue_card_evidence TEXT DEFAULT ''"),
        ("canonical_job_id", "ALTER TABLE jobs ADD COLUMN canonical_job_id TEXT DEFAULT ''"),
        ("run_id", "ALTER TABLE jobs ADD COLUMN run_id TEXT DEFAULT ''"),
        # Raw (un-normalised) location string for future re-derivation (F6),
        # and the auto/manual provenance flag protecting user corrections.
        ("raw_location", "ALTER TABLE jobs ADD COLUMN raw_location TEXT DEFAULT ''"),
        ("country_source", "ALTER TABLE jobs ADD COLUMN country_source TEXT DEFAULT 'auto'"),
    ]
    for col, sql in migrations:
        if col not in existing_cols:
            try:
                conn.execute(sql)
            except Exception as exc:
                logger.exception("Failed to apply migration for column %s", col)
                raise

    # BUGFIX: re-read the column set AFTER the ALTERs above.  The snapshot
    # taken at the top of this function predates the migration loop, so on a
    # legacy database that was missing these columns the ALTER added them but
    # the index guards below (which tested the stale snapshot) skipped the
    # CREATE entirely — the index was then silently never created, on every
    # subsequent launch too.  Both columns are (re)added by the loop above, so
    # re-reading here is always correct and stays idempotent.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}

    # New tables and indexes that depend on migration-added columns.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scan_runs (
            run_id TEXT PRIMARY KEY,
            method TEXT DEFAULT '',
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT,
            targets_ok INTEGER DEFAULT 0,
            targets_empty INTEGER DEFAULT 0,
            targets_error INTEGER DEFAULT 0,
            jobs_found INTEGER DEFAULT 0,
            jobs_quarantined INTEGER DEFAULT 0,
            jobs_duplicates INTEGER DEFAULT 0,
            ats_companies INTEGER DEFAULT 0,
            career_companies INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running',
            error TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS scan_log (
            id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL,
            scanner TEXT DEFAULT '',
            seed_name TEXT DEFAULT '',
            company TEXT DEFAULT '',
            source_type TEXT DEFAULT '',
            target_country TEXT DEFAULT '',
            status TEXT DEFAULT '',
            provider TEXT DEFAULT '',
            jobs_found INTEGER DEFAULT 0,
            quarantined INTEGER DEFAULT 0,
            duplicates INTEGER DEFAULT 0,
            rejected_scope INTEGER DEFAULT 0,
            error TEXT DEFAULT '',
            diagnostics TEXT DEFAULT '',
            duration_sec REAL DEFAULT 0,
            seed_url TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_scan_log_run ON scan_log(run_id);
        CREATE TABLE IF NOT EXISTS scan_events (
            id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL,
            ts TEXT DEFAULT CURRENT_TIMESTAMP,
            level TEXT DEFAULT 'info',
            phase TEXT DEFAULT 'pipeline',
            company TEXT DEFAULT '',
            message TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_scan_events_run ON scan_events(run_id);
    """)

    # ── Legacy tables removed with the Tkinter→PySide6 restart ──────────────
    # AI assets (AI features removed per project decision), the discovery
    # engine queue/results, and the connector health table are all obsolete.
    # jobs_fts is dead too: search uses LIKE/REGEXP and nothing writes the FTS
    # index, so it can only ever be empty.
    for legacy in ("user_ai_assets", "company_discovery_queue", "discoveries", "ats_health", "jobs_fts"):
        try:
            conn.execute(f"DROP TABLE IF EXISTS {legacy}")
        except Exception as exc:
            logger.exception("Failed to drop legacy table %s: %s", legacy, exc)

    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}

    if "remote_type" in existing_cols:
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_remote ON jobs(remote_type)")
        except Exception as exc:
            logger.exception("Failed to create idx_jobs_remote: %s", exc)

    if "experience_level" in existing_cols:
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_experience ON jobs(experience_level) WHERE experience_level IS NOT NULL AND experience_level != ''"
            )
        except Exception as exc:
            logger.exception("Failed to create idx_jobs_experience: %s", exc)

    conn.commit()

    # Sanity check that essential migration columns exist.
    expected_cols = {"remote_type", "eu_blue_card", "has_relocation", "experience_level", "source_subtype"}
    missing = expected_cols - existing_cols
    if missing:
        logger.error(
            "Database schema verification failed: missing columns %s",
            sorted(missing),
        )
        raise RuntimeError(
            f"Database initialization failed; missing columns: {', '.join(sorted(missing))}"
        )


def initialize(db_path=DB_PATH):
    conn = get_connection(db_path)
    try:
        # Check if the jobs table already exists on disk
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone()

        # If table exists, run migrations first to ensure all required columns
        # are present before executescript tries to build indexes on them.
        if table_exists:
            _apply_migrations(conn)

        conn.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
        conn.commit()

        # If the table didn't exist before, it has been created by executescript.
        # Now run migrations safely to configure outstanding indices or queues.
        if not table_exists:
            _apply_migrations(conn)

        # Fix country/location mismatch for any existing records.
        #
        # PERFORMANCE: this used to run unconditionally on EVERY app start.
        # migrate_job_countries() does `SELECT id, location, country,
        # country_source FROM jobs` — a full table fetch into Python memory on
        # every launch — and then re-derives the country for every row whose
        # value is empty/"Remote".  On a database with tens of thousands of
        # rows that is a multi-second startup stall, repeated for no benefit
        # once the backlog is cleared.
        #
        # It is a ONE-SHOT repair, so record completion in the database's
        # `user_version` pragma (unused elsewhere in this project) and skip it
        # on subsequent starts.  `force=True` still re-runs it on demand, and
        # deleting the DB resets the flag automatically.
        from sponsorscout.db.migrate_countries import migrate_job_countries
        try:
            _marker = int(
                (conn.execute("PRAGMA user_version").fetchone() or [0])[0] or 0)
        except Exception:
            _marker = 0
        if _marker < 1:
            try:
                migrate_job_countries(conn)
                conn.execute("PRAGMA user_version = 1")
            except Exception:
                logger.exception("Country migration failed; will retry next start")
        conn.commit()
    finally:
        conn.close()


def search_jobs(db_path, title="", company="", location="", country="All", source_type="All",
                verified_only=True, sponsorship_only=False, active_only=True,
                remote_filter="All", eu_blue_card_only=False, relocation_only=False,
                sponsorship_filter="All", blue_card_filter="All", relocation_filter="All",
                experience_filter="All", regex=False):
    conn = None
    try:
        conn = get_connection(db_path)
        # NOTE: `description` is deliberately NOT selected.  It holds a full job
        # description (often 10-50 KB) that no Search-tab cell, tooltip, sort
        # key or column-width measurement ever reads — yet the query has no
        # LIMIT and run_search() materialises EVERY matching row into a dict on
        # the GUI thread before rendering a single page.  Selecting it turned a
        # 50k-job search into tens of MB of throwaway strings on an 8 GB /
        # 2-core machine.
        query = """SELECT title, company, country, location, source_type, source_name,
                   trust_score, freshness_score, sponsorship_score, match_score,
                   verified_active, is_expired, url, last_verified_at,
                   first_seen_at,
                   COALESCE(remote_type, 'onsite') as remote_type,
                   COALESCE(eu_blue_card, 0) as eu_blue_card,
                   COALESCE(has_relocation, 0) as has_relocation,
                   COALESCE(experience_level, '') as experience_level,
                   COALESCE(experience_required, '') as experience_required,
                   COALESCE(experience_min_years, NULL) as experience_min_years,
                   COALESCE(experience_source, '') as experience_source,
                   COALESCE(industry, '') as industry,
                   COALESCE(ai_score, 0) as ai_score,
                   COALESCE(visa_sponsorship, '') as visa_sponsorship,
                   COALESCE(relocation_support, '') as relocation_support,
                   COALESCE(eu_blue_card_verdict, '') as eu_blue_card_verdict
                   FROM jobs WHERE 1=1"""
        params = []

        # Validate regex inputs up front so we can fall back to LIKE on a bad
        # pattern instead of silently matching nothing.
        import re as _re
        if regex:
            for pat in (title, company, location):
                if pat:
                    try:
                        _re.compile(pat)
                    except _re.error:
                        regex = False
                        break

        def _add_text_filter(column, value):
            nonlocal query, params
            if not value:
                return
            if regex:
                query += f" AND {column} REGEXP ?"
                params.append(value)
            else:
                # Escape LIKE wildcards so literal searches can't act as patterns.
                escaped = (value.lower().replace("\\", "\\\\")
                           .replace("%", "\\%").replace("_", "\\_"))
                query += f" AND lower({column}) LIKE ? ESCAPE '\\'"
                params.append(f"%{escaped}%")

        _add_text_filter("title", title)
        _add_text_filter("company", company)
        _add_text_filter("location", location)
        if country and country != "All":
            # Match jobs whose country matches the filter, or remote roles
            # that are explicitly EU/EMEA remote and therefore relevant to the
            # selected country.
            query += (
                " AND (country = ? "
                "OR (country = '' AND remote_type IN ('remote_eu','remote_emea')) )"
            )
            params.append(country)
        if source_type and source_type != "All":
            query += " AND source_type = ?"
            params.append(source_type)
        if verified_only:
            query += " AND verified_active = 1"
        if active_only:
            query += " AND is_expired = 0"
        if sponsorship_only:
            query += " AND sponsorship_score >= 70"
        if remote_filter and remote_filter != "All":
            if remote_filter == "Remote EU":
                query += " AND remote_type = 'remote_eu'"
            elif remote_filter == "Remote EMEA":
                query += " AND remote_type IN ('remote_eu', 'remote_emea')"
            elif remote_filter == "Remote Global":
                query += " AND remote_type IN ('remote_eu', 'remote_emea', 'remote_global', 'remote')"
            elif remote_filter == "Hybrid":
                query += " AND remote_type = 'hybrid'"
            elif remote_filter == "Remote Only":
                query += " AND remote_type IN ('remote_eu', 'remote_emea', 'remote_global', 'remote')"
            else:
                query += " AND remote_type = ?"
                params.append(remote_filter.lower())
        if eu_blue_card_only:
            query += " AND eu_blue_card = 1"
        if relocation_only:
            query += " AND has_relocation = 1"

        # ── Three-state verdict filters (locked decision #1) ─────────────────
        # Values: "All" | "Y" | "N" | "Unknown".  Honest semantics: legacy
        # rows (verdict == '') count as Unknown for the visa verdict, and for
        # blue-card / relocation the pre-migration booleans are honoured so
        # existing data remains filterable.  Unknown is never treated as N.
        def _verdict_clause(verdict_col, legacy_col, value):
            if value == "Y":
                if legacy_col:
                    return (f" AND (COALESCE({verdict_col},'') = 'Y' "
                            f"OR (COALESCE({verdict_col},'') = '' AND {legacy_col} = 1))")
                return " AND COALESCE(%s,'') = 'Y'" % verdict_col
            if value == "N":
                if legacy_col:
                    return (f" AND (COALESCE({verdict_col},'') = 'N' "
                            f"OR (COALESCE({verdict_col},'') = '' AND {legacy_col} = 0))")
                return " AND COALESCE(%s,'') = 'N'" % verdict_col
            # "Unknown" — explicitly unclassified or detector-said-unknown rows
            return f" AND COALESCE({verdict_col},'') IN ('Unknown', '')"

        if sponsorship_filter in ("Y", "N", "Unknown"):
            query += _verdict_clause("visa_sponsorship", None, sponsorship_filter)
        if blue_card_filter in ("Y", "N", "Unknown"):
            query += _verdict_clause("eu_blue_card_verdict", "eu_blue_card", blue_card_filter)
        # ── Experience filter (dynamic distinct-values dropdown) ─────────────
        # Case-insensitive: legacy normalizer rows are lowercase, scanner rows
        # are canonical (Intern/Entry/.../Exec).  Unknown covers '' + 'Unknown'.
        exp = (experience_filter or "All")
        if exp == "Tutti":
            exp = "All"
        if exp and exp not in ("All", ""):
            if exp in ("Unknown", "Unknown / Not classified"):
                query += " AND COALESCE(experience_level,'') IN ('', 'Unknown')"
            elif exp == "Any (incl. unknown)":
                pass
            else:
                query += " AND lower(COALESCE(experience_level,'')) = lower(?)"
                params.append(exp)

        if relocation_filter in ("Y", "N", "Unknown"):
            query += _verdict_clause("relocation_support", "has_relocation", relocation_filter)

        # Default sort by best match
        query += " ORDER BY sponsorship_score DESC, trust_score DESC, match_score DESC"
        rows = conn.execute(query, params).fetchall()
        return rows
    finally:
        if conn:
            conn.close()


# Canonical list of experience buckets for the UI dropdown. Order
# matters: this is the order they appear in the combobox.
EXPERIENCE_LEVELS = [
    "All",
    "Any (incl. unknown)",
    "Intern",
    "Entry",
    "Mid",
    "Senior",
    "Lead",
    "Exec",
    "Unknown / Not classified",
]

# BUGFIX (2024-Q4 round 2): the previous threshold of `>= 20` was too
# LOW for the `score()` function. The baseline is 20 with no signals at
# all, and most real jobs score exactly 20-30 even when there's no
# positive signal. With threshold 20 the "Sponsored" card was reporting
# ~98% of all jobs as sponsored, which is meaningless. We now use the
# 70-point threshold, which is what the Search tab's "Sponsored Only"
# filter already uses, so the dashboard and the search filter agree.
# A job must score >= 70 to be considered "sponsored" -- this is the
# level at which the text contained MULTIPLE positive signals, not just
# the baseline. The "Top companies by sponsorship score" table still
# uses the unfiltered MAX so users can see the gradient.
SPONSORSHIP_SCORE_THRESHOLD = 70


def get_dashboard_stats(db_path, _conn=None):
    # _conn: optional shared connection so one Dashboard refresh runs all
    # three dashboard queries without re-opening (and re-PRAGMA-ing) a
    # connection per query — this runs on the GUI thread on every scan tick.
    conn = _conn
    owned = conn is None
    try:
        if owned:
            conn = get_connection(db_path)
        stats = {
            "companies": conn.execute(
                "SELECT COUNT(DISTINCT company) FROM jobs "
                "WHERE verified_active = 1 AND is_expired = 0 AND company <> ''"
            ).fetchone()[0],
            "verified_jobs": conn.execute("SELECT COUNT(*) FROM jobs WHERE verified_active = 1 AND is_expired = 0").fetchone()[0],
            "discovery_jobs": conn.execute("SELECT COUNT(*) FROM jobs WHERE source_type = 'discovery'").fetchone()[0],
            # BUGFIX (round 1): previous version used a per-company COUNT(DISTINCT)
            # with a `>= 50` threshold, which produced 0 in almost every real
            # dataset. We now count JOBS (not companies) at the same threshold
            # the Search tab's "Sponsored Only" filter uses, so the numbers
            # on the dashboard always agree with what the user can filter for.
            "sponsored_jobs": conn.execute(
                "SELECT COUNT(*) FROM jobs "
                "WHERE sponsorship_score >= ? AND verified_active = 1 AND is_expired = 0",
                (SPONSORSHIP_SCORE_THRESHOLD,),
            ).fetchone()[0],
            "sponsored_companies": conn.execute(
                "SELECT COUNT(DISTINCT company) FROM jobs "
                "WHERE sponsorship_score >= ? AND verified_active = 1 AND is_expired = 0",
                (SPONSORSHIP_SCORE_THRESHOLD,),
            ).fetchone()[0],
            "applications": conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0],
            "countries": conn.execute("SELECT COUNT(DISTINCT country) FROM jobs WHERE country <> ''").fetchone()[0],
            "recent_jobs": conn.execute("SELECT COUNT(*) FROM jobs WHERE first_seen_at >= datetime('now', '-7 days')").fetchone()[0],
            "remote_jobs": conn.execute("SELECT COUNT(*) FROM jobs WHERE remote_type IN ('remote_eu','remote_emea','remote_global','remote') AND verified_active=1 AND is_expired=0").fetchone()[0],
            "eu_blue_card_jobs": conn.execute("SELECT COUNT(*) FROM jobs WHERE eu_blue_card=1 AND verified_active=1 AND is_expired=0").fetchone()[0],
        }
        return stats
    finally:
        if owned and conn:
            conn.close()


def get_dashboard_top_companies(db_path, limit=8, _conn=None):
    conn = _conn
    owned = conn is None
    try:
        if owned:
            conn = get_connection(db_path)
        rows = conn.execute("""
            SELECT company,
                   (SELECT country FROM jobs j2
                    WHERE j2.company = j1.company
                      AND j2.verified_active = 1 AND j2.is_expired = 0
                      AND j2.country <> ''
                    GROUP BY j2.country
                    ORDER BY COUNT(*) DESC, j2.country ASC
                    LIMIT 1) AS country,
                   COUNT(*) as job_count,
                   MAX(sponsorship_score) as max_sponsor,
                   MAX(match_score) as max_match
            FROM jobs j1
            WHERE j1.verified_active = 1 AND j1.is_expired = 0
              AND j1.company <> ''
            GROUP BY j1.company
            ORDER BY max_sponsor DESC, max_match DESC, job_count DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return rows
    finally:
        if owned and conn:
            conn.close()


def get_dashboard_country_counts(db_path, _conn=None):
    conn = _conn
    owned = conn is None
    try:
        if owned:
            conn = get_connection(db_path)
        rows = conn.execute("""
            SELECT country, COUNT(*) as count
            FROM jobs
            WHERE verified_active = 1 AND is_expired = 0 AND country <> ''
            GROUP BY country
            ORDER BY count DESC, country ASC
        """).fetchall()
        return rows
    finally:
        if owned and conn:
            conn.close()


def get_distinct_job_countries(db_path) -> list[str]:
    """Return a sorted list of distinct job location countries from the DB.

    Used to populate the Country filter dropdown with only countries that
    actually have jobs, rather than a static EU list.
    """
    conn = None
    try:
        conn = get_connection(db_path)
        rows = conn.execute("""
            SELECT DISTINCT country FROM jobs
            WHERE country <> '' AND verified_active = 1 AND is_expired = 0
            ORDER BY country ASC
        """).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def upsert_application(db_path, job_url, company, title, status="saved",
                       applied_at=None, next_followup_at=None, notes=""):
    """Insert or update one saved application (keyed on ``job_url``).

    BUGFIX: ``applied_at`` used to be written unconditionally as
    ``excluded.applied_at``.  Neither caller (Search "Save to Applications" and
    the Applications edit form) passes it, so the parameter default ``None``
    was written over the stored value on every edit — and the INSERT stored
    NULL too.  The Applications tab renders that column as "Saved on", so the
    date could never appear and was destroyed by the first status change.

    The column is now stamped once on insert (``COALESCE(..., CURRENT_TIMESTAMP)``)
    and preserved on update when the caller does not supply a new value
    (``COALESCE(excluded.applied_at, applications.applied_at)``), so editing
    status/notes never wipes the original save date.  Passing ``applied_at``
    explicitly still overwrites it.
    """
    conn = None
    try:
        conn = get_connection(db_path)
        conn.execute("""
            INSERT INTO applications (job_url, company, title, status, applied_at, next_followup_at, notes, updated_at)
            VALUES (?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(job_url) DO UPDATE SET
                company=excluded.company,
                title=excluded.title,
                status=excluded.status,
                applied_at=COALESCE(excluded.applied_at, applications.applied_at),
                next_followup_at=excluded.next_followup_at,
                notes=excluded.notes,
                updated_at=CURRENT_TIMESTAMP
        """, (job_url, company, title, status, applied_at, next_followup_at, notes))
        conn.commit()
    finally:
        if conn:
            conn.close()


def list_applications(db_path):
    conn = None
    try:
        conn = get_connection(db_path)
        rows = conn.execute(
            "SELECT company, title, status, applied_at, next_followup_at, notes, job_url FROM applications ORDER BY updated_at DESC"
        ).fetchall()
        return rows
    finally:
        if conn:
            conn.close()


# ── Scan-run evidence helpers ────────────────────────────────────────────────
# The pipeline records one scan_runs row per execution plus one scan_log row
# per scanned company (mirroring the _scan_log.csv the algorithm scripts emit),
# so the Tools tab can show per-scan evidence without re-reading CSV files.

def start_scan_run(db_path, run_id: str, method: str, ats_companies: int,
                   career_companies: int):
    conn = None
    try:
        conn = get_connection(db_path)
        conn.execute(
            """INSERT INTO scan_runs (run_id, method, ats_companies, career_companies, status)
               VALUES (?, ?, ?, ?, 'running')
               ON CONFLICT(run_id) DO UPDATE SET
                 method=excluded.method,
                 started_at=CURRENT_TIMESTAMP,
                 finished_at=NULL,
                 status='running',
                 error=''""",
            (run_id, method, int(ats_companies), int(career_companies)),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def finish_scan_run(db_path, run_id: str, targets_ok: int = 0,
                    targets_empty: int = 0, targets_error: int = 0,
                    jobs_found: int = 0, jobs_quarantined: int = 0,
                    jobs_duplicates: int = 0, status: str = "completed",
                    error: str = ""):
    conn = None
    try:
        conn = get_connection(db_path)
        conn.execute(
            """UPDATE scan_runs SET
                 targets_ok=?, targets_empty=?, targets_error=?,
                 jobs_found=?, jobs_quarantined=?, jobs_duplicates=?,
                 finished_at=CURRENT_TIMESTAMP, status=?, error=?
               WHERE run_id=?""",
            (int(targets_ok), int(targets_empty), int(targets_error),
             int(jobs_found), int(jobs_quarantined), int(jobs_duplicates),
             status, error, run_id),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def record_scan_log_rows(db_path, run_id: str, scanner: str, rows):
    """Insert per-company scan-log rows (dicts using the scan_log columns)."""
    if not rows:
        return
    conn = None
    try:
        conn = get_connection(db_path)
        conn.executemany(
            """INSERT INTO scan_log
               (run_id, scanner, seed_name, company, source_type, target_country,
                status, provider, jobs_found, quarantined, duplicates,
                rejected_scope, error, diagnostics, duration_sec, seed_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    run_id, scanner,
                    (r.get("seed_name") or r.get("Seed Name") or ""),
                    (r.get("company") or r.get("Company") or ""),
                    (r.get("source_type") or r.get("Source Type") or ""),
                    (r.get("target_country") or r.get("Target Country") or ""),
                    (r.get("status") or r.get("Status") or ""),
                    (r.get("provider") or r.get("Provider") or ""),
                    int(r.get("jobs_found", r.get("Jobs Found", 0)) or 0),
                    int(r.get("quarantined", r.get("Quarantined", 0)) or 0),
                    int(r.get("duplicates", r.get("Duplicates", 0)) or 0),
                    int(r.get("rejected_scope", r.get("Rejected Scope", 0)) or 0),
                    (r.get("error") or r.get("Error") or ""),
                    (r.get("diagnostics") or r.get("Diagnostics") or "")[:8000],
                    float(r.get("duration_sec", r.get("Duration Sec", 0)) or 0),
                    (r.get("seed_url") or r.get("Seed URL") or ""),
                )
                for r in rows
            ],
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def sum_scan_log_quarantined(db_path, run_id: str) -> int:
    """Total quarantined rows for one run, summed from scan_log (F9)."""
    conn = None
    try:
        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT COALESCE(SUM(quarantined), 0) FROM scan_log WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return int(row[0] or 0)
    finally:
        if conn:
            conn.close()


def list_scan_runs(db_path, limit: int = 25):
    """Most recent scan runs first (for the Tools tab scan-history view)."""
    conn = None
    try:
        conn = get_connection(db_path)
        rows = conn.execute(
            """SELECT run_id, method, started_at, finished_at, status, error,
                      targets_ok, targets_empty, targets_error,
                      jobs_found, jobs_quarantined, jobs_duplicates,
                      ats_companies, career_companies
               FROM scan_runs ORDER BY started_at DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
        return rows
    finally:
        if conn:
            conn.close()


def get_scan_log(db_path, run_id: str):
    """Per-company outcomes for one scan run."""
    conn = None
    try:
        conn = get_connection(db_path)
        rows = conn.execute(
            """SELECT seed_name, company, source_type, target_country, status,
                      provider, jobs_found, quarantined, duplicates,
                      rejected_scope, error, diagnostics, duration_sec, seed_url
               FROM scan_log WHERE run_id=? ORDER BY id ASC""",
            (run_id,),
        ).fetchall()
        return rows
    finally:
        if conn:
            conn.close()


def get_scan_run(db_path, run_id: str):
    """Return the summary row for one scan run (or None if it does not exist)."""
    conn = None
    try:
        conn = get_connection(db_path)
        return conn.execute(
            """SELECT run_id, method, started_at, finished_at, status, error,
                      targets_ok, targets_empty, targets_error,
                      jobs_found, jobs_quarantined, jobs_duplicates,
                      ats_companies, career_companies
               FROM scan_runs WHERE run_id=?""",
            (run_id,),
        ).fetchone()
    finally:
        if conn:
            conn.close()


def get_completed_scan_companies(db_path, run_id: str) -> dict:
    """Companies already finished in one run, split by scanner phase.

    Returns ``{"ats": set(names), "career": set(names)}`` (lower-cased
    company names from the run's ``scan_log`` rows).  A row exists only
    *after* a company fully completes, so anything absent here is safe to
    (re)scan: completed rows were already live-ingested into jobs.
    """
    done = {"ats": set(), "career": set()}
    conn = None
    try:
        conn = get_connection(db_path)
        rows = conn.execute(
            "SELECT scanner, company FROM scan_log WHERE run_id=?",
            (run_id,),
        ).fetchall()
    finally:
        if conn:
            conn.close()
    for scanner, company in rows or []:
        key = (scanner or "").strip().lower()
        name = (company or "").strip().lower()
        if not name:
            continue
        if key == "ats":
            done["ats"].add(name)
        elif key == "career":
            done["career"].add(name)
    return done


def parse_resume_link(error_text: str | None) -> str:
    """Extract the parent run_id from a scan_runs error field.

    The pipeline encodes ``resumed_from:<parent_run_id>`` into the error
    field (no schema migration needed); it may be ``;``-joined with real
    phase errors.  Returns ``""`` when no linkage is present.
    """
    for part in (error_text or "").split(";"):
        part = part.strip()
        if part.lower().startswith("resumed_from:"):
            return part.split(":", 1)[1].strip()
    return ""


def mark_scan_resumed(db_path, parent_run_id: str, child_run_id: str) -> bool:
    """Promote a stopped parent run to ``resumed`` once fully covered.

    Called when a resume child finishes: if the parent + child scan_log
    rows jointly cover every current seed company (i.e. the parent has no
    remaining work left), the parent's ``cancelled`` status becomes
    ``resumed`` ("stopped, later completed via resume") and records which
    child completed it.  Returns True when the parent was promoted.
    Only ``cancelled``/``partial``/``error`` parents are eligible; a child
    that was itself stopped with remainder left keeps the parent resumable.
    """
    conn = None
    try:
        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT status FROM scan_runs WHERE run_id=?",
            (parent_run_id,),
        ).fetchone()
        if not row or (row[0] or "") not in ("cancelled", "partial", "error"):
            return False
    finally:
        if conn:
            conn.close()
    # Union of parent + child completions covers everything?
    try:
        parent_done = get_completed_scan_companies(db_path, parent_run_id)
        child_done = get_completed_scan_companies(db_path, child_run_id)
    except Exception:
        return False
    covered = set(parent_done.get("ats", set())) | set(
        child_done.get("ats", set())) | set(
        parent_done.get("career", set())) | set(
        child_done.get("career", set()))
    try:
        from sponsorscout.application import seed_manager
        names = set()
        for path in (seed_manager.user_ats_path(),
                     seed_manager.user_career_path()):
            for r in seed_manager.read_seed_rows(path)["rows"]:
                name = (r.get("name") or "").strip().lower()
                if name:
                    names.add(name)
    except Exception:
        return False
    if names - covered:
        return False
    conn = None
    try:
        conn = get_connection(db_path)
        conn.execute(
            """UPDATE scan_runs SET status='resumed',
                  error=CASE WHEN COALESCE(error,'')=''
                             THEN ? ELSE error || '; ' || ? END
               WHERE run_id=?""",
            (f"resumed_by:{child_run_id}", f"resumed_by:{child_run_id}",
             parent_run_id),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        if conn:
            conn.close()


def parse_resumed_by(error_text: str | None) -> str:
    """Extract the child run_id that completed a ``resumed`` parent row."""
    for part in (error_text or "").split(";"):
        part = part.strip()
        if part.lower().startswith("resumed_by:"):
            return part.split(":", 1)[1].strip()
    return ""


def get_resumable_scan(db_path) -> dict | None:
    """Find the newest stopped scan that still has unscanned companies.

    Returns ``None`` when there is nothing to resume, else a checkpoint::

        {"run_id": ..., "method": ...,
         "remaining_ats": [names...], "remaining_career": [names...],
         "done_ats": n, "done_career": n, "total_ats": n, "total_career": n,
         "added_since_stop": n}

    Name matching is by lower-cased company name against the *current* user
    seeds, so seed edits between stop and resume are handled: newly added
    companies join ``remaining`` (counted in ``added_since_stop``), removed
    ones simply drop out.
    """
    from sponsorscout.application import seed_manager

    # Newest stopped run wins: a resume child stopped mid-way supersedes
    # its parent (the parent's remainder is a subset of the child's view
    # once the child's own completions are counted).  ``started_at`` has
    # only 1s resolution and ties are possible in tests, so break them by
    # rowid (insertion order) — later started = larger rowid.
    conn = None
    try:
        conn = get_connection(db_path)
        runs = conn.execute(
            """SELECT run_id, method, status, ats_companies, career_companies
               FROM scan_runs
               WHERE status IN ('cancelled', 'partial', 'error')
               ORDER BY rowid DESC LIMIT 10"""
        ).fetchall()
    finally:
        if conn:
            conn.close()
    if not runs:
        return None
    for run in runs:
        run_id = run[0]
        try:
            done = get_completed_scan_companies(db_path, run_id)
        except Exception:
            continue
        try:
            ats_rows = seed_manager.read_seed_rows(
                seed_manager.user_ats_path())["rows"]
            career_rows = seed_manager.read_seed_rows(
                seed_manager.user_career_path())["rows"]
        except Exception:
            continue
        remaining_ats = [
            (r.get("name") or "").strip()
            for r in ats_rows
            if (r.get("name") or "").strip()
            and (r.get("name") or "").strip().lower() not in done["ats"]
        ]
        remaining_career = [
            (r.get("name") or "").strip()
            for r in career_rows
            if (r.get("name") or "").strip()
            and (r.get("name") or "").strip().lower() not in done["career"]
        ]
        if not remaining_ats and not remaining_career:
            continue  # fully covered — nothing left to resume
        total_ats = len([r for r in ats_rows if (r.get("name") or "").strip()])
        total_career = len([r for r in career_rows
                            if (r.get("name") or "").strip()])
        done_ats = total_ats - len(remaining_ats)
        done_career = total_career - len(remaining_career)
        # Companies added to the seeds after the run stopped.
        try:
            planned = int(run[3] or 0) + int(run[4] or 0)
            added = max(0, (total_ats + total_career) - planned)
        except (TypeError, ValueError):
            added = 0
        return {
            "run_id": run_id,
            "method": run[1] or "full",
            "remaining_ats": remaining_ats,
            "remaining_career": remaining_career,
            "done_ats": max(0, done_ats),
            "done_career": max(0, done_career),
            "total_ats": total_ats,
            "total_career": total_career,
            "added_since_stop": added,
        }
    return None


def record_scan_event(db_path, run_id: str, level: str = "info",
                      phase: str = "pipeline", message: str = "",
                      company: str = ""):
    """Append one entry to the run's granular event timeline."""
    conn = None
    try:
        conn = get_connection(db_path)
        conn.execute(
            """INSERT INTO scan_events (run_id, level, phase, company, message)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, level, phase, company, str(message)[:2000]),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def record_scan_events(db_path, run_id: str, rows):
    """Insert many scan_events rows in ONE connection/transaction.

    ``rows`` is an iterable of ``(level, phase, company, message)`` tuples.
    Batching matters: the single-row ``record_scan_event`` opens and commits a
    connection per call, so persisting a chatty scan's progress line by line
    costs tens of thousands of round-trips and dominates disk I/O on low-end
    machines.
    """
    rows = [(run_id, str(level or "info"), str(phase or "pipeline"),
             str(company or ""), str(message or "")[:2000])
            for level, phase, company, message in rows]
    if not rows:
        return
    conn = None
    try:
        conn = get_connection(db_path)
        conn.executemany(
            """INSERT INTO scan_events (run_id, level, phase, company, message)
               VALUES (?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def get_scan_events(db_path, run_id: str):
    """Chronological event timeline for one scan run (oldest first)."""
    conn = None
    try:
        conn = get_connection(db_path)
        return conn.execute(
            """SELECT ts, level, phase, company, message
               FROM scan_events WHERE run_id=? ORDER BY id ASC""",
            (run_id,),
        ).fetchall()
    finally:
        if conn:
            conn.close()


def export_scan_run_csv(db_path, run_id: str) -> str:
    """Render one scan run as a structured CSV for downloading.

    Three sections, each with its own header row, so every column is
    machine-readable in Excel / LibreOffice (the old single-cell "k,v" rows
    split unreliably across columns):

      1. Summary      — Key / Value columns (run metadata)
      2. Per-company  — fixed 14 columns incl. Error, Diagnostics, Duration
                         Sec and Seed URL (these carry the critical failure
                         descriptions that were missing before)
      3. Event timeline — Timestamp / Level / Phase / Company / Message
    """
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")

    run = get_scan_run(db_path, run_id)
    if run is None:
        raise ValueError(f"Scan run not found: {run_id}")

    # ── 1. Summary (Key / Value) ─────────────────────────────────────────
    writer.writerow(["Key", "Value"])
    summary_fields = [
        ("Run ID", run["run_id"]),
        ("Method", run["method"] or ""),
        ("Started", run["started_at"] or ""),
        ("Finished", run["finished_at"] or ""),
        ("Status", run["status"] or ""),
        ("Jobs Found", run["jobs_found"] or 0),
        ("Duplicates", run["jobs_duplicates"] or 0),
        ("Quarantined", run["jobs_quarantined"] or 0),
        ("Targets OK", run["targets_ok"] or 0),
        ("Targets Empty", run["targets_empty"] or 0),
        ("Targets Error", run["targets_error"] or 0),
        ("Run Error", run["error"] or ""),
    ]
    for key, val in summary_fields:
        writer.writerow([key, val])
    writer.writerow([])  # blank separator row

    # ── 2. Per-company scan log (fixed columns) ─────────────────────────
    PER_COMPANY_COLS = [
        "Seed Name", "Company", "Source Type", "Target Country", "Status",
        "Provider", "Jobs Found", "Quarantined", "Duplicates",
        "Rejected Scope", "Error", "Diagnostics", "Duration Sec",
        "Seed URL",
    ]
    col_index = [
        "seed_name", "company", "source_type", "target_country", "status",
        "provider", "jobs_found", "quarantined", "duplicates",
        "rejected_scope", "error", "diagnostics", "duration_sec", "seed_url",
    ]
    writer.writerow(PER_COMPANY_COLS)
    for row in get_scan_log(db_path, run_id):
        # Rows are sqlite3.Row objects — index by column name, default "".
        writer.writerow([row[c] if row[c] is not None else "" for c in col_index])
    writer.writerow([])  # blank separator row

    # ── 3. Event timeline ───────────────────────────────────────────────
    events = []
    try:
        events = get_scan_events(db_path, run_id)
    except Exception:  # pragma: no cover - old DBs may lack scan_events
        pass
    writer.writerow(["Timestamp", "Level", "Phase", "Company", "Message"])
    for ev in events:
        writer.writerow([ev["ts"], ev["level"], ev["phase"],
                         ev["company"], ev["message"]])
    return buf.getvalue()


def get_distinct_remote_types(db_path) -> list[str]:
    """Return distinct remote_type values from active jobs for dynamic filter dropdown."""
    conn = None
    try:
        conn = get_connection(db_path)
        rows = conn.execute("""
            SELECT DISTINCT remote_type FROM jobs
            WHERE remote_type IS NOT NULL AND remote_type != ''
            AND verified_active = 1 AND is_expired = 0
            ORDER BY remote_type ASC
        """).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def get_distinct_job_companies(db_path) -> list[str]:
    """Return distinct company names from active jobs for dynamic filter dropdown."""
    conn = None
    try:
        conn = get_connection(db_path)
        rows = conn.execute("""
            SELECT DISTINCT company FROM jobs
            WHERE company IS NOT NULL AND company != ''
            AND verified_active = 1 AND is_expired = 0
            ORDER BY company ASC
        """).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def get_distinct_job_locations(db_path) -> list[str]:
    """Return distinct location values from active jobs for dynamic filter dropdown."""
    conn = None
    try:
        conn = get_connection(db_path)
        rows = conn.execute("""
            SELECT DISTINCT location FROM jobs
            WHERE location IS NOT NULL AND location != ''
            AND verified_active = 1 AND is_expired = 0
            ORDER BY location ASC
        """).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def get_distinct_experience_specs(db_path) -> list[tuple]:
    """Return distinct (required, level, min_years) triples from active jobs.

    The Search tab renders each triple through ui.tabs.search.
    _experience_cell() to build the Experience dropdown, so the dropdown
    always offers exactly the unique value list of the Experience column
    (canonical, untranslated values — see FILTER_ALL in ui.tabs.search).
    """
    conn = None
    try:
        conn = get_connection(db_path)
        rows = conn.execute("""
            SELECT DISTINCT experience_required, experience_level,
                   experience_min_years
            FROM jobs
            WHERE verified_active = 1 AND is_expired = 0
        """).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def get_distinct_industries(db_path) -> list[str]:
    """Return distinct industry values from active jobs for dynamic filter dropdown."""
    conn = None
    try:
        conn = get_connection(db_path)
        rows = conn.execute("""
            SELECT DISTINCT industry FROM jobs
            WHERE industry IS NOT NULL AND industry != ''
            AND verified_active = 1 AND is_expired = 0
            ORDER BY industry ASC
        """).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


# ── Scan-run evidence queries (see pipeline/Tools tab) ──────────────────────
# (The former AI asset storage functions were removed with the AI features.)


def delete_application(db_path, job_url: str):
    conn = None
    try:
        conn = get_connection(db_path)
        conn.execute("DELETE FROM applications WHERE job_url=?", (job_url,))
        conn.commit()
    finally:
        if conn:
            conn.close()
