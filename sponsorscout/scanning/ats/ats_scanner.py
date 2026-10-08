# ─────────────────────────────────────────────────────────────────────────────
# ATS Career Portal Scanner v5
#
# Scans public ATS job boards (Ashby, Greenhouse, Lever, SmartRecruiters,
# Personio, Recruitee, Workable, Workday) via their official APIs, with a
# browser fallback for anything else. This is the ATS counterpart of the
# career-page scanner (career_portal_scanner_v7.py) and shares its output
# schema, scan-log format, and policies:
#
#   • Fresh output by default (never silently appends); --resume is explicit.
#   • Recruiters written to a separate <output>_recruiter.csv.
#   • Quarantined rows written to <output>_quarantine.csv (never silently dropped).
#   • Per-run scan log <output>_scan_log.csv that matches the jobs output.
#   • Visa sponsorship / relocation / EU Blue Card classified ONLY from explicit
#     evidence in the job description; otherwise "Unknown" (no fabricated "N").
#   • Job Type / Location default to "Unknown" when no evidence (no fabricated
#     "Full-time / On-site" or "Not Specified").
#   • Canonical requisition IDs dedupe mirror URLs (apply vs job URLs).
#   • Network retry + backoff and a pre-flight connectivity gate.
#   • Self-healing seed upgrade (fixes wrong/legacy URLs, EU Lever, recruiter tags).
# ─────────────────────────────────────────────────────────────────────────────
import csv
import json
import logging
import os
import re
import urllib.request          # fetch_static_jobs uses urllib.request.* by name
import collections
import socket
import time
import unicodedata
from collections import Counter
from html import unescape
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse, urljoin, unquote
from urllib.request import Request, urlopen

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_IMPORT_ERROR = None
except ModuleNotFoundError as _pw_exc:
    sync_playwright = None
    # Keep the reason: a bare None hid WHICH module was missing from the
    # frozen bundle (playwright itself, greenlet or pyee).
    PLAYWRIGHT_IMPORT_ERROR = f"{type(_pw_exc).__name__}: {_pw_exc}"

# Desktop UI Stop/Pause. Imported, not reimplemented, so both scanners obey
# the same control protocol.
from sponsorscout.scanning.common import check_control

# Real-time logging: flush prints during long runs.
import builtins as _builtins

# Installed by the desktop app (set_progress_callback) so scanner output is
# routed to the UI log instead of a console nobody sees. None = plain stdout.
progress_cb = None


def set_progress_callback(callback):
    """Route this module's print()/_notify output to the desktop log."""
    global progress_cb
    progress_cb = callback
def print(*args, **kwargs):
    _notify(" ".join(str(a) for a in args))

def _playwright_unavailable_reason():
    """Why sync_playwright is unusable — surfaced in error logs."""
    return PLAYWRIGHT_IMPORT_ERROR or "playwright.sync_api could not be imported"

def _notify(msg):
    if progress_cb:
        progress_cb(msg)
    else:
        _builtins.print(msg, flush=True)

def _log_file_path() -> str:
    """Where to write the scanner's diagnostic log.

    Never the current working directory: a packaged Windows build is installed
    under Program Files, which is not writable — the previous hard-coded
    ``ats_scraper_errors.log`` relative path made ``logging.basicConfig`` fail
    (or silently write a stray file next to the seed CSVs).  The per-user scan
    output directory is always writable, and keeps the diagnostic log next to
    the run artifacts it describes.
    """
    try:
        from sponsorscout import paths

        return str(paths.ensure_scan_output_dir() / "ats_scraper_errors.log")
    except Exception:  # standalone single-file mode
        return "ats_scraper_errors.log"

logging.basicConfig(
    filename="ats_scraper_errors.log",
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
)

# ─────────────────────────────────────────────────────────────────────────────
# FIX P0-31: STATIC-HTML FAST PATH (speed, accuracy-preserving)
#
# Measured on this seed set: of 18 sampled provider=auto rows, 6 served their
# postings as plain anchors in the initial HTML response (Prada 18 links,
# Miro 58, Anymind 270, Bunq 16, Kaufland 27, Audible 15). Those companies do
# not need a 600 MB Chromium and ~7 s of fixed settling delays.
#
# HONEST SCOPE — what this does NOT do:
#   • It does not replace the browser. It TRIES static first and falls back to
#     the existing DOM path whenever the static yield looks thin.
#   • Blocking images was measured at +0% wall clock (11.2s -> 11.2s), so that
#     is NOT where the speedup comes from. The saving here is skipping browser
#     launch + settle time on pages that never needed a browser.
#
# SAFETY GATE — the fast path is only ACCEPTED when it finds at least
# _STATIC_MIN_JOBS distinct job-like links. Anything less and we discard the
# static result entirely and run the browser exactly as before, so a
# JS-rendered board can never silently produce a truncated row count.
# ─────────────────────────────────────────────────────────────────────────────

# A static result must clear this bar to be trusted. Chosen deliberately high:
# a board with 1-4 visible links is far more likely to be a JS shell that
# happens to expose a couple of static links than a genuinely tiny board.
_STATIC_MIN_JOBS = 5

# Hosts that are known JS-only shells: never waste a static request on them.
_STATIC_SKIP_HOSTS = (
    "myworkdayjobs.com", "icims.com", "taleo.net", "successfactors",
    "csod.com", "avature.net", "eightfold.ai", "phenompeople.com",
    "oraclecloud.com", "brassring.com", "jobvite.com", "workday.com",
)

# Client-side-rendering markers. When present, the HTML we received is a
# hydration shell: some postings are in the markup but the full list is built
# in the browser. Measured on Miro — static saw 12 links, the browser 27.
# Presence of ANY of these disqualifies the static fast path outright.
_STATIC_SPA_MARKERS = (
    "__NEXT_DATA__", "__NUXT__", "__INITIAL_STATE__", "__APOLLO_STATE__",
    "window.__remixContext", "data-reactroot", "ng-version=",
    "data-svelte-h", "__sveltekit", "data-vue-meta", "id=\"__nuxt\"",
)


# ── FIX P27 (2026-10-05): one workload classifier, shared by both scanners ──
# Amazon Italia printed 121 of 183 rows as "Part-time", including
# "Cloud Operations Architect" and "Senior Theatrical Marketing Manager".
# Every amazon.jobs posting carries the pay footnote
#     "The salary listed corresponds to working on a FULL-TIME basis.
#      For PART-TIME hours, the salary will be pro-rated."
# and the old classifiers tested `part[ -]?time` BEFORE full-time, against
# the whole card/JD blob, so the footnote decided the schedule of every
# corporate role on the board.
#
# The rules, strongest evidence first:
#   1. the employer's own structured field (employment_type / contract type)
#   2. the job TITLE
#   3. prose, with pay/pro-rata/conditional sentences discarded, and an
#      explicit full-time statement beating a surviving part-time mention
#      (a posting that states both is full-time with a part-time footnote)
# "Contract" is read from the employer field or the title ONLY: the word
# appears in ordinary prose constantly ("contract negotiation", "contract
# management") and used to make the ATS scanner call such roles Full-time
# while the career scanner called them Contract (P27b).

_WL_INTERN_RE = re.compile(
    r"\bintern(?:ship|s)?\b|\bpraktikum\b|\bpraktikant\w*|\btirocinio\b"
    r"|\bapprendistato\b|\btrainee\b|\bapprentic\w*|\bstagiaire\b"
    r"|\bstage\b|\bausbildung\b|\bduales studium\b|\bworking student\b"
    r"|\bwerkstudent\w*|\bbecari\w*|\bpr[áa]cticas\b", re.I)
_WL_PART_RE = re.compile(
    r"\bpart[ _-]?time\b|\bparttime\b|\bteilzeit\b|\bdeeltijd\b"
    r"|\btempo parziale\b|\btemps partiel\b|\bmedia jornada\b", re.I)
_WL_FULL_RE = re.compile(
    r"\bfull[ _-]?time\b|\bfulltime\b|\bvollzeit\b|\bvoltijd\b"
    r"|\btempo pieno\b|\btemps plein\b|\bjornada completa\b", re.I)
_WL_CONTRACT_RE = re.compile(
    r"\bcontract(?:or)?\b|\bfixed[ _-]?term\b|\btemporary\b|\btemp\b"
    r"|\binterim\b|\bbefristet\b|\bzeitarbeit\b|\btempo determinato\b"
    r"|\bcontratto a termine\b|\bcdd\b|\bint[ée]rim\b", re.I)
_WL_PERMANENT_RE = re.compile(
    r"\bpermanent\b|\bunbefristet\b|\bvast(?:e)? contract\b"
    r"|\btempo indeterminato\b|\bcdi\b|\bindefinido\b", re.I)

#: Sentences in which a part-time (or full-time) mention is NOT the schedule
#: of this posting: pay pro-rating, hour-reduction notes, equal-opportunity
#: boilerplate, and "if you work part-time" conditionals.
_WL_BOILERPLATE_RE = re.compile(
    r"\b(?:salary|salaries|compensation|pay|paid|wage|wages|rate|rates|"
    r"stipend|pension|holiday|leave|benefit|benefits|entitlement|pro[ -]?rata|"
    r"pro[ -]?rated|proportionally|accordingly|equivalent|basis|reduced|"
    r"regardless|whether|either|or\s+part|and/or)\b"
    r"|\bif\s+you\b|\bshould\s+you\b|\bwhere\s+applicable\b"
    r"|\bfor\s+part[ _-]?time\s+(?:hours|employees|colleagues|staff|roles)\b"
    r"|\bwe\s+(?:also\s+)?(?:offer|consider|welcome|support)\b", re.I)

_WL_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|[\n\r]+|\|")


def _wl_declared(text, pattern):
    """True when `pattern` fires in a sentence that is not pay boilerplate."""
    if not text:
        return False
    for sent in _WL_SENTENCE_SPLIT_RE.split(text):
        if not sent or not pattern.search(sent):
            continue
        if _WL_BOILERPLATE_RE.search(sent):
            continue
        return True
    return False


def classify_workload(employer_field="", title="", text=""):
    """Return Internship / Part-time / Full-time / Contract / "" (unknown).

    `employer_field` is a structured value from the provider (Workday's
    `timeType`, greenhouse's `employment_type`, the iCIMS `FULL_TIME` enum
    after humanisation). It is believed outright when it says anything.
    """
    field = (employer_field or "").strip()
    head = (title or "").strip()
    body = text or ""
    # 1. the employer said so.
    for src in (field, head):
        if not src:
            continue
        if _WL_INTERN_RE.search(src):
            return "Internship"
        if _WL_PART_RE.search(src):
            return "Part-time"
        if _WL_FULL_RE.search(src):
            return "Full-time"
        if _WL_CONTRACT_RE.search(src):
            return "Contract"
        if _WL_PERMANENT_RE.search(src):
            return "Full-time"
    # 2. prose, boilerplate sentences discarded.
    if _WL_INTERN_RE.search(body):
        return "Internship"
    part = _wl_declared(body, _WL_PART_RE)
    full = _wl_declared(body, _WL_FULL_RE)
    if part and not full:
        return "Part-time"
    if full:
        # Both stated in real sentences -> the posting is full-time and the
        # part-time wording is a footnote. This is the Amazon shape.
        return "Full-time"
    return ""


def classify_work_location_mode(text):
    """Return Remote / Hybrid / On-site / "" from the same evidence rules."""
    t = text or ""
    remote = bool(re.search(
        r"\bfully remote\b|\bremote[- ]first\b|\b100% remote\b|\bremote\b"
        r"|\bwork from home\b|\bhome[ -]?office\b|\bda remoto\b|\bremoto\b"
        r"|\bthuiswerk\w*|\bt[ée]l[ée]travail\b", t, re.I))
    hybrid = bool(re.search(
        r"\bhybrid\w*|\bibrido\b|\bsmart ?working\b|\bhybride\b", t, re.I))
    if remote and hybrid:
        return "Remote/Hybrid"
    if remote:
        return "Remote"
    if hybrid:
        return "Hybrid"
    if re.search(r"\bon[- ]?site\b|\bonsite\b|\bin[- ]?office\b|\bin office\b"
                 r"|\bvor ort\b|\bin sede\b|\bpresencial\b", t, re.I):
        return "On-site"
    return ""



_STATIC_ANCHOR_RE = re.compile(
    r"<a\b[^>]*?href\s*=\s*[\"']([^\"'#][^\"']*)[\"'][^>]*>(.*?)</a>",
    re.I | re.S)


def _static_strip_tags(fragment):
    """Inner HTML of an anchor -> visible text."""
    if not fragment:
        return ""
    txt = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


def fetch_static_jobs(seed_url, timeout_sec=15, min_jobs=None,
                      url_validator=None, title_validator=None):
    """Try to harvest postings from the raw HTML, with no browser.

    Returns (jobs, diagnostic). `jobs` is [] when the page needs JS — the
    caller must then run its normal browser path.

    The two validator callables are the scanner's own is_valid_job_url /
    is_valid_job_title, so the static path applies IDENTICAL acceptance rules
    to the browser path. That is what makes this safe: it changes how bytes
    are obtained, never what counts as a job.
    """
    if min_jobs is None:
        min_jobs = _STATIC_MIN_JOBS
    host = (urlparse(seed_url).hostname or "").lower()
    if any(marker in host for marker in _STATIC_SKIP_HOSTS):
        return [], "static: skipped (known JS-only host)"
    try:
        req = urllib.request.Request(seed_url, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            # Ask for the local language too: Italian/German boards often
            # serve localised markup, and the titles must survive.
            "Accept-Language": "en-US,en;q=0.9,it;q=0.8,de;q=0.8,nl;q=0.7,fr;q=0.7,es;q=0.7",
        })
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "html" not in ctype and "xml" not in ctype:
                return [], f"static: non-HTML content-type ({ctype[:40]})"
            raw = resp.read(4_000_000)
        charset = "utf-8"
        m = re.search(r"charset=([\w-]+)", ctype)
        if m:
            charset = m.group(1)
        html = raw.decode(charset, "replace")
    except Exception as exc:
        return [], f"static: fetch failed ({type(exc).__name__})"

    # Hydration shell -> the static markup is not the whole list. Bail out and
    # let the browser render it, rather than silently truncating the company.
    for _marker in _STATIC_SPA_MARKERS:
        if _marker in html:
            return [], f"static: client-rendered ({_marker}); using browser"

    jobs = []
    seen = set()
    for href, inner in _STATIC_ANCHOR_RE.findall(html):
        href = unescape(href.strip())
        if not href or href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(seed_url, href)
        if absolute in seen:
            continue
        title = _static_strip_tags(inner)
        if not title:
            continue
        if url_validator is not None and not url_validator(absolute):
            continue
        if title_validator is not None and not title_validator(title):
            continue
        seen.add(absolute)
        jobs.append({
            "job_title": title,
            "job_url": absolute,
            "location_hint": "",
            "card_context": "",
            "extraction_method": "static_html",
        })

    if len(jobs) < min_jobs:
        # Not trustworthy — discard and let the browser handle it.
        return [], (f"static: only {len(jobs)} job link(s) "
                    f"(<{min_jobs}); using browser")
    return jobs, f"static HTML: {len(jobs)}"

# ─────────────────────────────────────────────────────────────────────────────
# FIX P0-30: EXPERIENCE REQUIREMENT EXTRACTION
#
# Adds four output columns:
#   Experience Required   e.g. "5-8 years", "3+ years", "6 months", "None required"
#   Experience Min Years  numeric, for sorting/filtering
#   Experience Level      Internship / Junior / Mid / Senior / Lead / Executive
#   Experience Source     api_field > api_description > card_context > title_inference
#
# Design rules (mirrors the visa detector's evidence discipline):
#   • Sentence-scoped, never keyword-alone. A number counts only when its own
#     clause is about work experience (_EXP_ANCHOR) AND no disqualifier sits
#     within +/-45 chars (_EXP_BLOCK_NEAR). That window is what keeps
#     "at least 18 years old", "founded 25 years ago", "fixed-term contract of
#     2 years", "visa valid for 3 years" and "notice period of 3 months" out.
#   • Numeric years are reported ONLY when explicitly written. The LEVEL may be
#     inferred from the job title; "Experience Source" always records which
#     happened so an inference is never mistaken for a stated fact.
#   • Multilingual by construction (EN/DE/IT/NL/FR/ES/PT). The Career seed is
#     heavily Italian/German, so an English-only matcher would under-report.
#   • Title inference beats year-band inference: a "Senior Consultant" asking
#     for 4 years is Senior, not Mid.
# ─────────────────────────────────────────────────────────────────────────────

_EXP_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "a": 1, "an": 1,
    "ein": 1, "eine": 1, "eins": 1, "zwei": 2, "drei": 3, "vier": 4,
    "fuenf": 5, "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9,
    "zehn": 10, "elf": 11, "zwoelf": 12, "zwölf": 12,
    "uno": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6,
    "sette": 7, "otto": 8, "nove": 9, "dieci": 10,
    "een": 1, "twee": 2, "drie": 3, "vijf": 5, "zes": 6, "zeven": 7,
    "negen": 9, "tien": 10,
    "un": 1, "une": 1, "deux": 2, "trois": 3, "cinq": 5, "sept": 7,
    "huit": 8, "neuf": 9, "dix": 10,
    "dos": 2, "cuatro": 4, "cinco": 5, "siete": 7, "ocho": 8, "nueve": 9,
    "diez": 10,
}
_EXP_WORD_ALT = "|".join(sorted((re.escape(w) for w in _EXP_WORD_NUM),
                                key=len, reverse=True))

_EXP_YEAR_UNIT = (r"(?:years?|yrs?\.?|jahre?n?|anni|anno|jaar|jaren|"
                  r"ans|an|a[nñ]os|a[nñ]o)")
_EXP_MONTH_UNIT = (r"(?:months?|mon\.?|monate?n?|mesi|mese|maanden|maand|"
                   r"mois|meses|mes)")

# The number must sit in a clause genuinely about work experience.
_EXP_ANCHOR = re.compile(
    r"experien|berufserfahrung|erfahrung|praxis|esperienz|ervaring|"
    r"exp[ée]rien|experienc|experienci|"
    r"similar role|comparable role|equivalent role|relevant|professional|"
    r"requirements?|qualif\w*|must[- ]haves?|anforderungen|"
    r"voraussetzungen|requisiti|requisitos|vereisten|wer du bist|"
    r"what you|das bringen|nous recherchons|buscamos|"
    r"hands[-\s]?on|proven|track record|background in|working (?:in|with|as)|"
    r"seniority|vergleichbarer? (?:position|rolle)|einschl[äa]gig|"
    r"ruolo simile|ambito|settore|soortgelijke|vergelijkbare|"
    r"poste similaire|puesto similar|en el (?:sector|[áa]rea)",
    re.I)

_EXP_YEARS_IN = re.compile(r"(?:\d{1,2}|" + _EXP_WORD_ALT + r")\s*(?:\+|plus)?\s*(?:years?|yrs?|months?)\s+(?:in|of|as)\s+[A-Za-z]", re.I)

# Disqualifiers, checked in a tight window around the number so a sentence
# that merely also mentions a degree is not discarded wholesale.
_EXP_BLOCK_NEAR = re.compile(
    r"\b(?:old|of age|age of|ago|"
    r"last|past|next|recent|"
    r"founded|established|since|anniversar|"
    r"fixed[-\s]?term|befristet|tempo determinato|"
    r"contract|vertrag|contratto|duur|dur[ée]e|duration|"
    r"visa|permit|warrant|guarantee|garantie|"
    r"degree|bachelor|master|phd|doctora|studi|studium|laurea|"
    r"we(?:'|\s)?(?:ve|have)\s+(?:over|more than|than|nearly|almost|\d)|"
    r"our (?:company|team|group|history|story|brand)|"
    r"the company (?:has|was|is|been)|nous (?:avons|existons)|"
    r"abbiamo|la nostra azienda|im unternehmen|in the company|"
    r"in azienda|chez nous|ons bedrijf|wij bestaan|"
    r"universit|school|apprenticeship duration|"
    r"notice period|k[üu]ndigungsfrist|preavviso|"
    # FIX P16 (2026-10-04): PERKS AND TENURE ARE NOT EXPERIENCE.
    # LOOP (run 20261003T233023) wrote "Experience Required = 1 years" on 13
    # of its 17 rows. The JD says "EXPERIENCE  Experienced" -- no number
    # anywhere. The phantom came from the benefits copy:
    #   "4-day Workweek At LOOP you can choose between a 5-day or a 4-day
    #    workweek AFTER ONE YEAR WITH US"
    # a <=100-char fragment, so the weak tier scanned it, and nothing in the
    # +/-45 window disqualified it. Perk, loyalty and probation wording now
    # does. "with us" / "bei uns" / "da noi" is the giveaway: it describes
    # time SPENT AT THE EMPLOYER, never experience brought to the job.
    r"with us\b|bei uns|da noi|chez nous|bij ons|con nosotros|"
    r"workweek|work week|working week|arbeitswoche|settimana lavorativa|"
    r"holiday|vacation|urlaub|ferien|vakantie|vacanze|cong[ée]s|"
    r"sabbatical|probation|probezeit|periodo di prova|proeftijd|"
    r"bonus|pension|insurance|versicherung|assicurazione|"
    r"anniversary|jubil[äa]um|loyalty|tenure|betriebszugeh[öo]rigkeit|"
    r"once a year|per year|every year|annually|j[äa]hrlich|ogni anno|"
    r"pay ri[sz]e|salary review|gehaltserh[öo]hung)\b", re.I)

_EXP_NONE = re.compile(
    r"(no (?:prior |previous |work |professional )?experience (?:is )?"
    r"(?:required|necessary|needed)|"
    r"without (?:prior |previous )?experience|"
    r"keine (?:berufserfahrung|vorkenntnisse)|ohne (?:vor)?erfahrung|"
    r"nessuna esperienza (?:richiesta|necessaria)|senza esperienza|"
    r"geen ervaring (?:vereist|nodig)|"
    r"aucune exp[ée]rience (?:requise|n[ée]cessaire)|"
    r"sin experiencia (?:previa)?|no se requiere experiencia|"
    r"entry[-\s]?level|no experience)", re.I)

_EXP_LEVEL_PATTERNS = [
    ("Internship", re.compile(
        r"\b(intern(?:ship)?|internship|praktikum|praktikant|werkstudent|"
        r"working student|stage(?:air)?|stagiaire|stagista|tirocini|"
        r"becari|pr[áa]cticas|alternance|apprentice|apprendist|"
        r"ausbildung|azubi|lehrling|summer analyst)\b", re.I)),
    ("Executive", re.compile(
        r"\b(chief|c[etofi]o\b|cxo|vp\b|vice[-\s]president|svp|evp|"
        r"managing director|general manager|gesch[äa]ftsf[üu]hrer|"
        r"head of|leiter(?:in)?\b|direttore|directeur|director\b|"
        # "Partner" alone matched "Partner Solution Architect", "Partner
        # Manager" and "HR Business Partner" — none are executives.
        r"(?:managing|equity|founding|general)\s+partner\b|"
        r"amministratore)\b", re.I)),
    ("Lead", re.compile(
        r"\b(lead\b|leader\b|principal\b|"
        r"staff(?:\s+\w+){0,2}\s+(?:engineer|scientist|designer|developer|"
        r"researcher|analyst)|architect\b|team ?lead|tech ?lead|"
        r"capo(?:squadra)?|responsabile|teamleiter)\b", re.I)),
    ("Senior", re.compile(
        r"\b(senior|sr\.?\b|snr\b|experienced|expert(?:e)?\b|"
        r"esperto|senior[-\s]?level|erfahrene[rn]?)\b", re.I)),
    ("Mid", re.compile(
        r"\b(mid[-\s]?(?:level|weight)|intermediate|regular\b|"
        r"medior|confirm[ée]\b)\b", re.I)),
    ("Junior", re.compile(
        r"\b(junior|jr\.?\b|graduate|grad\b|entry[-\s]?level|entry\b|"
        r"einsteiger|berufseinsteiger|absolvent|neolaureat|"
        r"d[ée]butant|reci[ée]n titulad|starter|trainee|"
        r"associate\b|assistant\b)\b", re.I)),
]

# "Senior Care Assistant" is not a senior role; a role that TALKS TO senior
# people is not itself senior.
_EXP_LEVEL_FALSE = re.compile(
    r"\b(senior (?:care|living|citizen|home|school|resident|manager of care)|"
    r"junior (?:school|college|suite)|"
    r"director of (?:nursing|care)|seniorenheim|seniorenbetreuung|"
    r"(?:with|to|for|among|across|manage|managing|engage|engaging|"
    r"influence|influencing|present(?:ing)? to|report(?:ing)? to|"
    r"partner(?:ing)? with|work(?:ing)? with|liais(?:e|ing) with)\s+"
    r"(?:our\s+|the\s+|various\s+|multiple\s+|key\s+|"
    r"internal\s+|external\s+|c-level\s+)*"
    r"senior\s+(?:stakeholder|leader|management|leadership|executive|"
    r"colleague|team|member|client|partner|sponsor|manager)s?|"
    r"senior\s+(?:stakeholder|leadership|management)\b)", re.I)

# A seniority word in body text only counts when the sentence is about the
# person being hired. Without this, "you will lead a team for the next 2
# years" scored the row as a Lead role.
_EXP_HIRING_CUE = re.compile(
    r"(we are (?:looking|seeking|hiring)|are you an?|you are an?|"
    r"looking for an?|seeking an?|hiring an?|join us as|as an?\s|"
    r"the role|this role|position of|role of|vacancy|opening for|"
    r"wir suchen|du bist|sie sind|als\s|stelle als|"
    r"cerchiamo|sei un|ricerchiamo|posizione di|"
    r"wij zoeken|je bent|functie van|"
    r"nous recherchons|vous [êe]tes|poste de|"
    r"buscamos|eres un|puesto de)", re.I)

_EXP_SENT_SPLIT = re.compile(r"(?<=[.!?;:])\s+|[\n\r]+|\s*[•·▪▸–—]\s+")

# --- Requirements-section awareness (bare requirement fragments) ----------
# _EXP_SENT_SPLIT breaks at ':', bullets and newlines, so "Requirements: 3-5
# years" can arrive as the bare fragment "3-5 years" with the anchor word
# stranded in the header. These headers re-open the gate: a fragment under a
# requirements/qualifications heading, or any short fragment (a list bullet,
# <=100 chars), may be scanned even without an anchor. Disqualifiers and all
# numeric rules still apply; a strong (anchored) hit always wins.
_EXP_REQ_HEADER = re.compile(
    r"^(?:experience|requirements?|qualifications?|must[- ]haves?|"
    r"what you(?:'?ll)? (?:bring|need|have)|what we(?:'?re| are) looking for|"
    r"about you|your (?:profile|background|skills|experience)|"
    r"profil(?:e)?|we expect|ideal candidate|key (?:skills|qualifications)|"
    r"minimum (?:qualifications?|requirements?)|preferred qualifications?|"
    r"skills required|about the role|role requirements|who you are|"
    r"anforderungen|voraussetzungen|wer du bist|das erwarten wir|"
    r"das bringen sie mit|berufserfahrung|erfahrung|"
    r"requisiti|profilo|chi cerchiamo|esperienza|"
    r"ervaring|werkervaring|eisen|vereisten|profiel|"
    r"nous recherchons|exp[\u00e9e]rience|qu\u00e9 buscas|buscamos|"
    r"perfil)$",
    re.I)

# Section headings that end requirements scope - after one of these a relaxed
# fragment no longer counts, so marketing numbers ("About us: 25 years of
# experience") stay unclaimed by the weak tier.
_EXP_SECTION_END = re.compile(
    r"^(?:about (?:us|the company)|our (?:story|history|mission|values|"
    r"culture|offer)|what we (?:offer|provide|value)|we (?:offer|provide)|"
    r"benefits?|perks?|how to apply|apply (?:now|by)|next steps|"
    r"hiring process|equal opportunit|diversity|privacy|company culture|"
    r"chi siamo|cosa ti offriamo|la nostra (?:missione|azienda)|"
    r"wir bieten|[\u00fcu]ber uns|unser angebot|ons aanbod|wij bieden|"
    r"ce que nous offrons|notre entreprise|qu\u00e9 ofrecemos|nuestra empresa|"
    r"bewerbung|candidatura)$",
    re.I)

# Strict experience NOUN - narrower than _EXP_ANCHOR (no 'relevant' /
# 'professional' / 'working in'): the mention fallback that yields
# "Mentioned" instead of NA may only fire when the JD actually names
# experience in some language.
_EXP_MENTION_NOUN = re.compile(
    r"experien|berufserfahrung|erfahrung|vorkenntnis|"
    r"esperienz|exp\u00e9rience|ervaring|experiencia|experi[e\u00ea]nc", re.I)

_EXP_SOURCE_RANK = {"": 0, "none": 0, "title_inference": 1,
                    "card_context": 2, "api_description": 3, "detail_text": 3,
                    "api_field": 4}

# ATS-published seniority vocabulary -> canonical level. Employer-set, so it
# outranks anything inferred from a title or from prose.
_EXP_ATS_LEVEL = {
    "internship": "Internship", "intern": "Internship", "student": "Internship",
    "entry level": "Junior", "entry_level": "Junior", "entry": "Junior",
    "graduate": "Junior", "junior": "Junior", "associate": "Junior",
    "mid level": "Mid", "mid-level": "Mid", "intermediate": "Mid",
    "experienced": "Mid", "professional": "Mid",
    "mid-senior level": "Senior", "mid_senior_level": "Senior",
    "senior level": "Senior", "senior": "Senior", "expert": "Senior",
    "lead": "Lead", "principal": "Lead", "staff": "Lead", "manager": "Lead",
    "director": "Executive", "executive": "Executive", "vp": "Executive",
    "c-level": "Executive", "chief": "Executive",
}


def _jd_plain(raw, limit=20000):
    """HTML/markup -> plain text for experience extraction.

    Several ATS APIs already return the FULL job description in the same
    response used for titles (Ashby descriptionPlain, Greenhouse content,
    Workable description, Lever/Recruitee description). Feeding that straight
    in costs ZERO extra HTTP requests.

    Greenhouse returns HTML-ESCAPED markup ("&lt;p&gt;"); stripping tags before
    unescaping would leave every tag in the text, so unescape comes FIRST.
    """
    if not raw:
        return ""
    txt = str(raw)
    if "&lt;" in txt or "&gt;" in txt or "&amp;" in txt:
        txt = unescape(txt)
    txt = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", txt)
    txt = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>|</h[1-6]>", "\n", txt)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = unescape(txt)
    txt = re.sub(r"[ \t\xa0]+", " ", txt)
    txt = re.sub(r"\n\s*\n+", "\n", txt)
    return txt.strip()[:limit]


def _exp_num(tok):
    tok = (tok or "").strip().lower().rstrip(".")
    if tok.isdigit():
        return int(tok)
    return _EXP_WORD_NUM.get(tok)


def _exp_level_from_years(lo, hi):
    # For a stated range use the midpoint: "5-8 years" is a Senior ask, but
    # anchoring on the lower bound alone would call it Mid.
    if lo is not None and hi is not None:
        v = (lo + hi) / 2.0
    else:
        v = lo if lo is not None else hi
    if v is None:
        return ""
    if v <= 0:
        return "Entry"
    if v <= 2:
        return "Junior"
    if v < 5:
        return "Mid"
    if v < 10:
        return "Senior"
    return "Lead"


def _exp_level_from_text(text):
    if not text:
        return ""
    if _EXP_LEVEL_FALSE.search(text):
        return ""
    for level, rx in _EXP_LEVEL_PATTERNS:
        if rx.search(text):
            return level
    return ""


def _exp_scan_numbers(text):
    """Return the strongest explicit experience statement, or None.

    Two tiers, because _EXP_SENT_SPLIT breaks at ':', bullets and newlines
    and can strand a bare fragment ("Requirements: 3-5 years" arrives as just
    "3-5 years") away from the anchor words in its header:

    * STRONG - the fragment itself names experience (anchor / years-in).
      Anchored hits are unambiguous: they always win, lowest minimum first
      (the historic behaviour).
    * WEAK - the fragment sits under a requirements/qualifications heading
      (_EXP_REQ_HEADER, state persists until _EXP_SECTION_END) or is a short
      list fragment (<=100 chars). Used only when no strong hit exists.

    Every candidate, strong or weak, must clear the +/-45 char disqualifier
    window, so ages, contract durations and company-tenure marketing never
    become experience requirements.
    """
    strong_best = None
    weak_best = None
    in_req = False
    for sent in _EXP_SENT_SPLIT.split(text or ""):
        sent = (sent or "").strip()
        if not sent:
            continue
        probe = sent.strip(" *#>_:-")
        if _EXP_REQ_HEADER.match(probe):
            in_req = True
            continue
        if _EXP_SECTION_END.match(probe):
            in_req = False
            continue
        if len(sent) > 600:
            continue
        strong = bool(_EXP_ANCHOR.search(sent) or _EXP_YEARS_IN.search(sent))
        if not strong and not (in_req or len(sent) <= 100):
            continue
        num = rf"(?:\d{{1,2}}|{_EXP_WORD_ALT})"
        unit = rf"(?:{_EXP_YEAR_UNIT}|{_EXP_MONTH_UNIT})"
        # FIX P0-46: "Four or more years" / "4 or more years" produced
        # required="Mentioned", level=Unknown. "or" was only listed as a RANGE
        # connector, so the parser demanded a second NUMBER after it and
        # "more" is not one -- the whole match then failed. Every sibling
        # phrasing already worked ("at least four years", "minimum four
        # years", "Four+ years", "Four years"), which is why this stayed
        # hidden. It is NOT a words-vs-digits problem: the digit form failed
        # identically. Also accepts the restated-numeral form American
        # Express uses, "Four (4) or more years".
        rx = re.compile(
            rf"(?<![\w.,/-])({num})\s*(?:\+|plus)?\s*"
            rf"(?:\(\s*\d{{1,2}}\s*\)\s*)?"
            rf"(?P<ormore>\b(?:or|and|o|oder|of|ou|y)\s+"
            rf"(?:more|above|greater|over|higher|plus|pi[uù]|mehr|meer|"
            rf"m[aá]s|superiore?)\b\s*)?"
            rf"(?:(?:-|–|—|\bto\b|\bbis\b|\ba\b|\btot\b|\b[àa]\b|\bund\b|"
            rf"\be\b|\by\b|\bet\b|\bor\b|\bof\b)\s*"
            rf"({num})\s*(?:\+|plus)?\s*)?"
            rf"({unit})\b", re.I)
        for m in rx.finditer(sent):
            _g = m.groups()
            lo, hi = _exp_num(m.group(1)), _exp_num(_g[2])
            u = _g[3].lower()
            if lo is None:
                continue
            # "a month-end close process" / "year-end reporting": the unit is
            # part of a compound noun, not a duration.
            if re.match(r"\s*[-\u2010-\u2015](?:end|on-end|round)", sent[m.end():]):
                continue
            # A bare article is only a real quantity when something
            # quantifies it, else "a month-end" produces a phantom 1.
            if m.group(1).strip().lower() in {"a", "an"} and not re.search(
                    r"\b(?:at least|minimum|min\.|over|more than|within|after|"
                    r"first|least)\b", sent[:m.start()], re.I):
                continue
            win = sent[max(0, m.start() - 45):m.end() + 45]
            if _EXP_BLOCK_NEAR.search(win):
                continue
            months = bool(re.fullmatch(_EXP_MONTH_UNIT, u, re.I))
            # Word boundaries are mandatory: an unanchored "[üu]ber" matched
            # inside "K-uber-netes" and turned "6 months" into "6+ months".
            plus = bool(m.group("ormore")) or bool(re.search(
                r"\+|\b(?:plus|at least|minimum|min\.|mindestens|almeno|"
                r"minimaal|au moins|al menos|over|more than|[üu]ber|oltre|"
                r"upwards of|no less than|m[ií]nimo|minimo de|mindest|"
                r"ten minste|minstens)\b", sent, re.I))
            if hi is not None and hi < lo:
                hi = None
            lo_y = lo / 12.0 if months else float(lo)
            hi_y = (hi / 12.0 if months else float(hi)) if hi is not None else None
            if lo_y > 40:
                continue
            cand = (lo_y, hi_y, months, plus, lo, hi, sent[:300])
            # Prefer the lowest stated minimum: "3-5 years" beats a stray "10".
            # Anchored (strong) candidates are kept apart from relaxed (weak)
            # ones so a stray bullet can never outrank an explicit statement.
            if strong:
                if strong_best is None or lo_y < strong_best[0]:
                    strong_best = cand
            elif weak_best is None or lo_y < weak_best[0]:
                weak_best = cand
    return strong_best or weak_best


def extract_experience(text="", title=""):
    """Detect required experience from JD text and/or job title.

    Returns a dict: required / min_years / max_years / level / evidence / source.
    Numeric years appear ONLY when explicitly stated in the text.
    """
    out = {"required": "Unknown", "min_years": "", "max_years": "",
           "level": "Unknown", "evidence": "", "source": "none"}
    text = text or ""
    title = title or ""

    hit = _exp_scan_numbers(text)
    if hit:
        lo_y, hi_y, months, plus, lo_raw, hi_raw, ev = hit
        unit = "months" if months else "years"
        if hi_raw is not None:
            label = f"{lo_raw}-{hi_raw} {unit}"
        elif plus:
            label = f"{lo_raw}+ {unit}"
        else:
            label = f"{lo_raw} {unit}"
        out["required"] = label
        out["min_years"] = round(lo_y, 2) if months else int(lo_y)
        if hi_y is not None:
            out["max_years"] = round(hi_y, 2) if months else int(hi_y)
        out["level"] = (_exp_level_from_text(title)
                        or _exp_level_from_years(lo_y, hi_y))
        out["evidence"] = ev
        out["source"] = "detail_text"
        return out

    none_hit = _EXP_NONE.search(text) or _EXP_NONE.search(title)
    if none_hit:
        out.update({"required": "None required", "min_years": 0,
                    "level": _exp_level_from_text(title) or "Entry",
                    "evidence": none_hit.group(0)[:300],
                    "source": ("detail_text" if _EXP_NONE.search(text)
                               else "title_inference")})
        return out

    lvl = _exp_level_from_text(title)
    if lvl:
        out.update({"level": lvl, "source": "title_inference",
                    "evidence": title[:300]})
        return out

    for sent in _EXP_SENT_SPLIT.split(text):
        sent = (sent or "").strip()
        if not sent or len(sent) > 400:
            continue
        if not _EXP_HIRING_CUE.search(sent):
            continue
        lvl = _exp_level_from_text(sent)
        if lvl:
            out.update({"level": lvl, "source": "detail_text",
                        "evidence": sent[:300]})
            return out

    # The JD names experience somewhere but yielded neither a number nor a
    # seniority word: that is a reference, not an absence - report it as
    # "Mentioned" so the UI can reserve NA for descriptions that never name
    # experience at all. Disqualified sentences (company tenure, ages, ...)
    # are not references.
    for sent in _EXP_SENT_SPLIT.split(text):
        sent = (sent or "").strip()
        if not sent or len(sent) > 400:
            continue
        m = _EXP_MENTION_NOUN.search(sent)
        if not m:
            continue
        win = sent[max(0, m.start() - 60):m.end() + 60]
        if _EXP_BLOCK_NEAR.search(win):
            continue
        out.update({"required": "Mentioned", "evidence": sent[:300],
                    "source": "detail_text"})
        return out
    return out


def apply_experience_to_record(rec, jd_text="", card_context="", title="",
                               level_hint="", key_prefix="Experience "):
    """Fill the four Experience columns on a record, respecting source rank.

    Never downgrades: a weaker source cannot overwrite a stronger one.
    `level_hint` is an employer-published seniority string (e.g.
    SmartRecruiters experienceLevel) and outranks every inference.
    """
    jd = (jd_text or "").strip()
    if "&lt;" in jd or ("<" in jd and ">" in jd):
        # Raw HTML slipped through (some adapters pass markup verbatim);
        # extraction must always see plain text.
        jd = _jd_plain(jd)
    exp = extract_experience(jd or card_context or "", title or "")
    src = exp["source"]
    if src == "detail_text":
        src = "api_description" if jd else "card_context"

    hint = (level_hint or "").strip()
    if hint:
        mapped = _EXP_ATS_LEVEL.get(hint.lower())
        if mapped:
            exp["level"] = mapped
            if exp["required"] in ("Unknown", "Mentioned"):
                src = "api_field"

    cur = rec.get(key_prefix + "Source") or "none"
    if _EXP_SOURCE_RANK.get(src, 0) < _EXP_SOURCE_RANK.get(cur, 0):
        return False
    if exp["required"] == "Unknown" and exp["level"] == "Unknown":
        return False
    rec[key_prefix + "Required"] = exp["required"]
    rec[key_prefix + "Min Years"] = exp["min_years"]
    rec[key_prefix + "Level"] = exp["level"]
    rec[key_prefix + "Source"] = src
    return True

# ─────────────────────────────────────────────────────────────────────────────
# FIX P0-29: HOST-ADAPTIVE RESOURCE GOVERNOR (low-end laptop safety)
#
# Three measured problems this solves:
#
#   1. recommended_workers() was CALLED at two sites but never defined
#      anywhere in this file and never imported -> guaranteed NameError the
#      moment detail enrichment ran with max_workers unset.
#
#   2. Each DOM company launched its OWN Chromium inside its worker thread.
#      Measured on this box: 1 browser = 581 MB RSS, 2 = 1145 MB, 3 = 1706 MB.
#      On an 8 GB office laptop that is most of the free RAM.
#
#   3. No request interception at all: every scrape downloaded images, fonts,
#      video and analytics. Measured 5.59 MB -> 3.12 MB per 3 pages (-44%)
#      when images/media/fonts are blocked.
#
# NOTE ON SPEED: blocking images was measured at 11.2s -> 11.2s (+0%) wall
# clock. It is a MEMORY/BANDWIDTH fix, not a speed fix. Speed is handled
# separately (FIX P0-31); do not conflate the two.
# ─────────────────────────────────────────────────────────────────────────────

def _host_cpu_count():
    """Physical-ish CPU count that respects cgroup/affinity limits."""
    n = 0
    try:
        n = len(os.sched_getaffinity(0))
    except Exception:
        pass
    if not n:
        try:
            n = os.cpu_count() or 0
        except Exception:
            n = 0
    return max(1, n or 1)


def _host_free_mb():
    """Best-effort available RAM in MB. Returns None when undeterminable.

    Uses MemAvailable (what the kernel thinks is actually obtainable without
    swapping), not MemFree, and honours a cgroup v2 memory.max limit so a
    container with a small cap is not mistaken for a big host.
    """
    avail = None
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) / 1024.0
                    break
    except Exception:
        avail = None
    # Respect a cgroup v2 cap when it is lower than host-available.
    for cg in ("/sys/fs/cgroup/memory.max",
               "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(cg, encoding="utf-8") as fh:
                raw = fh.read().strip()
            if raw and raw != "max":
                cap = int(raw) / (1024.0 * 1024.0)
                if cap > 0 and (avail is None or cap < avail):
                    avail = cap
        except Exception:
            continue
    if avail is None:
        # FIX P0-35: os.sysconf is POSIX-only. On Windows -- this app's primary
        # platform -- it does not exist, so this fell through to None and
        # recommended_workers() silently sized the pool from CPU count alone
        # (6 HTTP workers instead of 3). career_scanner already defers to the
        # cross-platform reader; the ATS copy had not been updated, so the two
        # scanners ran at different concurrency on the same machine.
        try:
            from sponsorscout.scanning.common import host_workers_limits
            avail = host_workers_limits()[2] / (1024.0 * 1024.0)
        except Exception:
            avail = None
    return avail


# Measured peak RSS of one headless Chromium with a real job board loaded.
_BROWSER_RSS_MB = 600
# Leave this much for the OS, the user's other apps, and this process.
_RESERVE_MB = 1200


# FIX P0-36: this module used to define its OWN ``recommended_workers`` here.
# career_scanner already deleted its local copy and imports the shared one
# from ``sponsorscout.scanning.common``; this file did not, so the SAME
# machine was sized by two different formulas. Measured on a simulated
# 2-core / 8 GB Windows box: career chose 4 HTTP workers, this scanner chose
# 6 -- which is exactly the "results are inconsistent between the two
# scanners" symptom. There is now one definition for both scanners and the
# pipeline. The ``requested=`` cap the local copy offered is preserved in
# ``common.recommended_workers``, so no caller lost functionality.
from sponsorscout.scanning.common import recommended_workers as _common_recommended_workers


# REBASE 2026-10-03 (R3): identical knob to career_scanner.py so the two
# scanners still size themselves the same way. common.recommended_workers only
# ever clamps downward; SPONSORSCOUT_MAX_WORKERS / SPONSORSCOUT_HTTP_WORKERS is
# the one way to raise it, opt-in, hard-capped.
def recommended_workers(kind="browser", requested=None):
    n = _common_recommended_workers(kind, requested)
    raw = os.environ.get("SPONSORSCOUT_MAX_WORKERS" if kind == "browser"
                         else "SPONSORSCOUT_HTTP_WORKERS")
    if not raw:
        return n
    try:
        want = int(str(raw).strip())
    except (TypeError, ValueError):
        return n
    if want < 1:
        return n
    return min(want, 8 if kind == "browser" else 24)


def describe_host_budget():
    """One-line host summary for the run header (helps users self-diagnose)."""
    cpus = _host_cpu_count()
    free = _host_free_mb()
    free_s = f"{free:.0f} MB" if free is not None else "unknown"
    return (f"host: {cpus} cpu, {free_s} available RAM -> "
            f"browser workers={recommended_workers('browser')}, "
            f"http workers={recommended_workers('http')}")


# Resource types that never contain job data. Blocking them is safe for
# extraction correctness: no adapter reads pixels, fonts or video.
_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}

# Analytics/ads/chat hosts. These load slowly, spin the CPU and never carry
# postings. Matched as substrings against the request URL.
_BLOCKED_URL_MARKERS = (
    "google-analytics.com", "googletagmanager.com", "doubleclick.net",
    "facebook.net", "connect.facebook", "hotjar.com", "mixpanel.com",
    "segment.io", "segment.com/analytics", "fullstory.com", "clarity.ms",
    "intercom.io", "intercomcdn", "drift.com", "zdassets.com/ekr",
    "cdn.cookielaw.org", "onetrust.com", "cookiebot.com", "usercentrics",
    "newrelic.com", "nr-data.net", "sentry.io", "bugsnag.com",
    "youtube.com/embed", "player.vimeo.com", "adservice.google",
    "bat.bing.com", "snap.licdn.com", "analytics.tiktok",
)


def install_page_resource_blocking(target, block_types=None, block_hosts=True):
    """Attach request interception to a Playwright Page or BrowserContext.

    Measured effect: 5.59 MB -> 3.12 MB downloaded across 3 job boards (-44%)
    and a matching drop in decode/raster CPU. Wall-clock effect was ~0%, which
    is expected and fine — this exists to protect RAM/CPU/bandwidth.

    Fails open: if routing cannot be installed the scrape still runs.
    """
    if target is None:
        return False
    types = _BLOCKED_RESOURCE_TYPES if block_types is None else set(block_types)

    def _route(route, request=None):
        try:
            req = request if request is not None else route.request
            if req.resource_type in types:
                return route.abort()
            if block_hosts:
                u = (req.url or "").lower()
                for marker in _BLOCKED_URL_MARKERS:
                    if marker in u:
                        return route.abort()
            return route.continue_()
        except Exception:
            # Never let interception break a scrape.
            try:
                return route.continue_()
            except Exception:
                return None

    try:
        target.route("**/*", _route)
        return True
    except Exception:
        return False


# Chromium flags that cut memory and CPU without changing rendered DOM.
LOW_RESOURCE_BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-http2",
    "--ignore-certificate-errors",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=TranslateUI,BlinkGenPropertyTrees,MediaRouter",
    "--metrics-recording-only",
    "--mute-audio",
    "--no-first-run",
    "--no-default-browser-check",
    "--renderer-process-limit=2",
    "--js-flags=--max-old-space-size=512",
]

# ───────────────────────── CONFIG ─────────────────────────────────────────────

# FIX UI-2 parity (P9): one work-mode classifier, used by BOTH scanners and
# by the DB ingest. It was previously inline in the career writer only, so
# the ATS scanner shipped no "Work Mode" column at all and every ATS row had
# to be guessed at downstream from page furniture -- which is exactly how
# the dashboard's Remote card reached 589 on 111 remote jobs.
_WORK_MODE_STRONG_REMOTE = re.compile(
    r"\b(fully remote|100% remote|remote[- ]first|work from anywhere|"
    r"volledig op afstand|komplett remote)\b")
_WORK_MODE_HYBRID = re.compile(
    r"\b(hybrid|hybride|ibrido|smart working|smartworking)\b")
_WORK_MODE_REMOTE = re.compile(
    r"\b(remote|telelavoro|teletrabajo|t\u00e9l\u00e9travail|home office|"
    r"homeoffice|thuiswerk|da remoto|remoto)\b")
_WORK_MODE_ONSITE = re.compile(
    r"\b(on[- ]?site|onsite|in[- ]office|in office|presenza|vor ort|"
    r"op kantoor)\b")


def classify_work_mode(blob: str) -> str:
    """Remote / Hybrid / Remote-Hybrid / Onsite / Unknown from free text.

    "Unknown" is a real answer: a posting that never mentions where the work
    happens has not told us it is an office job, and downstream must not
    pretend otherwise.
    """
    low = str(blob or "").lower()
    strong = bool(_WORK_MODE_STRONG_REMOTE.search(low))
    hybrid = bool(_WORK_MODE_HYBRID.search(low))
    remote = strong or bool(_WORK_MODE_REMOTE.search(low))
    if remote and hybrid:
        return "Remote" if strong else "Remote/Hybrid"
    if hybrid:
        return "Hybrid"
    if remote:
        return "Remote"
    if _WORK_MODE_ONSITE.search(low):
        return "Onsite"
    return "Unknown"


OUTPUT_FIELDS = [
    "Company Name", "Seed Name", "Source Type", "Hiring Company",
    "Target Country", "Scope Policy", "Industry Type",
    "Sponsorship History Score", "English Friendly Score", "Remote Score",
    "Job Title", "Raw Job Title", "Job Location", "Raw Location", "Job Type",
    "Work Mode",
    "Job URL", "Canonical Job ID", "Provider", "Extraction Method",
    "EU Blue Card", "Blue Card Evidence", "Relocation/Visa Support",
    # FIX P19b (2026-10-04): schema parity. The career CSV carried 42
    # columns and this one 41 -- "Scope Confidence" was the missing one, in
    # the same slot the career writer uses (after Relocation/Visa Support).
    # Surfaced the moment both scanners produced the same DocuSign rows.
    "Scope Confidence",
    # FIX P0-30
    "Experience Required", "Experience Min Years", "Experience Level",
    "Experience Source",
    "Location Source", "Location Confidence",
    "URL Type", "Visa Sponsorship", "Relocation Support",
    "Relocation Required", "Support Confidence", "Support Evidence",
    "Support Evidence URL", "Support Evidence Type", "Record Status",
    "Quarantine Reason", "Run ID", "Scanned At",
]

LOG_FIELDS = [
    "Run ID", "Seed Name", "Company", "Source Type", "Target Country", "Status",
    "Provider", "Jobs Found", "Quarantined", "Duplicates", "Rejected Scope",
    "Error", "Diagnostics", "Duration Sec", "Seed URL",
]

ERROR_FIELDS = [
    "Run ID", "Timestamp", "Seed Name", "Phase", "Error Type", "Message", "Seed URL",
]

BAD_HOSTS = {
    "bcorporation.net", "glassdoor.com", "indeed.com", "linkedin.com",
    "youtube.com", "google.com", "cookie-script.com", "onetrust.com",
    "sharepoint.com", "my.greenhouse.io",
}

BAD_TITLES = {
    "create alert", "skip to main content", "open positions", "working at",
    "here", "report", "b corporation",
}


# Batch L (universal FP fix): role nouns that prove a title is a real job.
# Policy/banner phrases ("data protection", "equal opportunity", ...) also
# occur in genuine titles ("Data Protection Officer"), so those phrases only
# reject when NO role noun is present. Banners never contain one.
_ROLE_NOUNS_RE = re.compile(
    r"\b(manager|officer|engineer|specialist|analyst|lead|leader|head|chief|"
    r"director|consultant|counsel|advisor|adviser|architect|developer|"
    r"designer|scientist|associate|assistant|coordinator|administrator|"
    r"supervisor|strategist|partner|auditor|lawyer|attorney|solicitor|"
    r"paralegal|clerk|technician|technologist|operator|mechanic|electrician|"
    r"nurse|physician|doctor|surgeon|teacher|professor|lecturer|researcher|"
    r"writer|editor|accountant|recruiter|buyer|planner|driver|chef|"
    r"receptionist|secretary|intern|trainee|apprentice|agent|broker|trader|"
    r"banker|representative|executive|president|founder|owner)s?\b",
    re.IGNORECASE,
)


def _has_role_noun(title: str) -> bool:
    """True when the title names a job-holder role (real job signal)."""
    return bool(_ROLE_NOUNS_RE.search(title or ""))

# Network resilience (v5)
PREFLIGHT_PROBE_HOSTS = (
    "www.google.com", "boards-api.greenhouse.io", "api.ashbyhq.com",
    "api.lever.co", "api.smartrecruiters.com",
)
PREFLIGHT_PORT = 443
PREFLIGHT_TIMEOUT_SEC = 5
PREFLIGHT_MAX_FAILURES = 2
HTTP_RETRIES = 3
HTTP_BACKOFF_BASE_SEC = 1.5
HTTP_TIMEOUT_SEC = 35


# ─────────────────────────────────────────────────────────────────────────────
# SEED UPGRADE TABLE — fixes for the v4 seed. Keyed by company `name`.
# Applied in-memory when reading a v6-format seed (name, ats_type, careers_url,
# industry, ...). Corrections:
#   • innogames  → EU Lever API (the US api.lever.co 404s)
#   • avomind    → recruiter + Workable public API
#   • ecosia     → flag: Ashby board currently returns 0 jobs
#   • dbtlabsinc → flag: Greenhouse board taken private (404)
#   • crealytics / moss → flag: Personio board currently empty
# ─────────────────────────────────────────────────────────────────────────────
SEED_UPGRADE = {
    "InnoGames": {"lever_region": "eu"},
    "Avomind": {"source_type": "recruiter"},
}

# ATS types whose public list API is known to currently return 0/404 — surfaced
# in the scan log diagnostics rather than silently reported as healthy.
KNOWN_BOARD_ISSUES = {
    "Ecosia": "Ashby board 'ecosia.org' currently returns 0 jobs (genuine hiring freeze; board verified live 2026-09-11)",
    "Dbt Labs": "Greenhouse board 'dbtlabsinc' returns 404 (board taken private)",
    "Crealytics": "Personio board currently returns 0 positions",
    "Moss": "Personio board currently returns 0 positions",
}

# ───────────────────────── SHARED HELPERS ────────────────────────────────────
def clean(value):
    value = unescape(str(value or ""))
    value = value.replace("ï»¿", "")
    value = value.replace("\ufeff", "")
    match = re.fullmatch(
        r"\[[^\]]*\]\((https?://[^)]+)\)",
        value.strip(),
    )
    if match:
        value = match.group(1)
    if any(x in value for x in ("Ã", "Â", "â", "ð", "\ufffd")):
        try:
            value = value.encode("latin1").decode("utf-8")
        except (UnicodeError, UnicodeEncodeError):
            pass
    return re.sub(r"\s+", " ", value).strip()


def host_of(url):
    return urlparse(url).netloc.lower().split(":")[0]


# ───────────────────────── JD SUPPORT DETECTOR ────────────────────────────────
# (imported verbatim from career_portal_scanner_v7.py — keep in sync)
# ───────────────────── JD SUPPORT DETECTOR ─────────────────────
# Context-aware detection of Visa Sponsorship / Relocation Support in JD text.
# Never matches keywords alone: every mention is judged within its sentence/
# clause, with negation / requirement / conditional / scope qualifiers.
#   "We do NOT support relocation"              -> No      (negated)
#   "We support if you are READY to relocate"   -> No      (candidate must move)
#   "may be provided case-by-case"              -> Unknown (conditional)
# MANDATORY (rebase 2026-10-03, user decision): single source of truth.
# _org carried a 267-line inline copy of the classifier here, with its own
# VERDICT_* constants and NO detect_blue_card at all -- so the moment
# classify_support was brought up to the current version it raised
# NameError: detect_blue_card on the first row of every ATS board.
# Imported, not duplicated, exactly as in career_scanner.py.
from sponsorscout.scanning.jd_support import (
    JDSupportDetector,
    VERDICT_NO,
    VERDICT_UNKNOWN,
    VERDICT_YES,
    detect_blue_card,
)


# FIX P0-34b (parity with career_scanner): ISO-3166-1 alpha-3 codes, used to
# recognise an address tail such as "Jesi, AN, ITA" that is NOT a job title.
_ISO3_COUNTRY_CODES = {
    "abw", "afg", "ago", "alb", "and", "are", "arg", "arm", "aus", "aut",
    "aze", "bel", "ben", "bfa", "bgd", "bgr", "bhr", "bih", "blr", "bol",
    "bra", "brb", "brn", "bwa", "can", "che", "chl", "chn", "civ", "cmr",
    "col", "cri", "cub", "cyp", "cze", "deu", "dnk", "dom", "dza", "ecu",
    "egy", "esp", "est", "eth", "fin", "fra", "gbr", "geo", "gha", "grc",
    "gtm", "hkg", "hnd", "hrv", "hun", "idn", "ind", "irl", "irn", "irq",
    "isl", "isr", "ita", "jam", "jor", "jpn", "kaz", "ken", "khm", "kor",
    "kwt", "lao", "lbn", "lka", "ltu", "lux", "lva", "mar", "mco", "mda",
    "mex", "mkd", "mlt", "mmr", "mne", "mng", "moz", "mys", "nga", "nic",
    "nld", "nor", "npl", "nzl", "omn", "pak", "pan", "per", "phl", "pol",
    "prt", "pry", "qat", "rou", "rus", "rwa", "sau", "sgp", "slv", "srb",
    "svk", "svn", "swe", "syr", "tha", "tun", "tur", "twn", "tza", "uga",
    "ukr", "ury", "usa", "uzb", "ven", "vnm", "zaf", "zmb", "zwe",
}


# FIX P0-47 (S-07): ISO3 -> ISO2, so a three-letter country suffix can be
# resolved to a country name.  _ISO3_COUNTRY_CODES already existed but was
# used ONLY by the title validator, so "Vercelli, ITA" was recognised as a
# pure-location title and then thrown away by the location parser, which knew
# nothing about three-letter codes: _location_from_line("Milan, ITA") -> None
# while _location_from_line("Milan, IT") -> "Milan, Italy".  Workday-backed
# boards (American Express) emit the ISO3 form, so entire countries of rows
# landed on "Unknown".
_ISO3_TO_ISO2 = {
    "abw": "aw", "afg": "af", "ago": "ao", "alb": "al", "and": "ad",
    "are": "ae", "arg": "ar", "arm": "am", "aus": "au", "aut": "at",
    "aze": "az", "bel": "be", "ben": "bj", "bfa": "bf", "bgd": "bd",
    "bgr": "bg", "bhr": "bh", "bih": "ba", "blr": "by", "bol": "bo",
    "bra": "br", "brb": "bb", "brn": "bn", "bwa": "bw", "can": "ca",
    "che": "ch", "chl": "cl", "chn": "cn", "civ": "ci", "cmr": "cm",
    "col": "co", "cri": "cr", "cub": "cu", "cyp": "cy", "cze": "cz",
    "deu": "de", "dnk": "dk", "dom": "do", "dza": "dz", "ecu": "ec",
    "egy": "eg", "esp": "es", "est": "ee", "eth": "et", "fin": "fi",
    "fra": "fr", "gbr": "gb", "geo": "ge", "gha": "gh", "grc": "gr",
    "gtm": "gt", "hkg": "hk", "hnd": "hn", "hrv": "hr", "hun": "hu",
    "idn": "id", "ind": "in", "irl": "ie", "irn": "ir", "irq": "iq",
    "isl": "is", "isr": "il", "ita": "it", "jam": "jm", "jor": "jo",
    "jpn": "jp", "kaz": "kz", "ken": "ke", "khm": "kh", "kor": "kr",
    "kwt": "kw", "lao": "la", "lbn": "lb", "lka": "lk", "ltu": "lt",
    "lux": "lu", "lva": "lv", "mar": "ma", "mco": "mc", "mda": "md",
    "mex": "mx", "mkd": "mk", "mlt": "mt", "mmr": "mm", "mne": "me",
    "mng": "mn", "moz": "mz", "mys": "my", "nga": "ng", "nic": "ni",
    "nld": "nl", "nor": "no", "npl": "np", "nzl": "nz", "omn": "om",
    "pak": "pk", "pan": "pa", "per": "pe", "phl": "ph", "pol": "pl",
    "prt": "pt", "pry": "py", "qat": "qa", "rou": "ro", "rus": "ru",
    "rwa": "rw", "sau": "sa", "sgp": "sg", "slv": "sv", "srb": "rs",
    "svk": "sk", "svn": "si", "swe": "se", "syr": "sy", "tha": "th",
    "tun": "tn", "tur": "tr", "twn": "tw", "tza": "tz", "uga": "ug",
    "ukr": "ua", "ury": "uy", "usa": "us", "uzb": "uz", "ven": "ve",
    "vnm": "vn", "zaf": "za", "zmb": "zm", "zwe": "zw",
}


# ── FIX P0-50 (S-01/S-02): shared post-detect false-positive guards ─────────
# The ATS scanner ran five guards after detector.detect(); the career scanner
# ran NONE, so `grep -c VERDICT_UNKNOWN` was 8 in ats_scanner.py and 0 in
# career_scanner.py and both career call sites took sup["visa"]["verdict"]
# raw. amazon.jobs is crawled by the career engine, so an Amazon Italia JD
# whose only occurrence of the word was "mentoring people, sponsoring
# projects, and proposing technical solutions" was published as Sponsor = Y.
# The guards now live in one function with IDENTICAL BYTES in both scanners
# so the two engines cannot drift apart again.
_SUPPORT_VISA_KEYWORD_RE = re.compile(
    r"visa|work permit|work authori[sz]ation|immigration|h-?1b|"
    r"blue card|carta blu|blaue karte|blauwe kaart|carte bleue|"
    r"tarjeta azul|skilled (migrant|worker)|aufenthaltstitel|"
    r"arbeitserlaubnis|permesso di soggiorno|permis de travail|"
    r"werkvergunning|arbeidsvergunning|permiso de trabajo"
    # FIX P0-53: "we are willing to sponsor the right candidate" carries no
    # visa noun at all, so guard (1) threw away a plainly genuine offer. When
    # the OBJECT of "sponsor" is a PERSON rather than a project, an event or
    # a team, immigration sponsorship is the only thing it can mean in a job
    # ad -- and it still cannot match the Amazon false positive this guard
    # exists for ("sponsoring projects").
    r"|sponsor\w*\s+(?:the\s+)?(?:right\s+|suitable\s+|successful\s+|"
    r"eligible\s+|qualified\s+|international\s+|overseas\s+|foreign\s+)?"
    r"(?:candidate|applicant|employee|hire|new\s+joiner|individual|person|"
    r"professional|talent|worker|you)s?\b", re.I)

_SUPPORT_EVENT_RE = re.compile(
    r"sponsor\w*.{0,50}\b(event|conference|trade[- ]show|booth|"
    r"session|co[- ]market|partner|speaker)\b", re.I)
_SUPPORT_EVENT_RE2 = re.compile(
    r"\b(event|conference|trade[- ]show|booth|session|co[- ]market|"
    r"partner)\w*.{0,50}sponsor\w*", re.I)

_SUPPORT_TRAVEL_RE = re.compile(
    r"\b(visas?|work permits?)\s+for\s+(international\s+events?|"
    r"speakers?|travel|attendees?)", re.I)

# FIX P0-50 (S-02): the same thing written as a travel-coordination duty
# list. Appodeal's "Executive & Personal Assistant to CEO" says
# "International travel coordination - flights, hotels, visas, ground
# transport."  Guard (3) only matched "visas FOR travel", so the comma-list
# form was uncovered. Arranging someone's travel documents is not an offer to
# sponsor the candidate.
_SUPPORT_TRAVEL_LIST_RE = re.compile(
    # FIX P24 (2026-10-04): bare "transfers?" made this guard fire on
    # "Visa sponsorship TRANSFER support for eligible Highly Skilled Migrants
    #  already based in the Netherlands" (Michael Page NL). The detector
    # returned Yes 0.90 and this guard rewrote it to Unknown -- yet a visa
    # TRANSFER is an immigration service offered to the candidate, the exact
    # opposite of booking someone's airport transfer. The word now only
    # counts in its travel sense.
    r"\b(?:flights?|hotels?|accommodation|lodging|itinerar\w*|ground\s+"
    r"transport\w*|car\s+rental|per\s+diem|expense\s+reports?|"
    r"(?:airport|ground|hotel|airline|shuttle)\s+transfers?)"
    r"\b[^.;!?]{0,60}\bvisas?\b"
    r"|\bvisas?\b[^.;!?]{0,60}\b(?:flights?|hotels?|accommodation|lodging|"
    r"itinerar\w*|ground\s+transport\w*|car\s+rental|per\s+diem|"
    r"(?:airport|ground|hotel|airline|shuttle)\s+transfers?|"
    r"expense\s+reports?)\b", re.I)

_SUPPORT_FUNCTION_TITLE_RE = re.compile(
    r"\b(?:global|international)\s+mobility\b|\bimmigration\b|\brelocation\b", re.I)
_SUPPORT_FUNCTION_ROLE_RE = re.compile(
    r"\b(manager|specialist|coordinator|officer|lead|director|program|"
    r"administrator|consultant|partner|hr)\b", re.I)

_SUPPORT_DUTY_RE = re.compile(
    r"\b(track(?:ing)?|manag(?:e|ing)|oversee(?:ing)?|administer(?:ing)?|"
    r"process(?:ing)?|handle(?:ing)?|coordinat(?:e|ing))\s+(?:of\s+)?"
    r"(work\s+permits?|visas?|immigration\s+cases?)\b", re.I)

# A candidate-FACING offer vetoes the duty/travel downgrades: "we will cover
# your visa" is support even inside a paragraph about travel admin. A bare
# "we offer" or "benefits include" elsewhere in the JD is NOT enough.
# FIX P0-52: the first version of this veto only understood "your visa" and
# "we sponsor ... visa". It did not match the single most common way a JD
# actually offers sponsorship -- "we provide visa sponsorship", "we offer
# visa sponsorship", "visa sponsorship is available" -- so guard (2) threw
# those away. Deliberately uses "your" and not a bare "you": "You will
# manage visa applications" is a DUTY, not an offer, and must stay catchable
# by guard (5).
_SUPPORT_CANDIDATE_OFFER_RE = re.compile(
    # "... your visa / your work permit / your relocation"
    r"\byour\b[^.;!?]{0,25}\b(?:visa|work\s+permit|work\s+authori[sz]ation|"
    r"relocation|blue\s+card)\b"
    r"|\b(?:visa|work\s+permit|work\s+authori[sz]ation|relocation)\b"
    r"[^.;!?]{0,25}\bfor\s+you\b"
    # "we provide / offer / grant / cover / arrange ... visa sponsorship"
    r"|\bwe\b[^.;!?]{0,30}\b(?:provide|offer|grant|sponsor\w*|support|assist|"
    r"help|cover|pay|reimburse|arrange|handle|facilitate|secure|obtain|"
    r"bieten|uebernehmen|\u00fcbernehmen|offriamo|forniamo|ofrecemos|"
    r"proposons|bieden)\b[^.;!?]{0,45}\b(?:visa|work\s+permit|"
    r"work\s+authori[sz]ation|immigration|blue\s+card|sponsorship|"
    r"sponsoring|arbeitserlaubnis|werkvergunning)\b"
    # "visa sponsorship is available / provided / offered / possible"
    r"|\b(?:visa|work\s+permit|work\s+authori[sz]ation|immigration|"
    r"sponsorship)\b[^.;!?]{0,45}\b(?:is|are|can\s+be|will\s+be|may\s+be|"
    r"would\s+be)\s+(?:fully\s+|also\s+)?(?:available|provided|offered|"
    r"supported|considered|possible|arranged|covered|sponsored|granted)\b"
    # "visa sponsorship available", "relocation support provided"
    r"|\b(?:visa|work\s+permit|immigration|relocation)\s+"
    r"(?:sponsorship|support|assistance|package)\b[^.;!?]{0,20}"
    r"\b(?:available|provided|offered|included|possible)\b"
    # "eligible for sponsorship", "open to / willing to / happy to sponsor"
    r"|\beligible\s+for\b[^.;!?]{0,30}\b(?:visa|sponsorship|work\s+permit)\b"
    r"|\b(?:open|willing|happy|able|prepared)\s+to\s+sponsor\w*"
    r"|\bcan\s+sponsor\b|\bwill\s+sponsor\b"
    r"|\bsponsorship\s+(?:is\s+)?(?:available|provided|offered)\b", re.I)


# FIX P0-52: per-guard downgrade counters. "Sponsored Jobs = 24 out of 2,497"
# is either the honest base rate or a guard eating true positives, and there
# was no way to tell which. The scan summary now prints how many YES verdicts
# each guard removed, so the question is answerable from the run itself.
SUPPORT_GUARD_HITS = collections.Counter()


def _guard_hit(name):
    try:
        SUPPORT_GUARD_HITS[name] += 1
    except Exception:
        pass


def apply_support_fp_guards(visa, reloc, text, title=""):
    """Downgrade visa/relocation YES verdicts that are not offers to the hire.

    A mention is only "support offered to YOU" if it isn't the job FUNCTION,
    an event/travel arrangement, or brand sponsorship. Returns the possibly
    downgraded (visa, reloc) pair; anything other than YES is passed through
    untouched, so this can never invent a verdict.
    """
    text = text or ""
    # (1) "sponsorship" alone is ambiguous (event/brand/partnership). A visa
    #     "Yes" needs an actual visa/immigration/work-authorization keyword.
    if visa == VERDICT_YES and not _SUPPORT_VISA_KEYWORD_RE.search(text):
        visa = VERDICT_UNKNOWN
        _guard_hit("1_no_visa_keyword")
    # (2) event/trade-show/partner sponsorship is not visa sponsorship.
    #     FIX P0-52: this guard had NO candidate-offer veto, unlike (3) and
    #     (5). That was survivable in ats_scanner.py, where the text is a
    #     short structured API description, but career pages are whole
    #     marketing pages: one "we are a proud partner sponsor of the Berlin
    #     Tech Summit" or "we sponsor your conference attendance" in the
    #     perks list wiped out a genuine "we provide full visa sponsorship"
    #     further down the SAME page. Measured after shipping P0-50:
    #     "Visa sponsorship: we provide full visa sponsorship and relocation
    #     support" + a conference perk -> Unknown. Brand sponsorship and
    #     candidate sponsorship routinely coexist, so the mention alone
    #     cannot be disqualifying.
    if visa == VERDICT_YES and (_SUPPORT_EVENT_RE.search(text)
                                or _SUPPORT_EVENT_RE2.search(text)):
        if not _SUPPORT_CANDIDATE_OFFER_RE.search(text):
            visa = VERDICT_UNKNOWN
            _guard_hit("2_event_sponsorship")
    # (3) "visas for international events / speakers / travel", and the
    #     travel-coordination list form, are travel documents arranged for
    #     other people.
    if visa == VERDICT_YES and (
            _SUPPORT_TRAVEL_RE.search(text)
            or _SUPPORT_TRAVEL_LIST_RE.search(text)):
        if not _SUPPORT_CANDIDATE_OFFER_RE.search(text):
            visa = VERDICT_UNKNOWN
            _guard_hit("3_travel_documents")
    # (4) job FUNCTION: the role administers mobility/immigration/relocation
    #     for OTHERS (its title says so). These are not candidate benefits.
    t = (title or "").lower()
    if _SUPPORT_FUNCTION_TITLE_RE.search(t) and _SUPPORT_FUNCTION_ROLE_RE.search(t):
        if visa == VERDICT_YES or reloc == VERDICT_YES:
            _guard_hit("4_job_is_the_function")
        visa = VERDICT_UNKNOWN if visa == VERDICT_YES else visa
        reloc = VERDICT_UNKNOWN if reloc == VERDICT_YES else reloc
    # (5) duty-frame: "tracking of work permits" / "manage visa applications"
    #     describes work the HIRE performs for others, not a benefit.
    if visa == VERDICT_YES and _SUPPORT_DUTY_RE.search(text):
        if not _SUPPORT_CANDIDATE_OFFER_RE.search(text):
            visa = VERDICT_UNKNOWN
            _guard_hit("5_duty_not_benefit")
    return visa, reloc




_COUNTRY_TAIL_CACHE = {}


def _is_country_token(tok: str) -> bool:
    """True when tok is a country NAME or an ISO-2 / ISO-3 country code."""
    t = (tok or "").strip().lower()
    if not t:
        return False
    if not _COUNTRY_TAIL_CACHE:
        names, iso2 = set(), set()
        try:
            from sponsorscout.core.location_country import ISO2_TO_COUNTRY
            iso2 = {k.lower() for k in ISO2_TO_COUNTRY}
            names = {str(v).lower() for v in ISO2_TO_COUNTRY.values() if v}
        except Exception:
            pass
        _COUNTRY_TAIL_CACHE["names"] = names
        _COUNTRY_TAIL_CACHE["iso2"] = iso2
    if len(t) == 2:
        return t in _COUNTRY_TAIL_CACHE["iso2"]
    if len(t) == 3 and t in _ISO3_COUNTRY_CODES:
        return True
    return t in _COUNTRY_TAIL_CACHE["names"]


# ── FIX P36 (2026-10-06): a work-mode qualifier is not a location ─────────
# Greenhouse publishes multi-site roles as a pipe list whose FIRST segment
# is a hiring-policy label, not a place:
#
#   "Remote-Friendly (Travel-Required) | San Francisco, CA | Seattle, WA"
#
# format_location() split on "|" and then took parts[0] unconditionally, so
# the city in the very next segment was thrown away and the row was written
# with Job Location=Unknown. An audit of 638 Greenhouse rows (DocuSign +
# Anthropic) found 25 such rows -- 3.9% -- every one of which the API had
# supplied a perfectly good location for.
#
# Only the qualifier FORMS are skipped. A bare "Remote" still wins over a
# later city, because a posting that leads with "Remote" really is
# remote-first; that behaviour is unchanged.
_WORKMODE_QUALIFIER_RE = re.compile(
    r"(?i)^(?:remote[\s\-]?friendly|hybrid[\s\-]?friendly|office[\s\-]?based"
    r"|flexible|travel[\s\-]?required)(?:\s*\([^)]*\))?$")

def _location_confidence(location, loc_source):
    """How much a Job Location can be trusted (FIX W2-8, shared rule).

    Identical ladder to CareerPortalScanner._location_confidence so the two
    scanners cannot drift: anything derived from the web host or a broad
    region is "low", a detail/API/address read is "high".
    """
    loc = (location or "").strip()
    src = (loc_source or "").strip().lower()
    if not loc or loc.lower() in ("unknown", "not specified"):
        return "none"
    if "site" in src or src in ("company_hq", "region"):
        return "low"
        # NB: anything containing "site" was already caught above -- that is
    # deliberate, "Milan" off the card plus "Italy" off the host is still
    # a guessed country.
    if src in ("detail", "api", "address", "card+detail"):
        return "high"
    if src == "card":
        return "high" if "," in loc else "medium"
    if src in ("url", "title", "slug", "seed_scope+card"):
        return "medium"
    return "medium"


def _location_is_site_derived(loc_source):
    """True when the country came from the web host, not from the posting."""
    return "site" in (loc_source or "").strip().lower()


class ATSScanner:
    def __init__(self, seed_file="company_ATS_seed.csv",
                 output_file="scraped_ats_jobs_v5.csv",
                 skip_preflight=False, resume=False,
                 cancel_event=None, only_companies=None, pause_event=None):
        self.seed_file = seed_file
        self.output_file = output_file
        self.skip_preflight = skip_preflight
        self.resume = resume
        # Cooperative cancellation for the desktop UI Stop button: checked
        # before each target; the in-flight target is allowed to finish.
        self.cancel_event = cancel_event
        # Cooperative suspension for the desktop Pause button: while set, the
        # loop blocks in check_control() without ending the run, so Resume
        # continues in-place (no DB checkpoint, no new run_id).
        self.pause_event = pause_event
        # Optional whitelist of company names (Dashboard "Rescan Companies"):
        # when set, only those targets are scanned.
        self.only_companies = only_companies
        # Set by run(); the per-run errors artifact path.
        self._errors_csv = None
        self.run_id = time.strftime("%Y%m%dT%H%M%S")
        self.detector = JDSupportDetector()

    # ── HTTP helpers ─────────────────────────────────────────────────────────
    @staticmethod
    def _is_network_error(exc):
        msg = str(exc).lower()
        markers = (
            "net::err_", "err_name_not_resolved", "err_connection_",
            "err_timed_out", "err_ssl_", "err_http_", "dns", "socket",
            "connection reset", "timed out", "timeout", "temporary failure",
            "getaddrinfo", "connectionrefused",
        )
        return any(m in msg for m in markers)

    def _preflight_connectivity(self):
        if self.skip_preflight:
            return
        failures = []
        for host in PREFLIGHT_PROBE_HOSTS:
            try:
                socket.setdefaulttimeout(PREFLIGHT_TIMEOUT_SEC)
                infos = socket.getaddrinfo(host, PREFLIGHT_PORT, socket.AF_INET)
                if not infos:
                    raise socket.gaierror("no address")
                ip = infos[0][4][0]
                with socket.create_connection((ip, PREFLIGHT_PORT),
                                              timeout=PREFLIGHT_TIMEOUT_SEC):
                    pass
                print(f"[preflight] OK   {host}")
            except Exception as exc:
                failures.append(f"{host}: {type(exc).__name__}: {exc}")
                print(f"[preflight] FAIL {host}: {type(exc).__name__}: {exc}")
        if len(failures) >= PREFLIGHT_MAX_FAILURES:
            raise RuntimeError(
                "Connectivity pre-flight FAILED: "
                f"{len(failures)}/{len(PREFLIGHT_PROBE_HOSTS)} probes unreachable. "
                "Aborting before crawling. Check DNS/VPN/proxy, then re-run. "
                "(Use --skip-preflight to bypass.)\n  " + "\n  ".join(failures))

    def _fetch(self, url, method="GET", body=None, timeout=None):
        """Fetch a URL with transient-error retry + exponential backoff.
        Returns decoded text. Raises on definitive 404/410 (no retry)."""
        timeout = timeout or HTTP_TIMEOUT_SEC
        last_exc = None
        for attempt in range(1, HTTP_RETRIES + 1):
            try:
                data = json.dumps(body).encode() if body is not None else None
                req = Request(url, data=data, method=method, headers={
                    "User-Agent": "Mozilla/5.0 ATS Scanner",
                    "Accept": "application/json,text/xml,*/*",
                    "Content-Type": "application/json",
                })
                with urlopen(req, timeout=timeout) as resp:
                    return resp.read().decode("utf-8-sig", errors="replace")
            except Exception as exc:
                import urllib.error
                if isinstance(exc, urllib.error.HTTPError):
                    # 404/410 are definitive; 429/5xx are transient — retry them.
                    if exc.code in (404, 410):
                        raise
                    if exc.code != 429 and exc.code < 500:
                        raise
                    last_exc = exc
                elif not self._is_network_error(exc):
                    raise
                else:
                    last_exc = exc
            if attempt < HTTP_RETRIES:
                time.sleep(HTTP_BACKOFF_BASE_SEC * (2 ** (attempt - 1)))
        raise last_exc

    def _get_json(self, url):
        return json.loads(self._fetch(url))

    def _post_json(self, url, body):
        return json.loads(self._fetch(url, method="POST", body=body))

    # ── Seed ─────────────────────────────────────────────────────────────────
    def read_seed_file(self):
        if not os.path.exists(self.seed_file):
            raise FileNotFoundError(
                f"Seed file '{self.seed_file}' not found. "
                f"Put 'company_ATS_seed.csv' in the working directory or pass --input."
            )
        records = []
        errors = []
        seen_keys = set()
        seed_path = self.seed_file
        _legacy_provider_warned = False
        with open(self.seed_file, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            reader.fieldnames = [clean(x) for x in (reader.fieldnames or [])]
            for line_no, row in enumerate(reader, 2):
                # FIX P0-32: rows whose first column starts with "#" are
                # human-readable group headers (the HQ-country banners and the
                # "NEWLY ADDED" markers in the grouped seeds), not companies.
                # Skip them -- and fully blank spacer rows -- BEFORE validation,
                # otherwise each one is reported as "invalid URL ''" and the
                # ENTIRE run aborts with "Seed validation failed".
                _c0 = (row.get("name") or row.get("seed_name") or "").lstrip()
                if _c0.startswith("#") or not any(
                        (v or "").strip()
                        for v in row.values() if isinstance(v, str)):
                    continue
                name = clean(row.get("name"))
                # Schema: the seed carries ``provider`` (+ optional
                # ``board_slug``). ``ats_type`` was its v6 name and has been
                # removed from the schema, the seed manager and the Data
                # Management tab. It is still READ here -- a personal seed
                # under %APPDATA% may predate the rename -- but it is
                # announced once per file instead of being honoured in
                # silence, because a seed that disagrees with the shipped
                # schema will keep surprising its owner until they fix it.
                _legacy_ats = clean(row.get("ats_type"))
                ats_type = (clean(row.get("provider")) or _legacy_ats).lower()
                if _legacy_ats and not _legacy_provider_warned:
                    _legacy_provider_warned = True
                    print(f"  [seed] {seed_path}: uses the removed v6 column "
                          f"'ats_type'; read as 'provider'. Rename the column "
                          f"(or re-copy the shipped seed) to silence this.")
                industry = clean(row.get("industry") or "Tech")
                url = clean(row.get("careers_url"))
                if not name or not url:
                    errors.append(f"line {line_no}: missing name/careers_url")
                    continue
                if not url.startswith(("http://", "https://")):
                    errors.append(f"line {line_no} {name}: invalid URL {url!r}")
                    continue
                unresolved_auto = False
                if not ats_type or ats_type == "auto":
                    sniffed = self._sniff_ats_type(url)
                    if sniffed:
                        ats_type = sniffed
                    else:
                        # Plain careers site with no ATS signature: keep the
                        # row (ats_type stays "auto") so scan_target() routes
                        # it to the browser DOM fallback.  A company is never
                        # dropped just because its ATS cannot be identified.
                        ats_type = "auto"
                        unresolved_auto = True
                if not ats_type:
                    errors.append(
                        f"line {line_no} {name}: no ats_type/provider and the "
                        f"URL matches no known ATS: {url!r}")
                    continue
                if unresolved_auto:
                    print(f"   [seed] {name}: no API adapter for {url!r} — "
                          f"the browser DOM fallback will be used")
                # v6 → v5 upgrade
                upg = SEED_UPGRADE.get(name)
                if upg:
                    changed = []
                    for k in ("source_type", "lever_region"):
                        if upg.get(k):
                            changed.append(f"{k} -> {upg[k]}")
                    if changed:
                        print(f"   [upgrade] {name}: " + "; ".join(changed))
                source_type = ((upg or {}).get("source_type")
                               or clean(row.get("source_type"))
                               or "direct_employer")
                lever_region = (upg or {}).get("lever_region") or ""
                key = (name.casefold(), ats_type, url.casefold())
                if key in seen_keys:
                    print(f"   -> Seed deduped (duplicate): {name} line {line_no}")
                    continue
                seen_keys.add(key)
                scores = {}
                for col in ("sponsorship_history", "english_friendly", "remote_score"):
                    raw = clean(row.get(col))
                    try:
                        scores[col] = int(raw) if raw else None
                    except ValueError:
                        scores[col] = None
                records.append({
                    "name": name, "ats_type": ats_type, "url": url,
                    "industry": industry, "source_type": source_type,
                    "lever_region": lever_region,
                    # v7 columns (defaulted so v6 seeds behave exactly as before)
                    "target_country": clean(row.get("target_country")) or "Global",
                    "scope_policy": (clean(row.get("scope_policy"))
                                     or "global").lower(),
                    "board_slug": clean(row.get("board_slug")),
                    "notes": clean(row.get("notes")),
                    **scores,
                })
        self._audit_seed_contradictions(records)
        if errors:
            raise ValueError("Seed validation failed:\n - " + "\n - ".join(errors))
        return records

    # ── FIX P0-44: contradictory seed rows are now reported at load time ─────
    # Two silent-failure classes cost a full debugging cycle on 2026-10-03.
    #
    #   1. target_country=Global combined with scope_policy=job_location or
    #      seed_url. _scope_allows() short-circuits on "Global" BEFORE it ever
    #      reads scope_policy, so the policy is dead text. A2G Technologies
    #      looked like a broken job_location filter for days; in fact the seed
    #      had opted out of filtering altogether and its Pune postings were
    #      being kept exactly as configured.
    #
    #   2. The seed URL names a country that contradicts target_country --
    #      adecco.com/en-in/ scoped to Belgium, jobswype.pl scoped to Austria,
    #      jobs.elastic.co/jobs/country/ireland scoped to Netherlands. Nothing
    #      raises: the seed just quietly yields nothing, or yields rows from a
    #      country nobody asked for.
    #
    # Neither is detectable from the output, only from the config, so the
    # scanner says it out loud once per run instead of leaving it to be
    # reverse-engineered from the dashboard. This NEVER blocks a run: a seed
    # may legitimately be odd, and refusing to scan would be worse.
    # Hosts that belong to the ATS/CMS vendor rather than the employer, so
    # their TLD says nothing about where the jobs are.
    _ATS_VENDOR_HOSTS = (
        "personio.de", "personio.com", "greenhouse.io", "lever.co",
        "ashbyhq.com", "recruitee.com", "workable.com", "smartrecruiters.com",
        "smartrecruiterscareers.com", "myworkdayjobs.com", "teamtailor.com",
        "bamboohr.com", "avature.net", "csod.com", "icims.com", "taleo.net",
        "oraclecloud.com", "successfactors.eu", "successfactors.com",
        "eightfold.ai", "phenompeople.com", "brassring.com", "jobvite.com",
        "breezy.hr", "jobteaser.com", "avature.com",
    )

    _SEED_LOCALE_RE = re.compile(r"^([a-z]{2})[-_]([a-z]{2})$", re.I)

    def _seed_url_country(self, url):
        """Country named by the seed URL itself, or None.

        Three gazetteer-free signals, strongest first: an explicit
        /country/<name> segment, a locale segment such as /en-in/ or /nl-BE/,
        and finally the host ccTLD (via _country_from_site, which already
        excludes vanity TLDs like .io/.ai/.co).
        """
        if not url:
            return None
        try:
            parsed = urlparse(str(url))
            segments = [s for s in (parsed.path or "").split("/") if s]
        except Exception:
            return None
        try:
            from sponsorscout.core.location_country import ISO2_TO_COUNTRY
        except Exception:
            ISO2_TO_COUNTRY = {}
        names = {}
        for code, country in ISO2_TO_COUNTRY.items():
            if country:
                names[str(country).lower()] = country
        # 1. .../country/ireland, .../location/netherlands
        for index, segment in enumerate(segments[:-1]):
            if segment.lower() in ("country", "countries", "location", "locations"):
                candidate = segments[index + 1].replace("-", " ").replace("%20", " ")
                hit = names.get(candidate.split("?")[0].strip().lower())
                if hit:
                    return hit
        # 2. a bare country-name segment: .../jobs/netherlands
        for segment in segments:
            hit = names.get(segment.replace("-", " ").strip().lower())
            if hit:
                return hit
        # 3. host ccTLD -- but never an ATS VENDOR's host. celus.jobs.personio.de
        # is a German SaaS domain hosting a Portuguese company's board; the TLD
        # describes Personio, not the employer. Same for every vendor below.
        host = (parsed.hostname or "").lower()
        if not any(host == v or host.endswith("." + v) for v in self._ATS_VENDOR_HOSTS):
            from_host = self._country_from_site(url)
            if from_host:
                return from_host
        # 4. a locale segment: /en-in/, /nl_BE/. Weakest signal, and deliberately
        # last: "en_US" is the DEFAULT locale on most corporate sites rather than
        # a market selector, so jobs.enel.com/en_US/careers/JobOpeningsItaly is
        # an Italian board, not an American one. Skipping en-us alone removes
        # every false positive observed across 391 seed rows.
        for segment in segments:
            match = self._SEED_LOCALE_RE.match(segment)
            if not match:
                continue
            language, region = match.group(1).lower(), match.group(2).lower()
            if region in self._VANITY_TLDS:
                continue
            if language == "en" and region == "us":
                continue
            hit = ISO2_TO_COUNTRY.get(region)
            if hit:
                return hit
        return None

    def _audit_seed_contradictions(self, records):
        """Report seed rows whose own fields disagree. Warn only, never fail."""
        dead_policy, url_mismatch = [], []
        for record in records:
            label = (record.get("seed_name") or record.get("name") or "?").strip()
            url = (record.get("careers_url") or record.get("url") or "").strip()
            target = (record.get("target_country") or "Global").strip()
            policy = (record.get("scope_policy") or "global").strip().lower()
            if target.casefold() in ("global", "") and policy != "global":
                dead_policy.append((label, policy))
                continue
            if target.casefold() in ("global", "europe", ""):
                continue
            # A row whose notes say "scope_verified" has been eyeballed by a
            # human; stop nagging about it.
            if "scope_verified" in (record.get("notes") or "").lower():
                continue
            # Only seed_url policy treats the URL as the country authority.
            # Under job_location the URL is EXPECTED to differ -- crawling a
            # global careers page and keeping only Irish jobs is the whole
            # point -- so comparing them there produces nothing but noise.
            if policy != "seed_url":
                continue
            try:
                implied = self._seed_url_country(url)
            except Exception:
                implied = None
            if implied and implied.casefold() != target.casefold():
                url_mismatch.append((label, target, implied, url))
        if not dead_policy and not url_mismatch:
            return
        print("")
        print("   " + "-" * 68)
        print("   SEED WARNINGS (scan continues; nothing below blocks the run)")
        if dead_policy:
            print("   %d row(s) set target_country=Global, which makes scope_policy"
                  % len(dead_policy))
            print("   dead -- _scope_allows() returns True before it reads the policy:")
            for label, policy in dead_policy:
                print("     - %-32s scope_policy=%s is never applied" % (label, policy))
            print("     Fix: name the country in target_country, or set scope_policy=global")
            print("     to say 'accept everything' on purpose.")
        if url_mismatch:
            if dead_policy:
                print("")
            print("   %d row(s) whose seed URL names a different country than"
                  % len(url_mismatch))
            print("   target_country -- one of the two is wrong:")
            for label, target, implied, url in url_mismatch:
                print("     - %-28s target_country=%-16s URL says %s"
                      % (label, target, implied))
                print("       %s" % url[:96])
        print("   " + "-" * 68)
        print("")

    # URL fingerprints for the 8 adapters this scanner implements.  Mirrors
    # ``core.ats_detection`` (plus the SmartRecruiters legacy hosts) so a v7
    # seed row with ``provider=auto`` still resolves; kept local so the
    # standalone single-file mode needs no app imports.
    _ATS_URL_FINGERPRINTS = (
        (re.compile(r"(?:boards|job-boards)\.greenhouse\.io", re.I), "greenhouse"),
        (re.compile(r"(?:jobs|careers|api)\.lever\.co", re.I), "lever"),
        (re.compile(r"jobs\.ashbyhq\.com", re.I), "ashby"),
        (re.compile(r"apply\.workable\.com", re.I), "workable"),
        (re.compile(r"([a-z0-9_-]+\.)+(jobs\.)?personio\.(com|de)", re.I), "personio"),
        (re.compile(r"([a-z0-9_-]+\.)+myworkdayjobs\.com", re.I), "workday"),
        (re.compile(r"(?:jobs|careers)\.smartrecruiters\.com|smartrecruiterscareers\.com", re.I),
         "smartrecruiters"),
        (re.compile(r"([a-z0-9_-]+\.)+recruitee\.com", re.I), "recruitee"),
        (re.compile(r"([a-z0-9_-]+\.)+bamboohr\.com", re.I), "bamboohr"),
        # FIX P19 (2026-10-04): parity with the career scanner, which gained
        # adapters for both of these. A seed row moved between the two files
        # must behave identically.
        (re.compile(r"([a-z0-9_-]+\.)?icims\.com", re.I), "icims"),
        (re.compile(r"([a-z0-9_-]+\.)?eightfold\.ai", re.I), "eightfold"),
    )

    @classmethod
    def _sniff_ats_type(cls, url: str) -> str:
        """Resolve provider=auto / blank from the careers URL. '' = unknown."""
        for pattern, ats_type in cls._ATS_URL_FINGERPRINTS:
            if pattern.search(url or ""):
                return ats_type
        return ""

    # ── Normalization ────────────────────────────────────────────────────────
    @staticmethod
    def _strip_html(text):
        text = re.sub(r"<[^>]+>", " ", text or "")
        return clean(text)


    # ── W3-2 (P6): supplementary gazetteer ───────────────────────────────
    # core.location_country is authoritative and is NOT edited (user rule).
    # This table only fills the gaps it leaves. Every entry below was
    # checked against country_from_location() at build time: entries it
    # already resolves were dropped, and the two names where it disagreed
    # (Cordoba -> Argentina, Newcastle -> Australia) were removed rather
    # than overridden. Evidence: run 20261003T233023 shipped 120 rows whose
    # location resolved to no country at all -- Cuneo, Udine, Pordenone,
    # Gorizia, Treviso, Ludwigsburg, Garching, Wels, Leoben and 53 more.
    _W3_CITY_COUNTRY = {
        "aalst": "Belgium", "abruzzo": "Italy", "agrigento": "Italy",
        "aix-en-provence": "France", "albacete": "Spain", "alessandria": "Italy",
        "almeria": "Spain", "amersfoort": "Netherlands", "amiens": "France",
        "amstetten": "Austria", "ancona": "Italy", "andalucia": "Spain", "angers": "France",
        "annecy": "France", "aosta": "Italy", "arad": "Romania", "arezzo": "Italy",
        "ascoli piceno": "Italy", "asti": "Italy", "athlone": "Ireland", "aveiro": "Portugal",
        "avellino": "Italy", "bacau": "Romania", "badajoz": "Spain",
        "baden-wuerttemberg": "Germany", "baden-wurttemberg": "Germany", "baerum": "Norway",
        "baia mare": "Romania", "barletta": "Italy", "basilicata": "Italy",
        "basingstoke": "United Kingdom", "belluno": "Italy", "benevento": "Italy",
        "bentonville": "United States", "besancon": "France", "bialystok": "Poland",
        "białystok": "Poland", "biel": "Switzerland", "biella": "Italy", "birkirkara": "Malta",
        "bodo": "Norway", "bodø": "Norway", "bolzano": "Italy", "boras": "Sweden",
        "bradford": "United Kingdom", "brandenburg": "Germany", "brasov": "Romania",
        "bregenz": "Austria", "brest": "France", "brindisi": "Italy", "brugge": "Belgium",
        "bucuresti": "Romania", "burgas": "Bulgaria", "burgenland": "Austria",
        "burgos": "Spain", "bydgoszcz": "Poland", "bærum": "Norway", "cadiz": "Spain",
        "caen": "France", "calabria": "Italy", "caltanissetta": "Italy", "campania": "Italy",
        "campobasso": "Italy", "cartagena": "Spain", "caserta": "Italy", "castellon": "Spain",
        "cataluna": "Spain", "catanzaro": "Italy", "celje": "Slovenia",
        "ceske budejovice": "Czech Republic", "charleroi": "Belgium", "chieti": "Italy",
        "chur": "Switzerland", "clermont-ferrand": "France", "constanta": "Romania",
        "coppell": "United States", "coruna": "Spain", "cosenza": "Italy", "craiova": "Romania",
        "cremona": "Italy", "crotone": "Italy", "croydon": "United Kingdom", "cuneo": "Italy",
        "czestochowa": "Poland", "daugavpils": "Latvia", "den bosch": "Netherlands",
        "donostia": "Spain", "dordrecht": "Netherlands", "dornbirn": "Austria",
        "drammen": "Norway", "drenthe": "Netherlands", "drogheda": "Ireland",
        "dundalk": "Ireland", "dundee": "United Kingdom", "ede": "Netherlands",
        "eisenstadt": "Austria", "elche": "Spain", "emilia-romagna": "Italy",
        "emmen": "Netherlands", "enna": "Italy", "ennis": "Ireland", "erlangen": "Germany",
        "esbjerg": "Denmark", "esch-sur-alzette": "Luxembourg", "eskilstuna": "Sweden",
        "esslingen": "Germany", "euskadi": "Spain", "exeter": "United Kingdom",
        "fermo": "Italy", "ferrara": "Italy", "flensburg": "Germany",
        "flevoland": "Netherlands", "foggia": "Italy", "forli": "Italy",
        "fredrikstad": "Norway", "fribourg": "Switzerland", "friesland": "Netherlands",
        "friuli": "Italy", "friuli venezia giulia": "Italy", "frosinone": "Italy",
        "fuerth": "Germany", "furth": "Germany", "galati": "Romania", "galicia": "Spain",
        "garching": "Germany", "gavle": "Sweden", "gdynia": "Poland",
        "gelderland": "Netherlands", "gelsenkirchen": "Germany", "gießen": "Germany",
        "gijon": "Spain", "girona": "Spain", "goettingen": "Germany", "gorizia": "Italy",
        "gottingen": "Germany", "granada": "Spain", "grosseto": "Italy",
        "guimaraes": "Portugal", "gyor": "Hungary", "hagen": "Germany", "halmstad": "Sweden",
        "hameenlinna": "Finland", "hamm": "Germany", "hanau": "Germany", "hasselt": "Belgium",
        "heerlen": "Netherlands", "heilbronn": "Germany", "helsingborg": "Sweden",
        "heraklion": "Greece", "herne": "Germany", "herning": "Denmark", "hessen": "Germany",
        "hildesheim": "Germany", "hilversum": "Netherlands", "hoboken": "United States",
        "horsens": "Denmark", "hradec kralove": "Czech Republic", "huelva": "Spain",
        "huerth": "Germany", "hull": "United Kingdom", "hurth": "Germany", "iasi": "Romania",
        "imperia": "Italy", "isernia": "Italy", "jaen": "Spain", "jelgava": "Latvia",
        "jena": "Germany", "joensuu": "Finland", "jonkoping": "Sweden", "jyvaskyla": "Finland",
        "kaernten": "Austria", "kaiserslautern": "Germany", "karlstad": "Sweden",
        "karnten": "Austria", "kaunas": "Lithuania", "kecskemet": "Hungary", "kielce": "Poland",
        "kilkenny": "Ireland", "klagenfurt": "Austria", "klaipeda": "Lithuania",
        "kolding": "Denmark", "koper": "Slovenia", "kortrijk": "Belgium", "kosice": "Slovakia",
        "krems": "Austria", "kufstein": "Austria", "kuopio": "Finland", "l'aquila": "Italy",
        "la spezia": "Italy", "lahti": "Finland", "lappeenranta": "Finland",
        "larissa": "Greece", "larnaca": "Cyprus", "latina": "Italy", "lazio": "Italy",
        "le havre": "France", "le mans": "France", "lecce": "Italy", "lecco": "Italy",
        "leeuwarden": "Netherlands", "leicester": "United Kingdom", "leiden": "Netherlands",
        "leiria": "Portugal", "leoben": "Austria", "leon": "Spain", "liepaja": "Latvia",
        "liguria": "Italy", "limassol": "Cyprus", "limoges": "France", "livorno": "Italy",
        "lleida": "Spain", "lodi": "Italy", "logrono": "Spain", "lombardia": "Italy",
        "lorsch": "Germany", "lubeck": "Germany", "lucca": "Italy", "ludwigsburg": "Germany",
        "luebeck": "Germany", "lugo": "Spain", "macerata": "Italy", "mantova": "Italy",
        "marburg": "Germany", "marche": "Italy", "maribor": "Slovenia", "massa": "Italy",
        "matera": "Italy", "mechelen": "Belgium", "mecklenburg-vorpommern": "Germany",
        "messina": "Italy", "metz": "France", "milton keynes": "United Kingdom",
        "miskolc": "Hungary", "moenchengladbach": "Germany", "moers": "Germany",
        "molise": "Italy", "monchengladbach": "Germany", "mons": "Belgium", "monza": "Italy",
        "muelheim": "Germany", "muenster": "Germany", "mulheim": "Germany",
        "mulhouse": "France", "murcia": "Spain", "namur": "Belgium", "nancy": "France",
        "narva": "Estonia", "neuchatel": "Switzerland", "neunkirchen": "Austria",
        "neuss": "Germany", "newcastle upon tyne": "United Kingdom",
        "niederoesterreich": "Austria", "niederosterreich": "Austria",
        "niedersachsen": "Germany", "nimes": "France", "nitra": "Slovakia",
        "noord-brabant": "Netherlands", "noord-holland": "Netherlands",
        "nordrhein-westfalen": "Germany", "norrkoping": "Sweden", "novara": "Italy",
        "nuernberg": "Germany", "nuoro": "Italy", "nyiregyhaza": "Hungary",
        "oberhausen": "Germany", "oberoesterreich": "Austria", "oberosterreich": "Austria",
        "offenbach": "Germany", "offenburg": "Germany", "olsztyn": "Poland",
        "oostende": "Belgium", "opole": "Poland", "oradea": "Romania", "oristano": "Italy",
        "orleans": "France", "osijek": "Croatia", "osnabrueck": "Germany", "ourense": "Spain",
        "overijssel": "Netherlands", "oviedo": "Spain", "paderborn": "Germany",
        "palencia": "Spain", "pamplona": "Spain", "panevezys": "Lithuania", "paphos": "Cyprus",
        "parnu": "Estonia", "patras": "Greece", "pavia": "Italy", "pecs": "Hungary",
        "perpignan": "France", "perugia": "Italy", "pesaro": "Italy", "pescara": "Italy",
        "piacenza": "Italy", "piemonte": "Italy", "piraeus": "Greece", "pisa": "Italy",
        "pistoia": "Italy", "pitesti": "Romania", "ploiesti": "Romania", "plovdiv": "Bulgaria",
        "plymouth": "United Kingdom", "poitiers": "France", "pordenone": "Italy",
        "pori": "Finland", "potenza": "Italy", "prato": "Italy", "puglia": "Italy",
        "radom": "Poland", "ragusa": "Italy", "randers": "Denmark", "ravenna": "Italy",
        "recklinghausen": "Germany", "reggio calabria": "Italy", "reggio emilia": "Italy",
        "reims": "France", "remscheid": "Germany", "reutlingen": "Germany",
        "rheinland-pfalz": "Germany", "rieti": "Italy", "rijeka": "Croatia", "rimini": "Italy",
        "roeselare": "Belgium", "roskilde": "Denmark", "rouen": "France", "rovigo": "Italy",
        "rzeszow": "Poland", "saarbruecken": "Germany", "saarland": "Germany",
        "sachsen": "Germany", "sachsen-anhalt": "Germany", "saint-etienne": "France",
        "salamanca": "Spain", "salerno": "Italy", "salzgitter": "Germany", "sandnes": "Norway",
        "sankt poelten": "Austria", "sankt polten": "Austria", "santander": "Spain",
        "sardegna": "Italy", "sassari": "Italy", "savona": "Italy",
        "schleswig-holstein": "Germany", "schwerin": "Germany", "segovia": "Spain",
        "setubal": "Portugal", "siauliai": "Lithuania", "sibiu": "Romania", "sicilia": "Italy",
        "siegen": "Germany", "siena": "Italy", "silkeborg": "Denmark",
        "sint-niklaas": "Belgium", "sion": "Switzerland", "siracusa": "Italy",
        "sliema": "Malta", "sligo": "Ireland", "slough": "United Kingdom",
        "solingen": "Germany", "sollentuna": "Sweden", "solna": "Sweden", "sondrio": "Italy",
        "soria": "Spain", "split": "Croatia", "st. poelten": "Austria", "steiermark": "Austria",
        "steyr": "Austria", "stoke-on-trent": "United Kingdom", "sunderland": "United Kingdom",
        "sundsvall": "Sweden", "swindon": "United Kingdom", "szeged": "Hungary",
        "szekesfehervar": "Hungary", "taranto": "Italy", "tarragona": "Spain",
        "tartu": "Estonia", "teramo": "Italy", "terni": "Italy", "teruel": "Spain",
        "thueringen": "Germany", "thun": "Switzerland", "thuringen": "Germany",
        "timisoara": "Romania", "tirol": "Austria", "toledo": "Spain", "torun": "Poland",
        "toscana": "Italy", "toulon": "France", "tournai": "Belgium", "tours": "France",
        "tralee": "Ireland", "trapani": "Italy", "trentino": "Italy", "trento": "Italy",
        "treviso": "Italy", "trieste": "Italy", "tromso": "Norway", "tromsø": "Norway",
        "tubingen": "Germany", "tuebingen": "Germany", "udine": "Italy", "umbria": "Italy",
        "umea": "Sweden", "vaasa": "Finland", "varese": "Italy", "varna": "Bulgaria",
        "vasteras": "Sweden", "vaxjo": "Sweden", "vejle": "Denmark", "veneto": "Italy",
        "venlo": "Netherlands", "verbania": "Italy", "vercelli": "Italy",
        "vibo valentia": "Italy", "vicenza": "Italy", "villach": "Austria",
        "villeurbanne": "France", "viseu": "Portugal", "viterbo": "Italy", "vitoria": "Spain",
        "vlaanderen": "Belgium", "volos": "Greece", "vorarlberg": "Austria",
        "wallonie": "Belgium", "warszawa": "Poland", "waterloo": "Belgium",
        "watford": "United Kingdom", "wels": "Austria", "wexford": "Ireland",
        "white plains": "United States", "wiener neustadt": "Austria",
        "wolverhampton": "United Kingdom", "wuerzburg": "Germany", "zaandam": "Netherlands",
        "zabrze": "Poland", "zadar": "Croatia", "zamora": "Spain", "zaventem": "Belgium",
        "zeltweg": "Austria", "zilina": "Slovakia", "zlin": "Czech Republic",
        "zoetermeer": "Netherlands", "zuerich": "Switzerland", "zug": "Switzerland",
        "zuid-holland": "Netherlands", "zwickau": "Germany",
        # FIX P51 (2026-10-07): Dutch PROVINCES. Workday writes the
        # province, not the city ("NLD---North-Holland---Haarlem"), so
        # run 20261007T201018 shipped "North Holland" / "North Brabant"
        # with no country and demoted 3 MSD rows to unverified_seed_url.
        # "Limburg" is deliberately absent: it is a province of BOTH the
        # Netherlands and Belgium and would be a guess.
        "north holland": "Netherlands", "noord-holland": "Netherlands",
        "noord holland": "Netherlands",
        "south holland": "Netherlands", "zuid holland": "Netherlands",
        "north brabant": "Netherlands", "noord-brabant": "Netherlands",
        "noord brabant": "Netherlands",
        "gelderland": "Netherlands", "overijssel": "Netherlands",
        "flevoland": "Netherlands", "drenthe": "Netherlands",
        "friesland": "Netherlands", "fryslan": "Netherlands",
        "zeeland": "Netherlands",
    }
    # Country names in their own language (and the common exonyms).
    _W3_COUNTRY_ALIASES = {
        "allemagne": "Germany", "austria": "Austria", "belgie": "Belgium", "belgien": "Belgium",
        "belgique": "Belgium", "belgium": "Belgium", "bulgaria": "Bulgaria",
        "ceska republika": "Czech Republic", "cesko": "Czech Republic", "croatia": "Croatia",
        "cyprus": "Cyprus", "czech republic": "Czech Republic", "daenemark": "Denmark",
        "danemark": "Denmark", "danmark": "Denmark", "denmark": "Denmark",
        "deutschland": "Germany", "duitsland": "Germany", "eesti": "Estonia", "eire": "Ireland",
        "ellada": "Greece", "espana": "Spain", "estonia": "Estonia",
        "etats-unis": "United States", "finland": "Finland", "finnland": "Finland",
        "france": "France", "francia": "France", "frankreich": "France", "frankrijk": "France",
        "germania": "Germany", "germany": "Germany", "gran bretagna": "United Kingdom",
        "grecia": "Greece", "greece": "Greece", "griechenland": "Greece",
        "grossbritannien": "United Kingdom", "großbritannien": "United Kingdom",
        "hrvatska": "Croatia", "hungary": "Hungary", "ireland": "Ireland", "irland": "Ireland",
        "irlanda": "Ireland", "italia": "Italy", "italien": "Italy", "italy": "Italy",
        "kroatien": "Croatia", "latvia": "Latvia", "latvija": "Latvia", "lietuva": "Lithuania",
        "lithuania": "Lithuania", "lussemburgo": "Luxembourg", "luxembourg": "Luxembourg",
        "luxemburg": "Luxembourg", "magyarorszag": "Hungary", "malta": "Malta",
        "nederland": "Netherlands", "netherlands": "Netherlands", "niederlande": "Netherlands",
        "noreg": "Norway", "norge": "Norway", "norway": "Norway", "norwegen": "Norway",
        "oesterreich": "Austria", "osterreich": "Austria", "paesi bassi": "Netherlands",
        "pays-bas": "Netherlands", "poland": "Poland", "polen": "Poland", "polska": "Poland",
        "portogallo": "Portugal", "portugal": "Portugal", "regno unito": "United Kingdom",
        "romania": "Romania", "royaume-uni": "United Kingdom", "rumaenien": "Romania",
        "rumanien": "Romania", "schweden": "Sweden", "schweiz": "Switzerland",
        "slovakia": "Slovakia", "slovenia": "Slovenia", "slovenija": "Slovenia",
        "slovensko": "Slovakia", "spagna": "Spain", "spain": "Spain", "spanien": "Spain",
        "stati uniti": "United States", "suisse": "Switzerland", "suomi": "Finland",
        "sverige": "Sweden", "svizra": "Switzerland", "svizzera": "Switzerland",
        "sweden": "Sweden", "switzerland": "Switzerland", "tschechien": "Czech Republic",
        "turkey": "Turkey", "turkiye": "Turkey", "ungarn": "Hungary",
        "united kingdom": "United Kingdom", "united states": "United States",
        "vereinigte staaten": "United States", "verenigd koninkrijk": "United Kingdom",
        # FIX P54 (2026-10-07): a location string that NAMES its country
        # must always yield that country. Run 20261007T215944 shipped
        # "Baku, Azerbaijan", "Asuncion, Paraguay" and "Dar ES Salaam,
        # Tanzania" with NO country resolved, because the alias table was
        # built for Europe. Aggregators (Jaabz, VanHack, Visa Sponsor
        # Jobs) post worldwide, and several of these are major
        # sponsorship-source countries. Names only -- no cities, nothing
        # ambiguous. "Jamaica" is deliberately omitted: it is also a
        # neighbourhood of Queens, New York, and the comma-split in
        # _supplementary_country would turn "Jamaica, NY" into a country.
        "azerbaijan": "Azerbaijan", "armenia": "Armenia",
        "kazakhstan": "Kazakhstan", "uzbekistan": "Uzbekistan",
        "mongolia": "Mongolia", "montenegro": "Montenegro",
        "paraguay": "Paraguay", "bolivia": "Bolivia",
        "costa rica": "Costa Rica", "panama": "Panama",
        "guatemala": "Guatemala", "trinidad and tobago": "Trinidad and Tobago",
        "tanzania": "Tanzania", "oman": "Oman",
        "bangladesh": "Bangladesh", "pakistan": "Pakistan",
        "sri lanka": "Sri Lanka", "nepal": "Nepal",
        "viro": "Estonia",
    }
    # Multi-country / non-place markers: legitimate to display, never proof
    # of a country.
    _W3_REGION_MARKERS = frozenset({
        "europe", "european union", "eu", "eea", "emea", "apac", "amer",
        "americas", "latam", "nordics", "nordic", "benelux", "dach",
        "worldwide", "global", "international", "north america",
        "south america", "asia", "africa", "middle east", "anywhere",
        "multiple locations", "various locations", "various", "multiple",
    })
    _W3_LEGAL_SUFFIX_RE = re.compile(
        r"(?i)\b(s\.?p\.?a|s\.?r\.?l|s\.?a\.?s|gmbh|mbh|ag|kg|ohg|"
        r"b\.?v|n\.?v|ltd|limited|plc|inc|llc|corp|oy|oyj|ab|a/s|aps|"
        r"sp\.? z o\.?o|s\.?l|sarl)\b\.?\s*$")
    _W3_LOC_PREFIX_RE = re.compile(
        r"(?i)^(?:in|at|presso|sede\s+di|sede|standort|bei|near|c/o|"
        r"location|luogo|ort|plaats|lieu)\s+[:\-]?\s*")

    @staticmethod
    def _w3_norm(value):
        import unicodedata
        s = unicodedata.normalize("NFKD", str(value or ""))
        s = "".join(c for c in s if not unicodedata.combining(c))
        s = re.sub(r"[^0-9a-zA-Z\u00c0-\u024f\s'\-/.]", " ", s)
        return re.sub(r"\s+", " ", s).strip().lower()

    def _w3_strip_location_noise(self, value):
        """Trim the furniture that keeps a real place from being recognised.

        "Sede di Cuneo" -> "Cuneo", "64100 Teramo" -> "Teramo",
        "Innsbruck. Access to ..." -> "Innsbruck".
        """
        v = re.sub(r"\s+", " ", str(value or "")).strip(" \t,;|-")
        if not v:
            return ""
        # a trailing sentence glued to the place name
        v = re.split(r"(?<=[a-z\u00e0-\u024f])\.\s+[A-Z]", v)[0].strip(" .,;|-")
        v = self._W3_LOC_PREFIX_RE.sub("", v).strip()
        v = re.sub(r"^\d{4,6}[\s,-]+", "", v).strip()      # postcode first
        v = re.sub(r"[\s,-]+\d{4,6}$", "", v).strip()      # postcode last
        return v

    def _supplementary_country(self, value):
        """Country for a place core.location_country does not know. "" if none."""
        v = self._w3_strip_location_noise(value)
        if not v:
            return ""
        parts = [v] + [p.strip() for p in re.split(r"[,/|;()\[\]]+|\s+-\s+", v)
                       if p.strip()]
        # FIX P54b (2026-10-07): an explicitly NAMED country outranks a city
        # that merely shares its name with one. The single left-to-right pass
        # returned United States for "San Jose, Costa Rica", because San Jose
        # (California) is in the city table and was reached first. Two passes:
        # every part is tested against the country names before any of them is
        # tested against the city table.
        for table in (self._W3_COUNTRY_ALIASES, self._W3_CITY_COUNTRY):
            # Rightmost first for the country pass (FIX P54b): the country is
            # conventionally the last element of an address.
            for part in (reversed(parts)
                         if table is self._W3_COUNTRY_ALIASES else parts):
                n = self._w3_norm(part)
                if not n or n in self._W3_REGION_MARKERS:
                    continue
                hit = table.get(n)
                if hit:
                    return hit
                n2 = re.sub(r"^\d{4,6}\s+", "", n)
                hit = table.get(n2)
                if hit:
                    return hit
        return ""

    def _resolve_country(self, value):
        """country_from_location first, the supplementary table second."""
        v = str(value or "").strip()
        if not v:
            return ""
        try:
            # local import: the ATS module does not bind this name globally
            from sponsorscout.core.location_country import (
                country_from_location as _cfl)
            c = (_cfl(v) or "").strip()
            if c:
                return c
            c = (_cfl(self._w3_strip_location_noise(v)) or "").strip()
            if c:
                return c
        except Exception:
            pass
        return self._supplementary_country(v)

    def _w3_has_country(self, value):
        """True when the string already names a country in any language."""
        v = self._w3_strip_location_noise(value)
        if not v:
            return False
        for part in re.split(r"[,/|;()\[\]]+", v):
            n = self._w3_norm(part)
            if not n:
                continue
            if n in self._W3_COUNTRY_ALIASES:
                return True
            try:
                from sponsorscout.core.location_country import (
                    country_from_location as _cfl)
                c = (_cfl(part.strip()) or "").strip()
                if c and self._w3_norm(c) == n:
                    return True
            except Exception:
                pass
        return False

    def _w3_is_region_only(self, value):
        n = self._w3_norm(self._w3_strip_location_noise(value))
        return bool(n) and n in self._W3_REGION_MARKERS

    # FIX P0-49 (S-09/S-04): same gate as career_scanner._sanitize_job_location.
    # This file has no gazetteer of its own, so the test is the country
    # resolver plus the company-echo and non-place-marker rules; the intent
    # and the output vocabulary ("Unknown") are identical in both scanners.
    _LOC_STOPWORDS = frozenset({"in", "at", "the", "of", "and", "for", "a", "an"})
    _LOC_KEEP_MARKERS = frozenset({
        "remote", "hybrid", "onsite", "on-site", "on site", "flexible",
        "multiple locations", "multiple", "various", "various locations",
        "work from home", "home office", "field-based", "field based",
        "emea", "apac", "amer", "americas", "latam", "nordics", "benelux",
        "dach", "europe", "european union", "eu",
    })

    # ── W3-3 (P7): cross-bucket URL de-duplication ───────────────────────
    # Run 20261003T233023 shipped 69 job URLs in BOTH <run>.csv and
    # <run>_recruiter.csv -- Bolt's own postings republished by Work in
    # Estonia, and the like. The in-memory guard dedupes by canonical job
    # id (company + title + location + url), which by design differs when
    # two sources describe the same posting, so the DB collapses them and
    # the CSVs do not. This pass runs once, after every company has
    # finished, and keeps the EMPLOYER's copy: an aggregator's row carries
    # a worse Hiring Company and usually a vaguer location.
    _W3_DEDUPE_SKIP_URL_RE = re.compile(r"(?i)#job=|/(?:apply|application)(?:/|$)")

    def _dedupe_cross_bucket(self, direct_csv, recruiter_csv, quarantine_csv,
                             columns):
        """Returns (moved, scanned). Rewrites the files only if moved > 0."""
        import collections

        def _read(path):
            try:
                with open(path, "r", newline="", encoding="utf-8-sig") as f:
                    return list(csv.DictReader(f))
            except FileNotFoundError:
                return []

        direct, recruiter = _read(direct_csv), _read(recruiter_csv)
        if not direct or not recruiter:
            return 0, len(direct) + len(recruiter)
        # A URL that many rows share inside ONE company is a board/apply
        # page, not a posting: never collapse those.
        counts = collections.Counter(
            (r.get("Company Name", ""), r.get("Job URL", ""))
            for r in direct + recruiter)
        shared = {u for (_c, u), n in counts.items() if n > 3}
        employer_urls = {
            (r.get("Job URL") or "").strip() for r in direct
            if (r.get("Job URL") or "").strip()
            and not self._W3_DEDUPE_SKIP_URL_RE.search(r.get("Job URL") or "")
        } - shared
        keep, moved = [], []
        for r in recruiter:
            u = (r.get("Job URL") or "").strip()
            if u and u in employer_urls:
                r["Record Status"] = "quarantine"
                r["Quarantine Reason"] = "duplicate_url_cross_source"
                moved.append(r)
            else:
                keep.append(r)
        if not moved:
            return 0, len(direct) + len(recruiter)
        with open(recruiter_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=columns)
            w.writeheader()
            for r in keep:
                w.writerow({k: r.get(k, "") for k in columns})
        with open(quarantine_csv, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=columns)
            for r in moved:
                w.writerow({k: r.get(k, "") for k in columns})
        return len(moved), len(direct) + len(recruiter)

    def _sanitize_job_location(self, value, company=""):
        v = re.sub(r"\s+", " ", str(value or "")).strip(" \t,;|-")
        if not v:
            return "Unknown"
        # W3-2 parity: a place with furniture glued to it is still a place
        # ("Innsbruck. Access to the region", "64100 Teramo").
        _trimmed = self._w3_strip_location_noise(v)
        if _trimmed and _trimmed != v and self._resolve_country(_trimmed):
            v = _trimmed
        low = v.casefold()
        if low in {"unknown", "not specified", "n/a", "na", "none", "tbd", "-"}:
            return "Unknown"
        if low in self._LOC_KEEP_MARKERS:
            return v
        # Being a real place wins over every other rule -- see the career
        # scanner for the measurement ("Italia" at Piazza Italia is a real
        # location, not the company name echoed back).
        try:
            from sponsorscout.core.location_country import country_from_location
            if (country_from_location(v) or "").strip():
                return v
        except Exception:
            return v
        # W3-2 parity: a real town the core gazetteer does not list
        # (Cuneo, Udine, Ludwigsburg, Garching, Wels ...).
        if self._supplementary_country(v):
            return v
        if self._resolve_country(self._w3_strip_location_noise(v)):
            return self._w3_strip_location_noise(v)
        comp = re.sub(
            r"\b(inc|llc|ltd|limited|gmbh|b\.?v|n\.?v|s\.?a|s\.?r\.?l|ag|plc|"
            r"corp|corporation|company|group|holding|holdings|technologies|"
            r"technology|solutions|services|international|global)\b",
            " ", (company or "").casefold())
        comp_tokens = {t for t in re.findall(r"[a-z0-9]+", comp) if len(t) > 2}
        v_tokens = {t for t in re.findall(r"[a-z0-9]+", low)
                    if t not in self._LOC_STOPWORDS}
        if comp_tokens and v_tokens and v_tokens <= comp_tokens:
            return "Unknown"
        if re.search(r"[A-Za-z\u00c0-\u00ff]", v) and ("," in v or len(v.split()) <= 3):
            return v
        return "Unknown"

    def _norm(self, s):
        try:
            s = s.translate(str.maketrans({
                "\u0142": "l", "\u0141": "L", "\u0105": "a", "\u0104": "A",
                "\u0119": "e", "\u0118": "E", "\u0144": "n", "\u0143": "N",
                "\u015b": "s", "\u015a": "S", "\u017a": "z", "\u0179": "Z",
                "\u017c": "z", "\u017b": "Z", "\u0107": "c", "\u0106": "C",
                "\u00f8": "o", "\u00d8": "O", "\u00e5": "a", "\u00c5": "A",
                "\u00e6": "ae", "\u00c6": "AE", "\u0153": "oe", "\u0152": "OE",
                "\u00df": "ss",
            }))
            s = unicodedata.normalize("NFKD", s)
            s = s.encode("ascii", "ignore").decode()
        except Exception:
            pass
        return re.sub(r"\s+", " ", s).strip().lower()

    # Acronyms / country codes that must never be title-cased ("UK" → "Uk").
    _ACRONYMS = {"uk", "us", "usa", "uae", "eu", "emea", "apac", "latam",
                 "mena", "dach", "dnu", "hq"}

    # Source-data typos seen in ATS location strings.
    _TYPO_MAP = {"dehli": "Delhi"}

    @staticmethod
    def _fmt_token(tok):
        """Normalize one location token: strip Workday '(DNU)' / '(Remote)'
        artifacts and stray parentheses, fix known source typos, preserve
        acronym casing, otherwise keep the source casing (ATS strings are
        already title-cased)."""
        tok = re.sub(r"\s*\(?\s*dnu\s*\)?\s*$", "", tok, flags=re.I)
        tok = tok.strip(" )([")
        if not tok:
            return ""
        # fix source-data typos word-by-word
        for bad, good in ATSScanner._TYPO_MAP.items():
            tok = re.sub(r"\b" + re.escape(bad) + r"\b", good, tok, flags=re.I)
        # normalize acronyms inside a possibly comma-separated region string
        parts = [p.strip() for p in tok.split(",")]
        parts = [
            (p.upper() if p.lower() in ATSScanner._ACRONYMS else p)
            for p in parts if p
        ]
        return ", ".join(parts) if parts else tok

    def format_location(self, location, remote_hint=""):
        """Normalize a structured ATS location string to 'City, Country' or
        'Remote - X'. Returns 'Remote' when the posting is remote with no
        physical place, otherwise 'Unknown' when nothing usable remains."""
        location = clean(location or "")
        remote_hint = clean(remote_hint or "").lower()
        if not location:
            return ("Remote" if "remote" in remote_hint else "Unknown")
        # Collapse multi-line / pipe-separated location lists to the first value
        parts = [clean(p) for p in re.split(r"[;\n|]+", location) if clean(p)]
        # FIX P36: skip leading work-mode qualifiers so the real place, which
        # sits in a later segment, is the one that is read.
        _places = [p for p in parts if not _WORKMODE_QUALIFIER_RE.match(p)]
        location = (_places[0] if _places else (parts[0] if parts else ""))
        if not location:
            return ("Remote" if "remote" in remote_hint else "Unknown")
        low = location.lower()
        if low in ("global", "worldwide", "united", "anywhere", "multiple locations"):
            return ("Remote" if "remote" in remote_hint else "Unknown")
        # "2 Locations" style Workday placeholder (actual sites not in list API)
        if re.fullmatch(r"\d+\s+locations?", low):
            return "Unknown"

        # ── Remote-first forms: "Remote", "Remote - X", "Remote, X", "Remote (X)" ──
        if re.match(r"^remote\b", low):
            m = re.search(r"^remote[\s:.,()-]+(.+)$", location, re.I)
            if m:
                # strip trailing "(Remote)" / "(DNU)" qualifiers, keep the region
                region = re.sub(r"\s*\(\s*remote\s*\)\s*$", "", m.group(1), flags=re.I)
                region = self._fmt_token(region)
                return f"Remote - {region}" if region else "Remote"
            return "Remote"

        # ── "City1, City2 or Remote (Country)" → "City1, Country" ──────────────
        if re.search(r"\bor\b.{0,24}\bremote\b", low):
            cm = re.search(r"\(([^)]+)\)", location)
            country = self._fmt_token(cm.group(1)) if cm else ""
            first_city = self._fmt_token(location.split(",")[0])
            return f"{first_city}, {country}" if country else first_city

        # ── "Place - Remote Based" → keep the leading place ───────────────────
        if re.search(r"-\s*remote\b", low) or re.search(r"\bremote\s+based\b", low):
            leading = re.split(r"-\s*remote\b|\bremote\s+based\b", location, 1, re.I)[0]
            leading = self._fmt_token(leading)
            if leading:
                return leading

        # ── "City, Region, Country" (or bare city) ────────────────────────────
        segs = [s.strip() for s in location.split(",") if s.strip()]
        if segs:
            out = [self._fmt_token(s) for s in segs]
            out = [s for s in out if s]
            if out:
                return ", ".join(out)

        out = self._fmt_token(location)
        return out or ("Remote" if "remote" in remote_hint else "Unknown")

    # ── Classification (honest — no fabrication) ─────────────────────────────
    def classify_job_type(self, raw="", workplace="", description=""):
        """FIX P27 / P27b: delegate to the shared classifier.

        Two defects lived here. (a) `part[ -]?time` was tested BEFORE
        full-time against raw+workplace+description, so Amazon's pay
        footnote decided the schedule. (b) `contract|fixed-term|temporary`
        mapped to **Full-time** here while career_scanner.py mapped the same
        words to **Contract** -- the two scanners disagreed about the same
        job. Both now call identical bytes.
        """
        job_kind = classify_workload(employer_field=raw, title="",
                                     text=clean(f"{workplace} {description}")) \
            or "Unknown"
        mode = classify_work_location_mode(
            clean(f"{raw} {workplace} {description}")) or "Unknown"
        return f"{job_kind} / {mode}"

    def classify_support(self, description, title=""):
        """Run the explicit-evidence support detector over the JD text.
        Returns (eu_blue_card, visa, relocation, relocation_required,
                 confidence, evidence, support_flag)."""
        if not description:
            return ("Unknown", "Unknown", "Unknown", "Unknown", 0.0, "", "Unknown")
        text = self._strip_html(description)
        if not text:
            return ("Unknown", "Unknown", "Unknown", "Unknown", 0.0, "", "Unknown")
        sup = self.detector.detect(text)
        visa = sup["visa"]["verdict"]
        reloc = sup["relocation"]["verdict"]
        # FIX P0-50: the five false-positive guards that used to be inline
        # here now live in the shared apply_support_fp_guards() above, with
        # identical bytes in career_scanner.py, which previously ran none of
        # them at all (S-01). Guard (3) additionally covers the travel-list
        # form (S-02).
        visa, reloc = apply_support_fp_guards(visa, reloc, text, title)
        reloc_req = ("Yes" if sup["relocation"]["required"] else "Unknown")
        conf = round(max(sup["visa"]["confidence"], sup["relocation"]["confidence"]), 2)
        evidence = "; ".join(filter(None, [
            self.detector.best_evidence(sup["visa"]),
            self.detector.best_evidence(sup["relocation"]),
        ]))
        # Blue card is independent of general visa sponsorship.  Uses the same
        # shared classifier as the career scanner (jd_support.detect_blue_card)
        # so both scanners cannot disagree on the same JD text.
        blue = detect_blue_card(self.detector, text)
        flag = "Unknown"
        if visa == VERDICT_YES or reloc == VERDICT_YES:
            flag = "Y"
        elif visa == VERDICT_NO and reloc == VERDICT_NO:
            flag = "N"
        return (blue, visa, reloc, reloc_req, conf, evidence, flag)

    # ── Identity / validation ────────────────────────────────────────────────
    def canonical_job_id(self, company, url, provider, title="", location=""):
        u = unescape(url or "")
        candidates = []
        for pattern in (
            r"(?i)(?:jobid|job_id|gh_jid|reqid|requisitionid|career_job_req_id|postingid|r)=([A-Za-z]*\d{4,})",
            r"(?i)(?:^|[/_-])(R\d{5,})(?:[-_/?]|$)",
            r"(?i)[/_-](?:JR|REQ)[-_]?(\d{4,})(?:[-_/?#]|$)",
            r"(?i)/jobs?/(\d{5,})(?:/|$)",
            r"(?i)/([0-9a-f]{8}-[0-9a-f-]{27,})(?:/|$)",
            r"(?i)/(\d{5,})(?:/?(?:[?#]|$))",
        ):
            candidates.extend(re.findall(pattern, u))
        identity = candidates[0].casefold() if candidates else ""
        if not identity:
            p = urlparse(u)
            identity = p.netloc.casefold().removeprefix("www.") + p.path.rstrip("/").casefold()
        if not identity and title:
            identity = f"title:{self._norm(title)}|loc:{self._norm(location)}"
        # G3 fix: provider must not fragment identity — the same job seen via
        # two ATS boards (or API vs browser fallback) is the same job.
        return f"{company.casefold()}|{identity}"

    def valid_job_url(self, url):
        if not url or not url.startswith(("http://", "https://")):
            return False
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        if host in BAD_HOSTS or any(host.endswith("." + h) for h in BAD_HOSTS):
            return False
        # Reject UI routes by PATH SEGMENT, not substring. A job slug like
        # "Application-Engineer" must NOT match "/application", nor
        # "Team-Leader" match "/team", nor "Legal-Affairs" match "/legal".
        rejected_segments = {
            "users", "sign-in", "signin", "sign_in", "create-alert",
            "create_alert", "privacy", "cookie", "terms", "legal", "blog",
            "posts", "tags", "about", "team", "culture", "form",
            "applicationmethods", "apply", "application",
        }
        segments = [s for s in path.split("/") if s]
        for seg in segments:
            if seg in rejected_segments:
                return False
        # a couple of multi-segment UI routes (exact path, not substring)
        if re.search(r"/(users/sign_in|applicationmethods)(?:/|$)", path):
            return False
        if re.search(r"/(careers?|jobs?)/?$", path):
            return False
        return True

    # Compact place vocabulary for the G1 location-as-title guard below
    # (ATS port of the career-scanner fix; API titles are structured so this
    # mostly guards the browser_fallback DOM scrape).
    _TITLE_PLACES = frozenset("""
        milano milan roma rome torino turin napoli naples genova florence firenze
        bologna palermo wetzlar giessen walldorf darmstadt berlin munich muenchen
        münchen hamburg frankfurt stuttgart dusseldorf düsseldorf koln köln cologne
        essen leipzig dresden nuremberg nürnberg hannover paris lyon marseille
        london manchester birmingham leeds dublin amsterdam rotterdam eindhoven
        utrecht brussels bruxelles zurich zürich geneva vienna wien madrid barcelona
        lisbon lisboa porto warsaw warszawa krakow kraków prague praha budapest
        bucharest athens stockholm oslo copenhagen helsinki york francisco austin
        seattle boston chicago toronto vancouver atlanta dallas denver houston
        miami phoenix portland tacoma arlington hillsboro chillicothe ballston
        florham yixing westlake wetherill suzhou shanghai beijing shenzhen tokyo
        yokohama osaka kyoto singapore bangalore bengaluru hyderabad chennai mumbai
        delhi pune johor bahru sydney melbourne germany deutschland italy italia
        france spain espana españa netherlands nederland belgium schweiz switzerland
        austria ireland england scotland wales poland portugal sweden norway denmark
        finland greece hungary romania czechia china japan india australia canada
        mexico brazil texas california florida washington ontario bayern bavaria
        hessen baden-württemberg baden-wurttemberg nordrhein-westfalen europe eu emea
        apac dach nordics benelux balkans latam mena global worldwide
        """.split())

    def _title_is_pure_location(self, title: str) -> bool:
        """True when a title is only place/code/postal segments ("Milano, MI").

        Every comma segment must be a known place, a 2-3 letter code, or a
        postal code — anything else (e.g. "Sales, UK", "Nurse, Berlin") is
        kept as a title so real jobs are never quarantined by this check.
        """
        segs = [s.strip() for s in title.split(",")]
        if len(segs) < 2:
            return False
        # FIX P0-34b: the loop below needs at least one KNOWN place, so an
        # unlisted town defeated it ("Jesi, AN, ITA" was kept as a title).
        # A trailing country name/ISO-2/ISO-3 with no role word anywhere means
        # the string is an address, known town or not.
        # Guard rails (parity with career_scanner): >=3 segments AND a
        # non-tail segment that is a code or known place, so "Sales, UK" and
        # "Marketing, Digital, UK" stay titles.
        # A full country NAME or ISO-3 code anywhere in the string (never a
        # bare ISO-2, which collides with words like "UK" in "Sales, UK")
        # plus no role noun => address, even when the town is unknown to the
        # gazetteer ("ITA, PI, Pisa", "Pisa, Italy").
        if (any(len(s.strip()) >= 3 and _is_country_token(s) for s in segs)
                and not any(_has_role_noun(s.lower()) for s in segs)
                and all(len(s.split()) <= 4 for s in segs if s)):
            return True
        _head = [s for s in segs[:-1] if s]
        if (_is_country_token(segs[-1]) and _head
                and all(re.fullmatch(r"[a-z]{2,3}", s.lower()) for s in _head)):
            return True
        _anchor = any(
            re.fullmatch(r"[a-z]{2,3}", s.lower())
            or all(x in self._TITLE_PLACES for x in s.lower().split())
            for s in _head
        )
        if (_is_country_token(segs[-1]) and _anchor and len(segs) >= 3
                and not any(_has_role_noun(s.lower()) for s in segs)):
            if all(len(s.split()) <= 4 for s in segs if s):
                return True
        saw_place = False
        for s in segs:
            if not s:
                continue
            w = s.lower()
            if re.fullmatch(r"[a-z]{2,3}", w) or re.fullmatch(r"[\d\s\-]*\d[\d\s\-]*", w):
                continue  # state/country code or postal code
            words = w.split()
            if words and all(x in self._TITLE_PLACES for x in words):
                saw_place = True
                continue
            return False
        return saw_place


    # ── W3-1 (P5): multilingual title hygiene ────────────────────────────
    # Evidence, run 20261003T233023: the gate rejected real non-English
    # roles ("Koch (m/w/d)" -> "Koch", 4 letters, killed by the
    # single-word-under-5 rule) while accepting board furniture in the same
    # languages ("Job Detail", "Careers FAQs", "Jobsuche", "Stellenangebote",
    # "Offerte di lavoro", "Vacatures", "Alle Jobs anzeigen"). Both bugs are
    # English bias: the junk list was English-only and the length rule
    # assumes English role nouns are long.
    _W3_NAV_TITLES = frozenset({
        # English
        "job", "jobs", "job detail", "jobdetail", "job details", "jobdetails",
        "faq", "faqs", "careers faq", "careers faqs", "career faq",
        "career faqs", "job search", "search jobs", "search for jobs",
        "all jobs", "view all jobs", "see all jobs", "browse jobs",
        "job alert", "job alerts", "create job alert", "open positions",
        "all openings", "see all openings", "current openings",
        "current vacancies", "our vacancies", "vacancies", "vacancy",
        "career opportunities", "job opportunities", "opportunities",
        "positions", "openings", "careers", "career", "our jobs",
        "working here", "life at", "meet the team", "our teams",
        # German
        "jobsuche", "stellensuche", "stellenangebote", "stellenanzeigen",
        "alle stellenangebote", "alle stellen", "offene stellen",
        "alle jobs", "alle jobs anzeigen", "jobs anzeigen", "zur jobsuche",
        "karriere", "karriere bei uns", "stellenmarkt", "initiativbewerbung",
        "jobboerse", "jobbörse", "jobangebote",
        # Italian
        "offerte di lavoro", "tutte le offerte", "le nostre offerte",
        "posizioni aperte", "lavora con noi", "candidatura spontanea",
        "cerca lavoro", "ricerca lavoro", "opportunita di lavoro",
        "opportunità di lavoro", "carriere",
        # Dutch
        "vacatures", "alle vacatures", "werken bij", "open sollicitatie",
        "bekijk alle vacatures", "vacature",
        # French
        "emplois", "nos offres", "toutes les offres", "offres d'emploi",
        "offre d'emploi", "candidature spontanee", "candidature spontanée",
        "carrieres", "carrières", "nos metiers", "nos métiers",
        # Spanish / Portuguese
        "ofertas de empleo", "todas las ofertas", "trabaja con nosotros",
        "empleo", "empleos", "vagas", "todas as vagas", "trabalhe conosco",
        # Nordics / Finnish / Polish
        "ledige stillinger", "alle ledige stillinger", "lediga jobb",
        "alla lediga jobb", "avoimet tyopaikat", "avoimet työpaikat",
        "hae tyopaikkoja", "oferty pracy", "wszystkie oferty", "praca",
    })
    # Substrings that are furniture in any sentence they appear in. Kept
    # deliberately short: every entry was checked against the 9,280 accepted
    # titles of run 20261003T233023 and matches 0 real roles.
    _W3_NAV_SUBSTRINGS = (
        "job detail", "jobdetails", "careers faq", "career faq",
        "offerte di lavoro", "alle jobs anzeigen", "toutes les offres",
        "candidatura spontanea", "open sollicitatie", "create job alert",
        "bekijk alle vacatures", "initiativbewerbung",
    )
    # "<role> Jobs in <place>" / "<role> Stellenangebote in <ort>" — an
    # aggregator listing pattern. The role is real, the suffix is the board's
    # own search phrase, and the place is a usable location hint.
    _W3_JOBS_IN_RE = re.compile(
        r"(?i)\s*[-–—|,:]?\s*\b(?:jobs?|stellen(?:angebote|anzeigen)?|"
        r"offerte(?:\s+di\s+lavoro)?|vacatures?|emplois?|empleos?|vagas?|"
        r"oferty|tyopaikat|työpaikat)\s+"
        r"(?:in|bei|at|à|a|en|te|w|na)\s+"
        r"(?P<place>[A-Za-z\u00c0-\u024f][\w\u00c0-\u024f'’\-\. ]{1,38})\s*$"
    )
    # Role nouns that are complete titles in under five characters. English
    # has almost none, which is why the old rule looked safe.
    _W3_SHORT_ROLES = frozenset({
        "koch", "arzt", "chef", "kok", "cook", "sous", "vet", "nurse",
        "ceo", "cfo", "cto", "coo", "cio", "cmo", "chro", "cdo", "cso",
        "md", "gp", "pm", "qa",
    })

    def _split_title_place(self, title):
        """("Designer (m/w/d) Jobs in Innsbruck") -> ("Designer", "Innsbruck").

        Returns the title unchanged and "" when the pattern does not apply.
        """
        t = re.sub(r"\s+", " ", str(title or "")).strip()
        if not t:
            return "", ""
        m = self._W3_JOBS_IN_RE.search(t)
        if not m:
            return t, ""
        head = t[:m.start()].strip(" \t-–—|,:")
        place = re.sub(r"\s+", " ", m.group("place")).strip(" .,-")
        if len(head) < 3 or not any(ch.isalpha() for ch in head):
            return t, ""          # the suffix WAS the title ("Jobs in Wien")
        return head, place

    def _is_nav_title(self, title):
        """Board navigation / search furniture, in any of the seed languages."""
        low = re.sub(r"\s+", " ", str(title or "")).strip().casefold()
        low = low.strip(" .:|-–—")
        if not low:
            return True
        if low in self._W3_NAV_TITLES:
            return True
        if any(x in low for x in self._W3_NAV_SUBSTRINGS):
            return True
        # "Jobsuche Berlin", "Stellenangebote Hamburg", "Vacatures Amsterdam"
        if re.match(r"^(jobsuche|stellensuche|stellenangebote|stellenanzeigen|"
                    r"vacatures|offerte di lavoro|ofertas de empleo|"
                    r"offres d'emploi|job search|search jobs)\b", low):
            return True
        return False

    def valid_title(self, title):
        title = clean(title)
        title = self._split_title_place(title)[0]
        low = title.lower()
        if not title or len(title) > 180:
            return False
        # G1 fix: location stubs used as titles ("Milano, MI", "Wetzlar, DE").
        # make_row() quarantines invalid titles with a clear reason.
        if self._title_is_pure_location(title):
            return False
        # FIX P0-34a: card metadata lines ("Updated: 9/3/2026", "Posted: ...")
        # and bare dates were accepted as job titles.
        if re.match(r"^(updated|posted|published|last\s+updated|date|"
                    r"aggiornato|pubblicato|aktualisiert|bijgewerkt|"
                    r"mis\s+a\s+jour)\b\s*[:\-–]?\s*", low):
            return False
        if re.fullmatch(r"[\d\s/.\-–]+", title):
            return False
        # allow short CJK titles (e.g. 2-char "电工" = electrician); Latin titles
        # under 3 chars ("IT", "HR", "QA") are never real job titles
        if len(title) < 3 and not re.search(
                r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", title):
            return False
        if low in BAD_TITLES:
            return False
        # W3-1 (P5) parity with career_scanner: board navigation in any
        # language ("Job Detail", "Jobsuche", "Offerte di lavoro", ...).
        if self._is_nav_title(title):
            return False
        rejected = (
            "sorry, internet explorer", "skip to main", "looking for a job",
            "talent community", "talent pool", "candidate database",
            "career day", "save for later", "show job", "learn more",
            "read more", "view job", "view role",
        )
        if any(x in low for x in rejected):
            return False
        # Batch L: policy phrases are conditional — real jobs contain them
        # ("Data Protection Officer", "Equal Opportunity Specialist"), while
        # banners ("Privacy Policy", "Equal Opportunity Employer") carry no
        # role noun. Pool phrases above stay unconditional on purpose:
        # "Engineering Talent Pool" names a pool, not a job.
        policy_phrases = (
            "privacy policy", "cookie policy", "terms of service",
            "equal opportunity", "data protection",
        )
        if any(p in low for p in policy_phrases) and not _has_role_noun(low):
            return False
        # Bare generic level/function words are not real titles — EXCEPT the
        # complete entry-level titles below, which are legitimate on their own
        # ("Intern", "Apprentice", "Trainee" describe the role fully, unlike
        # a truncated "Director" or "Engineer").
        if low in {"senior", "junior", "associate", "principal", "lead",
                   "manager", "director", "expert", "owner", "officer",
                   "specialist", "analyst", "engineer",
                   "full time", "part time", "contract"}:
            return False
        return True

    # ── Row construction (full v5 schema, honest defaults) ───────────────────
    # ── FIX P0-41 (parity with career_scanner) ───────────────────────────
    _STREET_WORDS = (r"via|viale|v\.le|piazza|piazzale|p\.zza|corso|c\.so|strada|"
                     r"largo|vicolo|contrada|localit[àa]|lungomare|rue|avenue|"
                     r"boulevard|calle|carrer|rua|stra[sß]e|strasse|weg|allee|"
                     r"platz|laan|straat|street|road|avenida")
    _VANITY_TLDS = frozenset({
        "io", "ai", "co", "me", "tv", "cc", "ws", "fm", "ly", "to", "gg",
        "im", "je", "sh", "st", "vc", "nu", "bz", "cx", "mu", "ms", "tk",
        "ml", "ga", "cf", "gq", "am", "fo", "ag", "sc", "la", "ki", "mn",
    })
    _JUNK_TOWN_WORDS = frozenset({
        "codice", "riferimento", "calcolare", "distanza", "applicare", "ora",
        "sede", "indirizzo", "filiale", "negozio", "store", "adresse",
        "standort", "location", "address", "apply", "now", "reference",
        "job", "jobs", "contratto", "tempo", "full", "part", "time"})

    def _town_from_address(self, text):
        """Town out of a street address the place vocabulary does not list."""
        if not text:
            return None
        t = re.sub(r"\s+", " ", str(text))[:400]
        word = r"[A-ZÀ-ÖØ-Þ][\w'’\-\.]*(?:\s+(?:di|de|del|della|sul|sotto|a|in)\s+[A-ZÀ-ÖØ-Þ]?[\w'’\-\.]*)?"
        name = rf"{word}(?:\s+{word}){{0,2}}"
        m = (re.search(rf"({name})\s*,\s*(?:{self._STREET_WORDS})\b", t, re.IGNORECASE | re.UNICODE)
             or re.search(rf"(?:{self._STREET_WORDS})\b[^,]{{0,60}},\s*({name})", t, re.IGNORECASE | re.UNICODE)
             or re.search(rf"\b\d{{4,5}}\s+({name})\b", t))
        if not m:
            return None
        cand = re.sub(r"\s+", " ", m.group(1)).strip(" ,.-")
        if (not cand or len(cand) < 3 or len(cand.split()) > 3
                or cand.lower() in self._JUNK_TOWN_WORDS
                or _has_role_noun(cand.lower())
                or not re.match(r"^[A-ZÀ-ÖØ-Þ]", cand)):
            return None
        return cand

    #: FIX P49 (2026-10-07): Workday encodes the posting's country as an
    #: ISO-3166 alpha-3 segment in the job URL --
    #: "/job/NLD---North-Holland---Haarlem/...". The scanner read only the
    #: display string, so in run 20261007T201018 four MSD rows carried a bare
    #: Dutch province with no country and seven Wolters Kluwer rows at
    #: NLD---Alphen-Aan-Den-Rijn (their Dutch HQ) were quarantined as
    #: outside-target because the display string was "Multiple Locations".
    #: Only codes whose country the gazetteer already names are listed, so
    #: this never invents a country.
    _ISO3_TO_COUNTRY = {
        "AUT": "Austria", "AUS": "Australia", "BEL": "Belgium",
        "BGR": "Bulgaria", "BRA": "Brazil", "CAN": "Canada",
        "CHE": "Switzerland", "CHN": "China", "CZE": "Czech Republic",
        "DEU": "Germany", "DNK": "Denmark", "ESP": "Spain", "EST": "Estonia",
        "FIN": "Finland", "FRA": "France", "GBR": "United Kingdom",
        "GRC": "Greece", "HRV": "Croatia", "HUN": "Hungary", "IND": "India",
        "IRL": "Ireland", "ISR": "Israel", "ITA": "Italy", "JPN": "Japan",
        "KOR": "South Korea", "LTU": "Lithuania", "LUX": "Luxembourg",
        "LVA": "Latvia", "MEX": "Mexico", "MLT": "Malta", "NLD": "Netherlands",
        "NOR": "Norway", "NZL": "New Zealand", "POL": "Poland",
        "PRT": "Portugal", "ROU": "Romania", "SGP": "Singapore",
        "SVK": "Slovakia", "SVN": "Slovenia", "SWE": "Sweden",
        "TUR": "Turkey", "USA": "United States", "ZAF": "South Africa",
        "ARE": "United Arab Emirates", "SAU": "Saudi Arabia",
    }
    #: "/job/<SEGMENT>/" where SEGMENT is "NLD---North-Holland---Haarlem".
    _WORKDAY_URL_LOC_RE = re.compile(
        r"/job/([A-Z]{3})---([^/?#]+)")

    def _workday_url_location(self, *urls):
        """(place, country) encoded in a Workday job URL, else ("", "").

        FIX P49. Returns the country only for a code the table names, and the
        place with the alpha-3 stripped off. Never guesses.
        """
        for u in urls:
            if not u:
                continue
            m = self._WORKDAY_URL_LOC_RE.search(str(u))
            if not m:
                continue
            country = self._ISO3_TO_COUNTRY.get(m.group(1).upper())
            if not country:
                continue
            parts = [p.replace("-", " ").strip()
                     for p in m.group(2).split("---") if p.strip()]
            # Workday orders segments broad -> narrow (region, then site).
            # The first is the one the board itself displays.
            place = parts[0] if parts else ""
            return place, country
        return "", ""

    def _country_from_site(self, *urls):
        """Country implied by the host a posting is served from."""
        try:
            from sponsorscout.core.location_country import ISO2_TO_COUNTRY
        except Exception:
            return None
        for u in urls:
            if not u:
                continue
            try:
                host = (urlparse(str(u)).hostname or "").lower().strip(".")
            except Exception:
                continue
            if not host:
                continue
            labels = host.split(".")
            head = labels[0]
            if (len(head) == 2 and head != "ww" and head in ISO2_TO_COUNTRY
                    and head not in self._VANITY_TLDS):
                return ISO2_TO_COUNTRY[head]
            tld = labels[-1]
            if tld == "uk" and "gb" in ISO2_TO_COUNTRY:
                return ISO2_TO_COUNTRY["gb"]
            if (len(tld) == 2 and tld in ISO2_TO_COUNTRY
                    and tld not in self._VANITY_TLDS):
                return ISO2_TO_COUNTRY[tld]
        return None

    # ── FIX P0-40: scope enforcement (ported from career_scanner) ─────────
    # This module READ scope_policy and wrote it to output, but never acted on
    # it: there was no _scope_allows() in the file at all, and the scan log
    # hard-coded "Rejected Scope": 0. Every ATS board therefore ingested every
    # country regardless of the seed's target_country -- the same defect that
    # put 10 Pune/Hyderabad jobs under a Netherlands-scoped recruiter on the
    # career side. Same rule, same alias table, same quarantine reason, so the
    # two scanners stay in lockstep.
    EUROPE_COUNTRIES = frozenset({
        "austria", "belgium", "bulgaria", "croatia", "cyprus",
        "czech republic", "denmark", "estonia", "finland", "france",
        "germany", "greece", "hungary", "iceland", "ireland", "italy",
        "latvia", "liechtenstein", "lithuania", "luxembourg", "malta",
        "netherlands", "norway", "poland", "portugal", "romania",
        "slovakia", "slovenia", "spain", "sweden", "switzerland",
        "united kingdom",
    })


    # ── FIX P64 (2026-10-08): country scoping is OFF by default ─────────
    # A seed's target_country records the PORTAL's scope and the company's
    # HQ. It was never meant to delete that company's jobs in other
    # countries: "country batch means HQ is from a country, but doesn't mean
    # remove other country jobs from that portal's scan".
    #
    # Across the runs shared so far this filter had discarded 6,770 jobs
    # that carried a perfectly good location -- Barclays Pune x198, New York
    # x43, Mumbai x36; T-Systems Budapest/Warsaw/Munich; Cegedim
    # Boulogne-Billancourt x51 -- plus 2,830 more whose location could not be
    # proven. Every one is a real vacancy on the seeded portal, so every one
    # is now kept, carrying whatever location the JD itself states. The user
    # filters by location in the app's search tab.
    #
    # Set SPONSORSCOUT_SCOPE_FILTER=1 to restore the old behaviour.
    SCOPE_FILTER = (os.environ.get("SPONSORSCOUT_SCOPE_FILTER", "0")
                    .strip().lower() in ("1", "true", "yes", "on"))

    _SCOPE_ALIASES = {
        "germany": {"germany", "deutschland", "berlin", "hamburg", "munich", "munchen", "muenchen", "frankfurt", "cologne", "koln", "koeln", "dusseldorf", "duesseldorf", "stuttgart", "hannover", "bremen", "leipzig", "dresden", "bayern", "bavaria"},
        "italy": {"italy", "italia", "milan", "milano", "rome", "roma", "turin", "torino", "bologna", "napoli", "parma", "venice", "venezia", "florence", "firenze", "lombardia", "lombardy", "piemonte", "toscana", "sicilia"},
        "netherlands": {"netherlands", "nederland", "amsterdam", "rotterdam", "utrecht", "haarlem", "delft", "eindhoven", "north holland", "noord holland", "zuid holland"},
        "united kingdom": {"united kingdom", "england", "scotland", "wales", "northern ireland", "london", "manchester", "birmingham", "edinburgh", "glasgow", "uk"},
        "ireland": {"ireland", "dublin", "cork", "galway", "limerick"},
        "india": {"india", "bharat", "pune", "mumbai", "bombay", "delhi", "new delhi", "bengaluru", "bangalore", "hyderabad", "chennai", "madras", "kolkata", "calcutta", "ahmedabad", "noida", "gurgaon", "gurugram", "kochi", "cochin"},
    }

    def _scope_allows(self, target, location, context="", url=""):
        # FIX P64: no job is dropped for being in the "wrong" country.
        if not self.SCOPE_FILTER:
            return True
        policy = (target.get("scope_policy") or "global").lower()
        tc = (target.get("target_country") or "Global").strip()
        if policy == "global" or tc.casefold() == "global" or not tc:
            return True
        if policy == "seed_url":
            # The seed URL is itself the country signal; accept, but the page
            # evidence is still evaluated so the row can be flagged.
            self._last_scope_verified = self._scope_country_match(
                tc, location, context, url)
            return True
        # FIX P0-37 parity: "job_location" means the JOB's location decides.
        # Page context and URL are deliberately NOT consulted -- blending them
        # in let one mention of the target country anywhere on a board pass
        # every job on it.
        return self._scope_country_match(tc, location, "", "")

    def _scope_country_match(self, target, location, context="", url=""):
        blob = self._norm(" ".join([
            location or "", context or "",
            unquote(url or ""),
        ]))
        if target.casefold() == "europe":
            try:
                from sponsorscout.core.location_country import country_from_location
                c = (country_from_location(location or "") or "").casefold()
                if c and c in self.EUROPE_COUNTRIES:
                    return True
            except Exception:
                pass
            return any(
                re.search(r"(?:^|[^a-z])" + re.escape(a) + r"(?:$|[^a-z])", blob)
                for a in self.EUROPE_COUNTRIES)
        aliases = self._SCOPE_ALIASES.get(target.casefold(), {target.casefold()})
        if any(re.search(r"(?:^|[^a-z])" + re.escape(a) + r"(?:$|[^a-z])", blob)
               for a in aliases):
            return True
        # W3-2 parity: the alias table lists ~20 cities per country, so a job
        # in Cuneo or Ludwigsburg failed a scope test it should pass.
        try:
            return bool(location) and (
                self._resolve_country(location) or "").casefold() == target.casefold()
        except Exception:
            return False

    def _scope_confidence(self, target, location, context="", url=""):
        """FIX P19b: career-scanner vocabulary for the scope verdict."""
        policy = (target.get("scope_policy") or "global").lower()
        tc = (target.get("target_country") or "Global").strip()
        if not tc or tc.casefold() == "global":
            return "n/a"
        if policy == "seed_url":
            return ("verified"
                    if self._scope_country_match(tc, location, context, url)
                    else "unverified_seed_url")
        if policy == "job_location":
            # Rows that fail the job-location test never reach the writer --
            # make_row quarantines them -- so anything written is verified.
            return "verified"
        return "n/a"

    def make_row(self, target, title, url, location, raw_location, job_type,
                 description, extraction_method, location_source="api"):
        title = clean(title)
        url = clean(url)
        # FIX P0-41: recover an unlisted town / name the country from the
        # posting's host BEFORE the scope test, so an enriched row is judged
        # on its real location instead of being quarantined as "unknown".
        if not location or location in ("Not Specified", "Unknown"):
            _town = (self._town_from_address(raw_location)
                     or self._town_from_address(description))
            _site_c = self._country_from_site(url, target.get("url"))
            if _town and _site_c:
                location, location_source = f"{_town}, {_site_c}", "address+site"
            elif _town:
                location, location_source = _town, "address"
            elif _site_c:
                location, location_source = _site_c, "site_host"
        else:
            try:
                from sponsorscout.core.location_country import country_from_location
                if not (country_from_location(location) or ""):
                    # W3-2 parity: the place's own country first, the web
                    # host only as a last resort.
                    _gaz_c = self._supplementary_country(location)
                    # W3-2 parity: do not append a country the string
                    # already names in its own language.
                    if _gaz_c and self._w3_has_country(location):
                        _gaz_c = ""
                        location_source = f"{location_source}+gazetteer"
                    if _gaz_c:
                        location = f"{location}, {_gaz_c}"
                        location_source = f"{location_source}+gazetteer"
                    else:
                        _site_c = self._country_from_site(url, target.get("url"))
                        if _site_c:
                            location = f"{location}, {_site_c}"
                            location_source = f"{location_source}+site"
            except Exception:
                pass
        reason = None
        if not self.valid_job_url(url):
            reason = "invalid_or_application_only_url"
        elif not self.valid_title(title):
            reason = "invalid_generic_or_department_title"
        elif (self.SCOPE_FILTER
              and (target.get("scope_policy") or "global").lower() == "job_location"
              and _location_is_site_derived(location_source)):
            # FIX W2-8 (parity): under job_location the JOB's own location is
            # the only admissible evidence. A country read off the seed's host
            # let 1,117 career rows through in run 20261003T233023 (Randstad
            # -> "Belgium" x840) and inflated every Jobs-by-Country tile.
            reason = "outside_or_unproven_target_country"
        elif not self._scope_allows(target, location, description or "", url):
            # FIX P0-40: out-of-scope rows are QUARANTINED, never dropped, so
            # they stay reviewable in <output>_quarantine.csv.
            reason = "outside_or_unproven_target_country"
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        rec = {
            "Company Name": target["name"],
            "Seed Name": target["name"],
            "Source Type": target["source_type"],
            "Hiring Company": (target["name"] if target["source_type"] == "direct_employer" else "Unknown"),
            "Target Country": target.get("target_country", "Global"),
            "Scope Policy": target.get("scope_policy", "global"),
            "Industry Type": target["industry"],
            "Sponsorship History Score": target.get("sponsorship_history", ""),
            "English Friendly Score": target.get("english_friendly", ""),
            "Remote Score": target.get("remote_score", ""),
            "Job Title": title,
            "Raw Job Title": title,
            # FIX P0-49 (S-09/S-04): gate the value, same rule as career.
            "Job Location": self._sanitize_job_location(
                location if location and location != "Not Specified" else "",
                target["name"]),
            "Raw Location": raw_location or "",
            "Job Type": job_type,
            # FIX P19b: scope verdict, same vocabulary as the career writer.
            "Scope Confidence": self._scope_confidence(target, location,
                                                       raw_location, url),
            # FIX P19b: same classifier AND the same evidence the career
            # scanner uses -- title, the employer's own contract/work-mode
            # fields (job_type) and the location. Feeding the whole JD body
            # in here made the two scanners disagree on identical rows
            # (DocuSign: career "Hybrid" from the employer's tag vs ats
            # "Remote/Hybrid" from the word "remote" somewhere in the prose)
            # and is the same weak-evidence mistake that inflated the
            # dashboard Remote tile to 589 (P9).
            "Work Mode": classify_work_mode(
                f"{title} {job_type or ''} {raw_location or ''} {location or ''}"),
            "Job URL": url,
            "Canonical Job ID": self.canonical_job_id(
                target["name"], url, target["ats_type"], title, location or ""),
            "Provider": target["ats_type"],
            "Extraction Method": extraction_method,
            "EU Blue Card": "Unknown",
            "Blue Card Evidence": "",
            "Relocation/Visa Support": "Unknown",
            # FIX P0-30
            "Experience Required": "Unknown",
            "Experience Min Years": "",
            "Experience Level": "Unknown",
            "Experience Source": "none",
            "Location Source": location_source if location and location not in ("Not Specified", "Unknown") else "none",
            # FIX W2-8 (parity with career_scanner): a location inferred from
            # the web host is a hint, not an observation. It is still
            # published, but it is labelled and it cannot prove scope.
            "Location Confidence": _location_confidence(
                location if location and location not in ("Not Specified", "Unknown") else "",
                location_source),
            "URL Type": "real",
            "Visa Sponsorship": "Unknown",
            "Relocation Support": "Unknown",
            "Relocation Required": "Unknown",
            "Support Confidence": 0.0,
            "Support Evidence": "",
            "Support Evidence URL": url,
            "Support Evidence Type": "none",
            "Record Status": "quarantine" if reason else "accepted",
            "Quarantine Reason": reason or "",
            "Run ID": self.run_id,
            "Scanned At": now,
        }
        # Support detection from explicit JD evidence only.
        blue, visa, reloc, reloc_req, conf, evidence, flag = self.classify_support(description, title)
        rec["EU Blue Card"] = blue
        rec["Visa Sponsorship"] = visa
        rec["Relocation Support"] = reloc
        rec["Relocation Required"] = reloc_req
        rec["Support Confidence"] = conf
        rec["Support Evidence"] = evidence
        rec["Relocation/Visa Support"] = flag
        # FIX P19b: an evidence URL with no evidence behind it is noise, and
        # the career scanner never writes one. Same rule here.
        rec["Support Evidence URL"] = url if evidence else ""
        if evidence:
            rec["Support Evidence Type"] = "explicit_jd_sentence"
        # FIX P0-30: experience from the JD text the adapter already fetched
        # (zero extra requests), with the title as fallback evidence.
        apply_experience_to_record(rec, jd_text=description or "", title=title)
        return rec

    # ── ATS adapters ─────────────────────────────────────────────────────────

    # ── FIX P0-43: board_slug was a dead column ──────────────────────────────
    # Every adapter re-derived the board identifier from the URL and ignored
    # the board_slug the seed already carried, so a vanity careers URL silently
    # pointed the API at the wrong board:
    #
    #   Virtuagym   jobs.virtuagym.com  -> host.split(".")[0] = "jobs"
    #                                      -> jobs.recruitee.com (not Virtuagym)
    #   SOTI        /Careers/jobs       -> last path segment = "jobs"
    #                                      -> .../soti/jobs (site is "Careers")
    #
    # Both returned an empty or wrong board with no error. The seed column is
    # now authoritative when it is filled in; URL sniffing remains the
    # fallback, so every existing row behaves exactly as before.
    @staticmethod
    def _seed_slug(target, part=None):
        """board_slug from the seed, or "" to fall back to URL sniffing.

        ``part`` selects a field of a compound slug: Workday rows store
        "<tenant>/<site>", so _seed_slug(target, 0) -> tenant, 1 -> site.
        """
        slug = (target.get("board_slug") or "").strip().strip("/")
        if not slug:
            return ""
        if part is None:
            return slug
        pieces = [p for p in slug.split("/") if p]
        if len(pieces) <= part:
            return ""
        return pieces[part]

    def scan_ashby(self, target):
        url = target["url"]
        parts = [p for p in urlparse(url).path.split("/") if p]
        board = self._seed_slug(target) or (parts[0] if parts else "")
        if not board:
            return []
        data = self._get_json(
            f"https://api.ashbyhq.com/posting-api/job-board/{board}"
            f"?includeCompensation=false")
        rows = []
        for job in data.get("jobs", []):
            if job.get("isListed") is False:
                continue
            locs = [self.format_location(job.get("location"))]
            for sl in job.get("secondaryLocations", []):
                locs.append(self.format_location(
                    sl if isinstance(sl, str) else (sl or {}).get("location")))
            locs = [x for x in dict.fromkeys(locs) if x and x != "Unknown"]
            location = locs[0] if locs else "Unknown"
            raw_location = " | ".join(locs)
            desc = job.get("descriptionPlain") or self._strip_html(job.get("descriptionHtml") or "")
            job_type = self.classify_job_type(
                job.get("employmentType"), job.get("workplaceType"), desc)
            row = self.make_row(
                target, job.get("title"), job.get("jobUrl") or job.get("applyUrl"),
                location, raw_location, job_type, desc, "ashby_api")
            rows.append(row)
        return rows

    def scan_greenhouse(self, target):
        url = target["url"]
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split("/") if p]
        board = ""
        if "job_board=" in parsed.query:
            board = parsed.query.split("job_board=", 1)[1].split("&", 1)[0]
        elif path_parts:
            board = path_parts[-1]
        # FIX P0-43: the seed wins. This also retires the "figma.com -> figma"
        # hard-code, which only existed because board_slug was being ignored.
        board = self._seed_slug(target) or board
        if board.lower() in {"careers", "job-openings"} or not board:
            return []
        data = self._get_json(
            f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
        rows = []
        for job in data.get("jobs", []):
            loc_obj = job.get("location") or {}
            location = self.format_location(loc_obj.get("name") or "")
            desc = self._strip_html(job.get("content") or "")
            job_type = self.classify_job_type(
                "", loc_obj.get("name") or "", desc)
            row = self.make_row(
                target, job.get("title"), job.get("absolute_url"),
                location, loc_obj.get("name") or "", job_type, desc, "greenhouse_api")
            rows.append(row)
        return rows

    def scan_lever(self, target):
        url = target["url"]
        parts = [p for p in urlparse(url).path.split("/") if p]
        board = self._seed_slug(target) or (parts[0] if parts else "")
        if not board:
            return []
        region = target.get("lever_region") or ""
        if not region and ".eu.lever.co" in urlparse(url).netloc:
            region = "eu"
        base = "api.eu.lever.co" if region == "eu" else "api.lever.co"
        data = self._get_json(f"https://{base}/v0/postings/{board}?mode=json")
        rows = []
        if not isinstance(data, list):
            return rows
        for job in data:
            cats = job.get("categories", {})
            loc_val = cats.get("location") or "; ".join(cats.get("allLocations", []))
            location = self.format_location(loc_val)
            desc = job.get("descriptionPlain") or ""
            job_type = self.classify_job_type(
                cats.get("commitment"), loc_val, desc)
            row = self.make_row(
                target, job.get("text"), job.get("hostedUrl"),
                location, loc_val, job_type, desc, "lever_api")
            rows.append(row)
        return rows

    def scan_smartrecruiters(self, target):
        url = target["url"]
        host = urlparse(url).netloc.lower()
        # SmartRecruiters' own careers site uses a legacy host with no board in the
        # URL; its public board slug is "SmartRecruiters".
        board = self._seed_slug(target)
        if not board:
            if "smartrecruiterscareers.com" in host:
                board = "SmartRecruiters"
            else:
                m = re.search(r"smartrecruiters\.com/(?:jobs/)?([^/?#]+)", url, re.I)
                if not m:
                    return []
                board = m.group(1)
        offset = 0
        rows = []
        try:
            sr_detail_budget = [int(os.environ.get(
                "SPONSORSCOUT_SMARTRECRUITERS_DETAILS", "150"))]
        except ValueError:
            sr_detail_budget = [150]
        while True:
            data = self._get_json(
                f"https://api.smartrecruiters.com/v1/companies/{board}/postings"
                f"?limit=100&offset={offset}")
            jobs = data.get("content", [])
            for job in jobs:
                loc_obj = job.get("location", {}) or {}
                loc_parts = [x for x in [
                    loc_obj.get("city"), loc_obj.get("region"), loc_obj.get("country"),
                ] if x]
                location = ", ".join(loc_parts) if loc_parts else ""
                remote = bool(loc_obj.get("remote"))
                location = self.format_location(location, "remote" if remote else "")
                title = job.get("name", "")
                slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                job_url = (f"https://jobs.smartrecruiters.com/{board}/"
                           f"{job.get('id')}-{slug}")
                # FIX W1-1c: the /postings LIST response does NOT include
                # jobAd -- verified live against DeliveryHero (971 postings,
                # no jobAd key anywhere). So this loop always produced
                # desc="" and every SmartRecruiters row reported Unknown
                # sponsorship. The per-posting endpoint carries the sections.
                desc_parts = []
                jobad = job.get("jobAd") or {}
                for sec in (jobad.get("sections") or {}).values():
                    if isinstance(sec, dict):
                        txt = sec.get("text") or sec.get("description") or ""
                        desc_parts.append(self._strip_html(txt))
                desc = " ".join(desc_parts).strip()
                if not desc and sr_detail_budget[0] > 0 and job.get("id"):
                    try:
                        full = self._get_json(
                            f"https://api.smartrecruiters.com/v1/companies/"
                            f"{board}/postings/{job.get('id')}")
                        secs = ((full or {}).get("jobAd") or {}).get("sections") or {}
                        desc = " ".join(
                            self._strip_html((secs.get(k) or {}).get("text") or "")
                            for k in ("companyDescription", "jobDescription",
                                      "qualifications", "additionalInformation")
                        ).strip()
                        if desc:
                            sr_detail_budget[0] -= 1
                    except Exception:
                        desc = ""
                job_type = self.classify_job_type(
                    (job.get("typeOfEmployment") or {}).get("label"),
                    location, desc)
                row = self.make_row(
                    target, title, job_url, location,
                    location, job_type, desc, "smartrecruiters_api")
                # FIX P0-30: SmartRecruiters publishes a structured
                # experienceLevel on the LIST response — employer-set and
                # free. It outranks any inference from title or prose.
                apply_experience_to_record(
                    row, jd_text=desc, title=title,
                    level_hint=((job.get("experienceLevel") or {})
                                .get("label") or ""))
                rows.append(row)
            offset += len(jobs)
            if not jobs or offset >= data.get("totalFound", 0):
                break
        return rows

    def scan_personio(self, target):
        import xml.etree.ElementTree as ET
        host = urlparse(target["url"]).netloc
        slug = self._seed_slug(target) or (host.split(".")[0] if host else "")
        if not slug:
            return []
        raw = self._fetch(f"https://{slug}.jobs.personio.de/xml?language=en")
        root = ET.fromstring(raw)
        rows = []
        for pos in root.findall(".//position"):
            pid = (pos.findtext("id") or "").strip()
            job_url = (pos.findtext("jobUrl") or "").strip()
            if not job_url and pid:
                job_url = f"https://{slug}.jobs.personio.de/job/{pid}"
            office = (pos.findtext("office") or "").strip()
            location = self.format_location(office)
            ctx = " | ".join(filter(None, [
                pos.findtext("department"), pos.findtext("employmentType"),
                pos.findtext("schedule"),
            ]))
            job_type = self.classify_job_type("", "", ctx)
            # Personio list API carries no JD text → support stays Unknown (honest)
            row = self.make_row(
                target, pos.findtext("name"), job_url, location, office,
                job_type, "", "personio_api")
            rows.append(row)
        return rows

    def scan_recruitee(self, target):
        host = urlparse(target["url"]).netloc
        slug = self._seed_slug(target) or (host.split(".")[0] if host else "")
        if not slug:
            return []
        data = self._get_json(f"https://{slug}.recruitee.com/api/offers/")
        rows = []
        for offer in data.get("offers", []):
            location = self.format_location(
                offer.get("location") or offer.get("city") or "")
            desc = self._strip_html(offer.get("description") or "")
            job_type = self.classify_job_type(
                offer.get("employment_type_code"), "", desc)
            job_url = offer.get("careers_url") or (
                f"https://{slug}.recruitee.com/o/{offer.get('slug')}")
            row = self.make_row(
                target, offer.get("title"), job_url, location,
                offer.get("location") or "", job_type, desc, "recruitee_api")
            rows.append(row)
        return rows

    def scan_workable(self, target):
        # apply.workable.com/<slug>/  → slug is the FIRST PATH segment
        path_seg = [p for p in urlparse(target["url"]).path.split("/") if p]
        slug = self._seed_slug(target) or (path_seg[0] if path_seg else "")
        if not slug:
            return []
        data = self._get_json(f"https://www.workable.com/api/accounts/{slug}?details=true")
        rows = []
        for job in data.get("jobs", []):
            loc_parts = [x for x in [job.get("city"), job.get("country")] if x]
            location = ", ".join(loc_parts) if loc_parts else ""
            remote = job.get("worktype") == "remote" or job.get("telecommuting")
            location = self.format_location(location, "remote" if remote else "")
            desc = self._strip_html(job.get("description") or "")
            job_type = self.classify_job_type(
                job.get("employment_type"), job.get("worktype") or "", desc)
            job_url = job.get("url") or job.get("application_url") or ""
            row = self.make_row(
                target, job.get("title"), job_url, location,
                ", ".join(loc_parts), job_type, desc, "workable_api")
            rows.append(row)
        return rows

    # ── BambooHR ─────────────────────────────────────────────────────────────
    # Added 2026-10-03. Two seed rows (Astroscale, Brain Rocket) carried
    # provider=bamboohr with no adapter behind it, so scan_target fell through
    # to the DOM fallback on a board that renders entirely from JSON -- i.e.
    # they could only ever return 0 rows.
    #
    # Two endpoints, both public, no key:
    #   /careers/list          -> {"meta":{"totalCount":N},"result":[...]}
    #   /careers/<id>/detail   -> result.jobOpening.description (HTML) and
    #                             location.addressCountry, which the LIST
    #                             response does not carry.
    # Verified live against astroscale.bamboohr.com (46 openings) on
    # 2026-10-03; brainrocket.bamboohr.com answers with totalCount 0, i.e. an
    # empty board, not an error.
    #
    # The list alone has no JD text, so without the detail pass every verdict
    # would be "Unknown" -- the same blind spot scan_personio has. The detail
    # pass is therefore ON, with a per-company budget: SPONSORSCOUT_BAMBOOHR_DETAILS
    # (default 150). Past the budget rows are still emitted, just with no
    # description, which is honest rather than silently truncating the board.
    def scan_bamboohr(self, target):
        host = urlparse(target["url"]).netloc
        slug = self._seed_slug(target) or (host.split(".")[0] if host else "")
        if not slug:
            return []
        data = self._get_json(f"https://{slug}.bamboohr.com/careers/list")
        openings = (data or {}).get("result") or []
        try:
            budget = int(os.environ.get("SPONSORSCOUT_BAMBOOHR_DETAILS", "150"))
        except (TypeError, ValueError):
            budget = 150
        rows = []
        for job in openings:
            jid = str(job.get("id") or "").strip()
            if not jid:
                continue
            job_url = f"https://{slug}.bamboohr.com/careers/{jid}"
            loc = job.get("location") or {}
            ats_loc = job.get("atsLocation") or {}
            desc = ""
            country = ""
            if budget > 0:
                budget -= 1
                try:
                    detail = self._get_json(
                        f"https://{slug}.bamboohr.com/careers/{jid}/detail")
                    opening = ((detail or {}).get("result") or {}).get("jobOpening") or {}
                    desc = self._strip_html(opening.get("description") or "")
                    d_loc = opening.get("location") or {}
                    country = clean(d_loc.get("addressCountry") or "")
                    if not loc:
                        loc = d_loc
                except Exception:
                    desc, country = "", ""
            raw_parts = [
                clean(loc.get("city") or ats_loc.get("city") or ""),
                clean(loc.get("state") or ats_loc.get("state")
                      or ats_loc.get("province") or ""),
                country or clean(ats_loc.get("country") or ""),
            ]
            raw_parts = [p for p in raw_parts if p]
            # Some employers fill BambooHR's city/state boxes the wrong way
            # round ("city: France, state: Toulouse" on the Astroscale board,
            # observed live 2026-10-03). A segment that IS a country name is
            # moved to the end so the string reads city-first like every other
            # adapter. The test is exact: "Tokyo" resolves to Japan but is not
            # the word "Japan", so it is left where it is.
            try:
                from sponsorscout.core.location_country import country_from_location as _cfl
                for _i, _p in enumerate(list(raw_parts[:-1])):
                    if (_cfl(_p) or "").strip().casefold() == _p.strip().casefold():
                        raw_parts.append(raw_parts.pop(_i))
                        break
            except Exception:
                pass
            raw_location = ", ".join(raw_parts)
            remote = bool(job.get("isRemote")) or str(job.get("locationType") or "") == "1"
            location = self.format_location(raw_location, "remote" if remote else "")
            job_type = self.classify_job_type(
                job.get("employmentStatusLabel") or job.get("employmentType") or "",
                raw_location, desc)
            rows.append(self.make_row(
                target, job.get("jobOpeningName"), job_url, location,
                raw_location, job_type, desc, "bamboohr_api"))
        return rows

    def scan_workday(self, target):
        url = target["url"]
        parsed = urlparse(url)
        host = parsed.netloc
        # tenant.wdX.myworkdayjobs.com → tenant + wdX
        # FIX P0-42: the pattern was wd\d -- a SINGLE digit. EMBL is hosted on
        # embl.wd103.myworkdayjobs.com, so the match failed and scan_workday
        # returned [] with no error at all: the seed reported 0 jobs forever
        # and looked like an empty board.
        m = re.match(r"([^.]+)\.(wd\d+)\.myworkdayjobs\.com", host, re.I)
        if not m:
            return []
        tenant, wd = m.group(1), m.group(2)
        site = (parsed.path.strip("/").split("/") or [""])[-1]
        # FIX P0-43: board_slug is "<tenant>/<site>". SOTI's URL is
        # /Careers/jobs, so the last-segment guess picked "jobs" and queried a
        # site that does not exist.
        tenant = self._seed_slug(target, 0) or tenant
        site = self._seed_slug(target, 1) or site
        if not site:
            return []
        api = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
        try:
            detail_budget = [int(os.environ.get(
                "SPONSORSCOUT_WORKDAY_DETAILS", "150"))]
        except ValueError:
            detail_budget = [150]
        rows = []
        offset = 0
        limit = 20  # Workday API rejects limit > 20 (HTTP 400)
        total = None
        while True:
            data = self._post_json(api, {
                "appliedFacets": {}, "limit": limit, "offset": offset,
                "searchText": "",
            })
            # Workday only reports `total` on the FIRST page; later pages return 0.
            if total is None:
                total = data.get("total") or 0
            jobs = data.get("jobPostings", [])
            if not jobs:
                break
            for job in jobs:
                title = job.get("title", "")
                ext = job.get("externalPath", "")
                # FIX W1-1b: the public URL is /<site><externalPath>; without
                # the site segment the link does not resolve to the posting.
                job_url = f"https://{host}/{site}{ext}" if ext else ""
                location = self.format_location(job.get("locationsText") or "")
                req = ""
                bf = job.get("bulletFields") or []
                for b in bf:
                    if re.match(r"^R\d+$", str(b)):
                        req = str(b)
                        break
                # FIX W1-1b: the Workday LIST response carries no JD, so
                # every Workday company (11 seeds: Zalando, Philips, NXP,
                # Autodesk, Leonardo, SimCorp, SOTI, Zendesk, EMBL, Prysmian,
                # Wolters Kluwer) reported Unknown for sponsorship, Blue Card
                # and relocation. The CXS detail endpoint returns the full
                # description for one extra GET; budgeted like BambooHR.
                desc = ""
                if ext and detail_budget[0] > 0:
                    try:
                        d = self._get_json(
                            f"https://{tenant}.{wd}.myworkdayjobs.com"
                            f"/wday/cxs/{tenant}/{site}{ext}")
                        info = (d or {}).get("jobPostingInfo") or {}
                        desc = self._strip_html(info.get("jobDescription") or "")
                        if desc:
                            detail_budget[0] -= 1
                    except Exception:
                        desc = ""
                job_type = self.classify_job_type(
                    "", job.get("remoteType") or "", desc)
                row = self.make_row(
                    target, title, job_url, location,
                    job.get("locationsText") or "", job_type, desc,
                    "workday_api")
                if req:
                    row["Canonical Job ID"] = (
                        f"{target['name'].casefold()}|workday|{req.casefold()}")
                rows.append(row)
            offset += len(jobs)
            if total and offset >= total:
                break
            if len(jobs) < limit:
                break
        return rows

    def browser_fallback(self, target):
        """Last-resort DOM scrape for ATS types without a public API."""
        if sync_playwright is None:
            logging.error(
                "Playwright required for %s (%s)",
                target["url"],
                _playwright_unavailable_reason(),
            )
            return []
        rows = []
        try:
            with sync_playwright() as p:
                # FIX P0-29: low-resource flags + block images/fonts/media and
                # analytics hosts. Measured -44% bytes on real job boards; the
                # DOM the extractor reads is unchanged.
                browser = p.chromium.launch(headless=True,
                                            args=LOW_RESOURCE_BROWSER_ARGS)
                ctx = browser.new_context(viewport={"width": 1280, "height": 800})
                install_page_resource_blocking(ctx)
                page = ctx.new_page()
                page.goto(target["url"], wait_until="domcontentloaded", timeout=35000)
                page.wait_for_timeout(2500)
                jobs = page.evaluate(
                    """
                    () => {
                        const out = [];
                        for (const a of document.querySelectorAll('a[href]')) {
                            const href = a.href || '';
                            if (!href.startsWith('http')) continue;
                            const card = a.closest('li, article, tr, [class*="job" i], [class*="opening" i], [class*="position" i]') || a.parentElement;
                            const heading = card && card.querySelector('h1,h2,h3,h4,[class*="title" i]');
                            const title = (a.innerText || (heading && heading.innerText) || '').replace(/\\s+/g,' ').trim();
                            if (!title || title.length < 3 || title.length > 180) continue;
                            out.push({ title, href, text: (card && card.innerText || '').replace(/\\s+/g,' ').trim() });
                        }
                        return out;
                    }
                    """
                )
                browser.close()
        except Exception as exc:
            logging.exception("Browser fallback failed for %s", target["url"])
            self._record_error(target.get("name", "?"), "browser_fallback",
                               type(exc).__name__, str(exc),
                               target.get("url", ""))
            return []
        seen = set()
        for job in jobs:
            url = clean(job.get("href"))
            if url in seen:
                continue
            seen.add(url)
            title = clean(job.get("title"))
            if not self.valid_job_url(url) or not self.valid_title(title):
                continue
            desc = clean(job.get("text"))
            row = self.make_row(
                target, title, url, "Unknown", "", self.classify_job_type("", "", desc),
                desc, "browser_fallback", location_source="none")
            rows.append(row)
        return rows

    # ── Orchestration ────────────────────────────────────────────────────────
    # ── FIX P19 (2026-10-04): iCIMS + Eightfold ─────────────────────────
    # Same two boards the career scanner now handles, same request shapes,
    # same fallbacks -- the scanners must stay interchangeable.
    def _careersite_api_hosts(self, target):
        """Hosts that may serve a career-site JSON API, strongest first."""
        host = urlparse(target.get("url") or "").netloc
        slug = self._seed_slug(target) or ""
        out = []
        for h in (slug, host):
            if h and "." in h and h not in out:
                out.append(h)
        root = ".".join(host.split(".")[-2:]) if host.count(".") >= 1 else host
        for pre in ("careers", "jobs", "career"):
            cand = f"{pre}.{root}"
            if cand not in out:
                out.append(cand)
        return out[:4]

    def scan_icims(self, target):
        """iCIMS career-site (Jibe) feed: /api/jobs?page=N&limit=100.

        Verified live on careers.docusign.com (2026-10-04): 255 postings,
        full JD text, 24 of them in Ireland.
        """
        api_host, first = "", None
        for host in self._careersite_api_hosts(target):
            try:
                data = self._get_json(
                    f"https://{host}/api/jobs?page=1&limit=100"
                    "&sortBy=relevance&internal=false")
            except Exception:
                continue
            if isinstance(data, dict) and isinstance(data.get("jobs"), list):
                api_host, first = host, data
                break
        if not api_host:
            return []

        def _rows(payload):
            rows = []
            for entry in payload.get("jobs") or []:
                d = (entry or {}).get("data") or {}
                title = clean(d.get("title") or "")
                slug = str(d.get("slug") or d.get("req_id") or "").strip()
                if not title or not slug:
                    continue
                raw_loc = clean(d.get("full_location") or "") or ", ".join(
                    x for x in (d.get("city"), d.get("state"), d.get("country")) if x)
                desc = self._strip_html(" ".join(str(d.get(k) or "") for k in (
                    "description", "qualifications", "responsibilities")))
                tags = d.get("tags2")
                tags = tags if isinstance(tags, list) else ([tags] if tags else [])
                _emp = str(d.get("employment_type") or "").strip().upper()
                _emp = {"FULL_TIME": "Full-time", "PART_TIME": "Part-time",
                        "INTERN": "Internship", "INTERNSHIP": "Internship",
                        "CONTRACTOR": "Contract", "CONTRACT": "Contract",
                        "TEMPORARY": "Contract", "TEMP": "Contract",
                        "VOLUNTEER": "Volunteer", "PER_DIEM": "Contract",
                        "OTHER": ""}.get(_emp, _emp.replace("_", " ").title())
                # FIX P19b: classify from the employer's OWN fields, never
                # from the JD prose -- the career scanner does the same, and
                # feeding the body in made the two disagree on these rows.
                job_type = self.classify_job_type(
                    _emp, " ".join([raw_loc] + [str(t) for t in tags]), "")
                rows.append(self.make_row(
                    target, title,
                    f"https://{api_host}/jobs/{slug}?lang=en-us",
                    self.format_location(raw_loc), raw_loc, job_type, desc,
                    "icims_api"))
            return rows

        rows = _rows(first)
        try:
            total = int(first.get("totalCount") or first.get("count") or len(rows))
        except Exception:
            total = len(rows)
        page = 2
        while len(rows) < min(total, 2000) and page <= 25:
            if check_control(self.cancel_event, self.pause_event):
                break
            try:
                batch = _rows(self._get_json(
                    f"https://{api_host}/api/jobs?page={page}&limit=100"
                    "&sortBy=relevance&internal=false"))
            except Exception:
                break
            if not batch:
                break
            rows.extend(batch)
            page += 1
        return rows

    def scan_eightfold(self, target):
        """Eightfold.ai feed: /api/apply/v2/jobs?domain=<domain>&start=&num=.

        NOT verified against a live tenant (no Eightfold board was reachable
        from the build machine), so every unexpected shape returns [] and
        scan_target falls back to the DOM crawl used today.
        """
        host = (self._seed_slug(target)
                or urlparse(target.get("url") or "").netloc)
        if not host or "." not in host:
            return []
        parts = host.split(".")
        domain = ".".join(parts[-2:]) if len(parts) >= 2 else host
        rows, start, total = [], 0, None
        while start < 2000:
            if check_control(self.cancel_event, self.pause_event):
                break
            try:
                data = self._get_json(
                    f"https://{host}/api/apply/v2/jobs?domain={domain}"
                    f"&start={start}&num=50&exclude_pills=true")
            except Exception:
                break
            if not isinstance(data, dict):
                break
            if str(data.get("status") or "").lower() == "failure":
                break
            positions = data.get("positions")
            if not isinstance(positions, list) or not positions:
                break
            if total is None:
                try:
                    total = int(data.get("count") or 0)
                except Exception:
                    total = 0
            for it in positions:
                if not isinstance(it, dict):
                    continue
                title = clean(it.get("name") or it.get("title") or "")
                if not title:
                    continue
                loc = it.get("location")
                if not loc:
                    locs = it.get("locations")
                    loc = (locs[0] if isinstance(locs, list) and locs else "")
                raw_loc = clean(str(loc or ""))
                url = str(it.get("canonicalPositionUrl") or "").strip()
                if not url:
                    pid = str(it.get("id") or it.get("display_job_id") or "").strip()
                    if not pid:
                        continue
                    url = f"https://{host}/careers/job/{pid}"
                desc = self._strip_html(str(it.get("job_description") or ""))
                job_type = self.classify_job_type(
                    it.get("type") or "", raw_loc, desc)
                rows.append(self.make_row(
                    target, title, url, self.format_location(raw_loc),
                    raw_loc, job_type, desc, "eightfold_api"))
            if len(positions) < 50:
                break
            start += 50
        return rows

    def scan_target(self, target):
        adapters = {
            "ashby": self.scan_ashby,
            "greenhouse": self.scan_greenhouse,
            "lever": self.scan_lever,
            "smartrecruiters": self.scan_smartrecruiters,
            "personio": self.scan_personio,
            "recruitee": self.scan_recruitee,
            "workable": self.scan_workable,
            "workday": self.scan_workday,
            "bamboohr": self.scan_bamboohr,
            "icims": self.scan_icims,            # FIX P19
            "eightfold": self.scan_eightfold,    # FIX P19
        }
        adapter = adapters.get(target["ats_type"])
        if adapter is None:
            # FIX P0-31: try the static-HTML fast path before paying for a
            # Chromium launch. Uses this scanner's OWN validators so the
            # acceptance rules are identical; bails out on hydration shells
            # and on thin yields so a JS board is never truncated.
            try:
                static_jobs, diag = fetch_static_jobs(
                    target["url"],
                    url_validator=self.valid_job_url,
                    title_validator=self.valid_title,
                )
            except Exception:
                static_jobs, diag = [], "static: error"
            if static_jobs:
                print(f"   {diag} (no browser needed)")
                rows = []
                for job in static_jobs:
                    rows.append(self.make_row(
                        target, job["job_title"], job["job_url"],
                        "Unknown", "", self.classify_job_type("", "", ""),
                        "", "static_html", location_source="none"))
                return rows
            # Generic / unknown board (e.g. provider=auto on a plain careers
            # site with no ATS signature): the DOM fallback still harvests
            # visible job links instead of silently returning 0 and losing
            # that company's jobs.
            print(f"   {diag}; no API adapter for '{target['ats_type']}' "
                  f"— using browser DOM fallback")
            return self.browser_fallback(target)
        return adapter(target)

    def _record_error(self, seed_name, phase, err_type, message, seed_url=""):
        """Append one row to the run's errors CSV (immediate, crash-safe)."""
        path = getattr(self, "_errors_csv", None)
        if not path:
            return
        try:
            with open(path, "a", encoding="utf-8", newline="") as f:
                csv.DictWriter(f, fieldnames=ERROR_FIELDS).writerow({
                    "Run ID": self.run_id,
                    "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "Seed Name": seed_name, "Phase": phase,
                    "Error Type": err_type, "Message": (message or "")[:2000],
                    "Seed URL": seed_url,
                })
        except Exception:
            pass  # error logging must never crash a scan

    def _ensure_output_header(self, columns, path):
        """Write the header if the file is missing/empty; otherwise verify the
        existing header matches exactly (never leave output headerless)."""
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            with open(path, "w", encoding="utf-8", newline="") as f:
                csv.DictWriter(f, fieldnames=columns).writeheader()
            return
        with open(path, encoding="utf-8") as f:
            first = next(csv.reader(f), None)
        if first != list(columns):
            raise ValueError(
                f"Output schema mismatch for {path}. "
                f"Expected {list(columns)!r}, found {first!r}. "
                "Use a fresh --output path or migrate the file."
            )

    def run(self):
        import os
        targets = self.read_seed_file()
        # Optional whitelist (Dashboard "Rescan Companies"): keep only the
        # requested companies so a targeted rescan stays fast.  Same semantics
        # as the career scanner's only_companies handling.
        if self.only_companies:
            wanted = {c.strip().lower() for c in self.only_companies if c and c.strip()}
            targets = [t for t in targets if str(t.get("name", "")).strip().lower() in wanted]
            if not targets:
                print(f"no ATS targets matched only_companies={self.only_companies}")
                return
        if not targets:
            print("ATS scan: seed file contains no enabled targets")
            return
        self._preflight_connectivity()

        base = self.output_file[:-4] if self.output_file.lower().endswith(".csv") else self.output_file
        recruiter_csv = base + "_recruiter.csv"
        quarantine_csv = base + "_quarantine.csv"
        scan_log_csv = base + "_scan_log.csv"
        errors_csv = base + "_errors.csv"
        self._errors_csv = errors_csv

        # Fresh run: truncate + write headers. Resume: load existing canonical
        # IDs so only NEW requisitions are appended, and verify (or write) the
        # header so output files are never left headerless.
        if not self.resume:
            for path in (self.output_file, recruiter_csv, quarantine_csv):
                with open(path, "w", encoding="utf-8", newline="") as f:
                    csv.DictWriter(f, fieldnames=OUTPUT_FIELDS).writeheader()
            with open(scan_log_csv, "w", encoding="utf-8", newline="") as f:
                csv.DictWriter(f, fieldnames=LOG_FIELDS).writeheader()
            with open(errors_csv, "w", encoding="utf-8", newline="") as f:
                csv.DictWriter(f, fieldnames=ERROR_FIELDS).writeheader()
            seen_ids = set()
        else:
            for path in (self.output_file, recruiter_csv, quarantine_csv):
                self._ensure_output_header(OUTPUT_FIELDS, path)
            self._ensure_output_header(LOG_FIELDS, scan_log_csv)
            self._ensure_output_header(ERROR_FIELDS, errors_csv)
            # Load existing canonical IDs so resume dedupes against prior runs.
            seen_ids = set()
            for path in (self.output_file, recruiter_csv):
                if not os.path.exists(path):
                    continue
                with open(path, encoding="utf-8") as f:
                    reader = csv.reader(f)
                    next(reader, None)  # skip header
                    for row in reader:
                        if len(row) >= 18 and row[16]:
                            seen_ids.add(row[16])

        print(f"ATS scan started: {len(targets)} targets; run_id={self.run_id}; resume={self.resume}")

        for idx, target in enumerate(targets, 1):
            # Cooperative cancellation (desktop Stop button): stop between
            # targets so an in-flight HTTP/browser request can finish cleanly.
            if check_control(self.cancel_event, self.pause_event):
                print(f"   CANCELLED: stopping before target [{idx}] {target.get('name', '?')}")
                break
            started = time.monotonic()
            print(f"\n[{idx}/{len(targets)}] {target['name']} ({target['ats_type']})")
            error = ""
            diagnostics = []
            if target["name"] in KNOWN_BOARD_ISSUES:
                diagnostics.append(KNOWN_BOARD_ISSUES[target["name"]])
            err_type = err_msg = ""
            try:
                result = self.scan_target(target)
            except Exception as exc:
                result = []
                err_type, err_msg = type(exc).__name__, str(exc)
                error = f"{err_type}: {err_msg}"
                diagnostics.append(error)
            accepted = []
            quarantined = []
            duplicates = 0
            for row in result:
                cid = row["Canonical Job ID"]
                if row["Record Status"] == "quarantine":
                    quarantined.append(row)
                    continue
                if cid in seen_ids:
                    duplicates += 1
                    continue
                seen_ids.add(cid)
                accepted.append(row)
            dest = recruiter_csv if target["source_type"] == "recruiter" else self.output_file
            with open(dest, "a", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
                for r in accepted:
                    w.writerow({k: r.get(k, "") for k in OUTPUT_FIELDS})
            with open(quarantine_csv, "a", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
                for r in quarantined:
                    w.writerow({k: r.get(k, "") for k in OUTPUT_FIELDS})
            status = "error" if error and not accepted else ("ok" if accepted else "empty")
            with open(scan_log_csv, "a", encoding="utf-8", newline="") as f:
                csv.DictWriter(f, fieldnames=LOG_FIELDS).writerow({
                    "Run ID": self.run_id, "Seed Name": target["name"],
                    "Company": target["name"], "Source Type": target["source_type"],
                    "Target Country": target.get("target_country", "Global"), "Status": status,
                    "Provider": target["ats_type"], "Jobs Found": len(accepted),
                    "Quarantined": len(quarantined), "Duplicates": duplicates,
                    # FIX P0-40: was hard-coded 0 because nothing enforced scope.
                    "Rejected Scope": sum(
                        1 for _r in quarantined
                        if _r.get("Quarantine Reason")
                        == "outside_or_unproven_target_country"),
                    "Error": error,
                    "Diagnostics": " | ".join(diagnostics)[-4000:],
                    "Duration Sec": round(time.monotonic() - started, 1),
                    "Seed URL": target["url"],
                })
            if error:
                self._record_error(target["name"], "target", err_type, err_msg,
                                   target["url"])
            print(f"   {status.upper()}: wrote={len(accepted)}, quarantined={len(quarantined)}, dups={duplicates}")

        # FIX P0-52: make the false-positive guards auditable. If the
        # sponsored-jobs count looks low, this says whether the guards took
        # the verdicts or the JDs simply never offered sponsorship.
        if SUPPORT_GUARD_HITS:
            _tot = sum(SUPPORT_GUARD_HITS.values())
            print(f"  Support FP guards downgraded {_tot} 'Yes' verdict(s):")
            for _g, _n in sorted(SUPPORT_GUARD_HITS.items()):
                print(f"     {_g}: {_n}")
        else:
            print("  Support FP guards downgraded 0 verdicts "
                  "(a low sponsored count is the JDs, not the guards).")
        # W3-3 (P7) parity: an aggregator copy of an employer's own posting
        # must not sit in both result files.
        try:
            _dupes, _ = self._dedupe_cross_bucket(
                self.output_file, recruiter_csv, quarantine_csv, OUTPUT_FIELDS)
            if _dupes:
                print(f"  Cross-source duplicates moved to quarantine: {_dupes}")
        except Exception as _dexc:
            print(f"  [warn] cross-source dedupe skipped: "
                  f"{type(_dexc).__name__}: {_dexc}")
        print(f"\nATS scan complete. Outputs:\n  Direct: {self.output_file}\n"
              f"  Recruiters: {recruiter_csv}\n  Quarantine: {quarantine_csv}\n  Log: {scan_log_csv}\n"
              f"  Errors: {errors_csv}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ATS Career Portal Scanner v5")
    parser.add_argument("--input", default="company_ATS_seed.csv")
    parser.add_argument("--output", default=None,
                        help="Default: scraped_ats_jobs_v5.csv")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skip-preflight", action="store_true")
    args = parser.parse_args()
    scanner = ATSScanner(
        seed_file=args.input,
        output_file=args.output or "scraped_ats_jobs_v5.csv",
        skip_preflight=args.skip_preflight,
        resume=args.resume,
    )
    scanner.run()
