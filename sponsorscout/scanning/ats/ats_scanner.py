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
import socket
import time
import unicodedata
from collections import Counter
from html import unescape
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse, urljoin
from urllib.request import Request, urlopen
import urllib.request

try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:
    sync_playwright = None

# Real-time logging.  When a progress callback is installed (desktop app) all
# output lines are routed to it (and therefore into the Tools tab scan log);
# otherwise they print to stdout as before.
import builtins as _builtins

progress_cb = None


def _notify(msg):
    if progress_cb:
        progress_cb(msg)
    else:
        _builtins.print(msg, flush=True)


def print(*args, **kwargs):
    _notify(" ".join(str(a) for a in args))


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
    filename=_log_file_path(),
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
    r"hands[-\s]?on|proven|track record|background in|working (?:in|with|as)|"
    r"seniority|vergleichbarer? (?:position|rolle)|einschl[äa]gig|"
    r"ruolo simile|ambito|settore|soortgelijke|vergelijkbare|"
    r"poste similaire|puesto similar|en el (?:sector|[áa]rea)",
    re.I)

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
    r"universit|school|apprenticeship duration|"
    r"notice period|k[üu]ndigungsfrist|preavviso)\b", re.I)

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
    """Return the strongest explicit experience statement, or None."""
    best = None
    for sent in _EXP_SENT_SPLIT.split(text or ""):
        sent = (sent or "").strip()
        if not sent or len(sent) > 600:
            continue
        if not _EXP_ANCHOR.search(sent):
            continue
        num = rf"(?:\d{{1,2}}|{_EXP_WORD_ALT})"
        unit = rf"(?:{_EXP_YEAR_UNIT}|{_EXP_MONTH_UNIT})"
        rx = re.compile(
            rf"(?<![\w.,/-])({num})\s*(?:\+|plus)?\s*"
            rf"(?:(?:-|–|—|\bto\b|\bbis\b|\ba\b|\btot\b|\b[àa]\b|\bund\b|"
            rf"\be\b|\by\b|\bet\b|\bor\b|\bof\b)\s*"
            rf"({num})\s*(?:\+|plus)?\s*)?"
            rf"({unit})\b", re.I)
        for m in rx.finditer(sent):
            lo, hi = _exp_num(m.group(1)), _exp_num(m.group(2))
            u = m.group(3).lower()
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
            plus = bool(re.search(
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
            if best is None or lo_y < best[0]:
                best = cand
    return best


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
    return out


def apply_experience_to_record(rec, jd_text="", card_context="", title="",
                               level_hint="", key_prefix="Experience "):
    """Fill the four Experience columns on a record, respecting source rank.

    Never downgrades: a weaker source cannot overwrite a stronger one.
    `level_hint` is an employer-published seniority string (e.g.
    SmartRecruiters experienceLevel) and outranks every inference.
    """
    jd = (jd_text or "").strip()
    exp = extract_experience(jd or card_context or "", title or "")
    src = exp["source"]
    if src == "detail_text":
        src = "api_description" if jd else "card_context"

    hint = (level_hint or "").strip()
    if hint:
        mapped = _EXP_ATS_LEVEL.get(hint.lower())
        if mapped:
            exp["level"] = mapped
            if exp["required"] == "Unknown":
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
        try:
            import shutil  # noqa: F401  (presence check only)
            page = os.sysconf("SC_AVPHYS_PAGES")
            size = os.sysconf("SC_PAGE_SIZE")
            avail = (page * size) / (1024.0 * 1024.0)
        except Exception:
            avail = None
    return avail


# Measured peak RSS of one headless Chromium with a real job board loaded.
_BROWSER_RSS_MB = 600
# Leave this much for the OS, the user's other apps, and this process.
_RESERVE_MB = 1200


def recommended_workers(kind="browser", requested=None):
    """Size a worker pool for THIS machine.

    kind="browser" -> each worker may hold a Chromium (~600 MB measured)
    kind="http"    -> each worker is a socket + parser (cheap)

    The previous code called this function without ever defining it. Beyond
    fixing the NameError, the point is that a fixed pool of 3 is wrong on an
    8 GB laptop: 3 x 600 MB of Chromium plus the OS is a swap storm.
    """
    cpus = _host_cpu_count()
    free = _host_free_mb()

    if kind == "http":
        # Network-bound: oversubscribe CPUs, but stay modest on small boxes.
        cap = 12 if cpus >= 4 else 6
        n = min(cap, max(2, cpus * 3))
        if free is not None and free < 1500:
            n = min(n, 3)
        return n if requested is None else max(1, min(requested, n))

    # Browser work: RAM is the binding constraint, not CPU.
    by_cpu = max(1, cpus - 1) if cpus > 1 else 1
    if free is None:
        by_ram = 2
    else:
        by_ram = int(max(0, free - _RESERVE_MB) // _BROWSER_RSS_MB)
    n = max(1, min(by_cpu, by_ram if by_ram > 0 else 1))
    n = min(n, 4)  # diminishing returns; also politeness to target sites
    if requested is not None:
        n = max(1, min(requested, n))
    return n


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
OUTPUT_FIELDS = [
    "Company Name", "Seed Name", "Source Type", "Hiring Company",
    "Target Country", "Scope Policy", "Industry Type",
    "Sponsorship History Score", "English Friendly Score", "Remote Score",
    "Job Title", "Raw Job Title", "Job Location", "Raw Location", "Job Type",
    "Job URL", "Canonical Job ID", "Provider", "Extraction Method",
    "EU Blue Card", "Blue Card Evidence", "Relocation/Visa Support",
    # FIX P0-30
    "Experience Required", "Experience Min Years", "Experience Level",
    "Experience Source",
    "Location Source", "URL Type", "Visa Sponsorship", "Relocation Support",
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
# Single source of truth: ``sponsorscout.scanning.jd_support`` (shared with the
# career scanner).  The former in-file copy of this class was verified
# byte-identical and removed so the two scanners can never drift apart —
# changing visa/relocation detection is now a one-file edit.
#
# Dual-mode bootstrap: allow ``python ats_scanner.py`` to run as a loose script
# from the repo, where the package root is not on sys.path yet.
try:
    from sponsorscout.scanning.jd_support import (
        JDSupportDetector,
        VERDICT_NO,
        VERDICT_UNKNOWN,
        VERDICT_YES,
        detect_blue_card,
    )
except ModuleNotFoundError:  # standalone single-file mode
    import os as _os
    import sys as _sys

    _here = _os.path.dirname(_os.path.abspath(__file__))
    for _i in range(3):  # ats/ -> scanning/ -> sponsorscout/ -> repo root
        _here = _os.path.dirname(_here)
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    from sponsorscout.scanning.jd_support import (
        JDSupportDetector,
        VERDICT_NO,
        VERDICT_UNKNOWN,
        VERDICT_YES,
        detect_blue_card,
    )


class ATSScanner:
    def __init__(self, seed_file="company_ATS_seed.csv",
                 output_file="scraped_ats_jobs_v5.csv",
                 skip_preflight=False, resume=False,
                 cancel_event=None, only_companies=None):
        self.seed_file = seed_file
        self.output_file = output_file
        self.skip_preflight = skip_preflight
        self.resume = resume
        # Cooperative cancellation for the desktop UI Stop button: checked
        # before each target; the in-flight target is allowed to finish.
        self.cancel_event = cancel_event
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
        with open(self.seed_file, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            reader.fieldnames = [clean(x) for x in (reader.fieldnames or [])]
            for line_no, row in enumerate(reader, 2):
                name = clean(row.get("name"))
                # Schema support: the legacy v6 seed carries ``ats_type``
                # directly; the v7 seed (shared with the career scanner)
                # carries ``provider`` (+ optional ``board_slug``).  Accept
                # both, and resolve ``auto`` / blank providers from the URL.
                ats_type = (clean(row.get("ats_type"))
                            or clean(row.get("provider"))).lower()
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
        if errors:
            raise ValueError("Seed validation failed:\n - " + "\n - ".join(errors))
        return records

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
        location = parts[0] if parts else ""
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
        text = clean(f"{raw} {workplace} {description}").lower()
        job_kind = "Unknown"
        if re.search(r"\bintern(ship)?\b|praktikum|tirocinio|trainee|apprentic|stage\b", text):
            job_kind = "Internship"
        elif re.search(r"part[ -]?time|teilzeit|deeltijd", text):
            job_kind = "Part-time"
        elif re.search(r"\bfull[ -]?time|fulltime|vollzeit|voltijd|permanent|contract|fixed[ -]?term|temporary", text):
            job_kind = "Full-time"
        mode = "Unknown"
        if re.search(r"\bremote\b|work from home|home office|100% remote|fully remote", text):
            mode = "Remote"
        elif re.search(r"\bhybrid\b|smart working|smartworking", text):
            mode = "Hybrid"
        elif re.search(r"\bon[- ]?site|onsite|in[- ]?office|in office", text):
            mode = "On-site"
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
        # ── False-positive guards (a mention is only "support offered to YOU" if
        # it isn't the job FUNCTION, an event/travel arrangement, or brand
        # sponsorship) ─────────────────────────────────────────────────────────
        # (1) "sponsorship" alone is ambiguous (event/brand/partnership).
        #     A visa "Yes" needs an actual visa/immigration/work-authorization
        #     keyword present, not merely "sponsor a session / trade show".
        if visa == VERDICT_YES:
            if not re.search(
                r"visa|work permit|work authori[sz]ation|immigration|h-?1b|"
                r"blue card|carta blu|blaue karte|blauwe kaart|carte bleue|"
                r"tarjeta azul|skilled (migrant|worker)|aufenthaltstitel|"
                r"arbeitserlaubnis|permesso di soggiorno|permis de travail|"
                r"werkvergunning|arbeidsvergunning|permiso de trabajo",
                text, re.I,
            ):
                visa = VERDICT_UNKNOWN
        # (2) event/trade-show/partner sponsorship is not visa sponsorship.
        if re.search(
            r"sponsor\w*.{0,50}\b(event|conference|trade[- ]show|booth|"
            r"session|co[- ]market|partner|speaker)\b",
            text, re.I,
        ) or re.search(
            r"\b(event|conference|trade[- ]show|booth|session|co[- ]market|"
            r"partner)\w*.{0,50}sponsor\w*",
            text, re.I,
        ):
            visa = VERDICT_UNKNOWN if visa == VERDICT_YES else visa
        # (3) "visas for international events / speakers / travel" = arranging
        #     travel documents, not sponsoring the candidate.
        if re.search(
            r"\b(visas?|work permits?)\s+for\s+(international\s+events?|"
            r"speakers?|travel|attendees?)",
            text, re.I,
        ):
            visa = VERDICT_UNKNOWN if visa == VERDICT_YES else visa
        # (4) job FUNCTION: the role administers mobility/immigration/relocation
        #     for OTHERS (its title says so). These are not candidate benefits.
        t = clean(title or "").lower()
        if re.search(
            r"\b(?:global|international)\s+mobility\b|\bimmigration\b|\brelocation\b",
            t,
        ) and re.search(
            r"\b(manager|specialist|coordinator|officer|lead|director|program|"
            r"administrator|consultant|partner|hr)\b",
            t,
        ):
            visa = VERDICT_UNKNOWN if visa == VERDICT_YES else visa
            reloc = VERDICT_UNKNOWN if reloc == VERDICT_YES else reloc
        # (5) duty-frame: "tracking of work permits" / "manage visa applications"
        #     describes work the HIRE performs for others, not a benefit. Downgrade
        #     unless the JD ALSO makes a candidate-facing offer ("sponsor your
        #     visa" / "we will sponsor your visa"). A bare "we offer" or
        #     "benefits include" elsewhere in the JD is NOT enough.
        if visa == VERDICT_YES and re.search(
            r"\b(track(?:ing)?|manag(?:e|ing)|oversee(?:ing)?|administer(?:ing)?|"
            r"process(?:ing)?|handle(?:ing)?|coordinat(?:e|ing))\s+(?:of\s+)?"
            r"(work\s+permits?|visas?|immigration\s+cases?)\b",
            text, re.I,
        ):
            candidate_offer = re.search(
                r"\b(your|you)\b.{0,25}\b(visa|work\s+permit)\b"
                r"|\b(sponsor\w*|cover\w*|pay\s+for)\s+(your|the)\s+(visa|work\s+permit)"
                r"|\bwe\b.{0,30}\b(sponsor\w*)\b.{0,30}\b(visa|work\s+permit)\b",
                text, re.I,
            )
            if not candidate_offer:
                visa = VERDICT_UNKNOWN
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

    def valid_title(self, title):
        title = clean(title)
        low = title.lower()
        if not title or len(title) > 180:
            return False
        # G1 fix: location stubs used as titles ("Milano, MI", "Wetzlar, DE").
        # make_row() quarantines invalid titles with a clear reason.
        if self._title_is_pure_location(title):
            return False
        # allow short CJK titles (e.g. 2-char "电工" = electrician); Latin titles
        # under 3 chars ("IT", "HR", "QA") are never real job titles
        if len(title) < 3 and not re.search(
                r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", title):
            return False
        if low in BAD_TITLES:
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
    def make_row(self, target, title, url, location, raw_location, job_type,
                 description, extraction_method, location_source="api"):
        title = clean(title)
        url = clean(url)
        reason = None
        if not self.valid_job_url(url):
            reason = "invalid_or_application_only_url"
        elif not self.valid_title(title):
            reason = "invalid_generic_or_department_title"
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
            "Job Location": location if location and location != "Not Specified" else "Unknown",
            "Raw Location": raw_location or "",
            "Job Type": job_type,
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
        if evidence:
            rec["Support Evidence Type"] = "explicit_jd_sentence"
        # FIX P0-30: experience from the JD text the adapter already fetched
        # (zero extra requests), with the title as fallback evidence.
        apply_experience_to_record(rec, jd_text=description or "", title=title)
        return rec

    # ── ATS adapters ─────────────────────────────────────────────────────────
    def scan_ashby(self, target):
        url = target["url"]
        parts = [p for p in urlparse(url).path.split("/") if p]
        if not parts:
            return []
        board = parts[0]
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
        if "figma.com" in urlparse(url).netloc:
            board = "figma"
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
        if not parts:
            return []
        board = parts[0]
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
        if "smartrecruiterscareers.com" in host:
            board = "SmartRecruiters"
        else:
            m = re.search(r"smartrecruiters\.com/(?:jobs/)?([^/?#]+)", url, re.I)
            if not m:
                return []
            board = m.group(1)
        offset = 0
        rows = []
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
                # gather JD text from jobAd sections for support detection
                desc_parts = []
                jobad = job.get("jobAd") or {}
                for sec in (jobad.get("sections") or {}).values():
                    if isinstance(sec, dict):
                        txt = sec.get("text") or sec.get("description") or ""
                        desc_parts.append(self._strip_html(txt))
                desc = " ".join(desc_parts)
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
        slug = host.split(".")[0] if host else ""
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
        slug = host.split(".")[0] if host else ""
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
        slug = path_seg[0] if path_seg else ""
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

    def scan_workday(self, target):
        url = target["url"]
        parsed = urlparse(url)
        host = parsed.netloc
        # tenant.wdX.myworkdayjobs.com → tenant + wdX
        m = re.match(r"([^.]+)\.(wd\d)\.myworkdayjobs\.com", host, re.I)
        if not m:
            return []
        tenant, wd = m.group(1), m.group(2)
        site = (parsed.path.strip("/").split("/") or [""])[-1]
        if not site:
            return []
        api = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
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
                job_url = f"https://{host}{ext}" if ext else ""
                location = self.format_location(job.get("locationsText") or "")
                req = ""
                bf = job.get("bulletFields") or []
                for b in bf:
                    if re.match(r"^R\d+$", str(b)):
                        req = str(b)
                        break
                # Workday list API has no JD text → support Unknown; use remoteType
                job_type = self.classify_job_type("", job.get("remoteType") or "", "")
                row = self.make_row(
                    target, title, job_url, location,
                    job.get("locationsText") or "", job_type, "", "workday_api")
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
            logging.error("Playwright required for %s", target["url"])
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
            if self.cancel_event is not None and self.cancel_event.is_set():
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
                    "Rejected Scope": 0, "Error": error,
                    "Diagnostics": " | ".join(diagnostics)[-4000:],
                    "Duration Sec": round(time.monotonic() - started, 1),
                    "Seed URL": target["url"],
                })
            if error:
                self._record_error(target["name"], "target", err_type, err_msg,
                                   target["url"])
            print(f"   {status.upper()}: wrote={len(accepted)}, quarantined={len(quarantined)}, dups={duplicates}")

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
