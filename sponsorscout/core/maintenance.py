"""Explicit, backed-up, audited existing-data alias cleanup. NEVER a startup hook.

Only trusted URL/posting identities merge. Conflicting manual countries are
left for review; descriptions, evidence, source markers and applications stay.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sponsorscout.core.activity import ingestion_active, maintenance_activity
from sponsorscout.core.job_identity import posting_identity, merge_sources, record_quality

_UNKNOWN = {'','unknown','not specified','n/a','none'}


def preview_connection(conn):
    groups = {}
    for row in conn.execute('SELECT id,url,source_name,country,country_source FROM jobs WHERE is_expired=0 ORDER BY id'):
        record = dict(row)
        key = posting_identity(record.get('url'))
        if key:
            groups.setdefault(key,[]).append(record)
    planned,conflicts = [],[]
    for key,records in groups.items():
        if len(records)<2:
            continue
        manual = {str(row.get('country') or '').strip() for row in records
                  if row.get('country_source')=='manual' and row.get('country')}
        entry = {'posting_key':key,'ids':[row['id'] for row in records],
                 'urls':[row['url'] for row in records],
                 'sources':merge_sources(*(row.get('source_name') for row in records))}
        if len(manual)>1:
            entry['manual_countries'] = sorted(manual)
            conflicts.append(entry)
        else:
            planned.append(entry)
    return {'groups':planned,'conflicts':conflicts,
            'merge_rows':sum(len(group['ids'])-1 for group in planned)}


def preview_cleanup(db_path):
    from sponsorscout.db.database import get_connection
    conn = get_connection(db_path)
    try:
        return preview_connection(conn)
    finally:
        conn.close()


def _file_digest(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def _write_audit(path,document):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(document,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temporary,path)


def cleanup_connection(conn,*,artifact_dir=None):
    """Explicit API invocation. Writes a SQLite backup before the first change."""
    from sponsorscout.core.persistence import upsert_job
    db_path = conn.execute('PRAGMA database_list').fetchone()[2]
    if not db_path:
        raise ValueError('Cleanup requires a file-backed database so a restorable backup can be saved.')
    if ingestion_active(db_path):
        raise RuntimeError('Finish regular/assisted ingestion before running existing-data cleanup.')
    if conn.in_transaction:
        raise RuntimeError('Commit or roll back existing work before requesting a cleanup backup.')
    preview = preview_connection(conn)
    if not preview['merge_rows']:
        return dict(preview,merged=0,backup='',audit='')
    directory = Path(artifact_dir) if artifact_dir else Path(db_path).parent/'maintenance_backups'
    directory.mkdir(parents=True,exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = directory/f'pre_alias_cleanup_{stamp}.sqlite3'
    audit_path = directory/f'alias_cleanup_{stamp}.json'
    with maintenance_activity(db_path):
        # An uncoordinated external writer is possible even though our own
        # ingesters are excluded. Never delete a row absent from the backup.
        before_backup = (conn.execute('PRAGMA data_version').fetchone()[0], conn.total_changes)
        target = sqlite3.connect(str(backup))
        try:
            conn.backup(target)
        finally:
            target.close()
        document = {'operation':'explicit trusted-posting alias merge','started_at':stamp,
                    'database':str(db_path),'backup':str(backup),
                    'backup_sha256':_file_digest(backup),
                    'state':'planned','preview':preview,'actions':[]}
        _write_audit(audit_path,document)  # fail safely BEFORE modifying user data
        try:
            conn.execute('BEGIN IMMEDIATE')
            current = (conn.execute('PRAGMA data_version').fetchone()[0], conn.total_changes)
            if current != before_backup:
                raise RuntimeError('Database changed while the cleanup backup was being prepared; no cleanup applied. Preview and confirm again.')
            actual = preview_connection(conn)
            if actual != preview:
                raise RuntimeError('Database changed after the cleanup preview; no cleanup applied. Preview and confirm again.')
            for group in actual['groups']:
                placeholders = ','.join('?' for _ in group['ids'])
                records = [dict(row) for row in conn.execute(
                    f'SELECT * FROM jobs WHERE id IN ({placeholders}) ORDER BY id',group['ids'])]
                keep = records[0]  # stable row ID and an already observed URL
                best = max(records,key=record_quality)
                merged = dict(best)
                merged['url'] = keep['url']
                merged['source_name'] = merge_sources(*(row.get('source_name') for row in records))
                merged['description'] = max((row.get('description') or '' for row in records),key=len)
                for row in records:
                    for key,value in row.items():
                        if str(merged.get(key) or '').strip().casefold() in _UNKNOWN and str(value or '').strip().casefold() not in _UNKNOWN:
                            merged[key] = value
                    if row.get('country_source')=='manual':
                        merged['country'],merged['country_source'] = row.get('country',''),'manual'
                for verdict,flag in (('eu_blue_card_verdict','eu_blue_card'),('relocation_support','has_relocation')):
                    merged[flag] = int(str(merged.get(verdict) or '').casefold() in ('y','yes'))
                upsert_job(conn,merged,commit=False)
                conn.execute('UPDATE jobs SET insights_dirty=1,posting_key=? WHERE id=?',
                             (group['posting_key'],keep['id']))
                removed = []
                for row in records[1:]:
                    conn.execute('INSERT OR IGNORE INTO job_terms(job_id,kind,term) '
                                 'SELECT ?,kind,term FROM job_terms WHERE job_id=?',(keep['id'],row['id']))
                    conn.execute('DELETE FROM jobs WHERE id=?',(row['id'],))
                    removed.append(row['id'])
                # Applications reference original observed URLs, not job IDs.
                # They are intentionally untouched and remain resolvable via
                # get_job_for_tailoring's trusted posting-key fallback.
                document['actions'].append({'kept_id':keep['id'],'kept_url':keep['url'],
                                            'removed_ids':removed,'sources':merged['source_name'],
                                            'manual_country':merged.get('country') if merged.get('country_source')=='manual' else '',
                                            'description_characters':len(merged['description'])})
            document['state'] = 'committed'
            document['merged'] = sum(len(action['removed_ids']) for action in document['actions'])
            conn.execute('CREATE TABLE IF NOT EXISTS maintenance_audit '
                         '(id INTEGER PRIMARY KEY,created_at TEXT DEFAULT CURRENT_TIMESTAMP,backup_path TEXT,audit_json TEXT)')
            conn.execute('INSERT INTO maintenance_audit(backup_path,audit_json) VALUES(?,?)',
                         (str(backup),json.dumps(document,ensure_ascii=False)))
            conn.commit()
        except Exception:
            conn.rollback()
            document['state'] = 'rolled_back'
            _write_audit(audit_path,document)
            raise
        # The in-DB ledger remains authoritative if a later filesystem error
        # prevents updating the companion JSON after an already committed merge.
        try:
            _write_audit(audit_path,document)
        except OSError:
            document['audit_warning'] = 'Companion audit update failed; the complete audit is in maintenance_audit.'
        return dict(actual,merged=document['merged'],backup=str(backup),audit=str(audit_path),
                    audit_warning=document.get('audit_warning',''))


def cleanup_database(db_path,*,confirm=False,artifact_dir=None):
    if not confirm:
        return preview_cleanup(db_path)
    from sponsorscout.db.database import get_connection
    conn = get_connection(db_path)
    try:
        return cleanup_connection(conn,artifact_dir=artifact_dir)
    finally:
        conn.close()
