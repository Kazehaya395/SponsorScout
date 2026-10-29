import logging
from sponsorscout.core.url_normalizer import normalize_url
from sponsorscout.core.job_identity import posting_identity, merge_sources, weak_title

logger = logging.getLogger(__name__)


# F11 fix: company -> industry cache so bulk ingest does one lookup per
# company instead of one per row. Cleared whenever a company is saved.
_INDUSTRY_CACHE: dict[str, str] = {}


def _verdict_is_yes(value) -> bool:
    """True for an affirmative three-state verdict, in EITHER vocabulary.

    FIX P0-54: the scanners emit "Yes"/"No"/"Unknown" (jd_support.VERDICT_*)
    while the UI and the legacy boolean columns were written against
    "Y"/"N"/"Unknown". Both forms are accepted here so the two vocabularies
    can never silently disagree again.
    """
    return str(value or "").strip().lower() in {"y", "yes", "true", "1"}


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
    from sponsorscout.core.job_identity import administrative_action
    if (str(job.get('country_source')or'').casefold()!='manual'
            and str(job.get('source_type')or'').casefold()!='manual'
            and administrative_action(job.get('url',''),job.get('title',''))):
        # New observations only: never modify/hide an already stored row here.
        # Existing-data exclusions require the explicit backup/audit review.
        return False
    # Defense-in-depth (locked decision #1/#4): when three-state verdict
    # columns are present, the legacy derived booleans are always recomputed
    # from them ('Y' -> 1, everything else -> 0) regardless of what the
    # caller passed, so Unknown can never leak into the UI as a hard "No".
    # FIX P0-54: this tested ``verdict == "y"``, but BOTH scanners store the
    # three-state verdicts as "Yes" / "No" / "Unknown" -- jd_support.py sets
    # VERDICT_YES = "Yes", and career_scanner / ats_scanner write that value
    # straight into eu_blue_card_verdict and relocation_support. "yes" != "y",
    # so the comparison was NEVER true and both derived booleans were pinned
    # to 0 for every job ever ingested.
    #
    # That single mismatch is why the Dashboard reported **EU Blue Card = 0
    # across 2,497 jobs**: not a detection failure, an equality test against
    # the wrong literal. has_relocation was dead in exactly the same way.
    #
    # Accepts both vocabularies now, so it cannot break again if a caller
    # emits the short form.
    job = {**job,
           **({"eu_blue_card": 1 if _verdict_is_yes(job.get("eu_blue_card_verdict")) else 0}
              if str(job.get("eu_blue_card_verdict") or "").strip() else {}),
           **({"has_relocation": 1 if _verdict_is_yes(job.get("relocation_support")) else 0}
              if str(job.get("relocation_support") or "").strip() else {})}
    normalized_url = normalize_url(job.get("url", ""))
    company_name = (job.get("company", "") or "").strip()
    if not normalized_url:
        return

    posting_key = posting_identity(normalized_url)
    try:
        existing = conn.execute(
            'SELECT * FROM jobs WHERE posting_key=? OR url=? '
            'ORDER BY (url=?) DESC, id LIMIT 1',
            (posting_key, normalized_url, normalized_url)).fetchone()
    except Exception as exc:
        # Legacy DBs are migrated at app start. Direct callers may still pass
        # an older schema; the normal UPSERT compatibility guard below adds it.
        if 'no such column: posting_key' not in str(exc):
            raise
        existing = conn.execute('SELECT * FROM jobs WHERE url=?',(normalized_url,)).fetchone()
    if existing is not None:
        previous = dict(existing)
        job['source_name'] = merge_sources(previous.get('source_name'),job.get('source_name'))
        # Same-URL refreshes need the same protection as a trusted alias.
        # Empty/metadata-only updates are not new evidence about this posting.
        if weak_title(job.get('title','')) and not weak_title(previous.get('title','')):
            job['title'] = previous['title']
        if not str(job.get('title') or '').strip():
            job['title'] = previous.get('title','')
        if previous.get('source_subtype') in ('direct','direct_employer') and job.get('source_subtype')=='recruiter':
            job['source_subtype'] = previous['source_subtype']
            job['company'] = company_name = previous.get('company') or company_name
        previous_marker=str(previous.get('support_evidence_type')or'')
        new_marker=str(job.get('support_evidence_type')or'')
        previous_detail=('jd_read_status=readable'in previous_marker or 'explicit_detail_sentence'in previous_marker)
        incoming_card=new_marker.startswith('listing_card_sentence')
        if previous_detail and incoming_card:
            # A listing teaser never replaces a real captured JD/known verdict.
            job['description']=previous.get('description')or job.get('description','')
            for field in ('visa_sponsorship','relocation_support','eu_blue_card_verdict','relocation_required',
                          'support_evidence','blue_card_evidence','support_confidence','support_evidence_url'):
                if previous.get(field)not in(None,''):
                    job[field]=previous[field]
            job['support_evidence_type']=merge_sources(previous_marker,new_marker)
        job['_replace_card_description']=bool(job.get('_description_authority')=='detail' and not previous_detail
                                               and previous.get('ats_source')=='assisted_browser')
        unknown = ('','unknown','not specified','n/a','?','none')
        if str(job.get('location') or '').strip().casefold() in unknown:
            job['location'] = previous.get('location','')
            if str(job.get('country') or '').strip().casefold() in unknown:
                job['country'] = previous.get('country','')
                job['country_source'] = previous.get('country_source','none')
        preserved = False
        for verdict in ('visa_sponsorship','relocation_support','eu_blue_card_verdict'):
            new = str(job.get(verdict) or '').casefold()
            old = str(previous.get(verdict) or '').casefold()
            evidence = previous.get('blue_card_evidence') if verdict=='eu_blue_card_verdict' else previous.get('support_evidence')
            rejected_by_fresh_detail=(job.get('_support_authoritative') and verdict in job.get('_support_guarded',())
                                      and 'manual'not in previous_marker.casefold())
            if new in ('','unknown','possible') and old in ('y','n','yes','no') and evidence and not rejected_by_fresh_detail:
                job[verdict] = previous[verdict]
                preserved = True
        if preserved:
            for field in ('support_evidence','blue_card_evidence'):
                job[field] = merge_sources(previous.get(field),job.get(field))
            job['support_confidence'] = max(float(previous.get('support_confidence') or 0),float(job.get('support_confidence') or 0))
            for field in ('support_evidence_url','support_evidence_type'):
                job[field] = job.get(field) or previous.get(field,'')
            job['support_evidence_type']=str(job.get('support_evidence_type')or'')+'; support_value_reused_previous'
        if previous.get('url') != normalized_url:
            # An already observed URL remains stable, never a fabricated alias.
            normalized_url = previous['url']
    # Verdict preservation may have changed the incoming values above.
    # Recompute the derived booleans AFTER all lossless merge decisions.
    for verdict,flag in (('eu_blue_card_verdict','eu_blue_card'),('relocation_support','has_relocation')):
        if str(job.get(verdict) or '').strip():
            job[flag] = int(_verdict_is_yes(job[verdict]))

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
            # BUGFIX: a MISS is not a fact.  The old code cached the fallback
            # value unconditionally, so the very first lookup for a company
            # that had no registry row yet pinned "" in the cache for the
            # whole process lifetime — and because that cache is only cleared
            # by save_company(), the backfill could never pick up an industry
            # that appeared later in the same run.  Only a real hit is cached;
            # a miss simply re-queries next time (the SELECT is indexed and
            # cheap, and misses are the minority once the registry is loaded).
            if job_industry:
                _INDUSTRY_CACHE[company_name] = job_industry
            else:
                _INDUSTRY_CACHE.pop(company_name, None)

    # Country chain. FIX P0-55: this preferred whatever `country` the caller
    # happened to pass and only fell back to the job's own location text.
    # That is backwards for this product: the country shown in the Dashboard
    # and the Search tab must describe where the JOB is, never where the
    # company is headquartered. A caller-supplied country has no provenance
    # here -- it may be a real ATS country field, or it may be a seed's
    # target_country / an HQ guess -- so it can no longer outrank the one
    # value that is provably about the job.
    #
    # Order is now: the job's own location text, then an explicitly supplied
    # country, then nothing. `country_source` records which, so a wrong
    # country is traceable instead of anonymous. A user's manual correction
    # is untouched -- that is protected by the `country_source='manual'`
    # CASE in the UPSERT below, not here.
    from sponsorscout.core.location_country import country_from_location
    from sponsorscout.core.location_evidence import observed_geography,is_location_hint
    _hint_source=str(job.get('location_source') or job.get('country_source') or '')
    _loc_text,_observed_country=observed_geography(job.get('location',''),job.get('raw_location',''),_hint_source)
    if is_location_hint(_hint_source):
        job=dict(job)
        job['location']=_loc_text
        if str(job.get('country_source') or '').casefold()!='manual':
            job['country']=''
            job['country_source']='job_location' if _observed_country else 'none'
    _loc_text = str(_loc_text or '').strip()
    _loc_country = ""
    if _loc_text and _loc_text.lower() not in ("unknown", "not specified"):
        try:
            _loc_country = (country_from_location(_loc_text) or "").strip()
        except Exception:
            _loc_country = ""
    _explicit = str(job.get("country", "") or "").strip()
    _passed_source = str(job.get("country_source", "") or "").strip().lower()
    if _passed_source == "manual" and _explicit:
        job_country = _explicit
        _country_source = "manual"
    elif _explicit and _passed_source in {'structured_job_country','job_address','observed_country_field','provider_country'}:
        job_country = _explicit
        _country_source = _passed_source
    elif _loc_country:
        job_country = _loc_country
        _country_source = "job_location"
    elif _explicit:
        job_country = _explicit
        _country_source = _passed_source or "provided"
    else:
        job_country = ""
        _country_source = "none"

    if not job_country and existing is not None:
        previous_country=str(dict(existing).get('country')or'').strip()
        previous_source=str(dict(existing).get('country_source')or'')
        if previous_country.casefold()not in ('','unknown','none','not specified','n/a','?') and not is_location_hint(previous_source):
            # A known observed/provider country cannot be erased just because
            # this fresh card only states an ambiguous city. This is retention,
            # not a new country inference or retrospective repair.
            job_country=previous_country
            _country_source=previous_source or 'provided'

    if job.get('_support_authoritative') or 'jd_read_status='in str(job.get('support_evidence_type')or''):
        from sponsorscout.core.integrity import check_integrity
        effective_country=(dict(existing).get('country') if existing is not None and dict(existing).get('country_source')=='manual' else job_country)
        if str(effective_country or '').strip().casefold() in ('unknown','none','not specified','n/a','?'):
            effective_country=''
        job,violations=check_integrity(dict(job,country=effective_country))
        if violations:
            job['support_evidence_type']=str(job.get('support_evidence_type')or'')+'; country_integrity='+','.join(violations)

    _sql = """INSERT INTO jobs
           (external_id, title, company, country, location, url, ats_source,
            source_type, source_subtype, source_name, description, trust_score, freshness_score,
            sponsorship_score, match_score, verified_active, is_expired,
            last_verified_at, remote_type, eu_blue_card, has_relocation, experience_level,
            experience_required, experience_min_years, experience_source,
            industry, ai_score,
            visa_sponsorship, relocation_support, eu_blue_card_verdict,
            relocation_required, support_confidence, support_evidence,
            support_evidence_url, support_evidence_type, blue_card_evidence,
            canonical_job_id, run_id, raw_location, country_source, category, domain, posting_key)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(url) DO UPDATE SET
             title=excluded.title,
             company=excluded.company,
             country=CASE WHEN COALESCE(country_source,'auto')='manual' THEN country ELSE excluded.country END,
             location=excluded.location,
             ats_source=excluded.ats_source,
             source_type=excluded.source_type,
             source_subtype=excluded.source_subtype,
             source_name=excluded.source_name,
             description=CASE
               WHEN LENGTH(COALESCE(excluded.description,'')) >= LENGTH(COALESCE(description,''))
                    AND COALESCE(excluded.description,'') != ''
               THEN excluded.description
               ELSE COALESCE(description, '')
             END,
             trust_score=excluded.trust_score,
             freshness_score=excluded.freshness_score,
             sponsorship_score=excluded.sponsorship_score,
             match_score=excluded.match_score,
             verified_active=excluded.verified_active,
             is_expired=excluded.is_expired,
-- BUGFIX: this column was missing from the UPDATE clause, so
             -- mark_verified()'s timestamp was silently dropped on every
             -- re-upsert of an existing URL.  The freshness query in the
             -- Tools tab selects `last_verified_at IS NULL OR < 7 days`, so
             -- the same oldest N rows were re-verified forever and the check
             -- never advanced to a new job.  COALESCE keeps a NULL (never
             -- verified) from erasing an older stamp.
             last_verified_at=COALESCE(excluded.last_verified_at, last_verified_at),
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
             country_source=CASE WHEN COALESCE(country_source,'auto')='manual' AND excluded.country_source!='manual' THEN 'manual' ELSE excluded.country_source END,
             category=COALESCE(NULLIF(excluded.category,'Other'), category),
             domain=COALESCE(NULLIF(excluded.domain,'Unknown'), domain),
             posting_key=excluded.posting_key"""
    _params = (
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
        _country_source,   # FIX P0-55: provenance, not a hardcoded "auto"
        job.get("category", "Other") or "Other",
        job.get("domain", "Unknown") or "Unknown",
        posting_key,
    )
    if job.get('_replace_card_description'):
        _sql=_sql.replace('description=CASE\n               WHEN LENGTH', 'description=CASE\n               WHEN 1 THEN excluded.description\n               WHEN LENGTH',1)
    try:
        conn.execute(_sql, _params)
    except Exception as _exc:
        if any('no column named ' + col in str(_exc) for col in ('category','domain','posting_key')):
            for _ddl in (
                "ALTER TABLE jobs ADD COLUMN category TEXT DEFAULT 'Other'",
                "ALTER TABLE jobs ADD COLUMN domain TEXT DEFAULT 'Unknown'",
                "ALTER TABLE jobs ADD COLUMN posting_key TEXT DEFAULT ''",
            ):
                try:
                    conn.execute(_ddl)
                except Exception:
                    pass
            conn.execute(_sql, _params)
        else:
            raise
    # Ingest extracted terms into job_terms table if provided
    terms = job.get("terms")
    if terms:
        try:
            # Get job_id for this URL
            job_row = conn.execute("SELECT id FROM jobs WHERE url=?", (normalized_url,)).fetchone()
            if job_row:
                jid = job_row[0]
                for kind, term in terms:
                    if term:
                        conn.execute(
                            "INSERT OR IGNORE INTO job_terms (job_id, kind, term) VALUES (?, ?, ?)",
                            (jid, str(kind), str(term))
                        )
        except Exception as e:
            logger.debug("Failed to store job_terms for %s: %s", normalized_url, e)

    if commit:
        conn.commit()


def mark_job_expired(conn, url: str):
    conn.execute(
        "UPDATE jobs SET verified_active=0, is_expired=1, freshness_score=0, updated_at=CURRENT_TIMESTAMP WHERE url=?",
        (normalize_url(url),)
    )
    conn.commit()
