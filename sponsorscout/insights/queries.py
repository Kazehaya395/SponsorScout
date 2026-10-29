"""SQL queries and data aggregation helpers for the Insights tab."""
from __future__ import annotations

import sqlite3
from typing import Any

from sponsorscout.db.database import get_connection
from sponsorscout.insights import cube


def skill_term_clause(skill_term: str, alias: str = "") -> tuple[str, list[Any]]:
    """Semi-join restricting a panel to jobs that carry one skill term.

    Used by the skill bars in the Insights tab: clicking `Python` has to filter
    every panel to jobs where `Python` was actually extracted, which is a
    property of `job_terms`, not of a `jobs` column. `alias` qualifies the outer
    row id because two of the panel queries read `jobs` under an alias, and an
    unqualified `jobs.id` there would silently bind to a second, uncorrelated
    copy of the table.
    """
    if not skill_term:
        return "", []
    qualified = f"{alias}.id" if alias else "jobs.id"
    return (
        f" AND EXISTS (SELECT 1 FROM job_terms jt WHERE jt.job_id = {qualified}"
        " AND jt.kind = 'skill' AND jt.term = ?)",
        [skill_term],
    )


def _build_filter_clause(
    run_id: str = "",
    country: str = "",
    source_subtype: str = "",  # "direct", "recruiter", or "" (all)
    category: str = "",
    domain: str = "",
    skill_term: str = "",
) -> tuple[str, list[Any]]:
    clauses = ["verified_active = 1", "is_expired = 0"]
    params: list[Any] = []

    if run_id and run_id.lower() not in ("all", ""):
        clauses.append("run_id = ?")
        params.append(run_id)

    if country and country.lower() not in ("all", "all countries", ""):
        clauses.append("country = ?")
        params.append(country)

    if source_subtype and source_subtype.lower() not in ("all", ""):
        clauses.append("source_subtype = ?")
        params.append(source_subtype)

    if category and category.lower() not in ("all", "all categories", ""):
        if category == "Other":
            clauses.append("(category IS NULL OR category = '' OR category = 'Other')")
        else:
            clauses.append("category = ?")
            params.append(category)

    if domain and domain.lower() not in ("all", "all domains", ""):
        if domain == "Unknown":
            clauses.append("(domain IS NULL OR domain = '' OR domain = 'Unknown')")
        else:
            clauses.append("domain = ?")
            params.append(domain)

    # Appended last: its placeholder has to bind after every filter above it.
    fragment, extra = skill_term_clause(skill_term)
    if fragment:
        clauses.append(fragment[5:])   # drop the leading AND
        params.extend(extra)

    return " WHERE " + " AND ".join(clauses) if clauses else "", params


def get_insights_coverage(
    db_path, run_id="", country="", source_subtype="", category="", domain="",
    skill_term=""
) -> dict[str, Any]:
    """Return coverage statistics: total jobs, categorized jobs, jobs with skills."""
    fast = cube.coverage(db_path, run_id=run_id, country=country,
                         source_subtype=source_subtype, category=category, domain=domain,
                         skill_term=skill_term)
    if fast is not None:
        return fast
    # `where` keeps the skill-term semi-join (valid here: FROM jobs, no alias);
    # the aliased job_terms query below rebuilds its own clause list.
    where, params = _build_filter_clause(run_id, country, source_subtype, category=category,
                                         domain=domain, skill_term=skill_term)
    with get_connection(db_path) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM jobs{where}", params).fetchone()[0]
        # Empty and NULL are the same thing to the category chart, which
        # groups them under 'Other' (COALESCE(NULLIF(category,''),'Other')).
        # Coverage used to count '' as categorised while the chart showed it as
        # Other, so the two disagreed on any row the scanner left blank.
        categorized = conn.execute(
            f"SELECT COUNT(*) FROM jobs{where}{' AND' if where else ' WHERE'} COALESCE(NULLIF(category, ''), 'Other') != 'Other'",
            params,
        ).fetchone()[0]
        
        # Jobs with at least one skill term. Rebuilt from an unfiltered clause
        # list: `where` carries a `jobs.id` semi-join that is invalid against the
        # aliased table below, so the term filter is re-added with the alias.
        plain_where, plain_params = _build_filter_clause(run_id, country, source_subtype,
                                                          category=category, domain=domain)
        skill_where = plain_where.replace("verified_active", "j.verified_active").replace("is_expired", "j.is_expired")
        if run_id and run_id.lower() not in ("all", ""):
            skill_where = skill_where.replace("run_id", "j.run_id")
        if country and country.lower() not in ("all", "all countries", ""):
            skill_where = skill_where.replace("country", "j.country")
        if source_subtype and source_subtype.lower() not in ("all", ""):
            skill_where = skill_where.replace("source_subtype", "j.source_subtype")
        if category and category.lower() not in ("all", "all categories", ""):
            skill_where = skill_where.replace("category", "j.category")
        if domain and domain.lower() not in ("all", "all domains", ""):
            skill_where = skill_where.replace("domain", "j.domain")
        term_fragment, term_params = skill_term_clause(skill_term, alias="j")
        skills_count = conn.execute(
            f"""SELECT COUNT(DISTINCT j.id) FROM jobs j
                JOIN job_terms t ON j.id = t.job_id
                {skill_where}{' AND' if skill_where else ' WHERE'} t.kind = 'skill'{term_fragment}""",
            plain_params + term_params,
        ).fetchone()[0]

    return {
        "total_jobs": total,
        "categorized_jobs": categorized,
        "jobs_with_skills": skills_count,
        "category_coverage_pct": round(categorized / total * 100, 1) if total else 0.0,
        "skill_coverage_pct": round(skills_count / total * 100, 1) if total else 0.0,
    }


def get_jobs_by_category(
    db_path, run_id="", country="", source_subtype="", domain="", skill_term=""
) -> list[tuple[str, int]]:
    """Return job counts grouped by category, descending."""
    fast = cube.jobs_by_category(db_path, run_id=run_id, country=country,
                                 source_subtype=source_subtype, domain=domain,
                                 skill_term=skill_term)
    if fast is not None:
        return fast
    where, params = _build_filter_clause(run_id, country, source_subtype, domain=domain,
                                         skill_term=skill_term)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"""SELECT COALESCE(NULLIF(category, ''), 'Other') as cat, COUNT(*) as cnt
                FROM jobs{where}
                GROUP BY cat ORDER BY cnt DESC, cat ASC""",
            params,
        ).fetchall()
        return [(r[0], r[1]) for r in rows]


def get_jobs_by_domain(
    db_path, run_id="", country="", source_subtype="", category="", skill_term=""
) -> list[tuple[str, int]]:
    """Return job counts grouped by employer domain, descending."""
    fast = cube.jobs_by_domain(db_path, run_id=run_id, country=country,
                                source_subtype=source_subtype, category=category,
                                skill_term=skill_term)
    if fast is not None:
        return fast
    where, params = _build_filter_clause(run_id, country, source_subtype, category=category,
                                         skill_term=skill_term)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"""SELECT COALESCE(NULLIF(domain, ''), 'Unknown') as dom, COUNT(*) as cnt
                FROM jobs{where}
                GROUP BY dom ORDER BY cnt DESC, dom ASC""",
            params,
        ).fetchall()
        return [(r[0], r[1]) for r in rows]


def get_top_skills(
    db_path, limit: int = 15, run_id="", country="", source_subtype="", category="", domain="",
    skill_term=""
) -> list[tuple[str, int]]:
    """Return most frequent skill terms found in JDs, descending."""
    fast = cube.top_skills(db_path, limit, run_id=run_id, country=country,
                            source_subtype=source_subtype, category=category, domain=domain,
                            skill_term=skill_term)
    if fast is not None:
        return fast
    where, params = _build_filter_clause(run_id, country, source_subtype, category=category,
                                         domain=domain)
    term_fragment, term_params = skill_term_clause(skill_term, alias="j")
    j_where = where.replace("verified_active", "j.verified_active").replace("is_expired", "j.is_expired")
    if run_id and run_id.lower() not in ("all", ""):
        j_where = j_where.replace("run_id", "j.run_id")
    if country and country.lower() not in ("all", "all countries", ""):
        j_where = j_where.replace("country", "j.country")
    if source_subtype and source_subtype.lower() not in ("all", ""):
        j_where = j_where.replace("source_subtype", "j.source_subtype")
    if category and category.lower() not in ("all", "all categories", ""):
        j_where = j_where.replace("category", "j.category")
    if domain and domain.lower() not in ("all", "all domains", ""):
        j_where = j_where.replace("domain", "j.domain")

    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"""SELECT t.term, COUNT(DISTINCT j.id) as cnt
                FROM job_terms t
                JOIN jobs j ON t.job_id = j.id
                {j_where}{' AND' if j_where else ' WHERE'} t.kind = 'skill'{term_fragment}
                GROUP BY t.term ORDER BY cnt DESC, t.term ASC""",
            params + term_params,
        ).fetchall()
        return [(r[0], r[1]) for r in rows]


def get_segregated_skills(
    db_path, run_id="", country="", source_subtype="", category="", domain="", skill_term=""
) -> dict[str, list[tuple[str, int]]]:
    """Return top skills segregated into 'technical' and 'soft' lists.

    `skill_term` restricts the whole ranking to jobs carrying that term, which
    is what a click on a skill bar asks for: the remaining bars then read as the
    skills that co-occur with it.
    """
    from sponsorscout.insights.terms import get_soft_skill_terms

    soft_terms = get_soft_skill_terms()

    all_skills = get_top_skills(
        db_path, run_id=run_id, country=country, source_subtype=source_subtype, category=category, domain=domain, skill_term=skill_term
    )
    tech = []
    soft = []
    for term, count in all_skills:
        if term in soft_terms:
            soft.append((term, count))
        else:
            tech.append((term, count))

    return {
        "all": all_skills,
        "technical": tech,
        "soft": soft,
    }


def get_sponsorship_by_category(
    db_path, run_id="", country="", source_subtype="", category="", domain="",
    skill_term=""
) -> list[dict[str, Any]]:
    """Return sponsorship and relocation counts per category (Yes / No / Unknown separate)."""
    fast = cube.sponsorship_by_category(db_path, run_id=run_id, country=country,
                                         source_subtype=source_subtype, category=category, domain=domain,
                                         skill_term=skill_term)
    if fast is not None:
        return fast
    where, params = _build_filter_clause(run_id, country, source_subtype, category=category, domain=domain,
                                         skill_term=skill_term)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"""SELECT COALESCE(NULLIF(category, ''), 'Other') as cat,
                       SUM(CASE WHEN visa_sponsorship = 'Y' THEN 1 ELSE 0 END) as visa_yes,
                       SUM(CASE WHEN visa_sponsorship = 'N' THEN 1 ELSE 0 END) as visa_no,
                       SUM(CASE WHEN visa_sponsorship NOT IN ('Y', 'N') THEN 1 ELSE 0 END) as visa_unk,
                       SUM(CASE WHEN relocation_support = 'Y' THEN 1 ELSE 0 END) as reloc_yes,
                       SUM(CASE WHEN relocation_support = 'N' THEN 1 ELSE 0 END) as reloc_no,
                       SUM(CASE WHEN relocation_support NOT IN ('Y', 'N') THEN 1 ELSE 0 END) as reloc_unk,
                       COUNT(*) as total
                FROM jobs{where}
                GROUP BY cat ORDER BY total DESC, cat ASC""",
            params,
        ).fetchall()

        return [
            {
                "category": r[0],
                "visa_yes": r[1] or 0,
                "visa_no": r[2] or 0,
                "visa_unk": r[3] or 0,
                "reloc_yes": r[4] or 0,
                "reloc_no": r[5] or 0,
                "reloc_unk": r[6] or 0,
                "total": r[7] or 0,
            }
            for r in rows
        ]


def get_country_category_matrix(
    db_path, top_countries: int = 8, top_categories: int = 6, run_id="", source_subtype=""
) -> dict[str, Any]:
    """Return cross-tabulation matrix of Country x Category counts."""
    where, params = _build_filter_clause(run_id, "", source_subtype)
    with get_connection(db_path) as conn:
        # Get top countries
        c_rows = conn.execute(
            f"""SELECT country, COUNT(*) as cnt FROM jobs{where}
                {' AND' if where else ' WHERE'} country IS NOT NULL AND country != ''
                GROUP BY country ORDER BY cnt DESC LIMIT ?""",
            params + [top_countries],
        ).fetchall()
        countries = [r[0] for r in c_rows]

        # Get top categories
        cat_rows = conn.execute(
            f"""SELECT COALESCE(NULLIF(category, ''), 'Other') as cat, COUNT(*) as cnt
                FROM jobs{where}
                GROUP BY cat ORDER BY cnt DESC LIMIT ?""",
            params + [top_categories],
        ).fetchall()
        categories = [r[0] for r in cat_rows]

        matrix = {c: {cat: 0 for cat in categories} for c in countries}
        if countries and categories:
            placeholders_c = ",".join("?" for _ in countries)
            placeholders_cat = ",".join("?" for _ in categories)
            m_where = (f"{where} AND" if where else "WHERE") + f" country IN ({placeholders_c})"
            rows = conn.execute(
                f"""SELECT country, COALESCE(NULLIF(category, ''), 'Other') as cat, COUNT(*)
                    FROM jobs {m_where}
                    GROUP BY country, cat""",
                params + countries,
            ).fetchall()
            for c, cat, cnt in rows:
                if c in matrix and cat in matrix[c]:
                    matrix[c][cat] = cnt

    return {
        "countries": countries,
        "categories": categories,
        "matrix": matrix,
    }


def has_pending_insights(db_path) -> bool:
    """Unknown/no-skill is a completed result; only input/taxonomy changes count."""
    from sponsorscout.insights.terms import taxonomy_version
    conn = get_connection(db_path)
    try:
        cols = {c[1] for c in conn.execute('PRAGMA table_info(jobs)')}
        if 'insights_dirty' not in cols:
            return bool(conn.execute('SELECT 1 FROM jobs LIMIT 1').fetchone())
        return bool(conn.execute(
            "SELECT 1 FROM jobs WHERE verified_active=1 AND is_expired=0 "
            "AND (insights_dirty=1 OR insights_version<>?) LIMIT 1",
            (taxonomy_version(),)).fetchone())
    finally:
        conn.close()


def backfill_existing_job_terms(db_path, force_all: bool = False, *,
                                batch_size: int = 128,
                                progress_callback=None, cancel_event=None) -> int:
    """Bounded snapshots -> CPU without a transaction -> short atomic writes.

    Each snapshot carries an input-generation token. A concurrent JD/title edit
    cannot be stamped complete with stale results. Rows inserted during this
    bounded pass remain dirty for the next coalesced refresh.
    """
    import time
    from sponsorscout.insights.terms import extract_terms, taxonomy_version
    from sponsorscout.core.activity import wait_for_ingestion_idle
    version = taxonomy_version()  # one-time packaged resource load; never a scan preflight
    batch_size = max(1, min(500, int(batch_size)))

    def notify(done, total, phase):
        if callable(progress_callback):
            progress_callback(done, total, phase)

    def idle(done, total):
        return wait_for_ingestion_idle(db_path, cancel_event,
                                      lambda: notify(done, total, 'waiting'))

    if not idle(0, 0):
        return 0
    conn = get_connection(db_path)
    try:
        cols = {c[1] for c in conn.execute('PRAGMA table_info(jobs)')}
        if 'insights_dirty' not in cols:
            # Compatibility for a legacy DB passed directly by a CLI caller.
            # The desktop normally performs this tiny schema migration once.
            from sponsorscout.db.database import _apply_migrations
            _apply_migrations(conn)
        base = 'verified_active=1 AND is_expired=0'
        params = []
        if not force_all:
            base += ' AND (insights_dirty=1 OR insights_version<>?)'
            params.append(version)
        snapshot_ids = [row[0] for row in conn.execute('SELECT id FROM jobs WHERE '+base+' ORDER BY id',params)]
        total = len(snapshot_ids)
    finally:
        conn.close()
    notify(0, total, 'starting')
    processed,updated,offset = 0,0,0
    while offset<total:
        if not idle(processed, total):
            break
        batch_ids = snapshot_ids[offset:offset+batch_size]
        offset += len(batch_ids)
        conn = get_connection(db_path)
        try:
            placeholders = ','.join('?' for _ in batch_ids)
            rows = conn.execute(
                'SELECT id,title,location,experience_level,description,industry, '
                'support_evidence,company,insights_generation FROM jobs WHERE id IN ('+
                placeholders+') ORDER BY id',batch_ids).fetchall()
        finally:
            conn.close()  # no open read cursor/snapshot during the CPU phase
        # Deletions after the ID snapshot are accounted for without inflating
        # the scope with unrelated rows that become dirty later in this pass.
        processed += len(batch_ids)-len(rows)
        if not rows:
            notify(processed,total,'saved')
            continue
        results = []
        for row in rows:
            if cancel_event is not None and cancel_event.is_set():
                break
            jid,title,location,experience,description,industry,evidence,company,generation = row
            result = extract_terms(title or '', location or '', experience or '',
                                   f"{description or ''}\n{evidence or ''}".strip(),
                                   industry or '', company=company or '')
            results.append((jid,generation,result))
            processed += 1
            if processed % 16 == 0:
                notify(processed,total,'extracting')
        if not idle(processed,total):
            break
        # A transaction covers only already-computed results, never regex work.
        # BEGIN IMMEDIATE avoids upgrading an old read snapshot while a separate
        # ingester commits. Rollback/retry keeps a whole batch retryable.
        for attempt in range(4):
            writer = get_connection(db_path)
            try:
                writer.execute('BEGIN IMMEDIATE')
                batch_updated = 0
                for jid,generation,result in results:
                    cursor = writer.execute(
                        'UPDATE jobs SET category=?,domain=?,insights_dirty=0,insights_version=?, '
                        'insights_updated_at=CURRENT_TIMESTAMP WHERE id=? AND insights_generation=? AND verified_active=1 AND is_expired=0',
                        (result['category'],result['domain'],version,jid,generation))
                    if not cursor.rowcount:
                        continue  # newer input stays dirty, retaining its old usable chart data
                    writer.execute('DELETE FROM job_terms WHERE job_id=?',(jid,))
                    writer.executemany('INSERT OR IGNORE INTO job_terms(job_id,kind,term) VALUES(?,?,?)',
                                       [(jid,kind,term) for kind,term in result['terms'] if term])
                    batch_updated += 1
                writer.commit()
                updated += batch_updated
                break
            except sqlite3.OperationalError as exc:
                writer.rollback()
                if 'locked' not in str(exc).lower() or attempt == 3:
                    raise
                time.sleep(0.1 * (attempt + 1))
            finally:
                writer.close()
        notify(processed,total,'saved')
        if cancel_event is not None and cancel_event.is_set():
            break
    notify(processed,total,'complete')
    return updated
