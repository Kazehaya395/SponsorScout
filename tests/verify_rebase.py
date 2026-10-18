#!/usr/bin/env python3
"""Verification battery for the _org-rebased scanners.

Covers P0-45..P0-61 plus the _org behaviours that had to survive the rebase
and the app/UI integration that had to be ported onto it.
"""
import contextlib
import importlib.util
import io
import os
import re
import sys

CAREER = os.environ.get("CAREER", "/tmp/rev/rebased_career.py")
ATS = os.environ.get("ATS", "/tmp/rev/rebased_ats.py")

PASS, FAIL = [], []


def check(name, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    if not ok:
        print(f"  FAIL {name}\n       got : {got!r}\n       want: {want!r}")


def truthy(name, got):
    ok = bool(got)
    (PASS if ok else FAIL).append(name)
    if not ok:
        print(f"  FAIL {name}: expected truthy, got {got!r}")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(m)
    return m


car = load("reb_career", CAREER)
ats = load("reb_ats", ATS)
src_c = open(CAREER, encoding="utf-8").read()
src_a = open(ATS, encoding="utf-8").read()

with contextlib.redirect_stdout(io.StringIO()):
    S = car.CareerPortalScanner(input_csv="/dev/null", output_csv="/tmp/rev/out.csv")

print("\n-- A. P0-49 _sanitize_job_location order --")
check("A1 empty -> Unknown", S._sanitize_job_location("", "Acme"), "Unknown")
check("A2 'n/a' -> Unknown", S._sanitize_job_location("n/a", "Acme"), "Unknown")
check("A3 keeps Remote", S._sanitize_job_location("Remote", "Acme"), "Remote")
check("A4 place beats company echo",
      S._sanitize_job_location("Italia", "Piazza Italia"), "Italia")
check("A5 company echo -> Unknown",
      S._sanitize_job_location("Acme Corp", "Acme Corp"), "Unknown")
check("A6 real city kept", S._sanitize_job_location("Milan, Italy", "Acme"),
      "Milan, Italy")

print("\n-- B. P0-47 ISO3 / P0-48 ISO2 collisions --")
from sponsorscout.core.location_country import country_from_location as cfl
check("B1 ITA suffix", (S._sanitize_job_location("Spilamberto, MO, ITA", "Acme") != "Unknown"), True)
truthy("B2 iso3 helper exists", hasattr(S, "_iso3_country_name"))
# the library alone reads "HR" as Croatia; the SCANNER must not (P0-48)
check("B3 library would say Croatia", (cfl("Gurugram, HR") or ""), "Croatia")
check("B4 scanner drops the ambiguous code", S.extract_location("Gurugram, HR"), "Gurugram")
check("B5 US state code still outranks a city",
      (cfl("Florence, KY, US") or ""), "United States")
check("B6 ISO3 expanded", S.extract_location("Spilamberto, MO, ITA"),
      "Spilamberto, MO, Italy")

print("\n-- C. P0-61 job-title gate --")
for bad in ("Trainee", "Campus Recruiting", "ABN AMRO", "Antwerpen",
            "Updated: 9/3/2026", "Werkstudent", "Praktikum"):
    check(f"C-reject {bad!r}", S.is_valid_job_title(bad, "ABN AMRO"), False)
for good in ("Area Manager, FC Operations", "Trainee Product Manager",
             "Berlin Operations Lead", "Senior Data Engineer",
             "Werkstudent Marketing (m/w/d)"):
    check(f"C-keep {good!r}", S.is_valid_job_title(good, "ABN AMRO"), True)

print("\n-- D. P0-60 multi-location chip --")
truthy("D1 _MULTI_LOCATION_RE exists", hasattr(car, "_MULTI_LOCATION_RE"))
check("D2 label", car._MULTI_LOCATION_LABEL, "Multiple Locations")
truthy("D3 '2 Locations' matches", car._MULTI_LOCATION_RE.search("2 Locations"))
truthy("D4 '+3 more locations' matches", car._MULTI_LOCATION_RE.search("+3 more locations"))
check("D5 'Location' alone does not match",
      bool(car._MULTI_LOCATION_RE.search("Location")), False)

print("\n-- E. P0-50/52 support false-positive guards --")
truthy("E1 guard fn on career", hasattr(car, "apply_support_fp_guards"))
truthy("E2 guard fn on ats", hasattr(ats, "apply_support_fp_guards"))
truthy("E3 counters", hasattr(car, "SUPPORT_GUARD_HITS") or "SUPPORT_GUARD_HITS" in src_c)
for k in ("1_no_visa_keyword", "2_event_sponsorship", "3_travel_documents",
          "4_job_is_the_function", "5_duty_not_benefit"):
    check(f"E-counter {k}", k in src_c and k in src_a, True)

print("\n-- F. P0-57 Blue Card, multilingual --")
from sponsorscout.scanning import jd_support as jds
det = jds.JDSupportDetector()
for txt, lang in (("We support the EU Blue Card application.", "en"),
                  ("Wir unterstützen Sie bei der Blauen Karte EU.", "de"),
                  ("Supportiamo la richiesta della carta blu UE.", "it"),
                  ("Wij ondersteunen de aanvraag van de blauwe kaart.", "nl")):
    check(f"F-{lang}", jds.detect_blue_card(det, txt), "Yes")
check("F-negative", jds.detect_blue_card(det, "No blue card sponsorship is offered."), "No")

print("\n-- G. P0-51 detail budget knobs --")
# P65/A1: raised so the biggest board (GDIT, 1228 rows) gets a JD each.
check("G1 per-company default", car.ProductionScannerConfig.MAX_DETAIL_SCAN_PER_COMPANY, 1500)
check("G2 total default", car.ProductionScannerConfig.MAX_DETAIL_SCAN_TOTAL, 20000)
check("G3 time budget", car.ProductionScannerConfig.DETAIL_SCAN_TIME_BUDGET_SEC, 900)

print("\n-- H. P0-60 detail priority --")
weak = {"Job Location": "Multiple Locations"}
strong = {"Job Location": "Milan, Italy", "Visa Sponsorship": "Yes",
          "Support Evidence": "x", "Experience Required": "3"}
check("H1 multi-chip sorts first",
      car.CareerPortalScanner._detail_priority(weak) <
      car.CareerPortalScanner._detail_priority(strong), True)
check("H2 company_hq counts as weak",
      car.CareerPortalScanner._detail_priority(
          {"Job Location": "Pune, India", "Location Source": "company_hq"})[0], 0)

print("\n-- I. _org behaviours that had to survive --")
truthy("I1 install_page_resource_blocking defined", hasattr(car, "install_page_resource_blocking"))
check("I2 installed on crawl + detail contexts",
      src_c.count("install_page_resource_blocking(ctx)"), 2)
check("I3 installed on shared pages", src_c.count("install_page_resource_blocking(pg)"), 2)
truthy("I4 LOW_RESOURCE_BROWSER_ARGS kept", hasattr(car, "LOW_RESOURCE_BROWSER_ARGS"))
check("I5 BROWSER_ARGS aliases it", car.BROWSER_ARGS is car.LOW_RESOURCE_BROWSER_ARGS, True)
check("I6 renderer cap flag present",
      any("renderer-process-limit" in a for a in car.BROWSER_ARGS), True)
check("I7 viewport back to 1280x800", '"width": 1280, "height": 800' in src_c, True)
truthy("I8 local recommended_workers", hasattr(car, "recommended_workers"))
truthy("I9 _try_ats_fallback still defined", hasattr(car.CareerPortalScanner, "_try_ats_fallback"))

print("\n-- J. R3 worker override --")
os.environ.pop("SPONSORSCOUT_MAX_WORKERS", None)
base_n = car.recommended_workers("browser")
os.environ["SPONSORSCOUT_MAX_WORKERS"] = "6"
check("J1 env raises browser pool", car.recommended_workers("browser"), 6)
os.environ["SPONSORSCOUT_MAX_WORKERS"] = "99"
check("J2 hard cap 8", car.recommended_workers("browser"), 8)
os.environ["SPONSORSCOUT_MAX_WORKERS"] = "nonsense"
check("J3 garbage ignored", car.recommended_workers("browser"), base_n)
os.environ.pop("SPONSORSCOUT_MAX_WORKERS")
os.environ["SPONSORSCOUT_HTTP_WORKERS"] = "10"
check("J4 http knob", car.recommended_workers("http"), 10)
os.environ.pop("SPONSORSCOUT_HTTP_WORKERS")
check("J5 ats honours the same knob", ats.recommended_workers("browser") >= 1, True)
with contextlib.redirect_stdout(io.StringIO()):
    S2 = car.CareerPortalScanner(input_csv="/dev/null", output_csv="/tmp/rev/o2.csv",
                                 max_workers=5)
check("J6 explicit max_workers honoured", S2.max_workers, 5)

print("\n-- K. app/UI integration ported onto _org --")
# 2026-10-06: the installed scanner imports the control API in the
# parenthesised multi-line form (it also pulls in ScanCancelled /
# check_cancelled / sleep_interruptible). Assert the IMPORT, not its layout.
_k1 = re.search(
    r"from sponsorscout\.scanning\.common import\s*\(?[^)\n]*(?:\n[^)]*)?\)?",
    src_c)
check("K1 check_control imported from sponsorscout.scanning.common",
      bool(_k1) and "check_control" in _k1.group(0), True)
check("K2 stop/pause gates", src_c.count("check_control(self.cancel_event, self.pause_event)") >= 6, True)
truthy("K3 playwright reason fn", hasattr(car, "_playwright_unavailable_reason"))
check("K4 ats playwright reason", hasattr(ats, "_playwright_unavailable_reason"), True)
check("K5 ats _notify", hasattr(ats, "_notify"), True)
check("K6 ats _log_file_path", hasattr(ats, "_log_file_path"), True)
check("K7 host budget printed", "describe_host_budget()" in src_c.split("def describe_host_budget")[-1], True)
truthy("K8 detail header capture", hasattr(S, "_location_from_detail_header"))
truthy("K9 _location_is_unverified", hasattr(S, "_location_is_unverified"))
check("K10 detail JS captures hdr", "hdr:''" in src_c or "hdr: ''" in src_c, True)

print("\n-- L. mandatory imports (user decision) --")
check("L1 no standalone jd_support fallback",
      "FIX P0-16: SELF-CONTAINED VISA" in src_c, False)
check("L2 no standalone country resolver",
      "FIX P0-16b" in src_c, False)
check("L3 jd_support imported at module level",
      "from sponsorscout.scanning.jd_support import (" in src_c, True)
check("L4 VERDICT_UNKNOWN imported", hasattr(car, "VERDICT_UNKNOWN"), True)

print("\n-- M. two scanners, same logic --")
for fn in ("_sanitize_job_location", "_town_from_address", "_country_from_site",
           "_seed_url_country"):
    check(f"M-{fn} on both",
          hasattr(car.CareerPortalScanner, fn) and hasattr(ats.ATSScanner, fn), True)
check("M-apply_support_fp_guards module-level on both",
      hasattr(car, "apply_support_fp_guards") and hasattr(ats, "apply_support_fp_guards"), True)
with contextlib.redirect_stdout(io.StringIO()):
    a_inst = ats.ATSScanner(seed_file="/dev/null", output_file="/tmp/rev/ats_out.csv")
for loc, comp in (("", "Acme"), ("n/a", "Acme"), ("Milan, Italy", "Acme"),
                  ("Remote", "Acme"), ("Firenze", "Unicoop Firenze")):
    try:
        same = S._sanitize_job_location(loc, comp) == a_inst._sanitize_job_location(loc, comp)
    except Exception as exc:
        same = f"raised {exc}"
    check(f"M-parity {loc!r}", same, True)

# GAP-1 (pre-existing, identical in the files delivered before this rebase):
# the ATS scanner has no 1,809-entry gazetteer, so its place test is the
# shared library plus a lenient "<=3 words looks like a place" tail. Pinned
# here so the divergence cannot drift further without the battery noticing.
check("M-GAP1 career drops a bare company name",
      S._sanitize_job_location("Acme Corp", "Acme Corp"), "Unknown")
check("M-GAP1 ats keeps it (documented gap)",
      a_inst._sanitize_job_location("Acme Corp", "Acme Corp"), "Acme Corp")
check("M-GAP1 career keeps gazetteer-only place",
      S._sanitize_job_location("Italia", "Piazza Italia"), "Italia")
# GAP-1 CLOSED by W3-2: both scanners now share the supplementary
# gazetteer, so the ATS side keeps "Italia" exactly like the career side.
check("M-GAP1 ats keeps it too (gap closed in W3-2)",
      a_inst._sanitize_job_location("Italia", "Piazza Italia"), "Italia")


print("\n-- N. classify_support actually runs (regression guard) --")
# This is the check that was missing: the rebase left ats_scanner without a
# jd_support import, so classify_support raised NameError: detect_blue_card on
# the first row of every board. Nothing in groups A-M called it.
_YES = "We offer visa sponsorship and will cover relocation costs for this role."
try:
    a_out = a_inst.classify_support(_YES, "Senior Engineer")
    check("N1 ats classify_support returns a 7-tuple", len(a_out), 7)
    check("N2 ats sees sponsorship", str(a_out[1]).lower() in ("y", "yes"), True)
except Exception as exc:
    check(f"N1 ats classify_support raised {type(exc).__name__}: {exc}", False, True)
try:
    c_out = S.classify_support(_YES, "Senior Engineer") if hasattr(S, "classify_support") else None
    truthy("N3 career classifier reachable",
           c_out is not None or hasattr(car, "apply_support_fp_guards"))
except Exception as exc:
    check(f"N3 career classify_support raised {type(exc).__name__}", False, True)
check("N4 ats imports jd_support (no inline copy)",
      "from sponsorscout.scanning.jd_support import" in src_a, True)
check("N5 no inline detector class left in ats",
      "class JDSupportDetector" in src_a, False)

print("\n-- O. BambooHR adapter --")
check("O1 ats adapter defined", hasattr(ats.ATSScanner, "scan_bamboohr"), True)
check("O2 ats dispatch registered", '"bamboohr": self.scan_bamboohr' in src_a, True)
check("O3 url fingerprint", ats.ATSScanner._sniff_ats_type("https://astroscale.bamboohr.com/careers"), "bamboohr")
check("O4 career mirror defined", hasattr(car.CareerPortalScanner, "_fetch_bamboohr_jobs"), True)
check("O5 career dispatch registered", 'provider == "bamboohr"' in src_c, True)

_FIXTURE = {"meta": {"totalCount": 3}, "result": [
    {"id": "33", "jobOpeningName": "Senior GNC Engineer",
     "departmentLabel": "Engineering", "employmentStatusLabel": "Full-Time Employee",
     "location": {"city": "Tokyo", "state": "Sumida-ku"}, "atsLocation": {}},
    {"id": "77", "jobOpeningName": "Spacecraft Operator",
     "location": {"city": "France", "state": "Toulouse"}, "atsLocation": {}},
    {"id": "", "jobOpeningName": "no id -> skipped", "location": {}, "atsLocation": {}},
]}
_orig_get_json = ats.ATSScanner._get_json
ats.ATSScanner._get_json = lambda self, url: _FIXTURE if url.endswith("/list") else {}
try:
    _t = {"name": "Astroscale", "ats_type": "bamboohr",
          "url": "https://astroscale.bamboohr.com/careers", "board_slug": "",
          "target_country": "", "scope_policy": "global",
          "source_type": "direct_employer", "industry": "",
          "sponsorship_history": "", "english_friendly": "", "remote_score": "", "notes": ""}
    os.environ["SPONSORSCOUT_BAMBOOHR_DETAILS"] = "0"
    _rows = a_inst.scan_bamboohr(_t)
finally:
    ats.ATSScanner._get_json = _orig_get_json
    os.environ.pop("SPONSORSCOUT_BAMBOOHR_DETAILS", None)
check("O6 rows parsed, id-less row skipped", len(_rows), 2)
check("O7 job url built from id", _rows[0].get("Job URL"),
      "https://astroscale.bamboohr.com/careers/33")
check("O8 city/state kept in order", _rows[0].get("Job Location"), "Tokyo, Sumida-ku")
check("O9 swapped city/state repaired", _rows[1].get("Job Location"), "Toulouse, France")
check("O10 extraction method tagged", _rows[0].get("Extraction Method"), "bamboohr_api")

print("\n-- P. seed_manager --")
import importlib.util as _ilu
_sp = _ilu.spec_from_file_location("sm_mod", "/home/user/fixed/seed_manager.py")
sm = _ilu.module_from_spec(_sp); sys.modules["sm_mod"] = sm; _sp.loader.exec_module(sm)
from pathlib import Path as _P
_grouped = _P("/tmp/app/sponsorscout/data/seeds/company_Career_seed.csv")
check("P1 comment row recognised",
      sm.is_comment_row({"name": "# ----- NETHERLANDS (16) -----"}), True)
check("P2 comment row raises no validation errors",
      sm.validate_row({"name": "# ----- NETHERLANDS (16) -----"}), [])
if _grouped.exists():
    _ok, _errs = sm.validate_file(_grouped)
    check("P3 grouped seed validates clean", (_ok, len(_errs)), (True, 0))
    _d = sm.read_seed_rows(_grouped)
    _out = _P("/tmp/rev/p_roundtrip.csv")
    sm.write_seed_rows(_out, _d["columns"], _d["rows"])
    check("P4 save does not mutate the seed", _out.read_bytes() == _grouped.read_bytes(), True)
    check("P5 comment lines written verbatim",
          b"# ----- NETHERLANDS (16) -----\r\n" in _out.read_bytes(), True)
for _prov in ("bamboohr", "oracle", "teamtailor", "pam", "digitalrecruiters"):
    check(f"P6 allowlist has {_prov}", _prov in sm.SUPPORTED_ATS_TYPES, True)
import csv as _csv
_seed_providers = set()
for _f in ("/home/user/fixed/company_Career_seed.csv", "/home/user/fixed/company_ATS_seed.csv"):
    for _r in _csv.DictReader(open(_f, encoding="utf-8-sig")):
        _p = (_r.get("provider") or "").strip()
        if _p:
            _seed_providers.add(_p)
check("P7 every provider used in the seeds is allowed",
      sorted(_seed_providers - set(sm.SUPPORTED_ATS_TYPES)), [])


print("\n-- Q. no undefined names anywhere (runtime-crash guard) --")
# The 20261003T224923 run died on "NameError: check_control" -- a name the
# rebase used but never imported. py_compile cannot see that, and groups A-P
# only exercise the functions they call. pyflakes checks every line.
try:
    import subprocess
    _pf = subprocess.run([sys.executable, "-m", "pyflakes", CAREER, ATS],
                         capture_output=True, text=True)
    _und = [l for l in (_pf.stdout + _pf.stderr).splitlines() if "undefined name" in l]
    check("Q1 pyflakes finds no undefined names", _und, [])
except Exception as exc:
    print(f"  SKIP Q1 (pyflakes unavailable: {exc})")
for _name in ("check_control", "detect_blue_card", "PLAYWRIGHT_IMPORT_ERROR",
              "progress_cb", "urllib"):
    check(f"Q2 ats resolves {_name}", hasattr(ats, _name), True)
check("Q3 career resolves check_control", hasattr(car, "check_control"), True)


print("\n-- R. Wave 1/2: runtime + coverage (scan 20261003T233023) --")
_cfg = car.ProductionScannerConfig
check("R1 list budget default (raised 180->300, Wave 8i)",
      _cfg.LIST_TIME_BUDGET_SEC, 300)
check("R2 dead-end abort default", _cfg.DEAD_END_ABORT_SEC, 45)
check("R3 per-company detail floor", _cfg.DETAIL_MIN_PER_COMPANY, 40)
check("R4 repeat-page ratio", (_cfg.REPEAT_PAGE_RATIO, _cfg.REPEAT_PAGES_STOP),
      (0.10, 2))

# W1-1 provider sniffing
check("R5 sniff greenhouse", car.sniff_provider("", "https://boards.greenhouse.io/acme")[:2],
      ("greenhouse", "acme"))
check("R6 sniff ashby", car.sniff_provider("", "https://jobs.ashbyhq.com/openai")[:2],
      ("ashby", "openai"))
check("R7 sniff workday",
      car.sniff_provider("", "https://adobe.wd5.myworkdayjobs.com/en-US/external_experienced")[:2],
      ("workday", "adobe.wd5.myworkdayjobs.com|adobe|external_experienced"))
check("R8 sniff smartrecruiters",
      car.sniff_provider("", "https://careers.smartrecruiters.com/DeliveryHero")[:2],
      ("smartrecruiters", "DeliveryHero"))
check("R9 sniff names unsupported boards",
      car.sniff_provider("", "https://career012.successfactors.eu/career")[2],
      "successfactors")
check("R10 sniff ignores vendor noise",
      car.sniff_provider("", "https://www.celonis.com/careers/jobs/")[0], "")
for _m in ("_resolve_auto_providers", "_sniff_one_target", "_provider_cache_path"):
    truthy(f"R11 career has {_m}", hasattr(S, _m))

# W1-1b/c new adapters, both scanners
for _m in ("_fetch_workday_jobs", "_fetch_smartrecruiters_jobs"):
    truthy(f"R12 career adapter {_m}", hasattr(S, _m))
truthy("R13 career dispatches workday", 'provider == "workday"' in src_c)
truthy("R14 career dispatches smartrecruiters",
       'provider == "smartrecruiters"' in src_c)
truthy("R15 ats workday job url includes site",
       'f"https://{host}/{site}{ext}"' in src_a)
truthy("R16 ats workday fetches JD", "jobPostingInfo" in src_a)
truthy("R17 ats smartrecruiters fetches posting detail",
       "/postings/{job.get('id')}" in src_a or '/postings/{job.get(\'id\')}' in src_a)

# W1-2/3/4/5 crawl-loop guards
truthy("R18 dead-end abort wired", "_DeadEndBoard" in src_c
       and "DEAD_END_ABORT_SEC" in src_c)
truthy("R19 list budget enforced", "LIST budget reached" in src_c)
truthy("R20 repeat-page stop", "pages of repeats" in src_c)
truthy("R21 api/browser pool split", "api_pool" in src_c and "dom_pool" in src_c)

# W2-6 detail pipeline
truthy("R22 http-first detail pipeline", hasattr(S, "_run_detail_pipeline"))
import inspect as _insp
_sig = _insp.signature(S._enrich_support_from_detail).parameters
truthy("R23 detail enricher takes backfill/company_name",
       "backfill" in _sig and "company_name" in _sig)
truthy("R24 global detail cap is announced", "GLOBAL cap reached" in src_c)

# W2-6b free JD mining on API rows
_rec = {"Job Title": "Backend Engineer", "Job URL": "https://x/y"}
truthy("R25 support from API jd_text",
       S._apply_support_from_text(
           _rec, "We sponsor work visas for international candidates and cover "
                 "relocation costs."))
check("R26 ... writes a Yes verdict", _rec.get("Visa Sponsorship"), "Yes")
truthy("R27 ... records evidence", bool(_rec.get("Support Evidence")))
_rec2 = {"Job Title": "Entwickler"}
S._apply_support_from_text(
    _rec2, "Wir unterstuetzen Sie bei der Beantragung der Blauen Karte EU.")
check("R28 ... Blue Card from a German JD", _rec2.get("EU Blue Card"), "Yes")
truthy("R29 process_job mines provider jd_text",
       "_apply_support_from_text(rec, _jd" in src_c)

# W2-7 deferred scope
truthy("R30 deferred scope resolver", hasattr(S, "_resolve_deferred_scope"))
truthy("R31 unproven rows are deferred, not quarantined",
       "_scope_pending" in src_c and "scope re-check" in src_c)

# W2-8 location confidence, identical ladder in both scanners
for _src, _loc, _want in (("site_host", "Germany", "low"),
                          ("detail", "Berlin, Germany", "high"),
                          ("card+site", "Milan, Italy", "low"),
                          ("card", "Milan, Italy", "high"),
                          ("url", "Berlin", "medium"),
                          ("none", "Unknown", "none")):
    check(f"R32 career confidence {_src}",
          S._location_confidence(_loc, _src), _want)
    check(f"R33 ats confidence {_src}",
          ats._location_confidence(_loc, _src), _want)
truthy("R34 career: site-derived cannot prove job_location",
       "_location_is_site_derived(loc_source)" in src_c)
truthy("R35 ats: site-derived cannot prove job_location",
       "_location_is_site_derived(location_source)" in src_a)

# schema parity incl. the new column
_c_cols = src_c.split('"Location Source", "Location Confidence",')
_a_cols = src_a.split('"Location Source", "Location Confidence",')
check("R36 career has Location Confidence column", len(_c_cols) > 1, True)
check("R37 ats has Location Confidence column", len(_a_cols) > 1, True)

# W2-9 employer from JSON-LD
truthy("R38 jd fetch returns hiring org", "hiringOrganization" in src_c)
truthy("R39 employer applied to recruiter rows",
       "_apply_ld_side_effects" in src_c)
_rr = {"Hiring Company": "Unknown", "Job Location": "Unknown",
       "Location Source": "none", "Company Name": "Aggregator"}
S._apply_ld_side_effects(_rr, "Berlin, Germany", "Real Employer GmbH")
check("R40 ... fills Hiring Company", _rr["Hiring Company"], "Real Employer GmbH")
check("R41 ... fills the location", _rr["Job Location"], "Berlin, Germany")
check("R42 ... marks it high confidence", _rr["Location Confidence"], "high")

print("\n-- S. Wave 3: titles, gazetteer, dedupe, real URLs --")

with contextlib.redirect_stdout(io.StringIO()):
    A = ats.ATSScanner(seed_file="/dev/null", output_file="/tmp/rev/ats_out.csv",
                       skip_preflight=True)

# ── W3-1 title hygiene ───────────────────────────────────────────────────
check("S1 gender tag keeps word boundary",
      S.clean_job_title("Werkstudent (m/w/d) Marketing"), "Werkstudent Marketing")
check("S2 trailing gender tag still removed",
      S.clean_job_title("Data Analyst (m/f/d)"), "Data Analyst")
check("S3 aggregator suffix stripped",
      S.clean_job_title("Designer (m/w/d) Jobs in Innsbruck"), "Designer")
check("S4 place recovered from the title",
      S._split_title_place("Designer Jobs in Innsbruck"), ("Designer", "Innsbruck"))
check("S5 ats splits the same way",
      A._split_title_place("Designer Jobs in Innsbruck"), ("Designer", "Innsbruck"))
check("S6 'Jobs in X' alone is not a title",
      S._split_title_place("Jobs in Wien"), ("Jobs in Wien", ""))
for _t in ("Job Detail", "Careers FAQs", "Jobsuche", "Stellenangebote",
           "Offerte di lavoro", "Vacatures", "Alle Jobs anzeigen",
           "Create Job Alert", "Posizioni aperte"):
    check(f"S7 career rejects {_t!r}", S.is_valid_job_title(_t, "Acme"), False)
    check(f"S8 ats rejects {_t!r}", A.valid_title(_t), False)
for _t in ("Koch", "Arzt", "Chef", "CEO"):
    check(f"S9 career keeps short role {_t!r}", S.is_valid_job_title(_t, "Acme"), True)
for _t in ("Software Engineer", "Pflegefachkraft", "Cuoco",
           "Conseiller commercial", "Verkäufer"):
    check(f"S10 career keeps real role {_t!r}", S.is_valid_job_title(_t, "Acme"), True)
    check(f"S11 ats keeps real role {_t!r}", A.valid_title(_t), True)
truthy("S12 title place wired into process_job", "_title_place" in src_c)

# ── W3-2 gazetteer ───────────────────────────────────────────────────────
check("S13 career/ats city table identical",
      S._W3_CITY_COUNTRY == A._W3_CITY_COUNTRY, True)
check("S14 career/ats alias table identical",
      S._W3_COUNTRY_ALIASES == A._W3_COUNTRY_ALIASES, True)
truthy("S15 gazetteer is substantial", len(S._W3_CITY_COUNTRY) >= 400)
for _place, _want in (("Cuneo", "Italy"), ("Udine", "Italy"),
                      ("Pordenone", "Italy"), ("Ludwigsburg", "Germany"),
                      ("Garching", "Germany"), ("Wels", "Austria"),
                      ("Leoben", "Austria"), ("Limassol", "Cyprus"),
                      ("Sede di Cuneo", "Italy"), ("64100 Teramo", "Italy"),
                      ("Castelvetro di Modena, Italia", "Italy"),
                      ("Nederland", "Netherlands"), ("Österreich", "Austria")):
    check(f"S16 career resolves {_place!r}", S._resolve_country(_place), _want)
    check(f"S17 ats resolves {_place!r}", A._resolve_country(_place), _want)
check("S18 core still wins (Milano)", S._resolve_country("Milano"), "Italy")
check("S19 region marker is not a country", S._supplementary_country("Europe"), "")
check("S20 region marker is not a country (Nordics)",
      S._supplementary_country("Nordics"), "")
check("S21 'Cremona, Italia' already names a country",
      S._w3_has_country("Cremona, Italia"), True)
check("S22 'Cuneo' does not name a country", S._w3_has_country("Cuneo"), False)
check("S23 'Berlin' is a city, not a country name",
      S._w3_has_country("Berlin"), False)
check("S24 sanitizer keeps an unlisted town",
      S._sanitize_job_location("Cuneo", "Camst Group"), "Cuneo")
check("S25 sanitizer still kills a company name",
      S._sanitize_job_location("Unipol Assicurazioni S.p.A", "Unipol"), "Unknown")
check("S26 sanitizer trims a glued sentence",
      S._sanitize_job_location("Innsbruck. Access to the region", "Acme"),
      "Innsbruck")
check("S27 career scope: Cuneo satisfies Italy",
      S._scope_country_match("Italy", "Cuneo"), True)
check("S28 ats scope: Cuneo satisfies Italy",
      A._scope_country_match("Italy", "Cuneo"), True)
check("S29 career scope: Cuneo does not satisfy Germany",
      S._scope_country_match("Germany", "Cuneo"), False)
truthy("S30 career prefers the gazetteer over the host", "+gazetteer" in src_c)
truthy("S31 ats prefers the gazetteer over the host", "+gazetteer" in src_a)

# ── W3-3 cross-bucket dedupe ─────────────────────────────────────────────
truthy("S32 career has the dedupe pass", "_dedupe_cross_bucket" in src_c)
truthy("S33 ats has the dedupe pass", "_dedupe_cross_bucket" in src_a)
check("S34 career wires it into the run",
      src_c.count("_dedupe_cross_bucket") >= 2, True)
check("S35 ats wires it into the run",
      src_a.count("_dedupe_cross_bucket") >= 2, True)

import csv as _csv
_cols = ["Company Name", "Job Title", "Job URL", "Record Status",
         "Quarantine Reason"]


def _wr(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = _csv.DictWriter(f, fieldnames=_cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _row(c, t, u):
    return {"Company Name": c, "Job Title": t, "Job URL": u,
            "Record Status": "accepted", "Quarantine Reason": ""}


_d = [_row("Bolt", "Treasury Manager", "https://bolt.eu/jobs/1"),
      _row("Bolt", "Backend", "https://bolt.eu/jobs/2")]
_d += [_row("Shared", f"T{i}", "https://x.com/apply-now/5") for i in range(4)]
_r = [_row("Work in Estonia", "Treasury Manager", "https://bolt.eu/jobs/1"),
      _row("Work in Estonia", "Other", "https://other.ee/jobs/9"),
      _row("Agg", "X", "https://bolt.eu/careers#job=abc"),
      _row("AggShared", "T1", "https://x.com/apply-now/5")]
_wr("/tmp/rev/dd.csv", _d)
_wr("/tmp/rev/dd_recruiter.csv", _r)
_wr("/tmp/rev/dd_quar.csv", [])
_moved, _ = S._dedupe_cross_bucket("/tmp/rev/dd.csv", "/tmp/rev/dd_recruiter.csv",
                                   "/tmp/rev/dd_quar.csv", _cols)
check("S36 aggregator twin moved", _moved, 1)
_left = list(_csv.DictReader(open("/tmp/rev/dd_recruiter.csv", encoding="utf-8-sig")))
check("S37 unique recruiter rows kept", len(_left), 3)
check("S38 synthetic fragment never collapsed",
      any("#job=" in r["Job URL"] for r in _left), True)
check("S39 shared apply page never collapsed",
      any(r["Job URL"].endswith("/apply-now/5") for r in _left), True)
_qq = list(_csv.DictReader(open("/tmp/rev/dd_quar.csv", encoding="utf-8-sig")))
check("S40 duplicate reason recorded",
      [r["Quarantine Reason"] for r in _qq], ["duplicate_url_cross_source"])
check("S41 employer copy untouched",
      len(list(_csv.DictReader(open("/tmp/rev/dd.csv", encoding="utf-8-sig")))), 6)
check("S42 pass is idempotent",
      S._dedupe_cross_bucket("/tmp/rev/dd.csv", "/tmp/rev/dd_recruiter.csv",
                             "/tmp/rev/dd_quar.csv", _cols)[0], 0)
check("S43 ats shares the implementation",
      A._dedupe_cross_bucket("/tmp/rev/dd.csv", "/tmp/rev/dd_recruiter.csv",
                             "/tmp/rev/dd_quar.csv", _cols)[0], 0)

# ── W3-4 real URLs ───────────────────────────────────────────────────────
truthy("S44 digitalrecruiters deep links",
       '/annonce/' in src_c and '_dr_lang' in src_c)
truthy("S45 digitalrecruiters detail knob",
       "SPONSORSCOUT_DIGITALRECRUITERS_DETAILS" in src_c)
truthy("S46 pam keeps its JD text", '"jd_text": _jd_plain(_pam_desc)' in src_c)
check("S47 sniffer knows digitalrecruiters",
      car.sniff_provider(
          '<script>fetch("https://api.digitalrecruiters.com/careers/v1/'
          'careers-sites/lavoraconnoi")</script>',
          "https://lavoraconnoi.decathlon-careers.it/it/annonces ")[0],
      "digitalrecruiters")
check("S48 sniffer is not fooled by prose",
      car.sniff_provider("<p>annonces de presse</p>", "https://example.com/x")[0], "")
for _u in ("https://lavoraconnoi.decathlon-careers.it/it/annonce/4629847-sales-assistant",
           "https://example.it/it/offerta/12345-commesso",
           "https://example.nl/nl/vacature/9876-ontwikkelaar"):
    check(f"S49 non-English detail URL accepted {_u.split('/')[-2]}",
          S.is_valid_job_url(_u), True)
check("S50 a bare board page is still rejected",
      S.is_valid_job_url("https://example.it/it/annonces"), False)

print("\n-- T. UI/DB fixes (P9): picker banners, work mode, recruiters, empties --")
import sqlite3 as _sq
import types as _ty

_UI = os.environ.get("UI_DIR", "/home/user/fixed")
_dbmod = None
if os.path.exists(os.path.join(_UI, "database.py")):
    _pkg = _ty.ModuleType("sponsorscout"); _pkg.__path__ = []
    _paths = _ty.ModuleType("sponsorscout.paths")
    _paths.DB_PATH = "/tmp/rev/ui.db"
    _paths.ensure_user_data_dir = lambda *a, **k: None
    sys.modules.setdefault("sponsorscout", _pkg)
    sys.modules["sponsorscout.paths"] = _paths
    _dbmod = load("ui_database", os.path.join(_UI, "database.py"))

if _dbmod is None:
    print("  (skipped: no UI files next to the scanners)")
else:
    _p = "/tmp/rev/ui.db"
    if os.path.exists(_p):
        os.remove(_p)
    _cn = _sq.connect(_p)
    _cn.executescript("""
      CREATE TABLE jobs (id INTEGER PRIMARY KEY, company TEXT, country TEXT,
        location TEXT, title TEXT, url TEXT UNIQUE, source_type TEXT,
        source_subtype TEXT, sponsorship_score INT, match_score INT,
        verified_active INT, is_expired INT, remote_type TEXT,
        eu_blue_card INT, run_id TEXT, first_seen_at TEXT);
      CREATE TABLE applications (id INTEGER PRIMARY KEY, job_url TEXT);
      CREATE TABLE scan_log (id INTEGER PRIMARY KEY, run_id TEXT, scanner TEXT,
        seed_name TEXT, company TEXT, source_type TEXT, target_country TEXT,
        status TEXT, provider TEXT, jobs_found INT, quarantined INT,
        duplicates INT, rejected_scope INT, error TEXT, diagnostics TEXT,
        duration_sec REAL, seed_url TEXT);
    """)
    # work-mode vocabulary the ingest might write, all spellings
    _modes = [("remote", 3), ("Remote", 2), ("remote_eu", 1), ("remote-global", 1),
              ("hybrid", 4), ("Hybrid", 1), ("remote/hybrid", 2), ("onsite", 5),
              ("", 6), ("unknown", 2)]
    _i = 0
    for _m, _n in _modes:
        for _k in range(_n):
            _i += 1
            _cn.execute("INSERT INTO jobs (company,country,url,sponsorship_score,"
                        "match_score,verified_active,is_expired,remote_type,"
                        "eu_blue_card,run_id) VALUES (?,?,?,?,?,1,0,?,0,'R1')",
                        ("Employer A", "Germany", f"https://e.com/{_i}", 90, 50, _m))
    # an aggregator that out-volumes every employer
    for _k in range(40):
        _cn.execute("INSERT INTO jobs (company,country,url,sponsorship_score,"
                    "match_score,verified_active,is_expired,remote_type,"
                    "eu_blue_card,run_id) VALUES (?,?,?,?,?,1,0,'onsite',0,'R1')",
                    ("Arbeitnow", "Germany", f"https://agg.com/{_k}", 99, 99))
    _cn.execute("INSERT INTO jobs (company,country,url,sponsorship_score,match_score,"
                "verified_active,is_expired,remote_type,eu_blue_card,run_id) "
                "VALUES ('Employer B','Italy','https://b.com/1',95,60,1,0,'onsite',0,'R1')")
    _sl = [("R0", "Employer A", "direct_employer", "ok", "workday", 5, None, None),
           ("R1", "Employer A", "direct_employer", "ok", "workday", 27, None, None),
           ("R1", "Employer B", "direct_employer", "ok", "static", 1, None, None),
           ("R1", "Arbeitnow", "recruiter", "ok", "static", 40, None, None),
           ("R1", "BASF", "direct_employer", "empty", "", 0,
            None, "dead end: no job cards after 45s"),
           ("R1", "Cegedim", "direct_employer", "empty", "", 0,
            None, "client-rendered (__NUXT__)"),
           ("R1", "VMware", "direct_employer", "error", "", 0,
            "RuntimeError: seed page did not load", None)]
    for _r in _sl:
        _cn.execute("INSERT INTO scan_log (run_id,company,source_type,status,provider,"
                    "jobs_found,error,diagnostics,duration_sec,scanner) "
                    "VALUES (?,?,?,?,?,?,?,?,?, 'career')", _r + (10.0,))
    _cn.commit(); _cn.close()

    _st = _dbmod.get_dashboard_stats(_p)
    check("T1 remote excludes hybrid", _st["remote_jobs"], 7)
    check("T2 hybrid counted on its own", _st["hybrid_jobs"], 7)
    check("T3 'remote/hybrid' is not fully remote",
          _st["remote_jobs"] + _st["hybrid_jobs"], 14)
    check("T4 blank and 'unknown' are one bucket", _st["work_mode_unknown"], 8)
    check("T5 open vocabulary: remote_eu/remote-global counted",
          _st["remote_jobs"] >= 2, True)
    check("T6 remote + hybrid + unknown + onsite == all jobs",
          _st["remote_jobs"] + _st["hybrid_jobs"] + _st["work_mode_unknown"] + 5 + 40 + 1,
          _st["verified_jobs"])
    check("T7 last run id is the newest", _st["last_run_id"], "R1")
    check("T8 scanned companies from the last run only", _st["scanned_companies"], 6)
    check("T9 every zero-job company counted", _st["empty_companies"], 3)
    check("T10 error companies counted", _st["error_companies"], 1)

    _top = _dbmod.get_dashboard_top_companies(_p, limit=8)
    _names = [r[0] for r in _top]
    check("T11 aggregator dropped from top employers", "Arbeitnow" in _names, False)
    check("T12 real employers kept", sorted(_names), ["Employer A", "Employer B"])
    check("T13 ranking still returns rows", len(_top) > 0, True)

    _emp = _dbmod.get_dashboard_empty_companies(_p, limit=50)
    _en = [r[0] for r in _emp]
    check("T14 every no-job company listed", sorted(_en),
          ["BASF", "Cegedim", "VMware"])
    check("T15 producing companies not listed", "Employer A" in _en, False)
    _why = {r[0]: (r[3] or "") for r in _emp}
    truthy("T16 reason carried for empties", "dead end" in _why["BASF"])
    truthy("T17 error text carried", "did not load" in _why["VMware"])
    check("T18 status column present",
          sorted({r[2] for r in _emp}), ["empty", "error"])
    check("T19 limit honoured", len(_dbmod.get_dashboard_empty_companies(_p, limit=2)), 2)
    _rec = _dbmod._recruiter_companies(_dbmod.get_connection(_p))
    check("T20 recruiter set is case-folded", "arbeitnow" in _rec, True)

# ── the company picker must not offer "# BANNER" rows as companies ───────
_tools = os.path.join(_UI, "tools.py")
if not os.path.exists(_tools):
    print("  (picker checks skipped: no tools.py)")
else:
    _ts = open(_tools, encoding="utf-8").read()
    truthy("T21 picker has a banner test", "_is_banner" in _ts)
    truthy("T22 banner rows are unselectable", "Qt.NoItemFlags" in _ts)
    truthy("T23 selection filters on company items", "_is_company_item" in _ts)
    truthy("T24 empty filter restores every row",
           "_apply_filter" in _ts and "setHidden(False)" in _ts)


print("\n-- U. work mode: one classifier, Work Mode is authoritative --")
for _b, _want in (
        ("Senior Engineer - fully remote", "Remote"),
        ("Hybrid role, 2 days in Berlin", "Hybrid"),
        ("Remote and hybrid options", "Remote/Hybrid"),
        ("100% remote, hybrid optional", "Remote"),
        ("Smart working disponibile", "Hybrid"),
        ("Lavoro da remoto", "Remote"),
        ("Home office moeglich", "Remote"),
        ("On-site in Milan", "Onsite"),
        ("Software Engineer, Milan", "Unknown"),
        ("", "Unknown")):
    check(f"U1 classify {_b[:28]!r}", car.classify_work_mode(_b), _want)
check("U2 both scanners expose the classifier",
      hasattr(car, "classify_work_mode") and hasattr(ats, "classify_work_mode"), True)
for _b in ("fully remote", "hybrid", "remote and hybrid", "on-site", "nothing here"):
    check(f"U3 parity {_b!r}", car.classify_work_mode(_b), ats.classify_work_mode(_b))
check("U4 ats writes a Work Mode column", "Work Mode" in ats.OUTPUT_FIELDS, True)
truthy("U5 career still writes one", '"Work Mode", "Scope Confidence"' in src_c
       or '"Work Mode":' in src_c)

_pl = os.path.join(_UI, "pipeline.py")
if not os.path.exists(_pl):
    print("  (ingest checks skipped: no pipeline.py)")
else:
    _pls = open(_pl, encoding="utf-8").read()
    _g = {"re": __import__("re"),
          "country_from_location": __import__(
              "sponsorscout.core.location_country",
              fromlist=["x"]).country_from_location}
    exec(_pls[_pls.index("def _norm_location"):_pls.index("def _row_to_job")], _g)
    _rt = _g["_remote_type"]
    check("U6 Work Mode beats page furniture",
          _rt({"Work Mode": "Unknown", "Job Type": "Internship / Remote",
               "Job Location": "Paris"}), "unknown")
    check("U7 declared remote keeps its region",
          _rt({"Work Mode": "Remote", "Job Location": "Berlin, Germany"}), "remote_eu")
    check("U8 declared remote outside the EU",
          _rt({"Work Mode": "Remote", "Job Location": "Tokyo, Japan"}), "remote")
    check("U9 emea", _rt({"Work Mode": "Remote", "Job Location": "Dubai"}), "remote_emea")
    check("U10 hybrid stays hybrid",
          _rt({"Work Mode": "Hybrid", "Job Location": "Berlin, Germany"}), "hybrid")
    check("U11 remote/hybrid is not fully remote",
          _rt({"Work Mode": "Remote/Hybrid", "Job Location": "Berlin, Germany"}), "hybrid")
    check("U12 onsite", _rt({"Work Mode": "Onsite", "Job Location": "Milan"}), "onsite")
    check("U13 unseen token read by shape",
          _rt({"Work Mode": "Remote (EU only)", "Job Location": "Milan, Italy"}),
          "remote_eu")
    check("U14 no column at all -> legacy sniff",
          _rt({"Job Type": "Full-time / Remote", "Job Location": "Berlin, Germany"}),
          "remote_eu")
    check("U15 no column, no evidence -> unknown, never a fake onsite",
          _rt({"Job Type": "Full-time", "Job Location": "Berlin, Germany"}), "unknown")
    check("U16 blank Work Mode is unknown",
          _rt({"Work Mode": "", "Job Type": "Remote", "Job Location": "Berlin"}),
          "unknown")


print("\n-- V. seed schema: ats_type removed, grid follows the file --")
import hashlib as _hl
import pathlib as _pl

_smp = os.path.join(_UI, "seed_manager.py")
_SEEDS = os.environ.get("SEEDS", "/home/user/seeds_grouped")
if not os.path.exists(_smp):
    print("  (skipped: no seed_manager.py)")
else:
    # group T installed a bare sponsorscout.paths stub; seed_manager needs
    # SEEDS_DIR / ensure_user_data_dir from it.
    _ps = sys.modules.get("sponsorscout.paths")
    if _ps is not None and not hasattr(_ps, "SEEDS_DIR"):
        _ps.SEEDS_DIR = __import__("pathlib").Path("/tmp/rev/seeds")
        _ps.ensure_user_data_dir = lambda *a, **k: None
    _sm = load("ui_seed_manager", _smp)
    check("V1 ats_type is gone from the schema",
          "ats_type" in _sm.SEED_COLUMNS, False)
    check("V2 schema is the shipped 12 columns", _sm.SEED_COLUMNS,
          ["name", "careers_url", "provider", "board_slug", "source_type",
           "target_country", "scope_policy", "industry", "sponsorship_history",
           "english_friendly", "remote_score", "notes"])
    check("V3 optional columns still offered",
          _sm.OPTIONAL_COLUMNS, ["seed_name", "canonical_name"])
    check("V4 legacy map points at provider",
          _sm.LEGACY_COLUMNS.get("ats_type"), "provider")
    truthy("V5 auto_detect renamed", hasattr(_sm, "auto_detect_provider"))
    check("V6 old detector name withdrawn",
          hasattr(_sm, "auto_detect_ats_type"), False)
    for _f, _n, _b in (("company_ATS_seed.csv", 65, 0),
                       ("company_Career_seed.csv", 373, 45)):
        _p = _pl.Path(_SEEDS) / _f
        if not _p.exists():
            continue
        _d = _sm.read_seed_rows(_p)
        check(f"V7 {_f} columns come from the file",
              _d["columns"], _sm.SEED_COLUMNS)
        check(f"V8 {_f} rows read", len(_d["rows"]), _n)
        check(f"V9 {_f} banners preserved",
              sum(1 for r in _d["rows"] if _sm.is_comment_row(r)), _b)
        check(f"V10 {_f} no ats_type key on any row",
              any("ats_type" in r for r in _d["rows"]), False)
        _out = _pl.Path("/tmp/rev") / ("rt_" + _f)
        _sm.write_seed_rows(_out, _d["columns"], _d["rows"])
        check(f"V11 {_f} save is byte-identical",
              _hl.md5(_out.read_bytes()).hexdigest(),
              _hl.md5(_p.read_bytes()).hexdigest())
    # a legacy personal seed is migrated, and says so
    _v6 = _pl.Path("/tmp/rev/v6_seed.csv")
    _v6.write_text("name,ats_type,careers_url,industry\r\n"
                   "Acme,greenhouse,https://boards.greenhouse.io/acme,SaaS\r\n",
                   encoding="utf-8", newline="")
    _d6 = _sm.read_seed_rows(_v6)
    check("V12 legacy ats_type folded into provider",
          _d6["rows"][0].get("provider"), "greenhouse")
    check("V13 migration is reported, not silent",
          _d6["migrated"], ["ats_type -> provider"])
    check("V14 legacy column not written back",
          "ats_type" in _d6["columns"], False)
    check("V15 migrated row validates", _sm.validate_row(_d6["rows"][0]), [])
    check("V16 banner row is not a company",
          _sm.validate_row({"name": "# ----- AUSTRIA (23) -----"}), [])
    check("V17 a row with no provider is rejected",
          bool(_sm.validate_row({"name": "X", "careers_url": "https://x.io"})), True)

check("V18 career records drop the dead ats_type key",
      '"ats_type": (row.get("ats_type") or provider).strip(),' in src_c, False)
truthy("V19 career announces a legacy column", "_legacy_provider_warned" in src_c)
truthy("V20 ats announces a legacy column", "_legacy_provider_warned" in src_a)

_dmp = os.path.join(_UI, "data_management.py")
if not os.path.exists(_dmp):
    print("  (grid checks skipped: no data_management.py)")
else:
    _dm = open(_dmp, encoding="utf-8").read()
    check("V21 grid no longer hard-codes the v6 column list",
          "sm.BASE_COLUMNS + sm.EXTRA_COLUMNS" in _dm, False)
    truthy("V22 grid follows the file header",
           'self.columns = list(data.get("columns")' in _dm)
    truthy("V23 save writes the file's own columns",
           "sm.write_seed_rows(self.path, self.columns, self.rows)" in _dm)
    truthy("V24 banners render as spanning headers",
           "sm.is_comment_row(row)" in _dm and "setSpan" in _dm)
    truthy("V25 banners are not editable or deletable",
           "return None if sm.is_comment_row(self.rows[idx]) else idx" in _dm)
    truthy("V26 add dialog writes provider",
           '"provider": self.ats_combo.currentText().strip(),' in _dm)
    check("V27 dialog no longer writes ats_type",
          '"ats_type": self.ats_combo' in _dm, False)
    truthy("V28 detector call renamed", "sm.auto_detect_provider(url)" in _dm)


# ════════════════════════════════════════════════════════════════════════
# W. jd_support: apostrophe contractions (false YES)
# ════════════════════════════════════════════════════════════════════════
print("\n-- W. support detector: apostrophe contractions --")
_jdp = os.path.join(os.environ.get("UI_DIR", "/home/user/fixed"), "jd_support.py")
if not os.path.exists(_jdp):
    print("  (skipped: no jd_support.py)")
else:
    _jd = load("reb_jd", _jdp)
    _D = _jd.JDSupportDetector()

    def _v(text):
        return _D.detect(text)["visa"]["verdict"]

    check("W1 aren't able to sponsor -> No",
          _v("We aren't able to sponsor visas for this role."), "No")
    check("W2 are not able to sponsor -> No (unchanged)",
          _v("We are not able to sponsor visas for this role."), "No")
    check("W3 curly apostrophe aren\u2019t -> No",
          _v("We aren\u2019t able to sponsor visas for this role."), "No")
    check("W4 isn't eligible for sponsorship -> No",
          _v("This role isn't eligible for visa sponsorship."), "No")
    check("W5 haven't got a sponsorship programme -> No",
          _v("We haven't got a visa sponsorship programme."), "No")
    check("W6 apostrophe-less 'cant' -> No",
          _v("We cant sponsor visas."), "No")
    check("W7 can't (already worked) -> No", _v("We can't sponsor visas."), "No")
    check("W8 plain offer still Yes",
          _v("We offer visa sponsorship and relocation support."), "Yes")
    check("W9 sponsor verb still Yes",
          _v("We sponsor work visas for all engineering roles."), "Yes")
    check("W10 available still Yes",
          _v("Visa sponsorship is available for this position."), "Yes")
    check("W11 explicit denial still 0.95",
          _D.detect("We don't accept applicants who need visa sponsorship."
                    )["visa"]["confidence"], 0.95)
    truthy("W12 cue list carries the be/have contractions",
           "aren't" in _jd.JDSupportDetector.NEGATION.pattern
           and "isn't" in _jd.JDSupportDetector.NEGATION.pattern
           and "haven't" in _jd.JDSupportDetector.NEGATION.pattern)
    check("W13 apostrophe folding helper exists",
          _jd._norm_apostrophes("aren\u2019t"), "aren't")

# ════════════════════════════════════════════════════════════════════════
# X. P14/P15: label/value locations, invented locations
# ════════════════════════════════════════════════════════════════════════
print("\n-- X. P14 label/value location, P15 invented locations --")
_LOOP_HTML = (
    '<div class="specs__inner">'
    '<div class="specs__item"><span class="subheadline">Start</span>'
    '<span class="h4">Immediately</span></div>'
    '<div class="specs__item"><span class="subheadline">Location</span>'
    '<span class="h4">Salzburg - Vienna </span></div>'
    '<div class="specs__item"><span class="subheadline">Experience</span>'
    '<span class="h4">Experienced</span></div></div>')
check("X1 sibling-span label strip is read",
      car.extract_labelled_location(_LOOP_HTML)[:1], ["Salzburg - Vienna"])
check("X2 that value resolves to a real place",
      S.extract_location(car.extract_labelled_location(_LOOP_HTML)[0]), "Vienna")
check("X3 dt/dd", car.extract_labelled_location(
    "<dl><dt>Location</dt><dd>Dublin, Ireland</dd></dl>")[:1], ["Dublin, Ireland"])
check("X4 th/td German label", car.extract_labelled_location(
    "<tr><th>Standort</th><td>M\u00fcnchen</td></tr>")[:1], ["M\u00fcnchen"])
check("X5 plain 'Location: X'", car.extract_labelled_location(
    "<p>Location: Milan, Italy</p>")[:1], ["Milan, Italy"])
check("X6 meta tag", car.extract_labelled_location(
    '<meta name="job-location" content="Vienna, Austria">')[:1],
    ["Vienna, Austria"])
check("X7 data attribute", car.extract_labelled_location(
    '<div data-location="Barcelona"></div>')[:1], ["Barcelona"])
check("X8 furniture is rejected", car.extract_labelled_location(
    "<span>Location</span><span>Cookie settings</span>"), [])
check("X9 no html -> no candidates", car.extract_labelled_location(""), [])
truthy("X10 the static detail pass uses it",
       "self._label_location(html)" in src_c)
truthy("X11 the rendered-DOM pass uses it too",
       "self._label_location(rendered)" in src_c)
check("X11a multi-site strip is kept verbatim when one country",
      S._multisite_same_country("Salzburg - Vienna"), "Salzburg - Vienna")
check("X11b cross-border strip is not kept",
      S._multisite_same_country("Dublin - Paris"), "")
check("X11c single place is not a multi-site",
      S._multisite_same_country("Vienna"), "")

check("X12 'Global Automotive' is not a place",
      S.extract_location("Global Automotive"), "Not Specified")
check("X13 'Automotive Global' is not a place",
      S.extract_location("Automotive Global"), "Not Specified")
check("X14 'Remote Automotive' is not a place",
      S.extract_location("Remote Automotive"), "Not Specified")
check("X15 bare 'Global' is not a place",
      S.extract_location("Global"), "Not Specified")
check("X16 bare 'Worldwide' is not a place",
      S.extract_location("Worldwide"), "Not Specified")
check("X17 reversed country-city still works",
      S.extract_location("Netherlands Amstelveen"), "Amstelveen, Netherlands")
check("X18 city-country still works",
      S.extract_location("Barcelona Spain"), "Barcelona, Spain")
check("X19 'Remote - Berlin' still resolves",
      S.extract_location("Remote - Berlin"), "Berlin")
check("X20 plain city untouched", S.extract_location("Munich"), "Munich")
check("X21 City, ST untouched", S.extract_location("Chicago, IL"), "Chicago, IL")
truthy("X22 placeholder set exists", "global" in car._PSEUDO_PLACE_WORDS
       and "remote" in car._PSEUDO_PLACE_WORDS)

# ════════════════════════════════════════════════════════════════════════
# Y. P16/P17: perk text is not experience; shared blobs are not card context
# ════════════════════════════════════════════════════════════════════════
print("\n-- Y. P16 perk-text experience, P17 shared card context --")
_PERK = ("4-day Workweek At LOOP you can choose between a 5-day or a 4-day "
         "workweek after one year with us")
for _tag, _mod in (("career", car), ("ats", ats)):
    check(f"Y1 {_tag}: LOOP perk line is not experience",
          _mod.extract_experience(_PERK)["required"], "Unknown")
    check(f"Y2 {_tag}: 'sabbatical after 3 years with us' is not experience",
          _mod.extract_experience(
              "You can take a sabbatical after 3 years with us.")["required"],
          "Unknown")
    check(f"Y3 {_tag}: probation period is not experience",
          _mod.extract_experience(
              "Probation period of 6 months applies.")["required"], "Unknown")
    check(f"Y4 {_tag}: pension tenure is not experience",
          _mod.extract_experience(
              "Your pension kicks in after 2 years.")["required"], "Unknown")
    check(f"Y5 {_tag}: real requirement still read",
          _mod.extract_experience(
              "Requirements: 5+ years of experience in Python.")["required"],
          "5+ years")
    check(f"Y6 {_tag}: range still read",
          _mod.extract_experience("3-5 years experience required.")["required"],
          "3-5 years")
    check(f"Y7 {_tag}: 'no experience required' still read",
          _mod.extract_experience("No experience required.")["required"],
          "None required")

truthy("Y8 JS refuses a scope holding two jobs",
       "distinctJobLinkCount" in src_c
       and "if (distinctJobLinkCount(s) > 1)" in src_c)
truthy("Y9 the upward walk stops at the second job",
       "if (distinctJobLinkCount(p) > 1) break;" in src_c)
truthy("Y10 shared-context post-pass exists",
       "_strip_shared_card_context" in src_c)
truthy("Y11 it runs before the scope resolver",
       src_c.index("self._strip_shared_card_context(")
       < src_c.index("self._resolve_deferred_scope(\n                    target_row, company_jobs, quarantine, stats, diagnostics)"))

_recs = {
    "a": {"Job Title": "Account & Project Lead",
          "Job URL": "https://x.test/careers/account-project-lead",
          "Job Type": "Internship / Hybrid", "Job Location": "Automotive, Global",
          "Location Source": "card", "Location Confidence": "medium",
          "Experience Required": "1 years", "Experience Min Years": "1",
          "Experience Source": "api_description", "_ctx_key": "x" * 300},
    "b": {"Job Title": "iOS Developer",
          "Job URL": "https://x.test/careers/ios-developer",
          "Job Type": "Internship / Hybrid", "Job Location": "Automotive, Global",
          "Location Source": "card", "Location Confidence": "medium",
          "Experience Required": "1 years", "Experience Min Years": "1",
          "Experience Source": "api_description", "_ctx_key": "x" * 300},
}
_tr = {"name": "LOOP", "scope_policy": "job_location",
       "target_country": "Austria"}
_diag = []
with contextlib.redirect_stdout(io.StringIO()):
    _touched = S._strip_shared_card_context("LOOP", _tr, _recs, _diag)
check("Y12 both rows were reverted", _touched, 2)
check("Y13 invented location dropped", _recs["a"]["Job Location"], "Unknown")
check("Y14 location source reset", _recs["a"]["Location Source"], "none")
check("Y15 fabricated experience dropped",
      _recs["a"].get("Experience Required"), "Unknown")
check("Y16 borrowed job type dropped",
      _recs["a"]["Job Type"].startswith("Internship"), False)
truthy("Y17 row handed to the detail resolver", _recs["a"].get("_scope_pending"))
truthy("Y18 the pass is reported in the diagnostics",
       any("shared card context" in d for d in _diag))
check("Y19 marker key consumed", "_ctx_key" in _recs["a"], False)
with contextlib.redirect_stdout(io.StringIO()):
    check("Y20 second call is a no-op",
          S._strip_shared_card_context("LOOP", _tr, _recs, _diag), 0)

# ════════════════════════════════════════════════════════════════════════
# Z. P18 counters, P19 adapters
# ════════════════════════════════════════════════════════════════════════
print("\n-- Z. P18 scan-log counters, P19 icims/eightfold adapters --")
truthy("Z1 career derives Rejected Scope from the written quarantine list",
       '"Rejected Scope": sum(\n                            1 for _r in quarantine' in src_c)
check("Z2 career no longer logs the raw scope counter",
      '"Rejected Scope": stats["rejected_scope"], "Error": error,' in src_c, False)
truthy("Z3 the pre-prune counter survives in diagnostics",
       "scope rejects before dedupe" in src_c)
truthy("Z4 ats already derived it the same way",
       '"Rejected Scope": sum(' in src_a)

truthy("Z5 career has an icims adapter", hasattr(S, "_fetch_icims_jobs"))
truthy("Z6 career has an eightfold adapter", hasattr(S, "_fetch_eightfold_jobs"))
truthy("Z7 icims is dispatched", 'if provider == "icims":' in src_c)
truthy("Z8 eightfold is dispatched", 'if provider == "eightfold":' in src_c)
check("Z9 icims no longer sniffs as unsupported",
      car.sniff_provider("<iframe src='https://emeacareers-x.icims.com/jobs'>",
                         "https://www.x.com/careers")[0], "icims")
check("Z10 eightfold sniffs to a provider",
      car.sniff_provider("https://nvidia.eightfold.ai/careers",
                         "https://jobs.nvidia.com/")[0], "eightfold")
check("Z11 eightfold host is captured as the slug",
      car.sniff_provider("https://nvidia.eightfold.ai/careers",
                         "https://jobs.nvidia.com/")[1], "nvidia.eightfold.ai")
check("Z12 taleo is still only recognised, not claimed",
      car.sniff_provider("https://x.taleo.net/careersection", "https://x.com"),
      ("", "", "taleo"))
check("Z13 greenhouse sniffing unchanged",
      car.sniff_provider("boards.greenhouse.io/anthropic",
                         "https://anthropic.com/careers")[0], "greenhouse")
check("Z14 career host probe order",
      S._careersite_api_hosts({"careers_url": "https://www.docusign.com/company/careers",
                               "board_slug": ""})[:2],
      ["www.docusign.com", "careers.docusign.com"])
truthy("Z15 both adapters run on the socket pool, not the browser pool",
       '"icims", "eightfold"}' in src_c)
truthy("Z16 ats has the same two adapters",
       "def scan_icims" in src_a and "def scan_eightfold" in src_a)
truthy("Z17 ats dispatches them",
       '"icims": self.scan_icims,' in src_a
       and '"eightfold": self.scan_eightfold,' in src_a)
truthy("Z18 ats fingerprints them",
       "icims\\\\.com" in src_a.replace("\\\\\\\\", "\\\\") or "icims" in src_a)

# ════════════════════════════════════════════════════════════════════════
# AA. P19b: the two scanners must produce IDENTICAL rows
# ════════════════════════════════════════════════════════════════════════
print("\n-- AA. scanner parity (schema, work mode, job type, scope) --")
check("AA1 both writers emit the same 42 columns",
      list(ats.OUTPUT_FIELDS) == [c for c in car.COLUMNS] if hasattr(car, "COLUMNS")
      else len(ats.OUTPUT_FIELDS), 42 if not hasattr(car, "COLUMNS") else True)
truthy("AA2 ats gained Scope Confidence",
       "Scope Confidence" in ats.OUTPUT_FIELDS)
check("AA3 it sits where the career writer puts it",
      ats.OUTPUT_FIELDS[ats.OUTPUT_FIELDS.index("Relocation/Visa Support") + 1],
      "Scope Confidence")
truthy("AA4 ats work mode reads structured fields, not the JD body",
       'f"{title} {job_type or \'\'} {raw_location or \'\'} {location or \'\'}"'
       in src_a)
check("AA5 the JD body is no longer the work-mode evidence",
      "f\"{title} {description or ''} {location or ''}\"" in src_a, False)
truthy("AA6 ats has the scope-confidence helper",
       "def _scope_confidence" in src_a)
truthy("AA7 icims enum is humanised in both scanners",
       '"FULL_TIME": "Full-time"' in src_c and '"FULL_TIME": "Full-time"' in src_a)
truthy("AA8 api locations are labelled api in the career scanner too",
       'if loc_source == "card" and method.endswith("_api"):' in src_c)
truthy("AA9 ats writes an evidence URL only when there IS evidence",
       'rec["Support Evidence URL"] = url if evidence else ""' in src_a)
with contextlib.redirect_stdout(io.StringIO()):
    _A = ats.ATSScanner(seed_file="/dev/null", output_file="/tmp/rev/ats_out.csv",
                        skip_preflight=True)
check("AA10 job_location scope verdict",
      _A._scope_confidence({"scope_policy": "job_location",
                            "target_country": "Ireland"}, "Dublin, Ireland"),
      "verified")
check("AA11 seed_url verdict when the country matches",
      _A._scope_confidence({"scope_policy": "seed_url",
                            "target_country": "Ireland"}, "Dublin, Ireland"),
      "verified")
check("AA12 seed_url verdict when it does not",
      _A._scope_confidence({"scope_policy": "seed_url",
                            "target_country": "Ireland"}, "Berlin, Germany"),
      "unverified_seed_url")
check("AA13 global target is n/a",
      _A._scope_confidence({"scope_policy": "global",
                            "target_country": "Global"}, "Berlin, Germany"),
      "n/a")
check("AA14 classify_job_type reads the employer tag, not prose",
      _A.classify_job_type("Full-time", "Dublin, Ireland Hybrid", ""),
      "Full-time / Hybrid")

# ════════════════════════════════════════════════════════════════════════
# AB. Wave 6 — sponsorship detection defects P21..P26b (run 20261004T202946)
# ════════════════════════════════════════════════════════════════════════
print("\n-- AB. sponsorship detection (label fields, HTML, IT, bullets) --")


def _v(text, kind="visa"):
    return det.detect(text)[kind]["verdict"]


def _c(text, kind="visa"):
    return det.detect(text)[kind]["confidence"]


_MSD_YES = ("Employee Status: Regular Relocation: Yes VISA Sponsorship: Yes "
            "Travel Requirements: 10%")
_MSD_NO = ("Employee Status: Regular Relocation: No relocation "
           "VISA Sponsorship: No Travel Requirements: 50%")
_MSD_INTERN = ("Employee Status: Intern/Co-op (Fixed Term) Relocation: No relocation "
               "VISA Sponsorship: No Travel Requirements: No Travel Required")
_MSD_BLANK = "VISA Sponsorship:&lt; No relocation&lt; Relocation:&lt;"

# -- P21 employer-declared "Label: Value" fields --------------------------
check("AB1  label field Yes is read (was Unknown 0.2)", _v(_MSD_YES), "Yes")
check("AB2  label field Yes is authoritative", _c(_MSD_YES), 0.95)
check("AB3  relocation label Yes", _v(_MSD_YES, "relocation"), "Yes")
check("AB4  label field No still read", _v(_MSD_NO), "No")
check("AB5  relocation 'No relocation'", _v(_MSD_NO, "relocation"), "No")
check("AB6  intern block no longer 0.6 Unknown", _v(_MSD_INTERN), "No")
check("AB7  blank declared field never borrows the neighbour's answer",
      _v(_MSD_BLANK), "Unknown")
check("AB8  prose after a label is NOT treated as blank",
      _v("Visa: we sponsor the right candidate."), "Yes")
check("AB9  the value is not eaten by the next label name",
      jds.label_field_verdict(_MSD_YES, "visa")[0], "Yes")
check("AB10 contradictory fields are refused, not guessed",
      jds.label_field_verdict("Visa Sponsorship: Yes Relocation: x "
                              "Visa Sponsorship: No Shift: Day", "visa"), None)

# -- P22/P23 HTML entities and markup -------------------------------------
check("AB11 &rsquo; contraction no longer flips No to Yes",
      _v("<p>We can&rsquo;t offer visa sponsorship for this role.</p>"), "No")
check("AB12 double-encoded entities too",
      _v("<p>We can&amp;rsquo;t offer visa sponsorship.</p>"), "No")
check("AB13 markup is stripped before classification",
      _v("<ul><li>Pension</li><li>We provide full visa sponsorship for "
         "international hires</li></ul>"), "Yes")
truthy("AB14 evidence no longer carries tags",
       "<" not in det.detect(
           "<p>We will not sponsor applicants for work visas.</p>"
       )["visa"]["evidence"][0][3])
check("AB15 normalizer is idempotent",
      jds.normalize_jd_text(jds.normalize_jd_text("<p>a&amp;b</p>")),
      jds.normalize_jd_text("<p>a&amp;b</p>"))
check("AB16 plain text is untouched",
      jds.normalize_jd_text("We offer visa sponsorship."),
      "We offer visa sponsorship.")
check("AB17 nbsp becomes a real space",
      jds.normalize_jd_text("visa&nbsp;sponsorship"), "visa sponsorship")

# -- P24 the travel-document guard no longer eats a visa transfer ---------
_MP = ("Visa sponsorship transfer support for eligible Highly Skilled Migrants "
       "already based in the Netherlands.")
check("AB18 career guard keeps the Michael Page offer",
      car.apply_support_fp_guards("Yes", "Unknown", _MP, "Project Manager")[0],
      "Yes")
check("AB19 ats guard agrees", ats.apply_support_fp_guards(
      "Yes", "Unknown", _MP, "Project Manager")[0], "Yes")
check("AB20 real travel admin is still vetoed (career)",
      car.apply_support_fp_guards(
          "Yes", "Unknown",
          "You will arrange flights, hotels and visas for our speakers.",
          "Events Coordinator")[0], "Unknown")
check("AB21 airport transfers are still travel (career)",
      car.apply_support_fp_guards(
          "Yes", "Unknown",
          "Coordinate airport transfers and visas for visiting executives.",
          "Office Manager")[0], "Unknown")
check("AB22 real travel admin is still vetoed (ats)",
      ats.apply_support_fp_guards(
          "Yes", "Unknown",
          "You will arrange flights, hotels and visas for our speakers.",
          "Events Coordinator")[0], "Unknown")

# -- P25 Italian: trasferta is a business trip, not a move ----------------
check("AB23 'trasferte' no longer forces relocation No",
      _v("Disponibilita a trasferte presso gli stabilimenti del Gruppo "
         "situati a Castelnuovo Rangone.", "relocation"), "Unknown")
check("AB24 a real Italian relocation requirement still scores No",
      _v("E richiesta la disponibilita al trasferimento presso la sede di "
         "Milano.", "relocation"), "No")
check("AB25 'garantiamo ... alloggio' is a relocation offer",
      _v("Per gli inserimenti che richiedono il trasferimento, garantiamo "
         "3 mesi di alloggio in struttura convenzionata.", "relocation"), "Yes")
check("AB26 Italian negation still wins",
      _v("Non offriamo supporto al trasferimento.", "relocation"), "No")

# -- P26 benefit noun phrases in bullet lists -----------------------------
check("AB27 'A relocation package' is an offer", _v("A relocation package",
      "relocation"), "Yes")
check("AB28 '- Relocation assistance' too",
      _v("- Relocation assistance", "relocation"), "Yes")
check("AB29 the negated form is untouched",
      _v("This role does not come with relocation assistance.",
         "relocation"), "No")
check("AB30 the noun-phrase rule is anchored: a sentence never matches",
      det.BENEFIT_NOUN_PHRASE.match(
          "The relocation package is reviewed by the mobility team in Prague."),
      None)
check("AB30b a bare mention with no benefit noun stays Unknown",
      _v("Relocation of the Prague data centre continues into Q3.",
         "relocation"), "Unknown")

# -- P26b questions state no policy ---------------------------------------
check("AB31 'Do you need a relocation package?' is not a refusal",
      _v("Do you need a relocation package?", "relocation"), "Unknown")
check("AB32 'Is this role eligible for Immigration Sponsorship?'",
      _v("Is this role eligible for Immigration Sponsorship?"), "Unknown")
check("AB33 a statement ending in a question mark elsewhere is unaffected",
      _v("We provide full visa sponsorship."), "Yes")

# -- live-page shapes that the first cut of the fix got wrong -------------
_MSD_BR = ("Employee Status: Regular. Relocation: . No relocation. "
           "VISA Sponsorship: . No. Travel Requirements: 50%")
check("AB33a <br> between label and value (live jobs.msd.com shape)",
      _v(_MSD_BR), "No")
check("AB33b and its relocation field", _v(_MSD_BR, "relocation"), "No")
_MSD_EMPTY = "Relocation:. VISA Sponsorship:. Travel Requirements: Flexible"
check("AB33c two empty fields in a row stay Unknown", _v(_MSD_EMPTY), "Unknown")
check("AB33d the relocation one too", _v(_MSD_EMPTY, "relocation"), "Unknown")

# -- regression: the Wave 5 behaviour must survive ------------------------
check("AB34 W/P20 contraction still No",
      _v("We aren't able to sponsor visas for this role."), "No")
check("AB35 right-to-work requirement still No",
      _v("You must have the right to work in the country of employment."), "No")
check("AB36 hedged negation still Unknown",
      _v("Visa sponsorship is not guaranteed."), "Unknown")
check("AB37 plain offer still Yes",
      _v("We offer full visa sponsorship and relocation support."), "Yes")
check("AB38 empty text still Unknown",
      _v(""), "Unknown")

# ════════════════════════════════════════════════════════════════════════
# AC. Wave 7 — P27/P27b workload, P28 embedded JSON, P29 doubled country
# ════════════════════════════════════════════════════════════════════════
print("\n-- AC. workload classifier, embedded JSON, doubled country --")

_AMZ = ("Cloud Operations Architect, ES - EMEA South - Italy. "
        "Do you want to be part of history? "
        "The salary listed corresponds to working on a full-time basis. "
        "For part-time hours, the salary will be pro-rated.")

# -- P27 pay boilerplate no longer decides the schedule -------------------
check("AC1  Amazon pay footnote no longer says Part-time",
      car.classify_workload(title="Cloud Operations Architect", text=_AMZ), "")
check("AC2  ats agrees (identical bytes)",
      ats.classify_workload(title="Cloud Operations Architect", text=_AMZ), "")
check("AC3  a real part-time title still wins",
      car.classify_workload(title="Sales Assistant (Part-time)", text=_AMZ),
      "Part-time")
check("AC4  a real part-time statement still wins",
      car.classify_workload(title="Shop Assistant",
                            text="This is a part-time position, 20 hours per week."),
      "Part-time")
check("AC5  the employer's structured field outranks the prose",
      car.classify_workload(employer_field="PART_TIME", title="Engineer",
                            text=_AMZ), "Part-time")
check("AC6  internship still outranks everything",
      car.classify_workload(title="Data Center Technician - 2027 Internship",
                            text=""), "Internship")
check("AC7  Italian part-time", car.classify_workload(
      title="Addetto vendite", text="Contratto part-time 24 ore settimanali."),
      "Part-time")
check("AC8  nothing stated stays empty",
      car.classify_workload(title="Store Manager", text="Join our team in Milan."),
      "")

# -- P27b the two scanners agree about Contract --------------------------
check("AC9  contract from the employer field",
      car.classify_workload(employer_field="fixed_term", title="Analyst"),
      "Contract")
check("AC10 ats says Contract too, not Full-time",
      ats.classify_workload(employer_field="fixed_term", title="Analyst"),
      "Contract")
check("AC11 'contract' in ordinary prose is not a contract role",
      car.classify_workload(title="Legal Counsel",
                            text="You will lead contract negotiation and "
                                 "contract management."), "")
check("AC12 ats agrees",
      ats.classify_workload(title="Legal Counsel",
                            text="You will lead contract negotiation and "
                                 "contract management."), "")
check("AC13 permanent is full-time in both",
      (car.classify_workload(employer_field="permanent"),
       ats.classify_workload(employer_field="permanent")),
      ("Full-time", "Full-time"))
truthy("AC14 the ATS classifier delegates to the shared function",
       "classify_workload(employer_field=raw" in src_a)
truthy("AC15 the career listing path delegates too",
       "workload = classify_workload(title=title, text=context_text)" in src_c)

# -- P28 embedded JSON ----------------------------------------------------
_BS_HTML = (
    '<html><head><script id="__NEXT_DATA__" type="application/json">'
    '{"props":{"pageProps":{"list":[{"id":"63c548b3521886004f3daec0",'
    '"jobTitle":"Product manager","officeLocations":['
    '{"title":"Milan (Italy)","country":{"isoCode":"IT","name":"Italy"}},'
    '{"title":"London (UK)","country":{"isoCode":"GB","name":"United Kingdom"}}],'
    '"workSchedule":"full_time","typesOfContract":["permanent"],'
    '"highLevelDescription":"Build great products."}]}}}'
    '</script></head><body></body></html>')
_jobs, _index = car.extract_embedded_json_jobs(_BS_HTML, "https://jobs.x.com/")
check("AC16 a record with no url is parked for the DOM pass", len(_index), 1)
check("AC17 the plural officeLocations key is read",
      list(_index.values())[0]["location_hint"], "Milan (Italy); London (UK), United Kingdom")
check("AC18 the nested country is not duplicated into the office title",
      "Italy, Italy" in list(_index.values())[0]["location_hint"], False)
check("AC19 JD text comes along for free",
      "Build great products." in list(_index.values())[0]["jd_text"], True)
check("AC20 the employer's schedule is captured as card context",
      "full_time" in list(_index.values())[0]["card_context"], True)
_html_url = _BS_HTML.replace('"id":"63c5', '"url":"/positions/63c5","id":"63c5')
_jobs2, _ = car.extract_embedded_json_jobs(_html_url, "https://jobs.x.com/")
check("AC21 an explicit url yields a ready-to-use row", len(_jobs2), 1)
check("AC22 and it is absolute", _jobs2[0]["job_url"],
      "https://jobs.x.com/positions/63c5")
check("AC23 a page with no embedded JSON yields nothing",
      car.extract_embedded_json_jobs("<html><body>hi</body></html>", "https://x/"),
      ([], {}))
truthy("AC24 the SPA bail-out now mines before it gives up",
       "extract_embedded_json_jobs(" in src_c
       and "using browser{_extra}" in src_c)
check("AC25 synthetic #job= twins are dropped when the real url exists",
      [r["job_url"] for r in car.CareerPortalScanner._drop_synthetic_twins([
          {"job_title": "Product manager", "job_url": "https://x/positions/1"},
          {"job_title": "Product manager", "job_url": "https://x/#job=product-manager"},
      ])], ["https://x/positions/1"])
check("AC26 a synthetic row with no real twin is kept",
      len(car.CareerPortalScanner._drop_synthetic_twins([
          {"job_title": "Solo role", "job_url": "https://x/#job=solo-role"}])), 1)
check("AC27 flattening handles a bare string list",
      car._embed_loc_string(["Milan", "Rome"]), "Milan; Rome")
check("AC28 and a flat object", car._embed_loc_string(
      {"city": "Berlin", "country": "Germany"}), "Berlin, Germany")

# -- P29 the country is appended once ------------------------------------
truthy("AC29 the already-has-a-country test guards the site branch too",
       "if self._w3_has_country(out_location):\n                                loc_source "
       in src_c)
check("AC30 'Formia, Lazio, Italia' already names its country",
       S._w3_has_country("Formia, Lazio, Italia"), True)
check("AC31 so does the English form", S._w3_has_country("Cuneo, Italy"), True)
check("AC32 a bare town does not", S._w3_has_country("Cittaducale"), False)

# ════════════════════════════════════════════════════════════════════════
# AD. Wave 8 — P31 cross-host ATS, P32 redirectors, P33 facet chips, P34 API
# ════════════════════════════════════════════════════════════════════════
print("\n-- AD. link-shape validators and the jobsinnetwork adapter --")

# -- P31: the careers list lives on a third-party ATS host ----------------
check("AD1  Journi's kula.ai job url is accepted",
      S.is_valid_job_url("https://careers.kula.ai/journi/35169"), True)
check("AD2  the ATS board landing page is still not a job",
      S.is_valid_job_url("https://careers.kula.ai/journi"), False)
check("AD3  lever detail url", S.is_valid_job_url(
      "https://jobs.lever.co/acme/8f3c2b10-1a2b-4c3d-9e8f-123456789abc"), True)
check("AD4  workable detail url", S.is_valid_job_url(
      "https://apply.workable.com/acme/j/A1B2C3D4E5/"), True)
check("AD5  a careers.* host still needs an opaque id",
      S.is_valid_job_url("https://careers.example.com/about"), False)

# -- P32: aggregator clickout redirectors ---------------------------------
check("AD6  /clickout/<hash> is the job link", S.is_valid_job_url(
      "https://englishjobsearch.at/clickout/a37f0fea3caa547e"), True)
check("AD7  /clickout_alt/<hash> too", S.is_valid_job_url(
      "https://englishjobsearch.at/clickout_alt/806efa15679be53c"), True)
check("AD8  a bare /go/ is not a job",
      S.is_valid_job_url("https://example.com/go/"), False)
check("AD9  a redirector with no id is not a job",
      S.is_valid_job_url("https://example.com/redirect/x"), False)

# -- P33: department / facet chips must not be written as jobs ------------
for _seg in ("Sales-and-Sales-Related", "Marketing", "IT", "Public-Relations",
             "Business-Development", "Customer-Support",
             "Banken-Finanz-Versicherungen", "IT-Telekommunikation",
             "Marketing-and-Communications", "Healthcare-and-Medical",
             "Quality-and-Regulatory-Affairs",
             "PROFESSIONAL-SCIENTIFIC-AND-TECHNICAL-ACTIVITIES", "AT-Vienna"):
    check(f"AD10 chip /jobs/{_seg[:30]} rejected",
          S.is_valid_job_url(f"https://www.jobsinvienna.com/jobs/{_seg}"), False)
check("AD11 a Title-Cased REAL role survives the chip rule",
      S.is_valid_job_url("https://x.com/jobs/Senior-Software-Engineer"), True)
check("AD12 so does Head-of-Product",
      S.is_valid_job_url("https://x.com/jobs/Head-of-Product"), True)
check("AD13 and a normal slug with a requisition id",
      S.is_valid_job_url("https://x.com/jobs/senior-data-engineer-12345"), True)
check("AD14 'marketing' inside a real slug is not a chip",
      S.is_valid_job_url(
          "https://careers.x.com/jobs/marketing-manager-berlin-9981"), True)
check("AD15 Italian detail path still passes", S.is_valid_job_url(
      "https://it.action.jobs/offerte-di-lavoro/negozio/addetto-vendite-1234"), True)
check("AD16 Decathlon /annonce/ still passes (P0 regression)",
      S.is_valid_job_url("https://www.decathlon.it/it/annonce/55123-vendeur"), True)
check("AD17 chips get their own quarantine reason",
      car.is_category_facet_url(
          "https://www.jobsinvienna.com/jobs/Marketing"), True)
truthy("AD18 the reason is wired into process_job",
       'category_or_department_link' in src_c)

# -- P34: the jobsinnetwork adapter ---------------------------------------
truthy("AD19 adapter exists",
       hasattr(car.CareerPortalScanner, "_fetch_jobsinnetwork_jobs"))
truthy("AD20 it is reached by host, not only by provider name",
       "jobsin[a-z]+" in src_c)
truthy("AD21 it reads the board config off the page, not the seed",
       "boardCountry" in src_c and "apiUrl" in src_c)
truthy("AD22 it filters to the board's own language",
       '"language", locale' in src_c or "('language', locale)" in src_c)
truthy("AD23 a 2-letter ISO country is upper-cased for the resolver",
       "_country.isalpha()" in src_c)
truthy("AD24 rows carry JD text so no detail visit is needed",
       '"jd_text": _jd_plain(it.get("description")' in src_c)

# ── P35: location recovery is no longer gated on scope policy ─────────────
# LOOP (scope_policy=seed_url, Austria) wrote all 16 rows with Job
# Location=Unknown because the deferred detail fetch only ever ran for
# scope_policy=job_location rows. The label/value parser (P14) was already
# correct -- nothing was calling it.
truthy("AE1 accepted rows get a location-only flag",
       "_loc_pending" in src_c)
truthy("AE2 the flag is independent of the scope queue",
       '_loc_pending = (not _scope_pending) and _unproven and _real_url' in src_c)
truthy("AE3 the resolver drains both queues",
       'if rec.get("_loc_pending") and not rec.get("_scope_pending")' in src_c)
truthy("AE4 location-only rows have their own budget",
       "SPONSORSCOUT_LOC_RESOLVE_MAX" in src_c)
truthy("AE5 the scope queue is served first and cannot be starved",
       "pending = pending + loc_only[:loc_budget]" in src_c)
truthy("AE6 the confidence upgrade ignores context and URL",
       '_scope_country_match(_tc, loc, "", "")' in src_c)
truthy("AE7 the flag never reaches a CSV",
       'rec.pop("_loc_pending", None)' in src_c)
truthy("AE8 only _scope_pending rows can still be quarantined",
       src_c.count('"outside_or_unproven_target_country"') >= 2)

# Behavioural: the LOOP spec strip, offline.
_loop_html = (
    '<div class="loopoffices"><span class="city">Salzburg</span>'
    '<span class="city">New York City</span><span class="city">Vienna</span></div>'
    '<div class="specs"><div class="specs__item">'
    '<span class="subheadline">Start</span><span class="h4">Immediately</span></div>'
    '<div class="specs__item"><span class="subheadline">Location</span>'
    '<span class="h4">Salzburg - Vienna </span></div>'
    '<div class="specs__item"><span class="subheadline">Department</span>'
    '<span class="h4">Mobile Development</span></div></div>')
check("AE9 the labelled strip beats the office-switcher decoy",
      S._label_location(_loop_html), "Salzburg - Vienna")
check("AE10 a same-country multi-site value is kept verbatim",
      S._multisite_same_country("Salzburg - Vienna"), "Salzburg - Vienna")

# Behavioural: the resolver itself, with the network stubbed out.
def _p35_run(flag, resolved, target="Austria", policy="seed_url"):
    rec = {"Job Title": "X", "Job URL": "https://e.com/careers/x",
           "Job Location": "Unknown", "Location Source": "none",
           "Location Confidence": "none", "Scope Confidence": "unverified_seed_url",
           "Canonical Job ID": "cid1", flag: True}
    if flag == "_scope_pending":
        rec["_scope_context"] = ""
    jobs, quar, stats, diags = {"cid1": rec}, [], {"quarantined": 0,
                                                   "rejected_scope": 0}, []
    row = {"name": "E", "target_country": target, "scope_policy": policy,
           "careers_url": "https://e.com/careers"}
    orig = S._detail_location_from_url
    S._detail_location_from_url = lambda u, **k: (
        (resolved, "detail") if resolved else None)
    try:
        S._resolve_deferred_scope(row, jobs, quar, stats, diags)
    finally:
        S._detail_location_from_url = orig
    return rec, jobs, quar

_r, _j, _q = _p35_run("_loc_pending", "Vienna")
check("AE11 a location-only row is filled in", _r["Job Location"], "Vienna")
check("AE12 ... and marked as detail evidence", _r["Location Source"], "detail")
check("AE13 ... and kept, never quarantined", len(_q), 0)
check("AE14 ... and stays in the accepted set", "cid1" in _j, True)
check("AE15 ... and the seed_url guess is confirmed",
      _r["Scope Confidence"], "verified")

_r, _j, _q = _p35_run("_loc_pending", "Berlin")
check("AE16 an off-target location is still recorded", _r["Job Location"], "Berlin")
check("AE17 ... is NOT upgraded to verified",
      _r["Scope Confidence"], "unverified_seed_url")
check("AE18 ... and is STILL not quarantined (seed_url decides membership)",
      len(_q), 0)

_r, _j, _q = _p35_run("_loc_pending", None)
check("AE19 an unresolvable location-only row survives", len(_q), 0)
check("AE20 ... with an honest Unknown", _r["Job Location"], "Unknown")
check("AE21 ... and no leftover flag", "_loc_pending" in _r, False)

# AE22-AE24 REWRITTEN for P64 (2026-10-08). They used to pin "an unproven
# job_location row is quarantined". That is precisely the behaviour the user
# removed: a country batch groups by HQ, it does not delete that portal's
# jobs in other countries. The row must now be KEPT -- and the old behaviour
# is still pinned below, behind SPONSORSCOUT_SCOPE_FILTER=1.
_r, _j, _q = _p35_run("_scope_pending", None, policy="job_location")
check("AE22 P64: an unproven job_location row is KEPT, not quarantined",
      len(_q), 0)
check("AE23 ... and stays in the accepted set", "cid1" in _j, True)
check("AE24 ... labelled with the existing vocabulary",
      _r["Scope Confidence"], "unverified_seed_url")
check("AE24b ... and keeps its honest Unknown location",
      _r["Job Location"], "Unknown")


# ── Italian-portal defects from run 20261006T211320 (P36-P42) ────────────

# P37: a company name standing in front of a street is not a town.
for _bad in ("Unipol Assicurazioni S.p.A , Via Stalingrado 45",
             "ANDAMENTO UniSalute , Via Larga 8",
             "UNA Italian Hospitality , Via Melchiorre Gioia 8",
             "Unipol Rental S.p.A , Via Toscana 2"):
    check(f"AF1 org rejected as town: {_bad[:26]}",
          S._town_from_address(_bad, "Gruppo UNA"), None)
for _good, _want in (("Bologna , Via Stalingrado 45", "Bologna"),
                     ("Via Stalingrado 45, Bologna", "Bologna"),
                     ("40128 Bologna", "Bologna"),
                     ("Cittaducale , Via Roma 1", "Cittaducale"),
                     ("Serravalle Scrivia , Via Mazzini 3", "Serravalle Scrivia")):
    check(f"AF2 unlisted town still recovered: {_want}",
          S._town_from_address(_good, "Gruppo UNA"), _want)
check("AF2b a bare acronym is not a town (P37b: 'SIAT' survived v1)",
      S._town_from_address("SIAT , Via Toscana 2", "Gruppo UNA"), None)
# P37c (run 20261007T174338): Amazon published "Operations" x3 and "Our" as
# Job Location, source=address, confidence HIGH -- prose sitting in front of
# a street. Whole-candidate matches plus a leading-function-word rule.
for _p in ("Operations , Via Roma 1", "Our , Via Roma 1",
           "The Delivery Station , Via Roma 1", "In Amazon , Via Roma 1",
           "Warehouse , Via Roma 1"):
    check(f"AF2d prose rejected as town: {_p[:24]}",
          S._town_from_address(_p, "Amazon Italia"), None)
check("AF2e a town is still recovered after the prose rules",
      S._town_from_address("Cittaducale , Via Roma 1", "Amazon Italia"), "Cittaducale")

# P42b: the rescue must not publish a CAREER SITE's own page title.
for _t in ("UNIPOL_External Career Site", "Lavora con noi", "Job Search",
           "Offerte di lavoro", "Sign in"):
    truthy(f"AF24b page furniture refused: {_t[:26]}",
           bool(car._PAGE_FURNITURE_TITLE_RE.search(_t)))
for _t in ("Business Affairs Executive", "Operations Supervisor",
           "Site Procurement Manager", "Area Manager, FC Operations"):
    truthy(f"AF24c real title kept: {_t[:26]}",
           not car._PAGE_FURNITURE_TITLE_RE.search(_t))

check("AF2c ... but a title-case town still is",
      S._town_from_address("Spilamberto , Via Roma 1", "Gruppo UNA"), "Spilamberto")

# REGRESSION GUARD (run 20261006T225923): the first P38 pattern ended each
# alternative with (?:/|$|?), so "index.php" matched MID-PATH and 56 real
# Ferrero vacancies were rejected as site furniture. A script name is only
# an endpoint when it is the LAST segment.
for _u in ("https://www.ferrerocareers.com/int/index.php/en/jobs/warehouse",
           "https://www.ferrerocareers.com/int/index.php/en/jobs/deployment-planner",
           "https://www.ferrerocareers.com/int/index.php/en/jobs/quality-system"):
    check(f"AF4b Ferrero path-info job kept: {_u[-28:]}", S.is_valid_job_url(_u), True)
check("AF4c ... while the bare endpoint is still furniture",
      S.is_valid_job_url("https://autogrill.intervieweb.it/app.php"), False)
check("AF4d ... including with a query string",
      S.is_valid_job_url("https://novacoop.intervieweb.it/app.php?x=1"), False)
check("AF4e ... and an .aspx job with a query is NOT furniture",
      S.is_valid_job_url("https://joblink.allibo.com/ats3/job-offer.aspx?ID=109914"), True)

truthy("AF3 the gazetteer gate was NOT used for this (it would kill P0-41)",
       'self._sanitize_job_location(_town' not in src_c)

# P38: ATS vendor home pages and session endpoints are not vacancies.
for _u in ("https://autogrill.intervieweb.it/app.php",
           "https://novacoop.intervieweb.it/cdn-cgi/l/email-protection",
           "https://www.successfactors.com",
           "https://www.teamtailor.com"):
    check(f"AF4 furniture rejected: {_u[:44]}", S.is_valid_job_url(_u), False)
for _u in ("https://md.intervieweb.it/jobs/addettoa-reparto-gastronomia-mestre-1447/it",
           "https://joblink.allibo.com/ats3/job-offer.aspx?ID=109914",
           "https://careers.oniverse.it/it-IT/carriere/dettaglio-annuncio?JobID=2470",
           "https://boards.greenhouse.io/anthropic/jobs/4567"):
    check(f"AF5 real job kept: {_u[:46]}", S.is_valid_job_url(_u), True)
truthy("AF6 a tenant path on a vendor host is still a job",
       car.is_ats_furniture_url("https://boards.greenhouse.io/anthropic") is False)

# P39: a role offered in several countries keeps the target one.
_it = {"target_country": "Italy", "scope_policy": "job_location"}
check("AF7 multi-country posting resolves to the target country",
      S._location_in_target_country(
          "Milan (Italy), London (UK), Madrid (Spain), Warsaw (Poland), or fully remote",
          _it), "Milan, Italy")
check("AF8 ... also with the trailing 'or' form",
      S._location_in_target_country(
          "Milan (Italy), London (UK), Madrid (Spain), or Warsaw (Poland)", _it),
      "Milan, Italy")
check("AF9 a list WITHOUT the target stays unresolved (still quarantined)",
      S._location_in_target_country("Warsaw (Poland), or Madrid (Spain)", _it), "")
check("AF10 a single-site string is left completely alone",
      S._location_in_target_country("Milan, MI, ITA", _it), "")
check("AF11 ... and so is a bare city",
      S._location_in_target_country("Bologna", _it), "")
check("AF12 a Global seed never triggers it",
      S._location_in_target_country(
          "Milan (Italy), London (UK)", {"target_country": "Global"}), "")

# P40: the branch named in a store job's title.
check("AF13 Italian store title yields its city",
      S._location_from_title_text("RESPONSABILE DI SALA - ROSSOPOMODORO ROMA CENTRO"),
      "Roma")
check("AF14 ... reported as FOUND, not filtered to the target",
      S._location_from_title_text("PIZZAIOLO - ROSSOPOMODORO EATALY MUNCHEN"),
      "M\u00fcnchen")
check("AF15 ... so scope can still reject a German branch",
      S._location_from_title_text("Verk\u00e4uferin | INTIMISSIMI Erlangen"), "Erlangen")
for _t in ("Senior Software Engineer", "Legal counsel (corporate M&A)",
           "Account Manager, WW Returns & Recommerce", "RICERCA URGENTE"):
    check(f"AF16 no place invented in {_t[:30]!r}",
          S._location_from_title_text(_t), "")

# P41: Raw Location survives the P17 context revocation.
truthy("AF17 the shared-context reverter re-reads Raw Location",
       'rec.get("Raw Location") or ""' in src_c)
truthy("AF18 ... and only accepts it through the sanitiser",
       "_raw_loc = (\"Unknown\" if _raw_loc == \"Not Specified\"" in src_c)

# P42: a title that only exists on the detail page.
truthy("AF19 detail-title rescue exists",
       hasattr(car.CareerPortalScanner, "_title_from_detail_page"))
truthy("AF20 it is budgeted", "SPONSORSCOUT_TITLE_RESCUE_MAX" in src_c)
truthy("AF20b ... per company too, so one junk board cannot serialise the run",
       "SPONSORSCOUT_TITLE_RESCUE_PER_COMPANY" in src_c)
truthy("AF20c ... with a short timeout", "timeout=8" in src_c)
truthy("AF21 the budget counter is lock-protected",
       "_title_rescue_lock" in src_c and "threading.Lock()" in src_c)
truthy("AF22 the recovered title must pass the SAME validator",
       "if cleaned and self.is_valid_job_title(cleaned, company)" in src_c)
truthy("AF23 it is only reached after the slug rescue fails",
       src_c.index("_title_from_url_slug(clean_url)")
       < src_c.index("_title_from_detail_page(clean_url, name)"))
truthy("AF24 one fetch per row, not two",
       src_c.count("self._title_from_detail_page(clean_url, name)") == 1)

# P36: the ATS side stops reading a work-mode label as a place.
check("AF25 ATS skips the qualifier and reads the city",
      a_inst.format_location(
          "Remote-Friendly (Travel-Required) | San Francisco, CA | Seattle, WA"),
      "San Francisco, CA")
check("AF26 ... including the single-site form",
      a_inst.format_location("Remote-Friendly (Travel Required) | Canada"), "Canada")
check("AF27 ... and a country-only segment with stray spacing",
      a_inst.format_location("Remote-Friendly (Travel-Required) |  Washington, DC"),
      "Washington, DC")
check("AF28 a bare 'Remote' still wins over a later city (unchanged)",
      a_inst.format_location("Remote | Berlin"), "Remote")
check("AF29 a plain location is untouched",
      a_inst.format_location("London, UK"), "London, UK")


# ── P43/P44: the JD's own country wins; a recovered town keeps a country ──

# P43: under seed_url the verdict could be taken from context+URL, so an
# Amazon role located in India was stamped "verified" for an Italy seed.
for _loc, _want in (("India, Gurgaon", "India"),
                    ("Are, UAE, Abu Dhabi", "United Arab Emirates"),
                    ("San \u0120iljan, Malta", "Malta"),
                    ("Milan, Italy", "Italy"),
                    ("Roma", "Italy")):
    check(f"AG1 country of {_loc[:22]!r}", S._country_of_location(_loc), _want)
check("AG2 an unknown location yields no country",
      S._country_of_location("Unknown"), "")
check("AG3 a town the gazetteer lacks yields no country (honest)",
      S._country_of_location("Lissone"), "")
truthy("AG4 the seed_url verdict is overruled by the job's own country",
       '_own_country = self._country_of_location(out_location)' in src_c)
truthy("AG5 ... and that only downgrades, never drops the row",
       '_scope_conf = "unverified_seed_url"' in src_c
       and 'outside_or_unproven_target_country' in src_c)
truthy("AG6 the same rule is applied to P35-recovered rows",
       '_oc = self._country_of_location(loc)' in src_c)

# P44: _resolve_deferred_scope bypassed the inline country-naming step, so
# real Leroy Merlin towns shipped with no country at all.
_it = ("https://lavoro.leroymerlin.it/jobs/8500497-x",
       "https://lavoro.leroymerlin.it")
for _town in ("Lissone", "Seriate", "Corsico"):
    check(f"AG7 {_town} gains its country",
          S._append_country_if_missing(_town, "detail", *_it),
          (f"{_town}, Italy", "detail+site"))
check("AG8 a host-derived country is labelled, and so demoted",
      S._location_confidence("Lissone, Italy", "detail+site"), "low")
check("AG9 a location that already names its country is untouched",
      S._append_country_if_missing("Milan, Italy", "detail", *_it),
      ("Milan, Italy", "detail"))
check("AG10 a gazetteer-known town is untouched",
      S._append_country_if_missing("Bologna", "detail", *_it),
      ("Bologna", "detail"))
check("AG11 P29 guard: a foreign-language country is relabelled, not appended",
      S._append_country_if_missing("Formia, Lazio, Italia", "card",
                                   "https://x.it/a", "https://x.it"),
      ("Formia, Lazio, Italia", "card+gazetteer"))
check("AG12 no country anywhere -> left honest, not invented",
      S._append_country_if_missing("Gallarate", "title",
                                   "https://corporate.yamamay.com/it/x",
                                   "https://corporate.yamamay.com"),
      ("Gallarate", "title"))
truthy("AG13 the recovery path calls it",
       "_rsrc = self._append_country_if_missing(" in src_c)


print("\n-- AH. P45/P49/P50/P51/P53 (Netherlands batch 20261007T201018) --")
# ---- P49: ISO-3166 alpha-3 in a Workday job URL ----
_WD = "https://msd.wd5.myworkdayjobs.com/en-US/SearchJobs/job/"
check("AH1 NLD province URL -> place + Netherlands",
      S._workday_url_location(_WD + "NLD---North-Holland---Haarlem/Op_R1"),
      ("North Holland", "Netherlands"))
check("AH2 single-segment NLD URL (the Wolters Kluwer case)",
      S._workday_url_location(
          "https://careers.wolterskluwer.com/job/NLD---Alphen-Aan-Den-Rijn/Ed_R1"),
      ("Alphen Aan Den Rijn", "Netherlands"))
check("AH3 IRL", S._workday_url_location(_WD + "IRL---Dublin---Blackrock/Q_R2"),
      ("Dublin", "Ireland"))
check("AH4 ITA", S._workday_url_location(_WD + "ITA---Lombardia---Milano/L_R3"),
      ("Lombardia", "Italy"))
check("AH5 unknown code is never guessed",
      S._workday_url_location(_WD + "XXX---Nowhere/R_R4"), ("", ""))
check("AH6 a non-Workday URL is untouched",
      S._workday_url_location("https://example.com/jobs/engineer"), ("", ""))
check("AH7 lowercase must NOT match (alpha-3 is upper in Workday URLs)",
      S._workday_url_location(_WD + "nld---utrecht/x_R5"), ("", ""))
check("AH8 ATS parity on the same URL",
      A._workday_url_location(_WD + "NLD---North-Holland---Haarlem/Op_R1"),
      S._workday_url_location(_WD + "NLD---North-Holland---Haarlem/Op_R1"))
truthy("AH9 ISO3 table is shared verbatim",
       S._ISO3_TO_COUNTRY == A._ISO3_TO_COUNTRY)
# ---- P49 ranks above the host guess, and is NOT demoted to low ----
check("AH10 Workday URL country beats _country_from_site",
      S._append_country_if_missing(
          "Someplace", "detail", _WD + "NLD---Utrecht/x_R6"),
      ("Someplace, Netherlands", "detail+url"))
check("AH11 +url is not demoted to low",
      S._location_confidence("Someplace, Netherlands", "detail+url") != "low",
      True)
# ---- P51: Dutch provinces ----
for _p in ("North Holland", "Noord-Holland", "North Brabant", "Gelderland",
           "Overijssel", "Flevoland", "Drenthe", "Friesland", "Zeeland"):
    check(f"AH12 {_p} -> Netherlands", S._country_of_location(_p), "Netherlands")
check("AH13 Limburg stays ambiguous (NL *and* BE) -- never guessed",
      S._country_of_location("Limburg"), "")
check("AH14 Benelux is a region marker, not a country",
      S._country_of_location("Benelux"), "")
truthy("AH15 gazetteer parity still holds after the additions",
       S._W3_CITY_COUNTRY == A._W3_CITY_COUNTRY)
# ---- P50: the Multiple Locations placeholder is unproven ----
truthy("AH16 placeholder counts as unproven at the scope gate",
       "out_location == _MULTI_LOCATION_LABEL" in src_c)
# ---- P45: no for/else message when the loop never ran ----
truthy("AH17 page-cap message is guarded by a non-zero limit",
       "if _page_limit:" in src_c)
# ---- P53: no per-row browser launch inside the pool ----
truthy("AH18 the pool resolves over HTTP only",
       "allow_browser=False" in src_c)
truthy("AH19 _detail_location_from_url honours the flag",
       "if not allow_browser:" in src_c)
truthy("AH20 a single shared browser sweeps the remainder",
       "_browser_sweep" in src_c and "browser_page=_pg" in src_c)
truthy("AH21 the sweep is budgeted",
       "SPONSORSCOUT_SCOPE_BROWSER_MAX" in src_c)
# ---- regression pins: Wave 8f must survive all of the above ----
check("AH22 P44 Leroy Merlin row unchanged",
      S._append_country_if_missing("Lissone", "detail",
                                   "https://lavoro.leroymerlin.it/jobs/1"),
      ("Lissone, Italy", "detail+site"))
check("AH23 P44 host-derived country still demoted to low",
      S._location_confidence("Lissone, Italy", "detail+site"), "low")
check("AH24 P44 bare town with no country stays honest",
      S._append_country_if_missing("Gallarate", "title",
                                   "https://corporate.yamamay.com/it/x"),
      ("Gallarate", "title"))
check("AH25 P43 still reads the job's own country",
      S._country_of_location("India, Bengaluru"), "India")
check("AH26 Italia is still NOT canonicalised (career)",
      S._sanitize_job_location("Italia"), "Italia")
check("AH27 Italia is still NOT canonicalised (ats)",
      A._sanitize_job_location("Italia"), "Italia")

print("\n-- AI. P54/P55 (mixed-EU batch 20261007T215944) --")
# ---- P54: a string that NAMES its country must resolve ----
for _l, _c in [("Baku, Azerbaijan", "Azerbaijan"),
               ("Asuncion, Paraguay", "Paraguay"),
               ("Dar ES Salaam, Tanzania", "Tanzania"),
               ("Karachi, Pakistan", "Pakistan"),
               ("Dhaka, Bangladesh", "Bangladesh"),
               ("Colombo, Sri Lanka", "Sri Lanka"),
               ("Almaty, Kazakhstan", "Kazakhstan"),
               ("Yerevan, Armenia", "Armenia"),
               ("Tashkent, Uzbekistan", "Uzbekistan"),
               ("Muscat, Oman", "Oman"),
               ("Podgorica, Montenegro", "Montenegro"),
               ("San Jose, Costa Rica", "Costa Rica")]:
    check(f"AI1 {_l!r}", S._country_of_location(_l), _c)
check("AI2 ats resolves the same string",
      A._supplementary_country("Baku, Azerbaijan"), "Azerbaijan")
truthy("AI3 alias-table parity still holds",
       S._W3_COUNTRY_ALIASES == A._W3_COUNTRY_ALIASES)
# a region is still never a country
for _r in ("Remote", "North America", "Europe", "EMEA", "Unknown", "Benelux"):
    check(f"AI4 {_r!r} is not a country", S._country_of_location(_r), "")
# "Jamaica" was deliberately NOT added -- a US address must stay US
check("AI5 'Jamaica, NY' stays United States",
      S._country_of_location("Jamaica, NY"), "United States")
# ---- P55: the #job= fragment ----
check("AI6 fragment names city + country",
      S._location_from_url_fragment(
          "https://www.fxpro.com/careers#job=erp-product-manager--cyprus-ypsonas-hybrid"),
      "Ypsonas, Cyprus")
check("AI7 fragment with the slug repeated",
      S._location_from_url_fragment(
          "https://www.fxpro.com/careers#job=senior-qa-engineer-backend--senior-qa-engineer-backend-cyprus-ypsona"),
      "Cyprus")
check("AI8 a work mode is NOT a location (P0-12)",
      S._location_from_url_fragment(
          "https://www.fxpro.com/careers#job=bdm-japanese--remote"), "")
check("AI9 no fragment -> nothing",
      S._location_from_url_fragment("https://example.com/jobs/engineer"), "")
truthy("AI10 process_job consults the fragment",
       '_frag = self._location_from_url_fragment(clean_url)' in src_c)
truthy("AI11 and only for #job= rows the detail fetch refuses",
       '"#job=" in clean_url.lower()' in src_c)
# ---- regression pins for every earlier wave ----
check("AI12 P49 Workday still read",
      S._workday_url_location(
          "https://x.myworkdayjobs.com/job/NLD---Utrecht/x_R1"),
      ("Utrecht", "Netherlands"))
check("AI13 P51 Dutch province", S._country_of_location("North Holland"),
      "Netherlands")
check("AI14 P44 Leroy Merlin row byte-identical",
      S._append_country_if_missing("Lissone", "detail",
                                   "https://lavoro.leroymerlin.it/j/1"),
      ("Lissone, Italy", "detail+site"))
check("AI15 P43 job's own country", S._country_of_location("India, Bengaluru"),
      "India")
check("AI16 Italia still not canonicalised (career)",
      S._sanitize_job_location("Italia"), "Italia")
check("AI17 Italia still not canonicalised (ats)",
      A._sanitize_job_location("Italia"), "Italia")

# ══ GROUP AJ — Wave 8i: P56 Phenom detail paths, P57 boilerplate labels ══
# P56: /job-description/<slug>/<req>/ (Dyson, Phenom) was rejected outright.
check("AJ1 P56 Phenom detail path accepted",
      S.is_valid_job_url(
          "http://careers.dyson.com/en-gb/job-description/data-analyst/JR38601/"),
      True)
check("AJ2 P56 job-posting accepted",
      S.is_valid_job_url("https://x.com/job-posting/senior-dev/R-204"),
      True)
check("AJ3 P56 job-opening accepted",
      S.is_valid_job_url("https://x.com/job-openings/qa-lead/8891"),
      True)
truthy("AJ4 P56 token present in the compiled pattern",
       "job[-_]?(?:description" in src_c)
# The listing page itself must STILL be refused.
check("AJ5 P56 does not swallow the listing root",
      S.is_valid_job_url("https://careers.dyson.com/en-gb/search-results/"),
      False)
# P57: a screen-reader label is not a title.
check("AJ6 P57 'about X' tail wins",
      car._recover_anchor_title(
          "More details about Data Analyst",
          "http://x.com/job-description/data-analyst/JR38601/"), "Data Analyst")
check("AJ7 P57 bare label falls back to the slug",
      car._recover_anchor_title(
          "More details",
          "http://x.com/job-description/senior-data-analyst/JR38431/"),
      "senior data analyst")
check("AJ8 P57 'Apply now' is not a title",
      car._recover_anchor_title(
          "Apply now", "http://x.com/job-description/lead-finance-analyst/JR38579/"),
      "lead finance analyst")
check("AJ9 P57 a REAL title is returned untouched",
      car._recover_anchor_title("Senior Backend Engineer",
                              "https://x.com/jobs/123"),
      "Senior Backend Engineer")
check("AJ10 P57 no English bias - Italian title untouched",
      car._recover_anchor_title("Ingegnere del Software",
                              "https://x.it/offerte/456-ing"),
      "Ingegnere del Software")
check("AJ11 P57 nothing recoverable -> reject, never 'More details'",
      car._recover_anchor_title("More details",
                              "https://x.com/job-description/JR999/"), "")
check("AJ12 P57 a requisition id is never a title",
      car._title_from_url_slug("https://x.com/job-description/JR38601/"), "")
check("AJ13 P57 slug stop-words skipped",
      car._title_from_url_slug(
          "https://x.com/en-gb/job-description/data-engineer/"),
      "data engineer")
truthy("AJ14 P57 wired into the static anchor loop",
       "title = _recover_anchor_title(title, absolute)" in src_c)
truthy("AJ15 P57 uses urllib.parse.unquote, never a bare unquote",
       "urllib.parse.unquote(seg)" in src_c)
# P56/P57 are career-only: the ATS scanner consumes structured API objects
# and has no anchor/URL vocabulary at all.
truthy("AJ16 P56/P57 correctly absent from the ATS scanner",
       "_recover_anchor_title" not in src_a
       and "JOB_URL_PATTERN" not in src_a)
# Regression pins carried forward.
check("AJ17 P54b rightmost country still wins",
      S._country_of_location("San Jose, Costa Rica"), "Costa Rica")
check("AJ18 P55 fragment location still parsed",
      S._location_from_url_fragment(
          "https://www.fxpro.com/careers#job=analyst--cyprus-ypsonas-hybrid"),
      "Ypsonas, Cyprus")
check("AJ19 Italia still not canonicalised after 8i",
      S._sanitize_job_location("Italia"), "Italia")

# ---- P58 schema.org microdata location (Dyson / Phenom) ----
check("AJ20 P58 nested itemprop=address read",
      S._extract_microdata_location_from_html(
          '<dd itemprop="jobLocation" itemscope>'
          '<span itemprop="address">Malmesbury, UK</span></dd>'),
      "Malmesbury, UK")
check("AJ21 P58 addressLocality preferred",
      S._extract_microdata_location_from_html(
          '<span itemprop="addressLocality">Berlin</span>'
          '<span itemprop="address">Germany HQ</span>'), "Berlin")
check("AJ22 P58 never echoes the label back",
      S._extract_microdata_location_from_html(
          '<span itemprop="address">Location</span>'), "")
check("AJ23 P58 no English bias - German label also refused",
      S._extract_microdata_location_from_html(
          '<span itemprop="address">Standort:</span>'), "")
check("AJ24 P58 no microdata -> empty, never a guess",
      S._extract_microdata_location_from_html('<div class="loc">Vienna</div>'),
      "")
check("AJ25 P58 oversized blob refused",
      S._extract_microdata_location_from_html(
          '<span itemprop="address">' + ("x" * 200) + '</span>'), "")
check("AJ26 P58 a real town still survives",
      S._extract_microdata_location_from_html(
          '<span itemprop="addressLocality">Lissone</span>'), "Lissone")
truthy("AJ27 P58 sits AFTER json-ld and BEFORE the label strip",
       src_c.index("_extract_ld_location_from_html(html)")
       < src_c.index("_extract_microdata_location_from_html(html)")
       < src_c.index("_lab = self._label_location(html)"))
truthy("AJ28 P58 is career-only (ATS consumes API objects)",
       "_extract_microdata_location_from_html" not in src_a)

# ══ GROUP AK — Wave 8i follow-ups: seed target_country, LIST budget ══
import csv as _csv, io as _io, os as _os
_SEED = _os.environ.get(
    "SEED", "/home/user/uploads/company_Career_seed.csv")
if _os.path.isfile(_SEED):
    _raw = open(_SEED, "rb").read()
    _rows = [r for r in _csv.DictReader(
        _io.StringIO(_raw.decode("utf-8-sig")))
        if r.get("name") and not r["name"].startswith("#")]
    check("AK1 seed still has 294 data rows", len(_rows), 294)
    check("AK2 seed still has no BOM", _raw[:3] == b"\xef\xbb\xbf", False)
    check("AK3 seed still CRLF only",
          _raw.count(b"\n") - _raw.count(b"\r\n"), 0)
    check("AK4 every row still has 12 columns",
          sorted({len(r) for r in _rows}), [12])
    _ik = [r for r in _rows if r["name"] == "Ikea Italia"]
    check("AK5 Ikea Italia present exactly once", len(_ik), 1)
    # AK6 REVERTED for P64: the row is back to "Europe". With country
    # scoping off, target_country records the PORTAL's scope and the HQ --
    # it is not a filter, so a regional value is no longer a defect.
    check("AK6 Ikea Italia target_country reverted to Europe",
          _ik[0]["target_country"], "Europe")
    check("AK7 Ikea Italia seed URL is byte-identical (never rewrite a URL)",
          _ik[0]["careers_url"],
          "https://jobs.ikea.com/en/search-jobs?k=&l=&orgIds=22908")
    check("AK8 Ikea Italia keeps scope_policy=job_location",
          _ik[0]["scope_policy"], "job_location")
    _regions = {"europe", "eu", "emea", "nordics", "dach", "benelux",
                "worldwide", "apac", "latam"}
    # AK9 INVERTED for P64: a regional target is allowed again.
    check("AK9 the only regional target is the reverted Ikea row",
          [r["name"] for r in _rows
           if r["target_country"].strip().lower() in _regions],
          ["Ikea Italia"])
    check("AK10 Italy bucket is back to 38 rows",
          sum(1 for r in _rows if r["target_country"] == "Italy"), 38)
    check("AK11 the EUROPE / regional banner is restored",
          len([l for l in _raw.decode("utf-8").split("\r\n")
               if l.startswith("#") and "EUROPE / regional" in l]), 1)
    _urls = [r["careers_url"].strip().lower().rstrip("/") for r in _rows]
    check("AK12 still zero duplicate seed URLs",
          len(_urls) - len(set(_urls)), 0)
# LIST budget raised on measured evidence, env override still wins.
check("AK13 LIST budget default raised to 300",
      S.config.LIST_TIME_BUDGET_SEC, 300)
truthy("AK14 LIST budget still overridable by env",
       'os.environ.get("SPONSORSCOUT_LIST_BUDGET") or 300' in src_c)
truthy("AK15 LIST budget keeps its 30s floor",
       "max(30, int(" in src_c)

# AK16-AK21 REWRITTEN for P64. They used to prove that naming the real
# country made Madrid and Aarhus REJECTED. That is exactly the behaviour the
# user removed -- an Ikea job in Madrid is still an Ikea job on the seeded
# portal -- so these now pin that nothing is dropped for its country.
_IKEA = "https://jobs.ikea.com/en/search-jobs?k=&l=&orgIds=22908"
def _ikea_row(t):
    return {"target_country": t, "scope_policy": "job_location",
            "careers_url": _IKEA, "name": "Ikea Italia"}
check("AK16 Italian job accepted under target=Italy",
      S._scope_allows(_ikea_row("Italy"), "Milano, Italy", "", _IKEA), True)
check("AK17 Italian job was ALSO fine under Europe (not the bug)",
      S._scope_allows(_ikea_row("Europe"), "Milano, Italy", "", _IKEA), True)
check("AK18 THE BUG: Madrid was accepted as an 'Ikea Italia' row",
      S._scope_allows(_ikea_row("Europe"), "Madrid", "", _IKEA), True)
check("AK19 P64: Madrid is KEPT, not rejected",
      S._scope_allows(_ikea_row("Italy"), "Madrid", "", _IKEA), True)
check("AK20 P64: Aarhus is kept",
      S._scope_allows(_ikea_row("Italy"), "Aarhus", "", _IKEA), True)
check("AK21 P64: a non-European job is kept too",
      S._scope_allows(_ikea_row("Italy"), "Beijing", "", _IKEA), True)

# ══ GROUP AL — Wave 8j: P60 fabricated towns, P61/P62 randstad ══
print("\n-- AL. P60/P61/P62 (Belgium batch 20261008T085006) --")
# ---- P60: the 14 shipped fabrications must all die ----
for _i, _t in enumerate((
        "Vacature 6017 Hallo, leuk dat je er bent",
        "job 5925 POPULAIR Vous cherchez un emploi",
        "2025 Connectiviteit en entertainment",
        "ref 5876 Als je graag werkt",
        "nr 5905 Wil jij bij ons werken?",
        "5317 Wij zoeken een collega",
        "5780 Hoe solliciteer je",
        "5621 Softwareontwikkeling bij Telenet"), 1):
    check(f"AL{_i} P60 fabrication refused: {_t[:26]!r}",
          S._town_from_address(_t, "Telenet"), None)
# ---- P60: real addresses must survive, in 4 languages ----
for _i, (_t, _exp) in enumerate((
        ("Liersesteenweg 4, 2800 Mechelen", "Mechelen"),
        ("Rue de la Loi 16, 1000 Bruxelles", "Bruxelles"),
        ("Kortrijksesteenweg 1144, 9051 Gent", "Gent"),
        ("Keizerslaan 11, 1000 Brussel", "Brussel"),
        ("Via della Tollegna 1, 13900 Biella", "Biella"),
        ("Hauptstrasse 5, 80331 Munchen", "Munchen"),
        ("20900 Monza, Via Liberta 3", "Monza"),
        ("6700 Arlon, Rue de la Gare 5", "Arlon")), 9):
    check(f"AL{_i} P60 real address kept: {_exp}",
          S._town_from_address(_t, "Acme"), _exp)
# ---- P60: bare postcode + a town the gazetteer knows is still fine ----
check("AL17 P60 bare postcode, known town",
      S._town_from_address("Sede: 20121 Milano", "Acme"), "Milano")
check("AL18 P60 bare postcode, unknown word refused",
      S._town_from_address("Sede: 20121 Hallo", "Acme"), None)
check("AL19 P60 address confidence downgraded to medium",
      S._location_confidence("Mechelen", "address"), "medium")
check("AL20 P60 a stated detail location still outranks it",
      S._location_confidence("Mechelen", "detail"), "high")
# ---- P61: town out of a randstad vacancy URL ----
_RB = "https://www.randstad.be/werknemers/jobs/"
check("AL21 P61 simple town",
      S._randstad_url_location(
          _RB + "magazijnier_roeselare_02857714-89ad-4d57-87f4-880788effac1"),
      "Roeselare")
check("AL22 P61 town the gazetteer does NOT know is still read",
      S._randstad_url_location(
          _RB + "testoperator_ardooie_a455d58e-6a1d-4f63-80a3-8a51a2a9cb45"),
      "Ardooie")
check("AL23 P61 Dutch connectors stay lowercase",
      S._randstad_url_location(
          _RB + "store-manager_mechelen-aan-de-maas_456b92a7-1111-2222-3333-444444444444"),
      "Mechelen-aan-de-Maas")
check("AL24 P61 hyphenated saint-name title-cased",
      S._randstad_url_location(
          _RB + "diensthoofd_sint-katelijne-waver_7393f7d6-1111-2222-3333-444444444444"),
      "Sint-Katelijne-Waver")
check("AL25 P61 a facet URL yields no town",
      S._randstad_url_location(_RB + "jt-tijdelijk"), "")
check("AL26 P61 non-randstad host is untouched",
      S._randstad_url_location(
          "https://example.com/jobs/dev_berlin_11111111-2222-3333-4444-555555555555"),
      "")
# The country must come from the national domain, NOT the gazetteer:
# the gazetteer resolves this Halle to Germany, but it is in Flemish Brabant.
check("AL27 P61 country from the .be domain",
      S._country_from_site(
          _RB + "technieker_halle_6a286fb7-7b57-4ecf-8443-806a24d0aaaa"),
      "Belgium")
check("AL28 P61 (the gazetteer would have said Germany)",
      S._country_of_location("Halle"), "Germany")
# ---- P62: facets rejected, vacancies kept, other hosts untouched ----
check("AL29 P62 job-type facet rejected",
      S.is_valid_job_url(_RB + "jt-tijdelijk"), False)
check("AL30 P62 sector facet rejected",
      S.is_valid_job_url(_RB + "s-productie/s2-machine-operatoren/r-extrudeur"),
      False)
check("AL31 P62 city facet rejected",
      S.is_valid_job_url(_RB + "re-oost-vlaanderen/ci-aalst"), False)
check("AL32 P62 company facet rejected",
      S.is_valid_job_url(_RB + "c-volvo-cars"), False)
check("AL33 P62 advice page rejected",
      S.is_valid_job_url("https://www.randstad.be/carriere/sollicitatietips"),
      False)
check("AL34 P62 register page rejected",
      S.is_valid_job_url("https://www.randstad.be/my-randstad/register"), False)
check("AL35 P62 a real vacancy is kept",
      S.is_valid_job_url(
          _RB + "magazijnier_roeselare_02857714-89ad-4d57-87f4-880788effac1"),
      True)
# The reason a generic <code>-<slug> rule was rejected:
check("AL36 P62 /jobs/hr-manager elsewhere still valid",
      S.is_valid_job_url("https://example.com/jobs/hr-manager"), True)
check("AL37 P62 /jobs/c-sharp-developer elsewhere still valid",
      S.is_valid_job_url("https://example.com/jobs/c-sharp-developer"), True)
check("AL38 P62 greenhouse untouched",
      S.is_valid_job_url("https://boards.greenhouse.io/acme/jobs/55512"), True)
check("AL39 P56 Dyson still valid after P62",
      S.is_valid_job_url(
          "http://careers.dyson.com/en-gb/job-description/data-analyst/JR38601/"),
      True)
truthy("AL40 P61/P62 are career-only",
       "_randstad_url_location" not in src_a)

# ---- P63: the JSON-LD reader skipped the commonest payload shape ----
_LD_BARE = ('<script type="application/ld+json">{"@type":"JobPosting",'
            '"jobLocation":{"address":{"addressLocality":"Hyderabad",'
            '"addressCountry":"IND"}}}</script>')
_LD_GRAPH = ('<script type="application/ld+json">{"@graph":[{"@type":'
             '"JobPosting","jobLocation":{"address":{"addressLocality":'
             '"Utrecht","addressCountry":"NL"}}}]}</script>')
_LD_LIST = ('<script type="application/ld+json">[{"@type":"JobPosting",'
            '"jobLocation":{"address":{"addressLocality":"Gent",'
            '"addressCountry":"BEL"}}}]</script>')
check("AL41 P63 BARE JobPosting is read (was silently skipped)",
      S._extract_ld_location_from_html(_LD_BARE), "Hyderabad, India")
check("AL42 P63 @graph payload still read",
      S._extract_ld_location_from_html(_LD_GRAPH), "Utrecht, NL")
check("AL43 P63 top-level list still read",
      S._extract_ld_location_from_html(_LD_LIST), "Gent, Belgium")
check("AL44 P63 ISO3 mapped via P49's table, not a second one",
      S._extract_ld_location_from_html(
          '<script type="application/ld+json">{"@type":"JobPosting",'
          '"jobLocation":{"address":{"addressLocality":"Milan",'
          '"addressCountry":"ITA"}}}</script>'), "Milan, Italy")
check("AL45 P63 a 2-letter code is left alone (not an ISO3)",
      S._extract_ld_location_from_html(
          '<script type="application/ld+json">{"@type":"JobPosting",'
          '"jobLocation":{"address":{"addressLocality":"Gent",'
          '"addressCountry":"BE"}}}</script>'), "Gent, BE")
check("AL46 P63 a non-JobPosting document yields nothing",
      S._extract_ld_location_from_html(
          '<script type="application/ld+json">{"@type":"Organization",'
          '"name":"Acme"}</script>'), "")
check("AL47 P63 malformed JSON is still survivable",
      S._extract_ld_location_from_html(
          '<script type="application/ld+json">{not json</script>'), "")
truthy("AL48 P63 fixed in BOTH ld readers (location and JD text)",
       src_c.count('items = data.get("@graph") or [data]') == 2)

# ══ GROUP AM — P64: country scoping is off by default ══
print("\n-- AM. P64 (no job dropped for being in the wrong country) --")
check("AM1 career default is OFF", S.config.SCOPE_FILTER, False)
check("AM2 ats default is OFF", ats.ATSScanner.SCOPE_FILTER, False)
_UK = {"name": "Barclays", "target_country": "United Kingdom",
       "scope_policy": "job_location", "careers_url": "https://x.com/c"}
for _i, _loc in enumerate(("Pune", "New York", "Mumbai", "Singapore",
                           "Budapest", "Boulogne-Billancourt, France",
                           "Unknown", "Beijing"), 3):
    check(f"AM{_i} kept under job_location: {_loc}",
          S._scope_allows(_UK, _loc, "", "https://x.com/c"), True)
_SU = dict(_UK, scope_policy="seed_url")
check("AM11 kept under seed_url too",
      S._scope_allows(_SU, "Hyderabad", "", "https://x.com/c"), True)
_GL = dict(_UK, target_country="Global", scope_policy="global")
check("AM12 global seeds unchanged",
      S._scope_allows(_GL, "Pune", "", "https://x.com/c"), True)
# Replay the REAL rejects from both shared batches.
import csv as _csv
_csv.field_size_limit(10**9)
_tot = _ok = 0
for _run in ("20261008T085006", "20261008T100433"):
    _f = f"/home/user/uploads/{_run}_career_jobs_quarantine.csv"
    if not os.path.exists(_f):
        continue
    for _r2 in _csv.DictReader(open(_f, encoding="utf-8-sig")):
        if _r2.get("Quarantine Reason") != "outside_or_unproven_target_country":
            continue
        _tot += 1
        if S._scope_allows({"target_country": _r2["Target Country"],
                            "scope_policy": _r2["Scope Policy"],
                            "careers_url": _r2["Job URL"],
                            "name": _r2["Company Name"]},
                           _r2["Job Location"] or "Unknown", "",
                           _r2["Job URL"]):
            _ok += 1
truthy(f"AM13 all {_tot} real country-rejects now pass scope",
       _tot > 2000 and _ok == _tot)
# The escape hatch must still work.
_prev = S.config.SCOPE_FILTER
try:
    S.config.SCOPE_FILTER = True
    check("AM14 SCOPE_FILTER=1 restores the old rejection",
          S._scope_allows(_UK, "Pune", "", "https://x.com/c"), False)
    check("AM15 ... while in-country still passes",
          S._scope_allows(_UK, "London", "", "https://x.com/c"), True)
finally:
    S.config.SCOPE_FILTER = _prev
check("AM16 restored to OFF after the test", S.config.SCOPE_FILTER, False)
# P64 must not have damaged the functions my bad anchor landed in earlier.
truthy("AM17 _provider_cache_path still returns a path",
       isinstance(S._provider_cache_path(), str))
truthy("AM18 ats _scope_confidence still returns a verdict string",
       isinstance(ats.ATSScanner._scope_confidence(
           ats.ATSScanner.__new__(ats.ATSScanner),
           {"target_country": "Italy", "scope_policy": "job_location"},
           "Milan"), str))
truthy("AM19 the switch is documented in both scanners",
       "SPONSORSCOUT_SCOPE_FILTER" in src_c
       and "SPONSORSCOUT_SCOPE_FILTER" in src_a)

print("\n-- AN. P65 / A1-A6 (final-build audit of run 20261008T113455) --")

check("AN1 detail scan on by default",
      car.ProductionScannerConfig.ENABLE_DETAIL_SCAN, True)
check("AN2 per-company cap covers the biggest board (GDIT 1228)",
      car.ProductionScannerConfig.MAX_DETAIL_SCAN_PER_COMPANY >= 1228, True)
check("AN3 global cap covers a full run",
      car.ProductionScannerConfig.MAX_DETAIL_SCAN_TOTAL >= 4700, True)
_prev_env = os.environ.get("SPONSORSCOUT_DETAIL_SCAN")
os.environ["SPONSORSCOUT_DETAIL_SCAN"] = "0"
try:
    _spec_d = importlib.util.spec_from_file_location("car_detail_off", CAREER)
    _m_d = importlib.util.module_from_spec(_spec_d)
    with contextlib.redirect_stdout(io.StringIO()):
        _spec_d.loader.exec_module(_m_d)
    check("AN4 SPONSORSCOUT_DETAIL_SCAN=0 restores the old behaviour",
          _m_d.ProductionScannerConfig.ENABLE_DETAIL_SCAN, False)
finally:
    if _prev_env is None:
        os.environ.pop("SPONSORSCOUT_DETAIL_SCAN", None)
    else:
        os.environ["SPONSORSCOUT_DETAIL_SCAN"] = _prev_env
check("AN5 detail switch is env-driven, not hardcoded",
      "SPONSORSCOUT_DETAIL_SCAN" in src_c, True)

_D = jds.JDSupportDetector()
def _vv(t, k="visa"): return _D.detect(t)[k]["verdict"]
def _cc(t, k="visa"): return _D.detect(t)[k]["confidence"]
_ETSY = ("While Etsy supports visa sponsorship, sponsorship opportunities "
         "may be limited to certain roles and skills.")
check("AN6 'eligible for visa sponsorship' is a Yes",
      _vv("This role is eligible for visa sponsorship."), "Yes")
check("AN7 conditional offer surfaces as Yes, not Unknown", _vv(_ETSY), "Yes")
check("AN8 ...and carries the hedge in its confidence", _cc(_ETSY) < 0.9, True)
check("AN9 'must be eligible to work' stays a requirement (no false Yes)",
      _vv("Candidates must be eligible to work in Germany."), "No")
check("AN10 'right to work' requirement unchanged",
      _vv("Applicants must have the right to work in the UK."), "No")
check("AN11 hard refusal unchanged",
      _vv("Please note: work visa sponsorship will not be provided for this position."), "No")
check("AN12 hedged negation stays Unknown",
      _vv("Visa sponsorship is not guaranteed."), "Unknown")
check("AN13 'move faster' is not a relocation offer",
      _vv("You use AI-assisted tools to move faster in your day-to-day, "
          "whether that's coding, testing, debugging, or documentation.",
          "relocation"), "Unknown")
check("AN14 relocating EQUIPMENT is not a relocation offer",
      _vv("Support logistics activities related to the acquisition, storage, "
          "relocation, and deployment of communications equipment.",
          "relocation"), "Unknown")
check("AN15 a genuine relocation offer still reads Yes",
      _vv("We offer a generous relocation package.", "relocation"), "Yes")
check("AN16 relocation offer + equipment mention still Yes",
      _vv("We offer relocation support and provide all the equipment you need.",
          "relocation"), "Yes")
check("AN17 'assist with your relocation' still Yes",
      _vv("We will assist with your relocation to Berlin.", "relocation"), "Yes")
check("AN18 IT 'trasferte' still not a relocation",
      _vv("Disponibilita a trasferte presso gli stabilimenti del Gruppo.",
          "relocation"), "Unknown")
_LONG = ("A rewarding Compensation package! Prime Plus membership, Competitive "
         "salary and benefits package, including flexible benefits, birthday "
         "day off, and a relocation package to help you move to Barcelona.")
check("AN19 evidence keeps the words that justify the verdict",
      "relocation package" in _D.best_evidence(_D.detect(_LONG)["relocation"]), True)
check("AN20 blue card still fires in English", jds.detect_blue_card(
      _D, "We support EU Blue Card applications for this Berlin role."), "Yes")
check("AN21 blue card still fires in German", jds.detect_blue_card(
      _D, "Wir unterstuetzen die Beantragung der Blauen Karte EU."), "Yes")

check("AN22 rejects the greenhouse LOGIN page",
      S.is_valid_job_url("https://my.greenhouse.io/users/sign_in"), False)
check("AN23 rejects the Elastic values page",
      S.is_valid_job_url("https://www.elastic.co/careers/our-values"), False)
check("AN24 rejects /company-details",
      S.is_valid_job_url("https://www.skyscanner.co.in/company-details"), False)
check("AN25 rejects /candidate_details",
      S.is_valid_job_url("https://careers.etsy.com/v1/candidate_details"), False)
check("AN26 Workday field label stripped from the title",
      S.clean_job_title("Job Posting Title Information Systems Security Officer"),
      "Information Systems Security Officer")
check("AN27 a real 'Job Posting Specialist' role is untouched",
      S.clean_job_title("Job Posting Specialist"), "Job Posting Specialist")
check("AN28 a genuine greenhouse job URL still passes",
      S.is_valid_job_url("https://boards.greenhouse.io/acme/jobs/4567890"), True)

truthy("AN29 cross-seed URL dedupe exists",
       hasattr(S, "_dedupe_same_url_across_seeds"))
check("AN30 ...and is wired into the run",
      src_c.count("_dedupe_same_url_across_seeds(") >= 2, True)
truthy("AN31 stale-DNS-error retraction exists",
       hasattr(S, "_clear_recovered_dns_errors"))
check("AN32 ...and runs after the retry sweep",
      "self._clear_recovered_dns_errors(scan_log_csv)" in src_c, True)

print("\n-- AO. P66 / B1-B5 (batch check of run 20261008T202403) --")

# ---- B1: the JD reader must read the page BODY --------------------------
check("AO1 body-text floor is configured",
      car.ProductionScannerConfig.DETAIL_BODY_MIN_CHARS >= 200, True)
check("AO2 body floor is env-overridable",
      "SPONSORSCOUT_DETAIL_BODY_MIN" in src_c, True)
check("AO3 reader drops nav/header/footer before reading",
      all(t in src_c for t in ("_SCRIPT_STYLE_NAV_RE", "_CONTENT_CONTAINER_RES")), True)
check("AO4 reader prefers a content container over bare <body>",
      "<main" in src_c and "job[-_]?desc" in src_c, True)
check("AO5 career imports normalize_jd_text (block-aware HTML)",
      "normalize_jd_text," in src_c, True)
check("AO6 the naive tag-strip is gone from the JSON-LD description path",
      'desc = re.sub(r"<[^>]+>", " ", str(d))' in src_c, False)
_html_asml = ("<html><head><title>x</title></head><body><nav>Home Careers Contact</nav>"
              "<main><h1>Senior Engineer</h1><ul><li>Five years experience</li>"
              "<li>You must be able to work without sponsorship</li></ul>"
              + ("<p>Filler sentence about the role and the team. </p>" * 30) +
              "</main><footer>Privacy</footer></body></html>")
_d, _l, _o = S._parse_detail_html(_html_asml) if hasattr(S, "_parse_detail_html") else (None, None, None)
check("AO7 a page with no JSON-LD and no og:description still yields prose",
      bool(_d) and len(_d) > 1200 if _d is not None else "skipped", True)
check("AO8 ...and the body text keeps the deciding clause",
      ("without sponsorship" in _d) if _d else "skipped", True)
check("AO9 ...and navigation is not part of it",
      ("Privacy" not in _d) if _d else "skipped", True)
_shell = "<html><body><div id='root'></div><script>app()</script></body></html>"
_ds, _, _ = S._parse_detail_html(_shell) if hasattr(S, "_parse_detail_html") else ("", "", "")
check("AO10 a client-rendered shell is NOT mistaken for a JD",
      len(_ds or ""), 0)

# ---- B2: evidence must quote the clause that decided the verdict --------
_D2 = jds.JDSupportDetector()
_MEGA = ("We offer competitive compensation as well as thoughtful, valuable, and even fun "
         "benefits which include: Medical, life, and disability insurance Annual paid time "
         "off and generous paid leave scheme including: parent, grandparent, bereavement, "
         "sick, and care leave Industry leading product discounts for yourself, friends, "
         "and family, including automatic Genius Level 3 status Free access to online "
         "learning platforms, mentorship programs, and a rich catalogue of soft skills "
         "training Hybrid working including flexible working arrangements, and up to 20 "
         "days per year working from abroad Referral bonus scheme "
         "This role does not come with relocation and visa sponsorship assistance.")
_evm = _D2.best_evidence(_D2.detect(_MEGA)["visa"])
check("AO11 verdict on the Booking.com mega-sentence is still No",
      _D2.detect(_MEGA)["visa"]["verdict"], "No")
check("AO12 the quote now contains the deciding clause",
      "visa sponsorship assistance" in _evm, True)
check("AO13 ...and no longer leads with the benefits list",
      _evm.strip().startswith("We offer competitive compensation"), False)
check("AO14 a short sentence is quoted whole, unmarked",
      _D2.best_evidence(_D2.detect("We offer visa sponsorship.")["visa"]),
      "We offer visa sponsorship.")
check("AO15 evidence window helper exists",
      hasattr(jds.JDSupportDetector, "_evidence_window"), True)

# ---- B3: the Dutch highly-skilled-migrant route -------------------------
def _vv2(t, k="visa"): return _D2.detect(t)[k]["verdict"]
check("AO16 kennismigrant is a visa concept",
      _vv2("We will sponsor your kennismigrant permit."), "Yes")
check("AO17 kennismigrantenprocedure (inflected) too",
      _vv2("Wij regelen de kennismigrantenprocedure voor je."), "Yes")
check("AO18 kennismigrant is NOT reported as an EU Blue Card",
      jds.detect_blue_card(_D2, "Wij regelen de kennismigrantenprocedure."), "Unknown")
check("AO19 a real Blue Card is still a Blue Card",
      jds.detect_blue_card(_D2, "We support EU Blue Card applications."), "Yes")
check("AO20 the 30% ruling is a relocation concept",
      "30" in jds.JDSupportDetector.RELOCATION_CONCEPTS.pattern, True)

# ---- B4/B5: titles that are not jobs ------------------------------------
for _i, _t in enumerate(["September 7, 2026James", "August 31, 2026James",
                         "July 28, 2026publisher", "September 2, 2026"], 21):
    check(f"AO{_i} rejects date/byline title {_t[:22]!r}",
          S.is_valid_job_title(_t), False)
check("AO25 rejects 'Skip the line'", S.is_valid_job_title("Skip the line"), False)
check("AO26 a real hiphoptune job survives",
      S.is_valid_job_title("Customer Service Representative-Remote"), True)
check("AO27 a title that merely CONTAINS a month survives",
      S.is_valid_job_title("Senior Engineer, May 2026 start"), True)
check("AO28 a bare month word does not trigger the date rule",
      S.is_valid_job_title("March Analyst"), True)
check("AO29 the date rule needs an actual date, not just a month",
      bool(car.CareerPortalScanner._DATE_BYLINE_TITLE_RE.match(
          "May Day Events Coordinator")), False)
# NB "May Day Events Coordinator" and "May Project Manager" are rejected by
# is_valid_job_title, but that is PRE-EXISTING behaviour -- verified identical
# on the pre-P66 build -- and nothing to do with the date/byline rule.

# ---- B4/B5 parity: the ATS scanner must reject the same junk ----------
_Aao = ats.ATSScanner(seed_file="/dev/null",
                      output_file="/tmp/rev/ats_ao.csv", skip_preflight=True)
check("AO30 ATS rejects a date/byline title",
      _Aao._is_nav_title("September 7, 2026James"), True)
check("AO31 ATS rejects 'July 28, 2026publisher'",
      _Aao._is_nav_title("July 28, 2026publisher"), True)
check("AO32 ATS rejects 'Skip the line'", _Aao._is_nav_title("Skip the line"), True)
check("AO33 ATS keeps a real role", _Aao._is_nav_title("Senior Data Engineer"), False)
check("AO34 ATS keeps a title containing a month",
      _Aao._is_nav_title("Engineer, May 2026 cohort"), False)

print("\n-- AP. A4/A5/A6 exercised against real data (verification gap) --")
# These three P65 patches shipped without ever running against real inputs:
# no two seeds shared a board, none of the A4 companies were in the batch,
# and nothing failed DNS. This group closes that gap.
import csv, shutil as _sh, glob as _gl, collections as _co
csv.field_size_limit(10**9)

# ---- A6: recovered-DNS retraction --------------------------------------
_A6DIR = "/tmp/rev/a6"; os.makedirs(_A6DIR, exist_ok=True)
_DNS = "Host does not resolve (DNS)"
_LOGCOLS = ["Run ID", "Seed Name", "Company", "Source Type", "Target Country",
            "Status", "Provider", "Jobs Found", "Quarantined", "Duplicates",
            "Rejected Scope", "Error", "Diagnostics", "Duration Sec", "Seed URL"]

def _a6(run, errs, logs):
    ep = _A6DIR + "/err.csv"; lp = _A6DIR + "/log.csv"
    with io.open(ep, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=car._ERROR_COLUMNS); w.writeheader()
        for e in errs:
            w.writerow({k: e.get(k, "") for k in car._ERROR_COLUMNS})
    with io.open(lp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f); w.writerow(_LOGCOLS)
        for l in logs:
            w.writerow(l)
    _oe, _or = getattr(S, "_errors_csv", None), getattr(S, "run_id", None)
    try:
        S._errors_csv = ep; S.run_id = run
        with contextlib.redirect_stdout(io.StringIO()):
            S._clear_recovered_dns_errors(lp)
        with io.open(ep, encoding="utf-8-sig", newline="") as f:
            return len(list(csv.DictReader(f)))
    finally:
        S._errors_csv = _oe; S.run_id = _or

_E = lambda n, m: {"Run ID": "R1", "Seed Name": n, "Phase": "target",
                   "Error Type": "RuntimeError", "Message": m, "Seed URL": "u"}
_OK = ["R1", "Acme", "Acme", "direct_employer", "Global", "ok", "auto",
       "12", "0", "0", "0", "", "", 1.0, "u"]
_BAD = ["R1", "Acme", "Acme", "direct_employer", "Global", "error", "auto",
        "0", "0", "0", "0", _DNS, "", 1.0, "u"]
check("AP1 a recovered company's DNS error is retracted",
      _a6("R1", [_E("Acme", _DNS)], [_OK]), 0)
check("AP2 a still-failing company keeps its DNS error",
      _a6("R1", [_E("Acme", _DNS)], [_BAD]), 1)
check("AP3 an EARLIER run's error is never touched",
      _a6("R2", [_E("Acme", _DNS)], [["R2"] + _OK[1:]]), 1)
check("AP4 a non-DNS error is never retracted",
      _a6("R1", [_E("Acme", "HTTP 403 Forbidden")], [_OK]), 1)
# FIX P67: recovery is now registered under BOTH the scan log's Company and
# its Seed Name, because the errors file keys on Seed Name alone. Before the
# fix a seed named "Ikea Italia" resolving to company "IKEA" stayed accused
# after recovering. Latent until a seed name diverged -- these pin it shut.
check("AP5 seed-name/company-name mismatch still retracts (P67)",
      _a6("R1", [_E("Ikea Italia", _DNS)],
          [["R1", "Ikea Italia", "IKEA", "direct_employer", "Italy", "ok",
            "auto", "40", "0", "0", "0", "", "", 1.0, "u"]]), 0)
check("AP5b ...and a MISMATCHED company that is still failing is NOT retracted",
      _a6("R1", [_E("Ikea Italia", _DNS)],
          [["R1", "Ikea Italia", "IKEA", "direct_employer", "Italy", "error",
            "auto", "0", "0", "0", "0", _DNS, "", 1.0, "u"]]), 1)
check("AP5c ...matching on Company alone still works (seed name absent)",
      _a6("R1", [_E("Acme", _DNS)],
          [["R1", "", "Acme", "direct_employer", "Global", "ok", "auto",
            "9", "0", "0", "0", "", "", 1.0, "u"]]), 0)
check("AP5d ...an unrelated seed's DNS error is never collaterally retracted",
      _a6("R1", [_E("Other Co", _DNS)],
          [["R1", "Ikea Italia", "IKEA", "direct_employer", "Italy", "ok",
            "auto", "40", "0", "0", "0", "", "", 1.0, "u"]]), 1)

_UP = "/home/user/uploads"
_real = {}
for _ep in sorted(_gl.glob(_UP + "/*errors*.csv")):
    _lp = _ep.replace("_errors.csv", "_scan_log.csv")
    if not os.path.exists(_lp):
        continue
    with io.open(_ep, encoding="utf-8-sig", newline="") as f:
        _rows = list(csv.DictReader(f))
    _dns = [r for r in _rows if "does not resolve (DNS)" in (r.get("Message") or "")]
    if not _dns:
        continue
    _tmp = _A6DIR + "/replay.csv"; _sh.copy(_ep, _tmp)
    _oe, _or2 = getattr(S, "_errors_csv", None), getattr(S, "run_id", None)
    _n = 0
    try:
        S._errors_csv = _tmp
        for _rid in sorted({r.get("Run ID") for r in _dns if r.get("Run ID")}):
            S.run_id = _rid
            with io.open(_tmp, encoding="utf-8-sig", newline="") as f:
                _b = len(list(csv.DictReader(f)))
            with contextlib.redirect_stdout(io.StringIO()):
                S._clear_recovered_dns_errors(_lp)
            with io.open(_tmp, encoding="utf-8-sig", newline="") as f:
                _n += _b - len(list(csv.DictReader(f)))
    finally:
        S._errors_csv = _oe; S.run_id = _or2
    _real[os.path.basename(_ep)[:15]] = (len(_dns), _n)
check("AP6 real runs with DNS errors were found to replay",
      len(_real) >= 5, True)
check("AP7 run 20261008T113455: its one stale DNS error IS retracted",
      _real.get("20261008T113455", (0, 0))[1], 1)
check("AP8 run 20261008T100433: its one stale DNS error IS retracted",
      _real.get("20261008T100433", (0, 0))[1], 1)
check("AP9 genuinely dead hosts keep their errors (Cigierre/CIR Food)",
      _real.get("20261006T225923", (2, 0))[1], 0)
check("AP10 Camst Group keeps its error",
      _real.get("20261007T174338", (1, 0))[1], 0)

# ---- A5: cross-seed duplicate URL --------------------------------------
with io.open(_UP + "/20261008T202403_career_jobs.csv", encoding="utf-8-sig",
             newline="") as f:
    _JCOLS = list(csv.DictReader(f).fieldnames)
_A5DIR = "/tmp/rev/a5"; os.makedirs(_A5DIR, exist_ok=True)

def _a5(rows):
    p = _A5DIR + "/j.csv"
    with io.open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_JCOLS); w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in _JCOLS})
    with contextlib.redirect_stdout(io.StringIO()):
        n = S._dedupe_same_url_across_seeds(p, _A5DIR + "/q.csv", _JCOLS)
    with io.open(p, encoding="utf-8-sig", newline="") as f:
        return n, list(csv.DictReader(f))

def _jr(co, u, loc=""):
    d = {k: "" for k in _JCOLS}
    d.update({"Company Name": co, "Job URL": u, "Job Title": "Engineer",
              "Job Location": loc or "Unknown"})
    return d

_U = "https://tenthrevolution.com/jobs/servicenow-cta-123"
_n5, _k5 = _a5([_jr("Nigel Frank", _U, "London"),
                _jr("Tenth Revolution Group", _U)])
check("AP11 two seeds sharing a URL collapse to one row", (_n5, len(_k5)), (1, 1))
check("AP12 ...and the row with a real location is the survivor",
      _k5[0]["Job Location"], "London")
check("AP13 distinct URLs under one seed are untouched",
      _a5([_jr("A", _U), _jr("A", _U + "x"), _jr("A", _U + "y")])[0], 0)
check("AP14 the SAME seed twice is left alone (by design)",
      _a5([_jr("A", _U), _jr("A", _U)])[0], 0)

with io.open(_UP + "/20261008T113455_career_jobs.csv", encoding="utf-8-sig",
             newline="") as f:
    _shipped = list(csv.DictReader(f))
_by = _co.defaultdict(set)
for _r in _shipped:
    if _r["Job URL"].startswith("http"):
        _by[_r["Job URL"].casefold()].add(_r["Company Name"])
_xs = {u for u, c in _by.items() if len(c) > 1}
check("AP15 the shipped run really did contain cross-seed duplicates",
      len(_xs), 2)
_n15, _k15 = _a5([r for r in _shipped if r["Job URL"].casefold() in _xs])
_by2 = _co.defaultdict(set)
for _r in _k15:
    _by2[_r["Job URL"].casefold()].add(_r["Company Name"])
check("AP16 replaying A5 over them leaves zero cross-seed duplicates",
      sum(1 for _u, _c in _by2.items() if len(_c) > 1), 0)

# ---- A4: junk URLs and label-prefixed titles ---------------------------
for _i, (_u, _want) in enumerate([
        ("https://x.com/account/settings", False),
        ("https://x.com/users/sign_in", False),
        ("https://x.com/en/about-us/our-story", False),
        ("https://x.com/careers/our-values", False),
        ("https://x.com/company-details", False),
        ("https://x.com/careers/job/12345", True),
        ("https://x.com/jobs/accountant-ii", True)], 17):
    check(f"AP{_i} is_valid_job_url {_u.split('.com')[1][:30]!r}",
          S.is_valid_job_url(_u), _want)
check("AP24 'Job Title:' prefix is stripped",
      S.clean_job_title("Job Title: Senior Engineer"), "Senior Engineer")
check("AP25 'Functietitel' prefix is stripped",
      S.clean_job_title("Functietitel: Data Analist"), "Data Analist")
# GAP CLOSED by P69: the regex used to require the word "title"/"titel", so a
# bare "Position:" label survived. P69 adds _FIELD_LABEL_BARE_RE with a
# MANDATORY separator, so this now strips -- see AQ25-AQ36 for the cases it
# must still leave alone ("Role-Play Therapist", "Position Trader").
check("AP26 [P69] a bare 'Position:' label IS now stripped",
      S.clean_job_title("Position: Data Analyst"), "Data Analyst")
_allr = []
for _R in ("20261008T113455", "20261008T202403"):
    for _p in (_UP + f"/{_R}_career_jobs.csv",
               _UP + f"/{_R}_career_jobs_recruiter.csv"):
        if os.path.exists(_p):
            with io.open(_p, encoding="utf-8-sig", newline="") as f:
                _allr += list(csv.DictReader(f))
_bad = [r for r in _allr
        if r["Job URL"].startswith("http") and not S.is_valid_job_url(r["Job URL"])]
check("AP27 replaying A4 over 6,470 shipped rows rejects the known junk",
      len(_bad), 6)
# The six: Skyscanner /company-details, SumUp + Dremio
# my.greenhouse.io/users/sign_in, Elastic Finance + Elastic Sales
# /careers/our-values, Etsy /v1/candidate_details.
check("AP28 ...and every one of them is a sign-in/values/details page",
      sorted({r["Job URL"].rsplit("/", 1)[-1] for r in _bad}),
      ["candidate_details", "company-details", "our-values", "sign_in"])
check("AP29 ...spanning 6 rows across 6 distinct seeds",
      len({r["Company Name"] for r in _bad}), 6)

print("\n-- AQ. P68 Avature adapter + P69 bare field-label prefix --\n")

# ── AQ. P68 Avature adapter + P69 bare field-label prefix ─────────────────
# P68: Deloitte Digital's 229 rows in run 20261008T085006 were ALL quarantined
# `outside_or_unproven_target_country` (228 with Location Source `none`) because
# generic DOM scraping took each row's "Apply" button
# (.../ApplicationMethods?jobId=N) and never read the
# <strong>Location:</strong><span> sitting in the same <tr>. Verified live
# 2026-10-08: 238 jobs in 14 pages, 238/238 located. Everything below is
# OFFLINE -- same code, fixture markup, monkeypatched HTTP -- so the battery
# stays deterministic and network-free.
import csv as _aq_csv
import glob as _aq_glob
import io as _aq_io
import os as _aq_os
from html import unescape as _aq_unescape
from urllib.parse import urlparse as _aq_urlparse
_aq_csv.field_size_limit(10 ** 9)

_FIX = '''<table>
<tr class="card--box ">
  <td scope="row">
    <h3 class="article__header__text__title">
      <a href="https://deloittebe.avature.net/en_US/careers/JobDetail/SAP-VIM-Senior-Consultant/13024" class="t--02">
        SAP VIM (Senior) Consultant
      </a>
    </h3>
    <div class="article__header__text__subtitle__list">
      <strong>Field of Interest:</strong> <span>Technology</span> |
      <strong>Industry:</strong> <span>Industry Agnostic</span> |
      <strong>Location:</strong> <span>Zaventem</span>
    </div>
    <div class="article__header__text__subtitle__card toggle--content">
      <p><strong>Field of Interest</strong><span>Technology</span></p>
      <p><strong>Industry</strong><span>Industry Agnostic</span></p>
      <p><strong>Location</strong><span>Zaventem</span></p>
    </div>
  </td>
  <td class="td__actions">
    <a class="button button--primary"
       href="https://deloittebe.avature.net/en_US/careers/ApplicationMethods?jobId=13024">Apply</a>
  </td>
</tr>
</table>
<nav><a class="list-controls__pagination__item paginationNextLink"
   href="https://deloittebe.avature.net/en_US/careers/SearchJobs/?3_67_3=5882&amp;jobOffset=18"
   aria-label="Go to Next Page, Number 2">Next &gt;&gt;</a></nav>'''

_AVSEED = {"name": "Deloitte Digital", "provider": "auto", "board_slug": "",
           "careers_url": "https://deloittebe.avature.net/en_US/careers/"
                          "SearchJobs/?1764=5882#",
           "source_type": "direct_employer", "target_country": "Belgium",
           "scope_policy": "job_location"}

_C = car.CareerPortalScanner
check("AQ1 the Avature adapter exists", callable(
    getattr(S, "_fetch_avature_jobs", None)), True)

_rows = _C._AVATURE_ROW_RE.findall(_FIX)
check("AQ2 a card--box row is recognised", len(_rows), 1)
_m = _C._AVATURE_TITLE_RE.search(_rows[0])
check("AQ3 the title link is the canonical JobDetail page",
      "/careers/JobDetail/" in _m.group(1), True)
check("AQ4 ...and the title text is clean",
      S._avature_text(_m.group(2)), "SAP VIM (Senior) Consultant")
check("AQ5 the list variant 'Location:' is read",
      S._avature_text(_C._AVATURE_LOC_RE.search(_rows[0]).group(1)), "Zaventem")
check("AQ6 the card variant 'Location' (no colon) is read too",
      len(_C._AVATURE_LOC_RE.findall(_rows[0])), 2)
check("AQ7 the Apply button is NOT taken for the job URL",
      "ApplicationMethods" in _m.group(1), False)
_nx = _C._AVATURE_NEXT_RE.search(_FIX)
check("AQ8 the next-page link is found", bool(_nx), True)
check("AQ9 ...and its HTML entities are decoded before it is followed",
      _aq_unescape(_nx.group(1)).count("&amp;"), 0)
check("AQ10 _avature_text strips tags and decodes entities",
      S._avature_text("<span>A &amp; B</span>"), "A & B")

# ---- the whole adapter against the fixture, HTTP monkeypatched ----
_saved_http = S._http_fetch_with_retry
_calls = []


def _fake_http(url, headers, parse_json=True, post_body=None):
    _calls.append(url)
    return _FIX


S._http_fetch_with_retry = _fake_http
try:
    _jobs, _diag = S._fetch_avature_jobs(dict(_AVSEED))
    check("AQ11 the adapter yields the row", len(_jobs), 1)
    _j = _jobs[0]
    check("AQ12 location_hint is populated", _j["location_hint"], "Zaventem")
    check("AQ13 extraction_method is avature_html",
          _j["extraction_method"], "avature_html")
    check("AQ14 job_url is the JobDetail page", "/JobDetail/" in _j["job_url"], True)
    check("AQ15 card_context lists each field ONCE (the row repeats them)",
          _j["card_context"],
          "Field of Interest: Technology | Industry: Industry Agnostic")
    # the fixture's next link serves the same job id again: de-duplicate, and
    # stop rather than loop forever
    check("AQ16 following the next page does not duplicate a job", len(_jobs), 1)
    check("AQ17 ...and pagination terminated on its own", len(_calls), 2)

    # ---- dispatch ----
    _saved_adapter = S._fetch_avature_jobs
    _hits = []

    def _spy(target_row):
        _hits.append(target_row.get("provider") or "auto")
        return ([{"job_title": "X", "job_url": "https://x/JobDetail/x/1",
                  "location_hint": "Zaventem", "card_context": "",
                  "extraction_method": "avature_html"}],
                "avature HTML: 1 in 1 page(s)")

    S._fetch_avature_jobs = _spy
    try:
        _a, _d = S._fetch_provider_jobs(dict(_AVSEED, provider="avature"))
        check("AQ18 provider=avature dispatches to the adapter", _hits, ["avature"])
        check("AQ19 ...and returns its rows", _d, "avature HTML: 1 in 1 page(s)")
        del _hits[:]
        _a, _d = S._fetch_provider_jobs(dict(_AVSEED, provider="auto"))
        check("AQ20 provider=auto is routed by HOST, no seed edit needed",
              _hits, ["auto"])
        del _hits[:]
        _a, _d = S._fetch_provider_jobs(
            dict(_AVSEED, provider="auto",
                 careers_url="https://careers.example.com/jobs"))
        check("AQ21 a non-Avature host does NOT reach the adapter", _hits, [])

        # Zero rows must NOT be reported as a hard failure: the caller degrades
        # to the static/browser path only when the adapter yields nothing, so
        # returning [] is what keeps the company's jobs from vanishing.
        del _hits[:]

        def _empty(target_row):
            _hits.append(target_row.get("_provider_note"))
            return [], "avature HTML: 0 in 1 page(s)"

        S._fetch_avature_jobs = _empty
        _a, _d = S._fetch_provider_jobs(dict(_AVSEED, provider="avature"))
        check("AQ22 an empty Avature board yields no rows...", _a, [])
        check("AQ23 ...but carries the avature diagnostic into the scan log",
              "avature HTML: 0 in 1 page(s)" in (_d or ""), True)
    finally:
        S._fetch_avature_jobs = _saved_adapter

    # ---- the page cap ----
    del _calls[:]
    _aq_os.environ["SPONSORSCOUT_AVATURE_PAGES"] = "1"
    try:
        _jobs2, _diag2 = S._fetch_avature_jobs(dict(_AVSEED))
        check("AQ24 SPONSORSCOUT_AVATURE_PAGES=1 fetches exactly one page",
              len(_calls), 1)
    finally:
        _aq_os.environ.pop("SPONSORSCOUT_AVATURE_PAGES", None)
finally:
    S._http_fetch_with_retry = _saved_http

# ---- P69: bare field-label prefix, separator mandatory ----
_aq_strip = [("Position: Software Engineer", "Software Engineer"),
             ("Role: Data Analyst", "Data Analyst"),
             ("Position - Senior Consultant", "Senior Consultant"),
             ("Role \u2013 Product Owner", "Product Owner"),
             ("Vacancy: Warehouse Operative", "Warehouse Operative"),
             ("Functie: Monteur", "Monteur")]
for _i, (_src, _want) in enumerate(_aq_strip):
    check(f"AQ{25 + _i} P69 strips the bare label {_src!r}",
          S.clean_job_title(_src), _want)

_aq_keep = [("Role-Play Therapist", "Role-Play Therapist"),
            ("Position Trader", "Position Trader"),
            ("Position Analysis Manager", "Position Analysis Manager"),
            ("Role Modeling Expert", "Role Modeling Expert"),
            ("Vacancy Coordinator", "Vacancy Coordinator"),
            ("Job Posting Specialist", "Job Posting Specialist")]
for _i, (_src, _want) in enumerate(_aq_keep):
    check(f"AQ{31 + _i} P69 leaves {_src!r} alone",
          S.clean_job_title(_src), _want)

# the guard must not have widened onto real data
_aq_titles = []
for _f in sorted(_aq_glob.glob(_UP + "/*career_jobs*.csv"))[:6]:
    try:
        with _aq_io.open(_f, encoding="utf-8-sig", newline="") as _fh:
            for _r in _aq_csv.DictReader(_fh):
                for _c in ("Job Title", "Raw Job Title"):
                    _t = (_r.get(_c) or "").strip()
                    if _t:
                        _aq_titles.append(_t)
    except Exception:
        pass
    if len(_aq_titles) >= 20000:
        break
check("AQ37 P69 matches none of the real shipped titles",
      sum(1 for _t in _aq_titles
          if _C._FIELD_LABEL_BARE_RE.match(_t)), 0)
check("AQ38 ...and the real corpus was actually read", len(_aq_titles) > 1000, True)


# ---- P70: a job-detail slug containing a marketing word is a real job ----
# Deloitte Digital's "Pricing & Commercial Strategy & Analytics (Senior)
# Consultant" is served as
#   /en_US/careers/JobDetail/Pricing-Commercial-Strategy-Analytics-Senior-Consultant/10509
# and URL_EXCLUSION_PATTERN rejected it for the "/Pricing" segment -- one real
# vacancy lost out of 238. The exemption needs BOTH a detail marker and a
# trailing numeric job id.
_DEL = ("https://deloittebe.avature.net/en_US/careers/JobDetail/"
        "Pricing-Commercial-Strategy-Analytics-Senior-Consultant/10509")
check("AQ39 P70 rescues the Deloitte Pricing-... vacancy",
      S.is_valid_job_url(_DEL), True)
check("AQ40 ...and the exemption pattern itself matches that path",
      bool(car.ProductionScannerConfig._JOB_DETAIL_WITH_ID_RE.search(
          _aq_urlparse(_DEL).path)), True)
_aq_mkt = ["https://x.com/pricing", "https://x.com/blog", "https://x.com/values",
           "https://x.com/careers/our-values", "https://x.com/about",
           "https://x.com/privacy", "https://x.com/careers/pricing",
           "https://elastic.co/careers/our-values",
           "https://x.com/jobs/pricing-analyst",
           "https://x.com/careers/pricing/12",
           "https://x.com/jobdetail/pricing"]
check("AQ41 P70 lets NO marketing page back in (%d probes)" % len(_aq_mkt),
      sum(1 for _u in _aq_mkt if S.is_valid_job_url(_u)), 0)
check("AQ42 a detail marker with a 1-digit id is not enough",
      S.is_valid_job_url("https://x.com/JobDetail/Values-Champion/1"), False)
check("AQ43 a detail marker with no id at all is not enough",
      S.is_valid_job_url("https://x.com/JobDetail/Pricing/"), False)
check("AQ44 the marker must start a path segment",
      S.is_valid_job_url("https://x.com/pricing/JobDetail/10509"), False)
check("AQ45 other detail vocabularies are covered too",
      all(S.is_valid_job_url(u) for u in (
          "https://x.com/careers/job-detail/Blog-Editor/999",
          "https://x.com/stellenangebot/Pricing-Experte/4711")), True)
check("AQ46 P70 touches only the marketing blacklist, not the asset rule",
      S.is_valid_job_url("https://x.com/JobDetail/Pricing-Manager/10509.pdf"),
      False)

print("\n" + "=" * 60)
print(f"PASS {len(PASS)}   FAIL {len(FAIL)}")
if FAIL:
    print("failed:", ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
