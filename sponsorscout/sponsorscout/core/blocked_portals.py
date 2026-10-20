"""Portals that refused automated access, and handing them to the user's browser.

The scanner never gets past a bot-protection challenge (no solving, no reused
sessions). When a run finishes, the app lists the portals that blocked it and
can open their listing URLs in the user's default browser, so the user can
search them by hand. Nothing here contacts the site.
"""
from __future__ import annotations

import csv
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

# Only the career scanner raises this exact prefix, and only after a real
# bot-protection challenge page was detected (see career_scanner.py).
BOT_BLOCK_MARKER = "blocked by bot protection:"
# ATS boards are never listed: they have no browser challenge path.
CAREER_ARTIFACT = "career"


def blocked_portals_from_errors_csv(path) -> list[dict]:
    """Rows of a career run's ``*_errors.csv`` whose message is a bot block.

    Returns one dict per distinct URL: ``{"seed_name", "url", "reason"}``.
    A missing file gives an empty list, because a run that wrote no errors
    has nothing to report.
    """
    p = Path(path) if path else None
    if not p or not p.exists():
        return []
    out: list[dict] = []
    seen: set[str] = set()
    with open(p, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            message = (row.get("Message") or "").strip()
            if BOT_BLOCK_MARKER not in message.casefold():
                continue
            url = (row.get("Seed URL") or "").strip()
            if not is_web_url(url) or url.casefold() in seen:
                continue
            seen.add(url.casefold())
            out.append({
                "seed_name": (row.get("Seed Name") or "").strip(),
                "url": url,
                "reason": message,
            })
    return out


def blocked_portals_from_summary(summary: dict) -> list[dict]:
    """Collect blocked portals from every scanner artifact in a run summary."""
    artifacts = (summary or {}).get("artifacts", {}) or {}
    career = artifacts.get(CAREER_ARTIFACT)
    if not isinstance(career, dict):
        return []
    return blocked_portals_from_errors_csv(career.get("errors"))


def is_web_url(url: str) -> bool:
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def open_in_default_browser(url: str) -> bool:
    """Open ``url`` in the user's default browser. Returns False if refused."""
    if not is_web_url(url):
        return False
    return bool(webbrowser.open_new_tab(url))
