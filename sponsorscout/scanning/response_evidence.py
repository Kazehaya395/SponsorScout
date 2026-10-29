"""Transport-independent evidence for active access walls and listing coverage.

Pure inspection only: no I/O, probes, hostname presets or browser launch. Passive
SDK/apply-form CAPTCHA markup is not evidence that a job page is blocked.
"""
from __future__ import annotations
import re,json
from html import unescape


def visible_text(html):
    clean=re.sub(r'(?is)<(script|style|noscript)\b[^>]*>.*?</\1>',' ',str(html or ''))
    clean=re.sub(r'(?s)<!--.*?-->',' ',clean)
    return ' '.join(unescape(re.sub(r'<[^>]+>',' ',clean)).split())


def challenge_reason(html='',*,title='',body='',frame_urls=(),status=0,subject='listing'):
    """Positive access-wall evidence, independent of recommended job links.

    A positive title/foreground wall outranks links/counts. SDK tokens alone do
    not. Status503 alone is a service failure, not proof of human verification.

    `subject` names the thing the response replaced. The same detector runs on
    listing pages and on individual job-detail (JD) pages, and a JD wall used
    to be reported as "no listing could be read" -- which made a portal whose
    listing worked fine look like a blocked portal and pushed it into the
    Assisted queue. Only `subject='listing'` may claim the listing is
    unreadable; a verified 401/403/429 with no challenge evidence keeps saying
    "human verification not confirmed" so callers can tell an access error from
    a real wall.
    """
    html=str(html or '')
    clean=re.sub(r'(?is)<(script|style|noscript)\b[^>]*>.*?</\1>',' ',html)
    clean=re.sub(r'(?s)<!--.*?-->',' ',clean)
    if not title:
        m=re.search(r'(?is)<title[^>]*>(.*?)</title>',clean)
        title=visible_text(m[1]) if m else ''
    heading=re.search(r'(?is)<h[12]\b[^>]*>(.*?)</h[12]>',clean)
    heading=visible_text(heading[1]) if heading else ''
    text=' '.join(str(body or visible_text(clean)).split())
    low=text.casefold();title=' '.join(str(title).split()).casefold()
    primary=re.split(r'\s*[|]\s*',title,maxsplit=1)[0].strip()
    interstitial=bool(re.fullmatch(
        r'(?:just a moment\.{0,3}|even geduld\.{0,3}|attention required!?|'
        r'access denied|403 forbidden|human verification|robot check|security check|'
        r'pardon our interruption|checking your browser\.{0,3}|(?:please )?verify (?:that )?you are (?:a )?human)',primary))
    interstitial=interstitial or 'access to this page has been denied' in title
    prompt=re.compile(r'(?i)(?:verify (?:that )?you (?:are|are not) (?:a )?(?:human|robot)|'
        r'confirm (?:that )?you are (?:a )?human|complete (?:the |this )?(?:security check|captcha)|'
        r'press\s+(?:&|and)\s+hold|unusual traffic|checking (?:your|the) browser|automated (?:access|requests) (?:is |are )?(?:blocked|denied))')
    active_id=bool(re.search(r'''(?i)\bid\s*=\s*["'](?:px-captcha|cf-challenge-running|challenge-running)["']''',clean))
    frames=' '.join(str(u or '')for u in frame_urls).casefold()
    iframe_sources=' '.join(re.findall(r'''(?is)<iframe\b[^>]*\bsrc\s*=\s*["']([^"']+)''',clean)).casefold()
    widgets=frames+' '+iframe_sources+' '+clean.casefold()
    challenge_frame=any(s in frames+' '+iframe_sources for s in
        ('challenges.cloudflare.com','captcha-delivery.com','perimeterx.net','px-cloud.net','/_incapsula_resource'))
    captcha=any(s in widgets for s in ('g-recaptcha','h-captcha','frc-captcha','cf-turnstile','google.com/recaptcha','hcaptcha.com'))
    foreground=any(prompt.search(visible_text(fragment)) for fragment in re.findall(
        r'''(?is)<(?:div|section|dialog)\b[^>]*(?:aria-modal\s*=\s*["']true|position\s*:\s*fixed)[^>]*>(.{0,6000}?)</(?:div|section|dialog)>''',clean))
    json_posting=False;json_wall=False
    if html.lstrip().startswith('{'):
        try:
            data=json.loads(html)
            candidate=data.get('jobPostingInfo',data) if isinstance(data,dict)else {}
            if isinstance(candidate,dict):
                json_posting=any(len(str(candidate.get(key)or''))>=200 for key in ('jobDescription','job_description','description')) and bool(candidate.get('title')or candidate.get('jobTitle')or data.get('jobPostingInfo'))
            if isinstance(data,dict):
                json_wall=any(re.search(r'(?i)captcha[_ -]?(?:required|challenge)|(?:human|security)[_ -]?verification[_ -]?required|access[_ -]?denied',str(data.get(key)or'')) for key in ('error','errorCode','message'))
        except (TypeError,ValueError):pass
    real_posting=bool(json_posting or re.search(r'(?i)[\"\']JobPosting[\"\']',html) or
        (heading and len(text)>250 and re.search(r'(?i)\b(responsibilities|qualifications|requirements|job description)\b',text)))
    active_prompt=bool(prompt.search(heading) or (prompt.search(low) and
        (foreground or (len(text)<1500 and not real_posting))))
    # HTTP 200 JS interstitials need not contain a visible CAPTCHA or a
    # vendor token. This exact primary title + non-posting shell is positive
    # access evidence, not a hostname rule or a passive SDK match.
    client_shell=(bool(re.fullmatch(r'(?:client|browser|javascript) challenge[.!]?',primary))
                  and not real_posting and len(text)<1000
                  and not (heading and not re.search(r'(?i)challenge|verif|checking',heading)
                           and len(text)>=250))
    wall=json_wall or interstitial or active_id or client_shell or (active_prompt and (captcha or challenge_frame or re.search(r'(?i)press\s+(?:&|and)\s+hold|using automation tools',text))) or (challenge_frame and len(text)<160 and not real_posting)
    try:status=int(status or 0)
    except (TypeError,ValueError):status=0
    if not wall:
        if status in (401,403,429):
            return f'HTTP {status} access blocked (human verification not confirmed)'
        if status==503 and len(text)<1200 and re.search(r'(?i)\b(captcha|verify|bot protection|firewall|waf|access denied)\b',text):
            return 'HTTP 503 access wall (verification evidence in response)'
        return ''
    if client_shell and not (json_wall or interstitial or active_id or active_prompt or challenge_frame):
        return 'Client challenge interstitial (human verification not confirmed)'
    evidence=(title+' '+widgets).casefold()
    if any(s in evidence for s in ('cloudflare','cf-challenge','cf-turnstile','just a moment','even geduld')):vendor='Cloudflare'
    elif 'px-captcha' in evidence or 'perimeterx' in evidence or 'px-cloud.net' in evidence:vendor='PerimeterX'
    elif 'captcha-delivery.com' in evidence or 'datadome' in evidence:vendor='DataDome'
    elif 'incapsula' in evidence:vendor='Imperva'
    elif any(s in evidence for s in ('akamai','_abck','bm_sv','edgesuite','aka_sis')):vendor='Akamai'
    elif not re.search(r'(?i)human|captcha|robot|press\s+(?:&|and)\s+hold|checking (?:your|the) browser',title+' '+low):
        return f'HTTP {status} access blocked (human verification not confirmed)' if status else 'Access blocked: access-denied wall (human verification not confirmed)'
    else:vendor='Human verification'
    if str(subject or '').casefold()=='detail':
        return f'{vendor} challenge page served instead of the job detail text; no job description could be read'
    return f'{vendor} challenge page served instead of the job list; no listing could be read'


def response_metadata(html='',*,status=0,transport='HTTP'):
    match=re.search(r'(?is)<title[^>]*>(.*?)</title>',str(html or ''))
    return {'status':int(status or 0),'transport':transport,'title':visible_text(match[1])[:180]if match else ''}


class BlockedResponseError(RuntimeError):
    def __init__(self,reason,**evidence):
        self.response_evidence=evidence
        self.block_reason=str(reason)
        super().__init__('blocked access: '+self.block_reason)


UNCONFIRMED_VERIFICATION = 'human verification not confirmed'


def is_confirmed_wall(reason) -> bool:
    """True when `reason` names an actual verification wall.

    A bare 401/403/429 is an access error: the response carried no challenge
    evidence, so the app must not present it as a bot wall that a human has to
    clear. `challenge_reason` marks those verdicts explicitly, and this is the
    single place that reads the marker.
    """
    value = str(reason or '').casefold()
    return bool(value.strip()) and UNCONFIRMED_VERIFICATION not in value


def listing_block_message(reason) -> str:
    """The message a LISTING-level failure reports, with the right severity.

    A verified challenge keeps the `blocked by bot protection:` marker the
    Assisted queue keys on. A bare HTTP 401/403/429/503 on the listing URL is a
    genuine failure to read the board, but it is not a human-verification wall,
    so it is reported as `listing access blocked:` and names the missing
    evidence instead of blaming a bot detector.
    """
    reason = str(reason or '').strip()
    if is_confirmed_wall(reason):
        return 'blocked by bot protection: ' + reason
    return ('listing access blocked: '
            + reason.replace('access blocked (human verification not confirmed)',
                             'access blocked (no verification challenge observed)'))


def http_error_reason(exc, *, subject='listing'):
    """Inspect an already received HTTP error; never performs another request."""
    if getattr(exc,'block_reason',''):
        return str(exc.block_reason)
    code=getattr(exc,'code',0)
    html=''
    if code and callable(getattr(exc,'read',None)):
        try:html=exc.read(16000).decode('utf-8','replace')
        except Exception:pass
    try:exc.response_evidence=response_metadata(html,status=code)
    except Exception:pass
    return challenge_reason(html,status=code,subject=subject)


class ListingDiagnostic(str):
    """String-compatible legacy API, with structured continuation evidence."""
    def __new__(cls,text,*,needs_dom=False,block_reason='',complete=None,expected_total=None):
        obj=super().__new__(cls,text)
        obj.needs_dom=bool(needs_dom);obj.block_reason=str(block_reason or '');obj.complete=complete;obj.expected_total=expected_total
        return obj


def listing_continuation(html,observed):
    """Positive dynamic-pagination evidence; not arbitrary numbers in scripts."""
    html=str(html or '');text=visible_text(html)
    total=stated_job_total(html)
    if total is not None and total>observed:
        return f'stated job total {total} exceeds {observed} observed'
    for m in re.finditer(r'(?i)(?:showing|displaying|results?|jobs?|vacancies)\s*[: ]*\d+\s*[-–]\s*\d+\s+(?:of|out of)\s+(\d+)',text):
        if int(m[1])>observed:return f'stated result total {m[1]} exceeds {observed} observed'
    for m in re.finditer(r'(?i)\b(\d+)\s+(?:jobs?|vacancies|vacatures|open positions|openings)\b',text):
        if int(m[1])>observed:return f'stated job total {m[1]} exceeds {observed} observed'
    for m in re.finditer(r'(?is)<(?:button|a)\b([^>]*)>(.*?)</(?:button|a)>',html):
        attrs=m[1];label=visible_text(m[2]).casefold()
        if re.search(r'(?i)\bdisabled\b|aria-disabled\s*=\s*["\']true',attrs):continue
        if re.fullmatch(r'(?i)(?:show|load|see|view)\s+(?:more|all)(?:\s+(?:jobs?|results?|vacancies))?(?:\s*\(\d+\))?|next(?: page)?|volgende|mostra (?:ancora|altro|altri)|carica (?:altro|altri)',label):
            return 'enabled pagination/load-more control requires DOM continuation'
    return ''


def stated_job_total(html):
    text=visible_text(html)
    number=r'(?:\d{1,3}(?:[,. \u00a0]\d{3})+|\d+)'
    match=re.search(r'(?i)(?:showing|displaying|results?|jobs?|vacancies)\s*[: ]*'+number+r'\s*[-–]\s*'+number+r'\s+(?:of|out of)\s+('+number+r')',text)
    if not match:
        match=re.search(r'(?i)\b('+number+r')\s+(?:jobs?|vacancies|vacatures|open positions|openings)\b',text)
    if match:return int(re.sub(r'[^0-9]','',match[1]))
    match=re.search(r"""(?i)\bdata-total-(?:jobs|results|count)\s*=\s*["'](\d+)""",str(html or ''))
    return int(match[1]) if match else None
