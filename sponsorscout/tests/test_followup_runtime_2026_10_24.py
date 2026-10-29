"""Permanent offline runtime proofs for the approved 24 October follow-up.

No seed changes, real portal traffic, implicit browsers or taxonomy retuning.
The opt-in local Chromium/30k developer harness lives outside the application.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from sponsorscout.core import maintenance, persistence
from sponsorscout.core.activity import ingestion_activity
from sponsorscout.core.job_identity import posting_identity, read_job_csv, write_job_csv
from sponsorscout.db import database as db
from sponsorscout.insights import queries, terms
from sponsorscout.scanning.career import career_scanner as career


@pytest.fixture
def runtime_db(tmp_path):
    path=tmp_path/'runtime.db'
    db.initialize(path)
    return path


def _legacy_aliases(path,conflict=False):
    """Simulate rows stored BEFORE the posting-key migration, not new upserts."""
    with db.get_connection(path) as conn:
        for index,url in enumerate(('https://board.example/en/job-search/jobs/499509',
                                    'https://board.example/en/jobs/backend-engineer-499509')):
            conn.execute('INSERT INTO jobs(id,title,company,url,description,source_name,source_subtype,country,country_source,location,verified_active,visa_sponsorship,support_evidence) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (index+1,'Backend Engineer' if index else 'Permanent','Actual Employer',url,
                 'Full description with Python and PostgreSQL. '*100 if index else 'Short.',
                 'Seed B' if index else 'Seed A','direct' if index else 'recruiter',
                 'Germany' if conflict and index else 'Brazil','manual' if (index or conflict) else 'provided',
                 'Berlin, Germany',1,'Yes' if index else 'Unknown',
                 'Visa sponsorship is provided.' if index else ''))
        conn.execute('INSERT INTO job_terms(job_id,kind,term) VALUES(1,\'skill\',\'Python\')')
        conn.execute('INSERT INTO job_terms(job_id,kind,term) VALUES(2,\'skill\',\'PostgreSQL\')')
        conn.execute("INSERT INTO applications(job_url,company,title,status,notes) VALUES(?, 'Actual Employer', 'Backend Engineer', 'applied', 'Saved evidence and notes')",
                     ('https://board.example/en/jobs/backend-engineer-499509',))
        conn.commit()


def test_cleanup_is_explicit_restorable_audited_and_preserves_applications(runtime_db,tmp_path):
    _legacy_aliases(runtime_db)
    with db.get_connection(runtime_db) as conn:
        applications=[tuple(r) for r in conn.execute('SELECT * FROM applications')]
    preview=maintenance.cleanup_database(runtime_db)
    assert preview['merge_rows']==1
    with db.get_connection(runtime_db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==2
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='maintenance_audit'").fetchone()
    result=maintenance.cleanup_database(runtime_db,confirm=True,artifact_dir=tmp_path/'backups')
    assert result['merged']==1
    backup=Path(result['backup'])
    audit=json.loads(Path(result['audit']).read_text(encoding='utf-8'))
    assert audit['state']=='committed'
    assert audit['backup_sha256']==hashlib.sha256(backup.read_bytes()).hexdigest()
    with sqlite3.connect(backup) as restored:
        assert restored.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert restored.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==2
        assert restored.execute('SELECT COUNT(*) FROM applications').fetchone()[0]==1
    with db.get_connection(runtime_db) as conn:
        row=dict(conn.execute('SELECT * FROM jobs').fetchone())
        assert row['id']==1 and row['url'].endswith('/job-search/jobs/499509')
        assert row['title']=='Backend Engineer' and len(row['description'])>4000
        assert row['source_name']=='Seed A; Seed B'
        assert (row['country'],row['country_source'])==('Brazil','manual')
        assert row['source_subtype']=='direct' and row['visa_sponsorship']=='Yes'
        assert row['insights_dirty']==1 and row['posting_key']==posting_identity(row['url'])
        assert {r[0] for r in conn.execute('SELECT term FROM job_terms')}=={'Python','PostgreSQL'}
        assert [tuple(r) for r in conn.execute('SELECT * FROM applications')]==applications
        assert json.loads(conn.execute('SELECT audit_json FROM maintenance_audit').fetchone()[0])['merged']==1
    assert len(db.get_job_for_tailoring(runtime_db,'https://board.example/en/jobs/backend-engineer-499509')['description'])>4000
    assert maintenance.cleanup_database(runtime_db,confirm=True)['merged']==0


def test_cleanup_skips_manual_conflicts_and_unrelated_numeric_or_title_matches(runtime_db):
    _legacy_aliases(runtime_db,conflict=True)
    with db.get_connection(runtime_db) as conn:
        for number,url in enumerate(('https://board.example/vacancies/1234/12345',
                                      'https://board.example/vacancies/9876/12345'),3):
            conn.execute('INSERT INTO jobs(id,title,company,url,verified_active) VALUES(?,?,?,?,1)',
                         (number,'Backend Engineer','Actual Employer',url))
        conn.commit()
    preview=maintenance.preview_cleanup(runtime_db)
    assert preview['merge_rows']==0 and len(preview['conflicts'])==1
    assert maintenance.cleanup_database(runtime_db,confirm=True)['merged']==0
    with db.get_connection(runtime_db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==4


def test_cleanup_rolls_back_on_write_failure_and_never_mutates_without_backup(runtime_db,tmp_path,monkeypatch):
    _legacy_aliases(runtime_db)
    monkeypatch.setattr(persistence,'upsert_job',lambda *a,**k: (_ for _ in ()).throw(sqlite3.OperationalError('fixture write failure')))
    with pytest.raises(sqlite3.OperationalError,match='fixture'):
        maintenance.cleanup_database(runtime_db,confirm=True,artifact_dir=tmp_path/'backups')
    assert len(list((tmp_path/'backups').glob('*.sqlite3')))==1
    audit=json.loads(next((tmp_path/'backups').glob('*.json')).read_text(encoding='utf-8'))
    assert audit['state']=='rolled_back'
    with db.get_connection(runtime_db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==2
        assert conn.execute('SELECT COUNT(*) FROM job_terms').fetchone()[0]==2


def test_cleanup_refuses_while_ingesting_or_backup_fails(runtime_db,tmp_path,monkeypatch):
    _legacy_aliases(runtime_db)
    with ingestion_activity(runtime_db),pytest.raises(RuntimeError,match='Finish'):
        maintenance.cleanup_database(runtime_db,confirm=True)
    monkeypatch.setattr(maintenance,'_write_audit',lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError,match='disk full'):
        maintenance.cleanup_database(runtime_db,confirm=True,artifact_dir=tmp_path/'backups')
    with db.get_connection(runtime_db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==2


def test_same_url_and_alias_refresh_preserve_strong_evidence_and_derived_flags(runtime_db):
    old={'title':'Backend Engineer','company':'Actual Employer','url':'https://board.example/en/jobs/499509',
         'description':'A full job description. '*100,'source_name':'Seed A','source_subtype':'direct',
         'location':'Berlin, Germany','visa_sponsorship':'Yes','eu_blue_card_verdict':'Yes',
         'relocation_support':'Yes','support_evidence':'Visa sponsorship and relocation are provided.',
         'blue_card_evidence':'We support an EU Blue Card.','support_confidence':0.9}
    with db.get_connection(runtime_db) as conn:
        persistence.upsert_job(conn,old)
        for url in (old['url'],'https://board.example/en/jobs/backend-engineer-499509'):
            persistence.upsert_job(conn,{'url':url,'title':'Permanent','company':'Agency',
                'description':'Short refresh','source_name':'Seed B','source_subtype':'recruiter',
                'location':'Unknown','country':'Unknown','visa_sponsorship':'Unknown',
                'relocation_support':'Unknown','eu_blue_card_verdict':'Unknown'})
        rows=[dict(r) for r in conn.execute('SELECT * FROM jobs')]
    assert len(rows)==1
    row=rows[0]
    assert row['title']=='Backend Engineer' and row['description']==old['description']
    assert row['company']=='Actual Employer' and row['source_subtype']=='direct'
    assert row['source_name']=='Seed A; Seed B' and row['country']=='Germany'
    assert row['visa_sponsorship']==row['relocation_support']==row['eu_blue_card_verdict']=='Yes'
    assert row['eu_blue_card']==row['has_relocation']==1


def test_insights_snapshot_does_not_expand_when_another_row_becomes_dirty(runtime_db,monkeypatch):
    with db.get_connection(runtime_db) as conn:
        for i in range(3):
            persistence.upsert_job(conn,{'url':f'https://fixture.example/jobs/{i}','title':'Backend Engineer','description':'Python'})
    queries.backfill_existing_job_terms(runtime_db)
    with db.get_connection(runtime_db) as conn:
        conn.execute('UPDATE jobs SET insights_dirty=1 WHERE id IN (1,3)');conn.commit()
    progress=[]
    extractor=terms.extract_terms
    calls=[]
    def changed(*a,**k):
        calls.append(a)
        if len(calls)==1:
            with db.get_connection(runtime_db) as conn:
                conn.execute("UPDATE jobs SET description='SQL plus PostgreSQL' WHERE id=2");conn.commit()
        return extractor(*a,**k)
    monkeypatch.setattr(terms,'extract_terms',changed)
    assert queries.backfill_existing_job_terms(runtime_db,batch_size=1,progress_callback=lambda *a:progress.append(a))==2
    assert all(done<=total for done,total,phase in progress)
    assert progress[-1][:2]==(2,2)
    with db.get_connection(runtime_db) as conn:
        assert conn.execute('SELECT insights_dirty FROM jobs WHERE id=2').fetchone()[0]==1
    assert queries.backfill_existing_job_terms(runtime_db)==1


def test_insights_yields_without_locking_and_cancels_during_ingestion(runtime_db,monkeypatch):
    with db.get_connection(runtime_db) as conn:
        persistence.upsert_job(conn,{'url':'https://fixture.example/jobs/a','title':'Specialist','description':'Python'})
    cancelled=threading.Event();waiting=threading.Event();results=[]
    def progress(done,total,phase):
        if phase=='waiting':waiting.set()
    with ingestion_activity(runtime_db):
        worker=threading.Thread(target=lambda:results.append(queries.backfill_existing_job_terms(runtime_db,cancel_event=cancelled,progress_callback=progress)))
        worker.start()
        assert waiting.wait(3)
        with db.get_connection(runtime_db) as conn:
            persistence.upsert_job(conn,{'url':'https://fixture.example/jobs/b','title':'Another Specialist','description':'SQL'})
        cancelled.set();worker.join(3)
        assert not worker.is_alive()
    assert results==[0] and queries.has_pending_insights(runtime_db)


def test_missing_taxonomy_error_is_cached_without_repeated_growth(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('_isolated_taxonomy',terms.__file__)
    isolated=importlib.util.module_from_spec(spec);spec.loader.exec_module(isolated)
    monkeypatch.setattr(isolated,'_find_data_file',lambda name:tmp_path/name)
    failures=[]
    for _ in range(3):
        with pytest.raises(isolated.InsightsDataError) as error:
            isolated.extract_skills('Python')
        failures.append(str(error.value))
    assert len(set(failures))==1 and isolated._CATEGORY_REGEXES==[] and isolated._SKILLS==[]


def test_free_title_cache_reuse_even_after_title_and_shared_request_caps(monkeypatch):
    scanner=career.CareerPortalScanner(input_csv='__unused__.csv')
    url='https://fixture.example/jobs/100001'
    scanner._remember_detail_html(url,'<h1>Backend Engineer</h1><p>Location: Berlin, Germany</p>')
    monkeypatch.setenv('SPONSORSCOUT_TITLE_RESCUE_MAX','0')
    monkeypatch.setenv('SPONSORSCOUT_TITLE_RESCUE_PER_COMPANY','0')
    monkeypatch.setattr(scanner,'_fetch_jd_text_uncached',lambda *a:pytest.fail('cached title needs no HTTP'))
    assert scanner._title_from_detail_page(url)=='Backend Engineer'
    assert scanner._title_rescue_used==0 and scanner._detail_state.total==0


def test_parallel_location_title_support_share_one_actual_http_read(monkeypatch):
    import time
    scanner=career.CareerPortalScanner(input_csv='__unused__.csv')
    url='https://fixture.example/jobs/100001';calls=[]
    html='<h1>Backend Engineer</h1><div>Location: Berlin, Germany</div><p>'+('We hire Python developers. Visa sponsorship is available. '*12)+'</p>'
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def read(self,*a):return html.encode()
    def fetch(*a,**k):
        calls.append(a);time.sleep(.04);return Response()
    monkeypatch.setattr('urllib.request.urlopen',fetch)
    class Cache:
        def get(self,*a):return None
        def put(self,*a):pass
    monkeypatch.setattr(career,'_jd_cache',Cache)
    work=[lambda:scanner._fetch_jd_text(url,allow_browser=False),
          lambda:scanner._detail_location_from_url(url,allow_browser=False),
          lambda:scanner._title_from_detail_page(url)]*4
    with ThreadPoolExecutor(max_workers=12) as pool:
        results=list(pool.map(lambda f:f(),work))
    assert len(calls)==1 and scanner._detail_state.total==1
    assert results[1] and results[2]=='Backend Engineer'


def test_parallel_dom_detail_calls_cannot_overrun_global_browser_cap(monkeypatch):
    scanner=career.CareerPortalScanner(input_csv='__unused__.csv')
    monkeypatch.setenv('SPONSORSCOUT_JD_BROWSER_MAX','1')
    calls=[]
    class Page:
        def goto(self,*a,**k):calls.append(a);raise RuntimeError('fixture failure')
    urls=[f'https://fixture-{i}.example/jobs/100001' for i in range(12)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        results=list(pool.map(lambda url:scanner._detail_enrich_one(Page(),url,{}),urls))
    assert len(calls)==scanner._jd_browser_used==1
    assert results.count('skipped')==11


def test_negative_http_result_is_shared_across_title_location_and_support(monkeypatch):
    import urllib.error
    scanner=career.CareerPortalScanner(input_csv='__unused__.csv');calls=[]
    class Cache:
        def get(self,*a):return None
        def put(self,*a):pass
    monkeypatch.setattr(career,'_jd_cache',Cache)
    def fail(req,**kwargs):
        calls.append(req.full_url);raise urllib.error.HTTPError(req.full_url,403,'blocked',{},None)
    monkeypatch.setattr('urllib.request.urlopen',fail)
    url='https://fixture.example/jobs/100001'
    for _ in range(3):
        assert scanner._detail_location_from_url(url,allow_browser=False) is None
        assert not scanner._title_from_detail_page(url)
        assert scanner._fetch_jd_text(url,allow_browser=False)==('','','')
    assert calls==[url]
    assert scanner._detail_state.total==1


def _seed_file(path,rows):
    fields=['name','careers_url','provider','board_slug','source_type','target_country','scope_policy','industry','notes']
    write_job_csv(path,[dict(zip(fields,row)) for row in rows],fields)


def test_actual_career_dns_retry_keeps_both_scopes_and_reconciles_final_csv(tmp_path,monkeypatch):
    seed=tmp_path/'seed.csv';output=tmp_path/'career_jobs.csv'
    _seed_file(seed,[('Scoped','https://fixture.example/jobs?country=BR','greenhouse','','direct','Brazil','global','',''),
                     ('Scoped','https://fixture.example/jobs?country=DE','greenhouse','','direct','Germany','global','','')])
    scanner=career.CareerPortalScanner(input_csv=str(seed),output_csv=str(output),skip_preflight=True,detail_scan=False)
    scanner.config.PROVIDER_SNIFF=False
    attempts={}
    def provider(target):
        url=target['careers_url'];attempts[url]=attempts.get(url,0)+1
        if attempts[url]==1:
            raise RuntimeError('Page.goto: net::ERR_NAME_NOT_RESOLVED')
        number='100001' if 'BR' in url else '100002'
        return [{'job_title':'Backend Engineer','job_url':'https://fixture.example/jobs/'+number,
                 'location_hint':'Berlin, Germany','extraction_method':'greenhouse_api',
                 'jd_text':'We need Python backend engineers in Berlin, Germany. Build reliable applications. '*20}],'fixture API: complete'
    monkeypatch.setattr(scanner,'_fetch_provider_jobs',provider)
    monkeypatch.setattr(scanner,'_preflight_connectivity',lambda *a,**k:pytest.fail('no network preflight'))
    monkeypatch.setattr('socket.getaddrinfo',lambda *a,**k:pytest.fail('no DNS probes'))
    scanner.execute_crawler()
    assert len(attempts)==2 and set(attempts.values())=={2}
    rows=read_job_csv(output);logs=read_job_csv(tmp_path/'career_jobs_scan_log.csv')
    assert len(rows)==2 and {r['Target Country'] for r in rows}=={'Brazil','Germany'}
    assert len(logs)==2 and {r['Attempts'] for r in logs}=={'2'}
    assert all(r['Status']=='ok' for r in logs)
    assert sum(int(r['Jobs Retained']) for r in logs)==2
    assert read_job_csv(tmp_path/'career_jobs_errors.csv')==[]


def test_final_reconciliation_counts_late_quarantine_and_source_merges(tmp_path):
    from sponsorscout.core.job_identity import finalize_job_csvs
    from sponsorscout.scanning.common import reconcile_scan_artifacts
    main,recruiter,quarantine,log=[tmp_path/f'{name}.csv' for name in ('main','recruiter','quarantine','log')]
    fields=['Run ID','Seed Name','Company Name','Source Type','Job Title','Job URL','Target Country','Record Status','Quarantine Reason']
    base=dict(zip(fields,['RUN','Employer','Employer','direct_employer','Backend Engineer','https://fixture.example/jobs/100001','Global','accepted','']))
    write_job_csv(main,[base],fields)
    write_job_csv(recruiter,[dict(base,**{'Seed Name':'Agency','Source Type':'recruiter'})],fields)
    write_job_csv(quarantine,[dict(base,**{'Job URL':'https://fixture.example/jobs/bad','Record Status':'quarantine','Quarantine Reason':'late validation'})],fields)
    logs=[{'Run ID':'RUN','Seed Name':name,'Company':name,'Seed URL':'https://fixture.example/jobs','Target Country':'Global','Jobs Found':1,'Status':'ok','Error':'','Diagnostics':'','Quarantined':0} for name in ('Employer','Agency')]
    write_job_csv(log,logs,list(logs[0]))
    merged=finalize_job_csvs(main,recruiter,fields)
    summary=reconcile_scan_artifacts(log,main,quarantine,'RUN',merged)
    final=read_job_csv(log)
    assert summary['written']==1 and summary['quarantined']==summary['merged']==1
    assert not recruiter.exists() and read_job_csv(main)[0]['Seed Name']=='Employer; Agency'
    assert sum(int(r['Jobs Retained']) for r in final)==1
    assert sum(int(r['Jobs Merged']) for r in final)==1
    assert sum(int(r['Quarantined']) for r in final)==1
    assert main.read_bytes().startswith(b'\xef\xbb\xbf')


def test_assisted_worker_clears_old_report_before_connection_failure(monkeypatch,tmp_path):
    from sponsorscout.core import blocked_portals
    from sponsorscout.ui.tabs.assisted_ingestion import AssistedWorker
    monkeypatch.setattr(blocked_portals,'LAST_INGESTION_REPORT',{'pages_visited':999,'completed':True})
    monkeypatch.setattr(db,'get_connection',lambda *a:(_ for _ in ()).throw(sqlite3.OperationalError('fixture unavailable')))
    worker=AssistedWorker('https://fixture.example/jobs','Fixture',str(tmp_path/'absent.db'))
    worker.run()
    assert worker.report=={} and worker.count==0 and 'fixture unavailable' in worker.error


def test_assisted_commit_retry_never_counts_a_rolled_back_write(runtime_db,monkeypatch):
    from sponsorscout.core import blocked_portals
    original=persistence.upsert_job;calls=[]
    def locked_once(*a,**k):
        calls.append(a)
        if len(calls)==1:raise sqlite3.OperationalError('database is locked')
        return original(*a,**k)
    monkeypatch.setattr(persistence,'upsert_job',locked_once)
    record={'title':'Backend Engineer','company':'Fixture','url':'https://fixture.example/jobs/100001'}
    with db.get_connection(runtime_db) as conn:
        assert blocked_portals._persist_assisted_job(conn,record) is False
        assert blocked_portals._persist_assisted_job(conn,record) is True
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1
    assert len(calls)==3


def test_trusted_alias_prefers_role_heading_over_recruiting_prose_without_losing_jd(runtime_db):
    from sponsorscout.core.job_identity import merge_job_rows
    rows=[{'Job URL':'https://fixture.example/en/job-search/jobs/771122',
           'Job Title':'President','Seed Name':'Seed A','Raw Description':'Short details.',
           'Source Type':'direct_employer'},
          {'Job URL':'https://fixture.example/en/jobs/president-771122',
           'Job Title':'An opportunity to lead, manage and grow our global business!',
           'Seed Name':'Seed B','Raw Description':'Full detailed job description. '*120,
           'Source Type':'recruiter'}]
    merged,_=merge_job_rows(rows)
    assert len(merged)==1 and merged[0]['Job Title']=='President'
    assert merged[0]['Raw Description']==rows[1]['Raw Description']
    assert merged[0]['Seed Name']=='Seed A; Seed B'
    conn=db.get_connection(runtime_db)
    try:
        for row in rows:
            persistence.upsert_job(conn,{'url':row['Job URL'],'title':row['Job Title'],
                'company':'Observed Employer','description':row['Raw Description'],
                'source_subtype':'direct','source_name':row['Seed Name']})
        saved=conn.execute('SELECT title,description,source_name FROM jobs').fetchone()
        assert saved['title']=='President' and saved['description']==rows[1]['Raw Description']
        assert saved['source_name']=='Seed A; Seed B'
    finally:conn.close()


@pytest.mark.parametrize('phase', ['ats','career'])
def test_both_scanners_reuse_richest_captured_jd_only_for_trusted_aliases(phase):
    from sponsorscout.scanning.ats.ats_scanner import ATSScanner
    scanner=(ATSScanner(seed_file='__unused__.csv',skip_preflight=True) if phase=='ats'
             else career.CareerPortalScanner(input_csv='__unused__.csv',skip_preflight=True))
    bare='https://fixture.example/en/job-search/jobs/771122'
    slug='https://fixture.example/en/jobs/president-771122'
    full=('A detailed employer-published description with Python and SQL. '*100).strip()
    scanner.store_captured_jd(bare,'Short statement.')
    scanner.store_captured_jd(slug,full)
    assert scanner.get_captured_jd(bare)==scanner.get_captured_jd(slug)==full
    assert scanner.get_captured_jd(bare+'?requisition=other')==''
    assert scanner.get_captured_jd(bare+'#job=another')==''


def test_assisted_error_rows_are_honest_for_partial_completed_and_cancelled_sessions():
    from sponsorscout.core.blocked_portals import _assisted_error_rows
    url='https://fixture.example/jobs'
    rows=_assisted_error_rows([{'url':url+'/100001','error':'fixture write failure'}],
                              'TargetClosedError',url,'browser closed',False,False)
    assert rows==[{'error':'database_write','url':url+'/100001','detail':'fixture write failure'},
                  {'error':'browser_session','url':url,'detail':'TargetClosedError'}]
    assert _assisted_error_rows([],'',url,'end reached',True,False)==[]
    assert _assisted_error_rows([],'',url,'cancelled by user',False,True)==[]
    assert _assisted_error_rows([],'',url,'no human clearance',False,False)==[
        {'error':'incomplete_session','url':url,'detail':'no human clearance'}]


def test_cleanup_excludes_local_ingestion_and_aborts_external_changes_not_in_backup(runtime_db,tmp_path,monkeypatch):
    _legacy_aliases(runtime_db)
    planned=threading.Event();release=threading.Event();outcome={}
    original=maintenance._write_audit
    def paused(path,document):
        original(path,document)
        if document['state']=='planned':
            planned.set()
            assert release.wait(8)
    monkeypatch.setattr(maintenance,'_write_audit',paused)
    def cleanup():
        try:outcome['result']=maintenance.cleanup_database(runtime_db,confirm=True,artifact_dir=tmp_path/'backups')
        except Exception as exc:outcome['error']=exc
    worker=threading.Thread(target=cleanup);worker.start()
    try:
        assert planned.wait(4)
        with pytest.raises(RuntimeError,match='cleanup is in progress'):
            with ingestion_activity(runtime_db):pytest.fail('ingester entered during explicit cleanup')
        # Model an external program that does not participate in our local gate.
        with db.get_connection(runtime_db) as other:
            other.execute('INSERT INTO jobs(title,company,url,source_name) VALUES(?,?,?,?)',
                          ('Backend Engineer','Actual Employer','https://board.example/en/job-search/jobs/backend-engineer-499509','Seed C'))
            other.commit()
    finally:
        release.set();worker.join(8)
    assert not worker.is_alive() and 'result' not in outcome
    assert isinstance(outcome['error'],RuntimeError) and 'Database changed' in str(outcome['error'])
    backup=next((tmp_path/'backups').glob('*.sqlite3'))
    audit=json.loads(next((tmp_path/'backups').glob('*.json')).read_text(encoding='utf-8'))
    assert audit['state']=='rolled_back' and audit['actions']==[]
    assert audit['backup_sha256']==hashlib.sha256(backup.read_bytes()).hexdigest()
    with sqlite3.connect(backup) as restored:
        assert restored.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==2
    with db.get_connection(runtime_db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==3
    # A fresh explicit invocation backs up the new data BEFORE merging it.
    monkeypatch.setattr(maintenance,'_write_audit',original)
    result=maintenance.cleanup_database(runtime_db,confirm=True,artifact_dir=tmp_path/'retry_backups')
    assert result['merged']==2
    with sqlite3.connect(result['backup']) as restored:
        assert restored.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==3


def test_complementary_api_target_configs_retain_spontaneous_jobs_without_scope_collapse(tmp_path,monkeypatch):
    seed=tmp_path/'seed.csv';output=tmp_path/'career_jobs.csv'
    _seed_file(seed,[('Retail Fixture','https://fixture.example/careers/region/lazio','pam','region:Lazio','direct_employer','Italy','seed_url','',''),
                     ('Retail Fixture','https://fixture.example/careers/head-office','pam','sede','direct_employer','Italy','seed_url','','')])
    scanner=career.CareerPortalScanner(input_csv=str(seed),output_csv=str(output),skip_preflight=True,detail_scan=False)
    scanner.config.PROVIDER_SNIFF=False
    def item(number,title,city,state,department):
        return {'id':number,'status':'published','title':title,
                'description':'Employer-published job duties and requirements. '*20,
                'details':{'loc':{'city':{'1':{'value':city}},'state':{'1':{'value':state}}}},
                'custom_fields':{'pam_departments':department}}
    payload=[item('410001','Candidatura Spontanea','Roma','Lazio','negozi'),
             item('410002','Store Assistant','Roma','Lazio','negozi'),
             item('410003','Backend Engineer','Mestre','Veneto','sede')]
    monkeypatch.setattr(scanner,'_http_fetch_with_retry',lambda *a,**k:json.dumps(payload))
    monkeypatch.setattr(scanner,'_preflight_connectivity',lambda *a,**k:pytest.fail('No seed/network preflight'))
    monkeypatch.setattr(scanner,'_get_or_launch_worker_browser',lambda *a,**k:pytest.fail('Provider fixture needs no browser'))
    scanner.execute_crawler()
    rows=read_job_csv(output);logs=read_job_csv(tmp_path/'career_jobs_scan_log.csv')
    assert len(rows)==3 and 'Candidatura Spontanea' in {r['Job Title'] for r in rows}
    assert len(logs)==2 and {r['Seed URL'] for r in logs}=={r['careers_url'] for r in scanner.read_seed_file()}
    assert sorted(int(r['Jobs Retained']) for r in logs)==[1,2]
    assert sum(int(r['Quarantined']) for r in logs)==0
    assert all(r['Status']=='ok' for r in logs)
