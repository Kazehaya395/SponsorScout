"""
Backfill: copy industry from companies table into existing jobs records.

Run this once after upgrading to populate the industry column for jobs
that were scanned before the fix was applied.

Usage: python -m sponsorscout.scripts.backfill_industry
"""

import logging

from sponsorscout.db.database import get_connection, DB_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def backfill():
    conn = get_connection(DB_PATH)
    try:
        # Find jobs where industry is empty but we have a company match with industry
        updated = conn.execute("""
            UPDATE jobs
            SET industry = (
                SELECT companies.industry
                FROM companies
                WHERE companies.name = jobs.company
                  AND companies.industry != ''
                LIMIT 1
            )
            WHERE (jobs.industry IS NULL OR jobs.industry = '')
              AND EXISTS (
                SELECT 1 FROM companies
                WHERE companies.name = jobs.company
                  AND companies.industry != ''
              )
        """)
        affected = updated.rowcount
        conn.commit()

        # Show companies that still have no industry
        missing = conn.execute("""
            SELECT DISTINCT j.company
            FROM jobs j
            WHERE (j.industry IS NULL OR j.industry = '')
              AND NOT EXISTS (
                SELECT 1 FROM companies c
                WHERE c.name = j.company AND c.industry != ''
              )
        """).fetchall()

        logger.info("Backfilled industry for %d job(s).", affected)
        if missing:
            logger.info(
                "Companies still missing industry (add to CSV): %s",
                ", ".join(r["company"] for r in missing),
            )
        else:
            logger.info("All jobs now have industry data.")
    finally:
        conn.close()


if __name__ == "__main__":
    backfill()