"""Actual shared-policy and human-cleared session JD integration regressions.

Text-only cases do not pretend to prove a real CAPTCHA. Opt-in browser cases
below exercise actual HTTP/body→session clearance→detail→CSV/SQLite/Search/UI.
No live portals, classifier/ML retuning or automatic visible regular browser.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import csv,hashlib,json,os,threading,time
import pytest
from sponsorscout.core import blocked_portals as bp,persistence
from sponsorscout.core.session_details import parse_detail,SessionDetailEnricher,DetailResult,FLOW_REVISION
from sponsorscout.core.support_application import apply_support_to_job
from sponsorscout.db import database as db
from sponsorscout.scanning.career.career_scanner import CareerPortalScanner
from sponsorscout.tests._qt_support import application,wait_until,wait_loaders
from sponsorscout.tests.test_responsiveness_2026_10_24 import window,heartbeat,max_gap

BASE=('Responsibilities: Build reliable backend services, maintain automated tests and collaborate with product teams. '
      'Requirements: Strong engineering communication and practical experience operating distributed systems. ')*8
POS=BASE+' We offer visa sponsorship for this role. We offer relocation assistance. We support the EU Blue Card.'
NEG=BASE+' We do not offer visa sponsorship. No relocation assistance is available. We do not offer the EU Blue Card.'
SILENT=BASE+' You will receive a learning budget and flexible hours.'
INTERNAL=BASE+' We offer visa and relocation support within the EU for this role.'
CARD='Backend Engineer\nLocation: Berlin, Germany\nWork with a collaborative team.'
URL='https://fixture.invalid/jobs/123456-backend-engineer.html'


def scanner():return CareerPortalScanner(input_csv='__unused__.csv',skip_preflight=True)
def job(**kw):return dict(dict(url=URL,title='Backend Engineer',company='Fixture',location='Berlin, Germany',country='Germany',country_source='job_location',ats_source='assisted_browser',source_type='recruiter',source_subtype='recruiter'),**kw)
def html_ld(text,url=URL,country='DE'):
    return '<html><title>Backend Engineer</title><h1>Backend Engineer</h1><script type="application/ld+json">'+json.dumps({'@type':'JobPosting','url':url,'title':'Backend Engineer','description':text,'jobLocation':{'address':{'addressLocality':'Berlin','addressCountry':country}}})+'</script></html>'


@pytest.mark.parametrize('text,visa,reloc,blue',[(POS,'Y','Y','Yes'),(NEG,'N','N','No'),(SILENT,'Unknown','Unknown','Unknown'),(INTERNAL,'Unknown','Unknown','Unknown')])
def test_assisted_uses_actual_regular_application_and_context_policy(text,visa,reloc,blue):
    result=apply_support_to_job(scanner(),job(),text)
    assert(result['visa_sponsorship'],result['relocation_support'],result['eu_blue_card_verdict'])==(visa,reloc,blue)
    assert result['jd_read_status']=='readable'and result['jd_chars']==len(__import__('sponsorscout.scanning.jd_support',fromlist=['normalize_jd_text']).normalize_jd_text(text))
    if text==INTERNAL:assert result['support_guard_notes']=='visa,relocation'
    if visa in('Y','N'):assert result['support_evidence']and result['support_evidence_url']==URL


def test_listing_teaser_silent_is_not_a_readable_jd_or_a_refusal():
    result=apply_support_to_job(scanner(),job(),CARD,kind='card',read_status='unattempted',method='listing_card')
    assert result['visa_sponsorship']==result['relocation_support']=='Unknown'
    assert result['jd_chars']==0 and result['jd_source']=='listing_card'


def test_function_and_required_flags_are_independent_of_benefits():
    result=apply_support_to_job(scanner(),job(title='International mobility expert'),BASE+' We offer visa sponsorship and relocation support.')
    assert result['visa_sponsorship']==result['relocation_support']=='Unknown'
    own_cost=apply_support_to_job(scanner(),job(),BASE+' You must be willing to relocate at your own cost.')
    assert own_cost['relocation_support']=='N'and own_cost['relocation_required']=='Y'


def test_primary_jsonld_not_recommended_job_and_long_tail_not_discarded():
    s=scanner();other=html_ld(POS,'https://fixture.invalid/jobs/999999-other.html')
    actual=html_ld(NEG,URL)
    result=parse_detail(other+actual,URL,URL,s)
    assert result.status=='readable'and 'do not offer'in result.text
    tail=BASE*9+' We offer visa sponsorship for this role.'
    result=parse_detail(html_ld(tail),URL,URL,s)
    assert len(result.text)>8000 and apply_support_to_job(s,job(),result.text)['visa_sponsorship']=='Y'


def test_multiple_unmatched_postings_and_redirected_listing_are_not_primary_jd():
    s=scanner();text=html_ld(POS,'https://fixture.invalid/jobs/555555-a.html')+html_ld(POS,'https://fixture.invalid/jobs/666666-b.html')
    assert parse_detail(text,URL,URL,s).status=='empty'
    body='<h1>Search jobs</h1><main>'+BASE+''.join(f'<a href="/jobs/{n}-engineer.html">Engineer</a>'for n in(100001,100002,100003))+'</main>'
    assert parse_detail(body,URL,'https://fixture.invalid/careers',s).status=='empty'


def test_tiny_meta_is_not_full_jd_and_truncation_is_explicit():
    s=scanner();text='<meta name="description" content="'+BASE[:400]+'"><h1>Backend Engineer</h1>'
    assert parse_detail(text,URL,URL,s).status=='empty'
    result=parse_detail(html_ld(BASE*80),URL,URL,s)
    assert result.status=='truncated'and result.truncated and len(result.text)==60000


def test_authoritative_detail_guard_does_not_resurrect_old_raw_card_yes(tmp_path):
    path=tmp_path/'guard.db';db.initialize(path);conn=db.get_connection(path)
    persistence.upsert_job(conn,dict(job(),description=INTERNAL,visa_sponsorship='Y',relocation_support='Y',support_evidence=INTERNAL,support_evidence_type='job_posting'))
    corrected=apply_support_to_job(scanner(),job(),INTERNAL);corrected['description']=INTERNAL
    persistence.upsert_job(conn,corrected)
    row=conn.execute('SELECT * FROM jobs').fetchone()
    assert row['visa_sponsorship']==row['relocation_support']=='Unknown'
    assert 'context_guard='in row['support_evidence_type'];conn.close()


def test_real_jd_replaces_longer_listing_chrome_and_card_cannot_replace_full_jd(tmp_path):
    path=tmp_path/'rich.db';db.initialize(path);conn=db.get_connection(path);s=scanner()
    card=apply_support_to_job(s,job(),CARD*60,kind='card',read_status='unattempted',method='listing_card');card['description']=CARD*60
    persistence.upsert_job(conn,card)
    detail=apply_support_to_job(s,job(),POS);detail['description']=POS;persistence.upsert_job(conn,detail)
    assert conn.execute('SELECT description FROM jobs').fetchone()[0]==POS
    card=apply_support_to_job(s,job(),CARD*80,kind='card',read_status='unattempted',method='listing_card');card['description']=CARD*80
    persistence.upsert_job(conn,card)
    row=conn.execute('SELECT * FROM jobs').fetchone()
    assert row['description']==POS and row['visa_sponsorship']=='Y'
    assert 'jd_read_status=readable'in row['support_evidence_type'];conn.close()


def test_effective_manual_country_integrity_and_blue_card_remain_consistent(tmp_path):
    path=tmp_path/'manual.db';db.initialize(path);conn=db.get_connection(path);s=scanner()
    persistence.upsert_job(conn,dict(job(),country='Brazil',country_source='manual'))
    detail=apply_support_to_job(s,job(),POS);detail['description']=POS;persistence.upsert_job(conn,detail)
    row=conn.execute('SELECT * FROM jobs').fetchone()
    assert row['country']=='Brazil'and row['country_source']=='manual'
    assert row['visa_sponsorship']=='Unknown'and row['eu_blue_card_verdict']=='Unknown'and row['eu_blue_card']==0
    assert row['relocation_support']=='Y';conn.close()


def test_session_cookie_http_cache_budget_and_retrieval_quality(tmp_path,monkeypatch):
    s=scanner();calls=[]
    class Request:
        def get(self,url,**kw):calls.append(url);return SimpleNamespace(status=200,url=url,text=lambda:html_ld(POS),dispose=lambda:None)
    session=SessionDetailEnricher(SimpleNamespace(request=Request()),s,'scope')
    monkeypatch.setattr('sponsorscout.core.session_details.time',SimpleNamespace(sleep=lambda t:None,monotonic=time.monotonic))
    result=session.read(URL);again=session.read(URL)
    assert result.status=='readable'and again.text==result.text and calls==[URL]
    assert session.counts['http']==1 and session.counts['reused']==1
    assert s._detail_state.coverage('scope',[URL])['readable']==1


def test_saved_jd_cache_preserves_original_capture_time_and_checks_digest(tmp_path):
    path=tmp_path/'cache.db';db.initialize(path);conn=db.get_connection(path);s=scanner()
    stamp=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
    detail=apply_support_to_job(s,job(),POS,captured_at=stamp);detail['description']=__import__('sponsorscout.scanning.jd_support',fromlist=['normalize_jd_text']).normalize_jd_text(POS);persistence.upsert_job(conn,detail)
    session=SessionDetailEnricher(SimpleNamespace(request=SimpleNamespace(get=lambda *a,**k:pytest.fail('cache refetched'))),s,'scope',conn=conn)
    result=session.read(URL)
    assert result.reused and result.captured_at==stamp and session.counts['http']==0
    again=apply_support_to_job(s,job(),result.text,method=result.method,captured_at=result.captured_at)
    assert 'jd_captured_at='+stamp in again['support_evidence_type']
    conn.execute("UPDATE jobs SET description='different stored text'");conn.commit()
    assert session._saved(URL)is None;conn.close()


@pytest.mark.skipif(os.environ.get('SPONSORSCOUT_TEST_BROWSER')!='1' or (not os.environ.get('DISPLAY')and os.name!='nt'),reason='Developer-only real human-cleared browser fixture')
@pytest.mark.parametrize('scenario',['offers','guards','required','js','passive','reblock','detailclear','budget','latest584'])
def test_real_cleared_session_full_jds_csv_sqlite_search_and_ui(window,tmp_path,monkeypatch,heartbeat,scenario,request):
    from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
    from urllib.parse import urlsplit,parse_qs
    from playwright.sync_api import BrowserType,BrowserContext,Page
    from sponsorscout.ui.tabs import assisted_ingestion as ui
    from sponsorscout.scanning.career.career_scanner import ProductionScannerConfig
    from sponsorscout import paths
    import html as html_module
    n=584 if scenario=='latest584'else 3 if scenario in('offers','reblock','budget')else 2 if scenario=='guards'else 1
    pages=[];launches=[];gets=[];cookies=[];notices=[];clears=[]
    def role(i):return'International mobility expert'if scenario=='guards'and i==1 else f'Backend Engineer {i}'
    def listing(number):
        cards=''.join(f'<article class="job-card"><h2><a href="/jobs/{6100000+i}-backend-engineer.html">{role(i)}</a></h2><p>Location: Berlin, Germany</p><p>Build reliable services with a collaborative team.</p></article>'for i in range((number-1)*10,min(number*10,n)))
        maximum=(n+9)//10;start=((number-1)//10)*10+1
        nav=''.join(f'<a href="?p={value-1}">{value}</a>'for value in range(start,min(start+10,maximum+1)))
        if number<maximum:nav+=f'<a aria-label="Next" href="?p={number}">&gt;</a>'
        # No current/active class: actual URL-to-numeric href mapping supplies
        # current-page evidence, not a magiclast number/hostname assumption.
        return f'<h1>{n} job results</h1>'+cards+'<nav class="pagination">'+nav+'</nav>'
    def doc(text,index):
        return html_ld(text,base+f'/jobs/{6100000+index}-backend-engineer.html')+'<section class="job-description">'+text+'</section>'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            parsed=urlsplit(self.path);gets.append(self.path);status=200
            if parsed.path.startswith('/jobs/'):
                cookies.append(self.headers.get('Cookie',''));index=int(parsed.path.split('/')[-1].split('-')[0])-6100000
                if scenario in('reblock','detailclear'):
                    status=403;body='<title>Just a moment...</title><h1>Verify you are human</h1><div id="cf-challenge-running"></div>'
                    if scenario=='detailclear':
                        value=json.dumps(doc(POS,index));action='document.title="Backend Engineer";document.body.innerHTML='+value
                        body+='<button id="detail-clear" onclick="'+html_module.escape(action,quote=True)+'">Continue local fixture</button>'
                elif scenario=='js':
                    body='<title>Backend Engineer</title><h1>Backend Engineer</h1><main>Loading...</main><script>setTimeout(()=>{document.querySelector("main").textContent='+json.dumps(POS)+';},70)</script>'
                else:
                    text=(POS,NEG,SILENT)[index]if scenario=='offers'else INTERNAL if scenario=='guards'else BASE+' You must be willing to relocate at your own cost.'if scenario=='required'else SILENT if scenario=='latest584'else POS
                    body=doc(text,index)
                    if scenario=='passive':body+='<iframe src="https://challenges.cloudflare.com/passive-apply-widget"></iframe>'
            else:
                number=int(parse_qs(parsed.query).get('p',['0'])[0])+1
                if 'fixture-session=human-cleared' not in self.headers.get('Cookie',''):
                    value=json.dumps('<title>Engineering careers</title>'+listing(1));action='document.title="Engineering careers";document.body.innerHTML='+value
                    body='<title>Just a moment...</title><h1>Verify you are human</h1><div id="cf-challenge-running"></div><button id="board-clear" onclick="'+html_module.escape(action,quote=True)+'">Continue local fixture</button>'
                else:body='<title>Engineering careers</title>'+listing(number)
            raw=('<html><body>'+body+'</body></html>').encode();self.send_response(status)
            self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Set-Cookie','fixture-session=human-cleared; Path=/')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}';launch0=BrowserType.launch;page0=BrowserContext.new_page;wait0=Page.wait_for_timeout
    monkeypatch.setattr(BrowserType,'launch',lambda self,*a,**k:(launches.append(k),launch0(self,*a,**k))[1])
    def new_page(self,*a,**k):
        def guard(route):
            assert urlsplit(route.request.url).hostname in('localhost','127.0.0.1')
            route.continue_()
        self.route('**/*',guard);page=page0(self,*a,**k);pages.append(page);return page
    monkeypatch.setattr(BrowserContext,'new_page',new_page)
    monkeypatch.setattr(Page,'wait_for_timeout',lambda page,ms:wait0(page,min(ms,5)))
    class Clock:
        def __getattr__(self,key):return getattr(time,key)
        def sleep(self,seconds):time.sleep(min(seconds,.005))
    monkeypatch.setattr(bp,'time',Clock())
    monkeypatch.setattr('sponsorscout.core.session_details.time',Clock())
    if scenario=='budget':monkeypatch.setattr(ProductionScannerConfig,'MAX_DETAIL_SCAN_PER_COMPANY',1)
    original=bp.run_assisted_ingestion
    def engine(*a,**kw):
        progress=kw.get('progress_callback')
        def action(message):
            if progress:progress(message)
            if 'Action needed'in message:
                if 'job-detail'in message:
                    if scenario=='detailclear':pages[1 if len(pages)>1 else 0].locator('#detail-clear').click();clears.append('detail')
                else:pages[0].locator('#board-clear').click();clears.append('board')
        kw['progress_callback']=action;kw['clear_wait_s']=.3
        return original(*a,**kw)
    monkeypatch.setattr(bp,'run_assisted_ingestion',engine)
    monkeypatch.setattr(ui.QMessageBox,'information',lambda *a,**k:notices.append(a))
    tab=window.assisted_tab;window.tabs.setCurrentWidget(tab);tab.url_edit.setText(base+'/careers?p=0');tab.name_edit.setText('Fixture')
    try:
        tab._start_ingestion();worker=tab._worker
        try:
            # This is a functional/heartbeat test, not a wall-time benchmark.
            # A584-response fixture can spend bounded retry/cooldown time under
            # CI contention; production budgets/politeness are unchanged.
            wait_until(lambda:tab._worker is None,300 if scenario=='latest584'else 160)
        except AssertionError:
            pytest.fail('Incomplete fixture at deadline: status='+tab.status_lbl.text()+'; last HTTP='+repr(gets[-10:]),pytrace=True)
        report=worker.report
        assert worker.count==n and report['harvested']==n and report['listing_completed']
        assert clears.count('board')==1 and len(launches)==1 and launches[0]['headless']is False and'--disable-gpu'in launches[0]['args']
        assert all('fixture-session=human-cleared'in cookie for cookie in cookies)
        assert report['support_flow_revision']==FLOW_REVISION and report['export_schema']==5
        expected_complete=scenario not in('reblock','budget')
        assert report['completed']is expected_complete and len(notices)==1
        conn=db.get_connection(window.db_path)
        rows=[dict(row)for row in conn.execute('SELECT * FROM jobs ORDER BY url')]
        assert len(rows)==n
        if scenario=='offers':assert[(r['visa_sponsorship'],r['relocation_support'],r['eu_blue_card_verdict'])for r in rows]==[('Y','Y','Yes'),('N','N','No'),('Unknown','Unknown','Unknown')]
        if scenario=='guards':assert all(r['visa_sponsorship']==r['relocation_support']=='Unknown'for r in rows)
        if scenario=='required':assert rows[0]['relocation_support']=='N'and rows[0]['relocation_required']=='Y'
        if scenario in('js','passive','detailclear'):assert rows[0]['visa_sponsorship']==rows[0]['relocation_support']=='Y'
        if scenario=='reblock':assert report['jd_coverage']['blocked']==1 and report['jd_coverage']['budget_skipped']==2 and all(r['visa_sponsorship']=='Unknown'for r in rows)
        if scenario=='budget':assert report['jd_coverage']['readable']==1 and report['jd_coverage']['budget_skipped']==2
        if scenario=='latest584':assert report['pages_visited']==59 and report['jd_coverage']['readable']==584 and report['jd_coverage']['readable_unknown_support']==584
        assert all(len(r['description'])>=200 for r in rows if 'jd_read_status=readable'in str(r['support_evidence_type']))
        run=conn.execute('SELECT run_id FROM scan_runs ORDER BY started_at DESC LIMIT 1').fetchone()[0];conn.close()
        main=paths.ensure_scan_output_dir()/(run+'_career_jobs.csv')
        with main.open(encoding='utf-8-sig',newline='')as f:export=list(csv.DictReader(f))
        assert len(export)==n and main.read_bytes().startswith(b'\xef\xbb\xbf')
        by_url={r['url']:r for r in rows}
        for r in export:
            saved=by_url[r['url']]
            assert all(r[k]==str(saved[k]or'')for k in('visa_sponsorship','relocation_support','eu_blue_card_verdict'))
            assert r['jd_read_status']and r['jd_source']or r['jd_read_status']=='budget_skipped'
        assert len(db.search_jobs(window.db_path,company='Fixture'))==n
        if scenario=='offers':assert len(db.search_jobs(window.db_path,company='Fixture',sponsorship_only=True))==1
        assert max_gap(heartbeat)<.3
        for key in('harvested','pages_visited','completed'):request.node.user_properties.append((key,str(report[key])))
        request.node.user_properties.append(('jd_coverage',json.dumps(report['jd_coverage'])))
    finally:
        if tab._worker is not None:tab.cancel_ingestion();wait_until(lambda:tab._worker is None,20)
        server.shutdown();server.server_close();thread.join(2)


def test_semantic_full_description_beats_structured_preview_not_marketing():
    s=scanner();preview=BASE[:400]
    html=html_ld(preview)+ '<section class="job-description">'+POS+'</section><footer>Unrelated employer marketing</footer>'
    result=parse_detail(html,URL,URL,s)
    assert result.status=='readable'and len(result.text)>400
    assert apply_support_to_job(s,job(),result.text)['visa_sponsorship']=='Y'
    assert 'Unrelated employer marketing'not in result.text


def test_non_detail_payload_does_not_inherit_authoritative_flags():
    s=scanner();old=apply_support_to_job(s,job(),INTERNAL)
    card=apply_support_to_job(s,old,CARD,kind='card',read_status='unattempted',method='listing_card')
    assert not card.get('_support_authoritative')and not card.get('_support_guarded')


def test_deliberate_unknown_manual_country_is_not_evidence_against_support(tmp_path):
    path=tmp_path/'unknown_manual.db';db.initialize(path);conn=db.get_connection(path)
    persistence.upsert_job(conn,job())
    conn.execute("UPDATE jobs SET country='Unknown',country_source='manual'");conn.commit()
    detail=apply_support_to_job(scanner(),job(),POS);detail['description']=POS;persistence.upsert_job(conn,detail)
    row=conn.execute('SELECT * FROM jobs').fetchone()
    assert row['country']=='Unknown'and row['country_source']=='manual'
    assert row['visa_sponsorship']=='Y'and row['relocation_support']=='Y';conn.close()


def test_stale_saved_cache_is_not_presented_as_a_fresh_read(tmp_path):
    path=tmp_path/'stale_cache.db';db.initialize(path);conn=db.get_connection(path);s=scanner()
    stamp=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()
    detail=apply_support_to_job(s,job(),POS,captured_at=stamp)
    detail['description']=__import__('sponsorscout.scanning.jd_support',fromlist=['normalize_jd_text']).normalize_jd_text(POS)
    persistence.upsert_job(conn,detail)
    session=SessionDetailEnricher(SimpleNamespace(),s,'scope',conn=conn)
    assert session._saved(URL)is None;conn.close()


def test_ambiguous_card_does_not_erase_previous_structured_country(tmp_path):
    path=tmp_path/'country_retention.db';db.initialize(path);conn=db.get_connection(path);s=scanner()
    observed=job(location='Dordrecht',country='Netherlands',country_source='structured_job_country')
    detail=apply_support_to_job(s,observed,POS);detail['description']=__import__('sponsorscout.scanning.jd_support',fromlist=['normalize_jd_text']).normalize_jd_text(POS)
    persistence.upsert_job(conn,detail)
    card=apply_support_to_job(s,dict(observed,country='',country_source='none'),CARD,kind='card',read_status='unattempted',method='listing_card');card['description']=CARD
    persistence.upsert_job(conn,card)
    row=conn.execute('SELECT * FROM jobs').fetchone()
    assert row['country']=='Netherlands'and row['country_source']=='structured_job_country'
    session=SessionDetailEnricher(SimpleNamespace(),s,'scope',conn=conn);cached=session._saved(URL)
    assert cached.country=='Netherlands'and cached.country_source=='structured_job_country';conn.close()


@pytest.mark.parametrize('fresh,wall,expected',[(True,False,True),(False,False,False),(True,True,False)])
def test_late_successful_navigation_requires_new_valid_content_not_url_or_wall(fresh,wall,expected):
    class Page:
        url='https://fixture.invalid/careers?page=3'
        frames=[]
        def title(self):return 'Just a moment...'if wall else'Current vacancies'
        def content(self):
            links=''.join(f'<article><a href="/jobs/{123450+n}-engineer.html">Engineer {n}</a></article>'for n in range(3))
            return ('<h1>Verify you are human</h1><div id="cf-challenge-running"></div>'if wall else'<h1>Current vacancies</h1>')+links
        def evaluate(self,script):return 'new signature'if fresh else'old signature'
    assert bp._late_listing_advance(Page(),'old signature')is expected
