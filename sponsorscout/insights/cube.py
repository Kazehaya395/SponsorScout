"""Cube-backed reads for the Insights tab.

Why this exists (measured, not assumed)
--------------------------------------
The five panels of the Insights tab each ran their own ``GROUP BY`` over the
whole ``jobs`` table, each on a fresh read-only connection whose SQLite page
cache is limited to 2 MB (``core/db_tasks.py``).  On a 35,555-row database that
cost 0.61 s unfiltered and 0.82 s with the Unknown-domain selection - per
click, because a pie-slice click re-runs all five queries.  Adding the two
indexes in ``db/schema.sql`` cut the SQL by 8-24x but the *interaction* stayed
at 0.8-1.2 s: the cost was connection churn and cache misses, not the scan.

So the tab answers its panels from a pre-aggregated fact table instead:
``insights_cube_fact`` holds one row per (category, domain, country) with the
counts every panel needs, and ``insights_cube_skill`` the same shape per skill
term.  Every panel is then a ``SUM``/``GROUP BY`` over a few thousand rows -
0.0003 s measured against 0.24 s for the live query - and the numbers stay
identical because the aggregation is the same expression, just materialised.

Scope rules
-----------
* The cube only covers the tab's default scope (``verified_active = 1 AND
  is_expired = 0`` over every run).  A run filter or a source-type filter
  returns ``None`` and the caller runs its original SQL, so nothing is silently
  answered from the wrong population.
* Freshness is a stamp of ``taxonomy_version`` + row count + newest
  ``jobs.updated_at``.  A stale cube is rebuilt on the next read (0.08 s
  measured) instead of serving last run's answer.
* Every entry point returns ``None`` on any problem (missing table, locked
  build, unexpected shape).  Callers fall back to SQL, so the tab still works
  against a database this module has never seen.
"""
from __future__ import annotations

import threading
from typing import Any, Optional

SCHEMA_VERSION = "1"
FACT = "insights_cube_fact"
SKILL = "insights_cube_skill"
META = "insights_cube_meta"
# All panels read these columns; keep the order in sync with _BUILD_FACT.
# Verdict columns the Sponsorship panel needs, in output order.
_VERDICTS = ("visa_yes", "visa_no", "visa_unk", "reloc_yes", "reloc_no", "reloc_unk")

_DDL = f"""
CREATE TABLE IF NOT EXISTS {FACT} (
    category TEXT NOT NULL,
    domain TEXT NOT NULL,
    country TEXT NOT NULL,
    total INTEGER NOT NULL DEFAULT 0,
    uncategorized INTEGER NOT NULL DEFAULT 0,
    with_skills INTEGER NOT NULL DEFAULT 0,
    visa_yes INTEGER NOT NULL DEFAULT 0,
    visa_no INTEGER NOT NULL DEFAULT 0,
    visa_unk INTEGER NOT NULL DEFAULT 0,
    reloc_yes INTEGER NOT NULL DEFAULT 0,
    reloc_no INTEGER NOT NULL DEFAULT 0,
    reloc_unk INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS {SKILL} (
    term TEXT NOT NULL,
    category TEXT NOT NULL,
    domain TEXT NOT NULL,
    country TEXT NOT NULL,
    jobs_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_{SKILL}_term ON {SKILL}(term, jobs_count DESC);
CREATE TABLE IF NOT EXISTS {META} (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_BUILD = f"""
WITH scoped AS (
    SELECT j.id,
           COALESCE(NULLIF(j.category, ''), 'Other')  AS category,
           COALESCE(NULLIF(j.domain, ''), 'Unknown')  AS domain,
           COALESCE(j.country, '')                     AS country,
           j.visa_sponsorship, j.relocation_support,
           CASE WHEN j.category IS NULL OR j.category = '' OR j.category = 'Other'
                THEN 1 ELSE 0 END                      AS uncategorized
    FROM jobs j WHERE j.verified_active = 1 AND j.is_expired = 0
),
skilled AS (SELECT DISTINCT job_id FROM job_terms WHERE kind = 'skill')
INSERT INTO {FACT}
    SELECT s.category, s.domain, s.country, COUNT(*),
           SUM(s.uncategorized),
           SUM(CASE WHEN k.job_id IS NOT NULL THEN 1 ELSE 0 END),
           SUM(CASE WHEN s.visa_sponsorship = 'Y' THEN 1 ELSE 0 END),
           SUM(CASE WHEN s.visa_sponsorship = 'N' THEN 1 ELSE 0 END),
           SUM(CASE WHEN s.visa_sponsorship NOT IN ('Y', 'N') THEN 1 ELSE 0 END),
           SUM(CASE WHEN s.relocation_support = 'Y' THEN 1 ELSE 0 END),
           SUM(CASE WHEN s.relocation_support = 'N' THEN 1 ELSE 0 END),
           SUM(CASE WHEN s.relocation_support NOT IN ('Y', 'N') THEN 1 ELSE 0 END)
    FROM scoped s LEFT JOIN skilled k ON k.job_id = s.id
    GROUP BY 1, 2, 3;
DELETE FROM {SKILL};
INSERT INTO {SKILL}
    SELECT t.term, s.category, s.domain, s.country, COUNT(DISTINCT t.job_id)
    FROM job_terms t
    JOIN (SELECT id, COALESCE(NULLIF(category, ''), 'Other') AS category,
                 COALESCE(NULLIF(domain, ''), 'Unknown') AS domain,
                 COALESCE(country, '') AS country
          FROM jobs WHERE verified_active = 1 AND is_expired = 0) s
      ON s.id = t.job_id
    WHERE t.kind = 'skill'
    GROUP BY 1, 2, 3, 4;
"""

_lock = threading.Lock()
_building = False


def _is_all(value: Any) -> bool:
    text = str(value or "").strip().casefold()
    return not text or text in ("all", "all countries", "all categories", "all domains")


def usable(**filters: Any) -> bool:
    """True when these filters describe the population the cube was built for.

    A skill-term filter is a semi-join against `job_terms`, which the fact table
    does not carry, and a run or source-type filter narrows the population below
    what the cube was built from. All three fall through to the live SQL.
    """
    if str(filters.get("skill_term") or "").strip():
        return False
    return _is_all(filters.get("run_id")) and _is_all(filters.get("source_subtype"))


def _slice(category: str = "", domain: str = "", country: str = "") -> tuple[str, list[Any]]:
    clauses, params = [], []
    if not _is_all(country):
        clauses.append("country = ?"); params.append(str(country).strip())
    if not _is_all(category):
        clauses.append("category = ?"); params.append(str(category).strip())
    if not _is_all(domain):
        clauses.append("domain = ?"); params.append(str(domain).strip())
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


_stamps: dict[tuple, str] = {}


def _stamp(conn, *, cache: bool = True) -> str:
    """Change detector for the aggregate: taxonomy + rows + newest update.

    Cached for the lifetime of one snapshot read task only. A refresh issues
    five panel reads and recomputing the stamp five times would cost more than
    the panels it protects, while any time-based TTL could mask an ingest that
    lands right after a read - so staleness is decided per snapshot, never by
    a clock. Outside a read task (tests, CLI) it is always recomputed.
    """
    from sponsorscout.core.db_tasks import current_database_task
    from sponsorscout.insights.terms import taxonomy_version
    task = current_database_task() if cache else None
    key = None
    if task is not None:
        key = (id(task), str(getattr(conn, "database", "") or ""))
        hit = _stamps.get(key)
        if hit is not None:
            return hit
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    newest = "COALESCE(MAX(updated_at), '')" if "updated_at" in columns else "''"
    count, last = conn.execute(f"SELECT COUNT(*), {newest} FROM jobs").fetchone()
    stamp = f"{SCHEMA_VERSION}|{taxonomy_version()}|{count}|{last}"
    if key is not None:
        if len(_stamps) > 64:
            _stamps.clear()
        _stamps[key] = stamp
    return stamp


def refresh(db_path, force: bool = False) -> bool:
    """Rebuild the cube when its stamp no longer matches the jobs table.

    Returns True when a cube matching the current data exists afterwards.
    Called from the Insights refresh worker and, if a scan changed rows in the
    meantime, from the first read that notices the stamp.
    """
    global _building
    import sqlite3
    from pathlib import Path
    from sponsorscout.core.db_tasks import current_database_task
    if current_database_task() is not None:
        # A snapshot connection is read-only; never write from one.
        return False
    if _building or not _lock.acquire(blocking=False):
        return False
    _building = True
    conn = None
    try:
        conn = sqlite3.connect(str(Path(db_path).expanduser()), timeout=30.0)
        conn.execute("PRAGMA busy_timeout=4000")
        conn.executescript(_DDL)
        stamp = _stamp(conn, cache=False)
        row = conn.execute(f"SELECT value FROM {META} WHERE key = 'stamp'").fetchone()
        if not force and row and row[0] == stamp:
            return True
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(f"DELETE FROM {FACT}")
            conn.executescript(_BUILD)
            conn.execute(f"INSERT OR REPLACE INTO {META}(key,value) VALUES('stamp',?)", (stamp,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return True
    except Exception:
        return False
    finally:
        conn and conn.close()
        _building = False
        _lock.release()


# --- panel readers: each returns None when it cannot answer ----------------

def _rows(db_path, sql: str, params: list[Any], *, table: str = FACT) -> Optional[list[tuple]]:
    """Run `sql` only when a cube matching the current jobs table exists.

    The stamp check is what keeps a click from answering with a stale
    aggregate: no cube, or a cube built before the last ingest, returns None
    and the caller runs its live SQL instead.
    """
    from sponsorscout.db.database import get_connection
    try:
        with get_connection(db_path) as conn:
            stamp = conn.execute(
                f"SELECT value FROM {META} WHERE key='stamp' AND "
                f"EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name=?)",
                (table,)).fetchone()
            if stamp is None or stamp[0] != _stamp(conn):
                return None
            return [tuple(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        return None


def coverage(db_path, **filters: Any) -> Optional[dict[str, Any]]:
    if not usable(**filters):
        return None
    where, params = _slice(filters.get("category", ""), filters.get("domain", ""), filters.get("country", ""))
    rows = _rows(db_path, f"SELECT COUNT(*), COALESCE(SUM(total),0), COALESCE(SUM(uncategorized),0),"
                          f" COALESCE(SUM(with_skills),0) FROM {FACT}{where}", params)
    if not rows or len(rows) != 1 or rows[0][0] == 0:
        # An empty result is ambiguous: a genuinely empty slice or a cube that
        # has never seen these categories.  Let SQL answer it.
        return None
    _, total, uncategorized, with_skills = rows[0]
    total, categorized = int(total), int(total) - int(uncategorized)
    return {
        "total_jobs": total,
        "categorized_jobs": categorized,
        "jobs_with_skills": int(with_skills),
        "category_coverage_pct": round(categorized / total * 100, 1) if total else 0.0,
        "skill_coverage_pct": round(int(with_skills) / total * 100, 1) if total else 0.0,
    }


def _grouped(db_path, column: str, **filters: Any) -> Optional[list[tuple[str, int]]]:
    if not usable(**filters):
        return None
    where, params = _slice(filters.get("category", ""), filters.get("domain", ""), filters.get("country", ""))
    rows = _rows(db_path, f"SELECT {column}, SUM(total) FROM {FACT}{where} "
                          f"GROUP BY 1 ORDER BY SUM(total) DESC, {column} ASC", params)
    if rows is None or not rows:
        return None
    return [(str(name), int(count)) for name, count in rows]


def jobs_by_category(db_path, **filters: Any):
    return _grouped(db_path, "category", **filters)


def jobs_by_domain(db_path, **filters: Any):
    return _grouped(db_path, "domain", **filters)


def sponsorship_by_category(db_path, **filters: Any):
    if not usable(**filters):
        return None
    where, params = _slice(filters.get("category", ""), filters.get("domain", ""), filters.get("country", ""))
    sums = ", ".join(f"COALESCE(SUM({column}), 0)" for column in _VERDICTS)
    rows = _rows(db_path, f"SELECT category, {sums}, COALESCE(SUM(total), 0) FROM {FACT}{where} "
                          f"GROUP BY 1 ORDER BY SUM(total) DESC, category ASC", params)
    if not rows:
        return None
    keys = ("category",) + _VERDICTS + ("total",)
    return [dict(zip(keys, (str(row[0]),) + tuple(int(value) for value in row[1:]) )) for row in rows]


def top_skills(db_path, limit: int = 15, **filters: Any):
    if not usable(**filters):
        return None
    where, params = _slice(filters.get("category", ""), filters.get("domain", ""), filters.get("country", ""))
    rows = _rows(db_path, f"SELECT term, SUM(jobs_count) FROM {SKILL}{where} "
                          f"GROUP BY 1 ORDER BY SUM(jobs_count) DESC, term ASC",
                 params, table=SKILL)
    if rows is None or not rows:
        return None
    return [(str(term), int(count)) for term, count in rows]
