"""ATS->Career completion handoff and honest accepted-write failure contract."""
from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace

import pytest

from sponsorscout.db import database as db
from sponsorscout.scanning import pipeline
from sponsorscout.core import blocked_portals as bp


def _csv(path, rows, fields=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def two_phase_scan(tmp_path, monkeypatch):
    from sponsorscout.application import seed_manager
    ats_seed, career_seed = tmp_path / 'ats_seed.csv', tmp_path / 'career_seed.csv'
    _csv(ats_seed, [dict(name=name,url='https://fixture.example/api?name='+name,country='Brazil') for name in ('Shared','Other')])
    _csv(career_seed, [dict(name=name,url='https://fixture.example/careers?name='+name,country='Germany') for name in ('Shared','Other')])
    monkeypatch.setattr(seed_manager,'ensure_user_seeds',lambda: pytest.fail('Existing seeds must not be reconciled at scan startup'))
    monkeypatch.setattr(seed_manager, 'user_ats_path', lambda: ats_seed)
    monkeypatch.setattr(seed_manager, 'user_career_path', lambda: career_seed)
    monkeypatch.setattr(pipeline.paths,'ensure_scan_output_dir',lambda:tmp_path/'exports')
    monkeypatch.setattr(bp, 'run_assisted_ingestion', lambda *a, **k:
                        pytest.fail('Regular scan started Assisted Ingestion'))
    monkeypatch.setattr(bp, 'open_in_default_browser', lambda *a, **k:
                        pytest.fail('Regular scan opened a visible external browser'))
    order = []

    def write_phase(path, phase, country, selected):
        jobs,records = [],[]
        for index,name in enumerate(('Shared','Other'),1):
            if selected is not None and name not in selected:
                continue
            job_id=(100000 if phase=='ats' else 200000)+index
            jobs.append({'Seed Name':name,'Company Name':'Observed Employer',
                         'Job Title':'Backend Engineer','Job URL':f'https://fixture.example/jobs/backend-engineer-{job_id}',
                         'Job Location':country,'Job Country':country,'Source Type':'direct_employer'})
            records.append({'Seed Name':name,'Company':name,'Target Country':country,
                            'Seed URL':f'https://fixture.example/board?name={name}&country={country}#open-roles',
                            'Status':'partial' if name=='Shared' else 'ok',
                            'Jobs Found':'1','Jobs Retained':'1','Provider':'fixture',
                            'Error':'Blocked by bot protection: page 2, please verify you are human' if name=='Shared' else ''})
        _csv(path,jobs)
        _csv(Path(path).with_name(Path(path).stem+'_scan_log.csv'),records)

    class FakeATS:
        def __init__(self, **kwargs):
            assert kwargs['skip_preflight'] is True
            self.output = kwargs['output_file']
            self.selected = kwargs['only_companies']
        def run(self):
            order.append('ats')
            write_phase(self.output,'ats','Brazil',self.selected)
        def get_captured_jd(self, url):
            return 'Python SQL API backend engineering. Full captured description for ' + url

    class FakeCareer:
        def __init__(self, **kwargs):
            assert kwargs['skip_preflight'] is True
            self.output = kwargs['output_csv']
            self.selected = kwargs['only_companies']
        def execute_crawler(self):
            order.append('career')
            write_phase(self.output,'career','Germany',self.selected)
        def get_captured_jd(self, url):
            return 'Python SQL API backend engineering. Full captured description for ' + url

    monkeypatch.setattr(pipeline, 'ats_module', SimpleNamespace(ATSScanner=FakeATS,progress_cb=None))
    monkeypatch.setattr(pipeline, 'career_module', SimpleNamespace(CareerPortalScanner=FakeCareer,progress_cb=None))
    path = tmp_path / 'handoff.db'
    db.initialize(path)
    return path, order


@pytest.mark.parametrize('custom', [False, True])
def test_both_phases_queue_scoped_blocks_continue_and_persist_without_browser(two_phase_scan, custom):
    path, order = two_phase_scan
    options = dict(only_ats=['Shared'], only_career=['Shared']) if custom else {}
    summary = pipeline.run_scan(db_path=path, **options)
    assert order == ['ats','career']
    accepted=2 if custom else 4
    assert summary['ingested'] == accepted and summary['status'] == 'partial'
    assert summary['targets_partial'] == 2
    assert len(summary['blocked_portals']) == 2
    rows = [dict(row) for row in db.list_blocked_portals(path)]
    assert len(rows) == 2
    assert {row['phase'] for row in rows} == {'ats','career'}
    assert {'Brazil','Germany'} == {
        'Brazil' if 'country=Brazil' in row['url'] else 'Germany' for row in rows}
    assert all(row['status'] == 'pending' and row['url'].endswith('#open-roles') for row in rows)
    # A fresh connection/restart sees the same queue; reinsertion only merges metadata.
    db.enqueue_blocked_portals(path,summary['blocked_portals'],run_id=summary['run_id'])
    assert len(db.list_blocked_portals(path)) == 2
    conn = db.get_connection(path)
    assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == accepted
    assert {row[0] for row in conn.execute('SELECT country FROM jobs')} == {'Brazil','Germany'}
    assert conn.execute('SELECT COUNT(*) FROM scan_runs').fetchone()[0] == 1
    conn.close()


def test_final_accepted_write_failures_cannot_report_completed(two_phase_scan, monkeypatch):
    path, _ = two_phase_scan
    monkeypatch.setattr(pipeline.persistence, 'upsert_job',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('fixture write failure')))
    summary = pipeline.run_scan(db_path=path)
    assert summary['ingested'] == 0 and summary['status'] == 'error'
    assert len(summary['ingestion_failures']) == 4
    assert all(row['stage'] == 'persistence' for row in summary['ingestion_failures'])
    stored = db.get_scan_run(path,summary['run_id'])
    assert 'accepted row(s) could not be saved' in stored['error']
    assert len(db.list_blocked_portals(path)) == 2


class _Signal:
    def __init__(self): self.count = 0
    def emit(self): self.count += 1


@pytest.mark.parametrize('navigate,queue_failure', [(False,False),(True,False),(False,True)])
def test_one_completion_notice_only_navigates_never_launches(monkeypatch, tmp_path, navigate, queue_failure):
    from sponsorscout.ui.tabs import tools
    from sponsorscout import i18n
    monkeypatch.setattr(i18n, '_locale', 'en')
    path = tmp_path / 'notice.db'
    db.initialize(path)
    summary = {'run_id':'notice-fixture','blocked_portals':[
        {'seed_name':'Blocked','url':'https://fixture.example/blocked?country=Brazil',
         'phase':'ats','reason':'CAPTCHA verification'}]}
    boxes = []
    class Box:
        Ok, ActionRole = 1, 2
        def __init__(self, parent): self.text=''; self.view=object(); boxes.append(self)
        def setWindowTitle(self, value): pass
        def setText(self, value): self.text=value
        def setStandardButtons(self, value): pass
        def addButton(self, *a): return self.view
        def setDefaultButton(self, value): pass
        def exec(self): pass
        def clickedButton(self): return self.view if navigate else None
    monkeypatch.setattr(tools, 'QMessageBox', Box)
    if queue_failure:
        monkeypatch.setattr(db, 'enqueue_blocked_portals', lambda *a, **k:
                            (_ for _ in ()).throw(RuntimeError('fixture queue unavailable')))
    monkeypatch.setattr(bp,'run_assisted_ingestion',lambda *a, **k: pytest.fail('Notice launched assisted browser'))
    monkeypatch.setattr(bp,'open_in_default_browser',lambda *a, **k: pytest.fail('Notice launched external browser'))
    from sponsorscout.tests._qt_support import notice_widget,dispose,wait_until
    harness=notice_widget(path)
    changed,nav=_Signal(),_Signal()
    harness.blocked_portals_changed.connect(changed.emit)
    harness.assisted_tab_requested.connect(nav.emit)
    tools.ToolsTab._offer_blocked_portals(harness,summary)
    wait_until(lambda:len(boxes)==1)
    assert len(boxes) == 1
    assert changed.count == 1
    assert nav.count == int(navigate)
    if queue_failure:
        assert 'could not be saved' in boxes[0].text
        assert 'have been added' not in boxes[0].text
    else:
        assert 'have been added' in boxes[0].text
        assert len(db.list_blocked_portals(path)) == 1
    dispose(harness)


_QT_APP = None


def test_live_language_switch_preserves_inputs_filters_and_idle_workers(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    from sponsorscout import i18n
    from sponsorscout.core import persistence
    from sponsorscout.insights import queries
    from sponsorscout.ui.tabs import assisted_ingestion as assisted_ui
    from sponsorscout.ui.tabs import insights as insights_ui
    global _QT_APP
    _QT_APP = QApplication.instance() or QApplication([])
    monkeypatch.setattr(i18n,'_locale','en')
    monkeypatch.setattr(i18n,'_I18N_CONFIG',tmp_path/'locale.json')
    path=tmp_path/'language.db'
    db.initialize(path)
    conn=db.get_connection(path)
    persistence.upsert_job(conn,dict(url='https://fixture.example/jobs/backend-100001',
        title='Backend Engineer',company='Observed Employer',description='Python SQL Docker',
        country='Brazil',country_source='provided',location='Brazil',source_name='Fixture'))
    conn.close()
    queries.backfill_existing_job_terms(path)
    db.enqueue_blocked_portals(path,[dict(url='https://fixture.example/board?country=Brazil#scope',
        seed_name='Fixture',phase='ats',reason='Blocked by bot protection: human verification')])
    launches=[]
    monkeypatch.setattr(assisted_ui.AssistedWorker,'start',lambda self:launches.append('assisted'))
    monkeypatch.setattr(insights_ui._InsightsRefreshWorker,'start',lambda self:launches.append('insights'))
    monkeypatch.setattr(bp,'open_in_default_browser',lambda url:launches.append('external'))
    assisted=assisted_ui.AssistedIngestionTab(str(path))
    insights=insights_ui.InsightsTab(str(path))
    from sponsorscout.tests._qt_support import wait_loaders
    wait_loaders(assisted);wait_loaders(insights)
    assisted.name_edit.setText('Typed company name')
    assisted.url_edit.setText('https://fixture.example/typed?filter=kept#job')
    assisted.source_type_combo.setCurrentIndex(1)
    insights.country_combo.setCurrentText('Brazil')
    insights.source_combo.setCurrentIndex(2)
    insights.active_pie_filter=('category','Backend Engineering')
    try:
        for language in ('it','en','it','en'):
            i18n.set_locale(language)
            assisted.retranslate()
            insights.retranslate()
            wait_loaders(assisted);wait_loaders(insights)
            assert assisted.name_edit.text()=='Typed company name'
            assert assisted.url_edit.text()=='https://fixture.example/typed?filter=kept#job'
            assert assisted.source_type_combo.currentData()=='recruiter'
            assert insights.country_combo.currentText()=='Brazil'
            assert insights.source_combo.currentData()=='recruiter'
            assert insights.active_pie_filter==('category','Backend Engineering')
            assert assisted._worker is None and insights._insights_worker is None
            assert assisted.portals_table.rowCount()==1
            assert assisted.launch_btn.text()==i18n._('Launch Assisted Browser and Ingest')
            assert insights.rebuild_btn.text()==i18n._('Rebuild all')
            assert assisted.portals_table.horizontalHeaderItem(2).text()==i18n._('Reason / Phase')
        assert not launches
    finally:
        assisted.shutdown()
        insights.shutdown()
        assisted.close(); insights.close()
        assisted.deleteLater(); insights.deleteLater()
        _QT_APP.processEvents()


def test_final_pipeline_keeps_richer_career_jd_over_short_ats_capture_for_same_posting(two_phase_scan,monkeypatch):
    from sponsorscout.core.job_identity import posting_identity,read_job_csv
    path,_=two_phase_scan
    bare='https://fixture.example/jobs/100001'
    full=('Full employer job description with Python, PostgreSQL and engineering responsibilities. '*80).strip()
    key=posting_identity(bare)
    ats_type=pipeline.ats_module.ATSScanner
    career_type=pipeline.career_module.CareerPortalScanner
    monkeypatch.setattr(ats_type,'get_captured_jd',lambda self,url:'Short API summary.' if posting_identity(url)==key else '')
    monkeypatch.setattr(career_type,'get_captured_jd',lambda self,url:full if posting_identity(url)==key else '')
    original=career_type.execute_crawler
    def career_run(self):
        original(self)
        rows=read_job_csv(self.output)
        assert len(rows)==1
        rows[0].update({'Job URL':bare,'Job Country':'Brazil','Job Location':'Brazil'})
        _csv(self.output,rows)
    monkeypatch.setattr(career_type,'execute_crawler',career_run)
    summary=pipeline.run_scan(db_path=path,only_ats=['Shared'],only_career=['Shared'])
    with db.get_connection(path) as conn:
        rows=conn.execute('SELECT title,description,country FROM jobs').fetchall()
    assert len(rows)==1 and rows[0]['description']==full
    assert rows[0]['country']=='Brazil'  # never the Germany target/HQ
    assert summary['ingested']==1
