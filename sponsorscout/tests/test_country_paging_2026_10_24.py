"""Shared geography, posting evidence, terminal paging and explicit repair pins.

No live portals; exact uploaded observations are separately replayed by developer
Changelogs tooling. The real browser fixture is opt-in and generic, not a host
preset. It exercises586/59/omittedNext and administrative overlay pollution.
"""
from __future__ import annotations
import hashlib,json,os,sqlite3,threading,time
from pathlib import Path
from types import SimpleNamespace
import pytest
from sponsorscout.core.location_country import country_from_location
from sponsorscout.core.place_index import place_country_candidates,place_evidence
from sponsorscout.core import job_identity as ji,blocked_portals as bp,persistence
from sponsorscout.core.country_details import structured_job_country,SessionCountryResolver
from sponsorscout.core.data_review import preview_data_review,apply_data_review
from sponsorscout.db import database as db
from sponsorscout.scanning.career.career_scanner import CareerPortalScanner
from sponsorscout.tests._qt_support import application,wait_until,wait_loaders
from sponsorscout.tests.test_responsiveness_2026_10_24 import window,heartbeat,max_gap

PLACE_COUNTS = {'Hoofddorp': 19, 'Dordrecht': 19, 'Barendrecht': 12, 'Hilversum': 9, 'Amersfoort': 9, 'Sliedrecht': 7, 'Ridderkerk': 5, 'Lijnden': 4, 'Nieuwegein': 4, 'Lelystad': 4, 'Gelderland': 4, 'Schiphol-Rijk': 4, 'Bussum': 4, 'Goes': 4, 'Weesp': 3, 'Deventer': 3, 'Rijswijk': 3, 'Capelle aan den IJssel': 3, 'Gorinchem': 3, 'Schiedam': 3, 'Veenendaal': 3, 'Waalwijk': 3, 'Oosterhout': 3, 'Hardinxveld-Giessendam': 2, 'Soest': 2, 'Vianen': 2, 'Barneveld': 2, 'Zaandam': 2, 'Hillegom': 2, 'Den Bosch': 2, 'Helmond': 2, 'Zoetermeer': 2, 'Woudenberg': 2, 'Ede': 2, 'Velsen-Noord': 2, 'Tiel': 2, 'Valkenswaard': 2, 'Heerenveen': 2, 'Oostzaan': 2, 'Dronten': 2, 'Houten': 2, 'Naarden': 2, 'Oss': 2, 'Uithoorn': 2, 'Sevenum': 2, 'Wageningen': 2, 'Diemen': 1, 'Hoorn': 1, 'Heemskerk': 1, 'Strijen': 1, 'Nunspeet': 1, 'Emmeloord': 1, 'Epe': 1, 'Leusden': 1, 'Heemstede': 1, 'Hengelo': 1, 'Gouda': 1, 'Woerden': 1, 'Zeist': 1, 'Oud-Beijerland': 1, 'Nijkerk': 1, 'Culemborg': 1, 'Leiden': 1, 'Spijkenisse': 1, 'Amstelveen': 1, 'Papendrecht': 1, 'Roermond': 1, 'Geldermalsen': 1, 'IJmuiden': 1, 'Berkel en Rodenrijs': 1, 'Schijndel': 1, 'Alphen aan den Rijn': 1}
PLACES = tuple(PLACE_COUNTS)
AMBIGUOUS={'Dordrecht','Amersfoort','Goes','Soest','Barneveld','Ede','Wageningen','Epe','Gouda'}
LEGAL_PATHS = ('/legal/jobs/overig/1917244-head-of-legal-affairs.html', '/legal/jobs/bankingfinance/1916942-senior-legal-counsel-finance-insurance--banking.html', '/legal/jobs/overig/1916941-senior-legal-counsel-tech.html', '/legal/jobs/overig/1916936-junior-legal--compliance-officer.html', '/legal/jobs/publicsector/1916672-interim-juridisch-beleidsadviseur.html', '/legal/jobs/overig/1916526-legal-counsel.html', '/legal/jobs/overig/1916420-head-of-legal-affairs.html', '/legal/jobs/publicsector/1916061-interim-juridisch-beleidsadviseur.html', '/legal/jobs/overig/1915804-advocaat-ondernemingsrecht-en-financiering.html', '/legal/jobs/commerciallaw/1913389-senior-legal-counsel.html', '/legal/jobs/overig/1910632-senior-legal-counsel.html', '/legal/jobs/overig/1909770-functionaris-gegevensbescherming.html', '/legal/jobs/commerciallaw/1909751-legal-counsel.html', '/legal/jobs/overig/1909671-compliance-officer.html')
ALERT='https://fixture.invalid/legacy-content/overlay/saved-searches/save-search.html'
ALERT_TITLE='Maak een vacaturemelding aan'


@pytest.mark.parametrize('place',PLACES)
def test_shared_observed_place_resolution_and_ambiguity(place):
    actual=country_from_location(place)
    if place in AMBIGUOUS:
        assert not actual and len(place_country_candidates(place))>1
    else:assert actual=='Netherlands'


@pytest.mark.parametrize('path',LEGAL_PATHS)
def test_legal_prefix_id_rescue_before_exclusion(path):
    scanner=CareerPortalScanner(input_csv='__unused__.csv',skip_preflight=True)
    url='https://fixture.invalid'+path
    assert ji.explicit_detail_url(url)and scanner.is_valid_job_url(url)
    assert ji.posting_candidate(url,'Legal Counsel',scanner.is_valid_job_url)
    other=url.replace('fixture.invalid','different.invalid')
    assert ji.posting_identity(url)!=ji.posting_identity(other)


@pytest.mark.parametrize('tail,title',[('/saved-searches/save-search.html',ALERT_TITLE),('/job-alerts','Create a job alert'),('/newsletter/subscribe','Subscribe'),('/save-search','Créer une alerte emploi')])
def test_administrative_actions_not_postings(tail,title):
    url='https://fixture.invalid'+tail
    assert ji.administrative_action(url,title)
    assert not ji.posting_candidate(url,title)
    assert bp._is_save_action_url(url)


@pytest.mark.parametrize('url,title',[('https://fixture.invalid/about/careers/jobs/8325928a-68a9-4b6c-9c32-785f168e6d81','Backend Engineer'),
 ('https://fixture.invalid/jobs/legal/123456-job-alert-engineer.html','Job Alert Engineer'),
 ('https://fixture.invalid/positions?jobId=123456&country=DE#scope','Legal Counsel'),
 ('https://fixture.invalid/careers/open-sollicitatie','Open sollicitatie')])
def test_positive_posting_and_evergreen_not_admin(url,title):
    assert not ji.administrative_action(url,title)
    assert ji.posting_candidate(url,title)


def test_posting_acceptance_never_changes_identity_or_collapses_titles():
    a='https://fixture.invalid/tenant-a/jobs/123456-backend-engineer.html?country=NL#job-a'
    b=a.replace('tenant-a','tenant-b');c=a.replace('job-a','job-b')
    assert all(ji.explicit_detail_url(u)for u in(a,b,c))
    assert len({ji.posting_identity(u)for u in(a,b,c)})==3
    assert not ji.explicit_detail_url('https://fixture.invalid/saved-searches/save-search.html')


def ld(country):
    return '<script type="application/ld+json">'+json.dumps({'@type':'JobPosting','description':('Responsibilities: Build reliable services. Requirements: Collaborate and maintain tests. '*10),'jobLocation':{'address':{'addressCountry':country}}})+'</script>'


@pytest.mark.parametrize('value,expected',[('NL','Netherlands'),({'name':'Netherlands'},'Netherlands'),('Bolivia','Bolivia'),('nonsense','')])
def test_explicit_job_country_not_hq(value,expected):
    assert structured_job_country(ld(value))==expected
    assert structured_job_country('<script type="application/ld+json">'+json.dumps({'@type':'Organization','address':{'addressCountry':'NL'}})+'</script>')==''


def test_multiple_job_countries_remain_unresolved():
    assert structured_job_country(ld('NL')+ld('DE'))==''
    assert country_from_location('Soest, Netherlands')=='Netherlands'
    assert country_from_location('Soest, Germany')=='Germany'
    assert country_from_location('Remote - EU')==''
    assert country_from_location('La Paz - Bolivia')=='Bolivia'


def test_country_detail_uses_shared_budget_cache_and_no_jd_success_claim(monkeypatch):
    scanner=CareerPortalScanner(input_csv='__unused__.csv',skip_preflight=True);calls=[]
    class Request:
        def get(self,url,**kwargs):
            calls.append(url);return SimpleNamespace(status=200,text=lambda:ld('NL'),dispose=lambda:None)
    resolver=SessionCountryResolver(SimpleNamespace(request=Request()),scanner,'scoped fixture')
    monkeypatch.setattr('sponsorscout.core.country_details.time',SimpleNamespace(sleep=lambda t:None))
    url='https://fixture.invalid/jobs/123456-legal-counsel.html'
    assert resolver.resolve(url,'Dordrecht')[0]=='Netherlands'
    assert resolver.resolve(url,'Dordrecht')[0]=='Netherlands'
    assert calls==[url]and resolver.counts['reused']==1
    assert scanner._detail_state.coverage('scoped fixture',[url])['readable']==0


def test_structured_country_outranks_derived_place_but_manual_survives(tmp_path):
    path=tmp_path/'country.db';db.initialize(path);conn=db.get_connection(path)
    row=dict(url='https://fixture.invalid/jobs/123456-role.html',title='Backend Engineer',company='Fixture',location='Hoofddorp',country='Germany',country_source='structured_job_country')
    persistence.upsert_job(conn,row)
    assert conn.execute('SELECT country FROM jobs').fetchone()[0]=='Germany'
    conn.execute("UPDATE jobs SET country='Brazil',country_source='manual'");conn.commit()
    persistence.upsert_job(conn,dict(row,country='Netherlands'))
    assert conn.execute('SELECT country FROM jobs').fetchone()[0]=='Brazil';conn.close()


def historical_db(path):
    db.initialize(path);conn=db.get_connection(path)
    records=[dict(url='https://fixture.invalid/jobs/123451-engineer.html',title='Engineer',location='Zoetermeer'),
             dict(url='https://fixture.invalid/jobs/123452-engineer.html',title='Engineer',location='Dordrecht'),
             dict(url=ALERT,title=ALERT_TITLE,location='Unknown')]
    for r in records:
        if r['url']==ALERT:
            conn.execute('INSERT INTO jobs(url,title,company,location,country,country_source,verified_active,is_expired)VALUES(?,?,?,?,?,?,1,0)',(r['url'],r['title'],'Fixture',r['location'],'','none'))
        else:
            persistence.upsert_job(conn,dict(r,company='Fixture',country='',country_source='none'))
        conn.execute("UPDATE jobs SET country='',country_source='none'WHERE url=?",(r['url'],))
    conn.commit();conn.close();return records


def test_explicit_review_backup_audit_no_deletion_or_history_rewrite(tmp_path):
    path=tmp_path/'review.db';records=historical_db(path);before=db.get_connection(path)
    before.execute("INSERT INTO scan_runs(run_id,method,status,jobs_found)VALUES('historical','assisted','partial',3)");before.commit();before.close()
    preview=preview_data_review(path)
    assert(preview['country_fills'],preview['non_job_exclusions'],len(preview['unresolved']))==(1,1,1)
    with pytest.raises(ValueError):apply_data_review(path,plan_sha256=preview['plan_sha256'])
    result=apply_data_review(path,plan_sha256=preview['plan_sha256'],confirm=True)
    assert hashlib.sha256(Path(result['backup']).read_bytes()).hexdigest()==result['backup_sha256']
    saved=sqlite3.connect(result['backup']);assert saved.execute('SELECT COUNT(*)FROM jobs').fetchone()[0]==3
    assert saved.execute("SELECT country FROM jobs WHERE url=?",(records[0]['url'],)).fetchone()[0]=='';saved.close()
    conn=db.get_connection(path)
    assert conn.execute('SELECT COUNT(*)FROM jobs').fetchone()[0]==3
    assert conn.execute('SELECT verified_active FROM jobs WHERE url=?',(ALERT,)).fetchone()[0]==0
    assert conn.execute('SELECT jobs_found FROM scan_runs').fetchone()[0]==3
    audit=json.loads(conn.execute('SELECT audit_json FROM maintenance_audit').fetchone()[0]);assert audit['state']=='committed'and len(audit['actions'])==2
    conn.close();assert len(db.search_jobs(path,company='Fixture'))==2
    assert not preview_data_review(path)['actions']


def test_review_protects_manual_real_country_and_saved_application(tmp_path):
    path=tmp_path/'protected.db';rows=historical_db(path);conn=db.get_connection(path)
    conn.execute("UPDATE jobs SET country_source='manual'WHERE url=?",(rows[0]['url'],))
    conn.commit();conn.close();db.upsert_application(path,ALERT,'Fixture',ALERT_TITLE)
    preview=preview_data_review(path)
    assert not preview['actions']and len(preview['protected'])==2


def test_review_stale_plan_rejected_before_changes(tmp_path):
    path=tmp_path/'stale.db';rows=historical_db(path);plan=preview_data_review(path)
    conn=db.get_connection(path);conn.execute("UPDATE jobs SET country='Brazil',country_source='manual'WHERE url=?",(rows[0]['url'],));conn.commit();conn.close()
    with pytest.raises(RuntimeError,match='changed since preview'):apply_data_review(path,plan_sha256=plan['plan_sha256'],confirm=True)
    assert not(path.parent/'maintenance_backups').exists()
    assert len(db.search_jobs(path,company='Fixture'))==3


def test_external_writer_during_backup_aborts_and_preserves_backup(tmp_path,monkeypatch):
    import sponsorscout.core.data_review as review
    path=tmp_path/'race.db';historical_db(path);plan=preview_data_review(path);original=review._write_audit
    def write(destination,document):
        original(destination,document)
        if document['state']=='planned':
            other=db.get_connection(path);other.execute("UPDATE jobs SET location='Changed externally'WHERE url=?",(ALERT,));other.commit();other.close()
    monkeypatch.setattr(review,'_write_audit',write)
    with pytest.raises(RuntimeError,match='changed during backup'):apply_data_review(path,plan_sha256=plan['plan_sha256'],confirm=True)
    assert len(db.search_jobs(path,company='Fixture'))==3
    audit=json.loads(next((tmp_path/'maintenance_backups').glob('*.json')).read_text());assert audit['state']=='rolled_back'and not audit['actions']
    assert hashlib.sha256(Path(audit['backup']).read_bytes()).hexdigest()==audit['backup_sha256']


def test_explicit_review_dialog_preview_and_apply_are_responsive(window,monkeypatch,heartbeat):
    from sponsorscout.ui.data_review import DataReviewDialog
    from PySide6.QtWidgets import QMessageBox
    # Reuse full window DB, but never invoke preview on startup/Scan.
    conn=db.get_connection(window.db_path)
    conn.execute('INSERT INTO jobs(url,title,company,location,country,country_source,verified_active,is_expired)VALUES(?,?,?,?,?,?,1,0)',(ALERT,ALERT_TITLE,'Fixture','Unknown','','none'));conn.commit()
    conn.close();dialog=DataReviewDialog(window.db_path,window)
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.Yes)
    dialog.preview();wait_loaders(dialog)
    assert dialog.plan['non_job_exclusions']==1
    a=time.monotonic();dialog.apply();assert time.monotonic()-a<.1
    wait_until(lambda:not dialog._applying)
    assert not db.search_jobs(window.db_path,company='Fixture')and max_gap(heartbeat)<.3
    dialog.reject();dialog.deleteLater();application().processEvents()


@pytest.mark.skipif(os.environ.get('SPONSORSCOUT_TEST_BROWSER')!='1' or (not os.environ.get('DISPLAY')and os.name!='nt'),reason='Developer-only real headed Chromium/Xvfb')
@pytest.mark.parametrize('scenario',['normal','count_mismatch','country_blocked','wall13'])
def test_real_terminal586_59_no_next_with_saved_search_overlay(window,tmp_path,monkeypatch,heartbeat,scenario,request):
    from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
    from urllib.parse import urlsplit,parse_qs
    from playwright.sync_api import BrowserType,BrowserContext,Page
    from sponsorscout.ui.tabs import assisted_ingestion as ui
    locations=['Rotterdam']*379+[place for place,count in PLACE_COUNTS.items()for _ in range(count)]
    assert len(locations)==586
    calls=[];details=[];cookies=[];launches=[]
    declared=600 if scenario=='count_mismatch'else 586
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            parsed=urlsplit(self.path);calls.append(self.path)
            status=200
            if parsed.path.startswith('/jobs/'):
                details.append(self.path);cookies.append(self.headers.get('Cookie',''))
                if scenario=='country_blocked':status=403;html='<title>Just a moment...</title><h1>Verify you are human</h1><div id="cf-challenge-running"></div>'
                else:html=ld('NL')
            else:
                page=int(parse_qs(parsed.query).get('p',['0'])[0])+1
                if page==13 and scenario=='wall13':
                    html='<title>Just a moment...</title><h1>Verify you are human</h1><div id="cf-challenge-running"></div>'+''.join(f'<article><a href="/jobs/{9000000+i}-recommended.html">Recommended Engineer {i}</a></article>'for i in range(10))
                else:
                    html='<title>Vacancies</title>'+f'<h1>{declared} job results</h1>'
                    if page<=59:
                        for i in range((page-1)*10,min(page*10,586)):
                            # Include a generic legal department/prefix-ID shape.
                            html+=f'<article class="job-card"><h2><a href="/jobs/{1000000+i}-backend-engineer.html">Backend Engineer {i}</a></h2><p>Location: {locations[i]}</p><p>Responsibilities: Python services and reliable automated tests.</p></article>'
                    html+='<section class="search-result"><a href="/legacy-content/overlay/saved-searches/save-search.html">Maak een vacaturemelding aan</a></section>'
                    start=((min(page,59)-1)//10)*10+1
                    html+='<nav class="pagination">'
                    if page>1:html+='<a aria-label="Previous" href="?p='+str(page-2)+'">&lt;</a>'
                    for n in range(start,min(start+10,60)):
                        html+=f'<a class="{"current"if n==min(page,59)else""}" aria-current="{"page"if n==min(page,59)else"false"}" href="?p={n-1}">{n}</a>'
                    if page<59:html+=f'<a aria-label="Next" href="?p={page}">&gt;</a>'
                    html+='</nav>' # actual terminal OMITs Next, no disabled fake control
            raw=('<html><body>'+html+'</body></html>').encode();self.send_response(status)
            self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Set-Cookie','fixture-session=yes; Path=/')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}';conn=db.get_connection(window.db_path)
    for i in range(120):persistence.upsert_job(conn,dict(url=base+f'/jobs/{1000000+i}-backend-engineer.html',title=f'Backend Engineer {i}',company='Fixture',location=locations[i],country='Netherlands',country_source='provided'))
    conn.close();launch0=BrowserType.launch;page0=BrowserContext.new_page;wait0=Page.wait_for_timeout
    monkeypatch.setattr(BrowserType,'launch',lambda self,*a,**k:(launches.append(k),launch0(self,*a,**k))[1])
    def new_page(self,*a,**k):
        def guard(route):
            assert urlsplit(route.request.url).hostname in('127.0.0.1','localhost')
            route.continue_()
        self.route('**/*',guard);return page0(self,*a,**k)
    monkeypatch.setattr(BrowserContext,'new_page',new_page)
    monkeypatch.setattr(Page,'wait_for_timeout',lambda page,ms:wait0(page,min(ms,5)))
    class Clock:
        def __getattr__(self,key):return getattr(time,key)
        def sleep(self,seconds):time.sleep(min(seconds,.005))
    monkeypatch.setattr(bp,'time',Clock())
    monkeypatch.setattr('sponsorscout.core.session_details.time',Clock())
    original_engine=bp.run_assisted_ingestion
    def engine(*a,**k):k['clear_wait_s']=.2;return original_engine(*a,**k)
    monkeypatch.setattr(bp,'run_assisted_ingestion',engine)
    monkeypatch.setattr(ui.QMessageBox,'information',lambda *a,**k:None)
    tab=window.assisted_tab;window.tabs.setCurrentWidget(tab);tab.url_edit.setText(base+'/careers?q=');tab.name_edit.setText('Fixture')
    try:
        tab._start_ingestion();worker=tab._worker;wait_until(lambda:tab._worker is None,150)
        report=worker.report;genuine=120 if scenario=='wall13'else 586
        assert report['harvested']==genuine and report['duplicates']==120 and worker.count==genuine-120
        assert report['pages_visited']==(12 if scenario=='wall13'else 59)
        assert report['completed']is(scenario=='normal')
        assert len(db.search_jobs(window.db_path,company='Fixture'))==genuine
        conn=db.get_connection(window.db_path)
        assert conn.execute("SELECT COUNT(*)FROM jobs WHERE url LIKE '%save-search%'").fetchone()[0]==0
        assert not conn.execute('SELECT * FROM jobs WHERE title=?',(ALERT_TITLE,)).fetchone()
        conn.close()
        if scenario=='normal':
            assert not any('p=59' in c for c in calls if not c.startswith('/jobs/'))
            assert report['country_resolved']==586 and not report['country_unresolved']
            assert len(details)==586 and all('fixture-session=yes'in value for value in cookies)
            assert report['terminal_evidence']['kind']=='terminal_pager_reconciled'
            assert report['initial_response']and len(report['page_summaries'])>=59
        elif scenario=='country_blocked':
            assert report['listing_completed']and report['country_resolved']==544 and report['country_unresolved']==42
            assert len(details)==2 and report['session_detail_activity']['blocked']==1
            assert report['access_deferred']==585  # first unresolved JD gate, not a second needless wait
        assert len(launches)==1 and launches[0]['headless']is False and'--disable-gpu'in launches[0]['args']
        assert max_gap(heartbeat)<.3
        for key in('harvested','duplicates','pages_visited','completed','country_resolved','country_unresolved'):request.node.user_properties.append((key,str(report[key])))
    finally:
        if tab._worker is not None:tab.cancel_ingestion();wait_until(lambda:tab._worker is None,20)
        server.shutdown();server.server_close();thread.join(2)


def test_new_observation_guard_rejects_admin_without_touching_existing(tmp_path):
    path=tmp_path/'guard.db';historical_db(path);conn=db.get_connection(path)
    assert persistence.upsert_job(conn,dict(url=ALERT,title=ALERT_TITLE,company='Fixture',location='Amsterdam'))is False
    assert conn.execute('SELECT verified_active FROM jobs WHERE url=?',(ALERT,)).fetchone()[0]==1
    fresh='https://fixture.invalid/saved-searches/new-search.html'
    assert persistence.upsert_job(conn,dict(url=fresh,title=ALERT_TITLE,company='Fixture'))is False
    assert not conn.execute('SELECT 1 FROM jobs WHERE url=?',(fresh,)).fetchone()
    conn.close()


def test_review_refuses_active_ingestion_without_backup_or_changes(tmp_path):
    from sponsorscout.core.activity import ingestion_activity
    path=tmp_path/'ingestion.db';historical_db(path);plan=preview_data_review(path)
    with ingestion_activity(path):
        with pytest.raises(RuntimeError,match='Finish ingestion'):apply_data_review(path,plan_sha256=plan['plan_sha256'],confirm=True)
    assert not(path.parent/'maintenance_backups').exists()
    assert len(db.search_jobs(path,company='Fixture'))==3


def test_review_audit_write_failure_prevents_all_live_changes(tmp_path,monkeypatch):
    import sponsorscout.core.data_review as review
    path=tmp_path/'audit_failure.db';historical_db(path);plan=preview_data_review(path)
    monkeypatch.setattr(review,'_write_audit',lambda *a,**k:(_ for _ in ()).throw(OSError('fixture audit unavailable')))
    with pytest.raises(OSError,match='audit unavailable'):apply_data_review(path,plan_sha256=plan['plan_sha256'],confirm=True)
    assert len(db.search_jobs(path,company='Fixture'))==3
    backup=next((path.parent/'maintenance_backups').glob('*.sqlite3'))
    conn=sqlite3.connect(backup);assert conn.execute('SELECT COUNT(*)FROM jobs').fetchone()[0]==3;conn.close()
