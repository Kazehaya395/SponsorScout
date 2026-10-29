"""Observed job geography, shared by CSV writers and ingestion.

Board/seed/HQ country hints do not become facts merely by being normalized.
No network, locale/HQ inference, or destructive retrospective operation.
"""
from __future__ import annotations
from sponsorscout.core.location_country import country_from_location

_HINTS={'site_host','site','seed','seed_url','target_country','company_hq','hq','headquarters'}
_EMPTY={'','unknown','not specified','global','multiple locations'}


def is_location_hint(source):
    parts=set(str(source or '').strip().casefold().replace('-','_').split('+'))
    return bool(parts&_HINTS)


def observed_geography(location='',raw_location='',source=''):
    source=str(source or '').strip().casefold()
    loc=str(location or '').strip();raw=str(raw_location or '').strip()
    if loc.casefold()in _EMPTY:loc=''
    if raw.casefold()in _EMPTY:raw=''
    if is_location_hint(source):
        # An original card/field string can contain actual geography even when
        # normalization subsequently appended a guessed host country.
        if source not in {'company_hq','hq','headquarters','seed','target_country'} and raw:
            loc=raw
        elif '+site' in source and ',' in loc:
            loc=loc.rsplit(',',1)[0].strip()
        else:loc=''
    country=country_from_location(loc) if loc else ''
    if not country and raw and raw!=loc and not is_location_hint(source):
        country=country_from_location(raw) or ''
    return loc,str(country or '')


def row_geography(row):
    return observed_geography(row.get('Job Location',''),row.get('Raw Location',''),row.get('Location Source',''))


def apply_row_geography(row):
    """Normalize one fresh scan observation; keep valid seed_url jobs accepted."""
    old_source=row.get('Location Source','');old=row.get('Job Location','')
    loc,country=row_geography(row)
    if is_location_hint(old_source):
        row['_location_hint']=str(old or '')
        row['_location_hint_source']=str(old_source or '')
        row['Location Source']='card' if loc and row.get('Raw Location') else ('observed_place' if loc else 'none')
        row['Location Confidence']='medium' if loc else 'none'
        if str(row.get('Scope Policy') or '').casefold()=='seed_url':
            target=str(row.get('Target Country') or '').strip()
            if not country or (target and target.casefold()not in ('global',country.casefold())):
                row['Scope Confidence']='unverified_seed_url'
    row['Job Location']=loc or 'Unknown'
    row['Job Country']=country or 'Unknown'
    return row
