"""Run-local JD/country enrichment in the explicitly opened Assisted session.

API first, at most one reused detail tab in the SAME browser context. Active
walls are never job prose; clearance is explicit/human, not an automated bypass.
Shared request/time/browser budgets, bounded caches/text, no startup or geocoder.
"""
from __future__ import annotations
from collections import Counter,OrderedDict
from dataclasses import dataclass
from html.parser import HTMLParser
import json,re,time,hashlib
from datetime import datetime,timezone
from urllib.parse import urljoin,urlsplit
from sponsorscout.i18n import _
from sponsorscout.core.job_identity import posting_identity,posting_candidate,administrative_action
from sponsorscout.core.country_details import structured_job_country
from sponsorscout.scanning.jd_support import normalize_jd_text
from sponsorscout.scanning.response_evidence import challenge_reason,response_metadata,listing_block_message
from sponsorscout.core.browser_evidence import inspect_page,public_snapshot,redact_url,redact_text,watch_page_responses

FLOW_REVISION='20261027-assisted-parallel-reprobe-v1'
MAX_JD_CHARS=60000


@dataclass
class DetailResult:
    text:str=''
    country:str=''
    country_source:str=''
    location:str=''
    hiring_org:str=''
    status:str='empty'
    method:str=''
    reason:str=''
    http_status:int=0
    title:str=''
    transport:str=''
    truncated:bool=False
    reused:bool=False
    captured_at:str=''
    requested_url:str=''
    final_url:str=''
    phase:str=''
    error:str=''
    http_reason:str=''
    http_title:str=''
    http_code:int=0
    http_final_url:str=''
    http_phase:str=''
    http_error:str=''
    browser_evidence:dict|None=None


class _Body(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack=[];self.body=[];self.main=[];self.specific=[];self.h1=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs);parent=self.stack[-1][1:]if self.stack else(False,False,False,False)
        skip,main,specific,heading=parent
        label=' '.join((attrs.get('id',''),attrs.get('class',''),attrs.get('data-automation-id','')))
        skip=skip or tag in('script','style','noscript','nav','footer','header','aside','form')or bool(re.search(r'(?i)cookie|consent|breadcrumb|related[-_ ]|recommend',label))
        main=main or tag in('main','article')
        specific=specific or attrs.get('itemprop')=='description'or bool(re.search(r'(?i)job[-_ ]?description|vacancy[-_ ]?description|posting[-_ ]?content|jobPostingDescription',label))
        heading=heading or tag=='h1'
        if tag not in('br','hr','img','input','meta','link','source','wbr','area','base','embed'):
            self.stack.append((tag,skip,main,specific,heading))
        if tag in('br','p','div','li','h1','h2','h3','section','article')and not skip:self.body.append('\n');self.main.append('\n');self.specific.append('\n')
    def handle_endtag(self,tag):
        for n in range(len(self.stack)-1,-1,-1):
            if self.stack[n][0]==tag:self.stack=self.stack[:n];break
    def handle_data(self,value):
        skip,main,specific,heading=self.stack[-1][1:]if self.stack else(False,False,False,False)
        if skip:return
        self.body.append(value)
        if main:self.main.append(value)
        if specific:self.specific.append(value)
        if heading:self.h1.append(value)


def _nodes(value):
    if isinstance(value,list):
        for child in value[:64]:yield from _nodes(child)
    elif isinstance(value,dict):
        if value.get('@graph'):yield from _nodes(value['@graph'])
        kind=value.get('@type')
        if kind=='JobPosting'or isinstance(kind,list)and'JobPosting'in kind:yield value


def _page_content_stable(page):
    """Read current DOM content, absorbing a transient navigation race.

    Playwright can raise ``Unable to retrieve content because the page is
    navigating and changing the content`` just after ``goto``.  That is not a
    bot wall and must not start a 180-second human-verification wait.
    """
    last=None
    for attempt in range(4):
        try:
            return page.content()or''
        except Exception as exc:
            last=exc
            if 'Unable to retrieve content because the page is navigating' not in str(exc):
                raise
            if attempt<3:time.sleep(.1*(attempt+1))
    raise last


def parse_detail(text,url,final_url,scanner,title='',browser_evidence=None):
    """Select the primary JobPosting, never recommended jobs/employer prose."""
    result=DetailResult(title=title)
    if browser_evidence and browser_evidence.get('listing_ready')and not browser_evidence.get('detail_ready'):
        result.status='empty';result.reason='current rendered document is a listing, not the requested primary JD';return result
    nodes=[]
    for payload in re.findall(r'''(?is)<script\b[^>]*type=["']application/ld\+json["'][^>]*>(.*?)</script>''',text):
        try:nodes.extend(_nodes(json.loads(payload)))
        except (ValueError,TypeError):pass
    data=None
    if text.lstrip().startswith(('{','[')):
        try:data=json.loads(text);nodes.extend(_nodes(data))
        except (ValueError,TypeError):pass
    candidates=[]
    for node in nodes:
        target=node.get('url')or node.get('@id')or''
        if isinstance(target,str)and target:
            absolute=urljoin(final_url or url,target)
            if posting_identity(absolute)in {posting_identity(url),posting_identity(final_url)}:candidates.append(node)
    primary=candidates[0]if len(candidates)==1 else nodes[0]if len(nodes)==1 else None
    if len(nodes)==1 and not candidates:
        address=nodes[0].get('url')or nodes[0].get('@id')or''
        if isinstance(address,str)and(address.startswith(('http://','https://','/'))):
            result.status='empty';result.reason='JobPosting belongs to a different posting; primary identity unproven';return result
    description=''
    if primary is not None:
        description=primary.get('description')or''
        if isinstance(description,dict):description=description.get('text')or''
        if isinstance(description,list):description=' '.join(str(x)for x in description)
        result.country=structured_job_country(json.dumps(primary),getattr(scanner,'_ISO3_TO_COUNTRY',{}))
        try:
            _desc,result.location,result.hiring_org=scanner._parse_detail_html('<script type="application/ld+json">'+json.dumps(primary)+'</script>')
        except Exception:pass
        result.method='jobposting_jsonld'
    elif isinstance(data,dict):
        info=data.get('jobPostingInfo')
        if isinstance(info,dict):
            description=info.get('jobDescription')or info.get('description')or''
            result.location=str(info.get('location')or'')
            result.method='provider_job_description'
    description=normalize_jd_text(str(description))
    if browser_evidence and browser_evidence.get('detail_ready') and not (len(nodes)>1 and primary is None):
        rendered=normalize_jd_text(str(browser_evidence.get('primary_text')or''))
        if len(rendered)>=200:
            result.text=rendered[:MAX_JD_CHARS];result.truncated=bool(browser_evidence.get('primary_truncated'))or len(rendered)>MAX_JD_CHARS
            result.method='rendered_visible_primary'+(' + jobposting_jsonld'if primary is not None else'')
            result.status='truncated'if result.truncated else'readable';return result
    if len(description)>=200:
        # Some sites publish a short JSON-LD preview but expose a longer
        # primary description container. Prefer that semantic container, not
        # arbitrary whole-page marketing/recommended-job text.
        body_candidate=_Body()
        try:body_candidate.feed(text)
        except Exception:pass
        specific=normalize_jd_text(' '.join(body_candidate.specific))
        if len(specific)>len(description):
            description=specific;result.method+=' + description_container'
        result.text=description[:MAX_JD_CHARS];result.truncated=len(description)>MAX_JD_CHARS
        result.status='truncated'if result.truncated else'readable';return result
    if len(nodes)>1 and primary is None:
        result.status='empty';result.reason='multiple unmatched JobPosting objects; primary JD unproven';return result
    if final_url and administrative_action(final_url):
        result.status='empty';result.reason='detail redirected to administrative action';return result
    body=_Body()
    try:body.feed(text)
    except Exception:pass
    heading=normalize_jd_text(' '.join(body.h1))
    hrefs=set(re.findall(r'''(?i)href\s*=\s*["']([^"']+)''',text))
    detail_links=[h for h in hrefs if re.search(r'/(?:jobs?|vacancies|vacatures?)/[^/?#]+',h,re.I)]
    if len(detail_links)>=3 and re.search(r'(?i)search|results|vacancies|vacatures|jobs|careers',heading):
        result.status='empty';result.reason='listing document returned for a detail URL';return result
    specific=normalize_jd_text(' '.join(body.specific))
    main=normalize_jd_text(' '.join(body.main))
    fallback=normalize_jd_text(' '.join(body.body))
    candidate=specific if len(specific)>=200 else main if len(main)>=scanner.config.DETAIL_BODY_MIN_CHARS else fallback
    # Specific description containers are semantic evidence. Generic whole-body
    # fallback must be substantial AND have a non-listing primary role heading.
    role_heading=bool(heading and not re.search(r'(?i)^(?:search|job results|vacancies|vacatures|careers|sign in|log in|privacy)',heading))
    if (len(specific)>=200 or len(candidate)>=scanner.config.DETAIL_BODY_MIN_CHARS and role_heading):
        result.text=candidate[:MAX_JD_CHARS];result.truncated=len(candidate)>MAX_JD_CHARS
        result.method='description_container'if len(specific)>=200 else'primary_job_body'
        result.status='truncated'if result.truncated else'readable'
    else:
        result.text=description[:MAX_JD_CHARS]
        result.status='thin'if result.text else'empty';result.reason='no substantial primary JD text'
    return result


class _AssistedBudget:
    """Only explicit Assisted reads use browser as a primary transport.

    Delegate all shared count/time limits; never mutate regular scanner config.
    Browser visits still reserve real requests, including a bounded retry.
    """
    def __init__(self,config):self.base=config
    def __getattr__(self,name):
        if name=='DETAIL_BROWSER_FALLBACK_PER_COMPANY':
            return max(0,int(self.base.MAX_DETAIL_SCAN_PER_COMPANY))
        return getattr(self.base,name)


class SessionDetailEnricher:
    def __init__(self,context,scanner,scope,*,conn=None,cancel_event=None,progress=None,clear_wait_s=180,control=None):
        self.context,self.scanner,self.scope=context,scanner,scope
        self.conn,self.cancel,self.progress,self.clear_wait=conn,cancel_event,progress,clear_wait_s
        self.policy=_AssistedBudget(scanner.config)
        self.counts=Counter();self.cache=OrderedDict();self.samples=[];self.page=None
        self.preferred_origins=set();self.browser_closed=False
        self.control=control;self.gate_reason='';self.events=[];self.event_counts=Counter();self._started=time.monotonic()
        cfg=scanner.config
        self.max_tabs=min(3,max(1,int(getattr(cfg,'ASSISTED_DETAIL_TABS',3)or 3)))
        self.http_reprobe_enabled=bool(getattr(cfg,'ASSISTED_HTTP_REPROBE',True))
        self.http_reprobe_after=max(1,int(getattr(cfg,'ASSISTED_HTTP_REPROBE_AFTER',5)or 5))
        self.http_reprobe_cooldown=max(0,int(getattr(cfg,'ASSISTED_HTTP_REPROBE_COOLDOWN_SEC',60)or 0))
        self.pages=[];self._parallel_pages=[];self._free_parallel_pages=[]
        self._prefetched={};self._prefetched_results={}
        self._http_failed_origins=set();self._last_reprobe={};self._successful_browser_reads=0
        self._batch_mode=False;self.batch_interrupted=False;self.batch_interrupt_reason=''
        self._lookahead=[];self._lookahead_index=0;self._lookahead_active=False
    def prime_lookahead(self,urls):
        """Supply the upcoming JD URL order for cooperative prefetch in ``read``.

        This keeps the caller's existing per-row progress, persistence and
        interruption loop intact while overlapping known browser navigations.
        """
        self._lookahead=list(urls);self._lookahead_index=0;self._lookahead_active=bool(urls)
        self.batch_interrupted=False;self.batch_interrupt_reason=''
    def stopped(self):return self.cancel is not None and self.cancel.is_set()
    @staticmethod
    def _origin(url):
        parts=urlsplit(url or '')
        return parts.scheme.lower()+'://'+parts.netloc.lower()
    def _new_page(self):
        if self.context is None:return None
        page=self.context.new_page();watch_page_responses(page)
        self.pages.append(page)
        return page
    def _new_parallel_page(self):
        if len(self._parallel_pages)>=self.max_tabs:return None
        page=self._new_page()
        if page is not None:self._parallel_pages.append(page)
        return page
    def _release_parallel_page(self,page):
        if page is None:return
        try:
            if page.is_closed():return
        except Exception:
            return
        if not any(candidate is page for candidate in self._free_parallel_pages):
            self._free_parallel_pages.append(page)
    def _needs_browser(self,url):
        origin=self._origin(url)
        return origin in self.preferred_origins or origin in self._http_failed_origins
    def _event(self,phase,url,**data):
        self.event_counts[phase]+=1
        self.events.append(dict(captured_at=datetime.now(timezone.utc).isoformat(),elapsed_seconds=round(time.monotonic()-self._started,3),phase=phase,url=redact_url(url),**{k:redact_text(v)if k in('title','reason','error')else v for k,v in data.items()}))
        if len(self.events)>128:self.events=self.events[:16]+self.events[-112:]
    def _attention(self,waiting,url,reason=''):
        if self.control is not None:self.control.attention(dict(waiting=waiting,phase='detail',url=url,reason=reason))
    def defer_access(self,reason):self.gate_reason=str(reason)
    def _note(self,result,url):
        # Deferrals are counted per row but must not evict the actual wall.
        item=dict(url=url,status=result.status,http_status=result.http_status,title=result.title,
            transport=result.transport,reason=result.reason,jd_chars=len(result.text),method=result.method,
            requested_url=result.requested_url or url,final_url=result.final_url,phase=result.phase,error=result.error,
            http_reason=result.http_reason,http_title=result.http_title,http_code=result.http_code,
            http_final_url=result.http_final_url,http_phase=result.http_phase,http_error=result.http_error,browser_evidence=result.browser_evidence or{})
        if result.status=='budget_skipped':
            if any(x['status']=='budget_skipped'and x['reason']==result.reason for x in self.samples):return
        if self.samples and all(self.samples[-1].get(k)==item.get(k)for k in('url','status','transport','phase','reason')):return
        for key in('url','requested_url','final_url','http_final_url'):item[key]=redact_url(item.get(key))
        for key in('title','reason','error','http_reason','http_title','http_error'):item[key]=redact_text(item.get(key))
        self.samples.append(item)
        if len(self.samples)>24:self.samples=self.samples[:12]+self.samples[-12:]
    def _saved(self,url):
        if self.conn is None:return None
        row=self.conn.execute('SELECT description,location,country,country_source,support_evidence_type,ats_source FROM jobs WHERE url=?',(url,)).fetchone()
        if row:
            row=dict(row);marker=str(row.get('support_evidence_type')or'')
            match=re.search(r'jd_sha256=([0-9a-f]{64})',marker)
            captured=re.search(r'jd_captured_at=([^; ]+)',marker)
            fresh=False
            if captured:
                try:fresh=0<=(datetime.now(timezone.utc)-datetime.fromisoformat(captured[1])).total_seconds()<86400
                except (ValueError,TypeError):pass
            proven=('jd_read_status=readable'in marker and match is not None and fresh
                    and hashlib.sha256(str(row.get('description')or'').encode()).hexdigest()==match[1])
            if proven and len(str(row.get('description')or''))>=200:
                country=(row.get('country')or'') if row.get('country_source')in('structured_job_country','job_address','provider_country','observed_country_field')else''
                return DetailResult(text=row['description'],location=row.get('location')or'',country=country,
                    country_source=row.get('country_source')or'',status='readable',method='stored_detail_cache',
                    reused=True,captured_at=captured[1],requested_url=url,final_url=url,phase='saved_cache')
        return None
    def _deferred(self,url,reason):
        return DetailResult(status='budget_skipped',reason=reason,requested_url=url,phase='admission')
    def _budget_reason(self,url):
        scope=self.scanner._detail_state.scopes.get(self.scanner._detail_state.owner(url),{})
        return ', '.join(sorted(scope.get('stops',())))or'shared detail request/time allowance exhausted'
    def _maybe_reprobe_origin(self,url,*,force=False):
        """One bounded HTTP request after clearance/successful browser reads.

        Success removes only the observed origin from browser-preferred
        transport. Failure keeps the existing conservative fallback. The probe
        reserves the same shared request/time budget as every other read.
        """
        origin=self._origin(url)
        if not self.http_reprobe_enabled or self.context is None:return False
        if not force and origin not in self.preferred_origins:return False
        now=time.monotonic()
        if now-self._last_reprobe.get(origin,0)<self.http_reprobe_cooldown:return False
        self._last_reprobe[origin]=now
        state=self.scanner._detail_state;state.register(url,self.scope)
        if not state.reserve(url,self.policy,browser=False):
            self._event('http_reprobe_deferred',url,reason=self._budget_reason(url))
            return False
        self.counts['http_reprobe']+=1;response=None
        try:
            response=self.context.request.get(url,timeout=min(12000,self.scanner.config.DETAIL_SCAN_TIMEOUT_MS))
            html=response.text()[:2000000];code=int(response.status);final=str(getattr(response,'url',url))
            if challenge_reason(html,status=code,subject='detail')or code>=400:
                self._event('http_reprobe_failed',url,status=code)
                return False
            if posting_identity(final)!=posting_identity(url):
                self._event('http_reprobe_identity',url,status=code)
                return False
            parsed=parse_detail(html,url,final,self.scanner)
            if parsed.status not in('readable','truncated')or not parsed.text:
                self._event('http_reprobe_thin',url,status=code)
                return False
            self.preferred_origins.discard(origin);self._http_failed_origins.discard(origin)
            self.counts['http_reprobe_cleared']+=1
            self._event('http_reprobe_cleared',url,status=code)
            return True
        except Exception as exc:
            self._event('http_reprobe_error',url,reason=redact_text(type(exc).__name__+': '+str(exc))[:300])
            return False
        finally:
            if response is not None:
                try:response.dispose()
                except Exception:pass
    def _prefetch_ahead(self,urls,start):
        """Start up to ``max_tabs`` browser navigations with ``wait_until='commit'``.

        Only origins already observed to need the browser are prefetched, so a
        normal HTTP-readable portal does not waste shared budget or open tabs.
        Each navigation is reserved once; ``_browser`` later finalizes it.
        """
        if self.max_tabs<=1 or self.stopped() or self.browser_closed or self.gate_reason:return
        candidates=[]
        for offset in range(self.max_tabs):
            index=start+offset
            if index>=len(urls):break
            url=urls[index]
            if url in self._prefetched or url in self._prefetched_results:continue
            if not self._needs_browser(url):continue
            candidates.append(url)
        for url in candidates:
            if self.stopped() or self.browser_closed or self.gate_reason:break
            page=self._free_parallel_pages.pop(0)if self._free_parallel_pages else self._new_parallel_page()
            if page is None:break
            try:
                if page.is_closed():
                    self.browser_closed=True;self._event('parallel_page_closed',url);break
            except Exception:
                pass
            state=self.scanner._detail_state;state.register(url,self.scope)
            if not state.reserve(url,self.policy,browser=True):
                self._prefetched_results[url]=self._finish(url,self._deferred(url,self._budget_reason(url)))
                self._release_parallel_page(page)
                continue
            self.counts['browser']+=1;self.counts['parallel_prefetch']+=1
            try:
                response=page.goto(url,wait_until='commit',timeout=self.scanner.config.DETAIL_BROWSER_TIMEOUT_MS)
                status=int(getattr(response,'status',0)or 0)
                try:page._assisted_nav_status=status
                except Exception:pass
                self._prefetched[url]=(page,status)
                self._event('parallel_navigation_started',url,status=status)
            except Exception as exc:
                error=redact_text(type(exc).__name__+': '+str(exc))[:600]
                self.counts['browser_errors']+=1
                self._prefetched_results[url]=self._finish(url,DetailResult(
                    status='failed',reason=error,transport='browser (assisted session)',
                    requested_url=url,phase='parallel_navigation',error=error))
                self._release_parallel_page(page)
    def read_many(self,urls,*,progress=None,should_stop=None):
        """Read a batch in order while overlapping known browser navigations.

        Results stay in the caller's order and always use ``read()`` for the
        final decision, preserving cache, saved-JD, HTTP, challenge, budget,
        verification and honest partial-result behavior. ``progress`` receives
        ``(done,total)``; ``should_stop`` is polled before every read so the
        listing page's Stop/close state remains authoritative.
        """
        results={};urls=list(urls)
        if not urls:return results
        self._batch_mode=True;self.counts['parallel_batches']+=1
        prior_lookahead=self._lookahead_active;self._lookahead_active=False
        try:
            for index,url in enumerate(urls):
                stopped=False
                if callable(should_stop):
                    try:stopped=bool(should_stop())
                    except Exception:stopped=False
                if stopped or self.stopped() or self.browser_closed or self.gate_reason:
                    reason=self.gate_reason or('cancelled'if self.stopped()or stopped else'detail browser was closed; resume explicitly')
                    if stopped and not self.gate_reason and not self.stopped():
                        reason='assisted browser closed during JD enrichment; resume explicitly'
                    self.batch_interrupted=True;self.batch_interrupt_reason=reason
                    for remaining in urls[index:]:
                        results[remaining]=self._finish(remaining,self._deferred(remaining,reason))
                    break
                if callable(progress):
                    try:progress(index+1,len(urls))
                    except Exception:pass
                if self.max_tabs>1:self._prefetch_ahead(urls,index)
                if url in self._prefetched_results:
                    results[url]=self._prefetched_results.pop(url)
                else:
                    results[url]=self.read(url)
                if self.gate_reason or self.browser_closed or self.stopped():
                    reason=self.gate_reason or('cancelled'if self.stopped()else'detail browser was closed; resume explicitly')
                    self.batch_interrupted=True;self.batch_interrupt_reason=reason
                    for remaining in urls[index+1:]:
                        results[remaining]=self._finish(remaining,self._deferred(remaining,reason))
                    break
        finally:
            self._batch_mode=False;self._lookahead_active=prior_lookahead
        return results
    def read(self,url):
        state=self.scanner._detail_state;state.register(url,self.scope)
        if self.stopped():return self._finish(url,self._deferred(url,'cancelled'))
        if url in self.cache:
            self.counts['reused']+=1;state.count(url,'reused');return self.cache[url]
        saved=self._saved(url)
        if saved is not None:
            self.counts['reused']+=1;self.counts['readable']+=1;state.count(url,'reused')
            state.observe(url,saved.text,transport='saved JD',kind='readable')
            self._note(saved,url);return self._remember(url,saved)
        if not self.scanner.config.ENABLE_DETAIL_SCAN:
            return self._finish(url,DetailResult(status='disabled',reason='detail enrichment disabled',requested_url=url))
        if self.browser_closed:return self._finish(url,self._deferred(url,'detail browser was closed; resume explicitly'))
        if self.control is not None and self.control.save_requested:self.gate_reason='saved partial during verification; resume explicitly'
        if self.gate_reason:return self._finish(url,self._deferred(url,self.gate_reason))
        if self.counts['blocked']>=2:
            return self._finish(url,self._deferred(url,'two unresolved detail access walls; resume after human clearance'))
        if self._lookahead_active and not self._batch_mode:
            # Overlap navigations for the current lookahead window. The current
            # URL may itself be prefetched; _browser() consumes that page.
            self._prefetch_ahead(self._lookahead,self._lookahead_index)
            self._lookahead_index+=1
        origin=urlsplit(url).scheme.lower()+'://'+urlsplit(url).netloc.lower()
        if origin in self.preferred_origins:
            self.counts['browser_preferred']+=1
            result=self._browser(url)
        elif not state.reserve(url,self.policy,browser=False):
            result=self._deferred(url,self._budget_reason(url))
        else:
            self.counts['http']+=1;response=None
            try:
                time.sleep(.12)
                response=self.context.request.get(url,timeout=min(12000,self.scanner.config.DETAIL_SCAN_TIMEOUT_MS))
                html=response.text()[:2000000];code=int(response.status);final=str(getattr(response,'url',url))
                meta=response_metadata(html,status=code,transport='HTTP (assisted session)')
                reason=challenge_reason(html,status=code,subject='detail')
                if reason:
                    self.counts['http_blocked']+=1
                    result=DetailResult(status='blocked',reason=reason,http_status=code,title=meta['title'],transport=meta['transport'])
                elif code>=400:
                    self.counts['http_failed']+=1
                    result=DetailResult(status='failed',reason=f'HTTP {code} detail unavailable',http_status=code,title=meta['title'],transport=meta['transport'])
                elif posting_identity(final)!=posting_identity(url):
                    result=DetailResult(status='empty',reason='detail redirected to a different posting; identity unproven',http_status=code,title=meta['title'],transport=meta['transport'])
                else:
                    result=parse_detail(html,url,final,self.scanner,meta['title']);result.http_status=code;result.transport=meta['transport']
                result.final_url=final;result.phase='http_response'
            except Exception as exc:
                self.counts['http_failed']+=1
                error=redact_text(type(exc).__name__+': '+str(exc))[:1200]
                result=DetailResult(status='failed',reason=error,transport='HTTP (assisted session)',phase='http_request',error=error)
            finally:
                if response is not None:
                    try:response.dispose()
                    except Exception:pass
            result.requested_url=url
            if result.status not in('readable','truncated'):
                self._http_failed_origins.add(origin)
            if result.status not in('readable','truncated')and not self.stopped():
                self._note(result,url);http=result
                result=self._browser(url)
                if not result.country:result.country=http.country
                result.http_reason=http.reason;result.http_title=http.title;result.http_code=http.http_status;result.http_final_url=http.final_url;result.http_phase=http.phase;result.http_error=http.error
                if result.status in('readable','truncated')and http.status in('blocked','empty','thin'):
                    # Evidence is scoped to this explicit portal and origin,
                    # never a seed/host preset or a fingerprint/CAPTCHA bypass.
                    self.preferred_origins.add(origin)
                    self.counts['transport_switches']+=1
                    if callable(self.progress):
                        self.progress(_('Direct HTTP did not provide a JD; continuing in the existing Chromium detail tab within shared limits.'))
        if self._lookahead_active and not self._batch_mode and(
                self.gate_reason or self.browser_closed or self.stopped()):
            self.batch_interrupted=True
            self.batch_interrupt_reason=self.gate_reason or(
                'cancelled'if self.stopped()else'detail browser was closed; resume explicitly')
        return self._finish(url,result)
    def _finish(self,url,result):
        state=self.scanner._detail_state;result.requested_url=result.requested_url or url
        for key in('requested_url','final_url','http_final_url'):setattr(result,key,redact_url(getattr(result,key)))
        for key in('reason','error','title','http_reason','http_title','http_error'):setattr(result,key,redact_text(getattr(result,key)))
        if result.status in('readable','truncated'):
            result.captured_at=result.captured_at or datetime.now(timezone.utc).isoformat()
            self.counts[result.status]+=1;state.count(url,'fetched')
            state.observe(url,result.text,kind='readable',transport=result.transport,status=result.http_status,title=result.title)
            state.put(url,(result.text,result.location,result.hiring_org),dict(kind='full_jd',country=result.country,method=result.method,truncated=result.truncated))
            self.scanner.store_captured_jd(url,result.text)
        elif result.status in('blocked','failed'):
            if result.status=='blocked':self.gate_reason='unresolved detail verification; saved batch paused for explicit continuation'
            self.counts[result.status]+=1;state.failure(url,blocked=result.status=='blocked',reason=result.reason,transport=result.transport,status=result.http_status,title=result.title)
        else:
            self.counts[result.status]+=1
            if result.status not in('budget_skipped','disabled'):
                state.observe(url,result.text,kind='thin'if result.text else'empty',reason=result.reason,transport=result.transport,status=result.http_status,title=result.title)
        self._note(result,url);return self._remember(url,result)
    def _remember(self,url,result):
        self.cache[url]=result;self.cache.move_to_end(url)
        while len(self.cache)>512:self.cache.popitem(last=False)
        return result
    def _browser(self,url):
        state=self.scanner._detail_state;last=None
        prefetched=self._prefetched.pop(url,None)
        consumed_parallel_page=None
        try:
            for attempt in range(2):
                use_prefetched=attempt==0 and prefetched is not None
                if self.stopped():return self._deferred(url,'cancelled')
                if self.browser_closed:return self._deferred(url,'detail browser was closed; resume explicitly')
                if not use_prefetched:
                    if not state.reserve(url,self.policy,browser=True,retry=attempt>0):
                        deferred=self._deferred(url,self._budget_reason(url))
                        if last is not None:deferred.error=last.error;deferred.reason+='; '+last.reason
                        return deferred
                    self.counts['browser']+=1
                    if attempt:self.counts['browser_retries']+=1
                phase='page_creation';page=(prefetched[0]if use_prefetched else self.page);status=0;title='';prompted=False
                try:
                    if page is not None and page.is_closed():
                        self.browser_closed=True
                        return DetailResult(status='failed',reason='detail browser closed by user; resume explicitly',transport='browser (assisted session)',requested_url=url,phase='page_closed')
                    if page is None:
                        self.page=page=self._new_page()
                        if page is None:
                            return DetailResult(status='failed',reason='assisted browser context unavailable',transport='browser (assisted session)',requested_url=url,phase='page_creation')
                    if use_prefetched:
                        self.page=page;consumed_parallel_page=page;status=int(prefetched[1]or 0)
                        prefetched=None
                        # The commit-phase navigation is already in flight; wait
                        # briefly for DOM readiness before reading its document.
                        try:page.wait_for_load_state('domcontentloaded',timeout=min(3000,self.scanner.config.DETAIL_BROWSER_TIMEOUT_MS))
                        except Exception:pass
                        phase='document_read'
                    else:
                        time.sleep(.12)
                        phase='navigation'
                        # Never parse the previous document after a failed goto.
                        response=page.goto(url,wait_until='domcontentloaded',timeout=self.scanner.config.DETAIL_BROWSER_TIMEOUT_MS)
                        status=int(getattr(response,'status',0)or 0)
                    deadline=time.monotonic()+max(.1,self.clear_wait);prompted=False;waited=False
                    while not self.stopped()and not page.is_closed():
                        phase='document_read'
                        html=_page_content_stable(page);title=page.title()or'';final=page.url
                        status=int(getattr(page,'_assisted_nav_status',status)or 0)
                        snapshot=inspect_page(page,kind='detail',status=status,validator=getattr(self.scanner,'is_valid_job_url',None))
                        reason=snapshot['reason']if snapshot is not None else challenge_reason(html,title=title,frame_urls=[f.url for f in page.frames],subject='detail')
                        # A human can replace a 403 interstitial with a real JD
                        # without a new top-level response. Last-navigation status
                        # must not override a now-proven primary posting DOM.
                        if snapshot is None and not reason and status in(401,403,429)and parse_detail(html,url,final,self.scanner,title).status not in('readable','truncated'):
                            reason=challenge_reason(html,title=title,status=status,subject='detail')
                        if prompted and not reason and snapshot is not None and not snapshot.get('detail_ready'):
                            reason='Awaiting current visible primary job content after verification; human verification not confirmed'
                        if reason:
                            if not prompted:
                                prompted=True
                                self._event('verification_waiting',url,status=status,title=title,evidence=public_snapshot(snapshot))
                                self._attention(True,url,reason)
                                try:page.bring_to_front()
                                except Exception:pass
                                if callable(self.progress):
                                    message=_('Action needed: job-detail access was denied at {url}. Inspect Chromium or stop; no CAPTCHA was confirmed.') if 'not confirmed'in reason else _('Action needed: job-detail verification at {url} — please solve the check in Chromium or stop.')
                                    self.progress(message.format(url=url))
                                continue
                            command=self.control.poll(page)if self.control is not None else''
                            if command=='save':
                                self._event('verification_saved_partial',url,evidence=public_snapshot(snapshot));self._attention(False,url,reason)
                                self.gate_reason='saved partial during verification; resume explicitly'
                                result=self._deferred(url,self.gate_reason)
                                result.http_status=status;result.title=title;result.transport='browser (assisted session)'
                                result.final_url=final;result.phase='human_save_partial';result.browser_evidence=public_snapshot(snapshot)
                                return result
                            if command=='check':self._event('verification_check_requested',url,evidence=public_snapshot(snapshot))
                            if time.monotonic()>=deadline:
                                self._event('verification_not_accepted',url,status=status,title=title,evidence=public_snapshot(snapshot));self._attention(False,url,reason)
                                return DetailResult(status='blocked',reason=reason,http_status=status,title=title,transport='browser (assisted session)',requested_url=url,final_url=final,phase='human_clearance',browser_evidence=public_snapshot(snapshot))
                            time.sleep(min(.5,max(.01,deadline-time.monotonic())));continue
                        if posting_identity(final)!=posting_identity(url):
                            return DetailResult(status='empty',reason='browser reached a different posting; requested identity unproven',http_status=status,title=title,transport='browser (assisted session)',requested_url=url,final_url=final,phase='identity_check')
                        if prompted:
                            self._event('verification_cleared',url,status=status,title=title,evidence=public_snapshot(snapshot));self._attention(False,url)
                            prompted=False
                            self._maybe_reprobe_origin(url,force=True)
                        result=parse_detail(html,url,final,self.scanner,title,browser_evidence=snapshot)
                        result.http_status=status;result.transport='browser (assisted session)'
                        result.requested_url=url;result.final_url=final;result.phase=phase;result.browser_evidence=public_snapshot(snapshot)
                        if result.status in('readable','truncated')or waited:
                            if result.status in('readable','truncated'):
                                self._successful_browser_reads+=1
                                if self._successful_browser_reads%self.http_reprobe_after==0:
                                    self._maybe_reprobe_origin(url)
                            if last is not None:
                                result.error=last.error
                                result.phase+=' (recovered '+last.phase+')'
                            return result
                        phase='javascript_wait';waited=True
                        try:
                            page.wait_for_function("() => {const n=document.querySelector('[itemprop=description],[class*=job-description],main');return n && (n.innerText||'').length>=200}",timeout=min(3000,self.scanner.config.DETAIL_BROWSER_TIMEOUT_MS))
                        except Exception:pass
                        # Reinspect the wall and URL after JavaScript, not just prose.
                    if page.is_closed():self.browser_closed=True
                    return DetailResult(status='failed',reason='detail browser closed or cancelled; saved jobs retained',transport='browser (assisted session)',requested_url=url,phase='page_closed')
                except Exception as exc:
                    error=redact_text(type(exc).__name__+': '+str(exc))[:1200]
                    try:final=page.url if page is not None else''
                    except Exception:final=''
                    last=DetailResult(status='failed',reason=error,transport='browser (assisted session)',requested_url=url,final_url=final,phase=phase,error=error)
                    self._note(last,url);self.counts['browser_errors']+=1
                    try:closed=page is not None and page.is_closed()
                    except Exception:closed=False
                    if closed:self.browser_closed=True;return last
                    if prompted:
                        self._event('verification_retry',url,status=status,title=title,reason=error)
                        self._attention(False,url,reason='transient detail read error; retrying')
                    if attempt:return last
                finally:
                    if consumed_parallel_page is not None:
                        self._release_parallel_page(consumed_parallel_page)
                        consumed_parallel_page=None
        finally:
            if consumed_parallel_page is not None:
                self._release_parallel_page(consumed_parallel_page)
        return last or self._deferred(url,'detail browser unavailable')
    def close(self):
        if self.control is not None:self.control.attention(dict(waiting=False,phase='detail',url='',reason=''))
        seen=set()
        for page in list(self.pages)+([self.page]if self.page is not None else[]):
            if page is None or id(page)in seen:continue
            seen.add(id(page))
            try:page.close()
            except Exception:pass
        self._free_parallel_pages=[];self._prefetched={};self._prefetched_results={}
    def report(self):return dict(self.counts),list(self.samples)
