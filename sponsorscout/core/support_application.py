"""Apply the existing regular Career support policy to Assisted observations.

No classifier-rule changes: same detector, employer/context guards, evidence and
Blue Card. Retrieval/read quality is metadata, never an invented Yes or No.
"""
from __future__ import annotations
import hashlib
from datetime import datetime,timezone
from sponsorscout.scanning.jd_support import VERDICT_UNKNOWN,normalize_jd_text,_BLUE_CARD_RE


def _stored(value):
    value=str(value or '').strip().casefold()
    return 'Y' if value in ('y','yes') else 'N' if value in ('n','no') else 'Unknown'


def apply_support_to_job(scanner,job,text,*,kind='detail',read_status='readable',method='',captured_at=''):
    text=normalize_jd_text(text)
    rec={'Company Name':job.get('company',''),'Job Title':job.get('title',''),
         'Job URL':job.get('url',''),'Visa Sponsorship':VERDICT_UNKNOWN,
         'Relocation Support':VERDICT_UNKNOWN,'EU Blue Card':VERDICT_UNKNOWN}
    # This is the exact regular application routine, not an independent
    # Assisted copy of detector or false-positive policy.
    scanner._apply_support_from_text(rec,text,job.get('url',''))
    raw=scanner.detector.detect(text,scanner._other_company_names(job.get('company','')))
    guarded=[]
    for source,target in (('Visa Sponsorship','visa'),('Relocation Support','relocation')):
        if raw[target]['verdict']=='Yes'and _stored(rec.get(source))!='Y':guarded.append(target)
    evidence=rec.get('Support Evidence')or''
    blue_evidence=rec.get('Blue Card Evidence')or''
    if not blue_evidence:
        blue_evidence=next((sentence[:600]for sentence in scanner.detector.split_sentences(text)if _BLUE_CARD_RE.search(sentence)), '')
    if blue_evidence and rec.get('EU Blue Card')not in(None,'','Unknown')and not evidence:
        evidence=blue_evidence
    origin='explicit_detail_sentence'if kind=='detail'else'listing_card_sentence'
    marker=f'{origin}; jd_read_status={read_status}; jd_method={method or kind}; jd_chars={len(text) if kind=="detail" else 0}; input_chars={len(text)}'
    if kind=='detail':marker+='; jd_sha256='+hashlib.sha256(text.encode()).hexdigest()+'; jd_captured_at='+(captured_at or datetime.now(timezone.utc).isoformat())
    if guarded:marker+='; context_guard='+','.join(guarded)
    result=dict(job,visa_sponsorship=_stored(rec.get('Visa Sponsorship')),
                relocation_support=_stored(rec.get('Relocation Support')),
                eu_blue_card_verdict=rec.get('EU Blue Card')or'Unknown',
                relocation_required=_stored(rec.get('Relocation Required')),
                support_confidence=float(rec.get('Support Confidence')or 0),
                support_evidence=evidence,support_evidence_url=job.get('url','')if evidence else'',
                support_evidence_type=marker,blue_card_evidence=blue_evidence,
                jd_read_status=read_status,jd_source=method or kind,jd_chars=len(text)if kind=='detail'else 0,
                support_guard_notes=','.join(guarded))
    # Defensive tri-state guard: an Unknown verdict is never evidence of No.
    # Keep the provenance marker, but drop any confidence/evidence inherited
    # from a bare mention or a scanner that has not yet fixed its rec fields.
    if (result['visa_sponsorship'] == VERDICT_UNKNOWN
            and result['relocation_support'] == VERDICT_UNKNOWN
            and result['eu_blue_card_verdict'] in ('', VERDICT_UNKNOWN, None)
            and result['relocation_required'] == VERDICT_UNKNOWN):
        result['support_confidence'] = 0.0
        result['support_evidence'] = ''
        result['support_evidence_url'] = ''
        result['blue_card_evidence'] = ''
    for flag in ('_description_authority','_support_authoritative','_support_guarded'):
        result.pop(flag,None)
    if kind=='detail'and read_status=='readable':
        result['_description_authority']='detail'
        result['_support_authoritative']=True
        result['_support_guarded']=[{'visa':'visa_sponsorship','relocation':'relocation_support'}[name]for name in guarded]
    return result
