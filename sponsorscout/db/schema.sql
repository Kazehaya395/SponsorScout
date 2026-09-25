CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    country TEXT NOT NULL,
    ats_type TEXT NOT NULL,
    careers_url TEXT NOT NULL,
    industry TEXT DEFAULT '',
    sponsorship_history_score INTEGER DEFAULT 0,
    english_friendly_score INTEGER DEFAULT 0,
    remote_score INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    external_id TEXT,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    country TEXT DEFAULT '',
    country_source TEXT DEFAULT 'auto',
    location TEXT DEFAULT '',
    raw_location TEXT DEFAULT '',
    url TEXT UNIQUE NOT NULL,
    ats_source TEXT DEFAULT '',
    source_type TEXT DEFAULT 'verified',
    -- Phase 3: distinguishes aggregator-sourced jobs (e.g., LinkedIn, Indeed,
    -- Remotive) from direct career-page jobs.  Used by the scanner to decide
    -- whether the per-card 'company' field should be trusted over the registry.
    source_subtype TEXT DEFAULT 'direct',
    source_name TEXT DEFAULT '',
    description TEXT DEFAULT '',
    trust_score INTEGER DEFAULT 0,
    freshness_score INTEGER DEFAULT 0,
    sponsorship_score INTEGER DEFAULT 0,
    match_score INTEGER DEFAULT 0,
    verified_active INTEGER DEFAULT 0,
    is_expired INTEGER DEFAULT 0,
    first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_verified_at TEXT,
    remote_type TEXT DEFAULT 'onsite',
    eu_blue_card INTEGER DEFAULT 0,
    has_relocation INTEGER DEFAULT 0,
    -- BUGFIX: added in v0.1.1 to support the new "Experience" filter
    -- (intern / entry / mid / senior / lead / exec). Derived by the
    -- normalizer at scan time from the job title; stored as a TEXT
    -- because experience-level is a soft enum (the model never changes)
    -- and a tiny one. NULL means "not yet classified" (legacy rows).
    experience_level TEXT DEFAULT '',
    -- FIX P0-30: scanner-extracted experience requirement columns
    -- (additive; NULL/'' = not yet classified, never a hard fact).
    experience_required TEXT DEFAULT '',
    experience_min_years REAL,
    experience_source TEXT DEFAULT '',
    -- NEW: industry tag sourced from company registry (v0.2.0)
    industry TEXT DEFAULT '',
    -- AI domain detection: ★ when AI keywords detected in job listing
    ai_score INTEGER DEFAULT 0,
    -- ── Scan evidence columns (PySide6 restart migration) ────────────────────
    -- Honest three-state verdicts from the shared JD support detector:
    -- 'Y' / 'N' / 'Unknown' ('' for legacy rows never classified).
    -- The legacy INTEGER columns above (eu_blue_card, has_relocation) are
    -- derived booleans (verdict 'Y' -> 1, else 0) kept so existing queries
    -- keep working; these TEXT columns are the authoritative values the UI
    -- renders.  Unknown is never coerced to a hard "No".
    visa_sponsorship TEXT DEFAULT '',
    relocation_support TEXT DEFAULT '',
    eu_blue_card_verdict TEXT DEFAULT '',
    relocation_required TEXT DEFAULT '',
    support_confidence REAL DEFAULT 0,
    support_evidence TEXT DEFAULT '',
    support_evidence_url TEXT DEFAULT '',
    support_evidence_type TEXT DEFAULT '',
    blue_card_evidence TEXT DEFAULT '',
    -- Provenance: which scan produced / last confirmed this row.
    canonical_job_id TEXT DEFAULT '',
    run_id TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- B2 fix: case/whitespace-insensitive uniqueness on company name to prevent
-- duplicates like 'ING' / 'ing' / '  Ing  ' from coexisting.
CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_name_norm
    ON companies(LOWER(TRIM(name)));

-- ── Scan evidence (per-scan logs) ────────────────────────────────────────────
-- One row per scan execution (Quick / Full / Full+Detail).  The scan log keeps
-- the per-company outcomes exactly as the algorithm scripts emit them, so the
-- Tools tab can show "scan history" evidence without touching job rows.
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

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY,
    job_url TEXT UNIQUE NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'saved',
    applied_at TEXT,
    next_followup_at TEXT,
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jobs_title ON jobs(title);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_country ON jobs(country);
CREATE INDEX IF NOT EXISTS idx_jobs_sponsorship ON jobs(sponsorship_score);
CREATE INDEX IF NOT EXISTS idx_jobs_verified ON jobs(verified_active, is_expired);
CREATE INDEX IF NOT EXISTS idx_jobs_match ON jobs(match_score);
CREATE INDEX IF NOT EXISTS idx_jobs_url ON jobs(url);
CREATE INDEX IF NOT EXISTS idx_jobs_firstseen ON jobs(first_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_sponsored_fresh ON jobs(sponsorship_score DESC, first_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_source_subtype ON jobs(source_subtype);
CREATE INDEX IF NOT EXISTS idx_jobs_run_id ON jobs(run_id);
CREATE INDEX IF NOT EXISTS idx_jobs_canonical ON jobs(canonical_job_id);
-- Perf (Dashboard KPI refresh, also every scan tick): these COUNTs filter on
-- source_type and on (remote_type + verified flags) over the whole jobs table.
CREATE INDEX IF NOT EXISTS idx_jobs_source_type ON jobs(source_type);
CREATE INDEX IF NOT EXISTS idx_jobs_remote_verified
    ON jobs(remote_type, verified_active, is_expired);