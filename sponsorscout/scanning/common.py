"""Shared constants and tiny helpers for the scanning package.

Extracted from ats_portal_scanner.py / career_scanner.py.

Only what both scanners genuinely share lives here: the lean low-resource
Chromium flag set (``BROWSER_ARGS`` / ``LOW_RESOURCE_BROWSER_ARGS``) and
host-adaptive pool sizing (``recommended_workers``).  Each scanner keeps its
own text helpers and CSV schemas next to the code that uses them (39-column
output / 15-column scan log / error log), so ``tools/check_dev_sync.py`` can
keep every copy honest against its dev script.
"""


def host_workers_limits() -> tuple[int, int]:
    """Return ``(cpu_count, total_ram_bytes)`` for this machine.

    RAM is read via ``GlobalMemoryStatusEx`` on Windows and ``sysconf`` on
    POSIX; when neither works (exotic platform / sandbox) a conservative
    8 GiB is assumed so the pool sizing stays *small* rather than optimistic.
    """
    import os

    cpu = os.cpu_count() or 2

    ram = 0
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
        else:
            ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except Exception:
        ram = 0

    if ram <= 0:
        ram = 8 * 1024 ** 3
    return cpu, ram


def recommended_workers(kind: str = "browser") -> int:
    """Concurrency level suited to *this* machine, not to a dev workstation.

    Each Playwright Chromium instance costs roughly 150-400 MB resident, so the
    previously hard-coded pools (3 concurrent browsers, 6 concurrent detail
    fetches) can exhaust the RAM of an 8 GB laptop and stall the whole OS —
    exactly the "scanning freezes my system" failure mode.

    ``kind``:
      * ``"browser"`` — concurrent browser contexts for the career crawl.
      * ``"http"``    — concurrent lightweight HTTP detail fetches.

    Returned values are always >= 1 and deliberately conservative; the scans
    stay correct at any concurrency, they are merely slower.
    """
    cpu, ram = host_workers_limits()
    if kind == "http":
        if ram < 6 * 1024 ** 3:
            return 3 if cpu >= 2 else 2
        return max(2, min(cpu * 2, 8))
    # browser contexts: the heavy case
    if ram < 6 * 1024 ** 3 or cpu <= 2:
        return 1
    if cpu <= 4:
        return 2
    return max(2, min(cpu // 2, 4))


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