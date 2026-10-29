"""Generic response->regular scan->durable queue regressions (no live portals)."""
from __future__ import annotations
import csv,json,threading
from collections import Counter
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
import pytest
from sponsorscout.scanning.response_evidence import challenge_reason,listing_continuation
from sponsorscout.scanning.detail_state import DetailRequestState
from sponsorscout.core.location_evidence import row_geography,apply_row_geography
from sponsorscout.scanning import pipeline
from sponsorscout.scanning.career import career_scanner as career
from sponsorscout.scanning.ats import ats_scanner as ats
from sponsorscout.db import database as db
from sponsorscout.core import blocked_portals as bp,persistence

JD=('Responsibilities: Design reliable distributed services and maintain automated tests. '
    'Requirements: Collaborate with the engineering team and document production changes. '
    'Benefits: Flexible hours and a learning budget. ')*5
LINKS=''.join(f'<article><a href="/jobs/backend-engineer-{100001+n}">Backend Engineer {n}</a></article>'for n in range(10))
WALL='<title>Just a moment...</title><h1>Verify you are human</h1><div id="cf-challenge-running"><iframe src="https://challenges.cloudflare.com/local-fixture"></iframe></div>'+LINKS


def write_csv(path,rows):
    fields=list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w',encoding='utf-8-sig',newline='')as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)


def read_csv(path):
    with open(path,encoding='utf-8-sig',newline='')as f:return list(csv.DictReader(f))


@pytest.fixture
def local_board():
    state={'mode':'readable','requests':Counter()}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            state['requests']['POST '+self.path]+=1
            data={'total':2,'jobPostings':[{'title':f'Backend Engineer {n}',
                'externalPath':f'/job/Berlin/Engineer_R{100001+n}',
                'locationsText':'Berlin, Germany','bulletFields':[f'R{100001+n}']}for n in range(2)]}
            raw=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            state['requests'][self.path]+=1
            status=200
            if self.path.startswith('/careers'):
                if state['mode']=='unavailable':
                    status=503;html='<title>Service unavailable</title><h1>Service temporarily unavailable</h1>'
                elif state['mode']=='listingwall':
                    html=WALL
                elif state['mode']=='dynamicmore':
                    extra=''.join(f'<article><a href="/jobs/backend-engineer-{100011+n}">Backend Engineer Extra {n}</a></article>'for n in range(2))
                    import html as html_module
                    action='this.insertAdjacentHTML("beforebegin",'+json.dumps(extra)+');this.remove()'
                    html='<title>Engineering careers</title><h1>12 jobs</h1>'+LINKS+'<button id="more" onclick="'+html_module.escape(action,quote=True)+'">Load more jobs</button>'
                else:
                    html='<title>Engineering careers</title><h1>10 jobs</h1>'+LINKS
            elif state['mode']in ('blocked','workday') and self.path.endswith('100001'):
                status=403;html=WALL
            elif state['mode']=='workday':
                html=json.dumps({'jobPostingInfo':{'location':'Berlin, Germany','jobDescription':JD}})
            elif state['mode']=='thin':
                html='<html><title>Loading</title><body><main>Please wait</main></body></html>'
            else:
                data={'@type':'JobPosting','title':'Backend Engineer','description':JD,
                    'jobLocation':{'address':{'addressLocality':'Berlin','addressCountry':'DE'}},
                    'hiringOrganization':{'name':'Observed Employer'}}
                html='<html><title>Backend Engineer</title><body><h1>Backend Engineer</h1><script type="application/ld+json">'+json.dumps(data)+'</script><main>'+JD+'</main></body></html>'
            raw=html.encode();self.send_response(status);self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def log_message(self,*a):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    state['url']=f'http://127.0.0.1:{server.server_port}/careers?country=DE#roles'
    yield state
    server.shutdown();server.server_close();thread.join(timeout=3)


# 2026-10-28 contract change (issue: false bot blocks): a wall on ONE job-detail
# URL is JD coverage, not a blocked portal, so the readable 'blocked' listing is
# no longer queued. The wall is still reported in errors/scan-log (asserted below).
@pytest.mark.parametrize('mode,expected_status,queue_count',[('blocked','partial',0),('readable','completed',0),('thin','partial',0)])
@pytest.mark.parametrize('custom',[False,True])
def test_actual_regular_transport_enrichment_pipeline_queue(local_board,tmp_path,monkeypatch,mode,expected_status,queue_count,custom):
    from sponsorscout.application import seed_manager
    local_board['mode']=mode
    seed=tmp_path/'career_seed.csv';out=tmp_path/'exports';out.mkdir()
    write_csv(seed,[{'name':'Generic Board','careers_url':local_board['url'],'provider':'auto',
        'source_type':'recruiter','target_country':'Global','scope_policy':'seed_url','enabled':'true'}])
    monkeypatch.setattr(seed_manager,'user_career_path',lambda:seed)
    monkeypatch.setattr(seed_manager,'user_ats_path',lambda:seed)
    monkeypatch.setattr(seed_manager,'ensure_user_seeds',lambda:pytest.fail('scan startup reconciled seeds'))
    monkeypatch.setattr(pipeline.paths,'ensure_scan_output_dir',lambda:out)
    # A confirmed HTTP challenge cannot open any interactive fallback. Other
    # tests/harness cover real Chromium; disable optional headless location work here.
    monkeypatch.setattr(career,'sync_playwright',None)
    class MemoryCache:
        def __init__(self):self.data={}
        def get(self,url):return self.data.get(url)
        def put(self,url,value):self.data[url]=value
    cache=MemoryCache();monkeypatch.setattr(career,'_jd_cache',lambda:cache)
    monkeypatch.setattr(bp,'run_assisted_ingestion',lambda *a,**k:pytest.fail('regular scan started Assisted'))
    monkeypatch.setattr(bp,'open_in_default_browser',lambda *a,**k:pytest.fail('regular scan opened visible browser'))
    path=tmp_path/'jobs.sqlite3';db.initialize(path)
    options={'method':'custom','only_career':['Generic Board']}if custom else{}
    summary=pipeline.run_scan(db_path=path,run_ats=False,run_career=True,**options)
    assert summary['ingested']==10 and summary['quarantined']==0
    assert summary['status']==expected_status
    assert len(summary['blocked_portals'])==queue_count
    queue=[dict(r)for r in db.list_blocked_portals(path)]
    assert len(queue)==queue_count
    assert_actual_finish_notice(summary,path,monkeypatch,queue_count)
    if queue:
        assert queue[0]['url']==local_board['url'] and queue[0]['source_subtype']=='recruiter'
        assert queue[0]['phase']=='career' and queue[0]['status']=='pending'
        assert '100001' in queue[0]['reason']
        db.enqueue_blocked_portals(path,summary['blocked_portals'],run_id=summary['run_id'])
        assert len(db.list_blocked_portals(path))==1
    artifacts=summary['artifacts']['career']
    target=read_csv(artifacts['scan_log'])[0]
    assert target['Status']==('ok'if expected_status=='completed'else'partial')
    assert target['Jobs Retained']=='10' and target['Quarantined']=='0'
    assert len(read_csv(artifacts['jobs']))==10
    from pathlib import Path
    assert all(Path(artifacts[k]).read_bytes().startswith(b'\xef\xbb\xbf')for k in ('jobs','quarantine','scan_log','errors'))
    assert 'JD coverage:' in target['Diagnostics']
    errors=read_csv(artifacts['errors'])
    if mode=='blocked':
        assert any('blocked access:' in r['Message'] for r in errors)
        assert 'http_status=403' in target['Diagnostics']
        assert 'title=Just a moment' in target['Diagnostics']
    else:assert not errors
    with db.get_connection(path)as conn:
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==10
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE visa_sponsorship='N'").fetchone()[0]==0
    # Every HTTP detail is fetched once; the support pass reuses scope data,
    # not a new request per consumer. The positive blocked URL is not retried.
    assert all(value==1 for key,value in local_board['requests'].items()if '/jobs/'in key)


@pytest.mark.parametrize('html,status,blocked',[
    (WALL,200,True),
    ('<title>Careers</title><h1>10 jobs</h1><script src="/captcha-sdk.js"></script>'+LINKS,200,False),
    ('<title>Backend Engineer</title><h1>Backend Engineer</h1>'+JD+'<form><div class="g-recaptcha">Please verify you are human</div></form>',200,False),
    ('<title>Service unavailable</title><body>Please retry later</body>',503,False),
    ('<title>Forbidden</title><body>Forbidden</body>',403,True),
    ('<title>Loading</title><body>Please wait</body>',200,False),
    (json.dumps({'jobPostingInfo':{'title':'Backend Engineer','jobDescription':JD+'<form><div class="g-recaptcha">Verify you are human</div></form>'}}),200,False),
    (json.dumps({'error':'captcha_required','jobPostingInfo':{'title':'Backend Engineer','jobDescription':JD}}),200,True),
    ('<title>Backend Engineer</title><h1>Backend Engineer</h1>'+JD+'<div aria-modal="true"><h2>Verify you are human</h2><iframe src="https://challenges.cloudflare.com/local-fixture"></iframe></div>'+LINKS,200,True),
])
def test_active_wall_precedence_not_passive_sdk(html,status,blocked):
    assert bool(challenge_reason(html,status=status))is blocked
    if status==200:assert career._raw_html_is_bot_challenge(html)is blocked
    if status==503:assert not bp._is_block_message('HTTP 503 service unavailable; job listing could not be read')


def test_cached_confirmed_failure_keeps_both_scoped_owners_without_extra_request():
    state=DetailRequestState();url='https://fixture.example/jobs/100001'
    state.register(url,'https://fixture.example/careers?scope=A')
    state.failure(url,blocked=True,reason='human-verification challenge')
    state.register(url,'https://fixture.example/careers?scope=B')
    assert state.get(url)==('','','')
    assert len(state.scope_blocked('https://fixture.example/careers?scope=A'))==1
    assert len(state.scope_blocked('https://fixture.example/careers?scope=B'))==1
    state.observe(url,JD,transport='HTTP')
    assert not state.scope_blocked('https://fixture.example/careers?scope=A')


@pytest.mark.parametrize('source,location,raw,expected',[
    ('site_host','Netherlands','',('','')),
    ('site_host','Netherlands','Berlin, Germany',('Berlin, Germany','Germany')),
    ('company_hq','Pune, India','Pune, India',('','')),
    ('title+site','Berlin, Netherlands','Berlin',('Berlin','Germany')),
    ('card','Paris, France','',('Paris, France','France')),
    ('','Toronto, Canada','',('Toronto, Canada','Canada')),
])
def test_observed_country_provenance(source,location,raw,expected):
    row={'Job Location':location,'Raw Location':raw,'Location Source':source,
        'Scope Policy':'seed_url','Target Country':'Netherlands','Scope Confidence':'verified'}
    assert row_geography(row)==expected
    assert pipeline._job_country(row)==expected[1]
    apply_row_geography(row)
    assert row['Job Location']==(expected[0]or'Unknown')
    assert row['Job Country']==(expected[1]or'Unknown')
    if not expected[1]:assert row['Scope Confidence']=='unverified_seed_url'


def test_hint_cannot_enter_low_level_country_and_manual_edit_survives(tmp_path):
    path=tmp_path/'geo.db';db.initialize(path)
    job={'url':'https://fixture.example/jobs/100001','title':'Backend Engineer','company':'Observed',
        'country':'Netherlands','location':'Netherlands','country_source':'site_host'}
    with db.get_connection(path)as conn:
        persistence.upsert_job(conn,job)
        row=conn.execute('SELECT country,location FROM jobs').fetchone()
        assert not row['country'] and not row['location']
        conn.execute("UPDATE jobs SET country='Brazil',country_source='manual'")
        persistence.upsert_job(conn,job)
        row=conn.execute('SELECT country,country_source FROM jobs').fetchone()
        assert row['country']=='Brazil'and row['country_source']=='manual'


@pytest.mark.parametrize('partial,merged',[ (0,0),(3,27)])
def test_actual_history_renderer_named_counts(tmp_path,partial,merged):
    from PySide6.QtWidgets import QApplication,QTableWidget
    from PySide6.QtCore import QCoreApplication,QEvent
    from sponsorscout.ui.tabs.tools import ToolsTab
    global _APP
    _APP=QApplication.instance()or QApplication([])
    path=tmp_path/'history.db';db.initialize(path)
    with db.get_connection(path)as conn:
        conn.execute('INSERT INTO scan_runs(run_id,method,status,targets_error,targets_partial,jobs_found,jobs_quarantined,jobs_duplicates,jobs_merged)VALUES(?,?,?,?,?,?,?,?,?)',
            ('history','custom','partial',5,partial,107,13,7,merged))
    table=QTableWidget(0,8)
    ToolsTab._populate_runs_table(SimpleNamespace(runs_table=table),db.list_scan_runs(path))
    assert [table.item(0,n).text()for n in range(4,8)]==['107','7','13','5']
    table.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);_APP.processEvents()


@pytest.mark.parametrize('markup',[
    '<h1>Showing 1-10 of 120 jobs</h1>',
    '<button type="button">Load more jobs</button>',
    '<a role="button" href="#results">Next page</a>',
])
def test_static_count_does_not_override_positive_continuation(markup):
    assert listing_continuation(markup+LINKS,10)


def test_disabled_pager_not_a_false_continuation():
    assert not listing_continuation('<button disabled>Next</button><h1>10 jobs</h1>'+LINKS,10)


def test_ats_real_fetch_block_record_survives_swallowed_provider_detail(tmp_path,monkeypatch):
    import urllib.error,io
    scanner=ats.ATSScanner(seed_file=str(tmp_path/'seed.csv'),output_file=str(tmp_path/'jobs.csv'))
    scope='https://fixture.example/careers?scope=engineering';url='https://fixture.example/api/jobs/100001'
    scanner._tls.evidence_scope=scope
    monkeypatch.setattr(ats,'_keepalive_request',lambda *a,**k:None)
    def deny(*a,**k):raise urllib.error.HTTPError(url,403,'Forbidden',{},io.BytesIO(WALL.encode()))
    monkeypatch.setattr(ats,'urlopen',deny)
    # Like an API adapter that keeps the listing after its detail failure:
    try:scanner._get_json(url)
    except urllib.error.HTTPError:pass
    incidents=scanner._detail_state.scope_blocked(scope)
    assert len(incidents)==1 and incidents[0]['url']==url
    assert 'challenge' in incidents[0]['reason']


@pytest.mark.parametrize('url',[
    'https://tenant.wd5.myworkdayjobs.com/en-US/Careers/job/Berlin/Engineer_R100001',
    'https://apply.workable.com/example/j/ABC100001',
])
def test_provider_detail_http200_challenge_not_json_empty_success(tmp_path,monkeypatch,url):
    import io
    scanner=career.CareerPortalScanner(input_csv=str(tmp_path/'seed.csv'),output_csv=str(tmp_path/'jobs.csv'),detail_scan=True)
    scope='https://fixture.example/careers?scope=engineering'
    scanner._tls.detail_scope=scope
    requests=[]
    class Response(io.BytesIO):
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
    def fetch(*a,**k):requests.append(a[0]);return Response(WALL.encode())
    monkeypatch.setattr(career.urllib.request,'urlopen',fetch)
    scanner._fetch_jd_text(url,allow_browser=False)
    incident=scanner._detail_state.scope_blocked(scope)
    assert len(incident)==1 and incident[0]['url']==url
    assert incident[0]['kind']=='blocked' and 'challenge' in incident[0]['reason']
    scanner._fetch_jd_text(url,allow_browser=False)
    assert len(requests)==1


def test_actual_workday_nested_http_failure_to_ats_pipeline_queue(local_board,tmp_path,monkeypatch):
    from sponsorscout.application import seed_manager
    from urllib.parse import urlsplit
    local_board['mode']='workday'
    seed=tmp_path/'ats.csv';out=tmp_path/'exports';out.mkdir()
    scope='https://tenant.wd5.myworkdayjobs.com/Careers?country=DE#roles'
    write_csv(seed,[{'name':'Generic Workday','careers_url':scope,'ats_type':'workday','type':'workday',
        'board_slug':'tenant/Careers','source_type':'direct_employer','target_country':'Global','scope_policy':'seed_url','enabled':'true'}])
    monkeypatch.setattr(seed_manager,'user_ats_path',lambda:seed)
    monkeypatch.setattr(seed_manager,'user_career_path',lambda:seed)
    monkeypatch.setattr(seed_manager,'ensure_user_seeds',lambda:pytest.fail('seed audit at scan startup'))
    monkeypatch.setattr(pipeline.paths,'ensure_scan_output_dir',lambda:out)
    monkeypatch.setattr(ats,'urlopen',lambda *a,**k:pytest.fail('non-local fallback request'))
    monkeypatch.setenv('SPONSORSCOUT_WORKDAY_WORKERS','2')
    monkeypatch.setenv('SPONSORSCOUT_WORKDAY_DETAILS','10')
    original=ats._keepalive_request
    base=local_board['url'].split('/careers')[0]
    workers=[]
    def local_transport(url,*args,**kwargs):
        workers.append(threading.current_thread().name)
        p=urlsplit(url)
        return original(base+p.path+('?' +p.query if p.query else ''),*args,**kwargs)
    monkeypatch.setattr(ats,'_keepalive_request',local_transport)
    path=tmp_path/'ats.db';db.initialize(path)
    summary=pipeline.run_scan(db_path=path,run_ats=True,run_career=False,only_ats=['Generic Workday'])
    assert summary['ingested']==2 and summary['status']=='partial'
    # The board answered and its rows were kept, so a walled job-detail URL is
    # JD coverage, not a blocked listing: nothing is queued (2026-10-28). The
    # evidence still has to survive into the artifacts.
    assert summary['blocked_portals']==[]
    assert any('ThreadPoolExecutor' in name for name in workers)
    assert len(db.list_blocked_portals(path))==0
    assert_actual_finish_notice(summary,path,monkeypatch,0)
    log=read_csv(summary['artifacts']['ats']['scan_log'])[0]
    assert log['Status']=='partial' and 'blocked access:' in log['Diagnostics']
    assert '100001' in log['Diagnostics'] or any('100001' in (r['Message']+r['Diagnostics']) for r in read_csv(summary['artifacts']['ats']['errors']))
    assert len(read_csv(summary['artifacts']['ats']['jobs']))==2


def assert_actual_finish_notice(summary,path,monkeypatch,expected):
    from sponsorscout.ui.tabs import tools
    boxes=[]
    class Signal:
        def __init__(self):self.count=0
        def emit(self):self.count+=1
    class Box:
        Ok,ActionRole=1,2
        def __init__(self,parent):boxes.append(self)
        def setWindowTitle(self,*a):pass
        def setText(self,text):self.text=text
        def setStandardButtons(self,*a):pass
        def addButton(self,*a):return object()
        def setDefaultButton(self,*a):pass
        def exec(self):pass
        def clickedButton(self):return None
    monkeypatch.setattr(tools,'QMessageBox',Box)
    monkeypatch.setattr(bp,'run_assisted_ingestion',lambda *a,**k:pytest.fail('finish notice launched Assisted'))
    monkeypatch.setattr(bp,'open_in_default_browser',lambda *a,**k:pytest.fail('finish notice opened browser'))
    from sponsorscout.tests._qt_support import notice_widget,dispose
    parent=notice_widget(path)
    navigation=Signal();parent.assisted_tab_requested.connect(navigation.emit)
    tools.ToolsTab._offer_blocked_portals(parent,summary)
    assert len(boxes)==int(bool(expected))
    assert navigation.count==0
    assert len(db.list_blocked_portals(path))==expected
    dispose(parent)
