"""Regression for HTTP-200 client shells + readable explicit browser JDs.

Only local/synthetic portals. Actual Chromium cases are opt-in, headed under
Xvfb/offscreen Qt; no live CAPTCHA, vendor or Windows certification claims.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
import csv,hashlib,json,os,threading,time
import pytest
from sponsorscout.core import blocked_portals as bp,persistence,assisted_resume as resume
from sponsorscout.core.session_details import SessionDetailEnricher,parse_detail,FLOW_REVISION
from sponsorscout.scanning.response_evidence import challenge_reason
from sponsorscout.scanning.detail_state import DetailRequestState
from sponsorscout.scanning.career.career_scanner import ProductionScannerConfig,CareerPortalScanner
from sponsorscout.db import database as db
from sponsorscout.tests._qt_support import application,wait_until
from sponsorscout.tests.test_responsiveness_2026_10_24 import window,heartbeat,max_gap
from sponsorscout.tests.test_assisted_support_2026_10_24 import BASE,POS,NEG,SILENT,html_ld,job,scanner,URL

SHELL='<html><head><title>Client Challenge</title><script>window.localChallenge=true;</script></head><body></body></html>'


@pytest.mark.parametrize('html,blocked',[
    (SHELL,True),
    ('<title>Browser Challenge</title><main>Checking access</main>',True),
    ('<title>Javascript Challenge</title>',True),
    ('<title>Loading</title><main>Loading...</main>',False),
    ('<title>Backend Engineer</title><script src="/client-challenge-sdk.js"></script>'+html_ld(POS),False),
    ('<title>Client Challenge</title><h1>Backend Engineer</h1><main>'+SILENT+'</main>',False),
    ('<title>Client Challenge</title>'+html_ld(POS),False),
    ('<title>Service unavailable</title><p>Please retry later</p>',False),
])
def test_client_interstitial_not_passive_or_real_posting(html,blocked):
    assert bool(challenge_reason(html,status=200))is blocked
    if html==SHELL:assert 'not confirmed'in challenge_reason(html,status=200)


def test_single_jobposting_for_a_different_job_is_not_primary():
    result=parse_detail(html_ld(POS,'https://fixture.invalid/jobs/999999-other.html'),URL,URL,scanner())
    assert result.status=='empty'and not result.text and'identity unproven'in result.reason


def fake_reader(monkeypatch,*,failures=0,config=None,redirect=False):
    config=config or ProductionScannerConfig()
    state=DetailRequestState();requests=[];visits=[];captured=[]
    s=SimpleNamespace(config=config,_detail_state=state,store_captured_jd=lambda u,t:captured.append((u,t)))
    class NavigationError(Exception):pass
    class Page:
        url='about:blank';frames=[];closed=False
        def is_closed(self):return self.closed
        def goto(self,url,**kw):
            visits.append(url)
            if len(visits)<=failures:raise NavigationError('controlled original navigation message')
            self.url='https://fixture.invalid/jobs/999999-previous.html'if redirect else url
            return SimpleNamespace(status=200)
        def title(self):return'Synthetic Engineer'
        def content(self):return'<title>Synthetic Engineer</title><h1>Synthetic Engineer</h1><section class="job-description">'+POS+'</section>'
        def close(self):self.closed=True
    class Request:
        def get(self,url,**kw):requests.append(url);return SimpleNamespace(status=200,url=url,text=lambda:SHELL,dispose=lambda:None)
    page=Page();context=SimpleNamespace(request=Request(),new_page=lambda:page)
    monkeypatch.setattr('sponsorscout.core.session_details.time',SimpleNamespace(sleep=lambda delay:None,monotonic=time.monotonic))
    return SessionDetailEnricher(context,s,'scope'),state,requests,visits,page


def test_browser_primary_transport_goes_beyond_twelve_and_stops_redundant_http(monkeypatch):
    reader,state,requests,visits,page=fake_reader(monkeypatch)
    results=[reader.read(f'https://fixture.invalid/jobs/{8100000+n}-engineer.html')for n in range(580)]
    assert all(r.status=='readable'for r in results)
    # Phase B adds one bounded HTTP re-probe after 5 successful browser reads;
    # the blocked fixture keeps browser preference and adds one shared request.
    assert len(requests)==2 and requests[0].endswith('8100000-engineer.html')and requests[1].endswith('8100004-engineer.html')
    assert len(visits)==580
    assert reader.counts['http_blocked']==1 and reader.counts['browser_preferred']==579
    assert reader.counts['http_reprobe']==1 and reader.counts['http_reprobe_cleared']==0
    assert state.total==582 and ProductionScannerConfig.DETAIL_BROWSER_FALLBACK_PER_COMPANY==12
    assert reader.scanner.config.DETAIL_BROWSER_FALLBACK_PER_COMPANY==12
    reader.close()


def test_every_retry_is_budgeted_and_original_exception_message_is_retained(monkeypatch):
    reader,state,requests,visits,page=fake_reader(monkeypatch,failures=1)
    recovered=reader.read(URL)
    assert recovered.status=='readable'and'controlled original navigation message'in recovered.error
    assert'recovered navigation'in recovered.phase
    assert reader.counts['browser']==2 and reader.counts['browser_retries']==1 and state.total==3
    samples=reader.report()[1]
    assert any('controlled original navigation message'in s['error']and s['phase']=='navigation'for s in samples)
    assert all(s['requested_url']==URL for s in samples)


def test_two_failed_navigations_never_read_stale_previous_jd(monkeypatch):
    reader,state,requests,visits,page=fake_reader(monkeypatch,failures=2)
    result=reader.read(URL)
    assert result.status=='failed'and not result.text and'controlled original navigation message'in result.error
    assert len(visits)==2 and reader.counts['browser']==2


def test_redirect_to_different_posting_is_unread_not_previous_job_support(monkeypatch):
    reader,state,requests,visits,page=fake_reader(monkeypatch,redirect=True)
    result=reader.read(URL)
    assert result.status=='empty'and not result.text and'identity unproven'in result.reason


def test_shared_request_cap_defers_browser_truthfully(monkeypatch):
    config=ProductionScannerConfig();config.MAX_DETAIL_SCAN_PER_COMPANY=1
    reader,state,requests,visits,page=fake_reader(monkeypatch,config=config)
    result=reader.read(URL)
    assert result.status=='budget_skipped'and'per-target request cap'in result.reason
    assert result.http_title=='Client Challenge'and result.http_code==200
    assert reader.counts['http_blocked']==1 and not visits and state.total==1


def test_user_closed_detail_tab_is_not_reopened_for_each_job(monkeypatch):
    reader,state,requests,visits,page=fake_reader(monkeypatch)
    assert reader.read(URL).status=='readable';page.close()
    assert reader.read('https://fixture.invalid/jobs/222222-engineer.html').status=='failed'
    assert reader.read('https://fixture.invalid/jobs/333333-engineer.html').status=='budget_skipped'
    assert len(visits)==1 and reader.browser_closed


def test_regular_detail_budget_remains_twelve_and_retry_cannot_evade_limit():
    state=DetailRequestState();cfg=ProductionScannerConfig()
    for n in range(12):
        u=f'https://fixture.invalid/jobs/{900000+n}-engineer.html';state.register(u,'scope')
        assert state.reserve(u,cfg,browser=True)
    extra='https://fixture.invalid/jobs/999999-engineer.html';state.register(extra,'scope')
    assert not state.reserve(extra,cfg,browser=True)
    assert not state.reserve(u,cfg,browser=True,retry=True)
    assert state.total==12


def test_shared_time_limit_still_defers_without_any_request(monkeypatch):
    reader,state,requests,visits,page=fake_reader(monkeypatch)
    state.register(URL,'scope');scope=state._scope(URL)
    scope['started']=time.monotonic()-reader.policy.DETAIL_SCAN_TIME_BUDGET_SEC-1
    result=reader.read(URL)
    assert result.status=='budget_skipped'and'time budget exhausted'in result.reason
    assert not requests and not visits and state.total==0


@pytest.fixture
def saved(tmp_path,monkeypatch):
    from sponsorscout import paths
    out=tmp_path/'exports';out.mkdir();monkeypatch.setattr(paths,'SCAN_OUTPUT_DIR',out)
    p=tmp_path/'saved.db';db.initialize(p);conn=db.get_connection(p)
    records=[job(url=f'https://fixture.invalid/jobs/{7300000+n}-engineer.html')for n in range(3)]
    for record in records:persistence.upsert_job(conn,record)
    rows=[dict(r,jd_read_status='empty')for r in records]
    url='https://fixture.invalid/careers?country=DE#engineering'
    yield conn,url,rows,out
    conn.close()


def test_manifest_scope_database_and_filters_are_not_widened(saved,tmp_path):
    conn,url,rows,out=saved
    batch=resume.save_batch(conn,url,'Fixture',rows,run_id='original_assisted',listing_completed=True,pages=1)
    assert resume.load_batch(conn,url,'Fixture')['rows']==rows
    for wrong in(url.replace('DE','NL'),url.replace('#engineering','#sales')):
        with pytest.raises(ValueError,match='No saved JD batch'):resume.load_batch(conn,wrong,'Fixture')
    p=tmp_path/'other.db';db.initialize(p);other=db.get_connection(p)
    with pytest.raises(ValueError,match='No saved JD batch'):resume.load_batch(other,url,'Fixture')
    other.close()


def test_resume_never_recreates_deleted_or_reactivates_excluded_jobs(saved):
    conn,url,rows,out=saved
    batch=resume.save_batch(conn,url,'Fixture',rows,run_id='original_assisted',listing_completed=True,pages=1)
    conn.execute('DELETE FROM jobs WHERE url=?',(rows[0]['url'],))
    conn.execute('UPDATE jobs SET verified_active=0,is_expired=1 WHERE url=?',(rows[1]['url'],));conn.commit()
    pairs=resume.existing_records(conn,batch)
    assert [record is None for record,row in pairs]==[True,True,False]
    assert pairs[0][1]['jd_read_status']==pairs[1][1]['jd_read_status']=='protected'
    assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==2


def test_old_schema_three_batch_comes_from_exact_run_csv_not_company_sweep(saved):
    conn,url,rows,out=saved;run='20261025T102226870626_assisted'
    for row in rows:row['support_evidence_type']='listing_card_sentence; jd_read_status=empty'
    fields=list(rows[0]);source=out/(run+'_career_jobs.csv')
    with source.open('w',encoding='utf-8-sig',newline='')as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    conn.execute("INSERT INTO scan_log(run_id,scanner,seed_name,seed_url,status,diagnostics) VALUES(?,'assisted_browser','Fixture',?,'partial',?)",(run,url,'Harvested 3 jobs across 1 page(s); listing traversal complete, JD coverage incomplete: 0/3 readable'))
    conn.commit();before=source.read_bytes()
    batch=resume.load_batch(conn,url,'Fixture')
    assert batch['listing_completed']and batch['pages']==1 and len(batch['rows'])==3
    assert source.read_bytes()==before and not resume._path(conn,url).exists()


def test_invalid_manifest_fails_closed_without_falling_back_to_broader_jobs(saved):
    conn,url,rows,out=saved
    resume.save_batch(conn,url,'Fixture',rows,run_id='original_assisted',listing_completed=True,pages=1)
    path=resume._path(conn,url);data=json.loads(path.read_text());data['portal_key']='https://other.invalid/'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='does not match'):resume.load_batch(conn,url,'Fixture')


def test_failed_atomic_checkpoint_keeps_previous_batch(saved,monkeypatch):
    conn,url,rows,out=saved
    resume.save_batch(conn,url,'Fixture',rows,run_id='first_assisted',listing_completed=True,pages=1)
    path=resume._path(conn,url);before=path.read_bytes()
    def fail(*a):raise OSError('controlled atomic replace failure')
    monkeypatch.setattr(resume.os,'replace',fail)
    with pytest.raises(OSError,match='atomic replace failure'):
        resume.save_batch(conn,url,'Fixture',rows,run_id='second_assisted',listing_completed=False,pages=1)
    assert path.read_bytes()==before and not list(path.parent.glob('*.tmp'))


@pytest.mark.skipif(os.environ.get('SPONSORSCOUT_TEST_BROWSER')!='1' or (not os.environ.get('DISPLAY')and os.name!='nt'),reason='Opt-in real Chromium local fixture')
@pytest.mark.parametrize('scenario',['large580','first_error','resume','legacy_resume','reblock','client_clear','cancel','window_close','detail_close','redirect'])
def test_actual_http_challenge_browser_transport_and_explicit_resume(window,tmp_path,monkeypatch,heartbeat,scenario,request):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from urllib.parse import urlsplit,parse_qs
    from playwright.sync_api import BrowserType,BrowserContext,Page,Error
    from sponsorscout.ui.tabs import assisted_ingestion as ui
    from sponsorscout import paths
    import html as hm
    n=580 if scenario=='large580'else 24 if scenario in('resume','legacy_resume','cancel','window_close','detail_close')else 16 if scenario=='first_error'else 3
    pages=[];launches=[];calls=[];notices=[];injected=[]
    def listing(number):
        cards=''.join(f'<article class="job-card"><h2><a href="/jobs/{8200000+i}-backend-engineer.html">Backend Engineer {i}</a></h2><p>Location: Berlin, Germany</p></article>'for i in range((number-1)*10,min(number*10,n)))
        maximum=(n+9)//10;nav=''.join(f'<a href="?p={value-1}">{value}</a>'for value in range(1,maximum+1))
        if number<maximum:nav+=f'<a aria-label="Next" href="?p={number}">Next</a>'
        return f'<title>Engineering careers</title><h1>{n} job results</h1>'+cards+'<nav class="pagination">'+nav+'</nav>'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            parsed=urlsplit(self.path);navigation=self.headers.get('Sec-Fetch-Dest')=='document'
            calls.append((parsed.path,navigation,self.headers.get('Cookie','')))
            status=200
            if parsed.path.startswith('/jobs/'):
                index=int(parsed.path.split('/')[-1].split('-')[0])-8200000
                if not navigation:body=SHELL
                elif scenario=='reblock':body='<title>Client Challenge</title><h1>Client Challenge</h1>'
                elif scenario=='client_clear'and'detail-cleared=1'not in self.headers.get('Cookie',''):
                    content=html_ld((POS,NEG,SILENT)[index%3],base+parsed.path)+'<section class="job-description">'+(POS,NEG,SILENT)[index%3]+'</section>'
                    action='document.cookie="detail-cleared=1; path=/";document.title="Backend Engineer";document.body.innerHTML='+json.dumps(content)
                    body='<title>Client Challenge</title><button id="detail-clear" onclick="'+hm.escape(action,quote=True)+'">Continue local fixture</button>'
                elif scenario=='redirect'and index==1:
                    self.send_response(302);self.send_header('Location','/jobs/8200000-backend-engineer.html');self.send_header('Content-Length','0');self.end_headers();return
                else:
                    text=(POS,NEG,SILENT)[index%3]
                    body=html_ld(text,base+parsed.path)+'<section class="job-description">'+text+'</section>'
            else:
                number=int(parse_qs(parsed.query).get('p',['0'])[0])+1
                if 'fixture-session=cleared'not in self.headers.get('Cookie',''):
                    action='document.cookie="fixture-session=cleared; path=/";document.title="Engineering careers";document.body.innerHTML='+json.dumps(listing(1))
                    body='<title>Just a moment...</title><h1>Verify you are human</h1><div id="cf-challenge-running"></div><button id="local-clear" onclick="'+hm.escape(action,quote=True)+'">Continue local fixture</button>'
                else:body=listing(number)
            raw=('<html><body>'+body+'</body></html>').encode();self.send_response(status)
            self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}';url=base+'/careers?p=0';out=tmp_path/'exports';out.mkdir()
    monkeypatch.setattr(paths,'SCAN_OUTPUT_DIR',out)
    monkeypatch.setattr(paths,'ensure_scan_output_dir',lambda:out)
    launch0=BrowserType.launch;page0=BrowserContext.new_page;goto0=Page.goto;wait0=Page.wait_for_timeout
    monkeypatch.setattr(BrowserType,'launch',lambda self,*a,**k:(launches.append(k),launch0(self,*a,**k))[1])
    def page_new(self,*a,**kw):
        def guard(route):
            assert urlsplit(route.request.url).hostname in('localhost','127.0.0.1')
            route.continue_()
        self.route('**/*',guard);page=page0(self,*a,**kw);pages.append(page);return page
    monkeypatch.setattr(BrowserContext,'new_page',page_new)
    def goto(page,target,*a,**kw):
        if scenario=='first_error'and '/jobs/'in target and not injected:
            injected.append(target);raise Error('controlled transient navigation failure with original message')
        return goto0(page,target,*a,**kw)
    monkeypatch.setattr(Page,'goto',goto)
    monkeypatch.setattr(Page,'wait_for_timeout',lambda self,ms:wait0(self,min(ms,5)))
    class Clock:
        def __getattr__(self,name):return getattr(time,name)
        def sleep(self,seconds):time.sleep(min(seconds,.002))
    monkeypatch.setattr(bp,'time',Clock());monkeypatch.setattr('sponsorscout.core.session_details.time',Clock())
    original=bp.run_assisted_ingestion
    def engine(*a,**kw):
        progress=kw.get('progress_callback')
        def action(message):
            if progress:progress(message)
            if 'Action needed'in message and'job-detail'not in message:pages[0].locator('#local-clear').click()
            if scenario=='client_clear'and'Action needed'in message and'job-detail'in message:pages[1 if len(pages)>1 else 0].locator('#detail-clear').click()
            if scenario=='detail_close'and message.startswith('Reading full job descriptions 4/'):
                # Phase B may open up to 3 parallel detail tabs; close the
                # first dedicated detail tab, not the listing page or a tab
                # created later by lookahead prefetch.
                pages[1 if len(pages)>1 else 0].close()
        kw['progress_callback']=action;kw['clear_wait_s']=.2
        return original(*a,**kw)
    monkeypatch.setattr(bp,'run_assisted_ingestion',engine)
    monkeypatch.setattr(ui.QMessageBox,'information',lambda *a,**kw:notices.append(a))
    if scenario in('resume','legacy_resume'):monkeypatch.setattr(ProductionScannerConfig,'MAX_DETAIL_SCAN_PER_COMPANY',15)
    tab=window.assisted_tab;window.tabs.setCurrentWidget(tab);tab.url_edit.setText(url);tab.name_edit.setText('Fixture')
    try:
        tab._start_ingestion();worker=tab._worker
        if scenario in('cancel','window_close'):
            wait_until(lambda:sum(path.startswith('/jobs/')and nav for path,nav,cookie in calls)>=3,40)
            if scenario=='cancel':tab.stop_btn.click()
            else:
                before_close=time.monotonic();assert not window.close();assert time.monotonic()-before_close<.1
        wait_until(lambda:tab._worker is None,300 if scenario=='large580'else 100)
        report=dict(worker.report)
        assert report['harvested']==n and report['listing_completed']and len(notices)==(0 if scenario=='window_close'else 1)
        assert report['support_flow_revision']==FLOW_REVISION and report['export_schema']==5
        assert launches[0]['headless']is False and'--disable-gpu'in launches[0]['args']
        assert len(launches)==1 and len(pages)<=4  # listing page + up to 3 Phase B detail tabs
        if scenario in('large580','first_error','client_clear'):
            assert report['completed']and report['jd_coverage']['readable']==n
            assert report['session_detail_activity']['http']==1
            assert report['session_detail_activity']['browser']==n+(1 if scenario=='first_error'else 0)
            if scenario=='large580':assert report['pages_visited']==58
            if scenario=='first_error':assert report['session_detail_activity']['browser_retries']==1
        if scenario in('resume','legacy_resume'):
            assert not report['completed']and report['jd_coverage']['readable']==14 and report['jd_coverage']['budget_skipped']==10
            conn=db.get_connection(window.db_path)
            old_runs=[dict(r)for r in conn.execute('SELECT * FROM scan_runs ORDER BY started_at')]
            old_run=old_runs[-1]['run_id'];source=out/(old_run+'_career_jobs.csv');source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
            stamps={r['url']:r['support_evidence_type']for r in conn.execute("SELECT url,support_evidence_type FROM jobs WHERE support_evidence_type LIKE '%jd_sha256=%'")}
            if scenario=='legacy_resume':resume._path(conn,url).unlink()
            conn.close();before_calls=len(calls);monkeypatch.setattr(ProductionScannerConfig,'MAX_DETAIL_SCAN_PER_COMPANY',1500)
            tab.resume_btn.click();second=tab._worker
            wait_until(lambda:tab._worker is None,100);final=dict(second.report)
            assert final['resumed_details']and final['completed']and final['jd_coverage']['readable']==n
            assert final['session_detail_activity']['reused']==14 and final['session_detail_activity']['browser']==10
            assert not any(not path.startswith('/jobs/')for path,nav,cookie in calls[before_calls:])
            assert second.count==0 and final['duplicates']==n and len(notices)==2
            assert hashlib.sha256(source.read_bytes()).hexdigest()==source_hash
            conn=db.get_connection(window.db_path)
            assert [dict(r)for r in conn.execute('SELECT * FROM scan_runs WHERE run_id=?',(old_run,))]==old_runs
            for row in conn.execute('SELECT url,support_evidence_type FROM jobs'):
                if row['url']in stamps:
                    original_stamp=stamps[row['url']].split('jd_captured_at=')[1].split(';')[0]
                    assert 'jd_captured_at='+original_stamp in row['support_evidence_type']
            conn.close();report=final
        if scenario=='reblock':assert report['jd_coverage']['blocked']==1 and report['jd_coverage']['budget_skipped']==2
        if scenario in('cancel','window_close'):assert report['cancelled']and not report['completed']and report['jd_coverage']['readable']<n
        if scenario=='cancel':
            before_calls=len(calls);tab.resume_btn.click();second=tab._worker
            wait_until(lambda:tab._worker is None,100)
            assert second.report['completed']and second.report['listing_completed']and second.count==0
            assert not any(not path.startswith('/jobs/')for path,nav,cookie in calls[before_calls:])
            report=dict(second.report)
        if scenario=='detail_close':assert not report['completed']and report['jd_coverage']['failed']==1 and report['jd_coverage']['budget_skipped']==20
        if scenario=='redirect':assert report['jd_coverage']['empty']==1 and not report['completed']
        conn=db.get_connection(window.db_path);rows=[dict(r)for r in conn.execute('SELECT * FROM jobs ORDER BY url')];conn.close()
        assert len(rows)==n
        if report['completed']:
            assert [(r['visa_sponsorship'],r['relocation_support'],r['eu_blue_card_verdict'])for r in rows]==[('Y','Y','Yes')if i%3==0 else('N','N','No')if i%3==1 else('Unknown','Unknown','Unknown')for i in range(n)]
            assert len(db.search_jobs(window.db_path,company='Fixture',sponsorship_only=True))==(n+2)//3
        if scenario=='redirect':assert rows[1]['visa_sponsorship']=='Unknown'
        exports=sorted(out.glob('*_career_jobs.csv'),key=lambda p:p.stat().st_mtime_ns)
        with exports[-1].open(encoding='utf-8-sig',newline='')as f:export=list(csv.DictReader(f))
        assert len(export)==n and exports[-1].read_bytes().startswith(b'\xef\xbb\xbf')
        assert 'jd_phase'in export[0]and'jd_http_title'in export[0]
        if scenario=='first_error':assert'controlled transient navigation failure'in export[0]['jd_error']and'recovered navigation'in export[0]['jd_phase']
        assert [(r['visa_sponsorship'],r['relocation_support'],r['eu_blue_card_verdict'])for r in export]==[(r['visa_sponsorship'],r['relocation_support'],r['eu_blue_card_verdict'])for r in rows]
        assert max_gap(heartbeat)<.3
        request.node.user_properties.append(('jd_coverage',json.dumps(report['jd_coverage'])))
        request.node.user_properties.append(('detail_activity',json.dumps(report['session_detail_activity'])))
    finally:
        if tab._worker is not None:tab.cancel_ingestion();wait_until(lambda:tab._worker is None,25)
        server.shutdown();server.server_close();thread.join(2)
