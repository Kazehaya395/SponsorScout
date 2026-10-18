#!/usr/bin/env python3
"""SUPERSEDED 2026-10-17: workspace consolidated into codebase/;
the user supplies the replacement build script. Kept for the canonical-md5 map.

Was: Build download/SponsorScout_Wave8s_full_codebase.zip.

Lives in the workspace (not /tmp, which is wiped between turns) so the archive is
reproducible. Usage:  python tests/build_zip.py

Recipe (NOTES.md §6): explicit arcname -> source map, assert every source exists,
real directory entries, build to ``<out>.building``, re-open and verify every
embedded md5 against its source, only then ``os.replace``. The archive's own byte
size is never written into a document that is inside the archive -- that makes
every rebuild stale by the length of the edit.

``build_zip.py`` itself is deliberately NOT shipped: a zip containing the script
that reports on the zip is self-referential.
"""
from __future__ import annotations

import hashlib
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "download" / "SponsorScout_Wave8s_full_codebase.zip"
PKG = ROOT / "codebase" / "sponsorscout"

CANONICAL = {  # arcname -> expected md5 (the four files a wave is judged by)
    "sponsorscout/scanning/career/career_scanner.py": "26b3c49523a2e3f3d40e0f46a34489cc",
    "sponsorscout/scanning/ats/ats_scanner.py": "cf130838d2f2ae855de7200b8a989656",
    "sponsorscout/scanning/jd_support.py": "6654ac0e64ec8980d9667eb6464fa72c",
    "sponsorscout/data/seeds/company_Career_seed.csv": "7bd605f8ef52d35c8797ba07a3705f98",
}

# Files that exist ONLY in the user's install. Listed so the archive never claims
# to be complete, and so this script fails loudly if one ever appears here
# unannounced (REG-1 / NOTES.md §2c).
NOT_IN_ARCHIVE = [
    "main.py", "i18n.py", "application/scan_coordinator.py", "core/ats_detection.py",
    "core/dedup.py", "core/integrity.py", "core/verification_service.py",
    "db/migrate_countries.py",
]

# REG-1: reconstructions, not recovered originals. Must stay in the archive and
# must keep their RECONSTRUCTED header.
RECONSTRUCTED = [
    "sponsorscout/paths.py",
    "sponsorscout/core/url_normalizer.py",
    "sponsorscout/services/browser_fetcher.py",
]

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache"}


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def collect() -> dict:
    """arcname -> source path. Asserts every source exists."""
    files: dict[str, Path] = {}

    files["INSTALL.txt"] = ROOT / "codebase" / "INSTALL.txt"
    for doc in ("README.md", "CHANGELOG.md", "NOTES.md", "RUN_REVIEW_20261016.md",
            "RUN_REVIEW_20261016B.md"):
        files[doc] = ROOT / doc

    # the package, minus generated dirs
    for p in sorted(PKG.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        rel = p.relative_to(PKG).as_posix()
        files["sponsorscout/" + rel] = p
    # requirements.txt lives beside the package, but belongs inside it
    files["sponsorscout/requirements.txt"] = ROOT / "codebase" / "requirements.txt"

    # test seeds only -- the production ones already sit in data/seeds/
    for p in sorted((ROOT / "seeds").glob("test_*.csv")):
        files["seeds_test/" + p.name] = p
    # the user's own 108-row seed with the B7 retirement applied (DATA-1)
    files["seeds_alt/company_Career_seed_108row_B7retired.csv"] = (
        ROOT / "seeds" / "company_Career_seed_108row_B7retired.csv")
    # bad-portals-only test seed: 56 issue portals, 52 working removed, 2 URLs repaired
    files["seeds_alt/Test_company_Career_seed_bad_only.csv"] = (
        ROOT / "seeds" / "Test_company_Career_seed_bad_only.csv")

    for p in sorted((ROOT / "tests").glob("*.py")):
        if p.name == "build_zip.py":
            continue
        files["tests/" + p.name] = p

    missing = [a for a, s in files.items() if not s.exists()]
    if missing:
        sys.exit("build_zip: missing sources for: " + ", ".join(missing))

    dupes = [a for a in files if list(files).count(a) > 1]
    if dupes:
        sys.exit("build_zip: duplicate arcnames: " + ", ".join(sorted(set(dupes))))
    return files


def main() -> None:
    files = collect()
    tmp = OUT.with_suffix(OUT.suffix + ".building")
    tmp.parent.mkdir(parents=True, exist_ok=True)

    # every ancestor directory, not just the immediate parent -- a file at
    # a/b/c.py needs entries for a/ and a/b/ too
    dirs: set[str] = set()
    for a in files:
        parts = a.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for d in sorted(dirs):
            zi = zipfile.ZipInfo(d + "/")
            zi.external_attr = (0o40755 << 16) | 0x10
            z.writestr(zi, b"")
        for arc in sorted(files):
            z.write(files[arc], arc)

    # --- verify before the final name is ever written -----------------------
    src_md5 = {arc: md5(src) for arc, src in files.items()}
    bad = []
    with zipfile.ZipFile(tmp) as z:
        if z.testzip() is not None:
            sys.exit("build_zip: CRC failure in " + tmp.name)
        names = [n for n in z.namelist() if not n.endswith("/")]
        if sorted(names) != sorted(files):
            sys.exit("build_zip: archive contents differ from the source map")
        for arc in names:
            got = hashlib.md5(z.read(arc)).hexdigest()
            if got != src_md5[arc]:
                bad.append((arc, got, src_md5[arc]))
    if bad:
        for arc, got, want in bad:
            print("  MISMATCH %s\n     zip %s\n     src %s" % (arc, got, want))
        tmp.unlink(missing_ok=True)
        sys.exit("build_zip: %d md5 mismatch(es); archive discarded" % len(bad))

    for arc, want in CANONICAL.items():
        got = src_md5.get(arc)
        if got != want:
            tmp.unlink(missing_ok=True)
            sys.exit("build_zip: canonical md5 for %s is %s, expected %s" % (arc, got, want))

    # guard: a reconstructed file must still say it is one
    for arc in RECONSTRUCTED:
        head = files[arc].read_text(encoding="utf-8", errors="replace")[:1600]
        if "RECONSTRUCTED" not in head:
            tmp.unlink(missing_ok=True)
            sys.exit("build_zip: %s lost its RECONSTRUCTED header" % arc)

    # guard: none of the install-only modules may have crept in
    for rel in NOT_IN_ARCHIVE:
        if ("sponsorscout/" + rel) in files:
            tmp.unlink(missing_ok=True)
            sys.exit("build_zip: %s now exists in the tree -- update "
                     "NOT_IN_ARCHIVE and NOTES.md §2c before shipping" % rel)

    os.replace(tmp, OUT)

    n_dirs = len(dirs)
    print("built %s" % OUT.relative_to(ROOT))
    print("  %d files, %d directory entries, %d bytes" % (len(files), n_dirs, OUT.stat().st_size))
    print("  md5 %s" % md5(OUT))
    print("  canonical: all 4 verified")
    print("  reconstructed (REG-1): %s" % ", ".join(RECONSTRUCTED))
    print("  NOT included (user-install only): %d modules -- %s"
          % (len(NOT_IN_ARCHIVE), ", ".join(NOT_IN_ARCHIVE)))
    prod = sorted(a for a in files if a.startswith("sponsorscout/data/seeds/"))
    print("  data/seeds/: %s" % ", ".join(p.split("/")[-1] for p in prod))
    print("  seeds_test/: %s" % ", ".join(a.split("/")[-1] for a in sorted(files)
                                          if a.startswith("seeds_test/")))


if __name__ == "__main__":
    main()
