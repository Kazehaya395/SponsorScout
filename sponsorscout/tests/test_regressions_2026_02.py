"""Regression tests for the 2026-02 codebase audit.

Grouped by the area they protect rather than by module, so the link between a
symptom and its fix stays obvious.

  * B7  — ``applied_at`` (the Applications tab's "Saved on" column) was written
    as the ``None`` parameter default on INSERT and then overwritten with
    ``excluded.applied_at`` on every edit, so the date could never appear and
    was destroyed by the first status change.
  * B8  — ``save_company()`` was the only writer of the ``companies`` table and
    nothing called it, so the registry stayed permanently empty. That silently
    disabled the Dashboard "Total Companies" KPI and ``upsert_job``'s industry
    backfill (which SELECTs FROM companies).
  * B9  — ``_INDUSTRY_CACHE`` cached a lookup MISS as if it were the answer, so
    the first job of a company seen before its registry row pinned "" for the
    whole process and the backfill could never recover.
  * B10 — the index-creation guards in ``_apply_migrations`` tested a PRAGMA
    snapshot taken BEFORE the ALTER loop, so on a legacy database the columns
    were added but the indexes were silently skipped, permanently.
  * B11 — the ``_company_record`` body was pasted INTO
    ``_row_to_job_unverified``, clobbering the row->job mapping and leaving it
    referencing an undefined name. Every ingested row raised ``NameError``,
    which the live ingester swallowed, so ``jobs`` stayed empty and BOTH the
    Dashboard and the Search tab showed 0 even though the scan reported
    "wrote=N" for every company. The ingest loop itself now has to survive a
    per-row mapping failure instead of losing the remainder of the file.
  * B12 — the detail pass could never correct a location it had not observed:
    it only overwrote "Unknown"/"Not Specified", so the hardcoded company_hq
    guess became permanent, AND it read the location solely from a JobPosting
    ld+json that many career pages do not publish. A job posted in Eindhoven
    was therefore reported as the employer's HQ city ("Pune, India") and the
    Search tab's Country column was simply wrong.
  * B13 — ``core.integrity`` compared verdicts against "Yes" while the pipeline
    stores "Y", so the whole module was dead code: a posting in India kept a
    hard "we sponsor visas = Yes" that the Blue Card / region guards existed to
    prevent. It is the one place that keeps a text signal from contradicting
    geography, so it has to actually run.
  * B14 — the A2G seed was scoped to a single country while the employer posts
    in two, so every row either got quarantined or inherited the HQ guess. A
    single-country scope cannot describe a multi-country board.
  * B15 — ``_job_country`` fell back to the seed's ``Target Country``, so any
    job whose location text could not be parsed was displayed AND filtered as
    if it were in the country the user merely asked to look for. Scope is an
    input; the Country column is a fact about the posting, and the two must not
    be conflated.
"""
import csv
import io
import sqlite3

from sponsorscout.core import persistence
from sponsorscout.db import database as db
from sponsorscout.scanning import pipeline
from sponsorscout.scanning.career import career_scanner as career
from sponsorscout.scanning.ats.ats_scanner import OUTPUT_FIELDS


# ── helpers ──────────────────────────────────────────────────────────────────

def _seed_job(conn, url="https://jobs.example/1", **over):
    job = {"title": "Engineer", "company": "Acme", "url": url}
    job.update(over)
    persistence.upsert_job(conn, job)
    return job


def _save_company(conn, name="Acme", industry="Fintech"):
    persistence.save_company(conn, {
        "name": name, "country": "Germany", "ats_type": "ashby",
        "careers_url": "", "industry": industry,
    })


def _write_jobs_csv(path, rows):
    """Write a scanner-shaped jobs CSV using the real output schema."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=OUTPUT_FIELDS,
                            extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in OUTPUT_FIELDS})
    path.write_bytes(buf.getvalue().encode("utf-8"))
    return path


def _pleo_row(**over):
    row = {
        "Company Name": "Pleo", "Seed Name": "Pleo", "Hiring Company": "Pleo",
        "Target Country": "Global", "Industry Type": "Fintech",
        "Job Location": "Berlin, Germany", "Raw Location": "Berlin, Germany",
        "Provider": "ashby",
    }
    row.update(over)
    return row


# ── B7: applied_at must survive an application edit ───────────────────────────

def test_application_applied_at_is_stamped_on_first_save(db_path):
    """Saving a job to Applications must record the "Saved on" date.

    The column was inserted as the parameter default (None), because no caller
    ever passes it, so the Applications tab's "Saved on" column could never
    render a date at all.
    """
    db.upsert_application(db_path, job_url="https://jobs.example/a",
                          company="Acme", title="Engineer")
    conn = db.get_connection(db_path)
    applied_at = conn.execute(
        "SELECT applied_at FROM applications WHERE job_url=?",
        ("https://jobs.example/a",)).fetchone()[0]
    conn.close()
    assert applied_at, "applied_at must be stamped on insert"


def test_application_edit_does_not_wipe_applied_at(db_path):
    """Changing status/notes must preserve the original save date.

    ON CONFLICT wrote ``excluded.applied_at`` unconditionally, so the edit
    form — which never passes the field — overwrote it with NULL, destroying
    the date the first time a user moved a job to "interview".
    """
    db.upsert_application(db_path, job_url="https://jobs.example/b",
                          company="Acme", title="Engineer")
    db.upsert_application(db_path, job_url="https://jobs.example/b",
                          company="Acme", title="Engineer",
                          status="interview", notes="phone screen")
    conn = db.get_connection(db_path)
    row = conn.execute(
        "SELECT applied_at, status, notes FROM applications WHERE job_url=?",
        ("https://jobs.example/b",)).fetchone()
    conn.close()
    assert row["applied_at"], "editing an application must not wipe applied_at"
    assert row["status"] == "interview"
    assert row["notes"] == "phone screen"


def test_application_explicit_applied_at_still_wins(db_path):
    """An explicitly supplied applied_at must still overwrite the stored one."""
    db.upsert_application(db_path, job_url="https://jobs.example/c",
                          company="Acme", title="Engineer")
    db.upsert_application(db_path, job_url="https://jobs.example/c",
                          company="Acme", title="Engineer",
                          applied_at="2021-05-04 10:00:00")
    conn = db.get_connection(db_path)
    applied_at = conn.execute(
        "SELECT applied_at FROM applications WHERE job_url=?",
        ("https://jobs.example/c",)).fetchone()[0]
    conn.close()
    assert applied_at == "2021-05-04 10:00:00", \
        "an explicitly supplied applied_at must still overwrite the stored value"
# ── B8: the companies registry must actually be written ───────────────────────

def test_ingest_populates_the_companies_registry(db_path, tmp_path):
    """save_company() was the only writer of `companies` and nothing called it.

    That left the registry permanently empty, which silently disabled the
    Dashboard "Total Companies" KPI and upsert_job's industry backfill. The
    ingest pass now registers each company it sees.
    """
    csv_path = _write_jobs_csv(tmp_path / "jobs.csv", [_pleo_row(**{
        "Job Title": "Senior Backend Engineer",
        "Job URL": "https://jobs.ashbyhq.com/pleo/abc",
        "Canonical Job ID": "pleo-abc",
        "Visa Sponsorship": "Yes",
    })])
    ingested, _dupes = pipeline._ingest_output_csv(
        db_path, csv_path, "R1", "direct", set())

    conn = db.get_connection(db_path)
    companies = conn.execute(
        "SELECT name, country, ats_type, industry FROM companies").fetchall()
    jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    assert ingested == 1 and jobs == 1
    assert len(companies) == 1, "the companies registry must be populated"
    assert companies[0]["name"] == "Pleo"
    assert companies[0]["country"] == "Germany"
    assert companies[0]["industry"] == "Fintech"


def test_ingest_registers_each_company_once(db_path, tmp_path):
    """Several jobs from one company must produce exactly one registry row."""
    csv_path = _write_jobs_csv(tmp_path / "jobs.csv", [
        _pleo_row(**{"Job Title": f"Engineer {i}",
                     "Job URL": f"https://jobs.ashbyhq.com/pleo/job-{i}",
                     "Canonical Job ID": f"pleo-{i}"})
        for i in range(4)
    ])
    pipeline._ingest_output_csv(db_path, csv_path, "R1", "direct", set())

    conn = db.get_connection(db_path)
    count = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    assert jobs == 4
    assert count == 1


def test_ingest_registry_survives_a_bad_company_row(db_path, tmp_path,
                                                    monkeypatch):
    """A failing registry write must not discard the ingested job rows."""
    csv_path = _write_jobs_csv(tmp_path / "jobs.csv", [_pleo_row(**{
        "Job Title": "Engineer",
        "Job URL": "https://jobs.ashbyhq.com/pleo/abc",
        "Canonical Job ID": "pleo-abc",
    })])
    calls = {"n": 0}

    def _boom(conn, company):
        calls["n"] += 1
        raise sqlite3.OperationalError("simulated registry failure")

    monkeypatch.setattr(persistence, "save_company", _boom)
    ingested, _dupes = pipeline._ingest_output_csv(
        db_path, csv_path, "R1", "direct", set())

    conn = db.get_connection(db_path)
    jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    assert jobs == 1, \
        "a failing registry write must not discard the committed job rows"
    assert calls["n"] == 1, "the registry write must have been attempted"


# ── B11: a scanner row must map to a job, or nothing reaches the UI ───────────

def test_row_maps_to_a_job_dict():
    """``_row_to_job`` must return a complete job mapping for a real row.

    Regression: the ``_company_record`` body was pasted INTO
    ``_row_to_job_unverified``, which clobbered the real mapping and left it
    referencing an undefined ``job`` name.  Every ingested row then raised
    ``NameError`` inside the ingest loop, so the whole file was dropped and
    ``jobs`` stayed empty — Dashboard and Search both read from that table, so
    both tabs showed 0 while the scan itself happily reported "wrote=166".
    """
    row = _pleo_row(**{
        "Job Title": "Senior Backend Engineer",
        "Job URL": "https://jobs.ashbyhq.com/pleo/abc",
        "Canonical Job ID": "pleo-abc",
        "Visa Sponsorship": "Yes",
        "Support Confidence": "0.8",
        "Sponsorship History Score": "75",
        "Experience Level": "Senior",
    })
    job = pipeline._row_to_job(row, source_subtype="direct", run_id="R1")

    assert job is not None, "a well-formed row must map to a job"
    # The keys the DB write and the Search tab depend on.
    assert job["title"] == "Senior Backend Engineer"
    assert job["company"] == "Pleo"
    assert job["url"] == "https://jobs.ashbyhq.com/pleo/abc"
    assert job["external_id"] == "pleo-abc"
    assert job["run_id"] == "R1"
    assert job["verified_active"] is True and job["is_expired"] is False
    assert job["sponsorship_score"] > 0
    assert job["experience_level"] == "Senior"


def test_row_without_url_or_title_maps_to_none():
    """The guard clause must still reject unusable rows (it must not be a crash)."""
    assert pipeline._row_to_job(_pleo_row(**{"Job Title": "Engineer"}),
                                source_subtype="direct", run_id="R1") is None
    assert pipeline._row_to_job(_pleo_row(**{"Job Title": "Engineer",
                                             "Job URL": ""}),
                                source_subtype="direct", run_id="R1") is None
    assert pipeline._row_to_job(_pleo_row(**{"Job Title": "Unknown",
                                             "Job URL": "https://x/1"}),
                                source_subtype="direct", run_id="R1") is None


def test_ingest_writes_every_row_and_surfaces_no_error_events(db_path, tmp_path):
    """End-to-end: a multi-row CSV must land in `jobs`, with zero ingest errors.

    This is the exact user-visible symptom (both tabs read `jobs`), asserted at
    the level it was observed: the row count and the run's error timeline.
    """
    csv_path = _write_jobs_csv(tmp_path / "jobs.csv", [
        _pleo_row(**{"Job Title": f"Engineer {i}",
                     "Job URL": f"https://jobs.ashbyhq.com/pleo/job-{i}",
                     "Canonical Job ID": f"pleo-{i}"})
        for i in range(5)
    ])
    ingested, dupes = pipeline._ingest_output_csv(
        db_path, csv_path, "R1", "direct", set())

    conn = db.get_connection(db_path)
    stored = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    events = conn.execute(
        "SELECT level, message FROM scan_events WHERE run_id='R1'").fetchall()
    conn.close()

    assert ingested == 5 and dupes == 0
    assert stored == 5, "every accepted scanner row must reach the jobs table"
    bad = [dict(e) for e in events if e["level"] in ("error", "warning")]
    assert not bad, f"ingest must not record failures: {bad}"

    # And the two tabs that were showing 0 must now see the rows.
    stats = db.get_dashboard_stats(db_path)
    assert stats["verified_jobs"] == 5
    assert stats["companies"] == 1
    assert len(db.search_jobs(db_path)) == 5


def test_one_unmappable_row_does_not_discard_the_rest_of_the_file(
        db_path, tmp_path, monkeypatch):
    """A single poisoned row must cost one row, not the whole CSV (B11).

    This is the failure mode that made the bug invisible: the mapper raised,
    the exception escaped the ingest loop, and both callers swallowed it, so
    the run reported success while ingesting nothing at all.
    """
    csv_path = _write_jobs_csv(tmp_path / "jobs.csv", [
        _pleo_row(**{"Job Title": f"Engineer {i}",
                     "Job URL": f"https://jobs.ashbyhq.com/pleo/job-{i}",
                     "Canonical Job ID": f"pleo-{i}"})
        for i in range(5)
    ])
    real_mapper = pipeline._row_to_job
    state = {"n": 0}

    def _explode_on_third(row, **kw):
        state["n"] += 1
        if state["n"] == 3:
            raise RuntimeError("simulated mapper bug")
        return real_mapper(row, **kw)

    monkeypatch.setattr(pipeline, "_row_to_job", _explode_on_third)
    ingested, _dupes = pipeline._ingest_output_csv(
        db_path, csv_path, "R1", "direct", set())

    conn = db.get_connection(db_path)
    stored = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    events = [dict(r) for r in conn.execute(
        "SELECT level, message FROM scan_events WHERE run_id='R1'").fetchall()]
    conn.close()

    assert ingested == 4, "the four healthy rows must still be ingested"
    assert stored == 4
    # And the loss must be visible in the run timeline, not silent.
    assert any(e["level"] == "error" and "Row mapping failed" in e["message"]
               for e in events), \
        f"the dropped row must be recorded as a run event: {events}"


# ── B12: a row's location must reflect the job, not the employer's HQ ─────────

def test_detail_header_location_is_harvested_from_pages_without_ldjson():
    """WordPress/Elementor pages state country+city as text under the <h1>.

    Real header text captured from a2gtechnologies.com, which publishes only a
    Yoast WebSite graph and NO JobPosting ld+json -- the exact case the old
    detail pass could not see.
    """
    s = career.CareerPortalScanner()
    assert s._location_from_detail_header(
        "Netherlands\nEindhoven\nAny Masters Degree") == "Netherlands, Eindhoven"
    # "Best" is not in the gazetteer, so the composite is rejected and the
    # country (which IS known) is used instead -- never a wrong answer.
    assert s._location_from_detail_header(
        "Netherlands\nBest\nAny Bachelors Degree") == "Netherlands"
    # A genuinely-Indian posting must not be dragged to the HQ country.
    assert s._location_from_detail_header(
        "India\nPune\nBTech, MTech") == "India, Pune"


def test_detail_header_location_rejects_non_location_text():
    """Education / CTA lines must never be mistaken for a place."""
    s = career.CareerPortalScanner()
    assert s._location_from_detail_header(
        "Any Masters Degree\nApply for this position\nJob Description") == ""
    assert s._location_from_detail_header("") == ""
    assert s._location_from_detail_header(None) == ""


def test_company_hq_guess_is_replaceable_but_a_real_card_location_is_not():
    """Only never-observed locations may be overwritten by detail evidence."""
    is_unverified = career.CareerPortalScanner._location_is_unverified

    # The bug: the HQ guess looked like a real location and was kept forever.
    assert is_unverified({"Job Location": "Pune, India",
                          "Location Source": "company_hq"})
    # Placeholders, as before.
    assert is_unverified({"Job Location": "Unknown", "Location Source": "card"})
    assert is_unverified({"Job Location": "", "Location Source": ""})
    # A location actually read off the listing card / API is real evidence.
    assert not is_unverified({"Job Location": "Netherlands, Eindhoven",
                              "Location Source": "card"})
    assert not is_unverified({"Job Location": "Pune, India",
                              "Location Source": "detail"})


class _FakePage:
    """Minimal Playwright page double returning a canned evaluate() result."""

    def __init__(self, data):
        self._data = data

    def goto(self, *_a, **_kw):
        pass

    def wait_for_timeout(self, *_a):
        pass

    def evaluate(self, _script):
        return self._data


def _enrich(s, rec, data):
    """Run _detail_enrich_one over one row with a canned page payload."""
    url = rec["Job URL"]
    return s._detail_enrich_one(_FakePage(data), url, {url: rec})


def _hq_row():
    """A row as the listing crawl leaves it: HQ guess, no real location."""
    return {"Job Title": "Sr. Scrum Master", "Job Location": "Pune, India",
            "Location Source": "company_hq", "Raw Location": "",
            "Job URL": "https://a2gtechnologies.com/jobs/sr-scrum-master",
            "Job Type": "Unknown / Unknown"}



def test_detail_pass_corrects_a_company_hq_guess():
    """The user's case: an Eindhoven job reported as Pune, India."""
    s = career.CareerPortalScanner()
    rec = _hq_row()
    outcome = _enrich(s, rec, {
        "desc": "Sr. Scrum Master\nNetherlands\nEindhoven\n"
                "Any Masters Degree\nVisa Sponsorship Available",
        "loc": "",           # no JobPosting ld+json on this page
        "hdr": "Netherlands\nEindhoven\nAny Masters Degree",
    })
    assert outcome == "ok"
    assert rec["Job Location"] == "Netherlands, Eindhoven"
    assert rec["Location Source"] == "detail"
    # The harvested text is kept so the country can be re-derived later.
    assert "Netherlands" in rec["Raw Location"]

    # And that is exactly what the Search tab's Country column is built from.
    from sponsorscout.core.location_country import country_from_location
    assert country_from_location(rec["Job Location"]) == "Netherlands"


def test_detail_pass_never_clobbers_a_real_card_location():
    """A card-observed location must survive the detail visit."""
    s = career.CareerPortalScanner()
    rec = _hq_row()
    rec["Job Location"] = "Amsterdam, Netherlands"
    rec["Location Source"] = "card"
    _enrich(s, rec, {"desc": "Sr. Scrum Master\nSomewhere else",
                     "loc": "", "hdr": "Germany\nBerlin"})
    assert rec["Job Location"] == "Amsterdam, Netherlands"
    assert rec["Location Source"] == "card"


def test_detail_pass_uses_ldjson_location_when_present():
    """A real JobPosting jobLocation still wins -- it is the best evidence."""
    s = career.CareerPortalScanner()
    rec = _hq_row()
    _enrich(s, rec, {"desc": "Sr. Scrum Master", "loc": "Berlin, Germany",
                     "hdr": "Netherlands\nEindhoven"})
    assert rec["Job Location"] == "Berlin, Germany"
    assert rec["Location Source"] == "detail"


def test_hq_guessed_rows_are_visited_first_by_the_detail_budget():
    """A guess must sort as WEAK, else the visit that could fix it is skipped."""
    common = {"Visa Sponsorship": "Yes", "Support Evidence": "x",
              "Experience Required": "Mentioned"}
    guess = career.CareerPortalScanner._detail_priority(
        {"Job Location": "Pune, India", "Location Source": "company_hq", **common})
    real = career.CareerPortalScanner._detail_priority(
        {"Job Location": "Netherlands, Eindhoven", "Location Source": "card",
         **common})
    assert guess[0] == 0, "a company_hq guess must be treated as weak evidence"
    assert real[0] == 1
    assert guess < real, "guessed rows must be visited before settled ones"


def test_detail_evaluate_js_is_a_raw_literal():
    """The harvest JS must stay a RAW string.

    A non-raw literal turns ``split('\\n')`` into a real newline inside a
    single-quoted JavaScript string literal, which is a JS SyntaxError -- the
    evaluate() would throw and every detail visit would be lost. No ordinary
    Python check catches that, so it is pinned here.
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(career))
    literals = [n.args[0].value for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and getattr(n.func, "attr", "") == "evaluate"
                and n.args and isinstance(n.args[0], ast.Constant)
                and isinstance(n.args[0].value, str)
                and "JobPosting" in n.args[0].value]
    assert literals, "the detail-harvest evaluate literal was not found"
    js = literals[0]
    assert "split('\\n')" in js, \
        "JS string escapes were expanded: the literal must be raw"
    # A JS string literal may not contain a raw newline. If the Python literal
    # were non-raw, the \n in split('\n') would arrive here as a real one.
    assert "'\n'" not in js, \
        "a real newline leaked inside a JS string literal"


# ── B13: the geography guards must actually run ──────────────────────────────

def _scanner_row(country_via_location, **verdicts):
    row = {
        "Company Name": "Acme", "Seed Name": "Acme",
        "Hiring Company": "Acme", "Provider": "ashby",
        "Job Title": "Engineer",
        "Job URL": "https://jobs.ashbyhq.com/acme/1",
        "Canonical Job ID": "acme-1",
        "Job Location": country_via_location,
        "Raw Location": country_via_location,
    }
    row.update(verdicts)
    return row


def test_verdict_guards_fire_on_the_stored_vocabulary():
    """'Y' in, 'Unknown' out -- the whole point of the integrity module.

    It compared against "Yes", the pipeline stores "Y", so nothing was ever
    demoted and a job in Pune could advertise an EU Blue Card.
    """
    job = pipeline._row_to_job(
        _scanner_row("Pune, India", **{
            "Visa Sponsorship": "Yes", "EU Blue Card": "Yes"}),
        source_subtype="direct", run_id="R1")
    assert job["country"] == "India"
    assert job["visa_sponsorship"] == "Unknown", \
        "a sponsorship claim outside the region must be demoted to Unknown"
    assert job["eu_blue_card_verdict"] == "Unknown"
    # Never a hard "No": the text signal stays, just not as a fact.
    assert job["visa_sponsorship"] != "N"
    # The legacy booleans must follow the authoritative columns.
    assert job["eu_blue_card"] == 0
    # And the reason is auditable rather than silent.
    assert "integrity" in (job.get("support_evidence_type") or "")


def test_verdict_guards_keep_a_valid_in_region_claim():
    """The fix must not over-correct: an EU job keeps a real "Y"."""
    job = pipeline._row_to_job(
        _scanner_row("Eindhoven, Netherlands", **{
            "Visa Sponsorship": "Yes", "EU Blue Card": "Yes"}),
        source_subtype="direct", run_id="R1")
    assert job["country"] == "Netherlands"
    assert job["visa_sponsorship"] == "Y"
    assert job["eu_blue_card_verdict"] == "Y"
    assert job["eu_blue_card"] == 1


def test_verdict_guards_leave_an_explicit_refusal_alone():
    """A hard "No" is real evidence and is never rewritten to Unknown."""
    job = pipeline._row_to_job(
        _scanner_row("Pune, India", **{
            "Visa Sponsorship": "No", "EU Blue Card": "No"}),
        source_subtype="direct", run_id="R1")
    assert job["visa_sponsorship"] == "N"
    assert job["eu_blue_card_verdict"] == "N"


def test_verdict_guards_accept_both_spellings():
    """Direct callers may pass the long form; both must behave identically."""
    from sponsorscout.core.integrity import check_integrity
    for spelling in ("Y", "Yes", "yes", "true", "1"):
        out, violations = check_integrity(
            {"country": "India", "visa_sponsorship": spelling,
             "eu_blue_card_verdict": spelling, "relocation_support": "N"})
        assert out["visa_sponsorship"] == "Unknown", spelling
        assert out["eu_blue_card_verdict"] == "Unknown", spelling
        assert out["eu_blue_card"] == 0, spelling
        assert violations, spelling



# ── B14: a multi-country board must not be pinned to one country ─────────────

def test_a2g_seed_is_scoped_global():
    """The bundled seed must not pin a two-country employer to one of them."""
    import csv as _csv
    from pathlib import Path
    path = (Path(__file__).resolve().parent.parent / "data" / "seeds"
            / "company_Career_seed.csv")
    if not path.exists():          # installed layout
        path = Path("company_Career_seed.csv")
    if not path.exists():
        import pytest
        pytest.skip("bundled career seed not found")
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = {_r["name"]: _r for _r in _csv.DictReader(fh)}
    a2g = rows["A2G Technologies"]
    assert (a2g.get("target_country") or "").strip().casefold() == "global", \
        "A2G posts in both NL and IN; a single-country scope loses half of it"


def test_seed_repair_converges_every_historical_a2g_value():
    """Installs may still hold any value we ever shipped; all must converge."""
    from sponsorscout.application import seed_manager as sm
    url = "https://a2gtechnologies.com/jobs"
    for stale in ("Netherlands", "India"):
        row = {"name": "A2G Technologies", "careers_url": url,
               "target_country": stale, "notes": "whatever"}
        sm._apply_repairs_to_rows("career", [row], log_fn=lambda _m: None)
        assert row["target_country"] == "Global", \
            f"a seed still carrying {stale!r} must be repaired to Global"


def test_seed_repair_never_clobbers_a_deliberate_user_edit():
    """Only the exact stale values we shipped may be rewritten."""
    from sponsorscout.application import seed_manager as sm
    for keep in ("Global", "Germany"):
        row = {"name": "A2G Technologies",
               "careers_url": "https://a2gtechnologies.com/jobs",
               "target_country": keep, "notes": "user edited this"}
        n = sm._apply_repairs_to_rows("career", [row], log_fn=lambda _m: None)
        assert n == 0 and row["target_country"] == keep, \
            f"a user's deliberate {keep!r} scope must survive"


def test_seed_repair_only_touches_its_own_row():
    """A different company sharing a stale value is left untouched."""
    from sponsorscout.application import seed_manager as sm
    row = {"name": "ABN AMRO", "careers_url": "https://werkenbijabnamro.nl",
           "target_country": "Netherlands", "notes": "verified: A2G Consulting"}
    n = sm._apply_repairs_to_rows("career", [row], log_fn=lambda _m: None)
    assert n == 0 and row["target_country"] == "Netherlands"


def test_global_scope_admits_every_country():
    """Global must short-circuit the scope filter for both regions."""
    s = career.CareerPortalScanner()
    target_row = {"scope_policy": "job_location", "target_country": "Global"}
    for loc in ("Netherlands, Eindhoven", "India, Pune"):
        assert s._scope_allows(target_row, loc, "", "https://x/jobs/1"), loc


# ── B15: Country is evidence, never the seed's scope ─────────────────────────

_SCAN_ROW = {"Company Name": "Acme", "Seed Name": "Acme", "Provider": "ashby",
             "Job Title": "Engineer", "Job URL": "https://x/1",
             "Canonical Job ID": "c1"}


def test_country_comes_from_the_posting_not_the_seed_scope():
    """A real location always wins over the seed's Target Country."""
    row = {**_SCAN_ROW, "Job Location": "Netherlands, Eindhoven",
           "Raw Location": "Netherlands Eindhoven", "Target Country": "India"}
    assert pipeline._job_country(row) == "Netherlands"


def test_raw_location_still_counts_as_evidence():
    """F6 behaviour must survive: Raw Location is used when Job Location is not."""
    row = {**_SCAN_ROW, "Job Location": "Unknown", "Raw Location": "Pune, India",
           "Target Country": "Italy"}
    assert pipeline._job_country(row) == "India"


def test_unreadable_location_yields_no_country_not_the_seed():
    """The regression: an unparsable location used to inherit the seed country."""
    for location, raw in (("Somewhere", ""), ("Unknown", ""), ("", "")):
        row = {**_SCAN_ROW, "Job Location": location, "Raw Location": raw,
               "Target Country": "India"}
        assert pipeline._job_country(row) == "", \
            f"{location!r}/{raw!r} must not be reported as the seed's country"


def test_registry_country_does_not_inherit_the_seed_either():
    """The companies registry must obey the same rule."""
    row = {**_SCAN_ROW, "Job Location": "Somewhere", "Raw Location": "",
           "Target Country": "India"}
    job = {"company": "Acme", "country": pipeline._job_country(row)}
    assert pipeline._company_record(row, job)["country"] == ""


def test_registry_country_is_populated_when_the_posting_says_where():
    row = {**_SCAN_ROW, "Job Location": "Berlin, Germany",
           "Raw Location": "Berlin, Germany", "Target Country": "India"}
    job = {"company": "Acme", "country": pipeline._job_country(row)}
    assert pipeline._company_record(row, job)["country"] == "Germany"


def test_search_tab_renders_an_unknown_country_explicitly():
    """A blank cell is indistinguishable from a bug; say "Unknown" instead."""
    from sponsorscout.ui.tabs.search import _row_values
    row = {"title": "T", "company": "C", "country": "", "location": "L",
           "_exp_display": "?", "visa_sponsorship": "N",
           "eu_blue_card_verdict": "N", "relocation_support": "N",
           "remote_type": "onsite", "first_seen_at": "2026-10-02"}
    assert _row_values(row)[2] == "Unknown"
    row["country"] = "  "
    assert _row_values(row)[2] == "Unknown", "whitespace is not a country"
    row["country"] = "Netherlands"
    assert _row_values(row)[2] == "Netherlands"


def test_seed_scope_does_not_overwrite_a_parsed_country():
    """"Milan, Spain" parsed as Spain must not be rewritten to Italy by scope."""
    import inspect as _inspect

    from sponsorscout.core.location_country import country_from_location
    # The disambiguation is retained for "Milan, MI" (Michigan code collision),
    # but only as a display tidy: the parser already reads that as Italy, so the
    # country is unchanged either way.
    assert country_from_location("Milan, MI") == "Italy"
    # A real Spanish reading must survive the Italy-scoped seeds untouched.
    assert country_from_location("Milan, Spain") == "Spain"

    body = _inspect.getsource(career)
    assert 'out_location in {"Milan, MI", "Milan, Spain"}' not in body, \
        "the seed scope must no longer be able to overwrite a parsed country"
    assert 'out_location == "Milan, MI"' in body, \
        "the Michigan-code disambiguation should still be present"


def test_verdict_tooltip_quotes_the_sentence_behind_the_cell():
    """A Y/N/? must be auditable without opening the posting.

    The Amazon case that prompted this: Sponsor "N" beside Reloc "Y" looked
    like a bug, because the evidence was stored but nothing in the UI ever
    displayed it.
    """
    from sponsorscout.ui.tabs.search import _verdict_tooltip
    row = {"visa_sponsorship": "N", "relocation_support": "Y",
           "eu_blue_card_verdict": "Unknown", "support_confidence": 0.9,
           "support_evidence": "You must have the right to work in the "
                               "country of employment.",
           "blue_card_evidence": ""}
    tip = _verdict_tooltip(row, 5)
    assert "Sponsor" in tip and "right to work" in tip
    assert "90%" in tip
    assert "relocation_support" not in tip


def test_verdict_tooltip_never_attributes_the_wrong_evidence():
    """The Blue Card must not be credited with a visa/relocation sentence.

    ``support_evidence`` is a shared column (best visa AND best relocation
    sentence). Quoting it under "Blue Card" would claim a relocation sentence
    decided the Blue Card verdict.
    """
    from sponsorscout.ui.tabs.search import _verdict_tooltip
    row = {"visa_sponsorship": "N", "relocation_support": "Y",
           "eu_blue_card_verdict": "Unknown", "support_confidence": 0.9,
           "support_evidence": "Relocation support will be provided.",
           "blue_card_evidence": ""}
    tip = _verdict_tooltip(row, 6)
    assert "Relocation support will be provided." not in tip
    assert "not a \"no\"" in tip, "Unknown must be explained, not just shown"
    # With its own evidence present, the Blue Card DOES quote it.
    row["blue_card_evidence"] = "We offer a Blue Card."
    assert "We offer a Blue Card." in _verdict_tooltip(row, 6)


def test_verdict_tooltip_handles_a_row_with_no_evidence_at_all():
    from sponsorscout.ui.tabs.search import _verdict_tooltip
    bare = {"visa_sponsorship": "Unknown", "eu_blue_card_verdict": "Unknown",
            "relocation_support": "Unknown", "support_evidence": "",
            "blue_card_evidence": "", "support_confidence": 0}
    for col in (5, 6, 7):
        tip = _verdict_tooltip(bare, col)
        assert "?" in tip and "not a \"no\"" in tip


def test_search_jobs_exposes_the_evidence_columns(db_path):
    """The tooltip is only possible if search_jobs actually selects them."""
    conn = db.get_connection(db_path)
    persistence.upsert_job(conn, {
        "title": "Engineer", "company": "Acme",
        "url": "https://jobs.example/ev", "visa_sponsorship": "N",
        "support_evidence": "You must have the right to work.",
        "support_confidence": 0.8, "blue_card_evidence": "Blue card offered."})
    conn.close()
    row = db.search_jobs(db_path)[0]
    assert row["support_evidence"] == "You must have the right to work."
    assert row["blue_card_evidence"] == "Blue card offered."
    assert row["support_confidence"] == 0.8
    assert row["relocation_required"] == ""


def test_search_jobs_bounds_the_evidence_columns(db_path):
    """substr() guards the same memory concern that excludes `description`."""
    conn = db.get_connection(db_path)
    persistence.upsert_job(conn, {
        "title": "Engineer", "company": "Acme",
        "url": "https://jobs.example/long",
        "support_evidence": "x" * 5000})
    conn.close()
    assert len(db.search_jobs(db_path)[0]["support_evidence"]) == 800


def test_row_to_job_and_company_record_have_separate_bodies():
    """Structural guard: the two mappers must not share a body.

    Cheap belt-and-braces against the copy/paste that caused B11 — if someone
    pastes one into the other again, the source no longer contains the other's
    distinctive keys.
    """
    import inspect
    job_src = inspect.getsource(pipeline._row_to_job_unverified)
    rec_src = inspect.getsource(pipeline._company_record)
    assert "sponsorship_history" not in job_src, \
        "_company_record body has leaked into _row_to_job_unverified"
    assert "sponsorship_score" in job_src
# ── B9: a cache MISS must never be cached as a fact ──────────────────────────

def test_industry_cache_does_not_pin_a_miss(db_path):
    """A company ingested before its registry row must still backfill later.

    _INDUSTRY_CACHE stored the fallback value even on a miss, so the first
    lookup for a company with no registry row pinned "" for the whole process.
    """
    conn = db.get_connection(db_path)
    persistence._INDUSTRY_CACHE.clear()

    # First job: the registry has no row for Acme yet -> a MISS.
    _seed_job(conn, url="https://jobs.example/1")
    assert "Acme" not in persistence._INDUSTRY_CACHE, \
        "a lookup miss must not be cached as an empty industry"

    # The registry row appears (as it now does mid-scan).
    _save_company(conn)

    # The second job must pick the industry up.
    _seed_job(conn, url="https://jobs.example/2")
    row = conn.execute(
        "SELECT industry FROM jobs WHERE url=?",
        ("https://jobs.example/2",)).fetchone()
    conn.close()
    assert row["industry"] == "Fintech"


def test_industry_cache_still_caches_real_hits(db_path):
    """The positive cache must survive — it is the F11 bulk-ingest win."""
    conn = db.get_connection(db_path)
    persistence._INDUSTRY_CACHE.clear()
    _save_company(conn)
    _seed_job(conn, url="https://jobs.example/3")
    assert persistence._INDUSTRY_CACHE.get("Acme") == "Fintech"
    conn.close()


# ── B10: migrations must create the indexes they guard on ────────────────────

def test_migration_creates_indexes_for_legacy_columns(db_path):
    """Index creation must be guarded on the POST-migration column set.

    The guards tested a PRAGMA snapshot taken before the ALTER loop, so on a
    legacy database the columns were added but the indexes were silently
    skipped — permanently, on every later launch as well.
    """
    # A legacy jobs table: it predates remote_type / experience_level but has
    # the rest of the original shape the ALTER-based migration runs against.
    legacy = sqlite3.connect(db_path)
    legacy.execute("DROP TABLE IF EXISTS jobs")
    legacy.execute("""
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY,
            external_id TEXT,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            country TEXT DEFAULT '',
            location TEXT DEFAULT '',
            url TEXT UNIQUE NOT NULL,
            ats_source TEXT DEFAULT '',
            source_type TEXT DEFAULT 'verified',
            source_name TEXT DEFAULT '',
            description TEXT DEFAULT '',
            trust_score INTEGER DEFAULT 0,
            freshness_score INTEGER DEFAULT 0,
            sponsorship_score INTEGER DEFAULT 0,
            match_score INTEGER DEFAULT 0,
            verified_active INTEGER DEFAULT 0,
            is_expired INTEGER DEFAULT 0,
            first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_verified_at TEXT
        )
    """)
    legacy.commit()
    legacy.close()

    db.initialize(db_path)  # runs _apply_migrations

    conn = db.get_connection(db_path)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    conn.close()

    assert "remote_type" in cols and "experience_level" in cols
    assert "idx_jobs_remote" in names, "idx_jobs_remote must be created"
    assert "idx_jobs_experience" in names, "idx_jobs_experience must be created"
