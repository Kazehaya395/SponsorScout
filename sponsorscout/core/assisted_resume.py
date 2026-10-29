"""Explicit exact-portal continuation with selectable per-run snapshots.

No startup/seed/company sweep. Immutable checkpoint payloads plus an atomic
latest pointer keep prior complete scopes selectable after a partial run.
Legacy v1 manifests/schema3 CSVs remain worker-only compatible.
"""
from __future__ import annotations
import csv,hashlib,json,os,re,tempfile
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlsplit,parse_qsl
from sponsorscout.core.job_identity import portal_queue_key,posting_identity,posting_candidate,administrative_action
from sponsorscout.i18n import _
from sponsorscout.core.browser_evidence import sensitive_url
MAX_BATCH_ROWS=20000
MAX_BATCH_BYTES=25000000
MAX_SNAPSHOTS=1000


def database_key(conn):
    rows=conn.execute('PRAGMA database_list').fetchall()
    filename=next((r[2]for r in rows if r[1]=='main'),'')
    return os.path.normcase(str(Path(filename).resolve()))if filename else':memory:'


def _path(conn,url):
    from sponsorscout import paths
    key=portal_queue_key(url)
    if not key:raise ValueError(_('Please provide a valid HTTP or HTTPS portal URL.'))
    digest=hashlib.sha256((database_key(conn)+'\0'+key).encode()).hexdigest()
    return paths.SCAN_OUTPUT_DIR/'assisted_batches'/(digest+'.json')


def _run(value):
    value=str(value)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value):raise ValueError('Invalid saved run identifier')
    return value


def _check_cancelled():
    from sponsorscout.core.db_tasks import current_database_task
    task=current_database_task()
    if task is not None and task.cancelled.is_set():raise InterruptedError('Superseded saved-batch read')


def _read(path):
    _check_cancelled()
    if path.stat().st_size>MAX_BATCH_BYTES:raise ValueError('Saved JD batch exceeds the bounded file limit')
    raw=path.read_bytes()
    match=re.fullmatch(r'[A-Za-z0-9_-]{1,100}--([0-9a-f]{20})\.json',path.name)
    if match and hashlib.sha256(raw).hexdigest()[:20]!=match[1]:raise ValueError('Saved checkpoint integrity mismatch')
    return json.loads(raw.decode('utf-8'))


def _identity(conn,url,data):
    if not isinstance(data,dict)or data.get('version')not in(1,2)or data.get('portal_key')!=portal_queue_key(url)or data.get('database')!=database_key(conn):
        raise ValueError(_('Saved JD batch does not match this database and exact portal URL.'))


def _validate(conn,url,data):
    _identity(conn,url,data)
    rows=data.get('rows')
    if not isinstance(rows,list)or not rows or len(rows)>MAX_BATCH_ROWS:raise ValueError(_('Saved JD batch is empty or invalid. Run Assisted listing ingestion first.'))
    urls=[];validator=None
    for row in rows:
        if not isinstance(row,dict):raise ValueError(_('Saved JD batch contains an invalid posting identity.'))
        target,title=str(row.get('url')or''),str(row.get('title')or'')
        if not posting_candidate(target,title):
            # Preserve the same existing generic validator used at ingestion,
            # including localized detail routes and short fixture identifiers.
            # No seed load, preflight, network, policy/rule change or scan.
            if validator is None:
                from sponsorscout.scanning.career.career_scanner import CareerPortalScanner
                validator=CareerPortalScanner(input_csv='__unused__.csv',skip_preflight=True).is_valid_job_url
            if not posting_candidate(target,title,validator):raise ValueError(_('Saved JD batch contains an invalid posting identity.'))
        urls.append(row['url'])
    if len(set(urls))!=len(urls):raise ValueError(_('Saved JD batch contains duplicate posting URLs.'))
    return data


def _atomic(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True);temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix=path.stem+'.',suffix='.tmp',delete=False)as f:
            temporary=Path(f.name);f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(temporary,path)
    finally:
        if temporary is not None and temporary.exists():temporary.unlink()


def _snapshot(path,data):
    raw=json.dumps(data,ensure_ascii=False).encode('utf-8')
    if len(raw)>MAX_BATCH_BYTES:raise ValueError('Assisted saved batch exceeds the bounded file limit')
    name=_run(data['run_id'])+'--'+hashlib.sha256(raw).hexdigest()[:20]+'.json'
    target=path.with_suffix('')/name
    if target.exists():
        if target.read_bytes()!=raw:raise ValueError('Saved checkpoint integrity mismatch')
    else:_atomic(target,raw)
    return name


def save_batch(conn,url,seed_name,rows,*,run_id,listing_completed,pages,loads=0,
               source_subtype='direct',stop_reason='',root_run_id='',continuation=None,stated_total=None):
    if not rows:return None
    if len(rows)>MAX_BATCH_ROWS:raise ValueError('Assisted saved batch exceeds the bounded row limit')
    data=dict(version=2,database=database_key(conn),portal_key=portal_queue_key(url),url=url,
        seed_name=seed_name,source_subtype=source_subtype,run_id=_run(run_id),root_run_id=root_run_id or run_id,
        listing_completed=bool(listing_completed),pages=int(pages),loads=int(loads),stop_reason=stop_reason,
        saved_at=datetime.now(timezone.utc).isoformat(),continuation=continuation or{},stated_total=stated_total,rows=rows)
    _validate(conn,url,data);path=_path(conn,url)
    # Retain a v1 scope before replacing its pointer. Never discard or rewrite
    # original CSV/history. Invalid cross-scope pointers fail closed.
    if path.exists():
        previous=_read(path);_identity(conn,url,previous)
        if 'rows'in previous:_snapshot(path,_validate(conn,url,previous))
    payload=_snapshot(path,data)
    pointer=dict(version=2,database=data['database'],portal_key=data['portal_key'],payload=payload,run_id=data['run_id'])
    _atomic(path,json.dumps(pointer).encode('utf-8'))
    return data


def _snapshots(conn,url):
    directory=_path(conn,url).with_suffix('')
    if not directory.exists():return []
    files=[]
    for p in directory.iterdir():
        _check_cancelled()
        if p.is_file()and re.fullmatch(r'[A-Za-z0-9_-]{1,100}--[0-9a-f]{20}\.json',p.name):files.append(p)
        if len(files)>MAX_SNAPSHOTS:raise ValueError('Too many saved checkpoints; archive older scoped checkpoints explicitly')
    return files


def _legacy_candidates(conn,url,seed_name,run_id=None):
    rows=conn.execute("SELECT run_id,seed_url,status,diagnostics,jobs_found FROM scan_log WHERE scanner='assisted_browser' AND LOWER(seed_name)=LOWER(?) ORDER BY id DESC LIMIT 200",(seed_name,)).fetchall()
    return [dict(row)for row in rows if portal_queue_key(row['seed_url'])==portal_queue_key(url)and(not run_id or row['run_id']==run_id)]


def _legacy_meta(conn,row):
    event=conn.execute("SELECT message FROM scan_events WHERE run_id=? AND phase='assisted_batch' ORDER BY id DESC LIMIT 1",(row['run_id'],)).fetchone()
    meta=json.loads(event[0])if event else{}
    diagnostics=str(row.get('diagnostics')or'')
    complete=bool(meta.get('listing_completed'))if event else row['status']=='ok'or'listing traversal complete, JD coverage incomplete:'in diagnostics
    match=re.search(r'across (\d+) page\(s\)',diagnostics)
    return dict(meta,listing_completed=complete,pages=int(meta.get('pages',match[1]if match else 0)))


def _legacy(conn,url,seed_name,run_id=None):
    selected=next(iter(_legacy_candidates(conn,url,seed_name,run_id)),None)
    if selected is None:raise ValueError(_('No saved JD batch for this exact portal URL. Run Assisted listing ingestion first.'))
    from sponsorscout import paths
    run_id=_run(selected['run_id']);source=paths.SCAN_OUTPUT_DIR/(run_id+'_career_jobs.csv')
    if not source.is_file():raise ValueError(_('The saved run CSV is unavailable. Run Assisted listing ingestion first.'))
    if source.stat().st_size>MAX_BATCH_BYTES:raise ValueError('Saved run CSV exceeds the bounded file limit')
    with source.open(encoding='utf-8-sig',newline='')as f:
        reader=csv.DictReader(f)
        if not {'title','url','jd_read_status','support_evidence_type'}<=set(reader.fieldnames or()):raise ValueError(_('This saved run has no auditable JD coverage. Run Assisted listing ingestion first.'))
        rows=[]
        for row in reader:
            rows.append(dict(row))
            if len(rows)>MAX_BATCH_ROWS:raise ValueError('Saved run exceeds the bounded row limit')
    meta=_legacy_meta(conn,selected)
    data=dict(version=1,database=database_key(conn),portal_key=portal_queue_key(url),url=url,seed_name=seed_name,
        source_subtype=rows[0].get('source_subtype','direct')if rows else'direct',run_id=run_id,
        root_run_id=meta.get('root_run_id')or run_id,listing_completed=meta['listing_completed'],pages=meta['pages'],loads=0,
        continuation=meta.get('continuation')or{},stated_total=meta.get('stated_total'),
        stop_reason='saved listing complete'if meta['listing_completed']else'saved listing remains incomplete',rows=rows)
    return _validate(conn,url,data)


def _observed_cursor(conn,url,data):
    if data.get('listing_completed')or data.get('continuation'):return data
    # Upgrade only from this exact saved run's own browser evidence. No page
    # arithmetic, company sweep, network probe or retrospective DB writes.
    try:
        scope=conn.execute("SELECT seed_url FROM scan_log WHERE run_id=? AND scanner='assisted_browser' ORDER BY id DESC LIMIT 1",(data['run_id'],)).fetchone()
        if not scope or portal_queue_key(scope[0])!=portal_queue_key(url):return data
        rows=conn.execute("SELECT message FROM scan_events WHERE run_id=? AND phase='assisted_response' ORDER BY id DESC LIMIT 64",(data['run_id'],)).fetchall()
    except Exception:
        _check_cancelled();return data
    for row in rows:
        _check_cancelled()
        try:event=json.loads(row[0])
        except (TypeError,ValueError):continue
        target=str(event.get('url')or'');page=event.get('page')
        if(event.get('phase')in('foreground_challenge','foreground_access','waiting_for_clearance','waiting_after_reload','recovery_challenge','recovery_access')
           and type(page)is int and page>int(data.get('pages',0))and urlsplit(target).scheme in('http','https')
           and event.get('reason')and not sensitive_url(target) and not administrative_action(target)):
            data=dict(data,continuation=dict(url=target,page=page,provenance='observed_browser',portal_key=portal_queue_key(url),captured_at=event.get('captured_at',''),source_run_id=data['run_id']))
            break
    return data


def _admitted(conn,url,data):return _observed_cursor(conn,url,_validate(conn,url,data))


def load_batch(conn,url,seed_name,run_id=None):
    """Load exactly the selected run, or latest when the user leaves it blank."""
    path=_path(conn,url)
    if run_id:
        run_id=_run(run_id);choices=[]
        for file in _snapshots(conn,url):
            if file.name.startswith(run_id+'--'):
                data=_validate(conn,url,_read(file))
                if data['run_id']==run_id:choices.append(data)
        if choices:return _observed_cursor(conn,url,max(choices,key=lambda x:x.get('saved_at','')))
        if path.exists():
            data=_read(path);_identity(conn,url,data)
            if data.get('run_id')==run_id and'rows'in data:return _admitted(conn,url,data)
        return _observed_cursor(conn,url,_legacy(conn,url,seed_name,run_id))
    if path.exists():
        data=_read(path);_identity(conn,url,data)
        if 'rows'in data:return _admitted(conn,url,data)
        payload=str(data.get('payload')or'')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}--[0-9a-f]{20}\.json',payload):raise ValueError('Invalid scoped checkpoint pointer')
        value=_read(path.with_suffix('')/payload)
        if value.get('run_id')!=data.get('run_id'):raise ValueError('Invalid scoped checkpoint pointer identity')
        return _admitted(conn,url,value)
    return _observed_cursor(conn,url,_legacy(conn,url,seed_name))


def list_batches(conn,url,seed_name):
    """Explicit UI action, background worker only, bounded exact-scope metadata."""
    found={}
    for file in _snapshots(conn,url):
        data=_validate(conn,url,_read(file));run=data['run_id']
        item=dict(run_id=run,rows=len(data['rows']),pages=data.get('pages',0),listing_completed=bool(data.get('listing_completed')),
            saved_at=data.get('saved_at',''),can_resume_listing=bool(data.get('continuation'))and not data.get('listing_completed'),source='checkpoint')
        if run not in found or item['saved_at']>found[run]['saved_at']:found[run]=item
    path=_path(conn,url)
    if path.exists():
        data=_read(path);_identity(conn,url,data)
        if 'rows'in data and data.get('run_id')not in found:
            data=_observed_cursor(conn,url,data)
            found[data['run_id']]=dict(run_id=data['run_id'],rows=len(data['rows']),pages=data.get('pages',0),listing_completed=bool(data.get('listing_completed')),saved_at='',can_resume_listing=bool(data.get('continuation')),source='v1 checkpoint')
    from sponsorscout import paths
    for row in _legacy_candidates(conn,url,seed_name):
        _check_cancelled()
        run=_run(row['run_id'])
        if run in found:continue
        source=paths.SCAN_OUTPUT_DIR/(run+'_career_jobs.csv')
        if not source.is_file()or source.stat().st_size>MAX_BATCH_BYTES:continue
        with source.open(encoding='utf-8-sig',newline='')as f:
            if not {'url','jd_read_status','support_evidence_type'}<=set(next(csv.reader(f),[])):continue
        meta=_legacy_meta(conn,row)
        found[run]=dict(run_id=run,rows=row['jobs_found'],pages=meta['pages'],listing_completed=meta['listing_completed'],saved_at='',
            can_resume_listing=bool(meta.get('continuation'))and not meta['listing_completed'],source='saved CSV')
    return sorted(found.values(),key=lambda x:(x['saved_at'],x['run_id']),reverse=True)[:200]



def observed_url_in_scope(root,target):
    """Replayable same-route state must retain the selected filters/hash.

    Pagination keys/path segments may differ, but no URL is constructed here.
    Opaque live-only widget/session state is not a serializable continuation.
    """
    paging={'p','page','pg','pagenumber','pagenum','currentpage','offset','start','pagina','seite'}
    try:
        a,b=urlsplit(root),urlsplit(target)
        if (a.scheme.casefold(),a.netloc.casefold())!=(b.scheme.casefold(),b.netloc.casefold()):return False
        route=lambda p:re.sub(r'/page/\d+(?=/|$)','',p,flags=re.I).rstrip('/')or'/'
        if route(a.path)!=route(b.path):return False
        def contains_filters(original,current):
            keys={k for k,v in parse_qsl(original,keep_blank_values=True)if k.casefold()not in paging}
            return all([v for k,v in parse_qsl(original,keep_blank_values=True)if k==key]==[v for k,v in parse_qsl(current,keep_blank_values=True)if k==key]for key in keys)
        if not contains_filters(a.query,b.query):return False
        if a.fragment:
            ap,sep,aq=a.fragment.partition('?');bp,_,bq=b.fragment.partition('?')
            if ap!=bp or(sep and not contains_filters(aq,bq)):return False
        return not sensitive_url(target)
    except (TypeError,ValueError):return False


def listing_continuation(batch):
    """Only previously observed browser/href state; never invent a page URL."""
    if batch.get('listing_completed'):raise ValueError(_('Saved listing is already complete. Use Resume unread JDs.'))
    state=batch.get('continuation')or{}
    target=str(state.get('url')or'');parts=urlsplit(target)
    if (state.get('provenance')not in('observed_browser','observed_forward_link')or
        state.get('portal_key')!=batch['portal_key']or parts.scheme not in('http','https')or not parts.netloc or
        administrative_action(target)or not observed_url_in_scope(batch.get('url')or batch['portal_key'],target)or
        type(state.get('page'))is not int or state['page']<=int(batch.get('pages',0))or
        portal_queue_key(target)==batch['portal_key']):
        raise ValueError(_('No auditable observed listing continuation is saved. Start a fresh listing run; no page URL was guessed.'))
    return state


def existing_records(conn,batch):
    """Return only recorded batch identities; never repopulate deleted rows."""
    pairs=[]
    for original in batch['rows']:
        row=dict(original);url=row['url']
        saved=conn.execute('SELECT * FROM jobs WHERE url=? OR posting_key=? ORDER BY (url=?) DESC,id LIMIT 1',(url,posting_identity(url),url)).fetchone()
        record=dict(saved)if saved is not None else None
        if record is not None:
            record['url']=url
            # Preserve all current manual/source/active/country fields. An
            # explicit quality-review exclusion is never undone by resume.
            if (record.get('is_expired')or not record.get('verified_active')or
                str(record.get('source_type')or'').casefold()in('excluded_nonjob','manual_excluded')):
                record=None
        if record is None:
            row.update(jd_read_status='protected',jd_reason='saved posting is missing, inactive or excluded; not recreated')
        pairs.append((record,row))
    return pairs
