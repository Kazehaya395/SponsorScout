"""Versioned offline place-country evidence, never a host/HQ/seed inference.

GeoNames CC BY4.0 attribution and input checksums travel in the bundled asset.
Exact location components only; cross-country homonyms remain ambiguous. This
is a fallback index, not a claim that every place on Earth is represented.
"""
from __future__ import annotations
from functools import lru_cache
import json,logging,sys,unicodedata,threading
from pathlib import Path

logger=logging.getLogger(__name__)
_INDEX=None
_INDEX_LOCK=threading.Lock()
_EMPTY={'','unknown','other','none','n a','not specified','global','worldwide','remote','hybrid','on site','multiple locations'}


def normalize_place(value):
    value=unicodedata.normalize('NFKD',str(value or '').casefold().replace('’',"'").replace('`',"'"))
    value=''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in value).split())


def _load_data():
    candidates=[Path(__file__).resolve().parents[1]/'data/location_places.json']
    if getattr(sys,'frozen',False):
        base=Path(getattr(sys,'_MEIPASS',Path(sys.executable).parent))
        candidates.extend((base/'sponsorscout/data/location_places.json',base/'location_places.json'))
    for path in candidates:
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding='utf-8'))
            except (ValueError,OSError) as exc:
                logger.warning('Offline place index unavailable: %s',exc)
    return {'countries':{},'places':{}}


def _data():
    global _INDEX
    with _INDEX_LOCK:
        if _INDEX is None:
            _INDEX=_load_data()
        return _INDEX


def _reset_cache():
    """Developer cold-resource test helper; never used by startup/Scan."""
    global _INDEX
    with _INDEX_LOCK:_INDEX=None
    place_country_candidates.cache_clear()
    _country_names.cache_clear()


@lru_cache(maxsize=8192)
def place_country_candidates(value):
    key=normalize_place(value)
    if key in _EMPTY or len(key)<3:
        return ()
    return tuple(filter(None,_data()['places'].get(key,'').split(',')))


def place_evidence(value):
    codes=place_country_candidates(str(value or ''))
    country=_data()['countries'].get(codes[0],'') if len(codes)==1 else ''
    return {'country':country,'candidates':list(codes),'method':'offline_observed_place' if country else 'ambiguous_place' if codes else 'unresolved_place',
            'place':str(value or ''),'authority':'GeoNames 2026-10-24 / CC BY4.0'}


@lru_cache(maxsize=1)
def _country_names():
    return {normalize_place(name):name for name in _data()['countries'].values()}


def explicit_country_name(value, allow_code=False):
    return _country_names().get(normalize_place(value),'') or (_data()['countries'].get(str(value).strip().upper(),'')if allow_code else '')


def country_from_place_components(components):
    # An explicit country component (e.g. La Paz, Bolivia) outranks a small
    # settlement elsewhere named Bolivia. Do not resolve countries as cities.
    explicit={explicit_country_name(part) for part in components}
    explicit.discard('')
    if explicit:
        return next(iter(explicit)) if len(explicit)==1 else ''
    observed=[set(place_country_candidates(str(part))) for part in components]
    observed=[value for value in observed if value]
    if not observed:
        return ''
    intersection=set.intersection(*observed)
    return _data()['countries'].get(next(iter(intersection)),'') if len(intersection)==1 else ''
