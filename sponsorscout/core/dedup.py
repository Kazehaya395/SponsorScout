from __future__ import annotations

import hashlib
import re
from sponsorscout.core.url_normalizer import normalize_url


# B11 fix: pre-compile the (m/f) / (f/m) / (m/f/d) etc. gender-noise regex
# once at import time. We use it both in normalize_title and in the
# fingerprint so that jobs differing only by gender noise hash to the same
# value.
_GENDER_NOISE_RE = re.compile(
    r"\s*[\(\[]\s*(?:"
    r"m\s*/\s*f|f\s*/\s*m|m\s*/\s*w\s*/\s*d|f\s*/\s*m\s*/\s*x|m\s*/\s*f\s*/\s*d|"
    r"f\s*/\s*m\s*/\s*d|any\s+gender|all\s+genders?"
    r")\s*[\)\]]\s*",
    re.IGNORECASE,
)


def _normalize_for_fingerprint(text: str) -> str:
    """Lowercase, collapse whitespace, strip gender / diversity noise."""
    if not text:
        return ""
    cleaned = _GENDER_NOISE_RE.sub(" ", text)
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def job_fingerprint(title: str, company: str, location: str = "", url: str = "") -> str:
    normalized_url = normalize_url(url or "")
    key = "|".join([
        _normalize_for_fingerprint(title),
        _normalize_for_fingerprint(company),
        _normalize_for_fingerprint(location),
        (normalized_url or "").strip(),
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _delete_in_chunks(conn, table: str, ids: list[int], chunk: int = 500) -> None:
    """DELETE ``ids`` from ``table`` in bounded batches, then commit once.

    BUGFIX: both dedup routines used to build a single
    ``DELETE ... WHERE id IN (?,?,...)`` with one placeholder per row.  SQLite
    caps the number of bound parameters per statement, so a database holding
    more duplicates than that cap raises ``too many SQL variables`` and the
    whole dedup aborts — leaving the table exactly as dirty as before.

    Note we DELETE rather than set is_expired=1: is_expired hides the job from
    the UI and suppresses future re-scanning, which is wrong when the job is
    still active but was seen from two sources (e.g. Greenhouse API +
    official_careers scrape).  Deleting the lower-priority duplicate is safer:
    the canonical row (lowest id, from the first source seen) stays active.
    """
    for start in range(0, len(ids), chunk):
        batch = ids[start:start + chunk]
        placeholders = ",".join("?" * len(batch))
        conn.execute(
            f"DELETE FROM {table} WHERE id IN ({placeholders})",  # noqa: S608
            batch,
        )
    conn.commit()


def dedup_jobs(jobs: list[dict]) -> list[dict]:
    """Trusted observed identities only; merge sources instead of losing them."""
    from sponsorscout.core.job_identity import merge_job_rows
    return merge_job_rows(jobs)[0]


def dedup_jobs_in_db(conn) -> int:
    """Explicit compatibility API; backup + audit before trusted alias merges."""
    from sponsorscout.core.maintenance import cleanup_connection
    return int(cleanup_connection(conn)['merged'])


def dedup_companies_in_db(conn) -> int:
    """Remove duplicate company entries (same name, different case/spacing).

    BUGFIX (2024-Q4): previous version used `(row["name"] or "").strip().lower()`
    which crashes with `TypeError: object of type 'NoneType' has no len()`
    when the name is `NULL` — the schema technically allows NULL even though
    the UNIQUE INDEX on `LOWER(TRIM(name))` would reject empty strings at
    insert time. The defensive cast to `str` and the explicit skip below
    means a stray NULL in the companies table (e.g. from a hand-edited CSV
    import) no longer aborts the whole dedup run with a traceback. Those
    rows are simply kept as-is.
    """
    rows = conn.execute("SELECT id, name FROM companies ORDER BY id ASC").fetchall()
    seen: dict[str, int] = {}
    dupe_ids = []
    for row in rows:
        raw = row["name"]
        if raw is None:
            # Skip NULL names — can't meaningfully dedupe them, and we
            # don't want to delete them either (other rows may reference
            # them by id). Just leave them in the table.
            continue
        key = re.sub(r"\s+", " ", str(raw).strip().lower())
        if not key:
            continue
        if key in seen:
            dupe_ids.append(row["id"])
        else:
            seen[key] = row["id"]
    if dupe_ids:
        _delete_in_chunks(conn, "companies", dupe_ids)
    return len(dupe_ids)
