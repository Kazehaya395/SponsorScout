# SponsorScout — CODEBASE Encyclopedia + Developer Guide

> Generated 2026-10-05 from a full read of the working tree (package
> `sponsorscout/`, `tools/`, build scripts, seeds, tests).
> Companion to `README.md` (user guide) and `extra_for_dev_purpose(do not delete)/cleanup.txt`
> (personal ops cheat-sheet). This file is the *architecture + function
> reference + safe-modification manual*.

---
## 0. TL;DR for a new developer

1. `sponsorscout/main.py` boots locale + Qt theme + `ui/app.py::SponsorScoutApp`.
2. `ui/app.py` assembles 5 tabs from `ui/tabs/`; Tools tab drives scans.
3. `application/scan_coordinator.py` runs `scanning/pipeline.py::run_scan`
   in a worker thread with pause/stop events + batched Qt signals.
4. `scanning/pipeline.py` runs ATS APIs first (`scanning/ats/`), then the
   career crawler (`scanning/career/`), normalises every row via `_row_to_job`,
   guards verdicts via `core/integrity.py::check_integrity`, scores via
   `derive_sponsorship_score`, persists via `core/persistence.py::upsert_job`.
5. Text evidence comes from `scanning/jd_support.py::JDSupportDetector`
   (three-state `Yes/No/Unknown`, never a fabricated No).
6. Storage is SQLite (`db/database.py` + `db/schema.sql`), paths via
   `paths.py` (`%APPDATA%/SponsorScout` on Windows, `~/.sponsorscout` else).
7. Seeds are v7 CSVs (`application/seed_manager.py` + `data/seeds/`); user
   edits live in the per-user copy, never in the bundle.
8. EN/IT strings via `i18n.py`; tests in `sponsorscout/tests/`
   (`python -m pytest sponsorscout/tests`).

## 1. Repository map (what lives where)

```text
SponsorScout/
  sponsorscout/                  # shipped package (bundled by PyInstaller)
    main.py                      # GUI entry point + frozen self-check
    paths.py                     # per-user dirs, DB path, Playwright env fix
    i18n.py                      # EN/IT dictionary + locale load/save
    application/
      scan_coordinator.py        # Qt worker-thread scan orchestration
      seed_manager.py            # v7 seed CSV read/validate/write/migrate
    core/                        # pure, UI-free business rules
      ats_detection.py           # careers-URL -> (provider, board token)
      dedup.py                   # in-memory + in-DB job/company dedup
      http_client.py             # pooled requests Session + bot-block check
      integrity.py               # location-aware Yes->Unknown guards
      location_country.py        # raw location string -> canonical country
      normalizer.py              # title/country/location/experience cleanup
      persistence.py             # save_company / upsert_job (DB writes)
      url_normalizer.py          # tracking-param strip, order-preserving
      verification.py            # freshness stamps (SQLite-format time)
      verification_service.py    # URL liveness check (active/expired/?)
    db/
      schema.sql                 # canonical DDL (companies/jobs/scans/apps)
      database.py                # connections, migrations, all queries
      migrate_countries.py       # one-time country re-derivation
    models/job.py                # Job dataclass (schema mirror)
    scanning/
      common.py                  # check_control + host-sized pools + UA flags
      jd_support.py              # JD evidence classifier (Yes/No/Unknown)
      pipeline.py                # ATS->career orchestration + CSV ingest
      ats/ats_scanner.py         # official ATS-board APIs (fast phase)
      career/career_scanner.py   # generic career-page crawler (deep phase)
    services/browser_fetcher.py  # HTTP-first + Playwright fallback fetch
    scripts/
      run_scan.py                # CLI scan wrapper (--quick/--company/--dedup)
      backfill_industry.py       # one-time industry backfill
    ui/
      app.py                     # QMainWindow + header + 5 tabs + live timer
      style.py                   # light palette + global QSS
      tabs/
        dashboard.py             # KPI cards + top companies/countries/empties
        search.py                # filter row + results table + verdict dots
        applications.py          # saved-application pipeline tracker
        tools.py                 # scan control + history + dedup + verify
        data_management.py       # in-app seed CSV editor
    data/
      default_profile.json / country_profile.json
      sponsorscout.png/.ico + icons/*.png
      seeds/*.csv                # bundled v7 defaults (ATS/career/remote)
    tests/                       # ~20 pytest modules (see section 8)
  tools/
    find_dead_code.py            # read-only unused-import/def/i18n reporter
    find_dead_code.allow         # deliberate keepers (public API)
    check_readme_anchors.py      # in-page markdown link validator
  assets/*.png                   # README screenshots
  build_exe.ps1 / build_deb.sh / build_rpm.sh / installer.iss
  SponsorScout.spec              # PyInstaller build SOURCE (never delete)
  extra_for_dev_purpose(do not delete)/  # legacy reference (see section 10)
  requirements.txt / pyproject.toml / MANIFEST.in
  README.md (users) / CODEBASE.md (this file, developers)
```

## 2. Runtime dataflow (one scan, end to end)

```text
Tools tab [Start] -> ScanCoordinator.start()
  -> worker thread -> pipeline.run_scan(method="full")
       -> seed_manager.user_*_path() rows (ATS + career + remote)
       -> ats_scanner (API boards, fast) -> CSV artifacts in SCAN_OUTPUT_DIR
       -> career_scanner (static-HTML fast path -> Playwright DOM fallback)
       -> pipeline._row_to_job per row:
            _as_verdict / _remote_type / _job_country / _exp_level
            -> check_integrity (Yes->Unknown demotions only)
            -> derive_sponsorship_score
            -> persistence.upsert_job (ON CONFLICT(url) upsert)
       -> scan_runs/scan_log/scan_events rows (Tools history evidence)
  -> progress chunks -> Tools log + PROGRESS: bar ticks
  -> finished(summary) -> app._refresh_all() (Dashboard/Search/Tools)
```

Key files per hop: `ui/tabs/tools.py::start_scan`,
`application/scan_coordinator.py::start`, `scanning/pipeline.py::run_scan`,
`scanning/ats/ats_scanner.py`, `scanning/career/career_scanner.py`,
`scanning/jd_support.py`, `core/integrity.py`, `core/persistence.py`,
`db/database.py`, `ui/app.py::_refresh_all`.

## 3. Module-by-module encyclopedia

### 3.1 `sponsorscout/main.py` — entry point + frozen self-check

| Function | Signature | What it does, inputs/outputs, gotchas |
|---|---|---|
| `_self_check_write` | `(msg: str) -> None` | Prints to stdout/stderr/`__stdout__`/`__stderr__`, first that works. Survives console-less windowed builds (`console=False` in spec). Never raises. |
| `self_check` | `(try_browser: bool) -> int` | Headless smoke test used by all three build scripts. Imports `sponsorscout.paths` (side effect: sets `PLAYWRIGHT_BROWSERS_PATH` for frozen bundles), imports `playwright.sync_api`, optionally launches Chromium headless with `--no-sandbox --disable-dev-shm-usage`. Returns 0/1. No Qt started. |
| `main` | `() -> None` | `logging.basicConfig(INFO)`, `load_saved_locale()`, `QApplication(Fusion + light palette + QSS)`, deferred `SponsorScoutApp()` import (needs a live `QApplication`), `show()` + `app.exec()`. Handles `--self-check`/`--self-check-browser` first. |
| `SELF_CHECK_FLAG`, `SELF_CHECK_BROWSER_FLAG` | constants | `"--self-check"`, `"--self-check-browser"`. Build scripts call the frozen exe with these and abort on non-zero exit. |

### 3.2 `sponsorscout/paths.py` — where mutable data lives

| Function/const | Details |
|---|---|
| `APP_DIR_ENV="SPONSORSCOUT_DATA_DIR"`, `DB_PATH_ENV="SPONSORSCOUT_DB_PATH"` | Env overrides. Tests set `SPONSORSCOUT_DATA_DIR` to tmp before importing `paths` (see `tests/conftest.py::data_dir`). |
| `_configure_bundled_playwright_browsers_path()` | Import-time side effect: if `sys.frozen` and `<exe>/_playwright/` exists and no explicit env, sets `PLAYWRIGHT_BROWSERS_PATH`. Fixes first-run-after-install on Windows where Inno's HKCU env write has not propagated yet. Respects user override. `main.py` imports `sponsorscout.paths` for this side effect (see `find_dead_code.allow`). |
| `_get_windows_appdata_dir(app_name)` | `%APPDATA%/app_name`, fallback `~/.<lower>`. Never `Program Files` (not writable). |
| `get_user_data_dir()` | Env override else `%APPDATA%/SponsorScout` (nt) else `~/.sponsorscout`. |
| `USER_DATA_DIR`, `DB_PATH`, `SEEDS_DIR`, `SCAN_OUTPUT_DIR` | Module constants computed at import. `SEEDS_DIR=<data>/seeds` (user seed copies), `SCAN_OUTPUT_DIR=<data>/scan_output` (CSV run artifacts + logs). |
| `ensure_user_data_dir()`, `ensure_scan_output_dir()` | `mkdir(parents=True, exist_ok=True)`; return the path. Cheap; `database.get_connection` calls the former only once per process (`_user_dir_ready` flag). |

### 3.3 `sponsorscout/i18n.py` — EN/IT localisation

| Function/const | Details |
|---|---|
| `_locale="en"`, `_I18N_CONFIG=USER_DATA_DIR/"locale.json"` | Runtime locale + persisted file. Disk write failures are swallowed (app still runs in memory locale). |
| `set_locale(locale)` | Sets global + writes `{"locale": code}`. Called by header combo (`ui/app.py::_on_language_change`). |
| `get_locale()`, `get_available_locales()`, `get_locale_name(locale)` | `en`/`it`; names `English`/`Italiano`. Unknown code echoes back (never crashes). |
| `load_saved_locale()` | Reads file at startup; corrupt/missing -> `"en"`. Called before first widget is built. |
| `_(text)` (implied helper) | Identity in EN; `LANGUAGES["it"]` lookup in IT. Convention: **UI chrome** goes through `_()`; **data values** (filter options, cell text, DB enums) stay canonical English so queries keep matching after a language switch (see `ui/tabs/search.py::FILTER_ALL="All"`). |
| `LANGUAGES` | `dict[locale, dict[en_key, translated]]`. EN is identity. IT must cover every literal `_()` key (enforced by `tests/test_i18n_parity.py`) and placeholders must match (`{n}`, `{level}`, `{c:.0%}` — enforced by `test_translation_placeholders_match_keys`). Dead (unused) EN keys are reported by `tools/find_dead_code.py --i18n`, but ~170 of them are *legacy* AI/ATS-Health strings for removed tabs — kept, not called (see section 9.3). |

### 3.4 `application/scan_coordinator.py` — Qt scan thread

Signals: `progress(str)` log chunks, `progress_tick(int,int,str,str)`
`(done,total,phase,label)`, `finished(dict)` pipeline summary,
`state_changed(str)` one of `idle/running/paused/stopping`.

Batching (`PROGRESS_FLUSH_SEC=0.15`, `PROGRESS_FLUSH_LINES=40`) exists because
per-line signals flood the GUI thread on 200-company scans. Lines starting
`PROGRESS: 12/208:career:About You` are stripped from the log widget and drive
the bar (`_parse_progress_tick`: pure string ops, clamps `done<=total`,
`total<=0` -> `None`).

`__init__(db_path)` owns `_cancel` (stop) + `_pause` (suspend) events.
`pause()` suspends in place (browsers stay open, same run, instant resume, no
DB trip). `resume()` clears it. `stop()` clears pause FIRST (else Stop queues
behind Pause and looks ignored), sets cancel, keeps the DB checkpoint so the
separate Resume continues unfinished companies even after restart.
`start(method, scan_scope, resume_from)` spawns the daemon `ScanWorker` that
calls `pipeline.run_scan(..., cancel_event, pause_event, batched progress)`;
`scan_scope={ats,career,run_ats,run_career}` selects subsets for custom scans.
Exceptions become `status="error"` summaries — the thread never dies silently.

### 3.5 `application/seed_manager.py` — v7 seed CSVs

Schema (file order): `name, careers_url, provider, board_slug, source_type,
target_country, scope_policy, industry, sponsorship_history, english_friendly,
remote_score, notes` + optional `seed_name, canonical_name` + legacy
`ats_type` (read-migrated to `provider`, never written back).

- `SUPPORTED_ATS_TYPES`: `official_careers, auto, ashby, greenhouse, lever,
  smartrecruiters, personio, recruitee, workable, workday, bamboohr, oracle,
  teamtailor, pam, digitalrecruiters` — each must have a real adapter, else the
  Data tab flags good rows invalid.
- `SCOPE_POLICIES`: `global/seed_url/job_location`. `SOURCE_TYPES`:
  `direct_employer/recruiter`.
- `bundled_*_path()` = `<pkg>/data/seeds/` (read-only defaults).
  `user_*_path()` = `<USER_DATA>/seeds/` (mutable copies; first run copies
  bundled->user; every edit targets these). `ensure_user_seeds()` backfills.
- `read_seed_rows(path)` -> `{columns, rows, migrated}`: skips `#` banners +
  blanks, preserves them as comment rows (`is_comment_row`, `__raw__`) so the
  writer echoes them byte-stable; folds `ats_type`->`provider`.
- `validate_row(row)` / `validate_file(path)`: URL scheme, scope/source enums,
  0–100 scores, duplicate `(name,careers_url)`; returns `(ok, ["line N: msg"])`.
- `write_seed_rows(path, columns, rows)`: drops legacy cols, guarantees
  `name/careers_url/provider`, echoes comment rows raw (else `DictWriter`
  appends 11 stray commas per banner), never invents columns.
- `auto_detect_provider(url)`: `ats_detection.detect_ats_from_links` else
  `official_careers`; drives the Data dialog live autodetect.


### 3.6 `core/` — pure business rules (no Qt, no DB)

`ats_detection.detect_ats_from_links(links) -> (ats, token)`: first-match
fingerprint for greenhouse/lever/ashby/workable/personio/workday/teamtailor/
smartrecruiters/bamboohr/recruitee/jobvite/icims/breezy/freshteam/homerun/
welcometothejungle/manatal/successfactors/taleo/oraclehcm/eightfold/phenom +
workable aliases (wise/babbel/booking). `("","")` when unknown. Feeds seed
autodetect + `provider=auto` resolution.

`url_normalizer.normalize_url(url)`: lowercase scheme/host, strip tracking
params (`utm_*/gh_src/lever-source/source/ref/fbclid/gclid`), KEEP param order
(sorting breaks SAP/Oracle/Workday) and fragments (`#jobs` SPAs need them).

`dedup`: `_normalize_for_fingerprint` (lowercase + gender-noise strip via
precompiled `_GENDER_NOISE_RE`); `job_fingerprint(t,c,l,u)` (SHA-256 4-part
key); `_delete_in_chunks(conn,table,ids,500)` (bounded DELETE — one giant IN
list hits SQLite's variable cap); `dedup_jobs(list)`; `dedup_jobs_in_db` /
`dedup_companies_in_db` (keep lowest id; NULL/empty company names skipped).

`http_client`: `_USER_AGENTS` pool rotated per session; `_CLOUDFLARE_MARKERS`
(challenge-only; `cf-ray`/`cf_clearance`/Turnstile excluded — they appear on
legit pages); `is_bot_blocked(html)`; `_new_session()` (Retry 3x, backoff 2.0,
forcelist 500/502/503/504, 429 excluded, pool 20/20, Chrome-only Sec-CH-*);
`build_session()` (caller closes); `http_session()` context manager (safe path).

`normalizer`: `COUNTRY_MAP`, `TITLE_NOISE`, `_EXPERIENCE_PATTERNS`
(intern/entry/mid/senior/lead/exec, first match),
`detect_experience_level(title)`, `normalize_country/country/title/location`,
`_VAGUE_REGIONS` (europe/emea/global/… kept for display, never country
evidence), `normalize_job(raw, source_type, source_name, fallback_company)`
(HQ fallback only when location is silent; ValueError on missing
company/title/url; propagates `industry`).

`integrity`: `EU_COUNTRIES` (27) + `SPONSORSHIP_REGIONS` (EU+UK/CH/NO/IS/LI);
`_verdict_is_yes` accepts `y/yes/true/1` (literal-`"Yes"`-only test once made
the module dead code); `sponsorship_region_applies` / `eu_blue_card_applies`
(empty = permissive); `check_integrity(job)` mutates in place, only
`Yes->Unknown`, re-derives legacy ints so they never disagree.

`verification`: `_SQLITE_TS="%Y-%m-%d %H:%M:%S"` (matches CURRENT_TIMESTAMP;
ISO `T/+00:00` broke string ordering); `utc_now_stamp()`; `mark_verified`
(trust>=90/freshness 100); `mark_expired` (stamps even on death, else dead rows
re-queue forever); `mark_inconclusive` (only the stamp moves — 403/timeout is
not death).

`verification_service`: `DEAD_PHRASES` (26); `_GONE_STATUSES={404,410,451}`
(403/429/5xx/0 are INCONCLUSIVE — old `(404,410,403,0)` set deleted live rows);
`check_url(url)` never raises; `verify_url_active(url)`; `verify_job(job)`.
### 3.7 `db/` + `models/job.py` — storage

`schema.sql` tables: `companies` (UNIQUE name + `LOWER(TRIM(name))` index);
`jobs` (UNIQUE url; verdict TEXT cols `visa_sponsorship/relocation_support/
eu_blue_card_verdict/relocation_required` + derived ints `eu_blue_card/
has_relocation`; `remote_type`; `experience_level/required/min_years/source`;
`industry`; `ai_score`; evidence `support_*/blue_card_*/canonical_job_id/
run_id`; `raw_location` + `country_source auto/manual`); `scan_runs/scan_log/
scan_events` (per-run evidence for Tools history); `applications`
(`job_url` UNIQUE, status saved/applied/interview/offer/rejected).
Indexes on title/company/country/sponsorship/verified/match/url/first_seen/
sponsored_fresh/source_subtype/run_id/canonical/source_type/
(remote,verified,expired).

`database.py`: `_configure_connection` (WAL + FK + busy 5s + NORMAL + 8MB
cache); `_regexp_like` (Python `re` REGEXP, case-insensitive, bad pattern
matches nothing); `get_connection(db_path)` (mkdir + once-per-process userdir
init + REGEXP fn); `_apply_migrations` (idempotent ADD COLUMNs incl. legacy
table drops `user_ai_assets/company_discovery_queue/discoveries/ats_health/
jobs_fts`); `initialize(db_path)` (schema + migrations + seed companies +
`migrate_job_countries`); query API: `get_dashboard_stats` (KPIs incl.
hybrid/unknown-workmode + empty/error-company counts), `get_dashboard_top_
companies` (recruiter-filtered, over-fetches 4x then trims),
`get_dashboard_empty_companies`, `get_jobs_by_country`, `run_search`
(LIKE/REGEXP, LIKE wildcards escaped, remote-EU relevance rule),
`start_scan_run/finish_scan_run/log_scan_row/log_event/get_scan_runs/
get_scan_log/get_resumable_scan/export_scan_run_csv`, applications CRUD,
`EXPERIENCE_LEVELS`. All dashboard/search reads accept `_conn` for reuse.

`migrate_countries.migrate_job_countries(conn, force=False)`: re-derives
country from `location` only for empty/`remote`/legacy rows; `manual` rows
skipped unless `force`. Returns updated count. CLI: `--force/--all`.

`models/job.py::Job`: dataclass mirroring `jobs` columns + `to_record()`
(dict copy for `upsert_job`). Drift rule: new DB column without a field here
flows through as a silent dict key — add both.

`core/persistence.py`: `_verdict_is_yes` (both vocabularies); `save_company`
(UPSERT on name, keeps non-empty careers_url/industry); `upsert_job(conn, job,
### 3.8 `scanning/` — the engine

`common.check_control(cancel, pause, poll=0.1)`: pause loop (stop wins),
`False` when both None. `host_workers_limits()` (Win GlobalMemoryStatusEx /
POSIX sysconf, else 8GB assumption; available-RAM sizing, not total).
`recommended_workers(kind, requested)`: `browser` 1/2/2-4 by CPU+RAM,
`http` 2-3 low / 2-8 else; `requested` only caps. `BROWSER_ARGS` /
`LOW_RESOURCE_BROWSER_ARGS` (alias): `--no-sandbox --disable-dev-shm-usage
--disable-http2 imagesEnabled=false renderer-limit=2 max-old-space=512 …`.

`jd_support`: `VERDICT_YES/NO/UNKNOWN = Yes/No/Unknown`;
`normalize_jd_text` (strip script/style, block tags -> sentence breaks, double
entity-unescape, NBSP/ZWSP cleanup, idempotent); `_strip_next_label` +
`_LABEL_FIELD_RES/_LABEL_YES_RE/_LABEL_NO_RE/_LABEL_LEAD_RE/_LEAD_JUNK_RE`
(structured `VISA Sponsorship: No` Workday blocks, minimal trailing-label
strip); `JDSupportDetector` (sentence/clause-scoped cues + negation/
requirement/conditional/scope qualifiers, keyword-alone never matches, No
never fabricated). Single source of truth — both scanners share it.

`pipeline`: `lower_process_priority()` (Win BELOW_NORMAL / POSIX nice 10);
`derive_sponsorship_score(verdict, conf, history)` (Y=70+conf*20+hist*0.10
capped 100; Unknown=35 flat — no bonuses on unevidenced rows; N=0);
`_as_verdict` (y/yes/true/1->Y, n/no/false/0->N, unknown->Unknown, else "");
`_as_bool` (strict Y->1); `_norm_location` (unknown/not specified->"");
`_EU_COUNTRIES/_EMEA_COUNTRIES`; `_WORK_MODE_TOKENS` + `_sniff_work_mode`
(last-resort text sniff; absence of "remote" is Unknown, never onsite) +
`_remote_type(row)` (Work Mode column FIRST; Unknown stays unknown; remote ->
remote/remote_eu/remote_emea by country); `_job_country` (Job Location, then
Raw Location, else ""); `_exp_min_years/_exp_level/_exp_required`
(scanner vocab -> Intern/Entry/Mid/Senior/Lead/Exec); `_row_to_job_unverified`
+ `_row_to_job` (adds `check_integrity`, appends `integrity:` note to
`support_evidence_type`); `run_scan(method, db_path, cancel/pause/progress,
resume_from, only_ats/only_career/run_ats/run_career, ...)` (ATS then career,
CSV artifacts in SCAN_OUTPUT_DIR, ingest accepted rows, quarantine never
ingested, scan_runs/log/events written; returns summary dict).

`ats/ats_scanner.py` (~200KB): official APIs for ashby/greenhouse/lever/
smartrecruiters/personio/recruitee/workable/workday (+oracle/pam/
digitalrecruiters via career side); static-HTML fast path with
`_STATIC_MIN_JOBS` acceptance gate (thin static -> full browser, never
truncated); canonical requisition-ID dedup; retry+backoff; seed self-healing;
`set_progress_callback` routes `print` to the UI log; `urlparse/urljoin/
unquote` kept (the only live urllib imports); `_log_file_path()` -> per-user
log (see bugfix 2026-10-05 in section 9.2).

`career/career_scanner.py` (~590KB): generic crawler — static fast path ->
Playwright DOM fallback; provider adapters (bamboohr/oracle/teamtailor/pam/
digitalrecruiters/workday/smartrecruiters/…); JSON-LD pass; pagination with
pause/stop gates; `_try_ats_fallback` + `_kill_child_processes` +
`_looks_like_location/contract_fragment` are live via dynamic dispatch
(`getattr`/config paths) — the AST-only dead-code tool cannot see them, do
NOT delete (see section 9.3).

commit=True)` (recomputes legacy booleans from verdicts; country chain =
location text first, caller country last, `manual` never overwritten by auto;
industry backfill w/ `_INDUSTRY_CACHE`; `ON CONFLICT(url)` full-column UPSERT;
batch with `commit=False`); `mark_job_expired(conn, url)`.

### 3.9 `services/browser_fetcher.py` — HTTP-first fetch

`_DYNAMIC_HINTS` (`__NEXT_DATA__/__NUXT__/root/app/ng-version/…`) +
`_looks_dynamic(html)` (<5KB -> dynamic); `_looks_like_careers_url(url)`
(path/fragment contains career/jobs/join/vacanc/stellen/emploi/trabajo/…);
`_extract_title`; `_playwright_import_error/_playwright_available` (report the
WHICH, not just boolean); `_AUTO_INSTALL_ENV=
SPONSORSCOUT_AUTO_INSTALL_BROWSERS` (opt-in `playwright install chromium`,
default off, `_INSTALL_TIMEOUT=300`); `fetch_rendered_html(url)` returns
`{status, html, title}` (`status=0` = transport failure, never "gone").
Used by `verification_service` + career detail enrichment.

### 3.10 `scripts/` — CLI helpers

`run_scan.main()`: `--quick` (dev, skip detail enrichment; app uses full),
`--full` (deprecated alias), `--dedup` (post-scan `dedup_*_in_db`),
`--company NAME` (substring match across both user seed sets). Always
`initialize(DB_PATH)` first; progress -> stdout; summary line at end.

`backfill_industry.backfill()`: one-time `UPDATE jobs SET industry =
companies.industry` where empty + match; logs affected count + still-missing
companies. Run once after upgrading: `python -m sponsorscout.scripts.
backfill_industry`.

### 3.11 `ui/` — PySide6 desktop

`app.SponsorScoutApp(QMainWindow)`: navy header (title + subtitle + lang
combo + status) + 5-tab workspace; `_refresh_all()` (stages timed at DEBUG);
5s `_scan_refresh_timer` while scanning (cheap COUNTs, skipped when hidden);
`_on_scan_finished` refreshes EVERY view (old code left stale tables);
`_on_language_change` -> `retranslate()` fan-out; `_check_first_run` prompts
on empty DB; win32 AppUserModelID groups the taskbar icon.

`style`: palette consts (`HEADER_BG #1d2d44, ACCENT #3a7bd5, BODY #f0f2f5`),
`FONT_FAMILY=Helvetica`, `build_light_palette()` (blocks Qt>=6.5 dark-mode
washout), `build_qss()` (Header/Card/TabBar/Table/Button/Dialog/Tooltip).

`tabs/__init__`: re-exports 5 tabs; constructor-injected `db_path` keeps each
tab testable in isolation (`QApplication([])` offscreen in tests).

`tabs/dashboard.py::DashboardTab`: 7 `CARD_KEYS` incl. `No Jobs (last run)`;
Remote card reads `112 + 183 hybrid` with unknown-workmode tooltip; 3 tables
(top companies / by country / empties with Why diagnostics).

`tabs/search.py::SearchTab`: 10 canonical `HEADERS` (index-defining — never
translate/reorder); `FILTER_ALL="All"` stays English in every locale so values
keep matching; content-measured column widths; experience compacts (`4+`)
with verbatim tooltip; verdicts `Y/N/?` + evidence tooltips; LIKE/REGEXP +
country/sponsorship/remote/experience filters.

`tabs/applications.py::ApplicationsTab`: `saved/applied/interview/offer/
rejected` pipeline; Company/Title/Status/Saved-on/URL table + edit form;
Save/Remove/Refresh; `data_changed/status_message` signals.

`tabs/tools.py::ToolsTab`: run/company history tables (`HEADERS_RUNS/LOG`);
`SCAN_METHOD="full"` (quick is CLI-dev-only); tooltip consts in ONE place
(`__init__` vs `retranslate()` drift once left stale Italian strings);
checkpoint tooltip refreshes on `showEvent`/scan-end, never in `retranslate()`
(that froze on SQLite busy); pause=instant, stop=checkpointed.

`tabs/data_management.py`: grid follows the FILE header (`FALLBACK_COLUMNS=
sm.SEED_COLUMNS` — hard-coded v6 list once hid `provider`); `SeedRowDialog`
with live provider autodetect; edits target USER seed copies; saves stay
byte-stable (banners echoed raw).

### 3.12 Data, tests, tools, packaging

`data/`: `default_profile.json` + `country_profile.json` (filter presets);
`sponsorscout.png/.ico` + `icons/` 16–512px (taskbar/installer/Qt);
`seeds/company_ATS_seed.csv` (~65 API boards), `company_Career_seed.csv`
(~373 career pages), `remote_seed.csv` (remote boards). Grouped by
`target_country` with `#` banners the code skips but the writer preserves.

`tests/` (~20 modules): `conftest` (`data_dir` tmp redirect via
`SPONSORSCOUT_DATA_DIR` + module purge/restore; `db_path` fresh DB);
`test_db_schema` (DDL + migrations + legacy drops); `test_persistence`
(upsert/country-chain/industry); `test_pipeline` (verdict/remote/country
mapping); `test_sponsorship_detection` + `test_experience` (JD classifier);
`test_location_country` + `test_career_html_static` (geo + static fixtures);
`test_i18n_parity` (every `_()` key has IT; placeholders match) +
`test_tools_i18n` (pause/resume tooltips follow language, no DB in
`retranslate`); `test_scan_controls/progress/resume` (coordinator gates);
`test_custom_scan_scope` + `test_seed_v7` (scope subsets + schema);
`test_search_tab` (filters/widths/tooltips); `test_regressions[_2026_02]`
(pinned historical bugs); `test_playwright_health` (bundled Chromium);
`test_packaging_uninstall` (spec/manifest/installer coherence).
Run: `venv\Scripts\python.exe -m pytest sponsorscout\tests -q` (~70s).

`tools/find_dead_code.py`: read-only reporter (unused imports via AST;
private `_defs` referenced once-or-never across pkg+tests+docs; literal
`_(...)` EN keys not used outside `i18n.py`, with `_DISPATCH_CONSTANTS`
HEADERS/RUNS/LOG/CARD_KEYS exempted). Exit 1 = findings. Never deletes.
`tools/find_dead_code.allow`: keepers (db/core/application public API +
`main::paths` side-effect import). `tools/check_readme_anchors.py`:
GitHub-slug validator for `#fragment` links (README: 26 links OK).

Packaging: `SponsorScout.spec` (PyInstaller source: `main.py`, collects
`sponsorscout/PySide6/playwright` data, excludes pandas/PIL/bs4/lxml/tkinter/
pytest); `build_exe.ps1` (Inno Setup -> `dist/*-setup.exe`, self-checks the
binary); `build_deb.sh` / `build_rpm.sh` (isolated `.build/*-venv`, bundle
Chromium, binary self-check, deb: normal user, zstd opt; rpm: needs
`rpmbuild`); `installer.iss` (HKCU Playwright path + post-install launch);
`pyproject.toml` (runtime: requests/playwright/PySide6; dev: pytest/
pyinstaller; `sponsorscout` + `sponsorscout-scan` entry points; package-data
json/csv/png/ico/sql); `requirements.txt` (runtime + pyinstaller; pytest via
`.[dev]`); `MANIFEST.in` (data + README + CODEBASE + LICENSE + spec).

## 4. What was cleaned on 2026-10-05 (and what was NOT)

Deleted: all `__pycache__/` dirs + `*.pyc` under `sponsorscout/` + `tools/`
(regenerated on import; git-ignored). Nothing else was deleted.

Deliberately KEPT (do not "clean" these):

- `extra_for_dev_purpose(do not delete)/` — legacy algorithm reference the
  owner marked do-not-delete; `cleanup.txt` 4.NEVER DELETE + `.gitignore`
  `*.exe` rule protect it. The untracked
  `extra_for_dev_purpose(do not delete)/company_Career_seed.csv` (47KB, Austria
  group first) is a scratch/merge copy vs the tracked 7.7KB Italy-first seed —
  left for the owner to confirm, NOT auto-deleted.
- `venv/` + `extra.../main_job_search_algorithms/venv/` — environments,
  git-ignored, never source.
- `tools/*.py` — tracked source (dead-code + anchor checkers). `.gitignore`
  wrongly listed `tools/` as "large binaries"; fixed 2026-10-05 to ignore only
  `windows_build_tools/` (section 9.1).
- `CODEBASE.md` — `.gitignore` listed it as an artefact; removed 2026-10-05
  (this file is tracked source; `MANIFEST.in` ships it).
- `venv/*.pyc`, `.git/*` — never touched.
- `find_dead_code` "unused imports" in `ats_scanner.py` — fixed (section 9.2),
  `--imports` now clean. Remaining def/i18n findings are false positives or
  legacy keepers (section 9.3) — do not bulk-delete.

## 5. Bugs found + fixed on 2026-10-05

### 5.1 FIXED — `.gitignore` ignored tracked source `tools/` (high)

`tools/` (whole dir) was ignored as "large binaries" while its three files
are tracked source. `git check-ignore -v` proved it. A fresh clone +
`git clean -fdx` could drop the audit tooling. Fix: ignore only
`windows_build_tools/` + comment. Also removed the `CODEBASE.md` ignore line
(this file is tracked; `MANIFEST.in` ships it).

### 5.2 FIXED — `ats_scanner.py` dead imports + CWD log path (medium)

`parse_qsl/urlencode/urlunparse` each occurred exactly once (the import line);
live code uses only `urlparse/urljoin/unquote`. Removed the three:
`--imports` is now clean, `compileall` passes. Same file:
`logging.basicConfig(filename="ats_scraper_errors.log")` contradicted the
`_log_file_path()` helper above it (per-user scan-output dir; CWD unwritable
under Program Files). Now `filename=_log_file_path()`.

### 5.3 TRIAGED (not bugs — do not "fix" by deleting)

- `career_scanner` 4 private defs (count==1 each): live via dynamic dispatch
  inside the 590KB crawler; the AST counter cannot see them. Deleting breaks
  title cleaning, zero-yield rescue, child-process hygiene.
- ~170 `i18n: unused EN key` (AI Tailor / ATS Health / CV prompts): legacy
  strings for removed tabs. `test_i18n_parity` REQUIRES EN/IT parity; bulk
  delete breaks it. Remove only with the owning tab, both locales at once.
- `data/*.csv+json` ignore + `!data/seeds/*.csv`, `*.db/*.log` ignores:
  correct (regenerated artefacts vs bundled seeds). Keep.
- 16 modified tracked files + 1 untracked seed copy: in-flight feature work
  (pipeline remote-type, persistence verdicts, dashboard empties, seed
  regroups) — reviewed, not reverted.

## 6. Developer guide — how to change each file safely

### 6.1 Everyday recipes

Add a company: Data Management tab (validates + writes the USER seed copy),
or append a v7 row to `sponsorscout/data/seeds/*.csv` for a bundled default
(all 12 cols; `provider=auto` unless you know the adapter; `scope_policy`
`global/seed_url/job_location`). Then Tools -> full scan.

Add a country: `core/location_country.py` (`ISO2_TO_COUNTRY`, `COUNTRY_NAMES`,
`CITY_TO_COUNTRY`, city list), plus `core/normalizer.COUNTRY_MAP`,
`core/integrity.EU/SPONSORSHIP_REGIONS` if EEA/EU membership changes,
`pipeline._EU/_EMEA_COUNTRIES` (dashboard remote refinement). Add tests in
`test_location_country.py`; run `migrate_countries --force` for old rows.

Add an ATS adapter: `career_scanner._fetch_provider_jobs` dispatch +
`ats_scanner.scan_target` branch + `seed_manager.SUPPORTED_ATS_TYPES` entry
(all three, or the Data tab flags rows invalid). Mirror the JSON shape of a
sibling adapter; keep the 39-col output schema byte-identical.

Change a verdict rule: ONLY `scanning/jd_support.py` (both scanners share the
class). Add sentence-level fixtures to `test_sponsorship_detection.py` first
(Yes/No/Unknown incl. negation `can't offer`, double-encoded `&amp;rsquo;`,
Workday `VISA Sponsorship: No` labels). Never keyword-match alone.

Change scoring/country/remote mapping: `pipeline.derive_sponsorship_score /
_job_country / _remote_type` + `test_pipeline.py` expectations in the same
commit (Dashboard numbers come from here, not SQL).

### 6.2 UI rules (break these and filters/i18n silently fail)

- Column-index lists (`search.HEADERS`, `tools.HEADERS_RUNS/LOG`,
  `applications.HEADERS`) are canonical and positional — never translate,
  reorder, or insert mid-list (append + migrate readers instead).
- Filter/cell VALUES stay canonical English; only chrome (labels, buttons,
  tooltips, placeholders) goes through `_()`. Translating a value orphans
  every saved filter and breaks `run_search` matching.
- New `_()` literal needs EN identity + IT translation in the same commit
  (`test_i18n_parity` fails otherwise); keep `{placeholders}` identical.
- `HEADERS/CARD_KEYS` string lists translated via `_(var)` must be added to
  `tools/find_dead_code.py::_DISPATCH_CONSTANTS`, or the reporter flags live
  UI text as dead.
- Long tooltip strings: define once as module consts (see `tools.py`
  `*_TOOLTIP`), use in both `__init__` and `retranslate()`. Never DB-query
  in `retranslate()` (SQLite-busy freeze — see `test_tools_i18n`).

### 6.3 DB rules

- New column: `schema.sql` + `_apply_migrations` entry + `models/job.py`
  field + `persistence.upsert_job` UPSERT both halves + dashboard/search
  SELECT if displayed. Partial indexes need `WHERE col != ''` guards.
- `url` stays the UNIQUE stable key; `country_source=manual` is never
  overwritten by auto (see `persistence` country chain + `migrate_countries`).
- Verdict TEXT cols are authoritative; int cols are derived (`Y->1`).
  Unknown is never a hard No — in SQL, Python, or tooltips.

### 6.4 Scan-control rules

- Every network/pagination loop needs a `check_control(cancel, pause)` gate
  (pause = instant resume, stop = checkpointed resume). Use
  `recommended_workers()` for pool sizes; never hard-code 4 browsers.
- Scanners write CSV artifacts; ONLY `pipeline` writes the DB. Quarantine
  rows are never ingested. `run_id` + `canonical_job_id` provenance on every
  row.

### 6.5 Conventions, gotchas, and the definition of done

- `logging.basicConfig` exactly once per process (`main.py` / CLI scripts).
  Library modules only `getLogger`. Never log to CWD (use `paths` dirs).
- `paths` constants are import-time: tests must set `SPONSORSCOUT_DATA_DIR`
  BEFORE importing `sponsorscout.*` (see `conftest.data_dir` purge/restore).
- Line endings: `.gitattributes` forces LF for `*.py/sh/toml`; a stray CRLF
  edit shows as a whole-file diff — normalise before committing.
- Dead-code tool: `--imports` must be 0; def/i18n findings need human review
  (dynamic dispatch + legacy parity, section 5.3). Allowlist additions need a
  `# reason`.
- Done = `compileall` clean + `find_dead_code --imports` 0 +
  `check_readme_anchors README.md` OK + `pytest sponsorscout/tests -q` green
  (~70s) + no new `git status` artefacts (`__pycache__/*.pyc` belong in the
  ignore, never in a commit).

