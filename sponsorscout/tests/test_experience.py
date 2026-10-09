"""FIX P0-30: experience extraction engine + persistence round-trip."""
import os
import sqlite3

from sponsorscout.scanning.career import career_scanner as career_mod

# Minimal ``jobs`` replica: only the columns ``upsert_job`` writes as NOT-NULL
# free text plus the FIX P0-30 experience columns under test.
_JOBS_DDL = """CREATE TABLE jobs (
        id INTEGER PRIMARY KEY, external_id TEXT, title TEXT, company TEXT,
        country TEXT, location TEXT, url TEXT UNIQUE, ats_source TEXT,
        source_type TEXT, source_subtype TEXT, source_name TEXT,
        description TEXT, trust_score INTEGER, freshness_score INTEGER,
        sponsorship_score INTEGER, match_score INTEGER,
        verified_active INTEGER, is_expired INTEGER, last_verified_at TEXT,
        first_seen_at TEXT DEFAULT '', last_seen_at TEXT DEFAULT '',
        created_at TEXT DEFAULT '', updated_at TEXT DEFAULT '',
        remote_type TEXT, eu_blue_card INTEGER DEFAULT 0,
        has_relocation INTEGER DEFAULT 0, experience_level TEXT DEFAULT '',
        experience_required TEXT DEFAULT '', experience_min_years REAL,
        experience_source TEXT DEFAULT '', industry TEXT DEFAULT '',
        ai_score INTEGER DEFAULT 0, visa_sponsorship TEXT,
        relocation_support TEXT, eu_blue_card_verdict TEXT,
        relocation_required TEXT, support_confidence REAL,
        support_evidence TEXT, support_evidence_url TEXT,
        support_evidence_type TEXT, blue_card_evidence TEXT,
        canonical_job_id TEXT, run_id TEXT, raw_location TEXT DEFAULT '',
        country_source TEXT DEFAULT 'auto')"""


def _make_jobs_db(tmp_path) -> str:
    """Create a throwaway DB with the replica schema; return its path."""
    db_path = str(tmp_path / "t.db")
    conn = sqlite3.connect(db_path)
    conn.execute(_JOBS_DDL)
    conn.commit()
    conn.close()
    return db_path


def test_years_range_extracted():
    exp = career_mod.extract_experience(
        "You have 5-8 years of professional experience in backend.", "")
    assert exp["required"] == "5-8 years"
    assert exp["min_years"] == 5
    assert exp["max_years"] == 8
    assert exp["level"] == "Senior"  # midpoint 6.5
    assert exp["source"] == "detail_text"


def test_months_and_plus():
    exp = career_mod.extract_experience("at least 6 months of experience", "")
    assert exp["min_years"] == 0.5
    assert "month" in exp["required"]


def test_disqualifiers_ignored():
    # Ages, founding dates and visa durations must never become experience.
    for text in ("You must be at least 18 years old.",
                 "We were founded 25 years ago.",
                 "visa valid for 3 years"):
        exp = career_mod.extract_experience(text, "")
        assert exp["required"] == "Unknown", text


def test_none_required():
    exp = career_mod.extract_experience("No prior experience required.", "")
    assert exp["required"] == "None required"
    assert exp["min_years"] == 0


def test_title_inference_fallback():
    exp = career_mod.extract_experience("", "Senior Backend Engineer")
    assert exp["level"] == "Senior"
    assert exp["source"] == "title_inference"
    assert exp["required"] == "Unknown"


def test_apply_never_downgrades():
    rec = {"Experience Source": "api_field",
           "Experience Required": "3+ years",
           "Experience Min Years": 3,
           "Experience Level": "Senior"}
    hit = career_mod.apply_experience_to_record(
        rec, jd_text="no experience needed", title="Junior Dev")
    # Weaker source (detail text) cannot overwrite api_field.
    assert rec["Experience Required"] == "3+ years"
    assert rec["Experience Level"] == "Senior"


def test_level_hint_beats_inference():
    rec = {}
    career_mod.apply_experience_to_record(
        rec, jd_text="some experience preferred", title="Dev",
        level_hint="Mid-Senior level")
    assert rec["Experience Level"] == "Senior"
    assert rec["Experience Source"] == "api_field"


def test_row_to_job_maps_experience_columns():
    from sponsorscout.scanning.pipeline import _row_to_job
    from sponsorscout.tests.test_pipeline import _career_row
    row = _career_row(**{"Experience Required": "5-8 years",
                         "Experience Min Years": 5,
                         "Experience Level": "Senior",
                         "Experience Source": "api_description"})
    job = _row_to_job(row, run_id="R1")
    assert job["experience_level"] == "Senior"
    assert job["experience_required"] == "5-8 years"
    assert job["experience_min_years"] == 5.0
    assert job["experience_source"] == "api_description"


def test_persistence_roundtrip(tmp_path):
    from sponsorscout.core import persistence
    from sponsorscout.db import database
    db_path = _make_jobs_db(tmp_path)
    job = {
        "url": "https://x/j1", "title": "Dev", "company": "Acme",
        "external_id": "acme-1", "experience_level": "Senior",
        "experience_required": "5-8 years", "experience_min_years": 5.0,
        "experience_source": "api_description",
    }
    conn = database.get_connection(db_path)
    try:
        persistence.upsert_job(conn, job)
    finally:
        conn.close()
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT experience_required, experience_min_years,"
                       " experience_source, experience_level FROM jobs").fetchone()
    conn.close()
    assert row == ("5-8 years", 5.0, "api_description", "Senior")


def test_persistence_never_downgrades_experience(tmp_path):
    """Re-ingesting a row with no experience data must keep the stored value."""
    from sponsorscout.core import persistence
    from sponsorscout.db import database
    db_path = _make_jobs_db(tmp_path)
    conn = database.get_connection(db_path)
    try:
        persistence.upsert_job(conn, {"url": "https://x/j1", "title": "Dev",
                                      "company": "Acme",
                                      "experience_required": "5-8 years",
                                      "experience_min_years": 5.0,
                                      "experience_source": "api_description",
                                      "experience_level": "Senior"})
        # Second sighting of the same URL carries no experience evidence.
        persistence.upsert_job(conn, {"url": "https://x/j1", "title": "Dev",
                                      "company": "Acme"})
    finally:
        conn.close()
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT experience_required, experience_min_years,"
                       " experience_source FROM jobs").fetchone()
    conn.close()
    assert row == ("5-8 years", 5.0, "api_description")


def test_scanner_vocabulary_is_canonicalised_on_ingest():
    """Scanner words must land as the app vocabulary (db.EXPERIENCE_LEVELS).

    The scanners emit employer-facing words (Internship/Junior/Executive) but
    the jobs table and its filter use Intern/Entry/.../Exec.  Mapping happens
    once, in ``_row_to_job``, so the Experience filter can never miss a row.
    """
    from sponsorscout.db.database import EXPERIENCE_LEVELS
    from sponsorscout.scanning.pipeline import _row_to_job
    from sponsorscout.tests.test_pipeline import _career_row
    canon = set(EXPERIENCE_LEVELS) - {"All", "Any (incl. unknown)"}
    for scanner_word, expected in (("Internship", "Intern"),
                                   ("Junior", "Entry"),
                                   ("Mid", "Mid"),
                                   ("Senior", "Senior"),
                                   ("Lead", "Lead"),
                                   ("Executive", "Exec")):
        job = _row_to_job(
            _career_row(**{"Experience Level": scanner_word}), run_id="R1")
        assert job["experience_level"] == expected, scanner_word
        assert job["experience_level"] in canon, scanner_word


def test_unclassifiable_experience_stored_blank():
    """'Unknown' becomes '' for both the level (enum) and the text column.

    The required-text column is free-form (it holds "5-8 years", "None
    required", ...), so only the literal placeholder is scrubbed.
    """
    from sponsorscout.scanning.pipeline import _row_to_job
    from sponsorscout.tests.test_pipeline import _career_row
    for raw in ("", "Unknown"):
        job = _row_to_job(_career_row(**{
            "Experience Level": raw,
            "Experience Required": raw,
            "Experience Min Years": "",
            "Experience Source": "title_inference"}), run_id="R1")
        assert job["experience_level"] == "", raw
        assert job["experience_required"] == "", raw
        assert job["experience_min_years"] is None, raw


def test_out_of_vocabulary_level_dropped_but_text_kept():
    """An unknown level word is dropped; free-text detail is preserved."""
    from sponsorscout.scanning.pipeline import _row_to_job
    from sponsorscout.tests.test_pipeline import _career_row
    job = _row_to_job(_career_row(**{"Experience Level": "Wizard",
                                     "Experience Required": "Wizard"}),
                      run_id="R1")
    assert job["experience_level"] == ""
    assert job["experience_required"] == "Wizard"


def test_experience_none_required_is_kept():
    """'None required' is a real fact, unlike 'Unknown' — never dropped."""
    from sponsorscout.scanning.pipeline import _row_to_job
    from sponsorscout.tests.test_pipeline import _career_row
    job = _row_to_job(_career_row(**{"Experience Required": "None required",
                                     "Experience Min Years": 0}), run_id="R1")
    assert job["experience_required"] == "None required"
    assert job["experience_min_years"] == 0.0


def test_junk_min_years_becomes_none():
    """A non-numeric 'Experience Min Years' must not raise on ingest."""
    from sponsorscout.scanning.pipeline import _row_to_job
    from sponsorscout.tests.test_pipeline import _career_row
    job = _row_to_job(_career_row(**{"Experience Min Years": "n/a"}),
                      run_id="R1")
    assert job["experience_min_years"] is None

def test_years_in_domain_pattern_mistral_regression():
    exp = career_mod.extract_experience("4+ years in Office Management or Workplace Operations (startup/scale-up a plus)", "Workplace Operations Manager")
    assert exp["required"] == "4+ years", exp
    assert exp["min_years"] == 4, exp
    assert exp["level"] == "Mid", exp

def test_years_of_domain_pattern():
    exp = career_mod.extract_experience("3 years of sales experience required", "Sales Rep")
    assert exp["min_years"] == 3, exp

def test_disqualifiers_still_excluded_with_years_in_gate():
    for text in ("We were founded 25 years ago.", "You must be at least 18 years old.", "visa valid for 3 years"):
        exp = career_mod.extract_experience(text, "")
        assert exp["required"] == "Unknown", text


# ── FIX P0-30 (i18n): the seeds carry IT / NL / DE / FR / ES boards, so the ──
# ── detector must read a requirement in those languages too.               ──

_MULTILINGUAL_SAMPLES = [
    # (jd text, expected min_years, expected level)
    ("Cerchiamo una figura con almeno 3 anni di esperienza nel settore retail.",
     3, "Mid"),
    ("Sono richiesti 5 anni nel settore della logistica.", 5, "Senior"),
    ("Esperienza minima di 6 mesi nel ruolo.", 0.5, "Junior"),
    ("Almeno 10 anni di esperienza richiesti.", 10, "Lead"),
    ("Wir suchen Sie mit mindestens 5 Jahren Berufserfahrung im Vertrieb.",
     5, "Senior"),
    ("Sie bringen 3-5 Jahre Erfahrung in der Softwareentwicklung mit.",
     3, "Mid"),
    ("4 Jahre im Vertrieb eines Technologieunternehmens vorausgesetzt.",
     4, "Mid"),
    ("Mindestens 6 Monate Berufserfahrung erforderlich.", 0.5, "Junior"),
    ("Mindestens 2 Jahre als Entwickler gearbeitet.", 2, "Junior"),
    ("Je bent een ervaren professional met minimaal 4 jaar ervaring.",
     4, "Mid"),
    ("3 jaar in sales is een vereiste.", 3, "Mid"),
    ("Minimaal 5 jaar werkervaring in de logistiek.", 5, "Senior"),
    ("5 ans d experience minimum dans le domaine.", 5, "Senior"),
    ("Se requieren 4 a\u00f1os de experiencia en el sector industrial.",
     4, "Mid"),
    ("Experi\u00eancia m\u00ednima de 3 anos na \u00e1rea comercial.",
     3, "Mid"),
]


def test_multilingual_requirements_extracted():
    """IT / DE / NL / FR / ES / PT requirement sentences must all resolve."""
    for jd, min_years, level in _MULTILINGUAL_SAMPLES:
        exp = career_mod.extract_experience(jd, "")
        assert exp["min_years"] == min_years, (jd, exp)
        assert exp["level"] == level, (jd, exp)
        assert exp["source"] == "detail_text", (jd, exp)


def test_multilingual_ats_career_parity():
    """A seed company on an ATS board and on a career board must agree."""
    from sponsorscout.scanning.ats import ats_scanner as ats_mod

    for jd, _min, _lvl in _MULTILINGUAL_SAMPLES:
        assert ats_mod.extract_experience(jd, "") == \
            career_mod.extract_experience(jd, ""), jd


def test_multilingual_non_experience_durations_rejected():
    """'seit 20 Jahren' / 'dal 1998' / 'desde 1999' are age, not experience."""
    for text in ("Sie sind seit 20 Jahren im Unternehmen.",
                 "Nous existons depuis 15 ans dans le secteur.",
                 "Wij zijn sinds 10 jaar actief in Nederland.",
                 "L azienda e operativa dal 1998 e cerca un profilo junior."):
        exp = career_mod.extract_experience(text, "")
        assert exp["required"] == "Unknown", (text, exp)


# ── Display layer (Search tab): absolute values, then level, then NA ─────────

def test_display_layer_shows_absolute_values():
    """The column must show '4+' / '3-5' — not a paraphrase."""
    from sponsorscout.ui.tabs.search import _experience_cell

    assert _experience_cell("4+ years", "Mid", 4) == "4+"
    assert _experience_cell("3-5 years", "Senior", 3) == "3-5"
    assert _experience_cell("5 years", "Senior", 5) == "5"
    assert _experience_cell("10+ years", "Lead", 10) == "10+"


def test_display_layer_level_only_and_no_statement():
    """Level word when the JD names seniority; '?' when there is no signal."""
    from sponsorscout.ui.tabs.search import _experience_cell

    assert _experience_cell("Unknown", "Senior", None) == "Senior"
    assert _experience_cell("", "Mid", None) == "Mid"
    assert _experience_cell("Unknown", "Unknown", None) == "?"
    assert _experience_cell("", "", None) == "?"
    assert _experience_cell("N/A", "Unknown", "") == "?"


def test_display_layer_months_and_none():
    """Months stay months (converted in the sort key), 'None' stays explicit."""
    from sponsorscout.ui.tabs.search import _experience_cell, _experience_sort_key

    assert _experience_cell("6 months", "Junior", 0.5) == "6 mo"
    assert _experience_cell("None required", "Entry", 0) == "None"
    # 6 months must sort before any year-based requirement.
    assert _experience_sort_key("6 months", "Junior", 0.5) < \
        _experience_sort_key("1+ years", "Entry", 1)
    # Stated years sort ascending, not lexicographically ("11" vs "3").
    assert _experience_sort_key("11+ years", "Lead", 11) > \
        _experience_sort_key("3+ years", "Mid", 3)
    # Level-only rows sort after numbers; the '?' tier last.
    assert _experience_sort_key("Unknown", "Senior", None) > \
        _experience_sort_key("11+ years", "Lead", 11)
    assert _experience_sort_key("Unknown", "Unknown", None) > \
        _experience_sort_key("Unknown", "Senior", None)


def test_table_items_sort_without_crashing():
    """Regression: sorting a column with no sort_key must not segfault.

    ``_CellItem`` overrides ``__lt__``. PySide6's base implementation re-enters
    that Python override while comparing display text, which overflows the C++
    stack (access violation 0xC0000005) the moment the user clicks any column
    other than Experience. This test clicks two columns: the keyed one and a
    plain one.

    The Search table no longer asks Qt to sort (``setSortingEnabled(False)``):
    it orders the row DATA in Python and fills the cells itself, so this
    Python ``__lt__`` is no longer on the app's sort path at all. The override
    itself is kept (and still exercised here) because ``_CellItem`` is the
    table's cell type.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication, QTableWidget
    except Exception:  # pragma: no cover - PySide6 missing
        import pytest
        pytest.skip("PySide6 unavailable")
    app = QApplication.instance() or QApplication([])
    assert app is not None

    from sponsorscout.ui.tabs.search import _CellItem

    table = QTableWidget(0, 2)
    table.setSortingEnabled(False)
    for i, (txt, key) in enumerate([("4+", (0, 4.0, 2)), ("6 mo", (0, 0.5, 1)),
                                    ("?", (2, 0.0, 9)), ("3-5", (0, 3.0, 3))]):
        table.insertRow(i)
        item = _CellItem(txt, key)
        table.setItem(i, 0, item)
        table.setItem(i, 1, _CellItem("plain-" + txt))
    table.setSortingEnabled(True)

    # Keyed column: numeric order, not lexical ("4+" must not precede "3-5").
    table.sortItems(0)
    assert [table.item(r, 0).text() for r in range(4)] == \
        ["6 mo", "3-5", "4+", "?"]
    # Unkeyed column: falls back to text comparison, no crash.
    table.sortItems(1)
    assert [table.item(r, 1).text() for r in range(4)] == \
        ["plain-3-5", "plain-4+", "plain-6 mo", "plain-?"]


# --- FIX: requirements-section fragments + the single "no statement" value ----

def test_requirements_header_colon_isolates_number():
    for jd, req, lo in (
        ("Requirements: 3-5 years", "3-5 years", 3),
        ("Experience: 4 years", "4 years", 4),
        ("Requirements\n3-5 years", "3-5 years", 3),
        ("Requirements:\n\u2022 4+ years\n\u2022 MS in CS", "4+ years", 4),
    ):
        exp = career_mod.extract_experience(jd, "")
        assert exp["required"] == req, (jd, exp)
        assert exp["min_years"] == lo, (jd, exp)
        assert exp["source"] == "detail_text", (jd, exp)


def test_anchorless_fragment_in_requirements_section():
    exp = career_mod.extract_experience(
        "Requirements: proven ability to ship. 4+ years building distributed systems.",
        "")
    assert exp["required"] == "4+ years", exp
    assert exp["min_years"] == 4, exp


def test_company_tenure_is_never_experience():
    for jd in ("We have over 25 years of experience delivering robots.",
               "Our company has 25 years of experience in logistics."):
        exp = career_mod.extract_experience(jd, "")
        assert exp["required"] == "Unknown", (jd, exp)


def test_mentioned_when_jd_names_experience_without_number():
    """The STORED value stays "Mentioned" (scanner/DB contract, unchanged).

    Only the rendered Search cell collapses it into the single "?" value - see
    test_bare_mention_renders_as_the_single_question_mark below.
    """
    exp = career_mod.extract_experience(
        "Experience in fintech is a plus; we value pragmatic engineers.", "")
    assert exp["required"] == "Mentioned", exp
    assert exp["min_years"] == "", exp
    assert exp["source"] == "detail_text", exp


def test_na_only_when_jd_never_mentions_experience():
    from sponsorscout.ui.tabs.search import _experience_cell
    exp = career_mod.extract_experience("Nice office with free snacks.", "")
    assert exp["required"] == "Unknown", exp
    assert _experience_cell(exp["required"], exp["level"], exp["min_years"]) == "?"


def test_bare_mention_renders_as_the_single_question_mark():
    """A bare mention and a silent ad render the SAME cell value: '?'.

    They used to be "Referenced" vs "NA" - two labels for "no usable
    requirement", which read as two different facts and told the user nothing
    about what the ad actually said. The distinction is not thrown away: the
    hover tooltip still states which case it is.
    """
    from sponsorscout.ui.tabs.search import (
        _EXP_NO_STATEMENT, _experience_cell, _experience_sort_key,
        _experience_tooltip,
    )
    assert _EXP_NO_STATEMENT == "?"
    # Same value for a mention-only ad and a description that never names
    # experience; a level word still wins over both.
    assert _experience_cell("Mentioned", "Unknown", "") == "?"
    assert _experience_cell("Unknown", "Unknown", None) == "?"
    assert _experience_cell("", "", None) == "?"
    assert _experience_cell("Mentioned", "Senior", "") == "Senior"
    # The tooltip is where the difference survives.
    mention_tip = _experience_tooltip({"experience_required": "Mentioned",
                                       "experience_level": "",
                                       "experience_source": "api_description"})
    silent_tip = _experience_tooltip({"experience_required": "Unknown",
                                      "experience_level": "",
                                      "experience_source": "none"})
    assert mention_tip != silent_tip
    assert "figure or a level" in mention_tip
    assert "No experience requirement found" in silent_tip
    # And the two never split the sort either: one tier, listed last.
    assert _experience_sort_key("Mentioned", "Unknown", "") == \
        _experience_sort_key("Unknown", "Unknown", None)
    assert _experience_sort_key("Mentioned", "Unknown", "") > \
        _experience_sort_key("Unknown", "Senior", None)


def test_level_only_row_tooltip_never_claims_no_requirement():
    """A level-only row (title inference) must not be tooltip-ed as empty.

    The cell shows "Senior" (inferred from the job title), so a tooltip
    reading "no experience requirement found in the job description"
    contradicts the very cell it is attached to.
    """
    from sponsorscout.ui.tabs.search import _experience_tooltip

    tip = _experience_tooltip({"experience_required": "",
                                "experience_level": "Senior",
                                "experience_source": "title_inference"})
    assert "Senior" in tip
    assert "No experience requirement found" not in tip


def test_ats_career_parity_new_cases():
    from sponsorscout.scanning.ats import ats_scanner as ats_mod
    for jd in ("Requirements: 3-5 years", "Experience: 4 years",
               "Requirements:\n\u2022 4+ years",
               "Experience in fintech is a plus."):
        assert ats_mod.extract_experience(jd, "") == career_mod.extract_experience(jd, ""), jd


# ── FIX LOCEXP (2026-10-09): shared engine + language coverage ───────────────

def test_both_scanners_use_the_shared_experience_engine():
    """The P0-30 engine lives in core/experience; both scanners import it."""
    from sponsorscout.core import experience as core_exp
    from sponsorscout.scanning.ats import ats_scanner as ats_mod

    assert career_mod.extract_experience is core_exp.extract_experience
    assert ats_mod.extract_experience is core_exp.extract_experience
    assert career_mod.apply_experience_to_record is core_exp.apply_experience_to_record
    # The drift this unification killed: career previously failed P0-46.
    exp = career_mod.extract_experience("Four or more years of experience", "")
    assert (exp["required"], exp["min_years"], exp["level"]) == ("4+ years", 4, "Mid")


_NUMBER_WORD_SAMPLES = [
    # (jd text, expected min_years, expected level)
    ("Quatre ans d'expérience en développement.", 4, "Mid"),
    ("Six ans d'expérience minimum.", 6, "Senior"),
    ("Se requieren tres años de experiencia.", 3, "Mid"),
    ("Seis años de experiencia en ventas.", 6, "Senior"),
    ("Once años de experiencia.", 11, "Lead"),
    ("Con almeno undici anni di esperienza.", 11, "Lead"),
    ("Je hebt minimaal vier jaar ervaring.", 4, "Mid"),
    ("Acht jaar ervaring in de logistiek.", 8, "Senior"),
    ("Experiência mínima de três anos.", 3, "Mid"),
    ("Quatro anos de experiência em vendas.", 4, "Mid"),
    ("Dez anos de experiência.", 10, "Lead"),
]


def test_spelled_out_numbers_extracted():
    """FR/ES/IT/NL/PT number words must resolve like digits do."""
    for jd, min_years, level in _NUMBER_WORD_SAMPLES:
        exp = career_mod.extract_experience(jd, "")
        assert exp["min_years"] == min_years, (jd, exp)
        assert exp["level"] == level, (jd, exp)
        assert exp["source"] == "detail_text", (jd, exp)


def test_plus_forms_pt_es():
    """'Mais/más de' and 'pelo menos' set the plus; FR ', mais de' must not."""
    for jd, req in (("Más de cinco años de experiencia requeridos.", "5+ years"),
                    ("Mais de cinco anos de experiência.", "5+ years"),
                    ("Pelo menos quatro anos de experiência.", "4+ years")):
        exp = career_mod.extract_experience(jd, "")
        assert exp["required"] == req, (jd, exp)
    # FR ", mais de ..." ("but ...") must NOT turn "5 ans" into "5+ ans".
    exp = career_mod.extract_experience(
        "Vous avez 5 ans d'expérience, mais de solides bases en maths.", "")
    assert exp["required"] == "5 years", exp
    assert exp["min_years"] == 5, exp


_NORDIC_SAMPLES = [
    # (jd text, expected min_years, expected level)
    ("Minimum 5 lat doświadczenia w sprzedaży.", 5, "Senior"),
    ("Co najmniej 3 lata doświadczenia.", 3, "Mid"),
    ("Požadujeme minimálně 4 roky zkušeností.", 4, "Mid"),
    ("Nejméně 2 roky zkušeností.", 2, "Junior"),
    ("Vi söker dig med minst 5 års erfarenhet.", 5, "Senior"),
    ("Minimum 3 års erfaring innen salg.", 3, "Mid"),
    ("Vähintään 5 vuotta kokemusta myynnistä.", 5, "Senior"),
    ("6 kuukautta kokemusta vaaditaan.", 0.5, "Junior"),
]


def test_pl_cs_nordic_requirements_extracted():
    for jd, min_years, level in _NORDIC_SAMPLES:
        exp = career_mod.extract_experience(jd, "")
        assert exp["min_years"] == min_years, (jd, exp)
        assert exp["level"] == level, (jd, exp)
        assert exp["source"] == "detail_text", (jd, exp)


def test_none_required_new_languages():
    for jd in ("Sem experiência prévia necessária.",
               "Bez doświadczenia mile widziane.",
               "Ingen erfarenhet krävs."):
        exp = career_mod.extract_experience(jd, "")
        assert exp["required"] == "None required", (jd, exp)
        assert exp["min_years"] == 0, (jd, exp)


def test_since_words_block_company_tenure():
    """'Since X years' tenure must never become a requirement (any language)."""
    for text in ("Wij zijn sinds 10 jaar actief in Nederland.",
                 "Seit 20 Jahren im Unternehmen.",
                 "Notre société existe depuis 15 ans.",
                 "La empresa opera desde hace 10 años.",
                 "Firma istnieje od 5 lat na rynku.",
                 "5 lat temu założyliśmy firmę."):
        exp = career_mod.extract_experience(text, "")
        assert exp["required"] == "Unknown", (text, exp)


def test_level_words_pl_cs_titles():
    for title, level in (("Starszy Programista", "Senior"),
                         ("Młodszy Specjalista", "Junior"),
                         ("Stażysta IT", "Internship"),
                         ("Stážista marketingu", "Internship")):
        exp = career_mod.extract_experience("", title)
        assert exp["level"] == level, (title, exp)
        assert exp["source"] == "title_inference", (title, exp)
