import csv, glob, io, os, re, sys
from collections import defaultdict, Counter

csv.field_size_limit(10 ** 9)
# Repaired 2026-10-15 after the workspace cleanup: this script mined the scan
# output that used to live in /home/user/uploads, which was deleted. Point it at
# a directory of fresh scan output (run CSVs) either as argv[1] or via
# SPONSORSCOUT_RUNS. With no such directory it exits with a clear message rather
# than silently writing an empty seed.
UP = (sys.argv[1] if len(sys.argv) > 1
      else os.environ.get("SPONSORSCOUT_RUNS", "/home/user/runs"))
SEED = os.environ.get("SPONSORSCOUT_SEED", "/home/user/seeds/company_Career_seed.csv")
OUT_DIR = os.environ.get("SPONSORSCOUT_OUT", "/home/user/seeds")
OUT = os.path.join(OUT_DIR, "test_bad_portals_Career_seed.csv")
REPORT = os.path.join(OUT_DIR, "BAD_PORTALS.md")

if not os.path.isdir(UP):
    sys.exit("no scan-output directory at %r\n"
             "usage: python3 make_bad_portals_seed.py <dir with *_scan_log.csv / "
             "*_errors.csv / *_career_jobs*.csv>\n"
             "   or: SPONSORSCOUT_RUNS=<dir> python3 make_bad_portals_seed.py" % UP)


def rd(p):
    try:
        with io.open(p, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


# ---------- raw seed lines, so rows stay byte-identical ----------
raw = io.open(SEED, encoding="utf-8-sig", newline="").read()
raw = raw.replace("\r\n", "\n")
lines = raw.split("\n")
header_line = lines[0]
NCOL = len(next(csv.reader([header_line])))
raw_by_name, order = {}, []
n_data = n_banner = 0
for ln in lines[1:]:
    if not ln.strip():
        continue
    if ln.lstrip().startswith("#"):
        n_banner += 1
        continue
    name = next(csv.reader([ln]))[0].strip()
    raw_by_name[name] = ln
    order.append(name)
    n_data += 1
print("seed: %d cols, %d data rows, %d banner rows" % (NCOL, n_data, n_banner))

# ---------- evidence ----------
per = defaultdict(lambda: {"runs": set(), "found": 0, "quar": 0, "err": set()})
for p in sorted(glob.glob(UP + "/*_scan_log.csv")):
    for r in rd(p):
        n = (r.get("Seed Name") or "").strip()
        if not n or n.startswith("#"):
            continue
        d = per[n]
        d["runs"].add(r.get("Run ID"))
        try:
            d["found"] += int(float(r.get("Jobs Found") or 0))
            d["quar"] += int(float(r.get("Quarantined") or 0))
        except Exception:
            pass
        if (r.get("Error") or "").strip():
            d["err"].add((r.get("Error") or "").strip())

err = defaultdict(set)
for p in sorted(glob.glob(UP + "/*_errors.csv")):
    for r in rd(p):
        n = (r.get("Seed Name") or "").strip()
        if n:
            err[n].add((r.get("Message") or r.get("Error Type") or "").strip())

rows = defaultdict(lambda: {"n": 0, "loc": 0, "quar": 0, "qreason": Counter(),
                            "lsrc": Counter(), "junkurl": 0, "junktitle": 0,
                            "meth": Counter()})
JUNK_URL = re.compile(r"(sign_in|our-values|company-details|candidate_details|create-account)", re.I)
JUNK_TITLE = re.compile(r"^(\d{1,2}\s+\w+\s+\d{4}|skip\s+(the\s+)?line)", re.I)
for p in sorted(glob.glob(UP + "/*_career_jobs*.csv")):
    if p.endswith(("_scan_log.csv", "_errors.csv")):
        continue
    for r in rd(p):
        n = (r.get("Seed Name") or r.get("Company Name") or "").strip()
        if not n or n.startswith("#"):
            continue
        d = rows[n]
        d["n"] += 1
        jl = (r.get("Job Location") or "").strip()
        if jl and jl.lower() not in ("unknown", "n/a", "-"):
            d["loc"] += 1
        if (r.get("Record Status") or "").strip().lower() == "quarantine":
            d["quar"] += 1
            d["qreason"][(r.get("Quarantine Reason") or "").strip()] += 1
        d["lsrc"][(r.get("Location Source") or "(blank)").strip()] += 1
        d["meth"][(r.get("Extraction Method") or "(blank)").strip()] += 1
        if JUNK_URL.search(r.get("Job URL") or ""):
            d["junkurl"] += 1
        if JUNK_TITLE.search((r.get("Job Title") or "").strip()):
            d["junktitle"] += 1

# ---------- classify ----------
CLASS_ORDER = ["DEAD_DNS", "NO_LOAD", "JUNK_URL", "JUNK_TITLE",
               "NO_LOCATION", "LOW_LOCATION", "ZERO_ROWS", "SCOPE_ONLY"]
LABEL = {
    "DEAD_DNS": "host does not resolve (DNS) -- portal unreachable",
    "NO_LOAD": "seed page did not load / HTTP error / timeout",
    "JUNK_URL": "harvested non-job URLs (sign-in, values, details pages)",
    "JUNK_TITLE": "harvested non-job titles (dates, 'Skip the line')",
    "NO_LOCATION": "rows harvested but >=80% had Location Source 'none'",
    "LOW_LOCATION": "rows harvested but <25% carried a usable location",
    "ZERO_ROWS": "scanned without a hard error but produced 0 job rows",
    "SCOPE_ONLY": "quarantined ONLY by the scope filter -- abolished by P64",
}
tags_of, primary = {}, {}
for n in sorted(set(list(per) + list(err) + list(rows))):
    p, r = per.get(n), rows.get(n) or {}
    msgs = " || ".join(sorted(err.get(n, set())) + sorted((p or {}).get("err", set())))
    t = []
    if "does not resolve" in msgs or "DNS" in msgs:
        t.append("DEAD_DNS")
    if "did not load" in msgs or "HTTP Error" in msgs or "timed out" in msgs:
        t.append("NO_LOAD")
    if r.get("n", 0) >= 10:
        if r["lsrc"].get("none", 0) >= 0.8 * r["n"]:
            t.append("NO_LOCATION")
        elif 100.0 * r["loc"] / r["n"] < 25:
            t.append("LOW_LOCATION")
        if r["junkurl"]:
            t.append("JUNK_URL")
        if r["junktitle"]:
            t.append("JUNK_TITLE")
        qr = r["qreason"]
        if qr and sum(qr.values()) >= 0.9 * r["n"] and set(qr) <= {"outside_or_unproven_target_country"}:
            t.append("SCOPE_ONLY")
    if p and not t and r.get("n", 0) == 0 and p.get("found", 0) == 0:
        t.append("ZERO_ROWS")
    if not t:
        continue
    tags_of[n] = (t, msgs)
    live = [x for x in t if x != "SCOPE_ONLY"]
    primary[n] = sorted(live or t, key=CLASS_ORDER.index)[0]

inseed = {n: v for n, v in primary.items() if n in raw_by_name}
dropped = sorted(n for n in primary if n not in raw_by_name)
groups = defaultdict(list)
for n, c in inseed.items():
    groups[c].append(n)
for c in groups:
    groups[c].sort(key=lambda x: order.index(x))   # keep the seed's own order

# ---------- write the CSV ----------
out = [header_line]
total = 0
for c in CLASS_ORDER:
    names = groups.get(c) or []
    if not names:
        continue
    note = (" -- HISTORICAL, regression witness only" if c == "SCOPE_ONLY" else "")
    out.append("# ===== BAD RESULT: %s (%d)%s =====" % (c, len(names), note))
    out.append("# %s" % LABEL[c])
    for n in names:
        out.append(raw_by_name[n])
        total += 1
    out.append("")
body = "\r\n".join(x for x in out)
body = re.sub(r"(\r\n)+$", "\r\n", body)
os.makedirs(OUT_DIR, exist_ok=True)
with io.open(OUT, "w", encoding="utf-8", newline="") as f:
    f.write(body)
print("wrote %s: %d portal rows, %d groups, %d bytes"
      % (OUT, total, sum(1 for c in CLASS_ORDER if groups.get(c)), len(body)))
print("flagged but NOT in the current seed (%d): %s" % (len(dropped), ", ".join(dropped[:8])))

# ---------- report ----------
L = ["# Bad-portal test seed", "",
     "**File:** `test_seeds/bad_portals_Career_seed.csv`",
     "**Rows:** %d portals, in %d failure groups" % (total, sum(1 for c in CLASS_ORDER if groups.get(c))),
     "**Format:** identical to the production seed -- same %d columns, same header,"
     % NCOL,
     "same field order, CRLF, UTF-8 with no BOM. Every row is copied **verbatim** from",
     "`uploads/company_Career_seed.csv`; no seed URL was altered.",
     "", "Built from all 15 runs present in `uploads/` (scan logs, errors files, job and",
     "quarantine CSVs). A portal is included if it showed a bad result in **any** run.",
     ""]
for c in CLASS_ORDER:
    names = groups.get(c) or []
    if not names:
        continue
    L += ["## %s -- %d portals" % (c, len(names)), "", LABEL[c], ""]
    if c == "SCOPE_ONLY":
        L += ["**Historical.** These were quarantined solely by `scope_policy=job_location`",
              "under the old filter. P64 set `SPONSORSCOUT_SCOPE_FILTER=0`, so the rows are",
              "kept now. Retained as regression witnesses, not as live defects.", ""]
    L += ["| portal | rows | located | quarantined | runs | other tags | evidence |",
          "|---|---|---|---|---|---|---|"]
    for n in names:
        r = rows.get(n) or {}
        t, msgs = tags_of[n]
        other = ",".join(x for x in t if x != c) or "-"
        ev = (msgs or (max(r["qreason"].items(), key=lambda kv: kv[1])[0]
                       if r.get("qreason") else ""))[:88]
        L.append("| %s | %d | %d | %d | %d | %s | %s |"
                 % (n, r.get("n", 0), r.get("loc", 0), r.get("quar", 0),
                    len(per.get(n, {}).get("runs", ())), other, ev.replace("|", "/")))
    L.append("")
L += ["## Flagged but no longer in the seed -- %d" % len(dropped), "",
      "These showed bad results in an older run but were pruned from the production",
      "seed, so they are **not** in the test file: ", "",
      ", ".join(dropped) or "(none)", ""]
io.open(REPORT, "w", encoding="utf-8", newline="").write("\r\n".join(L))
print("wrote", REPORT)
