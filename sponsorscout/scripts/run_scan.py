"""CLI scan runner (PySide6 restart).

Usage:
    python -m sponsorscout.scripts.run_scan              # full scan (default)
    python -m sponsorscout.scripts.run_scan --quick      # dev: skip detail pages
    python -m sponsorscout.scripts.run_scan --dedup      # run dedup after scanning
    python -m sponsorscout.scripts.run_scan --company X  # scan a single company

Wraps :func:`sponsorscout.scanning.pipeline.run_scan`, which runs the ATS
phase then the career phase and ingests the results into the database.

The app uses a single, thorough scan mode (``full``).  ``--quick`` is a
developer-only escape hatch that skips the per-job detail-page enrichment.
"""
from __future__ import annotations

import argparse
import sys

from sponsorscout.db.database import DB_PATH, get_connection, initialize
from sponsorscout.core.maintenance import cleanup_database
from sponsorscout.scanning import pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="SponsorScout job scanner")
    parser.add_argument("--quick", action="store_true",
                        help="Dev only: skip per-job detail-page enrichment")
    parser.add_argument("--full", action="store_true",
                        help="Deprecated alias — full is now the default")
    parser.add_argument("--dedup", action="store_true",
                        help="Explicitly merge trusted aliases after scanning; save backup and audit first")
    parser.add_argument("--company", type=str, default=None,
                        help="Only scan one company by name")
    args = parser.parse_args()

    initialize(DB_PATH)
    method = "quick" if args.quick else "full"

    if args.company:
        from sponsorscout.application import seed_manager
        matches = [r for r in seed_manager.read_seed_rows(
            seed_manager.user_ats_path())["rows"]
                   if args.company.lower() in (r.get("name") or "").lower()]
        matches += [r for r in seed_manager.read_seed_rows(
            seed_manager.user_career_path())["rows"]
                    if args.company.lower() in (r.get("name") or "").lower()]
        if not matches:
            print(f"No company matching '{args.company}' found in seeds.")
            sys.exit(1)
        only_companies = [m["name"] for m in matches]
        print(f"Found {len(matches)} matching company/companies: "
              f"{', '.join(only_companies)}")
    else:
        only_companies = None

    print(f"Scanning (method={method})…")
    summary = pipeline.run_scan(method=method, db_path=DB_PATH,
                                only_companies=only_companies,
                                progress=lambda msg: print(msg, flush=True))
    status = summary.get("status", "error")
    print(f"Scan {status}: ingested={summary.get('ingested', 0)}, "
          f"duplicates={summary.get('duplicates', 0)}, "
          f"quarantined={summary.get('quarantined', 0)}")

    if args.dedup:  # explicit CLI opt-in; never part of ordinary scanning
        result = cleanup_database(DB_PATH,confirm=True)
        print(f"Merged {result['merged']} trusted alias rows; backup={result['backup']}; audit={result['audit']}")


if __name__ == "__main__":
    main()
