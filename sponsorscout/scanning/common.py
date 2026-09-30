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


def recommended_workers(kind: str = "browser") -> int:
    """Concurrency level suited to *this* machine, not to a dev workstation.

    Each Playwright Chromium instance costs roughly 150-400 MB resident, so the
    previously hard-coded pools (3 concurrent browsers, 6 concurrent detail
    fetches) can exhaust the RAM of an 8 GB laptop and stall the whole OS —
    exactly the "scanning freezes my system" failure mode.

    Sizing uses *available* RAM (floored at half of total, so a transient dip
    under memory pressure cannot starve the scan to a single worker forever),
    not total RAM.  Both phases draw on the same budget: on a 2-core / 8 GB box
    the browser pool correctly drops to 1, but the lightweight HTTP pool used
    to be computed independently from *total* RAM and still returned 4 — so a
    career scan ran four concurrent fetchers on top of one Chromium on two
    cores.

    ``kind``:
      * ``"browser"`` — concurrent browser contexts for the career crawl.
      * ``"http"``    — concurrent lightweight HTTP detail fetches.

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
            return 3 if cpu >= 2 else 2
        return max(2, min(cpu * 2, 8))
    # browser contexts: the heavy case
    if ram < _LOW_MEMORY_RAM or cpu <= 2:
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