"""Freshness-verification bookkeeping (trust / freshness score updates)."""

from datetime import datetime, timezone

#: Canonical on-disk timestamp format.
#:
#: BUGFIX: this used to be ``datetime.now(timezone.utc).isoformat()``, which
#: writes ``2026-09-30T14:03:06.424912+00:00`` — a capital ``T`` separator and a
#: ``+00:00`` suffix.  Every comparison against this column is a *string*
#: comparison (``last_verified_at < datetime('now','-7 days')`` and
#: ``ORDER BY last_verified_at``), and SQLite's ``datetime()`` produces
#: ``2026-09-30 14:03:06`` (space separator, no timezone) — which is also what
#: the schema's ``CURRENT_TIMESTAMP`` default writes.  Mixing the two formats in
#: one column made the ordering arbitrary: the same instant compared as stale in
#: one format and fresh in the other, because ``'T' > ' '``.  Writing the exact
#: SQLite format everywhere makes the comparisons correct and keeps the column
#: sortable.
_SQLITE_TS = "%Y-%m-%d %H:%M:%S"


def utc_now_stamp() -> str:
    """Return the current UTC time in SQLite's ``YYYY-MM-DD HH:MM:SS`` format."""
    return datetime.now(timezone.utc).strftime(_SQLITE_TS)


def mark_verified(job: dict):
    job["verified_active"] = True
    job["is_expired"] = False
    job["last_verified_at"] = utc_now_stamp()
    job["trust_score"] = max(int(job.get("trust_score", 0)), 90)
    job["freshness_score"] = 100
    return job


def mark_expired(job: dict):
    job["verified_active"] = False
    job["is_expired"] = True
    job["freshness_score"] = 0
    # Stamp the check even when the verdict is negative: without it the row
    # keeps its old (or NULL) timestamp, so `last_verified_at IS NULL OR < 7
    # days` re-selects the same dead job on every single run.
    job["last_verified_at"] = utc_now_stamp()
    return job


def mark_inconclusive(job: dict):
    """Record that a check ran but could not reach a verdict.

    The verdict columns are left exactly as they were — an inconclusive check
    (403 from a WAF, timeout, DNS failure) is NOT evidence that a job died, so
    it must never flip ``is_expired``/``verified_active``.  Only
    ``last_verified_at`` moves, so the row leaves the "needs a check" queue and
    comes back for another attempt later instead of being hammered forever.
    """
    job["last_verified_at"] = utc_now_stamp()
    return job
