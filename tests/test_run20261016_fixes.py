#!/usr/bin/env python3
"""Regression tests for the run-20261016T140013 fix wave (P72-P76, P74, P75, RUNTIME-2).

Standalone:  python tests/test_run20261016_fixes.py
The frozen 1014-check battery (verify_rebase.py) cannot be re-run -- its input
artefacts were deleted on purpose -- so each wave keeps its own small,
self-contained test file. Every case here is a value the live run actually
produced, or a guard against a regression the fix could cause.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import os
import sqlite3
import sys
import tempfile
import re
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if not (ROOT / "sponsorscout").is_dir():
    ROOT = ROOT / "codebase"  # legacy pre-consolidation workspace layout
sys.path.insert(0, str(ROOT))

PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    ok = got == want
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print("  %s %-62s got=%r%s" % ("PASS" if ok else "FAIL", label, got,
                                   "" if ok else " want=%r" % (want,)))


def load(name, rel):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


print("== P72: location resolver ==")
lc = load("lc", "sponsorscout/core/location_country.py")
cfl = lc.country_from_location
for loc, want in [
    ("Zaventem", "Belgium"), ("Charleroi", "Belgium"), ("Hasselt", "Belgium"),
    ("Roeselare", "Belgium"), ("Watermael-Boitsfort", "Belgium"),
    ("Munich Hybrid (NavVis GmbH)", "Germany"),
    ("Munich Onsite (NavVis GmbH)", "Germany"),
    ("US Remote / Hybrid (NavVis Inc.)", "United States"),
    ("BASF India Limited", "India"), ("BASF (China) Company Ltd.", "China"),
    ("BASF Australia Ltd.", "Australia"), ("BASF Italia S.p.A.", "Italy"),
    ("United State", "United States"),
    # guards: nothing that worked may stop working
    ("Hybrid", ""), ("On-site", ""), ("Remote", ""),
    ("Remote - Sydney, Australia", "Australia"),
    ("Berlin, DEU", "Germany"), ("NC, USA,", "United States"),
    ("London (Hybrid)", "United Kingdom"), ("Remote (Netherlands)", "Netherlands"),
    ("Milan, MI", "Italy"), ("München, DE", "Germany"),
]:
    check("country_from_location(%r)" % loc, cfl(loc), want)

print("== P72 regression over every Raw Location the run produced ==")
run = ROOT / "uploads" / "20261016T140013_career_jobs.csv"
if run.exists():
    rows = list(csv.DictReader(io.open(run, encoding="utf-8-sig")))
    raws = sorted({(r["Raw Location"] or "").strip() for r in rows})
    before = after = 0
    for raw in raws:
        if not raw:
            continue
        resolved = bool(cfl(raw))
        after += resolved
    print("  distinct raw locations: %d, resolved now: %d" % (len(raws) - 1, after))
    # the five Deloitte cities that were '' before the fix must resolve now
    check("Deloitte cities resolved",
          all(cfl(c) == "Belgium" for c in
              ("Zaventem", "Charleroi", "Hasselt", "Roeselare",
               "Watermael-Boitsfort")), True)
else:
    print("  (run artefact not present -- skipped)")

print("== P73: category-page title gate + evidence scoping ==")
car = load("car", "sponsorscout/scanning/career/career_scanner.py")
sc = car.CareerPortalScanner(
    skip_preflight=True,
    input_csv=str(ROOT / "sponsorscout/data/seeds/company_Career_seed.csv"))
for title, url, want in [
    ("Visa Sponsorship", "https://jaabz.com/jobs/visasponsorship", False),
    ("Programming", "https://jaabz.com/jobs/programming/relocation", False),
    ("Machine Learning", "https://jaabz.com/jobs/machine-learning/visasponsorship", False),
    ("United State", "https://jaabz.com/jobs/in/united-state/visasponsorship", False),
    # real jobs keep passing
    ("QA Tester Agentic Web Application Senior",
     "https://jaabz.com/jobs/285606-qa-tester-agentic-web-application-senior", True),
    ("Senior Backend Software Engineer (Fintech, Distributed Systems)",
     "https://jaabz.com/jobs/274705-senior-software-engineer", True),
    ("Category Manager", "https://example.com/jobs/category-manager", True),
]:
    check("is_valid_job_title(%r)" % title, sc.is_valid_job_title(title, "Jaabz", url), want)

jd = load("jd", "sponsorscout/scanning/jd_support.py")
det = jd.JDSupportDetector()
page = ("Find companies that sponsor work visas and help you relocate. "
        "Siemens takes a distinctly European approach, offering regular EU Blue "
        "Card sponsorship for engineers and scientists. This role offers visa "
        "sponsorship for the right candidate.")
check("blue card scoped (Siemens sentence dropped)",
      jd.detect_blue_card(det, page, other_companies=("Siemens",)), "Unknown")
check("blue card unscoped still fires (old behaviour proof)",
      jd.detect_blue_card(det, page), "Yes")
client = ("Our client Kraft Heinz seeks an analyst. Kraft Heinz offers visa "
          "sponsorship and relocation support for this role.")
check("named client survives scoping",
      det.detect(client, other_companies=("Siemens", "Jaabz"))["visa"]["verdict"], "Yes")

print("== P74: one verdict per evidence set, and evidence exists ==")
ats = load("ats", "sponsorscout/scanning/ats/ats_scanner.py")
blob = ("We are a remote-first company with a hybrid working model in office "
        "two days a week.")
for mod, name in ((car, "career"), (ats, "ats")):
    check("%s classify_work_mode(remote+hybrid)" % name,
          mod.classify_work_mode(blob), "Remote/Hybrid")
    check("%s classify_work_mode(pure hybrid)" % name,
          mod.classify_work_mode("hybrid working, two days onsite"), "Hybrid")
    check("%s classify_work_mode(strong remote only)" % name,
          mod.classify_work_mode("fully remote, anywhere in EMEA"), "Remote")
check("work_mode_evidence quotes the hybrid clause",
      "hybrid" in car.work_mode_evidence(blob).lower(), True)
for _b in ("hybrid twice a week", "fully remote EMEA", "onsite in Berlin",
           "remote-first with home office", "no mode words at all"):
    check("evidence parity on %r" % _b[:28],
          car.work_mode_evidence(_b), ats.work_mode_evidence(_b))

print("== P75: glued location tails ==")
for title, want in [
    ("Senior Lead, Federal Government Affairs (US)Remote - Americas",
     "Senior Lead, Federal Government Affairs"),
    ("Communications - Editorial Lead, Engineering & Technical ContentRemote - America",
     "Communications - Editorial Lead, Engineering & Technical Content"),
    ("Senior Financial Analyst -  Multiple Roles Remote - Americas",
     "Senior Financial Analyst -  Multiple Roles"),
    ("Flutter Developer (Full-time, Remote)", "Flutter Developer (Full-time, Remote)"),
    ("Node.js Software Engineer", "Node.js Software Engineer"),
]:
    check("ats strip_glued_remote_tail(%r)" % title[:44],
          ats.strip_glued_remote_tail(title), want)
    check("career clean_job_title(%r)" % title[:44],
          sc.clean_job_title(title), re.sub(r"\s+", " ", want))

print("== P76: scan_log quarantine bump ==")
with tempfile.TemporaryDirectory() as td:
    dbp = os.path.join(td, "t.db")
    con = sqlite3.connect(dbp)
    con.execute("CREATE TABLE scan_log (run_id TEXT, scanner TEXT, "
                "seed_name TEXT, quarantined INTEGER)")
    con.execute("INSERT INTO scan_log VALUES ('R1','career','Tenth Revolution Group',1)")
    con.commit(); con.close()
    db = load("dbm", "sponsorscout/db/database.py")
    db.bump_scan_log_quarantined(dbp, "R1", {"Tenth Revolution Group": 8})
    con = sqlite3.connect(dbp)
    got = con.execute("SELECT quarantined FROM scan_log WHERE run_id='R1'").fetchone()[0]
    con.close()
    check("quarantined 1 + 8", got, 9)

print("== RUNTIME-2: company pool vs browser sessions ==")
os.environ["SPONSORSCOUT_MAX_WORKERS"] = "6"
check("env MAX_WORKERS honoured", sc._company_pool(), 6)
os.environ.pop("SPONSORSCOUT_MAX_WORKERS")
check("host-sized pool in 2..4", 2 <= sc._company_pool() <= 4, True)
check("browser semaphore exists", type(sc._browser_sem).__name__, "Semaphore")

print("== schema parity: Work Mode Evidence column ==")
import re as _re  # noqa: E402
src = (ROOT / "sponsorscout/scanning/career/career_scanner.py").read_text()
check("career field list carries the column",
      '"Work Mode", "Work Mode Evidence", "Scope Confidence",' in src, True)
check("ats field list carries the column",
      '"Work Mode", "Work Mode Evidence",' in
      (ROOT / "sponsorscout/scanning/ats/ats_scanner.py").read_text(), True)

print("\n%d PASS / %d FAIL" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
