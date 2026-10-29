"""Real Qt main-window scheduling, transport, contention and Assisted evidence.

Offline only. Real local HTTP is parsed by production scanner/pipeline workers;
no adapter invents a blocked result. Large browser traversal has an opt-in real
Chromium fixture below. These developer tests are never called by Scan.
"""
from __future__ import annotations
import csv,json,os,threading,time,sqlite3
from pathlib import Path
from types import SimpleNamespace
import pytest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from sponsorscout.db import database as db
from sponsorscout.core import blocked_portals as bp,persistence
from sponsorscout.core.db_tasks import DatabaseReadTask,current_database_task
from sponsorscout.ui.async_load import AsyncLoader
from sponsorscout.tests._qt_support import application,wait_until,wait_loaders
from sponsorscout.tests.test_post_delivery_2026_10_24 import local_board,write_csv,JD,LINKS,WALL


@pytest.fixture
def window(tmp_path,monkeypatch):
    from sponsorscout.ui.app import SponsorScoutApp
    from PySide6.QtCore import QCoreApplication,QEvent
    application();monkeypatch.setattr(SponsorScoutApp,'_check_first_run',lambda self:None)
    path=tmp_path/'ui.db';w=SponsorScoutApp(str(path));w.tabs.setCurrentWidget(w.tools_tab)
    wait_loaders(w)
    yield w
    # Enter the production closing state BEFORE draining queued snapshots;
    # otherwise a queued pending result can legitimately start another worker.
    w.close()
    wait_until(lambda:w.assisted_tab._worker is None and w.insights_tab._insights_worker is None,20)
    wait_until(lambda:bool(w.close()),20)
    w.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);application().processEvents()


@pytest.fixture
def heartbeat(request):
    from PySide6.QtCore import QTimer
    application();ticks=[];timer=QTimer();timer.setInterval(10);timer.timeout.connect(lambda:ticks.append(time.monotonic()));timer.start()
    yield ticks
    timer.stop()
    request.node.user_properties.append(("maximum_gui_heartbeat_gap_seconds", max_gap(ticks)))


def max_gap(ticks):return max([b-a for a,b in zip(ticks,ticks[1:])]or[0])


class Notice:
    Ok,ActionRole=1,2
    records=[]
    def __init__(self,parent):self.text='';self.created=time.monotonic();self.records.append(self)
    def setWindowTitle(self,*a):pass
    def setText(self,text):self.text=text
    def setStandardButtons(self,*a):pass
    def addButton(self,*a):return object()
    def setDefaultButton(self,*a):pass
    def exec(self):pass
    def clickedButton(self):return None


# 2026-10-28 (false bot-block issue): 'blocked' walls only ONE job-detail URL
# on a listing that kept 10 rows, so the portal is no longer queued - the
# "nothing added" case is what the notice must handle by staying silent.
# 'listingwall' is a proven listing-level wall with zero rows: that still queues.
@pytest.mark.parametrize('mode,ingested,queue',[('blocked',10,0),('listingwall',0,1)])
def test_real_http_pipeline_full_window_notice_does_not_wait_for_hidden_reads(window,local_board,tmp_path,monkeypatch,heartbeat,mode,ingested,queue):
    from sponsorscout.application import seed_manager
    from sponsorscout.scanning import pipeline
    from sponsorscout.scanning.career import career_scanner as career
    from sponsorscout.ui.tabs import tools
    local_board['mode']=mode;seed=tmp_path/'seed.csv';out=tmp_path/'exports';out.mkdir()
    write_csv(seed,[dict(name='Generic Board',careers_url=local_board['url'],provider='auto',source_type='recruiter',target_country='Global',scope_policy='seed_url',enabled='true')])
    monkeypatch.setattr(seed_manager,'user_career_path',lambda:seed)
    monkeypatch.setattr(seed_manager,'user_ats_path',lambda:seed)
    monkeypatch.setattr(seed_manager,'ensure_user_seeds',lambda:pytest.fail('runtime seed audit'))
    monkeypatch.setattr(pipeline.paths,'ensure_scan_output_dir',lambda:out)
    monkeypatch.setattr(career,'sync_playwright',None)
    class Cache:
        def __init__(self):self.data={}
        def get(self,url):return self.data.get(url)
        def put(self,url,value):self.data[url]=value
    monkeypatch.setattr(career,'_jd_cache',lambda:Cache())
    monkeypatch.setattr(bp,'run_assisted_ingestion',lambda *a,**k:pytest.fail('regular auto-assisted'))
    monkeypatch.setattr(bp,'open_in_default_browser',lambda *a,**k:pytest.fail('auto external'))
    Notice.records=[];monkeypatch.setattr(tools,'QMessageBox',Notice)
    hidden=[]
    monkeypatch.setattr(window.search_tab,'run_search',lambda:hidden.append('search'))
    monkeypatch.setattr(window.insights_tab,'refresh',lambda *a,**k:hidden.append('charts'))
    captured=[];window.tools_tab.coordinator.finished.connect(lambda value:captured.append(value))
    window.tools_tab.scan_started.emit()
    assert window.tools_tab.coordinator.start('custom',scan_scope={'run_ats':False,'run_career':True,'career':['Generic Board']})
    wait_until(lambda:bool(captured),15)
    wait_until(lambda:bool(captured))
    result=captured[0]
    assert result['status']=='partial'and result['ingested']==ingested
    assert len(db.list_blocked_portals(window.db_path))==queue
    assert not hidden and len(Notice.records)==queue
    if queue:
        assert result['queue_persisted']is True
        assert 'have been added'in Notice.records[0].text
    window.tools_tab._offer_blocked_portals(result)
    assert len(Notice.records)==queue
    assert not window.assisted_tab._scan_running and window.assisted_tab._worker is None
    assert max_gap(heartbeat)<.25


def test_actual_finish_notice_has_no_gui_queue_write_and_retry_stays_responsive(window,monkeypatch,heartbeat):
    from sponsorscout.ui.tabs import tools
    Notice.records=[];monkeypatch.setattr(tools,'QMessageBox',Notice)
    url='https://fixture.invalid/careers?country=NL#scope';portal=dict(url=url,seed_name='Scoped Fixture',phase='career',reason='blocked access: HTTP403 denied')
    db.enqueue_blocked_portals(window.db_path,[portal])
    locker=sqlite3.connect(window.db_path,check_same_thread=False);locker.execute('BEGIN IMMEDIATE');locker.execute("UPDATE blocked_portals SET result='locked'")
    release=threading.Timer(.65,lambda:(locker.commit(),locker.close()));release.start()
    try:
        a=time.monotonic();window.tools_tab._offer_blocked_portals({'run_id':'persisted','blocked_portals':[portal],'queue_persisted':True})
        assert time.monotonic()-a<.1 and len(Notice.records)==1
        a=time.monotonic();window.tools_tab._offer_blocked_portals({'run_id':'retry','blocked_portals':[portal],'queue_persisted':False})
        assert time.monotonic()-a<.1
        wait_until(lambda:len(Notice.records)==2)
        assert 'have been added'in Notice.records[1].text and max_gap(heartbeat)<.2
    finally:release.join()
    assert len(db.list_blocked_portals(window.db_path))==1


def test_sql_reads_and_insights_callbacks_are_worker_owned(window,monkeypatch,heartbeat):
    from sponsorscout.insights import queries
    main=threading.get_ident();calls=[]
    for module,name in [(db,'get_dashboard_stats'),(db,'search_jobs'),(db,'get_distinct_job_countries'),(db,'list_applications'),
                        (queries,'get_insights_coverage'),(queries,'has_pending_insights'),(db,'list_blocked_portals')]:
        original=getattr(module,name)
        def wrapped(*a,_fn=original,_name=name,**k):
            calls.append((_name,threading.get_ident(),current_database_task()is not None))
            time.sleep(.025)
            return _fn(*a,**k)
        monkeypatch.setattr(module,name,wrapped)
    window.dashboard_tab.refresh();window.search_tab.populate_static_filters(refresh_search=True)
    window.applications_tab.load_applications();window.assisted_tab.refresh_queue()
    window.insights_tab.refresh(auto_backfill=False);window.insights_tab._on_insights_progress(1,1,'saved')
    window.insights_tab.request_auto_refresh()
    wait_loaders(window)
    assert calls and all(thread!=main and scoped for _name,thread,scoped in calls)
    assert max_gap(heartbeat)<.2


def test_slow_background_chart_does_not_delay_zero_change_finish(window,monkeypatch,heartbeat):
    from sponsorscout.insights import queries
    from sponsorscout.ui.tabs import tools
    entered=threading.Event();release=threading.Event();original=queries.get_insights_coverage
    def slow(*a,**k):entered.set();release.wait(3);return original(*a,**k)
    monkeypatch.setattr(queries,'get_insights_coverage',slow)
    window.insights_tab.refresh(auto_backfill=False);wait_until(entered.is_set)
    Notice.records=[];monkeypatch.setattr(tools,'QMessageBox',Notice)
    portal=dict(url='https://fixture.invalid/board',seed_name='Fixture',phase='career',reason='blocked access: denial')
    db.enqueue_blocked_portals(window.db_path,[portal])
    try:
        a=time.monotonic();window.tools_tab._on_scan_finished(dict(run_id='zero',status='partial',ingested=0,duplicates=0,merged=0,blocked_portals=[portal],queue_persisted=True))
        assert len(Notice.records)==1 and time.monotonic()-a<.15
        wait_until(lambda:len(heartbeat)>10)
        assert max_gap(heartbeat)<.2
    finally:release.set()
    wait_loaders(window)


def test_loader_discards_stale_result_and_keeps_all_actions(window):
    read=AsyncLoader(window,name='stale fixture');values=[];gate=threading.Event();started=threading.Event()
    def old():started.set();gate.wait(3);return'old'
    read.request(old,values.append);wait_until(started.is_set)
    read.request(lambda:'new',values.append);gate.set();wait_until(lambda:not read.busy)
    assert values==['new']
    actions=AsyncLoader(window,read_only=False);values=[];gate.clear();started.clear()
    actions.request(old,values.append);wait_until(started.is_set)
    actions.request(lambda:'second',values.append);actions.request(lambda:'third',values.append)
    gate.set();wait_until(lambda:not actions.busy)
    assert values==['old','second','third']


def test_background_read_scope_is_readonly_closes_and_cancels(tmp_path):
    path=tmp_path/'read.db';db.initialize(path)
    task=DatabaseReadTask()
    with task:
        conn=db.get_connection(path)
        assert conn.execute('PRAGMA query_only').fetchone()[0]==1
        with pytest.raises(sqlite3.OperationalError):conn.execute("INSERT INTO jobs(url)VALUES('https://fixture.invalid/job')")
        task.cancel()
        with pytest.raises(InterruptedError):db.get_connection(path)
    with pytest.raises(sqlite3.ProgrammingError):conn.execute('SELECT 1')


def test_assisted_registration_wait_cancel_outcome_with_full_window(window,monkeypatch,heartbeat):
    from sponsorscout.ui.tabs import assisted_ingestion as ui
    main=threading.get_ident();writes=[];wait=threading.Event()
    for name in ('enqueue_blocked_portals','update_blocked_portal'):
        original=getattr(db,name)
        def write(*a,_fn=original,**k):writes.append(threading.get_ident());time.sleep(.04);return _fn(*a,**k)
        monkeypatch.setattr(db,name,write)
    def engine(url,name,conn,**kw):
        kw['progress_callback']('Action needed: local verification fixture')
        wait.set()
        while not kw['cancel_event'].is_set():time.sleep(.02)
        bp.LAST_INGESTION_REPORT.update(cancelled=True,completed=False,stop_reason='cancelled by user',duplicates=0,pages_visited=0)
        return 0
    monkeypatch.setattr(bp,'run_assisted_ingestion',engine)
    notices=[];monkeypatch.setattr(ui.QMessageBox,'information',lambda *a,**k:notices.append(a))
    tab=window.assisted_tab;window.tabs.setCurrentWidget(tab)
    tab.url_edit.setText('https://fixture.invalid/board?country=NL');tab.name_edit.setText('Typed name')
    a=time.monotonic();tab._start_ingestion();assert time.monotonic()-a<.1
    first=tab._worker;tab._start_ingestion();assert tab._worker is first
    wait_until(wait.is_set);assert not window.tools_tab.scan_btn.isEnabled()
    a=time.monotonic();tab.cancel_ingestion();assert time.monotonic()-a<.1
    wait_until(lambda:tab._worker is None)
    assert writes and all(tid!=main for tid in writes)
    assert max_gap(heartbeat)<.2 and len(notices)==1 and tab.launch_btn.isEnabled()
    rows=db.list_blocked_portals(window.db_path,include_resolved=True)
    assert len(rows)==1 and rows[0]['status']=='partial'


class Page:
    url='https://fixture.invalid/jobs?page=13'
    frames=[]
    def __init__(self,html,title):self.html,self.name=html,title
    def content(self):return self.html
    def title(self):return self.name
    def query_selector_all(self,*a):return[]


@pytest.mark.parametrize('html,title,blocked',[(WALL,'Just a moment...',True),
    ('<h1>Backend Engineer</h1><main>'+JD+'</main><iframe src="https://challenges.cloudflare.com/passive"></iframe>','Backend Engineer',False),
    ('<script>perimeterx</script>'+LINKS,'Search jobs',False),
    ('<title>Search jobs</title><p>Loading…</p>','Search jobs',False)])
def test_assisted_uses_shared_foreground_passive_authority(html,title,blocked):
    assert bp._page_is_challenge(Page(html,title))is blocked


def test_assisted_failure_capture_has_actual_cue_url_scope_status_and_total(monkeypatch):
    page=Page('<h1>587 job results</h1>'+WALL,'Just a moment...');page._assisted_nav_status=403
    monkeypatch.setattr(bp,'LAST_INGESTION_REPORT',{})
    evidence=bp._record_assisted_evidence(page,'waiting_for_clearance',13,count=0)
    assert evidence['http_status']==403 and evidence['stated_total']==587 and evidence['page']==13
    assert evidence['url']==page.url and evidence['parsed_rows']==0
    assert 'Cloudflare challenge'in evidence['reason']and evidence['title']=='Just a moment...'
    for n in range(30):bp._record_assisted_evidence(page,'fixture',n)
    assert len(bp.LAST_INGESTION_REPORT['response_evidence'])==16
    assert 'secret'not in bp._redact_evidence_url(page.url+'&access_token=secret')


def test_no_listing_title_alone_is_clearance_and_lone_nav_is_not_job():
    assert not bp._page_has_listing(Page('<h1>Search results</h1><a href="/jobs">Jobs</a>','Search jobs'))


@pytest.mark.skipif(os.environ.get('SPONSORSCOUT_TEST_BROWSER')!='1' or (not os.environ.get('DISPLAY') and os.name!='nt'),reason='Developer-only real visible Chromium/Xvfb fixture')
@pytest.mark.parametrize('scenario',['full','passive13','blocked13','clear13','cancel13','clear_initial'])
def test_real_assisted_587_board_full_window_and_page13_controls(window,tmp_path,monkeypatch,heartbeat,scenario,request):
    from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
    from urllib.parse import urlsplit,parse_qs
    from playwright.sync_api import BrowserType,BrowserContext,Page as PWPage
    from sponsorscout.ui.tabs import assisted_ingestion as ui
    from sponsorscout.scanning.career.career_scanner import CareerPortalScanner
    import html as html_module
    def listing(number):
        cards=''.join(f'<article class="job-card"><h2><a href="/jobs/backend-engineer-{1000000+n}">Backend Engineer {n}</a></h2><p>Location: Berlin, Germany</p><p>{JD}</p></article>'for n in range((number-1)*10+1,min(number*10,587)+1))
        start=((number-1)//10)*10+1
        nav=''.join(f'<a data-page="{n}" href="?page={n}">{n}</a>'for n in range(start,min(start+10,60)))
        nav+=f'<a aria-label="Next" href="?page={number+1}">&gt;</a>'if number<59 else'<button aria-label="Next" disabled>Next</button>'
        return '<h1>587 job results</h1>'+cards+'<nav class="pagination">'+nav+'</nav>'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            parsed=urlsplit(self.path)
            if parsed.path.startswith('/jobs/'):
                description=('Responsibilities: Build reliable services and maintain automated tests. Requirements: Collaborate with the engineering team. '*8)
                data={'@type':'JobPosting','url':'http://'+self.headers['Host']+self.path,'title':'Backend Engineer','description':description,'jobLocation':{'address':{'addressCountry':'DE'}}}
                payload=('<title>Backend Engineer</title><script type="application/ld+json">'+json.dumps(data)+'</script>').encode()
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload);return
            number=int(parse_qs(parsed.query).get('page',['1'])[0])
            if (number==13 and scenario in ('blocked13','clear13','cancel13')) or (number==1 and scenario=='clear_initial'):
                body='<title>Just a moment...</title><h1>Verify you are human</h1><div id="cf-challenge-running"></div>'+LINKS
                if scenario in ('clear13','clear_initial'):
                    replacement=json.dumps('<title>Current vacancies</title>'+listing(number))
                    action='document.title="Current vacancies";document.body.innerHTML='+replacement
                    body+='<button id="fixture-clear" onclick="'+html_module.escape(action,quote=True)+'">Continue local fixture</button>'
            else:
                body='<title>Current vacancies</title>'+listing(number)
                if scenario=='passive13'and number==13:body+='<iframe src="https://challenges.cloudflare.com/passive-widget"></iframe>'
            raw=('<html><body>'+body+'</body></html>').encode();self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}';url=base+'/careers?page=1'
    conn=db.get_connection(window.db_path)
    for n in range(1,11):persistence.upsert_job(conn,dict(url=base+f'/jobs/backend-engineer-{1000000+n}',title=f'Backend Engineer {n}',company='Fixture',location='Berlin, Germany',country='Germany',country_source='observed'))
    conn.close()
    original_launch=BrowserType.launch;original_page=BrowserContext.new_page;original_route=BrowserContext.route
    launches=[];pages=[];aborted=[]
    def launch(self,*a,**k):launches.append(k);return original_launch(self,*a,**k)
    def register(self,url,handler,*a,**k):
        def guard(route,request):
            if urlsplit(request.url).hostname not in('localhost','127.0.0.1'):aborted.append(request.url);route.abort()
            else:handler(route)
        return original_route(self,url,guard,*a,**k)
    def new_page(self,*a,**k):
        self.route('**/*',lambda route:route.continue_())
        page=original_page(self,*a,**k);pages.append(page);return page
    monkeypatch.setattr(BrowserType,'launch',launch);monkeypatch.setattr(BrowserContext,'route',register);monkeypatch.setattr(BrowserContext,'new_page',new_page)
    # Accelerate ONLY developer fixture pacing; production delays are untouched.
    real_wait=PWPage.wait_for_timeout
    monkeypatch.setattr(PWPage,'wait_for_timeout',lambda page,ms:real_wait(page,min(ms,5)))
    class Clock:
        def __getattr__(self,key):return getattr(time,key)
        def sleep(self,seconds):time.sleep(min(seconds,.005))
    monkeypatch.setattr(bp,'time',Clock())
    monkeypatch.setattr('sponsorscout.core.session_details.time',Clock())
    original_engine=bp.run_assisted_ingestion
    def engine(*a,**k):
        progress=k.get('progress_callback')
        def notice(message):
            if progress:progress(message)
            if 'Action needed'in message:
                if scenario in ('clear13','clear_initial'):
                    pages[0].locator('#fixture-clear').click()
                    if scenario=='clear_initial':time.sleep(.12)
                elif scenario=='cancel13':k['cancel_event'].set()
        k['progress_callback']=notice;k['clear_wait_s']=.01 if scenario=='clear_initial'else .25
        return original_engine(*a,**k)
    monkeypatch.setattr(bp,'run_assisted_ingestion',engine)
    notices=[];monkeypatch.setattr(ui.QMessageBox,'information',lambda *a,**k:notices.append(a))
    tab=window.assisted_tab;window.tabs.setCurrentWidget(tab);tab.url_edit.setText(url);tab.name_edit.setText('Generic Fixture');tab.source_type_combo.setCurrentIndex(1)
    try:
        tab._start_ingestion();worker=tab._worker
        wait_until(lambda:tab._worker is None,100)
        report=worker.report
        full=scenario in('full','passive13','clear13','clear_initial')
        assert report['harvested']==(587 if full else 120)
        assert report['duplicates']==10 and worker.count==(577 if full else 110)
        assert report['pages_visited']==(59 if full else 12)
        assert report['completed']is full and len(notices)==1
        conn=db.get_connection(window.db_path)
        assert conn.execute('SELECT COUNT(DISTINCT url)FROM jobs').fetchone()[0]==(587 if full else 120)
        run=conn.execute('SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT 1').fetchone()
        assert run['status']==('completed'if full else'cancelled'if scenario=='cancel13'else'partial')
        evidence=[json.loads(r[0])for r in conn.execute("SELECT message FROM scan_events WHERE phase='assisted_response'")]
        conn.close()
        assert evidence and all('url'in row and'http_status'in row for row in evidence)
        if scenario=='blocked13':
            # A computed-visible foreground denial is required. An empty SDK
            # marker does not justify requiring a particular vendor label.
            assert any(r['page']==13 and r['reason']and r.get('visible_sample_source')=='computed_visible_dom'and r.get('rendered_state',{}).get('ready')is False for r in evidence)
        assert report['stated_total']==587 and max_gap(heartbeat)<.3
        request.node.user_properties.extend((key,str(report[key]))for key in ('harvested','duplicates','pages_visited','completed','stated_total'))
        request.node.user_properties.append(('new_jobs',str(worker.count)))
        assert len(launches)==1 and launches[0]['headless']is False and'--disable-gpu'in launches[0]['args']
        assert db.list_blocked_portals(window.db_path,include_resolved=True)[0]['jobs']==report['harvested']
    finally:
        if tab._worker is not None:tab.cancel_ingestion();wait_until(lambda:tab._worker is None)
        server.shutdown();server.server_close();thread.join(2)


def test_human_clearance_fresh_dom_does_not_inherit_stale403_navigation():
    page=Page(WALL,'Just a moment...');page._assisted_nav_status=403
    assert bp._page_is_challenge(page)
    page.html='<h1>Current vacancies</h1>'+LINKS;page.name='Current vacancies'
    assert not bp._page_is_challenge(page)
    assert bp._page_response_evidence(page)['status']==403  # truthful last navigation metadata


def test_cooperative_close_does_not_wait_or_destroy_live_assisted_thread(window,monkeypatch):
    entered=threading.Event()
    def engine(url,name,conn,**kw):
        entered.set()
        while not kw['cancel_event'].is_set():time.sleep(.01)
        bp.LAST_INGESTION_REPORT.update(completed=False,cancelled=True,duplicates=0,pages_visited=0,stop_reason='cancelled by user')
        return 0
    monkeypatch.setattr(bp,'run_assisted_ingestion',engine)
    tab=window.assisted_tab;tab.url_edit.setText('https://fixture.invalid/close');tab._start_ingestion();wait_until(entered.is_set)
    a=time.monotonic();assert not window.close();assert time.monotonic()-a<.1
    assert tab._worker is not None
    wait_until(lambda:tab._worker is None)
    assert window.close()


@pytest.mark.parametrize('markup,total',[('<h1>587 job results</h1>',587),('<h1>5,870 jobs</h1>',5870),('<h1>5.870 open positions</h1>',5870),('<main data-total-jobs="5870"></main>',5870)])
def test_structural_stated_total_not_truncated_at_thousands(markup,total):
    from sponsorscout.scanning.response_evidence import stated_job_total,listing_continuation
    assert stated_job_total(markup)==total
    assert listing_continuation(markup,120)


def test_slow_search_reply_cannot_overwrite_new_filter_intent(window,monkeypatch):
    tab=window.search_tab;entered=threading.Event();release=threading.Event();original=db.search_jobs
    def slow(*a,**k):entered.set();release.wait(3);return original(*a,**k)
    monkeypatch.setattr(db,'search_jobs',slow)
    tab.title_edit.setText('old');tab.run_search();wait_until(entered.is_set)
    tab.title_edit.setText('new');tab.title_edit.textEdited.emit('new')
    applied=[];monkeypatch.setattr(tab,'_apply_search',lambda rows:applied.append(rows))
    release.set();wait_loaders(tab)
    assert not applied and tab.title_edit.text()=='new'


def test_application_refresh_preserves_unsaved_notes_and_selection(window):
    url='https://fixture.invalid/job/application'
    db.upsert_application(window.db_path,url,'Fixture','Backend Engineer',status='saved')
    tab=window.applications_tab;tab.load_applications();wait_loaders(tab)
    tab.table.selectRow(0);tab.notes_edit.setText('Keep this unsaved manual note');tab.status_combo.setCurrentText('interview')
    tab.load_applications();wait_loaders(tab)
    assert tab._editing_url==url and tab.notes_edit.text()=='Keep this unsaved manual note'
    assert tab.status_combo.currentText()=='interview'and tab.table.currentRow()==0
