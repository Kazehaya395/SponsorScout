"""
Freshness verification service.
Checks whether job URLs still resolve to active job pages.
Uses lightweight HTTP (no browser required).
"""
from __future__ import annotations
from sponsorscout.services.browser_fetcher import fetch_rendered_html
from sponsorscout.core.verification import (
    mark_expired, mark_inconclusive, mark_verified,
)

# Phrases that reliably indicate a job is no longer active
DEAD_PHRASES = [
    "this job is no longer available",
    "job no longer available",
    "position has been filled",
    "this position is no longer",
    "vacancy has been filled",
    "application period has ended",
    "listing has expired",
    "job listing expired",
    "no longer accepting",
    "page not found",
    "404 not found",
    "sorry, this job",
    "job has been removed",
    "this role has been filled",
]

#: Verdicts returned by :func:`check_url`.
VERDICT_ACTIVE = "active"
VERDICT_EXPIRED = "expired"
VERDICT_INCONCLUSIVE = "inconclusive"

#: HTTP statuses that prove the page is gone.
#:
#: BUGFIX: this used to be ``(404, 410, 403, 0)``.  That was destructive:
#:
#: * ``403`` is what a WAF/bot-manager returns to a scraper — the posting is
#:   very much alive, the crawler was simply refused.  Career portals 403
#:   non-browser clients routinely.
#: * ``status == 0`` is what ``fetch_rendered_html`` returns for *any* fetch
#:   failure: timeout, DNS error, TLS reset, connection refused.
#:
#: Either value therefore marked a live job as ``is_expired=1``, which hides it
#: from Search/Dashboard and made it a candidate for the Tools tab's
#: "Clear stale data" ``DELETE FROM jobs WHERE is_expired=1`` — i.e. one flaky
#: network moment could permanently delete real job rows.
#:
#: Only a status that actually means "gone" may expire a job.  Everything else
#: is inconclusive and leaves the stored verdict untouched.
_GONE_STATUSES = frozenset({404, 410, 451})


def check_url(url: str) -> tuple[str, str]:
    """Classify a job URL as active / expired / inconclusive.

    Returns ``(verdict, detail)`` where ``detail`` is a short human-readable
    reason (used for the scan log).  Never raises.
    """
    if not url:
        return VERDICT_EXPIRED, "no url"

    try:
        result = fetch_rendered_html(url)
    except Exception as exc:  # never let one bad URL abort the run
        return VERDICT_INCONCLUSIVE, f"fetch failed: {type(exc).__name__}: {exc}"

    status = int(result.get("status", 0) or 0)

    # A transport failure (0) or a refusal (403/429) says nothing about
    # whether the posting still exists.
    if status == 0:
        return VERDICT_INCONCLUSIVE, "no response (network/DNS/TLS error)"
    if status in _GONE_STATUSES:
        return VERDICT_EXPIRED, f"HTTP {status}"
    if status in (401, 403, 429) or status >= 500:
        return VERDICT_INCONCLUSIVE, f"HTTP {status} (blocked/rate-limited/server error)"

    html_lower = (result.get("html") or "").lower()
    title_lower = (result.get("title") or "").lower()
    combined = html_lower[:5000] + " " + title_lower

    for phrase in DEAD_PHRASES:
        if phrase in combined:
            return VERDICT_EXPIRED, f"dead phrase: {phrase!r}"

    return VERDICT_ACTIVE, f"HTTP {status}"


def verify_url_active(url: str) -> bool:
    """Return True only when the job URL is *confirmed* still live.

    An inconclusive result (blocked, rate-limited, network error) returns
    False, but callers that persist the outcome must use :func:`verify_job`,
    which keeps the stored verdict for those cases instead of expiring the job.
    """
    verdict, _detail = check_url(url)
    return verdict == VERDICT_ACTIVE


def verify_job(job: dict) -> dict:
    """Verify a job dict and return it with an updated freshness verdict.

    Only a confirmed "gone" response expires a job.  Blocked / unreachable
    URLs are recorded as checked-but-inconclusive: the timestamp advances so
    the row leaves the queue, but ``is_expired``/``verified_active`` are left
    exactly as they were.
    """
    verdict, _detail = check_url(job.get("url", ""))
    if verdict == VERDICT_ACTIVE:
        return mark_verified(job)
    if verdict == VERDICT_EXPIRED:
        return mark_expired(job)
    return mark_inconclusive(job)
