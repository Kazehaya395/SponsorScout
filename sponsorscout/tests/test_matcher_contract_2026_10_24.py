"""Permanent lossless prefilter, packaged resources and incremental pins.

No seed scans, third-party portals or hardware acceleration are involved.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from sponsorscout.db import database as db
from sponsorscout.core import persistence
from sponsorscout.insights import terms, queries


@pytest.fixture
def skill_index(monkeypatch):
    names = ("_MATCH_EDGES", "_MATCH_FAIL", "_MATCH_OUTPUTS",
             "_MATCH_LITERAL_CLAUSES", "_MATCH_CLAUSE_SIZE",
             "_MATCH_CLAUSE_SKILL", "_MATCH_ORDER", "_SKILLS")
    for name in names:
        monkeypatch.setattr(terms, name, [])
    monkeypatch.setattr(terms, "_MATCH_FALLBACK", set())
    monkeypatch.setattr(terms, "_DATA_LOADED", True)

    def install(patterns):
        entries = [dict(term=f"skill-{n}", group="General",
                        regex=re.compile(pattern, flags))
                   for n, (pattern, flags) in enumerate(patterns)]
        terms._SKILLS = entries
        terms._build_skill_index()
        return entries
    return install


@pytest.mark.parametrize("pattern,flags,texts", [
    (r"(?:alpha|beta)\s+management", re.I,
     ["ALPHA management", "beta\nmanagement", "stakeholder management", "alpha alone"]),
    (r"(?:alpha|)\s*omega", re.I, ["omega", "alpha omega", "beta alpha"]),
    (r"(?:optional)?required", 0, ["required", "optionalrequired", "optional"]),
    (r"(?:ab){2,4}tail", 0, ["ababtail", "ababababtail", "abtail"]),
    (r"(?<=prefix)word", re.I, ["PREFIXword", "word", "prefix alone"]),
    (r"(?=alpha)alpha", 0, ["alpha", "beta"]),
    (r"alpha(?!beta)", re.I, ["ALPHA gamma", "alphabeta"]),
    (r"(alpha)\1", 0, ["alphaalpha", "alpha", "AlphaAlpha"]),
    (r"(alpha)?(?(1)beta|gamma)", 0, ["alphabeta", "gamma", "alpha"]),
    (r"(?>alpha|beta)omega", 0, ["alphaomega", "betaomega", "alpha"]),
    (r"(?:alpha){1,3}+omega", 0, ["alphaomega", "alphaalphaomega", "omega"]),
    (r"[a-z]{2,4}\s+cloud", re.I, ["xy cloud", "xy\nCLOUD", "cloud"]),
    (r"kilo\s+skill", re.I, ["KILO ſKILL", "kilo skill", "kilo other"]),
    (r"title\s+ironic", re.I, ["TİTLE ıRONIC", "title ironic", "title"]),
    (r"C\+\+|C#|\.NET", 0, ["C++", "C#", ".NET", ".net", "C"]),
    (r"(?i:alpha)(?-i:BETA)", 0, ["ALPHABETA", "alphabeta", "alphaBETA"]),
    (r"^alpha$", re.I | re.M, ["before\nALPHA\nafter", "alpha suffix"]),
    (r"alpha\b|beta\b", re.I, ["ALPHA!", "beta", "alphabet"]),
    (r"\d+|\w", 0, ["123", "x", "!!!"]),
])
def test_prefilter_never_drops_original_regex_match(skill_index, pattern, flags, texts):
    entries = skill_index([(pattern, flags)])
    regex = entries[0]["regex"]
    for text in texts:
        expected = ["skill-0"] if regex.search(text) else []
        assert terms.extract_skills(text) == expected, (pattern, text)
        if expected:
            assert 0 in terms._candidate_skills(text)


def test_order_case_sensitivity_and_final_regex_authority(skill_index):
    entries = skill_index([(r"alpha", 0), (r"beta", re.I),
                           (r"alpha\s+management", re.I), (r"C\+\+", 0)])
    for text in ["BETA alpha management C++", "ALPHA beta management", "C++ beta"]:
        ordered = sorted(enumerate(entries), key=lambda pair:
                         (not bool(pair[1]["regex"].flags & re.I), pair[0]))
        expected = [entry["term"] for _, entry in ordered if entry["regex"].search(text)]
        assert terms.extract_skills(text) == expected
    # Shared common words alone must not trigger a multi-component candidate.
    assert 2 not in terms._candidate_skills("stakeholder management")


def test_large_or_and_cartesian_clauses_stay_bounded_and_lossless(skill_index):
    large_or = "(?:" + "|".join(f"token{n:04d}_end" for n in range(600)) + ")"
    cartesian = "".join(f"(?:alpha{n}|beta{n}|gamma{n})" for n in range(12))
    skill_index([(large_or, 0), (cartesian, 0)])
    for pattern in [large_or, cartesian]:
        clauses = terms._literal_clauses(terms._regex_parser.parse(pattern))
        assert len(clauses) <= 256
    for text in ["token0000_end", "token0599_end",
                 "".join(f"beta{n}" for n in range(12)), "not a matching term"]:
        expected = [entry["term"] for entry in terms._SKILLS
                    if entry["regex"].search(text)]
        assert terms.extract_skills(text) == expected


def test_frozen_bundle_loads_once_from_unrelated_working_directory(tmp_path):
    assets = Path(terms.__file__).parent
    bundle = tmp_path / "bundle"
    target = bundle / "sponsorscout" / "insights"
    target.mkdir(parents=True)
    for name in ("categories.json", "domains.json", "skills_dictionary.csv"):
        shutil.copy2(assets / name, target / name)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    source_root = str(assets.parents[1])
    code = """
import json, pathlib, sys
sys._MEIPASS = sys.argv[1]
from sponsorscout.insights import terms
terms._MODULE_DIR = pathlib.Path(sys.argv[2])
assert terms._find_data_file('skills_dictionary.csv').is_relative_to(pathlib.Path(sys._MEIPASS))
terms.extract_terms('Backend Engineer','','','Python SQL Docker Kubernetes','Software')
state = (len(terms._SKILLS),len(terms._CATEGORY_REGEXES),terms.taxonomy_version())
for _ in range(4):
    terms.extract_terms('Unknown','','','','Unknown')
assert state == (len(terms._SKILLS),len(terms._CATEGORY_REGEXES),terms.taxonomy_version())
assert state[0] > 1000 and state[1] > 10
print(json.dumps({'skills':state[0],'categories':state[1],'stable':True}))
"""
    env = dict(os.environ, PYTHONPATH=source_root, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-B", "-c", code,
                             str(bundle), str(unrelated / "absent")],
                            cwd=unrelated, env=env, capture_output=True,
                            text=True, timeout=30, check=True)
    assert json.loads(result.stdout)["stable"] is True


def _seed_job(conn, *, url="https://jobs.example/job/engineering-91", **extra):
    job = dict(source_name="Fixture", source_type="Career", url=url,
               company="Fixture", title="Backend Engineer", country="Unknown",
               location="Unknown", support_status="Unknown",
               description="Python SQL Docker and Kubernetes backend engineering.")
    job.update(extra)
    return persistence.upsert_job(conn, job)


def test_taxonomy_change_and_explicit_rebuild_are_exact_and_preserve_ingestion(monkeypatch, tmp_path):
    path = tmp_path / "terms.db"
    db.initialize(path)
    conn = db.get_connection(path)
    _seed_job(conn)
    conn.close()
    monkeypatch.setattr(terms, "taxonomy_version", lambda: "version-A")
    assert queries.backfill_existing_job_terms(path) == 1
    assert queries.backfill_existing_job_terms(path) == 0
    # A new taxonomy invalidates unchanged rows once, without rereading at startup.
    monkeypatch.setattr(terms, "taxonomy_version", lambda: "version-B")
    assert queries.has_pending_insights(path)
    assert queries.backfill_existing_job_terms(path) == 1
    assert queries.backfill_existing_job_terms(path) == 0

    progress = []
    assert queries.backfill_existing_job_terms(path, force_all=True,
                                      progress_callback=lambda n, total, phase: progress.append((n, total))) == 1
    assert progress[-1] == (1, 1)
    assert queries.backfill_existing_job_terms(path) == 0
    conn = db.get_connection(path)
    assert conn.execute("SELECT insights_version FROM jobs").fetchone()[0] == "version-B"
    conn.close()


def test_metadata_only_changes_do_not_trigger_work_but_longer_jd_does(tmp_path):
    path = tmp_path / "dirty.db"
    db.initialize(path)
    conn = db.get_connection(path)
    _seed_job(conn)
    conn.close()
    assert queries.backfill_existing_job_terms(path) == 1
    conn = db.get_connection(path)
    # Country is an observed filter, not a classification hint or term source.
    conn.execute("UPDATE jobs SET country='Brazil',country_source='manual',visa_sponsorship='Yes'")
    conn.commit()
    conn.close()
    assert queries.backfill_existing_job_terms(path) == 0
    conn = db.get_connection(path)
    conn.execute("UPDATE jobs SET description=description || ' PostgreSQL Terraform cloud infrastructure.'")
    conn.commit()
    conn.close()
    assert queries.backfill_existing_job_terms(path) == 1
    assert queries.backfill_existing_job_terms(path) == 0
