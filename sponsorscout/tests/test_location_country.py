"""Regression suite for sponsorscout.core.location_country (Batch B + Giessen fix).

76 cases covering: EU/US/Asia/LatAm/MEA cities, state/postal disambiguation
(Milan-MI vs Milan-Spain, Bethesda-MD vs Chisinau-MD, Parma), Remote-X forms,
CJK transliterations, legacy sweep ("Holland", "United States of America"),
and guarded unknowns (Remote-EU, La Paz-Bolivia, Baden-Wurttemberg).

Run:  pytest tests/test_location_country.py
"""
try:
    from sponsorscout.core.location_country import country_from_location
except ImportError:  # standalone checkout: module next to the tests
    from location_country import country_from_location

import pytest

CASES = [
    ("Giessen", "Germany"), ("Wetzlar, DE", "Germany"), ("Yixing", "China"),
    ("Tianjin", "China"), ("Walldorf", "Germany"), ("Darmstadt", "Germany"),
    ("Austin (Ed Bluestein, Manufacturing", "United States"),
    ("London (Hybrid)", "United Kingdom"), ("Amsterdam (Remote)", "Netherlands"),
    ("Kuala Lumpur, my", "Malaysia"), ("Athens, gr", "Greece"),
    ("United States of America", "United States"), ("Holland", "Netherlands"),
    ("Milan, MI", "Italy"), ("Milan, Spain", "Spain"), ("Milan, Italy", "Italy"),
    ("Remote - Germany", "Germany"), ("Remote - Netherlands", "Netherlands"),
    ("Remote, Austin, TX", "United States"), ("Remote (Netherlands)", "Netherlands"),
    ("Tacoma, WA 98101", "United States"), ("Plano, TX 75023", "United States"),
    ("Westlake Village, CA, US, 9362", "United States"),
    ("West Palm Beach, FL", "United States"), ("Seattle, WA 98101", "United States"),
    ("Saudi Arabia", "Saudi Arabia"), ("Egypt", "Egypt"), ("Peru", "Peru"),
    ("Bahrain", "Bahrain"), ("Ecuador", "Ecuador"), ("Hong Kong", "Hong Kong"),
    ("Penang, MY", "Malaysia"), ("Johor Bahru, MY", "Malaysia"),
    ("DE - Darmstadt - Europahaus", "Germany"), ("Berlin (Hybrid", "Germany"),
    ("Frankfurt am Main", "Germany"), ("Tel-Aviv", "Israel"),
    ("Global Services, London", "United Kingdom"), ("Toronto, ON", "Canada"),
    ("Toronto, Ontario", "Canada"), ("Sydney, NSW", "Australia"),
    ("Bengaluru, KA", "India"), ("Paris, FR", "France"), ("Dublin, IE", "Ireland"),
    ("Tokyo, Japan", "Japan"), ("Japan", "Japan"), ("Zürich", "Switzerland"),
    ("Prague", "Czech Republic"), ("Czechia", "Czech Republic"),
    ("Manama", "Bahrain"), ("Quito", "Ecuador"), ("Algeria", "Algeria"),
    ("Bethesda, MD", "United States"), ("Chisinau, MD", "Moldova"),
    ("Wilmington, DE", "United States"), ("San Jose, CA", "United States"),
    ("Springfield, MO", "United States"), ("Bangalore, IN", "India"),
    ("hybrid - berlin", "Germany"), ("Remote - EU", ""), ("Remote", ""),
    ("worldwide", ""), ("Unknown", ""), ("Not Specified", ""),
    ("Parma", "Italy"), ("Suzhou, CN", "China"), ("Milan, MI", "Italy"),
    ("Baden-Württemberg", ""), ("La Paz - Bolivia", ""),
    ("", ""), ("Algiers", ""), ("Algeria", "Algeria"),
    # Session regressions: screenshot cities + Workable API shape + NJ site
    ("Milano, MI", "Italy"), ("Milano", "Italy"),
    ("Berlin, Germany", "Germany"), ("Florham Park, NJ, US", "United States"),
]


@pytest.mark.parametrize("location,expected", CASES)
def test_country_from_location(location, expected):
    assert country_from_location(location) == expected


def test_all_keys_lowercase():
    """The Giessen saga: dict keys must be lowercase (case-sensitive lookup)."""
    try:
        import sponsorscout.core.location_country as lc
    except ImportError:
        import location_country as lc
    tables = {"ISO2_TO_COUNTRY": getattr(lc, "ISO2_TO_COUNTRY", None),
              "COUNTRY_NAMES": getattr(lc, "COUNTRY_NAMES", None),
              "CITY_TO_COUNTRY": getattr(lc, "CITY_TO_COUNTRY", None)}
    for name, table in tables.items():
        assert isinstance(table, dict) and table, f"{name} table missing/empty"
        bad = [k for k in table if isinstance(k, str) and k != k.lower()]
        assert not bad, f"{name} has {len(bad)} non-lowercase keys: {bad[:5]}"
