"""Visible clearance, explicit hand-off and exact saved-run continuation.

All generated prose/boards are LOCAL FIXTURES, not live employer/CAPTCHA or
Windows proof. Tests never run from Scan/startup. No detector/ML rule tuning.
"""
from __future__ import annotations
import csv,hashlib,json,os,threading,time
from pathlib import Path
from types import SimpleNamespace
import pytest
from sponsorscout.core import browser_evidence as be,assisted_resume as resume,blocked_portals as bp,persistence
from sponsorscout.core.assisted_control import AssistedControl
from sponsorscout.core.session_details import parse_detail,SessionDetailEnricher,FLOW_REVISION
from sponsorscout.core.job_identity import portal_queue_key
from sponsorscout.scanning.detail_state import DetailRequestState
from sponsorscout.scanning.career.career_scanner import ProductionScannerConfig
from sponsorscout.db import database as db
from sponsorscout.tests._qt_support import application,wait_until,wait_loaders
from sponsorscout.tests.test_responsiveness_2026_10_24 import window,heartbeat,max_gap
from sponsorscout.tests.test_assisted_support_2026_10_24 import POS,NEG,SILENT,html_ld,job,scanner,URL


def snapshot(**kw):
    return dict(dict(version=1,url=URL,title='Backend Engineer',headings=['Backend Engineer'],anchors=[],primary_text=SILENT,main_text=SILENT,
        body_preview='Backend Engineer responsibilities',active_ids=[],hidden_challenge_nodes=0,overlays=[],foreground_frames=[]),**kw)


@pytest.mark.parametrize('change,blocked',[
    ({'title':'Access to this page has been denied','hidden_challenge_nodes':1},False),
    ({'headings':['Backend Engineer','Verify you are human']},False),
    ({'active_ids':[dict(id='px-captcha',text='Press & Hold',fixed=False)]},False),
    ({'active_ids':[dict(id='px-captcha',text='Press & Hold',fixed=True)]},True),
    ({'overlays':['Please verify you are human; press and hold']},True),
    ({'headings':['Access to this page has been denied']},True),
    ({'foreground_frames':['https://video.invalid/player?token=not-a-captcha']},False),
    ({'foreground_frames':['https://challenges.cloudflare.com/check?token=private']},True),
    ({'primary_text':'','main_text':'','headings':['Verify you are human'],'title':'Access to this page has been denied'},True),
])
def test_rendered_primary_and_foreground_authority(change,blocked):
    data=snapshot(**change);page=SimpleNamespace(evaluate=lambda _:data)
    evidence=be.inspect_page(page,kind='detail',status=403)
    assert bool(evidence['reason'])is blocked
    assert evidence['detail_ready']is not blocked


def test_current_listing_can_clear_stale_status_title_and_hidden_widgets():
    links=[dict(url=f'https://fixture.invalid/jobs/{7300000+i}-engineer.html',title=f'Engineer {i}')for i in range(3)]
    data=snapshot(title='Access to this page has been denied',headings=['Vacancies'],anchors=links,primary_text='',main_text='',hidden_challenge_nodes=1)
    evidence=be.inspect_page(SimpleNamespace(evaluate=lambda _:data),kind='listing',status=403)
    assert evidence['listing_ready']and not evidence['reason']and evidence['visible_job_links']==3


def test_positive_current_dom_does_not_accept_a_foreign_primary_job():
    data=snapshot();evidence=be.inspect_page(SimpleNamespace(evaluate=lambda _:data),kind='detail')
    result=parse_detail(html_ld(POS,URL.replace('123456','999999')),URL,URL,scanner(),browser_evidence=evidence)
    assert result.status=='empty'and not result.text


def test_visible_primary_prose_not_hidden_raw_support_claims():
    data=snapshot();evidence=be.inspect_page(SimpleNamespace(evaluate=lambda _:data),kind='detail')
    raw=html_ld(SILENT)+'<main class="job-description"><div hidden>'+POS*3+'</div>'+SILENT+'</main>'
    result=parse_detail(raw,URL,URL,scanner(),browser_evidence=evidence)
    from sponsorscout.core.support_application import apply_support_to_job
    applied=apply_support_to_job(scanner(),job(),result.text)
    assert applied['visa_sponsorship']==applied['relocation_support']=='Unknown'
    assert 'We offer visa sponsorship'not in result.text and result.method.startswith('rendered_visible_primary')


def test_visible_listing_at_a_detail_url_is_not_full_jd_prose():
    anchors=[dict(url=f'https://fixture.invalid/jobs/{7400000+i}-engineer.html',title='Backend Engineer',in_card=True)for i in range(3)]
    evidence=be.inspect_page(SimpleNamespace(evaluate=lambda _:snapshot(primary_text='',main_text=SILENT,headings=['Current vacancies'],anchors=anchors)),kind='detail')
    assert evidence['listing_ready']and not evidence['detail_ready']
    assert parse_detail('<h1>Current vacancies</h1><main>'+SILENT+'</main>',URL,URL,scanner(),browser_evidence=evidence).status=='empty'


def test_diagnostic_snapshot_has_no_full_jd_or_frame_query_secrets():
    data=snapshot(primary_text='DO_NOT_EXPORT'*10000,foreground_frames=['https://frame.invalid/check?token=SECRET#private'])
    data=be.inspect_page(SimpleNamespace(evaluate=lambda _:data),kind='detail')
    public=be.public_snapshot(data);serialized=json.dumps(public)
    assert 'DO_NOT_EXPORT'not in serialized and 'SECRET'not in serialized and 'private'not in serialized
    assert public['foreground_frames']==['https://frame.invalid/check']


def test_bounded_resource_failures_are_observed_not_invented_and_redacted():
    callbacks={};page=SimpleNamespace(main_frame=object(),on=lambda event,callback:callbacks.setdefault(event,callback),evaluate=lambda _:snapshot())
    be.watch_page_responses(page)
    req=SimpleNamespace(is_navigation_request=lambda:False,resource_type='fetch',url=URL+'?token=PRIVATE',failure='controlled data failure')
    for i in range(6):callbacks['response'](SimpleNamespace(request=req,status=503,url=req.url))
    callbacks['requestfailed'](req)
    public=be.public_snapshot(be.inspect_page(page,kind='detail'))
    assert len(public['failed_data_responses'])==4 and 'PRIVATE'not in json.dumps(public)
    assert public['failed_data_responses'][-1]['http_status']==0 and public['failed_data_responses'][-1]['captured_at']
    callbacks['response'](SimpleNamespace(request=SimpleNamespace(is_navigation_request=lambda:True,frame=page.main_frame),status=200))
    assert be.inspect_page(page,kind='detail')['navigation_status']==200 and not page._assisted_failed_responses


def test_nonbrowser_adapter_does_not_claim_computed_visibility():
    assert be.inspect_page(SimpleNamespace(evaluate=lambda _:True))is None
    assert be.inspect_page(SimpleNamespace())is None


def test_commands_are_worker_safe_and_continue_does_not_certify():
    shown=[];notices=[];control=AssistedControl(notices.append)
    page=SimpleNamespace(bring_to_front=lambda:shown.append('worker'))
    control.show_browser();control.continue_check();assert not shown
    assert control.poll(page)=='check'and shown==['worker']and not control.waiting
    control.attention(dict(waiting=True,phase='detail'));assert control.waiting
    control.save_partial();assert control.poll(page)=='save'and control.save_requested
    assert len(notices)==1


@pytest.fixture
def scoped(tmp_path,monkeypatch):
    from sponsorscout import paths
    out=tmp_path/'out';out.mkdir();monkeypatch.setattr(paths,'SCAN_OUTPUT_DIR',out)
    path=tmp_path/'scope.db';db.initialize(path);conn=db.get_connection(path)
    url='https://fixture.invalid/careers?country=DE#engineering'
    rows=[dict(job(url=f'https://fixture.invalid/jobs/{8300000+i}-engineer.html'),jd_read_status='unattempted',support_evidence_type='listing_card')for i in range(580)]
    yield conn,url,rows,out
    conn.close()


def test_previous_complete580_remains_selectable_after_new_partial330(scoped):
    conn,url,rows,out=scoped
    resume.save_batch(conn,url,'Fixture',rows,run_id='complete580',listing_completed=True,pages=58)
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in resume._path(conn,url).with_suffix('').glob('*.json')}
    resume.save_batch(conn,url,'Fixture',rows[:330],run_id='partial330',listing_completed=False,pages=33)
    assert len(resume.load_batch(conn,url,'Fixture')['rows'])==330
    old=resume.load_batch(conn,url,'Fixture',run_id='complete580');assert old['listing_completed']and len(old['rows'])==580
    choices=resume.list_batches(conn,url,'Fixture');assert {c['run_id']:c['rows']for c in choices}==dict(partial330=330,complete580=580)
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in before.items())



def test_rendered_readiness_and_checkpoint_use_existing_ingestion_validator(scoped):
    conn,url,rows,out=scoped;s=scanner()
    cards=[dict(url=f'https://fixture.invalid/vacatures/software-engineer-{i}.html',title='Vacature bekijken',in_card=True)for i in range(1,4)]
    evidence=be.inspect_page(SimpleNamespace(evaluate=lambda _:snapshot(primary_text='',main_text='',headings=['Current vacancies'],anchors=cards)),kind='listing',validator=s.is_valid_job_url)
    assert evidence['listing_ready']and evidence['visible_job_links']==3
    selected=[dict(job(url=a['url'],title='Software Engineer'),jd_read_status='unattempted')for a in cards]
    resume.save_batch(conn,url,'Fixture',selected,run_id='localized',listing_completed=True,pages=1)
    assert len(resume.load_batch(conn,url,'Fixture',run_id='localized')['rows'])==3


def test_run_prefix_does_not_select_a_different_named_batch(scoped):
    conn,url,rows,out=scoped
    for run,size in [('same',3),('same--other',5)]:resume.save_batch(conn,url,'Fixture',rows[:size],run_id=run,listing_completed=True,pages=1)
    assert len(resume.load_batch(conn,url,'Fixture',run_id='same')['rows'])==3


def test_named_batch_does_not_widen_database_or_url_scope(scoped,tmp_path):
    conn,url,rows,out=scoped;resume.save_batch(conn,url,'Fixture',rows[:3],run_id='selected',listing_completed=True,pages=1)
    for wrong in(url.replace('DE','NL'),url.replace('#engineering','#sales')):
        with pytest.raises(ValueError):resume.load_batch(conn,wrong,'Fixture',run_id='selected')
    other=tmp_path/'other.db';db.initialize(other)
    with db.get_connection(other)as second:
        with pytest.raises(ValueError):resume.load_batch(second,url,'Fixture',run_id='selected')


def test_immutable_payload_corruption_fails_closed(scoped):
    conn,url,rows,out=scoped;resume.save_batch(conn,url,'Fixture',rows[:3],run_id='selected',listing_completed=True,pages=1)
    pointer=json.loads(resume._path(conn,url).read_text());file=resume._path(conn,url).with_suffix('')/pointer['payload']
    file.write_bytes(file.read_bytes()+b' ')
    with pytest.raises(ValueError,match='integrity mismatch'):resume.load_batch(conn,url,'Fixture')


@pytest.mark.parametrize('state',[
    {},{'url':'https://fixture.invalid/careers?p=33','page':34,'provenance':'guessed'},
    {'url':'https://fixture.invalid/careers?p=33','page':True,'provenance':'observed_browser'},
    {'url':'https://fixture.invalid/saved-searches/save-search.html','page':34,'provenance':'observed_browser'},
])
def test_listing_continuation_never_invents_or_accepts_invalid_state(state):
    batch=dict(listing_completed=False,portal_key='https://fixture.invalid/careers',continuation=dict(state,portal_key='https://fixture.invalid/careers'))
    with pytest.raises(ValueError,match='No auditable observed'):resume.listing_continuation(batch)


def test_legacy_partial_cursor_comes_only_from_own_observed_event(scoped):
    conn,url,rows,out=scoped
    original=dict(version=1,database=resume.database_key(conn),portal_key=portal_queue_key(url),run_id='old_partial',seed_name='Fixture',rows=rows[:330],listing_completed=False,pages=33)
    path=resume._path(conn,url);path.parent.mkdir(parents=True);path.write_text(json.dumps(original))
    target='https://fixture.invalid/careers?country=DE&p=33#engineering'
    conn.execute("INSERT INTO scan_log(run_id,scanner,seed_name,seed_url,status,jobs_found)VALUES('old_partial','assisted_browser','Fixture',?,'partial',330)",(url,))
    conn.execute("INSERT INTO scan_events(run_id,phase,message)VALUES('old_partial','assisted_response',?)",(json.dumps(dict(phase='foreground_challenge',page=34,url=target,reason='access denied',captured_at='2026-10-25T12:19:53Z')),));conn.commit()
    before=path.read_bytes();loaded=resume.load_batch(conn,url,'Fixture')
    assert resume.listing_continuation(loaded)['url']==target and loaded['continuation']['page']==34 and path.read_bytes()==before
    conn.execute('UPDATE scan_log SET seed_url=?',(url.replace('DE','NL'),));conn.commit()
    with pytest.raises(ValueError,match='No auditable observed'):resume.listing_continuation(resume.load_batch(conn,url,'Fixture'))



@pytest.mark.parametrize('target',[
    'https://fixture.invalid/careers?country=NL&p=33#engineering',
    'https://fixture.invalid/careers?country=DE&p=33#sales',
    'https://foreign.invalid/careers?country=DE&p=33#engineering',
    'https://fixture.invalid/careers?country=DE&p=0#engineering',
    'https://fixture.invalid/careers?country=DE&country=NL&p=33#engineering',
])
def test_observed_listing_cursor_cannot_widen_selected_query_hash_or_repeat_root(target):
    root='https://fixture.invalid/careers?country=DE&p=0#engineering'
    batch=dict(url=root,portal_key=portal_queue_key(root),pages=33,listing_completed=False,
        continuation=dict(url=target,page=34,provenance='observed_browser',portal_key=portal_queue_key(root)))
    with pytest.raises(ValueError,match='No auditable observed'):resume.listing_continuation(batch)


def test_named_resume_preserves_deleted_inactive_and_manual_data(scoped):
    conn,url,rows,out=scoped
    for row in rows[:3]:persistence.upsert_job(conn,row)
    resume.save_batch(conn,url,'Fixture',rows[:3],run_id='selected',listing_completed=True,pages=1)
    conn.execute('DELETE FROM jobs WHERE url=?',(rows[0]['url'],));conn.execute('UPDATE jobs SET verified_active=0,is_expired=1 WHERE url=?',(rows[1]['url'],))
    conn.execute("UPDATE jobs SET country='France',country_source='manual'WHERE url=?",(rows[2]['url'],));conn.commit()
    pairs=resume.existing_records(conn,resume.load_batch(conn,url,'Fixture',run_id='selected'))
    assert [r is None for r,row in pairs]==[True,True,False]
    assert pairs[-1][0]['country']=='France'and conn.execute('SELECT COUNT(*)FROM jobs').fetchone()[0]==2


def test_saved_batch_ui_is_explicit_localized_and_preserves_selected_run(window,tmp_path,monkeypatch):
    from sponsorscout import paths,i18n
    from sponsorscout.ui.tabs import assisted_ingestion as ui
    out=tmp_path/'out';out.mkdir();monkeypatch.setattr(paths,'SCAN_OUTPUT_DIR',out)
    tab=window.assisted_tab;url='https://fixture.invalid/careers?country=DE#engineering'
    with db.get_connection(window.db_path)as conn:
        resume.save_batch(conn,url,'Fixture',[dict(job(),jd_read_status='empty')],run_id='selected',listing_completed=True,pages=1)
    tab.name_edit.setText('Fixture');tab.url_edit.setText(url)
    assert tab.batch_combo.count()==1 and not tab._batch_loader.busy and tab._worker is None
    tab.batch_load_btn.click();wait_loaders(tab);assert tab.batch_combo.count()==2 and tab._worker is None
    tab.batch_combo.setCurrentIndex(tab.batch_combo.findData('selected'))
    try:
        for locale,label in [('it','Riprendi raccolta elenco'),('en','Resume listing collection')]:
            i18n.set_locale(locale);tab.retranslate();wait_loaders(tab)
            assert tab.resume_listing_btn.text()==label and tab.batch_combo.currentData()=='selected'and tab.url_edit.text()==url
        launches=[];monkeypatch.setattr(ui.AssistedWorker,'start',lambda self:launches.append((self.resume_listing,self.resume_run_id)))
        monkeypatch.setattr(ui.QMessageBox,'information',lambda *a:None)
        tab.resume_listing_btn.click();assert launches==[(True,'selected')]
        worker=tab._worker;tab._on_attention(dict(waiting=True));tab.show_browser_btn.click();tab.continue_btn.click()
        assert worker.control._show.is_set()and worker.control._check.is_set()
        tab.save_partial_btn.click();assert worker.control.save_requested
        worker.report=dict(completed=False,stop_reason='controlled UI-only dispatch');tab._on_ingestion_done(0,'')
    finally:i18n.set_locale('en')


def test_atomic_pointer_failure_leaves_previous_complete_batch(scoped,monkeypatch):
    conn,url,rows,out=scoped
    resume.save_batch(conn,url,'Fixture',rows,run_id='complete580',listing_completed=True,pages=58)
    path=resume._path(conn,url);before=path.read_bytes();replace=resume.os.replace
    def fail_pointer(source,target):
        if Path(target)==path:raise OSError('controlled atomic-pointer failure')
        return replace(source,target)
    monkeypatch.setattr(resume.os,'replace',fail_pointer)
    with pytest.raises(OSError,match='controlled atomic-pointer'):
        resume.save_batch(conn,url,'Fixture',rows[:330],run_id='partial330',listing_completed=False,pages=33)
    assert path.read_bytes()==before and len(resume.load_batch(conn,url,'Fixture')['rows'])==580
    assert not list(path.parent.rglob('*.tmp'))


@pytest.mark.parametrize('target',[
    'https://fixture.invalid/careers?p=33&__cf_chl_extra=SECRET',
    'https://fixture.invalid/careers?p=33&session=SECRET',
    'https://user:SECRET@fixture.invalid/careers?p=33',
    'https://fixture.invalid/careers?p=33#access_token=SECRET',
    'https://fixture.invalid/careers?p=33&x=%5Bredacted%5D',
])
def test_observed_cursor_never_replays_private_or_redacted_session_state(target):
    batch=dict(listing_completed=False,portal_key='scope',continuation=dict(url=target,page=34,provenance='observed_browser',portal_key='scope'))
    with pytest.raises(ValueError):resume.listing_continuation(batch)


def test_diagnostic_url_error_sanitization_preserves_public_filters():
    original='https://fixture.invalid/careers?country=DE&p=33#engineering'
    assert be.redact_url(original)==original
    private='https://user:VERYPRIVATE@fixture.invalid/careers?country=DE&access_token=TOKENPRIVATE&__cf_chl_extra=CFPRIVATE'
    safe=be.redact_text('Navigation failed at '+private+'\nCookie: COOKIEPRIVATE\nAuthorization: AUTHPRIVATE')
    assert 'country=DE'in safe and not any(x in safe for x in('VERYPRIVATE','TOKENPRIVATE','CFPRIVATE','COOKIEPRIVATE','AUTHPRIVATE'))


def test_329_deferrals_do_not_evict_split_http_and_browser_wall(monkeypatch):
    from sponsorscout.tests.test_assisted_transport_2026_10_25 import fake_reader
    reader,state,requests,visits,page=fake_reader(monkeypatch)
    page.content=lambda:'<title>Access to this page has been denied</title><h1>Verify you are human</h1><div id="px-captcha">Press &amp; Hold</div>'
    page.title=lambda:'Access to this page has been denied'
    reader.clear_wait=.001
    first=reader.read(URL);assert first.status=='blocked'and first.http_code==200 and first.http_title=='Client Challenge'
    results=[reader.read(f'https://fixture.invalid/jobs/{7800000+i}-engineer.html')for i in range(329)]
    assert all(r.status=='budget_skipped'and'verification'in r.reason for r in results)
    activity,samples=reader.report()
    assert activity['blocked']==1 and activity['budget_skipped']==329 and len(requests)==len(visits)==1 and state.total==2
    assert any(s['http_title']=='Client Challenge'and s['title']=='Access to this page has been denied'and s['phase']=='human_clearance'for s in samples)
    assert len(samples)<=24 and reader.event_counts['verification_waiting']==reader.event_counts['verification_not_accepted']==1
    reader.close()


def test_verified_fresh_cache_is_reused_before_access_gate(scoped):
    from sponsorscout.core.support_application import apply_support_to_job
    from sponsorscout.scanning.jd_support import normalize_jd_text
    conn,url,rows,out=scoped;s=scanner();record=job()
    proven=apply_support_to_job(s,record,POS);proven['description']=normalize_jd_text(POS);persistence.upsert_job(conn,proven)
    context=SimpleNamespace(request=SimpleNamespace(get=lambda *a,**kw:pytest.fail('cache or closed access gate issued HTTP')))
    reader=SessionDetailEnricher(context,s,'scope',conn=conn);reader.defer_access('unresolved listing verification')
    cached=reader.read(URL);assert cached.status=='readable'and cached.reused
    assert reader.read(URL.replace('123456','987654')).status=='budget_skipped'
    assert reader.counts['reused']==1 and reader.counts['http']==reader.counts['browser']==0


def test_cancelled_background_batch_read_exits_without_loading_more_files(scoped):
    from sponsorscout.core.db_tasks import DatabaseReadTask
    conn,url,rows,out=scoped;resume.save_batch(conn,url,'Fixture',rows[:3],run_id='selected',listing_completed=True,pages=1)
    with DatabaseReadTask()as task:
        task.cancel()
        with pytest.raises(InterruptedError):resume.list_batches(conn,url,'Fixture')


def test_bounded_verification_samples_do_not_truncate_counters(monkeypatch):
    report={};monkeypatch.setattr(bp,'LAST_INGESTION_REPORT',report)
    evidence=dict(status=403,title='Access denied',reason='unresolved wall',rendered=None)
    monkeypatch.setattr(bp,'_page_response_evidence',lambda p:evidence)
    page=SimpleNamespace(url=URL)
    for i in range(300):bp._verification_event(page,'verification_cleared')
    assert len(report['verification_events'])==256 and report['verification_counts']['verification_cleared']==300
    reader=SessionDetailEnricher(None,scanner(),'scope')
    for i in range(300):reader._event('verification_cleared',URL)
    assert len(reader.events)==128 and reader.event_counts['verification_cleared']==300


REAL_BROWSER=pytest.mark.skipif(os.environ.get('SPONSORSCOUT_TEST_BROWSER')!='1'or(not os.environ.get('DISPLAY')and os.name!='nt'),reason='Opt-in headed local browser fixture; not live CAPTCHA or Windows certification')


@REAL_BROWSER
def test_actual_javascript_computed_visibility_primary_and_small_listings(request):
    from playwright.sync_api import sync_playwright
    variations=[
        ('<span style="display:none">HIDDEN_DISPLAY</span>','HIDDEN_DISPLAY',False),
        ('<div style="opacity:0"><span>HIDDEN_PARENT</span></div>','HIDDEN_PARENT',False),
        ('<span hidden style="display:block">VISIBLE_OVERRIDE</span>','VISIBLE_OVERRIDE',True),
        ('<span aria-hidden="true">VISIBLE_ARIA</span>','VISIBLE_ARIA',True),
        ('<div style="visibility:hidden"><span style="visibility:visible">VISIBLE_CHILD</span></div>','VISIBLE_CHILD',True),
        ('<span style="display:contents">VISIBLE_CONTENTS</span>','VISIBLE_CONTENTS',True),
        ('<span style="clip-path:inset(50%)">HIDDEN_CLIPPED</span>','HIDDEN_CLIPPED',False),
    ]
    with sync_playwright()as pw:
        browser=pw.chromium.launch(headless=False,args=['--disable-gpu']);page=browser.new_page()
        try:
            for fragment,word,expected in variations:
                page.set_content('<title>Access to this page has been denied</title><h1>Backend Engineer</h1><main><section class="job-description">'+SILENT+fragment+'</section></main><div id="px-captcha" style="display:none">Press &amp; Hold</div>')
                data=be.inspect_page(page,kind='detail',status=403)
                assert data['detail_ready']and not data['reason']and data['hidden_challenge_nodes']==1
                assert(word in data['primary_text'])is expected
            page.set_content('<h1>Vacancies</h1><article class="job-card"><a href="https://fixture.invalid/jobs/123456-backend-engineer.html">Backend Engineer</a></article>')
            assert be.inspect_page(page,kind='listing',status=403)['listing_ready']
            page.locator('h1').evaluate('(n)=>n.textContent="Current vacancies"')
            assert be.inspect_page(page,kind='listing',status=403)['listing_ready']
            page.locator('h1').evaluate('(n)=>n.textContent="Engineering opportunities"')
            assert be.inspect_page(page,kind='listing',status=403)['listing_ready']
            page.set_content('<h1>Vacancies</h1><a href="https://fixture.invalid/jobs/123456-backend-engineer.html">Backend Engineer</a>')
            assert not be.inspect_page(page,kind='listing')['listing_ready']
            page.set_content('<h1>1 job result</h1><article class="job-card"><a href="https://fixture.invalid/jobs/123456-backend-engineer.html">Backend Engineer</a></article>')
            assert be.inspect_page(page,kind='listing',status=403)['listing_ready']
            page.set_content('<h1>Backend Engineer</h1><main><section class="job-description">'+SILENT+'</section></main><aside><section class="job-description">'+POS*4+'</section></aside><div class="recommended-jobs"><section class="job-description">'+NEG*4+'</section></div>')
            actual=be.inspect_page(page,kind='detail',status=403)
            assert actual['detail_ready']and 'We offer visa sponsorship'not in actual['primary_text']and 'We do not offer visa sponsorship'not in actual['primary_text']
            page.set_content('<title>Access denied</title><h1>Unavailable</h1><aside>'+''.join(f'<a href="https://fixture.invalid/jobs/{123456+i}-engineer.html">Engineer</a>'for i in range(3))+'</aside>')
            assert not be.inspect_page(page,kind='listing',status=403)['listing_ready']

            page.set_content('<h1>Backend Engineer</h1><main><section class="job-description">'+SILENT+'</section></main><dialog open><form><h2>Verify you are human</h2><p>Complete the security check.</p></form></dialog>')
            assert be.inspect_page(page,kind='detail')['reason']
            request.node.user_properties.append(('computed_visibility_variants',len(variations)+8))
        finally:browser.close()


@REAL_BROWSER
@pytest.mark.parametrize('scenario',['hidden_stale','three_clearances','three_listings','late_clearance','failed_clearance','repeated_check','save_partial','stop','window_close','partial330'])
def test_actual_visible_clearance_controls_and_observed_listing_resume(window,tmp_path,monkeypatch,heartbeat,scenario,request):
    """Actual local HTTP + headed Chromium + real Qt buttons/worker/SQLite."""
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from urllib.parse import urlsplit,parse_qs
    from playwright.sync_api import BrowserType,BrowserContext,Page
    from sponsorscout.ui.tabs import assisted_ingestion as ui
    from sponsorscout import paths
    import html as hm
    n=580 if scenario=='partial330'else 40 if scenario=='three_listings'else 3
    pages=[];launches=[];calls=[];notices=[];actions=[];state=dict(resume=False);attention=[]
    def job_url(index):return base+f'/jobs/{9200000+index}-backend-engineer.html'
    def listing(number):
        cards=''.join(f'<article class="job-card"><h2><a href="{job_url(i)}">Backend Engineer {i}</a></h2><p>Location: Berlin, Germany</p><p>Build reliable services with a collaborative team.</p></article>'for i in range((number-1)*10,min(number*10,n)))
        maximum=(n+9)//10
        nav=''.join(f'<a href="?country=DE&amp;p={value-1}#engineering">{value}</a>'for value in range(1,maximum+1))
        if number<maximum:nav+=f'<a aria-label="Next" href="?country=DE&amp;p={number}#engineering">Next</a>'
        return f'<h1>{n} job results</h1>'+cards+'<nav class="pagination">'+nav+'</nav>'
    def text(index):return SILENT if scenario=='hidden_stale'else(POS,NEG,SILENT)[index%3]
    def detail(index):
        return ('<h1>Backend Engineer</h1><main><section class="job-description">'+text(index)+
            '<div style="display:none">'+POS*3+'</div></section></main><div id="px-captcha" style="display:none">Press &amp; Hold</div>'+html_ld(text(index),job_url(index)))
    def gate(content,*,delay=0,fail=False,cookie=''):
        action=('document.cookie='+json.dumps(cookie+'; path=/')+';'if cookie else'')+'document.body.innerHTML='+json.dumps(content)
        if fail:action='document.title="Still blocked"'
        if delay:action='setTimeout(()=>{'+action+'},'+str(delay)+')'
        return '<title>Access to this page has been denied</title><h1>Verify you are human</h1><div id="px-captcha">Press &amp; Hold</div><button id="fixture-clear" onclick="'+hm.escape(action,quote=True)+'">Local fixture: human clearance</button>'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            parsed=urlsplit(self.path);navigation=self.headers.get('Sec-Fetch-Dest')=='document';cookie=self.headers.get('Cookie','')
            number=int(parse_qs(parsed.query).get('p',['0'])[0])+1
            calls.append(dict(path=parsed.path,query=parsed.query,page=number,navigation=navigation))
            status=200
            if parsed.path.startswith('/jobs/'):
                index=int(parsed.path.split('/')[-1].split('-')[0])-9200000
                if not navigation:body='<title>Client Challenge</title><script>window.clientChallenge=true</script>'
                else:
                    status=403  # Deliberately stale response/title after genuine DOM replacement.
                    blocked=(scenario=='three_clearances'and index in(0,1))or(scenario in('late_clearance','failed_clearance','repeated_check','save_partial','stop','window_close')and index==0)
                    body=gate(detail(index),delay=300 if scenario=='late_clearance'else 0,fail=scenario=='failed_clearance')if blocked else '<title>Access to this page has been denied</title>'+detail(index)
            else:
                blocked=(scenario=='three_clearances'and number==1 and'listing-clear=1'not in cookie)or(scenario=='three_listings'and number in(2,3,4)and f'listing-{number}=1'not in cookie)or(scenario=='partial330'and number==34)
                if blocked:
                    status=403;body=gate(listing(number),cookie='listing-clear=1'if scenario=='three_clearances'else f'listing-{number}=1')
                else:body='<title>Engineering careers</title>'+listing(number)
            raw=('<html><body>'+body+'</body></html>').encode();self.send_response(status)
            self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}';url=base+'/careers?country=DE&p=0#engineering';out=tmp_path/'exports';out.mkdir()
    monkeypatch.setattr(paths,'SCAN_OUTPUT_DIR',out);monkeypatch.setattr(paths,'ensure_scan_output_dir',lambda:out)
    launch0=BrowserType.launch;page0=BrowserContext.new_page;wait0=Page.wait_for_timeout
    monkeypatch.setattr(BrowserType,'launch',lambda self,*a,**kw:(launches.append(kw),launch0(self,*a,**kw))[1])
    def new_page(self,*a,**kw):
        def guard(route):
            assert urlsplit(route.request.url).hostname in('localhost','127.0.0.1')
            route.continue_()
        self.route('**/*',guard);page=page0(self,*a,**kw);pages.append(page);return page
    monkeypatch.setattr(BrowserContext,'new_page',new_page)
    monkeypatch.setattr(Page,'wait_for_timeout',lambda self,ms:wait0(self,min(ms,5)))
    class Clock:
        def __getattr__(self,key):return getattr(time,key)
        def sleep(self,seconds):time.sleep(min(seconds,.002))
    monkeypatch.setattr(bp,'time',Clock());monkeypatch.setattr('sponsorscout.core.session_details.time',Clock())
    engine0=bp.run_assisted_ingestion
    def engine(*a,**kw):
        progress=kw.get('progress_callback');control=kw.get('control');old_attention=control.on_attention
        def on_attention(info):
            attention.append(info)
            if old_attention:old_attention(info)
        control.on_attention=on_attention
        def action(message):
            if progress:progress(message)
            if 'Action needed'not in message:return
            detail_gate='job-detail'in message
            should_clear=scenario in('three_clearances','three_listings','late_clearance','failed_clearance')or(scenario=='partial330'and state['resume'])
            if should_clear:
                wall=pages[1]if detail_gate and len(pages)>1 else pages[0]
                actions.append(dict(detail=detail_gate,at=time.monotonic(),url=wall.url))
                wall.locator('#fixture-clear').click()
        kw['progress_callback']=action;kw['clear_wait_s']=.6 if scenario in('late_clearance','partial330')else .3 if scenario in('repeated_check','save_partial','stop','window_close')else .2
        return engine0(*a,**kw)
    monkeypatch.setattr(bp,'run_assisted_ingestion',engine)
    monkeypatch.setattr(ui.QMessageBox,'information',lambda *a,**kw:notices.append(a))
    tab=window.assisted_tab;window.tabs.setCurrentWidget(tab);tab.url_edit.setText(url);tab.name_edit.setText('Fixture')
    if scenario=='partial330':
        with db.get_connection(window.db_path)as conn:
            for index in list(range(70))+list(range(330,370)):
                persistence.upsert_job(conn,job(url=job_url(index),title=f'Backend Engineer {index}',country='',country_source='none',location='',description='',ats_source='career',source_name='Fixture'))
    timer=None
    try:
        tab.launch_btn.click();worker=tab._worker
        if scenario in('repeated_check','save_partial','stop','window_close','partial330'):
            wait_until(lambda:tab._attention_waiting and attention and attention[-1]['phase']==('listing'if scenario=='partial330'else'detail'),60)
            assert tab.show_browser_btn.isEnabled()and tab.continue_btn.isEnabled()and tab.save_partial_btn.isEnabled()
            tab.show_browser_btn.click();tab.continue_btn.click()
            if scenario in('save_partial','partial330'):tab.save_partial_btn.click()
            elif scenario=='stop':tab.stop_btn.click()
            elif scenario=='window_close':
                before=time.monotonic();assert not window.close();assert time.monotonic()-before<.1
            else:
                from PySide6.QtCore import QTimer
                timer=QTimer();timer.setInterval(10);timer.timeout.connect(tab.continue_btn.click);timer.start()
        wait_until(lambda:tab._worker is None,300)
        if timer:timer.stop()
        report=dict(worker.report)
        assert not worker.error and report['support_flow_revision']==FLOW_REVISION and report['export_schema']==5
        assert launches[0]['headless']is False and'--disable-gpu'in launches[0]['args']and len(pages)<=4  # Phase B detail tabs
        assert len(notices)==(0 if scenario=='window_close'else 1)
        assert report['harvested']==(330 if scenario=='partial330'else n)
        if scenario in('hidden_stale','three_clearances','three_listings','late_clearance'):
            assert report['completed']and report['listing_completed']and report['jd_coverage']['readable']==n
            assert report['session_detail_activity']['http']==1 and report['session_detail_activity']['browser']==n
            expected=3 if scenario in('three_clearances','three_listings')else 1 if scenario=='late_clearance'else 0
            assert report['verification_cleared']==expected
        if scenario in('failed_clearance','repeated_check'):
            assert not report['completed']and report['listing_completed']
            assert report['jd_coverage']['blocked']==1 and report['jd_coverage']['budget_skipped']==2 and report['access_deferred']==2
            assert report['session_detail_activity']['browser']==report['session_detail_activity']['http']==1
            assert report['detail_verification_counts']['verification_not_accepted']==1
            assert sum(c['path'].startswith('/jobs/')for c in calls)==2
        if scenario=='repeated_check':assert report['detail_verification_counts']['verification_check_requested']>=1
        if scenario=='save_partial':
            assert report['listing_completed']and not report['completed']and report['access_deferred']==n
            assert report['detail_verification_counts']['verification_saved_partial']==1
            assert report['session_detail_activity']['browser']==report['session_detail_activity']['http']==1
        if scenario in('stop','window_close'):
            assert report['cancelled']and report['listing_completed']and not report['completed']
        if scenario=='partial330':
            assert not report['completed']and not report['listing_completed']and worker.count==260 and report['duplicates']==70
            assert report['pages_visited']==33 and report['last_listing_page']==33 and report['access_deferred']==330
            assert 'saved partial during verification'in report['stop_reason']and report['verification_counts']['verification_saved_partial']==1
            assert report['jd_coverage']['budget_skipped']==330 and not any(c['path'].startswith('/jobs/')for c in calls)
            assert len(db.search_jobs(window.db_path,company='Fixture'))==370
            with db.get_connection(window.db_path)as conn:
                batch=resume.load_batch(conn,url,'Fixture');run=batch['run_id'];cursor=resume.listing_continuation(batch)
                assert cursor['page']==34 and cursor['url']==base+'/careers?country=DE&p=33#engineering'
                csv_original=out/(run+'_career_jobs.csv');old_hash=hashlib.sha256(csv_original.read_bytes()).hexdigest()
                old_log=[tuple(r)for r in conn.execute('SELECT * FROM scan_log WHERE run_id=?',(run,))]
                old_events=[tuple(r)for r in conn.execute('SELECT * FROM scan_events WHERE run_id=? ORDER BY id',(run,))]
                assert len({r['url']for r in batch['rows']})==330
            before_calls=len(calls);before_pages=len(pages);state['resume']=True
            tab.batch_load_btn.click();wait_loaders(tab);tab.batch_combo.setCurrentIndex(tab.batch_combo.findData(run))
            tab.resume_listing_btn.click();second=tab._worker;assert second is not None
            wait_until(lambda:tab._worker is None,400);final=dict(second.report)
            assert not second.error and final['resumed_listing']and final['completed']and final['listing_completed']and final['harvested']==580
            assert final['resumed_from']==run and final['selected_batch']==run and second.count==210 and final['duplicates']==370
            assert final['pages_visited']==58 and final['jd_coverage']['readable']==580 and final['verification_cleared']==1
            assert len(pages)-before_pages==2 and len(launches)==2 and len(notices)==2
            resumed_listing=[c['page']for c in calls[before_calls:]if c['path']=='/careers'and c['navigation']]
            assert resumed_listing and resumed_listing[0]==34 and all(p>=34 for p in resumed_listing)
            assert hashlib.sha256(csv_original.read_bytes()).hexdigest()==old_hash
            with db.get_connection(window.db_path)as conn:
                assert [tuple(r)for r in conn.execute('SELECT * FROM scan_log WHERE run_id=?',(run,))]==old_log
                assert [tuple(r)for r in conn.execute('SELECT * FROM scan_events WHERE run_id=? ORDER BY id',(run,))]==old_events
                assert len(resume.load_batch(conn,url,'Fixture',run_id=run)['rows'])==330
            assert len(db.search_jobs(window.db_path,company='Fixture'))==580
            report=final
        events=report.get('verification_events',[])+report.get('detail_verification_events',[])
        for event in events:
            if event['phase']=='verification_waiting':assert event.get('evidence',{}).get('ready')is False
            if event['phase']=='verification_cleared':assert event.get('evidence',{}).get('ready')is True
        exports=sorted(out.glob('*_career_jobs.csv'),key=lambda p:p.stat().st_mtime_ns)
        assert exports[-1].read_bytes().startswith(b'\xef\xbb\xbf')
        with exports[-1].open(encoding='utf-8-sig',newline='')as f:rows=list(csv.DictReader(f))
        with db.get_connection(window.db_path)as conn:
            stored=[dict(r)for r in conn.execute('SELECT * FROM jobs ORDER BY url')]
            assert len(stored)==n
            if report['completed']:
                assert len(rows)==n and all(r['country']=='Germany'for r in rows)
                assert[(r['visa_sponsorship'],r['relocation_support'],r['eu_blue_card_verdict'])for r in rows]==[(r['visa_sponsorship'],r['relocation_support'],r['eu_blue_card_verdict'])for r in stored]
                if scenario=='hidden_stale':assert all(r['visa_sponsorship']=='Unknown'and r['relocation_support']=='Unknown'for r in rows)
                else:assert[(r['visa_sponsorship'],r['relocation_support'],r['eu_blue_card_verdict'])for r in rows]==[('Y','Y','Yes')if i%3==0 else('N','N','No')if i%3==1 else('Unknown','Unknown','Unknown')for i in range(n)]
                assert rows[0]['jd_http_status']=='200'and rows[0]['jd_http_title']=='Client Challenge'and rows[0]['jd_browser_status']=='403'
                assert rows[0]['jd_http_phase']=='http_response'and rows[0]['jd_browser_phase']=='document_read'
                assert all(r['jd_http_status']=='0'and r['jd_http_title']==''for r in rows[1:])
            if scenario in('failed_clearance','repeated_check','save_partial'):
                assert rows[0]['jd_http_status']=='200'and rows[0]['jd_browser_status']=='403'and rows[0]['jd_browser_evidence']
                assert all(r['jd_defer_kind']=='access_gate'for r in rows[1:])
        assert max_gap(heartbeat)<.3
        request.node.user_properties.extend([('jd_coverage',json.dumps(report['jd_coverage'])),('detail_activity',json.dumps(report['session_detail_activity'])),('accepted_clearances',report['verification_cleared'])])
    finally:
        if timer:timer.stop()
        if tab._worker is not None:tab.cancel_ingestion();wait_until(lambda:tab._worker is None,30)
        server.shutdown();server.server_close();thread.join(2)


def test_assisted_launch_preserves_previous_working_configuration():
    """Pin the user's prior working launch, not a new browser/profile design."""
    import ast
    import inspect
    tree=ast.parse(inspect.getsource(bp.run_assisted_ingestion))
    launch=next(n for n in ast.walk(tree)if isinstance(n,ast.Call)and isinstance(n.func,ast.Attribute)and n.func.attr=='launch'and isinstance(n.func.value,ast.Attribute)and n.func.value.attr=='chromium')
    launch_options={k.arg:ast.literal_eval(k.value)for k in launch.keywords}
    assert launch_options==dict(headless=False,args=['--disable-blink-features=AutomationControlled','--disable-gpu','--start-maximized'])
    context=next(n for n in ast.walk(tree)if isinstance(n,ast.Call)and isinstance(n.func,ast.Attribute)and n.func.attr=='new_context'and isinstance(n.func.value,ast.Name)and n.func.value.id=='browser')
    context_options={k.arg:ast.literal_eval(k.value)for k in context.keywords}
    assert context_options==dict(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',no_viewport=True)
