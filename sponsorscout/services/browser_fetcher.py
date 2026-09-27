"""
Lightweight page fetcher used by verification and career-page scanning.

It prefers a normal HTTP fetch, then optionally upgrades to a rendered
browser snapshot via Playwright for JavaScript-heavy pages.
"""
from __future__ import annotations

import json
import logging
import random
import re
import sys
from typing import Any
from urllib.parse import urlparse

from sponsorscout.core.http_client import http_session, is_bot_blocked

logger = logging.getLogger(__name__)

_DYNAMIC_HINTS = (
    "__NEXT_DATA__",
    "__NUXT__",
    "__INITIAL_DATA__",
    "__PRELOADED_STATE__",
    "window.__DATA__",
    "window.__APP__",
    'id="root"',
    'id="app"',
    "ng-version",
    "data-reactroot",
    "application/ld+json",
)

_JOB_LISTING_SELECTORS = (
    "a[href*='/jobs/']",
    "a[href*='/careers/']",
    "a[href*='/position/']",
    "a[href*='/positions/']",
    "a[href*='/opportunity/']",
    "a[href*='/job/']",
    "[data-job-id]",
    "[data-position-id]",
    '[class*="job-card"]',
    '[class*="JobCard"]',
    '[class*="job-listing"]',
    '[class*="job-item"]',
    '[class*="position-card"]',
    '[class*="vacancy-item"]',
)


def _looks_dynamic(html: str) -> bool:
    html = html or ""
    if len(html) < 5000:
        return True
    low = html.lower()
    return any(hint.lower() in low for hint in _DYNAMIC_HINTS)


def _looks_like_careers_url(url: str) -> bool:
    """Heuristic: official career portals usually contain one of these paths."""
    try:
        parsed = urlparse(url or "")
    except Exception:
        return False
    path = (parsed.path or "").lower()
    fragment = (parsed.fragment or "").lower()
    full = path + " " + fragment
    if not full.strip():
        return False
    return any(part in full for part in (
        "career", "careers",
        "job", "jobs",
        "join",
        "open-roles", "open-positions",
        "vacanc", "opportunit", "position",
        "rolle", "stellen", "karriere",
        "emploi", "poste",
        "trabajo", "empleo",
    ))


def _extract_title(html: str) -> str:
    if "<title" not in html.lower():
        return ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _playwright_import_error() -> str | None:
    """Return why ``playwright.sync_api`` is not importable, or None if it is.

    A bare boolean hid the reason entirely, which is how a frozen bundle that
    lost Playwright (or its ``greenlet``/``pyee`` deps) ended up reporting only
    a generic "Chromium is not available" warning.
    """
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _playwright_available() -> bool:
    """Check if Playwright package is importable."""
    return _playwright_import_error() is None


def _ensure_playwright_browsers() -> bool:
    """
    Verify browser binaries are present and auto-install if missing.
    Called once per process; result is cached on the function itself.

    The error seen in production:
      BrowserType.launch: Executable doesn't exist at
      ~/.cache/ms-playwright/chromium_headless_shell-XXXX/chrome-headless-shell

    This happens when `pip install playwright` was run but
    `playwright install chromium` was never run — the Python package
    exists but no browser binary was downloaded.

    Returns True if browsers are ready, False if install failed/unavailable.
    """
    if _ensure_playwright_browsers._done:
        return _ensure_playwright_browsers._ok

    _ensure_playwright_browsers._done = True

    if not _playwright_available():
        _ensure_playwright_browsers._ok = False
        return False

    # Try a minimal launch to verify the binary actually exists
    try:
        from playwright.sync_api import sync_playwright as _sp
        with _sp() as p:
            b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            b.close()
        logger.debug("Playwright browser check passed — chromium is ready.")
        _ensure_playwright_browsers._ok = True
        return True
    except Exception as exc:
        exc_str = str(exc).lower()
        browser_missing = (
            "executable doesn't exist" in exc_str
            or "executable not found" in exc_str
            or "browsertype.launch" in exc_str
        )
        if not browser_missing:
            # Some other error (permissions, sandbox) — don't auto-install
            logger.warning("Playwright launch check failed (non-install error): %s", exc)
            _ensure_playwright_browsers._ok = False
            return False

    # Binary missing — attempt recovery. In a frozen (packaged) build there is
    # no usable `python -m playwright` (sys.executable is SponsorScout.exe /
    # the SponsorScout ELF binary), so auto-install can never succeed there;
    # instead the browsers must ship inside the bundle's `_playwright`
    # directory (see build_exe.ps1 / build_deb.sh and sponsorscout/paths.py).
    if getattr(sys, "frozen", False):
        logger.error(
            "Playwright Chromium browser is missing from the installed app "
            "(no `_playwright` browser bundle next to the executable and no "
            "downloaded browser cache). SPA / JS-rendered career portals "
            "cannot be crawled. Fix: reinstall SponsorScout from a full "
            "installer package (the installer ships `_playwright`), or run "
            "`playwright install chromium` on this machine and restart the app."
        )
        _ensure_playwright_browsers._ok = False
        return False

    logger.info(
        "Playwright browser not installed. Running 'playwright install chromium' now. "
        "This downloads ~130 MB and takes ~30 seconds on first run."
    )
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
            timeout=360,
        )
        if result.returncode == 0:
            logger.info("playwright install chromium completed successfully.")
            _ensure_playwright_browsers._ok = True
            return True
        else:
            logger.warning(
                "playwright install chromium failed (exit code %d). "
                "Run 'playwright install chromium' manually in your terminal.",
                result.returncode,
            )
            _ensure_playwright_browsers._ok = False
            return False
    except Exception as exc:
        logger.warning(
            "Auto-install of Playwright browsers failed: %s. "
            "Run 'playwright install chromium' manually in your terminal.",
            exc,
        )
        _ensure_playwright_browsers._ok = False
        return False


_ensure_playwright_browsers._done = False
_ensure_playwright_browsers._ok = False


def _apply_stealth_patches(page) -> None:
    """Apply comprehensive anti-detection patches to the Playwright page."""
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
            enumerable: true,
            configurable: true
        });
        if (navigator.__proto__) {
            delete navigator.__proto__.webdriver;
        }

        const makeFakePlugins = () => {
            const plugins = [
                {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format', version: undefined, length: 1},
                {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: 'Portable Document Format', version: undefined, length: 1},
                {name: 'Native Client', filename: 'internal-nacl-plugin', description: '', version: undefined, length: 2}
            ];
            const pluginsObj = {
                length: 3,
                item: function(idx) { return plugins[idx] || null; },
                namedItem: function(name) {
                    for (let i = 0; i < plugins.length; i++) {
                        if (plugins[i].name === name) return plugins[i];
                    }
                    return null;
                },
                refresh: function() {}
            };
            for (let i = 0; i < plugins.length; i++) {
                pluginsObj[i] = plugins[i];
            }
            return pluginsObj;
        };

        Object.defineProperty(navigator, 'plugins', {
            get: makeFakePlugins,
            enumerable: true,
            configurable: true
        });

        const makeFakeMimeTypes = () => {
            const mimeTypes = [
                {type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format', enabledPlugin: navigator.plugins[0]},
                {type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format', enabledPlugin: navigator.plugins[1]},
                {type: 'application/x-nacl', suffixes: '', description: 'Native Client module', enabledPlugin: navigator.plugins[2]}
            ];
            const mimeObj = {
                length: 3,
                item: function(idx) { return mimeTypes[idx] || null; },
                namedItem: function(name) {
                    for (let i = 0; i < mimeTypes.length; i++) {
                        if (mimeTypes[i].type === name) return mimeTypes[i];
                    }
                    return null;
                }
            };
            for (let i = 0; i < mimeTypes.length; i++) {
                mimeObj[i] = mimeTypes[i];
            }
            return mimeObj;
        };

        Object.defineProperty(navigator, 'mimeTypes', {
            get: makeFakeMimeTypes,
            enumerable: true,
            configurable: true
        });

        window.chrome = {
            runtime: {
                OnInstalledReason: {CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update'},
                OnRestartRequiredReason: {APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic'},
                PlatformArch: {ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', MIPS64EL: 'mips64el', MIPSEL: 'mipsel', X86_32: 'x86-32', X86_64: 'x86-64'},
                PlatformNaclArch: {ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', MIPS64EL: 'mips64el', MIPSEL: 'mipsel', MIPSEL64: 'mipsel64', X86_32: 'x86-32', X86_64: 'x86-64', NONE: 'none'},
                PlatformOs: {ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win'},
                RequestUpdateCheckStatus: {NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available'},
                OnConnect: {},
                OnMessage: {},
                OnMessageExternal: {},
                OnConnectExternal: {},
                sendMessage: function() {},
                connect: function() {},
                getManifest: function() { return {}; },
                getURL: function() { return ''; },
                csi: function() {},
                loadTimes: function() { return {}; }
            },
            app: {
                isInstalled: false,
                InstallState: {DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed'},
                RunningState: {CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running'}
            },
            csi: function() {},
            loadTimes: function() { return {}; }
        };

        if (window.Permissions && window.Permissions.prototype) {
            const origQuery = Permissions.prototype.query;
            Permissions.prototype.query = function(desc) {
                if (desc && desc.name === 'notifications') {
                    return Promise.resolve({state: 'prompt', onchange: null});
                }
                if (desc && desc.name === 'midi') {
                    return Promise.resolve({state: 'prompt', onchange: null});
                }
                if (desc && desc.name === 'midi-sysex') {
                    return Promise.resolve({state: 'prompt', onchange: null});
                }
                return origQuery.call(this, desc);
            };
        }

        if (window.Notification) {
            Object.defineProperty(Notification, 'permission', {
                get: () => 'default',
                enumerable: true,
                configurable: true
            });
        }

        Object.defineProperty(navigator, 'vendor', {get: () => 'Google Inc.', enumerable: true, configurable: true});
        Object.defineProperty(navigator, 'productSub', {get: () => '20030107', enumerable: true, configurable: true});
        Object.defineProperty(navigator, 'deviceMemory', {get: () => 8, enumerable: true, configurable: true});
        Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8, enumerable: true, configurable: true});
        Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 0, enumerable: true, configurable: true});
        Object.defineProperty(navigator, 'platform', {get: () => 'Win32', enumerable: true, configurable: true});
        Object.defineProperty(navigator, 'language', {get: () => 'en-US', enumerable: true, configurable: true});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en'], enumerable: true, configurable: true});

        Object.defineProperty(window, 'outerWidth', {get: () => window.innerWidth, enumerable: true, configurable: true});
        Object.defineProperty(window, 'outerHeight', {get: () => window.innerHeight, enumerable: true, configurable: true});
        Object.defineProperty(window, 'devicePixelRatio', {get: () => 1, enumerable: true, configurable: true});
        if (window.screen) {
            Object.defineProperty(window.screen, 'availWidth', {get: () => window.innerWidth, enumerable: true, configurable: true});
            Object.defineProperty(window.screen, 'availHeight', {get: () => window.innerHeight, enumerable: true, configurable: true});
        }

        Object.defineProperty(document, 'hidden', {get: () => false, enumerable: true, configurable: true});
        Object.defineProperty(document, 'visibilityState', {get: () => 'visible', enumerable: true, configurable: true});
    """)


def _dismiss_cookie_banners(page) -> int:
    """Click common cookie accept/dismiss buttons with human-like delays."""
    cookie_selectors = [
        'button:has-text("Accept")',
        'button:has-text("Accept all")',
        'button:has-text("Allow all")',
        'button:has-text("Allow cookies")',
        'button:has-text("I agree")',
        'button:has-text("Agree")',
        'button:has-text("Continue")',
        'button:has-text("Got it")',
        'button:has-text("OK")',
        'button:has-text("Yes")',
        'button:has-text("Understood")',
        '[class*="cookie"] button',
        '[class*="consent"] button',
        '[class*="gdpr"] button',
        '[class*="privacy"] button',
        '[id*="cookie"] button',
        '[id*="consent"] button',
        '[id*="gdpr"] button',
        '[aria-label*="cookie"]',
        '[aria-label*="Cookie"]',
        '[aria-label*="consent"]',
        '[aria-label*="Consent"]',
        'button[data-testid*="cookie"]',
        'button[data-testid*="consent"]',
    ]
    clicked = 0
    for selector in cookie_selectors:
        try:
            btn = page.locator(selector).first
            if btn and btn.is_visible(timeout=800):
                # Human-like: scroll to button, pause, then click
                btn.scroll_into_view_if_needed(timeout=500)
                page.wait_for_timeout(random.randint(200, 600))
                btn.click(timeout=1500)
                page.wait_for_timeout(random.randint(400, 900))
                clicked += 1
                logger.debug("Dismissed cookie banner with selector: %s", selector)
                break
        except Exception:
            continue
    return clicked


def _wait_for_job_elements(page, timeout_ms: int = 8000) -> bool:
    """Wait for job listing elements to appear on the page using a MutationObserver."""
    import json; selector_js = json.dumps(_JOB_LISTING_SELECTORS)
    timeout_js = int(timeout_ms)

    race_script = (
        "(async () => {"
        "  const selectors = [" + selector_js + "];"
        "  const racePromise = new Promise((resolve) => {"
        "    for (const sel of selectors) {"
        "      if (document.querySelector(sel)) { resolve(true); return; }"
        "    }"
        "    const observer = new MutationObserver(() => {"
        "      for (const sel of selectors) {"
        "        if (document.querySelector(sel)) {"
        "          observer.disconnect();"
        "          resolve(true);"
        "          return;"
        "        }"
        "      }"
        "    });"
        "    observer.observe(document.body, { childList: true, subtree: true });"
        "    setTimeout(() => { observer.disconnect(); resolve(false); }, "
        + str(timeout_js)
        + ");"
        "  });"
        "  return racePromise;"
        "})()"
    )

    try:
        result = page.evaluate(race_script)
        if result is True:
            return True
    except Exception as exc:
        logger.debug("wait_for_job_elements race-script failed: %s", exc)

    for selector in _JOB_LISTING_SELECTORS:
        try:
            el = page.wait_for_selector(selector, timeout=2000)
            if el:
                logger.debug("Found job element with selector: %s", selector)
                return True
        except Exception:
            continue
    return False


_JOB_API_URL_HINTS = re.compile(
    r"(job|career|position|vacanc|opening|role|posting|requisition|"
    r"graphql|search|listing|board|"
    r"api/|/v[12]/|/wp-json|/contentful|"
    r"recruiting|talent|applicant|"
    r"workday|greenhouse|lever|ashby|bamboo|personio|teamtailor|"
    r"smartrecruiters|recruitee|icims|jobvite|freshteam|breezy|homerun|"
    r"wttj|manatal)",
    re.I,
)

_BROAD_API_URL_HINTS = re.compile(
    r"(/api/|/v[12]/|/graphql|/data/|/rest/|/ws/|\.json$|/feed/|/catalog/)",
    re.I,
)

_LOAD_MORE_TEXT_RE = re.compile(
    r"^(load more|show more|more jobs|more roles|more positions|more results|"
    r"view more|see more|see all|next|view all|see all roles|show all|"
    r"mehr laden|mehr anzeigen|alle anzeigen|weitere|näste|nästa|"
    r"charger plus|voir plus|voir tout|afficher plus|"
    r"carica altro|mostra altro|vedi tutto|"
    r"cargar más|ver más|ver todo|mostrar más)\b",
    re.I,
)

_ACCORDION_HEADER_SELECTORS = [
    # Department/team groupings — the most common accordion pattern on career pages
    '[class*="department"] [aria-expanded="false"]',
    '[class*="team-group"] [aria-expanded="false"]',
    '[class*="job-group"] [aria-expanded="false"]',
    '[class*="group-header"] [aria-expanded="false"]',
    # Explicit accordion widgets
    '[data-accordion] button',
    '[data-accordion-trigger]',
    '[class*="accordion-item"]:not([class*="filter"]) button',
    '[class*="accordion-toggle"]',
    '[class*="accordion-button"]:not([class*="filter"])',
    '[class*="accordion-trigger"]',
    '[class*="accordion-header"]',
    '[class*="Accordion"]:not([class*="filter"]) button',
    # Details/summary elements (native HTML accordions)
    'details:not([open]) > summary',
    # Generic aria-expanded — only inside main content, not filter/nav/select dropdowns
    'main [aria-expanded="false"]:not([class*="filter"]):not([class*="select"]):not([class*="dropdown"]):not([class*="nav"])',
    # Expand/show-details buttons
    '[class*="expandable"]:not([class*="filter"])',
    '[class*="collapsible"]:not([class*="filter"])',
    'button[aria-label*="expand"]:not([class*="filter"])',
    'button:has-text("+")',
    'button:has-text("Show details")',
]

_ACCORDION_MAX_CLICKS = 25  # was 5; Databricks has 15+ dept sections, Elastic has team groups


def _expand_all_accordions(page) -> int:
    """Click accordion/collapsible headers with human-like pacing."""
    clicked = 0
    for selector in _ACCORDION_HEADER_SELECTORS:
        try:
            elements = page.locator(selector).all()
        except Exception:
            continue
        for el in elements:
            if clicked >= _ACCORDION_MAX_CLICKS:
                break
            try:
                if not el.is_visible(timeout=300):
                    continue
                try:
                    aria_expanded = el.get_attribute("aria-expanded")
                    if aria_expanded == "true":
                        continue
                except Exception:
                    pass
                # Human-like: scroll, pause, click, pause
                el.scroll_into_view_if_needed(timeout=500)
                page.wait_for_timeout(random.randint(300, 800))
                el.click(timeout=1500)
                page.wait_for_timeout(random.randint(500, 1200))
                clicked += 1
            except Exception:
                continue
        if clicked >= _ACCORDION_MAX_CLICKS:
            break
    return clicked


def _register_json_capture(page, max_items: int = 50, max_bytes: int = 1_000_000) -> list:
    """
    Intercept JSON API responses using page.route() — the only safe way to
    read response bodies in Playwright's sync API.

    Using page.on("response", handler) and calling response.body() inside
    the handler triggers asyncio.CancelledError on every response because
    body() schedules an await on the already-running event loop. That error
    corrupts the event loop state and causes TargetClosedError on all
    subsequent page interactions. page.route() avoids this entirely by
    running the handler in a proper synchronous request lifecycle.
    """
    captured: list = []
    broad_captured: list = []

    def _handle_route(route):
        # Fetch the response through the route — safe to call synchronously
        try:
            response = route.fetch()
        except Exception:
            try:
                route.continue_()
            except Exception:
                pass
            return

        try:
            url = response.url or ""
            if not url or url.startswith("data:"):
                route.fulfill(response=response)
                return

            ctype = (response.headers or {}).get("content-type", "")
            ctype_lower = ctype.lower()
            is_json_ct = "json" in ctype_lower or "javascript" in ctype_lower
            is_no_ct = not ctype or ctype_lower.startswith("text/plain")

            is_primary_match = bool(_JOB_API_URL_HINTS.search(url))
            is_broad_match = bool(_BROAD_API_URL_HINTS.search(url))

            should_capture = (
                (is_primary_match or is_broad_match)
                and (is_json_ct or is_no_ct)
                and (len(captured) < max_items or len(broad_captured) < max_items)
            )

            if should_capture:
                try:
                    body = response.body()   # safe here — not inside an event handler
                    if body and len(body) <= max_bytes:
                        decoded = body.decode("utf-8", errors="ignore").strip()
                        if decoded and (decoded.startswith("{") or decoded.startswith("[")):
                            blob = json.loads(decoded)
                            if is_primary_match and len(captured) < max_items:
                                captured.append(blob)
                            elif is_broad_match and len(broad_captured) < max_items:
                                broad_captured.append(blob)
                except Exception:
                    pass

        except Exception:
            pass
        finally:
            try:
                route.fulfill(response=response)
            except Exception:
                try:
                    route.continue_()
                except Exception:
                    pass

    try:
        # Intercept likely API/data URLs; let everything else pass through normally
        page.route(
            re.compile(
                r"\.(json)(\?|$)|"
                r"/(api|v[12]|graphql|jobs|careers|positions|postings|openings|search|listings|feed)/",
                re.I,
            ),
            _handle_route,
        )
    except Exception:
        pass

    return _CaptureResult(captured, broad_captured)


class _CaptureResult(list):
    """Thin wrapper that carries both primary and broad JSON captures."""
    def __init__(self, primary, broad):
        super().__init__(primary)
        self.broad = broad or []


def _extract_spa_memory_state(page) -> list:
    """Extract in-memory SPA state (Apollo, Redux, Nuxt) from the window object."""
    extraction_script = """
    () => {
        const blobs = [];
        try {
            if (window.__APOLLO_CLIENT__) {
                const cache = window.__APOLLO_CLIENT__.cache?.extract?.();
                if (cache && cache.data) blobs.push(cache.data);
                if (cache) blobs.push(cache);
            }
            if (window.__REDUX_STORE__) blobs.push(window.__REDUX_STORE__.getState());
            if (window.__NUXT__) blobs.push(window.__NUXT__);
            if (window.__INITIAL_STATE__) blobs.push(window.__INITIAL_STATE__);
            if (window.__APP_STATE__) blobs.push(window.__APP_STATE__);
            if (window.__DATA__) blobs.push(window.__DATA__);
        } catch (e) {
            // Fail silently
        }
        return blobs;
    }
    """
    try:
        blobs = page.evaluate(extraction_script)
        if blobs:
            logger.debug("Extracted %d in-memory SPA state blobs", len(blobs))
        return blobs or []
    except Exception as exc:
        logger.debug("SPA memory state extraction failed: %s", exc)
        return []


def _scroll_and_expand(page, max_rounds: int = 5) -> str:
    """
    Scroll through the page with human-like behavior.
    Returns the HTML snapshot captured at the deepest scroll position,
    BEFORE scrolling back to top.

    Taking the snapshot before scrolling back prevents DOM virtualisation
    (used by Notion, Revolut, Glovo, etc.) from unmounting job cards that
    have scrolled out of the viewport, which would leave them missing from
    page.content() after the scroll-back.
    """
    try:
        last_height = page.evaluate("document.body.scrollHeight")
    except Exception:
        return ""

    for _ in range(max_rounds):
        # Human-like: scroll in smaller increments with pauses
        try:
            scroll_target = random.randint(int(last_height * 0.6), last_height)
            page.evaluate(f"window.scrollTo(0, {scroll_target});")
        except Exception:
            break
        page.wait_for_timeout(random.randint(800, 1500))

        # Random mouse movement to simulate human behavior
        try:
            page.mouse.move(random.randint(100, 800), random.randint(100, 600))
        except Exception:
            pass

        try:
            load_triggers = page.locator(
                "button:has-text('Load more'), button:has-text('View more'), "
                "button:has-text('View all'), button:has-text('See all'), "
                "button:has-text('Show more'), button:has-text('Show all'), "
                "a:has-text('View all'), a:has-text('See all'), "
                "a:has-text('Load more'), a:has-text('Show more'), "
                "button:has-text('Mehr laden'), button:has-text('Mehr anzeigen'), "
                "button:has-text('Alle anzeigen'), a:has-text('Alle anzeigen')"
            )
            if load_triggers.count() > 0 and load_triggers.first.is_visible():
                load_triggers.first.scroll_into_view_if_needed(timeout=500)
                page.wait_for_timeout(random.randint(400, 800))
                load_triggers.first.click(timeout=2000)
                page.wait_for_timeout(random.randint(1500, 2500))
        except Exception:
            pass

        try:
            new_height = page.evaluate("document.body.scrollHeight")
        except Exception:
            break
        if new_height <= last_height:
            break
        last_height = new_height

    # Capture HTML NOW — at the deepest scroll position — before unmounting starts
    html_at_bottom = ""
    try:
        html_at_bottom = page.content() or ""
    except Exception:
        pass

    # Scroll back to top (human-likeness only — snapshot already taken above)
    try:
        page.evaluate("window.scrollTo(0, 0);")
        page.wait_for_timeout(random.randint(300, 600))
    except Exception:
        pass

    return html_at_bottom


def _render_with_playwright(url: str, wait_ms: int = 6000, timeout: int = 30) -> dict[str, Any] | None:
    """Return a rendered snapshot using Playwright, or None if unavailable."""
    # Ensure the browser binary exists — auto-installs on first run if missing.
    # This is the fix for:
    #   BrowserType.launch: Executable doesn't exist at
    #   ~/.cache/ms-playwright/chromium_headless_shell-XXXX/chrome-headless-shell
    if not _ensure_playwright_browsers():
        logger.warning(
            "Playwright browser not available — SPA / JS-rendered career "
            "pages will return 0 jobs. Run: playwright install chromium"
        )
        return None

    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-untyped]
    except Exception as exc:
        logger.warning(
            "Playwright is not available (%s); SPA / JS-rendered career "
            "pages will return 0 jobs.",
            exc.__class__.__name__,
        )
        return None

    browser = None
    accordion_expanded = 0
    try:
        with sync_playwright() as p:
            launch_args = [
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-web-security",
                "--no-first-run",
                "--no-default-browser-check",
                "--no-pings",
            ]
            if sys.platform.startswith("linux"):
                launch_args.extend(["--no-sandbox", "--disable-setuid-sandbox"])

            browser = p.chromium.launch(
                headless=True,
                args=launch_args,
            )

            viewport_width = random.choice([1366, 1440, 1536, 1920])
            viewport_height = random.choice([768, 900, 1080])
            page = browser.new_page(
                viewport={"width": viewport_width, "height": viewport_height},
                locale="en-US",
                timezone_id="America/New_York",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    f"Chrome/{random.choice(['120', '121', '122', '123', '124', '125', '126', '127', '128', '129', '130', '131'])}.0.0.0 "
                    "Safari/537.36"
                ),
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                    "Sec-CH-UA": f'"Chromium";v="{random.choice(["120", "122", "124", "126", "128", "130", "131"])}", "Google Chrome";v="{random.choice(["120", "122", "124", "126", "128", "130", "131"])}"',
                    "Sec-CH-UA-Mobile": "?0",
                    "Sec-CH-UA-Platform": '"Windows"',
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1",
                },
            )

            _apply_stealth_patches(page)
            captured_json = _register_json_capture(page)

            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)

            initial_wait = max(wait_ms, random.randint(5000, 8000))
            page.wait_for_timeout(initial_wait)

            # If URL has a hash anchor, scroll to it immediately to trigger
            # intersection-observer lazy-loaded sections (e.g. #open-roles, #positions)
            _fragment = urlparse(url).fragment
            if _fragment:
                try:
                    page.evaluate(f"""
                        const el = document.getElementById('{_fragment}')
                                || document.querySelector('[name="{_fragment}"]')
                                || document.querySelector('.{_fragment}');
                        if (el) {{
                            el.scrollIntoView({{behavior: 'instant', block: 'start'}});
                        }}
                    """)
                    page.wait_for_timeout(2500)
                except Exception:
                    pass

            cookie_dismissed = _dismiss_cookie_banners(page)

            # Let page settle after cookie dismiss before any interactions
            page.wait_for_timeout(random.randint(1000, 2000))

            found_jobs = _wait_for_job_elements(page, timeout_ms=min(timeout * 1000 // 2, 8000))
            accordion_expanded = 0

            accordion_expanded = _expand_all_accordions(page)
            if accordion_expanded:
                logger.debug(
                    "Expanded %d accordion(s) on %s", accordion_expanded, url
                )
                # Longer settle after accordion expansion
                page.wait_for_timeout(random.randint(2500, 4000))

            # _scroll_and_expand returns HTML captured at deepest scroll depth
            # (before DOM virtualisation unmounts off-screen cards on scroll-back)
            bottom_html = _scroll_and_expand(page)

            # Final settle before extraction — let any late XHR/fetch complete
            page.wait_for_timeout(random.randint(2000, 3500))
            found_jobs = _wait_for_job_elements(page, timeout_ms=5000)

            html_snapshots = []
            if bottom_html:
                html_snapshots.append(bottom_html)
            html_snapshots.append(page.content() or "")

            for page_idx in range(4):
                try:
                    next_button = page.locator(
                        "button[aria-label*='Next'], button[aria-label*='next'], "
                        "a[aria-label*='Next'], a[aria-label*='next'], "
                        "[class*='pagination'] button:has-text('>'), "
                        "[class*='pager'] button:has-text('>'), "
                        "[class*='Pagination'] button:has-text('>')"
                    )

                    if next_button.count() > 0 and next_button.first.is_visible() and next_button.first.is_enabled():
                        next_button.first.click(timeout=2000)
                        page.wait_for_timeout(3000)
                        html_snapshots.append(page.content() or "")
                    else:
                        break
                except Exception:
                    break

            html = "\n<!-- PAGE_SPLIT -->\n".join(html_snapshots)

            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

            page.wait_for_timeout(1500)

            memory_state_blobs = _extract_spa_memory_state(page)
            if memory_state_blobs:
                if isinstance(captured_json, list):
                    captured_json.extend(memory_state_blobs)

            # Determine bot_blocked carefully.
            # Critical rule: if Playwright successfully interacted with the page
            # (expanded accordions, found job elements, dismissed cookie banners),
            # it is NOT a bot challenge page — challenge pages have no accordions
            # to expand and no job elements to find.
            # Without this guard, Cloudflare Analytics scripts embedded on real
            # pages trigger false positives (e.g. Bolt.eu expands 25 accordions
            # but is still flagged because CF analytics appears in page source).
            page_was_interactive = (
                accordion_expanded > 0
                or found_jobs
                or cookie_dismissed > 0
            )
            _bot_blocked = is_bot_blocked(html) and not page_was_interactive

            if _bot_blocked:
                logger.info("Bot challenge on %s — will skip portal crawl and use connector fallback", url)
            elif is_bot_blocked(html) and page_was_interactive:
                logger.debug(
                    "Bot marker detected in HTML for %s but page was interactive "
                    "(accordions=%d, found_jobs=%s, cookie_dismissed=%d) — "
                    "treating as false positive, not blocking.",
                    url, accordion_expanded, found_jobs, cookie_dismissed,
                )

            return {
                "url": page.url or url,
                "title": _extract_title(html),
                "html": html,
                "status": 200,
                "rendered_by": "playwright",
                "bot_blocked": _bot_blocked,
                "found_jobs": found_jobs,
                "captured_json": captured_json,
                "accordion_expanded": accordion_expanded,
                "cookie_dismissed": cookie_dismissed,
            }
    except Exception as exc:
        logger.debug("Playwright render failed for %s: %s", url, exc)
        return {"url": url, "title": "", "html": "", "status": 0, "error": str(exc), "bot_blocked": False, "found_jobs": False, "captured_json": [], "accordion_expanded": 0, "cookie_dismissed": 0}
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def fetch_rendered_html(
    url: str,
    wait_ms: int = 2500,
    timeout: int = 25,
    force_browser: bool = False,
) -> dict[str, Any]:
    """Fetch a URL and return {url, title, html, status}."""
    if force_browser:
        rendered = _render_with_playwright(url, wait_ms=wait_ms, timeout=timeout)
        if rendered and rendered.get("html"):
            return rendered

    try:
        with http_session() as session:
            response = session.get(url, timeout=timeout)
            html = response.text or ""
            title = _extract_title(html)

            blocked = is_bot_blocked(html, response.status_code)

            static = {
                "url": response.url,
                "title": title,
                "html": html,
                "status": response.status_code,
                "rendered_by": "requests",
                "bot_blocked": blocked,
                "found_jobs": False,
                "captured_json": [],
            }

            if blocked:
                logger.info("Bot-blocked response from %s — upgrading to Playwright", url)
                rendered = _render_with_playwright(url, wait_ms=wait_ms, timeout=timeout)
                if rendered and rendered.get("html"):
                    return rendered
                return static

            should_render = (
                not response.ok
                or _looks_dynamic(html)
                or _looks_like_careers_url(url)
            )

            if not should_render:
                return static

            rendered = _render_with_playwright(url, wait_ms=wait_ms, timeout=timeout)
            if rendered and rendered.get("html"):
                rendered_html = rendered.get("html") or ""
                if len(rendered_html) >= len(html) or _looks_like_careers_url(url):
                    return rendered

            return static
    except Exception as exc:
        fallback = _render_with_playwright(url, wait_ms=wait_ms, timeout=timeout)
        if fallback and fallback.get("html"):
            return fallback
        return {"url": url, "title": "", "html": "", "status": 0, "error": str(exc), "rendered_by": "error", "bot_blocked": False, "found_jobs": False, "captured_json": []}
