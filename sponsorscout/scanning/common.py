"""Shared constants and tiny helpers for the scanning package.

Extracted from ats_portal_scanner.py / career_scanner.py.

Only what both scanners genuinely share lives here: the lean low-resource
Chromium flag set (``BROWSER_ARGS`` / ``LOW_RESOURCE_BROWSER_ARGS``),
host-adaptive pool sizing (``recommended_workers``) and the pause/stop gate
(``check_control``). Each scanner keeps its own text helpers and CSV schemas
next to the code that uses them, preserving the pipeline's 39-column jobs
output and 15-column scan log.
"""

def check_control(cancel_event, pause_event=None, poll_sec: float = 0.1) -> bool:
    """Wait out a pause, then report whether the scan must stop.

    Returns ``True`` when the scan was cancelled — the caller must abort
    immediately. Returns ``False`` when scanning may continue.

    This is the single gate behind both desktop controls:

    * **Pause** suspends the calling worker *in place* (``pause_event`` set):
      no new work is started, browsers stay open, and the run keeps its
      identity, so Resume continues instantly with no database round-trip.
    * **Stop** always wins over a Pause — pressing Stop while paused still
      ends the scan promptly instead of deadlocking behind the pause.

    Both events may be ``None`` (CLI and tests), in which case this returns
    ``False`` without blocking.
    """
    if pause_event is not None and pause_event.is_set():
        while pause_event.is_set():
            if cancel_event is not None and cancel_event.is_set():
                return True
            pause_event.wait(poll_sec)
    return cancel_event is not None and cancel_event.is_set()


#: Absolute memory floor for pool sizing (2 GiB).  Sizing reads AVAILABLE RAM,
#: not total; this only prevents a transient dip from pinning a scan to one
#: worker for the whole run.
_MIN_USABLE_RAM = 2 * 1024 ** 3

#: Below this, we assume a low-end machine and stay deliberately small.
_LOW_MEMORY_RAM = 6 * 1024 ** 3


def host_workers_limits() -> tuple[int, int, int]:
    """Return ``(cpu_count, total_ram_bytes, available_ram_bytes)`` for this machine.

    RAM is read via ``GlobalMemoryStatusEx`` on Windows and ``sysconf`` on
    POSIX; when neither works (exotic platform / sandbox) a conservative
    8 GiB is assumed so the pool sizing stays *small* rather than optimistic.

    Available RAM is reported alongside total because total alone is
    misleading on a low-end machine: a 16 GB box that is currently 14 GB into
    swap reads as "plenty of memory" while it is in fact thrashing.  Sizing
    pools on total was how a single scan could still exhaust a machine.
    """
    import os

    cpu = os.cpu_count() or 2

    ram = avail = 0
    try:
        if os.name == "nt":
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                ram = int(stat.ullTotalPhys)
                avail = int(stat.ullAvailPhys)
        else:
            ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            try:
                avail = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
            except (ValueError, OSError, AttributeError):
                avail = 0
    except Exception:
        ram = avail = 0

    if ram <= 0:
        ram = 8 * 1024 ** 3
    if avail <= 0:
        avail = ram
    return cpu, ram, avail


def recommended_workers(kind: str = "browser", requested: int | None = None) -> int:
    """Concurrency level suited to *this* machine, not to a dev workstation.

    Each Playwright Chromium instance costs roughly 150-400 MB resident, so the
    previously hard-coded pools (3 concurrent browsers, 6 concurrent detail
    fetches) can exhaust the RAM of an 8 GB laptop and stall the whole OS —
    exactly the "scanning freezes my system" failure mode.

    Sizing uses *available* RAM (floored at ``_MIN_USABLE_RAM``), not total
    RAM.  Both phases draw on the same budget: on a 2-core / 8 GB box the
    browser pool correctly drops to 1, but the lightweight HTTP pool used to be
    computed independently from *total* RAM and still returned 4 — so a career
    scan ran four concurrent fetchers on top of one Chromium on two cores.

    ``kind``:
      * ``"browser"`` — concurrent browser contexts for the career crawl.
      * ``"http"``    — concurrent lightweight HTTP detail fetches.

    ``requested`` caps the result without ever raising it, so a caller that
    knows a tighter budget (or a test) can clamp it.

    Returned values are always >= 1 and deliberately conservative; the scans
    stay correct at any concurrency, they are merely slower.
    """
    cpu, ram_total, ram_avail = host_workers_limits()
    # Use available RAM, floored at 2 GiB.  The floor exists only so a
    # momentary spike in another application's memory cannot pin a scan to a
    # single worker forever — but the floor must be an ABSOLUTE one, not a
    # fraction of total: on a 32 GB box that is currently swapping, total/2 is
    # 16 GB and would hand back the very large pool the machine cannot run.
    ram = max(ram_avail, _MIN_USABLE_RAM)
    if kind == "http":
        if ram < _LOW_MEMORY_RAM:
            n = 3 if cpu >= 2 else 2
        else:
            n = max(2, min(cpu * 2, 8))
    elif ram < _LOW_MEMORY_RAM or cpu <= 2:
        # browser contexts: the heavy case
        n = 1
    elif cpu <= 4:
        n = 2
    else:
        n = max(2, min(cpu // 2, 4))
    if requested is not None:
        try:
            n = max(1, min(int(requested), n))
        except (TypeError, ValueError):
            pass
    return n


# Lean Chromium flags.  ``--blink-settings=imagesEnabled=false`` alone removes
# the bulk of the download/render work on image-heavy career pages, and the
# remaining flags stop background networking that costs CPU and bandwidth
# without contributing a single job row.  The last three cap the renderer's
# process count and V8 old-space so a stray heavy board cannot balloon a
# worker's RAM during a long crawl.
BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-http2",
    "--ignore-certificate-errors",
    "--blink-settings=imagesEnabled=false",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-client-side-phishing-detection",
    "--disable-default-apps",
    "--disable-extensions",
    "--disable-features=TranslateUI,BlinkGenPropertyTrees,MediaRouter",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-sync",
    "--disable-translate",
    "--metrics-recording-only",
    "--mute-audio",
    "--no-first-run",
    "--no-default-browser-check",
    "--renderer-process-limit=2",
    "--js-flags=--max-old-space-size=512",
]

# Back-compat alias: both scanners (and the dev scripts they are synced from)
# refer to the low-resource launch flags by this name.  Keeping one definition
# here means a flag added for the ATS phase can never be missing from the
# career phase.
LOW_RESOURCE_BROWSER_ARGS = BROWSER_ARGS

# ── test stand-ins for the cancellation API the app provides (2026-10-06) ──
# The installed career_scanner imports these at module level; a loud
# ImportError is correct in production, so the HARNESS must supply them.
class ScanCancelled(Exception):
    """Raised when the UI's Stop button aborts a scan."""


def check_cancelled(cancel_event=None):
    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        raise ScanCancelled("scan cancelled")
    return False


def sleep_interruptible(seconds, cancel_event=None, pause_event=None):
    """Sleep for `seconds`, waking early on Stop.

    Returns True when the full time elapsed, False when cancel_event was set.
    Callers read False as "stop now", so this must really sleep: the stand-in
    that returned False after 0.05 s made every HTTP retry backoff and the DNS
    retry abort the company at once (2026-10-19). While paused, the clock does
    not advance.
    """
    import time as _t
    check_cancelled(cancel_event)
    end = _t.monotonic() + max(0.0, float(seconds or 0))
    while True:
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            return False
        if pause_event is not None and getattr(pause_event, "is_set", lambda: False)():
            end += 0.2
            _t.sleep(0.2)
            continue
        remaining = end - _t.monotonic()
        if remaining <= 0:
            return True
        _t.sleep(min(0.2, remaining))


# ── Workday spaced site codes (2026-10-19) ───────────────────────────────────
# Leonardo's Workday board prints "IT - Venegono Superiore", "GB - Yeovil -
# Lysander Rd", "PL - Świdnik S.A.", "NLD - Alphen Aan Den Rijn". The career
# sanitiser rejected every one of them (about 186 blank locations). The ATS
# scanner has the same rule in format_location; this copy serves the career side.
_WD_SPACED_ISO2 = {
    "IT", "GB", "PL", "DE", "FR", "ES", "NL", "AT", "BE", "CH", "SE", "NO", "DK",
    "FI", "IE", "PT", "CZ", "RO", "HU", "GR", "BG", "HR", "SK", "SI", "LT", "LV",
    "EE", "LU", "US", "CA", "IN", "JP", "CN", "KR", "SG", "AU", "NZ", "BR", "MX",
    "AR", "CL", "ZA", "AE", "EG", "IL", "TR", "UA", "RU",
    "USA", "IND", "CAN", "GBR", "DEU", "FRA", "ESP", "ITA", "NLD", "POL", "AUT",
    "BEL", "CHE", "SWE", "NOR", "DNK", "FIN", "PRT", "IRL", "CZE", "ROU", "HUN",
    "GRC", "BGR", "HRV", "SVK", "SVN", "LTU", "LVA", "EST", "LUX",
}
_WD_STREET_RE = None


def workday_spaced_location(location):
    """'IT - Venegono Superiore' -> 'Venegono Superiore, IT'.

    Returns None when the text is not a spaced site code, so callers fall back
    to their normal handling. A street-like second segment yields the country
    code alone, never a guessed city.
    """
    import re as _re
    global _WD_STREET_RE
    if _WD_STREET_RE is None:
        _WD_STREET_RE = _re.compile(
            r"\b(rue|strasse|stra(?:ss|ß)e|road|rd|street|avenue|via|calle|blvd|"
            r"boulevard|drive|lane|weg|allee|platz|route|chemin)\b|\d", _re.I)
    m = _re.match(r"^([A-Z]{2,3})\s+-\s+(.+)$", str(location or "").strip())
    if not m or m.group(1) not in _WD_SPACED_ISO2:
        return None
    cc = m.group(1)
    city = _re.split(r"\s+-\s+", m.group(2))[0].strip()
    city = _re.sub(r"\s+(S\.p\.A\.|S\.A\.|Loc\.?)\s*$", "", city).strip()
    if city and not _WD_STREET_RE.search(city):
        return f"{city}, {cc}"
    return cc


def tls_context():
    """SSL context that trusts the operating system's certificate store.

    2026-10-19: Spendesk (career.spendesk.com) failed on a user's Windows
    machine with CERTIFICATE_VERIFY_FAILED ("unable to get local issuer
    certificate"). The server sends a complete chain (Certainly Intermediate
    R1 under Starfield Root CA G2). Python's bundled CA list lacks that root,
    while browsers and Windows use the OS store. ``truststore`` makes ssl use
    the OS store. Verification stays ON in every case; if truststore is not
    installed we fall back to the default context.
    """
    import ssl
    try:
        import truststore  # noqa: WPS433 (optional, pinned in requirements)
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return ssl.create_default_context()
