#!/usr/bin/env python3
"""Regression tests for the run-20261016T173850 fix wave (P77-P80, UI-7).

Standalone:  python tests/test_run20261016B_fixes.py
Every case here is a value the live run actually produced (Jaabz facet URLs,
NavVis rescued raws, the Nigel Frank cross-seed dup count) or a guard against
a regression the fix could cause (real job URLs and one-word titles must keep
passing the gate).
"""
from __future__ import annotations

import csv
import importlib.util
import io
import os
import sqlite3
import sys
import tempfile
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


car = load("car", "sponsorscout/scanning/career/career_scanner.py")
atsm = load("atsm", "sponsorscout/scanning/ats/ats_scanner.py")
sc = car.CareerPortalScanner(
    skip_preflight=True,
    input_csv=str(ROOT / "sponsorscout/data/seeds/company_Career_seed.csv"))
ats = atsm.ATSScanner(
    seed_file=str(ROOT / "sponsorscout/data/seeds/company_ATS_seed.csv"),
    skip_preflight=True)

print("== P77+P78: gate sees the URL on every path; facet tails drop the exemption ==")
# the run's accepted noise, verbatim from 20261016T173850 recruiter CSV
NOISE = [
    ("Visa Sponsorship", "https://jaabz.com/jobs/visasponsorship"),
    ("Blockchain", "https://jaabz.com/jobs/blockchain/visasponsorship"),
    ("Product Management", "https://jaabz.com/jobs/product-management/relocation"),
    ("Networking", "https://jaabz.com/jobs/networking/relocation"),
    ("Testing", "https://jaabz.com/jobs/testing/remote"),
    ("Graphic Design", "https://jaabz.com/jobs/graphic-design/visasponsorship"),
]
for title, url in NOISE:
    check("career gate rejects %r" % title,
          sc.is_valid_job_title(title, "Jaabz", url), False)
    check("ats gate rejects %r" % title, ats.valid_title(title, url), False)
# guards: real postings and real one-word titles keep passing
for title, url, want in [
    ("Senior Software Engineer, 3D Perception (C++ and Python)",
     "https://job-boards.eu.greenhouse.io/navvis/jobs/4904613101", True),
    ("Barista", "https://example.com/jobs/barista", True),
    ("Devops", "https://example.com/jobs/devops", True),  # documented residual
    ("Relocation Manager", "https://example.com/jobs/relocation-manager-123", True),
    ("Remote Engineer", "https://example.com/jobs/remote-engineer", True),
    ("Category Manager", "https://example.com/jobs/category-manager", True),
]:
    check("career gate keeps %r" % title[:40],
          sc.is_valid_job_title(title, "X", url), want)
    check("ats gate keeps %r" % title[:40], ats.valid_title(title, url), want)
# is_category_facet_url now knows the support-facet tail
for url, want in [
    ("https://jaabz.com/jobs/blockchain/visasponsorship", True),
    ("https://jaabz.com/jobs/visasponsorship", True),
    ("https://jaabz.com/jobs/testing/remote", True),
    ("https://job-boards.eu.greenhouse.io/navvis/jobs/4904613101", False),
    ("https://example.com/jobs/relocation-manager-123", False),
]:
    check("is_category_facet_url(%s)" % url.split("/")[-1],
          car.is_category_facet_url(url), want)

print("== P79: rescued raw locations enter the pipeline ==")
for raw, want in [
    ("Munich Hybrid (NavVis GmbH)", "Munich"),
    ("Munich Onsite (NavVis GmbH)", "Munich"),
    ("Remote", ""),
    ("", ""),
    ("Zaventem Hybrid (Deloitte BV)", "Zaventem"),
    # title glued to a place is NOT a location string
    ("Franchise Partner - Specialty Foods from Austria, Lower Austria", ""),
    # 8r: country inside the parenthetical of a legal-entity string
    ("BASF (China) Company Ltd.", "China"),
    ("BASF India Limited", "BASF India Limited"),  # verbatim contract
]:
    check("career _clean_rescued_location(%r)" % raw[:34],
          sc._clean_rescued_location(raw), want)
    check("ats _clean_rescued_location(%r)" % raw[:34],
          ats._clean_rescued_location(raw), want)
row = ats.make_row(
    {"name": "NavVis", "url": "https://www.navvis.de/careers",
     "source_type": "direct_employer", "target_country": "Global",
     "scope_policy": "global", "industry": "Unknown",
     "ats_type": "greenhouse", "provider": "greenhouse"},
    "Calibration Engineer",
    "https://job-boards.eu.greenhouse.io/navvis/jobs/4904613101",
    "Unknown", "Munich Onsite (NavVis GmbH)", "Unknown / Unknown",
    "jd text", "static_html", location_source="none")
check("ats make_row rescues location", row["Job Location"], "Munich")
check("ats make_row marks source detail", row["Location Source"], "detail")

print("== P80: bump reports (updated, requested) ==")
with tempfile.TemporaryDirectory() as td:
    dbp = os.path.join(td, "t.db")
    con = sqlite3.connect(dbp)
    con.execute("CREATE TABLE scan_log (run_id TEXT, scanner TEXT, "
                "seed_name TEXT, quarantined INTEGER)")
    con.execute("INSERT INTO scan_log VALUES ('R1','career','Nigel Frank',1)")
    con.commit(); con.close()
    db = load("dbm2", "sponsorscout/db/database.py")
    got = db.bump_scan_log_quarantined(dbp, "R1", {"Nigel Frank": 10})
    check("bump returns (updated, requested)", got, (1, 10))
    got = db.bump_scan_log_quarantined(dbp, "R1", {"Ghost Seed": 10})
    check("bump loud-miss returns (0, requested)", got, (0, 10))
    con = sqlite3.connect(dbp)
    v = con.execute(
        "SELECT quarantined FROM scan_log WHERE seed_name='Nigel Frank'"
    ).fetchone()[0]
    con.close()
    check("scan_log value 1 + 10", v, 11)

print("== P80b: scan_log CSV is the bumped source ==")
with tempfile.TemporaryDirectory() as td:
    import csv as _c
    lp = os.path.join(td, "x_scan_log.csv")
    with open(lp, "w", newline="", encoding="utf-8-sig") as fh:
        w = _c.writer(fh, lineterminator="\r\n")
        w.writerow(["Run ID", "Seed Name", "Quarantined"])
        w.writerow(["R9", "Tenth Revolution Group", 1])
        w.writerow(["R9", "Other", 0])
    sc.run_id = "R9"
    n = sc._bump_scan_log_csv_quarantined(lp, {"Tenth Revolution Group": 9})
    rows = list(_c.DictReader(open(lp, encoding="utf-8-sig")))
    check("csv bump updates the source file", (n, rows[0]["Quarantined"],
                                               rows[1]["Quarantined"]),
          (1, "10", "0"))
    raw = open(lp, "rb").read()
    check("csv bump keeps BOM+CRLF",
          (raw[:3] == b"\xef\xbb\xbf", raw.count(b"\n") == raw.count(b"\r\n")),
          (True, True))
    sc.run_id = ""

print("== 8t: page-shape detection closes the /jobs/devops residual ==")
CAT = ("<html><body><h1>Devops</h1><p>Devops jobs with visa sponsorship.</p>"
       '<a href="/jobs/devops/visasponsorship/sre-123">Senior SRE</a>'
       '<a href="/jobs/devops/visasponsorship/plat-456">Platform Eng</a>'
       '<a href="/jobs/devops/relocation/sre-789">SRE DE</a></body></html>')
JOB = ("<html><body><h1>Senior SRE</h1><p>We offer relocation support.</p>"
       '<a href="https://apply.example.com/jobs/1">Apply now</a>'
       '<a href="/jobs/other-1">R1</a><a href="/jobs/other-2">R2</a>'
       '<a href="/jobs/other-3">R3</a></body></html>')
for scn in (sc, ats):
    tag = "career" if scn is sc else "ats"
    check("%s slug_is_title category" % tag,
          scn._slug_is_title("https://jaabz.com/jobs/devops", "Devops"), True)
    check("%s slug_is_title real job" % tag,
          scn._slug_is_title("https://x.com/jobs/4877693101", "Barista"), False)
    check("%s category page = listing" % tag,
          scn._page_looks_like_listing(CAT, "https://jaabz.com/jobs/devops"), True)
    check("%s job page != listing (apply marker)" % tag,
          scn._page_looks_like_listing(JOB, "https://x.com/jobs/1"), False)

print("== UI-7: tools.py carries the retry-phase handling ==")
src = (ROOT / "sponsorscout/ui/tabs/tools.py").read_text(encoding="utf-8")
check("retry sniff present", "[dns retry]" in src and "_retry_busy" in src, True)
check("busy-bar restore on tick", "setRange(0, 1000)" in src, True)

print("== DATA-1: 108-row seed retirement artefact ==")
fix = ROOT / "seeds/company_Career_seed_108row_B7retired.csv"
if fix.exists():
    new = fix.read_bytes()
    check("retired file keeps CRLF (source had no BOM)",
          (new[:3] != b"\xef\xbb\xbf") and b"\r\n" in new, True)
    rows = list(csv.DictReader(io.StringIO(new.decode("utf-8-sig"))))
    act = [r for r in rows if r["name"] and not r["name"].startswith("#")]
    check("active rows 108 -> 107", len(act), 107)
    check("no live VMware row",
          any("vmware" in r["name"].lower() for r in act), False)
    check("B7 banner present", b"# B7 (2026-10-16)" in new, True)
    kept_n = [l for l in new.split(b"\r\n")
              if l and not l.startswith(b"#") and b"vmware" not in l.lower()]
    check("non-comment lines all intact (>=12 cols; quoted commas allowed)",
          all(l.count(b",") >= 11 for l in kept_n), True)
else:
    print("  (retired 108-row seed not built yet -- skipped)")

print()
print("%d PASS / %d FAIL" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
