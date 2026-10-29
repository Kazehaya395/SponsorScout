"""Domain, Category, and Skill taxonomy loader and full-JD pattern matchers.

Designed to run outside the job scan loop (post-scan or on-demand from SQLite)
so scanning stays lightweight while Insights gets full-JD accuracy.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import sys
import hashlib
import threading
from collections import deque
try:
    from re import _parser as _regex_parser
except ImportError:  # Python 3.10
    import sre_parse as _regex_parser
from pathlib import Path

logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).resolve().parent


def _find_data_file(filename: str) -> Path:
    candidates = [
        _MODULE_DIR / filename,
        Path(getattr(sys, "_MEIPASS", "")) / "sponsorscout" / "insights" / filename,
        Path(getattr(sys, "_MEIPASS", "")) / filename,
        Path(os.getcwd()) / "sponsorscout" / "insights" / filename,
    ]
    for p in candidates:
        if p and p.exists():
            return p
    return _MODULE_DIR / filename


_CATEGORIES: dict[str, list[str]] = {}
_CATEGORY_REGEXES: list[tuple[str, re.Pattern]] = []
_CATEGORY_KEYWORD_RE: dict[str, list[tuple[re.Pattern, int]]] = {}
_DOMAINS: dict[str, list[str]] = {}
_DOMAIN_MAP: dict[str, str] = {}
_DOMAIN_SEED_REGEXES: list[tuple[re.Pattern, str, int]] = []
_SKILLS: list[dict] = []
_CI_BUCKETS: list[tuple[re.Pattern, list[dict]]] = []
_CS_SKILLS: list[dict] = []
_SOFT_SKILL_TERMS: set[str] = set()

# Categories whose specific role keywords should outrank generic "engineer" /
# "developer" / "manager" matches in broad umbrella categories.
_SPECIALIZED_CATEGORIES = {
    "Cloud & DevOps",
    "Data & Analytics",
    "Cybersecurity",
    "Quality Assurance & Testing",
    "Healthcare & Life Sciences",
    "Legal & Regulatory",
    "Finance & Accounting",
    "HR & Recruitment",
    "Hospitality & Food Service",
    "Retail & Store Operations",
}

_GENERIC_TITLE_TOKENS = {
    "engineer",
    "engineers",
    "engineering",
    "developer",
    "specialist",
    "manager",
    "lead",
    "director",
    "associate",
    "assistant",
    "coordinator",
    "consultant",
    "operator",
    "technician",
    "software",
}

# Full-JD cues used ONLY when seed industry is missing / "Unknown" and JD text
# is available (e.g. Assisted Ingestion, recruiter boards, custom portals).
_JD_DOMAIN_CUES: dict[str, list[str]] = {
    "Financial Services & Fintech": [
        "fintech", "banking", "payment processing", "payments platform",
        "capital markets", "wealth management", "asset management",
        "algorithmic trading", "credit risk", "insurtech", "psd2", "kyc",
        "aml", "sepa", "open banking", "financial institution", "neobank",
    ],
    "Healthcare, Pharma & Life Sciences": [
        "pharmaceutical", "biotech", "biotechnology", "clinical trials",
        "medical devices", "healthtech", "patient care", "hospital",
        "pharmacovigilance", "life sciences", "diagnostics", "therapeutics",
    ],
    "E-Commerce, Retail & Fashion": [
        "e-commerce", "ecommerce", "online marketplace", "omnichannel retail",
        "merchandising", "luxury fashion", "apparel", "consumer goods",
        "fmcg", "supermarket", "retail stores", "d2c brand",
    ],
    "Engineering, Industrial & Automotive": [
        "semiconductor", "lithography", "automotive", "electric vehicles",
        "aerospace", "industrial automation", "mechatronics", "robotics",
        "renewable energy", "power grid", "solar", "wind turbine",
        "chemical plant", "manufacturing plant", "embedded hardware",
    ],
    "AI, Data & Analytics": [
        "artificial intelligence company", "ai research lab", "foundation models",
        "large language models", "data platform", "business intelligence platform",
        "quantum computing", "geospatial analytics",
    ],
    "Telecommunications & Media": [
        "telecommunications", "telecom operator", "5g network", "game studio",
        "video games", "igaming", "streaming platform", "broadcast media",
    ],
    "Logistics, Supply Chain & Transport": [
        "freight forwarding", "supply chain platform", "last-mile delivery",
        "fleet management", "maritime", "shipping", "3pl", "warehousing",
    ],
    "Travel, Hospitality & Food": [
        "online travel", "travel platform", "hotel group", "hospitality group",
        "airline", "catering", "restaurant group",
    ],
    "Staffing, HR & Recruiting": [
        "recruitment agency", "executive search firm", "staffing agency",
        "recruitment consultancy", "our client is looking", "namens onze opdrachtgever",
        "voor onze opdrachtgever", "im auftrag unseres kunden",
    ],
    "Tech & Software": [
        "saas platform", "b2b saas", "cloud-native platform", "software company",
        "developer platform", "enterprise software", "cybersecurity platform",
    ],
}
_JD_DOMAIN_REGEXES: list[tuple[str, re.Pattern]] = []

_SOFT_SKILL_GROUPS = {
    "Soft Skills",
    "Professional Skills & Work Methods",
    "Project, Product & Business Operations",
    "HR, Recruitment & People Operations",
}


class InsightsDataError(RuntimeError):
    """Missing or unusable packaged taxonomy; surfaced by the Insights UI."""


_DATA_LOCK = threading.RLock()
_DATA_LOADED = False
_DATA_ERROR = ""
_DATA_VERSION = ""
_MATCH_EDGES: list[dict] = [{}]
_MATCH_FAIL: list[int] = [0]
_MATCH_OUTPUTS: list[set] = [set()]
_MATCH_FALLBACK: set[int] = set()
_MATCH_LITERAL_CLAUSES: list[list[int]] = []
_MATCH_CLAUSE_SIZE: list[int] = []
_MATCH_CLAUSE_SKILL: list[int] = []
_MATCH_ORDER: list[tuple] = []
_FOLD_TRANSLATION = str.maketrans({'İ': 'i', 'ı': 'i', 'ſ': 's', 'K': 'k'})


def _fold(text):
    # Superset of Python re.IGNORECASE, including its four special Unicode
    # ASCII-letter equivalences. The ORIGINAL regex still verifies every hit.
    return text.translate(_FOLD_TRANSLATION).casefold()


def _required_literals(sequence):
    """A conservative OR-clause of literals required by any possible match.

    A sequence may choose any mandatory component; a branch must cover ALL
    alternatives. Optional/unknown constructs are never used to reject a row.
    Patterns without a provable literal use the exact-regex fallback.
    """
    options, run = [], []
    def flush():
        if len(run) >= 2:
            options.append({_fold(''.join(run))})
        run.clear()
    for op, arg in sequence:
        name = str(op)
        if name == 'LITERAL':
            run.append(chr(arg))
            continue
        flush()
        clause = set()
        if name == 'SUBPATTERN':
            clause = _required_literals(arg[-1])
        elif name == 'BRANCH':
            alternatives = [_required_literals(branch) for branch in arg[1]]
            if alternatives and all(alternatives):
                clause = set().union(*alternatives)
        elif name in ('MAX_REPEAT', 'MIN_REPEAT', 'POSSESSIVE_REPEAT') and arg[0] >= 1:
            clause = _required_literals(arg[-1])
        if clause:
            options.append(clause)
    flush()
    if not options:
        return set()
    return max(options, key=lambda words: (min(map(len, words)), -len(words), sum(map(len, words))))


def _literal_clauses(sequence):
    """DNF of NECESSARY literals: all words in any one clause must occur.

    A branch unions alternatives; a sequence intersects mandatory components.
    Optional/unsupported constructs add no restrictions. Bounded expansion
    may discard an AND component (a weaker safe prefilter), never a branch.
    The unchanged original regex is always the final matching authority.
    """
    clauses,run = [frozenset()],[]
    def require(component):
        nonlocal clauses
        if component==[frozenset()]:
            return
        if len(clauses)*len(component)>256:
            # A AND B implies either side. Keep the more selective side rather
            # than expanding an adversarial Cartesian product without bound.
            def score(items):
                return min(sum(len(word) for word in item) for item in items)
            if score(component)>score(clauses):
                clauses=component
            return
        combined = {left|right for left in clauses for right in component}
        # Absorb supersets: (A) OR (A AND B) is exactly (A).
        minimal=[]
        for item in sorted(combined,key=len):
            if not any(previous<=item for previous in minimal):
                minimal.append(item)
        clauses=minimal
    def flush():
        if len(run)>=2:
            require([frozenset((_fold(''.join(run)),))])
        run.clear()
    for op,arg in sequence:
        name=str(op)
        if name=='LITERAL':
            run.append(chr(arg));continue
        flush()
        if name=='SUBPATTERN':
            require(_literal_clauses(arg[-1]))
        elif name=='BRANCH':
            alternatives=[]
            for branch in arg[1]:
                alternatives.extend(_literal_clauses(branch))
                if len(alternatives)>256:
                    # An oversized OR cannot drop alternatives. Discard only
                    # this restriction, retaining any surrounding AND terms.
                    alternatives=[frozenset()]
                    break
            if any(not clause for clause in alternatives):
                alternatives=[frozenset()]
            require(alternatives or [frozenset()])
        elif name in ('MAX_REPEAT','MIN_REPEAT','POSSESSIVE_REPEAT') and arg[0]>=1:
            require(_literal_clauses(arg[-1]))
    flush()
    return clauses


def _build_skill_index():
    global _MATCH_EDGES,_MATCH_FAIL,_MATCH_OUTPUTS,_MATCH_FALLBACK
    global _MATCH_LITERAL_CLAUSES,_MATCH_CLAUSE_SIZE,_MATCH_CLAUSE_SKILL,_MATCH_ORDER
    edges,failures,outputs,fallback=[{}],[0],[set()],set()
    literal_ids,literal_clauses,clause_sizes,clause_skills={},{},[],[]
    for index,item in enumerate(_SKILLS):
        try:
            clauses=_literal_clauses(_regex_parser.parse(item['regex'].pattern,item['regex'].flags))
        except Exception:
            clauses=[frozenset()]
        if any(not clause for clause in clauses):
            fallback.add(index);continue
        for clause in clauses:
            clause_id=len(clause_sizes)
            clause_sizes.append(len(clause));clause_skills.append(index)
            for literal in clause:
                if literal not in literal_ids:
                    literal_id=literal_ids[literal]=len(literal_ids)
                    state=0
                    for char in literal:
                        if char not in edges[state]:
                            edges[state][char]=len(edges)
                            edges.append({});failures.append(0);outputs.append(set())
                        state=edges[state][char]
                    outputs[state].add(literal_id)
                literal_clauses.setdefault(literal_ids[literal],[]).append(clause_id)
    pending=deque(edges[0].values())
    while pending:
        state=pending.popleft()
        for char,child in edges[state].items():
            pending.append(child);fail=failures[state]
            while fail and char not in edges[fail]:
                fail=failures[fail]
            failures[child]=edges[fail].get(char,0)
            outputs[child].update(outputs[failures[child]])
    _MATCH_EDGES,_MATCH_FAIL,_MATCH_OUTPUTS,_MATCH_FALLBACK=edges,failures,outputs,fallback
    _MATCH_LITERAL_CLAUSES=[literal_clauses.get(i,[]) for i in range(len(literal_ids))]
    _MATCH_CLAUSE_SIZE,_MATCH_CLAUSE_SKILL=clause_sizes,clause_skills
    _MATCH_ORDER=[(not bool(item['regex'].flags & re.I),i) for i,item in enumerate(_SKILLS)]


def _candidate_skills(text):
    # One pass records literal presence. AND/OR prerequisites reject e.g.
    # "project management" on "stakeholder management", without rescanning the
    # whole description once for every shared-word regex candidate.
    literals=set();state=0
    edges,failures,outputs=_MATCH_EDGES,_MATCH_FAIL,_MATCH_OUTPUTS
    for char in _fold(text):
        while state and char not in edges[state]:
            state=failures[state]
        state=edges[state].get(char,0)
        if outputs[state]:
            literals.update(outputs[state])
    candidates=set(_MATCH_FALLBACK);counts={}
    for literal in literals:
        for clause in _MATCH_LITERAL_CLAUSES[literal]:
            skill=_MATCH_CLAUSE_SKILL[clause]
            if skill in candidates:
                continue
            count=counts.get(clause,0)+1;counts[clause]=count
            if count==_MATCH_CLAUSE_SIZE[clause]:
                candidates.add(skill)
    return sorted(candidates,key=_MATCH_ORDER.__getitem__)


def taxonomy_version() -> str:
    global _DATA_VERSION,_DATA_ERROR
    if _DATA_VERSION:
        return _DATA_VERSION
    with _DATA_LOCK:
        if _DATA_ERROR:
            raise InsightsDataError(_DATA_ERROR)
        if not _DATA_VERSION:
            try:
                fingerprint = hashlib.sha256(b'indexed-matcher-20261024-v2-clauses')
                for name in ('categories.json','domains.json','skills_dictionary.csv'):
                    path = _find_data_file(name)
                    if not path.is_file():
                        raise InsightsDataError('Insights taxonomy file is missing: '+name)
                    fingerprint.update(path.read_bytes())
                _DATA_VERSION = fingerprint.hexdigest()[:24]
            except Exception as exc:
                _DATA_ERROR = str(exc)
                raise InsightsDataError(_DATA_ERROR) from exc
        return _DATA_VERSION


def _load_data() -> None:
    global _DATA_LOADED, _DATA_ERROR, _DATA_VERSION
    if _DATA_LOADED:
        return
    with _DATA_LOCK:
        if _DATA_LOADED:
            return
        if _DATA_ERROR:
            raise InsightsDataError(_DATA_ERROR)
        try:
            for name in ('categories.json', 'domains.json', 'skills_dictionary.csv'):
                if not _find_data_file(name).is_file():
                    raise InsightsDataError('Insights taxonomy file is missing: ' + name)
            _load_data_impl()
            if not _SKILLS or not _CATEGORIES or not _DOMAINS:
                raise InsightsDataError('Insights taxonomy is empty or unreadable; reinstall the packaged data files.')
            _build_skill_index()
            fingerprint = hashlib.sha256(b'indexed-matcher-20261024-v2-clauses')
            for name in ('categories.json', 'domains.json', 'skills_dictionary.csv'):
                fingerprint.update(_find_data_file(name).read_bytes())
            _DATA_VERSION = fingerprint.hexdigest()[:24]
            _DATA_LOADED = True
        except Exception as exc:
            _DATA_ERROR = str(exc)
            raise InsightsDataError(_DATA_ERROR) from exc


def _load_data_impl() -> None:
    global _CATEGORIES, _CATEGORY_REGEXES, _CATEGORY_KEYWORD_RE
    global _DOMAINS, _DOMAIN_MAP, _DOMAIN_SEED_REGEXES, _JD_DOMAIN_REGEXES
    global _SKILLS, _CI_BUCKETS, _CS_SKILLS, _SOFT_SKILL_TERMS
    # Initialize once under _DATA_LOCK. Missing assets cache an explicit
    # error; they must never cause repeated regex appends per job.
    _CATEGORIES.clear(); _CATEGORY_REGEXES.clear(); _CATEGORY_KEYWORD_RE.clear()
    _DOMAINS.clear(); _DOMAIN_MAP.clear(); _SKILLS.clear()

    # 1. Load categories
    cat_file = _find_data_file("categories.json")
    if cat_file.exists():
        try:
            with open(cat_file, "r", encoding="utf-8") as f:
                _CATEGORIES = json.load(f)
            for cat, keywords in _CATEGORIES.items():
                sorted_kw = sorted(keywords, key=len, reverse=True)
                pattern = r"\b(?:" + "|".join(re.escape(k) for k in sorted_kw) + r")\b"
                _CATEGORY_REGEXES.append((cat, re.compile(pattern, re.I)))
                kw_res: list[tuple[re.Pattern, int]] = []
                for kw in sorted_kw:
                    kw_norm = kw.strip().lower()
                    words = kw_norm.split()
                    if kw_norm in _GENERIC_TITLE_TOKENS:
                        weight = 2
                    elif len(words) >= 2:
                        weight = 5 + min(len(words), 3)
                    else:
                        weight = 4
                    if cat in _SPECIALIZED_CATEGORIES and kw_norm not in _GENERIC_TITLE_TOKENS:
                        weight += 2
                    kw_res.append((re.compile(r"\b" + re.escape(kw) + r"\b", re.I), weight))
                _CATEGORY_KEYWORD_RE[cat] = kw_res
        except Exception as e:
            logger.warning("Could not load categories.json: %s", e)

    # 2. Load domains
    dom_file = _find_data_file("domains.json")
    if dom_file.exists():
        try:
            with open(dom_file, "r", encoding="utf-8") as f:
                _DOMAINS = json.load(f)
            seed_entries: list[tuple[re.Pattern, str, int]] = []
            for dom, industries in _DOMAINS.items():
                for ind in industries:
                    norm_ind = ind.strip().casefold()
                    if not norm_ind:
                        continue
                    _DOMAIN_MAP[norm_ind] = dom
                    seed_entries.append((
                        re.compile(r"(?<!\w)" + re.escape(norm_ind) + r"(?!\w)", re.I),
                        dom,
                        len(norm_ind),
                    ))
            # Match longer/more specific industry labels before short tokens like "AI" or "Tech"
            seed_entries.sort(key=lambda item: item[2], reverse=True)
            _DOMAIN_SEED_REGEXES = seed_entries
        except Exception as e:
            logger.warning("Could not load domains.json: %s", e)

    _JD_DOMAIN_REGEXES = [
        (
            dom,
            re.compile(r"\b(?:" + "|".join(re.escape(c) for c in sorted(cues, key=len, reverse=True)) + r")\b", re.I),
        )
        for dom, cues in _JD_DOMAIN_CUES.items()
    ]

    # 3. Load skills + build chunked fast-prefilter regex buckets
    skill_file = _find_data_file("skills_dictionary.csv")
    if skill_file.exists():
        ci_items: list[tuple[str, dict]] = []
        cs_items: list[dict] = []
        soft_set: set[str] = set()
        try:
            with open(skill_file, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    grp = row.get("group", "General")
                    if grp in ("Location Targeting", "Job Portal / Source Targeting", "Work Arrangement & Mobility"):
                        continue
                    is_cs = row.get("case_sensitive", "").strip().lower() == "true"
                    flags = 0 if is_cs else re.I
                    raw_pat = row["pattern"]
                    try:
                        pat = re.compile(raw_pat, flags)
                    except Exception:
                        continue
                    entry = {
                        "term": row["term"],
                        "group": grp,
                        "regex": pat,
                    }
                    _SKILLS.append(entry)
                    if grp in _SOFT_SKILL_GROUPS:
                        soft_set.add(entry["term"])
                    if is_cs:
                        cs_items.append(entry)
                    else:
                        ci_items.append((raw_pat, entry))
        except Exception as e:
            logger.warning("Could not load skills_dictionary.csv: %s", e)

        _SOFT_SKILL_TERMS = soft_set
        _CS_SKILLS = cs_items
        _CI_BUCKETS = []  # compatibility diagnostic; giant regex buckets retired


def get_soft_skill_terms() -> set[str]:
    """Return cached set of soft/professional skill canonical names."""
    _load_data()
    return _SOFT_SKILL_TERMS


def classify_category(title: str, text: str = "") -> str:
    """Classify a job's functional category from title + full JD text."""
    _load_data()
    clean_title = (title or "").strip()

    if clean_title:
        best_cat = ""
        best_score = 0
        for cat, reg in _CATEGORY_REGEXES:
            if not reg.search(clean_title):
                continue
            score = 0
            for kw_re, weight in _CATEGORY_KEYWORD_RE.get(cat, ()):
                if kw_re.search(clean_title):
                    score += weight
            if score > best_score:
                best_score = score
                best_cat = cat
        if best_cat:
            return best_cat

    clean_text = (text or "").strip()
    if clean_text:
        lead_text = clean_text[:900]
        body_text = clean_text[:12000]
        best_cat = ""
        best_score = 0
        for cat, reg in _CATEGORY_REGEXES:
            if not reg.search(body_text):
                continue
            score = 0
            for kw_re, weight in _CATEGORY_KEYWORD_RE.get(cat, ()):
                hits = len(kw_re.findall(body_text))
                if hits:
                    lead_bonus = 2 if kw_re.search(lead_text) else 0
                    score += min(hits, 3) * weight + lead_bonus
            if score > best_score:
                best_score = score
                best_cat = cat
        if best_cat and best_score >= 4:
            return best_cat

    return "Other"


def classify_domain(industry: str, title: str = "", text: str = "", company: str = "") -> str:
    """Map seed industry or full-JD context to an Employer Domain."""
    _load_data()
    raw_ind = (industry or "").strip()
    if raw_ind and raw_ind.lower() != "unknown":
        norm = raw_ind.casefold()
        if norm in _DOMAIN_MAP:
            return _DOMAIN_MAP[norm]
        for rx, dom, _ in _DOMAIN_SEED_REGEXES:
            if rx.search(raw_ind):
                return dom

    combined = f"{title or ''}\n{(text or '')[:10000]}".strip()
    if combined:
        best_dom = ""
        best_hits = 0
        for dom, rx in _JD_DOMAIN_REGEXES:
            matches = rx.findall(combined)
            if not matches:
                continue
            # Require >=2 domain cue occurrences or a strong multi-word phrase
            distinct = {m.casefold() for m in matches}
            score = len(matches) + len(distinct)
            if score > best_hits:
                best_hits = score
                best_dom = dom
        if best_dom and best_hits >= 3:
            return best_dom

    return "Unknown"


def _iter_matched_skills(text: str):
    """Lossless indexed candidates followed by the original exact regexes."""
    for index in _candidate_skills(text):
        item = _SKILLS[index]
        if item['regex'].search(text):
            yield item


def extract_skills(text: str) -> list[str]:
    """Extract matching technical and soft skills from JD text."""
    _load_data()
    if not text:
        return []
    seen: set[str] = set()
    found: list[str] = []
    for item in _iter_matched_skills(text):
        term_name = item["term"]
        if term_name not in seen:
            seen.add(term_name)
            found.append(term_name)
    return found


def is_soft_skill(group_name: str, term_name: str = "") -> bool:
    return group_name in _SOFT_SKILL_GROUPS


def extract_terms(
    title: str,
    location: str,
    experience_level: str,
    text: str,
    industry: str,
    company: str = "",
) -> dict:
    """Extract category, domain, and skill/metadata terms from SQLite job fields."""
    _load_data()
    category = classify_category(title, text)
    domain = classify_domain(industry, title=title, text=text, company=company)
    combined = f"{title or ''}\n{text or ''}".strip()

    terms: list[tuple[str, str]] = []
    seen_skills: set[str] = set()
    if combined:
        for item in _iter_matched_skills(combined):
            term_name = item["term"]
            if term_name in seen_skills:
                continue
            seen_skills.add(term_name)
            grp = item["group"]
            kind = "soft_skill" if grp in _SOFT_SKILL_GROUPS else "technical_skill"
            terms.append(("skill", term_name))
            terms.append((kind, term_name))

    if location and location.lower() not in ("unknown", "not specified"):
        terms.append(("location", location.strip()))
    if experience_level and experience_level.lower() not in ("unknown", "?", ""):
        terms.append(("experience", experience_level.strip()))

    return {
        "category": category,
        "domain": domain,
        "terms": terms,
    }


extract_skills_from_text = extract_skills
