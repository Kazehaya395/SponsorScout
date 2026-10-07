import os
import re
import csv
import time
import socket
import threading  # FIX P42: module-level, the title-rescue budget needs a lock
import collections
import unicodedata
import urllib.parse
import urllib.request
import json
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse
from html import unescape  # FIX P0-30: JD HTML -> plain text
from html.parser import HTMLParser
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
    """Why sync_playwright is unusable - surfaced in errors and diagnostics."""
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
const jobUrlRe = /(\/job(s)?\/[^\/?#]+|\/career(s)?\/(?!disciplines?\/|departments?\/|teams?\/|locations?\/|offices?\/|categor(y|ies)\/|areas?\/|functions?\/)[^/?#]+\/[^\/?#]+|\/career(s)?\/(?!benefits?\b|belonging\b|culture\b|values\b|story\b|people\b|team(s)?\b|location(s)?\b|office(s)?\b|student(s)?\b|discipline(s)?\/?|department(s)?\/?|our-story\b|interview-tips\b|recruitment-process\b|about\b|about-us\b|compatibility\b|emerging-talent\b|home\b|feed\b|search\b|all-jobs\b|overview\b|life\b|life-at-|why-|how-we-hire\b|hiring-process\b|faq\b|diversity\b|inclusion\b|blog\b|news\b|event(s)?\b|program(s)?\b|internship(s)?\b)[^\/?#]{4,}|\/o\/[a-zA-Z0-9-]+|\/role\/[a-zA-Z0-9-]+|\/(?:jobangebote|jobangebot|stellenangebote|stellenangebot|stellenanzeigen|stellenanzeige|stellen|job-offers|job-offer|offre|offres|offre-de-emploi|offre-d-emploi|offres-d-emploi|annonce|annonces|offerta-di-lavoro|offerte-di-lavoro|offerta|offerte|oferta-de-empleo|ofertas-de-empleo|oferta-de-trabajo|ofertas-de-trabajo|oferta-de-trabalho|ofertas-de-trabalho|emploi|emplois|vacature|vacatures|vaga|vagas|ofertas|puesto|posiciones?|posizione|posizioni|lavoro|praca|tyopaikka|tyomahdollisuus|stilling|stillinger|jobb|jobber|lediga-jobb|lediga-tjaenster|lediga-tjänster|werkenbij|werken-bij|karriere|carriere|carrières?|opportunit[aà])\/[^/?#]+|\/position|\/vacancy|\/vacancies|\/opening|\/role|\/requisition|\/posting|\/apply|\/stelle|\/work-with-us\/[^\/?#]+|intervieweb|arca24|inrecruiting|altamiraweb|detail|jobid|job_id|gh_jid|reqid|requisition|posting|lever\.co|greenhouse\.io|personio|workable|smartrecruiters|teamtailor|ashby|workdayjobs|successfactors|phenompeople|eightfold|deel\.com\/job-boards)/i;
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
// European employer boards often put the *listing* under /jobs/<localized-slug>
// and the detail pages under /jobangebote/<slug>, /stellenangebote/<slug>, etc.
// Never let the listing slug itself pass the generic /jobs/<slug> rule.
const listingPathRe = /\/(?:jobs?|careers?)\/(?:all[-_]?jobs?|open[-_]?jobs?|open[-_]?positions?|current[-_]?openings?|jobangebote|jobangebot|offene[-_]?jobangebote|offene[-_]?stellen(?:angebote)?|stellenangebote|stellenanzeigen|vacancies|vacature(?:s)?|offres?(?:-d-emploi)?|offres-d-emploi|offerte(?:-di-lavoro)?|offerte-di-lavoro|posizioni-aperte|annunci(?:-di-lavoro)?|ofertas?(?:-de-empleo|-de-trabajo)?|empleos?|puestos?)(?:[/?#]|$)/i;
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
    if (listingPathRe.test(href)) return false;
    // Keep the DOM gate aligned with the Python validator: a detail page can
    // be identified by its path vocabulary OR a job/requisition query id.
    // Many European employer sites use URLs such as /view?job=12345 and
    // /jobangebote/<slug>, neither of which belonged to the old path filter.
    return jobUrlRe.test(href) || hasJobQuery(href);
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
        '[itemprop="title" i]',
        '[data-title]', '[data-job-title]', '[data-position-title]',
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
// FIX P17 (2026-10-04): how many DISTINCT jobs does this element contain?
// A container that holds two or more different job links is a LIST, not a
// card, and its text must never be attributed to a single job. LOOP proved
// the cost: the whole jobs list (20 links, perks copy and all) became the
// "card context" of every row, so "Account & Project Lead" was written as
// Job Type "Internship / Hybrid" (from the sibling /careers/internship
// card -- that JD contains the word 0 times) and Experience "1 years"
// (from "a 4-day workweek after one year with us").
const distinctJobLinkCount = (el) => {
    if (!el) return 0;
    const keys = new Set();
    for (const a of querySelectorAllDeep('a[href]', el)) {
        if (!a || !looksJobUrl(a.href)) continue;
        let k = String(a.href).split('#')[0].split('?')[0]
            .replace(/\/(apply|application|bewerben|postuler|candidatura)\/?$/i, '')
            .replace(/\/+$/, '');
        keys.add(k.toLowerCase());
        if (keys.size > 2) return keys.size;
    }
    return keys.size;
};
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
                // FIX P17: a scope holding more than one distinct job is a
                // list container, not this job's card.
                if (distinctJobLinkCount(s) > 1) {
                    continue;
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
        // FIX P17: same rule on the generic upward walk -- stop climbing the
        // moment the element covers a second job.
        if (distinctJobLinkCount(p) > 1) break;
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

    REBASE 2026-10-03: _org read /proc/meminfo and cgroup files only, so on
    Windows -- the only platform this app ships on -- it always returned None
    and recommended_workers() fell back to its blind ``by_ram = 2``. The
    Windows branch (GlobalMemoryStatusEx) is lifted from
    sponsorscout.scanning.common.host_workers_limits so the sizing is finally
    based on a real number. POSIX behaviour is unchanged.
    """
    import os as _os
    if _os.name == "nt":
        try:
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullAvailPhys) / (1024.0 * 1024.0)
        except Exception:
            return None
        return None

    avail = None
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) / 1024.0
                    break
    except Exception:
        avail = None
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
            page = _os.sysconf("SC_AVPHYS_PAGES")
            size = _os.sysconf("SC_PAGE_SIZE")
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
        n = n if requested is None else max(1, min(requested, n))
        return _apply_worker_override(kind, n)

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
    return _apply_worker_override(kind, n)


# REBASE 2026-10-03 (R3): both sizers only ever clamp DOWNWARD, so a user who
# knows their machine could not raise the pool -- on a box reading under ~6 GiB
# available the career crawl ran one browser worker and the full-seed scan took
# three times longer than it needed to. This knob is the only way to go above
# the heuristic, it is opt-in, and it is logged in the run header.
def _apply_worker_override(kind, computed):
    raw = os.environ.get("SPONSORSCOUT_MAX_WORKERS" if kind == "browser"
                         else "SPONSORSCOUT_HTTP_WORKERS")
    if not raw:
        return computed
    try:
        want = int(str(raw).strip())
    except (TypeError, ValueError):
        return computed
    if want < 1:
        return computed
    # Hard ceiling stays: 8 Chromium instances will thrash any laptop.
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
# FIX P0-29b: BROWSER_ARGS was referenced at two detail-enrichment launch
# sites (see below) but never defined anywhere in this file — an unguarded
# NameError that would abort detail enrichment the first time it ran.
# It is now an alias of the low-resource arg list.
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

# Back-compat alias for pre-existing call sites.
BROWSER_ARGS = LOW_RESOURCE_BROWSER_ARGS

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
        # W3-4: the detail-path vocabulary was English-only, so a perfectly
        # real non-English job URL was quarantined as
        # "invalid_or_application_only_url" -- Decathlon Italia's
        # /it/annonce/<id>-<slug> is 167 such rows in one run.
        r"(/(?:annonce|annunci|offerta|offerte|offerta-di-lavoro|offerte-di-lavoro|"
        r"jobangebote|jobangebot|job-offers|job-offer|stellenangebote|stellenangebot|stellenanzeigen|"
        r"stellenanzeige|vacature|vacatures|vaga|vagas|oferta|ofertas|oferta-de-empleo|"
        r"ofertas-de-empleo|oferta-de-trabajo|ofertas-de-trabajo|oferta-de-trabalho|"
        r"ofertas-de-trabalho|empleo|emploi|emplois|offre|offre-de-emploi|offre-d-emploi|"
        r"offres-d-emploi|puesto|posizione|posizioni|lavoro|praca|emprego|empregos|vacante|vacantes|tyopaikka|"
        r"tyomahdollisuus|stilling|stillinger|jobb|jobber|lediga-jobb|lediga-tjaenster|lediga-tjänster|"
        r"werkenbij|werken-bij|karriere|carriere)/[^/?#]+|"
        r"/job(s)?/[^/?#]+|/career(s)?/.*(job|position|opening|vacanc|role)|"
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
        "A2G Technologies": "Eindhoven, Netherlands",
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
        "Harnham Germany": "Berlin, Germany",
        "Hays Germany": "Mannheim, Germany",
        "HelloFresh": "Berlin, Germany",
        "Highsnobiety": "Berlin, Germany",
        "HubSpot": "Cambridge, USA",
        "Huxley Netherlands": "Amsterdam, Netherlands",
        "Ikea Italia": "Milan, Italy",
        "ING": "Amsterdam, Netherlands",
        "Intesa Sanpaolo": "Turin, Italy",
        "Kaufland e-com": "Cologne, Germany",
        "Kelly Services Germany": "Frankfurt, Germany",
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
        "Nexthink (Germany)": "Frankfurt, Germany",
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
        "Talentor Germany": "Munich, Germany",
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
    # Browser detail visits are enrichment fallbacks, not a second listing crawl.
    # Keep them deliberately small because the HTTP pass already reads most
    # plain-HMTL detail pages in parallel.
    DETAIL_BROWSER_TIMEOUT_MS = 8000
    DETAIL_BROWSER_FALLBACK_PER_COMPANY = 12
    DETAIL_BROWSER_HOST_FAILURES = 2
    DETAIL_SCAN_TIME_BUDGET_SEC = 480   # max seconds one company's detail scan may run (8 min)
    MAIN_HEARTBEAT_SEC = 30             # main thread prints how many companies are still running
    MAX_STALL_SEC = 600             # abort ONLY when NO page-level ACTIVITY anywhere for 10 min
                                    # (queued companies waiting for a worker slot are NOT a hang)
    # FIX P0-51 (S-10): these are a RUNTIME BUDGET, not a correctness
    # setting, and they were the real reason so many cells read "?".
    # American Express returns ~2,040 rows; at 120 the detail scan reached
    # 6% of them, so the other 94% never had a JD fetched and Experience /
    # Sponsor / Blue Card / Reloc could only ever fall back to inference
    # from the job TITLE. Proof: running extract_experience("", title) on
    # the exact on-screen titles reproduces the screenshot exactly -- every
    # row showing a value has a level word in its title ("Senior Manager",
    # "Director", "Apprentice", "Graduate", all source=title_inference) and
    # every "?" row has none ("Software Engineer III", "Manager - Data
    # Analytics", "Analyst-Compliance"). Nothing was being read from the
    # JDs, so the extractor regexes were never at fault.
    #
    # Raised, and now overridable without editing this file. Raising them
    # costs wall-clock time directly -- this trades against the 35-45 min
    # target, so the numbers are yours to set:
    #     set SPONSORSCOUT_DETAIL_PER_COMPANY=1000
    #     set SPONSORSCOUT_DETAIL_TOTAL=12000
    # Note the per-company scan is ALSO bounded by
    # DETAIL_SCAN_TIME_BUDGET_SEC (8 min), which keeps one huge board from
    # eating the whole run however high this is set.
    MAX_DETAIL_SCAN_PER_COMPANY = max(1, int(
        os.environ.get("SPONSORSCOUT_DETAIL_PER_COMPANY") or 300))
    MAX_DETAIL_SCAN_TOTAL = max(1, int(
        os.environ.get("SPONSORSCOUT_DETAIL_TOTAL") or 20000))
    # FIX W2-6: the global cap above used to be 5000 and it was enforced with
    # a SILENT `break`.  Run 20261003T233023 exhausted it mid-crawl, so the 22
    # companies that happened to be scanned late (Celonis 221 rows, Bolt 214,
    # Decathlon 167) got ZERO detail pages and reported Unknown for every
    # verdict -- indistinguishable from "the extractor failed".  The cap is
    # raised, it is announced when it trips, and DETAIL_MIN_PER_COMPANY below
    # reserves a floor for every company so late companies are never starved.
    DETAIL_MIN_PER_COMPANY = max(0, int(
        os.environ.get("SPONSORSCOUT_DETAIL_FLOOR") or 40))

    # ── WAVE 1 (2026-10-04): runtime budgets ────────────────────────────
    # Run 20261003T233023 spent 5h32m on 208 of 370 companies, effectively
    # serial, and 58 of those minutes went to 97 companies that produced
    # nothing at all.  These three knobs bound that.
    LIST_TIME_BUDGET_SEC = max(30, int(          # per-company LIST phase
        os.environ.get("SPONSORSCOUT_LIST_BUDGET") or 180))
    DEAD_END_ABORT_SEC = max(10, int(            # give up on a dead board
        os.environ.get("SPONSORSCOUT_DEAD_END_SEC") or 45))
    REPEAT_PAGE_RATIO = 0.10      # <=10% new rows on a page == a repeat page
    REPEAT_PAGES_STOP = 2         # stop after N consecutive repeat pages
    # ── WAVE 1: provider sniffing for provider=auto seeds ───────────────
    PROVIDER_SNIFF = (os.environ.get("SPONSORSCOUT_PROVIDER_SNIFF", "1")
                      .strip().lower() not in ("0", "false", "no"))
    PROVIDER_SNIFF_TIMEOUT_SEC = 12
    PROVIDER_CACHE_TTL_DAYS = 14

# ───────────── JD SUPPORT DETECTOR (shared) ─────────────
# Single source of truth moved to sponsorscout.scanning.jd_support.
# MANDATORY (rebase 2026-10-03, user decision): no standalone fallback.
# _org shipped a second, older copy of this classifier behind an
# `except ImportError`; its own header records the run where all 12,298 rows
# came back "Unknown" because that copy loaded instead of the real module.
# A loud ImportError is better than a silent downgrade.
# App/UI integration: Stop and Pause must take effect between pages, not
# only at the 15-minute MAX_COMPANY_TIME_SEC cutoff.
from sponsorscout.scanning.common import (
    ScanCancelled,
    check_cancelled,
    check_control,
    sleep_interruptible,
)

from sponsorscout.scanning.jd_support import (
        JDSupportDetector,
        VERDICT_YES,
        VERDICT_NO,
        # FIX P0-50 (S-01): VERDICT_UNKNOWN was never imported here, which is
        # the mechanical reason the career scanner could not downgrade a
        # false-positive verdict even in principle. ats_scanner.py imported
        # it and ran five guards; this file ran none.
        VERDICT_UNKNOWN,
        detect_blue_card,
    )
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


class _DeadEndBoard(Exception):
    """Internal signal: this board renders no job cards at all (FIX W1-2)."""


class _DetailHttpOnly(Exception):
    """Internal signal: detail enrichment ran over HTTP, no browser needed."""


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


class _StaticAnchorParser(HTMLParser):
    """Small, dependency-free anchor parser for server-rendered boards."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self._anchor = None
        self._tag_stack = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "a" and self._anchor is None:
            attrs_map = {str(k).lower(): (v or "") for k, v in attrs}
            self._anchor = {"href": attrs_map.get("href", ""), "parts": []}
        if self._anchor is not None:
            self._tag_stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag.lower() != "a":
            return
        attrs_map = {str(k).lower(): (v or "") for k, v in attrs}
        self.items.append((attrs_map.get("href", ""), ""))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._anchor is None:
            return
        # The anchor is complete when its own closing tag arrives.
        if tag == "a":
            self.items.append((self._anchor["href"], "".join(self._anchor["parts"])))
            self._anchor = None
            self._tag_stack = []
            return
        # Pop the most recent nested tag when possible.
        for i in range(len(self._tag_stack) - 1, -1, -1):
            if self._tag_stack[i] == tag:
                del self._tag_stack[i:]
                break

    def handle_data(self, data):
        if self._anchor is not None:
            self._anchor["parts"].append(data)

    def handle_entityref(self, name):
        if self._anchor is not None:
            self._anchor["parts"].append(unescape(f"&{name};"))

    def handle_charref(self, name):
        if self._anchor is not None:
            self._anchor["parts"].append(unescape(f"&#{name};"))


def _parse_static_anchors(html):
    parser = _StaticAnchorParser()
    try:
        parser.feed(html or "")
        parser.close()
        if parser.items:
            return parser.items
    except Exception:
        pass
    return _STATIC_ANCHOR_RE.findall(html or "")


def _static_strip_tags(fragment):
    """Inner HTML of an anchor -> visible text."""
    if not fragment:
        return ""
    txt = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


# ── FIX P28 (2026-10-05): mine the page's own embedded JSON ────────────────
# `_STATIC_SPA_MARKERS` made the static path bail the moment it saw
# `__NEXT_DATA__`, handing the company to the browser. But that script tag
# IS the job list, already parsed and typed, and reading it needs no browser
# at all. jobs.bendingspoons.com ships all 47 postings in
# props.pageProps.list with jobTitle / officeLocations / workSchedule /
# typesOfContract / the full description; the run that discarded it accepted
# ONE job and quarantined 105 with Location Source=none.
#
# Deliberately conservative: a record is only emitted when a REAL url can be
# built from the data (an explicit url/href/slug field, or an id that appears
# in one of the page's own anchors). Records without one are returned
# separately as an enrichment index keyed by title, so the browser pass can
# fill in the location it could not see. Nothing is invented.

_EMBED_SCRIPT_RE = re.compile(
    r'<script[^>]*\bid="(?:__NEXT_DATA__|__NUXT_DATA__)"[^>]*>(.*?)</script>'
    r'|<script[^>]*\btype="application/json"[^>]*>(.*?)</script>'
    r'|window\.__(?:NUXT|INITIAL_STATE|APOLLO_STATE|PRELOADED_STATE)__\s*=\s*'
    r'(\{.*?\})\s*[;<]',
    re.S | re.I)

_EMBED_TITLE_KEYS = ("jobTitle", "positionTitle", "roleTitle", "vacancyTitle",
                     "title", "name", "label", "displayName")
_EMBED_URL_KEYS = ("url", "jobUrl", "applyUrl", "absoluteUrl", "externalUrl",
                   "externalPath", "path", "link", "permalink", "canonicalUrl",
                   "canonicalPositionUrl", "jobPostingUrl", "detailUrl",
                   "href", "slug")
_EMBED_ID_KEYS = ("jobId", "jobID", "requisitionId", "reqId", "postingId",
                  "positionId", "id", "uuid")
_EMBED_LOC_KEYS = ("location", "locations", "officeLocations", "jobLocations",
                   "workLocations", "offices", "locationName", "city",
                   "cities", "office", "workplace", "place", "places",
                   "country", "region")
_EMBED_JOBISH_RE = re.compile(
    r"job|posting|position|requisition|vacanc|opening|role", re.I)
_EMBED_ROLE_RE = re.compile(
    r"\b(engineer|developer|manager|analyst|scientist|specialist|consultant|"
    r"architect|designer|director|lead|head|principal|senior|junior|intern|"
    r"trainee|associate|advisor|officer|administrator|recruiter|counsel|"
    r"lawyer|accountant|controller|planner|coordinator|assistant|bookkeeper|"
    r"representative|agent|technician|mechanic|operator|expert|owner|"
    r"sales|marketing|finance|security|devops|frontend|backend|software|"
    r"data|qa|quality|addetto|commesso|cuoco|operaio|impiegat|venditore|"
    r"responsabile|tecnico|magazzinier|cassier)\b", re.I)


def _embed_loc_string(loc, depth=0):
    """Flatten a location value of any shape into a readable string."""
    if loc is None or depth > 3:
        return ""
    if isinstance(loc, str):
        return loc.strip()
    if isinstance(loc, (list, tuple)):
        out = []
        for item in list(loc)[:12]:
            piece = _embed_loc_string(item, depth + 1)
            if piece and piece not in out:
                out.append(piece)
        return "; ".join(out)
    if isinstance(loc, dict):
        head = next((str(loc[k]).strip() for k in
                     ("title", "name", "displayName", "label", "locationName",
                      "fullName", "text", "value", "city", "town", "office",
                      "addressLocality")
                     if isinstance(loc.get(k), str) and loc.get(k).strip()), "")
        tail = next((str(loc[k]).strip() for k in
                     ("region", "state", "province", "addressRegion")
                     if isinstance(loc.get(k), str) and loc.get(k).strip()), "")
        country = loc.get("country")
        if isinstance(country, dict):
            country = (country.get("name") or country.get("title")
                       or country.get("displayName") or country.get("isoCode")
                       or country.get("code") or "")
        if not isinstance(country, str):
            country = ""
        country = country.strip()
        # "Milan (Italy)" + country "Italy" must not become
        # "Milan (Italy), Italy" -- the P29 class of defect, one layer up.
        blob = " ".join((head, tail)).casefold()
        if country and country.casefold() in blob:
            country = ""
        parts = [p for p in (head, tail, country) if p]
        return ", ".join(parts)
    return ""


def _embed_walk(node, out, depth=0):
    """Collect job-shaped dicts out of an arbitrary JSON tree."""
    if depth > 9 or len(out) > 2000:
        return
    if isinstance(node, list):
        for item in node[:500]:
            _embed_walk(item, out, depth + 1)
        return
    if not isinstance(node, dict):
        return
    keyblob = " ".join(node.keys())
    title = next((node[k] for k in _EMBED_TITLE_KEYS
                  if isinstance(node.get(k), str) and node[k].strip()), "")
    url = next((node[k] for k in _EMBED_URL_KEYS
                if isinstance(node.get(k), str) and node[k].strip()), "")
    ident = next((str(node[k]) for k in _EMBED_ID_KEYS
                  if isinstance(node.get(k), (str, int)) and str(node[k]).strip()), "")
    if title and (url or ident) and _EMBED_JOBISH_RE.search(keyblob):
        loc = next((node[k] for k in _EMBED_LOC_KEYS if node.get(k)), None)
        desc_parts = [node.get(k) for k in
                      ("highLevelDescription", "description", "jobDescription",
                       "content", "body", "responsibilities", "requirements")
                      if node.get(k)]
        ctx_parts = [str(node.get(k)) for k in
                     ("workSchedule", "typesOfContract", "employmentType",
                      "contractType", "typesOfWork", "department", "team",
                      "workplaceType", "remote", "availableAsRemoteInDefaultCountries")
                     if node.get(k) not in (None, "", [], {})]
        out.append({
            "title": title.strip(),
            "url": url.strip(),
            "id": ident.strip(),
            "location": _embed_loc_string(loc),
            "card_context": " | ".join(ctx_parts)[:800],
            "jd_text": _jd_plain(" ".join(
                x if isinstance(x, str) else " ".join(map(str, x))
                for x in desc_parts))[:20000] if desc_parts else "",
        })
    for value in node.values():
        if isinstance(value, (dict, list)):
            _embed_walk(value, out, depth + 1)


def extract_embedded_json_jobs(html, base_url, url_validator=None,
                               title_validator=None):
    """Return ``(jobs, index)`` mined from the page's embedded JSON.

    ``jobs`` are complete rows with a REAL url, ready to use without a
    browser. ``index`` maps a normalised title to the same metadata for every
    record whose url could not be established, so a later DOM pass can borrow
    the location instead of quarantining the row.
    """
    records = []
    for match in _EMBED_SCRIPT_RE.finditer(html or ""):
        blob = next((g for g in match.groups() if g), "")
        blob = blob.strip()
        if not blob or len(blob) > 12_000_000 or blob[0] not in "{[":
            continue
        try:
            data = json.loads(blob)
        except Exception:
            continue
        try:
            _embed_walk(data, records)
        except Exception:
            continue
    if not records:
        return [], {}
    anchors = set(re.findall(r'href="([^"]+)"', html or ""))
    jobs, index, seen = [], {}, set()
    for rec in records:
        title = rec["title"]
        if len(title) < 4 or len(title) > 150 or not _EMBED_ROLE_RE.search(title):
            continue
        url = rec["url"]
        if url and not url.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
            absolute = urljoin(base_url, unescape(url))
        else:
            absolute = ""
            if rec["id"]:
                hit = next((h for h in anchors if rec["id"] in h), "")
                if hit:
                    absolute = urljoin(base_url, unescape(hit))
        key = (title.casefold(), absolute.casefold())
        if key in seen:
            continue
        seen.add(key)
        payload = {
            "job_title": title,
            "job_url": absolute,
            "location_hint": rec["location"],
            "card_context": rec["card_context"],
            "jd_text": rec["jd_text"],
            "extraction_method": "embedded_json",
        }
        if absolute:
            if url_validator is not None and not url_validator(absolute):
                continue
            if title_validator is not None and not title_validator(title):
                continue
            jobs.append(payload)
        else:
            index.setdefault(_embed_title_key(title), payload)
    return jobs, index


def _embed_title_key(title):
    return re.sub(r"[^a-z0-9]+", " ", (title or "").casefold()).strip()


#: Per-seed metadata mined from embedded JSON when no url could be built.
#: Consulted by the DOM path so a client-rendered board still gets its
#: locations. Keyed by seed url; bounded so a long run cannot grow it.
_EMBEDDED_INDEX = {}
_EMBEDDED_INDEX_MAX = 200


def _static_accept_language(seed_url):
    """Local-language-first Accept-Language for the raw HTTP fast path."""
    host = (urlparse(seed_url or "").hostname or "").lower()
    path = (urlparse(seed_url or "").path or "").lower()
    pairs = []
    if re.search(r"(?:^|[./_-])(it)(?:[-_][a-z]{2})?(?:[./_-]|$)", path + "/" + host):
        pairs = ["it-IT", "it", "en-US", "en"]
    elif re.search(r"(?:^|[./_-])(de)(?:[-_][a-z]{2})?(?:[./_-]|$)", path + "/" + host):
        pairs = ["de-DE", "de", "en-US", "en"]
    elif re.search(r"(?:^|[./_-])(nl)(?:[-_][a-z]{2})?(?:[./_-]|$)", path + "/" + host):
        pairs = ["nl-NL", "nl", "en-US", "en"]
    elif re.search(r"(?:^|[./_-])(fr)(?:[-_][a-z]{2})?(?:[./_-]|$)", path + "/" + host):
        pairs = ["fr-FR", "fr", "en-US", "en"]
    elif host.endswith(".it"):
        pairs = ["it-IT", "it", "en-US", "en"]
    elif host.endswith((".de", ".at", ".ch")):
        pairs = ["de-DE", "de", "en-US", "en"]
    elif host.endswith((".nl", ".be")):
        pairs = ["nl-NL", "nl", "fr-BE", "fr", "en-US", "en"]
    elif host.endswith(".fr"):
        pairs = ["fr-FR", "fr", "en-US", "en"]
    else:
        pairs = ["en-US", "en", "de", "it", "fr", "nl"]
    weights = []
    for i, lang in enumerate(pairs[:6]):
        q = max(0.55, 1.0 - i * 0.08)
        weights.append(lang if i == 0 else f"{lang};q={q:.2f}")
    return ",".join(weights)


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
            "Accept-Language": _static_accept_language(seed_url),
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

    # Hydration shell. FIX P28: before handing the company to the browser,
    # read the hydration payload itself -- it is the job list, already
    # structured. Only rows with a real url are returned; everything else is
    # parked in _EMBEDDED_INDEX for the DOM pass to borrow locations from.
    for _marker in _STATIC_SPA_MARKERS:
        if _marker in html:
            try:
                _emb_jobs, _emb_index = extract_embedded_json_jobs(
                    html, seed_url, url_validator, title_validator)
            except Exception as _exc:
                _emb_jobs, _emb_index = [], {}
                print(f"      embedded-json miner failed: "
                      f"{type(_exc).__name__}: {_exc}")
            if _emb_index and len(_EMBEDDED_INDEX) < _EMBEDDED_INDEX_MAX:
                _EMBEDDED_INDEX[seed_url] = _emb_index
            if len(_emb_jobs) >= min_jobs:
                return _emb_jobs, (f"embedded JSON ({_marker}): "
                                   f"{len(_emb_jobs)}")
            _extra = ""
            if _emb_index:
                _extra = (f"; {len(_emb_index)} record(s) cached for the "
                          f"DOM pass")
            return [], (f"static: client-rendered ({_marker}); "
                        f"using browser{_extra}")

    jobs = []
    seen = set()
    for href, inner in _parse_static_anchors(html):
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


# ── FIX P37 (2026-10-06): a COMPANY NAME is not a town ────────────────────
# _town_from_address() reads "<Town> , Via della Tollegna 1". On Oracle Cloud
# HCM boards the line is "Unipol Assicurazioni S.p.A , Via Stalingrado 45",
# so the EMPLOYER was captured as the town and written to Job Location with
# Location Source=address, which _location_confidence rates **high**. Run
# 20261006T211320 published 28 such rows -- "Unipol Assicurazioni S.p.A",
# "Unipol Rental S.p.A", "SIAT", "UNA Italian Hospitality" and even
# "ANDAMENTO UniSalute" (a heading meaning "UniSalute TREND") -- every one
# of them confidently wrong and yielding no country.
#
# The value cannot simply be pushed through _sanitize_job_location(): that
# gate rejects any place the gazetteer does not list, and this extractor
# exists precisely to recover Cittaducale, Spilamberto, Vicolungo and
# Serravalle Scrivia. So the ORGANISATION shapes are named instead.
_ORG_LEGAL_FORM_RE = re.compile(
    r"(?i)(?:^|[\s,.])(?:s\.?p\.?a|s\.?r\.?l|s\.?n\.?c|s\.?a\.?s|"
    r"gmbh|mbh|ag|kg|ohg|e\.?v|ug|gbr|b\.?v|n\.?v|v\.?o\.?f|"
    r"ltd|limited|plc|llp|llc|inc|corp|co\.|&\s*co|"
    r"s\.?a|s\.?l|s\.?a\.?s\.?u|sarl|sas|sasu|eurl|"
    r"oy|ab|a\/s|aps|as|nv|zrt|kft|sp\.?\s*z\s*o\.?o|d\.?o\.?o"
    r")(?:[\s,.]|$)")
#: Corporate / editorial nouns that never name a place on their own.
_ORG_WORD_RE = re.compile(
    r"(?i)\b(?:assicurazion\w+|rental|holding\w*|group|gruppo|hospitality|"
    r"bank\w*|banca|insurance|consulting|solutions?|services?|servizi|"
    r"technolog\w+|systems?|partners?|ventures?|capital|finance|finanz\w+|"
    r"industr\w+|logistic\w*|retail|energia|energy|immobiliare|"
    r"andamento|risultati|bilancio|fatturato|utile|ricavi|"
    r"trend|results?|revenue|overview|careers?|carriere)\b")

#: FIX P42b: titles that belong to a CAREER SITE rather than to a vacancy.
_PAGE_FURNITURE_TITLE_RE = re.compile(
    r"(?i)\b(?:external\s+career\s+site|career\s+site|careers?\s+(?:home|page|portal)"
    r"|job\s+search|search\s+jobs?|job\s+listing|all\s+jobs?|current\s+(?:openings?|vacancies)"
    r"|lavora\s+con\s+noi|offerte\s+di\s+lavoro|posizioni\s+aperte"
    r"|stellenangebote|karriere(?:seite|portal)?|vacatures|offres\s+d'emploi"
    r"|page\s+not\s+found|access\s+denied|sign\s+in|log\s+in)\b")

# ── FIX P38 (2026-10-06): ATS furniture published as vacancies ────────────
# Run 20261006T211320 accepted 8 rows that are not jobs: "Recupero Password"
# (.../app.php), "[email protected]" (/cdn-cgi/l/email-protection), plus
# "Realizzato da SuccessFactors" and "Sistema di tracking del candidato di
# Teamtailor" -- footer credits pointing at the ATS VENDOR'S OWN marketing
# site rather than the employer. A vendor root domain with no tenant in the
# path can never be a specific vacancy.
_ATS_VENDOR_HOME_RE = re.compile(
    r"(?i)^(?:www\.)?(?:successfactors|teamtailor|greenhouse|lever|workable|"
    r"recruitee|personio|smartrecruiters|bamboohr|jobvite|icims|taleo|"
    r"workday|myworkday|ashbyhq|breezy|softgarden|heyjobs|factorialhr|"
    r"intervieweb|altamirahrm|allibo|oraclecloud|avature|eightfold)"
    r"\.(?:com|io|co|it|de|net|org)$")
#: Session / utility endpoints every ATS ships.
#
# REGRESSION FIX (run 20261006T225923): the first version of this pattern
# ended each alternative with (?:/|$|\?), so "index.php" matched in the
# MIDDLE of a path. ferrerocareers.com routes through PHP path-info --
# /int/index.php/en/jobs/warehouse -- and 56 genuine Ferrero vacancies were
# rejected as site furniture. A script name is only an endpoint when it is
# the LAST segment; anything after it is a route, not a utility page.
_SITE_UTILITY_FILE_RE = re.compile(
    r"(?i)(?:^|/)(?:app|index|login|signin|logout|default|error|search)"
    r"\.(?:php|aspx?|jsp|cgi)(?:$|\?|#)")
_SITE_UTILITY_PATH_RE = re.compile(
    r"(?i)(?:^|/)(?:recupero[-_]?password|"
    r"password[-_]?(?:reset|recovery|dimenticata)|"
    r"cdn-cgi/)")


def is_ats_furniture_url(url):
    """True for ATS vendor home pages and session/utility endpoints."""
    try:
        parts = urlparse(url or "")
    except Exception:
        return False
    host = (parts.hostname or "").lower()
    path = parts.path or ""
    if _SITE_UTILITY_FILE_RE.search(path) or _SITE_UTILITY_PATH_RE.search(path):
        return True
    if _ATS_VENDOR_HOME_RE.match(host) and len(
            [p for p in path.split("/") if p]) == 0:
        return True
    return False

# ── FIX P31/P32/P33 (2026-10-06): link shapes the URL validator misread ────
# All three were found on Austrian seeds that reported 0 or junk rows while
# the portal plainly listed jobs.

#: P31 -- an employer whose careers page is hosted by a third-party ATS on a
#: DIFFERENT domain. journiapp.com/sk/careers links out to
#: careers.kula.ai/journi/35169; JOB_URL_PATTERN has no token for
#: "/<tenant>/<id>", so all 6 of Journi's jobs were rejected and the company
#: reported EMPTY. Matching on the HOST is what makes "/journi/35169"
#: unambiguous -- these domains serve nothing but job detail pages.
_ATS_JOB_HOST_RE = re.compile(
    r"(?:^|\.)(?:"
    r"kula\.ai|join\.com|recruitee\.com|teamtailor\.com|workable\.com|"
    r"lever\.co|greenhouse\.io|ashbyhq\.com|smartrecruiters\.com|"
    r"personio\.de|jobs\.personio\.com|bamboohr\.com|breezy\.hr|"
    r"jobvite\.com|softgarden\.io|heyjobs\.co|factorialhr\.com|"
    r"applytojob\.com|careers-page\.com|homerun\.co|pinpointhq\.com|"
    r"hibob\.com|rippling\.com|polymer\.co|jazzhr\.com|workizard\.com|"
    r"myworkdayjobs\.com|icims\.com|taleo\.net|successfactors\.com|"
    r"avature\.net|eightfold\.ai|phenompeople\.com|oraclecloud\.com"
    r")$", re.I)

#: A host that exists to serve job pages: careers.x.com, jobs.x.com, ...
_JOB_SUBDOMAIN_RE = re.compile(
    r"^(?:careers?|jobs?|apply|recruiting|recruitment|vacancies|hiring|work)\.",
    re.I)

#: An opaque record id: 35169, 44bdf359cd980a79973ba9119532279e, a UUID.
_OPAQUE_ID_SEG_RE = re.compile(
    r"^(?:[0-9]{3,}|[0-9a-f]{8,}|[0-9a-f-]{20,}|[A-Za-z0-9_-]*\d[A-Za-z0-9_-]{5,})$",
    re.I)

#: P32 -- aggregators that hide the employer behind a redirector.
#: englishjobsearch.at lists 15 real jobs, every one of them behind
#: /clickout/<hash> or /clickout_alt/<hash>, with the title in the anchor
#: text. No path token matched, so the seed reported 0 jobs written.
_REDIRECT_JOB_PATH_RE = re.compile(
    r"/(?:clickout|clickout_alt|click|out|goto|go|redirect|redir|jump|"
    r"track|tracking|visit|away|link|apply-redirect|r)/"
    r"([A-Za-z0-9][A-Za-z0-9_-]{5,})/?$", re.I)

#: P33 -- a department / function / place CHIP sitting under /jobs/.
#: jobsinvienna.com's static HTML holds NO job-detail links at all, only
#: /jobs/Sales-and-Sales-Related, /jobs/Marketing, /jobs/IT, /jobs/AT-Vienna,
#: /jobs/PROFESSIONAL-SCIENTIFIC-AND-TECHNICAL-ACTIVITIES. Every one passed
#: JOB_URL_PATTERN (which accepts /jobs/<anything>) and the old
#: CATEGORY_PATH_INDICATORS only caught the /jobs/categories/<x> shape, so a
#: run wrote 13 chips as if they were jobs -- and then timed out trying to
#: open a category page as a job detail.
_DEPT_WORDS = (
    r"sales|marketing|it|ict|tech|technology|technologies|telekommunikation|"
    r"finance|financial|accounting|banking|banken|versicherungen|insurance|"
    r"hr|human|resources|personal|recruiting|legal|compliance|audit|"
    r"administration|admin|office|operations|logistics|supply|chain|"
    r"procurement|purchasing|production|manufacturing|engineering|"
    r"construction|design|creative|media|communications|communication|"
    r"public|relations|press|customer|support|service|services|helpdesk|"
    r"research|development|science|scientific|healthcare|medical|health|"
    r"pharma|nursing|education|teaching|training|hospitality|tourism|travel|"
    r"retail|consulting|management|quality|regulatory|affairs|security|"
    r"data|analytics|product|project|business|general|other|various|"
    r"professional|technical|activities|and|或|und|et|e|y|of|the|for|amp"
)
_DEPT_SEGMENT_RE = re.compile(
    r"^(?:" + _DEPT_WORDS + r")(?:[-_+%20 &]+(?:" + _DEPT_WORDS + r"))*$", re.I)
#: "AT-Vienna", "DE-Berlin" -- a country/city facet chip.
_GEO_FACET_SEG_RE = re.compile(r"^[A-Z]{2}[-_][A-Za-z][A-Za-z-]{2,}$")
#: A word that only ever appears in a ROLE, never in a department chip. It is
#: the escape hatch for boards that publish Title-Cased job slugs with no id
#: (/jobs/Senior-Software-Engineer must stay a job).
_ROLE_WORD_RE = re.compile(
    r"\b(?:engineer|developer|programmer|manager|analyst|scientist|architect|"
    r"designer|consultant|specialist|advisor|officer|director|head|lead|chief|"
    r"president|principal|senior|junior|graduate|intern|trainee|apprentice|"
    r"associate|assistant|coordinator|administrator|executive|representative|"
    r"agent|recruiter|accountant|controller|auditor|counsel|lawyer|paralegal|"
    r"technician|mechanic|electrician|operator|driver|chef|cook|waiter|nurse|"
    r"doctor|physician|pharmacist|teacher|tutor|professor|researcher|editor|"
    r"writer|copywriter|translator|planner|buyer|cashier|clerk|secretary|"
    r"receptionist|supervisor|foreman|warehouse|picker|packer|installer|"
    r"welder|plumber|carpenter|painter|cleaner|guard|stylist|therapist|"
    r"addetto|impiegato|operaio|responsabile|tecnico|commesso|venditore|"
    r"mitarbeiter|leiter|berater|techniker|verkaeufer|ingenieur|praktikant)\b",
    re.I)


def _url_last_segment(url):
    try:
        path = urlparse(url).path.rstrip("/")
    except Exception:
        return ""
    return path.rsplit("/", 1)[-1] if path else ""


def is_ats_detail_url(url):
    """P31: a job detail page on a third-party ATS / dedicated careers host."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    if not (_ATS_JOB_HOST_RE.search(host) or _JOB_SUBDOMAIN_RE.search(host)):
        return False
    segs = [x for x in urlparse(url).path.split("/") if x]
    # Needs a tenant AND an opaque record id, so the board's own landing page
    # (careers.kula.ai/journi) is still not mistaken for a job.
    return len(segs) >= 2 and bool(_OPAQUE_ID_SEG_RE.match(segs[-1]))


def is_redirector_job_url(url):
    """P32: an aggregator clickout that stands in for a job detail page."""
    try:
        return bool(_REDIRECT_JOB_PATH_RE.search(urlparse(url).path))
    except Exception:
        return False


def is_category_facet_url(url):
    """P33: /jobs/<Department>, /jobs/<COUNTRY-City> and friends."""
    seg = _url_last_segment(url)
    if not seg or len(seg) > 90:
        return False
    try:
        segs = [x for x in urlparse(url).path.split("/") if x]
    except Exception:
        return False
    if len(segs) < 2 or segs[-2].lower() not in (
            "job", "jobs", "career", "careers", "vacancy", "vacancies",
            "offerte", "stellenangebote", "empleo", "emploi"):
        return False
    if any(ch.isdigit() for ch in seg):
        # A real slug almost always carries the requisition id.
        return bool(_GEO_FACET_SEG_RE.match(seg))
    if _GEO_FACET_SEG_RE.match(seg):
        return True
    if seg.isupper() and len(seg) > 3:
        # PROFESSIONAL-SCIENTIFIC-AND-TECHNICAL-ACTIVITIES
        return True
    if _DEPT_SEGMENT_RE.match(seg):
        return True
    # Structural fallback, so the fix does not depend on a vocabulary that can
    # never be complete ("Sales-and-Sales-Related",
    # "Banken-Finanz-Versicherungen"). A facet chip is Title-Cased, carries no
    # requisition id and names no role; a real slug does the opposite.
    words = [w for w in re.split(r"[-_+ ]+", seg) if w]
    if (1 <= len(words) <= 6
            and any(w[:1].isupper() for w in words)
            and not _ROLE_WORD_RE.search(seg.replace("-", " "))):
        return True
    return False


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


# ───────────── COUNTRY RESOLVER (G3 Europe target) ─────────────
# MANDATORY (rebase 2026-10-03): the inline resolver this replaced knew only
# the EUROPE_COUNTRIES allowlist and silently returned None elsewhere.
from sponsorscout.core.location_country import country_from_location
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


def _dns_resolves(host: str, cancel_event=None, pause_event=None) -> bool:
    """Fail-fast DNS check so dead hosts skip browser retries entirely.

    Batch F2: one retry after 10s — survives sub-minute resolver blips
    (the 09-12/13 runs lost 72+20 targets to 11-27s outages).

    FIX P0-15: the 09-15 run lost 24 consecutive companies (La Fosse ->
    Picnic, alphabetically contiguous) to a resolver outage that outlived
    the old 2-try/10s window. Re-testing those hosts afterwards showed 7 of
    8 resolving fine. Now: 3 attempts, 5s/15s backoff, and AF_UNSPEC so an
    IPv6-only answer still counts as alive.

    STOP FAST: the 5s/15s waits are interruptible — a Stop pressed here
    returns False immediately so the scan unwinds instead of sleeping.
    """
    if not host:
        return False
    delays = (5, 15, 0)
    for attempt, delay in enumerate(delays, 1):
        if cancel_event is not None and cancel_event.is_set():
            return False
        try:
            socket.getaddrinfo(host, 443)  # AF_UNSPEC: A or AAAA both count
            return True
        except Exception:
            if delay:
                if not sleep_interruptible(delay, cancel_event, pause_event):
                    return False
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


# FIX P0-34b: ISO-3166-1 alpha-3 codes. Job boards write the country as a
# 3-letter code in address tails ("Jesi, AN, ITA", "ITA, PI, Pisa"), which the
# 2-letter COUNTRY_CODES map cannot recognise. Used only to decide whether a
# comma-separated string is an ADDRESS rather than a job title.
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

# FIX P0-60 (S-12): Eightfold / Workday-style boards collapse a posting that
# spans several sites into a CHIP -- "2 Locations", "3 Locations", "Multiple
# Locations" -- instead of listing them on the card. The card therefore
# carries no parseable place at all and the row came out with a blank Job
# Location, while its single-site siblings were fine. That is exactly the
# American Express "Manager, Business Development" case: the expanded list
# ("PA, United States / NY, United States / (Remote)") only exists on the
# DETAIL page, and the earlier probe that "could not reproduce" it was fed
# that expanded block rather than what the card actually contains.
#
# Recognising the chip turns an anonymous blank into a stated fact AND keeps
# the row eligible for detail enrichment (see _MULTI_LOCATION_LABEL in
# _detail_priority / _enrich_ns_locations), so the real sites still replace
# it whenever the detail budget reaches the row.
_MULTI_LOCATION_RE = re.compile(
    r"^\s*(?:\+?\s*\d{1,3}\s*(?:more\s+)?locations?"
    r"|multiple\s+locations?|various\s+locations?|several\s+locations?"
    r"|mehrere\s+standorte|verschiedene\s+standorte"
    r"|pi[uù]\s+sedi|sedi\s+multiple|varias\s+ubicaciones"
    r"|plusieurs\s+(?:sites|lieux)|meerdere\s+locaties)\s*$", re.I)
_MULTI_LOCATION_LABEL = "Multiple Locations"

# ── FIX P15 (2026-10-04): words that are NOT places ──────────────────────
# config.COUNTRIES_AND_REGIONS contains catch-all tokens ("global",
# "remote", "worldwide"). Three code paths treated such a token as proof
# that the string was a location and then promoted whatever word sat next
# to it to "city", so LOOP's title "Senior Digital Brand Manager - Global
# Automotive" was stored as Job Location "Automotive, Global" with
# Location Source=card (3 rows, run 20261003T233023). A FABRICATED location
# is worse than Unknown: it is accepted as scope evidence, it blocks the
# detail-page lookup that would have found the real city, and the UI files
# the row under a country the posting never named.
# These tokens may never be a location on their own and may never lend
# place-hood to a neighbouring word.
_PSEUDO_PLACE_WORDS = frozenset({
    "global", "globally", "worldwide", "world", "anywhere", "everywhere",
    "remote", "remotely", "virtual", "virtually", "hybrid", "onsite",
    "on-site", "international", "nationwide", "flexible", "various",
    "multiple", "unspecified", "undisclosed", "home", "homeoffice",
    "field", "travel", "unknown", "other", "n/a", "tbd",
})


def _is_pseudo_place(word):
    """True when `word` is a catch-all placeholder, not a real place."""
    return str(word or "").strip().strip(".,;").casefold() in _PSEUDO_PLACE_WORDS


# ── FIX P14 (2026-10-04): LABEL/VALUE metadata strips ────────────────────
# LOOP's JD pages state the place in a spec strip:
#     <span class="subheadline">Location</span>
#     <span class="h4">Salzburg - Vienna </span>
# The page carries NO JSON-LD, and the old static fallback
#     (?i)(?:location|standort|...)\s*[:<]\s*([A-Za-z...])
# cannot cross "</span><span class=...>" -- the character right after the
# label is "/", so it never matched and 14 of 17 LOOP rows were written with
# Job Location=Unknown / Country Unknown while the detail page was already
# in hand (its visa evidence came from exactly that fetch).
# This reads the universal label-then-value shapes: sibling spans, dt/dd,
# th/td, "Location: X" in plain text, and the usual meta tags.
_LABEL_LOC_WORDS = (
    r"location|locations|job location|work location|standort|standorte|"
    r"arbeitsort|einsatzort|dienstort|ort|lieu|lieu de travail|localisation|"
    r"ubicaci[oó]n|localizaci[oó]n|lugar|luogo|sede|localit[aà]|locatie|"
    r"plaats|werklocatie|miejsce pracy|lokalizacja|sijainti|plats|ciudad|"
    r"citt[aà]|stadt|ville|city|office|b[üu]ro"
)
_LABEL_LOC_TAGS = r"span|dt|dd|th|td|div|p|strong|b|em|h[1-6]|label|li"
_LABEL_LOC_PATTERNS = (
    # <span>Location</span> <span>Salzburg - Vienna</span>   (also dt/dd, th/td)
    re.compile(
        r"(?is)>\s*(?:" + _LABEL_LOC_WORDS + r")\s*:?\s*"
        r"</(?:" + _LABEL_LOC_TAGS + r")>\s*"
        r"(?:<[^>]{0,200}>\s*){0,3}([^<>]{2,80})"),
    # <meta name="job_location" content="Vienna, Austria">
    re.compile(
        r"(?is)<meta[^>]+(?:name|property|itemprop)=[\"'][^\"']*"
        r"(?:location|locality|city)[^\"']*[\"'][^>]*content=[\"']([^\"']{2,80})[\"']"),
    # data-location="Vienna" / aria-label="Location: Vienna"
    re.compile(r"(?is)data-(?:location|city)=[\"']([^\"']{2,80})[\"']"),
    # Plain text "Location: Salzburg - Vienna" (kept last, weakest)
    re.compile(
        r"(?i)\b(?:" + _LABEL_LOC_WORDS + r")\s*[::]\s*"
        r"([A-Za-zÀ-ÿ][^<\n\r;|]{1,60})"),
)
# Values that are furniture rather than a place.
_LABEL_LOC_JUNK = re.compile(
    r"(?i)(cookie|privacy|consent|javascript|function\s*\(|\{|\}|©|all rights|"
    r"sign in|log in|apply now|search|filter|select|choose|menu|home\s*$)")


def extract_labelled_location(html, limit=6):
    """Pull location values out of label/value metadata strips in raw HTML.

    Returns a list of candidate strings (strongest first), never None. The
    caller is responsible for validating them through the location parser --
    this function only finds the TEXT the page labelled as a location.
    """
    if not html:
        return []
    out = []
    seen = set()
    for pat in _LABEL_LOC_PATTERNS:
        for m in pat.finditer(html):
            val = unescape(m.group(1) or "")
            val = re.sub(r"<[^>]+>", " ", val)
            val = re.sub(r"\s+", " ", val).strip(" \t\r\n,;:|-·•")
            if not val or len(val) < 2 or len(val) > 80:
                continue
            if not re.search(r"[A-Za-zÀ-ÿ]", val):
                continue
            if _LABEL_LOC_JUNK.search(val):
                continue
            key = val.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(val)
            if len(out) >= limit:
                return out
    return out



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





# ─────────────── PROVIDER SNIFFING (FIX W1-1, 2026-10-04) ────────────────
# 305 of 328 career seeds carry provider=auto.  `_fetch_provider_jobs` has no
# adapter for "auto", so every one of them fell straight through to
# "provider adapter not configured" and a full Chromium DOM crawl: 198 of the
# 208 companies in run 20261003T233023, ~100 s each, 97 of them yielding
# nothing at all.
#
# Almost all of those boards ARE a known ATS -- they just embed it (an iframe,
# a redirect, an XHR to the board API) instead of naming it in the seed.  One
# cheap HTML fetch finds the fingerprint, and from then on the company goes
# down the API path (~2 s) instead of the browser path (~100 s).  The verdict
# is cached on disk, so run 2 does not even pay the sniff.
#
# This NEVER edits the seed file: the resolved provider lives in the in-memory
# target row for the duration of the run.
_SNIFF_PATTERNS = (
    ("greenhouse", (
        r"boards\.greenhouse\.io/embed/job_board\?for=([A-Za-z0-9_.-]+)",
        r"boards\.greenhouse\.io/([A-Za-z0-9_.-]+)",
        r"job-boards\.greenhouse\.io/([A-Za-z0-9_.-]+)",
        r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_.-]+)",
    )),
    ("ashby", (
        r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)",
        r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.-]+)",
    )),
    ("lever", (
        r"jobs\.lever\.co/([A-Za-z0-9_.-]+)",
        r"api\.lever\.co/v0/postings/([A-Za-z0-9_.-]+)",
    )),
    ("personio", (
        r"([A-Za-z0-9-]+)\.jobs\.personio\.(?:de|com)",
        r"([A-Za-z0-9-]+)\.personio\.(?:de|com)/(?:job|position|recruiting)",
    )),
    ("recruitee", (
        r"([A-Za-z0-9-]+)\.recruitee\.com",
        r"([A-Za-z0-9-]+)\.rec\.recruitee\.com",
    )),
    ("workable", (
        r"apply\.workable\.com/([A-Za-z0-9_.-]+)",
        r"([A-Za-z0-9-]+)\.workable\.com/j/",
    )),
    ("smartrecruiters", (
        r"careers\.smartrecruiters\.com/([A-Za-z0-9_.-]+)",
        r"jobs\.smartrecruiters\.com/([A-Za-z0-9_.-]+)",
        r"api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9_.-]+)",
    )),
    ("teamtailor", (
        r"([A-Za-z0-9-]+)\.teamtailor\.com",
    )),
    ("bamboohr", (
        r"([A-Za-z0-9-]+)\.bamboohr\.com",
    )),
    # W3-4: Decathlon Italia's board is DigitalRecruiters/Cegid and the
    # scanner already has a working adapter for it -- it just never
    # recognised the board unless the seed said so. The careers page
    # embeds its own API host, which is the fingerprint.
    ("digitalrecruiters", (
        r"//([A-Za-z0-9.-]+\.[A-Za-z]{2,})/[a-z]{2}/annonces?(?:[/?#\"'\s]|$)",
    )),
)
# Workday needs two captures (tenant host + site path), so it is matched apart.
_SNIFF_WORKDAY = re.compile(
    r"(?P<host>[A-Za-z0-9-]+\.(?:wd\d+)\.myworkdayjobs\.com)"
    r"(?:/wday/cxs/(?P<tenant>[A-Za-z0-9_-]+))?"
    r"/(?:[a-z]{2}-[A-Z]{2}/)?(?P<site>[A-Za-z0-9_-]+)", re.I)
# Boards we can RECOGNISE but have no API adapter for: naming them in the
# diagnostics is still worth it (it explains a slow DOM crawl).
_SNIFF_UNSUPPORTED = (
    ("successfactors", r"(career\d*\.successfactors\.(?:eu|com)|/sfcareer/)"),
    ("taleo", r"\.taleo\.net"),
    ("avature", r"\.avature\.net"),
)
# FIX P19: icims and eightfold moved OUT of the unsupported list -- both now
# have an adapter. Neither fingerprint carries the API host, so the slug is
# the host we know about and the adapter probes the usual career subdomains.
_SNIFF_EIGHTFOLD = re.compile(
    r"([A-Za-z0-9-]+\.eightfold\.ai)|(/api/apply/v2/jobs)", re.I)
_SNIFF_ICIMS = re.compile(r"[A-Za-z0-9-]*\.icims\.com", re.I)


def sniff_provider(text, base_url=""):
    """Find an ATS fingerprint in a careers page.

    Returns ``(provider, slug, note)``.  ``provider`` is "" when nothing
    usable was found; ``note`` names an unsupported-but-recognised board.
    """
    blob = f"{base_url}\n{text or ''}"
    m = _SNIFF_WORKDAY.search(blob)
    if m:
        host = m.group("host")
        tenant = m.group("tenant") or host.split(".")[0]
        site = m.group("site")
        if site and site.lower() not in ("wday", "en-us"):
            return "workday", f"{host}|{tenant}|{site}", ""
    # DigitalRecruiters-hosted career portals are common in France/Belgium.
    # Cegedim is a known example: /en/annonces is a direct listing and the
    # detail pages sit below /en/annonce/<id>-<slug>. Prefer the API adapter
    # over DOM scraping when the vendor fingerprint is present.
    if re.search(r"digitalrecruiters|bankess", blob, re.I) and re.search(
            r"/(?:[a-z]{2}/)?annonces?(?:[/?#\"'\s]|$)", blob, re.I):
        return "digitalrecruiters", urlparse(base_url).netloc, ""
    if re.search(r"careers\.cegedim\.com", blob, re.I) and re.search(
            r"/(?:en|fr)/annonces?(?:[/?#\"'\s]|$)", blob, re.I):
        return "digitalrecruiters", "careers.cegedim.com", ""
    for provider, patterns in _SNIFF_PATTERNS:
        for pat in patterns:
            hit = re.search(pat, blob, re.I)
            if not hit:
                continue
            slug = (hit.group(1) or "").strip().strip(".-")
            low = slug.lower()
            # Guard against matching the vendor's own marketing pages.
            if not slug or low in ("www", "api", "jobs", "job", "boards",
                                   "embed", "careers", "apply", "static",
                                   "assets", "support", "help", "about",
                                   "resources", "customers", "blog"):
                continue
            return provider, slug, ""
    # FIX P19: platforms whose fingerprint does not name the API host.
    m = _SNIFF_EIGHTFOLD.search(blob)
    if m:
        return "eightfold", (m.group(1) or urlparse(base_url).netloc), ""
    if _SNIFF_ICIMS.search(blob):
        return "icims", urlparse(base_url).netloc, ""
    for name, pat in _SNIFF_UNSUPPORTED:
        if re.search(pat, blob, re.I):
            return "", "", name
    return "", "", ""




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


class CareerPortalScanner:
    def __init__(self, input_csv="company_Career_seed.csv", output_csv="scraped_jobs_v7.csv",
                 max_workers=None, detail_scan=False, resume=False,
                 allow_synthetic=False, skip_preflight=False, cancel_event=None,
                 only_companies=None, max_company_time_sec=None, pause_event=None):
        self.input_csv = input_csv
        self.output_csv = output_csv
        # FIX P42: per-run budget for detail-page title rescues. process_job
        # runs under a thread pool, so the counter needs its own lock.
        self._title_rescue_used = 0
        self._title_rescue_by_company = {}
        self._title_rescue_lock = threading.Lock()
        # Host-adaptive company-level concurrency.  Each worker drives its own
        # Chromium context (~150-400 MB resident), so the previous fixed 3 could
        # exhaust an 8 GB / 2-core machine and freeze the desktop.  None means
        # "size the pool for this host"; an explicit value is always honoured.
        try:
            # An explicit value from the UI/CLI is a decision, not a hint: it
            # is honoured as given (capped at 8) instead of being clamped down
            # to the heuristic.
            self.max_workers = (min(8, max(1, int(max_workers))) if max_workers
                                else recommended_workers("browser"))
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
            check_cancelled(self.cancel_event)
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
                    # which has no preflight). Interruptible so Stop lands fast.
                    dns_retried = True
                    if not sleep_interruptible(
                            10, self.cancel_event, self.pause_event):
                        raise ScanCancelled()
            if attempt < self.config.HTTP_RETRIES:
                backoff = self.config.HTTP_BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                if not sleep_interruptible(
                        backoff, self.cancel_event, self.pause_event):
                    raise ScanCancelled()
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
        _legacy_provider_warned = False
        with open(self.input_csv, newline="", encoding="utf-8-sig") as f:
            sample = f.read(4096)
            f.seek(0)
            delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
            reader = csv.DictReader(f, delimiter=delimiter)
            headers = set(reader.fieldnames or [])
            if "careers_url" not in headers:
                raise ValueError("Seed CSV must contain careers_url")
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
                # ``ats_type`` is the removed v6 name for this column. A
                # personal seed under %APPDATA% may predate the rename, so it
                # is still read -- and announced, once per file, rather than
                # honoured in silence.
                _legacy_ats = (row.get("ats_type") or "").strip()
                provider = ((row.get("provider") or "").strip()
                            or _legacy_ats or "auto").lower()
                if _legacy_ats and not _legacy_provider_warned:
                    _legacy_provider_warned = True
                    print(f"   -> [seed] {self.input_csv}: uses the removed v6 "
                          f"column 'ats_type'; read as 'provider'. Rename the "
                          f"column (or re-copy the shipped seed) to silence this.")
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
                    "notes": (row.get("notes") or "").strip(),
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
            # W3-1: was "" -- it swallowed the spaces on BOTH sides, so every
            # German title with an inline gender tag came out welded together:
            # "Designer (m/w/d) Jobs in Innsbruck" -> "DesignerJobs in
            # Innsbruck", "Werkstudent (m/w/d) Marketing" ->
            # "WerkstudentMarketing". A single space keeps word boundaries;
            # the strip() below removes it when the tag was trailing.
            " ", title, flags=re.IGNORECASE,
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
        # W3-1: drop a trailing aggregator search phrase ("... Jobs in
        # Innsbruck"); the place itself is recovered separately as a location
        # hint by _split_title_place, so nothing is lost.
        title, _ = self._split_title_place(title)
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
        # FIX P0-34b: this routine required at least one KNOWN place, so an
        # unlisted city defeated it entirely: "Jesi, AN, ITA" (Jesi is not in
        # KNOWN_PLACES) was accepted as a job title. Anchor on the LAST
        # segment instead -- when it is a country name or an ISO-2/ISO-3
        # country code and no segment contains a role word, the whole string
        # is an address, whether or not we recognise the town.
        _last = segs[-1].strip().lower()
        _is_country_tail = (
            _last in self.config.COUNTRIES_AND_REGIONS
            or (len(_last) == 2 and _last in self.COUNTRY_CODES)
            or (len(_last) == 3 and _last in _ISO3_COUNTRY_CODES)
        )
        # Guard rails so real titles are never swallowed: at least 3 segments
        # AND at least one NON-tail segment that is itself a subdivision code
        # or a known place. That keeps "Sales, UK" and "Marketing, Digital,
        # UK" as titles while catching "Jesi, AN, ITA" / "AN, ITA".
        # Parity with ats_portal_scanner: a full country NAME or ISO-3 code
        # anywhere, with no role noun, means address (never a bare ISO-2 --
        # "UK" in "Sales, UK" must stay a title).
        _names = self._country_name_set()
        if (any(len(s.strip()) >= 3
                and (s.strip().lower() in _names
                     or s.strip().lower() in _ISO3_COUNTRY_CODES)
                for s in segs)
                and not any(self.config.ROLE_WORD_PATTERN.search(s)
                            for s in segs)
                and all(len(s.split()) <= 4 for s in segs if s)):
            return True
        _head = [s for s in segs[:-1] if s]
        # All-code address stub with a country tail ("AN, ITA") -- no known
        # place anywhere, so the loop below can never catch it.
        if (_is_country_tail and _head
                and all(re.fullmatch(r"[a-z]{2,3}", s.lower()) for s in _head)):
            return True
        _anchor = any(
            re.fullmatch(r"[a-z]{2,3}", s.lower())
            or s.lower() in self.KNOWN_PLACES
            or self._norm(s.lower()) in self.NORM_KNOWN
            for s in _head
        )
        if (_is_country_tail and _anchor and len(segs) >= 3
                and not any(self.config.ROLE_WORD_PATTERN.search(s)
                            for s in segs)):
            if all(len(s.split()) <= 4 for s in segs if s):
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

    #: Department / programme labels that are a SECTION heading on a careers
    #: page, not a job. FIX P0-61.
    _TITLE_DEPT_LABELS = frozenset({
        "campus recruiting", "campus hiring", "early careers", "early talent",
        "graduate programme", "graduate program", "trainee", "traineeship",
        "internship", "internships", "apprenticeship", "apprenticeships",
        "student jobs", "students", "talent community", "talent pool",
        "join our talent community", "general application",
        "speculative application", "open application", "other",
        "engineering", "sales", "marketing", "finance", "operations",
        "human resources", "legal", "customer service", "corporate",
        "technology", "product", "design", "data", "research",
        "praktikum", "ausbildung", "werkstudent", "stage", "stages",
        "tirocinio", "stagiaire", "becario",
    })


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
        "candidati", "candidatura", "invia candidatura", "cerca lavoro",
        "cerca lavoro", "ricerca lavoro", "opportunita di lavoro",
        "opportunità di lavoro", "carriere",
        # Dutch
        "vacatures", "alle vacatures", "werken bij", "open sollicitatie",
        "bekijk alle vacatures", "vacature", "solliciteer", "sollicitatie",
        # French
        "emplois", "nos offres", "toutes les offres", "offres d'emploi",
        "offre d'emploi", "candidature spontanee", "candidature spontanée",
        "postuler", "postulez",
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

    def is_valid_job_title(self, title, company=""):
        if not title:
            return False
        t = re.sub(r"\s+", " ", title).strip()
        low = t.casefold()
        if len(t) < 3 or len(t) > 200 or not any(ch.isalpha() for ch in t):
            return False
        # FIX P0-61 (S-15): three kinds of non-title were being accepted, and
        # because they are identical across every posting they made distinct
        # jobs look like duplicates in the UI. Verified against the 12,298-row
        # scan output: "ABN AMRO" (the company's own name) x2, "Antwerpen" (a
        # city) x3, "Trainee" x9 and "Campus Recruiting" x4 all shipped as job
        # titles, each group pointing at DIFFERENT job URLs.
        #
        # NB this is NOT the main cause of the apparent duplicates -- that was
        # settled separately: of 407 repeated Company+Title+Location groups in
        # that file, 0 shared a URL, so dedup is sound and the real driver is
        # a blank Location (S-09 / S-12). These 18 rows are the genuine
        # title-side remainder.
        if company:
            _c = re.sub(r"\s+", " ", str(company)).strip().casefold()
            _c_core = re.sub(
                r"\b(inc|llc|ltd|limited|gmbh|b\.?v|n\.?v|s\.?a|s\.?r\.?l|ag|"
                r"plc|corp|corporation|company|group|holding|holdings)\b",
                " ", _c)
            _c_core = re.sub(r"[^a-z0-9]+", " ", _c_core).strip()
            _t_core = re.sub(r"[^a-z0-9]+", " ", low).strip()
            if _t_core and (_t_core == _c.strip() or _t_core == _c_core):
                return False
        if low in self._TITLE_DEPT_LABELS:
            return False
        # W3-1 (P5): board navigation in ANY language. The old list was
        # English-only, so "Job Detail", "Careers FAQs", "Jobsuche",
        # "Stellenangebote", "Offerte di lavoro", "Vacatures" and
        # "Alle Jobs anzeigen" all shipped as job titles.
        if self._is_nav_title(t):
            return False
        # A title that is nothing but a place. _title_is_pure_location only
        # consults a ~190-entry list, which has Amsterdam but not Antwerpen;
        # the full location parser and the country resolver know both.
        if len(t.split()) <= 3 and not self.config.ROLE_WORD_PATTERN.search(t):
            try:
                if self._location_from_line(t):
                    return False
            except Exception:
                pass
            try:
                if country_from_location is not None and (
                        country_from_location(t) or "").strip():
                    return False
            except Exception:
                pass
        # FIX P0-34a: job-card METADATA lines were accepted as titles.
        # Amazon's cards render "Updated: 9/3/2026" and a bare location line
        # as separate nodes; both became rows with that text as Job Title.
        if re.match(r"^(updated|posted|published|last\s+updated|date|"
                    r"aggiornato|pubblicato|aktualisiert|bijgewerkt|"
                    r"mis\s+a\s+jour)\b\s*[:\-–]?\s*", low):
            return False
        # A title that is only a date / date range carries no role information.
        if re.fullmatch(r"[\d\s/.\-–]+", t):
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
            r"upload|submit|download|share|print|email|save|apply|bewerben|bewerbung|candidati|candidarsi|postuler|postulez|solliciteer|sollicitatie|scopri|leggi|invia|vedi)\b", low  # multilingual CTA verbs
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
        # W3-1 (P5): a one-word title under five characters is junk in
        # English and a complete role in German/Dutch/French ("Koch",
        # "Arzt", "Chef", "Kok"). Keep the rule, exempt the role nouns.
        if (len(t.split()) == 1 and len(t) < 5
                and low not in self._W3_SHORT_ROLES):
            return False
        return True

    # ── Locale-aware career crawling ───────────────────────────────────────
    # Career portals are not reliably English just because the company has an
    # English corporate site. In the seed this matters especially for Germany,
    # Austria, Italy, Netherlands, France and Belgium. The previous browser
    # context was hard-coded to en-US, which can make a local-only portal render
    # an empty/default shell, redirect to a generic page, or hide the local job
    # list behind a language switcher.
    _COUNTRY_LOCALE_PREFERENCES = {
        "germany": ("de-DE", "de", "en-US", "en"),
        "austria": ("de-AT", "de", "en-US", "en"),
        "switzerland": ("de-CH", "de", "fr-CH", "it-CH", "en"),
        "italy": ("it-IT", "it", "en-US", "en"),
        "netherlands": ("nl-NL", "nl", "en-US", "en"),
        "belgium": ("nl-BE", "nl", "fr-BE", "fr", "en"),
        "france": ("fr-FR", "fr", "en-US", "en"),
        "spain": ("es-ES", "es", "en-US", "en"),
        "portugal": ("pt-PT", "pt", "en-US", "en"),
        "poland": ("pl-PL", "pl", "en-US", "en"),
        "finland": ("fi-FI", "fi", "sv-FI", "en"),
        "sweden": ("sv-SE", "sv", "en-US", "en"),
        "denmark": ("da-DK", "da", "en-US", "en"),
        "norway": ("nb-NO", "nb", "en-US", "en"),
        "estonia": ("et-EE", "et", "en"),
        "lithuania": ("lt-LT", "lt", "en"),
        "ireland": ("en-IE", "en"),
        "united kingdom": ("en-GB", "en-US", "en"),
    }
    _LANG_TO_LOCALE = {
        "de": "de-DE", "it": "it-IT", "nl": "nl-NL", "fr": "fr-FR",
        "es": "es-ES", "pt": "pt-PT", "pl": "pl-PL", "fi": "fi-FI",
        "sv": "sv-SE", "da": "da-DK", "nb": "nb-NO", "no": "nb-NO",
        "et": "et-EE", "lt": "lt-LT", "en": "en-US",
    }

    def _preferred_browser_locales(self, target_row, seed_url):
        """Return (locale, Accept-Language) with local-language-first policy.

        URL locale wins over the seed country because `/it/`, `/de-DE/`, etc.
        are stronger evidence than a company's target-country metadata. If no
        locale is encoded in the URL, target_country selects a sensible local
        preference list, while English remains an explicit fallback.
        """
        url = str(seed_url or "")
        path = (urlparse(url).path or "").lower()
        host = (urlparse(url).hostname or "").lower()
        candidates = []
        # Locale path/subdomain: /de/, /de-de/, /it_IT/, jobs.fr.example.com.
        for raw in re.findall(r"(?:^|[/_\-.])([a-z]{2})(?:[-_]([a-z]{2}))?(?=[/_\-.]|$)", path + "/" + host):
            lang, region = raw
            if lang in self._LANG_TO_LOCALE:
                candidates.append(f"{lang}-{region.upper()}" if region else self._LANG_TO_LOCALE[lang])
        target = str((target_row or {}).get("target_country") or "").strip().casefold()
        candidates.extend(self._COUNTRY_LOCALE_PREFERENCES.get(target, ("en-US", "en")))
        # Preserve order while removing duplicates.
        ordered = []
        for x in candidates:
            if x and x not in ordered:
                ordered.append(x)
        locale = ordered[0] if ordered else "en-US"
        weights = []
        for i, lang in enumerate(ordered[:6]):
            q = max(0.55, 1.0 - i * 0.08)
            weights.append(lang if i == 0 else f"{lang};q={q:.2f}")
        return locale, ",".join(weights)

    _DIRECT_LISTING_PATH_RE = re.compile(
        r"/(?:"
        r"jobs?|joboffers?|"
        r"vacatures?|vacancies?|"
        r"stellen(?:angebote|anzeigen)?|jobangebote?|"
        r"offres?(?:[-_](?:d[-_])?emploi)?|emplois?|"
        r"annonces?|"
        r"offerte?(?:[-_](?:di[-_])?lavoro)?|"
        r"posizioni(?:[-_]aperte)?|annunci?(?:[-_]di[-_]lavoro)?|"
        r"offertas?|ofertas?[-_]de[-_](?:empleo|trabajo)|"
        r"vagas?|empregos?|"
        r"werk(?:en[-_]bij)?|werken[-_]bij|"
        r"lediga[-_](?:jobb|tjaenster|tjänster)|"
        r"avoimet[-_]tyopaikat|oferty[-_]pracy|praca"
        r")(?:/|$)", re.I)
    _DIRECT_LISTING_SUBPATH_RE = re.compile(
        r"/(?:jobs?|careers?)/(?:all[-_]?jobs?|open[-_]?jobs?|open[-_]?positions?|"
        r"current[-_]?openings?|jobangebote?|offene[-_]?jobangebote|"
        r"offene[-_]stellen(?:angebote)?|stellenangebote|stellenanzeigen|"
        r"vacatures?|vacancies?|offres?|offerte(?:[-_]di[-_]lavoro)?|"
        r"posizioni[-_]aperte|annunci?(?:[-_]di[-_]lavoro)?|ofertas?|empleos?|puestos?)(?:/|$)", re.I)

    @classmethod
    def _is_direct_listing_seed_url(cls, seed_url):
        """Whether the seed itself is intended to be a listing, not a landing page.

        This is a crawl-strategy signal, not a job URL validator. It tells the
        browser not to click search/landing CTAs on a page whose URL already
        denotes a job board/list. Ambiguous roots such as AMS / are deliberately
        left as landing pages.
        """
        parsed = urlparse(str(seed_url or ""))
        path = (parsed.path or "").rstrip("/") or "/"
        low = path.casefold()
        if cls._DIRECT_LISTING_SUBPATH_RE.search(low):
            return True
        if cls._DIRECT_LISTING_PATH_RE.search(low):
            return True
        if re.search(r"/(?:find[-_]job|jobsuche|jobsearch|search[-_]jobs)(?:/|$)", low, re.I):
            return True
        # Fragment-only job listing (e.g. /careers/#jobs) is a direct listing
        # only when the fragment explicitly names the job collection.
        fragment = (parsed.fragment or "").casefold()
        if fragment in {"jobs", "job", "vacancies", "positions", "openings", "jobs-list", "job-list"}:
            return True
        return False

    @staticmethod
    def _direct_listing_pagination_state(target):
        """Return visible, structural pagination signals without scrolling/clicking."""
        js = r"""() => {
            const visible = el => {
                try { const r=el.getBoundingClientRect(), s=getComputedStyle(el);
                    return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden' && s.opacity!=='0'; }
                catch(e) { return false; }
            };
            const text = el => String(el?.innerText || el?.textContent || '').replace(/\s+/g,' ').trim();
            const roots = Array.from(document.querySelectorAll('nav, [class*="pagination" i], [aria-label*="pagination" i], [role="navigation"]'));
            let hasNext=false, hasLoadMore=false, hasPager=false;
            for (const root of roots) {
                if (!visible(root)) continue;
                for (const el of root.querySelectorAll('a,button')) {
                    if (!visible(el)) continue;
                    const t=text(el).toLowerCase();
                    const al=String(el.getAttribute('aria-label') || '').toLowerCase();
                    const blob=(t+' '+al).trim();
                    if (/(^|\b)(next|suivant|suivante|volgende|volgend|volgende pagina|weiter|nächste|nächster|prossimo|successiva|sucesivo|siguiente|seguinte|nästa|næste|next page)(\b|$)/i.test(blob)) hasNext=true;
                    if (/(load more|show more|more jobs|more vacancies|mehr laden|weitere|mehr anzeigen|weitere stellen|più offerte|carica altro|mostra altro|voir plus|plus d'offres|meer vacatures|toon meer|meer laden|cargar más|más ofertas|mostrar más|mostrar mais|ver mais|więcej|pokaż więcej|visa fler|vis flere|näytä lisää)/i.test(blob)) hasLoadMore=true;
                    if (/^\d{1,3}$/.test(t) && Number(t)>1) hasPager=true;
                }
            }
            return {hasNext,hasLoadMore,hasPager};
        }"""
        try:
            out = target.evaluate(js) or {}
            return {k: bool(out.get(k)) for k in ("hasNext","hasLoadMore","hasPager")}
        except Exception:
            return {"hasNext": False, "hasLoadMore": False, "hasPager": False}

    @staticmethod
    def _direct_listing_anchor_rows(target):
        """Extract repeated same-host job anchors without card-scope rejection.

        List containers intentionally contain many job links. The normal anchor
        extractor rejects such containers because their shared context is unsafe
        for metadata attribution. This extractor only needs the anchor itself, so
        it is safe for direct listing pages and is the important path for
        WordPress/Elementor/localized employer boards like 3 Banken IT.
        """
        js = r"""() => {
            const clean = s => String(s || '').replace(/\s+/g,' ').trim();
            const visible = el => { try { const r=el.getBoundingClientRect(), s=getComputedStyle(el);
                return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden' && s.opacity!=='0'; } catch(e){return false;} };
            const hrefLooks = href => {
                if (!href || !/^https?:/i.test(href)) return false;
                const u = new URL(href, location.href);
                const p = u.pathname.toLowerCase();
                if (/(privacy|cookie|terms|legal|contact|about|blog|news|login|signup)/i.test(p)) return false;
                if (/[?&](job|jobid|job_id|jid|gh_jid|req|reqid|requisition|posting|postingid|id)=/i.test(u.search)) return true;
                return /(\/(?:jobangebote?|stellenangebote?|stellenanzeigen?|stelle|vacanc(?:y|ies)|opening|position|requisition|posting|annonce|offre?s?|offert(?:a|e)(?:-di-lavoro)?|posizion[ei]|annunci?|lavoro|empleo?s?|puesto?s?|oferta?s?|vag[ae]s?|werk(?:en-bij)?|vacatures?|job[s]?|role|recruit|jobsuche|find-job)\/[^/?#]+)/i.test(p)
                    || /\/(?:job|role|requisition|posting)\/[A-Za-z0-9_-]{2,}(?:/|$)/i.test(p);
            };
            const generic = /^(apply(?: now| here| online)?|view(?: job| role| position| details)?|details?|read more|learn more|more info|next|previous|back|home|jobs?|careers?|search|filter|sort|select|close|open|load more|show more|candidati(?: ora)?|bewerben|mehr anzeigen|scopri di più|leggi l'annuncio|postuler|solliciteer|toon meer)$/i;
            const out=[]; const seen=new Set();
            for (const a of document.querySelectorAll('a[href]')) {
                if (!visible(a)) continue;
                const href = a.href;
                if (!hrefLooks(href)) continue;
                const u = new URL(href, location.href);
                if (u.origin !== location.origin) continue;
                const title = clean(a.innerText || a.textContent || a.getAttribute('aria-label') || a.getAttribute('title') || '');
                if (title.length < 3 || title.length > 220 || generic.test(title)) continue;
                if (seen.has(href.toLowerCase())) continue;
                seen.add(href.toLowerCase());
                out.push({job_title:title, job_url:u.href, card_context:'', location_hint:'', extraction_method:'direct_listing_anchor'});
            }
            return out;
        }"""
        try:
            return target.evaluate(js) or []
        except Exception:
            return []

    @staticmethod
    def _local_job_signal_re():
        """Strong job-board vocabulary used only as semantic evidence.

        This is deliberately broader than roleWordRe. A title does not need to
        contain an English role noun: `Sachbearbeiter`, `Koch`, `Addetto...`,
        `Responsable...`, `Medewerker...` are valid titles. The terms below are
        navigation/employment signals, not title requirements.
        """
        return re.compile(
            r"(?i)\b(?:bewerben|bewerbung|jetzt bewerben|stellenangebot|"
            r"stellenangebote|stellenanzeige|stellenanzeigen|jobangebot|jobangebote|"
            r"vollzeit|teilzeit|unbefristet|befristet|berufserfahrung|"
            r"candidati|candidatura|candidarsi|offerta di lavoro|offerte di lavoro|"
            r"posizione aperta|posizioni aperte|tempo pieno|tempo parziale|"
            r"postuler|postulez|offre d'emploi|offres d'emploi|cdi|cdd|temps plein|"
            r"temps partiel|solliciteer|sollicitatie|vacature|vacatures|fulltime|"
            r"parttime|werken bij|oferta de empleo|ofertas de empleo|vagas|"
            r"ofertas de trabalho|lediga jobb|lediga tjänster|avoimet työpaikat|"
            r"oferty pracy|praca)\b"
        )

    def is_valid_job_url(self, url):
        if not url or not url.startswith("http"):
            return False
        if _ASSET_URL_RE.search(url) or _is_download_url(url):
            return False
        if is_ats_furniture_url(url):  # FIX P38
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
        # FIX P33: a department / function / geography CHIP under /jobs/.
        # Checked BEFORE the accept rules, because /jobs/<anything> matches
        # JOB_URL_PATTERN and would otherwise be written as a real job.
        if is_category_facet_url(url):
            return False
        if self.config.CATEGORY_PATH_INDICATORS.search(url):
            return False
        # FIX P31: the careers list is hosted by an ATS on another domain
        # (careers.kula.ai/journi/35169). FIX P32: an aggregator's
        # /clickout/<hash> redirector IS the job link.
        if is_ats_detail_url(url) or is_redirector_job_url(url):
            return True
        # Listing pages can themselves contain a slug that looks like a
        # /jobs/<slug> detail URL. This is especially common on localized
        # European employer sites, e.g. /jobs/offene-jobangebote/ (3 Banken IT).
        if re.search(
            r"/(?:jobs?|careers?)/(?:all[-_]?jobs?|open[-_]?jobs?|"
            r"open[-_]?positions?|current[-_]?openings?|jobangebote|jobangebot|"
            r"offene[-_]?jobangebote|offene[-_]?stellen(?:angebote)?|"
            r"stellenangebote|stellenanzeigen|vacancies|vacature(?:s)?|"
            r"offres?(?:-d-emploi)?|offres-d-emploi|offerte(?:-di-lavoro)?|"
            r"offerte-di-lavoro|posizioni-aperte|annunci(?:-di-lavoro)?|"
            r"ofertas?(?:-de-empleo|-de-trabajo)?|empleos?|puestos?)(?:[/?#]|$)",
            url,
            re.IGNORECASE,
        ):
            return False
        clean_path = urlparse(url).path.rstrip("/").lower()
        if clean_path.endswith((
            "/jobs", "/careers", "/career", "/vacancies",
            "/openings", "/positions", "/roles", "/search",
            "/jobangebote", "/jobangebot", "/stellenangebote",
            "/stellenanzeigen", "/job-offers", "/job-offer"
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

    _COUNTRY_NAME_CACHE = None

    def _country_name_set(self):
        """FIX P0-33: set of genuine country NAMES (never cities/regions)."""
        if CareerPortalScanner._COUNTRY_NAME_CACHE is None:
            names = {str(v).lower() for v in self.COUNTRY_CODES.values() if v}
            try:
                from sponsorscout.core.location_country import ISO2_TO_COUNTRY
                names |= {str(v).lower() for v in ISO2_TO_COUNTRY.values() if v}
            except Exception:
                pass
            CareerPortalScanner._COUNTRY_NAME_CACHE = names
        return CareerPortalScanner._COUNTRY_NAME_CACHE

    def _iso3_country_name(self, token):
        """FIX P0-47: three-letter ISO country code -> country name."""
        t = (token or "").strip().lower()
        iso2 = _ISO3_TO_ISO2.get(t)
        if not iso2:
            return None
        name = self.COUNTRY_CODES.get(iso2)
        if name:
            return name
        try:
            from sponsorscout.core.location_country import ISO2_TO_COUNTRY
            return ISO2_TO_COUNTRY.get(iso2)
        except Exception:
            return None

    def _corroborating_country(self, segments):
        """FIX P0-48: country implied by the non-code segments of a location.

        Used to arbitrate an ambiguous two-letter token. "Gurugram" resolves
        to India, so an "HR" beside it is Haryana and must not become
        Croatia; "Zagreb" resolves to Croatia, so "HR" there genuinely is
        Croatia. Returns None when no segment resolves, which the caller
        treats as "no evidence" rather than "no country".
        """
        if country_from_location is None:
            return None
        # A US/Canadian state code in the same string is a stronger signal
        # than a city name and must not be overruled by one: "Florence, KY,
        # US" is Kentucky, not the Italian Florence, so return no opinion and
        # let the normal expansion stand.
        for seg in segments or []:
            if (seg or "").strip().lower() in self.STATE_CODES:
                return None
        for seg in segments or []:
            s = (seg or "").strip()
            if len(s) <= 2 or not re.search(r"[A-Za-z\u00c0-\u00ff]", s):
                continue
            if len(s) == 3 and s.lower() in _ISO3_COUNTRY_CODES:
                continue
            try:
                c = (country_from_location(s) or "").strip()
            except Exception:
                c = ""
            if c:
                return c
        return None


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

    # FIX P0-49 (S-09/S-04): values that are never a place but were reaching
    # the Job Location column.
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
        """FIX P0-49: last gate before a value is written to Job Location.

        parse_job_metadata validates its own output, but two other writers do
        not -- the detail-page enricher and the detail-URL enricher -- so
        page furniture reached the column verbatim: "Operations", "Are",
        "Amazon Ads", "At Amazon", "Amazon Web Services", "In Amazon",
        "Amazon". Every one of those is rejected by _location_from_line and
        yields no country, i.e. the parser already knew they were not places;
        nothing was asking it. Rule: a value is kept only if the location
        parser can make something of it, a country can be derived from it,
        or it is a recognised non-place work marker ("Remote", "EMEA").
        Everything else becomes "Unknown" instead of a fake place.
        """
        v = re.sub(r"\s+", " ", str(value or "")).strip(" \t,;|-")
        if not v:
            return "Unknown"
        # W3-2: "Innsbruck. Access to the whole region" and "64100 Teramo"
        # are places with furniture attached, not non-places. Trim to the
        # place whenever the trimmed form is the one that resolves.
        _trimmed = self._w3_strip_location_noise(v)
        if _trimmed and _trimmed != v and self._resolve_country(_trimmed):
            v = _trimmed
        low = v.casefold()
        if low in {"unknown", "not specified", "n/a", "na", "none", "tbd", "-"}:
            return "Unknown"
        if low in self._LOC_KEEP_MARKERS:
            return v
        # Being a real place wins over every other rule. Checked FIRST
        # because the company-name test below is a token-subset test, and
        # plenty of companies are named after where they are: replaying this
        # over the 11,177 located rows in scraped_career_jobs.csv, running
        # it first threw away "Italia" for Piazza Italia / Penny Italia /
        # Eataly Italia, "Italy" for NH Hotel Group Italy and "Firenze" for
        # Unicoop Firenze -- 38 correct rows. Ordered this way the same
        # replay rejects 0.
        try:
            if self._location_from_line(v):
                return v
        except Exception:
            return v
        try:
            if country_from_location is not None and (country_from_location(v) or "").strip():
                return v
        except Exception:
            pass
        if low in self.KNOWN_PLACES or self._norm(low) in self.NORM_KNOWN:
            return v
        # W3-2: a real town the core gazetteer does not list (Cuneo, Udine,
        # Ludwigsburg, Garching, Wels ...).
        if self._supplementary_country(v):
            return v
        # Not a place. Is it just the employer's own name? ("Amazon",
        # "In Amazon", "Amazon Web Services")
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
        return "Unknown"

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
                    # FIX P0-47: accept a three-letter ISO country code as a
                    # segment. Without this the backward walk treated "ITA"
                    # as an unknown office name, stopped, and returned None
                    # for the whole string.
                    if re.fullmatch(r"[a-z]{3}", sg_low) and sg_low in _ISO3_COUNTRY_CODES:
                        return True
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
                    # FIX P0-33: STATE_CODES only covers US + Canada (64 codes),
                    # so every OTHER country's subdivision abbreviation fell
                    # through to COUNTRY_CODES and was expanded into a foreign
                    # country: "Gurugram, HR, India" -> "Gurugram, Croatia,
                    # India" (HR = Haryana, but also ISO-3166 for Croatia).
                    # 48 two-letter codes collide this way (GA, DE, CA, MA,
                    # IN, LA, TN, AN, ...). Rule: an explicitly named country
                    # in the same string always wins -- a 2-letter token can
                    # then only be a subdivision OF that country, so keep it
                    # as a bare code instead of expanding it.
                    # NB: config.COUNTRIES_AND_REGIONS must NOT be used here --
                    # it also contains CITIES ("paris", "berlin", "london"), so
                    # it would treat "Paris, FR" as "a country is named" and
                    # stop FR from resolving to France. Anchor only on true
                    # country names: the scanner's own COUNTRY_CODES values
                    # plus the canonical ISO2_TO_COUNTRY map from
                    # core.location_country (84 names), so countries missing
                    # from the scanner's 66-name list are still recognised.
                    _named = None
                    for _s in keep:
                        _sl = _s.strip().lower()
                        if len(_sl) > 2 and _sl in self._country_name_set():
                            _named = _sl
                            break
                    # FIX P0-48: P0-33 only half-closed the ISO2/subdivision
                    # collision. Its anchor needs an explicit country NAME in
                    # the same string, so the three-part "Gurugram, HR, India"
                    # was saved while the two-part "Gurugram, HR" -- the form
                    # American Express actually emits -- still expanded to
                    # "Gurugram, Croatia" and the row was filed under Croatia.
                    # Second source of truth: the city itself. If the city
                    # resolves to a country and that country contradicts the
                    # code's expansion, the token is a subdivision, not a
                    # country, and is DROPPED rather than guessed at -- an
                    # absent region is recoverable, a wrong country is not.
                    # Deliberately NOT extended to the uncorroborated case.
                    # Replayed over the 3,151 distinct location strings in
                    # scraped_career_jobs.csv, also refusing to expand a code
                    # merely because it COULD be a subdivision cost 84 rows
                    # their correct country ("Temuco, CL" -> Chile,
                    # "Senningerberg, LU" -> Luxembourg, "rouen, 76, FR" ->
                    # France) and fixed nothing corroboration had not already
                    # fixed. An unknown city therefore keeps the historic
                    # behaviour: expand.
                    _has_amb = any(
                        re.fullmatch(r"[a-z]{2}", _s.strip().lower())
                        and _s.strip().lower() in self.COUNTRY_CODES
                        and _s.strip().lower() not in self.STATE_CODES
                        for _s in keep)
                    _city_c = (self._corroborating_country(list(keep) + list(segs))
                               if (_named is None and _has_amb) else None)
                    mapped_keep = []
                    for seg in keep:
                        code = seg.lower()
                        if re.fullmatch(r"[a-z]{3}", code) and code in _ISO3_COUNTRY_CODES:
                            mapped_keep.append(self._iso3_country_name(code) or seg.upper())
                        elif re.fullmatch(r"[a-z]{2}", code) and code in self.STATE_CODES:
                            mapped_keep.append(code.upper())
                        elif re.fullmatch(r"[a-z]{2}", code) and code in self.COUNTRY_CODES:
                            _exp = self.COUNTRY_CODES[code]
                            if _named and _exp.lower() != _named:
                                mapped_keep.append(code.upper())
                            elif _city_c and _exp.casefold() != _city_c.casefold():
                                continue        # contradicted -> drop the token
                            else:
                                mapped_keep.append(_exp)
                        else:
                            mapped_keep.append(seg)
                    # Re-dedupe: expansion can collide with an existing segment
                    # ("Zagreb, HR, Croatia" -> Croatia twice).
                    _seen2 = set(); _dd = []
                    for seg in mapped_keep:
                        k2 = seg.strip().lower()
                        if k2 and k2 not in _seen2:
                            _seen2.add(k2); _dd.append(seg)
                    keep = _dd
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
            # FIX P15: a trailing run made only of placeholder words
            # ("... Global", "... Remote") proves nothing. Drop them; if
            # nothing real is left, this is not a location.
            known_run = [w for w in known_run if not _is_pseudo_place(w)]
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
            # FIX P15: the leading token must be a real COUNTRY, not any
            # member of COUNTRIES_AND_REGIONS. "Netherlands Amstelveen" is a
            # reversed address; "Global Automotive" and "Remote Automotive"
            # are a scope word plus a business unit, and promoting the second
            # word to "city" invented locations that never existed.
            w0_country = (w0 in self.config.COUNTRIES_AND_REGIONS
                          and w0 not in self.KNOWN_CITIES
                          and not _is_pseudo_place(w0)
                          and w0 in self._country_name_set())
            if w0_country and not _is_pseudo_place(w1) \
                    and not self.LOCATION_REJECT_RE.search(w1) \
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
        # FIX P15: "Global" / "Remote" / "Worldwide" are scope words, not
        # places, and must never become a Job Location on their own.
        if _is_pseudo_place(low):
            return None
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
                # FIX P15: strip placeholder tokens; a phrase that is ONLY
                # placeholders ("global remote") is not a location.
                real = [w for w in words if not _is_pseudo_place(w)]
                if not real:
                    return None
                return ", ".join(self._fmt_place(self._norm(w)) for w in real)

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
    # ── FIX P0-41: unlisted towns + country from the POSTING'S OWN SITE ───
    # Every location path in this scanner was gated on KNOWN_PLACES (1,809
    # mostly-large cities), so a small town simply vanished: Action Italia's
    # "Vigliano Biellese , Via della Tollegna 1" and "Cittaducale , Viale
    # delle scienze 18/20" both resolved to Not Specified -> Job Location
    # "Unknown" -> Country "Unknown", even though the JD states the town and
    # the posting lives on it.action.jobs. That breaks local job search for
    # European users, who are mostly applying to exactly these smaller towns.
    #
    # Note this is NOT the company-HQ guess that P0-38 removed. The street
    # address comes from the job description itself, and the country comes
    # from the host the posting is served on -- both are properties of the
    # POSTING, not of the employer's headquarters.
    _STREET_WORDS = (r"via|viale|v\.le|piazza|piazzale|p\.zza|corso|c\.so|strada|"
                     r"largo|vicolo|contrada|localit[àa]|lungomare|rue|avenue|"
                     r"boulevard|calle|carrer|rua|stra[sß]e|strasse|weg|allee|"
                     r"platz|laan|straat|street|road|avenida")

    def _town_from_address(self, text, company=""):
        """Pull the town out of a street address the gazetteer does not know.

        Handles "<Town>, Via della Tollegna 1" and "Via X 1, <Town>" plus the
        continental "<postcode> <Town>" form. Returns None when nothing that
        looks like a town name is present.
        """
        if not text:
            return None
        t = re.sub(r"\s+", " ", str(text))[:400]
        word = r"[A-ZÀ-ÖØ-Þ][\w'’\-\.]*(?:\s+(?:di|de|del|della|sul|sotto|a|in)\s+[A-ZÀ-ÖØ-Þ]?[\w'’\-\.]*)?"
        name = rf"{word}(?:\s+{word}){{0,2}}"
        # "<Town> , Via della Tollegna 1"
        m = re.search(rf"({name})\s*,\s*(?:{self._STREET_WORDS})\b", t, re.IGNORECASE | re.UNICODE)
        if not m:
            # "Via della Tollegna 1, <Town>"
            m = re.search(rf"(?:{self._STREET_WORDS})\b[^,]{{0,60}},\s*({name})", t, re.IGNORECASE | re.UNICODE)
        if not m:
            # "<postcode> <Town>"  (IT/DE/FR/ES 4-5 digit postcodes)
            m = re.search(rf"\b\d{{4,5}}\s+({name})\b", t)
        if not m:
            return None
        cand = re.sub(r"\s+", " ", m.group(1)).strip(" ,.-")
        if not cand or len(cand) < 3 or len(cand.split()) > 3:
            return None
        # FIX P37: the text in front of a street is often the EMPLOYER.
        if _ORG_LEGAL_FORM_RE.search(cand) or _ORG_WORD_RE.search(cand):
            return None
        # P37b (run 20261006T225923): "SIAT" survived the two rules above --
        # a bare acronym carries no legal form and no corporate noun. Real
        # town names are written in title case ("Bologna", "Serravalle
        # Scrivia", "Cittaducale"); an all-capitals token is an initialism,
        # not a place.
        if not re.search(r"[a-zà-öø-ÿ]", cand):
            return None
        # ... and so is the company whose page this is.
        _cowords = {w for w in re.findall(r"[A-Za-zÀ-ÿ]{3,}", str(company or ""))}
        if _cowords and {w for w in re.findall(r"[A-Za-zÀ-ÿ]{3,}", cand)} & _cowords:
            return None
        low = cand.lower()
        if self.config.ROLE_WORD_PATTERN.search(cand):
            return None
        if low in self._junk_town_words():
            return None
        # P37c: "The Delivery Station", "In Amazon" -- prose, not a place.
        if low.split()[0] in self._FUNCTION_FIRST_WORDS:
            return None
        if not re.match(r"^[A-ZÀ-ÖØ-Þ]", cand):
            return None
        return cand

    @staticmethod
    def _junk_town_words():
        return {"codice", "riferimento", "calcolare", "distanza", "applicare",
                "ora", "sede", "indirizzo", "filiale", "negozio", "store",
                "adresse", "standort", "location", "address", "apply", "now",
                "reference", "job", "jobs", "contratto", "tempo", "full",
                "part", "time", "stelle", "azienda",
                # P37c (run 20261007T174338): Amazon published "Operations"
                # (x3) and "Our" as Job Location, source=address, confidence
                # HIGH -- prose that happened to sit in front of a street.
                # These are whole-candidate matches, so a real town is never
                # touched.
                "operations", "operation", "our", "your", "their", "the",
                "team", "teams", "department", "division", "warehouse",
                "office", "offices", "headquarters", "customer", "customers",
                "service", "services", "support", "delivery", "station",
                "network", "area", "region", "site", "centre", "center",
                "plant", "hub", "campus", "building", "floor", "unit"}

    #: Leading words that mark prose rather than a place name.
    _FUNCTION_FIRST_WORDS = frozenset({
        "the", "our", "your", "their", "its", "his", "her", "a", "an",
        "in", "at", "on", "to", "of", "for", "from", "with", "by",
        "il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "del",
        "della", "dei", "delle", "nel", "nella", "presso",
        "der", "die", "das", "den", "dem", "ein", "eine", "bei", "im",
        "de", "het", "een", "les", "des", "du", "el", "los", "las"})

    #: ccTLDs that are sold as vanity domains and say nothing about location.
    _VANITY_TLDS = frozenset({
        "io", "ai", "co", "me", "tv", "cc", "ws", "fm", "ly", "to", "gg",
        "im", "je", "sh", "st", "vc", "nu", "bz", "cx", "mu", "ms", "tk",
        "ml", "ga", "cf", "gq", "am", "fo", "ag", "sc", "la", "ki", "mn",
    })

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

    #: FIX P55 (2026-10-07): a "#job=<slug>" anchor is a fragment, so it is
    #: the SAME document as the listing page -- _detail_location_from_url
    #: refuses to fetch it (rightly: there is nothing new to fetch). That
    #: makes the slug the only place the location can ever come from, and it
    #: was never read. Run 20261007T215944 shipped four FxPro rows as Unknown
    #: whose own URL ends "--cyprus-ypsonas-hybrid".
    _URL_FRAGMENT_JOB_RE = re.compile(r"#job=([^&]+)", re.I)

    def _location_from_url_fragment(self, url):
        """Location named inside a '#job=' slug, else ''. FIX P55."""
        m = self._URL_FRAGMENT_JOB_RE.search(str(url or ""))
        if not m:
            return ""
        try:
            slug = urllib.parse.unquote(m.group(1))
        except Exception:
            slug = m.group(1)
        # The trailing chunk after "--" is the location/qualifier tail that
        # these boards append; fall back to the whole slug when absent.
        tail = slug.split("--")[-1] if "--" in slug else slug
        text = re.sub(r"[-_+]+", " ", tail).strip()
        if not text:
            return ""
        # Reuse P40's window scan: every 1-3 word window through
        # extract_location, longest wins. Same parser, same gazetteer.
        got = self._location_from_title_text(text) or ""
        # P0-12 keeps work mode OUT of Job Location: "--remote" is a work
        # mode, not a place, and classify_work_mode already reads it.
        if got and self._norm(got) in {
                "remote", "hybrid", "onsite", "on site", "office",
                "work from home", "wfh", "anywhere"}:
            return ""
        return got

    def _country_from_site(self, *urls):
        """Country implied by the host a posting is served from.

        "it.action.jobs" -> Italy (country subdomain); "action.de" -> Germany
        (ccTLD). Vanity ccTLDs (.io/.ai/.co/...) are ignored, and so is any
        host whose country label is absent from ISO2_TO_COUNTRY.
        """
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
            # leading country label: it.action.jobs, de.example.com
            head = labels[0]
            if (len(head) == 2 and head not in ("ww",) and head in ISO2_TO_COUNTRY
                    and head not in self._VANITY_TLDS):
                return ISO2_TO_COUNTRY[head]
            tld = labels[-1]
            if tld == "uk" and "gb" in ISO2_TO_COUNTRY:
                return ISO2_TO_COUNTRY["gb"]
            if (len(tld) == 2 and tld in ISO2_TO_COUNTRY
                    and tld not in self._VANITY_TLDS):
                return ISO2_TO_COUNTRY[tld]
        return None

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

        # v7: card/listing text cannot prove immigration support or Blue Card.
        eu_blue_card = "Unknown"
        reloc_support = "Unknown"
        # FIX P27: was a flat keyword sweep over title+card text with
        # part-time tested FIRST, so Amazon's pay footnote ("...on a
        # full-time basis. For part-time hours, the salary will be
        # pro-rated.") typed 121 of 183 corporate roles as Part-time.
        # classify_workload() reads the title before the prose and drops
        # pay/pro-rata sentences; identical bytes in ats_scanner.py (P27b).
        workload = classify_workload(title=title, text=context_text) or "Unknown"

        work_mode = classify_work_location_mode(combined) or "Unknown"

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
        # FIX P55: the "#job=" slug, for rows the detail fetch can never reach.
        if location == "Not Specified" and clean_url and "#job=" in clean_url.lower():
            _frag = self._location_from_url_fragment(clean_url)
            if _frag and self._norm(_frag) not in {"global", "worldwide", "united"}:
                location, source = _frag, "url"
        if location == "Not Specified":
            reg = self.extract_country_or_region(context_text, end_of_string=False)
            if reg and self._norm(reg) not in {"global", "worldwide", "united"}:
                location, source = reg, "region"
        # FIX P0-60 (S-12): last resort -- the card said "2 Locations" and
        # nothing else. Report that instead of a blank cell. The row stays
        # weak for _detail_priority, so a detail visit can still replace this
        # with the real sites.
        if location == "Not Specified":
            for _ln in str(context_text or "").splitlines():
                if _MULTI_LOCATION_RE.match(_ln.strip(" \t,;|-")):
                    location, source = _MULTI_LOCATION_LABEL, "multi_chip"
                    break

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
        # FIX P0-37: job_location scope leaked. _scope_country_match() blobs
        # location + page CONTEXT + URL together, so one mention of the target
        # country anywhere on the page -- a recruiter's own "Eindhoven,
        # Netherlands" header, a footer address, a language switcher -- marked
        # EVERY job on that page as in-scope. A2G Technologies
        # (target=Netherlands, scope_policy=job_location) therefore ingested 10
        # Pune/Hyderabad/Bangalore jobs. A "job_location" policy means exactly
        # what it says: match the JOB's own location and nothing else. Page
        # context stays in play for "seed_url" policy above, where a
        # page-level signal is the whole point.
        return self._scope_country_match(target, location, "", "")

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
            # Major cities per country, so a job_location scope can actually
            # match a real posting. (The "india" set was added for the A2G
            # retarget of 2026-09-28, which has since been superseded -- A2G
            # posts in both regions and is now scoped Global. The city list
            # itself stays: India is a real scope target for other rows.)
            "india": {"india", "bharat", "pune", "mumbai", "bombay", "delhi", "new delhi", "bengaluru", "bangalore", "hyderabad", "chennai", "madras", "kolkata", "calcutta", "ahmedabad", "noida", "gurgaon", "gurugram", "kochi", "cochin"},
        }
        if any(re.search(r"(?:^|[^a-z])" + re.escape(a) + r"(?:$|[^a-z])", blob)
               for a in aliases.get(target.casefold(), {target.casefold()})):
            return True
        # W3-2: the hand-written alias table above lists ~20 cities per
        # country, so a job in Cuneo or Ludwigsburg failed an Italy/Germany
        # scope test that it should pass. Fall back to the resolver.
        try:
            return bool(location) and (
                self._resolve_country(location) or "").casefold() == target.casefold()
        except Exception:
            return False

    # ── Batch N phase 2a custom provider adapters ─────────────────────────
    # ───────── provider sniffing / cache (FIX W1-1) ─────────
    def _provider_cache_path(self):
        """Where the sniffed provider verdicts live (app data dir if present)."""
        try:
            from sponsorscout.paths import DATA_DIR  # app install
            return os.path.join(str(DATA_DIR), "provider_cache.json")
        except Exception:
            base = os.path.dirname(os.path.abspath(self.output_csv or ".")) or "."
            return os.path.join(base, "provider_cache.json")

    def _load_provider_cache(self):
        try:
            with open(self._provider_cache_path(), encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_provider_cache(self, cache):
        try:
            path = self._provider_cache_path()
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, indent=1, sort_keys=True)
        except Exception:
            pass

    def _sniff_one_target(self, target_row):
        """One cheap HTTP fetch of a seed page -> (provider, slug, note)."""
        # STOP FAST: queued sniff tasks must drain instantly on Stop/pause
        # instead of running full HTTP fetches nobody will read — the sniff
        # pool's context manager waits for every submitted task to finish.
        if check_control(self.cancel_event, self.pause_event):
            return "", "", ""
        url = (target_row.get("careers_url") or "").strip()
        if not url.startswith("http"):
            return "", "", ""
        # The seed URL itself is often already the fingerprint.
        prov, slug, note = sniff_provider("", url)
        if prov:
            return prov, slug, note
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 Chrome/126 Safari/537.36"),
                "Accept": "text/html,application/xhtml+xml,*/*",
            })
            with urllib.request.urlopen(
                    req, timeout=self.config.PROVIDER_SNIFF_TIMEOUT_SEC) as resp:
                html = resp.read(400000).decode("utf-8", "replace")
                final = resp.geturl() or url
        except Exception:
            return "", "", ""
        return sniff_provider(html, final)

    def _resolve_auto_providers(self, targets):
        """Resolve provider=auto rows to a real adapter before crawling.

        Runs in the HTTP pool (cheap), uses a disk cache, and only ever
        touches the in-memory row -- the seed file is never rewritten.
        """
        import concurrent.futures as _cf
        if not self.config.PROVIDER_SNIFF:
            return
        pending = [t for t in targets
                   if (t.get("provider") or "auto").strip().lower() in ("", "auto")]
        if not pending:
            return
        cache = self._load_provider_cache()
        now = time.time()
        ttl = self.config.PROVIDER_CACHE_TTL_DAYS * 86400
        todo, hits, notes = [], 0, {}
        for t in pending:
            key = (t.get("careers_url") or "").strip().lower()
            ent = cache.get(key)
            if isinstance(ent, dict) and (now - float(ent.get("ts") or 0)) < ttl:
                if ent.get("provider"):
                    t["provider"] = ent["provider"]
                    t["board_slug"] = ent.get("slug") or t.get("board_slug") or ""
                    t["_provider_source"] = "cache"
                    hits += 1
                elif ent.get("note"):
                    t["_provider_note"] = ent["note"]
                continue
            todo.append(t)
        if todo:
            workers = recommended_workers("http")
            print(f"   [sniff] resolving {len(todo)} provider=auto seed(s) "
                  f"with {workers} HTTP worker(s)...")
            with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(self._sniff_one_target, t): t for t in todo}
                for fut in _cf.as_completed(futs):
                    t = futs[fut]
                    if check_control(self.cancel_event, self.pause_event):
                        # STOP FAST: cancel the queued sniffs too — the pool's
                        # context manager waits for every submitted task, and
                        # un-cancelled queued tasks would each pay an HTTP
                        # fetch the scan will never read.
                        for _f in futs:
                            _f.cancel()
                        break
                    try:
                        prov, slug, note = fut.result()
                    except Exception:
                        prov, slug, note = "", "", ""
                    key = (t.get("careers_url") or "").strip().lower()
                    cache[key] = {"provider": prov, "slug": slug,
                                  "note": note, "ts": now}
                    if prov:
                        t["provider"] = prov
                        t["board_slug"] = slug or t.get("board_slug") or ""
                        t["_provider_source"] = "sniff"
                        hits += 1
                    elif note:
                        t["_provider_note"] = note
                        notes[note] = notes.get(note, 0) + 1
            self._save_provider_cache(cache)
        if hits or notes:
            extra = ("; recognised-but-no-adapter: "
                     + ", ".join(f"{k} x{v}" for k, v in sorted(notes.items()))
                     ) if notes else ""
            print(f"   [sniff] {hits}/{len(pending)} auto seeds resolved to a "
                  f"provider API{extra}")

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
            # W3-4: the list payload already contains the whole job ad --
            # it was being thrown away, so every Pam row scored "Unknown"
            # on visa/relocation/Blue Card for no reason. The board has no
            # per-job page (verified: /offerta/<id>, /job/<id> and
            # ?job=<id> all return the same 465-byte shell), so the URL
            # stays the listing page + #job=<id>, which at least opens.
            _pam_desc = (it.get("description") or det.get("description") or "")
            jobs.append({
                "job_title": title,
                "job_url": f"{seed_url}#job={jid}",
                "location_hint": ", ".join(x for x in (city, state, "Italia") if x),
                "card_context": " | ".join(x for x in (contract, dept) if x),
                "jd_text": _jd_plain(_pam_desc) if _pam_desc else "",
                "extraction_method": "pam_api",
            })
        return jobs, f"pam API: {len(jobs)} ({slug}; skipped {skipped_spont} spontaneous)"

    def _fetch_digitalrecruiters_jobs(self, target_row):
        """DigitalRecruiters/Cegid boards (POST JSON API).

        board_slug: the careers-site host, e.g.
        lavoraconnoi.decathlon-careers.it. POSTs
        {"filters": {}, "coordinates": {"lat": 0, "lng": 0}} with
        limit=100 + page loop.

        FIX W3-4 (P8): the old comment claimed the site exposes no detail
        pages, so every row got "<seed>#job=<slug>" -- 167 unopenable URLs
        in run 20261003T233023, the single largest block of them. It does:
        https://<domain>/<lang>/annonce/<url-slug> is server-rendered (57 KB
        with og:title = the job title; the listing page is 40 KB). The API
        also has a per-ad endpoint,
        GET /public/v1/careers-site/job-ads/<job_ad_id>?domainName=<domain>,
        which returns description + profile (the JD) and a structured
        address with city/state/COUNTRY. Both are used now: real URLs for
        every row, JD text and an exact location for the first
        SPONSORSCOUT_DIGITALRECRUITERS_DETAILS of them.
        """
        slug = (target_row.get("board_slug") or "").strip()
        seed_url = (target_row.get("careers_url") or "").strip().rstrip("/")
        if not slug or not seed_url:
            return [], "digitalrecruiters API skipped: need board_slug + careers_url"
        headers = {"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36",
                   "Accept": "application/json",
                   "Origin": f"https://{slug}", "Referer": seed_url}
        # language segment of the public site ("/it/annonces" -> "it")
        _dr_parts = [p for p in urlparse(seed_url).path.split("/") if p]
        _dr_lang = _dr_parts[0] if (_dr_parts and len(_dr_parts[0]) == 2) else "en"
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
                job_slug = (it.get("url") or "").strip()
                # W3-4: real, server-rendered detail URL when the API gave
                # us the slug; the old fragment only as a last resort.
                if job_slug:
                    job_url = f"https://{slug}/{_dr_lang}/annonce/{job_slug}"
                else:
                    job_url = f"{seed_url}#job={jid}"
                jobs.append({
                    "job_title": title,
                    "job_url": job_url,
                    "location_hint": (it.get("location") or "").strip(),
                    "card_context": " | ".join(x for x in ((it.get("job") or ""), (it.get("contract") or "")) if x),
                    "extraction_method": "digitalrecruiters_api",
                    "_dr_id": jid,
                })
            total = data.get("count") or 0
            if len(items) < 100 or (total and len(seen) >= total):
                break
            page += 1
        # W3-4: per-ad detail -> JD text + an exact, structured location.
        note = f"digitalrecruiters API: {len(jobs)}"
        try:
            cap = int(os.environ.get("SPONSORSCOUT_DIGITALRECRUITERS_DETAILS", "150"))
        except ValueError:
            cap = 150
        if cap > 0 and jobs:
            got = 0
            for job in jobs[:cap]:
                jid = job.pop("_dr_id", "")
                if not jid:
                    continue
                try:
                    det = json.loads(self._http_fetch_with_retry(
                        f"https://api.digitalrecruiters.com/public/v1/"
                        f"careers-site/job-ads/{jid}?domainName={slug}",
                        headers))
                except Exception:
                    continue
                body = " ".join(x for x in (det.get("description") or "",
                                            det.get("profile") or "") if x)
                if body:
                    job["jd_text"] = _jd_plain(body)
                    got += 1
                addr = det.get("address") or {}
                city = (addr.get("city") or "").strip()
                country = (addr.get("country") or "").strip()
                if city and country:
                    job["location_hint"] = f"{city}, {country}"
                elif (det.get("formatted_address") or "").strip():
                    job["location_hint"] = det["formatted_address"].strip()
            note += f" ({got} with JD text)"
        for job in jobs:
            job.pop("_dr_id", None)
        return jobs, note

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

    # ── FIX P19 (2026-10-04): the two boards that returned nothing ───────
    # Run 20261004T182639 logged "provider adapter not configured (icims
    # board)" for DocuSign and "(eightfold board)" for HP: the sniffer
    # RECOGNISED both and then still fell through to a Chromium DOM crawl
    # that produced 0 jobs. Both platforms publish a JSON feed.
    def _careersite_api_hosts(self, target_row):
        """Candidate hosts for a career-site JSON API, strongest first.

        The board fingerprint (`*.icims.com`) is almost never the host that
        serves the API: DocuSign's seed is www.docusign.com, the iframe says
        emeacareers-docusign.icims.com, and the feed lives on
        careers.docusign.com. Probing is cheap and bounded (<=4 HEAD-sized
        GETs) and the result is only used in memory.
        """
        seed = (target_row.get("careers_url") or "").strip()
        host = urlparse(seed).netloc
        slug = (target_row.get("board_slug") or "").strip()
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

    def _fetch_icims_jobs(self, target_row):
        """iCIMS career-site (Jibe) JSON feed.

        GET https://<host>/api/jobs?page=N&limit=100&internal=false
            -> {"jobs":[{"data":{title, slug, city, state, country,
                                 full_location, description, qualifications,
                                 employment_type, tags2, apply_url}}],
                "totalCount": N}
        Verified live against careers.docusign.com on 2026-10-04:
        255 postings, 24 of them in Ireland -- the seed that this run logged
        as 0 jobs found.
        """
        headers = {"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36",
                   "Accept": "application/json"}
        api_host = ""
        first = None
        for host in self._careersite_api_hosts(target_row):
            try:
                raw = self._http_fetch_with_retry(
                    f"https://{host}/api/jobs?page=1&limit=100"
                    "&sortBy=relevance&internal=false", headers)
            except Exception:
                continue
            if not raw.lstrip().startswith("{"):
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if isinstance(data.get("jobs"), list):
                api_host, first = host, data
                break
        if not api_host:
            return [], "icims API not reachable on any career host; falling back to DOM"

        def _rows(payload):
            out = []
            for entry in payload.get("jobs") or []:
                d = (entry or {}).get("data") or {}
                title = str(d.get("title") or "").strip()
                slug = str(d.get("slug") or d.get("req_id") or "").strip()
                if not title or not slug:
                    continue
                loc = str(d.get("full_location") or "").strip()
                if not loc:
                    loc = ", ".join(x for x in (
                        d.get("city"), d.get("state"), d.get("country")) if x)
                jd = " ".join(str(d.get(k) or "") for k in
                              ("description", "qualifications", "responsibilities"))
                tags = d.get("tags2")
                tags = tags if isinstance(tags, list) else ([tags] if tags else [])
                # FIX P19b: the feed ships SCREAMING_SNAKE enums
                # ("FULL_TIME"); the workload classifier reads human text, so
                # an unmapped value left Job Type as "Unknown / ...".
                _emp = str(d.get("employment_type") or "").strip().upper()
                _emp = {"FULL_TIME": "Full-time", "PART_TIME": "Part-time",
                        "INTERN": "Internship", "INTERNSHIP": "Internship",
                        "CONTRACTOR": "Contract", "CONTRACT": "Contract",
                        "TEMPORARY": "Contract", "TEMP": "Contract",
                        "VOLUNTEER": "Volunteer", "PER_DIEM": "Contract",
                        "OTHER": ""}.get(_emp, _emp.replace("_", " ").title())
                out.append({
                    "job_title": title,
                    "job_url": f"https://{api_host}/jobs/{slug}?lang=en-us",
                    "location_hint": loc,
                    "card_context": " | ".join(
                        str(x) for x in ([d.get("category"), _emp]
                                         + list(tags)) if x),
                    "jd_text": _jd_plain(jd),
                    "extraction_method": "icims_api"})
            return out

        jobs = _rows(first)
        total = int(first.get("totalCount") or first.get("count") or len(jobs))
        page = 2
        while len(jobs) < min(total, 2000) and page <= 25:
            if check_control(self.cancel_event, self.pause_event):
                break
            try:
                raw = self._http_fetch_with_retry(
                    f"https://{api_host}/api/jobs?page={page}&limit=100"
                    "&sortBy=relevance&internal=false", headers)
                batch = _rows(json.loads(raw))
            except Exception:
                break
            if not batch:
                break
            jobs.extend(batch)
            page += 1
        return jobs, f"icims API ({api_host}): {len(jobs)} of {total}"

    def _fetch_eightfold_jobs(self, target_row):
        """Eightfold.ai talent-hub feed.

        GET https://<host>/api/apply/v2/jobs?domain=<domain>&start=N&num=50
            -> {"count": N, "positions":[{name, location(s), id,
                                          canonicalPositionUrl, job_description}]}
        NOT verified against a live tenant: HP's host (careers.hp.com) does
        not resolve from this machine and no other Eightfold board was
        reachable, so the parser is written defensively -- any unexpected
        shape returns no rows and the company falls back to the DOM crawl it
        already uses today. Nothing regresses if the guess is wrong.
        """
        headers = {"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36",
                   "Accept": "application/json"}
        seed = (target_row.get("careers_url") or "").strip()
        host = (target_row.get("board_slug") or "").strip() or urlparse(seed).netloc
        parts = host.split(".")
        domain = ".".join(parts[-2:]) if len(parts) >= 2 else host
        jobs, start, total = [], 0, None
        while start < 2000:
            if check_control(self.cancel_event, self.pause_event):
                break
            api = (f"https://{host}/api/apply/v2/jobs?domain={domain}"
                   f"&start={start}&num=50&exclude_pills=true")
            try:
                raw = self._http_fetch_with_retry(api, headers)
            except Exception as exc:
                if not jobs:
                    return [], (f"eightfold API unavailable "
                                f"({type(exc).__name__}); falling back to DOM")
                break
            if not raw.lstrip().startswith("{"):
                return [], "eightfold API returned non-JSON; falling back to DOM"
            try:
                data = json.loads(raw)
            except Exception:
                return [], "eightfold API returned unparsable JSON; falling back to DOM"
            if str(data.get("status") or "").lower() == "failure":
                return [], (f"eightfold API refused the tenant "
                            f"({data.get('errorMsg')}); falling back to DOM")
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
                title = str(it.get("name") or it.get("title") or "").strip()
                if not title:
                    continue
                loc = it.get("location")
                if not loc:
                    locs = it.get("locations")
                    loc = (locs[0] if isinstance(locs, list) and locs else "")
                url = str(it.get("canonicalPositionUrl") or "").strip()
                if not url:
                    pid = str(it.get("id") or it.get("display_job_id") or "").strip()
                    if not pid:
                        continue
                    url = f"https://{host}/careers/job/{pid}"
                jobs.append({
                    "job_title": title,
                    "job_url": url,
                    "location_hint": str(loc or "").strip(),
                    "card_context": " | ".join(str(x) for x in (
                        it.get("department"), it.get("work_location_option"),
                        it.get("type")) if x),
                    "jd_text": _jd_plain(str(it.get("job_description") or "")),
                    "extraction_method": "eightfold_api"})
            if len(positions) < 50:
                break
            start += 50
        if not jobs:
            return [], "eightfold API returned no positions; falling back to DOM"
        return jobs, f"eightfold API ({host}): {len(jobs)}"

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

    def _fetch_bamboohr_jobs(self, target_row):
        """BambooHR public board. Mirrors ats_scanner.scan_bamboohr exactly.

        Added 2026-10-03 so the two scanners stay interchangeable: a row moved
        between the seeds must behave the same in either file.
        /careers/list carries no JD text, so the per-job /detail call is what
        makes a sponsorship verdict possible at all. Budget:
        SPONSORSCOUT_BAMBOOHR_DETAILS (default 150 per company).
        """
        url = (target_row.get("careers_url") or "").strip()
        host = urlparse(url).netloc
        slug = (target_row.get("board_slug") or "").strip() or (host.split(".")[0] if host else "")
        if not slug:
            return [], "bamboohr: no slug"
        headers = {
            "User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36",
            "Accept": "application/json,*/*",
        }
        raw = self._http_fetch_with_retry(f"https://{slug}.bamboohr.com/careers/list", headers)
        data = json.loads(raw)
        openings = (data or {}).get("result") or []
        try:
            budget = int(os.environ.get("SPONSORSCOUT_BAMBOOHR_DETAILS", "150"))
        except (TypeError, ValueError):
            budget = 150
        jobs = []
        for job in openings:
            jid = str(job.get("id") or "").strip()
            if not jid:
                continue
            loc = job.get("location") or {}
            ats_loc = job.get("atsLocation") or {}
            desc = ""
            country = ""
            if budget > 0:
                budget -= 1
                try:
                    draw = self._http_fetch_with_retry(
                        f"https://{slug}.bamboohr.com/careers/{jid}/detail", headers)
                    opening = ((json.loads(draw) or {}).get("result") or {}).get("jobOpening") or {}
                    desc = unescape(re.sub(r"<[^>]+>", " ", opening.get("description") or ""))
                    desc = re.sub(r"\s+", " ", desc).strip()
                    d_loc = opening.get("location") or {}
                    country = (d_loc.get("addressCountry") or "").strip()
                    if not loc:
                        loc = d_loc
                except Exception:
                    desc, country = "", ""
            parts = [
                (loc.get("city") or ats_loc.get("city") or "").strip(),
                (loc.get("state") or ats_loc.get("state") or ats_loc.get("province") or "").strip(),
                country or (ats_loc.get("country") or "").strip(),
            ]
            parts = [p for p in parts if p]
            # Same city/state-swap repair as the ATS adapter.
            try:
                if country_from_location is not None:
                    for _i, _p in enumerate(list(parts[:-1])):
                        if (country_from_location(_p) or "").strip().casefold() == _p.casefold():
                            parts.append(parts.pop(_i))
                            break
            except Exception:
                pass
            jobs.append({
                "job_title": job.get("jobOpeningName") or "",
                "job_url": f"https://{slug}.bamboohr.com/careers/{jid}",
                "location_hint": ", ".join(parts),
                "card_context": " | ".join(filter(None, [
                    job.get("departmentLabel") or "",
                    job.get("employmentStatusLabel") or "",
                ])),
                "jd_text": desc,
                "extraction_method": "bamboohr_api",
            })
        return jobs, f"bamboohr API: {len(jobs)}"

    def _fetch_workday_jobs(self, target_row):
        """Workday CXS public API (FIX W1-1b).

        board_slug is "<host>|<tenant>|<site>" as produced by sniff_provider,
        e.g. "adobe.wd5.myworkdayjobs.com|adobe|external_experienced".  A bare
        slug (just the site) is accepted too when the seed URL names the host.

        List : POST https://<host>/wday/cxs/<tenant>/<site>/jobs
               {"appliedFacets":{},"limit":20,"offset":N,"searchText":""}
               -> {"total":N,"jobPostings":[{title, externalPath,
                                             locationsText, bulletFields}]}
        Detail: GET https://<host>/wday/cxs/<tenant>/<site><externalPath>
               -> {"jobPostingInfo":{"jobDescription": "<html>", ...}}

        Workday boards are the single slowest thing in a DOM crawl (every
        page is client-rendered), which is exactly why the API path matters.
        """
        raw = (target_row.get("board_slug") or "").strip()
        seed_url = (target_row.get("careers_url") or "").strip()
        host = tenant = site = ""
        if raw.count("|") == 2:
            host, tenant, site = [x.strip() for x in raw.split("|")]
        else:
            m = _SNIFF_WORKDAY.search(seed_url)
            if m:
                host = m.group("host")
                tenant = m.group("tenant") or host.split(".")[0]
                site = raw or m.group("site")
        if not (host and tenant and site):
            return [], "workday API skipped: need host|tenant|site"
        headers = {"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36",
                   "Accept": "application/json"}
        api = f"https://{host}/wday/cxs/{tenant}/{site}"
        jobs, seen, offset, total = [], set(), 0, None
        while offset < 2000:
            try:
                data = json.loads(self._http_fetch_with_retry(
                    api + "/jobs", headers,
                    post_body={"appliedFacets": {}, "limit": 20,
                               "offset": offset, "searchText": ""}))
            except Exception as exc:
                if not jobs:
                    raise
                return jobs, f"workday API: {len(jobs)} (stopped: {type(exc).__name__})"
            postings = data.get("jobPostings") or []
            if total is None:
                total = data.get("total") or 0
            if not postings:
                break
            for it in postings:
                path = (it.get("externalPath") or "").strip()
                title = (it.get("title") or "").strip()
                if not path or not title or path in seen:
                    continue
                seen.add(path)
                jobs.append({
                    "job_title": title,
                    "job_url": f"https://{host}/{site}{path}",
                    "location_hint": (it.get("locationsText") or "").strip(),
                    "card_context": " | ".join(
                        str(x) for x in (it.get("bulletFields") or []) if x),
                    "extraction_method": "workday_api",
                    "_workday_detail": api + path,
                })
            offset += 20
            if total and len(seen) >= total:
                break
        # JD text: one extra GET per job, budgeted. Without it every Workday
        # row reports Unknown for sponsorship (the list carries no JD).
        try:
            budget = int(os.environ.get("SPONSORSCOUT_WORKDAY_DETAILS", "150"))
        except ValueError:
            budget = 150
        fetched = 0
        for job in jobs:
            if fetched >= budget or check_control(self.cancel_event, self.pause_event):
                break
            durl = job.pop("_workday_detail", "")
            if not durl:
                continue
            try:
                d = json.loads(self._http_fetch_with_retry(durl, headers))
            except Exception:
                continue
            info = (d or {}).get("jobPostingInfo") or {}
            desc = info.get("jobDescription") or ""
            if desc:
                job["jd_text"] = _jd_plain(desc)
                fetched += 1
            loc = (info.get("location") or "").strip()
            if loc and not job.get("location_hint"):
                job["location_hint"] = loc
        for job in jobs:
            job.pop("_workday_detail", None)
        return jobs, f"workday API: {len(jobs)} ({fetched} with JD text)"

    def _fetch_smartrecruiters_jobs(self, target_row):
        """SmartRecruiters public postings API (FIX W1-1c).

        List : https://api.smartrecruiters.com/v1/companies/<slug>/postings
               ?limit=100&offset=N  -> {"totalFound":N,"content":[...]}
        Detail: .../postings/<id> -> jobAd.sections.*.text (HTML)
        Public URL: https://jobs.smartrecruiters.com/<slug>/<id>
        """
        slug = (target_row.get("board_slug") or "").strip()
        if not slug:
            m = re.search(r"smartrecruiters\.com/([A-Za-z0-9_.-]+)",
                          target_row.get("careers_url") or "")
            slug = m.group(1) if m else ""
        if not slug:
            return [], "smartrecruiters API skipped: need board_slug"
        headers = {"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36",
                   "Accept": "application/json"}
        base = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
        jobs, seen, offset, total = [], set(), 0, None
        while offset < 2000:
            data = json.loads(self._http_fetch_with_retry(
                f"{base}?limit=100&offset={offset}", headers))
            content = data.get("content") or []
            if total is None:
                total = data.get("totalFound") or 0
            if not content:
                break
            for it in content:
                jid = str(it.get("id") or "").strip()
                title = (it.get("name") or "").strip()
                if not jid or not title or jid in seen:
                    continue
                seen.add(jid)
                loc = it.get("location") or {}
                parts = [loc.get("city"), loc.get("region"),
                         (loc.get("country") or "").upper()]
                jobs.append({
                    "job_title": title,
                    "job_url": f"https://jobs.smartrecruiters.com/{slug}/{jid}",
                    "location_hint": ", ".join(p for p in parts if p),
                    "card_context": " | ".join(filter(None, [
                        ((it.get("department") or {}) or {}).get("label") or "",
                        ((it.get("typeOfEmployment") or {}) or {}).get("label") or "",
                        "Remote" if loc.get("remote") else "",
                    ])),
                    "extraction_method": "smartrecruiters_api",
                    "_sr_detail": f"{base}/{jid}",
                })
            offset += 100
            if len(content) < 100 or (total and len(seen) >= total):
                break
        try:
            budget = int(os.environ.get("SPONSORSCOUT_SMARTRECRUITERS_DETAILS", "150"))
        except ValueError:
            budget = 150
        fetched = 0
        for job in jobs:
            durl = job.pop("_sr_detail", "")
            if fetched >= budget or not durl:
                continue
            if check_control(self.cancel_event, self.pause_event):
                break
            try:
                d = json.loads(self._http_fetch_with_retry(durl, headers))
            except Exception:
                continue
            sections = ((d or {}).get("jobAd") or {}).get("sections") or {}
            text = " ".join(
                str((sections.get(k) or {}).get("text") or "")
                for k in ("companyDescription", "jobDescription",
                          "qualifications", "additionalInformation"))
            if text.strip():
                job["jd_text"] = _jd_plain(text)
                fetched += 1
        for job in jobs:
            job.pop("_sr_detail", None)
        return jobs, f"smartrecruiters API: {len(jobs)} ({fetched} with JD text)"

    def _fetch_jobsinnetwork_jobs(self, target_row):
        """FIX P34 (2026-10-06): read a JobsinNetwork board from its own API.

        jobsinvienna.com -- and every sibling board (jobsinamsterdam,
        jobsinberlin, jobsinbrussels, ...) -- renders its job list entirely
        client-side. The served HTML carries NO job-detail links at all: the
        only /jobs/ hrefs are department chips (/jobs/Marketing,
        /jobs/Sales-and-Sales-Related). A run therefore wrote 13 chips as
        jobs and reached none of the real listings.

        The page hands us everything needed in a `globalVariables` block::

            apiUrl: 'https://search-api.jobsinnetwork.services',
            boardCountry: 'AT', boardCity: 'Vienna', locale: 'en',

        and that API is open and unauthenticated::

            GET /api/jobs?location.countryCode=AT&locations.city=Vienna
                         &language=en&page=1
            -> hydra:member[] with title, company.name, location.city,
               remote_type, employment_type, view_url and the FULL
               description.

        Because the config is read from the page, no seed URL changes and the
        adapter works for any board in the network. The description means
        these rows arrive with JD text, so sponsorship detection can run
        without a per-job detail visit.
        """
        seed_url = (target_row.get("careers_url") or "").strip()
        if not seed_url:
            return [], "jobsinnetwork: no seed url"
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0.0.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        shell = self._http_fetch_with_retry(seed_url, headers)
        if isinstance(shell, bytes):
            shell = shell.decode("utf-8", "replace")

        def _gv(key, default=""):
            m = re.search(key + r"\s*:\s*'([^']*)'", shell)
            return m.group(1).strip() if m else default

        api = _gv("apiUrl") or "https://search-api.jobsinnetwork.services"
        country = _gv("boardCountry")
        city = _gv("boardCity")
        locale = _gv("locale", "en")
        if not (country or city):
            return [], "jobsinnetwork: board config not found on page"

        params = []
        if country:
            params.append(("location.countryCode", country))
        if city:
            params.append(("locations.city", city))
        # Only English-language ads: this network aggregates German feeds too
        # (5,960 Vienna rows in all languages vs 339 in English).
        if locale:
            params.append(("language", locale))

        try:
            per_company = int(os.environ.get(
                "SPONSORSCOUT_JOBSINNETWORK_MAX", "600"))
        except (TypeError, ValueError):
            per_company = 600
        api_headers = {
            "User-Agent": headers["User-Agent"],
            "Accept": "application/ld+json",
            "Origin": f"https://{urlparse(seed_url).netloc}",
            "Referer": seed_url,
        }
        jobs, page, total = [], 1, None
        while len(jobs) < per_company and page <= 60:
            qs = urlencode(params + [("page", str(page))])
            raw = self._http_fetch_with_retry(f"{api}/api/jobs?{qs}",
                                              api_headers)
            data = json.loads(raw)
            members = data.get("hydra:member") or data.get("member") or []
            if total is None:
                total = data.get("hydra:totalItems") or data.get("totalItems")
            if not members:
                break
            for it in members:
                title = (it.get("title") or "").strip()
                url = (it.get("view_url") or it.get("url") or "").strip()
                if not title or not url:
                    continue
                loc = it.get("location") or {}
                # The feed mixes "Austria", "Wien" and a bare lowercase ISO
                # code ("at") in the same field; a 2-letter code must be
                # upper-cased or country_from_location() cannot resolve it.
                _country = (loc.get("country") or "").strip()
                if len(_country) == 2 and _country.isalpha():
                    _country = _country.upper()
                place = ", ".join(x for x in (
                    (loc.get("city") or "").strip(), _country) if x)
                if not place and city:
                    place = ", ".join(x for x in (city, country) if x)
                company = ((it.get("company") or {}).get("name") or "").strip()
                ctx = " | ".join(x for x in (
                    company,
                    str(it.get("employment_type") or ""),
                    str(it.get("work_hours") or ""),
                    ("remote" if str(it.get("remote_type") or "").lower()
                     not in ("", "no", "none") else ""),
                ) if x)
                jobs.append({
                    "job_title": title,
                    "job_url": url,
                    "location_hint": place,
                    "card_context": ctx[:800],
                    "jd_text": _jd_plain(it.get("description") or "")[:20000],
                    "extraction_method": "jobsinnetwork_api",
                })
            page += 1
        with_text = sum(1 for j in jobs if j["jd_text"])
        return jobs, (f"jobsinnetwork API: {len(jobs)} of {total} "
                      f"({with_text} with JD text)")

    def _fetch_provider_jobs(self, target_row):
        """Provider APIs first. Returns (jobs, diagnostic)."""
        provider = (target_row.get("provider") or "auto").lower()
        slug = (target_row.get("board_slug") or "").strip()
        # FIX P34: a JobsinNetwork board serves no job links in its HTML, so
        # it must go through the API or it yields nothing usable. Detected by
        # host so `provider=auto` seeds are covered without a seed edit.
        _host = urlparse((target_row.get("careers_url") or "")).netloc.lower()
        if provider == "jobsinnetwork" or re.match(
                r"^(?:www\.)?jobsin[a-z]+\.com$", _host):
            try:
                return self._fetch_jobsinnetwork_jobs(target_row)
            except Exception as exc:
                return [], (f"jobsinnetwork API failed: "
                            f"{type(exc).__name__}: {exc}")
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
        if provider == "bamboohr":
            try:
                return self._fetch_bamboohr_jobs(target_row)
            except Exception as exc:
                return [], f"bamboohr API failed: {type(exc).__name__}: {exc}"
        if provider == "workday":
            try:
                return self._fetch_workday_jobs(target_row)
            except Exception as exc:
                return [], f"workday API failed: {type(exc).__name__}: {exc}"
        if provider == "smartrecruiters":
            try:
                return self._fetch_smartrecruiters_jobs(target_row)
            except Exception as exc:
                return [], f"smartrecruiters API failed: {type(exc).__name__}: {exc}"
        # FIX P19: two boards that used to log "adapter not configured".
        if provider == "icims":
            try:
                return self._fetch_icims_jobs(target_row)
            except Exception as exc:
                return [], f"icims API failed: {type(exc).__name__}: {exc}"
        if provider == "eightfold":
            try:
                return self._fetch_eightfold_jobs(target_row)
            except Exception as exc:
                return [], f"eightfold API failed: {type(exc).__name__}: {exc}"
        if not slug or provider not in {"greenhouse", "ashby", "lever", "personio", "recruitee", "workable"}:
            _note = target_row.get("_provider_note") or ""
            if _note:
                # Sniffed, recognised, but we have no adapter: say which board
                # it is so a slow DOM crawl is explainable in the scan log.
                return [], f"provider adapter not configured ({_note} board)"
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
        # Direct-list seeds get a card-scope-independent anchor pass. The
        # ordinary anchor sweep correctly rejects shared list containers, but
        # that would also reject every posting on a simple server-rendered
        # listing such as 3 Banken IT. Anchor-only extraction is safe here
        # because we intentionally do not borrow list-container metadata.
        if self._is_direct_listing_seed_url(getattr(self, "_current_seed_url", "")):
            extractors.insert(0, self._direct_listing_anchor_rows)
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
        all_results = self._apply_embedded_index(all_results)
        all_results = self._drop_synthetic_twins(all_results)
        return all_results

    def _apply_embedded_index(self, rows):
        """FIX P28: fill a missing location from the page's hydration payload.

        The static pass parked every embedded-JSON record it could not build
        a url for. A DOM extractor that found the url but no location (the
        jobs.bendingspoons.com shape: 46 real `/positions/<id>` links, zero
        locations) borrows it here, matched on the title.
        """
        index = _EMBEDDED_INDEX.get(getattr(self, "_current_seed_url", "")) or {}
        if not index or not rows:
            return rows
        filled = 0
        for row in rows:
            if (row.get("location_hint") or "").strip():
                continue
            hit = index.get(_embed_title_key(row.get("job_title")))
            if not hit:
                continue
            row["location_hint"] = hit.get("location_hint") or ""
            if hit.get("card_context") and not (row.get("card_context") or "").strip():
                row["card_context"] = hit["card_context"]
            if hit.get("jd_text") and not (row.get("jd_text") or "").strip():
                row["jd_text"] = hit["jd_text"]
            if row["location_hint"]:
                filled += 1
        if filled:
            print(f"      embedded JSON supplied {filled} missing location(s)")
            try:
                self._embedded_notes.append(
                    f"embedded JSON: {filled} location(s) recovered")
            except AttributeError:
                self._embedded_notes = [
                    f"embedded JSON: {filled} location(s) recovered"]
        return rows

    @staticmethod
    def _drop_synthetic_twins(rows):
        """FIX P28: drop the `#job=<slug>` stand-in when the real url exists.

        The JSON extractor manufactures `<page>#job=<slug>` when a record has
        no url field, while the anchor sweep finds the same posting's real
        link. Bending Spoons produced 46 real + 77 synthetic rows for 47
        jobs, every synthetic one of them an unopenable duplicate.
        """
        real = {_embed_title_key(r.get("job_title"))
                for r in rows if "#job=" not in (r.get("job_url") or "").lower()}
        if not real:
            return rows
        kept = [r for r in rows
                if "#job=" not in (r.get("job_url") or "").lower()
                or _embed_title_key(r.get("job_title")) not in real]
        dropped = len(rows) - len(kept)
        if dropped:
            print(f"      dropped {dropped} synthetic #job= twin(s)")
        return kept
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
            // FIX P28 (2026-10-05): this helper handled a STRING or a flat
            // object and nothing else, and the key list omitted `title`.
            // jobs.bendingspoons.com stores
            //   officeLocations: [{title:"Milan (Italy)", country:{isoCode:"IT",
            //                      name:"Italy"}}, {title:"London (UK)"}, ...]
            // so every one of its 47 jobs came back with location_hint = ""
            // -> 105 rows quarantined "outside_or_unproven_target_country"
            // with Location Source=none, and ONE job accepted out of 47.
            // Arrays are now flattened (a job really can be open in several
            // offices, and the scope matcher must see all of them) and the
            // nested country object is resolved.
            const getLocString = (loc, depth) => {
                depth = depth || 0;
                if (!loc || depth > 3) return '';
                if (typeof loc === 'string') return loc.trim();
                if (typeof loc === 'number') return '';
                if (Array.isArray(loc)) {
                    const out = [];
                    for (const x of loc.slice(0, 12)) {
                        const s = getLocString(x, depth + 1);
                        if (s && out.indexOf(s) === -1) out.push(s);
                    }
                    return out.join('; ');
                }
                if (typeof loc === 'object') {
                    const head = [
                        loc.title, loc.name, loc.displayName, loc.label,
                        loc.locationName, loc.fullName, loc.text, loc.value,
                        loc.city, loc.town, loc.office, loc.addressLocality
                    ].filter(v => typeof v === 'string' && v.trim());
                    const tail = [
                        loc.region, loc.state, loc.province, loc.addressRegion
                    ].filter(v => typeof v === 'string' && v.trim());
                    let country = loc.country;
                    if (country && typeof country === 'object') {
                        country = country.name || country.title ||
                                  country.displayName || country.isoCode ||
                                  country.code || '';
                    }
                    if (typeof country !== 'string') country = '';
                    const parts = [];
                    if (head.length) parts.push(head[0].trim());
                    if (tail.length) parts.push(tail[0].trim());
                    const blob = parts.join(' ').toLowerCase();
                    const c = country.trim();
                    // "Milan (Italy)" + "Italy" must not become
                    // "Milan (Italy), Italy".
                    if (c && blob.indexOf(c.toLowerCase()) === -1) parts.push(c);
                    if (parts.length) return parts.join(', ');
                    return '';
                }
                return '';
            };
            const add = (title, url, ctx, loc) => {
                title = String(title || '').trim();
                url = String(url || '').trim();
                if (!title || title.length < 3 || title.length > 200) return;
                // Do not require an English role word here. This extractor is
                // already guarded by structural job/posting keys below, so a
                // local title such as "Sachbearbeiter", "Koch" or "Addetto"
                // must be allowed through to the shared Python title validator.
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
                    obj.externalUrl || obj.externalPath || obj.path || obj.link ||
                    obj.permalink || obj.canonicalUrl || obj.canonicalPositionUrl ||
                    obj.jobPostingUrl || obj.detailUrl || obj.href || obj.slug;
                // FIX P28: the PLURAL and compound keys were missing -- the
                // exact ones a modern Next.js careers site uses.
                const loc =
                    obj.location || obj.locations || obj.officeLocations ||
                    obj.jobLocations || obj.workLocations || obj.offices ||
                    obj.locationName || obj.city || obj.cities ||
                    obj.office || obj.workplace || obj.place || obj.places ||
                    obj.country || obj.region;
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
                if (!jobUrl) {
                    const explicitJobText = /\b(apply|apply now|bewerben|bewerbung|jetzt bewerben|candidati|candidatura|candidarsi|postuler|postulez|solliciteer|sollicitatie|jobangebot|jobangebote|stellenangebot|stellenangebote|vacature|vacatures|offerte di lavoro|offres d'emploi|oferta de empleo|ofertas de empleo|werken bij|praca|oferty pracy)\b/i.test(fullText);
                    const hasEmploymentSignal = /(full[- ]?time|part[- ]?time|fulltime|parttime|intern|trainee|werkstudent|praktikum|remote|hybrid|onsite|on-site|vollzeit|teilzeit|unbefristet|befristet|tempo pieno|tempo parziale|temps plein|temps partiel|cdi|cdd|vast|tijdelijk)/i.test(fullText);
                    // Synthetic URLs are only safe when the DOM itself exposes
                    // job semantics. The old rule accepted any role-word
                    // inside an <article>, turning news content into fake jobs.
                    if ((hasJobAttr || explicitJobText) && (loc || hasEmploymentSignal)) {
                        jobUrl = synthJobUrl(title, loc);
                    }
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
                if (genericTextRe.test(title) || uiTextRe.test(title)) return;
                const loc = locationFromScope(card);
                let jobUrl = pickJobUrl(card);
                if (!jobUrl) {
                    const attrs = cleanText([
                        getClassName(card), card.id || '',
                        card.getAttribute?.('data-job-id') || '',
                        card.getAttribute?.('data-jobid') || '',
                        card.getAttribute?.('data-posting-id') || '',
                        card.getAttribute?.('data-requisition-id') || '',
                        card.getAttribute?.('data-testid') || '',
                        card.getAttribute?.('data-qa') || '',
                        card.getAttribute?.('data-automation') || ''
                    ].join(' '));
                    const explicitJobText = /\b(apply|apply now|bewerben|bewerbung|jetzt bewerben|candidati|candidatura|candidarsi|postuler|postulez|solliciteer|sollicitatie|jobangebot|jobangebote|stellenangebot|stellenangebote|vacature|vacatures|offerte di lavoro|offres d'emploi|oferta de empleo|ofertas de empleo|werken bij|praca|oferty pracy)\b/i.test(text);
                    const hasJobAttr = /(job|position|posting|opening|vacancy|role|requisition|stelle|offerta|offre|vacature)/i.test(attrs);
                    const hasEmploymentSignal = /(full[- ]?time|part[- ]?time|fulltime|parttime|intern|trainee|werkstudent|praktikum|vollzeit|teilzeit|unbefristet|befristet|remote|hybrid|onsite|on-site|tempo pieno|tempo parziale|temps plein|temps partiel|cdi|cdd|vast|tijdelijk)/i.test(text);
                    if (!(hasJobAttr || explicitJobText) || !(loc || hasEmploymentSignal)) return;
                    jobUrl = synthJobUrl(title, loc);
                }
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
                    /weitere laden/i, /mehr anzeigen/i, /mehr jobs/i, /weitere stellen/i,
                    /ver m[áa]s/i, /cargar m[áa]s/i, /voir plus/i, /afficher plus/i,
                    /plus d'offres/i, /plus de postes/i, /meer vacatures/i, /meer resultaten/i,
                    /bekijk meer/i, /meer laden/i, /carica pi[ùu]/i, /mostra altro/i,
                    /więcej ofert/i, /pokaż więcej/i, /näytä lisää/i, /visa fler/i, /vis flere/i
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
                /p[áa]gina siguiente/i, /page suivante/i,
                /nächste seite/i, /vorherige/i, /zur nächsten/i, /weiter/i,
                /pagina successiva/i, /pagina seguente/i, /pagina suivante/i,
                /volgende pagina/i, /volgende/i, /suivant/i, /suivante/i,
                /siguiente página/i, /seguinte/i, /seguinte página/i,
                /następna/i, /następnej/i, /nast[eė]j/i, /seuraava/i,
                /nästa/i, /næste/i
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
    
    @staticmethod
    def _location_confidence(location, loc_source):
        """How much the Job Location can be trusted (FIX W2-8).

        Run 20261003T233023 wrote 2,576 rows whose "location" was simply the
        country of the SEED's web host -- Randstad's Belgian site made 840
        jobs "Belgium", stellenonline's German site made 592 "Germany".  That
        is the recruiter's country, not the job's, and it silently satisfied
        the job_location scope test and inflated every Jobs-by-Country tile.

        The value is still published (it is a useful hint), but it is now
        labelled, and `_scope_allows` no longer accepts it as proof.
        """
        loc = (location or "").strip()
        src = (loc_source or "").strip().lower()
        if not loc or loc.lower() in ("unknown", "not specified"):
            return "none"
        if "site" in src or src in ("company_hq", "region"):
            return "low"          # inferred from the host / a broad region
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

    @staticmethod
    def _location_is_site_derived(loc_source):
        """True when the country came from the web host, not the posting."""
        return "site" in (loc_source or "").strip().lower()

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

    def _strip_shared_card_context(self, name, target_row, company_jobs,
                                   diagnostics):
        """FIX P17: undo attributes derived from a blob shared by many jobs.

        A card context that is long (>=200 chars) AND identical across two or
        more jobs of the same company cannot be evidence about any single one
        of them -- it is the list page, a department panel or the perks
        section. LOOP: one blob became the context of all 17 rows, which is
        how a project-lead role was filed as Job Type "Internship / Hybrid"
        and Experience "1 years".

        Every field that blob produced is reverted here:
          * Job Type        -> re-derived from the title alone
          * Experience *    -> re-derived from the title alone
          * Job Location    -> dropped when it came from the blob ("card"),
                               and the row is handed to the deferred-scope
                               resolver, which fetches the job's own detail
                               page (that is where the real place lives).
        Idempotent: the marker keys are popped as they are consumed.
        """
        buckets = {}
        for rec in company_jobs.values():
            key = rec.get("_ctx_key")
            if key:
                buckets.setdefault(key, []).append(rec)
        shared = [recs for recs in buckets.values() if len(recs) > 1]
        for rec in company_jobs.values():
            rec.pop("_ctx_key", None)
        if not shared:
            return 0
        _policy = (target_row.get("scope_policy") or "global").lower()
        touched = 0
        for recs in shared:
            for rec in recs:
                title = rec.get("Job Title") or ""
                url = rec.get("Job URL") or ""
                # Title/URL only -- no context at all this time.
                loc2, job_type2, _, _, src2 = self.parse_job_metadata(
                    name, title, "", url)
                rec["Job Type"] = job_type2
                rec["Work Mode"] = classify_work_mode(f"{title} {url}")
                for k in ("Experience Required", "Experience Min Years",
                          "Experience Level", "Experience Source"):
                    rec.pop(k, None)
                apply_experience_to_record(rec, jd_text="", card_context="",
                                           title=title)
                if (rec.get("Location Source") or "") in (
                        "card", "region", "multi_chip"):
                    if loc2 != "Not Specified":
                        rec["Job Location"] = self._sanitize_job_location(
                            loc2, name)
                        rec["Location Source"] = src2
                    else:
                        # FIX P41 (2026-10-06): P17 revokes anything derived
                        # from a SHARED context blob, but "Raw Location" is
                        # the row's OWN captured string and was never part of
                        # the shared text. Amazon Italia rows went out with
                        # Job Location=Unknown while still carrying
                        # Raw Location="Milan, ITA" in the same CSV row.
                        # Re-read it before giving up -- no network.
                        _raw_loc = self.extract_location(
                            rec.get("Raw Location") or "")
                        _raw_loc = ("Unknown" if _raw_loc == "Not Specified"
                                    else self._sanitize_job_location(_raw_loc, name))
                        if _raw_loc != "Unknown":
                            rec["Job Location"] = _raw_loc
                            rec["Location Source"] = "card"
                        else:
                            rec["Job Location"] = "Unknown"
                            rec["Location Source"] = "none"
                    rec["Location Confidence"] = self._location_confidence(
                        rec.get("Job Location"), rec.get("Location Source"))
                    if (_policy == "job_location"
                            and rec.get("Job Location") == "Unknown"
                            and url.startswith("http")
                            and "#job=" not in url.lower()):
                        rec["_scope_pending"] = True
                        rec["_scope_context"] = ""
                        rec["Scope Confidence"] = "pending_location"
                    elif (rec.get("Job Location") == "Unknown"
                            and url.startswith("http")
                            and "#job=" not in url.lower()):
                        # FIX P35: context was revoked, so this row lost its
                        # location too -- recover it without re-scoping.
                        rec["_loc_pending"] = True
                touched += 1
        diagnostics.append(
            f"shared card context: {len(shared)} blob(s) reused across "
            f"{touched} row(s); context-derived fields reverted (P17)")
        return touched

    def _resolve_deferred_scope(self, target_row, company_jobs, quarantine,
                                stats, diagnostics):
        """Second chance for rows whose location was never actually read.

        FIX W2-7.  In run 20261003T233023, 2,433 rows were quarantined as
        "outside_or_unproven_target_country" -- but 1,617 of them had
        Location Source=none, i.e. nothing was ever proven about them one way
        or the other (Magnet.me alone lost 1,283 rows this way).  Under
        scope_policy=job_location those rows now get ONE cheap HTTP fetch of
        their own detail page (JSON-LD JobPosting) before the verdict, and
        only then are they accepted or quarantined.

        Rows whose only "location" was the seed host (FIX W2-8) land here too:
        a host-derived country is a hint, never proof.
        """
        import concurrent.futures as _cf
        pending = [rec for rec in company_jobs.values()
                   if rec.get("_scope_pending")]
        # FIX P35: rows that need a LOCATION but not a verdict. They ride the
        # same fetch, under their own budget so they can never starve the
        # scope queue, and they are resolved last.
        loc_only = [rec for rec in company_jobs.values()
                    if rec.get("_loc_pending") and not rec.get("_scope_pending")]
        if not pending and not loc_only:
            return
        budget = min(len(pending), max(0, int(
            os.environ.get("SPONSORSCOUT_SCOPE_RESOLVE_MAX") or 400)))
        pending = pending[:budget]
        loc_budget = min(len(loc_only), max(0, int(
            os.environ.get("SPONSORSCOUT_LOC_RESOLVE_MAX") or 400)))
        pending = pending + loc_only[:loc_budget]
        workers = recommended_workers("http")

        def _resolve(rec):
            url = rec.get("Job URL") or ""
            if not url.startswith("http") or "#job=" in url.lower():
                return rec, None
            try:
                # FIX P53: HTTP only in the pool -- see _browser_sweep below.
                return rec, self._detail_location_from_url(
                    url, allow_browser=False)
            except Exception:
                return rec, None

        def _browser_sweep(unresolved):
            """One shared headless browser for the rows HTTP could not read.

            FIX P53 (2026-10-07). _detail_location_from_url falls back to a
            browser when the static parse fails, and with browser_page=None
            it launches a BRAND NEW Chromium per call. Inside this 24-thread
            pool that meant up to one browser per row: Stafide spent
            1300.2 s (21.7 min, 47% of run 20261007T201018) here for 109
            rows. Same rows, same parser, same results -- one browser,
            serially, under its own budget.
            """
            out = {}
            if not unresolved:
                return out
            cap = max(0, int(os.environ.get(
                "SPONSORSCOUT_SCOPE_BROWSER_MAX") or 150))
            unresolved = unresolved[:cap]
            if not unresolved:
                return out
            _pw = _bro = _pg = None
            try:
                from playwright.sync_api import sync_playwright as _sp
                _pw = _sp().start()
                _bro = _pw.chromium.launch(headless=True, args=BROWSER_ARGS)
                _pg = _bro.new_page()
                install_page_resource_blocking(_pg)
                _deadline = time.monotonic() + max(30, int(os.environ.get(
                    "SPONSORSCOUT_SCOPE_BROWSER_BUDGET") or 240))
                for rec in unresolved:
                    if check_control(self.cancel_event, self.pause_event):
                        break
                    if time.monotonic() > _deadline:
                        break
                    try:
                        out[id(rec)] = self._detail_location_from_url(
                            rec.get("Job URL") or "", browser_page=_pg)
                    except Exception:
                        continue
            except Exception:
                return out
            finally:
                for _c in (getattr(_pg, "close", None),
                           getattr(_bro, "close", None),
                           getattr(_pw, "stop", None)):
                    try:
                        if _c:
                            _c()
                    except Exception:
                        pass
            return out

        recovered = 0
        _static_done, _needs_browser = [], []
        with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
            for rec, res in ex.map(_resolve, pending):
                if res:
                    _static_done.append((rec, res))
                else:
                    _needs_browser.append(rec)
        _swept = _browser_sweep(_needs_browser)
        _results = _static_done + [(r, _swept.get(id(r)))
                                   for r in _needs_browser]
        if _results:
            for rec, res in _results:
                if check_control(self.cancel_event, self.pause_event):
                    break
                if not res:
                    continue
                loc = self._sanitize_job_location(
                    res[0], rec.get("Company Name") or "")
                if loc and loc != "Unknown":
                    # FIX P44: a recovered town gets its country named, the
                    # same way the inline path does it.
                    loc, _rsrc = self._append_country_if_missing(
                        loc, res[1] or "detail",
                        rec.get("Job URL"), target_row.get("careers_url"))
                    rec["Job Location"] = loc
                    rec["Location Source"] = _rsrc
                    rec["Location Confidence"] = self._location_confidence(
                        loc, rec.get("Location Source"))
                    recovered += 1
                    # FIX P35: a seed_url row was accepted on the seed's word
                    # alone. Now that the job's OWN location has been read, it
                    # can be confirmed. Context and URL are deliberately NOT
                    # passed: the upgrade to "verified" must rest on the job's
                    # location and nothing else. A mismatch is left at
                    # unverified_seed_url -- never quarantined, because under
                    # seed_url the seed, not the location, decides membership.
                    if (rec.get("_loc_pending")
                            and not rec.get("_scope_pending")
                            and rec.get("Scope Confidence") == "unverified_seed_url"):
                        _tc = (target_row.get("target_country") or "").strip()
                        if (_tc and _tc.casefold() != "global"
                                and self._scope_country_match(_tc, loc, "", "")):
                            rec["Scope Confidence"] = "verified"
                    # FIX P43 (same rule, applied to rows that were already
                    # verified before their real location was read).
                    elif (rec.get("_loc_pending")
                            and not rec.get("_scope_pending")
                            and rec.get("Scope Confidence") == "verified"):
                        _tc = (target_row.get("target_country") or "").strip()
                        _oc = self._country_of_location(loc)
                        if (_tc and _tc.casefold() != "global" and _oc
                                and not self._scope_country_match(_tc, _oc, "", "")):
                            rec["Scope Confidence"] = "unverified_seed_url"

        # FIX P49 (2026-10-07): a row the fetch could not read may still
        # state its country in its own Workday URL
        # ("/job/NLD---Alphen-Aan-Den-Rijn/..."). Seven real Wolters Kluwer
        # jobs at the company's Dutch HQ were quarantined in run
        # 20261007T201018 for want of exactly this. The place is the
        # employer's own data, so it is reported at face value -- but it is
        # applied ONLY to rows that still have nothing, so it can never
        # overwrite a location that was actually read.
        _wd_fixed = 0
        for rec in pending:
            _cur = (rec.get("Job Location") or "").strip()
            if _cur and _cur not in ("Unknown", _MULTI_LOCATION_LABEL):
                continue
            _place, _country = self._workday_url_location(rec.get("Job URL"))
            if not _country:
                continue
            _place = self._sanitize_job_location(
                _place, rec.get("Company Name") or "") if _place else ""
            _loc = (f"{_place}, {_country}"
                    if _place and _place != "Unknown" else _country)
            rec["Job Location"] = _loc
            rec["Location Source"] = "url"
            rec["Location Confidence"] = self._location_confidence(
                _loc, "url")
            _wd_fixed += 1
            # P43's rule, unchanged: the job's own country decides.
            _tc = (target_row.get("target_country") or "").strip()
            if _tc and _tc.casefold() != "global":
                _match = self._scope_country_match(_tc, _country, "", "")
                if rec.get("_scope_pending"):
                    pass  # the verdict loop below re-tests it properly
                elif rec.get("Scope Confidence") in (
                        "verified", "unverified_seed_url"):
                    rec["Scope Confidence"] = ("verified" if _match
                                               else "unverified_seed_url")
        if _wd_fixed:
            diagnostics.append(
                f"P49: {_wd_fixed} row(s) located from the ISO country code "
                f"in their Workday URL")

        dropped = 0
        for rec in list(company_jobs.values()):
            rec.pop("_loc_pending", None)  # FIX P35: never re-judged, never retried
            if not rec.get("_scope_pending"):
                continue
            rec.pop("_scope_pending", None)
            ctx = rec.pop("_scope_context", "") or ""
            loc = rec.get("Job Location") or "Unknown"
            proven = (loc != "Unknown"
                      and not self._location_is_site_derived(
                          rec.get("Location Source"))
                      and self._scope_allows(target_row, loc, ctx,
                                             rec.get("Job URL") or ""))
            if proven:
                rec["Scope Confidence"] = "verified"
                continue
            cid = rec.get("Canonical Job ID")
            company_jobs.pop(cid, None)
            rec["Record Status"] = "quarantine"
            rec["Quarantine Reason"] = "outside_or_unproven_target_country"
            quarantine.append(rec)
            stats["quarantined"] += 1
            stats["rejected_scope"] += 1
            dropped += 1
        diagnostics.append(
            f"scope re-check: {len(pending)} unproven row(s) fetched "
            f"({loc_budget} for location only, P35), "
            f"{recovered} location(s) recovered, {dropped} quarantined")

    def _detail_enrich_one(self, page, url, company_jobs, browser_timeout_ms=None) -> str:
        """Enrich one row from its detail page.
        Returns "ok" / "network_fail" / "other_fail" / "skipped" so the
        caller can run the host circuit-breaker and one retry pass."""
        try:
            _timeout = int(browser_timeout_ms or self.config.DETAIL_SCAN_TIMEOUT_MS)
            page.goto(url,wait_until="domcontentloaded",timeout=_timeout)
            page.wait_for_timeout(500)
            # NOTE: raw string — the JS below contains regex/string escapes
            # (\s, \n). In a non-raw Python literal \n would be turned into a
            # REAL newline inside a single-quoted JS string literal, which is a
            # JavaScript syntax error and would abort every detail visit.
            data=page.evaluate(r"""() => {
                const out={desc:'',type:'',loc:'',remote:false,hdr:''};
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
                // BUGFIX (B12): a large share of career pages publish NO
                // JobPosting ld+json at all (WordPress/Elementor templates
                // ship only a Yoast WebSite graph), so out.loc stays '' and the
                // detail pass had no location to apply. Those pages still print
                // country and city as plain text directly under the <h1>, so
                // capture that short header block. It is only ever a CANDIDATE:
                // the Python side re-validates it against the known-place
                // gazetteer before it can reach a row.
                try {
                    const h1=document.querySelector('h1');
                    const title=(h1?h1.innerText:'').replace(/\s+/g,' ').trim();
                    if (title) {
                        const lines=out.desc.split('\n')
                            .map(s=>s.replace(/\s+/g,' ').trim()).filter(Boolean);
                        const i=lines.findIndex(l=>l.toLowerCase()===title.toLowerCase());
                        if (i>=0) out.hdr=lines.slice(i+1,i+5).join('\n');
                    }
                } catch(e) {}
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
        # FIX P0-50 (S-01): this site published the raw verdict.
        visa, reloc = apply_support_fp_guards(
            visa, reloc, desc, rec.get("Job Title") or "")
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
        # FIX P27: same shared classifier as the listing path and the ATS
        # scanner. The employer's own `type` field outranks the JD body, and
        # a part-time mention inside a pay/pro-rata sentence is ignored.
        emp = (data.get("type") or "")
        workload = classify_workload(employer_field=emp,
                                     title=(rec.get("Job Title") or ""),
                                     text=desc)
        mode = "Remote" if data.get("remote") else classify_work_location_mode(desc)
        old_parts=(rec.get("Job Type") or "Unknown / Unknown").split(" / ",1)
        if workload or mode:
            rec["Job Type"]=f"{workload or old_parts[0]} / {mode or (old_parts[1] if len(old_parts)>1 else 'Unknown')}"

        # BUGFIX (B12): the detail page is the AUTHORITATIVE source for this one
        # posting's location, so it may replace a value that was never
        # observed. It previously overwrote only "Unknown"/"Not Specified",
        # which meant the company_hq guess (P0-11) was permanent: a multi-site
        # employer stamped every row with its HQ city, and a job actually
        # posted in Eindhoven was reported as Pune, India. A hardcoded
        # headquarters is a fact about the COMPANY, not about the JOB, so it
        # is treated as a guess that real page evidence always outranks.
        loc=(data.get("loc") or "").strip()
        detail_location = ""
        if loc:
            parsed=self.extract_location(loc)
            if parsed!="Not Specified":
                detail_location=parsed
        if not detail_location:
            detail_location=self._location_from_detail_header(data.get("hdr") or "")
        if detail_location and self._location_is_unverified(rec):
            # FIX P0-49 (S-09): this writer never validated its value.
            detail_location=self._sanitize_job_location(
                detail_location, rec.get("Company Name") or rec.get("Hiring Company") or "")
        if detail_location and detail_location != "Unknown" and self._location_is_unverified(rec):
            rec["Job Location"]=detail_location
            rec["Location Source"]="detail"
            # Keep the harvested text so the country can be re-derived later
            # without re-crawling the page.
            rec["Raw Location"]=data.get("hdr") or loc
        return "ok"

    def _location_from_detail_header(self, hdr) -> str:
        """Parse country/city printed directly under the detail page's <h1>.

        WordPress/Elementor career templates publish no JobPosting ld+json, so
        the structured location never reaches the scanner, yet the page still
        states it as two short lines under the title:

            Sr. Scrum Master
            Netherlands
            Eindhoven
            Any Masters Degree

        Only the first two lines are treated as the location, and every
        candidate must survive ``extract_location``'s known-place validation
        before it is returned, so an education line ("Any Masters Degree") or
        a call to action can never be mistaken for a place. An empty string
        means "no usable evidence", and the row keeps whatever it had.
        """
        lines=[l.strip() for l in str(hdr or "").splitlines() if l.strip()]
        if not lines:
            return ""
        for candidate in (", ".join(lines[:2]), "\n".join(lines[:3]), lines[0]):
            parsed=self.extract_location(candidate)
            if parsed!="Not Specified":
                return parsed
        return ""

    @staticmethod
    def _location_is_unverified(rec) -> bool:
        """True when a row's location is a placeholder or an inferred guess.

        Only those may be replaced by detail-page evidence. A location that
        was actually read off the listing card ("card", "detail", "api", ...)
        is left alone, so this can never rewrite a real observation.
        """
        current = str(rec.get("Job Location") or "").strip()
        if current.lower() in {"", "unknown", "not specified", "global", "united"}:
            return True
        return str(rec.get("Location Source") or "").strip().lower() in {
            "", "none", "unknown", "company_hq"}

    @staticmethod
    def _detail_priority(rec) -> tuple:
        """Sort key sending weak-evidence rows to the detail budget first.

        A row is "strong" on an axis when the listing crawl already produced
        an explicit verdict, supporting evidence, or a usable location.  Only
        the visit order changes — the same per-company / global caps, the same
        evidence rules and the same writers apply, so no row can lose data it
        would otherwise have received.
        """
        weak_location = (str(rec.get("Job Location") or "").strip().lower() in (
            "", "unknown", "not specified", "global",
            # FIX P0-60: a multi-location chip is a placeholder, not evidence.
            _MULTI_LOCATION_LABEL.lower())
            # BUGFIX (B12): a company_hq value LOOKS like a usable location, so
            # these rows sorted as "strong" and were visited last -- exactly
            # backwards, since they are the rows a detail visit can correct.
            # A guess is weak evidence by definition.
            or str(rec.get("Location Source") or "").strip().lower() == "company_hq")
        weak_verdict = str(rec.get("Visa Sponsorship") or "").strip().lower() not in (
            "y", "n", "yes", "no")
        weak_evidence = not str(rec.get("Support Evidence") or "").strip()
        weak_experience = str(rec.get("Experience Required") or "").strip().lower() in ("", "unknown")
        return (0 if weak_location else 1,
                0 if weak_verdict else 1,
                0 if weak_evidence else 1,
                0 if weak_experience else 1)

    def _run_detail_pipeline(self, page, name, company_jobs):
        """Detail enrichment, HTTP-parallel first (FIX W2-6).

        Run 20261003T233023 read a JD for only 2,602 of 9,280 rows (28%), and
        22 companies got none at all, because the only detail path was a
        SERIAL browser loop inside a 480 s budget.  Most job pages are plain
        HTML: `_enrich_support_from_detail` already fetches them in parallel
        over HTTP (and it fills visa/relocation/Blue Card/experience in one
        pass).  Run that first, then spend the expensive browser only on the
        rows HTTP could not read.
        """
        if not self.detail_scan or not company_jobs:
            return
        rows = list(company_jobs.values())
        try:
            self._enrich_support_from_detail(
                rows, use_browser=False,
                max_fetch=self.config.MAX_DETAIL_SCAN_PER_COMPANY,
                backfill=False, company_name=name)
        except Exception as exc:
            print(f"   [detail] {name}: HTTP pass failed "
                  f"({type(exc).__name__}: {exc}); falling back to browser")
        # Browser pass: only rows still missing BOTH a verdict and evidence.
        remaining = {cid: rec for cid, rec in company_jobs.items()
                     if not str(rec.get("Support Evidence") or "").strip()
                     and str(rec.get("Visa Sponsorship") or "").strip().lower()
                     in ("", "unknown")}
        if remaining and page is not None:
            self._detail_scan_for_company(page, name, remaining)

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
        _cap=self.config.MAX_DETAIL_SCAN_PER_COMPANY
        urls=[u for u, _ in ordered[:_cap]]
        _browser_cap = max(0, int(getattr(self.config, "DETAIL_BROWSER_FALLBACK_PER_COMPANY", 12)))
        # FIX P0-51 (S-10): say so out loud. A row whose JD was never
        # fetched reports Experience/Sponsor as Unknown, which looked
        # identical to "the extractor failed". It is a budget decision and
        # it is now stated, with the knob to change it.
        if len(ordered) > _cap:
            print(f"   [detail] {name}: {len(ordered)} rows, scanning {_cap} "
                  f"(MAX_DETAIL_SCAN_PER_COMPANY); {len(ordered)-_cap} rows "
                  f"keep Experience/Sponsor from the title only. "
                  f"Raise SPONSORSCOUT_DETAIL_PER_COMPANY to change.")
        detail_start=time.monotonic(); enriched=0
        _floor = self.config.DETAIL_MIN_PER_COMPANY
        _spent = [0]

        def _take_detail_budget():
            with self._detail_lock:
                if (_spent[0] >= _floor
                        and self._detail_count >= self.config.MAX_DETAIL_SCAN_TOTAL):
                    if not getattr(self, "_detail_cap_warned", False):
                        self._detail_cap_warned = True
                        print(f"   [detail] GLOBAL cap reached "
                              f"({self.config.MAX_DETAIL_SCAN_TOTAL} pages, "
                              f"SPONSORSCOUT_DETAIL_TOTAL); remaining "
                              f"companies keep only their reserved "
                              f"{_floor}-page floor.")
                    return False
                self._detail_count += 1
                _spent[0] += 1
                return True

        host_netfails: dict = {}
        tripped_hosts: set = set()
        failed_network_urls: list = []
        browser_attempts = 0
        for url_i,url in enumerate(urls,1):
            if check_control(self.cancel_event, self.pause_event):
                print(f"   CANCELLED: stopping detail scan for {name}")
                break
            if time.monotonic()-detail_start > self.config.DETAIL_SCAN_TIME_BUDGET_SEC:
                print(f"   -> {name}: detail budget exhausted at {url_i}/{len(urls)}")
                break
            self._last_activity=time.monotonic()
            host = urlparse(url).netloc.lower()
            # FIX W2-6: skipped URLs used to consume the global detail budget
            # before they were skipped, so a dead host could burn thousands of
            # the 5,000 allowance and silently starve later companies.
            if host in tripped_hosts:
                continue
            if _is_download_url(url):
                continue
            if browser_attempts >= _browser_cap:
                break
            if not _take_detail_budget():
                break
            browser_attempts += 1
            outcome = self._detail_enrich_one(page, url, company_jobs, browser_timeout_ms=getattr(self.config, "DETAIL_BROWSER_TIMEOUT_MS", self.config.DETAIL_SCAN_TIMEOUT_MS))
            if outcome == "ok":
                enriched += 1
                host_netfails.pop(host, None)
            elif outcome == "network_fail":
                failed_network_urls.append(url)
                host_netfails[host] = host_netfails.get(host, 0) + 1
                if host_netfails[host] >= getattr(self.config, "DETAIL_BROWSER_HOST_FAILURES", 2):
                    tripped_hosts.add(host)
                    print(f"   -> {name}: host {host} unreachable ({host_netfails[host]} network failures) - skipping remaining detail URLs")
        # One retry pass over transient network failures (DNS/timeouts often
        # clear); hosts that already tripped the breaker stay skipped.
        for url in failed_network_urls:
            if browser_attempts >= _browser_cap:
                break
            host = urlparse(url).netloc.lower()
            if host in tripped_hosts:
                continue
            if not _take_detail_budget():
                break
            browser_attempts += 1
            if self._detail_enrich_one(page, url, company_jobs, browser_timeout_ms=getattr(self.config, "DETAIL_BROWSER_TIMEOUT_MS", self.config.DETAIL_SCAN_TIMEOUT_MS)) == "ok":
                enriched += 1
        if enriched:
            print(f"   -> {name}: detail-enriched {enriched} rows")
        if ordered and _browser_cap == 0:
            print(f"   -> {name}: browser detail fallback disabled; HTTP detail only")
        elif browser_attempts >= _browser_cap and len(failed_network_urls) + len(urls) > _browser_cap:
            print(f"   -> {name}: browser detail fallback capped at {_browser_cap} visit(s)")

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

    def _title_from_detail_page(self, url, company=""):
        """FIX P42 (2026-10-06): read a job's real title off its own page.

        amazon.jobs cards expose only "Updated: 10/2/2026" as their anchor
        text, and the URL is /jobs/10559108 with no slug, so both the card
        and the J2 slug rescue produce a title that
        `is_valid_job_title` rightly refuses -- 22 genuine Italian Amazon
        jobs were quarantined in run 20261006T211320 as
        invalid_generic_or_department_title while carrying perfectly good
        locations ("Rome, RM, ITA", "Milan, ITA").

        The page itself states it plainly (`<h1>Business Affairs
        Executive</h1>`, 43 KB, no browser). One cheap static GET is spent
        per rescued row, under a per-run budget, and the recovered text must
        still satisfy the SAME title validator -- this widens what can be
        read, never what counts as a valid title.
        """
        if not url or not url.startswith("http"):
            return ""
        # Two budgets. The global one bounds the run; the per-company one
        # stops a single junk-heavy board from spending the whole budget and
        # from serialising hundreds of fetches inside one crawl_target
        # (these run sequentially within a company). Worst case per company
        # is 40 x 8 s, well inside MAX_COMPANY_TIME_SEC.
        cap = max(0, int(os.environ.get("SPONSORSCOUT_TITLE_RESCUE_MAX") or 200))
        per_co = max(0, int(
            os.environ.get("SPONSORSCOUT_TITLE_RESCUE_PER_COMPANY") or 40))
        key = (company or "").casefold()
        with self._title_rescue_lock:
            if self._title_rescue_used >= cap:
                return ""
            if self._title_rescue_by_company.get(key, 0) >= per_co:
                return ""
            self._title_rescue_used += 1
            self._title_rescue_by_company[key] = (
                self._title_rescue_by_company.get(key, 0) + 1)
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"),
                "Accept": "text/html,application/xhtml+xml,*/*",
            })
            with urllib.request.urlopen(req, timeout=8) as resp:
                html_text = resp.read(400000).decode("utf-8", "replace")
        except Exception:
            return ""
        cands = []
        m = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", html_text)
        if m:
            cands.append(m.group(1))
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html_text)
        if m:
            # "Business Affairs Executive  - Job ID: 10559108 | Amazon.jobs"
            head = re.split(r"\s*(?:\||\u2013|\u2014)\s*", unescape(m.group(1)))[0]
            cands.append(re.sub(r"(?i)\s*-\s*job\s*id\s*:.*$", "", head))
        for cand in cands:
            text = re.sub(r"\s+", " ", unescape(
                re.sub(r"(?s)<[^>]+>", " ", cand))).strip()
            if not text or len(text) > 160:
                continue
            # P42b (run 20261007T174338): the <h1>/<title> of an ATS landing
            # page is the SITE's name, not a vacancy -- Gruppo UNA published
            # "UNIPOL_External Career Site" as a job. is_valid_job_title has
            # no reason to refuse it, so the page-furniture shapes are named
            # here, where the text's origin is known.
            if _PAGE_FURNITURE_TITLE_RE.search(text):
                continue
            cleaned = self.clean_job_title(text)
            if cleaned and self.is_valid_job_title(cleaned, company):
                return cleaned
        return ""

    def _explicit_country_name(self, location):
        """The RIGHTMOST explicitly named country in a string, else "".

        FIX P54b (2026-10-07). "San Jose, Costa Rica" resolved to the United
        States: San Jose (California) is a known city and was matched before
        anything read the words "Costa Rica". A country the string NAMES
        outranks a city that merely shares a name with one, and the
        convention everywhere is that the country comes last -- so the
        rightmost match wins ("Georgia, United States" -> United States,
        "Tbilisi, Georgia" -> Georgia).
        """
        v = self._w3_strip_location_noise(location)
        if not v:
            return ""
        parts = [p.strip() for p in re.split(r"[,/|;()\[\]]+|\s+-\s+", v)
                 if p.strip()]
        for part in reversed(parts):
            n = self._w3_norm(part)
            if not n or n in self._W3_REGION_MARKERS:
                continue
            hit = self._W3_COUNTRY_ALIASES.get(n)
            if hit:
                return hit
        return ""

    def _country_of_location(self, location):
        """The country a location string resolves to, or "" (FIX P43)."""
        loc = (location or "").strip()
        if not loc or loc.lower() in ("unknown", "not specified"):
            return ""
        # FIX P54b: an explicitly named country wins over a city lookup.
        _named = self._explicit_country_name(loc)
        if _named:
            return _named
        if country_from_location is not None:
            try:
                got = (country_from_location(loc) or "").strip()
                if got:
                    return got
            except Exception:
                pass
        return (self._supplementary_country(loc) or "").strip()

    def _append_country_if_missing(self, location, source, *urls):
        """FIX P44 (2026-10-07): name the country on a RECOVERED location.

        process_job runs this step inline, but a location recovered later by
        _resolve_deferred_scope (P35) bypassed it, so a town the gazetteer
        does not list was written bare. Run 20261007T174338 shipped
        "Lissone", "Seriate" and "Corsico" -- all real Leroy Merlin stores in
        Lombardy, served from a .it host -- with a correct town and NO
        country, which leaves them Unknown in the dashboard.

        Mirrors the inline rules, including P29: a string that already names
        its country in another language is only re-labelled, never appended
        to. A country taken from the host is marked "+site", which
        _location_confidence deliberately demotes to `low`.
        """
        loc = (location or "").strip()
        if not loc or loc.lower() in ("unknown", "not specified"):
            return loc, source
        if country_from_location is not None:
            try:
                if (country_from_location(loc) or "").strip():
                    return loc, source
            except Exception:
                pass
        if self._w3_has_country(loc):
            return loc, f"{source}+gazetteer"
        gaz = (self._supplementary_country(loc) or "").strip()
        if gaz:
            return f"{loc}, {gaz}", f"{source}+gazetteer"
        # FIX P49: a Workday job URL states the country outright as an
        # ISO-3166 alpha-3 segment. That is the employer's own data, not a
        # guess from the web host, so it outranks _country_from_site and is
        # NOT demoted to `low`.
        _wd_place, _wd_country = self._workday_url_location(*urls)
        if _wd_country:
            return f"{loc}, {_wd_country}", f"{source}+url"
        site = (self._country_from_site(*urls) or "").strip()
        if site:
            return f"{loc}, {site}", f"{source}+site"
        return loc, source

    def _location_in_target_country(self, text, target_row):
        """FIX P39 (2026-10-06): keep a multi-country posting's TARGET site.

        Bending Spoons advertises one role in several offices:

            "Milan (Italy), London (UK), Madrid (Spain), or Warsaw (Poland)"

        The collapse to a single value picked the LAST entry -- Job Location
        was written as "Poland", "UK" or (once the word "or" appeared)
        "Unknown" -- and the row was then quarantined as outside Italy. In
        run 20261006T211320 that cost 40 rows at the single most
        sponsorship-relevant employer in the Italian seed, every one of which
        names Milan explicitly.

        When a posting offers several COUNTRIES and one of them is the seed's
        target, that is the site the seed is asking about, so it is the one
        reported. Returns "" for anything that is not genuinely
        multi-country, so single-site strings ("Milan, MI, ITA") and
        same-country lists (handled by _multisite_same_country) are
        untouched.
        """
        target = (target_row.get("target_country") or "").strip()
        if not target or target.casefold() == "global":
            return ""
        raw = re.sub(r"\s+", " ", str(text or "")).strip()
        if not raw or len(raw) > 300:
            return ""
        # Only the explicit "City (Country)" list form. A bare comma split
        # would tear "Milan, MI, ITA" into pieces and lose its country.
        pairs = re.findall(
            r"([A-ZÀ-Þ][\w'’\-\. ]{1,30}?)\s*\(([^)]{2,30})\)", raw)
        if len(pairs) < 2:
            return ""
        parsed, countries = [], set()
        for city, country in pairs:
            got = self.extract_location(
                f"{city.strip(' ,')}, {country.strip()}")
            if got == "Not Specified":
                continue
            parsed.append(got)
            name = ""
            if country_from_location is not None:
                try:
                    name = (country_from_location(got) or "").strip()
                except Exception:
                    name = ""
            name = name or (self._supplementary_country(got) or "")
            if name:
                countries.add(name.casefold())
        # Two or more DISTINCT countries is what makes this a choice of
        # sites rather than one place written out in full.
        if len(countries) < 2:
            return ""
        for got in parsed:
            if self._scope_country_match(target, got, "", ""):
                return got
        return ""

    def _location_from_title_text(self, title):
        """FIX P40 (2026-10-06): the branch named inside a store job's title.

        Italian retail and restaurant chains identify the shop in the title
        and nowhere else -- "RESPONSABILE DI SALA - ROSSOPOMODORO ROMA
        CENTRO". The card carries no location field, so the row fell back to
        the seed host ("Italy"), which is not proof, and was quarantined:
        27 Rossopomodoro and 4 Oniverse rows in run 20261006T211320. Their
        detail pages cannot help either -- joblink.allibo.com answers with a
        2 KB JavaScript shell.

        Every 1-3 word window of the title is offered to extract_location,
        which validates against the gazetteer and rejects role words, and the
        longest accepted place wins. The place is reported as it is FOUND,
        never filtered to the target country: "INTIMISSIMI Mannheim" must
        resolve to Mannheim so the scope rules can correctly reject it.
        """
        text = re.sub(r"[|/]", " ", str(title or ""))
        tokens = [w for w in re.split(r"[\s,\-–—]+", text) if w]
        if not tokens or len(tokens) > 24:
            return ""
        for size in (3, 2, 1):
            best = ""
            for i in range(len(tokens) - size + 1):
                window = " ".join(tokens[i:i + size]).strip("()[].,:;")
                if len(window) < 3:
                    continue
                got = self.extract_location(window)
                if got != "Not Specified" and len(got) > len(best):
                    best = got
            if best:
                return best
        return ""

    def _label_location(self, html):
        """FIX P14: best location value from a label/value metadata strip.

        Multi-site strips ("Salzburg - Vienna") are kept VERBATIM when every
        part resolves to the same country -- reporting only the last city
        would hide half of what the posting says -- and collapsed to the one
        parsed place when the parts straddle countries, where a joined
        string would make the country ambiguous.
        """
        for cand in extract_labelled_location(html):
            parsed = self.extract_location(cand)
            if parsed == "Not Specified":
                continue
            return self._multisite_same_country(cand) or parsed
        return ""

    def _multisite_same_country(self, value):
        """Return `value` when it names 2+ places in ONE country, else ""."""
        raw = re.sub(r"\s+", " ", str(value or "")).strip(" \t,;|-")
        if not raw or len(raw) > 60:
            return ""
        parts = [p.strip() for p in re.split(
            r"\s*(?:[-\u2013\u2014/;|&]|\band\b|\bund\b|\bor\b|\bo\b)\s*", raw)
            if p.strip()]
        if len(parts) < 2:
            return ""
        countries, places = set(), 0
        for part in parts:
            got = self.extract_location(part)
            if got == "Not Specified":
                return ""
            places += 1
            country = ""
            if country_from_location is not None:
                try:
                    country = (country_from_location(got) or "").strip()
                except Exception:
                    country = ""
            if not country:
                country = self._supplementary_country(got) or ""
            if not country:
                return ""
            countries.add(country.casefold())
        if places >= 2 and len(countries) == 1:
            return raw
        return ""

    def _detail_location_from_url(self, url, timeout_ms=20000, browser_page=None,
                                  allow_browser=True):
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
            # FIX P14: label/value metadata strip (no JSON-LD needed).
            _lab = self._label_location(html)
            if _lab:
                return _lab, "detail"
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
        # FIX P53 (2026-10-07): the caller can forbid the browser. The bulk
        # resolver used to let every row in a 24-thread pool launch its OWN
        # headless Chromium; Stafide's 109 rows cost 1300 s that way.
        if not allow_browser:
            return None
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
                # FIX P14: same label/value strip, now on the RENDERED DOM --
                # JS-built spec strips only exist after hydration.
                try:
                    rendered = pg.content() or ""
                except Exception:
                    rendered = ""
                _lab = self._label_location(rendered)
                if _lab:
                    return _lab, "detail"
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
        # FIX P0-60: "Multiple Locations" is a placeholder, not an answer, so
        # these rows must stay eligible for a detail visit that can name the
        # actual sites.
        targets = [r for r in rows
                   if (r.get("Job Location") or "").strip()
                   in ("Not Specified", _MULTI_LOCATION_LABEL)]
        if not targets:
            return 0
        if max_fetch:
            targets = targets[:max_fetch]
        print(f"   Detail-page location enrichment: {len(targets)} rows to check...")

        def fetch(r):
            res = self._detail_location_from_url(r["Job URL"])
            if res:
                # FIX P0-49 (S-09): nor did this one. "Operations", "Are",
                # "Amazon Ads", "At Amazon", "Amazon Web Services", "In
                # Amazon" and "Amazon" all reached the Job Location column
                # from here -- every one of them is rejected by
                # _location_from_line and yields no country, i.e. the parser
                # already knew they were not places; nothing was asking it.
                _loc = self._sanitize_job_location(
                    res[0], r.get("Company Name") or r.get("Hiring Company") or "")
                if _loc == "Unknown":
                    return 0
                r["Job Location"] = _loc
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

        Returns ``(desc_text, ld_location, hiring_org)`` — empty strings when
        unavailable.  FIX W2-9 added the third element: JSON-LD JobPosting
        carries ``hiringOrganization.name``, which is the only reliable way to
        learn WHO is hiring behind an aggregator.  All 6,522 recruiter rows in
        run 20261003T233023 said "Unknown".
        """
        if _is_download_url(url):
            return "", "", ""
        check_cancelled(self.cancel_event)
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"),
                "Accept": "text/html,application/xhtml+xml,*/*",
            })
            with urllib.request.urlopen(req, timeout=12) as resp:
                html = resp.read().decode("utf-8", "replace")
        except ScanCancelled:
            raise
        except Exception:
            return "", "", ""
        desc = ""
        loc = ""
        org = ""
        # JSON-LD JobPosting: description + location + hiring organisation
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
                # FIX W2-9: who is actually hiring (aggregator rows).
                if not org:
                    ho = it.get("hiringOrganization")
                    if isinstance(ho, dict):
                        org = str(ho.get("name") or "").strip()[:120]
                    elif isinstance(ho, str):
                        org = ho.strip()[:120]
        if not desc:
            # meta description / og:description fallback
            m = re.search(
                r'<meta[^>]*(?:name|property)=["\'](?:og:)?description["\'][^>]*content=["\']([^"\']{50,1500})["\']',
                html, re.I)
            if m:
                desc = re.sub(r"<[^>]+>", " ", m.group(1))
                desc = re.sub(r"\s+", " ", desc).strip()[:8000]
        return desc, loc, org

    def _apply_support_from_text(self, rec, desc, evidence_url=""):
        """Run the shared support detector over a JD and write the verdicts.

        FIX W2-6b.  Every provider adapter already returns the full JD
        (greenhouse `content=true`, ashby, lever, recruitee, workable,
        bamboohr `/detail`, workday CXS, smartrecruiters jobAd), but the
        career crawler only mined it for EXPERIENCE -- sponsorship, Blue Card
        and relocation were left for the detail pass, which never reached 72%
        of the rows in run 20261003T233023.  This is free evidence: zero extra
        requests.  Same classifier, same false-positive guards and same
        Blue Card rule as ats_scanner.classify_support.
        """
        if not desc:
            return False
        sup = self.detector.detect(desc)
        if not sup["visa"]["evidence"] and not sup["relocation"]["evidence"]:
            # Blue Card is independent of the general visa verdict.
            blue = detect_blue_card(self.detector, desc)
            if blue and blue != VERDICT_UNKNOWN:
                rec["EU Blue Card"] = blue
                return True
            return False
        _v, _r = apply_support_fp_guards(
            sup["visa"]["verdict"], sup["relocation"]["verdict"],
            desc, rec.get("Job Title") or "")
        rec["Visa Sponsorship"] = _v
        rec["Relocation Support"] = _r
        rec["Relocation Required"] = "Yes" if sup["relocation"]["required"] else "No"
        rec["Support Confidence"] = round(max(
            sup["visa"]["confidence"], sup["relocation"]["confidence"]), 2)
        rec["Support Evidence"] = "; ".join(filter(None, [
            self.detector.best_evidence(sup["visa"]),
            self.detector.best_evidence(sup["relocation"]),
        ]))
        if rec["Support Evidence"]:
            rec["Support Evidence Type"] = "explicit_detail_sentence"
            rec["Support Evidence URL"] = (evidence_url
                                           or rec.get("Job URL") or "")
        if _v == VERDICT_YES or _r == VERDICT_YES:
            rec["Relocation/Visa Support"] = "Y"
        elif _v == VERDICT_NO and _r == VERDICT_NO:
            rec["Relocation/Visa Support"] = "N"
        else:
            rec["Relocation/Visa Support"] = "Unknown"
        rec["EU Blue Card"] = detect_blue_card(self.detector, desc)
        _ev = ""
        for sent in self.detector.split_sentences(desc):
            if re.search(r"\b(?:eu[- ]?)?blue[- ]?card|blaue[nr]? karte"
                         r"|carta blu|blauwe kaart|carte bleue|tarjeta azul",
                         sent, re.I):
                _ev = sent[:300]
                break
        if _ev:
            rec["Blue Card Evidence"] = _ev
        return True

    def _apply_ld_side_effects(self, rec, ld_location, hiring_org):
        """Fill a weak location / unknown employer from one JSON-LD read."""
        if ld_location:
            cur = (rec.get("Job Location") or "").strip()
            weak = (cur.lower() in ("", "unknown", "not specified")
                    or self._location_is_site_derived(rec.get("Location Source")))
            if weak:
                loc = self._sanitize_job_location(
                    ld_location, rec.get("Company Name") or "")
                if loc and loc != "Unknown":
                    rec["Job Location"] = loc
                    rec["Location Source"] = "detail"
                    rec["Location Confidence"] = self._location_confidence(
                        loc, "detail")
        if hiring_org:
            cur = (rec.get("Hiring Company") or "").strip()
            if cur.lower() in ("", "unknown"):
                rec["Hiring Company"] = hiring_org

    def _enrich_support_from_detail(self, rows, max_workers=None, max_fetch=None,
                                    use_browser=True, browser_page=None,
                                    backfill=True, company_name=""):
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
        # FIX W2-6: every company keeps a reserved floor of detail fetches, so
        # a company scanned late in the run can never be starved to 0% the way
        # Celonis (221 rows), Bolt (214) and Decathlon (167) were.
        floor = self.config.DETAIL_MIN_PER_COMPANY
        spent_here = [0]

        def _take_budget():
            with self._detail_lock:
                if spent_here[0] >= floor and self._detail_count >= cap:
                    if not getattr(self, "_detail_cap_warned", False):
                        self._detail_cap_warned = True
                        print(f"   [detail] GLOBAL cap reached "
                              f"({cap} pages, SPONSORSCOUT_DETAIL_TOTAL); "
                              f"remaining companies keep only their reserved "
                              f"{floor}-page floor.")
                    return False
                self._detail_count += 1
                spent_here[0] += 1
                return True
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
            except ScanCancelled:
                raise
            except Exception:
                return "", "", ""

        def _enrich_from_desc(r, desc):
            if not desc:
                return 0
            # FIX P0-30: extract experience BEFORE the visa-evidence gate
            # below. Most JDs state experience but say nothing about visas;
            # running this after the early return would leave the new columns
            # empty for exactly those rows.
            _exp_hit = apply_experience_to_record(
                r, jd_text=desc, title=r.get("Job Title") or "")
            # FIX P0-45 / P0-50 / P0-57 all live in the shared routine now,
            # so the API path and the detail path cannot drift apart again.
            hit = self._apply_support_from_text(r, desc, r.get("Job URL") or "")
            if not hit:
                return 1 if _exp_hit else 0
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
                    if not _take_budget():
                        break
                    done += 1
                    try:
                        res = fut.result()
                    except Exception:
                        res = None
                    if res:
                        desc = res[0]
                        # FIX W2-7/W2-9: the same single fetch also carries the
                        # posting's own location and its hiring organisation.
                        # Throwing them away was why 1,617 rows stayed
                        # location-less and 6,522 recruiter rows stayed
                        # "Hiring Company: Unknown".
                        try:
                            self._apply_ld_side_effects(
                                r, res[1] if len(res) > 1 else "",
                                res[2] if len(res) > 2 else "")
                        except Exception:
                            pass
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
                if not _take_budget():
                    break
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
        _label = f" [{company_name}]" if company_name else ""
        print(f"   Support detection{_label}: {changed} rows enriched "
              f"from detail pages ({done} fetched)")
        if not backfill:
            return changed
        # FIX P0-30b: rows whose experience is still unknown but that have a
        # real job URL get a bounded second pass through the SAME fetchers,
        # inside the SAME global cap accounting. No new threads, no new budget.
        missing = [r for r in targets if str(r.get("Experience Required") or "").strip().lower() in ("", "unknown") and str(r.get("Job URL") or "").startswith("http")]
        missing = missing[:100]
        for r in missing:
            if check_control(self.cancel_event, self.pause_event):
                break
            if not _take_budget():
                break
            try:
                desc, _loc, _org = self._fetch_jd_text(r.get("Job URL") or "")
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
            if not new_title or not self.is_valid_job_title(new_title, name):
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
        # FIX W1-1: resolve provider=auto seeds to a real ATS adapter before
        # anything else. 198 of the 208 companies in run 20261003T233023 went
        # down the Chromium path purely because the seed said "auto".
        try:
            self._resolve_auto_providers(targets)
        except Exception as _sexc:
            print(f"   [sniff] skipped: {type(_sexc).__name__}: {_sexc}")

        crawl_start = time.monotonic()
        self._last_activity = time.monotonic()
        self._detail_lock = threading.Lock()
        self._detail_count = 0

        # REBASE 2026-10-03: _org computed this one-line host summary and then
        # never printed it, so nobody could see that the pool had collapsed to
        # one worker. It is the first thing in the log now.
        try:
            print(describe_host_budget()
                  + f" -> using {self.max_workers} browser worker(s)")
        except Exception:
            pass

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
            "Location Source", "Location Confidence",
            "URL Type", "Visa Sponsorship", "Relocation Support",
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
            # FIX P28: the embedded-JSON index and its notes are per company.
            self._current_seed_url = ""
            self._embedded_notes = []
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
                    # FIX W2-8: how much the location can be trusted.
                    "Location Confidence": "none",
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
                    # FIX P33: name the department/facet chips separately so a
                    # board that lists only chips is visible in the quarantine
                    # file instead of hiding among genuine bad URLs.
                    _why = ("category_or_department_link"
                            if is_category_facet_url(clean_url)
                            else "invalid_or_application_only_url")
                    quarantine_job(raw_title, raw_url, raw_location, _why, method)
                    return False
                if self.is_self_listing_url(clean_url, seed_url):
                    stats["rejected_url"] += 1
                    quarantine_job(raw_title, raw_url, raw_location, "self_listing_url", method)
                    return False
                clean_title = self.clean_job_title(raw_title)
                if not self.is_valid_job_title(clean_title, name):
                    # J2: try the URL slug before quarantining (button labels
                    # like "Show job" / chips like "Full time" on real job URLs).
                    rescued = self._title_from_url_slug(clean_url)
                    if rescued and self.is_valid_job_title(rescued, name):
                        clean_title = rescued
                        method = f"{method}+slug_title"
                    else:
                        # FIX P42: the card never showed the title; the job's
                        # own page does. ONE fetch, budgeted.
                        rescued = self._title_from_detail_page(clean_url, name)
                        if rescued:
                            clean_title = rescued
                            method = f"{method}+detail_title"
                    if not self.is_valid_job_title(clean_title, name):
                        stats["rejected_title"] += 1
                        quarantine_job(raw_title, raw_url, raw_location, "invalid_generic_or_department_title", method)
                        return False
                # W3-1: "Designer (m/w/d) Jobs in Innsbruck" -> the title
                # keeps the role, the place becomes a location candidate
                # (lowest priority: only used if nothing else resolves).
                _title_place = self._split_title_place(raw_title)[1]
                combined_context = f"{raw_location}\n{context}".strip()
                location, job_type, _, _, loc_source = self.parse_job_metadata(
                    name, clean_title, combined_context, clean_url
                )
                # FIX P19b: a location that came out of a provider API is a
                # STRUCTURED field, not scraped card furniture -- label it
                # the way the ATS scanner does so the same job gets the same
                # Location Source / Location Confidence in both files.
                if loc_source == "card" and method.endswith("_api"):
                    loc_source = "api"
                out_location = "Unknown" if location == "Not Specified" else location
                # FIX P0-49 (S-09/S-04): one gate in front of every writer.
                out_location = self._sanitize_job_location(out_location, name)
                # FIX P39: one role offered in several countries -- report the
                # seed's own country rather than whichever came last.
                _multi = self._location_in_target_country(raw_location, target_row)
                if _multi:
                    out_location, loc_source = _multi, "multi_target"
                # FIX P0-38: P0-11 used COMPANY_HEADQUARTERS as a last-resort
                # fill for Job Location. That fabricated a location the job
                # posting never stated, and because the UI derives Country
                # from Job Location, the HQ country was displayed as the JOB's
                # country -- A2G Technologies showed "India" for a role scoped
                # to the Netherlands purely because its HQ row said
                # "Pune, India". Country must come from the JD, so an unstated
                # location now stays "Unknown" instead of being guessed.
                # COMPANY_HEADQUARTERS is retained as reference data (and is
                # corrected to the RECRUITING OFFICE, not the global parent),
                # but it is deliberately no longer written into Job Location.
                # FIX P0-41: recover a town the gazetteer does not list, and
                # name the country from the posting's own host.
                if out_location == "Unknown" and _title_place:
                    # W3-1: the place the aggregator put in the title.
                    _tp = self._sanitize_job_location(_title_place, name)
                    if _tp != "Unknown":
                        out_location, loc_source = _tp, "title"
                if out_location == "Unknown":
                    # FIX P40: store jobs name the branch in the title and
                    # nowhere else. Tried BEFORE the host fallback so a real
                    # place always beats a guessed country.
                    _tt = self._location_from_title_text(raw_title)
                    if _tt:
                        out_location, loc_source = _tt, "title"
                if out_location == "Unknown":
                    _town = (self._town_from_address(raw_location, name)
                             or self._town_from_address(combined_context, name))
                    _site_c = self._country_from_site(
                        clean_url, target_row.get("careers_url"))
                    if _town and _site_c:
                        out_location, loc_source = f"{_town}, {_site_c}", "address+site"
                    elif _town:
                        out_location, loc_source = _town, "address"
                    elif _site_c:
                        out_location, loc_source = _site_c, "site_host"
                elif out_location and country_from_location is not None:
                    # Town resolved but no country in the string ("Cittaducale")
                    # -> name the country. W3-2: ask the supplementary
                    # gazetteer FIRST. "Cuneo" is Italy because Cuneo is in
                    # Italy, not because the page was served from a .it host,
                    # so that row keeps its real Location Confidence instead
                    # of being demoted to a site-derived guess.
                    try:
                        if not (country_from_location(out_location) or ""):
                            # FIX P29 (2026-10-05): the "already names its
                            # country in another language" test guarded the
                            # GAZETTEER branch only. When it fired it blanked
                            # _gaz_c and fell through to the `else`, which
                            # appended the SITE country unconditionally -- so
                            # "Formia, Lazio, Italia" was written as
                            # "Formia, Lazio, Italia, Italy" and, because the
                            # source string gained "+site", every one of those
                            # rows was demoted to Location Confidence `low`.
                            # 106 rows in run 20261005T003613 (Decathlon 97,
                            # Pam 9), all of them perfectly located. The test
                            # now short-circuits BOTH branches.
                            if self._w3_has_country(out_location):
                                loc_source = f"{loc_source}+gazetteer"
                            else:
                                _gaz_c = self._supplementary_country(out_location)
                                if _gaz_c:
                                    out_location = f"{out_location}, {_gaz_c}"
                                    loc_source = f"{loc_source}+gazetteer"
                                else:
                                    _site_c = self._country_from_site(
                                        clean_url, target_row.get("careers_url"))
                                    if _site_c:
                                        out_location = f"{out_location}, {_site_c}"
                                        loc_source = f"{loc_source}+site"
                    except Exception:
                        pass
                # "Milan, MI" is an ambiguous City/ST stub (MI = Michigan), and
                # the country parser already resolves "Milan" to Italy on its
                # own, so this only tidies the DISPLAY string -- the country is
                # the same either way. "Milan, Spain" used to be in this set
                # too, which meant a posting that genuinely parsed as Spain had
                # its country overwritten from the seed's scope. A real
                # country read off the page outranks a preset one, so that
                # case is now left alone.
                if target_row.get("target_country") == "Italy" and out_location == "Milan, MI":
                    out_location, loc_source = "Milan, Italy", "seed_scope+card"
                # J1: same job as an accepted row but in the other URL form
                # (fragment vs clean) — quarantine as a variant dupe (still
                # reviewable), don't double-count it.
                if self._frag_twin_in_index(seen_title_forms, clean_title, out_location,
                                            "#job=" in clean_url.lower(), combined_context):
                    stats["duplicates"] += 1
                    rec = base_record(raw_title, clean_title, raw_location, clean_url, "duplicate_url_variant")
                    rec.update({"Job Location": out_location, "Job Type": job_type,
                                "Location Source": loc_source,
                                "Location Confidence": self._location_confidence(
                                    out_location, loc_source),
                                "Extraction Method": method})
                    quarantine.append(rec); stats["quarantined"] += 1
                    return False
                # FIX W2-7 / W2-8: a row is only "outside the target country"
                # when something was actually READ. 1,617 rows in run
                # 20261003T233023 were quarantined with Location Source=none,
                # i.e. nothing was known about them, and 1,117 more passed on a
                # location inferred from the seed's web host. Both now go to a
                # deferred queue that gets one cheap detail fetch before any
                # verdict (see _resolve_deferred_scope).
                _policy_now = (target_row.get("scope_policy") or "global").lower()
                _real_url = (clean_url.startswith("http")
                             and "#job=" not in clean_url.lower())
                # FIX P50 (2026-10-07): "Multiple Locations" is Workday's
                # placeholder for a posting with several sites, not a place.
                # It was being scope-tested as though it were one, so all 18
                # such rows in run 20261007T201018 failed the gate -- six of
                # them Wolters Kluwer jobs whose URL says NLD. Treat it as
                # unproven so it earns the same deferred fetch as a blank.
                _unproven = (out_location == "Unknown"
                             or out_location == _MULTI_LOCATION_LABEL
                             or self._location_is_site_derived(loc_source))
                _scope_pending = False
                if _policy_now == "job_location" and _unproven and _real_url:
                    _scope_pending = True
                elif not self._scope_allows(target_row, out_location, combined_context, clean_url):
                    stats["rejected_scope"] += 1
                    rec = base_record(raw_title, clean_title, raw_location, clean_url, "outside_or_unproven_target_country")
                    rec.update({"Job Location": out_location, "Job Type": job_type,
                                "Location Source": loc_source,
                                "Location Confidence": self._location_confidence(
                                    out_location, loc_source),
                                "Extraction Method": method})
                    quarantine.append(rec); stats["quarantined"] += 1
                    return False
                # ── FIX P35 (2026-10-06): location recovery was tied to SCOPE ──
                # The deferred detail fetch above is the only thing that ever
                # calls _detail_location_from_url() for a list row, and it only
                # ran under scope_policy=job_location. LOOP is seed_url /
                # Austria, so its rows were accepted on the seed URL and NOTHING
                # ever fetched their detail page: all 16 were written with Job
                # Location=Unknown, Location Source=none, Country=Unknown, even
                # though the JD page states "Location  Salzburg - Vienna" in a
                # label/value strip that FIX P14 already parses correctly.
                #
                # How a row was SCOPED and whether its location is KNOWN are two
                # different questions. Any accepted row with a real URL and no
                # proven location now earns the same cheap fetch -- but on a
                # separate flag, because these rows must never be re-judged:
                # _scope_pending rows are quarantined when they stay unproven,
                # and reusing it here would have deleted the very 16 rows this
                # fix exists to populate.
                _loc_pending = (not _scope_pending) and _unproven and _real_url
                cid = self.canonical_job_id(
                    f"{name}|{target_row.get('target_country','Global')}", clean_url,
                    clean_title, out_location, provider,
                )
                # FIX P0-12: split work mode out of Job Location. "Remote"
                # inside a location string is ambiguous for a sponsorship tool
                # (remote-from-anywhere vs remote-within-country differ legally).
                work_mode = classify_work_mode(
                    f"{clean_title} {combined_context} {out_location}")
                rec = base_record(raw_title, clean_title, raw_location, clean_url)
                _policy = (target_row.get("scope_policy") or "global").lower()
                _tc = (target_row.get("target_country") or "Global").strip()
                if _policy == "seed_url" and _tc.casefold() != "global":
                    _scope_conf = ("verified"
                                   if self._scope_country_match(_tc, out_location,
                                                                combined_context, clean_url)
                                   else "unverified_seed_url")
                    # FIX P43 (2026-10-07): under seed_url the verdict may be
                    # taken from the surrounding context and the URL, and an
                    # Italy-scoped seed page says "Italy" all over itself. In
                    # run 20261007T174338 that marked an Amazon role whose own
                    # location reads "India" as **verified** for Italy, and
                    # accepted 8 rows sited in India, the UAE and Malta.
                    # Membership under seed_url still belongs to the seed, so
                    # the row is NOT dropped -- but a job that states its own
                    # country cannot be called verified against a different
                    # one. The JD's location overrules the page around it.
                    _own_country = self._country_of_location(out_location)
                    if (_own_country
                            and not self._scope_country_match(
                                _tc, _own_country, "", "")):
                        _scope_conf = "unverified_seed_url"
                elif _policy == "job_location" and _tc.casefold() != "global":
                    _scope_conf = "verified"
                else:
                    _scope_conf = "n/a"
                rec.update({
                    "Job Location": out_location,
                    "Work Mode": work_mode,
                    "Scope Confidence": ("pending_location" if _scope_pending
                                         else _scope_conf),
                    "Job Type": job_type,
                    "Location Source": loc_source,
                    "Location Confidence": self._location_confidence(
                        out_location, loc_source),
                    "Canonical Job ID": cid,
                    "Extraction Method": method,
                })
                if _scope_pending:
                    rec["_scope_pending"] = True
                    rec["_scope_context"] = combined_context[:500]
                elif _loc_pending:
                    rec["_loc_pending"] = True  # FIX P35: location only
                # FIX P17: remember WHICH blob this row's attributes came
                # from. A long blob reused by several jobs is a page/list
                # text, not a card, and _strip_shared_card_context() undoes
                # everything that was derived from it before the row is
                # written. (Belt and braces: the JS scope gate above already
                # refuses to hand out list containers, but API adapters and
                # static HTML paths do not go through it.)
                if len(combined_context) >= 200:
                    rec["_ctx_key"] = re.sub(
                        r"\s+", " ", combined_context).strip().casefold()[:4000]
                # FIX P0-30: prefer the FULL description when the provider API
                # already returned one (jd_text) — zero extra requests — else
                # fall back to the card blurb. An employer-published seniority
                # field (experience_level_hint) outranks every inference.
                _jd = str(job.get("jd_text") or "")
                apply_experience_to_record(
                    rec,
                    jd_text=_jd,
                    card_context=combined_context,
                    title=clean_title,
                    level_hint=str(job.get("experience_level_hint") or ""),
                )
                # FIX W2-6b: free sponsorship/Blue Card evidence when the
                # provider API already shipped the description.
                if _jd:
                    try:
                        self._apply_support_from_text(rec, _jd, clean_url)
                    except Exception:
                        pass
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
                        self._current_seed_url = seed_url
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
                    if _seed_host and not _dns_resolves(
                            _seed_host, self.cancel_event, self.pause_event):
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
                    _crawl_locale, _crawl_accept_language = self._preferred_browser_locales(target_row, seed_url)
                    diagnostics.append(f"browser locale: {_crawl_locale}; Accept-Language: {_crawl_accept_language}")
                    with sync_playwright() as pw:
                        browser = pw.chromium.launch(
                            headless=True,
                            args=BROWSER_ARGS
                        )
                        ctx = browser.new_context(
                            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
                            # FIX P0-29 (restored from _org): a smaller viewport
                            # rasterises less on every page of every company.
                            viewport={"width": 1280, "height": 800},
                            locale=self._preferred_browser_locales(target_row, seed_url)[0],
                            extra_http_headers={"Accept-Language": self._preferred_browser_locales(target_row, seed_url)[1]},
                        )
                        # FIX P0-29 (restored from _org): drop images, media,
                        # fonts and ~28 analytics/chat/CMP hosts for the WHOLE
                        # crawl context. Measured -44% bytes in its docstring.
                        # The refactor to common.BROWSER_ARGS kept
                        # --blink-settings=imagesEnabled=false, which covers
                        # images only, and silently lost the rest. Fails open.
                        install_page_resource_blocking(ctx)
                        page = ctx.new_page()
                        loaded = False
                        for attempt in range(1, self.config.GOTO_RETRIES + 1):
                            # STOP FAST: seed navigation retries are a blocking
                            # Playwright wait — gate each attempt on Stop so a
                            # cancel mid-backoff doesn't wait out the retry
                            # chain.
                            check_cancelled(self.cancel_event)
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
                        self._current_seed_url = seed_url
                        _direct_listing_seed = self._is_direct_listing_seed_url(seed_url)
                        if _direct_listing_seed:
                            diagnostics.append("seed classified as direct job listing; search/landing reroute disabled")
                        current = self.extract_visible_jobs(target)
                        if _direct_listing_seed and not current:
                            # Run the scope-independent direct-listing extractor
                            # before any landing/search recovery. This is the
                            # critical path for plain WordPress/Elementor lists.
                            current = self._direct_listing_anchor_rows(target)
                            if current:
                                diagnostics.append(f"direct-listing anchors: {len(current)}")
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
                        if not current and not _direct_listing_seed:
                            changed = self.handle_landing_page_redirect(page, seed_url) or self.trigger_search_if_present(page)
                            if changed:
                                page.wait_for_timeout(1500)
                                self.dismiss_initial_blockers(page, deep=False)
                                target = self.check_iframes(page) or page
                                current = self.extract_visible_jobs(target)
                        # FIX W1-2: a board that shows nothing after the
                        # consent/redirect/search recovery steps is dead. 97
                        # companies in run 20261003T233023 produced zero rows
                        # and still cost 58 minutes between them, because the
                        # crawler went on to scroll, click and paginate an
                        # empty page. Give up instead.
                        if not current and not company_jobs:
                            _dead_for = time.monotonic() - started
                            if _dead_for > self.config.DEAD_END_ABORT_SEC:
                                diagnostics.append(
                                    f"dead end: no job cards after "
                                    f"{_dead_for:.0f}s (DEAD_END_ABORT_SEC="
                                    f"{self.config.DEAD_END_ABORT_SEC}); "
                                    f"skipping scroll/pagination")
                                raise _DeadEndBoard()
                        for job in current: process_job(job)
                        _direct_pager = self._direct_listing_pagination_state(target) if _direct_listing_seed else {"hasNext": True, "hasLoadMore": True, "hasPager": True}
                        _needs_direct_pagination = bool(_direct_pager.get("hasNext") or _direct_pager.get("hasLoadMore") or _direct_pager.get("hasPager"))
                        if _direct_listing_seed:
                            if _direct_pager.get("hasLoadMore"):
                                self.click_load_more_repeatedly(page, target)
                            elif not _needs_direct_pagination:
                                diagnostics.append("direct listing complete: no pagination/load-more control detected")
                        else:
                            self.aggressive_infinite_scroll(page, target)
                            self.click_load_more_repeatedly(page, target)

                        prev_total = -1; empty_pages = 0; low_yield = 0
                        repeat_pages = 0
                        seed_params = parse_qsl(urlparse(seed_url).query)
                        _page_limit = self.config.MAX_PAGINATION_PAGES if (not _direct_listing_seed or _needs_direct_pagination) else 0
                        for page_num in range(1, _page_limit + 1):
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
                            # FIX W1-3: a hard LIST wall-clock cap. Hays
                            # Germany spent 23 minutes here on one board and
                            # Work in Austria/Finland ~21 each; three
                            # aggregators alone cost over an hour of a 5.5h
                            # run. The detail budget had a cap, the list phase
                            # did not.
                            if (time.monotonic() - started
                                    > self.config.LIST_TIME_BUDGET_SEC):
                                diagnostics.append(
                                    f"LIST budget reached at page {page_num} "
                                    f"({self.config.LIST_TIME_BUDGET_SEC}s, "
                                    f"SPONSORSCOUT_LIST_BUDGET); "
                                    f"{len(company_jobs)} rows kept")
                                break
                            target = self.check_iframes(page) or page
                            if not _direct_listing_seed:
                                self.progressive_scroll_and_wait(page, target)
                            batch = self.extract_visible_jobs(target)
                            if _direct_listing_seed and not batch:
                                batch = self._direct_listing_anchor_rows(target)
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
                            # FIX W1-5: 10,798 duplicates were re-crawled in
                            # run 20261003T233023 -- pagination kept walking
                            # pages that were overwhelmingly the same cards.
                            # `added <= 2` never fires on a 20-card page that
                            # is 19/20 repeats, so measure the RATIO too.
                            if batch and (added / max(1, len(batch))) <= self.config.REPEAT_PAGE_RATIO:
                                repeat_pages += 1
                            else:
                                repeat_pages = 0
                            if repeat_pages >= self.config.REPEAT_PAGES_STOP:
                                self._log_pagination_stop(
                                    diagnostics,
                                    f"STOPPED: {repeat_pages} pages of repeats",
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
                            # FIX P45 (2026-10-07): a direct listing with no
                            # pager sets _page_limit = 0, so range(1, 1) is
                            # EMPTY -- and a for/else runs its else clause
                            # when the loop ends without break, empty
                            # included. Every such company logged the
                            # self-contradicting pair "no pagination/
                            # load-more control detected" AND "page cap
                            # reached (250) after page 250" (Coolblue,
                            # McDonald's, Elior, Stafide, Orange Quarter).
                            # No pages were ever walked; only the message was
                            # wrong. Say nothing when there was no loop.
                            if _page_limit:
                                self._log_pagination_stop(
                                    diagnostics,
                                    f"page cap reached ({self.config.MAX_PAGINATION_PAGES})",
                                    self.config.MAX_PAGINATION_PAGES,
                                    len(company_jobs))

                        company_jobs = self._dedupe_records(company_jobs)
                        self._resolve_deferred_scope(
                            target_row, company_jobs, quarantine, stats,
                            diagnostics)
                        if self.detail_scan and company_jobs:
                            self._run_detail_pipeline(page, name, company_jobs)
                        try: ctx.close()
                        except Exception: pass
                        try: browser.close()
                        except Exception: pass
                        browser = ctx = page = None
                elif company_jobs and not self.detail_scan:
                    company_jobs = self._dedupe_records(company_jobs)
                    self._resolve_deferred_scope(
                        target_row, company_jobs, quarantine, stats, diagnostics)
                elif self.detail_scan and company_jobs:
                    company_jobs = self._dedupe_records(company_jobs)
                    self._resolve_deferred_scope(
                        target_row, company_jobs, quarantine, stats, diagnostics)
                    # API-first crawl still gets explicit detail evidence when
                    # requested. FIX W2-6c: the detail pass is HTTP-first now,
                    # so a missing Chromium must degrade it, not abort the
                    # company -- before this, every API company on a machine
                    # without Playwright was logged as an ERROR after writing
                    # all of its rows.
                    if sync_playwright is None:
                        _pw_reason = _playwright_unavailable_reason()
                        diagnostics.append(
                            f"detail: HTTP only (playwright unavailable: "
                            f"{_pw_reason})")
                        self._run_detail_pipeline(None, name, company_jobs)
                        raise _DetailHttpOnly()
                    with sync_playwright() as pw:
                        browser = pw.chromium.launch(headless=True, args=BROWSER_ARGS)
                        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
                        # FIX P0-29 (restored from _org): the detail pass visits
                        # hundreds of pages per company; it is the single
                        # heaviest consumer of bandwidth in a run.
                        install_page_resource_blocking(ctx)
                        page = ctx.new_page()
                        self._run_detail_pipeline(page, name, company_jobs)
                        try: ctx.close()
                        except Exception: pass
                        try: browser.close()
                        except Exception: pass
                        browser = ctx = page = None
            except _DeadEndBoard:
                pass
            except _DetailHttpOnly:
                pass
            except ScanCancelled:
                print(f"   CANCELLED: stopped during {name}")
                error = "ScanCancelled: stopped by user"
                diagnostics.append(error)
                status = "cancelled"
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

            # FIX W2-7b: the deferred-scope resolver MUST run even when the
            # company ended in an exception (Playwright missing, seed page
            # dead, budget abort). Picnic Technologies proved it: the browser
            # branch raised, the in-branch call was skipped, and two rows with
            # Job Location=Unknown were written as accepted with
            # Scope Confidence=pending_location. The method pops its own
            # markers, so a second call is a no-op.
            # FIX P17: strip blob-derived attributes BEFORE the scope
            # resolver runs -- rows whose location was invented by a shared
            # blob are demoted to pending here, and the resolver then fetches
            # their own detail page for the real one.
            try:
                self._strip_shared_card_context(
                    name, target_row, company_jobs, diagnostics)
            except Exception as _cexc:
                diagnostics.append(
                    f"shared-context pass failed: {type(_cexc).__name__}: {_cexc}")
            try:
                self._resolve_deferred_scope(
                    target_row, company_jobs, quarantine, stats, diagnostics)
            except Exception as _rexc:
                # Never let the safety net itself drop a company's rows:
                # anything still pending is quarantined, unproven.
                diagnostics.append(
                    f"scope re-check failed: {type(_rexc).__name__}: {_rexc}")
                for _cid, _rec in list(company_jobs.items()):
                    _rec.pop("_loc_pending", None)  # FIX P35: not a scope flag
                    if _rec.pop("_scope_pending", None):
                        _rec.pop("_scope_context", None)
                        company_jobs.pop(_cid, None)
                        _rec["Record Status"] = "quarantine"
                        _rec["Quarantine Reason"] = "outside_or_unproven_target_country"
                        quarantine.append(_rec)
                        stats["quarantined"] += 1
                        stats["rejected_scope"] += 1

            # FIX P18: record the pre-prune scope counter so the two numbers
            # can still be compared without them contradicting each other.
            if stats.get("rejected_scope"):
                diagnostics.append(
                    f"scope rejects before dedupe: {stats['rejected_scope']}")
            # Scope can improve after detail scan; normalize internal sentinel.
            for rec in company_jobs.values():
                if rec.get("Job Location") == "Not Specified": rec["Job Location"] = "Unknown"

            written = 0
            duplicates_global = 0
            accepted_rows = []
            assert not any(r.get("_scope_pending") for r in company_jobs.values()), \
                "W2-7: a pending-scope row reached the writer"
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
                        # FIX P18 (2026-10-04): "Quarantined" is the PRUNED
                        # list actually written to the CSV (accepted twins and
                        # rows already quarantined in an earlier pass are
                        # dropped just above), while "Rejected Scope" used to
                        # be a raw counter that still included them -- so the
                        # log claimed more scope rejects than quarantines:
                        # Harnham 48 > 24, Amazon 28 > 19, Atos 35 > 23 in run
                        # 20261004T182639. Both columns are now counted off
                        # the SAME pruned list, so the scan log reconciles
                        # with the quarantine CSV row for row. The pre-prune
                        # counter is kept, in the diagnostics, where a gap is
                        # information rather than a contradiction.
                        "Rejected Scope": sum(
                            1 for _r in quarantine
                            if _r.get("Quarantine Reason")
                            == "outside_or_unproven_target_country"),
                        "Error": error,
                        "Diagnostics": " | ".join(
                            diagnostics + list(
                                getattr(self, "_embedded_notes", []))
                        )[-4000:],
                        "Duration Sec": round(time.monotonic()-started,1), "Seed URL": seed_url,
                    })
                if error:
                    etype, _, emsg = error.partition(": ")
                    self._record_error(seed_name, "target", etype, emsg, seed_url)
            print(f"   {status.upper()} {name}: wrote={written}, quarantined={len(quarantine)}, "
                  f"dups={stats['duplicates']}, scope_reject={stats['rejected_scope']}")
            return written, len(quarantine), status

        totals = collections.Counter()
        # FIX W1-4: run 20261003T233023 was effectively SERIAL -- the sum of
        # the per-company durations (5.73 h) equalled the wall clock, because
        # every company, including the pure-API ones, queued behind the single
        # browser worker this host could afford. API companies cost a socket,
        # not 600 MB of Chromium, so they get their own (much wider) pool and
        # run while the browser pool grinds through the DOM boards.
        _api_providers = {"greenhouse", "ashby", "lever", "personio",
                          "recruitee", "workable", "teamtailor", "oracle",
                          "bamboohr", "pam", "digitalrecruiters", "workday",
                          "smartrecruiters",
                          # FIX P19: both are JSON feeds now, so they belong
                          # in the socket pool, not behind the browser pool.
                          "icims", "eightfold"}

        def _is_api_target(row):
            return (row.get("provider") or "auto").strip().lower() in _api_providers

        api_targets = [(i, r) for i, r in enumerate(targets, 1) if _is_api_target(r)]
        dom_targets = [(i, r) for i, r in enumerate(targets, 1) if not _is_api_target(r)]
        api_workers = min(recommended_workers("http"), max(1, len(api_targets)))
        print(f"   pools: {len(api_targets)} API target(s) x{api_workers} HTTP "
              f"worker(s) || {len(dom_targets)} DOM target(s) "
              f"x{self.max_workers} browser worker(s)")

        def _drain(futures):
            # STOP FAST: cf.as_completed() blocks until the NEXT future
            # finishes, so a Stop pressed while every worker sat inside a slow
            # browser fetch waited minutes for the drain to even notice.  Poll
            # the pending set every 0.5 s instead: the cancel is seen promptly,
            # queued work is cancelled, and in-flight companies get a short
            # grace window to unwind through their own gates.
            pending = set(futures)
            while pending:
                if self.cancel_event is not None and self.cancel_event.is_set():
                    for _f in pending:
                        _f.cancel()
                    # Grace: gated workers raise ScanCancelled within ~1 s of
                    # their current blocking call.  Stragglers stuck in a long
                    # Playwright goto are left to finish in the background —
                    # pool shutdown is wait=False and the CSV/DB ingest is
                    # idempotent, so nothing is corrupted by walking away.
                    done, pending = cf.wait(pending, timeout=3.0)
                    for _f in done:
                        try:
                            wrote, quarantined, status = _f.result()
                        except (ScanCancelled, cf.CancelledError):
                            totals["cancelled"] += 1
                        except Exception:
                            pass  # already stopping; don't log straggler noise
                        else:
                            totals["written"] += wrote
                            totals["quarantined"] += quarantined
                            totals[status] += 1
                    break
                done, pending = cf.wait(
                    pending, timeout=0.5,
                    return_when=cf.FIRST_COMPLETED)
                for future in done:
                    try:
                        wrote, quarantined, status = future.result()
                        totals["written"] += wrote
                        totals["quarantined"] += quarantined
                        totals[status] += 1
                    except (ScanCancelled, cf.CancelledError):
                        totals["cancelled"] += 1
                    except Exception as exc:
                        totals["thread_errors"] += 1
                        print(f"Worker escaped error: {type(exc).__name__}: {exc}")
                        self._record_error("?", "worker", type(exc).__name__, str(exc))

        api_pool = cf.ThreadPoolExecutor(max_workers=api_workers,
                                         thread_name_prefix="api")
        dom_pool = cf.ThreadPoolExecutor(max_workers=self.max_workers,
                                         thread_name_prefix="dom")
        try:
            futures = []
            for i, row in api_targets:
                if check_control(self.cancel_event, self.pause_event):
                    break
                futures.append(api_pool.submit(crawl_target, i, row))
            for i, row in dom_targets:
                if check_control(self.cancel_event, self.pause_event):
                    print(f"   CANCELLED: not submitting remaining targets")
                    break
                futures.append(dom_pool.submit(crawl_target, i, row))
            _drain(futures)
        finally:
            # STOP FAST: cancel() only stops queued work; running companies
            # unwind via ScanCancelled. shutdown(wait=False) avoids blocking
            # here on stragglers, then cancel() the leftovers.
            api_pool.shutdown(wait=False, cancel_futures=True)
            dom_pool.shutdown(wait=False, cancel_futures=True)

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
                    if _dns_resolves(h, self.cancel_event, self.pause_event):
                        still_live.append(r)
                    else:
                        print(f"   confirmed unreachable: {r['name']} ({h})")
                if still_live:
                    print(f"[dns retry] {len(still_live)} host(s) now resolve — re-crawling.")
                    with cf.ThreadPoolExecutor(max_workers=self.max_workers) as ex2:
                        futs = [ex2.submit(crawl_target, i, r)
                                for i, r in enumerate(still_live, 1)]
                        for fut in cf.as_completed(futs):
                            # STOP FAST: a Stop during the retry sweep must not
                            # wait on stragglers — cancel the queued retries
                            # (crawl_target gates at entry anyway) and leave.
                            if (self.cancel_event is not None
                                    and self.cancel_event.is_set()):
                                for _f in futs:
                                    _f.cancel()
                                break
                            try:
                                wrote, quarantined, status = fut.result()
                                totals["written"] += wrote
                                totals["quarantined"] += quarantined
                                totals[status] += 1
                                totals["dns_recovered"] += 1 if wrote else 0
                            except (ScanCancelled, cf.CancelledError):
                                totals["cancelled"] += 1
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
        # W3-3 (P7): the same posting must not be in two result files.
        try:
            _dupes, _scanned = self._dedupe_cross_bucket(
                self.output_csv, recruiter_csv, quarantine_csv, columns)
            if _dupes:
                print(f"  Cross-source duplicates moved to quarantine: {_dupes} "
                      f"(aggregator copies of an employer's own posting)")
        except Exception as _dexc:
            print(f"  [warn] cross-source dedupe skipped: "
                  f"{type(_dexc).__name__}: {_dexc}")
        print(f"\nv7 scan complete in {elapsed}s: direct/recruiter rows={totals['written']}, "
              f"quarantined={totals['quarantined']}, thread_errors={totals['thread_errors']}")
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
