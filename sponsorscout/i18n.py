"""Internationalization (i18n) for SponsorScout.

Provides English (default) and Italian translations.
Usage:  from sponsorscout.i18n import _, set_locale, get_locale
        text = _("Search")
"""
from __future__ import annotations

import json

from sponsorscout.paths import USER_DATA_DIR

# ── current locale (runtime, persisted separately) ──────────────────────────

_locale: str = "en"

# Config file for persisting language preference
_I18N_CONFIG = USER_DATA_DIR / "locale.json"


def set_locale(locale: str) -> None:
    """Set the active locale at runtime."""
    global _locale
    _locale = locale
    # Persist to disk
    try:
        _I18N_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        _I18N_CONFIG.write_text(json.dumps({"locale": locale}))
    except Exception:
        pass


def get_locale() -> str:
    """Return the current locale code ('en' or 'it')."""
    return _locale


def load_saved_locale() -> str:
    """Load persisted locale from disk. Returns 'en' if not found."""
    global _locale
    try:
        if _I18N_CONFIG.exists():
            data = json.loads(_I18N_CONFIG.read_text())
            _locale = data.get("locale", "en")
    except Exception:
        _locale = "en"
    return _locale


def get_available_locales() -> list[str]:
    """Return list of available locale codes."""
    return ["en", "it"]


def get_locale_name(locale: str) -> str:
    """Return human-readable locale name."""
    names = {"en": "English", "it": "Italiano"}
    return names.get(locale, locale)


# ── Translation dictionary ─────────────────────────────────────────────────
# Each key is the English string. English is the identity (key == value).
# Italian provides precise translations.

LANGUAGES: dict[str, dict[str, str]] = {
    "en": {
        # ── Header ─────────────────────────────────────────────────────
        "Verified sponsorship-focused jobs from official career pages and ATS boards":
            "Verified sponsorship-focused jobs from official career pages and ATS boards",
        "Ready": "Ready",
        "Scan complete.": "Scan complete.",

        # ── Tab names ──────────────────────────────────────────────────
        "Dashboard": "Dashboard",
        "Applications": "Applications",
        "ATS Health": "ATS Health",
        "AI Tailor": "AI Tailor",
        "Tools": "Tools",

        # ── Search tab ─────────────────────────────────────────────────
        "Title:": "Title:",
        "Company:": "Company:",
        "Country:": "Country:",
        "Search": "Search",
        "Clear": "Clear",
        "Sponsorship:": "Sponsorship:",
        "Remote:": "Remote:",
        "Experience:": "Experience:",
        "Experience": "Experience",
        "No experience requirement found in the job description": "No experience requirement found in the job description",
        "The ad mentions experience without stating a figure or a level.": "The ad mentions experience without stating a figure or a level.",
        "Seniority level \"{level}\" — the ad states no number of years.": "Seniority level \"{level}\" — the ad states no number of years.",
        "Sort:": "Sort:",
        "Objective:": "Objective:",
        "Balanced": "Balanced",
        "Strict quality": "Strict quality",
        "Visa sponsor": "Visa sponsor",
        "Local EU": "Local EU",
        "Blue Card focus": "Blue Card focus",
        "Relocation": "Relocation",
        "All": "All",
        "Regex": "Regex",
        "Enable regular-expression matching in Title / Company / Location filters (e.g. ^senior (backend|platform)$).":
            "Enable regular-expression matching in Title / Company / Location filters (e.g. ^senior (backend|platform)$).",
        "Invalid regular expression": "Invalid regular expression",
        "Regex disabled — invalid pattern:\n{error}":
            "Regex disabled — invalid pattern:\n{error}",
        "Any (incl. unknown)": "Any (incl. unknown)",
        "Unknown / Not classified": "Unknown / Not classified",
        "Intern": "Intern",
        "Entry": "Entry",
        "Mid": "Mid",
        "Senior": "Senior",
        "Lead": "Lead",
        "Exec": "Exec",
        "Best match": "Best match",
        "Latest": "Latest",
        "Sponsored Only": "Sponsored Only",
        "Remote EU": "Remote EU",
        "Remote EMEA": "Remote EMEA",
        "Remote Global": "Remote Global",
        "Remote Only": "Remote Only",
        "Hybrid": "Hybrid",
        "jobs found": "jobs found",
        "job found": "job found",

        # ── AI Rating panel ────────────────────────────────────────────
        "AI Job Rating & Eligibility": "AI Job Rating & Eligibility",
        "Rate this job": "Rate this job",
        "Tailor CV & Letter": "Tailor CV & Letter",
        "Select a job, then click 'Rate this job'.":
            "Select a job, then click 'Rate this job'.",
        "No job selected.": "No job selected.",
        "Rating against your saved CV profile":
            "Rating against your saved CV profile",
        "No CV on file — paste yours in AI Tailor tab for personalised results":
            "No CV on file — paste yours in AI Tailor tab for personalised results",
        "Rating will use your saved CV profile":
            "Rating will use your saved CV profile",
        "Contacting Gemini API...": "Contacting Gemini API...",
        "Contacting custom AI endpoint...": "Contacting custom AI endpoint...",
        "Contacting {provider} OpenAI-compatible API...":
            "Contacting {provider} OpenAI-compatible API...",

        # ── Search: right-click context menu ───────────────────────────
        "Open in browser": "Open in browser",
        "Save to Applications": "Save to Applications",
        "Rate with AI": "Rate with AI",
        "Tailor CV & Cover Letter": "Tailor CV & Cover Letter",

        # ── Dashboard ──────────────────────────────────────────────────
        "Companies": "Companies",
        "Verified Jobs": "Verified Jobs",
        "Sponsored": "Sponsored",
        "Remote": "Remote",
        "EU Blue Card": "EU Blue Card",
        "New this week": "New this week",
        "Top companies by sponsorship score":
            "Top companies by sponsorship score",
        "Jobs by country": "Jobs by country",

        # ── Applications tab ───────────────────────────────────────────
        "Saved applications": "Saved applications",
        "Refresh": "Refresh",
        "Remove Selected": "Remove Selected",
        "Edit selected": "Edit selected",
        "Notes:": "Notes:",
        "saved": "saved",
        "applied": "applied",
        "interview": "interview",
        "offer": "offer",
        "rejected": "rejected",

        # ── ATS Health tab ─────────────────────────────────────────────
        "ATS Connector Health": "ATS Connector Health",
        "Success/failure rates per connector after each scan.":
            "Success/failure rates per connector after each scan.",

        # ── Tools tab ──────────────────────────────────────────────────
        "Scanner": "Scanner",
        "Scan all 111 companies via their official ATS APIs.":
            "Scan all 111 companies via their official ATS APIs.",
        "Status:": "Status:",
        "idle": "idle",
        "Scan Now": "Scan Now",
        "Auto (1 h)": "Auto (1 h)",
        "Stop": "Stop",
        "Pause": "Pause",
        "Showing {a}–{b} of {n} results":
            "Showing {a}–{b} of {n} results",
        "Previous page": "Previous page",
        "Next page": "Next page",
        "Rows per page": "Rows per page",
        "Scan Log:": "Scan Log:",
        "Data Quality": "Data Quality",
        "Remove duplicate jobs and companies from the database.":
            "Remove duplicate jobs and companies from the database.",
        "Run Dedup": "Run Dedup",
        "Clear Scan Data": "Clear Scan Data",
        "The database already contains no scanned data.":
            "The database already contains no scanned data.",
        "This will permanently delete {jobs} scanned job(s), {runs} scan run(s) and {logs} scan log row(s) from the database.\nSeed CSVs and saved applications are NOT affected. Continue?":
            "This will permanently delete {jobs} scanned job(s), {runs} scan run(s) and {logs} scan log row(s) from the database.\nSeed CSVs and saved applications are NOT affected. Continue?",
        "Scan data cleared": "Scan data cleared",
        "Removed {jobs} job(s), {runs} scan run(s) and {logs} scan log row(s). Seed CSVs were not touched.":
            "Removed {jobs} job(s), {runs} scan run(s) and {logs} scan log row(s). Seed CSVs were not touched.",
        "Stale data cleared": "Stale data cleared",
        "Removed {n} expired job(s) from the database.":
            "Removed {n} expired job(s) from the database.",
        "No expired jobs to remove — the database is already clean.":
            "No expired jobs to remove — the database is already clean.",
        "View Per-Company Log": "View Per-Company Log",
        "Download Scan Log": "Download Scan Log",
        "Select a scan run first.": "Select a scan run first.",
        "Scan log downloaded": "Scan log downloaded",
        "Scan log saved to:\n{path}": "Scan log saved to:\n{path}",
        "CSV files (*.csv);;All files (*.*)": "CSV files (*.csv);;All files (*.*)",
        "Could not save scan log:\n{error}": "Could not save scan log:\n{error}",
        "The file appears empty:\n{path}": "The file appears empty:\n{path}",
        "Scan log ({bytes} bytes, {rows} company row(s), {events} event(s)) saved to:\n{path}":
            "Scan log ({bytes} bytes, {rows} company row(s), {events} event(s)) saved to:\n{path}",
        "AI Settings": "AI Settings",
        "AI provider API key + prompts for job rating, eligibility (uses your CV from AI Tailor tab), and document generation.":
            "AI provider API key + prompts for job rating, eligibility (uses your CV from AI Tailor tab), and document generation.",
        "AI API Key / Token:": "AI API Key / Token:",
        "Save Key": "Save Key",
        "Provider:": "Provider:",
        "Model:": "Model:",
        "(type any model name; chips are provider-specific)":
            "(type any model name; chips are provider-specific)",
        "Base URL:": "Base URL:",
        "(auto-filled for built-in providers; required for custom)":
            "(auto-filled for built-in providers; required for custom)",
        "type the exact model ID from your provider":
            "type the exact model ID from your provider",
        "Job Rating & Eligibility Prompt  (AI uses this + your CV to score each job):":
            "Job Rating & Eligibility Prompt  (AI uses this + your CV to score each job):",
        "Save Prompt": "Save Prompt",
        "Reset to Default": "Reset to Default",
        "CV Tailoring Prompt  (how AI rewrites your CV to match a JD):":
            "CV Tailoring Prompt  (how AI rewrites your CV to match a JD):",
        "Save CV Prompt": "Save CV Prompt",
        "Reset": "Reset",
        "Cover / Motivation Letter Prompt  (for EU-based roles — personalised from your CV + JD):":
            "Cover / Motivation Letter Prompt  (for EU-based roles — personalised from your CV + JD):",
        "Save Letter Prompt": "Save Letter Prompt",
        "Company Discovery": "Company Discovery",
        "Probes 210 curated ATS boards.  Use role keywords: 'analyst', 'engineer', 'backend', 'data'.":
            "Probes 210 curated ATS boards.  Use role keywords: 'analyst', 'engineer', 'backend', 'data'.",
        "Query:": "Query:",
        "Discover": "Discover",
        "Freshness Check": "Freshness Check",
        "Verify jobs still exist online — auto-expires dead links.":
            "Verify jobs still exist online — auto-expires dead links.",
        "Max jobs:": "Max jobs:",
        "Run": "Run",

        # ── AI Tailor tab ──────────────────────────────────────────────
        "AI CV & Cover Letter Tailor": "AI CV & Cover Letter Tailor",
        "Select a job in Search → 'Tailor CV & Letter', or load a JD manually below":
            "Select a job in Search → 'Tailor CV & Letter', or load a JD manually below",
        "How to use": "How to use",
        "No job selected — use Search tab or paste a JD below":
            "No job selected — use Search tab or paste a JD below",
        "Job URL:": "Job URL:",
        "Fetch JD": "Fetch JD",
        "— or paste full JD below —": "— or paste full JD below —",
        "Use this JD": "Use this JD",
        "My CV (stored locally)": "My CV (stored locally)",
        "Paste your current CV once — it's saved for all future tailoring sessions.":
            "Paste your current CV once — it's saved for all future tailoring sessions.",
        "Save CV": "Save CV",
        "CV saved": "CV saved",
        "Base Cover Letter (template for AI)":
            "Base Cover Letter (template for AI)",
        "Paste an example cover letter you like. The AI will match its style/tone when generating new letters.":
            "Paste an example cover letter you like. The AI will match its style/tone when generating new letters.",
        "Save Template": "Save Template",
        "Template saved": "Template saved",
        "Generate": "Generate",
        "Tailor My CV": "Tailor My CV",
        "Write Cover Letter": "Write Cover Letter",
        "Both": "Both",
        "Result": "Result",
        "CV": "CV",
        "Cover Letter": "Cover Letter",
        "Copy": "Copy",
        "Fetching…": "Fetching…",

        # ── Messages: tailor tab ───────────────────────────────────────
        "Empty JD": "Empty JD",
        "Paste a job description first.": "Paste a job description first.",
        "No URL": "No URL",
        "Enter a job post URL first.": "Enter a job post URL first.",
        "Empty CV": "Empty CV",
        "Paste your CV first.": "Paste your CV first.",
        "No Job Description": "No Job Description",
        "Fetch or paste a job description first, then click 'Use this JD'.":
            "Fetch or paste a job description first, then click 'Use this JD'.",
        "No CV": "No CV",
        "Paste your CV in the 'My CV' box and save it first.":
            "Paste your CV in the 'My CV' box and save it first.",
        "Copied to clipboard!": "Copied to clipboard!",
        "Select a row": "Select a row",
        "Click a row first, then click Remove.":
            "Click a row first, then click Remove.",
        "Remove": "Remove",
        "Search error": "Search error",
        "Error": "Error",
        "Missing": "Missing",
        "Enter a search query.": "Enter a search query.",

        # ── Messages: AI settings ──────────────────────────────────────
        "Saved": "Saved",
        "AI API key saved.": "AI API key saved.",
        "Empty": "Empty",
        "Prompt cannot be empty.": "Prompt cannot be empty.",
        "Custom prompt saved.": "Custom prompt saved.",
        "CV tailoring prompt saved.": "CV tailoring prompt saved.",
        "Cover letter prompt saved.": "Cover letter prompt saved.",
        "Reset prompt to default?": "Reset prompt to default?",
        "Reset CV tailoring prompt to default?":
            "Reset CV tailoring prompt to default?",
        "Reset cover letter prompt to default?":
            "Reset cover letter prompt to default?",
        "Unknown provider": "Unknown provider",
        "Missing model": "Missing model",
        "Type a model name first.": "Type a model name first.",
        "Missing base URL": "Missing base URL",

        # ── Messages: first run ────────────────────────────────────────
        "Welcome to SponsorScout": "Welcome to SponsorScout",

        # ── Messages: general ──────────────────────────────────────────
        "No job selected": "No job selected",
        "Select a job in the Search tab first.":
            "Select a job in the Search tab first.",
        "Dashboard data could not be loaded.":
            "Dashboard data could not be loaded.",
        "Dedup complete": "Dedup complete",
        "Probing ATS boards for": "Probing ATS boards for",
        "Found": "Found",
        "candidate(s).": "candidate(s).",
        "No new companies found.":
            "No new companies found.",
        "Try: 'analyst', 'backend', 'data engineer'":
            "Try: 'analyst', 'backend', 'data engineer'",
        "Discovery done.": "Discovery done.",
        "Discovery failed.": "Discovery failed.",
        "Running discovery…": "Running discovery…",
        "running…": "running…",
        "Scanning…": "Scanning…",
        "auto — every 1 h": "auto — every 1 h",
        "stopped": "stopped",
        "Failed.": "Failed.",
        "Verified": "Verified",

        # ── Tooltip: How to Use ────────────────────────────────────────
        "AI Tailor — How to Use": "AI Tailor — How to Use",

        # ── Tooltip: JD section ────────────────────────────────────────
        "Job Description": "Job Description",

        # ── Tooltip: CV section ────────────────────────────────────────
        "My CV": "My CV",

        # ── Tooltip: Cover Letter section ──────────────────────────────
        "Base Cover Letter Template": "Base Cover Letter Template",

        # ── Tooltip: Generate section ──────────────────────────────────
        "Generate Buttons": "Generate Buttons",

        # ── Tooltip: Result section ────────────────────────────────────
        "Result Area": "Result Area",

        # ── Language toggle ────────────────────────────────────────────
        # ── Dashboard ──────────────────────────────────────────────────
        "Total Companies": "Total Companies",
        "Sponsored Jobs": "Sponsored Jobs",
        "Remote Jobs": "Remote Jobs",
        "Top Companies by Sponsorship": "Top Companies by Sponsorship",
        "Jobs by Country": "Jobs by Country",
        "Company": "Company",
        "Country": "Country",
        "Jobs": "Jobs",
        "Top Sponsor": "Top Sponsor",
        "Rescan Companies": "Rescan Companies",
        "Unknown": "Unknown",

        # ── Tools descriptions ────────────────────────────────────────
        "Scanner description": "Start a job scan across all seeded companies — every ATS board and career page is crawled, then each job is enriched from its detail page. Live output appears below.",
        "Scan every seeded company (ATS boards + career pages) and enrich each job from its detail page, so no listing misses its evidence.":
            "Scan every seeded company (ATS boards + career pages) and enrich each job from its detail page, so no listing misses its evidence.",
        "Pause the running scan in place — workers stop at the next company and browsers wait. Press Resume to continue instantly, no new scan is started.":
            "Pause the running scan in place — workers stop at the next company and browsers wait. Press Resume to continue instantly, no new scan is started.",
        "Continue the paused scan where it stopped — same run, no loss.":
            "Continue the paused scan where it stopped — same run, no loss.",
        "Stop the scan now and keep everything found so far. The stopped run is checkpointed — Resume (the other button) starts a new scan for the companies that were not finished, even after an app restart.":
            "Stop the scan now and keep everything found so far. The stopped run is checkpointed — Resume (the other button) starts a new scan for the companies that were not finished, even after an app restart.",
        "Looking up the last stopped scan…":
            "Looking up the last stopped scan…",
        "Paused": "Paused",
        "Pausing…": "Pausing…",
        "Stopping…": "Stopping…",
        "Stopped.": "Stopped.",
        "Stop the running scan? Everything found so far is kept and can be resumed later.":
            "Stop the running scan? Everything found so far is kept and can be resumed later.",
        "Scan History description": "Every past scan run. Select a row to view or download its per-company log with errors.",
        "Resume": "Resume",
        "Continue the last stopped scan — only companies it did not finish are scanned, so no progress is lost.":
            "Continue the last stopped scan — only companies it did not finish are scanned, so no progress is lost.",
        "Stop the scan now and keep everything found so far. Press Resume later to continue the remaining companies — all browsers close, so other apps run smoothly again.":
            "Stop the scan now and keep everything found so far. Press Resume later to continue the remaining companies — all browsers close, so other apps run smoothly again.",
        "Resuming scan": "Resuming scan",
        "Nothing to resume — no stopped scan with unfinished companies.":
            "Nothing to resume — no stopped scan with unfinished companies.",
        "Could not find a scan to resume:\n{error}":
            "Could not find a scan to resume:\n{error}",
        "Resuming {run} — {done}/{total} companies already done, {remaining} remaining.":
            "Resuming {run} — {done}/{total} companies already done, {remaining} remaining.",
        "(+{n} companies added to seeds since the stop — they are included.)":
            "(+{n} companies added to seeds since the stop — they are included.)",
        "Resuming — {done}/{total} done.":
            "Resuming — {done}/{total} done.",
        "Resume {run} — {done}/{total} done, {remaining} remaining.":
            "Resume {run} — {done}/{total} done, {remaining} remaining.",
        "resumed": "resumed",
        "resumed from": "resumed from",
        "Starting scan…": "Starting scan…",
        "ATS": "ATS",
        "Career": "Career",
        "Scan": "Scan",
        "Finished.": "Finished.",
        "Cancelled — partial progress shown.": "Cancelled — partial progress shown.",
        "Data Quality description": "Remove duplicate jobs/companies, clear expired (stale) jobs, or wipe all scanned data.",
        "Freshness Check description": "Re-verify saved jobs against their live pages and mark expired listings.",
    },

    # ═══════════════════════════════════════════════════════════════════
    # ITALIAN TRANSLATIONS
    # ═══════════════════════════════════════════════════════════════════
    "it": {
        # ── Header ─────────────────────────────────────────────────────
        "Verified sponsorship-focused jobs from official career pages and ATS boards":
            "Lavori verificati da pagine carriera ufficiali e bacheche ATS - Per Hamliee ❤ !!",
        "Ready": "Pronto",
        "Scan complete.": "Scansione completata.",

        # ── Tab names ──────────────────────────────────────────────────
        "Search": "Cerca",
        "Dashboard": "Pannello",
        "Applications": "Candidature",
        "ATS Health": "Stato ATS",
        "AI Tailor": "AI Personalizza",
        "Tools": "Strumenti",

        # ── Search tab ─────────────────────────────────────────────────
        "Title:": "Posizione:",
        "Company:": "Azienda:",
        "Country:": "Paese:",
        "Sponsorship:": "Sponsorizzazione:",
        "Remote:": "Remoto:",
        "Experience:": "Esperienza:",
        "Experience": "Esperienza",
        "No experience requirement found in the job description": "Nessun requisito di esperienza indicato nella descrizione del lavoro",
        "The ad mentions experience without stating a figure or a level.": "L'annuncio cita l'esperienza senza indicare né cifre né livello.",
        "Seniority level \"{level}\" — the ad states no number of years.": "Livello di seniority \"{level}\" — l'annuncio non indica un numero di anni.",
        "Job title": "Posizione",
        "Location": "Località",
        "Copy URL": "Copia URL",
        "Job saved to Applications.": "Lavoro salvato nelle Candidature.",
        "Blue Card": "Carta Blu",
        "Reloc": "Ricollocaz.",
        "Sponsor": "Sponsor",
        "SponsorScout": "SponsorScout",
        "Sort:": "Ordina:",
        "Objective:": "Obiettivo:",
        "Balanced": "Bilanciato",
        "Strict quality": "Qualità rigorosa",
        "Visa sponsor": "Sponsor visto",
        "Local EU": "UE locale",
        "Blue Card focus": "Focus Blue Card",
        "EU Blue Card": "Carta Blu UE",
        "Relocation": "Ricollocazione",
        "All": "Tutti",
        "Regex": "Regex",
        "Enable regular-expression matching in Title / Company / Location filters (e.g. ^senior (backend|platform)$).":
            "Abilita la corrispondenza con espressioni regolari nei filtri Posizione / Azienda / Località (es. ^senior (backend|platform)$).",
        "Invalid regular expression": "Espressione regolare non valida",
        "Regex disabled — invalid pattern:\n{error}":
            "Regex disabilitato — pattern non valido:\n{error}",
        "Any (incl. unknown)": "Qualsiasi (incl. sconosciuto)",
        "Unknown / Not classified": "Sconosciuto / Non classificato",
        "Intern": "Stage",
        "Entry": "Junior",
        "Mid": "Intermedio",
        "Senior": "Senior",
        "Lead": "Capo",
        "Exec": "Dirigente",
        "Best match": "Miglior corrispondenza",
        "Latest": "Più recenti",
        "Sponsored Only": "Solo Sponsorizzati",
        "Remote EU": "Remoto UE",
        "Remote EMEA": "Remoto EMEA",
        "Remote Global": "Remoto Globale",
        "Remote Only": "Solo Remoto",
        "Hybrid": "Ibrido",
        "jobs found": "lavori trovati",
        "job found": "lavoro trovato",

        # ── AI Rating panel ────────────────────────────────────────────
        "AI Job Rating & Eligibility": "Valutazione AI ed Eleggibilità Lavoro",
        "Tailor CV & Letter": "Personalizza CV e Lettera",
        "No job selected.": "Nessun lavoro selezionato.",
        "Rating against your saved CV profile":
            "Valutazione basata sul tuo CV salvato",
        "Rating will use your saved CV profile":
            "La valutazione userà il tuo CV salvato",

        # ── Search: right-click context menu ───────────────────────────
        "Open in browser": "Apri nel browser",
        "Save to Applications": "Salva nelle Candidature",
        "Tailor CV & Cover Letter": "Personalizza CV e Lettera",

        # ── Dashboard ──────────────────────────────────────────────────
        "Companies": "Aziende",
        "Verified Jobs": "Lavori Verificati",
        "Sponsored": "Sponsorizzati",
        "Remote": "Remoti",
        "New this week": "Nuovi questa settimana",
        "Top companies by sponsorship score":
            "Migliori aziende per punteggio di sponsorizzazione",
        "Jobs by country": "Lavori per paese",

        # ── Applications tab ───────────────────────────────────────────
        "Saved applications": "Candidature salvate",
        "Refresh": "Aggiorna",
        "Remove Selected": "Rimuovi Selezionato",
        "Edit selected": "Modifica selezionato",
        "Status:": "Stato:",
        "Notes:": "Note:",
        "saved": "salvato",
        "applied": "candidatura inviata",
        "interview": "colloquio",
        "offer": "offerta",
        "rejected": "rifiutato",

        # ── ATS Health tab ─────────────────────────────────────────────
        "ATS Connector Health": "Stato Connettore ATS",
        "Success/failure rates per connector after each scan.":
            "Tasso di successo/errore per connettore dopo ogni scansione.",

        # ── Tools tab ──────────────────────────────────────────────────
        "Scanner": "Scansione",
        "Scan all 111 companies via their official ATS APIs.":
            "Scansiona tutte le 111 aziende tramite le loro API ATS ufficiali.",
        "idle": "inattivo",
        "Scan Now": "Scansiona Ora",
        "Auto (1 h)": "Automatico (1 h)",
        "Stop": "Ferma",
        "Pause": "Pausa",
        "Showing {a}–{b} of {n} results":
            "Mostrati {a}–{b} di {n} risultati",
        "Previous page": "Pagina precedente",
        "Next page": "Pagina successiva",
        "Rows per page": "Righe per pagina",
        "Scan Log:": "Registro Scansione:",
        "Data Quality": "Qualità Dati",
        "Remove duplicate jobs and companies from the database.":
            "Rimuovi lavori e aziende duplicati dal database.",
        "Clear Scan Data": "Cancella Dati di Scansione",
        "The database already contains no scanned data.":
            "Il database non contiene dati scansionati.",
        "This will permanently delete {jobs} scanned job(s), {runs} scan run(s) and {logs} scan log row(s) from the database.\nSeed CSVs and saved applications are NOT affected. Continue?":
            "Questo eliminerà permanentemente {jobs} lavoro/i scansionato/i, {runs} esecuzione/i di scansione e {logs} riga/i di registro dal database.\nI CSV semina e le candidature salvate NON saranno influenzati. Continuare?",
        "Scan data cleared": "Dati di Scansione Cancellati",
        "Removed {jobs} job(s), {runs} scan run(s) and {logs} scan log row(s). Seed CSVs were not touched.":
            "Rimosso {jobs} lavoro/i, {runs} esecuzione/i di scansione e {logs} riga/i di registro. I CSV semina non sono stati toccati.",
        "Stale data cleared": "Dati Obsoleti Cancellati",
        "Removed {n} expired job(s) from the database.":
            "Rimosso {n} lavoro/i scaduto/i dal database.",
        "No expired jobs to remove — the database is already clean.":
            "Nessun lavoro scaduto da rimuovere — il database è già pulito.",
        "View Per-Company Log": "Vedi Registro per Azienda",
        "Download Scan Log": "Scarica Registro di Scansione",
        "Select a scan run first.": "Seleziona prima un'esecuzione di scansione.",
        "Scan log downloaded": "Registro di scansione scaricato",
        "Scan log saved to:\n{path}": "Registro di scansione salvato in:\n{path}",
        "CSV files (*.csv);;All files (*.*)": "File CSV (*.csv);;Tutti i file (*.*)",
        "Could not save scan log:\n{error}": "Impossibile salvare il registro di scansione:\n{error}",
        "The file appears empty:\n{path}": "Il file appare vuoto:\n{path}",
        "Scan log ({bytes} bytes, {rows} company row(s), {events} event(s)) saved to:\n{path}":
            "Registro di scansione ({bytes} byte, {rows} riga/i aziendale/i, {events} evento/i) salvato in:\n{path}",
        "Run Dedup": "Esegui Deduplicazione",
        "AI Settings": "Impostazioni AI",
        "AI provider API key + prompts for job rating, eligibility (uses your CV from AI Tailor tab), and document generation.":
            "Chiave API del provider AI + prompt per valutazione lavori, eleggibilità (usa il CV dalla scheda AI Personalizza) e generazione documenti.",
        "AI API Key / Token:": "Chiave API / Token AI:",
        "Save Key": "Salva Chiave",
        "Provider:": "Provider:",
        "Model:": "Modello:",
        "(type any model name; chips are provider-specific)":
            "(inserisci qualsiasi nome modello; i suggerimenti sono specifici del provider)",
        "Base URL:": "URL Base:",
        "(auto-filled for built-in providers; required for custom)":
            "(compilato automaticamente per provider integrati; obbligatorio per personalizzato)",
        "type the exact model ID from your provider":
            "inserisci l'ID esatto del modello dal tuo provider",
        "Job Rating & Eligibility Prompt  (AI uses this + your CV to score each job):":
            "Prompt Valutazione ed Eleggibilità Lavoro  (l'AI usa questo + il tuo CV per valutare ogni lavoro):",
        "Save Prompt": "Salva Prompt",
        "Reset to Default": "Ripristina Predefinito",
        "CV Tailoring Prompt  (how AI rewrites your CV to match a JD):":
            "Prompt Personalizzazione CV  (come l'AI riscrive il tuo CV per corrispondere a un annuncio):",
        "Save CV Prompt": "Salva Prompt CV",
        "Reset": "Ripristina",
        "Cover / Motivation Letter Prompt  (for EU-based roles — personalised from your CV + JD):":
            "Prompt Lettera di Presentazione  (per ruoli nell'UE — personalizzata dal tuo CV + annuncio):",
        "Save Letter Prompt": "Salva Prompt Lettera",
        "Company Discovery": "Scoperta Aziende",
        "Probes 210 curated ATS boards.  Use role keywords: 'analyst', 'engineer', 'backend', 'data'.":
            "Indaga 210 bacheche ATS curate. Usa parole chiave: 'analista', 'ingegnere', 'backend', 'dati'.",
        "Query:": "Ricerca:",
        "Discover": "Scopri",
        "Freshness Check": "Verifica Aggiornamento",
        "Verify jobs still exist online — auto-expires dead links.":
            "Verifica che i lavori esistano ancora online — scadenza automatica dei link morti.",
        "Max jobs:": "Max lavori:",
        "Run": "Esegui",

        # ── AI Tailor tab ──────────────────────────────────────────────
        "AI CV & Cover Letter Tailor": "AI Personalizzazione CV e Lettera di Presentazione",
        "Select a job in Search → 'Tailor CV & Letter', or load a JD manually below":
            "Seleziona un lavoro in Cerca → 'Personalizza CV e Lettera', o carica un annuncio manualmente",
        "No job selected — use Search tab or paste a JD below":
            "Nessun lavoro selezionato — usa la scheda Cerca o incolla un annuncio",
        "Job URL:": "URL Lavoro:",
        "Fetch JD": "Scarica Annuncio",
        "— or paste full JD below —": "— oppure incolla l'annuncio completo qui sotto —",
        "Use this JD": "Usa questo Annuncio",
        "My CV (stored locally)": "Il mio CV (salvato localmente)",
        "Paste your current CV once — it's saved for all future tailoring sessions.":
            "Incolla il tuo CV una volta — viene salvato per tutte le future sessioni di personalizzazione.",
        "Save CV": "Salva CV",
        "CV saved": "CV salvato",
        "Base Cover Letter (template for AI)":
            "Lettera di Presentazione Base (modello per l'AI)",
        "Paste an example cover letter you like. The AI will match its style/tone when generating new letters.":
            "Incolla una lettera di presentazione che ti piace. L'AI ne riprodurrà lo stile/tono quando genererà nuove lettere.",
        "Save Template": "Salva Modello",
        "Template saved": "Modello salvato",
        "Generate": "Genera",
        "Tailor My CV": "Personalizza il mio CV",
        "Write Cover Letter": "Scrivi Lettera di Presentazione",
        "Both": "Entrambi",
        "Result": "Risultato",
        "CV": "CV",
        "Cover Letter": "Lettera di Presentazione",
        "Copy": "Copia",
        "Fetching…": "Scaricamento…",

        # ── Messages: tailor tab ───────────────────────────────────────
        "Empty JD": "Annuncio vuoto",
        "Paste a job description first.":
            "Incolla prima una descrizione del lavoro.",
        "No URL": "Nessun URL",
        "Enter a job post URL first.":
            "Inserisci prima un URL dell'annuncio di lavoro.",
        "Empty CV": "CV vuoto",
        "Paste your CV first.":
            "Incolla prima il tuo CV.",
        "No Job Description": "Nessun Annuncio",
        "Fetch or paste a job description first, then click 'Use this JD'.":
            "Scarica o incolla prima un annuncio, poi clicca 'Usa questo Annuncio'.",
        "No CV": "Nessun CV",
        "Paste your CV in the 'My CV' box and save it first.":
            "Incolla il tuo CV nella sezione 'Il mio CV' e salvalo prima.",
        "Copied to clipboard!": "Copiato negli appunti!",
        "Select a row": "Seleziona una riga",
        "Click a row first, then click Remove.":
            "Clicca prima su una riga, poi su Rimuovi.",
        "Remove": "Rimuovi",
        "Search error": "Errore di ricerca",
        "Error": "Errore",
        "Missing": "Manca",
        "Enter a search query.":
            "Inserisci una ricerca.",

        # ── Messages: AI settings ──────────────────────────────────────
        "AI API key saved.": "Chiave API AI salvata.",
        "Empty": "Vuoto",
        "Prompt cannot be empty.": "Il prompt non può essere vuoto.",
        "Custom prompt saved.": "Prompt personalizzato salvato.",
        "CV tailoring prompt saved.": "Prompt personalizzazione CV salvato.",
        "Cover letter prompt saved.": "Prompt lettera di presentazione salvato.",
        "Reset prompt to default?":
            "Ripristinare il prompt predefinito?",
        "Reset CV tailoring prompt to default?":
            "Ripristinare il prompt di personalizzazione CV predefinito?",
        "Reset cover letter prompt to default?":
            "Ripristinare il prompt della lettera di presentazione predefinito?",
        "Unknown provider": "Provider sconosciuto",
        "Missing model": "Modello mancante",
        "Type a model name first.":
            "Inserisci prima il nome di un modello.",
        "Missing base URL": "URL base mancante",

        # ── Messages: first run ────────────────────────────────────────
        "Welcome to SponsorScout": "Benvenuto in SponsorScout",

        # ── Messages: general ──────────────────────────────────────────
        "No job selected": "Nessun lavoro selezionato",
        "Select a job in the Search tab first.":
            "Seleziona prima un lavoro nella scheda Cerca.",
        "Dashboard data could not be loaded.":
            "Impossibile caricare i dati del pannello.",
        "Dedup complete": "Deduplicazione completata",
        "Probing ATS boards for": "Indagine bacheche ATS per",
        "Found": "Trovati",
        "candidate(s).": "candidato/i.",
        "No new companies found.":
            "Nessuna nuova azienda trovata.",
        "Try: 'analyst', 'backend', 'data engineer'":
            "Prova: 'analista', 'backend', 'ingegnere dati'",
        "Discovery done.": "Scoperta completata.",
        "Discovery failed.": "Scoperta fallita.",
        "Running discovery…": "Scoperta in corso…",
        "running…": "in esecuzione…",
        "Scanning…": "Scansione in corso…",
        "auto — every 1 h": "automatico — ogni 1 h",
        "stopped": "fermato",
        "Failed.": "Fallito.",
        "Verified": "Verificato",

        # ── Tooltip: How to Use ────────────────────────────────────────
        "AI Tailor — How to Use": "AI Personalizza — Come Usare",

        # ── Tooltip: JD section ────────────────────────────────────────

        # ── Tooltip: CV section ────────────────────────────────────────
        "My CV": "Il mio CV",

        # ── Tooltip: Cover Letter section ──────────────────────────────
        "Base Cover Letter Template": "Modello Lettera di Presentazione Base",

        # ── Tooltip: Generate section ──────────────────────────────────
        "Generate Buttons": "Pulsanti di Generazione",

        # ── Tooltip: Result section ────────────────────────────────────
        "Result Area": "Area Risultati",

        # ── AI Assistant tab ────────────────────────────────────────────
        "How to use": "Come usare",

        # ── Language toggle ────────────────────────────────────────────

        # ── AI / Search extras ───────────────────────────────────────
        "Contacting Gemini API...": "Contatto API Gemini...",
        "Contacting custom AI endpoint...": "Contatto endpoint AI personalizzato...",
        "Contacting {provider} OpenAI-compatible API...": "Contatto API {provider} compatibile OpenAI...",
        "Job Description": "Descrizione del lavoro",
        "No CV on file — paste yours in AI Tailor tab for personalised results":
            "Nessun CV salvato — incolla il tuo nella scheda AI Personalizza per risultati personalizzati",
        "Rate this job": "Valuta questo lavoro",
        "Rate with AI": "Valuta con AI",
        "Saved": "Salvato",
        "Select a job, then click 'Rate this job'.": "Seleziona un lavoro, poi clicca 'Valuta questo lavoro'.",

        # ── Tools tab extras ─────────────────────────────────────────
        "Scan History": "Storico Scansioni",
        "Idle": "Inattivo",
# ── Dashboard ──────────────────────────────────────────────────
        "Total Companies": "Aziende Totali",
        "Sponsored Jobs": "Lavori Sponsorizzati",
        "Remote Jobs": "Lavori Remoti",
        "Top Companies by Sponsorship": "Migliori Aziende per Sponsorizzazione",
        "Jobs by Country": "Lavori per Paese",
        "Company": "Azienda",
        "Country": "Paese",
        "Jobs": "Lavori",
        "Top Sponsor": "Miglior Sponsor",
        "Rescan Companies": "Riscansiona Aziende",
        "Unknown": "Sconosciuto",

        # ── Tools descriptions ────────────────────────────────────────
        "Scanner description": "Avvia una scansione lavori su tutte le aziende seminate — ogni board ATS e pagina carriere viene scansionata, poi ogni lavoro viene arricchito dalla sua pagina di dettaglio. L'output live appare qui sotto.",
        "Scan every seeded company (ATS boards + career pages) and enrich each job from its detail page, so no listing misses its evidence.":
            "Scansiona ogni azienda seminata (board ATS + pagine carriere) e arricchisce ogni lavoro dalla sua pagina di dettaglio, così nessun annuncio resta senza evidenza.",
        "Custom Scan": "Scansione Personalizzata",
        "Custom scan started": "Scansione personalizzata avviata",
        "Starting custom scan…": "Avvio scansione personalizzata…",
        "Choose specific companies and/or source types (ATS and/or career portals) to scan instead of every seeded company.":
            "Scegli aziende specifiche e/o tipi di fonte (ATS e/o pagine carriere) da scansionare invece di tutte le aziende seminate.",
        "Choose the source types and companies to scan. Selected companies are scanned exactly like in a full scan — the full scan simply covers every seeded company.":
            "Scegli i tipi di fonte e le aziende da scansionare. Le aziende selezionate vengono scansionate esattamente come in una scansione completa — la scansione completa copre semplicemente tutte le aziende seminate.",
        "Scan ATS portals (API-based, fast)": "Scansiona i portali ATS (basati su API, veloce)",
        "Scan career portals (crawled, slower)": "Scansiona i portali carriere (crawled, più lento)",
        "Filter companies…": "Filtra aziende…",
        "Select all": "Seleziona tutto",
        "Clear": "Azzera",
        "Start Custom Scan": "Avvia Scansione Personalizzata",
        "Selected: {parts}": "Selezionati: {parts}",
        "ATS: {n}/{total}": "ATS: {n}/{total}",
        "Career: {n}/{total}": "Carriere: {n}/{total}",
        "Select at least one source type.": "Seleziona almeno un tipo di fonte.",
        "Select at least one company.": "Seleziona almeno un'azienda.",
        "Could not open the custom scan dialog.": "Impossibile aprire la finestra di scansione personalizzata.",
        "Scan History description": "Ogni scansione passata. Seleziona una riga per visualizzare o scaricare il suo registro per azienda con errori.",
        "Resume": "Riprendi",
        "Continue the last stopped scan — only companies it did not finish are scanned, so no progress is lost.":
            "Continua l'ultima scansione interrotta — vengono scansionate solo le aziende non completate, nessun progresso va perso.",
        "Stop the scan now and keep everything found so far. Press Resume later to continue the remaining companies — all browsers close, so other apps run smoothly again.":
            "Ferma ora la scansione mantenendo tutto ciò che è stato trovato. Premi Riprendi più tardi per continuare le aziende restanti — tutti i browser si chiudono, così le altre app tornano fluide.",
        "Pause the running scan in place — workers stop at the next company and browsers wait. Press Resume to continue instantly, no new scan is started.":
            "Metti in pausa la scansione in corso — i worker si fermano alla prossima azienda e i browser attendono. Premi Riprendi per continuare subito, senza avviare una nuova scansione.",
        "Continue the paused scan where it stopped — same run, no loss.":
            "Continua la scansione in pausa da dove si è fermata — stessa esecuzione, nessuna perdita.",
        "Stop the scan now and keep everything found so far. The stopped run is checkpointed — Resume (the other button) starts a new scan for the companies that were not finished, even after an app restart.":
            "Ferma ora la scansione mantenendo tutto ciò che è stato trovato. L'esecuzione interrotta viene salvata — Riprendi (l'altro pulsante) avvia una nuova scansione solo per le aziende non finite, anche dopo un riavvio dell'app.",
        "Looking up the last stopped scan…":
            "Ricerca dell'ultima scansione interrotta…",
        "Paused": "In pausa",
        "Pausing…": "Pausa in corso…",
        "Stopping…": "Arresto in corso…",
        "Stopped.": "Fermata.",
        "Stop the running scan? Everything found so far is kept and can be resumed later.":
            "Fermare la scansione in corso? Tutto ciò che è stato trovato viene mantenuto e potrà essere ripreso più tardi.",
        "Resuming scan": "Ripresa scansione",
        "Nothing to resume — no stopped scan with unfinished companies.":
            "Nulla da riprendere — nessuna scansione interrotta con aziende incompiute.",
        "Could not find a scan to resume:\n{error}":
            "Impossibile trovare una scansione da riprendere:\n{error}",
        "Resuming {run} — {done}/{total} companies already done, {remaining} remaining.":
            "Ripresa {run} — {done}/{total} aziende già fatte, {remaining} restanti.",
        "(+{n} companies added to seeds since the stop — they are included.)":
            "(+{n} aziende aggiunte ai seed dopo lo stop — sono incluse.)",
        "Resuming — {done}/{total} done.":
            "Ripresa — {done}/{total} fatte.",
        "Resume {run} — {done}/{total} done, {remaining} remaining.":
            "Riprendi {run} — {done}/{total} fatte, {remaining} restanti.",
        "resumed": "ripresa",
        "resumed from": "ripresa da",
        "Starting scan…": "Avvio scansione…",
        "ATS": "ATS",
        "Career": "Carriere",
        "Scan": "Scansione",
        "Finished.": "Finita.",
        "Cancelled — partial progress shown.": "Annullata — avanzamento parziale mostrato.",
        "Data Quality description": "Rimuovi lavori/aziende duplicati, cancella lavori scaduti (obsoleti) o elimina tutti i dati scansionati.",
        "Freshness Check description": "Riverifica i lavori salvati rispetto alle loro pagine live e segna le scadute.",

        # ── Complete UI coverage (tools/data/apps/dashboard + dialogs) ──
        # Every literal key used anywhere in the app must exist here so the
        # Italian UI never falls back to English. Guarded by
        # tests/test_i18n_parity.py::test_every_ui_key_has_italian_translation.
        " Edit selected ": " Modifica selezionato ",
        "A scan is already running.": "Una scansione è già in corso.",
        "A source with this name and URL already exists.": "Esiste già una fonte con questo nome e questo URL.",
        "ATS / source type": "ATS / tipo di fonte",
        "ATS portals": "Portali ATS",
        "Add source": "Aggiungi fonte",
        "Add…": "Aggiungi…",
        "Advanced (optional)": "Avanzate (facoltativo)",
        "All reasons": "Tutti i motivi",
        "Application updated.": "Candidatura aggiornata.",
        "Artifact:": "Artefatto:",
        "Cancel": "Annulla",
        "Cannot save": "Impossibile salvare",
        "Career portals": "Portali carriere",
        "Careers URL": "URL carriera",
        "Check up to": "Controlla fino a",
        "Checked {checked} — expired {expired}.": "Controllati {checked} — scaduti {expired}.",
        "Clear Stale Data": "Cancella Dati Obsoleti",
        "Company name": "Nome azienda",
        "Data Management": "Gestione Dati",
        "Delete": "Elimina",
        "Delete '{}' from this seed file? (Not written until you save.)":
            "Eliminare '{}' da questo file seed? (Non scritto finché non salvi.)",
        "Delete source": "Elimina fonte",
        "Discard all edits and restore the seed file shipped with the application.":
            "Scarta tutte le modifiche e ripristina il file seed incluso nell'applicazione.",
        "Discard all edits and restore the seed file shipped with the application?":
            "Scartare tutte le modifiche e ripristinare il file seed incluso nell'applicazione?",
        "Duplicate source": "Fonte duplicata",
        "Dups": "Dup",
        "Edit source": "Modifica fonte",
        "Edit…": "Modifica…",
        "Errors": "Errori",
        "File:": "File:",
        "Freshness check done.": "Controllo aggiornamento completato.",
        "Industry": "Settore",
        "Invalid data": "Dati non validi",
        "Language: ": "Lingua: ",
        "Load": "Carica",
        "Maximum number of active jobs to re-verify per run.":
            "Numero massimo di lavori attivi da riverificare per esecuzione.",
        "Method": "Metodo",
        "No bundled default file is available.": "Nessun file predefinito incluso disponibile.",
        "No quarantine artifacts found.": "Nessun artefatto in quarantena trovato.",
        "No selection": "Nessuna selezione",
        "Promote Selected to Jobs": "Porta selezionati nei lavori",
        "Quarantine": "Quarantena",
        "Quarantine Review": "Revisione Quarantena",
        "Quarantined": "In quarantena",
        "Reason:": "Motivo:",
        "Reload": "Ricarica",
        "Reset failed": "Ripristino fallito",
        "Reset to bundled defaults": "Ripristina i valori predefiniti inclusi",
        "Review Quarantine": "Rivedi Quarantena",
        "Row {} has problems:": "La riga {} ha problemi:",
        "Run ID": "ID esecuzione",
        "Running…": "In esecuzione…",
        "Save": "Salva",
        "Save to CSV": "Salva in CSV",
        "Saved on": "Salvato il",
        "Scan finished: ": "Scansione terminata: ",
        "Scan output appears here…": "L'output della scansione appare qui…",
        "Scan started": "Scansione avviata",
        "Scan stopped.": "Scansione fermata.",
        "Scope policy": "Politica di ambito",
        "Posted": "Pubblicato",
        "Seed files changed — they will be used on the next scan.":
            "File seed modificati — verranno usati alla prossima scansione.",
        "Select a row to delete first.": "Seleziona prima una riga da eliminare.",
        "Select a row to edit first.": "Seleziona prima una riga da modificare.",
        "Select rows first.": "Seleziona prima delle righe.",
        "Source type": "Tipo di fonte",
        "Started": "Avviata",
        "Status": "Stato",
        "Title": "Titolo",
        "URL": "URL",
        "Verifying up to {n} jobs…": "Verifica di fino a {n} lavori…",
        "jobs": "lavori",
        "{n} jobs ingested": "{n} lavori acquisiti",
        "{n} row(s) promoted into jobs.": "{n} riga/e promosse nei lavori.",
        "{shown} of {total} quarantined rows.": "{shown} di {total} righe in quarantena.",
        "{} companies": "{} aziende",
        "{} companies written to\n{}": "{} aziende scritte in\n{}",
        "↻ Refresh": "↻ Aggiorna",
        "Remove  {title}  at  {company}?": "Rimuovere  {title}  presso  {company}?",
        "Removed {jobs} duplicate job(s) and {companies} duplicate company entry(ies).":
            "Rimosse {jobs} voci duplicate di lavori e {companies} voci duplicate di aziende.",
        "Manage the source URLs scanned by SponsorScout.  ATS portals are scanned via their job-board APIs; career portals are crawled on the company site.  Edits are saved to your personal seed files and take effect on the next scan.":
            "Gestisci gli URL delle fonti scansionate da SponsorScout. I portali ATS vengono scansionati tramite le API dei job board; i portali carriere vengono crawpati sul sito dell'azienda. Le modifiche sono salvate nei tuoi file seed personali e hanno effetto alla prossima scansione.",
        "No data yet.\n\nRun the first scan now? It fetches jobs from each company's official career page and ATS board (1–3 minutes).":
            "Nessun dato ancora.\n\nAvviare ora la prima scansione? Recupera i lavori dalla pagina carriera e dal board ATS ufficiale di ogni azienda (1–3 minuti).",
    },
}


def _(text: str) -> str:
    """Translate a string to the current locale.

    Falls back to English (the key itself) if the string is not translated.
    """
    return LANGUAGES.get(_locale, {}).get(text, text)