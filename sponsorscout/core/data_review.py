"""Explicit preview/confirmation only: observed countries and non-job exclusion.

NEVER called by startup/Scan. No network, deletion, history rewrites or inferred
host/seed/HQ country. Backup+SHA+audit precede all writes; stale plans abort.
"""
from __future__ import annotations
import hashlib,json,sqlite3
from datetime import datetime,timezone
from pathlib import Path
from sponsorscout.core.activity import ingestion_active,maintenance_activity
from sponsorscout.core.job_identity import administrative_action,posting_identity
from sponsorscout.core.location_evidence import observed_geography
from sponsorscout.core.place_index import place_evidence
from sponsorscout.core.integrity import check_integrity
from sponsorscout.core.maintenance import _file_digest,_write_audit

_EMPTY={'','unknown','none','not specified','n/a','?'}
_COLUMNS='id,url,title,company,country,country_source,location,raw_location,source_name,source_type,verified_active,is_expired,visa_sponsorship,relocation_support,eu_blue_card_verdict,sponsorship_score'


def preview_connection(conn,company=''):
    sql='SELECT '+_COLUMNS+' FROM jobs WHERE verified_active=1 AND is_expired=0'
    params=[]
    if company.strip():
        sql+=" AND lower(company) LIKE ? ESCAPE '\\'"
        value=company.strip().casefold().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        params.append('%'+value+'%')
    rows=conn.execute(sql+' ORDER BY id',params).fetchall()
    applications={posting_identity(r[0]) for r in conn.execute('SELECT job_url FROM applications')}
    actions=[];protected=[];unresolved=[]
    for raw in rows:
        row=dict(raw);base={k:row.get(k)for k in('id','url','title','company','location','raw_location','country','country_source')}
        manual=str(row.get('country_source')or'').casefold()=='manual' or str(row.get('source_type')or'').casefold()=='manual'
        bad=administrative_action(row['url'],row['title'])
        if bad:
            if manual or posting_identity(row['url'])in applications:
                protected.append(dict(base,reason='manual country/record or saved application; never exclude automatically'))
            else:
                actions.append(dict(base,action='exclude_non_job',new_country=row.get('country')or'',reason='administrative saved-search/alert action; no positive posting identity'))
            continue
        if str(row.get('country')or'').strip().casefold()not in _EMPTY:
            continue  # richer/real countries, even if a new lookup disagrees
        if manual:
            protected.append(dict(base,reason='manual country is protected, including deliberately unknown'))
            continue
        location,country=observed_geography(row.get('location'),row.get('raw_location'),row.get('country_source'))
        if country:
            checked,violations=check_integrity(dict(row,country=country))
            if violations:
                protected.append(dict(base,reason='country-dependent support integrity needs separate review; no verdict changes applied'))
                continue
            actions.append(dict(base,action='fill_country',new_country=country,reason='observed job place; shared offline resolver',evidence=place_evidence(location)))
        else:
            unresolved.append(dict(base,reason='ambiguous/unresolved observed location',evidence=place_evidence(location)))
    document=dict(company_filter=company.strip(),actions=actions,protected=protected,unresolved=unresolved,
                  country_fills=sum(a['action']=='fill_country'for a in actions),non_job_exclusions=sum(a['action']=='exclude_non_job'for a in actions))
    document['plan_sha256']=hashlib.sha256(json.dumps(document,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return document


def preview_data_review(db_path,company=''):
    from sponsorscout.db.database import get_connection
    conn=get_connection(db_path)
    try:return preview_connection(conn,company)
    finally:conn.close()


def apply_data_review(db_path,*,plan_sha256,company='',confirm=False,artifact_dir=None):
    if not confirm:raise ValueError('Explicit preview confirmation is required')
    if ingestion_active(db_path):raise RuntimeError('Finish ingestion before applying data review')
    from sponsorscout.db.database import get_connection
    conn=get_connection(db_path)
    try:
        if conn.in_transaction:raise RuntimeError('Data review requires an idle connection')
        preview=preview_connection(conn,company)
        if preview['plan_sha256']!=plan_sha256:raise RuntimeError('Data changed since preview; preview and confirm again')
        if not preview['actions']:return dict(changed=0,country_fills=0,non_job_exclusions=0,backup='',audit='')
        directory=Path(artifact_dir)if artifact_dir else Path(db_path).parent/'maintenance_backups'
        directory.mkdir(parents=True,exist_ok=True)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup=directory/('pre_data_review_'+stamp+'.sqlite3');audit=directory/('data_review_'+stamp+'.json')
        with maintenance_activity(db_path):
            version=(conn.execute('PRAGMA data_version').fetchone()[0],conn.total_changes)
            target=sqlite3.connect(backup)
            try:conn.backup(target)
            finally:target.close()
            record=dict(operation='explicit observed-country/non-job review',state='planned',started_at=stamp,backup=str(backup),backup_sha256=_file_digest(backup),preview=preview,actions=[])
            _write_audit(audit,record)
            try:
                conn.execute('BEGIN IMMEDIATE')
                if version!=(conn.execute('PRAGMA data_version').fetchone()[0],conn.total_changes):
                    raise RuntimeError('Database changed during backup; no review applied')
                actual=preview_connection(conn,company)
                if actual['plan_sha256']!=plan_sha256:raise RuntimeError('Database changed after preview; no review applied')
                for action in actual['actions']:
                    if action['action']=='fill_country':
                        conn.execute("UPDATE jobs SET country=?,country_source='job_location_offline' WHERE id=?",(action['new_country'],action['id']))
                    else:
                        # Keep row/terms/evidence and URL intact for rollback/audit;
                        # no source record or saved application is deleted.
                        conn.execute('UPDATE jobs SET verified_active=0 WHERE id=?',(action['id'],))
                    record['actions'].append(action)
                record['state']='committed'
                conn.execute('CREATE TABLE IF NOT EXISTS maintenance_audit(id INTEGER PRIMARY KEY,created_at TEXT DEFAULT CURRENT_TIMESTAMP,backup_path TEXT,audit_json TEXT)')
                conn.execute('INSERT INTO maintenance_audit(backup_path,audit_json) VALUES(?,?)',(str(backup),json.dumps(record,ensure_ascii=False)))
                conn.commit()
            except Exception:
                conn.rollback();record['state']='rolled_back';_write_audit(audit,record);raise
            warning=''
            try:_write_audit(audit,record)
            except OSError:warning='Companion audit update failed; complete committed audit is in maintenance_audit'
            return dict(changed=len(record['actions']),country_fills=actual['country_fills'],non_job_exclusions=actual['non_job_exclusions'],backup=str(backup),backup_sha256=record['backup_sha256'],audit=str(audit),audit_warning=warning)
    finally:conn.close()
