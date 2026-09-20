from sponsorscout.core.url_normalizer import normalize_url


# F11 fix: company -> industry cache so bulk ingest does one lookup per
# company instead of one per row. Cleared whenever a company is saved.
_INDUSTRY_CACHE: dict[str, str] = {}


def _norm_name(name: str) -> str:
    """Normalize company name for case/whitespace-insensitive dedup."""
    return " ".join((name or "").strip().lower().split())


def save_company(conn, company):
    """
    Insert or update a company.

    B1 fix: previous version used INSERT OR IGNORE on the UNIQUE name column,
    which silently dropped new companies whose name collided (case/whitespace).
    Now we use ON CONFLICT to UPDATE the existing row instead.
    """
    name = company.get("name", "").strip()
    if not name:
        return
    conn.execute(
        """INSERT INTO companies
           (name, country, ats_type, careers_url, industry,
            sponsorship_history_score, english_friendly_score, remote_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET
             country=excluded.country,
             ats_type=excluded.ats_type,
             careers_url=COALESCE(NULLIF(excluded.careers_url, ''), companies.careers_url),
             industry=COALESCE(NULLIF(excluded.industry, ''), companies.industry),
             sponsorship_history_score=excluded.sponsorship_history_score,
             english_friendly_score=excluded.english_friendly_score,
             remote_score=excluded.remote_score,
             updated_at=CURRENT_TIMESTAMP""",
        (
            name,
            company.get("country", ""),
            company.get("ats_type", ""),
            company.get("careers_url", ""),
            company.get("industry", ""),
            int(company.get("sponsorship_history", company.get("sponsorship_history_score", 0)) or 0),
            int(company.get("english_friendly", company.get("english_friendly_score", 0)) or 0),
            int(company.get("remote_score", 0) or 0),
        ),
    )
    conn.commit()
    _INDUSTRY_CACHE.clear()


def upsert_job(conn, job, commit: bool = True):
    """
    B2 fix: previous version used INSERT OR IGNORE + an unconditional UPDATE
    keyed on url. If two jobs shared the same normalized URL, the UPDATE
    could silently rewrite the wrong row's columns. Now the INSERT/UPDATE
    preserves all persisted fields, including experience_level, while still
    using the unique job URL as the stable key.

    B3 fix: when no industry is provided on the job record, backfill from
    the companies table using the company name so the column is always
    populated for new inserts and updates.

    commit=False batches the write into the caller's transaction (bulk
    ingest commits every 500 rows instead of once per row — F11). The
    default True preserves one-shot behaviour for single-row callers.

    country_source: rows flagged 'manual' (user-corrected countries) are
    never overwritten by rescans — only an explicit 'manual' write or a
    forced migration changes them.
    """
    # Defense-in-depth (locked decision #1/#4): when three-state verdict
    # columns are present, the legacy derived booleans are always recomputed
    # from them ('Y' -> 1, everything else -> 0) regardless of what the
    # caller passed, so Unknown can never leak into the UI as a hard "No".
    verdict = str(job.get("eu_blue_card_verdict") or "").strip().lower()
    if verdict:
        job = {**job, "eu_blue_card": 1 if verdict == "y" else 0}
    verdict = str(job.get("relocation_support") or "").strip().lower()
    if verdict:
        job = {**job, "has_relocation": 1 if verdict == "y" else 0}
    normalized_url = normalize_url(job.get("url", ""))
    company_name = (job.get("company", "") or "").strip()
    if not normalized_url:
        return

    # Backfill industry from the companies table if not provided on the job
    # (F11: cached per company; save_company() clears the cache on write).
    job_industry = job.get("industry", "")
    if not job_industry and company_name:
        if company_name in _INDUSTRY_CACHE:
            job_industry = _INDUSTRY_CACHE[company_name]
        else:
            try:
                row = conn.execute(
                    "SELECT industry FROM companies WHERE name=? AND industry != '' LIMIT 1",
                    (company_name,),
                ).fetchone()
                if row:
                    job_industry = row["industry"]
            except Exception:
                pass
            if len(_INDUSTRY_CACHE) > 5000:
                _INDUSTRY_CACHE.clear()
            _INDUSTRY_CACHE[company_name] = job_industry

    # Country chain (Q8 decision): explicit job country wins; otherwise
    # derive a best-effort country from the job location text.
    job_country = str(job.get("country", "") or "").strip()
    if not job_country:
        from sponsorscout.core.location_country import country_from_location
        job_country = country_from_location(str(job.get("location", "") or ""))

    conn.execute(
        """INSERT INTO jobs
           (external_id, title, company, country, location, url, ats_source,
            source_type, source_subtype, source_name, description, trust_score, freshness_score,
            sponsorship_score, match_score, verified_active, is_expired,
            last_verified_at, remote_type, eu_blue_card, has_relocation, experience_level,
            experience_required, experience_min_years, experience_source,
            industry, ai_score,
            visa_sponsorship, relocation_support, eu_blue_card_verdict,
            relocation_required, support_confidence, support_evidence,
            support_evidence_url, support_evidence_type, blue_card_evidence,
            canonical_job_id, run_id, raw_location, country_source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(url) DO UPDATE SET
             title=excluded.title,
             company=excluded.company,
             country=CASE WHEN COALESCE(country_source,'auto')='manual' THEN country ELSE excluded.country END,
             location=excluded.location,
             ats_source=excluded.ats_source,
             source_type=excluded.source_type,
             source_subtype=excluded.source_subtype,
             source_name=excluded.source_name,
             description=excluded.description,
             trust_score=excluded.trust_score,
             freshness_score=excluded.freshness_score,
             sponsorship_score=excluded.sponsorship_score,
             match_score=excluded.match_score,
             verified_active=excluded.verified_active,
             is_expired=excluded.is_expired,
             last_seen_at=CURRENT_TIMESTAMP,
             updated_at=CURRENT_TIMESTAMP,
             remote_type=excluded.remote_type,
             eu_blue_card=excluded.eu_blue_card,
             has_relocation=excluded.has_relocation,
             experience_level=COALESCE(NULLIF(excluded.experience_level,''), experience_level),
             experience_required=COALESCE(NULLIF(excluded.experience_required,''), experience_required),
             experience_min_years=COALESCE(excluded.experience_min_years, experience_min_years),
             experience_source=COALESCE(NULLIF(excluded.experience_source,''), experience_source),
             industry=COALESCE(NULLIF(excluded.industry,''), industry),
             ai_score=excluded.ai_score,
             visa_sponsorship=excluded.visa_sponsorship,
             relocation_support=excluded.relocation_support,
             eu_blue_card_verdict=excluded.eu_blue_card_verdict,
             relocation_required=excluded.relocation_required,
             support_confidence=excluded.support_confidence,
             support_evidence=excluded.support_evidence,
             support_evidence_url=excluded.support_evidence_url,
             support_evidence_type=excluded.support_evidence_type,
             blue_card_evidence=excluded.blue_card_evidence,
             canonical_job_id=excluded.canonical_job_id,
             run_id=excluded.run_id,
             raw_location=COALESCE(NULLIF(excluded.raw_location,''), raw_location),
             country_source=CASE WHEN COALESCE(country_source,'auto')='manual' AND excluded.country_source!='manual' THEN 'manual' ELSE excluded.country_source END""",
        (
            job.get("external_id", ""),
            job.get("title", ""),
            company_name,
            job_country,
            job.get("location", ""),
            normalized_url,
            job.get("ats_source", ""),
            job.get("source_type", "verified"),
            job.get("source_subtype", "direct"),
            job.get("source_name", ""),
            job.get("description", ""),
            int(job.get("trust_score", 0) or 0),
            int(job.get("freshness_score", 0) or 0),
            int(job.get("sponsorship_score", 0) or 0),
            int(job.get("match_score", 0) or 0),
            int(bool(job.get("verified_active", True))),
            int(bool(job.get("is_expired", False))),
            job.get("last_verified_at", None),
            job.get("remote_type", "onsite"),
            int(job.get("eu_blue_card", 0) or 0),
            int(job.get("has_relocation", 0) or 0),
            job.get("experience_level", ""),
            # FIX P0-30: experience requirement columns (scanner-extracted).
            job.get("experience_required", ""),
            job.get("experience_min_years", None),
            job.get("experience_source", ""),
            job_industry,
            int(job.get("ai_score", 0) or 0),
            # ── Scan evidence columns ────────────────────────────────────────
            job.get("visa_sponsorship", ""),
            job.get("relocation_support", ""),
            job.get("eu_blue_card_verdict", ""),
            job.get("relocation_required", ""),
            float(job.get("support_confidence", 0) or 0),
            job.get("support_evidence", ""),
            job.get("support_evidence_url", ""),
            job.get("support_evidence_type", ""),
            job.get("blue_card_evidence", ""),
            job.get("canonical_job_id", ""),
            job.get("run_id", ""),
            job.get("raw_location", ""),
            job.get("country_source", "auto"),
        ),
    )
    if commit:
        conn.commit()


def mark_job_expired(conn, url: str):
    conn.execute(
        "UPDATE jobs SET verified_active=0, is_expired=1, freshness_score=0, updated_at=CURRENT_TIMESTAMP WHERE url=?",
        (normalize_url(url),)
    )
    conn.commit()
