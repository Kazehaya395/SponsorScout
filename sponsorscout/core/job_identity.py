"""Conservative posting identity and source-preserving CSV finalization.

No hostname rules. A numeric token anywhere in a URL is NOT a job identity:
query requisitions, tenants, fragments and scoped boards stay distinct.
"""
from __future__ import annotations

import csv
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from sponsorscout.core.url_normalizer import normalize_url

# Two observed generic endpoint forms for the SAME explicitly numbered job.
# The board/locale/tenant prefix is retained; arbitrary last-number heuristics
# would merge different requisitions sharing a site ID or postcode.
_NUMBERED_JOB = re.compile(r'^(.*?)/(?:job-search/)?jobs/(?:[^/]+-)?(\d{6,})/?$', re.I)
_METADATA_TITLE = re.compile(
    r'^(?:permanent|full[- ]time|part[- ]time|contract|temporary|'
    r'go to job site|view job|view details|apply(?: now)?|read more|learn more)$', re.I)
_UNKNOWN = {'', 'unknown', 'not specified', 'n/a', '?', 'none'}


def merge_sources(*values) -> str:
    seen, result = set(), []
    for value in values:
        for name in str(value or '').split(';'):
            name = name.strip()
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                result.append(name)
    return '; '.join(result)


def portal_queue_key(url: str) -> str:
    """Only normalize scheme/host: preserve path, query order, filters and hash."""
    try:
        p = urlsplit(str(url or '').strip())
        if p.scheme.lower() not in ('http', 'https') or not p.hostname or p.username or p.password:
            return ''
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', p.query, p.fragment))
    except ValueError:
        return ''


def posting_identity(url: str) -> str:
    normalized = normalize_url(str(url or ''))
    if not normalized:
        return ''
    p = urlsplit(normalized)
    # Any hash may select different jobs. Keep it (and its case) intact.
    if p.fragment:
        return normalized
    m = _NUMBERED_JOB.fullmatch(p.path)
    # Explicit query identifiers/filters are not erased in the alias rescue.
    if m and not p.query:
        leaf = p.path.rstrip('/').rsplit('/', 1)[-1]
        slug = leaf[:-len(m[2])].rstrip('-')
        # Multiple requisition/routing numbers (or a date + another number)
        # make the trailing token ambiguous. A shared postcode/site number
        # must NEVER collapse distinct explicit requisitions.
        if re.search(r'\d{4,}', slug):
            return normalized
        return f'{p.scheme.lower()}://{p.netloc.lower()}{m[1]}/jobs/@id:{m[2]}'
    return normalized


def metadata_title(title: str) -> bool:
    return bool(_METADATA_TITLE.fullmatch(str(title or '').strip()))


def weak_title(title: str) -> bool:
    """Relative evidence quality ONLY, never an acceptance/identity rule.

    Long first/second-person recruiting sentences should not overwrite an
    actual role heading on the same trusted posting. Keep their fuller JD.
    """
    text=str(title or '').strip()
    if metadata_title(text) or not text:
        return True
    return (len(re.findall(r'\b\w+\b',text))>=8
            and bool(re.search(r'\b(?:we|our|your|you)\b',text,re.I))
            and bool(re.search(r'\b(?:join|grow|build|lead|manage|looking|seeking|hiring|offer)\b',text,re.I)))


_EVERGREEN_TITLE = re.compile(
    r'(?:\b(?:general|open|speculative|proactive|spontaneous|unsolicited)\s+application\b|'
    r'\btalent\s+(?:pool|community|network)\b|\bcandidate database\b|'
    r'\bcandidatur[ae]\s+spontan(?:e[aeé]|é[e]?)\b|'
    r'\binitiativbewerbung\b|\bopen sollicitatie\b|\bspontane sollicitatie\b)',re.I)
POSTING_CONTEXT_PATTERN = (
    r'^(?:jobs?|job-?details?|find-job|(?:open[-_])?positions?|vacanc(?:y|ies)|'
    r'requisitions?|postings?|openings?|job[-_](?:openings?|offers?)|'
    r'open[-_](?:roles?|vacanc(?:y|ies)))$')
_POSTING_SEGMENT = re.compile(POSTING_CONTEXT_PATTERN,re.I)
_OPAQUE_POSTING_ID = re.compile(r'^(?:[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}|[0-9a-f]{16,64})$',re.I)


def evergreen_title(title: str) -> bool:
    return bool(_EVERGREEN_TITLE.search(str(title or '')))


def explicit_detail_url(url: str) -> bool:
    """Acceptance evidence, not a dedupe key. Never an arbitrary site number."""
    try:
        parts = urlsplit(url)
        segments = [part for part in parts.path.split('/') if part]
        has_context = any(_POSTING_SEGMENT.fullmatch(part) for part in segments[:-1])
        identifiers = {'jobid','job_id','gh_jid','reqid','requisitionid','career_job_req_id','postingid','vacancyid'}
        if any(value.strip() and (key.lower() in identifiers or
               (key.lower() in {'id','jid'} and has_context)) for key,value in parse_qsl(parts.query)):
            return True
        if not has_context:
            return False
        # A numeric-prefix/title.html or title-numeric.html LEAF in posting
        # context is acceptance evidence, not a dedupe/canonicalization rule.
        leaf = segments[-1]
        if re.fullmatch(r'(?:[A-Za-z]{0,3}[-_]?)?\d{4,}[-_][^/]+\.html?|[^/]+[-_]\d{4,}\.html?', leaf, re.I):
            return True
        # A context-bound UUID/hash OR explicit numbered requisition slug.
        return any(_OPAQUE_POSTING_ID.fullmatch(part) for part in segments[1:]) or bool(
            re.search(r'(?:[-_/])(?:[A-Za-z]{0,3}[-_]?)?\d{4,}(?:/|$)',parts.path))
    except ValueError:
        return False


_ADMIN_ACTION_PATH = re.compile(
    r'(?i)(?:^|/)(?:saved[-_]search(?:es)?|save[-_]search|create[-_]alert|'
    r'job[-_]alerts?|alert[-_]subscription|saved[-_]jobs|save[-_]job|'
    r'newsletter|subscribe|register[-_]cv|submit[-_]cv|salary[-_]calculator)(?:/|$|\.)')
_ACTION_TITLE = re.compile(
    r'(?i)^(?:create(?: a)? job alerts?|save (?:this )?search|subscribe(?: to (?:job )?alerts)?|'
    r'maak (?:een )?vacaturemelding aan|stel (?:een )?vacaturealert in|'
    r'cr[ée]er une alerte(?: emploi)?|crear (?:una )?alerta(?: de empleo)?|'
    r'crea (?:un )?avviso(?: di lavoro)?|jobalarm erstellen)[.! ]*$')


def administrative_action(url, title='') -> bool:
    """Subscription/account furniture, not a role. Positive job evidence wins.

    No hostname/locale presets; a genuine role can contain `legal`, `about`,
    `saved-search` or `job-alert` in its title/path. IDs are never deduped here.
    """
    if explicit_detail_url(url) or evergreen_title(title):
        return False
    try:
        return bool(_ADMIN_ACTION_PATH.search(urlsplit(str(url or '')).path) or _ACTION_TITLE.fullmatch(str(title or '').strip()))
    except ValueError:
        return False


def posting_candidate(url, title='', validator=None) -> bool:
    """Common card/anchor acceptance BEFORE observation and page accounting."""
    if not str(url or '').startswith(('https://','http://')):
        return False
    if re.search(r'(?i)\.(?:pdf|png|jpe?g|gif|svg|css|js|zip)(?:$|[?#])', str(url)):
        return False
    if administrative_action(url, title):
        return False
    if explicit_detail_url(url) or evergreen_title(title):
        return True
    if callable(validator):
        return bool(validator(url))
    # Lightweight adapters must still carry a detail context, not an arbitrary
    # anchor merely because an outer search-results wrapper looked like a card.
    try:
        parts=[x for x in urlsplit(url).path.split('/') if x]
        return len(parts)>1 and any(_POSTING_SEGMENT.fullmatch(x) for x in parts[:-1])
    except ValueError:
        return False


def record_quality(row) -> tuple:
    """Prefer actual employer detail records over CTA/contract/aggregator copies."""
    title = row.get('Job Title') or row.get('title') or ''
    source = row.get('Source Type') or row.get('source_subtype') or ''
    location = row.get('Job Location') or row.get('location') or ''
    evidence = row.get('Support Evidence') or row.get('support_evidence') or ''
    description = row.get('Raw Description') or row.get('description') or ''
    return (not weak_title(title), source in ('direct_employer', 'direct'),
            bool(evidence), str(location).casefold() not in _UNKNOWN, len(description), len(str(title)))


def merge_job_rows(rows, quality=None):
    """Return (kept, removed_by_seed). Only explicit identity, never title alone."""
    groups, order = {}, []
    for row in rows:
        row = dict(row)
        url = row.get('Job URL') or row.get('url') or ''
        key = posting_identity(url)
        if not key:
            key = f'__unkeyed__:{len(order)}'
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    kept, removed = [], Counter()
    for key in order:
        group = groups[key]
        best = max(group, key=lambda r: (record_quality(r), quality(r) if quality else 0))
        merged = dict(best)
        # Preserve encounter order, not whichever thread happened to win quality.
        source_field = 'Seed Name' if any('Seed Name' in r for r in group) else 'source_name'
        merged[source_field] = merge_sources(*(r.get(source_field, '') for r in group))
        for row in group:
            if row is not best:
                removed[row.get('Seed Name') or row.get('source_name') or row.get('Company Name') or ''] += 1
            for field, value in row.items():
                if str(merged.get(field) or '').strip().casefold() in _UNKNOWN and str(value or '').strip().casefold() not in _UNKNOWN:
                    merged[field] = value
            # A shorter refresh never throws away a full description.
            for field in ('Raw Description', 'description'):
                if len(str(row.get(field) or '')) > len(str(merged.get(field) or '')):
                    merged[field] = row[field]
        if 'Record Status' in merged:
            merged['Record Status'] = 'accepted'
            if 'Quarantine Reason' in merged:
                merged['Quarantine Reason'] = ''
        kept.append(merged)
    return kept, dict(removed)


def read_job_csv(path):
    try:
        with Path(path).open(encoding='utf-8-sig', newline='') as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def write_job_csv(path, rows, columns):
    """Atomic BOM CSV replacement, preserving the previous file on failure."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def finalize_job_csvs(main_csv, recruiter_csv, columns, quality=None):
    """One main file, all source types. Merges are NEVER sent to quarantine."""
    rows = read_job_csv(main_csv) + read_job_csv(recruiter_csv)
    kept, removed = merge_job_rows(rows, quality=quality)
    write_job_csv(main_csv, kept, columns)
    # The separate file was only an in-flight spool, not a second deliverable.
    Path(recruiter_csv).unlink(missing_ok=True)
    return {'written': len(kept), 'merged': len(rows) - len(kept), 'by_seed': removed}
