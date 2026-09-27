import os
import re
import csv
import time
import socket
import collections
import unicodedata
import urllib.parse
import urllib.request
import json
from html import unescape  # Used by _jd_plain / _static_strip_tags / fetch_static_jobs.
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    PLAYWRIGHT_IMPORT_ERROR = None
except ModuleNotFoundError as _pw_exc:  # Provider-API-only runs can still work without a browser.
    sync_playwright = None
    # Keep the reason. A bare None made every DOM target fail with an opaque
    # "Playwright is required" banner and hid WHICH module was missing from the
    # frozen bundle (playwright itself, greenlet or pyee).
    PLAYWRIGHT_IMPORT_ERROR = f"{type(_pw_exc).__name__}: {_pw_exc}"

    class PlaywrightTimeoutError(Exception):
        pass


def _playwright_unavailable_reason():
    """Why sync_playwright is unusable — surfaced in errors and diagnostics."""
    return PLAYWRIGHT_IMPORT_ERROR or "playwright.sync_api could not be imported"

# Real-time logging. When a progress callback is installed (desktop app) all
# output lines are routed to it; otherwise they print to stdout as before.
import builtins as _builtins

# Dual-mode bootstrap: when launched as a loose script (python career_scanner.py)
# the repo root is NOT on sys.path, so add it here — BEFORE the first
# `sponsorscout.*` import below. No-op when imported as a package module.
if __name__ == "__main__":
    import sys as _sys
    _here = os.path.dirname(os.path.abspath(__file__))
    for _i in range(3):  # career/ -> scanning/ -> sponsorscout/ -> repo root
        _here = os.path.dirname(_here)
    if _here not in _sys.path:
        _sys.path.insert(0, _here)

progress_cb = None


def _notify(msg):
    if progress_cb:
        progress_cb(msg)
    else:
        _builtins.print(msg, flush=True)


def print(*args, **kwargs):
    _notify(" ".join(str(a) for a in args))
# ───────────────────────── JS HELPERS ──────────────────────────
# Upgraded with high-precision regex matching and robust card scoping to prevent 
# matching large layout containers and extracting generic page texts.
JS_HELPERS = r"""
const querySelectorAllDeep = (selector, root = document) => {
    const out = [];
    const walk = (node) => {
        if (!node) return;
        if (node.nodeType === 1) {
            try {
                if (node.matches && node.matches(selector)) out.push(node);
            } catch(e) {}
            if (node.shadowRoot) {
                walk(node.shadowRoot);
            }
        }
        if (node.childNodes) {
            for (const child of node.childNodes) walk(child);
        }
    };
    walk(root);
    return out;
};
const cleanText = (s) => String(s || '').replace(/\s+/g, ' ').trim();
const getClassName = (el) => {
    if (!el) return '';
    const cls = el.className;
    if (typeof cls === 'string') return cls;
    if (cls && typeof cls === 'object' && 'baseVal' in cls) return cls.baseVal || '';
    return String(cls || '');
};
const isVisible = (el) => {
    if (!el) return false;
    try {
        const r = el.getBoundingClientRect();
        const st = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 &&
               st.display !== 'none' &&
               st.visibility !== 'hidden' &&
               st.opacity !== '0';
    } catch(e) {
        return false;
    }
};
const badScopeSelector = [
    'header', 'nav', 'footer',
    '[role="banner"]', '[role="contentinfo"]',
    '.site-header', '.site-footer',
    '.navbar', '.navigation', '.main-nav', '.primary-nav',
    '.cookie-banner', '.cookie-consent', '.cookie-notice',
    '[class*="CookieBanner"]', '[class*="cookie-bar"]',
    '[id*="cookie-notice"]'
].join(',');
const isBadScope = (el) => {
    try {
        return !!el.closest(badScopeSelector);
    } catch(e) {
        return false;
    }
};
// Expanded to match custom-hosted ATS directories like Catawiki's /o/ and Deliveroo's /role/
// H1 (2026-09-13): Italian detail paths — /offerte* (Carrefour /offertedilavoro/<id>,
// Action /offerte-di-lavoro/<slug>), /work-with-us/<slug> (Piazza), /carriere/<slug> (Yamamay).
const jobUrlRe = /(\/job(s)?\/[^\/?#]+|\/career(s)?\/(?!disciplines?\/|departments?\/|teams?\/|locations?\/|offices?\/|categor(y|ies)\/|areas?\/|functions?\/)[^/?#]+\/[^\/?#]+|\/career(s)?\/(?!benefits?\b|belonging\b|culture\b|values\b|story\b|people\b|team(s)?\b|location(s)?\b|office(s)?\b|student(s)?\b|discipline(s)?\/?|department(s)?\/?|our-story\b|interview-tips\b|recruitment-process\b|about\b|about-us\b|compatibility\b|emerging-talent\b|home\b|feed\b|search\b|all-jobs\b|overview\b|life\b|life-at-|why-|how-we-hire\b|hiring-process\b|faq\b|diversity\b|inclusion\b|blog\b|news\b|event(s)?\b|program(s)?\b|internship(s)?\b)[^\/?#]{4,}|\/o\/[a-zA-Z0-9-]+|\/role\/[a-zA-Z0-9-]+|\/position|\/vacancy|\/vacancies|\/opening|\/role|\/requisition|\/posting|\/apply|\/stellenangebot|\/stelle|\/lavoro|\/posizioni|\/annuncio|\/offerta|\/offerte|\/work-with-us\/[^\\/?#]+|\/carriere\/[^\\/?#]+|\/opportunit|\/annunci\/|\/offre-de-emploi\/|\/ofertas\/|intervieweb|arca24|inrecruiting|altamiraweb|detail|jobid|job_id|gh_jid|reqid|requisition|posting|lever\.co|greenhouse\.io|personio|workable|smartrecruiters|teamtailor|ashby|workdayjobs|successfactors|phenompeople|eightfold|deel\.com\/job-boards)/i;
// FIXED: Uses leading slashes and word boundaries for path keywords (like /about, /press)
// to prevent matching entire domain names like aboutyou.de or americanexpress.com!
const badUrlRe = /(\/(privacy|cookie|terms|legal|about|contact|history|press|investor|culture|benefit|login|signup|help|blog|pricing|faq|values|diversity|inclusion|mission|story|leadership|impact|journey|how-we-hire|talent-community|talent-network|job-alert|subscribe|download|upload|notify)\b|facebook|linkedin|twitter|instagram|youtube|support\.google|play\.google|apps\.apple|mailto:|tel:)/i;
const genericTextRe = /^(apply|apply now|view|view job|view role|view position|read more|details|click here|learn more|more info|more information|maggiori informazioni|mehr informationen|meer informatie|load more|show more|see more|next|previous|back|home|jobs|careers|search|filter|sort|select|choose|open|close|\+|-|>|<|\d+|job listing|job listings|job vacancy|job vacancies|vacancy|vacancies|current opening|current openings|open position|open positions|opening|openings|role|roles|position|positions|job|jobs|career|careers|learn more|read more|apply here|apply online|view details|job details|role details|position details|vacancy details|read job description|job description|description|full description|full details|link|apply for this job|apply for this role|candidati|candidati ora|scopri di più|leggi l'annuncio|leggi l’annuncio|invia candidatura|invia la candidatura|vedi annuncio)$/i;
const uiTextRe = /(checkbox|items per page|page \d+|open jobs|posting date|clear all filters|filter results|privacy statement|terms of use|cookie|stay connected|job alert|manage preferences|recruitment fraud|business code of conduct|human rights|whistleblowing|code of ethics|per saperne di più|scopri di più)/i;
const roleWordRe = /\b(engineer|developer|manager|analyst|scientist|specialist|consultant|architect|designer|director|lead|head|principal|senior|junior|intern|trainee|associate|advisor|officer|administrator|recruiter|counsel|lawyer|accountant|controller|planner|coordinator|assistant|representative|agent|technician|mechanic|operator|driver|picker|cashier|crew|barista|rider|expert|owner|scrum master|product owner|sales|marketing|finance|security|devops|frontend|backend|full stack|fullstack|software|data|qa|quality|stagiair|stage|werkstudent|apprentice|graduate|nurse|doctor|pharmacist|planner|scheduler|receptionist|waiter|warehouse|legal counsel|business partner|addett[oai]|addette|banconier[ei]|banconist[ai]|commess[oai]|cassier[ei]|cassiera|scaffalist[ai]|magazzinier[ei]|macella[io]|panettier[ei]|pasticcer[ei]|salumier[ei]|pescivendol[oi]|camerier[ei]|cuoc[oh][oi]?|aiuto cuoco|pizzaiol[oi]|barista|baristi|governante|facchin[oi]|lavapiatti|chef de rang|maitre|ma[îi]tre|sommelier|impiegat[oai]|contabil[ei]|ragionier[ei]|segretari[oa]|centralinist[ai]|opera[io]|operai[aeo]?|tecnic[oi]|manutentor[ei]|elettricist[ai]|idraulic[oi]|meccanic[oi]|macellaio|macellaia|macellai|saldator[ei]|carrellist[ai]|mulettist[ai]|autist[ai]|corrier[ei]|farmacist[ai]|infermier[ei]|fisioterapist[ai]|educator[ei]|insegnante|responsabil[ei]|direttor[ei]|direttrice|capo reparto|capo negozio|vice capo|allievo|allieva|apprendist[ai]|tirocinante|stagista|praticante|consulent[ei]|venditor[ei]|agente|promoter|hostess|steward|guardia giurata|parrucchier[ei]|estetist[ai]|receptionist|portier[ei]|custode|progettist[ai]|disegnator[ei]|analist[ai]|programmator[ei]|sviluppator[ei]|ingegner[ei]|architett[oi]|geometra|perito|buyer|categoria protetta|vendeur|vendeuse|caissier|caissi[èe]re|employ[ée]|responsable|charg[ée] de|dependient[ae]|cajer[oa]|encargad[oa]|mozo|repartidor|medewerker|verkoper|magazijn|monteur|chauffeur|stagiair[e]?)\b/i;
const locationRe = /(remote|hybrid|onsite|on-site|amsterdam|berlin|hamburg|munich|münchen|frankfurt|cologne|köln|london|manchester|paris|lyon|madrid|barcelona|lisbon|porto|milano|milan|roma|rome|torino|turin|bologna|dublin|stockholm|copenhagen|oslo|helsinki|vienna|wien|zurich|zürich|warsaw|krakow|kraków|prague|praha|budapest|bucharest|sofia|tallinn|riga|vilnius|bengaluru|bangalore|mumbai|delhi|hyderabad|tokyo|singapore|sydney|new york|san francisco|chicago|boston|austin|seattle|toronto|netherlands|germany|italy|france|spain|portugal|united kingdom|uk|united states|usa|india|poland|sweden|denmark|norway|finland|austria|switzerland|belgium|ireland|estonia|latvia|lithuania|romania|greece|hungary|czech|napoli|naples|firenze|florence|genova|genoa|palermo|catania|bari|verona|padova|padua|brescia|modena|parma|perugia|cagliari|trieste|bergamo|vicenza|salerno|rimini|ravenna|ferrara|latina|monza|como|udine|pescara|taranto|livorno|treviso|lecce|novara|piacenza|ancona|sassari|siracusa|arezzo|reggio calabria|reggio emilia|forl[ìi]|cesena|pisa|lucca|pistoia|prato|grosseto|siena|terni|viterbo|frosinone|caserta|avellino|benevento|foggia|andria|barletta|trani|brindisi|potenza|matera|catanzaro|cosenza|crotone|trapani|messina|agrigento|ragusa|caltanissetta|enna|nuoro|oristano|olbia|alessandria|asti|cuneo|biella|vercelli|varese|lecco|sondrio|cremona|mantova|lodi|pavia|savona|imperia|la spezia|belluno|rovigo|pordenone|gorizia|bolzano|trento|aosta|macerata|fermo|ascoli|teramo|chieti|isernia|campobasso|l'aquila|sardegna|sardinia|calabria|basilicata|molise|trentino|alto adige|valle d'aosta|italia|japan|china|australia|canada)/i;
// FIXED: rejects UI link text, departments, brands, abbreviations and contract words
// from ever being treated as a location (kills "Internal Services Share Learn more",
// "LensCrafters", "CDI", "Nightshift", "JobDetail", "DACH", ...)
const badLocationRe = /(share|learn more|read more|view more|show more|load more|more results|apply now|details|public sector|financial services|internal services|customer services|customer service|information technology|field operations|supply chain|business development|people team|talent team|marketing & communications|sunglass hut|target optical|for eyes|vogue eyewear|ikea store|living rooms|human resources|job alerts?|career areas|open positions|privacy policy|terms of use|cookie policy|stay connected|talent community|marketing|sales|operations|engineering|finance|legal|insurance|hr|people|talent|product|design|security|audit|tax|support|communications|facilities|business|infrastructure|systems|procurement|logistics|warehouse|strategy|recruitment|compliance|commerce|retail|corporate|administration|accounting|analytics|data|cloud|platform|solutions|services|internal|customer|manufacturing|public|sector|financial|technology|information|dach|emea|latam|apac|mena|ind|flex|gtm|csm|rxo|gqe|cdi|cdd|nightshift|shift|store|stores|markthalle|lenscrafters|oakley|opsm|ray-ban|persol|eyemed|glasses|jobdetail|externaljobs|jobsuche|praxissoftware|career|careers|vacancy|vacancies|position|positions|opening|openings|requisition|posting|trainee|internship|intern|praktikum|werkstudent|scholarship|location|locations|department|departments|workplace)\b/i;
const currentClean = window.location.href
    .split('#')[0]
    .split('?')[0]
    .toLowerCase()
    .replace(/\/$/, '');
const hasJobQuery = (href) => /[?&](job|jobid|job_id|jid|gh_jid|req|reqid|requisition|requisitionid|posting|postingid|id)=/i.test(href);
const isSelfListingUrl = (href) => {
    const clean = href.split('#')[0].split('?')[0].toLowerCase().replace(/\/$/, '');
    if (/#job=/i.test(href)) return false;
    if (hasJobQuery(href)) return false;
    return clean === currentClean;
};
const looksJobUrl = (href) => {
    if (!href || !String(href).startsWith('http')) return false;
    if (badUrlRe.test(href)) return false;
    if (isSelfListingUrl(href)) return false;
    return jobUrlRe.test(href);
};
// FIXED: Upgraded with a two-pass system that prefers line matches for roleWordRe.
// This prevents picking up location headers or boilerplate as job titles from multiline cards (e.g. Deliveroo's 'Emilia-Romagna').
const firstGoodLine = (text) => {
    const lines = String(text || '')
        .split(/\n|\\n/)
        .map(x => cleanText(x))
        .filter(Boolean);
    
    // Pass 1: Try to match a line that has a strong role keyword
    for (const line of lines) {
        if (line.length < 4 || line.length > 150) continue;
        if (!/[A-Za-zÀ-ÿ]/.test(line)) continue;
        if (genericTextRe.test(line)) continue;
        if (uiTextRe.test(line)) continue;
        if (/^[^A-Za-zÀ-ÿ0-9]+$/.test(line)) continue;
        if (roleWordRe.test(line)) return line;
    }
    // Pass 2: Fallback to the first available line
    for (const line of lines) {
        if (line.length < 4 || line.length > 150) continue;
        if (!/[A-Za-zÀ-ÿ]/.test(line)) continue;
        if (genericTextRe.test(line)) continue;
        if (uiTextRe.test(line)) continue;
        if (/^[^A-Za-zÀ-ÿ0-9]+$/.test(line)) continue;
        // FIXED: skip lines that are pure locations (e.g. "Emilia-Romagna", "Berlin")
        if (locationRe.test(line) && !roleWordRe.test(line)) continue;
        if (badLocationRe.test(line)) continue;
        return line;
    }
    return '';
};
const titleFromScope = (scope) => {
    if (!scope) return '';
    const titleSelectors = [
        'a[data-automation-id="jobTitle"]',
        '[data-automation-id="jobTitle"]',
        '[data-ph-at-id="job-title"]',
        '[data-testid*="job-title" i]',
        '[data-testid*="title" i]',
        '[data-qa*="job-title" i]',
        '[class*="job-title" i]',
        '[class*="jobTitle" i]',
        '[class*="position-title" i]',
        '[class*="posting-title" i]',
        '[class*="vacancy-title" i]',
        '[class*="role-title" i]',
        '[class*="title" i]',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'strong', 'b'
    ];
    for (const sel of titleSelectors) {
        const nodes = querySelectorAllDeep(sel, scope);
        for (const n of nodes) {
            if (!isVisible(n)) continue;
            const t = firstGoodLine(n.innerText || n.textContent || '');
            if (!t) continue;
            // FIXED: never use a pure location or boilerplate/department text as a title
            if ((locationRe.test(t) && !roleWordRe.test(t))) continue;
            if (badLocationRe.test(t) && !roleWordRe.test(t)) continue;
            if (/^(sales & commercial|corporate banking|private banking|financial services|internal services|customer services|information technology|public sector|risk management|marketing & communications|data & analytics|operations|manufacturing|engineering|finance|marketing|sales|legal|human resources|product|design|insurance|retail|store operations|communications|logistics|supply chain|customer service|customer success|field operations)$/i.test(t)) continue;
            return t;
        }
    }
    return firstGoodLine(scope.innerText || scope.textContent || '');
};
// FIXED: Checked data-attributes on the scope element itself!
// Prevents missing locations when the container element holds metadata as attributes instead of raw text.
const locationFromScope = (scope) => {
    if (!scope) return '';
    
    // Check attributes on the scope element itself first
    const attrLoc = 
        scope.getAttribute?.('data-location') ||
        scope.getAttribute?.('data-office') ||
        scope.getAttribute?.('data-city') ||
        scope.getAttribute?.('data-country') ||
        scope.getAttribute?.('data-place');
    if (attrLoc) {
        const cleaned = cleanText(attrLoc);
        if (cleaned && !badLocationRe.test(cleaned)) return cleaned;
    }
    
    const locSelectors = [
        '[data-automation-id*="location" i]',
        '[data-testid*="location" i]',
        '[data-qa*="location" i]',
        '[class*="location" i]',
        '[class*="city" i]',
        '[class*="office" i]',
        '[class*="place" i]',
        '[aria-label*="location" i]'
    ];
    for (const sel of locSelectors) {
        const nodes = querySelectorAllDeep(sel, scope);
        for (const n of nodes) {
            if (!isVisible(n)) continue;
            const txt = cleanText(n.innerText || n.textContent || '');
            if (!txt || txt.length > 200 || !locationRe.test(txt)) continue;
            if (badLocationRe.test(txt)) continue;
            // prefer the LAST segment for "City - Country" / "Brand - City" layouts
            const segs = txt.split(/\s*-\s*|\s*\|\s*|\s{2,}/).map(x => cleanText(x)).filter(Boolean);
            return txt;
        }
    }
    const lines = String(scope.innerText || '')
        .split(/\n|\\n/)
        .map(x => cleanText(x))
        .filter(Boolean);
    for (const line of lines) {
        if (line.length <= 120 && locationRe.test(line) && !badLocationRe.test(line)) {
            const segs = line.split(/\s*-\s*|\s*\|\s*/).map(x => cleanText(x)).filter(Boolean);
            return line;
        }
    }
    return '';
};
// FIXED: Upgraded with accordion/card/row-class selectors and layout filters.
// Also starts searching from parentElement to avoid returning the 'a' anchor itself!
const scopeForAnchor = (a) => {
    if (!a) return null;
    const selectors = [
        '[data-job-id]', '[data-jobid]', '[data-job]',
        '[data-position-id]', '[data-posting-id]', '[data-requisition-id]',
        '[data-automation*="job" i]', '[data-testid*="job" i]',
        '[data-testid*="accordion" i]', '[data-testid*="card" i]', '[data-testid*="item" i]',
        '[class*="job-card" i]', '[class*="job-item" i]', '[class*="job-listing" i]',
        '[class*="position-card" i]', '[class*="position-item" i]',
        '[class*="posting" i]', '[class*="opening" i]', '[class*="vacancy" i]',
        '[class*="accordion-item" i]', '[class*="accordionItem" i]', '[class*="accordion" i]',
        '[class*="card" i]', '[class*="row" i]', '[class*="item" i]',
        'li', 'article', 'tr', '[role="listitem"]'
    ];
    for (const sel of selectors) {
        try {
            const s = a.parentElement ? a.parentElement.closest(sel) : null;
            if (s) {
                if (s.tagName === 'BODY' || s.tagName === 'MAIN') continue;
                const txt = cleanText(s.innerText || '');
                const linksCount = querySelectorAllDeep('a[href]', s).length;
                if (txt.length > 3000 || linksCount > 15) {
                    continue; // Skip layout containers
                }
                return s;
            }
        } catch(e) {}
    }
    let p = a.parentElement;
    for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
        if (p.tagName === 'BODY' || p.tagName === 'MAIN') break;
        const txt = cleanText(p.innerText || '');
        const linksCount = querySelectorAllDeep('a[href]', p).length;
        if (txt.length >= 10 && txt.length <= 2500 && linksCount <= 15) return p;
    }
    return a; // Fallback to anchor itself if parent layout wraps too much content
};
const pickJobUrl = (scope) => {
    if (!scope) return '';
    if (scope.tagName === 'A' && looksJobUrl(scope.href)) {
        return scope.href;
    }
    const links = querySelectorAllDeep('a[href]', scope);
    for (const a of links) {
        if (!isVisible(a)) continue;
        if (looksJobUrl(a.href)) return a.href;
    }
    const dataHref =
        scope.getAttribute?.('data-href') ||
        scope.getAttribute?.('data-url') ||
        scope.getAttribute?.('data-link') ||
        scope.getAttribute?.('data-permalink');
    if (dataHref) {
        try {
            const full = new URL(dataHref, window.location.href).href;
            if (looksJobUrl(full)) return full;
        } catch(e) {}
    }
    const dataInfo =
        scope.getAttribute?.('data-info') ||
        scope.getAttribute?.('data-slug') ||
        scope.getAttribute?.('data-id') ||
        scope.getAttribute?.('data-job-id') ||
        scope.getAttribute?.('data-posting-id');
    if (dataInfo && dataInfo.length > 3) {
        try {
            const base = window.location.href.split('?')[0].split('#')[0].replace(/\/$/, '');
            if (base.endsWith('/jobs')) {
                return base + '/' + dataInfo;
            }
            if (base.includes('/jobs/')) {
                return base + '/' + dataInfo;
            }
            return base + '/jobs/' + dataInfo;
        } catch(e) {}
    }
    const clickable = scope.querySelector?.('[onclick]') || (scope.hasAttribute?.('onclick') ? scope : null);
    if (clickable) {
        const oc = clickable.getAttribute('onclick') || '';
        const m = oc.match(/['"]([^'"]*(?:job|career|position|vacancy|role|opening|posting|requisition)[^'"]*)['"]/i);
        if (m) {
            try {
                const full = new URL(m[1], window.location.href).href;
                if (!badUrlRe.test(full)) return full;
            } catch(e) {}
        }
    }
    return '';
};
const synthJobUrl = (title, loc) => {
    const titleSlug = cleanText(title).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').substring(0, 70);
    const locSlug = cleanText(loc || 'unknown').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').substring(0, 40);
    return window.location.href.split('?')[0].split('#')[0] + '#job=' + titleSlug + '--' + locSlug;
};
"""
# ───────────────────────── CONFIG ──────────────────────────────
class ProductionScannerConfig:
    ACTION_TIMEOUT_MS = 35000
    STABILIZATION_DELAY_SEC = 6
    MAX_PAGINATION_PAGES = 250  # hard cap; loop also self-terminates earlier (see below)
    MAX_COMPANY_TIME_SEC = 900    # per-company wall-clock budget (15 min) — giant boards release worker slots
    LOW_YIELD_PAGES = 5           # stop when N consecutive pages each add <= 2 new jobs
    # ── Network resilience (v7.1) ────────────────────────────────────────────
    # A single DNS/connection blip must not zero out a 3-hour crawl. These knobs
    # gate the run up-front and add exponential backoff to every navigation/fetch.
    PREFLIGHT_ENABLED = True          # probe connectivity before crawling
    PREFLIGHT_PROBE_HOSTS = (         # representative hosts; must resolve + connect
        "www.google.com",
        "boards-api.greenhouse.io",
        "www.amazon.jobs",
        "jobs.sap.com",
        "www.asml.com",
    )
    PREFLIGHT_PORT = 443
    PREFLIGHT_TIMEOUT_SEC = 5
    PREFLIGHT_MAX_FAILURES = 2        # abort if this many (or more) probes fail
    GOTO_RETRIES = 3                  # navigation attempts per URL
    GOTO_BACKOFF_BASE_SEC = 2.0       # exponential: 2s, 4s, 8s...
    HTTP_RETRIES = 3                  # provider-API fetch attempts
    HTTP_BACKOFF_BASE_SEC = 1.5
    HTTP_TIMEOUT_SEC = 20
    PAGINATION_WAIT_MS = 2500     # wait after clicking next
    DOM_QUIET_MS = 3000           # shorter dom-quiet for pagination
    MAX_INFINITE_SCROLL = 35
    MAX_LOAD_MORE_CLICKS = 30
    HARD_TITLE_BLACKLIST = {
        "jobs", "job", "careers", "career", "all jobs", "all openings",
        "open positions", "open roles", "current openings", "vacancies",
        "vacancy", "our jobs", "our openings", "our roles", "search jobs",
        "browse jobs", "view all jobs", "see all jobs", "job search",
        "search", "job openings", "job listings", "openings",
        "home", "homepage", "main", "skip to content", "skip to main content",
        "back", "back to top", "close", "menu", "toggle menu", "open menu",
        "learn more", "read more", "view more", "see more", "show more",
        "load more", "click here", "details", "view details", "view role",
        "view job", "view position", "apply", "apply now",
        "privacy", "privacy statement", "privacy policy", "terms",
        "terms of use", "terms & conditions", "legal", "cookie policy",
        "cookie settings", "manage cookies", "consent", "settings",
        "notice", "notifications", "accept", "this website uses cookies",
        "we use cookies", "cookie notice", "recruitment fraud warning",
        "business code of conduct", "code of ethics",
        "human rights & environmental policy", "whistleblowing",
        "complaints procedure", "sustainable sourcing policies",
        "accessibility", "disclaimer",
        "title", "job title", "position title", "loading", "please wait",
        "no results", "items per page:", "items per page", "filter results",
        "clear all filters", "posting dates", "posting date", "career area",
        "workplace", "location", "locations", "country", "region",
        "department", "departments", "teams", "select language",
        "choose language", "language", "english", "deutsch", "italiano",
        "français", "español", "nederlands", "português", "polski",
        "open jobs", "page", "ellipsis",
        "job description", "description", "job details", "position details",
        "full time", "part time", "internship", "contract", "remote",
        "hybrid", "on-site", "onsite", "upload your cv", "upload cv",
        "submit cv", "submit resume", "download", "share this job",
        "print", "email this job", "save this job", "save for later", "show job",
        "job alerts",
        "culture", "our culture", "our values", "values", "mission",
        "how we hire", "hiring process", "hiring", "our people",
        "our team", "the team", "our impact", "diversity", "inclusion",
        "benefits", "perks", "life at", "why us", "why work here",
        "about us", "about", "our story", "who we are", "what we do",
        "history", "leadership", "our leadership", "meet the team",
        "join us", "connect with us", "talent community",
        "join our talent community",
        "engineering", "marketing", "sales", "finance", "operations",
        "product", "design", "hr", "human resources", "legal",
        "customer success", "data", "analytics", "logistics",
        "compliance", "it", "technology", "field operations",
        "data & analytics", "data and analytics", "corporate banking",
        "private banking", "risk management", "finance & risk management",
        "digital & innovation", "marketing & communications",
        "customer & products", "expertise areas",
        "students & young professionals", "interns and trainees",
        "early careers", "experienced", "students", "graduates",
        "internships", "marketplace", "retail media", "campaign material",
        "operational risk management and control", "analytics & risk",
        "customer service and", "corporate", "sales & relationship",
        "applications engineering", "business performance improvement",
        "customer support", "design engineering and architecture",
        "learning and knowledge management", "legal, compliance, risk and assurance",
        "management support", "manufacturing", "projects, programs and change",
        "real estate and facilities management",
        "research and technology development",
        "sales & customer management", "sourcing and supply chain management",
        "d&e architects", "d&e planner / integrator",
        "electrical engineering", "management design engineering",
        "mechanical engineering", "mechatronics", "system industrialization",
        "system integration and testing", "chemical engineering",
        "computer science", "data science", "materials science",
        "mathematics", "other non-technical backgrounds",
        "other technical backgrounds",
        "working at abn amro", "why abn amro?", "testimonials",
        "fringe benefits", "learning and development", "challenging work",
        "making an impact", "hybrid working", "working level",
        "number of hours", "workexperience", "the reboot program",
        "all vacancies", "work with us", "find the job that matches you.",
        "service & contacts", "play store", "go to home page",
        "career areas", "ai usage", "stay connected.",
        "colleagues", "about american express", "people care",
        "people development", "daily life", "vision, mission & values",
        "#ourcareers", "manage your preferences", "be open",
        "sorry! no openings!", "for brands", "for publishers",
        "for creators", "product update", "announcement", "case study",
        "ir information", "financial highlights", "ir library",
        "stock information", "ir calendar", "suppliernet", "customernet",
        "high school", "vocational", "10-15 years",
    }
    COUNTRIES_AND_REGIONS = {
        "india", "united states", "usa", "united", "germany", "japan",
        "china", "netherlands", "france", "italy", "spain", "uk",
        "united kingdom", "europe", "asia", "north america",
        "south america", "global", "worldwide", "remote",
        "barcelona", "amsterdam", "london", "berlin", "paris",
        "madrid", "milan", "milano", "rome", "roma", "hamburg",
        "munich", "frankfurt", "dublin", "lisbon", "stockholm",
        "copenhagen", "oslo", "helsinki", "vienna", "zurich",
        "warsaw", "krakow", "kraków", "prague", "praha", "budapest",
        "bucharest", "sofia", "tallinn", "riga", "vilnius", "bengaluru",
        "bangalore", "mumbai", "delhi", "hyderabad", "tokyo", "singapore",
        "sydney", "toronto", "new york", "boston", "chicago", "austin",
        "seattle", "san francisco",
        # Italian regions
        "lombardia", "lombardy", "piemonte", "piedmont", "veneto", 
        "emilia-romagna", "lazio", "campania", "puglia", "apulia", 
        "sicilia", "sicily", "toscana", "tuscany", "friuli-venezia giulia", 
        "abruzzo", "umbria", "marche", "liguria"
    }
    ROLE_WORD_PATTERN = re.compile(
        r"\b("
        r"engineer|developer|manager|analyst|scientist|specialist|consultant|"
        r"president|vice|chief|cfo|ceo|cto|cmo|coo|chro|chairman|chef|"
        r"architect|designer|director|lead|head|principal|senior|junior|"
        r"intern|trainee|associate|advisor|officer|administrator|recruiter|"
        r"counsel|lawyer|accountant|controller|planner|coordinator|assistant|"
        r"representative|agent|technician|mechanic|operator|driver|picker|"
        r"cashier|crew|barista|rider|expert|owner|scrum master|product owner|"
        r"sales|marketing|finance|security|devops|frontend|backend|full stack|"
        r"fullstack|software|data|qa|quality|stagiair|stage|werkstudent|"
        r"apprentice|graduate|nurse|doctor|pharmacist|planner|scheduler|"
        r"receptionist|waiter|warehouse|legal counsel|business partner"
        r")\b",
        re.IGNORECASE,
    )
    SUSPICIOUS_TITLE_PATTERNS = [
        re.compile(r"^\s*vacancies?\s*\(?\d*\)?\s*$", re.IGNORECASE),
        re.compile(r"^\s*jobs?\s*\(?\d*\)?\s*$", re.IGNORECASE),
        re.compile(r"^\s*\d+\s*(jobs?|openings?|positions?|vacancies?|roles?)\s*$", re.IGNORECASE),
        re.compile(r"^\s*skip\s+to\s+", re.IGNORECASE),
        re.compile(r"^\s*(view|see|browse|explore|find|search)\s+(all\s+)?(jobs?|roles?|positions?|openings?|vacancies?)\s*$", re.IGNORECASE),
        re.compile(r"^\s*\d+\s*$"),
        re.compile(r"^[^a-zA-Z0-9À-ÿ]+$"),
        re.compile(r"^\s*to\s+apply\s+for\s+this\s+job", re.IGNORECASE),
        re.compile(r"^\s*(explore|see|view)\s+\d+\s+open\s+roles?", re.IGNORECASE),
        re.compile(r"^\s*homepage?\s*$", re.IGNORECASE),
        re.compile(r"^\s*this\s+website\s+uses", re.IGNORECASE),
        re.compile(r"^\s*(upload|submit|download)\s+", re.IGNORECASE),
        re.compile(r"^\s*(office|home|main|back)\s*$", re.IGNORECASE),
        re.compile(r"^\s*page\s+\d+.*$", re.IGNORECASE),
        re.compile(r".*checkbox.*label.*", re.IGNORECASE),
        re.compile(r"^\s*\d+\s+open\s+jobs\s*$", re.IGNORECASE),
        re.compile(r".*click this button to view.*", re.IGNORECASE),
        re.compile(r"^\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b", re.IGNORECASE),
    ]
    URL_EXCLUSION_PATTERN = re.compile(
        r"(/("
        r"privacy|cookie|terms|legal|about|contact|history|press|investor|culture|"
        r"benefit|login|signup|help|blog|pricing|faq|values|diversity|inclusion|"
        r"mission|story|leadership|impact|journey|how-we-hire|hiring-process|"
        r"life-at|talent-community|talent-network|download|upload|job-alert|"
        r"job-alerts|notify|subscribe"
        r")\b|facebook|linkedin|twitter|instagram|youtube|support\.google|"
        r"play\.google|apps\.apple|mailto:|tel:)",
        re.IGNORECASE,
    )
    CATEGORY_PATH_INDICATORS = re.compile(
        # FIX P0-1d: category LISTING pages (…/careers/disciplines/engineering,
        # …/careers/departments/sales) are not jobs. Anchored mid-path, not just
        # at the end, because these paths continue with the category slug.
        r"/(?:careers?|jobs?)/(?:disciplines?|departments?|teams?|locations?|"
        r"offices?|categor(?:y|ies)|areas?|functions?)(?:/|$)|"
        r"/(all-jobs|all-openings|browse|search|filter|category|categories|"
        r"department|departments|team|teams|location|locations|ufficio|"  # H2: Action /offerte-di-lavoro/ufficio category
        r"talent-community|talent-network|newsletter|privacy|terms|cookie)/?$",
        re.IGNORECASE,
    )
    JOB_URL_PATTERN = re.compile(
        r"(/job(s)?/[^/?#]+|/career(s)?/.*(job|position|opening|vacanc|role)|"
        r"/career(s)?/(?!disciplines?/|departments?/|teams?/|locations?/|"
        r"offices?/|categor(y|ies)/|areas?/|functions?/)[^/]+/[^/]+|"
        # FIX P0-1: single-segment detail paths. /jobs/<slug> was allowed but
        # /careers/<slug> required TWO segments, silently dropping the most
        # common modern layout (~44% of the seed is DOM-scraped + exposed).
        r"/career(s)?/(?!"
        r"benefits?|belonging|culture|values|story|our-story|people|team|teams|"
        r"locations?|our-locations|offices?|students?|students-new-graduates|"
        r"interview-tips|recruitment-process|educational-career-guidance|"
        r"engineering-at-[^/?#]+|we-keep-growing|who-we-are|about-us|about|"
        r"compatibility|emerging-talent|home|feed|search|all-jobs|overview|"
        r"life|life-at-[^/?#]+|why-[^/?#]+|how-we-hire|hiring-process|faq|"
        r"diversity|inclusion|blog|news|events?|programs?|internships?"
        r")[^/?#]*[a-z0-9][^/?#]*|"
        r"/(?:vacature|vacatures|werkenbij|werken-bij|karriere|"
        r"emplois|empleo|offre|offres|jobb|stillinger)/[^/?#]+|"
        r"/o/[^/?#]+|/role/[^/?#]+|/position|/vacancy|"
        r"/vacancies|/opening|/role|/requisition|/posting|/apply|/stellenangebot|"
        r"/stelle|/lavoro|/posizioni|/annuncio|/offerta|/offerte|/work-with-us/[^/?#]+|/carriere/[^/?#]+|/opportunit|detail|"
        # FIX P0-5: Italian/EU ATS platforms in the seed that were unmatched —
        # Altamira (/Annunci/JobNNN.html), Profils (/offre-de-emploi/...),
        # plus intervieweb/Arca24/InRecruiting/Zucchetti host tokens.
        r"/annunci/|/offre-de-emploi/|/offre-d-emploi/|/ofertas?/|"
        r"intervieweb|arca24|inrecruiting|altamiraweb|profils\.org|"
        r"zucchetti|talentia|inda\.it|"
        # H2 (2026-09-14): /offerte* (Carrefour/Action), /work-with-us/<slug> (Piazza), /carriere/<slug> (Yamamay).
        r"jobid|job_id|gh_jid|reqid|requisition|posting|lever\.co|greenhouse\.io|"
        r"personio|workable|smartrecruiters|teamtailor|ashby|workdayjobs|"
        r"successfactors|phenompeople|eightfold|deel\.com/job-boards)",
        re.IGNORECASE,
    )
    LANDING_PAGE_CTAS = [
        "see open roles", "view all openings", "explore open roles",
        "check openings", "browse openings", "view open roles",
        "check our openings", "open positions", "see all jobs",
        "view all jobs", "browse jobs", "explore jobs",
        "see job openings", "find your role", "all openings",
        "find open roles", "see roles", "view roles",
        "scopri le posizioni aperte", "lavora con noi",
        "posizioni aperte", "vedi tutti gli annunci",
        "offene stellen", "alle jobs", "stellenangebote",
        "bekijk vacatures", "alle vacatures",
        "voir les offres", "nos offres",
    ]
    # FIXED: Added fallback seed-company headquarters map to guarantee no location
    # remains generic Onsite/Hybrid or "Not Specified"!
    COMPANY_HEADQUARTERS = {
        "ABN AMRO": "Amsterdam, Netherlands",
        "About You": "Hamburg, Germany",
        "adjoe": "Hamburg, Germany",
        "Airbyte": "Remote",
        "Amazon Italia": "Milan, Italy",
        "American Express": "New York, USA",
        "Angelini Pharma": "Rome, Italy",
        "Anymind Group": "Singapore",
        "Appodeal": "Barcelona, Spain",
        "ASML": "Veldhoven, Netherlands",
        "Audible": "Newark, USA",
        "Babbel": "Berlin, Germany",
        "Barilla Group": "Parma, Italy",
        "Bolt": "Tallinn, Estonia",
        "Booking.com": "Amsterdam, Netherlands",
        "Buena": "Berlin, Germany",
        "Bunq": "Amsterdam, Netherlands",
        "Bynder": "Amsterdam, Netherlands",
        "Caeli Wind": "Berlin, Germany",
        "Cal.com": "Remote",
        "Catawiki": "Amsterdam, Netherlands",
        "Celonis": "Munich, Germany",
        "Choco": "Berlin, Germany",
        "ClearVue": "Remote",
        "Conad": "Bologna, Italy",
        "Coop Italia": "Bologna, Italy",
        "Databricks": "San Francisco, USA",
        "Decathlon Italia Retail": "Milan, Italy",
        "Deliveroo": "London, UK",
        "Detectify": "Stockholm, Sweden",
        "DevsData": "Warsaw, Poland",
        "Doctolib": "Paris, France",
        "Doist": "Remote",
        "Elastic Sales": "Mountain View, USA",
        "Elastic Finance": "Mountain View, USA",
        "Elastic Marketing": "Mountain View, USA",
        "Enel": "Rome, Italy",
        "Eni": "Rome, Italy",
        "Esselunga": "Milan, Italy",
        "Eurospin": "San Martino Buon Albergo, Italy",
        "Exact": "Delft, Netherlands",
        "Exness": "Limassol, Cyprus",
        "Factorial": "Barcelona, Spain",
        "Ferrero": "Alba, Italy",
        "Flix": "Munich, Germany",
        "Forto": "Berlin, Germany",
        "Freeletics": "Munich, Germany",
        "FS Italiane / Trenitalia": "Rome, Italy",
        "Generali": "Trieste, Italy",
        "Glovo": "Barcelona, Spain",
        "Harnham Germany": "London, UK",
        "Hays Germany": "Mannheim, Germany",
        "HelloFresh": "Berlin, Germany",
        "Highsnobiety": "Berlin, Germany",
        "HubSpot": "Cambridge, USA",
        "Huxley Netherlands": "Amsterdam, Netherlands",
        "Ikea Italia": "Milan, Italy",
        "ING": "Amsterdam, Netherlands",
        "Intesa Sanpaolo": "Turin, Italy",
        "Kaufland e-com": "Cologne, Germany",
        "Kelly Services Germany": "Troy, USA",
        "King": "London, UK",
        "Klarna": "Stockholm, Sweden",
        "KONUX": "Munich, Germany",
        "KPN": "Rotterdam, Netherlands",
        "La Fosse": "London, UK",
        "Lavazza": "Turin, Italy",
        "Lidl Italia": "Arcole, Italy",
        "Lightspeed": "Montreal, Canada",
        "limehome": "Munich, Germany",
        "Luxottica": "Milan, Italy",
        "McDonald's Italia": "Milan, Italy",
        "MetaQuotes": "Limassol, Cyprus",
        "Michael Page Germany": "Düsseldorf, Germany",
        "Michael Page Netherlands": "Amsterdam, Netherlands",
        "Michael Page UK": "London, UK",
        "Miro": "Amsterdam, Netherlands",
        "Mollie": "Amsterdam, Netherlands",
        "Morgan McKinley": "Dublin, Ireland",
        "MSD Netherlands": "Haarlem, Netherlands",
        "NavVis": "Munich, Germany",
        "Nexthink (Germany)": "Lausanne, Switzerland",
        "Nigel Frank": "Newcastle, UK",
        "Notion": "San Francisco, USA",
        "Ocado Technology": "Hatfield, UK",
        "Optiver": "Amsterdam, Netherlands",
        "Organon": "Jersey City, USA",
        "Pam Panorama": "Venice, Italy",
        "Personio": "Munich, Germany",
        "Picnic": "Amsterdam, Netherlands",
        "Pipedrive": "Tallinn, Estonia",
        "Pitch": "Berlin, Germany",
        "Planhat": "Stockholm, Sweden",
        "Poste Italiane": "Rome, Italy",
        "Prada Group": "Milan, Italy",
        "Quinyx": "Stockholm, Sweden",
        "reisetopia": "Berlin, Germany",
        "Reperio Human Capital": "Belfast, UK",
        "Retool": "San Francisco, USA",
        "Revolut": "London, UK",
        "Robert Walters Germany": "Frankfurt, Germany",
        "Robert Walters Ireland": "Dublin, Ireland",
        "Robert Walters Netherlands": "Amsterdam, Netherlands",
        "Rows": "Porto, Portugal",
        "SAP": "Walldorf, Germany",
        "Scorewarrior": "Limassol, Cyprus",
        "Shopify": "Remote",
        "Siemens": "Munich, Germany",
        "Sigmar Recruitment": "Dublin, Ireland",
        "Skyscanner": "Edinburgh, UK",
        "SNAM": "San Donato Milanese, Italy",
        "Spendesk": "Paris, France",
        "Spotify": "Stockholm, Sweden",
        "Stellantis": "Amsterdam, Netherlands",
        "Stripe": "San Francisco, USA",
        "SumUp": "London, UK",
        "Talentor Germany": "Vienna, Austria",
        "Teamtailor": "Stockholm, Sweden",
        "TIM": "Rome, Italy",
        "Understanding Recruitment": "St Albans, UK",
        "Undutchables": "Amsterdam, Netherlands",
        "UniCredit": "Milan, Italy",
        "Veriff": "Tallinn, Estonia",
        "Voi Technology": "Stockholm, Sweden",
        "Wise": "London, UK"
    }

    # ── NEW: location-quality fixes ──────────────────────────────
    # Multi-word cities (prefix-completion + known-place validation)
    MULTI_WORD_CITIES = {
        "palo alto", "las vegas", "sao paulo", "são paulo", "rio de janeiro",
        "round rock", "fort worth", "fort lauderdale", "fort collins", "fort wayne",
        "san francisco", "san jose", "san diego", "san antonio", "san mateo",
        "san ramon", "san carlos", "san leandro", "san rafael", "san pedro",
        "santa clara", "santa monica", "santa cruz", "santa barbara", "santa rosa",
        "santa fe", "st louis", "st. louis", "st gallen", "st. gallen",
        "st petersburg", "new york", "new jersey", "new delhi", "new haven",
        "newport beach", "newport news", "new brunswick", "new castle",
        "los angeles", "los alamitos", "king of prussia", "mountain view",
        "walnut creek", "redwood city", "menlo park", "south lake tahoe",
        "el segundo", "el dorado hills", "salt lake city", "kansas city",
        "oklahoma city", "charlotte", "charlottesville", "myrtle beach",
        "white plains", "herndon", "reston", "tysons", "tysons corner",
        "frankfurt am main", "bad homburg", "neu-isenburg", "bad nauheim",
        "mörfelden-walldorf", "seeheim-jugenheim", "alsbach-hähnlein",
        "garching bei münchen", "unterschleißheim", "st. leon-rot",
        "wiesbaden", "düsseldorf", "köln", "münchen", "zürich", "nürnberg",
        "hannover", "mönchengladbach", "mülheim", "saarbrücken", "koblenz",
        "ludwigshafen", "kaiserslautern", "bad kreuznach", "rüsselsheim",
        "groß-gerau", "rödermark", "dietzenbach", "maintal", "langen",
        "egelsbach", "weiterstadt", "pfungstadt", "bensheim", "heppenheim",
        "lampertheim", "bürstadt", "toenisvorst", "willich", "kaarst",
        "meerbusch", "erkelenz", "hückelhoven", "wegberg", "wassenberg",
        "schwäbisch hall", "göppingen", "esslingen", "tübingen", "sindelfingen",
        "böblingen", "leonberg", "herrenberg", "nürtingen", "kirchheim",
        "filderstadt", "ostfildern", "waiblingen", "fellbach", "backnang",
        "ludwigsburg", "kornwestheim", "bietigheim-bissingen", "vaihingen",
        "bad rappenau", "sinsheim", "waibstadt", "meckesheim", "neckargemünd",
        "schönau", "wiesloch", "walldorf", "sandhausen", "nußloch", "leimen",
        "dossenheim", "schriesheim", "weinheim", "hoppenheim", "ladenburg",
        "edingen-neckarhausen", "ilvesheim", "heddesheim", "hockenheim",
        "altlußheim", "neulußheim", "brühl", "ketsch", "plankstadt",
        "schwetzingen", "offenau", "gundelsheim", "haßmersheim", "mosbach",
        "neunkirchen", "waldbrunn", "zwingenberg", "hirschhorn",
        "neckarsteinach", "schönbrunn", "bammental", "mauer", "wiesenbach",
        "spechbach", "epfenbach", "reichartshausen", "baden-baden", "bühl",
        "achern", "oberkirch", "offenburg", "lahr", "freiburg", "müllheim",
        "weil am rhein", "lörrach", "rheinfelden", "schopfheim",
        "zell im wiesental", "todtnau", "schönau im schwarzwald",
        "titisee-neustadt", "hinterzarten", "feldberg", "schluchsee",
        "bonndorf", "waldshut-tiengen", "bad säckingen", "laufenburg",
        "stein am rhein", "schaffhausen", "konstanz", "kreuzlingen",
        "radolfzell", "überlingen", "friedrichshafen", "lindau", "kempten",
        "memmingen", "kaufbeuren", "füssen", "garmisch-partenkirchen",
        "murnau", "weilheim", "starnberg", "herrsching", "germering",
        "garching", "neufahrn", "eching", "freising", "erding",
        "markt schwaben", "poing", "grafing", "zorneding", "kirchseeon",
        "ebersberg", "wasserburg", "rosenheim", "bad aibling", "kolbermoor",
        "raubling", "brannenburg", "kufstein", "wörgl", "kitzbühel",
        "st. johann in tirol", "saalfelden", "zell am see", "mittersill",
        "mayrhofen", "jenbach", "schwaz", "hall in tirol", "wattens",
        "fügen", "zillertal", "ramsau", "schladming", "liezen", "admont",
        "eisenerz", "leoben", "bruck an der mur", "kapfenberg",
        "mürzzuschlag", "krippenstein", "obertraun", "bad ischl", "gmunden",
        "traunsee", "vöcklabruck", "wels", "linz", "steyr", "enns",
        "amstetten", "sankt pölten", "tulln", "klosterneuburg", "korneuburg",
        "stockerau", "mistelbach", "hollabrunn", "retz", "znojmo",
        "břeclav", "hodonín", "kroměříž", "olomouc", "přerov", "prostějov",
        "šumperk", "jeseník", "krnov", "bruntál", "opava", "ostrava",
        "havířov", "karviná", "český těšín", "trinec", "frýdek-místek",
        "nový jičín", "valašské meziříčí", "vsetín", "zlín", "kroměříž",
        "uherské hradiště", "hodonín", "břeclav", "mikulov", "znojmo",
        "jihlava", "havlíčkův brod", "chotěboř", "žďár nad sázavou",
        "velké meziříčí", "třebíč", "telč", "slavonice", "jindřichův hradec",
        "tábor", "písek", "strakonice", "prachatice", "vimperk",
        "český krumlov", "kaplice", "vyšší brod", "lippstadt", "gütersloh",
        "bielefeld", "herford", "bad salzuflen", "lemgo", "detmold",
        "höxter", "warburg", "marsberg", "brilon", "meschede", "arnsberg",
        "sundern", "neheim-hüsten", "menden", "balve", "iserlohn",
        "lüdenscheid", "meinerzhagen", "kierspe", "halver", "schalksmühle",
        "plettenberg", "attendorn", "olpe", "lennestadt", "kirchhundem",
        "finnentrop", "schmallenberg", "winterberg", "medebach", "hallenberg",
        "wenden", "freudenberg", "siegen", "kreuztal", "netphen",
        "hilchenbach", "bad berleburg", "bad laasphe", "burbach",
        "wilkensdorf", "haiger", "dillenburg", "herborn", "wetzlar",
        "giessen", "butzbach", "friedberg", "oberursel", "kronberg",
        "königstein", "kelkheim", "hofheim", "flörsheim", "hochheim",
        "raunheim", "kelsterbach", "nauheim", "büttelborn", "griesheim",
        "bickenbach", "seeheim-jugenheim", "zwingenberg", "lorsch", "biblis",
        "gernsheim", "biebesheim", "stockstadt", "aschaffenburg", "goldbach",
        "hösbach", "lindenberg", "alzenau", "kahl am main", "seligenstadt",
        "babenhausen", "dieburg", "groß-umstadt", "höchst im odenwald",
        "bad könig", "michelstadt", "erbach", "beerfelden", "kirchheim",
        "rohrbach", "handschuhsheim", "wieblingen", "angelbachtal",
        "zuzenhausen", "eschelbronn", "neidenstein", "siegelsbach",
        "itilingen", "bad wimpfen", "binau", "felsenberg", "lindelbach",
        "billigheim", "schefflenz", "adelsheim", "seckach", "buchen",
        "walldürn", "hardheim", "külsheim", "wertheim", "marktheidenfeld",
        "lohr am main", "gemünden", "karlstadt", "arnstein", "hammelburg",
        "bad kissingen", "bad brückenau", "wildflecken", "fulda", "hünfeld",
        "bad hersfeld", "bebra", "rottenburg an der fulda",
        "bad soden-salmünster", "schlüchtern", "steinau an der straße",
        "gelnhausen", "wächtersbach", "bad orb", "birstein", "gedern",
        "schotten", "laubach", "grünberg", "hungen", "nidda", "büdingen",
        "hanau", "bruchköbel", "langenselbold", "erlensee", "schöneck",
        "niederdorfelden", "karben", "bad vilbel", "rosbach", "ober-mörlen",
        "münzenberg", "linden", "pohlheim", "braunfels", "leun", "solms",
        "aßlar", "ehringshausen", "braunfels", "wuppertal", "remscheid",
        "solingen", "hilden", "haan", "erkrath", "mettmann", "wülfrath",
        "velbert", "heiligenhaus", "ratingen", "grevenbroich",
        "rommerskirchen", "dormagen", "zons", "neuss", "korschenbroich",
        "jüchen", "pulheim", "frechen", "hürth", "wesseling", "bornheim",
        "swisttal", "weilerswist", "euskirchen", "zülpich",
        "bad münstereifel", "mechernich", "schleiden", "kall", "hellenthal",
        "nideggen", "heimbach", "monschau", "simmerath", "roetgen",
        "aachen", "würselen", "herzogenrath", "übach-palenberg",
        "geilenkirchen", "heinsberg", "nettetal", "grefrath", "kempen",
        "business bay", "east london", "north carolina", "south carolina",
        "north dakota", "south dakota", "north hollywood", "west hollywood",
        "south beach", "new south wales", "north rhine-westphalia",
        "newcastle upon tyne", "stratford-upon-avon", "sutton coldfield",
        "west midlands", "east midlands", "north yorkshire", "west yorkshire",
        "south yorkshire", "east sussex", "west sussex", "northamptonshire",
        "wellington", "stockholm", "new delhi", "são leopoldo",
        "greater london", "greater manchester", "greater china",
        "greater tokyo", "greater toronto", "greater boston",
    }

    # Single-word known cities (broad global + EU coverage)
    KNOWN_CITIES = {
        "amsterdam", "berlin", "hamburg", "munich", "cologne", "frankfurt",
        "stuttgart", "dusseldorf", "dortmund", "essen", "leipzig", "dresden",
        "nuremberg", "bremen", "mannheim", "heidelberg", "karlsruhe",
        "freiburg", "bonn", "mainz", "wiesbaden", "kiel", "rostock",
        "magdeburg", "erfurt", "potsdam", "walldorf", "veldhoven", "eindhoven",
        "utrecht", "groningen", "tilburg", "almere", "breda", "nijmegen",
        "haarlem", "arnhem", "delft", "leiden", "rotterdam", "den haag",
        "london", "manchester", "birmingham", "leeds", "liverpool",
        "newcastle", "sheffield", "bristol", "nottingham", "leicester",
        "southampton", "portsmouth", "brighton", "edinburgh", "glasgow",
        "cardiff", "belfast", "oxford", "cambridge", "york", "bath",
        "aberdeen", "dundee", "reading", "coventry", "plymouth", "derby",
        "swansea", "luton", "milton keynes", "northampton", "watford",
        "bournemouth", "norwich", "exeter", "cheltenham", "salzburg", "graz",
        "linz", "innsbruck", "vienna", "zurich", "geneva", "basel",
        "lausanne", "bern", "lugano", "milan", "milano", "rome", "roma",
        "naples", "napoli", "turin", "torino", "palermo", "genoa", "bologna",
        "florence", "venice", "verona", "bari", "trieste", "brescia",
        "parma", "modena", "alba", "cagliari", "catania", "livorno",
        "ravenna", "rimini", "ancona", "udine", "vicenza", "bergamo",
        "bolzano", "trento", "pisa", "siena", "lucca", "prato", "ferrara",
        "pescara", "lecce", "monza", "como", "asti", "novara", "la spezia",
        "treviso", "rovigo", "mantova", "cremona", "pavia", "varese",
        "biella", "vercelli", "cuneo", "savona", "agordo", "charenton",
        "warsaw", "krakow", "lodz", "wroclaw", "poznan", "gdansk", "szczecin",
        "bydgoszcz", "lublin", "katowice", "bialystok", "gdynia",
        "czestochowa", "radom", "torun", "rzeszow", "kielce", "olsztyn",
        "prague", "brno", "ostrava", "plzen", "liberec", "olomouc",
        "pardubice", "budapest", "debrecen", "szeged", "miskolc", "pecs",
        "gyor", "bucharest", "cluj", "timisoara", "iasi", "constanta",
        "craiova", "brasov", "galati", "ploiesti", "oradea", "braila",
        "arad", "sibiu", "bacau", "satu mare", "sophia", "sofia", "plovdiv",
        "varna", "burgas", "ruse", "stara zagora", "pleven", "sliven",
        "dobrich", "shumen", "pernik", "athens", "thessaloniki", "patras",
        "larissa", "heraklion", "volos", "chania", "rhodes", "stockholm",
        "gothenburg", "malmo", "uppsala", "vasteras", "orebro", "linkoping",
        "helsingborg", "jonkoping", "norrkoping", "lund", "umea", "gavle",
        "boras", "oslo", "bergen", "trondheim", "stavanger", "drammen",
        "fredrikstad", "kristiansand", "tromso", "copenhagen", "aarhus",
        "odense", "aalborg", "esbjerg", "randers", "kolding", "horsens",
        "vejle", "roskilde", "herning", "silkeborg", "helsinki", "espoo",
        "tampere", "vantaa", "oulu", "turku", "jyvaskyla", "lahti",
        "kuopio", "tallinn", "tartu", "narva", "riga", "daugavpils",
        "liepaja", "vilnius", "kaunas", "klaipeda", "siauliai", "dublin",
        "cork", "limerick", "galway", "waterford", "kilkenny", "brussels",
        "antwerp", "ghent", "charleroi", "liege", "bruges", "namur",
        "leuven", "luxembourg", "singapore", "tokyo", "osaka", "kyoto",
        "yokohama", "nagoya", "sapporo", "fukuoka", "kobe", "hiroshima",
        "seoul", "busan", "incheon", "daegu", "daejeon", "gwangju",
        "suwon", "shanghai", "beijing", "shenzhen", "guangzhou", "chengdu",
        "hangzhou", "wuhan", "xian", "nanjing", "chongqing", "suzhou",
        "tianjin", "qingdao", "dalian", "ningbo", "xiamen", "hong kong",
        "taipei", "kaohsiung", "taichung", "tainan", "hsinchu", "linkou",
        "bangkok", "kuala lumpur", "penang", "jakarta", "surabaya",
        "bandung", "manila", "cebu", "davao", "makati", "ho chi minh",
        "hanoi", "da nang", "mumbai", "delhi", "new delhi", "bengaluru",
        "hyderabad", "chennai", "kolkata", "pune", "ahmedabad", "jaipur",
        "surat", "lucknow", "kanpur", "nagpur", "indore", "bhopal",
        "visakhapatnam", "patna", "vadodara", "agra", "nashik", "meerut",
        "rajkot", "varanasi", "srinagar", "amritsar", "guwahati",
        "chandigarh", "gurgaon", "gurugram", "noida", "kochi",
        "coimbatore", "madurai", "mangalore", "mysore", "sydney",
        "melbourne", "brisbane", "perth", "adelaide", "canberra", "hobart",
        "geelong", "townsville", "cairns", "auckland", "wellington",
        "christchurch", "hamilton", "dunedin", "victoria", "saskatoon",
        "regina", "johannesburg", "cape town", "durban", "pretoria",
        "lagos", "abuja", "accra", "nairobi", "cairo", "alexandria",
        "casablanca", "rabat", "marrakech", "doha", "dubai", "abu dhabi",
        "sharjah", "riyadh", "jeddah", "dammam", "kuwait city", "manama",
        "muscat", "tel aviv", "jerusalem", "haifa", "beirut", "amman",
        "istanbul", "ankara", "izmir", "bursa", "antalya", "tehran",
        "tbilisi", "yerevan", "baku", "almaty", "astana", "tashkent",
        "islamabad", "karachi", "lahore", "dhaka", "colombo", "nicosia",
        "limassol", "valletta", "reykjavik", "new york", "los angeles",
        "chicago", "houston", "phoenix", "philadelphia", "san antonio",
        "san diego", "dallas", "san jose", "austin", "jacksonville",
        "columbus", "charlotte", "indianapolis", "seattle", "denver",
        "washington", "boston", "nashville", "detroit", "portland",
        "las vegas", "memphis", "louisville", "baltimore", "milwaukee",
        "albuquerque", "tucson", "fresno", "sacramento", "kansas city",
        "atlanta", "miami", "omaha", "raleigh", "cincinnati", "cleveland",
        "pittsburgh", "minneapolis", "tampa", "orlando", "newark",
        "cambridge", "auburn hills", "dearborn", "palo alto", "mountain view",
        "sunnyvale", "santa clara", "redwood city", "menlo park", "emeryville",
        "oakland", "berkeley", "irvine", "santa monica", "culver city",
        "burbank", "glendale", "pasadena", "long beach", "toronto",
        "montreal", "vancouver", "calgary", "ottawa", "edmonton", "winnipeg",
        "halifax", "mississauga", "brampton", "markham", "waterloo",
        "kitchener", "mexico city", "guadalajara", "monterrey", "sao paulo",
        "sao leopoldo", "rio de janeiro", "buenos aires", "santiago",
        "bogota", "lima", "montevideo", "lisbon", "porto", "braga",
        "coimbra", "faro", "aveiro", "guimaraes", "madrid", "barcelona",
        "valencia", "seville", "bilbao", "zaragoza", "malaga", "granada",
        "palma", "alicante", "paris", "lyon", "marseille", "toulouse",
        "nice", "nantes", "strasbourg", "bordeaux", "lille", "rennes",
        "grenoble", "montpellier", "hannover", "aachen", "kiel", "lubeck",
        "wiesbaden", "erlangen", "würzburg", "ingolstadt", "regensburg",
        "garching", "herndon", "kokomo", "newtown", "sterling heights",
        "tempe", "toledo", "chelsea", "middlesex", "aurora", "appleton",
        "hsinchu", "linkou", "agordo", "charenton", "kv", "kyiv", "lviv",
        "odessa", "kharkiv", "dnipro", "zaporizhzhia", "minsk", "chișinău",
        "batumi", "yerevan", "tbilisi", "bishkek", "dushanbe", "ashgabat",
    }

    # Words/phrases that must NEVER be accepted as a location
    LOCATION_REJECT_PHRASES = [
        "learn more", "read more", "view more", "show more", "load more",
        "more results", "share", "apply now", "apply", "details",
        "public sector", "financial services", "internal services",
        "customer services", "customer service", "information technology",
        "field operations", "supply chain", "business development",
        "people team", "talent team", "marketing & communications",
        "sunglass hut", "target optical", "for eyes", "vogue eyewear",
        "ikea store", "living rooms", "human resources", "life at",
        "about us", "career areas", "open positions", "job alerts",
        "job alert", "privacy policy", "terms of use", "cookie policy",
        "stay connected", "talent community", "join our", "work with us",
        "find the job", "all vacancies", "play store", "app store",
        "manage preferences", "recruitment fraud", "code of ethics",
        "code of conduct", "call us", "contact us", "join us", "more",
        "next page", "previous page", "back to top", "all jobs",
        "job search", "search", "browse", "filter", "sort", "view all",
        "vice president", "wholesale banking", "enterprise architecture",
        "power distribution", "transformation value management",
        "revenue growth management", "asset management", "cost management",
        "contact centre", "contact center", "intelligent automation",
        "workforce planning", "network planning", "database engine internals",
        "candy crush saga", "orbit program", "pearle vision",
        "orlen eye care", "walmart confections", "hazelnut company",
        "valley fair mall", "gurnee mills", "warranty kokomo engine plant",
        "warranty dundee engine plant", "wise buisness", "wise account",
        "china outbound", "belgium market", "protected categories",
        "women's health", "wind turbines", "front line", "cust svc",
        "western region", "eastern region", "northern region",
        "southern region", "greater china region", "orbit program",
    ]

    LOCATION_REJECT_WORDS = [
        "share", "more", "apply", "details", "linkedin", "facebook",
        "twitter", "instagram", "youtube", "pinterest", "tiktok",
        "marketing", "sales", "operations", "engineering", "finance",
        "legal", "insurance", "hr", "people", "talent", "product",
        "design", "security", "audit", "tax", "support", "communications",
        "facilities", "business", "infrastructure", "systems", "procurement",
        "logistics", "warehouse", "strategy", "recruitment", "compliance",
        "commerce", "retail", "corporate", "administration", "accounting",
        "analytics", "data", "cloud", "platform", "solutions", "services",
        "internal", "customer", "manufacturing", "public", "sector",
        "financial", "technology", "information", "central", "north",
        "south", "east", "west", "dach", "emea", "latam", "apac", "mena",
        "ind", "flex", "gtm", "csm", "rxo", "gqe", "cdi", "cdd",
        "nightshift", "shift", "store", "stores", "markthalle",
        "verkäufer", "verkaeufer", "mitarbeiter", "mitarbeiterin",
        "berater", "kaufmann", "kauffrau", "techniker", "ingenieur",
        "assistent", "leiter", "sachbearbeiter", "vendeur", "vendeuse",
        "collaborateur", "collaboratrice", "employé", "employe", "hôte",
        "hôtesse", "commesso", "commessa", "addetto", "addetta",
        "impiegato", "stagista", "tirocinante", "operaio", "operatore",
        "jobdetail", "externaljobs", "jobsuche", "praxissoftware",
        "lenscrafters", "lenscrafter", "oakley", "opsm", "ray-ban",
        "persol", "eyemed", "glasses", "career", "careers", "job", "jobs",
        "vacancy", "vacancies", "position", "positions", "opening",
        "openings", "role", "roles", "requisition", "posting", "hiring",
        "recruiting", "graduate", "students", "interns", "trainee",
        "trainees", "scholarship", "stipend", "internship", "stage",
        "praktikum", "apprenticeship", "apprentice", "freelance",
        "commission", "location", "locations", "workplace", "department",
        "departments", "team", "teams", "division", "unit", "area",
        "sector", "office", "headquarters", "campus", "hub", "site",
        "onboarding", "welcome", "register", "signup", "login", "logout",
        # languages / descriptors that are never locations
        "french", "german", "italian", "spanish", "portuguese", "dutch",
        "polish", "swedish", "danish", "norwegian", "finnish", "greek",
        "czech", "hungarian", "romanian", "bulgarian", "croatian", "serbian",
        "ukrainian", "russian", "turkish", "arabic", "chinese", "japanese",
        "korean", "hindi", "bengali", "thai", "vietnamese", "indonesian",
        "malay", "flemish", "swiss", "british", "american", "canadian",
        "australian", "irish", "scottish", "welsh", "english", "speaking",
        "speaker", "fluent", "native", "bilingual", "multilingual",
        "language", "international", "federal", "civilian", "regional",
        "national", "global", "worldwide", "hq", "headquarter",
        "greater", "metropolitan", "downtown", "uptown", "midtown",
        # workload / schedule words (German/Italian included)
        "vollzeit", "teilzeit", "seasonal", "virtual", "minijob", "aushilfe",
        "schicht", "nacht", "turno", "fulltime", "parttime",
        # tech stacks / skills — never locations
        "java", "javascript", "python", "azure", "aws", "gcp", "sql",
        "kubernetes", "docker", "terraform", "react", "angular", "node",
        "networking", "database", "sap", "salesforce", "oracle", "linux",
        "windows", "machine learning", "artificial intelligence",
        # departments / business units / generic nouns
        "hotels", "banking", "wholesale", "credit", "cards", "payments",
        "fincrime", "sanctions", "deactivations", "assets", "consumer",
        "development", "programs", "programme", "specialisation",
        "specialization", "growth", "upsell", "experimentation", "technical",
        "electrical", "construction", "mechanical", "treasury", "trade",
        "market", "outbound", "inbound", "account", "buisness", "business",
        "manag", "cust", "svc", "network", "payroll", "transformation",
        "revenue", "cost management", "contact", "intelligent", "workforce",
        "warranty", "plant", "engine", "region", "area", "division",
        "program", "project", "portfolio", "governance", "risk", "fraud",
        "collections", "billing", "invoicing", "vendor", "supplier",
        "merchandising", "planning", "scheduling", "inventory", "fleet",
        "dispatch", "staffing", "compensation", "channel", "alliance",
        "east", "west", "north", "south", "northeast", "northwest",
        "southeast", "southwest", "eastern", "western", "northern",
        "southern", "central", "midwest", "anywhere", "worldwide", "global",
        "international", "federal", "civilian", "national", "regional",
        "local", "virtual", "digital", "center", "centre",
        # store brands / product / program names
        "mack", "ram", "jeep", "dodge", "chrysler", "fiat", "lancia",
        "abarth", "alfa", "maserati", "opel", "vauxhall", "peugeot",
        "citroen", "cymer", "candy crush", "macys", "macy's", "cabela's",
        "walmart", "eyebuydirect", "pearle vision", "orlen eye care",
        "gurnee mills", "valley fair", "hazelnut", "engawa",
        "wise account", "wise buisness", "protected categories",
        "women's health", "wind turbines", "mall", "mills", "company",
        "corp", "inc", "ltd", "gmbh", "ag", "spa", "llc", "plc",
    ]

    # Phrases that are NEVER job titles even on their own (dropped by title cleaning)
    PURE_CONTRACT_PHRASES = {
        "fixed term", "fixed-term", "fixed term contract", "permanent",
        "temporary", "temporaire", "contract", "full time", "full-time",
        "part time", "part-time", "internship", "trainee", "apprenticeship",
        "secondment", "stage", "cdi", "cdd", "temporary contract",
        "permanent contract", "open position", "open positions",
        "apply now", "apply", "more", "learn more", "read more",
        "view more", "show more", "load more", "job description",
        "job details", "all genders", "m/f/d", "m/w/d", "w/m/d",
        "d/f/m", "f/m/d", "m/f/x", "w/m/x", "m/w/x",
    }

    # Exact department/boilerplate titles that must never be kept as a job title
    DEPT_AS_TITLE = {
        "sales & commercial", "sales and commercial", "retail banking sales",
        "corporate banking", "private banking", "financial services",
        "internal services", "customer services", "information technology",
        "public sector", "risk management", "marketing & communications",
        "data & analytics", "data and analytics", "operations", "manufacturing",
        "engineering", "finance", "marketing", "sales", "legal", "human resources",
        "product", "design", "insurance", "retail", "store operations",
        "communications", "logistics", "supply chain", "customer service",
        "customer success", "field operations", "expertise areas", "commercial",
        "retail operations", "store", "stores", "nightshift", "cdi", "cdd",
        "sales & commercial assistant", "store management", "merchandising",
        "quality & lean", "quality and lean", "food & beverage", "food and beverage",
        "ufficio",  # H2: Italian "office" (Action category link title)
        "night shift", "nightshift", "recovery", "customer relations",
        "visual merchandising", "food service", "bakery", "deli", "produce",
        "cashier", "checkout", "front end", "back end", "fresh food",
        "customer experience", "ecommerce", "e-commerce", "fulfillment",
    }

    # Companies where the HQ fallback must NOT be stamped (multi-location / global)
    HQ_FALLBACK_EXCEPTIONS = {
        "ASML", "Hays Germany", "Ferrero", "Anymind Group", "Luxottica",
        "Ikea Italia", "Siemens", "SAP", "Stripe", "Databricks",
        "American Express", "Amazon Italia", "Nigel Frank", "Harnham Germany",
        "Michael Page Germany", "Michael Page UK", "Michael Page Netherlands",
        "ING", "Revolut", "Wise", "SumUp", "Optiver", "Stellantis", "Enel",
        "Poste Italiane", "Generali", "Intesa Sanpaolo", "UniCredit",
        "Morgan McKinley", "Robert Walters Germany", "Robert Walters Ireland",
        "Robert Walters Netherlands", "Kelly Services Germany",
        "Sigmar Recruitment", "Understanding Recruitment",
        "Reperio Human Capital", "Klarna", "Spotify", "Booking.com",
        "Deliveroo", "Glovo", "Bolt", "HelloFresh", "Ocado Technology",
        "King", "Skyscanner", "Eni", "TIM", "SNAM", "McDonald's Italia",
        "Decathlon Italia Retail", "Conad", "Coop Italia", "Esselunga",
        "Eurospin", "Lidl Italia", "Pam Panorama", "FS Italiane / Trenitalia",
        "Barilla Group", "Lavazza", "Angelini Pharma", "Organon",
        "MSD Netherlands", "Huxley Netherlands", "Undutchables", "La Fosse",
        "Talentor Germany", "DevsData", "MetaQuotes", "Exness",
        "Scorewarrior", "Morgan McKinley", "Hays Germany", "Siemens",
        "Luxottica", "Stellantis", "American Express",
    }

    # Extended country/region list — the original set was missing many countries
    # (ukraine, romania, greece, sweden, brazil, ...). Merged into
    # COUNTRIES_AND_REGIONS at runtime.
    REGIONS_EXTRA = {
        # Europe
        "ukraine", "belarus", "moldova", "romania", "bulgaria", "greece",
        "hungary", "czechia", "czech republic", "slovakia", "slovenia",
        "croatia", "serbia", "bosnia", "bosnia and herzegovina",
        "north macedonia", "montenegro", "albania", "kosovo", "georgia",
        "armenia", "azerbaijan", "turkey", "cyprus", "malta", "iceland",
        "luxembourg", "monaco", "andorra", "san marino", "liechtenstein",
        "scandinavia", "baltics", "baltic states", "benelux", "nordics",
        "balkans", "sweden", "denmark", "norway", "finland", "austria",
        "switzerland", "belgium", "ireland", "estonia", "latvia", "lithuania",
        "poland", "czech", "czechia", "portugal", "canada", "australia",
        "netherlands", "germany", "italy", "france", "spain", "japan",
        "china", "india", "singapore", "usa", "uk", "united kingdom",
        "united states",
        # Americas
        "brazil", "argentina", "chile", "colombia", "peru", "uruguay",
        "paraguay", "bolivia", "ecuador", "venezuela", "guyana", "suriname",
        "mexico", "cuba", "dominican republic", "jamaica", "puerto rico",
        "panama", "costa rica", "guatemala", "honduras", "el salvador",
        "nicaragua", "belize", "trinidad", "central america",
        "latin america",
        # Middle East / Africa
        "israel", "palestine", "uae", "united arab emirates", "qatar",
        "saudi arabia", "kuwait", "bahrain", "oman", "jordan", "lebanon",
        "syria", "iraq", "iran", "yemen", "egypt", "morocco", "algeria",
        "tunisia", "libya", "sudan", "ethiopia", "nigeria", "ghana",
        "kenya", "tanzania", "uganda", "senegal", "ivory coast",
        "cote d'ivoire", "cameroon", "zimbabwe", "zambia", "botswana",
        "namibia", "mozambique", "angola", "rwanda", "south africa",
        "north africa", "west africa", "east africa",
        "sub-saharan africa",
        # Asia / Oceania
        "south korea", "taiwan", "thailand", "vietnam", "indonesia",
        "malaysia", "philippines", "myanmar", "cambodia", "laos",
        "mongolia", "nepal", "sri lanka", "bangladesh", "pakistan",
        "afghanistan", "kazakhstan", "uzbekistan", "kyrgyzstan",
        "tajikistan", "turkmenistan", "new zealand", "fiji",
        "papua new guinea", "new caledonia", "french polynesia",
        # Generic regions
        "europe", "asia", "africa", "oceania", "global", "worldwide",
    }

    # Known typos/OCR artifacts in job-board location strings
    TYPO_MAP = {
        "fort laurderdale": "fort lauderdale",
        "fort lauderale": "fort lauderdale",
        "garching bei munchen": "garching bei münchen",
    }

    # Public ATS job-board API fallbacks for companies whose career pages are
    # JS-heavy or blocked. Verified working 2026-08-06:
    #   Ashby: Lightspeed, Forto, Rows (Rowspace), Planhat
    #   Greenhouse: HelloFresh, Skyscanner, Catawiki
    ATS_FALLBACK = {
        "HelloFresh": {"greenhouse": ["hellofresh"]},
        "Skyscanner": {"greenhouse": ["skyscanner"]},
        "Catawiki": {"greenhouse": ["catawiki"]},
        "Rows": {"ashby": ["Rowspace", "rows"]},
        "Planhat": {"ashby": ["Planhat", "planhat"]},
        "Lightspeed": {"ashby": ["Lightspeed", "lightspeed"]},
        "Forto": {"ashby": ["Forto", "forto"]},
        "Retool": {"ashby": ["Retool", "retool"]},
        "Doist": {"ashby": ["Doist", "doist"], "lever": ["doist"]},
        "Pitch": {"ashby": ["Pitch", "pitch"], "lever": ["pitch"]},
        "Spendesk": {"personio": ["spendesk"]},
        "Freeletics": {"personio": ["freeletics"]},
        "Generali": {"greenhouse": ["generali"]},
        "Bynder": {"ashby": ["Bynder", "bynder"]},
        "Buena": {"ashby": ["Buena", "buena"]},
    }

    # Casing overrides (uk -> UK etc.)
    CASING_MAP = {
        "uk": "UK", "usa": "USA", "us": "US", "uae": "UAE", "eu": "EU",
        "apac": "APAC", "emea": "EMEA", "latam": "LATAM", "dach": "DACH",
        "mena": "MENA", "sa": "SA", "in": "IN", "de": "DE", "it": "IT",
        "fr": "FR", "es": "ES", "nl": "NL", "pl": "PL", "pt": "PT",
        "se": "SE", "no": "NO", "dk": "DK", "fi": "FI", "at": "AT",
        "ch": "CH", "be": "BE", "ie": "IE", "cn": "CN", "jp": "JP",
        "kr": "KR", "sg": "SG", "au": "AU", "ca": "CA", "mx": "MX",
        "br": "BR", "ar": "AR", "cl": "CL", "co": "CO", "pe": "PE",
        "za": "ZA", "ng": "NG", "ke": "KE", "eg": "EG", "ma": "MA",
        "il": "IL", "tr": "TR", "ae": "AE", "qa": "QA",
    }

    # Seed URL corrections for broken entries
    SEED_URL_OVERRIDES = {
        "SNAM": "https://carriere.snam.it/",
        "Pam Panorama": "https://lavoraconnoi.gruppopam.it/hr-jobsite/",
        "Eni": "https://www.eni.com/en-IT/careers.html",
    }

    # URL path segments that are UI routes, never locations
    URL_PATH_SEGMENT_BLACKLIST = {
        "jobdetail", "externaljobs", "jobsuche", "search", "search-results",
        "searchjobs", "careers", "career", "jobs", "job", "vacancies",
        "vacancy", "openings", "positions", "position", "results", "list",
        "page", "department", "departments", "locations", "location",
        "detail", "show", "index", "home", "apply", "form", "new",
        "requisition", "posting", "role", "o", "all", "browse", "filter",
        "category", "categories", "jobsearch", "jobsuche", "talent",
        "people", "about", "contact", "faq", "help", "privacy", "terms",
    }

    URL_JOB_MARKERS = {
        "job", "jobs", "o", "role", "vacancy", "vacancies", "position",
        "positions", "posting", "requisition", "career", "careers",
        "opening", "openings", "apply", "stellenangebot", "stelle",
        "lavoro", "posizioni", "annuncio", "offerta", "opportunity",
        "opportunities",
    }

    # Optional detail-page scan (off by default; enable with --detail)
    ENABLE_DETAIL_SCAN = False
    DETAIL_SCAN_TIMEOUT_MS = 12000
    DETAIL_SCAN_TIME_BUDGET_SEC = 480   # max seconds one company's detail scan may run (8 min)
    MAIN_HEARTBEAT_SEC = 30             # main thread prints how many companies are still running
    MAX_STALL_SEC = 600             # abort ONLY when NO page-level ACTIVITY anywhere for 10 min
                                    # (queued companies waiting for a worker slot are NOT a hang)
    MAX_DETAIL_SCAN_PER_COMPANY = 120
    MAX_DETAIL_SCAN_TOTAL = 3000

# ───────────── JD SUPPORT DETECTOR (shared) ─────────────
# ───────────── SHARED RESOURCE / BROWSER SETTINGS ─────────────
# Kept in sponsorscout.scanning.common so the concurrency caps and the lean
# Chromium flags have exactly one definition.  A 2-core / 8 GB machine must
# never be handed several concurrent browsers (see recommended_workers docs).
try:
    from sponsorscout.scanning.common import (
        BROWSER_ARGS,
        check_control,
        recommended_workers,
    )
except ImportError:  # standalone single-file mode
    def recommended_workers(kind="browser"):
        try:
            import os
            cpu = os.cpu_count() or 2
            return max(1, min(cpu // 2, 4)) if cpu > 2 else 1
        except Exception:
            return 1

    def check_control(cancel_event, pause_event=None, poll_sec=0.1):
        """Standalone copy of ``sponsorscout.scanning.common.check_control``."""
        import time as _time
        if pause_event is not None and pause_event.is_set():
            while pause_event.is_set():
                if cancel_event is not None and cancel_event.is_set():
                    return True
                _time.sleep(poll_sec)
        return cancel_event is not None and cancel_event.is_set()

    BROWSER_ARGS = [
        "--no-sandbox", "--disable-dev-shm-usage", "--disable-http2",
        "--ignore-certificate-errors", "--blink-settings=imagesEnabled=false",
        "--disable-background-networking", "--disable-extensions",
        "--disable-gpu", "--no-first-run",
    ]


# Single source of truth moved to sponsorscout.scanning.jd_support.
try:
    from sponsorscout.scanning.jd_support import (
        JDSupportDetector,
        VERDICT_YES,
        VERDICT_NO,
        VERDICT_UNKNOWN,
        detect_blue_card,
    )
except ImportError:  # standalone single-file mode (no app repo on sys.path)
    # ─────────────────────────────────────────────────────────────────────
    # FIX P0-16: SELF-CONTAINED VISA / RELOCATION DETECTOR
    #
    # This block used to be a stub that returned "Unknown" for everything.
    # Consequence: the 2026-09-15 run produced 12,298 rows in which *every
    # single* Visa Sponsorship, Relocation Support and EU Blue Card value was
    # "Unknown" — i.e. the entire point of a visa-sponsorship tool silently
    # evaluated to nothing, with only a one-line WARNING to say so.
    #
    # The real classifier now lives here so the file is genuinely standalone.
    # It is evidence-based, never keyword-alone: every hit is judged inside
    # its own sentence, with negation / requirement / conditional qualifiers,
    # in EN + DE + IT + NL + FR + ES.
    # ─────────────────────────────────────────────────────────────────────
    VERDICT_YES, VERDICT_NO, VERDICT_UNKNOWN = "Yes", "No", "Unknown"

    _JD_SENT_SPLIT = re.compile(r"(?<=[.!?;:])\s+|[\n\r]+|\s*[•·▪]\s*")

    # Sponsorship / relocation offered by the employer.
    _VISA_POS = re.compile(
        r"(visa\s+sponsorship|sponsor(?:ing|ship)?\s+(?:a\s+)?(?:work\s+)?visa|"
        r"we\s+sponsor|will\s+sponsor|can\s+sponsor|able\s+to\s+sponsor|"
        r"sponsorship\s+(?:is\s+)?(?:available|offered|provided)|"
        r"work\s+permit\s+(?:support|assistance|sponsorship)|"
        r"visa\s+(?:support|assistance)|immigration\s+support|"
        r"visum(?:sponsoring|unterst[üu]tzung)|arbeitserlaubnis|"
        r"sponsorizzazione\s+(?:del\s+)?visto|visto\s+di\s+lavoro|"
        r"visumsponsoring|werkvergunning|"
        r"parrainage\s+de\s+visa|patrocinio\s+de\s+visado)", re.I)

    _RELOC_POS = re.compile(
        r"(relocation\s+(?:package|support|assistance|bonus|allowance|budget|"
        r"expenses|costs?)|we\s+(?:offer|provide|cover)[^.]{0,40}relocat|"
        r"relocation\s+(?:is\s+)?(?:available|offered|provided|covered|reimbursed)|"
        r"help\s+(?:you\s+)?relocat|assist[^.]{0,30}relocat|"
        r"umzugs(?:kosten|hilfe|paket|unterst[üu]tzung)|"
        r"pacchetto\s+di\s+trasferimento|supporto\s+al\s+trasferimento|"
        r"verhuis(?:kosten|vergoeding|pakket)|"
        r"aide\s+[àa]\s+la\s+relocalisation|ayuda\s+(?:a|para)\s+la\s+reubicaci[óo]n)", re.I)

    # Explicit refusals.
    _NEG_NEAR = re.compile(
        r"\b(no|not|non|nicht|keine|geen|pas\s+de|sin|unable|cannot|can'?t|"
        r"won'?t|will\s+not|do\s+not|does\s+not|are\s+not|is\s+not|without|"
        r"unfortunately|regrettably|ineligible|excluded)\b", re.I)
    _NEG_PHRASE = re.compile(
        r"(no\s+(?:visa\s+)?sponsorship|not\s+(?:able\s+to\s+)?sponsor|"
        r"cannot\s+sponsor|can'?t\s+sponsor|unable\s+to\s+sponsor|"
        r"do(?:es)?\s+not\s+(?:offer|provide)\s+(?:visa\s+)?sponsorship|"
        r"sponsorship\s+is\s+not\s+(?:available|offered|provided)|"
        r"no\s+relocation|relocation\s+is\s+not\s+(?:available|offered|provided|covered)|"
        r"without\s+(?:visa\s+)?sponsorship|"
        r"keine\s+(?:visum|umzugs)|geen\s+(?:visum|verhuis))", re.I)

    # Candidate must already be authorised / must move at own cost.
    _REQUIRE = re.compile(
        r"(must\s+(?:already\s+)?(?:have|hold|possess|be)\s+[^.]{0,50}"
        r"(?:authoriz|authoris|permit|visa|right\s+to\s+work|eligib)|"
        r"(?:legally\s+)?authoriz(?:ed|ation)\s+to\s+work|"
        r"right\s+to\s+work\s+in|eligible\s+to\s+work\s+in|"
        r"valid\s+(?:work\s+)?(?:permit|visa)\s+(?:is\s+)?required|"
        r"requires?\s+[^.]{0,30}work\s+permit|"
        r"applicants?\s+must\s+be\s+[^.]{0,40}(?:citizen|resident)|"
        r"willing(?:ness)?\s+to\s+relocate|ready\s+to\s+relocate|"
        r"at\s+(?:your|their|own)\s+(?:own\s+)?(?:cost|expense))", re.I)

    # Hedged / case-by-case.
    _CONDITIONAL = re.compile(
        r"(may\s+be\s+(?:available|provided|offered|considered)|"
        r"case[- ]by[- ]case|on\s+a\s+case|depending\s+on|"
        r"where\s+applicable|if\s+(?:applicable|eligible|required|needed)|"
        r"potential(?:ly)?|possibl[ey]|could\s+be\s+(?:available|provided|offered)|"
        r"subject\s+to|at\s+(?:our|the\s+company'?s)\s+discretion)", re.I)

    _BLUE_CARD = re.compile(
        r"\b(?:eu\s+)?blue[\s-]?card\b|blaue\s+karte|carta\s+blu|"
        r"blauwe\s+kaart|carte\s+bleue|tarjeta\s+azul", re.I)

    class JDSupportDetector:
        """Sentence-scoped, negation-aware visa/relocation classifier.

        Same public API as sponsorscout.scanning.jd_support.JDSupportDetector:
            detect(text)                -> {"visa": {...}, "relocation": {...}}
            best_evidence(result, limit)-> str
            split_sentences(text)       -> list[str]
        """

        def split_sentences(self, text):
            if not text:
                return []
            return [s.strip() for s in _JD_SENT_SPLIT.split(text) if s and s.strip()]

        def _classify(self, sentences, pos_re):
            verdict, conf, evidence = VERDICT_UNKNOWN, 0.0, []
            required = False
            for sent in sentences:
                s = sent[:400]
                hit_pos = pos_re.search(s)
                hit_negphrase = _NEG_PHRASE.search(s)
                hit_require = _REQUIRE.search(s)

                # 1. Explicit refusal wins outright.
                if hit_negphrase:
                    if conf < 0.95:
                        verdict, conf = VERDICT_NO, 0.95
                        evidence = [s]
                    continue

                # 2. "You must already be authorised" / "willing to relocate"
                #    => the employer is NOT offering support.
                if hit_require and not hit_pos:
                    required = True
                    if conf < 0.7:
                        verdict, conf = VERDICT_NO, 0.7
                        evidence = [s]
                    continue

                if not hit_pos:
                    continue

                # 3. Positive phrase, but check for negation in the same clause.
                window = s[max(0, hit_pos.start() - 60):hit_pos.end() + 20]
                if _NEG_NEAR.search(window):
                    if conf < 0.9:
                        verdict, conf = VERDICT_NO, 0.9
                        evidence = [s]
                    continue

                # 4. Hedged language => Unknown, do not claim a Yes.
                if _CONDITIONAL.search(s):
                    if conf < 0.4:
                        verdict, conf = VERDICT_UNKNOWN, 0.4
                        evidence = [s]
                    continue

                # 5. Clean positive.
                if hit_require:
                    required = True
                if conf < 0.9:
                    verdict, conf = VERDICT_YES, 0.9
                    evidence = [s]
            return {"verdict": verdict, "confidence": conf,
                    "evidence": evidence, "required": required}

        def detect(self, text):
            sents = self.split_sentences(text or "")
            visa = self._classify(sents, _VISA_POS)
            reloc = self._classify(sents, _RELOC_POS)
            return {
                "visa": {"verdict": visa["verdict"], "confidence": visa["confidence"],
                         "evidence": visa["evidence"]},
                "relocation": {"verdict": reloc["verdict"], "confidence": reloc["confidence"],
                               "evidence": reloc["evidence"], "required": reloc["required"]},
            }

        def best_evidence(self, result, limit=2):
            ev = (result or {}).get("evidence") or []
            return "; ".join(e[:300] for e in ev[:limit])

    def detect_blue_card(detector, desc):
        """Blue Card is judged independently of general visa sponsorship."""
        if not desc:
            return VERDICT_UNKNOWN
        for sent in detector.split_sentences(desc):
            if not _BLUE_CARD.search(sent):
                continue
            if _NEG_PHRASE.search(sent) or _NEG_NEAR.search(sent):
                return VERDICT_NO
            if _CONDITIONAL.search(sent):
                return VERDICT_UNKNOWN
            return VERDICT_YES
        return VERDICT_UNKNOWN

# ───────────── COUNTRY RESOLVER (G3 Europe target) ─────────────
try:
    from sponsorscout.core.location_country import country_from_location
except ImportError:  # standalone single-file mode
    # FIX P0-16b: resolve a country from a location string without the app
    # repo. Order matters: explicit country name/alias first, then the
    # gazetteer (installed separately), then None. Kept deliberately small —
    # EUROPE_COUNTRIES/_COUNTRY_ALIASES below are the real allowlist.
    def country_from_location(location):
        text = (location or "").strip()
        if not text:
            return None
        low = text.casefold()
        # Trailing segment is usually the country: "Milan, Italy".
        for seg in reversed([s.strip() for s in re.split(r"[,/|·—–-]", text) if s.strip()]):
            seg_l = seg.casefold()
            try:
                alias = _COUNTRY_ALIASES.get(seg_l)
            except NameError:
                alias = None
            if alias:
                return alias
            try:
                if seg_l in EUROPE_COUNTRIES:
                    return seg_l
            except NameError:
                pass
        try:
            for country in EUROPE_COUNTRIES:
                if re.search(r"\b" + re.escape(country) + r"\b", low):
                    return country
        except NameError:
            pass
        # Gazetteer fallback (returns (city, country) or None).
        try:
            hit = gazetteer_lookup(text, text)
            if hit and hit[1]:
                return hit[1].casefold()
        except Exception:
            pass
        return None

# ───── legacy comment block retained below for historical context ─────
# Context-aware detection of Visa Sponsorship / Relocation Support in JD text.
# Never matches keywords alone: every mention is judged within its sentence/
# clause, with negation / requirement / conditional / scope qualifiers.
#   "We do NOT support relocation"              -> No      (negated)
#   "We support if you are READY to relocate"   -> No      (candidate must move)
#   "may be provided case-by-case"              -> Unknown (conditional)
# ───────────────────────── SCANNER ─────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# SEED UPGRADE TABLE (v7 final)
#
# The v7 scanner reads either schema:
#   • v7  (seed_name, canonical_name, source_type, target_country, scope_policy,
#          provider, board_slug, careers_url, ...)
#   • v6  (name, ats_type, careers_url, industry, sponsorship_history,
#          english_friendly, remote_score)
#
# A v6 seed carries none of the v7 metadata (recruiter tagging, country scope,
# provider/board slugs) and several rows carry wrong or placeholder URLs. Rather
# than fail or silently degrade, the scanner upgrades known v6 rows in-memory
# using this table. Every applied correction is logged to stdout and recorded in
# the per-run scan log so nothing is hidden.
#
# Keyed by seed `name` (case-insensitive). Omit a key and the row keeps v6
# defaults (direct_employer / Global / global / auto).
# ─────────────────────────────────────────────────────────────────────────────
SEED_UPGRADE = {
    # ── Recruiters: separate file + country scope ────────────────────────────
    "DevsData":                     {"source_type": "recruiter"},
    "Harnham Germany":              {"source_type": "recruiter", "target_country": "Germany", "scope_policy": "job_location"},
    "Hays Germany":                 {"source_type": "recruiter", "target_country": "Germany", "scope_policy": "seed_url"},
    "Huxley Netherlands":           {"source_type": "recruiter", "target_country": "Netherlands", "scope_policy": "seed_url"},
    "Kelly Services Germany":       {"source_type": "recruiter", "target_country": "Germany", "scope_policy": "job_location"},
    "La Fosse":                     {"source_type": "recruiter"},
    "Michael Page Germany":         {"source_type": "recruiter", "target_country": "Germany", "scope_policy": "seed_url"},
    "Michael Page Netherlands":     {"source_type": "recruiter", "target_country": "Netherlands", "scope_policy": "seed_url"},
    "Michael Page UK":              {"source_type": "recruiter", "target_country": "United Kingdom", "scope_policy": "seed_url"},
    "Morgan McKinley":              {"source_type": "recruiter"},
    "Nigel Frank":                  {"source_type": "recruiter"},
    "Reperio Human Capital":        {"source_type": "recruiter"},
    "Robert Walters Germany":       {"source_type": "recruiter", "scope_policy": "seed_url"},
    "Robert Walters Ireland":       {"source_type": "recruiter", "target_country": "Ireland", "scope_policy": "seed_url"},
    "Robert Walters Netherlands":   {"source_type": "recruiter", "target_country": "Netherlands", "scope_policy": "seed_url"},
    "Sigmar Recruitment":           {"source_type": "recruiter"},
    "Talentor Germany":             {"source_type": "recruiter", "target_country": "Germany", "scope_policy": "job_location"},
    "Understanding Recruitment":    {"source_type": "recruiter"},
    "Undutchables":                 {"source_type": "recruiter"},
    # ── Direct employers: country scope ──────────────────────────────────────
    "Amazon Italia":                {"target_country": "Italy", "scope_policy": "seed_url"},
    "Coop Italia":                  {"target_country": "Italy", "scope_policy": "seed_url",
                                     "careers_url": "https://lavoro.coopalleanza3-0.it/jobs.php"},
    "Decathlon Italia Retail":      {"target_country": "Italy", "scope_policy": "seed_url"},
    "Enel":                         {"target_country": "Italy", "scope_policy": "seed_url"},
    "Ikea Italia":                  {"target_country": "Italy", "scope_policy": "job_location"},
    "McDonald's Italia":            {"target_country": "Italy", "scope_policy": "seed_url"},
    "MSD Netherlands":              {"target_country": "Netherlands", "scope_policy": "job_location"},
    "Nexthink (Germany)":           {"target_country": "Germany", "scope_policy": "job_location"},
    "Pam Panorama":                 {"target_country": "Italy", "scope_policy": "seed_url"},
    # ── NEW: Italian hotels/retail added 2026-09-05 ───────────────────────────
    "Eataly Italia":                {"target_country": "Italy", "scope_policy": "seed_url"},
    "Gruppo UNA":                   {"target_country": "Italy", "scope_policy": "seed_url"},
    "NH Hotel Group Italy":         {"target_country": "Italy", "scope_policy": "job_location",
                                     "notes": "URL is generic minorhotels.com/search; job_location scope "
                                              "ensures only Italy-scoped jobs pass"},
    "Oniverse":                     {"target_country": "Italy", "scope_policy": "seed_url"},
    "OVS":                          {"target_country": "Italy", "scope_policy": "seed_url"},
    "Starhotels":                   {"target_country": "Italy", "scope_policy": "seed_url"},
    # ── Provider API (avoids fragile DOM scraping) ───────────────────────────
    "Airbyte":                      {"provider": "ashby", "board_slug": "airbyte"},
    "American Express":             {"provider": "oracle"},
    "Appodeal":                     {"provider": "greenhouse", "board_slug": "appodeal"},
    "Babbel":                       {"provider": "ashby", "board_slug": "Babbel"},
    "Buena":                        {"provider": "ashby", "board_slug": "Buena"},
    "Bynder":                       {"provider": "ashby", "board_slug": "Bynder"},
    "Catawiki":                     {"provider": "greenhouse", "board_slug": "catawiki",
                                     "careers_url": "https://job-boards.greenhouse.io/catawiki"},
    "Choco":                        {"provider": "ashby", "board_slug": "Choco"},
    "Forto":                        {"provider": "ashby", "board_slug": "Forto"},
    "HelloFresh":                   {"provider": "greenhouse", "board_slug": "hellofresh"},
    "Klarna":                       {"provider": "deel"},
    "KONUX":                        {"provider": "greenhouse", "board_slug": "KONUX"},
    "Lightspeed":                   {"provider": "ashby", "board_slug": "Lightspeed"},
    "Notion":                       {"provider": "ashby", "board_slug": "notion"},
    "Personio":                     {"provider": "custom",
                                     "careers_url": "https://www.personio.com/about-personio/careers/"},
    "Pitch":                        {"provider": "workable", "board_slug": "pitch-software"},
    "Planhat":                      {"provider": "ashby", "board_slug": "Planhat"},
    "Poste Italiane":               {"provider": "oracle"},
    "Rows":                         {"provider": "ashby", "board_slug": "Rowspace"},
    "Scorewarrior":                 {"provider": "ashby", "board_slug": "scorewarrior"},
    "Skyscanner":                   {"provider": "greenhouse", "board_slug": "skyscanner"},
    "Spendesk":                     {"provider": "personio", "board_slug": "spendesk"},
    "Teamtailor":                   {"provider": "teamtailor"},
    # ── Disabled: broken/unrelated host, do not scrape ───────────────────────
    "SNAM":                         {"enabled": False,
                                     "notes": "Disabled: official careers host (carriere.snam.it) is unreachable; "
                                              "v6 URL pointed at unrelated snamanalytics.in"},
}


_DOWNLOAD_URL_RE = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|zip|rar|csv|ics)(?:$|[?#])|eventDownload|/download\b",
    re.IGNORECASE,
)

# FIX P0-1b: static assets must never be treated as job URLs. The widened
# careers-path rule would otherwise admit /careers/foo.css, /assets/.../careers.js
_ASSET_URL_RE = re.compile(
    r"\.(css|js|mjs|json|xml|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|eot|map)(?:$|[?#])"
    r"|/wp-content/|/wp-includes/|/_next/static/|/static/|/assets?/|/dist/|/build/",
    re.IGNORECASE,
)


def _is_download_url(url: str) -> bool:
    """True for binary/download URLs that can never be parsed as job pages
    (Eni PDFs, Lidl eventDownload, ...). Skipped before any fetching."""
    return bool(url and _DOWNLOAD_URL_RE.search(url))


def _dns_resolves(host: str) -> bool:
    """Fail-fast DNS check so dead hosts skip browser retries entirely.

    Batch F2: one retry after 10s — survives sub-minute resolver blips
    (the 09-12/13 runs lost 72+20 targets to 11-27s outages).

    FIX P0-15: the 09-15 run lost 24 consecutive companies (La Fosse ->
    Picnic, alphabetically contiguous) to a resolver outage that outlived
    the old 2-try/10s window. Re-testing those hosts afterwards showed 7 of
    8 resolving fine. Now: 3 attempts, 5s/15s backoff, and AF_UNSPEC so an
    IPv6-only answer still counts as alive.
    """
    if not host:
        return False
    delays = (5, 15, 0)
    for attempt, delay in enumerate(delays, 1):
        try:
            socket.getaddrinfo(host, 443)  # AF_UNSPEC: A or AAAA both count
            return True
        except Exception:
            if delay:
                time.sleep(delay)
    return False


def _is_dns_error(exc: Exception) -> bool:
    """True when an exception is a DNS-resolution failure (F2)."""
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(s in text for s in (
        "getaddrinfo", "name or service not known", "nodename nor servname",
        "11001",  # Windows: getaddrinfo failed
        "dns", "nameserver", "name resolution",
    ))


# ─────────────────────── GAZETTEER (P0-9) ───────────────────────
# Replaces the hand-maintained ~600-entry city allowlist. Optional dependency:
# `pip install geonamescache`. If absent the scanner behaves exactly as before.
#
# Safety rules (why naive lookup was NOT shipped earlier):
#   • ambiguous names resolve by (1) country hint elsewhere in the string,
#     (2) US state code hint, (3) highest population — never first-match.
#   • a name is only accepted if population >= _GAZ_MIN_POP, killing hamlet
#     collisions ("Phoenix" -> Vacoas MU, "Brighton" -> Brighton AU).
try:
    import geonamescache as _gnc
    _GAZ = _gnc.GeonamesCache()
except Exception:
    _GAZ = None

_GAZ_MIN_POP = 5000
_GAZ_INDEX = {}
_GAZ_COUNTRY_BY_CODE = {}
_GAZ_COUNTRY_NAMES = {}


def _gaz_norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.lower().strip().split())


def _build_gazetteer():
    """Index city name + local spellings -> list of candidate records."""
    if _GAZ is None or _GAZ_INDEX:
        return
    try:
        for c in _GAZ.get_countries().values():
            _GAZ_COUNTRY_BY_CODE[c["iso"].lower()] = c["name"]
            _GAZ_COUNTRY_NAMES[_gaz_norm(c["name"])] = c["iso"].lower()
        for v in _GAZ.get_cities().values():
            pop = int(v.get("population") or 0)
            if pop < _GAZ_MIN_POP:
                continue
            rec = (v["name"], v["countrycode"].lower(), pop,
                   (v.get("admin1code") or "").lower())
            names = {v["name"]}
            for alt in (v.get("alternatenames") or []):
                if alt and 2 < len(alt) <= 40:
                    names.add(alt)
            for nm in names:
                _GAZ_INDEX.setdefault(_gaz_norm(nm), []).append(rec)
    except Exception:
        _GAZ_INDEX.clear()


def gazetteer_lookup(segment, context=""):
    """Resolve one segment to (City, Country). None when unknown/ambiguous."""
    if _GAZ is None:
        return None
    _build_gazetteer()
    key = _gaz_norm(segment)
    cands = _GAZ_INDEX.get(key)
    if not cands:
        return None
    # Prefer records whose CANONICAL name matches exactly; an alternate-name
    # hit on a different city ("Selangor" -> "Kuala Selangor") is a downgrade.
    # STRICT: only accept an exact canonical-name match. An alternate-name hit
    # pointing at a different place ("Selangor" -> "Kuala Selangor") is wrong;
    # an honest Unknown is better than a confidently wrong city.
    # Prefer an exact canonical-name match. Fall back to alternate-name hits
    # ONLY when they all point at the same place (so "Warszawa"->Warsaw works,
    # while "Selangor"->Kuala Selangor, an ambiguous downgrade, is refused).
    exact = [c for c in cands if _gaz_norm(c[0]) == key]
    if exact:
        cands = exact
    elif len({(c[0], c[1]) for c in cands}) != 1:
        return None
    else:
        # Single alternate-name hit: accept only if it is a true alias
        # (Warszawa->Warsaw, Antwerpen->Antwerp) and NOT a larger admin area
        # resolving to a city inside it ("Selangor" -> "Kuala Selangor").
        only = cands[0][0]
        if _gaz_norm(only) != key and key in _gaz_norm(only):
            return None
    ctx = _gaz_norm(context)
    if len(cands) > 1:
        # 1) explicit country name/code in the surrounding text
        for name, iso in _GAZ_COUNTRY_NAMES.items():
            if name and re.search(r"(?:^|[^a-z])" + re.escape(name) + r"(?:$|[^a-z])", ctx):
                match = [c for c in cands if c[1] == iso]
                if match:
                    cands = match
                    break
        else:
            toks = set(re.findall(r"\b[a-z]{2}\b", ctx))
            iso_hits = {t for t in toks if t in _GAZ_COUNTRY_BY_CODE}
            match = [c for c in cands if c[1] in iso_hits] if iso_hits else []
            if match:
                cands = match
            else:
                st = [c for c in cands if c[3] and c[3] in toks and c[1] == "us"]
                if st:
                    cands = st
    best = max(cands, key=lambda c: c[2])  # population tie-break
    country = _GAZ_COUNTRY_BY_CODE.get(best[1], best[1].upper())
    return best[0], country


_ERROR_COLUMNS = [
    "Run ID", "Timestamp", "Seed Name", "Phase", "Error Type", "Message", "Seed URL",
]

# Batch K: one-shot flag for the standalone browser pre-check notice (see below).
_BROWSER_PRECHECK_WARNED = False


# Batch F3: postal-anchored location stubs ("Monroe, OH, US, 45050" —
# 219 accepted in the 09-13 run). US ZIP / CA postcode / JP-style codes;
# search (not fullmatch) tolerates UI tails like "+1 more".
_F3_POSTAL_RE = re.compile(
    r"(?<![\dA-Z])\d{5}(?:-\d{4})?(?!\d)"
    r"|(?<![\dA-Z])[A-Z]\d[A-Z]\s?\d[A-Z]\d(?![\dA-Z])"
    r"|(?<!\d)\d{3}-\d{4}(?!\d)",
    re.IGNORECASE,
)
# Role words missing from config.ROLE_WORD_PATTERN but needed so F3 never
# eats real comma titles ("Team Leader, Monroe, OH" stays valid).
_F3_ROLE_EXTRA_RE = re.compile(
    r"\b(leader|leaders|supervisor|optometrist|optician)\b", re.IGNORECASE
)


# Batch G3: Europe region target = EU + EEA + UK + Switzerland: the
# sponsorship-relevant footprint (EU Blue Card + adjacent regimes).
# EUROPE_COUNTRIES matches location_country.country_from_location()
# output (casefolded at check time). EUROPE_ALIASES is the normed
# blob-substring fallback (context/URL evidence + standalone mode).
# Deliberately no bare "europe"/"eu": too weak in URLs (regional portal
# roots) and "eu" collides with Romanian "I" in context text.
EUROPE_COUNTRIES = frozenset({
    "austria", "belgium", "bulgaria", "croatia", "cyprus",
    "czech republic", "denmark", "estonia", "finland", "france",
    "germany", "greece", "hungary", "iceland", "ireland", "italy",
    "latvia", "liechtenstein", "lithuania", "luxembourg", "malta",
    "netherlands", "norway", "poland", "portugal", "romania",
    "slovakia", "slovenia", "spain", "sweden", "switzerland",
    "united kingdom",
})
EUROPE_ALIASES = frozenset({
    "austria", "belgium", "bulgaria", "croatia", "cyprus",
    "czech republic", "czechia", "czech", "denmark", "estonia",
    "finland", "france", "germany", "deutschland", "greece", "hungary",
    "iceland", "ireland", "italy", "italia", "latvia", "liechtenstein",
    "lithuania", "luxembourg", "malta", "netherlands", "nederland",
    "norway", "poland", "portugal", "romania", "slovakia", "slovenia",
    "spain", "espana", "sweden", "switzerland", "united kingdom", "uk",
    "england", "scotland", "wales",
})

# FIX P0-7: European city -> country index for bare-city scope checks.
_EUROPE_CITY_INDEX = frozenset({
    # DE
    "berlin", "munchen", "munich", "hamburg", "koln", "cologne", "frankfurt",
    "stuttgart", "dusseldorf", "dortmund", "essen", "leipzig", "bremen",
    "dresden", "hannover", "nurnberg", "duisburg", "bochum", "wuppertal",
    "bielefeld", "bonn", "munster", "karlsruhe", "mannheim", "augsburg",
    "wiesbaden", "mainz", "veldhoven", "walldorf", "garching",
    # NL
    "amsterdam", "rotterdam", "utrecht", "eindhoven", "haarlem", "delft",
    "groningen", "tilburg", "almere", "breda", "nijmegen", "hilversum",
    # IT
    "milano", "milan", "roma", "rome", "torino", "turin", "napoli", "naples",
    "bologna", "firenze", "florence", "venezia", "venice", "genova", "parma",
    "verona", "padova", "trieste", "bari", "catania", "palermo",
    # FR
    "paris", "lyon", "marseille", "toulouse", "lille", "bordeaux", "nantes",
    "nice", "strasbourg", "montpellier", "rennes", "grenoble",
    # ES / PT
    "madrid", "barcelona", "valencia", "sevilla", "seville", "bilbao",
    "malaga", "zaragoza", "lisbon", "lisboa", "porto", "braga", "coimbra",
    # UK / IE
    "london", "manchester", "birmingham", "edinburgh", "glasgow", "leeds",
    "bristol", "liverpool", "sheffield", "cardiff", "belfast", "cambridge",
    "oxford", "reading", "newcastle", "nottingham", "dublin", "cork",
    "galway", "limerick",
    # Nordics
    "stockholm", "gothenburg", "goteborg", "malmo", "uppsala", "copenhagen",
    "kobenhavn", "aarhus", "odense", "oslo", "bergen", "trondheim",
    "helsinki", "espoo", "tampere", "reykjavik",
    # CEE / other
    "warsaw", "warszawa", "krakow", "wroclaw", "poznan", "gdansk", "lodz",
    "prague", "praha", "brno", "bratislava", "budapest", "bucharest",
    "bucuresti", "cluj", "sofia", "zagreb", "ljubljana", "belgrade",
    "tallinn", "riga", "vilnius", "athens", "thessaloniki", "valletta",
    "nicosia", "limassol", "luxembourg",
    # AT / CH / BE
    "vienna", "wien", "graz", "salzburg", "linz", "innsbruck", "zurich",
    "geneva", "geneve", "basel", "bern", "lausanne", "lugano",
    "brussels", "bruxelles", "antwerp", "antwerpen", "ghent", "gent",
    "leuven", "liege", "bruges", "waregem",
})


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
                  r"ans|anos?|an|a[nñ]os|a[nñ]o)")
_EXP_MONTH_UNIT = (r"(?:months?|mon\.?|monate?n?|mesi|mese|maanden|maand|"
                   r"mois|meses|mes)")

# The number must sit in a clause genuinely about work experience.
_EXP_ANCHOR = re.compile(
    r"experien|berufserfahrung|erfahrung|praxis|esperienz|ervaring|"
    r"exp[ée]rien|experienc|experi[eê]nci|experi[eê]ncia|experiencia|"
    r"similar role|comparable role|equivalent role|relevant|professional|"
    r"requirements?|qualif\w*|must[- ]haves?|anforderungen|"
    r"voraussetzungen|requisiti|requisitos|vereisten|wer du bist|"
    r"what you|das bringen|nous recherchons|buscamos|"
    r"hands[-\s]?on|proven|track record|background in|working (?:in|with|as)|"
    r"seniority|vergleichbarer? (?:position|rolle)|einschl[äa]gig|"
    r"ruolo simile|ambito|settore|soortgelijke|vergelijkbare|"
    r"poste similaire|puesto similar|en el (?:sector|[áa]rea)",
    re.I)

# "4+ years in Office Management" carries a real requirement but no experience
# NOUN for _EXP_ANCHOR to catch. Same shape exists in every seed language:
# "3 anni nel settore vendite", "4 Jahre im Vertrieb", "3 jaar in sales",
# "3 ans dans le domaine", "3 años en el sector". Connectors are deliberately
# narrow — bare "en" is excluded because it would also match "20 ans en France".
_EXP_YEARS_IN = re.compile(
    r"(?:\d{1,2}|" + _EXP_WORD_ALT + r")\s*(?:\+|\bplus\b)?\s*"
    r"(?:" + _EXP_YEAR_UNIT + r"|" + _EXP_MONTH_UNIT + r")\s+"
    r"(?:in|of|as|im|als|bei|nel|nella|nello|come|presso|"
    r"dans le|dans la|au sein|en el|en la|como)\s+[A-Za-zÀ-ÿ]", re.I)

# Disqualifiers, checked in a tight window around the number so a sentence
# that merely also mentions a degree is not discarded wholesale.
_EXP_BLOCK_NEAR = re.compile(
    r"\b(?:old|of age|age of|ago|"
    r"last|past|next|recent|"
    r"founded|established|since|anniversar|"
    r"seit|dal|desde|depuis|sinds|vanaf|"
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


class CareerPortalScanner:
    def __init__(self, input_csv="company_Career_seed.csv", output_csv="scraped_jobs_v7.csv",
                 max_workers=None, detail_scan=False, resume=False,
                 allow_synthetic=False, skip_preflight=False, cancel_event=None,
                 only_companies=None, max_company_time_sec=None, pause_event=None):
        self.input_csv = input_csv
        self.output_csv = output_csv
        # Host-adaptive company-level concurrency.  Each worker drives its own
        # Chromium context (~150-400 MB resident), so the previous fixed 3 could
        # exhaust an 8 GB / 2-core machine and freeze the desktop.  None means
        # "size the pool for this host"; an explicit value is always honoured.
        try:
            self.max_workers = max(1, int(max_workers)) if max_workers else recommended_workers("browser")
        except (TypeError, ValueError):
            self.max_workers = recommended_workers("browser")
        self.detail_scan = detail_scan or ProductionScannerConfig.ENABLE_DETAIL_SCAN
        self.resume = bool(resume)
        # J1: retained for API compatibility but no longer gates anything —
        # #job= fragment URLs are treated as real job URLs (see process_job).
        self.allow_synthetic = bool(allow_synthetic)
        # Cooperative cancellation for the desktop Stop button: checked
        # before submitting each crawl target; in-flight targets finish.
        self.cancel_event = cancel_event
        # Cooperative suspension for the desktop Pause button: while set, the
        # workers block in check_control() without ending the run, so Resume
        # continues in-place (no DB checkpoint, no new run_id).
        self.pause_event = pause_event
        # Optional whitelist of company names (CLI --company): when set, only
        # these targets are scanned.
        self.only_companies = only_companies
        self.run_id = time.strftime("%Y%m%dT%H%M%S")
        self.config = ProductionScannerConfig()
        self.config.PREFLIGHT_ENABLED = not bool(skip_preflight)
        # Finalise: CLI --max-company-time override for giant boards
        # (Luxottica/Hays cap at the 900s default with pages left).
        # Guarded: absurd values fall back to the default silently.
        if max_company_time_sec:
            try:
                override = int(max_company_time_sec)
            except (TypeError, ValueError):
                override = 0
            if override >= 60:
                self.config.MAX_COMPANY_TIME_SEC = override
        self.detector = JDSupportDetector()

        # Merge the extended country/region list into the base set (the seed config
        # set was missing many countries: ukraine, romania, greece, sweden, brazil...)
        self.config.COUNTRIES_AND_REGIONS = (
            set(self.config.COUNTRIES_AND_REGIONS) | ProductionScannerConfig.REGIONS_EXTRA
        )
        self.ALL_REGIONS = set(self.config.COUNTRIES_AND_REGIONS)

        # Build the known-places index (countries + regions + single/multi-word cities)
        self.KNOWN_CITIES = set(self.config.KNOWN_CITIES) | {"wuxi", "hefei", "kunshan",
            "dongguan", "foshan", "zhengzhou", "changsha", "nanchang", "fuzhou",
            "ningbo", "xiamen", "suzhou", "odense", "aarhus", "esbjerg", "kolding",
            "horsens", "vejle", "roskilde", "herning", "silkeborg", "randers",
            # common US cities (seen in scraped store/retail data)
            "rochester", "sanford", "katy", "waco", "coconut creek", "pembroke pines",
            "lone tree", "centennial", "westminster", "arvada", "thornton",
            "youngstown", "weston", "welland", "thousand oaks", "saugus",
            "saratoga springs", "pleasant prairie", "parsippany", "medford",
            "casselberry", "bentonville", "beavercreek", "aventura", "arlington",
            "hillsboro", "wetzlar", "chillicothe", "athens",
            "giessen", "yixing", "westlake", "wetherill", "johor bahru",
            "manhasset", "garden city", "great neck", "huntington", "levittown",
            # German cities + ASCII transliterations (Hays URL slugs)
            "ulm", "boeblingen", "böblingen", "duesseldorf", "düsseldorf",
            "koeln", "köln", "muenchen", "münchen", "nuernberg", "nürnberg",
            "zuerich", "zürich", "wuerzburg", "würzburg", "fuerth", "fürth",
            "gelsenkirchen", "bochum", "kassel", "kiel", "luebeck", "lübeck",
            "flensburg", "osnabrueck", "osnabrück", "münster", "munster",
            "aachen", "leverkusen", "solingen", "rüsselsheim", "ruesselsheim",
            "darmstadt", "offenbach", "hanau", "marburg", "giessen", "gießen",
            "koblenz", "trier", "saarbruecken", "saarbrücken", "mainz",
            "wiesbaden", "karlsruhe", "mannheim", "heidelberg", "freiburg",
            "reutlingen", "tuebingen", "tübingen", "stuttgart", "ulm",
            "augsburg", "regensburg", "ingolstadt", "passau", "wolfsburg",
            "braunschweig", "hannover", "bremen", "hamburg", "kiel", "rostock",
            "schwerin", "potsdam", "magdeburg", "erfurt", "jena", "leipzig",
            "dresden", "chemnitz", "cottbus", "halle", "bielefeld", "paderborn",
            "guetersloh", "gütersloh", "minden", "detmold", "siegen",
            "wuppertal", "rheinberg", "krefeld", "moenchengladbach",
            "mönchengladbach", "neuss", "duisburg", "oberhausen", "essen",
            "dortmund", "herne", "bochum", "hagen", "witten", "hamm",
            "boynton beach", "jupiter", "palm beach gardens",
            "deltona", "sanford", "coral gables", "kendall", "homestead",
            "wake forest", "buffalo", "kissimmee", "st augustine", "destin", "lecanto",
            "springfield", "tallahassee", "fort myers", "naples", "gainesville",
            "ocala", "daytona beach", "delray beach", "boca raton", "west palm beach",
            "melbourne", "vero beach", "hollywood", "doral", "hialeah",
            "coral springs", "plantation", "sunrise", "davie", "miramar",
            "clermont", "oviedo", "winter park", "lakeland", "port orange",
            "new smyrna beach", "flagler beach", "palm coast",
            "plano", "irving", "garland", "frisco", "mckinney", "denton",
            "nashville", "memphis", "knoxville", "chattanooga",
            "raleigh", "charlotte", "greensboro", "winston-salem", "durham",
            "fayetteville", "cary", "wilmington", "asheville", "greenville",
            "columbia", "charleston", "aiken", "anderson", "savannah",
            "augusta", "macon", "albany", "athens",
            "birmingham", "montgomery", "tuscaloosa",
            "new orleans", "baton rouge", "shreveport", "lafayette",
            "oklahoma city", "tulsa", "lawton", "norman", "wichita", "topeka",
            "omaha", "lincoln", "des moines", "cedar rapids", "davenport",
            "kansas city", "st louis", "jefferson city",
            "little rock", "fort smith"}
        self.MULTI_WORD_CITIES = set(self.config.MULTI_WORD_CITIES)
        # more global cities (Ferrero/SAP/ASML detail pages)
        self.KNOWN_CITIES |= {
            "suqian", "kunshan", "changzhou", "nantong", "xuzhou", "yancheng",
            "yangzhou", "zhenjiang", "taizhou", "huai'an", "lianyungang",
            "wuhu", "bengbu", "ma'anshan", "fuyang", "jiaxing", "shaoxing",
            "jinhua", "wenzhou", "quanzhou", "zhangzhou", "putian",
            "jiujiang", "jingdezhen", "zhuzhou", "xiangtan", "hengyang",
            "yueyang", "guiyang", "kunming", "nanning", "liuzhou", "guilin",
            "haikou", "sanya", "shijiazhuang", "tangshan", "qinhuangdao",
            "handan", "baoding", "zhangjiakou", "taiyuan", "datong", "changzhi",
            "hohhot", "baotou", "harbin", "qiqihar", "mudanjiang", "jiamusi",
            "changchun", "anshan", "fushun", "lanzhou", "xining", "yinchuan",
            "urumqi", "kashgar", "mianyang", "leshan", "yibin", "xianyang",
            "baoji", "hancheng", "huangshi", "shiyan", "yichang", "xiangyang",
            "jingzhou", "xiaogan", "xianning", "suizhou", "changde",
            "zhangjiajie", "yiyang", "chenzhou", "yongzhou", "huaihua",
            "loudi", "shaoyang", "luzhou", "neijiang", "nanchong", "meishan",
            "guang'an", "suining", "zigong", "panzhihua", "ya'an", "bazhong",
            "ziyang",
        }
        # German state names (Hays URLs use them: nordrhein-westfalen, ...)
        self.config.COUNTRIES_AND_REGIONS = self.config.COUNTRIES_AND_REGIONS | {
            "bayern", "bavaria", "baden-wuerttemberg", "baden-württemberg",
            "nordrhein-westfalen", "north rhine-westphalia", "hessen", "hesse",
            "niedersachsen", "lower saxony", "rheinland-pfalz", "rhineland-palatinate",
            "sachsen", "saxony", "sachsen-anhalt", "saxony-anhalt",
            "thueringen", "thüringen", "thuringia", "schleswig-holstein",
            "mecklenburg-vorpommern", "saarland", "brandenburg", "rhein-main",
            "rhein-main-gebiet", "north holland", "noord-holland", "south holland",
            "zuid-holland", "north brabant", "noord-brabant", "gelderland",
            "flevoland", "drenthe", "overijssel", "zeeland",
        }
        # v7: refresh after adding regional aliases; v6 built region regexes from a stale copy.
        self.ALL_REGIONS = set(self.config.COUNTRIES_AND_REGIONS)
        self.KNOWN_PLACES = (
            set(self.config.COUNTRIES_AND_REGIONS)
            | self.KNOWN_CITIES
            | self.MULTI_WORD_CITIES
            | {
                "bavaria", "baden-wurttemberg", "north rhine-westphalia",
                "hesse", "saxony", "lower saxony", "rhineland-palatinate",
                "schleswig-holstein", "thuringia", "saarland", "brandenburg",
                "mecklenburg-vorpommern", "saxony-anhalt", "lombardy",
                "piedmont", "veneto", "emilia-romagna", "tuscany", "lazio",
                "campania", "puglia", "sicily", "sardinia", "liguria",
                "friuli-venezia giulia", "trentino-alto adige", "abruzzo",
                "umbria", "marche", "molise", "basilicata", "calabria",
                "england", "scotland", "wales", "northern ireland",
                "flanders", "wallonia", "catalonia", "andalusia", "basque",
                "galicia", "castile", "provence", "brittany", "normandy",
                "occitanie", "bavaria", "business bay", "east london",
                # US states
                "alabama", "alaska", "arizona", "arkansas", "california",
                "colorado", "connecticut", "delaware", "florida", "georgia",
                "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
                "kentucky", "louisiana", "maine", "maryland", "massachusetts",
                "michigan", "minnesota", "mississippi", "missouri", "montana",
                "nebraska", "nevada", "new hampshire", "new jersey",
                "new mexico", "north carolina", "north dakota", "ohio",
                "oklahoma", "oregon", "pennsylvania", "rhode island",
                "south carolina", "south dakota", "tennessee", "texas",
                "utah", "vermont", "virginia", "washington", "west virginia",
                "wisconsin", "wyoming", "district of columbia",
                # Canadian provinces
                "alberta", "british columbia", "manitoba", "new brunswick",
                "newfoundland", "newfoundland and labrador", "nova scotia",
                "ontario", "prince edward island", "quebec", "saskatchewan",
                # extra cities seen in data
                "sarasota", "huntsville", "wimbledon", "wilton", "chantilly",
                "newtown square", "malibu", "vista", "ventura", "warren",
                "waregem", "firenze", "xi'an", "makati",
                # Batch N: Italian cities from live Pam + DigitalRecruiters
                # API data (2026-09-12). Deliberately excluded (not cities):
                # zones/streets (artigianale-commerciale, pagliarone, riale,
                # cà bianca, zona industriale via beato francesco), provinces
                # (provincia di massa-carrara/venezia) and admin labels
                # (città metropolitana di *) — the Pam adapter normalizes
                # those to their city instead.
                # FIX P0-4: all Italian provincial capitals + common variants.
                # 23% of the seed is Italy-targeted and 18 capitals were missing,
                # so their locations resolved to "Unknown".
                "napoli", "naples", "firenze", "genova", "palermo", "catania",
                "bari", "verona", "padova", "brescia", "modena", "perugia",
                "cagliari", "trieste", "bergamo", "vicenza", "salerno",
                "rimini", "ravenna", "ferrara", "latina", "monza", "como",
                "udine", "pescara", "taranto", "livorno", "treviso", "lecce",
                "novara", "piacenza", "ancona", "sassari", "siracusa",
                "reggio calabria", "forlì", "forli", "cesena", "pisa", "lucca",
                "pistoia", "prato", "grosseto", "siena", "terni", "viterbo",
                "frosinone", "caserta", "avellino", "benevento", "foggia",
                "andria", "barletta", "trani", "brindisi", "potenza", "matera",
                "catanzaro", "cosenza", "crotone", "vibo valentia", "trapani",
                "messina", "agrigento", "caltanissetta", "enna", "nuoro",
                "oristano", "olbia", "asti", "cuneo", "biella", "vercelli",
                "verbania", "varese", "lecco", "sondrio", "cremona", "mantova",
                "lodi", "pavia", "savona", "imperia", "la spezia", "belluno",
                "rovigo", "pordenone", "gorizia", "bolzano", "bozen", "trento",
                "aosta", "macerata", "fermo", "ascoli piceno", "teramo",
                "chieti", "isernia", "campobasso", "rieti", "massa", "carrara",
                "pesaro", "urbino", "sanremo", "bolzano-bozen",
                # regions not already present
                "sardegna", "sardinia", "trentino-alto adige", "valle d'aosta",
                "molise", "basilicata", "calabria",
                "albenga", "alessandria", "altopascio", "arezzo",
                "barberino tavarnelle", "basiano", "busnago", "carugate",
                "cassino", "castel san pietro terme",
                "castelletto sopra ticino", "castenedolo",
                "cinisello balsamo", "colle di val d'elsa", "corsico",
                "dossobuono", "empoli", "faenza", "fidenza",
                "figline valdarno", "fiume veneto", "follonica", "formia",
                "gallarate", "genova", "grosseto", "gubbio", "imola",
                "ivrea", "l'aquila", "lido di ostia",
                "lignano sabbiadoro", "lissone", "lonato del garda",
                "milazzo", "moncalieri", "nettuno", "osnago", "padova",
                "perugia", "piacenza", "pistoia", "poirino", "pontedera",
                "pordenone", "porto sant'elpidio", "ragusa",
                "reggio emilia", "reggio nell'emilia", "rescaldina",
                "roncadelle", "rosate", "rozzano", "san fior", "sambuceto",
                "san donà di piave", "san mauro torinese", "san ġiljan",
                "sansepolcro", "santa vittoria d'alba",
                "santo stefano di magra", "savignano sul rubicone",
                "segrate", "seriate", "sesto fiorentino",
                "settimo torinese", "spinea-orgnano",
                "teramo", "thiene", "torre annunziata",
                "torri di quartesolo", "venezia", "viareggio",
                # Batch N: local-language country names (comma-combos such
                # as "Formia, Lazio, Italia" need the last segment known).
                "italia", "deutschland", "españa", "espana",
                "nederland", "österreich", "osterreich", "belgique",
                "belgië", "belgie", "schweiz", "suisse", "svizzera",
            }
        )

        # 2-letter country codes -> full names (for "Suqian CN" style LD strings)
        self.COUNTRY_CODES = {
            "cn": "china", "de": "germany", "us": "united states", "usa": "united states",
            "gb": "united kingdom", "uk": "united kingdom", "fr": "france", "it": "italy",
            "es": "spain", "nl": "netherlands", "be": "belgium", "ch": "switzerland",
            "at": "austria", "pl": "poland", "pt": "portugal", "se": "sweden", "no": "norway",
            "dk": "denmark", "fi": "finland", "ie": "ireland", "cz": "czechia",
            "sk": "slovakia", "hu": "hungary", "ro": "romania", "bg": "bulgaria",
            "gr": "greece", "hr": "croatia", "si": "slovenia", "rs": "serbia",
            "ee": "estonia", "lv": "latvia", "lt": "lithuania", "lu": "luxembourg",
            "mt": "malta", "cy": "cyprus", "tr": "turkey", "il": "israel", "ae": "uae",
            "qa": "qatar", "sa": "saudi arabia", "in": "india", "jp": "japan",
            "kr": "south korea", "sg": "singapore", "my": "malaysia", "th": "thailand",
            "vn": "vietnam", "ph": "philippines", "id": "indonesia", "au": "australia",
            "nz": "new zealand", "ca": "canada", "mx": "mexico", "br": "brazil",
            "ar": "argentina", "cl": "chile", "co": "colombia", "pe": "peru",
            "za": "south africa", "eg": "egypt", "ng": "nigeria", "ke": "kenya",
            "ma": "morocco", "tw": "taiwan", "hk": "hong kong", "ua": "ukraine",
            "ru": "russia", "kz": "kazakhstan",
        }
        # Diacritic-insensitive lookup index (Wrocław <-> wroclaw, München <-> munchen)
        self.NORM_KNOWN = {}
        for _p in self.KNOWN_PLACES:
            self.NORM_KNOWN.setdefault(self._norm(_p), _p)

        # US state + Canadian province codes (2-letter location codes we ACCEPT)
        self.STATE_CODES = {
            "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga",
            "hi", "id", "il", "in", "ia", "ks", "ky", "la", "me", "md",
            "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
            "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc",
            "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy",
            "dc", "ab", "bc", "mb", "nb", "nl", "ns", "nt", "nu", "on",
            "pe", "qc", "sk", "yt",
        }

        # Compiled location-reject regex (word/phrase boundaries)
        phrases = sorted(
            {p for p in self.config.LOCATION_REJECT_PHRASES if len(p) >= 3},
            key=len, reverse=True,
        )
        words = sorted(
            {w for w in self.config.LOCATION_REJECT_WORDS if len(w) >= 2},
            key=len, reverse=True,
        )
        self.LOCATION_REJECT_RE = re.compile(
            r"\b(?:" + "|".join(re.escape(p) for p in phrases) + r"|"
            + "|".join(re.escape(w) for w in words) + r")\b",
            re.IGNORECASE,
        )

        # Region/country detection regexes (end-of-string and end-of-line)
        regions = sorted(
            {r for r in self.ALL_REGIONS
             if r not in ("global", "worldwide", "united", "remote")},
            key=len, reverse=True,
        )
        region_alt = "|".join(re.escape(r) for r in regions)
        self.REGION_END_RE = re.compile(
            r"\b(" + region_alt + r")(?:[.,;!?)\]}\"'%]*)$", re.IGNORECASE)
        self.REGION_LINE_RE = re.compile(
            r"\b(" + region_alt + r")(?:[.,;!?)\]}\"'%]*)(?=$|[\n\r|•])",
            re.IGNORECASE | re.MULTILINE,
        )

        # Company names from the seed file (used to reject company-name-as-location)
        self.seed_company_names_lower = set()
        try:
            if os.path.exists(self.input_csv):
                with open(self.input_csv, newline="", encoding="utf-8-sig") as f:
                    sample = f.read(4096)
                    f.seek(0)
                    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
                    for row in csv.DictReader(f, delimiter=delimiter):
                        n = (row.get("name") or "").strip().lower()
                        if n:
                            self.seed_company_names_lower.add(n)
        except Exception:
            pass

    @staticmethod
    def _is_network_error(exc):
        """True if an exception is a transient network/DNS failure worth retrying."""
        msg = str(exc).lower()
        markers = (
            "net::err_", "err_name_not_resolved", "err_connection_", "err_timed_out",
            "err_ssl_", "err_http_", "dns", "socket", "connection reset", "timed out",
            "timeout", "temporary failure", "getaddrinfo", "connectionrefused",
        )
        return any(m in msg for m in markers)

    def _preflight_connectivity(self):
        """Resolve + connect a handful of representative hosts before crawling.

        Aborts the run with a clear error if the network is down, instead of
        letting 125 companies each burn their time budget on DNS timeouts.
        """
        if not self.config.PREFLIGHT_ENABLED:
            return
        failures = []
        for host in self.config.PREFLIGHT_PROBE_HOSTS:
            try:
                socket.setdefaulttimeout(self.config.PREFLIGHT_TIMEOUT_SEC)
                infos = socket.getaddrinfo(host, self.config.PREFLIGHT_PORT, socket.AF_INET)
                if not infos:
                    raise socket.gaierror("no address")
                ip = infos[0][4][0]
                with socket.create_connection((ip, self.config.PREFLIGHT_PORT),
                                              timeout=self.config.PREFLIGHT_TIMEOUT_SEC):
                    pass
                print(f"[preflight] OK   {host}")
            except Exception as exc:
                failures.append(f"{host}: {type(exc).__name__}: {exc}")
                print(f"[preflight] FAIL {host}: {type(exc).__name__}: {exc}")
        if len(failures) >= self.config.PREFLIGHT_MAX_FAILURES:
            raise RuntimeError(
                "Connectivity pre-flight FAILED: "
                f"{len(failures)}/{len(self.config.PREFLIGHT_PROBE_HOSTS)} probes unreachable. "
                "Aborting before crawling to avoid a wasted run. "
                "Check DNS/VPN/proxy, then re-run. "
                "(Use --skip-preflight to bypass this gate.)\n  "
                + "\n  ".join(failures)
            )
        if failures:
            print(f"[preflight] WARNING {len(failures)} probe(s) failed but proceeding.")

    def _http_fetch_with_retry(self, url, headers, parse_json=True, post_body=None):
        """GET (or JSON POST when post_body is given) a provider/API URL with
        transient-error retry + exponential backoff.

        Returns decoded body (str). Raises the last exception if all attempts fail.
        """
        data = None
        if post_body is not None:
            data = json.dumps(post_body).encode("utf-8")
            headers = dict(headers or {})
            headers.setdefault("Content-Type", "application/json")
        last_exc = None
        dns_retried = False
        for attempt in range(1, self.config.HTTP_RETRIES + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=headers,
                                             method="POST" if data is not None else "GET")
                with urllib.request.urlopen(req, timeout=self.config.HTTP_TIMEOUT_SEC) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                return raw
            except urllib.error.HTTPError as exc:
                # 404/410 are definitive (wrong slug); 429/5xx are transient.
                if exc.code in (404, 410):
                    raise
                last_exc = exc
            except Exception as exc:
                if not self._is_network_error(exc):
                    raise
                last_exc = exc
                if not dns_retried and _is_dns_error(exc):
                    # Batch F2: resolver blips outlast the normal backoff;
                    # wait them out once (covers the Pam/DR adapter path,
                    # which has no preflight).
                    dns_retried = True
                    time.sleep(10)
            if attempt < self.config.HTTP_RETRIES:
                backoff = self.config.HTTP_BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                time.sleep(backoff)
        raise last_exc

    def read_seed_file(self):
        """Read and validate the v7 seed schema.

        Backward-compatible with v6, but v7 fields are strongly preferred. Disabled
        seeds are logged and skipped rather than silently substituted in code.
        """
        if not os.path.exists(self.input_csv):
            # Actionable failure instead of a bare traceback. Resolve the path the
            # OS actually looked at, list sibling CSV candidates, and tell the user
            # exactly how to point at the right file.
            cwd = os.getcwd()
            resolved = os.path.abspath(self.input_csv)
            here = os.path.dirname(resolved) or cwd
            candidates = []
            for fn in ("company_Career_seed.csv", "company_Career_seed_v7.csv"):
                p = os.path.join(here, fn)
                if os.path.exists(p):
                    candidates.append(f"    found: {p}")
            hint = (
                "\n  The seed CSV was not found at:\n"
                f"    {resolved}\n"
                f"  (working directory: {cwd})\n"
            )
            if candidates:
                hint += "  Sibling seed files detected:\n" + "\n".join(candidates) + "\n"
            hint += (
                "  Fix one of:\n"
                "    1. Put 'company_Career_seed.csv' in your working directory, or\n"
                "    2. Run with: --input <full path to your seed CSV>\n"
                "  Note: the scanner reads either the v7 schema (source_type,\n"
                "  target_country, scope_policy, provider, board_slug) or the original\n"
                "  v6 schema (name, ats_type, careers_url, ...). A v6-format seed will\n"
                "  still run, but without recruiter separation and country-scope\n"
                "  enforcement (those default to Global/global)."
            )
            raise FileNotFoundError(f"Seed file '{self.input_csv}' not found.{hint}")
        records = []
        errors = []
        seen_seed_keys = set()
        with open(self.input_csv, newline="", encoding="utf-8-sig") as f:
            sample = f.read(4096)
            f.seek(0)
            delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
            reader = csv.DictReader(f, delimiter=delimiter)
            headers = set(reader.fieldnames or [])
            if "careers_url" not in headers:
                raise ValueError("Seed CSV must contain careers_url")
            for line_no, row in enumerate(reader, 2):
                seed_name = (row.get("seed_name") or row.get("name") or "").strip()
                name = (row.get("canonical_name") or seed_name).strip()
                url = (row.get("careers_url") or "").strip()
                # ── v6 → v7 in-memory upgrade (auditable, logged) ────────────────
                # Only applied to v6-format rows: when a row has no v7 metadata of
                # its own and a matching SEED_UPGRADE key exists, correct it.
                upg = SEED_UPGRADE.get(name) or SEED_UPGRADE.get(name.casefold()) \
                    or next((v for k, v in SEED_UPGRADE.items() if k.casefold() == name.casefold()), None)
                if upg:
                    changed = []
                    if "careers_url" in upg and upg["careers_url"] != url:
                        changed.append(f"url {url!r} -> {upg['careers_url']!r}")
                        url = upg["careers_url"]
                    if "source_type" in upg and not (row.get("source_type") or "").strip():
                        changed.append(f"source_type -> {upg['source_type']}")
                        row["source_type"] = upg["source_type"]
                    if "target_country" in upg and not (row.get("target_country") or "").strip():
                        changed.append(f"target_country -> {upg['target_country']}")
                        row["target_country"] = upg["target_country"]
                    if "scope_policy" in upg and not (row.get("scope_policy") or "").strip():
                        changed.append(f"scope_policy -> {upg['scope_policy']}")
                        row["scope_policy"] = upg["scope_policy"]
                    if "provider" in upg and not (row.get("provider") or "").strip():
                        changed.append(f"provider -> {upg['provider']}")
                        row["provider"] = upg["provider"]
                    if "board_slug" in upg and not (row.get("board_slug") or "").strip():
                        changed.append(f"board_slug -> {upg['board_slug']}")
                        row["board_slug"] = upg["board_slug"]
                    if changed:
                        print(f"   [upgrade] {seed_name or name}: " + "; ".join(changed))
                if upg and upg.get("enabled") is False:
                    notes = upg.get("notes", "") or row.get("notes", "")
                    print(f"   -> Seed disabled (upgrade): {seed_name or name} ({notes})")
                    continue
                enabled = (row.get("enabled") or "true").strip().lower() not in {
                    "0", "false", "no", "disabled"
                }
                if not enabled:
                    print(f"   -> Seed disabled: {seed_name or name} ({row.get('notes','')})")
                    continue
                if not seed_name or not name:
                    errors.append(f"line {line_no}: missing seed/canonical name")
                    continue
                if not url.startswith(("http://", "https://")) or "..." in url:
                    errors.append(f"line {line_no} {seed_name}: invalid URL {url!r}")
                    continue
                source_type = (row.get("source_type") or "direct_employer").strip().lower()
                if source_type not in {"direct_employer", "recruiter"}:
                    errors.append(f"line {line_no} {seed_name}: invalid source_type {source_type!r}")
                    continue
                target_country = (row.get("target_country") or "Global").strip()
                scope_policy = (row.get("scope_policy") or "global").strip().lower()
                if scope_policy not in {"global", "seed_url", "job_location"}:
                    errors.append(f"line {line_no} {seed_name}: invalid scope_policy {scope_policy!r}")
                    continue
                provider = (row.get("provider") or "auto").strip().lower()
                board_slug = (row.get("board_slug") or "").strip()
                key = (seed_name.casefold(), target_country.casefold(), url.casefold())
                if key in seen_seed_keys:
                    # Same company + same country + same (post-upgrade) URL is a
                    # duplicate seed row (e.g. a placeholder split into two region
                    # URLs that both resolve to one board). Collapse silently rather
                    # than fail the whole run.
                    print(f"   -> Seed deduped (duplicate): {seed_name} line {line_no}")
                    continue
                seen_seed_keys.add(key)
                # Parse scores instead of silently ignoring four seed columns.
                scores = {}
                for col in ("sponsorship_history", "english_friendly", "remote_score"):
                    raw = (row.get(col) or "").strip()
                    try:
                        value = int(raw) if raw else None
                    except ValueError:
                        errors.append(f"line {line_no} {seed_name}: non-numeric {col}={raw!r}")
                        value = None
                    if value is not None and not 0 <= value <= 100:
                        errors.append(f"line {line_no} {seed_name}: {col} outside 0..100")
                    scores[col] = value
                # Safe page-size normalization; query-aware behavior is handled by pagination.
                url = re.sub(r"([?&]size=)n_3_n(?=&|$)", r"\1n_100_n", url)
                records.append({
                    "seed_name": seed_name,
                    "name": name,
                    "careers_url": url,
                    "industry": (row.get("industry") or "Unknown").strip(),
                    "source_type": source_type,
                    "target_country": target_country,
                    "scope_policy": scope_policy,
                    "provider": provider,
                    "board_slug": board_slug,
                    "ats_type": (row.get("ats_type") or provider).strip(),
                    "notes": (row.get("notes") or "").strip(),
                    **scores,
                })
        if errors:
            raise ValueError("Seed validation failed:\n - " + "\n - ".join(errors))
        return records

    # FIX P0-17: vendor-specific consent IDs. These CMPs render their own
    # DOM and are not caught by generic text matching. Ordered most-common
    # first; OneTrust and Cookiebot alone cover a large share of EU portals.
    _CMP_SELECTORS = (
        "#onetrust-accept-btn-handler",
        "#onetrust-pc-btn-handler + button",
        "#accept-recommended-btn-handler",
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowallSelection",
        "#CybotCookiebotDialogBodyButtonAccept",
        "button#didomi-notice-agree-button",
        ".didomi-continue-without-agreeing",
        "#usercentrics-root >>> button[data-testid='uc-accept-all-button']",
        "button[data-testid='uc-accept-all-button']",
        "#uc-btn-accept-banner",
        ".iubenda-cs-accept-btn",
        "#iubenda-cs-banner button.iubenda-cs-accept-btn",
        ".qc-cmp2-summary-buttons button[mode='primary']",
        "button.css-47sehv",                      # Quantcast
        "#truste-consent-button",                 # TrustArc
        "#gdpr-consent-tool-wrapper button[data-tracking='accept']",
        "#hs-eu-confirmation-button",             # HubSpot
        ".osano-cm-accept-all",                   # Osano
        "#cmpwelcomebtnyes a",                    # Consentmanager
        "button[aria-label='Accept all']",
        "button[aria-label='Accept cookies']",
        "button[title='Accept all']",
        "[data-cookiebanner='accept_button']",
        "#wt-cli-accept-all-btn",                 # CookieYes
        ".cmplz-accept",                          # Complianz
        "#cn-accept-cookie",                      # Cookie Notice
    )

    def dismiss_initial_blockers(self, page, deep=True):
        """Dismiss cookie/consent overlays.

        FIX P0-17: previously this ran once, on the main frame only, with
        text selectors alone. Three consequences, all of which silently
        returned zero jobs on .it/.de portals:
          1. CMPs that render inside an iframe (Cookiebot, TrustArc, some
             OneTrust configs) were never reachable -> overlay stayed up.
          2. Vendor-specific buttons were missed by text matching when the
             label was an <a>/<div> rather than a <button>.
          3. Banners injected *after* the first paint, or re-shown after a
             pagination click, were never re-dismissed.
        Now: vendor IDs first, then text, then the same sweep across child
        frames, and callers re-invoke it after navigation.
        """
        selectors = list(self._CMP_SELECTORS) + [
            # FIX P0-5: Italian cookie-consent verbs (blocking overlays hide
            # the job list entirely on many .it portals).
            "button:has-text('Acconsento')",
            "button:has-text('Accetta e chiudi')",
            "button:has-text('Accetta tutti i cookie')",
            "button:has-text('Ho capito')",
            "button:has-text('Consenti tutti')",
            "button:has-text('Accetto')",
            "button:has-text('Accept all')",
            "button:has-text('Accept All')",
            "button:has-text('Accept')",
            "button:has-text('Agree')",
            "button:has-text('Allow all')",
            "button:has-text('Allow All')",
            "button:has-text('Allow')",
            "button:has-text('Consent')",
            "button:has-text('Accept All Cookies')",
            "button:has-text('Allow Cookies')",
            "button:has-text('Accetta tutti')",
            "button:has-text('Accetta')",
            "button:has-text('Alle akzeptieren')",
            "button:has-text('Akzeptieren')",
            "button:has-text('Accepteer alles')",
            "button:has-text('Accepteer')",
            "button:has-text('Accepter tout')",
            "button:has-text('Aceptar todo')",
            "button:has-text('OK')",
            "button:has-text('Got it')",
            "button:has-text('I agree')",
            "button:has-text('I understand')",
            "button:has-text('Continue')",
            "#onetrust-accept-btn-handler",
            "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
            "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowallSelection",
            ".cc-accept",
            ".cookie-accept",
            "button[id*='cookie' i]",
            "button[class*='cookie' i]",
            "button[id*='accept' i]",
            "button[class*='accept' i]",
            "button[data-testid*='accept']",
        ]
        # Text labels are also matched on <a> and role=button, not just
        # <button> — many Italian/German banners use anchors or divs.
        _TEXTS = (
            "Acconsento", "Accetta e chiudi", "Accetta tutti i cookie",
            "Accetta tutti", "Accetta", "Accetto", "Ho capito", "Consenti tutti",
            "Accept all", "Accept All Cookies", "Accept Cookies", "Accept",
            "Allow all", "Allow Cookies", "Allow", "Agree", "I agree",
            "Alle akzeptieren", "Akzeptieren", "Zustimmen", "Einverstanden",
            "Accepteer alles", "Accepteren", "Akkoord",
            "Accepter tout", "Tout accepter", "J'accepte",
            "Aceptar todo", "Aceptar", "Aceitar todos",
            "Godkänn alla", "Zaakceptuj", "Souhlasím",
            "OK", "Got it", "I understand", "Continue",
        )

        def _sweep(scope):
            clicked = 0
            for sel in selectors:
                try:
                    btn = scope.locator(sel).first
                    if btn.is_visible(timeout=250):
                        btn.click(timeout=1000)
                        clicked += 1
                        try:
                            scope.wait_for_timeout(300)
                        except Exception:
                            pass
                except Exception:
                    continue
            for label in _TEXTS:
                for role in ("button", "link"):
                    try:
                        btn = scope.get_by_role(role, name=label, exact=False).first
                        if btn.is_visible(timeout=200):
                            btn.click(timeout=1000)
                            clicked += 1
                            try:
                                scope.wait_for_timeout(300)
                            except Exception:
                                pass
                            break
                    except Exception:
                        continue
            return clicked

        total = 0
        try:
            total += _sweep(page)
        except Exception:
            pass

        # Same sweep inside child frames — Cookiebot/TrustArc live there.
        if deep:
            try:
                for fr in page.frames[1:]:
                    try:
                        url_l = (fr.url or "").lower()
                    except Exception:
                        url_l = ""
                    if url_l and not any(k in url_l for k in (
                            "consent", "cookie", "privacy", "gdpr", "cmp",
                            "onetrust", "cookiebot", "didomi", "usercentrics",
                            "trustarc", "iubenda", "quantcast", "sourcepoint")):
                        continue
                    try:
                        total += _sweep(fr)
                    except Exception:
                        continue
            except Exception:
                pass

        # Last resort: force-remove fixed overlays that block clicks but
        # expose no accept control (pure scroll-locks).
        if deep:
            try:
                page.evaluate(
                    """() => {
                        const KILL = /cookie|consent|gdpr|privacy-banner|cmp-|onetrust|didomi|usercentrics/i;
                        document.querySelectorAll('div,section,aside').forEach(el => {
                            try {
                                const cs = getComputedStyle(el);
                                if ((cs.position === 'fixed' || cs.position === 'sticky') &&
                                    parseInt(cs.zIndex || '0', 10) > 500 &&
                                    KILL.test((el.id || '') + ' ' + (el.className || ''))) {
                                    el.remove();
                                }
                            } catch (e) {}
                        });
                        for (const el of [document.body, document.documentElement]) {
                            if (!el) continue;
                            el.style.overflow = 'auto';
                            el.style.position = 'static';
                            el.classList.remove('no-scroll','modal-open','overflow-hidden','cookie-open');
                        }
                    }"""
                )
            except Exception:
                pass
        return total

    def navigate_to_fragment(self, page, url):
        if "#" not in url:
            return
        fragment = url.split("#", 1)[1].strip()
        if not fragment:
            return
        try:
            page.evaluate(
                """frag => {
                    const el = document.getElementById(frag) ||
                               document.querySelector('[name="' + frag + '"]');
                    if (el) el.scrollIntoView({behavior: "instant", block: "start"});
                }""",
                fragment,
            )
            page.wait_for_timeout(1200)
        except Exception:
            pass
    def handle_landing_page_redirect(self, page, base_url):
        for text in self.config.LANDING_PAGE_CTAS:
            try:
                loc = page.locator(f"a:has-text('{text}'), button:has-text('{text}')").first
                if loc.is_visible(timeout=300):
                    href = loc.get_attribute("href")
                    print(f"   -> Landing CTA: {text}")
                    if href:
                        page.goto(
                            urljoin(base_url, href),
                            wait_until="domcontentloaded",
                            timeout=self.config.ACTION_TIMEOUT_MS,
                        )
                    else:
                        loc.click(timeout=1500)
                    page.wait_for_timeout(3000)
                    return True
            except Exception:
                continue
        return False
    def trigger_search_if_present(self, page):
        selectors = [
            "button:has-text('Search')",
            "button:has-text('Search Jobs')",
            "button:has-text('Find Jobs')",
            "button:has-text('Show all')",
            "button:has-text('View all')",
            "button:has-text('All jobs')",
            "button:has-text('Cerca')",
            "button:has-text('Suchen')",
            "input[type='submit'][value*='Search' i]",
        ]
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=300):
                    btn.click(timeout=1500)
                    page.wait_for_timeout(2500)
                    return True
            except Exception:
                continue
        return False
    # FIXED: Re-engineered with:
    # 1. URL parsing to evaluate ONLY the domain + path (netloc/path), completely preventing matching keywords hidden inside tracking/referral parameters!
    # 2. Strict domain/provider exclusions for trackers, cookie consent widgets, chatbots, and layout players to guarantee we never accidentally target them.
    def check_iframes(self, page):
        best_frame = None
        best_score = 0
        
        EXCLUDED_IFRAME_DOMAINS = [
            "doubleclick.net", "demdex.net", "googleads", "googletagmanager", 
            "google-analytics", "analytics", "facebook.com", "linkedin.com", 
            "framer.com", "speakerdeck.com", "driftt.com", "drift.com", 
            "hotjar", "cookiebot", "onetrust", "cookie-consent", "youtube.com", 
            "vimeo.com", "twitter.com", "instagram.com", "hubspot.com", 
            "intercom", "recaptcha", "disqus", "optimizely", "krxd.net",
            "scorecardresearch", "adnxs.com", "ads-twitter", "snapchat.com",
            "adsrvr.org", "casalemedia.com", "rubiconproject.com", "pubmatic.com"
        ]
        
        # Batch N phase 2a: the page's own host (same-domain iframes are site
        # chrome — logos, widgets — never an embedded ATS board, which is
        # always cross-domain). Prevents FS-style hijacks where "career" in
        # fscareers.gruppofs.it scored a logo.svg iframe +8.
        try:
            page_host = (urlparse(page.url or "").netloc or "").lower()
        except Exception:
            page_host = ""
        # Asset/non-HTML frames can never hold job listings.
        _ASSET_EXT = (".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp",
                      ".ico", ".css", ".js", ".woff", ".woff2", ".ttf",
                      ".pdf", ".mp4", ".mp3", ".json", ".xml")
        try:
            for frame in page.frames[1:]:
                low_url = (frame.url or "").lower()
                if not low_url or any(d in low_url for d in EXCLUDED_IFRAME_DOMAINS):
                    continue

                parsed = urlparse(low_url)
                if parsed.path.lower().endswith(_ASSET_EXT):
                    continue
                check_str = f"{parsed.netloc}{parsed.path}"

                score = 0
                # Keyword +8 only for CROSS-domain frames: same-host matches
                # are the site's own domain (fscareers.*, jobs.*, career.*),
                # not an embedded ATS. Same-host frames can still win on
                # real content evidence (jobLinks scoring below).
                same_host = parsed.netloc.lower() == page_host
                if not same_host and any(k in check_str for k in [
                    "personio", "workable", "greenhouse", "lever", "ashby",
                    "smartrecruiters", "teamtailor", "breezy", "successfactors",
                    "myworkday", "workdayjobs", "jobs", "career", "tellent",
                    "recruitee", "comeet", "jobylon", "rippling"
                ]):
                    score += 8
                try:
                    text_score = frame.evaluate("""() => {
                        const txt = (document.body?.innerText || '').toLowerCase();
                        const links = [...document.querySelectorAll('a[href]')];
                        const jobLinks = links.filter(a => /\\/(job|jobs|o|role|position|vacancy|opening)\\b|jobid|gh_jid|requisition/i.test(a.href || '')).length;
                        let score = Math.min(jobLinks * 3, 20);
                        if (jobLinks >= 2 && /job|career|position|opening|vacancy|role/.test(txt)) score += 5;
                        return score;
                    }""")
                    score += int(text_score or 0)
                except Exception:
                    pass
                if score > best_score:
                    best_score = score
                    best_frame = frame
            if best_frame and best_score >= 8:
                return best_frame
        except Exception:
            pass
        return None
    def fix_encoding(self, text):
        if not text:
            return text
        replacements = {
            "Ã¼": "ü", "Ã¤": "ä", "Ã¶": "ö", "ÃŸ": "ß",
            "Ã©": "é", "Ã¨": "è", "Ã ": "à", "Ã¡": "á",
            "Ã¢": "â", "Ã­": "í", "Ã³": "ó", "Ã²": "ò",
            "Ã´": "ô", "Ã»": "û", "Ã§": "ç", "Ã±": "ñ",
            "Ãœ": "Ü", "Ã„": "Ä", "Ã–": "Ö", "Ã‰": "É", "Ã€": "À",
            "â€™": "'", "â€œ": '"', "â€": '"',
            "â€”" : "—", "â€¢": "•", "Â": "",
        }
        for bad, good in replacements.items():
            text = text.replace(bad, good)
        return text
    def has_job_identifier_query(self, url):
        try:
            parsed = urlparse(url)
            q = parsed.query.lower()
            path = parsed.path.lower()
        except Exception:
            return False
        strong = re.search(
            r"(^|&)(job|jobid|job_id|jid|gh_jid|req|reqid|requisition|"
            r"requisitionid|career_job_req_id|posting|postingid|externaljobid)=",
            q, re.IGNORECASE,
        )
        if strong:
            return True
        # Generic id= is identity only on an explicitly job-shaped path (e.g. Coop view-job.php?id=...).
        return bool(re.search(r"(^|&)id=", q, re.I) and re.search(r"job|vacanc|position", path, re.I))

    def clean_page_identity(self, url):
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/").lower()
    def is_self_listing_url(self, job_url, seed_url):
        if not seed_url:
            return False
        if "#job=" in job_url.lower():
            return False
        if self.has_job_identifier_query(job_url):
            return False
        return self.clean_page_identity(job_url) == self.clean_page_identity(seed_url)
    def clean_and_normalize_url(self, url):
        if not url:
            return ""
        url = self.fix_encoding(url.strip()).replace("&amp;", "&")
        if not url.startswith(("http://", "https://")):
            return ""
        parsed = urlparse(url)
        keep_keys = {
            "job", "jobid", "job_id", "jid", "gh_jid", "req", "reqid",
            "requisition", "requisitionid", "career_job_req_id", "posting",
            "postingid", "externaljobid", "lever-origin", "language", "lang",
        }
        if re.search(r"job|vacanc|position", parsed.path, re.I):
            keep_keys.add("id")
        kept_query = [
            (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k.lower() in keep_keys
        ]
        query = urlencode(kept_query, doseq=True)
        fragment = ""
        if parsed.fragment and re.search(r"(?:^|/)(?:job|jobs|position|vacancy)[=/]", parsed.fragment, re.I):
            fragment = parsed.fragment
        elif parsed.fragment.lower().startswith("job="):
            fragment = parsed.fragment
        path = re.sub(
            r"/(apply|submission|application|form|new|confirm|thank-you)/?(?=$)",
            "/", parsed.path, flags=re.IGNORECASE,
        )
        return urlunparse((
            parsed.scheme.lower(), parsed.netloc.lower(),
            path.rstrip("/") if not fragment else path,
            "", query, fragment,
        ))

    def _strip_gender_tags(self, title):
        # Removes (m/f/d), (m/w/d), (w/m/d), (d/f/m), (f/m/d), (m/f/x), (all genders),
        # (mwd), (m/f), (divers), standalone trailing m/f/d, etc.
        title = re.sub(
            r"\s*[\(\[]\s*(?:all\s+genders|"
            r"(?:[mwdfχxns]{1,4}[\s/|](?:[mwdfχxns]{1,4})(?:[\s/|](?:[mwdfχxns]{1,4}))?)|"
            r"(?:[mwdfχxns]{1,4}[\s/|](?:[mwdfχxns]{1,4})))\s*[\)\]]\s*",
            "", title, flags=re.IGNORECASE,
        )
        title = re.sub(
            r"\s+(m/f/d|m/w/d|w/m/d|d/f/m|f/m/d|m/f/x|w/m/x|m/w/x)\s*$",
            "", title, flags=re.IGNORECASE,
        )
        return title

    def _normalize_level_prefix(self, title):
        level_map = {
            "senior": "Senior", "junior": "Junior", "staff": "Staff",
            "principal": "Principal", "lead": "Lead", "executive": "Executive",
            "expert": "Expert", "associate": "Associate", "entry": "Entry",
            "mid": "Mid", "sr": "Sr.", "sr.": "Sr.", "jr": "Jr.", "jr.": "Jr.",
        }
        m = re.match(
            r"^\s*\(\s*([A-Za-zÀ-ÿ.'-]{1,12})\s*\)\s*", title)
        if m and m.group(1).lower() in level_map:
            return level_map[m.group(1).lower()] + " " + title[m.end():]
        return title

    def _looks_like_location_fragment(self, frag):
        s = (frag or "").strip()
        if not s:
            return False
        # a fragment that contains a role word is a title, not a location
        # (e.g. "Payments Consultant Germany & Austria")
        if self.config.ROLE_WORD_PATTERN.search(s):
            return False
        low = re.sub(r"^[\(\[]|[\)\]]$", "", s).strip(" .,;").lower()
        if low in ("remote", "hybrid", "onsite", "on-site", "on site",
                   "home office", "anywhere", "worldwide", "global"):
            return True
        # diacritic-aware known-place check (Gda\u0144sk, K\u00f6ln, Wroc\u0142aw)
        if low in self.KNOWN_PLACES or self._norm(low) in self.NORM_KNOWN:
            return True
        # slash-separated city list ("Linkou/Hsinchu/Taichung") is a location fragment
        if "/" in low:
            for part in low.split("/"):
                part = part.strip()
                if part and (part in self.KNOWN_PLACES or self._norm(part) in self.NORM_KNOWN):
                    return True
        # office / work-mode keywords ("In-Office", "Onsite")
        if re.search(r"\b(in[- ]?office|on[- ]?site|remote|hybrid|home office|"
                     r"work from home|flexible)\b", low):
            return True
        # known boilerplate phrases ("Target Optical", "Sunglass Hut")
        if any(ph in low for ph in self.config.LOCATION_REJECT_PHRASES):
            return True
        if low in self.KNOWN_PLACES:
            return True
        if re.fullmatch(r"[a-z]{2}", low):
            return True
        words = low.split()
        if words and words[-1] in self.config.COUNTRIES_AND_REGIONS:
            return True
        if re.search(
            r"\b(ny|ca|tx|ma|il|wa|fl|az|co|ga|nj|pa|mi|oh|mn|nc|va|md|ct|"
            r"or|ut|in|mo|wi|tn|sc|ky|la|al|ok|ks|ia|ar|nv|ne|id|nh|me|ri|"
            r"vt|wv|mt|nd|sd|wy|ak|hi|de|dc|on|bc|ab|qc|ns|mb|sk|nt|yt|nu|"
            r"pe|nl|nb)\b$", low,
        ):
            return True
        return False

    def _looks_like_contract_fragment(self, frag):
        low = (frag or "").strip().lower()
        if not low or len(low) > 80:
            return False
        # short fragments that START with a contract/type term
        # (e.g. "fixed term until June 30th, 2027" — but NOT
        #  "Campus Undergraduate Summer Internship Program")
        if re.search(
            r"^(fixed[- ]?term|permanent|temporary|temporaire|contract|"
            r"full[- ]?time|part[- ]?time|internship|trainee|werkstudent|"
            r"working student|praktikum|ausbildung|duales studium|"
            r"apprenticeship|apprentice|secondment)\b", low,
        ):
            return True
        # year + contract-word combo (e.g. "2027 fixed term")
        if re.search(r"\b20\d\d\b", low) and re.search(
            r"\b(term|until|ending|contract|fixed|year|month)\b", low):
            return True
        return False

    def clean_job_title(self, title):
        """Conservative, multilingual title normalization.

        v7 never removes comma-delimited function text, brand names, or words merely
        because they resemble a department/location. Raw extraction mistakes are
        rejected by validation instead of destructively rewritten into generic titles.
        """
        if not title:
            return ""
        title = self.fix_encoding(str(title))
        lines = [re.sub(r"\s+", " ", x).strip() for x in re.split(r"[\r\n]+", title) if x.strip()]
        if not lines:
            return ""
        title = lines[0]
        title = self._strip_gender_tags(title)
        title = self._normalize_level_prefix(title)
        title = re.sub(r"^(?:new|featured|hot)\s*[!:\-–—|]+\s*", "", title, flags=re.I)
        title = re.sub(r"^[•·→↗›\s|]+", "", title)
        title = re.sub(r"\s*[•·]+\s*$", "", title)
        # Remove an unambiguous trailing work-mode/contract parenthesis only.
        title = re.sub(
            r"\s*\((?:full[- ]?time|part[- ]?time|remote|hybrid|on[- ]?site|"
            r"permanent|temporary|fixed[- ]?term|contract)\)\s*$",
            "", title, flags=re.I,
        )
        # Remove salary tails, but never surrounding title words.
        title = re.sub(
            r"\s*[-–—|]\s*[€$£]\s?[\d,.]+(?:k)?(?:\s*(?:-|to)\s*[€$£]?\s?[\d,.]+(?:k)?)?"
            r"(?:\s*(?:per|/)\s*(?:hour|day|year|annum|hr))?\s*$",
            "", title, flags=re.I,
        )
        title = re.sub(r"\s+", " ", title).strip(" \t-–—|•")
        return title[:200].rstrip()

    def _is_postal_stub(self, segs) -> bool:
        """True for City/ST/Country/ZIP stubs with UNKNOWN cities (F3).

        The G1 loop below needs every segment known, so "Monroe, OH, US,
        45050" (Monroe unknown) sailed through. >=3 segments need no known
        city: a postal anchor + all-location rest + role-word-free first
        segment is unambiguous. 2-segment behavior is intentionally
        untouched ("Barista, Milano", "Sales, UK" stay valid).
        """
        first = (segs[0] or "").strip()
        if not first:
            return False
        if self.config.ROLE_WORD_PATTERN.search(first) or _F3_ROLE_EXTRA_RE.search(first):
            return False
        rest = [s.strip() for s in segs[1:] if s.strip()]
        if len(rest) < 2:
            return False
        anchor = False
        for i, s in enumerate(rest):
            w = s.lower()
            if _F3_POSTAL_RE.search(s):
                anchor = True
                continue
            if i == len(rest) - 1 and re.fullmatch(r"\d{4,10}", s):
                # Bare-digit final segment: stripped leading-zero ZIP
                # ("Elizabeth, NJ, US, 7201" = 07201), AU 4-digit and CL
                # 7-digit postcodes. Final-segment only, so Job IDs and
                # mid-title years can't anchor.
                anchor = True
                continue
            if re.fullmatch(r"[a-z]{2,3}", w) or w in self.config.COUNTRIES_AND_REGIONS:
                continue
            if w in self.KNOWN_PLACES or self._norm(w) in self.NORM_KNOWN:
                continue
            words = w.split()
            if words and all(
                x in self.KNOWN_PLACES or self._norm(x) in self.NORM_KNOWN
                or x in self.config.COUNTRIES_AND_REGIONS
                or re.fullmatch(r"[a-z]{2,3}|\d+", x)
                for x in words
            ):
                continue
            return False
        return anchor

    def _title_is_pure_location(self, t: str) -> bool:
        """True when a title is only place/code/postal segments ("Milano, MI").

        G1 fix: single-token places are already rejected by the KNOWN_PLACES
        check, but multi-token locations ("Milano, MI", "Wetzlar, DE") sailed
        through and became job titles. Every comma segment must be a known
        place/country, a 2-3 letter code, or a postal code — anything else
        (e.g. "Operaio Parma", "Nurse, Berlin") is kept as a title.
        F3 adds the >=3-segment postal-anchored rule (unknown cities).
        """
        segs = [s.strip() for s in t.split(",")]
        if len(segs) < 2:
            return False
        if len(segs) >= 3 and self._is_postal_stub(segs):
            return True
        saw_place = False
        for s in segs:
            if not s:
                continue
            w = s.lower()
            if re.fullmatch(r"[a-z]{2,3}", w) or re.fullmatch(r"[\d\s\-]*\d[\d\s\-]*", w):
                continue  # state/country code or postal code
            if w in self.KNOWN_PLACES or self._norm(w) in self.NORM_KNOWN:
                saw_place = True
                continue
            if w in self.config.COUNTRIES_AND_REGIONS:
                saw_place = True
                continue
            words = w.split()
            if words and all(
                x in self.KNOWN_PLACES or self._norm(x) in self.NORM_KNOWN
                or x in self.config.COUNTRIES_AND_REGIONS
                or re.fullmatch(r"[a-z]{2,3}|\d+", x)
                for x in words
            ):
                saw_place = True
                continue
            return False
        return saw_place

    # Listing/category slugs that must never become "titles" via slug rescue.
    _LISTING_SLUGS = frozenset({
        "open-positions", "openings", "positions", "vacancies", "search-jobs",
        "job-search", "jobs", "careers", "join-the-team", "join-us", "join-us-2",
        "work-with-us", "all-jobs", "find-jobs", "browse-jobs", "view-all-jobs",
        "career-opportunities", "job-opportunities", "life-at", "about-us",
    })

    @staticmethod
    def _title_from_url_slug(url):
        """Best-effort title from a job-URL slug (Phenom-style boards).

        J2: the DOM extractor sometimes picks button/filter text ("Show job",
        "Full time") instead of the title. The URL slug usually holds the
        real title (ING/Ikea: /en/job/<city>/<slug>/...). Returns "" when no
        slug-like segment exists. The caller must still run the result
        through is_valid_job_title.
        """
        try:
            segs = [s for s in urlparse(url).path.split("/") if s]
        except Exception:
            return ""
        best = ""
        for seg in segs:
            core = urllib.parse.unquote(seg).strip().lower()
            # Next.js convention (title--dept--location): the title is the
            # first chunk; also keeps "--" from breaking the pattern below.
            core = core.split("--")[0]
            # Batch M phase 1: Phenom slugs carry percent-encoded punctuation
            # (City%2C-ST, (m/w/d), +, &) that fails the strict slug pattern
            # below — 106 lost rescues in the 09-12 career run (SAP/Luxottica).
            # Fold every non-word run to one dash. \w keeps accented/CJK
            # letters, so German/French/Japanese slugs rescue too. Underscore
            # segments stay rejected (nav/asset slugs, never job titles).
            core = re.sub(r"[^\w]+", "-", core).strip("-")
            if (len(core) >= 8 and "_" not in core
                    and core not in CareerPortalScanner._LISTING_SLUGS
                    and re.fullmatch(r"\w+(?:-\w+){1,}", core)
                    and not re.fullmatch(r"\d+(?:-\d+)+", core)
                    and len(core) > len(best)):
                best = core
        if not best:
            return ""
        words = best.replace("-", " ").split()
        if len(words) < 2:
            return ""
        return " ".join(w[:1].upper() + w[1:] for w in words)

    @staticmethod
    def _frag_twin_in_index(index, title, location, is_frag, context=""):
        """True if `index` holds the same job in the other URL form.

        J1 guard: same normalized title + one side fragment (#job=) and the
        other clean + locations equal or compatible. Same-title/different-city
        jobs (Ocado: Erith UK vs Monroe Ohio) stay distinct when both
        locations are known. An Unknown side must prove the twin's location
        from its card context, else genuinely different jobs sharing a title
        would collapse. Errors quarantine (reviewable), never drop.
        """
        tnorm = re.sub(r"\s+", " ", (title or "")).strip().casefold()
        if not tnorm:
            return False
        lnorm = re.sub(r"\s+", " ", (location or "")).strip().casefold()
        ctx = (context or "").casefold()
        _STOP = {"remote", "hybrid", "onsite", "on-site", "europe", "global",
                 "worldwide", "emea", "unknown", "unspecified"}
        for loc_known, frag_known, ctx_known in index.get(tnorm, ()):
            if frag_known == is_frag:
                continue  # same form: canonical dedupe owns this case
            if lnorm == loc_known and lnorm not in ("", "unknown", "not specified"):
                return True
            if lnorm in ("", "unknown", "not specified") and loc_known in ("", "unknown", "not specified"):
                return True  # same title, both unlocated, mixed forms: dupes
            # One side unlocated: the KNOWN location must appear in the
            # UNLOCATED side's card context (checked symmetrically, since
            # either form can arrive first).
            pairs = ((loc_known, ctx) if lnorm in ("", "unknown", "not specified")
                     else (lnorm, ctx_known or ""))
            known, unknown_ctx = pairs
            toks = [w for w in re.split(r"[^a-z]+", known) if len(w) > 3 and w not in _STOP]
            if toks and any(w in unknown_ctx for w in toks):
                return True
        return False

    def is_valid_job_title(self, title):
        if not title:
            return False
        t = re.sub(r"\s+", " ", title).strip()
        low = t.casefold()
        if len(t) < 3 or len(t) > 200 or not any(ch.isalpha() for ch in t):
            return False
        hard = {x.casefold() for x in self.config.HARD_TITLE_BLACKLIST}
        dept = {x.casefold() for x in self.config.DEPT_AS_TITLE}
        # These can be genuine standalone retail roles, not only departments.
        dept -= {"cashier", "bakery", "deli", "produce", "recovery", "checkout"}
        dept |= {"marketing solutions", "pre-sales", "presales", "business development",
                 "customer service analytics", "field engineering", "professional services"}
        # J3: intern/apprentice/trainee are complete entry-level titles
        # (aligned with the ATS scanner's valid_title), not truncations.
        generic_single = {
            "senior", "junior", "associate", "principal", "lead", "manager",
            "director", "expert", "owner", "quality", "officer", "specialist",
            "analyst", "engineer",
        }
        if low in hard or low in dept or low in generic_single:
            return False
        if low in self.KNOWN_PLACES or self._norm(low) in self.NORM_KNOWN:
            return False
        # G1 fix: multi-token locations used as titles ("Milano, MI",
        # "Wetzlar, DE", "Suzhou, CN") pass the check above.
        if self._title_is_pure_location(t):
            return False
        for pattern in self.config.SUSPICIOUS_TITLE_PATTERNS:
            if pattern.match(t):
                return False
        if re.match(
            r"^(load|view|see|show|browse|explore|find|search|open|close|toggle|"
            r"upload|submit|download|share|print|email|save|apply|candidati|candidarsi|scopri|leggi|invia|vedi)\b", low  # H2: Italian CTA verbs
        ) and len(t.split()) <= 6:
            return False
        # Batch M phase 1: pool/button phrases ported from the ATS
        # scanner (Batch L parity) — 7 such rows slipped into the 09-12
        # career results. Deliberately NOT included: "sign in"/"log in"
        # ("Design Intern" contains "sign in" — substring trap).
        if any(x in low for x in (
            "privacy statement", "terms of use", "cookie", "items per page",
            "clear all filters", "recruitment fraud", "click this button",
            "talent pool", "talent community", "candidate database",
            "career day", "create alert", "learn more", "job details",
            "back to search", "looking for a job", "join us",
            "join our team", "join the team", "come join us",
            "per saperne di più", "scopri di più",  # H2
        )):
            return False
        # No English-only role-word gate: preserve Italian/German/French/Dutch,
        # accented and non-Latin titles. Multi-word context is the safer signal.
        if len(t.split()) == 1 and len(t) < 5:
            return False
        return True

    def is_valid_job_url(self, url):
        if not url or not url.startswith("http"):
            return False
        if _ASSET_URL_RE.search(url) or _is_download_url(url):
            return False
        if re.search(r"/(?:applicationmethods|apply|application)(?:/|$)", urlparse(url).path, re.I):
            # J4: boards whose DETAIL pages live under apply-paths (UniCredit
            # .../ApplicationMethods?jobId=) carry a job identifier — those
            # are specific jobs, not generic apply links.
            if not self.has_job_identifier_query(url):
                return False
        if self.config.URL_EXCLUSION_PATTERN.search(url):
            return False
        if "#job=" in url.lower():
            return True
        if self.config.CATEGORY_PATH_INDICATORS.search(url):
            return False
        clean_path = urlparse(url).path.rstrip("/").lower()
        if clean_path.endswith((
            "/jobs", "/careers", "/career", "/vacancies",
            "/openings", "/positions", "/roles", "/search"
        )):
            if not self.has_job_identifier_query(url):
                return False
        if not self.config.JOB_URL_PATTERN.search(url):
            if not self.has_job_identifier_query(url):
                return False
        return True
    def _fmt_place(self, low):
        """Format a lowercased place token with proper casing."""
        low = low.strip()
        if not low:
            return ""
        # restore canonical (diacritic) form: "munchen" -> "München"
        canon = self.NORM_KNOWN.get(self._norm(low))
        if canon:
            low = canon
        if low in self.config.CASING_MAP:
            return self.config.CASING_MAP[low]
        de_regions = {
            "nordrhein-westfalen": "Nordrhein-Westfalen",
            "nordrhein westfalen": "Nordrhein-Westfalen",
            "rhein-main-gebiet": "Rhein-Main-Gebiet",
            "rhein-main": "Rhein-Main",
            "baden-wuerttemberg": "Baden-Württemberg",
            "baden-württemberg": "Baden-Württemberg",
            "sachsen-anhalt": "Sachsen-Anhalt",
            "mecklenburg-vorpommern": "Mecklenburg-Vorpommern",
            "schleswig-holstein": "Schleswig-Holstein",
            "rheinland-pfalz": "Rheinland-Pfalz",
            "thueringen": "Thüringen",
            "thüringen": "Thüringen",
            "niedersachsen": "Niedersachsen",
        }
        if low in de_regions:
            return de_regions[low]
        display_map = {
            "sao paulo": "São Paulo", "sao leopoldo": "São Leopoldo",
            "duesseldorf": "Düsseldorf", "koeln": "Köln", "muenchen": "München",
            "munchen": "München", "zuerich": "Zürich", "zurich": "Zürich",
            "wroclaw": "Wrocław", "lodz": "Łódź", "gdansk": "Gdańsk",
            "krakow": "Kraków", "kyiv": "Kyiv", "lviv": "Lviv",
            "mexico city": "Mexico City", "ho chi minh": "Ho Chi Minh",
            "the hague": "The Hague", "tysons": "Tysons", "tysons corner": "Tysons Corner",
            "nuernberg": "Nürnberg", "nurnberg": "Nürnberg", "wuerzburg": "Würzburg",
            "wuertzburg": "Würzburg", "fuerth": "Fürth", "further": "Fürth",
            "rüsselsheim": "Rüsselsheim", "ruesselsheim": "Rüsselsheim",
            "saarbruecken": "Saarbrücken", "saarbrücken": "Saarbrücken",
            "tuebingen": "Tübingen", "tübingen": "Tübingen",
            "osnabrueck": "Osnabrück", "osnabrück": "Osnabrück",
            "moenchengladbach": "Mönchengladbach", "mönchengladbach": "Mönchengladbach",
            "guetersloh": "Gütersloh", "gütersloh": "Gütersloh",
            "boeblingen": "Böblingen", "böblingen": "Böblingen",
        }
        if low in display_map:
            return display_map[low]
        if "," in low:
            return ", ".join(self._fmt_place(p) for p in low.split(",") if p.strip())
        particles = {"am", "bei", "upon", "de", "la", "le", "du", "di", "del",
                     "der", "den", "und", "van", "von", "sur", "sous", "im", "in"}
        words = low.split()
        out = []
        for w in words:
            if w in self.config.CASING_MAP:
                out.append(self.config.CASING_MAP[w])
            elif w in particles:
                out.append(w)
            elif len(w) == 2 and w.isalpha():
                out.append(w.upper())  # state / country codes: ny -> NY
            elif "-" in w:
                # capitalize after hyphens: emilia-romagna -> Emilia-Romagna
                out.append("-".join(part.capitalize() for part in w.split("-")))
            else:
                out.append(w.capitalize())
        return " ".join(out)

    def _norm(self, s):
        """Normalize a place string: lowercase, strip diacritics
        (Wrocław -> wroclaw, München -> munchen)."""
        try:
            # characters with no NFKD decomposition must be transliterated manually
            s = s.translate(str.maketrans({
                "\u0142": "l", "\u0141": "L", "\u0105": "a", "\u0104": "A",
                "\u0119": "e", "\u0118": "E", "\u0144": "n", "\u0143": "N",
                "\u015b": "s", "\u015a": "S", "\u017a": "z", "\u0179": "Z",
                "\u017c": "z", "\u017b": "Z", "\u0107": "c", "\u0106": "C",
                "\u00f8": "o", "\u00d8": "O", "\u00e5": "a", "\u00c5": "A",
                "\u00e6": "ae", "\u00c6": "AE", "\u0153": "oe", "\u0152": "OE",
                "\u00df": "ss", "\u011f": "g", "\u011e": "G", "\u0131": "i",
                "\u015f": "s", "\u015e": "S", "\u0219": "s", "\u0218": "S",
                "\u021b": "t", "\u021a": "T", "\u0171": "u", "\u0151": "o",
                "\u0103": "a", "\u0102": "A", "\u010d": "c", "\u010c": "C",
                "\u0111": "d", "\u0110": "D", "\u0161": "s", "\u0160": "S",
                "\u017e": "z", "\u017d": "Z", "\u00f0": "d", "\u00d0": "D",
                "\u00fe": "th", "\u00de": "TH",
            }))
            s = unicodedata.normalize("NFKD", s)
            s = s.encode("ascii", "ignore").decode()
        except Exception:
            pass
        return re.sub(r"\s+", " ", s).strip().lower()

    def _fmt_region(self, region_lower):
        if region_lower in self.config.CASING_MAP:
            return self.config.CASING_MAP[region_lower]
        return region_lower.title()

    def _location_from_line(self, line):
        """Evaluate one candidate line/part as a location. Returns formatted location or None."""
        if not line:
            return None
        # strip parenthetical content completely (e.g. "(Part Time)", "(w/m/d)")
        line = re.sub(r"\(.*?\)", "", line)
        line = line.strip(" \t,;•.()[]-*")
        if not line:
            return None
        # strip trailing " office|site|campus|hub" etc., then re-evaluate
        stripped = re.sub(r"\b(office|site|campus|hub|location|headquarters)\b\s*$",
                          "", line.strip(), flags=re.IGNORECASE).strip()
        if stripped and stripped != line:
            return self._location_from_line(stripped)

        # FIX P0-8: strip trailing posting dates / timestamps BEFORE the digit
        # guard below. Boards like American Express append "09/14/2026" to the
        # location cell; guard #4 (\d{3,}) then rejected the entire string,
        # producing "Unknown" for 1,389 rows that had perfectly good data.
        line = re.sub(
            r"[\s,;|/–—-]*\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\s*$", "", line).strip()
        line = re.sub(
            r"[\s,;|/–—-]*\b\d{4}[/.-]\d{1,2}[/.-]\d{1,2}\s*$", "", line).strip()
        line = re.sub(
            r"[\s,;|-]*\b(posted|updated|pubblicato|aggiornato)\b.*$", "",
            line, flags=re.I).strip(" ,;|-")
        if not line:
            return None
        low = line.lower()
        if len(low) < 2 or len(low) > 60 or not re.search(r"[a-zA-ZÀ-ÿ]", line):
            return None
        nlow = self._norm(low)
        if low in {"global", "worldwide", "united", "anywhere"}:
            return None

        # 1) exact known place (diacritic-insensitive: Wrocław, München, Garching Bei München)
        if low in self.KNOWN_PLACES or nlow in self.NORM_KNOWN:
            canonical = self.NORM_KNOWN.get(nlow, low)
            return self._fmt_place(canonical)

        # 1b) "City, Country" / "City, State" comma pattern — checked BEFORE the
        #     reject list so office names get salvaged:
        #     "The River Building Hq, London" -> "London"
        if "," in line or "，" in line:
            raw_segs = re.split(r"[,，]", line)
            segs = [s.strip(" \t") for s in raw_segs if s.strip(" \t")]
            if segs:
                def _is_known_seg(sg):
                    sg_low = sg.lower()
                    sg_n = self._norm(sg_low)
                    if (sg_low in self.KNOWN_PLACES or sg_n in self.NORM_KNOWN
                            or sg_low in self.config.COUNTRIES_AND_REGIONS
                            or (re.fullmatch(r"[a-z]{2}", sg_low)
                                and (sg_low in self.STATE_CODES or sg_low in self.COUNTRY_CODES))):
                        return True
                    # P0-9: gazetteer, disambiguated by the FULL line as context
                    if len(sg.split()) <= 4 and not self.LOCATION_REJECT_RE.search(sg_low) \
                            and not self.config.ROLE_WORD_PATTERN.search(sg):
                        try:
                            return gazetteer_lookup(sg, line) is not None
                        except Exception:
                            return False
                    return False
                def _is_junk_seg(sg):
                    # title/dept garbage embedded in the location string
                    # ("Associate Optometrist-Katy, TX", "NL Credit Controller - ...")
                    return (self.config.ROLE_WORD_PATTERN.search(sg)
                            or bool(self.LOCATION_REJECT_RE.search(sg.lower())))
                # walk backward from the last segment:
                #   - keep known places / state codes
                #   - SKIP junk segments (role words, departments) so the real
                #     place at the end still wins:
                #     "Hesse, Germany Lakebase Specialist Hesse, Germany" -> "Hesse, Germany"
                #     "Boston, MA 14457 Recruiter (boston, MA" -> "Boston, MA"
                #   - stop at clean unknown segments (office names):
                #     "The River Building Hq, London" -> "London"
                keep = []
                i = len(segs) - 1
                stopped_at = None
                while i >= 0:
                    if re.fullmatch(r"[a-z]{2}", segs[i].lower()) and not _is_known_seg(segs[i]):
                        i -= 1      # unknown trailing code ("Paris, FR" -> drop FR)
                        continue
                    if _is_known_seg(segs[i]):
                        keep.append(segs[i])
                        i -= 1
                        continue
                    if _is_junk_seg(segs[i]):
                        i -= 1      # drop title/department junk
                        continue
                    stopped_at = segs[i]
                    break
                if keep:
                    keep.reverse()
                    # drop duplicates anywhere ("Madrid, Spain, Madrid" -> "Madrid, Spain")
                    deduped = []
                    seen_seg = set()
                    for seg in keep:
                        sk = seg.lower()
                        if sk not in seen_seg:
                            seen_seg.add(sk)
                            deduped.append(seg)
                    keep = deduped
                    # v7: in "City, XX", US/Canadian state/province codes take
                    # priority over overlapping country codes (CA, IN, IL, MA, ...).
                    # This prevents "Chicago, IL" -> "Chicago, Israel".
                    mapped_keep = []
                    for seg in keep:
                        code = seg.lower()
                        if re.fullmatch(r"[a-z]{2}", code) and code in self.STATE_CODES:
                            mapped_keep.append(code.upper())
                        elif re.fullmatch(r"[a-z]{2}", code) and code in self.COUNTRY_CODES:
                            mapped_keep.append(self.COUNTRY_CODES[code])
                        else:
                            mapped_keep.append(seg)
                    keep = mapped_keep
                    # FIX P0-3: preserve an UNKNOWN leading city segment.
                    # The backward walk keeps only allowlisted places, so real
                    # cities missing from KNOWN_PLACES were silently deleted:
                    #   "Bleiswijk, South Holland, NL" -> "South Holland, NL"
                    #   "Goodyear, AZ, United States"  -> "AZ, United States"
                    #   "Warszawa, Masovian, Poland"   -> "Poland"
                    # Office/building names must still be dropped, so a stopped
                    # segment is only promoted when it looks like a placename.
                    if (keep and segs
                            and segs[0].strip().lower()
                            not in {k.strip().lower() for k in keep}):
                        _sa = segs[0].strip()
                        _sal = _sa.lower()
                        _BUILDING = (
                            "building", "hq", "headquarter", "tower", "house",
                            "campus",
                            "office", "floor", "suite", "street", "road",
                            "avenue", "ave", "strasse", "straße", "via ",
                            "piazza", "gebouw", "warehouse", "site", "depot",
                            "store", "shop", "mall", "unit", "block", "wing",
                        )
                        if (1 <= len(_sa.split()) <= 3
                                and not re.search(r"\d", _sa)
                                and not any(b in _sal for b in _BUILDING)
                                and not _is_junk_seg(_sa)
                                and not self.LOCATION_REJECT_RE.search(_sal)
                                and not self.config.ROLE_WORD_PATTERN.search(_sa)
                                and _sal not in {x.casefold() for x in self.config.HARD_TITLE_BLACKLIST}
                                and re.fullmatch(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'\-]*", _sa)):
                            keep = [_sa] + keep

                    # never emit a BARE state code ("Associate, TX" -> reject)
                    if len(keep) == 1 and re.fullmatch(r"[a-z]{2}", keep[0].lower()):
                        code = keep[0].upper()
                        if stopped_at is not None and not _is_junk_seg(stopped_at):
                            sa = stopped_at.strip()
                            sa_low = sa.lower()
                            # typo / OCR fix
                            for bad, good in self.config.TYPO_MAP.items():
                                if sa_low == bad:
                                    sa = good
                                    sa_low = good
                                    break
                            # full-string known lookup first ("Fort Lauderdale")
                            if sa_low in self.KNOWN_PLACES or self._norm(sa_low) in self.NORM_KNOWN:
                                return f"{self._fmt_place(self._norm(sa_low))}, {code}"
                            # else: trailing known-word run ("Yieldstar Austin" -> "Austin")
                            sa_words = sa_low.split()
                            run = []
                            for w in reversed(sa_words):
                                w_n = self._norm(w)
                                if (w in self.KNOWN_PLACES or w_n in self.NORM_KNOWN
                                        or w in self.config.COUNTRIES_AND_REGIONS):
                                    run.append(w)
                                else:
                                    break
                            run = list(reversed(run))
                            if run:
                                countries = [
                                    w for w in run
                                    if w in self.config.COUNTRIES_AND_REGIONS
                                    and w not in self.KNOWN_CITIES
                                    and w not in self.MULTI_WORD_CITIES
                                ]
                                cities = [w for w in run if w not in countries]
                                if countries and cities:
                                    # "Frames - Turkey Istanbul, TN" -> "Istanbul, Turkey"
                                    return f"{self._fmt_place(self._norm(cities[-1]))}, {self._fmt_place(self._norm(countries[-1]))}"
                                if countries:
                                    return self._fmt_place(self._norm(countries[-1]))
                                # "Yieldstar Austin, TX" -> "Austin, TX"
                                return f"{self._fmt_place(self._norm(cities[-1]))}, {code}"
                        return None
                    return ", ".join(self._fmt_place(self._norm(s)) for s in keep)
                return None

        # 1c) trailing run of known place words ("Place Amedee Bonnet Lyon" -> "Lyon",
        #     "Barcelona Spain" -> "Barcelona, Spain") — checked before the reject
        #     list so a real place at the end is salvaged even when junk precedes it
        words = low.split()
        if len(words) >= 2:
            known_run = []
            for w in reversed(words):
                w_n = self._norm(w)
                # Country codes are intentionally not expanded in free text: ID in
                # "Entra ID" and CA in titles are not locations. Codes remain valid
                # only in comma-address form handled above.
                if (w in self.KNOWN_PLACES or w_n in self.NORM_KNOWN
                        or w in self.config.COUNTRIES_AND_REGIONS):
                    known_run.append(w)
                else:
                    break
            known_run = list(reversed(known_run))
            if known_run:
                parts_out = []
                for w in known_run:
                    parts_out.append(self._fmt_place(self._norm(w)))
                return ", ".join(parts_out)

        # FIX P0-8b: reversed "Country City" order (ABN AMRO emits
        # "Netherlands Amstelveen", "Belgium Antwerpen"). The trailing-run scan
        # above only handles "City Country".
        if len(words) == 2:
            w0, w1 = words[0], words[1]
            w0_country = (w0 in self.config.COUNTRIES_AND_REGIONS
                          and w0 not in self.KNOWN_CITIES)
            if w0_country and not self.LOCATION_REJECT_RE.search(w1) \
                    and not self.config.ROLE_WORD_PATTERN.search(w1) \
                    and re.fullmatch(r"[a-zà-ÿ][a-zà-ÿ.'\-]{2,}", w1):
                return f"{self._fmt_place(self._norm(w1))}, {self._fmt_place(self._norm(w0))}"

        # 2) reject words (UI text, departments, brands, abbreviations, job words,
        #    workload terms, tech stacks, business units)
        if self.LOCATION_REJECT_RE.search(low):
            return None

        # 3) role words / title blacklist
        if self.config.ROLE_WORD_PATTERN.search(line) or low in self.config.HARD_TITLE_BLACKLIST:
            return None

        # 4) currency / long digit runs / emails / symbols
        if re.search(r"[£$€¥%@&+]", line) or re.search(r"\d{3,}", low) or re.search(r"\d+%", low):
            return None

        # 5) work-mode / workload words — try to salvage a city after a mode prefix
        if re.search(
            r"\b(full[- ]?time|part[- ]?time|internship|intern|trainee|stage|"
            r"werkstudent|working student|praktikum|fixed[- ]?term|permanent|"
            r"contract|temporary|remote|hybrid|onsite|on[- ]?site|home office|"
            r"salary|annum|hourly|experience|years?|days?|weeks?|months?|"
            r"vollzeit|teilzeit|seasonal)\b", low,
        ):
            m = re.search(
                r"\b(?:remote|hybrid|onsite|on-site|full[- ]?time|part[- ]?time|"
                r"internship|trainee|stage|werkstudent|vollzeit|teilzeit|seasonal)\b"
                r"[^a-zA-Z]{1,20}([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'\-]{2,45})$",
                line, re.IGNORECASE,
            )
            if m:
                cand = m.group(1).strip()
                if cand.lower() != line.lower():
                    res = self._location_from_line(cand)
                    if res:
                        return res
            return None



        # 7) bare 2-letter codes are NEVER a location on their own
        #    ("FL", "GA", "DE" from store data are useless; state codes are
        #    only accepted inside "City, XX" via the comma branch above)
        if re.fullmatch(r"[a-z]{2}", low):
            return None

        # 8) single known city / country / region (diacritic-insensitive)
        if low in self.KNOWN_CITIES or low in self.config.COUNTRIES_AND_REGIONS or nlow in self.NORM_KNOWN:
            canonical = self.NORM_KNOWN.get(nlow, low)
            return self._fmt_place(canonical)

        # 9) multi-word city completion (e.g. "Palo" -> "Palo Alto")
        for two in self.MULTI_WORD_CITIES:
            if low == two or low.startswith(two + " "):
                return self._fmt_place(two)

        # 10) heuristic: a phrase is a plausible place ONLY if every word is a
        #     known city/country/region token (NO free-form proper-noun guessing —
        #     that produced "Java", "Vice President", "Mack", "Hotels", ...)
        if len(words) <= 5:
            known_hits = sum(
                1 for w in words
                if w in self.KNOWN_PLACES or self._norm(w) in self.NORM_KNOWN
                or w in self.config.COUNTRIES_AND_REGIONS
            )
            if known_hits and known_hits == len(words) and len(low) >= 3:
                return ", ".join(self._fmt_place(self._norm(w)) for w in words)

        # 11) FIX P0-9: gazetteer fallback (only reached when every allowlist
        # rule above failed). Disambiguated by country hint + population, so
        # "Ho Chi Minh City" / "Selangor" resolve while hamlet collisions
        # ("Phoenix"->Vacoas MU) do not. No-op when geonamescache is absent.
        if not self.LOCATION_REJECT_RE.search(low) \
                and not self.config.ROLE_WORD_PATTERN.search(line) \
                and len(words) <= 5:
            try:
                hit = gazetteer_lookup(line, line)
                if hit:
                    return f"{hit[0]}, {hit[1]}"
            except Exception:
                pass
        return None

    def extract_location(self, text):
        if not text:
            return "Not Specified"
        text = self.fix_encoding(str(text))
        if self._norm(text.strip()) in {"global", "worldwide", "united", "anywhere"}:
            return "Not Specified"

        # v7: preserve structural boundaries. Location hints are passed as the
        # first line, so evaluate lines independently before any flattening.
        raw_lines = [
            re.sub(r"[ \t]+", " ", part).strip()
            for part in re.split(r"[\r\n|•;]+", text)
            if part.strip()
        ]
        for line in raw_lines:
            res = self._location_from_line(line)
            if res:
                return res
            # Explicit City - Country or Mode - City structures.
            for part in re.split(r"\s+[-–—]\s+", line):
                res = self._location_from_line(part)
                if res:
                    return res

        # Last fallback: exact full text after safe space normalization.
        flat = re.sub(r"[ \t]+", " ", text).strip()
        if "\n" not in flat and "\r" not in flat:
            res = self._location_from_line(flat)
            if res:
                return res
        return "Not Specified"

    # FIXED: URL-path location parser, now validation-gated.
    # Only returns a value if it is a KNOWN place (city/country/region) found as a
    # segment AFTER a real job marker. UI route segments (JobDetail, externaljobs,
    # jobsuche) and last-segment fallbacks are rejected, killing the "JobDetail"
    # garbage and truncated city names ("Palo" from "Palo-Alto-CA").
    def extract_location_from_url(self, url):
        if not url:
            return None
        try:
            url_decoded = urllib.parse.unquote(url)
            parsed = urlparse(url_decoded)
            path = parsed.path
            segments = [s for s in path.split("/") if s]
            if not segments:
                return None

            # "in-[location]" pattern (e.g. /jobs-in-berlin)
            in_match = re.search(r"\bin-([a-z-]+)\b", path.lower())
            if in_match:
                cand = in_match.group(1).replace("-", " ").strip()
                cand = re.sub(r"\b(jid|id|\d+)\b", "", cand, flags=re.IGNORECASE).strip()
                if cand in self.KNOWN_PLACES:
                    return self._fmt_place(cand)

            # Candidate = segment right after the LAST job marker in the path
            # (e.g. /en/careers/jobs/hsinchu/123 -> "hsinchu", not "jobs")
            candidate = None
            for i, s in enumerate(segments):
                if s.lower() in self.config.URL_JOB_MARKERS and i + 1 < len(segments):
                    candidate = segments[i + 1]
            if candidate is None:
                # FIXED: Hays-style URLs bury the city inside one giant hyphenated
                # slug with NO standalone job marker
                # ("stellenangebote-jobs-detail-...-karlsruhe-883930/1").
                # Scan every slug part across all path segments instead.
                all_parts = []
                for seg in segments:
                    seg = seg.split(".")[0]
                    all_parts.extend(
                        p for p in re.split(r"[\-_]", seg)
                        if p and not p.isdigit() and len(p) >= 3
                    )
                for n in range(min(3, len(all_parts)), 0, -1):
                    for i in range(len(all_parts) - n + 1):
                        for sep in (" ", "-"):
                            joined = sep.join(all_parts[i:i + n]).lower()
                            if joined in self.KNOWN_PLACES or self._norm(joined) in self.NORM_KNOWN:
                                return self._fmt_place(joined)
                for p in all_parts:
                    p_low = p.lower()
                    if (p_low in self.KNOWN_CITIES or p_low in self.config.COUNTRIES_AND_REGIONS
                            or self._norm(p_low) in self.NORM_KNOWN):
                        return self._fmt_place(p_low)
                return None

            candidate = candidate.split(".")[0]
            parts = [p for p in re.split(r"[\-_]", candidate) if p and not p.isdigit()]
            if not parts:
                return None

            # Longest-first known-place subsequence scan within the slug parts
            best = None
            for n in range(min(4, len(parts)), 0, -1):
                for i in range(len(parts) - n + 1):
                    joined = " ".join(parts[i:i + n]).lower()
                    if joined in self.KNOWN_PLACES or self._norm(joined) in self.NORM_KNOWN:
                        best = joined
                        break
                if best:
                    break
            if best:
                return self._fmt_place(best)

            # Last resort: a single part that is a known city/country/region
            for p in parts:
                p_low = p.lower()
                if (p_low in self.KNOWN_CITIES or p_low in self.config.COUNTRIES_AND_REGIONS
                        or self._norm(p_low) in self.NORM_KNOWN):
                    return self._fmt_place(p_low)
        except Exception:
            pass
        return None

    # FIXED: Added a robust country and region scanner fallback.
    # Searches any text for major global and European country names or regional states (Piemonte, Lombardia, etc.) as whole words.
    # FIXED: Region scanner, now end-anchored and casing-aware.
    # "global"/"worldwide" are excluded, "uk" becomes "UK", and matches are only
    # accepted at end-of-string (title scans) or end-of-line (context scans).
    def extract_country_or_region(self, text, end_of_string=True):
        if not text:
            return None
        text = self.fix_encoding(text)
        low = text.lower()
        m = self.REGION_END_RE.search(low) if end_of_string else self.REGION_LINE_RE.search(low)
        if m:
            return self._fmt_region(m.group(1))
        return None

    def parse_job_metadata(self, name, title, context_text, clean_url=""):
        title = self.fix_encoding(title or "")
        context_text = self.fix_encoding(context_text or "")
        combined = f"{title}\n{context_text}".strip()
        cl = combined.casefold()

        # v7: card/listing text cannot prove immigration support or Blue Card.
        eu_blue_card = "Unknown"
        reloc_support = "Unknown"
        workload = "Unknown"
        if any(k in cl for k in ["part time", "part-time", "parttime", "teilzeit", "deeltijd"]):
            workload = "Part-time"
        elif any(k in cl for k in [
            "internship", "tirocinio", "apprendistato", "trainee", "praktikum",
            "stagiaire", "ausbildung", "duales studium", "working student",
            "werkstudent", "stage "
        ]):
            workload = "Internship"
        elif any(k in cl for k in ["full time", "full-time", "fulltime", "vollzeit", "voltijd"]):
            workload = "Full-time"
        elif any(k in cl for k in ["contract", "fixed-term", "fixed term", "temporary"]):
            workload = "Contract"

        work_mode = "Unknown"
        remote_hit = any(k in cl for k in [
            "fully remote", "remote-first", "remote", "da remoto", "remoto",
            "home office", "homeoffice", "thuiswerk", "télétravail"
        ])
        hybrid_hit = any(k in cl for k in ["hybrid", "ibrido", "smart working", "smartworking", "hybride"])
        if remote_hit and hybrid_hit:
            work_mode = "Remote/Hybrid"
        elif remote_hit:
            work_mode = "Remote"
        elif hybrid_hit:
            work_mode = "Hybrid"
        elif any(k in cl for k in ["on-site", "onsite", "in-office", "in office"]):
            work_mode = "On-site"

        location, source = "Not Specified", "none"
        # Explicit card location hint/context, then title and URL.
        loc = self.extract_location(context_text)
        if loc != "Not Specified":
            location, source = loc, "card"
        if location == "Not Specified":
            loc = self.extract_location(title)
            if loc != "Not Specified":
                location, source = loc, "title"
        if location == "Not Specified" and clean_url:
            url_loc = self.extract_location_from_url(clean_url)
            if url_loc and self._norm(url_loc) not in {"global", "worldwide", "united"}:
                location, source = url_loc, "url"
        if location == "Not Specified":
            reg = self.extract_country_or_region(context_text, end_of_string=False)
            if reg and self._norm(reg) not in {"global", "worldwide", "united"}:
                location, source = reg, "region"

        # Reject company names as locations. v7 never stamps headquarters into
        # the verified Job Location field.
        if location != "Not Specified":
            low_loc = location.casefold().strip()
            loc_words = set(re.findall(r"[a-zà-ÿ]+", low_loc))
            name_words = set(re.findall(r"[a-zà-ÿ]+", name.casefold()))
            if loc_words and loc_words <= name_words and self._norm(low_loc) not in self.NORM_KNOWN:
                location, source = "Not Specified", "none"
        return location, f"{workload} / {work_mode}", eu_blue_card, reloc_support, source

    def canonical_job_id(self, company, url, title="", location="", provider="auto"):
        """Stable identity independent of apply/detail mirror URLs."""
        u = urllib.parse.unquote(url or "")
        candidates = []
        for pattern in (
            r"(?i)(?:jobid|job_id|gh_jid|reqid|requisitionid|career_job_req_id|postingid|r)=([A-Za-z]*\d{4,})",
            r"(?i)(?:^|[/_-])(R\d{5,})(?:[-_/?]|$)",
            r"(?i)[/_-](?:JR|REQ)[-_]?(\d{4,})(?:[-_/?#]|$)",
            r"(?i)/jobs?/(\d{5,})(?:/|$)",
            r"(?i)/job/([0-9a-f]{8}-[0-9a-f-]{27,})(?:/|$)",
            r"(?i)/([0-9a-f]{8}-[0-9a-f-]{27,})(?:/|$)",
        ):
            candidates.extend(re.findall(pattern, u))
        identity = candidates[0].casefold() if candidates else ""
        # FIX P0-2: a bare 5+ digit number is NOT globally unique. Namespace it
        # with its (locale-stripped) parent path so /careers/roles/12345 and
        # /negozi/12345 stay distinct, while /en/ vs /de/ mirrors still collapse.
        if not identity:
            _p = urlparse(url or "")
            _m = re.search(r"/(\d{5,})(?:/?(?:[?#]|$))", _p.path)
            if _m:
                _ns = re.sub(r"^/(?:[a-z]{2}(?:[-_][a-z]{2})?)(?=/)", "", _p.path, flags=re.I)
                _ns = _ns[:_ns.rfind(_m.group(1))].rstrip("/").casefold()
                _host = _p.netloc.casefold().removeprefix("www.")
                identity = f"{_host}{_ns}/{_m.group(1).casefold()}"
        p = urlparse(url or "")
        if not identity:
            # Batch N phase 2a: the netloc+path fallback collapses every
            # same-page URL to ONE identity — fatal for fragment-identified
            # boards (Pam/DR #job= adapters: 159 jobs -> 1 row) and bare
            # ?id= boards (FS view-job.php: 3 jobs -> 1 row). Scope the
            # fallback with the fragment / id-query when present. Bare-ID
            # behavior above is untouched (G3 cross-mirror collapsing intact).
            base = (p.netloc.casefold().removeprefix("www.") + p.path.rstrip("/").casefold())
            frag = (p.fragment or "")
            if frag.lower().startswith("job=") and len(frag) > 4:
                identity = f"{base}#{frag.casefold()}"
            else:
                qid = ""
                try:
                    for k, v in parse_qsl(p.query or ""):
                        if k.lower() in ("id", "jobid", "job_id", "jid",
                                         "gh_jid", "reqid", "postingid") and (v or "").strip():
                            qid = v.strip()
                            break
                except Exception:
                    qid = ""
                identity = f"{base}?id={qid.casefold()}" if qid else base
        if not identity and title:
            identity = f"title:{self._norm(title)}|loc:{self._norm(location)}"
        # G3 fix: provider/target must not fragment identity — the same job
        # via ATS API vs career page (or under two seeds) is the same job.
        # The call site passes "name|target"; only the base name is used.
        base_company = company.split("|")[0].casefold()
        return f"{base_company}|{identity}"

    def _record_quality(self, rec):
        score = 0
        if rec.get("URL Type") == "real": score += 20
        if rec.get("Job Location") not in {"Unknown", "Not Specified", "Global", "United"}: score += 8
        if rec.get("Location Source") in {"card", "detail"}: score += 5
        if rec.get("Raw Job Title"): score += 1
        if rec.get("Raw Location"): score += 1
        if re.search(r"applicationmethods|/apply(?:/|$)", rec.get("Job URL", ""), re.I): score -= 30
        return score

    def _location_in_europe(self, location, blob):
        """Europe region-target check (G3: EU + EEA + UK + Switzerland).

        Resolver first (city-aware: "Amsterdam" -> Netherlands), then
        alias-substring fallback for context/URL evidence and standalone
        mode. Unproven locations return False, same as single-country
        targets (reviewable in quarantine, never silently dropped).
        """
        if country_from_location is not None:
            try:
                if (country_from_location(location or "") or "").casefold() in EUROPE_COUNTRIES:
                    return True
            except Exception:
                pass
        if any(re.search(r"(?:^|[^a-z])" + re.escape(a) + r"(?:$|[^a-z])", blob)
               for a in EUROPE_ALIASES):
            return True
        # FIX P0-7: standalone runs have no country_from_location module, so a
        # BARE European city ("Köln", "Berlin", "Amsterdam") matched no country
        # alias and was quarantined as non-European. Measured: 20 Köln rows
        # wrongly rejected from Ikea Italia alone. Resolve city -> country
        # using the allowlist already built in __init__.
        first = self._norm((location or "").split(",")[0]).strip()
        if first and first in _EUROPE_CITY_INDEX:
            return True
        return False

    def _scope_allows(self, target_row, location, context="", url=""):
        policy = (target_row.get("scope_policy") or "global").lower()
        target = (target_row.get("target_country") or "Global").strip()
        if policy == "global" or target.casefold() == "global":
            return True
        if policy == "seed_url":
            # FIX P0-13: previously an unconditional accept, so 42 rows that
            # declared a target_country got ZERO enforcement (Michael Page UK,
            # Hays DE, Robert Walters IE/NL all serve multi-country results).
            # Now: accept, but only after the same alias check runs, so the
            # caller can flag unverified rows rather than trusting blindly.
            if not target or target.casefold() == "global":
                return True
            self._last_scope_verified = self._scope_country_match(
                target, location, context, url)
            return True
        return self._scope_country_match(target, location, context, url)

    def _scope_country_match(self, target, location, context="", url=""):
        blob = self._norm(" ".join([location or "", context or "", urllib.parse.unquote(url or "")]))
        if target.casefold() == "europe":
            return self._location_in_europe(location, blob)
        aliases = {
            "germany": {"germany", "deutschland", "berlin", "hamburg", "munich", "munchen", "muenchen", "frankfurt", "cologne", "koln", "koeln", "dusseldorf", "duesseldorf", "stuttgart", "hannover", "bremen", "leipzig", "dresden", "bayern", "bavaria"},
            "italy": {"italy", "italia", "milan", "milano", "rome", "roma", "turin", "torino", "bologna", "napoli", "parma", "venice", "venezia", "florence", "firenze", "lombardia", "lombardy", "piemonte", "toscana", "sicilia"},
            "netherlands": {"netherlands", "nederland", "amsterdam", "rotterdam", "utrecht", "haarlem", "delft", "eindhoven", "north holland", "noord holland", "zuid holland"},
            "united kingdom": {"united kingdom", "england", "scotland", "wales", "northern ireland", "london", "manchester", "birmingham", "edinburgh", "glasgow", "uk"},
            "ireland": {"ireland", "dublin", "cork", "galway", "limerick"},
        }
        return any(re.search(r"(?:^|[^a-z])" + re.escape(a) + r"(?:$|[^a-z])", blob) for a in aliases.get(target.casefold(), {target.casefold()}))

    # ── Batch N phase 2a custom provider adapters ─────────────────────────
    def _fetch_pam_jobs(self, target_row):
        """Pam Panorama (lavoraconnoi.gruppopam.it) JSON API.

        board_slug: "sede" (HQ jobs, pam_departments==sede) or
        "region:<State>" (e.g. region:Lazio). One call returns every
        posting (limit/offset params are ignored server-side); we filter
        client-side to status==published and drop "Candidatura Spontanea"
        pools. The site exposes no per-job URLs (JS rows), so job_url is
        the seed listing page + #job=<id> (synthetic but stable/unique).
        """
        slug = (target_row.get("board_slug") or "").strip()
        seed_url = (target_row.get("careers_url") or "").strip().rstrip("/")
        if not slug or not seed_url:
            return [], "pam API skipped: need board_slug + careers_url"
        headers = {"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36",
                   "Accept": "application/json"}
        url = ("https://lavoraconnoi.gruppopam.it/api.php?function=job-posts"
               "&status=published&country=107&limit=100&sort=published")
        data = json.loads(self._http_fetch_with_retry(url, headers))
        mode, _, arg = slug.partition(":")
        mode, arg = mode.strip().lower(), arg.strip().lower()
        jobs, skipped_spont = [], 0
        for it in data if isinstance(data, list) else []:
            if (it.get("status") or "") != "published":
                continue
            title = (it.get("title") or "").strip()
            if not title:
                continue
            if title.startswith("Candidatura Spontanea"):
                skipped_spont += 1  # evergreen pool, not an opening
                continue
            det = it.get("details") or {}
            loc = det.get("loc") or {}
            def _pick(section):
                vals = loc.get(section) or {}
                return next(iter(vals.values()), {}).get("value", "") if isinstance(vals, dict) else ""
            city, state = _pick("city"), _pick("state")
            # Batch N: admin-area labels -> their city ("Città metropolitana
            # di Roma Capitale" -> "Roma"). Bounded to this prefix only.
            m = re.match(r"(?i)^citt[àa]\s+metropolitana\s+di\s+(.+)$",
                         (city or "").strip())
            if m:
                city = re.sub(r"(?i)\s+capitale$", "", m.group(1)).strip()
            if mode == "sede":
                if ((it.get("custom_fields") or {}).get("pam_departments") or "") != "sede":
                    continue
            elif mode == "region":
                if (state or "").lower() != arg:
                    continue
            else:
                return [], f"pam API skipped: bad board_slug {slug!r} (want 'sede' or 'region:<State>')"
            jid = (it.get("id") or "").strip()
            contract = det.get("contract") or ""
            dept = (it.get("custom_fields") or {}).get("pam_departments") or ""
            jobs.append({
                "job_title": title,
                "job_url": f"{seed_url}#job={jid}",
                "location_hint": ", ".join(x for x in (city, state, "Italia") if x),
                "card_context": " | ".join(x for x in (contract, dept) if x),
                "extraction_method": "pam_api",
            })
        return jobs, f"pam API: {len(jobs)} ({slug}; skipped {skipped_spont} spontaneous)"

    def _fetch_digitalrecruiters_jobs(self, target_row):
        """DigitalRecruiters/Cegid boards (POST JSON API).

        board_slug: the careers-site host, e.g.
        lavoraconnoi.decathlon-careers.it. POSTs
        {"filters": {}, "coordinates": {"lat": 0, "lng": 0}} with
        limit=100 + page loop. The site exposes no server-routed detail
        pages (SPA shell), so job_url is the seed listing page +
        #job=<slug> (synthetic but stable; slug holds the req id).
        """
        slug = (target_row.get("board_slug") or "").strip()
        seed_url = (target_row.get("careers_url") or "").strip().rstrip("/")
        if not slug or not seed_url:
            return [], "digitalrecruiters API skipped: need board_slug + careers_url"
        headers = {"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36",
                   "Accept": "application/json",
                   "Origin": f"https://{slug}", "Referer": seed_url}
        jobs, seen, page = [], set(), 1
        while page <= 10:  # safety cap: 10 x 100
            url = (f"https://api.digitalrecruiters.com/public/v1/careers-site/job-ads"
                   f"?domainName={slug}&limit=100&page={page}")
            data = json.loads(self._http_fetch_with_retry(
                url, headers, post_body={"filters": {}, "coordinates": {"lat": 0, "lng": 0}}))
            items = data.get("items") or []
            if not items:
                break
            for it in items:
                jid = str(it.get("job_ad_id") or it.get("id") or "").strip()
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                title = (it.get("title") or "").strip()
                if not title:
                    continue
                job_slug = (it.get("url") or jid).strip()
                jobs.append({
                    "job_title": title,
                    "job_url": f"{seed_url}#job={job_slug}",
                    "location_hint": (it.get("location") or "").strip(),
                    "card_context": " | ".join(x for x in ((it.get("job") or ""), (it.get("contract") or "")) if x),
                    "extraction_method": "digitalrecruiters_api",
                })
            total = data.get("count") or 0
            if len(items) < 100 or (total and len(seen) >= total):
                break
            page += 1
        return jobs, f"digitalrecruiters API: {len(jobs)}"

    def _fetch_teamtailor_jobs(self, target_row):
        """Teamtailor public feed: https://<slug>.teamtailor.com/jobs.json"""
        seed = (target_row.get("careers_url") or "").strip()
        slug = (target_row.get("board_slug") or "").strip()
        host = urlparse(seed).hostname or ""
        if not slug:
            slug = host.split(".")[0] if host.endswith("teamtailor.com") else ""
        base = f"https://{slug}.teamtailor.com" if slug else f"https://{host}"
        headers = {"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36",
                   "Accept": "application/json"}
        data = json.loads(self._http_fetch_with_retry(base + "/jobs.json", headers))
        # Teamtailor serves JSON Feed 1.1: {"items": [...]}
        items = data.get("items") or data.get("jobs") or []
        jobs = []
        for it in items:
            title = (it.get("title") or "").strip()
            url = (it.get("url") or it.get("external_url")
                   or it.get("careersite-job-url") or "").strip()
            if not title or not url:
                continue
            loc = it.get("location") or ""
            if isinstance(loc, dict):
                loc = loc.get("city") or loc.get("name") or ""
            tags = it.get("tags") or []
            if not loc and isinstance(tags, list):
                loc = next((t for t in tags if isinstance(t, str)), "")
            jobs.append({"job_title": title, "job_url": url,
                         "location_hint": str(loc or ""),
                         "card_context": " | ".join(
                             x for x in tags if isinstance(x, str))[:400],
                         "extraction_method": "teamtailor_api"})
        return jobs, f"teamtailor API: {len(jobs)}"

    def _fetch_oracle_jobs(self, target_row):
        """Oracle HCM (Fusion) recruiting REST feed used by Amex / Poste."""
        seed = (target_row.get("careers_url") or "").strip()
        p = urlparse(seed)
        m = re.search(r"/sites/([A-Za-z0-9_]+)", p.path)
        site = m.group(1) if m else (target_row.get("board_slug") or "").strip()
        if not site:
            return [], "oracle API skipped: no site code in careers_url"
        headers = {"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36",
                   "Accept": "application/json"}
        jobs, offset = [], 0
        while offset < 2000:
            api = (f"{p.scheme}://{p.netloc}/hcmRestApi/resources/latest/"
                   f"recruitingCEJobRequisitions?onlyData=true"
                   f"&expand=requisitionList.secondaryLocations"
                   f"&finder=findReqs;siteNumber={site},limit=200,offset={offset}")
            try:
                raw = self._http_fetch_with_retry(api, headers)
            except Exception as exc:
                return [], (f"oracle API unavailable ({type(exc).__name__}); "
                            "falling back to DOM")
            if not raw.lstrip().startswith(("{", "[")):
                return [], "oracle API returned non-JSON; falling back to DOM"
            data = json.loads(raw)
            items = (data.get("items") or [{}])[0].get("requisitionList") or []
            if not items:
                break
            for it in items:
                title = (it.get("Title") or "").strip()
                rid = (it.get("Id") or "").strip()
                if not title or not rid:
                    continue
                jobs.append({
                    "job_title": title,
                    "job_url": f"{p.scheme}://{p.netloc}{p.path}/job/{rid}",
                    "location_hint": (it.get("PrimaryLocation") or "").strip(),
                    "card_context": " | ".join(filter(None, [
                        it.get("JobFamily") or "", it.get("WorkplaceType") or ""])),
                    "extraction_method": "oracle_api"})
            if len(items) < 200:
                break
            offset += 200
        return jobs, f"oracle API: {len(jobs)}"

    def _fetch_provider_jobs(self, target_row):
        """Provider APIs first. Returns (jobs, diagnostic)."""
        provider = (target_row.get("provider") or "auto").lower()
        slug = (target_row.get("board_slug") or "").strip()
        if provider in ("pam", "digitalrecruiters"):
            # Batch N phase 2a: custom adapters with own fetch/filter logic.
            try:
                if provider == "pam":
                    return self._fetch_pam_jobs(target_row)
                return self._fetch_digitalrecruiters_jobs(target_row)
            except Exception as exc:
                return [], f"{provider} API failed: {type(exc).__name__}: {exc}"
        # FIX P0-10: adapters for providers named in the seed that previously
        # fell through to fragile DOM scraping (Amex, Poste Italiane, Klarna,
        # Teamtailor). Each degrades to the DOM path on failure.
        if provider == "teamtailor":
            try:
                return self._fetch_teamtailor_jobs(target_row)
            except Exception as exc:
                return [], f"teamtailor API failed: {type(exc).__name__}: {exc}"
        if provider == "oracle":
            try:
                return self._fetch_oracle_jobs(target_row)
            except Exception as exc:
                return [], f"oracle API failed: {type(exc).__name__}: {exc}"
        if not slug or provider not in {"greenhouse", "ashby", "lever", "personio", "recruitee", "workable"}:
            return [], "provider adapter not configured"
        try:
            if provider == "greenhouse":
                url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
            elif provider == "ashby":
                url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false"
            elif provider == "lever":
                url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
            elif provider == "personio":
                url = f"https://{slug}.jobs.personio.de/xml?language=en"
            elif provider == "workable":
                url = f"https://www.workable.com/api/accounts/{slug}?details=true"
            else:
                url = f"https://{slug}.recruitee.com/api/offers/"
            headers = {
                "User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36",
                "Accept": "application/json,text/xml,*/*",
            }
            raw = self._http_fetch_with_retry(url, headers)
            jobs = []
            if provider == "personio":
                import xml.etree.ElementTree as ET
                root = ET.fromstring(raw)
                for pos in root.findall(".//position"):
                    pid = (pos.findtext("id") or "").strip()
                    job_url = (pos.findtext("jobUrl") or "").strip()
                    if not job_url and pid:
                        job_url = f"https://{slug}.jobs.personio.de/job/{pid}"
                    jobs.append({
                        "job_title": (pos.findtext("name") or "").strip(),
                        "job_url": job_url,
                        "location_hint": (pos.findtext("office") or "").strip(),
                        "card_context": " | ".join(filter(None, [pos.findtext("department"), pos.findtext("employmentType"), pos.findtext("schedule")])),
                        "extraction_method": "personio_api",
                    })
                return jobs, f"personio API: {len(jobs)}"
            data = json.loads(raw)
            if provider == "greenhouse":
                for it in data.get("jobs") or []:
                    loc = (it.get("location") or {}).get("name") or ""
                    content = re.sub(r"<[^>]+>", " ", it.get("content") or "")
                    jobs.append({"job_title": it.get("title") or "", "job_url": it.get("absolute_url") or "", "location_hint": loc, "card_context": content[:1200], "jd_text": _jd_plain(it.get("content")), "extraction_method": "greenhouse_api"})
            elif provider == "ashby":
                for it in data.get("jobs") or []:
                    jobs.append({"job_title": it.get("title") or "", "job_url": it.get("jobUrl") or it.get("applyUrl") or "", "location_hint": it.get("location") or "", "card_context": " | ".join(filter(None, [it.get("department"), it.get("employmentType"), it.get("workplaceType")])), "jd_text": _jd_plain(it.get("descriptionPlain") or it.get("descriptionHtml")), "extraction_method": "ashby_api"})
            elif provider == "lever":
                for it in data:
                    cats = it.get("categories") or {}
                    loc = cats.get("location") or cats.get("allLocations") or ""
                    if isinstance(loc, list): loc = ", ".join(loc)
                    jobs.append({"job_title": it.get("text") or "", "job_url": it.get("hostedUrl") or "", "location_hint": str(loc), "card_context": " | ".join(filter(None, [cats.get("team"), cats.get("commitment"), it.get("workplaceType")])), "jd_text": _jd_plain(it.get("descriptionPlain") or it.get("description")), "extraction_method": "lever_api"})
            elif provider == "recruitee":
                for it in data.get("offers") or []:
                    jobs.append({"job_title": it.get("title") or "", "job_url": it.get("careers_url") or "", "location_hint": it.get("location") or "", "card_context": it.get("department") or "", "jd_text": _jd_plain(it.get("description") or it.get("requirements")), "extraction_method": "recruitee_api"})
            elif provider == "workable":
                for it in data.get("jobs") or []:
                    loc = ", ".join(filter(None, [it.get("city") or "", it.get("country") or ""]))
                    jobs.append({"job_title": it.get("title") or "", "job_url": it.get("url") or "", "location_hint": loc, "card_context": " | ".join(filter(None, [it.get("department"), it.get("employment_type")])), "jd_text": _jd_plain(it.get("description")), "extraction_method": "workable_api"})
            return jobs, f"{provider} API: {len(jobs)}"
        except Exception as exc:
            return [], f"{provider} API failed: {type(exc).__name__}: {exc}"

    def extract_visible_jobs(self, target):
        all_results = []
        seen = set()
        extractors = [
            self._extract_json_ld,
            self._extract_next_data_jobs,
            self._extract_semantic_cards,
            self._extract_anchor_sweep,
        ]
        for extractor in extractors:
            try:
                rows = extractor(target)
                for j in rows:
                    title = (j.get("job_title") or "").strip()
                    url = (j.get("job_url") or "").strip()
                    loc = (j.get("location_hint") or "").strip()
                    key = f"{title.lower()}|{url.lower()}|{loc.lower()}"
                    if title and url and key not in seen:
                        seen.add(key)
                        all_results.append(j)
            except Exception as exc:
                print(f"      extractor {extractor.__name__} failed: {type(exc).__name__}: {exc}")
        # FIX P0-6: layout robustness. The old gate (< 3) meant a page that
        # yielded 3 junk/partial rows never ran the fallback extractors, so
        # unusual layouts silently returned a near-empty result. Run them
        # whenever the yield looks implausibly low for a real job board; the
        # key-based dedupe above makes the extra pass free when it adds nothing.
        if len(all_results) < 10:
            for extractor in [self._extract_url_pattern_clusters, self._extract_click_cards]:
                try:
                    rows = extractor(target)
                    for j in rows:
                        title = (j.get("job_title") or "").strip()
                        url = (j.get("job_url") or "").strip()
                        loc = (j.get("location_hint") or "").strip()
                        key = f"{title.lower()}|{url.lower()}|{loc.lower()}"
                        if title and url and key not in seen:
                            seen.add(key)
                            all_results.append(j)
                except Exception as exc:
                    print(f"      fallback extractor {extractor.__name__} failed: {type(exc).__name__}: {exc}")
        return all_results
    def _extract_json_ld(self, target):
        js = r"""
        () => {
            const jobs = [];
            const scripts = document.querySelectorAll('script[type="application/ld+json"]');
            const addItem = (item) => {
                if (!item || typeof item !== 'object') return;
                const type = item['@type'];
                const isJob =
                    type === 'JobPosting' ||
                    (Array.isArray(type) && type.includes('JobPosting'));
                if (!isJob) return;
                let locStr = '';
                const loc = item.jobLocation;
                if (Array.isArray(loc)) {
                    locStr = loc.map(l => {
                        const a = l.address || {};
                        return [a.addressLocality, a.addressRegion, a.addressCountry].filter(Boolean).join(', ');
                    }).filter(Boolean).join(' | ');
                } else if (loc && loc.address) {
                    const a = loc.address;
                    locStr = [a.addressLocality, a.addressRegion, a.addressCountry].filter(Boolean).join(', ');
                }
                const rawUrl = item.url || item['@id'] || window.location.href;
                let fullUrl = rawUrl;
                try { fullUrl = new URL(rawUrl, window.location.href).href; } catch(e) {}
                jobs.push({
                    job_title: item.title || item.name || '',
                    job_url: fullUrl,
                    card_context: String(item.description || '').substring(0, 800),
                    location_hint: locStr
                });
            };
            scripts.forEach(s => {
                try {
                    const data = JSON.parse(s.textContent);
                    const items = Array.isArray(data) ? data : (data['@graph'] || [data]);
                    items.forEach(addItem);
                } catch(e) {}
            });
            return jobs;
        }
        """
        return target.evaluate(js) or []
    def _extract_next_data_jobs(self, target):
        js = r"""
        () => {
            const jobs = [];
            const seen = new Set();
            const roleRe = /\b(engineer|developer|manager|analyst|scientist|specialist|consultant|architect|designer|director|lead|head|principal|senior|junior|intern|trainee|associate|advisor|officer|administrator|recruiter|counsel|lawyer|accountant|controller|planner|coordinator|assistant|representative|agent|technician|mechanic|operator|expert|owner|scrum master|product owner|sales|marketing|finance|security|devops|frontend|backend|full stack|fullstack|software|data|qa|quality)\b/i;
            
            // FIXED: Upgraded with a helper function to resolve complex dynamic locations and prevent [object Object] serializations
            const getLocString = (loc) => {
                if (!loc) return '';
                if (typeof loc === 'string') return loc;
                if (typeof loc === 'object') {
                    const parts = [
                        loc.name, loc.city, loc.office, loc.country, loc.region, loc.locationName,
                        loc.addressLocality, loc.addressCountry, loc.addressRegion
                    ].filter(Boolean);
                    if (parts.length > 0) return parts.join(', ');
                    return JSON.stringify(loc);
                }
                return String(loc);
            };
            const add = (title, url, ctx, loc) => {
                title = String(title || '').trim();
                url = String(url || '').trim();
                if (!title || title.length < 4 || title.length > 150) return;
                if (!roleRe.test(title)) return;
                if (!url) {
                    const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').substring(0, 70);
                    url = window.location.href.split('?')[0].split('#')[0] + '#job=' + slug;
                } else {
                    try { url = new URL(url, window.location.href).href; } catch(e) {}
                }
                const key = (title + '|' + url).toLowerCase();
                if (seen.has(key)) return;
                seen.add(key);
                jobs.push({
                    job_title: title,
                    job_url: url,
                    card_context: String(ctx || '').substring(0, 800),
                    location_hint: getLocString(loc)
                });
            };
            const scan = (obj, depth = 0) => {
                if (!obj || depth > 8) return;
                if (Array.isArray(obj)) {
                    obj.forEach(x => scan(x, depth + 1));
                    return;
                }
                if (typeof obj !== 'object') return;
                const keys = Object.keys(obj);
                const keyBlob = keys.join(' ').toLowerCase();
                const title =
                    obj.title || obj.jobTitle || obj.positionTitle ||
                    obj.name || obj.label || obj.displayName;
                const url =
                    obj.url || obj.jobUrl || obj.applyUrl || obj.absoluteUrl ||
                    obj.externalUrl || obj.externalPath || obj.path || obj.link;
                const loc =
                    obj.location || obj.locationName || obj.city ||
                    obj.office || obj.country || obj.region;
                const id =
                    obj.jobId || obj.jobID || obj.requisitionId ||
                    obj.reqId || obj.postingId || obj.id;
                if (title && (url || id) && /(job|posting|position|requisition|vacancy|opening|role)/.test(keyBlob)) {
                    add(title, url, JSON.stringify(obj).slice(0, 800), loc);
                }
                for (const k of keys) {
                    const v = obj[k];
                    if (v && typeof v === 'object') scan(v, depth + 1);
                }
            };
            const scripts = document.querySelectorAll(
                'script#__NEXT_DATA__, script[type="application/json"], script[id*="__NEXT_DATA__"]'
            );
            scripts.forEach(s => {
                const txt = s.textContent || '';
                if (!txt || txt.length > 8000000) return;
                try {
                    const data = JSON.parse(txt);
                    scan(data);
                } catch(e) {}
            });
            return jobs;
        }
        """
        return target.evaluate(js) or []
    def _extract_semantic_cards(self, target):
        js = r"""
        () => {
        } """ + JS_HELPERS + r"""
            const jobs = [];
            const seen = new Set();
            const cardSelectors = [
                '[data-job-id]', '[data-jobid]', '[data-job]',
                '[data-position-id]', '[data-posting-id]', '[data-requisition-id]',
                '[data-automation*="job" i]',
                '[data-testid*="job-card" i]', '[data-testid*="job-item" i]',
                '[data-testid*="posting" i]', '[data-qa*="job" i]',
                'ef-jobs-list-item',
                '.job-card', '.job-item', '.job-listing', '.job-row',
                '.job-result', '.job-post', '.job-tile', '.job-entry',
                '.position-card', '.position-item', '.position-listing',
                '.career-card', '.career-item', '.opening-card',
                '.vacancy-card', '.vacancy-item', '.role-card',
                '.posting', '.posting-item',
                '[class*="job-card" i]', '[class*="job-item" i]',
                '[class*="job-listing" i]', '[class*="job-row" i]',
                '[class*="position-card" i]', '[class*="position-item" i]',
                '[class*="vacancy-card" i]', '[class*="opening" i]',
                '[class*="posting" i]',
                'article[class*="job" i]', 'article[class*="position" i]',
                'li[class*="job" i]', 'li[class*="position" i]',
                'li[class*="posting" i]', 'tr[class*="job" i]',
                'tr[class*="position" i]',
                '[role="link"]', '[role="button"]', '[data-item="true"]', '[data-info]'
            ];
            const cards = [];
            for (const sel of cardSelectors) {
                for (const c of querySelectorAllDeep(sel)) cards.push(c);
            }
            cards.forEach(card => {
                if (!card || !isVisible(card) || isBadScope(card)) return;
                const fullText = cleanText(card.innerText || card.textContent || '');
                if (fullText.length < 8 || fullText.length > 3000) return;
                const linkCount = querySelectorAllDeep('a[href]', card).length;
                if (linkCount > 20) return;
                const attrs = cleanText([
                    getClassName(card),
                    card.id || '',
                    card.getAttribute?.('data-job-id') || '',
                    card.getAttribute?.('data-jobid') || '',
                    card.getAttribute?.('data-posting-id') || '',
                    card.getAttribute?.('data-requisition-id') || '',
                    card.getAttribute?.('data-testid') || '',
                    card.getAttribute?.('data-qa') || '',
                    card.getAttribute?.('data-automation') || ''
                ].join(' '));
                const hasJobAttr = /(job|position|posting|opening|vacancy|role|requisition)/i.test(attrs);
                let jobUrl = pickJobUrl(card);
                const title = titleFromScope(card);
                const loc = locationFromScope(card);
                if (!title) return;
                if (!jobUrl && roleWordRe.test(title)) {
                    const hasContextSignal = loc || /(full[- ]?time|part[- ]?time|intern|remote|hybrid|onsite|on-site)/i.test(fullText);
                    if (hasContextSignal) jobUrl = synthJobUrl(title, loc);
                }
                if (!jobUrl) return;
                const key = (title + '|' + jobUrl + '|' + loc).toLowerCase();
                if (seen.has(key)) return;
                seen.add(key);
                jobs.push({
                    job_title: title,
                    job_url: jobUrl,
                    card_context: fullText.substring(0, 800),
                    location_hint: loc
                });
            });
            return jobs;
        }
        """
        js_fixed = js.replace("() => {\n        } ", "() => { ")
        return target.evaluate(js_fixed) or []
    def _extract_anchor_sweep(self, target):
        js = r"""
        () => {
        } """ + JS_HELPERS + r"""
            const jobs = [];
            const seen = new Set();
            for (const a of querySelectorAllDeep('a[href]')) {
                if (!a || !isVisible(a) || isBadScope(a)) continue;
                const href = a.href;
                if (!looksJobUrl(href)) continue;
                const scope = scopeForAnchor(a);
                if (!scope || isBadScope(scope)) continue;
                const scopeText = cleanText(scope.innerText || scope.textContent || '');
                if (scopeText.length > 3000) continue;
                const linkCount = querySelectorAllDeep('a[href]', scope).length;
                if (linkCount > 20) continue;
                let title = firstGoodLine(a.innerText || a.textContent || '');
                if (!title || genericTextRe.test(title) || uiTextRe.test(title)) {
                    title = titleFromScope(scope);
                }
                if (!title) continue;
                const loc = locationFromScope(scope);
                const key = (title + '|' + href + '|' + loc).toLowerCase();
                if (seen.has(key)) continue;
                seen.add(key);
                jobs.push({
                    job_title: title,
                    job_url: href,
                    card_context: scopeText.substring(0, 800),
                    location_hint: loc
                });
            }
            return jobs;
        }
        """
        js_fixed = js.replace("() => {\n        } ", "() => { ")
        return target.evaluate(js_fixed) or []
    def _extract_url_pattern_clusters(self, target):
        js = r"""
        () => {
        } """ + JS_HELPERS + r"""
            const groups = {};
            const jobs = [];
            const seen = new Set();
            for (const a of querySelectorAllDeep('a[href]')) {
                if (!a || !isVisible(a) || isBadScope(a)) continue;
                if (!looksJobUrl(a.href)) continue;
                try {
                    const u = new URL(a.href);
                    const seg = u.pathname.split('/').filter(Boolean);
                    if (seg.length < 2) continue;
                    const pattern = u.host + '/' + seg.slice(0, -1).join('/');
                    if (!groups[pattern]) groups[pattern] = [];
                    groups[pattern].push(a);
                } catch(e) {}
            }
            for (const [pattern, anchors] of Object.entries(groups)) {
                if (anchors.length < 2) continue;
                if (!jobUrlRe.test(pattern) && !anchors.some(a => jobUrlRe.test(a.href))) continue;
                anchors.forEach(a => {
                    const scope = scopeForAnchor(a);
                    if (!scope || isBadScope(scope)) return;
                    const title = firstGoodLine(a.innerText || '') || titleFromScope(scope);
                    if (!title || genericTextRe.test(title) || uiTextRe.test(title)) return;
                    const loc = locationFromScope(scope);
                    const ctx = cleanText(scope.innerText || '');
                    const key = (title + '|' + a.href + '|' + loc).toLowerCase();
                    if (seen.has(key)) return;
                    seen.add(key);
                    jobs.push({
                        job_title: title,
                        job_url: a.href,
                        card_context: ctx.substring(0, 800),
                        location_hint: loc
                    });
                });
            }
            return jobs;
        }
        """
        js_fixed = js.replace("() => {\n        } ", "() => { ")
        return target.evaluate(js_fixed) or []
    def _extract_click_cards(self, target):
        js = r"""
        () => {
        } """ + JS_HELPERS + r"""
            const jobs = [];
            const seen = new Set();
            const candidates = querySelectorAllDeep([
                '[data-job-id]', '[data-jobid]', '[data-posting-id]',
                '[data-requisition-id]', '[data-testid*="job" i]',
                '[class*="job-card" i]', '[class*="job-item" i]',
                '[class*="position-card" i]', '[class*="posting" i]',
                '[class*="opening" i]', '[class*="vacancy" i]',
                'li[role="listitem"]',
                'article',
                'tr',
                '[role="link"]', '[role="button"]', '[data-item="true"]', '[data-info]'
            ].join(','));
            candidates.forEach(card => {
                if (!card || !isVisible(card) || isBadScope(card)) return;
                const text = cleanText(card.innerText || card.textContent || '');
                if (text.length < 15 || text.length > 2500) return;
                const title = titleFromScope(card);
                if (!title) return;
                if (!roleWordRe.test(title)) return;
                if (genericTextRe.test(title) || uiTextRe.test(title)) return;
                const loc = locationFromScope(card);
                const contextSignal =
                    loc ||
                    /(full[- ]?time|part[- ]?time|intern|remote|hybrid|onsite|on-site|department|team|office)/i.test(text);
                if (!contextSignal) return;
                let jobUrl = pickJobUrl(card);
                if (!jobUrl) jobUrl = synthJobUrl(title, loc);
                const key = (title + '|' + jobUrl + '|' + loc).toLowerCase();
                if (seen.has(key)) return;
                seen.add(key);
                jobs.push({
                    job_title: title,
                    job_url: jobUrl,
                    card_context: text.substring(0, 800),
                    location_hint: loc
                });
            });
            return jobs;
        }
        """
        js_fixed = js.replace("() => {\n        } ", "() => { ")
        return target.evaluate(js_fixed) or []
    def wait_for_dom_quiet(self, page, target, timeout_ms=5000):
        try:
            prev = target.evaluate("""() => {
                return [
                    document.querySelectorAll('a,button,li,tr,article').length,
                    (document.body?.innerText || '').length
                ].join('|');
            }""")
            deadline = time.time() + timeout_ms / 1000
            stable_rounds = 0
            while time.time() < deadline:
                page.wait_for_timeout(500)
                curr = target.evaluate("""() => {
                    return [
                        document.querySelectorAll('a,button,li,tr,article').length,
                        (document.body?.innerText || '').length
                    ].join('|');
                }""")
                if curr == prev:
                    stable_rounds += 1
                    if stable_rounds >= 2:
                        return
                else:
                    stable_rounds = 0
                    prev = curr
        except Exception:
            pass
    def progressive_scroll_and_wait(self, page, target):
        try:
            prev_height = target.evaluate("document.body.scrollHeight")
            for step in [0.25, 0.5, 0.75, 1.0]:
                target.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {step})")
                page.wait_for_timeout(600)
            page.wait_for_timeout(1000)
            new_height = target.evaluate("document.body.scrollHeight")
            return new_height > prev_height
        except Exception:
            return False
    def aggressive_infinite_scroll(self, page, target):
        unchanged = 0
        last_height = 0
        for i in range(self.config.MAX_INFINITE_SCROLL):
            try:
                height = target.evaluate("document.body.scrollHeight")
                target.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1200)
                new_height = target.evaluate("document.body.scrollHeight")
                if new_height == height == last_height:
                    unchanged += 1
                    if unchanged >= 2:
                        return i + 1
                else:
                    unchanged = 0
                last_height = height
            except Exception:
                return i
        return self.config.MAX_INFINITE_SCROLL
    def click_load_more_repeatedly(self, page, target):
        clicks = 0
        for _ in range(self.config.MAX_LOAD_MORE_CLICKS):
            js = r"""
            () => {
            } """ + JS_HELPERS + r"""
                const patterns = [
                    /load more/i, /show more/i, /see more/i, /view more/i,
                    /more results/i, /more jobs/i, /more positions/i,
                    /carica altro/i, /mehr laden/i, /meer laden/i,
                    // FIX P0-5: Italian/ES/FR load-more phrasings. 23% of the
                    // seed is Italy and only "carica altro" was covered.
                    /carica altri/i, /carica ancora/i, /mostra altri/i,
                    /mostra di pi[ùu]/i, /vedi altri/i, /altre offerte/i,
                    /altri risultati/i, /altre posizioni/i, /visualizza altri/i,
                    /weitere laden/i, /mehr anzeigen/i, /ver m[áa]s/i,
                    /cargar m[áa]s/i, /voir plus/i, /afficher plus/i
                ];
                for (const el of querySelectorAllDeep('button, a')) {
                    if (!isVisible(el) || isBadScope(el)) continue;
                    const txt = cleanText(el.innerText || el.textContent || '');
                    const disabled =
                        el.disabled === true ||
                        el.getAttribute('aria-disabled') === 'true' ||
                        el.classList.contains('disabled');
                    if (disabled) continue;
                    if (patterns.some(p => p.test(txt))) {
                        // v7: require nearby job-list evidence; do not expand generic
                        // marketing, biography, FAQ or article "show more" controls.
                        let scope = el.parentElement;
                        let jobEvidence = /more jobs|more positions|more results/i.test(txt);
                        for (let depth = 0; scope && depth < 5 && !jobEvidence; depth++, scope = scope.parentElement) {
                            const attrs = [getClassName(scope), scope.id || '',
                                           scope.getAttribute?.('data-testid') || ''].join(' ');
                            const links = querySelectorAllDeep('a[href]', scope)
                                .filter(a => looksJobUrl(a.href)).length;
                            if (/job|vacanc|position|opening|career/i.test(attrs) || links >= 2) {
                                jobEvidence = true;
                            }
                        }
                        if (!jobEvidence) continue;
                        el.scrollIntoView({behavior: 'instant', block: 'center'});
                        el.click();
                        return true;
                    }
                }
                return false;
            }
            """
            js_fixed = js.replace("() => {\n            } ", "() => { ")
            try:
                clicked = target.evaluate(js_fixed)
                if not clicked:
                    break
                clicks += 1
                page.wait_for_timeout(2500)
                self.wait_for_dom_quiet(page, target, timeout_ms=4000)
            except Exception:
                break
        return clicks
    def try_increase_page_size(self, page, target):
        js = r"""
        () => {
            const selects = document.querySelectorAll('select');
            for (const sel of selects) {
                const label = [
                    sel.getAttribute('aria-label') || '',
                    sel.getAttribute('name') || '',
                    sel.id || '',
                    sel.parentElement?.innerText || ''
                ].join(' ').toLowerCase();
                if (!/(items?\s*per\s*page|per\s*page|page\s*size|show)/i.test(label)) {
                    continue;
                }
                let best = null;
                let bestNum = 0;
                for (const opt of sel.options) {
                    const n = parseInt(opt.value || opt.textContent, 10);
                    if (!isNaN(n) && n > bestNum) {
                        bestNum = n;
                        best = opt;
                    }
                }
                if (best && String(sel.value) !== String(best.value)) {
                    sel.value = best.value;
                    sel.dispatchEvent(new Event('input', {bubbles: true}));
                    sel.dispatchEvent(new Event('change', {bubbles: true}));
                    return bestNum;
                }
            }
            return 0;
        }
        """
        try:
            val = target.evaluate(js)
            if val:
                page.wait_for_timeout(2500)
                self.wait_for_dom_quiet(page, target, timeout_ms=4000)
                return val
        except Exception:
            pass
        return 0
    # FIXED: Added a robust global Next Button Fallback.
    # If standard, structured pagination blocks don't exist, we scan the whole page for visible standalone 
    # button/link elements with 'Next', REL='next', or localized equivalent text, and click them!
    def execute_advanced_pagination(self, page, target, current_page_num, seed_query_params=None):
        # FIXED: keep the seed page's filter params (e.g. IKEA orgIds=22908) when
        # following pagination links, so the regional scope isn't lost mid-crawl.
        seed_query_params = seed_query_params or []
        js = r"""
        (targetPage) => {
        } """ + JS_HELPERS + r"""
            const isDisabled = (el) => {
                if (!el) return true;
                return el.disabled === true ||
                       el.getAttribute('aria-disabled') === 'true' ||
                       el.classList.contains('disabled') ||
                       el.classList.contains('mat-button-disabled');
            };
            
            const SEED = __SEED_PARAMS__;
            const mergeSeed = (href) => {
                try {
                    const u = new URL(href, window.location.href);
                    if (u.origin !== window.location.origin) return href;
                    const have = new Set(u.searchParams.keys());
                    for (const pair of SEED) {
                        if (!have.has(pair[0])) u.searchParams.append(pair[0], pair[1]);
                    }
                    return u.href;
                } catch(e) { return href; }
            };
            const clickEl = (el) => {
                if (el && el.tagName === 'A' && el.href) {
                    el.href = mergeSeed(el.href);
                }
                el.scrollIntoView({behavior: 'instant', block: 'center'});
                el.click();
            };
            const nextPatterns = [
                /next/i, /weiter/i, /suivant/i, /siguiente/i,
                /successivo/i, /volgende/i, /›/, /»/, /►/,
                // FIX P0-5: Italian boards label the control "Successiva"
                // (pagina = feminine) or "Avanti" far more often than
                // "successivo". Without these the crawl stops at page 1.
                /successiv[ao]/i, /avanti/i, /prossim[ao]/i, /pagina seguente/i,
                /seguente/i, /pi[ùu] recenti/i, /nächste/i, /naechste/i,
                /p[áa]gina siguiente/i, /page suivante/i
            ];
            // 1. Structured pagination scopes (including paginators like mat-paginator)
            const paginationScopes = querySelectorAllDeep([
                'nav[aria-label*="pagination" i]',
                'nav[class*="pagination" i]',
                'nav[aria-label*="paginator" i]',
                'nav[class*="paginator" i]',
                '[class*="pagination" i]',
                '[class*="paginator" i]',
                'mat-paginator',
                '[data-testid*="pagination" i]',
                '[data-testid*="paginator" i]',
                '[role="navigation"]'
            ].join(','));
            
            for (const nav of paginationScopes) {
                if (!isVisible(nav)) continue;
                for (const el of querySelectorAllDeep('a, button, li, span[tabindex]', nav)) {
                    const txt = cleanText(el.innerText || el.textContent || '');
                    if (txt === String(targetPage) && isVisible(el) && !isDisabled(el)) {
                        const clickable = querySelectorAllDeep('a, button', el)[0] || el;
                        clickEl(clickable);
                        return 'page_number_structured';
                    }
                }
            }
            
            for (const nav of paginationScopes) {
                if (!isVisible(nav)) continue;
                for (const el of querySelectorAllDeep('a, button', nav)) {
                    if (!isVisible(el) || isDisabled(el)) continue;
                    const blob = [
                        el.innerText || '',
                        el.getAttribute('aria-label') || '',
                        el.getAttribute('title') || '',
                        getClassName(el),
                        el.getAttribute('rel') || ''
                    ].join(' ');
                    if (nextPatterns.some(p => p.test(blob)) || /rel="?next"?/i.test(blob)) {
                        clickEl(el);
                        return 'next_structured';
                    }
                }
            }
            
            // 2. Global standalone page number buttons (for class-less pagination widgets like Bolt)
            for (const el of querySelectorAllDeep('a, button, [role="button"]')) {
                if (!isVisible(el) || isDisabled(el)) continue;
                const txt = cleanText(el.innerText || el.textContent || '');
                if (txt === String(targetPage)) {
                    // Bubble up parents to verify sibling page digits exist (ensuring it is a real paginator button)
                    let gp = el.parentElement;
                    while (gp && gp.tagName !== 'BODY') {
                        const txtGP = cleanText(gp.innerText || '');
                        const otherPage = String(targetPage === 2 ? 1 : targetPage - 1);
                        if (txtGP.includes(otherPage) && txtGP.length < 500) {
                            clickEl(el);
                            return 'global_page_number';
                        }
                        gp = gp.parentElement;
                    }
                }
            }
            // 3. Strict fallback: never click a generic carousel/content "Next".
            // Require paginator ancestry, sibling page numbers, or a page-shaped URL.
            for (const el of querySelectorAllDeep('a, button')) {
                if (!isVisible(el) || isDisabled(el)) continue;
                const blob = [
                    el.innerText || '', el.getAttribute('aria-label') || '',
                    el.getAttribute('title') || '', getClassName(el),
                    el.getAttribute('rel') || ''
                ].join(' ');
                if (!(nextPatterns.some(p => p.test(blob)) || /rel="?next"?/i.test(blob))) continue;
                let paginatorEvidence = false;
                let gp = el.parentElement;
                for (let depth = 0; gp && depth < 5; depth++, gp = gp.parentElement) {
                    const attrs = [getClassName(gp), gp.id || '',
                                   gp.getAttribute?.('aria-label') || '',
                                   gp.getAttribute?.('data-testid') || ''].join(' ');
                    const gpText = cleanText(gp.innerText || '');
                    if (/paginat|paginator|page-nav/i.test(attrs) ||
                        (/\b1\b/.test(gpText) && /\b2\b/.test(gpText) && gpText.length < 500)) {
                        paginatorEvidence = true;
                        break;
                    }
                }
                if (!paginatorEvidence && el.tagName === 'A' && el.href) {
                    try {
                        const u = new URL(el.href, window.location.href);
                        paginatorEvidence = /[?&](page|p|offset|start)=\d+/i.test(u.search) ||
                                            /\/page\/\d+\/?$/i.test(u.pathname);
                    } catch(e) {}
                }
                if (paginatorEvidence) {
                    clickEl(el);
                    return 'next_strict_fallback';
                }
            }
            return null;
        }
        """
        js_fixed = js.replace("(targetPage) => {\n        } ", "(targetPage) => { ")
        try:
            import json as _json
            js_fixed = js_fixed.replace("__SEED_PARAMS__", _json.dumps(seed_query_params))
            result = target.evaluate(js_fixed, str(current_page_num + 1))
            if result:
                page.wait_for_timeout(self.config.PAGINATION_WAIT_MS)
                self.wait_for_dom_quiet(page, target, timeout_ms=self.config.DOM_QUIET_MS)
                return True
        except Exception:
            pass
        return False
    # FIXED: Added a dynamic waiter that waits for job elements to be attached/rendered 
    # before checking landing pages or extracting to prevent premature extraction on blank loading states.
    def wait_for_job_cards_to_load(self, page, timeout_ms=5000):
        selectors = [
            ".job-card", ".job-item", ".job-listing", ".job-row",
            "a[href*='/job/']", "a[href*='/jobs/']", "a[href*='/o/']", "a[href*='/role/']",
            "[data-testid*='job']", "[class*='job-card']", "[class*='job-item']"
        ]
        combined_sel = ", ".join(selectors)
        try:
            page.wait_for_selector(combined_sel, state="attached", timeout=timeout_ms)
            page.wait_for_timeout(1000) # extra buffer for rendering
            return True
        except Exception:
            return False
    
    def _dedupe_records(self, company_jobs):
        """Canonical requisition dedupe, preferring evidence-rich real records."""
        out = {}
        for key, rec in company_jobs.items():
            cid = rec.get("Canonical Job ID") or self.canonical_job_id(
                rec.get("Company Name", ""), rec.get("Job URL", ""),
                rec.get("Job Title", ""), rec.get("Job Location", ""),
                rec.get("Provider", "auto"),
            )
            prev = out.get(cid)
            if prev is None or self._record_quality(rec) > self._record_quality(prev):
                out[cid] = rec
        return out

    def _detail_enrich_one(self, page, url, company_jobs) -> str:
        """Enrich one row from its detail page.
        Returns "ok" / "network_fail" / "other_fail" / "skipped" so the
        caller can run the host circuit-breaker and one retry pass."""
        try:
            page.goto(url,wait_until="domcontentloaded",timeout=self.config.DETAIL_SCAN_TIMEOUT_MS)
            page.wait_for_timeout(500)
            data=page.evaluate("""() => {
                const out={desc:'',type:'',loc:'',remote:false};
                for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
                    try {
                        const d=JSON.parse(s.textContent);
                        const items=Array.isArray(d)?d:(d['@graph']||[d]);
                        for (const it of items) {
                            const t=it['@type'];
                            if (t==='JobPosting'||(Array.isArray(t)&&t.includes('JobPosting'))) {
                                out.desc=String(it.description||'').slice(0,12000);
                                out.type=Array.isArray(it.employmentType)?it.employmentType.join(','):String(it.employmentType||'');
                                const locs=Array.isArray(it.jobLocation)?it.jobLocation:[it.jobLocation];
                                out.loc=locs.filter(Boolean).map(l=>{
                                    const a=l.address||{};
                                    let c=a.addressCountry||'';
                                    if (c&&typeof c==='object') c=c.name||'';
                                    return [a.addressLocality,a.addressRegion,c].filter(Boolean).join(', ');
                                }).filter(Boolean).join(' | ');
                                out.remote=String(it.jobLocationType||'').toUpperCase().includes('TELECOMMUTE');
                                return out;
                            }
                        }
                    } catch(e) {}
                }
                out.desc=(document.body?document.body.innerText:'').slice(0,12000);
                return out;
            }""") or {}
        except Exception as exc:
            print(f"      detail failed {url}: {type(exc).__name__}: {exc}")
            return "network_fail" if self._is_network_error(exc) else "other_fail"
        desc=(data.get("desc") or "").strip()
        rec=next((r for r in company_jobs.values() if r.get("Job URL","").casefold()==url.casefold()),None)
        if rec is None or not desc:
            return "skipped"
        sup=self.detector.detect(desc)
        visa=sup["visa"]["verdict"]
        reloc=sup["relocation"]["verdict"]
        rec["Visa Sponsorship"]=visa
        rec["Relocation Support"]=reloc
        rec["Relocation Required"]="Yes" if sup["relocation"]["required"] else "Unknown"
        if visa==VERDICT_YES or reloc==VERDICT_YES:
            rec["Relocation/Visa Support"]="Y"
        elif visa==VERDICT_NO and reloc==VERDICT_NO:
            rec["Relocation/Visa Support"]="N"
        else:
            rec["Relocation/Visa Support"]="Unknown"
        rec["Support Confidence"]=round(max(sup["visa"]["confidence"],sup["relocation"]["confidence"]),2)
        rec["Support Evidence"]="; ".join(filter(None,[self.detector.best_evidence(sup["visa"]),self.detector.best_evidence(sup["relocation"])]))
        rec["Support Evidence URL"]=url
        rec["Support Evidence Type"]="explicit_detail_sentence" if rec["Support Evidence"] else "none"

        # Blue Card is independent of general visa sponsorship.
        # Shared, evidence-based classifier (sponsorscout.scanning.jd_support).
        blue = detect_blue_card(self.detector, desc)
        blue_ev = ""
        for sent in self.detector.split_sentences(desc):
            if re.search(r"\b(?:eu\s+)?blue[- ]?card|blaue karte|carta blu|blauwe kaart|carte bleue|tarjeta azul\b", sent, re.I):
                blue_ev = sent[:300]
                break
        rec["EU Blue Card"] = blue
        rec["Blue Card Evidence"] = blue_ev

        # FIX P0-30: the full JD is the strongest experience evidence.
        apply_experience_to_record(rec, jd_text=desc,
                                   title=rec.get("Job Title") or "")

        # Employment/work mode: update only when explicit.
        emp=(data.get("type") or "").casefold(); low=desc.casefold()
        workload=""
        if re.search(r"part[- _]?time|teilzeit|deeltijd",emp+" "+low): workload="Part-time"
        elif re.search(r"intern|trainee|praktikum|stagiaire|apprent",emp+" "+low): workload="Internship"
        elif re.search(r"contract|temporary|fixed[- _]?term",emp): workload="Contract"
        elif re.search(r"full[- _]?time|vollzeit|voltijd",emp+" "+low): workload="Full-time"
        mode=""
        if data.get("remote") or re.search(r"\bfully remote|remote[- ]first|100% remote\b",low): mode="Remote"
        elif re.search(r"\bhybrid|ibrido|hybride|smart working\b",low): mode="Hybrid"
        elif re.search(r"\bon[- ]site|in[- ]office\b",low): mode="On-site"
        old_parts=(rec.get("Job Type") or "Unknown / Unknown").split(" / ",1)
        if workload or mode:
            rec["Job Type"]=f"{workload or old_parts[0]} / {mode or (old_parts[1] if len(old_parts)>1 else 'Unknown')}"

        loc=(data.get("loc") or "").strip()
        if loc and rec.get("Job Location") in {"Unknown","Not Specified"}:
            parsed=self.extract_location(loc)
            if parsed!="Not Specified":
                rec["Job Location"]=parsed; rec["Location Source"]="detail"
        return "ok"

    @staticmethod
    def _detail_priority(rec) -> tuple:
        """Sort key sending weak-evidence rows to the detail budget first.

        A row is "strong" on an axis when the listing crawl already produced
        an explicit verdict, supporting evidence, or a usable location.  Only
        the visit order changes — the same per-company / global caps, the same
        evidence rules and the same writers apply, so no row can lose data it
        would otherwise have received.
        """
        weak_location = str(rec.get("Job Location") or "").strip().lower() in (
            "", "unknown", "not specified", "global")
        weak_verdict = str(rec.get("Visa Sponsorship") or "").strip().lower() not in (
            "y", "n", "yes", "no")
        weak_evidence = not str(rec.get("Support Evidence") or "").strip()
        weak_experience = str(rec.get("Experience Required") or "").strip().lower() in ("", "unknown")
        return (0 if weak_location else 1,
                0 if weak_verdict else 1,
                0 if weak_evidence else 1,
                0 if weak_experience else 1)

    def _detail_scan_for_company(self, page, name, company_jobs):
        """Visit real detail pages and enrich only from explicit page evidence."""
        if not self.detail_scan or not company_jobs:
            return
        urls=[]; seen=set()
        ordered=[]
        for rec in company_jobs.values():
            u=rec.get("Job URL", "")
            if u.startswith("http") and "#job=" not in u.lower() and u.casefold() not in seen:
                seen.add(u.casefold()); ordered.append((u, rec))
        # Weak-evidence rows first: the per-company cap is spent where a visit
        # can actually change a verdict/location (stable sort keeps crawl
        # order within equal priority).
        ordered.sort(key=lambda _p: self._detail_priority(_p[1]))
        urls=[u for u, _ in ordered[:self.config.MAX_DETAIL_SCAN_PER_COMPANY]]
        detail_start=time.monotonic(); enriched=0
        host_netfails: dict = {}
        tripped_hosts: set = set()
        failed_network_urls: list = []
        for url_i,url in enumerate(urls,1):
            if check_control(self.cancel_event, self.pause_event):
                print(f"   CANCELLED: stopping detail scan for {name}")
                break
            if time.monotonic()-detail_start > self.config.DETAIL_SCAN_TIME_BUDGET_SEC:
                print(f"   -> {name}: detail budget exhausted at {url_i}/{len(urls)}")
                break
            with self._detail_lock:
                if self._detail_count >= self.config.MAX_DETAIL_SCAN_TOTAL:
                    break
                self._detail_count += 1
            self._last_activity=time.monotonic()
            host = urlparse(url).netloc.lower()
            if host in tripped_hosts:
                continue
            if _is_download_url(url):
                continue
            outcome = self._detail_enrich_one(page, url, company_jobs)
            if outcome == "ok":
                enriched += 1
                host_netfails.pop(host, None)
            elif outcome == "network_fail":
                failed_network_urls.append(url)
                host_netfails[host] = host_netfails.get(host, 0) + 1
                if host_netfails[host] >= 5:
                    tripped_hosts.add(host)
                    print(f"   -> {name}: host {host} unreachable ({host_netfails[host]} network failures) - skipping remaining detail URLs")
        # One retry pass over transient network failures (DNS/timeouts often
        # clear); hosts that already tripped the breaker stay skipped.
        for url in failed_network_urls:
            host = urlparse(url).netloc.lower()
            if host in tripped_hosts:
                continue
            with self._detail_lock:
                if self._detail_count >= self.config.MAX_DETAIL_SCAN_TOTAL:
                    break
                self._detail_count += 1
            if self._detail_enrich_one(page, url, company_jobs) == "ok":
                enriched += 1
        if enriched:
            print(f"   -> {name}: detail-enriched {enriched} rows")

    def _extract_ld_location_from_html(self, html):
        """Parse JobPosting JSON-LD location out of raw HTML (fast, no browser)."""
        for m in re.finditer(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.DOTALL | re.IGNORECASE,
        ):
            try:
                data = json.loads(m.group(1))
            except Exception:
                continue
            items = data if isinstance(data, list) else (
                data.get("@graph") if isinstance(data, dict) else [data])
            if isinstance(items, dict):
                items = [items]
            for it in items or []:
                if not isinstance(it, dict):
                    continue
                t = it.get("@type")
                if t != "JobPosting" and not (
                        isinstance(t, list) and "JobPosting" in t):
                    continue
                loc = it.get("jobLocation")
                if isinstance(loc, list):
                    loc = loc[0] if loc else None
                a = (loc or {}).get("address") or {}
                country = a.get("addressCountry")
                if isinstance(country, dict):
                    country = country.get("name") or ""
                loc_str = ", ".join(filter(None, [
                    a.get("addressLocality"), a.get("addressRegion"), country,
                ])).strip()
                if loc_str:
                    return loc_str
        return ""

    def _detail_location_from_url(self, url, timeout_ms=20000, browser_page=None):
        """Extract location from ONE job detail page.
        1st pass: plain HTTP + JSON-LD parse (fast — no browser)
        2nd pass: an existing caller-owned browser page when supplied;
        otherwise a one-off headless browser (only if needed)."""
        if _is_download_url(url):
            return None
        # ── Fast pass: static HTML JSON-LD ──
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"),
                "Accept": "text/html,application/xhtml+xml,*/*",
            })
            with urllib.request.urlopen(req, timeout=12) as resp:
                html = resp.read().decode("utf-8", "replace")
            ld = self._extract_ld_location_from_html(html)
            if ld:
                parsed = self.extract_location(ld)
                if parsed != "Not Specified":
                    return parsed, "detail"
            # fallback: "Location: X" / meta in static HTML
            m = re.search(
                r'(?i)(?:location|standort|luogo|locatie)\s*[:<]\s*'
                r'([A-Za-zÀ-ÿ][^<,\n]{2,60})', html)
            if m:
                parsed = self.extract_location(m.group(1).strip())
                if parsed != "Not Specified":
                    return parsed, "detail"
        except Exception:
            pass

        # ── Slow pass: headless browser (JS-rendered pages only) ──
        own_browser = None
        try:
            pg = None
            if browser_page is not None:
                pg = browser_page
            else:
                from playwright.sync_api import sync_playwright as _sp
                _pw_ctx = _sp().start()
                try:
                    own_browser = _pw_ctx.chromium.launch(
                        headless=True,
                        args=BROWSER_ARGS
                    )
                    pg = own_browser.new_page()
                    install_page_resource_blocking(pg)  # FIX P0-29
                except Exception:
                    try:
                        _pw_ctx.stop()
                    except Exception:
                        pass
                    own_browser = None
            if pg is None:
                return None
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                pg.wait_for_timeout(2000)
                ld = pg.evaluate(
                    """() => {
                        for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
                            try {
                                const d = JSON.parse(s.textContent);
                                const items = Array.isArray(d) ? d : (d['@graph'] || [d]);
                                for (const it of items) {
                                    const t = it['@type'];
                                    if (t === 'JobPosting' || (Array.isArray(t) && t.includes('JobPosting'))) {
                                        const a = (it.jobLocation && it.jobLocation.address) || {};
                                        const s2 = [a.addressLocality, a.addressRegion, a.addressCountry].filter(Boolean).join(', ');
                                        if (s2) return s2;
                                    }
                                }
                            } catch(e) {}
                        }
                        return '';
                    }"""
                ) or ""
                if ld:
                    parsed = self.extract_location(ld)
                    if parsed != "Not Specified":
                        return parsed, "detail"
            finally:
                try:
                    if pg is not None and pg is not browser_page:
                        pg.close()
                except Exception:
                    pass
                if own_browser is not None:
                    try:
                        own_browser.close()
                    except Exception:
                        pass
                    try:
                        _pw_ctx.stop()
                    except Exception:
                        pass
        except Exception:
            pass
        return None

    def _enrich_ns_locations(self, rows, max_workers=None, max_fetch=None):
        """Enrich rows whose location is 'Not Specified' by visiting the job
        detail page. Returns number of rows enriched (mutates rows in place)."""
        import concurrent.futures as _cf
        # Host-adaptive pool: the old fixed 6 concurrent fetches could saturate
        # a 2-core / 8 GB machine.  None means "size it for this host".
        if not max_workers or max_workers < 1:
            max_workers = recommended_workers("http")
        targets = [r for r in rows if (r.get("Job Location") or "").strip() == "Not Specified"]
        if not targets:
            return 0
        if max_fetch:
            targets = targets[:max_fetch]
        print(f"   Detail-page location enrichment: {len(targets)} rows to check...")

        def fetch(r):
            res = self._detail_location_from_url(r["Job URL"])
            if res:
                r["Job Location"] = res[0]
                r["Location Source"] = res[1]
                return 1
            return 0

        done = 0
        checked = 0
        with _cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
            for ok in ex.map(fetch, targets):
                done += ok
                checked += 1
                if checked % 100 == 0:
                    print(f"      ...{checked}/{len(targets)} checked, {done} recovered", flush=True)
        print(f"   Detail enrichment done: {done}/{len(targets)} locations recovered")
        return done

    def _fetch_jd_text(self, url, timeout_ms=15000):
        """Fetch a job page (fast HTTP) and extract the description text.
        Returns (desc_text, ld_location) — empty strings when unavailable."""
        if _is_download_url(url):
            return "", ""
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"),
                "Accept": "text/html,application/xhtml+xml,*/*",
            })
            with urllib.request.urlopen(req, timeout=12) as resp:
                html = resp.read().decode("utf-8", "replace")
        except Exception:
            return "", ""
        desc = ""
        loc = ""
        # JSON-LD JobPosting: description + location
        for m in re.finditer(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.DOTALL | re.IGNORECASE,
        ):
            try:
                data = json.loads(m.group(1))
            except Exception:
                continue
            items = data if isinstance(data, list) else (
                data.get("@graph") if isinstance(data, dict) else [data])
            if isinstance(items, dict):
                items = [items]
            for it in items or []:
                if not isinstance(it, dict):
                    continue
                t = it.get("@type")
                if t != "JobPosting" and not (
                        isinstance(t, list) and "JobPosting" in t):
                    continue
                d = it.get("description") or ""
                if isinstance(d, list):
                    d = " ".join(str(x) for x in d)
                if isinstance(d, dict):
                    d = d.get("text") or ""
                if str(d).strip() and not desc:
                    desc = re.sub(r"<[^>]+>", " ", str(d))
                    desc = re.sub(r"\s+", " ", desc).strip()[:8000]
                jl = it.get("jobLocation")
                if isinstance(jl, list):
                    jl = jl[0] if jl else {}
                a = (jl or {}).get("address") or {}
                loc = ", ".join(filter(None, [
                    a.get("addressLocality"), a.get("addressRegion"),
                    a.get("addressCountry") if not isinstance(a.get("addressCountry"), dict)
                    else (a.get("addressCountry") or {}).get("name", ""),
                ])).strip()
        if not desc:
            # meta description / og:description fallback
            m = re.search(
                r'<meta[^>]*(?:name|property)=["\'](?:og:)?description["\'][^>]*content=["\']([^"\']{50,1500})["\']',
                html, re.I)
            if m:
                desc = re.sub(r"<[^>]+>", " ", m.group(1))
                desc = re.sub(r"\s+", " ", desc).strip()[:8000]
        return desc, loc

    def _enrich_support_from_detail(self, rows, max_workers=None, max_fetch=None,
                                    use_browser=True, browser_page=None):
        """Run the context-aware support detector on each row's job detail page.
        HTTP fast-path first (bounded parallel fetch); falls back to ONE shared
        headless browser for JS-only pages (Ashby/Greenhouse/etc.) — or to the
        caller-supplied browser page when given. Updates columns in place.
        Returns number of rows with NEW detector evidence."""
        import concurrent.futures as _cf
        import threading as _threading_mod
        if not rows:
            return 0
        targets = rows
        if max_fetch:
            targets = targets[:max_fetch]
        cap = self.config.MAX_DETAIL_SCAN_TOTAL
        # Weak-evidence rows first so the global cap is spent where a visit
        # can actually change a verdict (stable sort keeps original order
        # within equal priority).
        ranked = sorted(enumerate(targets),
                        key=lambda _p: (self._detail_priority(_p[1]), _p[0]))
        if not max_workers or max_workers < 1:
            max_workers = recommended_workers("http")
        done = 0
        changed = 0
        browser = None
        changed_lock = _threading_mod.Lock()

        def _fetch_one(r, url):
            if check_control(self.cancel_event, self.pause_event):
                return "", ""
            if not url.startswith("http") or "#job=" in url.lower():
                return "", ""
            try:
                return self._fetch_jd_text(url)
            except Exception:
                return "", ""

        def _enrich_from_desc(r, desc):
            if not desc:
                return 0
            # FIX P0-30: extract experience BEFORE the visa-evidence gate
            # below. Most JDs state experience but say nothing about visas;
            # running this after the early return would leave the new columns
            # empty for exactly those rows.
            _exp_hit = apply_experience_to_record(
                r, jd_text=desc, title=r.get("Job Title") or "")
            sup = self.detector.detect(desc)
            if not sup["visa"]["evidence"] and not sup["relocation"]["evidence"]:
                return 1 if _exp_hit else 0
            r["Visa Sponsorship"] = sup["visa"]["verdict"]
            r["Relocation Support"] = sup["relocation"]["verdict"]
            r["Relocation Required"] = "Yes" if sup["relocation"]["required"] else "No"
            r["Support Confidence"] = round(max(
                sup["visa"]["confidence"], sup["relocation"]["confidence"]), 2)
            r["Support Evidence"] = "; ".join(filter(None, [
                self.detector.best_evidence(sup["visa"]),
                self.detector.best_evidence(sup["relocation"]),
            ]))
            if sup["visa"]["verdict"] == VERDICT_YES:
                r["Relocation/Visa Support"] = "Y"
            if sup["relocation"]["verdict"] == VERDICT_YES:
                r["Relocation/Visa Support"] = "Y"
            elif sup["relocation"]["verdict"] == VERDICT_NO:
                r["Relocation/Visa Support"] = "N"
            # EU Blue Card uses the shared evidence-based classifier, NOT a
            # blanket "visa verdict Yes => Blue Card Y" shortcut.
            r["EU Blue Card"] = detect_blue_card(self.detector, desc)
            return 1

        def _shared_browser_visit(url):
            """Slow path for one JS-only URL on the single shared browser
            (or the caller-supplied page). Returns body text or ""."""
            nonlocal browser
            if browser_page is not None:
                pg, close_pg = browser_page, False
            else:
                try:
                    from playwright.sync_api import sync_playwright as _sp
                    if browser is None:
                        _pw_ctx = _sp().start()
                        browser = _pw_ctx.chromium.launch(
                            headless=True,
                            args=BROWSER_ARGS
                        )
                        browser._pw_ctx = _pw_ctx
                    pg, close_pg = browser.new_page(), True
                    install_page_resource_blocking(pg)  # FIX P0-29
                except Exception:
                    return ""
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=20000)
                pg.wait_for_timeout(1500)
                return (pg.evaluate(
                    "() => (document.body ? document.body.innerText : '').slice(0, 8000)"
                ) or "")
            except Exception:
                return ""
            finally:
                if close_pg:
                    try:
                        pg.close()
                    except Exception:
                        pass

        try:
            # Pass 1 (parallel): HTTP fast path for every row. Rows whose
            # page yields nothing are remembered for the shared-browser pass,
            # which MUST stay serial (one browser, one page at a time).
            need_browser = []
            with _cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
                futs = {ex.submit(_fetch_one, r, r.get("Job URL") or ""): r
                        for _, r in ranked}
                for fut in _cf.as_completed(futs):
                    r = futs[fut]
                    if check_control(self.cancel_event, self.pause_event):
                        break
                    with self._detail_lock:
                        if self._detail_count >= cap:
                            break
                        self._detail_count += 1
                    done += 1
                    try:
                        res = fut.result()
                    except Exception:
                        res = None
                    if res:
                        desc = res[0]
                        if desc:
                            with changed_lock:
                                changed += _enrich_from_desc(r, desc)
                                if changed % 50 == 0:
                                    print(f"      ...support detection {changed} rows enriched (of {len(targets)})",
                                          flush=True)
                        else:
                            # HTTP fetch failed: defer to the shared browser.
                            if use_browser:
                                need_browser.append(r)
                    elif res is None and use_browser:
                        # Skipped/invalid URL marker: still a browser candidate
                        # only when it is a real http(s) job page.
                        url = r.get("Job URL") or ""
                        if url.startswith("http") and "#job=" not in url.lower():
                            need_browser.append(r)
            # Pass 2 (serial, shared browser): only rows HTTP could not read.
            for r in need_browser:
                if check_control(self.cancel_event, self.pause_event):
                    break
                with self._detail_lock:
                    if self._detail_count >= cap:
                        break
                    self._detail_count += 1
                desc = _shared_browser_visit(r.get("Job URL") or "") if use_browser else ""
                with changed_lock:
                    changed += _enrich_from_desc(r, desc)
                    if changed and changed % 50 == 0:
                        print(f"      ...support detection {changed} rows enriched (of {len(targets)})",
                              flush=True)
        finally:
            try:
                if browser is not None:
                    try:
                        bp = getattr(browser, "process", None)
                        if bp is not None:
                            bp.kill()
                    except Exception:
                        pass
                    try:
                        browser.close()
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                # _pw_ctx exists only when WE launched the shared browser
                # (a caller-supplied page brings its own lifecycle).
                if browser is not None and hasattr(browser, "_pw_ctx"):
                    browser._pw_ctx.stop()
            except Exception:
                pass
        print(f"   Support detection: {changed} rows enriched from detail pages")
        # FIX P0-30b: rows whose experience is still unknown but that have a
        # real job URL get a bounded second pass through the SAME fetchers,
        # inside the SAME global cap accounting. No new threads, no new budget.
        missing = [r for r in targets if str(r.get("Experience Required") or "").strip().lower() in ("", "unknown") and str(r.get("Job URL") or "").startswith("http")]
        missing = missing[:100]
        for r in missing:
            if check_control(self.cancel_event, self.pause_event):
                break
            with self._detail_lock:
                if self._detail_count >= cap:
                    break
                self._detail_count += 1
            try:
                desc, _loc = self._fetch_jd_text(r.get("Job URL") or "")
            except Exception:
                continue
            if desc:
                apply_experience_to_record(r, jd_text=desc, title=r.get("Job Title") or "")
                changed += 1
        print(f"   Experience backfill: {len(missing)} rows revisited")
        return changed

    def reprocess_csv(self, input_csv=None, output_csv=None, detail=False):
        raise RuntimeError(
            "The unsafe v6-style reprocessor is disabled in v7. "
            "Use repair_scraped_jobs_v7.py, which preserves all rows across direct/recruiter/quarantine outputs."
        )
        """Deprecated unreachable v6 implementation retained below for migration reference only.
        Applies title cleaning, location re-parsing, HQ policy, and dedupe.
        Usage:  python job_scanner.py --reprocess --input scraped_jobs.csv
                python job_scanner.py --reprocess --input x.csv --detail   # + visit NS job pages
        """
        input_csv = input_csv or self.input_csv
        if not os.path.exists(input_csv):
            raise FileNotFoundError(f"Input CSV '{input_csv}' not found.")
        if not output_csv:
            output_csv = input_csv.replace(".csv", "_cleaned.csv")

        columns = [
            "Company Name", "Industry Type", "Job Title", "Job Location",
            "Job Type", "Job URL", "EU Blue Card", "Relocation/Visa Support",
            "Location Source", "URL Type",
            "Visa Sponsorship", "Relocation Support", "Relocation Required",
            "Support Confidence", "Support Evidence",
        ]
        with open(input_csv, newline="", encoding="utf-8-sig") as f:
            first_line = f.readline()
            f.seek(0)
            # FIXED: auto-detect headerless CSVs (first data row used as header)
            has_header = "Company Name" in first_line and "Job Title" in first_line
            if has_header:
                rows = list(csv.DictReader(f))
            else:
                reader = csv.reader(f)
                data = list(reader)
                if not data:
                    rows = []
                else:
                    rows = [
                        dict(zip(columns, row + [""] * (len(columns) - len(row))))
                        for row in data
                    ]
        hdr_flag = "yes" if has_header else "NO (auto-detected)"
        print(f"Reprocessing {len(rows)} rows from '{input_csv}' (header={hdr_flag})")

        brand_words = ("lenscrafters", "sunglass hut", "target optical",
                       "oakley", "opsm", "for eyes", "ray-ban")
        stats = {
            "title_changed": 0, "title_rejected": 0, "location_changed": 0,
            "global_fixed": 0, "uk_fixed": 0, "jobdetail_fixed": 0,
            "brand_fixed": 0, "dropped_dup": 0, "kept": 0,
        }
        out_rows = []
        seen_tl = set()

        for r in rows:
            name = (r.get("Company Name") or "").strip()
            industry = (r.get("Industry Type") or "Unknown").strip()
            old_title = (r.get("Job Title") or "").strip()
            old_loc = (r.get("Job Location") or "").strip()
            url = (r.get("Job URL") or "").strip()
            job_type = (r.get("Job Type") or "").strip()
            blue = (r.get("EU Blue Card") or "").strip()
            visa = (r.get("Relocation/Visa Support") or "").strip()

            new_title = self.clean_job_title(old_title)
            if not new_title or not self.is_valid_job_title(new_title):
                stats["title_rejected"] += 1
                continue
            if new_title != old_title:
                stats["title_changed"] += 1

            # re-derive location from the OLD location text (best available context)
            location, _, _, _, source = self.parse_job_metadata(
                name, new_title, old_loc, url)
            if location != old_loc:
                stats["location_changed"] += 1
            old_low = old_loc.lower()
            if old_low == "global":
                stats["global_fixed"] += 1
            if old_low == "uk":
                stats["uk_fixed"] += 1
            if "jobdetail" in old_low:
                stats["jobdetail_fixed"] += 1
            if any(b in old_low for b in brand_words):
                stats["brand_fixed"] += 1

            # dedupe on (company, title, location), preferring real URLs
            key = (name.lower(), new_title.lower(), location.lower())
            is_synth = "#job=" in url.lower()
            if key in seen_tl:
                stats["dropped_dup"] += 1
                if not is_synth:
                    for i, prev in enumerate(out_rows):
                        if (prev["Company Name"].lower(),
                                prev["Job Title"].lower(),
                                prev["Job Location"].lower()) == key:
                            if "#job=" in prev["Job URL"].lower():
                                out_rows[i] = dict(prev)
                                out_rows[i]["Job URL"] = url
                            break
                continue
            seen_tl.add(key)

            out_rows.append({
                "Company Name": name,
                "Industry Type": industry,
                "Job Title": new_title,
                "Job Location": location,
                "Job Type": job_type,  # keep original crawl value (best available)
                "Job URL": url,
                "EU Blue Card": blue,
                "Relocation/Visa Support": visa,
                "Location Source": source,
                "URL Type": "synthetic" if is_synth else "real",
                # carry over support columns if present in input, else defaults
                "Visa Sponsorship": (r.get("Visa Sponsorship") or "Unknown").strip(),
                "Relocation Support": (r.get("Relocation Support") or "Unknown").strip(),
                "Relocation Required": (r.get("Relocation Required") or "No").strip(),
                "Support Confidence": (r.get("Support Confidence") or "").strip(),
                "Support Evidence": (r.get("Support Evidence") or "").strip(),
            })
            stats["kept"] += 1

        # FIXED: optional detail-page enrichment — visit 'Not Specified' rows'
        # job pages and recover locations from JSON-LD / page body
        if detail and out_rows:
            self._enrich_ns_locations(out_rows)
            # FIXED: also run the context-aware support detector on fetched pages
            self._enrich_support_from_detail(out_rows)

        with open(output_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=columns)
            w.writeheader()
            w.writerows(out_rows)

        report = (
            "\n=== REPROCESS REPORT ===\n"
            f"Input rows        : {len(rows)}\n"
            f"Kept (cleaned)    : {stats['kept']}\n"
            f"Titles changed    : {stats['title_changed']}\n"
            f"Titles rejected   : {stats['title_rejected']}\n"
            f"Locations changed : {stats['location_changed']}\n"
            f"  'Global' fixed  : {stats['global_fixed']}\n"
            f"  'Uk' fixed      : {stats['uk_fixed']}\n"
            f"  'JobDetail' fix : {stats['jobdetail_fixed']}\n"
            f"  store-brand fix : {stats['brand_fixed']}\n"
            f"Dup rows removed  : {stats['dropped_dup']}\n"
            f"Output            : {output_csv}\n"
        )
        print(report)
        with open(output_csv.replace(".csv", "_clean_report.txt"),
                  "w", encoding="utf-8") as f:
            f.write(report)

    def _try_ats_fallback(self, p, name, process_job):
        """Zero-yield rescue: pull jobs from public ATS JSON/XML APIs
        (Greenhouse / Lever / Ashby / Personio / Recruitee / Workable).
        Returns number of jobs added."""
        import xml.etree.ElementTree as ET
        entries = self.config.ATS_FALLBACK.get(name)
        if not entries:
            return 0
        api_builders = {
            "ashby": lambda slug: ("https://api.ashbyhq.com/posting-api/job-board/"
                                   f"{slug}?includeCompensation=false", "json"),
            "lever": lambda slug: (f"https://api.lever.co/v0/postings/{slug}?mode=json", "json"),
            "greenhouse": lambda slug: (f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", "json"),
            "personio": lambda slug: (f"https://{slug}.jobs.personio.de/xml?language=en", "xml"),
            "recruitee": lambda slug: (f"https://{slug}.recruitee.com/api/offers/", "json"),
            "workable": lambda slug: (f"https://www.workable.com/api/accounts/{slug}?details=true", "json"),
        }
        added = 0
        for ats, slugs in entries.items():
            if ats not in api_builders:
                continue
            for slug in slugs:
                url, fmt = api_builders[ats](slug)
                try:
                    req = urllib.request.Request(url, headers={
                        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                       "AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"),
                        "Accept": "application/json,text/xml,*/*",
                    })
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        if resp.status != 200:
                            continue
                        raw = resp.read().decode("utf-8", "replace")
                    jobs = []
                    if fmt == "json":
                        import json as _json
                        data = _json.loads(raw)
                        if ats == "greenhouse":
                            for it in data.get("jobs") or []:
                                jobs.append({
                                    "job_title": it.get("title") or "",
                                    "job_url": it.get("absolute_url") or "",
                                    "location_hint": (it.get("location") or {}).get("name") or "",
                                    "card_context": " | ".join(filter(None, [
                                        it.get("department") or "",
                                        it.get("employment_type") or "",
                                    ])),
                                })
                        elif ats == "lever":
                            for it in data:
                                cats = it.get("categories") or {}
                                loc = cats.get("location") or cats.get("allLocations") or ""
                                if isinstance(loc, list):
                                    loc = ", ".join(loc)
                                jobs.append({
                                    "job_title": it.get("text") or "",
                                    "job_url": it.get("hostedUrl") or "",
                                    "location_hint": str(loc),
                                    "card_context": " | ".join(filter(None, [
                                        cats.get("team") or "",
                                        cats.get("commitment") or "",
                                        it.get("workplaceType") or "",
                                    ])),
                                })
                        elif ats == "ashby":
                            for it in (data.get("jobs") or []):
                                jobs.append({
                                    "job_title": it.get("title") or "",
                                    "job_url": it.get("jobUrl") or it.get("applyUrl") or "",
                                    "location_hint": it.get("location") or "",
                                    "card_context": " | ".join(filter(None, [
                                        it.get("department") or "",
                                        it.get("employmentType") or "",
                                    ])),
                                })
                        elif ats == "recruitee":
                            for it in (data.get("offers") or []):
                                jobs.append({
                                    "job_title": it.get("title") or "",
                                    "job_url": it.get("careers_url") or "",
                                    "location_hint": it.get("location") or "",
                                    "card_context": it.get("department") or "",
                                })
                        elif ats == "workable":
                            for it in (data.get("jobs") or []):
                                loc = " ".join(filter(None, [
                                    it.get("city") or "", it.get("country") or ""])).strip()
                                jobs.append({
                                    "job_title": it.get("title") or "",
                                    "job_url": it.get("url") or "",
                                    "location_hint": loc,
                                    "card_context": " | ".join(filter(None, [
                                        it.get("department") or "",
                                        it.get("employment_type") or "",
                                        it.get("worktype") or "",
                                    ])),
                                })
                    else:  # personio xml
                        try:
                            root = ET.fromstring(raw)
                        except Exception:
                            continue
                        for pos in root.findall(".//position"):
                            jobs.append({
                                "job_title": (pos.findtext("name") or "").strip(),
                                "job_url": (pos.findtext("jobUrl") or "").strip(),
                                "location_hint": (pos.findtext("office") or "").strip(),
                                "card_context": " | ".join(filter(None, [
                                    (pos.findtext("department") or "").strip(),
                                    (pos.findtext("employmentType") or "").strip(),
                                    (pos.findtext("schedule") or "").strip(),
                                ])),
                            })
                    for j in jobs:
                        if process_job(j):
                            added += 1
                    if added:
                        print(f"   -> {name}: ATS API fallback ({ats}/{slug}) added {added} jobs")
                        return added
                except Exception:
                    continue
        return added

    def _ensure_output_header(self, columns, path=None):
        path = path or self.output_csv
        if not columns:
            return
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                csv.DictWriter(f, fieldnames=columns).writeheader()
            return
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            first = next(reader, [])
        if first != columns:
            raise ValueError(
                f"Output schema mismatch for {path}. Expected {columns!r}, found {first!r}. "
                "Use a fresh output path or explicitly migrate the file."
            )

    def _kill_child_processes(self):
        """Kill lingering Playwright node-driver / chrome child processes
        (descendants of THIS process only) so they cannot dump EPIPE /
        unhandled-error output to the terminal after the script exits."""
        import signal, subprocess
        try:
            def _kill_children(ppid):
                try:
                    out = subprocess.run(["pgrep", "-P", str(ppid)],
                                         capture_output=True, text=True, timeout=5)
                    kids = [int(x) for x in out.stdout.split() if x.strip()]
                except Exception:
                    return []
                for k in kids:
                    try:
                        os.kill(k, signal.SIGKILL)
                    except Exception:
                        pass
                return kids
            level = [os.getpid()]
            for _ in range(4):  # python -> node driver -> chrome -> zygotes
                nxt = []
                for pid in level:
                    nxt += _kill_children(pid)
                if not nxt:
                    break
                level = nxt
        except Exception:
            pass

    def _write_errors_header(self, path):
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            csv.DictWriter(f, fieldnames=_ERROR_COLUMNS).writeheader()

    def _record_error(self, seed_name, phase, err_type, message, seed_url=""):
        """Append one row to the run's errors CSV (immediate, crash-safe)."""
        path = getattr(self, "_errors_csv", None)
        if not path:
            return
        try:
            with open(path, "a", newline="", encoding="utf-8-sig") as f:
                csv.DictWriter(f, fieldnames=_ERROR_COLUMNS).writerow({
                    "Run ID": self.run_id,
                    "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "Seed Name": seed_name, "Phase": phase,
                    "Error Type": err_type, "Message": (message or "")[:2000],
                    "Seed URL": seed_url,
                })
        except Exception:
            pass  # error logging must never crash a scan

    @staticmethod
    def _quarantine_key(seed, url, reason):
        """Identity for cross-run quarantine dedupe (F1).

        Quarantine rows carry no Canonical Job ID, so the key is
        (seed, url, reason). URL keeps case (paths are case-sensitive).
        """
        return ((seed or "").strip().casefold(),
                (url or "").strip(),
                (reason or "").strip().casefold())

    def _load_seen_quarantine(self, path):
        """Keys already recorded in the quarantine file (F1 resume)."""
        seen = set()
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    key = self._quarantine_key(row.get("Seed Name"),
                                               row.get("Job URL"),
                                               row.get("Quarantine Reason"))
                    if key[1]:
                        seen.add(key)
        except OSError:
            pass
        return seen

    def _drop_seen_quarantine(self, quarantine, seen, diagnostics):
        """Drop rows already recorded (in place). Returns skipped count."""
        new_q = []
        for rec in quarantine:
            key = self._quarantine_key(rec.get("Seed Name"),
                                       rec.get("Job URL"),
                                       rec.get("Quarantine Reason"))
            if key in seen:
                continue
            seen.add(key)
            new_q.append(rec)
        skipped = len(quarantine) - len(new_q)
        if skipped:
            diagnostics.append(f"quarantine re-seen skipped: {skipped}")
        quarantine[:] = new_q
        return skipped

    @staticmethod
    def _log_pagination_stop(diagnostics, reason, page_num, total):
        """One-line pagination outcome for scan_log Diagnostics (G1)."""
        diagnostics.append(
            f"pagination {reason} after page {page_num} ({total} jobs)")

    def _reextract_after_reload(self, page, target, page_num, diagnostics):
        """Reload a zero-card listing page and re-extract once (G1).

        A page yielding no cards at all is a load failure (throttle /
        hydration miss), not end-of-listing — the 09-13 Luxottica crawl
        silently died this way at page ~21 of 158. Returns the new batch
        (possibly still empty). Never raises.
        """
        try:
            page.reload(wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)
            target = self.check_iframes(page) or page
            batch = self.extract_visible_jobs(target)
        except Exception:
            return []
        if batch:
            diagnostics.append(
                f"page {page_num} recovered after reload ({len(batch)} cards)")
        return batch

    def _drop_accepted_twins(self, quarantine, seen_global, diagnostics):
        """Drop quarantine rows already accepted via another URL form (G2).

        Invalid titles quarantine BEFORE the canonical-ID dict, so URL-form
        twins (ING /search-jobs/jobs/ID vs slug URL: 684 noise rows on
        09-13) never collapse. End-of-run full knowledge makes this
        order-safe: a row dies only if its canonical ID is already in
        seen_global (resume-loaded + this run's accepted). Rows without a
        URL-derived identity are always kept.
        """
        kept, dropped = [], 0
        for rec in quarantine:
            cid = self.canonical_job_id(rec.get("Seed Name") or "",
                                        rec.get("Job URL") or "")
            ident = cid.split("|", 1)[1] if "|" in cid else ""
            if ident and cid in seen_global:
                dropped += 1
                continue
            kept.append(rec)
        if dropped:
            diagnostics.append(f"quarantine accepted twins dropped: {dropped}")
        quarantine[:] = kept
        return dropped

    def execute_crawler(self):
        """v7 crawl: provider-first, fresh/transactional outputs, separated recruiters."""
        import threading
        import concurrent.futures as cf

        base = self.output_csv[:-4] if self.output_csv.lower().endswith(".csv") else self.output_csv
        self._errors_csv = base + "_errors.csv"
        try:
            targets = self.read_seed_file()
        except Exception as exc:
            self._write_errors_header(self._errors_csv)
            self._record_error("?", "seed", type(exc).__name__, str(exc))
            print(f"Seed file error ({self.input_csv}): {exc}")
            return
        if self.only_companies:
            wanted = {c.strip().lower() for c in self.only_companies if c and c.strip()}
            targets = [t for t in targets if t.get("name", "").strip().lower() in wanted]
            if not targets:
                print(f"no career targets matched only_companies={self.only_companies}")
                return
        crawl_start = time.monotonic()
        self._last_activity = time.monotonic()
        self._detail_lock = threading.Lock()
        self._detail_count = 0

        # Gate the run: if DNS/network is down, abort BEFORE truncating any output
        # files, so a failed pre-flight never wipes a previous good dataset.
        self._preflight_connectivity()

        columns = [
            "Company Name", "Seed Name", "Source Type", "Hiring Company", "Target Country", "Scope Policy",
            "Industry Type", "Sponsorship History Score", "English Friendly Score", "Remote Score",
            "Job Title", "Raw Job Title", "Job Location", "Raw Location", "Job Type",
            "Job URL", "Canonical Job ID", "Provider", "Extraction Method",
            "EU Blue Card", "Blue Card Evidence", "Relocation/Visa Support",
            "Work Mode", "Scope Confidence",
            # FIX P0-30
            "Experience Required", "Experience Min Years", "Experience Level",
            "Experience Source",
            "Location Source", "URL Type", "Visa Sponsorship", "Relocation Support",
            "Relocation Required", "Support Confidence", "Support Evidence",
            "Support Evidence URL", "Support Evidence Type", "Record Status",
            "Quarantine Reason", "Run ID", "Scanned At",
        ]
        base = self.output_csv[:-4] if self.output_csv.lower().endswith(".csv") else self.output_csv
        recruiter_csv = base + "_recruiter.csv"
        quarantine_csv = base + "_quarantine.csv"
        scan_log_csv = base + "_scan_log.csv"
        errors_csv = base + "_errors.csv"
        self._errors_csv = errors_csv
        log_columns = [
            "Run ID", "Seed Name", "Company", "Source Type", "Target Country", "Status",
            "Provider", "Jobs Found", "Quarantined", "Duplicates", "Rejected Scope",
            "Error", "Diagnostics", "Duration Sec", "Seed URL",
        ]

        output_paths = [self.output_csv, recruiter_csv, quarantine_csv]
        if not self.resume:
            for path in output_paths:
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    csv.DictWriter(f, fieldnames=columns).writeheader()
            with open(scan_log_csv, "w", newline="", encoding="utf-8-sig") as f:
                csv.DictWriter(f, fieldnames=log_columns).writeheader()
            self._write_errors_header(errors_csv)
        else:
            for path in output_paths:
                self._ensure_output_header(columns, path)
            if not os.path.exists(scan_log_csv) or os.path.getsize(scan_log_csv) == 0:
                with open(scan_log_csv, "w", newline="", encoding="utf-8-sig") as f:
                    csv.DictWriter(f, fieldnames=log_columns).writeheader()
            else:
                self._ensure_output_header(log_columns, scan_log_csv)
            self._ensure_output_header(_ERROR_COLUMNS, errors_csv)

        file_lock = threading.Lock()
        seen_lock = threading.Lock()
        seen_global = set()
        if self.resume:
            for path in (self.output_csv, recruiter_csv):
                with open(path, newline="", encoding="utf-8-sig") as f:
                    for row in csv.DictReader(f):
                        cid = (row.get("Canonical Job ID") or "").strip()
                        if cid:
                            seen_global.add(cid)
        # F1: quarantine file has no Canonical IDs, so results resume left
        # it doubling every run (1295 dupes on 09-13). Load its keys too
        # (fresh runs load an empty set; also dedupes within-run repeats).
        seen_quarantine = self._load_seen_quarantine(quarantine_csv)

        print(f"v7 scan started: {len(targets)} enabled targets; run_id={self.run_id}; "
              f"resume={self.resume}; detail={self.detail_scan}")

        def crawl_target(idx, target_row):
            if check_control(self.cancel_event, self.pause_event):
                print(f"   CANCELLED: skipping target [{idx}] {target_row.get('name', '?')}")
                return 0, 0, "cancelled"
            name = target_row["name"]
            seed_name = target_row["seed_name"]
            seed_url = target_row["careers_url"]
            provider = target_row.get("provider", "auto")
            source_type = target_row.get("source_type", "direct_employer")
            started = time.monotonic()
            company_jobs = {}
            seen_title_forms = {}  # J1: norm title -> [(loc_norm, is_frag, ctx)]
            quarantine = []
            diagnostics = []
            stats = {
                "duplicates": 0, "rejected_scope": 0, "quarantined": 0,
                "rejected_url": 0, "rejected_title": 0, "synthetic": 0,
            }
            browser = ctx = page = None
            error = ""
            print(f"\n[{idx}/{len(targets)}] {seed_name} -> {name} [{source_type}, {target_row.get('target_country')}]")

            def base_record(raw_title, clean_title, raw_location, clean_url, reason=""):
                now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                return {
                    "Company Name": name,
                    "Seed Name": seed_name,
                    "Source Type": source_type,
                    "Hiring Company": name if source_type == "direct_employer" else "Unknown",
                    "Target Country": target_row.get("target_country", "Global"),
                    "Scope Policy": target_row.get("scope_policy", "global"),
                    "Industry Type": target_row.get("industry", "Unknown"),
                    "Sponsorship History Score": target_row.get("sponsorship_history") if target_row.get("sponsorship_history") is not None else "",
                    "English Friendly Score": target_row.get("english_friendly") if target_row.get("english_friendly") is not None else "",
                    "Remote Score": target_row.get("remote_score") if target_row.get("remote_score") is not None else "",
                    "Job Title": clean_title or raw_title or "Unknown",
                    "Raw Job Title": raw_title or "",
                    "Job Location": "Unknown",
                    "Raw Location": raw_location or "",
                    "Job Type": "Unknown / Unknown",
                    "Job URL": clean_url or "",
                    "Canonical Job ID": "",
                    "Provider": provider,
                    "Extraction Method": "unknown",
                    "EU Blue Card": "Unknown",
                    "Blue Card Evidence": "",
                    "Relocation/Visa Support": "Unknown",
                    "Work Mode": "Unknown",
                    "Scope Confidence": "n/a",
                    # FIX P0-30
                    "Experience Required": "Unknown",
                    "Experience Min Years": "",
                    "Experience Level": "Unknown",
                    "Experience Source": "none",
                    "Location Source": "none",
                    "URL Type": "synthetic" if "#job=" in (clean_url or "").lower() else "real",
                    "Visa Sponsorship": "Unknown",
                    "Relocation Support": "Unknown",
                    "Relocation Required": "Unknown",
                    "Support Confidence": 0.0,
                    "Support Evidence": "",
                    "Support Evidence URL": "",
                    "Support Evidence Type": "none",
                    "Record Status": "quarantine" if reason else "accepted",
                    "Quarantine Reason": reason,
                    "Run ID": self.run_id,
                    "Scanned At": now,
                }

            def quarantine_job(raw_title, raw_url, raw_location, reason, method="unknown"):
                clean_url = self.clean_and_normalize_url(raw_url)
                clean_title = self.clean_job_title(raw_title)
                rec = base_record(raw_title, clean_title, raw_location, clean_url, reason)
                rec["Extraction Method"] = method
                quarantine.append(rec)
                stats["quarantined"] += 1

            def process_job(job):
                raw_url = str(job.get("job_url") or "").strip()
                raw_title = str(job.get("job_title") or "").strip()
                raw_location = str(job.get("location_hint") or "").strip()
                context = str(job.get("card_context") or "").strip()
                method = str(job.get("extraction_method") or "dom_heuristic")
                clean_url = self.clean_and_normalize_url(raw_url)
                # J1: the old "#job=" blanket quarantine lived here — removed.
                # Those fragment URLs are real jobs (Exact/Miro/Ocado/KPN/...,
                # ~577 rows/run in the 09-09 quarantine data) with valid titles;
                # they now flow through normal validation. Same-job twins in
                # clean-URL form are caught by the _frag_twin_in_index guard
                # below instead of double-counting.
                if not self.is_valid_job_url(clean_url):
                    stats["rejected_url"] += 1
                    quarantine_job(raw_title, raw_url, raw_location, "invalid_or_application_only_url", method)
                    return False
                if self.is_self_listing_url(clean_url, seed_url):
                    stats["rejected_url"] += 1
                    quarantine_job(raw_title, raw_url, raw_location, "self_listing_url", method)
                    return False
                clean_title = self.clean_job_title(raw_title)
                if not self.is_valid_job_title(clean_title):
                    # J2: try the URL slug before quarantining (button labels
                    # like "Show job" / chips like "Full time" on real job URLs).
                    rescued = self._title_from_url_slug(clean_url)
                    if rescued and self.is_valid_job_title(rescued):
                        clean_title = rescued
                        method = f"{method}+slug_title"
                    else:
                        stats["rejected_title"] += 1
                        quarantine_job(raw_title, raw_url, raw_location, "invalid_generic_or_department_title", method)
                        return False
                combined_context = f"{raw_location}\n{context}".strip()
                location, job_type, _, _, loc_source = self.parse_job_metadata(
                    name, clean_title, combined_context, clean_url
                )
                out_location = "Unknown" if location == "Not Specified" else location
                # FIX P0-11: COMPANY_HEADQUARTERS was defined (120 entries) but
                # never referenced. Use it as the LAST resort so a row carries
                # the employer's base city instead of a bare "Unknown".
                if out_location == "Unknown":
                    hq = self.config.COMPANY_HEADQUARTERS.get(name)
                    if hq:
                        out_location, loc_source = hq, "company_hq"
                # Regional seed context resolves known local administrative-code collisions.
                if target_row.get("target_country") == "Italy" and out_location in {"Milan, MI", "Milan, Spain"}:
                    out_location, loc_source = "Milan, Italy", "seed_scope+card"
                # J1: same job as an accepted row but in the other URL form
                # (fragment vs clean) — quarantine as a variant dupe (still
                # reviewable), don't double-count it.
                if self._frag_twin_in_index(seen_title_forms, clean_title, out_location,
                                            "#job=" in clean_url.lower(), combined_context):
                    stats["duplicates"] += 1
                    rec = base_record(raw_title, clean_title, raw_location, clean_url, "duplicate_url_variant")
                    rec.update({"Job Location": out_location, "Job Type": job_type,
                                "Location Source": loc_source, "Extraction Method": method})
                    quarantine.append(rec); stats["quarantined"] += 1
                    return False
                if not self._scope_allows(target_row, out_location, combined_context, clean_url):
                    stats["rejected_scope"] += 1
                    rec = base_record(raw_title, clean_title, raw_location, clean_url, "outside_or_unproven_target_country")
                    rec.update({"Job Location": out_location, "Job Type": job_type,
                                "Location Source": loc_source, "Extraction Method": method})
                    quarantine.append(rec); stats["quarantined"] += 1
                    return False
                cid = self.canonical_job_id(
                    f"{name}|{target_row.get('target_country','Global')}", clean_url,
                    clean_title, out_location, provider,
                )
                # FIX P0-12: split work mode out of Job Location. "Remote"
                # inside a location string is ambiguous for a sponsorship tool
                # (remote-from-anywhere vs remote-within-country differ legally).
                _wm_blob = f"{clean_title} {combined_context} {out_location}".lower()
                if re.search(r"\b(fully remote|100% remote|remote[- ]first|work from anywhere)\b", _wm_blob):
                    work_mode = "Remote"
                elif re.search(r"\bhybrid|ibrido\b", _wm_blob):
                    work_mode = "Hybrid"
                elif re.search(r"\bremote|telelavoro|smart working\b", _wm_blob):
                    work_mode = "Remote"
                elif re.search(r"\bon[- ]?site|onsite|in[- ]office|presenza\b", _wm_blob):
                    work_mode = "Onsite"
                else:
                    work_mode = "Unknown"
                rec = base_record(raw_title, clean_title, raw_location, clean_url)
                _policy = (target_row.get("scope_policy") or "global").lower()
                _tc = (target_row.get("target_country") or "Global").strip()
                if _policy == "seed_url" and _tc.casefold() != "global":
                    _scope_conf = ("verified"
                                   if self._scope_country_match(_tc, out_location,
                                                                combined_context, clean_url)
                                   else "unverified_seed_url")
                elif _policy == "job_location" and _tc.casefold() != "global":
                    _scope_conf = "verified"
                else:
                    _scope_conf = "n/a"
                rec.update({
                    "Job Location": out_location,
                    "Work Mode": work_mode,
                    "Scope Confidence": _scope_conf,
                    "Job Type": job_type,
                    "Location Source": loc_source,
                    "Canonical Job ID": cid,
                    "Extraction Method": method,
                })
                # FIX P0-30: prefer the FULL description when the provider API
                # already returned one (jd_text) — zero extra requests — else
                # fall back to the card blurb. An employer-published seniority
                # field (experience_level_hint) outranks every inference.
                apply_experience_to_record(
                    rec,
                    jd_text=str(job.get("jd_text") or ""),
                    card_context=combined_context,
                    title=clean_title,
                    level_hint=str(job.get("experience_level_hint") or ""),
                )
                prev = company_jobs.get(cid)
                if prev is not None:
                    stats["duplicates"] += 1
                    if self._record_quality(rec) > self._record_quality(prev):
                        company_jobs[cid] = rec
                    return False
                company_jobs[cid] = rec
                # J1 cross-form index + telemetry (stats["synthetic"] now
                # counts fragment-form ACCEPTS, not quarantines).
                _tn = re.sub(r"\s+", " ", clean_title).strip().casefold()
                _ln = re.sub(r"\s+", " ", out_location).strip().casefold()
                _cx = re.sub(r"\s+", " ", combined_context).strip().casefold()[:500]
                seen_title_forms.setdefault(_tn, []).append((_ln, "#job=" in clean_url.lower(), _cx))
                if "#job=" in clean_url.lower():
                    stats["synthetic"] += 1
                return True

            try:
                # Provider adapters are authoritative and avoid fragile DOM pagination.
                api_jobs, api_diag = self._fetch_provider_jobs(target_row)
                diagnostics.append(api_diag)
                for job in api_jobs:
                    process_job(job)
                provider_success = bool(api_jobs)

                # FIX P0-31: static-HTML fast path. Before paying for a
                # Chromium launch + settle delays, try the raw HTML. Uses the
                # scanner's OWN url/title validators, so acceptance rules are
                # identical to the browser path; only the transport differs.
                # Rejected unless it yields >= _STATIC_MIN_JOBS links, so a JS
                # shell can never silently truncate a company's results.
                if not provider_success:
                    try:
                        _static_jobs, _static_diag = fetch_static_jobs(
                            seed_url,
                            url_validator=self.is_valid_job_url,
                            title_validator=self.is_valid_job_title,
                        )
                    except Exception as _sexc:
                        _static_jobs, _static_diag = [], (
                            f"static: error {type(_sexc).__name__}")
                    diagnostics.append(_static_diag)
                    if _static_jobs:
                        # FIX P0-31b: accepting a static result BLOCKS the
                        # browser, so a partial static harvest permanently
                        # truncates that company. Observed on Miro: static
                        # found 12 links, the browser found 27 (the rest are
                        # rendered client-side), so 15 real jobs vanished.
                        # Only trust static when the page carries a
                        # server-rendered listing AND shows no sign of a
                        # client-rendered job list.
                        _kept = sum(1 for j in _static_jobs if process_job(j))
                        if _kept >= _STATIC_MIN_JOBS:
                            provider_success = True
                            diagnostics.append(f"static accepted: {_kept} kept")
                        else:
                            diagnostics.append(
                                f"static yielded {_kept} accepted row(s) "
                                f"(<{_STATIC_MIN_JOBS}); running browser too")

                # Browser fallback only when no provider data was available.
                if not provider_success:
                    # FIX: fail fast on unresolvable seed hosts (DNS) instead
                    # of burning GOTO_RETRIES x backoff on doomed navigations.
                    _seed_host = urlparse(seed_url).hostname or ""
                    if _seed_host and not _dns_resolves(_seed_host):
                        diagnostics.append(f"seed host does not resolve (DNS): {_seed_host}")
                        raise RuntimeError(f"seed host does not resolve (DNS): {_seed_host} [{seed_url}]")
                    if sync_playwright is None:
                        _pw_reason = _playwright_unavailable_reason()
                        diagnostics.append(f"playwright unavailable: {_pw_reason}")
                        raise RuntimeError(
                            f"Playwright is required for DOM fallback ({_pw_reason}). "
                            "Install requirements and run: playwright install chromium"
                        )
                    # Verify the Chromium binary is actually present/ready
                    # before launching. Without this, a packaged build without
                    # the bundled `_playwright` browsers used to throw the raw
                    # Playwright "Executable doesn't exist" banner for every
                    # single company, wiping out all `provider=auto` targets.
                    try:
                        from sponsorscout.services.browser_fetcher import (
                            _ensure_playwright_browsers,
                        )
                        _browser_ready = _ensure_playwright_browsers()
                    except ImportError:
                        # Batch K standalone: the pre-check helper module is not
                        # shipped with the script, so skip the pre-check and let
                        # the Chromium launch below be the real readiness test.
                        # Launch failures still raise and land in errors.csv.
                        global _BROWSER_PRECHECK_WARNED
                        if not _BROWSER_PRECHECK_WARNED:
                            _BROWSER_PRECHECK_WARNED = True
                            _notify("sponsorscout.services.browser_fetcher not found - running standalone: skipping browser pre-check (launch failures surface per-target in errors.csv).")
                        _browser_ready = True
                    except Exception as _exc:  # pragma: no cover
                        _notify(f"Browser readiness check failed: {_exc}")
                        _browser_ready = False
                    if not _browser_ready:
                        raise RuntimeError(
                            "Chromium browser not available for DOM fallback "
                            "(packaged builds must ship the `_playwright` "
                            "browser bundle; dev builds need `playwright "
                            "install chromium`)"
                        )
                    with sync_playwright() as pw:
                        browser = pw.chromium.launch(
                            headless=True,
                            args=BROWSER_ARGS
                        )
                        ctx = browser.new_context(
                            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
                            viewport={"width": 1440, "height": 900}, locale="en-US",
                            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                        )
                        page = ctx.new_page()
                        loaded = False
                        for attempt in range(1, self.config.GOTO_RETRIES + 1):
                            try:
                                page.goto(seed_url, wait_until="domcontentloaded", timeout=self.config.ACTION_TIMEOUT_MS)
                                loaded = True; break
                            except PlaywrightTimeoutError:
                                # Navigation timed out but a partial DOM may be usable.
                                diagnostics.append(f"seed timeout attempt {attempt}; partial DOM used")
                                loaded = True; break
                            except Exception as exc:
                                net = self._is_network_error(exc)
                                diagnostics.append(
                                    f"seed attempt {attempt}: {type(exc).__name__}: {exc}"
                                    f"{' [network]' if net else ''}"
                                )
                                if net and attempt < self.config.GOTO_RETRIES:
                                    backoff = self.config.GOTO_BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                                    diagnostics.append(f"retrying in {backoff:.1f}s")
                                    page.wait_for_timeout(int(backoff * 1000))
                                elif attempt < self.config.GOTO_RETRIES:
                                    page.wait_for_timeout(1500)
                        if not loaded:
                            raise RuntimeError(f"seed page did not load: {seed_url}")
                        page.wait_for_timeout(min(3000, self.config.STABILIZATION_DELAY_SEC * 1000))
                        self.dismiss_initial_blockers(page)
                        self.navigate_to_fragment(page, seed_url)
                        self.wait_for_job_cards_to_load(page)
                        target = self.check_iframes(page) or page
                        size = self.try_increase_page_size(page, target)
                        if size: diagnostics.append(f"page size increased to {size}")
                        current = self.extract_visible_jobs(target)
                        if not current:
                            # FIX P0-17: a late-injected consent overlay is a
                            # common cause of "page loaded but zero jobs".
                            # Re-dismiss before concluding the board is empty.
                            try:
                                if self.dismiss_initial_blockers(page):
                                    page.wait_for_timeout(800)
                                    target = self.check_iframes(page) or page
                                    current = self.extract_visible_jobs(target)
                            except Exception:
                                pass
                        if not current:
                            changed = self.handle_landing_page_redirect(page, seed_url) or self.trigger_search_if_present(page)
                            if changed:
                                page.wait_for_timeout(1500)
                                self.dismiss_initial_blockers(page, deep=False)
                                target = self.check_iframes(page) or page
                                current = self.extract_visible_jobs(target)
                        for job in current: process_job(job)
                        self.aggressive_infinite_scroll(page, target)
                        self.click_load_more_repeatedly(page, target)

                        prev_total = -1; empty_pages = 0; low_yield = 0
                        seed_params = parse_qsl(urlparse(seed_url).query)
                        for page_num in range(1, self.config.MAX_PAGINATION_PAGES + 1):
                            # Pause/Stop must take effect between pages. Before
                            # this gate the only exit from the pagination loop
                            # was MAX_COMPANY_TIME_SEC (15 min), so pressing Stop
                            # left the scan running for many minutes and the
                            # button looked dead.
                            if check_control(self.cancel_event, self.pause_event):
                                diagnostics.append(f"stopped by user at page {page_num}")
                                break
                            if time.monotonic() - started > self.config.MAX_COMPANY_TIME_SEC:
                                diagnostics.append(f"company budget reached at page {page_num}")
                                break
                            target = self.check_iframes(page) or page
                            self.progressive_scroll_and_wait(page, target)
                            batch = self.extract_visible_jobs(target)
                            if not batch:
                                batch = self._reextract_after_reload(
                                    page, target, page_num, diagnostics)
                            added = sum(1 for job in batch if process_job(job))
                            self._last_activity = time.monotonic()
                            total = len(company_jobs)
                            if total == prev_total: empty_pages += 1
                            else: empty_pages = 0
                            prev_total = total
                            if empty_pages >= 3:
                                self._log_pagination_stop(
                                    diagnostics, "STOPPED: 3 no-growth pages",
                                    page_num, total)
                                break
                            if added <= 2: low_yield += 1
                            else: low_yield = 0
                            if low_yield >= self.config.LOW_YIELD_PAGES:
                                self._log_pagination_stop(
                                    diagnostics,
                                    f"STOPPED: {self.config.LOW_YIELD_PAGES} low-yield pages",
                                    page_num, total)
                                break
                            if not self.execute_advanced_pagination(page, target, page_num, seed_params):
                                self._log_pagination_stop(
                                    diagnostics, "complete: no next-page control",
                                    page_num, total)
                                break
                        else:
                            self._log_pagination_stop(
                                diagnostics,
                                f"page cap reached ({self.config.MAX_PAGINATION_PAGES})",
                                self.config.MAX_PAGINATION_PAGES, len(company_jobs))

                        company_jobs = self._dedupe_records(company_jobs)
                        if self.detail_scan and company_jobs:
                            self._detail_scan_for_company(page, name, company_jobs)
                        try: ctx.close()
                        except Exception: pass
                        try: browser.close()
                        except Exception: pass
                        browser = ctx = page = None
                elif self.detail_scan and company_jobs:
                    # API-first crawl still gets explicit detail evidence when requested.
                    if sync_playwright is None:
                        _pw_reason = _playwright_unavailable_reason()
                        diagnostics.append(f"playwright unavailable: {_pw_reason}")
                        raise RuntimeError(
                            f"Playwright is required for --detail ({_pw_reason}). "
                            "Install requirements and Chromium"
                        )
                    with sync_playwright() as pw:
                        browser = pw.chromium.launch(headless=True, args=BROWSER_ARGS)
                        ctx = browser.new_context()
                        page = ctx.new_page()
                        self._detail_scan_for_company(page, name, company_jobs)
                        try: ctx.close()
                        except Exception: pass
                        try: browser.close()
                        except Exception: pass
                        browser = ctx = page = None
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                diagnostics.append(error)
            finally:
                try:
                    if page is not None: page.close()
                except Exception: pass
                try:
                    if ctx is not None: ctx.close()
                except Exception: pass
                try:
                    if browser is not None: browser.close()
                except Exception: pass

            # Scope can improve after detail scan; normalize internal sentinel.
            for rec in company_jobs.values():
                if rec.get("Job Location") == "Not Specified": rec["Job Location"] = "Unknown"

            written = 0
            duplicates_global = 0
            accepted_rows = []
            with seen_lock:
                for cid, rec in company_jobs.items():
                    if cid in seen_global:
                        duplicates_global += 1
                        continue
                    seen_global.add(cid)
                    accepted_rows.append(rec)
            stats["duplicates"] += duplicates_global
            destination = recruiter_csv if source_type == "recruiter" else self.output_csv
            with file_lock:
                if accepted_rows:
                    with open(destination, "a", newline="", encoding="utf-8-sig") as f:
                        w = csv.DictWriter(f, fieldnames=columns)
                        for rec in accepted_rows:
                            w.writerow({k: rec.get(k, "") for k in columns}); written += 1
                if quarantine:
                    self._drop_accepted_twins(quarantine, seen_global, diagnostics)
                    self._drop_seen_quarantine(quarantine, seen_quarantine, diagnostics)
                if quarantine:
                    with open(quarantine_csv, "a", newline="", encoding="utf-8-sig") as f:
                        w = csv.DictWriter(f, fieldnames=columns)
                        for rec in quarantine:
                            w.writerow({k: rec.get(k, "") for k in columns})
                status = "error" if error and not written else ("partial" if error else ("ok" if written else "empty"))
                with open(scan_log_csv, "a", newline="", encoding="utf-8-sig") as f:
                    csv.DictWriter(f, fieldnames=log_columns).writerow({
                        "Run ID": self.run_id, "Seed Name": seed_name, "Company": name,
                        "Source Type": source_type, "Target Country": target_row.get("target_country"),
                        "Status": status, "Provider": provider, "Jobs Found": written,
                        "Quarantined": len(quarantine), "Duplicates": stats["duplicates"],
                        "Rejected Scope": stats["rejected_scope"], "Error": error,
                        "Diagnostics": " | ".join(diagnostics)[-4000:],
                        "Duration Sec": round(time.monotonic()-started,1), "Seed URL": seed_url,
                    })
                if error:
                    etype, _, emsg = error.partition(": ")
                    self._record_error(seed_name, "target", etype, emsg, seed_url)
            print(f"   {status.upper()} {name}: wrote={written}, quarantined={len(quarantine)}, "
                  f"dups={stats['duplicates']}, scope_reject={stats['rejected_scope']}")
            return written, len(quarantine), status

        totals = collections.Counter()
        with cf.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = []
            for i, row in enumerate(targets, 1):
                if check_control(self.cancel_event, self.pause_event):
                    print(f"   CANCELLED: not submitting remaining {len(targets) - i + 1} targets")
                    break
                futures.append(executor.submit(crawl_target, i, row))
            for future in cf.as_completed(futures):
                try:
                    wrote, quarantined, status = future.result()
                    totals["written"] += wrote; totals["quarantined"] += quarantined; totals[status] += 1
                except Exception as exc:
                    totals["thread_errors"] += 1
                    print(f"Worker escaped error: {type(exc).__name__}: {exc}")
                    self._record_error("?", "worker", type(exc).__name__, str(exc))

        # FIX P0-15b: automatic DNS retry sweep. A transient resolver outage
        # is indistinguishable in the log from a genuinely dead host, and the
        # 09-15 run silently lost 24 companies that way. Re-crawl anything
        # that died on DNS once the main pass is done (network has usually
        # recovered by then, and the hosts are re-checked before retrying).
        try:
            dns_failed = []
            with open(scan_log_csv, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    if (row.get("Run ID") or "") != self.run_id:
                        continue
                    if "does not resolve (DNS)" in (row.get("Error") or ""):
                        dns_failed.append(row.get("Company") or "")
            retry_rows = [r for r in targets if r["name"] in set(dns_failed)]
            if retry_rows and not (self.cancel_event is not None and self.cancel_event.is_set()):
                print(f"\n[dns retry] {len(retry_rows)} target(s) failed DNS; re-checking hosts...")
                still_live = []
                for r in retry_rows:
                    h = urlparse(r["careers_url"]).hostname or ""
                    if _dns_resolves(h):
                        still_live.append(r)
                    else:
                        print(f"   confirmed unreachable: {r['name']} ({h})")
                if still_live:
                    print(f"[dns retry] {len(still_live)} host(s) now resolve — re-crawling.")
                    with cf.ThreadPoolExecutor(max_workers=self.max_workers) as ex2:
                        futs = [ex2.submit(crawl_target, i, r)
                                for i, r in enumerate(still_live, 1)]
                        for fut in cf.as_completed(futs):
                            try:
                                wrote, quarantined, status = fut.result()
                                totals["written"] += wrote
                                totals["quarantined"] += quarantined
                                totals[status] += 1
                                totals["dns_recovered"] += 1 if wrote else 0
                            except Exception as exc:
                                totals["thread_errors"] += 1
                                print(f"Retry worker error: {type(exc).__name__}: {exc}")
                    print(f"[dns retry] recovered {totals['dns_recovered']} company/companies.")
        except Exception as exc:
            print(f"[dns retry] skipped ({type(exc).__name__}: {exc})")

        # FIX P0-14: regression gate. Persist per-company yields and shout when
        # a company that previously returned jobs collapses to ~0. Every bug
        # found in this project surfaced first as a quiet count drop.
        try:
            baseline_path = base + "_baseline.json"
            current = {}
            with open(scan_log_csv, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    if (row.get("Run ID") or "") != self.run_id:
                        continue
                    try:
                        current[row["Company"]] = int(row.get("Jobs Found") or 0)
                    except ValueError:
                        continue
            previous = {}
            if os.path.exists(baseline_path):
                with open(baseline_path, encoding="utf-8") as f:
                    previous = json.load(f).get("counts", {})
            regressions = []
            for comp, was in previous.items():
                now = current.get(comp, 0)
                if was >= 5 and now <= max(1, int(was * 0.2)):
                    regressions.append((comp, was, now))
            if regressions:
                print("\n!! REGRESSION ALERT — companies that collapsed vs last run:")
                for comp, was, now in sorted(regressions, key=lambda x: -x[1]):
                    print(f"     {comp}: {was} -> {now}")
                print("   (investigate before trusting this dataset)")
            else:
                print("\n[regression gate] no company collapses vs previous run.")
            if current:
                with open(baseline_path, "w", encoding="utf-8") as f:
                    json.dump({"run_id": self.run_id, "counts": current}, f, indent=1)
        except Exception as exc:
            print(f"[regression gate] skipped: {type(exc).__name__}: {exc}")

        elapsed = int(time.monotonic()-crawl_start)
        print(f"\nv7 scan complete in {elapsed}s: direct/recruiter rows={totals['written']}, "
              f"quarantined={totals['quarantined']}, thread_errors={totals['thread_errors']}")
        print(f"  Direct: {self.output_csv}\n  Recruiters: {recruiter_csv}\n  Quarantine: {quarantine_csv}\n  Log: {scan_log_csv}\n"
              f"  Errors: {errors_csv}")

def extract_location_from_url(url):
    scanner = CareerPortalScanner()
    return scanner.extract_location_from_url(url)


if __name__ == "__main__":
    """Standalone entry: scrape the career seed without the desktop app.

    Examples:
      python career_scanner.py
      python career_scanner.py --input company_Career_seed.csv --output scraped_jobs.csv
      python career_scanner.py --company "Retool" --company "Pitch"
    """
    import argparse
    parser = argparse.ArgumentParser(description="Career Portal Scanner (standalone)")
    parser.add_argument("--input", default="company_Career_seed.csv")
    parser.add_argument("--output", default=None,
                        help="Default: scraped_career_jobs.csv")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--detail", action="store_true",
                        help="Enable detail-page enrichment pass")
    parser.add_argument("--company", action="append", default=None,
                        help="Only scan these companies (repeatable)")
    parser.add_argument("--max-company-time", type=int, default=None,
                        help="Per-company wall-clock budget in seconds "
                             "(default 900). Raise for giant boards, e.g. "
                             "--max-company-time 1800 for Luxottica/Hays tails")
    args = parser.parse_args()
    scanner = CareerPortalScanner(
        input_csv=args.input,
        output_csv=args.output or "scraped_career_jobs.csv",
        detail_scan=args.detail,
        resume=args.resume,
        skip_preflight=args.skip_preflight,
        only_companies=args.company,
        max_company_time_sec=args.max_company_time,
    )
    scanner.execute_crawler()
