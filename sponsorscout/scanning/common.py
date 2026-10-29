"""Shared constants and concurrency-budget helpers across scanner phases.

The scanner runs in two phases (ATS API boards first, then career portals).
To avoid blowing past machine memory or locking up low-end laptops, this module
adapts concurrency to the host's actual cores and available RAM.
"""

from __future__ import annotations

import logging
import os
import ssl
import threading
import time

logger = logging.getLogger(__name__)

# Absolute floor: pretend the machine has at least this much available RAM so
# temporary spikes in another app cannot permanently starve the scan.
_MIN_USABLE_RAM = 2.0 * (1024 ** 3)  # 2 GiB

# Low-memory / low-core machine cutoffs (strict conservative mode for <=8 GB RAM or <=4 CPU cores):
_STRICT_MAX_CPUS = 4
_STRICT_MAX_RAM_GIB = 8.5
_STRICT_MIN_AVAIL_GIB = 2.5


def host_workers_limits() -> tuple[int, float, float]:
    """Return (cpu_count, total_ram_bytes, available_ram_bytes)."""
    cpu = os.cpu_count() or 2
    total_ram = 8.0 * (1024 ** 3)
    avail_ram = 6.0 * (1024 ** 3)

    try:
        import psutil
        vm = psutil.virtual_memory()
        return cpu, float(vm.total), float(vm.available)
    except Exception:
        pass

    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return cpu, float(stat.ullTotalPhys), float(stat.ullAvailPhys)
    except Exception:
        pass

    try:
        if os.path.exists("/proc/meminfo"):
            mem = {}
            with open("/proc/meminfo", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = parts[1].strip().split()[0]
                        mem[key] = int(val) * 1024
            if "MemTotal" in mem:
                tot = float(mem["MemTotal"])
                avail = float(mem.get("MemAvailable", mem.get("MemFree", int(tot * 0.6))))
                return cpu, tot, avail
    except Exception:
        pass

    try:
        import subprocess
        import sys as _sys
        if _sys.platform == "darwin":
            tot_s = subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=2).decode().strip()
            tot = float(int(tot_s))
            vm_out = subprocess.check_output(["vm_stat"], timeout=2).decode()
            page_sz = 4096
            free_pages = 0
            for ln in vm_out.splitlines():
                if "page size of" in ln:
                    for tok in ln.split():
                        if tok.isdigit():
                            page_sz = int(tok)
                            break
                elif any(ln.startswith(k) for k in ("Pages free:", "Pages inactive:", "Pages speculative:")):
                    num = ln.split(":", 1)[1].strip().rstrip(".")
                    if num.isdigit():
                        free_pages += int(num)
            avail = float(free_pages * page_sz) if free_pages > 0 else tot * 0.6
            return cpu, tot, avail
    except Exception:
        pass

    return cpu, total_ram, avail_ram


def set_high_performance(enabled: bool) -> None:
    """Enable or disable High-Performance (Turbo) concurrency scaling at runtime."""
    os.environ["SPONSORSCOUT_HIGH_PERFORMANCE"] = "1" if enabled else "0"


def is_high_performance() -> bool:
    """Return True when explicit High Performance / Turbo override is enabled."""
    return (
        os.environ.get("SPONSORSCOUT_HIGH_PERFORMANCE", "").strip().lower() in ("1", "true", "yes", "on")
        or os.environ.get("SPONSORSCOUT_TURBO", "").strip().lower() in ("1", "true", "yes", "on")
    )


def is_strict_low_spec(
    cpu: int | None = None,
    ram_total_bytes: float | None = None,
    ram_avail_bytes: float | None = None,
) -> bool:
    """Return True on dual/quad-core CPUs or machines with <=8 GB RAM (or tight free RAM)."""
    if cpu is None or ram_total_bytes is None or ram_avail_bytes is None:
        cpu, ram_total_bytes, ram_avail_bytes = host_workers_limits()
    gib = 1024 ** 3
    total_gib = ram_total_bytes / gib
    avail_gib = ram_avail_bytes / gib
    return (cpu <= _STRICT_MAX_CPUS) or (total_gib <= _STRICT_MAX_RAM_GIB) or (avail_gib < _STRICT_MIN_AVAIL_GIB)


def recommended_workers(kind: str = "browser", requested: int | None = None) -> int:
    """Hardware-aware concurrency level suited to *this* machine.

    Scaling policy:
    - **Strict conservative tier** (`cpu <= 4` OR `total_ram <= 8.5 GiB` OR
      `avail_ram < 2.5 GiB`): strictly caps threads and headless browsers so
      dual/quad-core and <=8 GB laptops never stutter or swap.
    - **Capable desktop auto-boost tier** (`cpu > 4` AND `total_ram > 8.5 GiB`):
      automatically boosts multi-threaded company/HTTP pools and parallel
      background headless Chrome browsers while reserving >=2 CPU threads and
      >=3.0 GiB of free RAM so normal PC usage stays completely lag-free.
    - **Optional High Performance (Turbo) override**: raises upper ceilings further
      on capable hardware while still preserving OS responsiveness headroom.
    - Honors explicit `SPONSORSCOUT_MAX_WORKERS` (browser) and
      `SPONSORSCOUT_HTTP_WORKERS` (http).
    """
    turbo = is_high_performance()

    if kind in ("browser", "http"):
        raw_env = os.environ.get(
            "SPONSORSCOUT_MAX_WORKERS" if kind == "browser" else "SPONSORSCOUT_HTTP_WORKERS"
        )
        if raw_env:
            try:
                val = int(raw_env.strip())
                if val >= 1:
                    cap = 8 if kind == "browser" else (32 if turbo else 24)
                    n = min(val, cap)
                    if requested is not None:
                        try:
                            n = max(1, min(int(requested), n))
                        except (TypeError, ValueError):
                            pass
                    return n
            except (ValueError, TypeError):
                pass

    cpu, ram_total, ram_avail = host_workers_limits()
    gib = 1024 ** 3
    total_gib = ram_total / gib
    raw_avail_gib = ram_avail / gib
    avail_gib = max(ram_avail, _MIN_USABLE_RAM) / gib

    if is_strict_low_spec(cpu, ram_total, ram_avail):
        very_tight = (cpu <= 2) or (total_gib <= 5.0) or (raw_avail_gib < 2.0)
        if kind == "http":
            n = 3 if very_tight else (10 if turbo else 8)
        elif kind == "company":
            n = 2 if very_tight else (5 if turbo else 4)
        else:  # browser
            n = 1 if very_tight else 2
    else:
        # Capable machine (>4 cores and >8 GB RAM): reserve OS/desktop headroom
        # so background scans never cause UI/desktop lag.
        reserve_cpu = max(1, cpu // 8) if turbo else max(2, cpu // 4)
        reserve_ram_gib = 2.0 if turbo else 3.0
        usable_cpu = max(2, cpu - reserve_cpu)
        usable_ram_gib = max(1.2, min(avail_gib - reserve_ram_gib, total_gib * (0.65 if turbo else 0.55)))

        if kind == "http":
            mult = 4 if turbo else 3
            ram_factor = 4.5 if turbo else 3.5
            cap = 32 if turbo else 24
            n = max(10, min(usable_cpu * mult, int(usable_ram_gib * ram_factor), cap))
        elif kind == "company":
            mult = 3 if turbo else 2
            ram_factor = 3.2 if turbo else 2.5
            cap = 24 if turbo else 16
            n = max(6, min(usable_cpu * mult, int(usable_ram_gib * ram_factor), cap))
        else:  # parallel background headless Chrome browsers
            per_browser_gib = 0.45 if turbo else 0.55
            by_ram = max(2, int(usable_ram_gib / per_browser_gib))
            by_cpu = max(2, (usable_cpu * 2 // 3) if turbo else (usable_cpu // 2))
            cap = 8 if turbo else 6
            n = max(2, min(by_cpu, by_ram, cap))

    if requested is not None:
        try:
            n = max(1, min(int(requested), n))
        except (TypeError, ValueError):
            pass
    return n


def describe_host_budget() -> str:
    """One-line hardware & worker budget summary for scan logs."""
    cpu, ram_total, ram_avail = host_workers_limits()
    total_mb = ram_total / (1024 * 1024)
    avail_mb = ram_avail / (1024 * 1024)
    tier = "strict-low-spec" if is_strict_low_spec(cpu, ram_total, ram_avail) else (
        "high-perf-turbo" if is_high_performance() else "auto-boost"
    )
    return (
        f"host ({tier}): {cpu} cpu, {avail_mb:.0f} MB available RAM "
        f"(of {total_mb:.0f} MB total) -> "
        f"browser workers={recommended_workers('browser')}, "
        f"company workers={recommended_workers('company')}, "
        f"http workers={recommended_workers('http')}"
    )


BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-breakpad",
    "--disable-component-extensions-with-background-pages",
    "--disable-default-apps",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--disable-features=Translate,BackForwardCache,AcceptCHFrame,MediaRouter,OptimizationHints",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-ipc-flooding-protection",
    "--disable-renderer-backgrounding",
    "--blink-settings=imagesEnabled=false",
    "--mute-audio",
    "--no-default-browser-check",
    "--no-first-run",
    "--no-pings",
    "--no-sandbox",
    "--renderer-process-limit=3",
]

LOW_RESOURCE_BROWSER_ARGS = BROWSER_ARGS


class ScanCancelled(Exception):
    """Raised when cooperative cancellation interrupts a scan worker."""
    pass


def check_control(cancel_event: threading.Event | None,
                  pause_event: threading.Event | None,
                  poll_sec: float = 0.25) -> bool:
    """Cooperative cancellation and pause checkpoint for scanner loops.

    Returns True if cancelled (caller should exit immediately).
    Blocks if paused until resumed or cancelled.
    """
    if cancel_event is not None and cancel_event.is_set():
        return True
    if pause_event is not None and pause_event.is_set():
        while pause_event.is_set():
            if cancel_event is not None and cancel_event.is_set():
                return True
            time.sleep(poll_sec)
    return bool(cancel_event is not None and cancel_event.is_set())


def check_cancelled(cancel_event: threading.Event | None):
    """Raise ScanCancelled if cancel_event is set."""
    if cancel_event is not None and cancel_event.is_set():
        raise ScanCancelled("Scan cancelled by user")


def sleep_interruptible(seconds: float, cancel_event: threading.Event | None = None,
                        pause_event: threading.Event | None = None, step: float = 0.05) -> bool:
    """Sleep for ``seconds`` while checking cancellation and pause events.

    Returns True if slept completely without cancellation.
    Returns False if cancelled.
    """
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check_control(cancel_event, pause_event, poll_sec=step):
            return False
        time.sleep(min(step, max(0.005, end - time.monotonic())))
    return True


def tls_context() -> ssl.SSLContext:
    """Create a verified SSLContext using OS truststore when available."""
    try:
        import truststore
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx
    except Exception:
        return ssl.create_default_context()


def workday_spaced_location(raw: str) -> str | None:
    """Normalize Workday location codes like 'IT - Venegono Superiore' -> 'Venegono Superiore, IT'
    or 'GB - 12 High Street' -> 'GB'. Returns None if raw does not match code format.
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    # Match patterns like: "IT - Venegono Superiore", "GB - Yeovil - Lysander Rd", "NLD - Alphen Aan Den Rijn"
    parts = [p.strip() for p in s.split(" - ") if p.strip()]
    if len(parts) >= 2:
        code = parts[0]
        # Country code: 2-3 uppercase letters
        if (len(code) in (2, 3)) and code.isalpha() and code.isupper():
            # Check remaining parts for city/address
            rest = parts[1:]
            # If second part starts with digits like "12 High Street", return country only
            if rest and rest[0] and rest[0][0].isdigit():
                return code
            # Clean off company suffixes like "S.A." or trailing address segments
            city = rest[0]
            if city.endswith(" S.A."):
                city = city[:-5].strip()
            return f"{city}, {code}"
    return None


# Listing outcomes are independent of enrichment coverage. A cap/repeat stop
# retains rows but cannot honestly claim the board was fully traversed.
def is_dns_failure(*messages) -> bool:
    text = ' '.join(str(value or '') for value in messages).casefold()
    return any(marker in text for marker in (
        'err_name_not_resolved','does not resolve (dns)','temporary failure in name resolution',
        'name or service not known','nodename nor servname','eai_again','dns lookup failed'))


def completion_status(found: int,error: str = '',diagnostics=()) -> str:
    import re
    if error:
        return 'partial' if found else 'error'
    if not found:
        return 'empty'
    pieces = [diagnostics] if isinstance(diagnostics,str) else list(diagnostics or [])
    listing = ' | '.join(str(piece) for piece in pieces if not re.search(
        r'(?i)^(?:\[?detail\]?|jd enrichment|enrichment activity|detail activity)',str(piece)))
    incomplete = re.search(
        r'(?i)(?:blocked access:|blocked by bot protection:|challenge page served instead of|JD coverage incomplete:|listing completeness unverified|listing incomplete:|page\s+cap[^|]*reached|(?:dom|listing|row)\s+cap[^|]*(?:reached|more)|'
        r'pagination\s+(?:stopped|cap|incomplete)|\bboard has more pages\b|'
        r'\bpages? of repeats\b|no new listings on \d+ consecutive pages|'
        r're-served the same listing|listing[^|]*time budget[^|]*exhausted)',listing)
    return 'partial' if incomplete else 'ok'


def reconcile_scan_artifacts(scan_log_csv,main_csv,quarantine_csv,run_id='',merge_info=None):
    """Audit final actions into the log, not guessed late quarantine increments.

    Jobs Found retains the observed pre-merge number. Jobs Retained attributes
    each final row once; Jobs Merged is explicitly separate from quarantine.
    Retried targets have one latest outcome plus an Attempts count.
    """
    from collections import Counter
    from sponsorscout.core.job_identity import read_job_csv,write_job_csv
    rows = read_job_csv(scan_log_csv)
    if not rows:
        return {}
    selected,order = {},[]
    for row in rows:
        key = tuple(row.get(field,'') for field in ('Run ID','Seed Name','Seed URL','Target Country'))
        if key not in selected:
            order.append(key)
            selected[key] = dict(row)
            selected[key]['Attempts'] = int(row.get('Attempts') or 1)
        else:
            attempts = selected[key]['Attempts'] + 1
            duration = float(selected[key].get('Duration Sec') or 0) + float(row.get('Duration Sec') or 0)
            selected[key] = dict(row)
            selected[key]['Attempts'] = attempts
            selected[key]['Duration Sec'] = round(duration,1)
    rows = [selected[key] for key in order]
    current = [i for i,row in enumerate(rows) if not run_id or row.get('Run ID')==run_id]
    retained,quarantined,merged = Counter(),Counter(),Counter()

    def owner(job):
        names = {name.strip().casefold() for name in str(job.get('Seed Name') or job.get('Company Name') or '').split(';') if name.strip()}
        choices = [i for i in current if str(rows[i].get('Seed Name') or rows[i].get('Company') or '').strip().casefold() in names]
        if not choices:
            return None
        company = str(job.get('Company Name') or '').strip().casefold()
        direct = [i for i in choices if str(rows[i].get('Seed Name') or '').strip().casefold()==company]
        if direct:
            choices = direct
        target = str(job.get('Target Country') or '').strip().casefold()
        scoped = [i for i in choices if str(rows[i].get('Target Country') or '').strip().casefold()==target]
        if scoped:
            choices = scoped
        url = str(job.get('Job URL') or '')
        return max(choices,key=lambda i: len(str(rows[i].get('Seed URL') or '')) if url.startswith(str(rows[i].get('Seed URL') or '\0')) else 0)

    jobs = read_job_csv(main_csv)
    quarantine = read_job_csv(quarantine_csv)
    for job in jobs:
        index = owner(job)
        if index is not None:
            retained[index] += 1
    for job in quarantine:
        if run_id and job.get('Run ID') and job.get('Run ID')!=run_id:
            continue
        index = owner(job)
        if index is not None:
            quarantined[index] += 1
    for seed,count in (merge_info or {}).get('by_seed',{}).items():
        names = {name.strip() for name in str(seed).split(';')}
        choices = [i for i in current if rows[i].get('Seed Name') in names]
        for i in choices:
            amount = min(int(count),max(0,int(rows[i].get('Jobs Found') or 0)-retained[i]))
            merged[i] += amount
            count -= amount
        if count and choices:
            merged[choices[0]] += int(count)
    for i in current:
        row = rows[i]
        row['Quarantined'] = quarantined[i]
        row['Jobs Retained'] = retained[i]
        row['Jobs Merged'] = merged[i]
        row['Status'] = completion_status(int(row.get('Jobs Found') or 0),row.get('Error') or '',[row.get('Diagnostics') or ''])
        # Keep repeated reconciliations idempotent.
        diagnostic = str(row.get('Diagnostics') or '').split(' | final: found=')[0]
        row['Diagnostics'] = diagnostic + (
            f" | final: found={row.get('Jobs Found') or 0}; retained={retained[i]}; "
            f"merged={merged[i]}; quarantined={quarantined[i]}")
    columns = list(rows[0])
    for name in ('Jobs Retained','Jobs Merged','Attempts'):
        if name not in columns:
            columns.append(name)
    write_job_csv(scan_log_csv,rows,columns)
    return {'written':len(jobs),'quarantined':sum(quarantined.values()),
            'merged':sum(merged.values()),'targets':len(current),
            'partial':sum(rows[i].get('Status')=='partial' for i in current)}
