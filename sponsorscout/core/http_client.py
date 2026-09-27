from __future__ import annotations

import logging
import random
from contextlib import contextmanager

from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Pool of realistic User-Agent strings to rotate between sessions.
# Using a variety of Chrome versions across different OS platforms
# to avoid detection by UA-based fingerprinting.
_USER_AGENTS = [
    # Chrome 131+ on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Chrome 130 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Chrome 129 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    # Chrome 128 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    # Chrome 131 on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Chrome 130 on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Chrome 131 on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Chrome 130 on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Edge 131 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    # Firefox 133 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    # Firefox 132 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
]

# Cloudflare challenge detection markers.
# IMPORTANT: Only use markers that are EXCLUSIVE to challenge/block pages.
# Many legitimate sites use Cloudflare for CDN, analytics and DDoS protection.
# These CF strings appear on ALL Cloudflare-protected sites, including normal
# rendered career pages — do NOT use them as bot-block signals:
#   cf-ray         → added to every CF response header, often embedded in HTML
#   cf_clearance   → CF session cookie, appears in forms/JS on real pages
#   challenges.cloudflare.com  → CF Turnstile widget used in job APPLY forms
#   __cf_chl_opt   → CF challenge var, but also appears in CF analytics
#   cf-wrapper     → generic class name used by many sites independently
_CLOUDFLARE_MARKERS = [
    "cf-browser-verification",       # exclusive to CF browser check pages
    "/_cf/challenge-platform/",      # exclusive to CF challenge script path
    "DDoS protection by",            # exclusive to CF challenge page text
    "Attention Required! | Cloudflare",  # exclusive to CF block page title
    "challenge-platform",            # exclusive to CF managed challenge
]

# Bot challenge markers that are safe to check even in large pages.
_SAFE_CHALLENGE_MARKERS = [
    "datadome",
    "Please verify you are a human",
    "Are you a human?",
    "Verify you're human",
    "Please stand by, while we are checking your browser",
    "Checking if the site connection is secure",
    "Just a moment...",
    "Enable JavaScript and cookies to continue",  # CF interstitial
    "Your IP has been blocked",
    "Access denied",
    "perimeterx",         # PerimeterX bot protection
    "px-captcha",
    "incapsula",          # Imperva/Incapsula
    "visitorapiresponse", # Imperva response token
]


def is_bot_blocked(html: str | None, status_code: int = 200) -> bool:
    """Detect if an HTTP response is a bot challenge/block page.

    Uses a conservative approach for 200-status responses to avoid false
    positives (flagging legitimate career pages as blocked). Only flags
    as blocked when there's high confidence (small challenge page or
    definitive Cloudflare markers).

    For non-200 status (403, 429, 503), the response is always flagged.

    Args:
        html: The response body text (may be None or empty).
        status_code: HTTP status code (default 200).

    Returns:
        True if the response appears to be a bot challenge page.
    """
    # Non-200 codes that are definitive blocks
    if status_code in (403, 429, 503):
        return True

    if not html:
        return False

    html_lower = html.lower()
    html_len = len(html)

    # Phase 1: Quick check for Cloudflare-specific markers (high confidence)
    for marker in _CLOUDFLARE_MARKERS:
        if marker.lower() in html_lower:
            return True

    # Phase 2: Small pages (< 3KB) that contain challenge markers
    # Challenge pages are typically very small (under 3KB)
    if html_len < 3000:
        for marker in _SAFE_CHALLENGE_MARKERS:
            if marker.lower() in html_lower:
                return True

        # Very small pages with no meaningful content are suspicious
        if html_len < 1000 and "<title>" in html_lower:
            import re
            m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            title = m.group(1).strip().lower() if m else ""
            if title in ("just a moment...", "please wait...", "verifying...", "attention required!", "access denied"):
                return True

    # Phase 3: Pages with DataDome that are NOT large career portals
    # DataDome challenge pages are typically < 10KB
    if html_len < 10000 and "datadome" in html_lower:
        return True

    return False


def _random_user_agent() -> str:
    """Return a random User-Agent string from the pool."""
    return random.choice(_USER_AGENTS)


def _new_session() -> Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=2.0,
        # 429 is intentionally excluded: it means "rate limited — back off".
        # Retrying 429 immediately just makes the rate-limit worse. Connectors
        # that need Retry-After handling (e.g. Greenhouse) do so manually.
        # 503 is included because it usually means "temporarily overloaded".
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD", "POST"}),
        raise_on_status=False,
    )
    session = Session()
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    # Use a random User-Agent per session to avoid UA-based fingerprinting
    ua = _random_user_agent()
    is_chrome = "Chrome/" in ua
    chrome_version = ""
    if is_chrome:
        m = __import__("re").search(r"Chrome/(\d+)", ua)
        if m:
            chrome_version = m.group(1)

    headers: dict[str, str] = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Upgrade-Insecure-Requests": "1",
    }

    # Add modern Chrome-specific headers only for Chrome UAs
    if is_chrome and chrome_version:
        headers["Sec-CH-UA"] = f'"Chromium";v="{chrome_version}", "Google Chrome";v="{chrome_version}"'
        headers["Sec-CH-UA-Mobile"] = "?0"
        headers["Sec-CH-UA-Platform"] = '"Windows"'
        headers["Sec-Fetch-Dest"] = "document"
        headers["Sec-Fetch-Mode"] = "navigate"
        headers["Sec-Fetch-Site"] = "none"
        headers["Sec-Fetch-User"] = "?1"

    session.headers.update(headers)
    return session


def build_session() -> Session:
    """Return a fresh Session. Caller is responsible for .close().

    B12 fix: prior to this change every connector called build_session() and
    dropped the reference, leaking connection pools. Use http_session() as a
    context manager for the safe path.
    """
    return _new_session()


@contextmanager
def http_session():
    """Context manager that closes the session (and its connection pool) on exit.

    Usage:
        with http_session() as s:
            s.get(...)
    """
    s = _new_session()
    try:
        yield s
    finally:
        try:
            s.close()
        except Exception as exc:
            logger.exception("Failed to close HTTP session")