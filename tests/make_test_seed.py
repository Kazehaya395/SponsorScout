#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a SMALL test seed from the full SponsorScout seeds.

Why not just take a random 20 companies: a random sample verifies almost
nothing here. Every fix in this round was driven by a specific company's
output, so the default profile picks the companies that produced the
defects -- each fix gets a witness that is known to exercise it.

Usage (run from anywhere):

    python make_test_seed.py                      # default "evidence" profile
    python make_test_seed.py --profile quick      # fastest, still 11 fixes
    python make_test_seed.py --profile big        # adds the huge boards
    python make_test_seed.py --random 25          # random sample instead
    python make_test_seed.py --list               # show profiles and exit

Output goes to ./test_seeds/ and keeps the seed format exactly: same 12
columns, same header, same field order, CRLF line endings, UTF-8 with no
BOM. Seed URLs are never modified.
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CWD = Path.cwd().resolve()
OUT_DIR = HERE / "test_seeds"

CAREER_NAME = "company_Career_seed.csv"
ATS_NAME = "company_ATS_seed.csv"


def _candidate_dirs():
    """Where a seed may live, best first.

    Order is deliberate: the files delivered in ./fixed win, then the
    bundled package seeds, and only then the per-user APPDATA copies --
    those are the mutable ones you were told to delete, so they must
    never silently shadow the bundled defaults.
    """
    dirs = []
    for base in (HERE, CWD, HERE.parent):
        # 2026-10-15 layout: seeds/ holds the canonical CSVs and
        # codebase/sponsorscout/data/seeds/ holds the shipped copies.
        dirs += [base / "seeds",
                 base / "codebase" / "sponsorscout" / "data" / "seeds",
                 base]
        # app root -> sponsorscout/data/seeds, and any parent of it
        for p in [base] + list(base.parents)[:4]:
            dirs += [
                p / "sponsorscout" / "data" / "seeds",
                p / "sponsorscout" / "data" / "seed",
                p / "data" / "seeds",
                p / "data" / "seed",
            ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs += [Path(appdata) / "SponsorScout" / "seeds",
                 Path(appdata) / "Sponsorscout" / "seeds"]
    dirs.append(Path.home() / ".sponsorscout" / "seeds")

    seen, out = set(), []
    for d in dirs:
        key = str(d).casefold()
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def find_seed(filename: str, explicit: Path | None):
    """Resolve a seed path, tolerating seed/seeds and a wrong directory."""
    if explicit is not None:
        if explicit.exists():
            return explicit, []
        tried = [explicit]
        # same filename, but the sibling spelling of the directory
        swap = {"seed": "seeds", "seeds": "seed"}.get(explicit.parent.name)
        if swap:
            alt = explicit.parent.parent / swap / explicit.name
            tried.append(alt)
            if alt.exists():
                return alt, tried[:-1]
        # same directory, canonical filename
        alt = explicit.parent / filename
        if alt not in tried:
            tried.append(alt)
            if alt.exists():
                return alt, tried[:-1]
        hit, more = _scan_dirs(filename)
        return hit, tried + more

    return _scan_dirs(filename)


def _scan_dirs(filename: str):
    tried = []
    for d in _candidate_dirs():
        p = d / filename
        tried.append(p)
        if p.exists():
            return p, tried[:-1]
    # last resort: shallow recursive search from the script dir and cwd
    for base in (HERE, CWD):
        for p in sorted(base.glob("**/" + filename))[:1]:
            return p, tried
    return None, tried


def die_missing(filename: str, tried) -> None:
    print(f"ERROR: could not find {filename}.\n", file=sys.stderr)
    print("Looked in:", file=sys.stderr)
    for p in tried[:14]:
        print(f"   {p}", file=sys.stderr)
    if len(tried) > 14:
        print(f"   ... and {len(tried) - 14} more", file=sys.stderr)
    print("\nFix it with an explicit path, e.g.:", file=sys.stderr)
    print(r"   python make_test_seed.py --career "
          r"D:\My_codes\SponsorScout\sponsorscout\data\seeds\company_Career_seed.csv",
          file=sys.stderr)
    print("\nNote the folder is 'seeds', not 'seed'.", file=sys.stderr)
    sys.exit(2)

BASE_COLUMNS = [
    "name", "careers_url", "provider", "board_slug", "source_type",
    "target_country", "scope_policy", "industry", "sponsorship_history",
    "english_friendly", "remote_score", "notes",
]

# ── Which company proves which fix ────────────────────────────────────────
# company -> (approx rows last run, what it is there to prove)
WITNESSES = {
    "A2G Technologies": (12, "P0-58 S-16 seed repair: must stay Netherlands/job_location and write ~2 rows, not 12"),
    "Amazon Italia": (165, "P0-47 ITA suffix - P0-49 junk locations - P0-50 S-01 sponsor FP - P0-55 country"),
    "American Express": (285, "P0-46 'four or more' - P0-48 Gurugram HR - P0-51 cap notice - P0-60 '2 Locations'"),
    "Appodeal": (14, "P0-50 S-02 travel-list false positive"),
    "ABN AMRO": (0, "P0-61 junk titles 'ABN AMRO' / 'Antwerpen' (wrote 0 rows last run - may be empty)"),
    "Anymind Group": (223, "P0-61 'Campus Recruiting' as a title"),
    "ING": (0, "P0-61 'Trainee' as a title"),
    "Databricks": (0, "P0-61 'McLean, Virginia' as a title"),
    "Action Italia Post": (11, "S-15 multi-site rows that only LOOK duplicated"),
    "Oniverse": (0, "S-15 'SALES POINTS' x141 - same-title, different stores"),
    "Alteos": (5, "P0-54 O-08 EU Blue Card - German employer"),
    "Applike Group": (41, "P0-54 O-08 EU Blue Card - German employer"),
    "adjoe": (48, "P0-52/53 sponsorship true positives on a German tech JD"),
    "Bolt": (214, "P0-52 guard (2) regression check on a large multi-country board"),
}

# Smallest set that still covers the most fixes. Amazon Italia and American
# Express are unavoidable -- between them they witness eight of the patches --
# and they are ~450 of these rows. Everything else here is tiny.
# Covers P0-45/46/47/48/49/50/51/54/55/58/60.
QUICK = [
    "A2G Technologies", "Amazon Italia", "American Express", "Appodeal",
    "Alteos", "Applike Group",
]

# The huge boards. Excluded by default: together they are ~1,080 rows and
# dominate the runtime. Add them only when testing the detail-budget notice.
BIG = ["ASML", "Arbeitnow"]

PROFILES = {
    "quick": QUICK,
    "evidence": list(WITNESSES),
    "big": list(WITNESSES) + BIG,
}


def read_seed(path: Path):
    if path is None or not path.exists():
        sys.exit(f"ERROR: seed not found: {path}")
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = [(c or "").strip() for c in (reader.fieldnames or [])]
        rows = [dict(r) for r in reader]

    # Keep comment / group-header lines exactly as written, so re-emitting
    # them cannot add trailing commas or otherwise alter the file format.
    text = path.read_text(encoding="utf-8-sig", newline="")
    raw_comments = [ln for ln in text.split("\r\n" if "\r\n" in text else "\n")
                    if ln.startswith("#")]
    idx = 0
    for r in rows:
        if (r.get("name") or "").strip().startswith("#") and idx < len(raw_comments):
            r["__raw__"] = raw_comments[idx]
            idx += 1
    return cols, rows


def write_seed(path: Path, cols, rows) -> None:
    """Write preserving the seed contract: CRLF, UTF-8 no BOM, same columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\r\n",
                           extrasaction="ignore")
        w.writeheader()
        for r in rows:
            raw = r.get("__raw__")
            if raw is not None:
                fh.write(raw + "\r\n")
                continue
            w.writerow({c: (r.get(c) or "") for c in cols})


def is_comment(row) -> bool:
    """A seed comment/group-header row, e.g. '# ----- JAPAN (2) -----'.

    Both scanners skip these (career_scanner L3230, ats L1467), so they are
    not companies and must never be sampled.
    """
    name = (row.get("name") or "").strip()
    return name.startswith("#") or not name


def companies_only(rows):
    return [r for r in rows if not is_comment(r)]


def banner(kept: int, total: int, label: str):
    """A single leading comment row, so the subset is never mistaken for the
    real seed. Group counts below it are the FULL-seed counts, left verbatim."""
    return {"name": f"# ===== TEST SAMPLE ({label}): {kept} of {total} companies"
                    f" - group counts below are from the full seed =====",
            "__raw__": f"# ===== TEST SAMPLE ({label}): {kept} of {total} companies"
                       f" - group counts below are from the full seed ====="}


def with_group_headers(all_rows, selected):
    """Keep each group's '# ----- ... -----' header if the group survived.

    Preserves the HQ grouping of the live seed instead of emitting orphaned
    companies under the wrong heading.
    """
    keep_ids = {id(r) for r in selected}
    out, pending = [], []
    for r in all_rows:
        if is_comment(r):
            if (r.get("name") or "").strip():
                pending = [r]          # a new group starts here
            continue
        if id(r) in keep_ids:
            out.extend(pending)
            pending = []
            out.append(r)
    return out


def pick(rows, names):
    """Select rows by name, case-insensitively, preserving seed order."""
    wanted = {n.casefold() for n in names}
    hit = [r for r in companies_only(rows)
           if (r.get("name") or "").strip().casefold() in wanted]
    found = {(r.get("name") or "").strip().casefold() for r in hit}
    missing = [n for n in names if n.casefold() not in found]
    return hit, missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=sorted(PROFILES), default="evidence")
    ap.add_argument("--random", type=int, metavar="N",
                    help="ignore the profile; take N random career companies")
    ap.add_argument("--seed", type=int, default=20261003,
                    help="RNG seed for --random, so a run is repeatable")
    ap.add_argument("--include", default="",
                    help="comma-separated extra company names to add")
    ap.add_argument("--exclude", default="",
                    help="comma-separated company names to drop")
    ap.add_argument("--career", type=Path, default=None,
                    help="path to company_Career_seed.csv (auto-detected if omitted)")
    ap.add_argument("--ats", type=Path, default=None,
                    help="path to company_ATS_seed.csv (auto-detected if omitted)")
    ap.add_argument("--where", action="store_true",
                    help="show which seed files would be used, then exit")
    ap.add_argument("--ats-rows", type=int, default=8,
                    help="how many ATS companies to keep (0 = none)")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--list", action="store_true", help="show profiles and exit")
    a = ap.parse_args()

    if a.list:
        for name, members in sorted(PROFILES.items()):
            total = sum(WITNESSES.get(m, (0, ""))[0] for m in members)
            print(f"\n{name}  ({len(members)} companies, ~{total} rows last run)")
            for m in members:
                why = WITNESSES.get(m, (0, "large board - detail-budget notice"))[1]
                print(f"   {m:<22} {why}")
        return 0

    career_path, tried = find_seed(CAREER_NAME, a.career)
    if career_path is None:
        die_missing(CAREER_NAME, tried)
    ats_path, ats_tried = find_seed(ATS_NAME, a.ats)

    print(f"career seed      : {career_path}")
    print(f"ATS seed         : {ats_path if ats_path else '(not found - skipping)'}")
    if a.where:
        return 0
    a.career, a.ats = career_path, ats_path

    cols, rows = read_seed(a.career)
    if cols != BASE_COLUMNS:
        print(f"WARNING: unexpected columns in {a.career.name}:\n  {cols}")

    pool = companies_only(rows)
    n_comment = len(rows) - len(pool)

    if a.random:
        rng = random.Random(a.seed)
        names = [r["name"].strip()
                 for r in rng.sample(pool, min(a.random, len(pool)))]
    else:
        names = list(PROFILES[a.profile])
    names += [x.strip() for x in a.include.split(",") if x.strip()]
    drop = {x.strip().casefold() for x in a.exclude.split(",") if x.strip()}
    names = [n for n in dict.fromkeys(names) if n.casefold() not in drop]

    if a.random:
        covered = {n for n in names if n in WITNESSES}
        blind = [n for n in WITNESSES if n not in covered]
        print(f"\nRANDOM MODE: {len(names)} companies, RNG seed {a.seed}.")
        print(f"  witnesses hit : {len(covered)} of {len(WITNESSES)}"
              + (f"  ({', '.join(sorted(covered))})" if covered else ""))
        if blind:
            patches = sorted({WITNESSES[n][1].split()[0] for n in blind})
            print("  NOT exercised : " + ", ".join(patches))
            print("  A random sample cannot confirm those fixes -- each was driven by a")
            print("  specific company. Force them in with:")
            print('     --include "' + ",".join(blind) + '"')
            print("  or just use --profile quick / evidence.")
        print()

    sel, missing = pick(rows, names)
    a.out.mkdir(parents=True, exist_ok=True)
    label = f"random {a.random}" if a.random else a.profile
    write_seed(a.out / "company_Career_seed.csv", cols,
               [banner(len(sel), len(pool), label)]
               + with_group_headers(rows, sel))

    n_ats = 0
    if a.ats_rows and a.ats is not None and a.ats.exists():
        acols, arows = read_seed(a.ats)
        asel = companies_only(arows)[: a.ats_rows]
        write_seed(a.out / "company_ATS_seed.csv", acols,
                   [banner(len(asel), len(companies_only(arows)), "ats")]
                   + with_group_headers(arows, asel))
        n_ats = len(asel)
    elif a.ats is not None and a.ats.exists():
        acols, _ = read_seed(a.ats)
        write_seed(a.out / "company_ATS_seed.csv", acols, [])

    est = sum(WITNESSES.get(r["name"].strip(), (60, ""))[0] for r in sel)
    extra = f", {n_comment} comment rows skipped" if n_comment else ""
    print(f"career companies : {len(sel)}  (from {len(pool)}{extra})")
    print(f"ATS companies    : {n_ats}")
    print(f"approx job rows  : ~{est} (based on the last full run)")
    print(f"written to       : {a.out}")
    if missing:
        print(f"\nNOT FOUND in the career seed (skipped): {', '.join(missing)}")
    print("\nWhat each company is there to prove:")
    for r in sel:
        nm = r["name"].strip()
        why = WITNESSES.get(nm, (0, "extra coverage"))[1]
        print(f"   {nm:<22} {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
