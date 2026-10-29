"""Current rendered browser authority, not a CAPTCHA solver or network client.

Used only in an explicitly opened Assisted context. Raw HTTP classification
stays structural; a browser may prove that a stale title/status/hidden widget
no longer represents the current primary document. No vendor/hostname presets.
"""
from __future__ import annotations
import re
from html import escape
from datetime import datetime,timezone
from sponsorscout.scanning.response_evidence import challenge_reason
from sponsorscout.core.job_identity import posting_candidate,posting_identity

# One bounded DOM read, no clicks, injected handlers, cookies or storage access.
VISIBLE_DOCUMENT_JS = r"""() => {
 const cache=new WeakMap();
 const visible=(e)=>{
  if(!e || e.nodeType!==1)return false;
  if(cache.has(e))return cache.get(e);
  let ok=true;
  for(let n=e;n&&n.nodeType===1;n=n.parentElement){
   const s=getComputedStyle(n);
   if(s.display==='none'||(n===e&&(s.visibility==='hidden'||s.visibility==='collapse'))||Number(s.opacity)===0||s.clipPath==='inset(50%)'){ok=false;break;}
  }
  if(ok){
   let rects=Array.from(e.getClientRects());
   if(getComputedStyle(e).display==='contents'){const range=document.createRange();range.selectNodeContents(e);rects=Array.from(range.getClientRects());}
   ok=rects.some(r=>r.width>1&&r.height>1);
  }
  cache.set(e,ok);return ok;
 };
 // A visible recommendation/sidebar is not the current primary posting or
 // listing. Do not let a longer related-job description win by text length.
 const secondary=(e,heading=false)=>{
  if(e.closest(heading?'nav,footer,aside':'nav,footer,aside,form'))return true;
  for(let n=e;n&&n!==document.body&&n!==document.documentElement;n=n.parentElement){
   const label=(n.id||'')+' '+(typeof n.className==='string'?n.className:'');
   if(/related[-_ ]|recommend|suggested[-_ ]jobs|similar[-_ ]jobs|breadcrumb/i.test(label))return true;
  }
  return false;
 };
 const text=(root,limit=60001,includeSecondary=false)=>{
  if(!root)return '';
  const walk=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let n,out=[],size=0,count=0;
  while((n=walk.nextNode())&&count++<12000&&size<limit){
   const e=n.parentElement;
   if(!e||e.closest('script,style,noscript,template')||!visible(e)||(!includeSecondary&&secondary(e)))continue;
   const s=(n.nodeValue||'').replace(/\s+/g,' ').trim();
   if(s){out.push(s);size+=s.length+1;}
  }
  return out.join(' ').slice(0,limit);
 };
 const headings=Array.from(document.querySelectorAll('h1,h2')).filter(e=>visible(e)&&!secondary(e,true)).slice(0,12).map(e=>text(e,240,true));
 const anchors=Array.from(document.querySelectorAll('a[href]')).filter(e=>visible(e)&&!secondary(e)).slice(0,180).map(e=>({url:e.href,title:text(e,240),in_card:!!e.closest('article,.job-card,[class*=job-result]')}));
 const descriptions=Array.from(document.querySelectorAll('[itemprop="description"],[class*="job-description"],[id*="job-description"],[class*="jobDescription"],[data-testid*="job-description"]')).filter(e=>visible(e)&&!secondary(e)).slice(0,24).map(e=>text(e));
 const primary=descriptions.sort((a,b)=>b.length-a.length)[0]||'';
 const main=document.querySelector('main,[role="main"]');
 const ids=Array.from(document.querySelectorAll('#px-captcha,#cf-challenge-running,#challenge-running'));
 const activeIds=ids.filter(visible).map(e=>({id:e.id,text:text(e,1200,true),fixed:getComputedStyle(e).position==='fixed'&&e.getBoundingClientRect().width*e.getBoundingClientRect().height>=innerWidth*innerHeight*.2}));
 const overlays=[];const frames=[];
 const nodes=Array.from(document.querySelectorAll('dialog,[aria-modal="true"],iframe,[style*="position"],#px-captcha,#cf-challenge-running,#challenge-running')).slice(0,300);
 for(const e of nodes){
  if(!visible(e))continue;
  const s=getComputedStyle(e),r=e.getBoundingClientRect();
  const modal=e.matches('dialog[open],[aria-modal="true"]');
  let fixed=s.position==='fixed';
  for(let n=e.parentElement;n&&!fixed;n=n.parentElement)fixed=getComputedStyle(n).position==='fixed'||n.getAttribute('aria-modal')==='true';
  if(e.tagName==='IFRAME'){
   if(modal||fixed&&r.width*r.height>=innerWidth*innerHeight*.2)frames.push(e.src||'');
  }else if(modal||fixed)overlays.push(text(e,1500,true));
 }
 return {version:1,url:location.href,title:document.title,headings,anchors,
  cards_count:Array.from(document.querySelectorAll('article,.job-card,[class*=job-result]')).filter(e=>visible(e)&&!secondary(e)).length,
  primary_text:primary,main_text:text(main),body_preview:text(document.body,1600),
  active_ids:activeIds,hidden_challenge_nodes:ids.filter(e=>!visible(e)).length,
  overlays:overlays.slice(0,12),foreground_frames:frames.slice(0,8)};
}"""
_PROMPT=re.compile(r'(?i)verify (?:that )?you (?:are|are not) (?:a )?(?:human|robot)|confirm (?:that )?you are (?:a )?human|press\s+(?:&|and)\s+hold|access (?:to this page has been |is )?denied|403 forbidden|checking (?:your|the) browser|complete (?:the |this )?(?:security check|captcha)')
_LISTING=re.compile(r'(?i)^(?:(?:[0-9][0-9,. ]*|current|available|all|latest|our)\s+)?(?:search|(?:job )?results?|vacancies|vacatures|careers|jobs?|open positions|stellenangebote)\b')


# Diagnostic-only sanitizers. Operational posting/scope identities are not
# rewritten. Public non-secret query/filter/fragment strings stay byte-for-byte.
def _private_key(key):
    key=str(key).casefold().replace('-','_')
    return ('token'in key or key.startswith('__cf_chl')or key in
        {'auth','authorization','password','passwd','session','sessionid','api_key','apikey','cookie','secret','jwt','code'})


def sensitive_url(value):
    from urllib.parse import urlsplit,parse_qsl,unquote
    try:
        p=urlsplit(str(value or''))
        return bool(p.username or p.password or '[redacted]'in unquote(str(value)).casefold()or
            any(_private_key(k)for k,v in parse_qsl(p.query,keep_blank_values=True))or
            re.search(r'(?i)(?:^|[?&])(?:[\w-]*token|auth|password|session|jwt|code)=',p.fragment))
    except ValueError:return True


def redact_url(value):
    from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
    value=str(value or'')
    try:
        p=urlsplit(value);pairs=parse_qsl(p.query,keep_blank_values=True)
        private=any(_private_key(k)for k,v in pairs)
        query=urlencode([(k,'[redacted]'if _private_key(k)else v)for k,v in pairs])if private else p.query
        fragment=re.sub(r'(?i)((?:^|[?&])(?:[\w-]*token|auth|password|session|jwt|code)=)[^&\s]*',r'\1[redacted]',p.fragment)
        if p.scheme in('data','javascript'):return '[embedded resource]'
        return urlunsplit((p.scheme,p.netloc.rsplit('@',1)[-1],p.path,query,fragment))
    except ValueError:return '[invalid URL]'


def redact_text(value):
    text=str(value or'')
    text=re.sub(r'https?://[^\s<>\]\)"\'`]+',lambda m:redact_url(m[0]),text)
    return re.sub(r'(?im)\b(?:set-cookie|cookie|proxy-authorization|authorization)\s*[:=]\s*[^\r\n]*','[private header redacted]',text)



def watch_page_responses(page):
    """Observe bounded document/data failures; no request/header/cookie reads."""
    if getattr(page,'_assisted_response_watch',False):return
    def retain(item):
        values=list(getattr(page,'_assisted_failed_responses',[]));values.append(item)
        page._assisted_failed_responses=values[-4:]
    def received(response):
        try:
            request=response.request
            if request.is_navigation_request()and request.frame==page.main_frame:
                page._assisted_nav_status=response.status;page._assisted_active_wall_seen=False;page._assisted_failed_responses=[]
            elif response.status>=400 and request.resource_type in('xhr','fetch'):
                retain(dict(url=redact_url(response.url),http_status=response.status,resource_type=request.resource_type,captured_at=datetime.now(timezone.utc).isoformat()))
        except Exception:pass
    def failed(request):
        try:
            if request.resource_type in('xhr','fetch'):
                retain(dict(url=redact_url(request.url),http_status=0,resource_type=request.resource_type,error=redact_text(request.failure)[:400],captured_at=datetime.now(timezone.utc).isoformat()))
        except Exception:pass
    try:
        page.on('response',received);page.on('requestfailed',failed);page._assisted_response_watch=True
    except Exception:pass  # nonbrowser adapters cannot claim observed failures


def inspect_page(page,*,kind='listing',status=0,validator=None):
    """Return None for non-browser adapters; otherwise a current DOM snapshot.

    A positive foreground wall always wins over recommended links or content
    behind an overlay. A title or old response alone cannot negate genuinely
    visible primary content. Posting identity is still checked by the reader.
    """
    try:data=page.evaluate(VISIBLE_DOCUMENT_JS)
    except Exception:return None
    if not isinstance(data,dict)or data.get('version')!=1:return None
    data['failed_data_responses']=list(getattr(page,'_assisted_failed_responses',[]))[-4:]
    data['navigation_status']=int(getattr(page,'_assisted_nav_status',status)or 0)
    headings=[str(x)[:240]for x in data.get('headings',[])[:12]]
    admitted=[a for a in data.get('anchors',[])[:180]if isinstance(a,dict)and posting_candidate(str(a.get('url')or''),str(a.get('title')or''),validator)]
    links={posting_identity(str(a.get('url')or''))for a in admitted}
    card_listing=any(a.get('in_card')and posting_identity(str(a.get('url')or''))!=posting_identity(str(data.get('url')or''))for a in admitted)
    heading=next((s for s in headings if s.strip()),'')
    primary=str(data.get('primary_text')or'')[:60001]
    main=str(data.get('main_text')or'')[:60001]
    role=bool(heading and not _LISTING.search(heading)and not _PROMPT.search(heading))
    listing=len(links)>=3 or card_listing
    detail=bool((len(primary)>=200 and not _LISTING.search(heading))or(role and len(main)>=600 and not listing))
    ready=listing if kind=='listing'else detail
    heading_wall=next((h for h in headings[:1]if _PROMPT.search(h)), '')
    overlays=[str(x)[:1500]for x in data.get('overlays',[])[:12]]
    foreground=next((x for x in overlays if _PROMPT.search(x)), '')
    ids=[x for x in data.get('active_ids',[])[:8]if isinstance(x,dict)]
    frames=[str(x)[:2000]for x in data.get('foreground_frames',[])[:8]if challenge_reason('',frame_urls=[str(x)])]
    # Visible provider ids in a wall-only document are strong evidence; an
    # application widget beside a readable primary JD is not a foreground wall.
    active=next((x for x in ids if not ready or x.get('fixed')),None)
    if heading_wall or foreground or active or frames:
        fragment='<h1>'+escape(heading_wall)+'</h1><div style="position:fixed">'+escape(foreground)+'</div>'
        if active:fragment+='<div id="'+escape(str(active.get('id')or''),quote=True)+'">'+escape(str(active.get('text')or''))+'</div>'
        reason=challenge_reason(fragment,frame_urls=frames)
        # A cross-origin foreground verification frame need not expose text.
        if not reason and frames:reason='Foreground access verification obscures the primary document (human verification not confirmed)'
        if not reason:reason='Visible access-denied foreground; primary content not available'
    elif ready:
        reason=''
    else:
        fragment='<title>'+escape(str(data.get('title')or''))+'</title><h1>'+escape(heading)+'</h1>'+escape(str(data.get('body_preview')or''))
        reason=challenge_reason(fragment,status=status)
    data.update(kind=kind,reason=reason,ready=bool(ready and not reason),listing_ready=bool(listing and not reason),
        detail_ready=bool(detail and not reason),primary_text=primary if len(primary)>=200 else main,
        primary_chars=len(primary if len(primary)>=200 else main),primary_truncated=len(primary if len(primary)>=200 else main)>=60001,visible_job_links=len(links),
        authority='current rendered primary/foreground',captured_at=datetime.now(timezone.utc).isoformat())
    return data


def public_snapshot(data):
    """No full JD, cookies, auth or frame-query tokens in diagnostic snapshots."""
    if not data:return {}
    from urllib.parse import urlsplit,urlunsplit
    def frame_path(value):
        try:p=urlsplit(redact_url(value));return urlunsplit((p.scheme,p.netloc,p.path,'',''))
        except Exception:return ''
    public={key:data.get(key)for key in('kind','title','headings','body_preview','primary_chars',
        'visible_job_links','hidden_challenge_nodes','reason','ready','authority','captured_at','navigation_status','failed_data_responses')}|{
        'foreground_frames':[frame_path(x)for x in data.get('foreground_frames',[])[:8]]}
    for key in('title','body_preview','reason'):public[key]=redact_text(public.get(key))
    public['headings']=[redact_text(x)for x in(public.get('headings')or[])]
    return public
