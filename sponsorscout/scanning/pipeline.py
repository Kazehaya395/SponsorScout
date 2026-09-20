"""Scan pipeline: orchestrates the ATS + career scanners and ingests results.

Design (locked with the project owner):

* The scanners keep their proven CLI behaviour — they write their own CSV
  artifacts (jobs, recruiter split, quarantine, per-run scan log) into
  ``paths.SCAN_OUTPUT_DIR``.  Nothing in the scanners was rewritten; only
  cooperative-cancel hooks and the progress shim were added.
* This module runs them (ATS first — it is API-fast — then the career
  crawler), then ingests the *accepted* job rows into the SQLite DB via
  ``persistence.upsert_job`` and copies the per-company scan-log rows into
  the ``scan_log`` table so the Tools tab can show per-scan evidence.
* Quarantined rows are never ingested as jobs (they stay in the quarantine
  CSV artifacts); their counts arrive via the scan-log rows.
* ``eu_blue_card`` / ``has_relocation`` (legacy INTEGER columns) are derived
  strictly from the verdict columns ('Y' -> 1, everything else -> 0), so
  Unknown is never presented as a hard "No" in boolean contexts either.
* ``sponsorship_score`` is derived from verdict + support confidence + the
  seed's ``sponsorship_history`` (locked decision #3), so the Dashboard
  "Sponsored" card and "strongest first" sorting keep working.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import threading
import time
from pathlib import Path
from typing import Callable

from sponsorscout import paths
from sponsorscout.application import seed_manager
from sponsorscout.scanning.ats import ats_scanner as ats_module
from sponsorscout.scanning.career import career_scanner as career_module
from sponsorscout.scanning.common import recommended_workers
from sponsorscout.core.location_country import country_from_location
from sponsorscout.core import persistence
from sponsorscout.db import database as db

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]


def _noop_progress(_msg: str) -> None:  # pragma: no cover
    pass


def lower_process_priority() -> bool:
    """Best-effort: run the scan below normal priority.

    The scan is a long background job; on a 2-core / 8 GB machine the desktop
    must stay responsive while it runs.  Lowering the priority lets the OS
    scheduler favour the UI (and whatever else the user is doing) whenever the
    two compete for CPU.  Purely an optimisation: never fatal.

    * Windows: ``BELOW_NORMAL_PRIORITY_CLASS`` via kernel32.
    * POSIX:   ``os.nice(10)`` (only when permitted).
    """
    try:
        import os

        if os.name == "nt":
            import ctypes

            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetCurrentProcess()
            return bool(kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS))
        try:
            os.nice(10)
            return True
        except (AttributeError, OSError):
            return False
    except Exception:  # pragma: no cover - optimisation only
        logger.debug("Could not lower process priority", exc_info=True)
        return False


# ── Score / verdict derivation ───────────────────────────────────────────────

def derive_sponsorship_score(visa_verdict: str, confidence: float,
                             sponsorship_history) -> int:
    """Derive the 0-100 sponsorship score (locked decision #3).

    verdict 'Y'      -> base 70 (+ confidence bonus + seed history bonus)
    verdict Unknown  -> flat 35 (neutral: no bonuses from evidence the row
                        does not have — confidence and seed history must not
                        inflate an unevidenced verdict, nor drag it down)
    verdict 'N'      -> 0
    """
    verdict = str(visa_verdict or "").strip().lower()
    if verdict == "y":
        base = 70
    elif verdict == "n":
        return 0
    else:
        return 35
    try:
        conf = max(0.0, min(1.0, float(confidence or 0)))
    except (TypeError, ValueError):
        conf = 0.0
    try:
        history = max(0, min(100, int(sponsorship_history or 0)))
    except (TypeError, ValueError):
        history = 0
    score = base + conf * 20 + history * 0.10
    return int(round(max(0, min(100, score))))


def _as_verdict(value) -> str:
    """Normalise a scanner verdict cell to 'Y' / 'N' / 'Unknown' / ''."""
    v = str(value or "").strip()
    low = v.lower()
    if low in ("y", "yes", "true", "1"):
        return "Y"
    if low in ("n", "no", "false", "0"):
        return "N"
    if low == "unknown":
        return "Unknown"
    return ""


def _as_bool(verdict: str) -> int:
    """Legacy boolean derivation: strictly 'Y' -> 1, everything else 0."""
    return 1 if str(verdict or "").strip().lower() == "y" else 0


def _norm_location(value) -> str:
    v = str(value or "").strip()
    return "" if v.lower() in ("unknown", "not specified") else v


# Canonical country names (must match location_country.py output).
_EU_COUNTRIES = frozenset({
    "Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic",
    "Denmark", "Estonia", "Finland", "France", "Germany", "Greece", "Hungary",
    "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg", "Malta",
    "Netherlands", "Poland", "Portugal", "Romania", "Slovakia", "Slovenia",
    "Spain", "Sweden",
})
_EMEA_COUNTRIES = _EU_COUNTRIES | frozenset({
    "United Kingdom", "Switzerland", "Norway", "Iceland", "Turkey", "Israel",
    "United Arab Emirates", "Saudi Arabia", "Qatar", "Kuwait", "Bahrain",
    "Jordan", "Lebanon", "Egypt", "Morocco", "Tunisia", "Kenya", "Nigeria",
    "Ghana", "South Africa",
})


def _remote_type(row: dict) -> str:
    """F8 fix: hybrid + EU/EMEA-aware remote mapping.

    Previously this could only ever return "remote"/"onsite", which left the
    DB/UI remote_eu / remote_emea / hybrid filters permanently empty.
    """
    hay = f"{row.get('Job Type', '')} {row.get('Job Location', '')} {row.get('Raw Location', '')}".lower()
    if "hybrid" in hay:
        return "hybrid"
    if "remote" not in hay:
        return "onsite"
    country = _job_country(row)
    if country in _EU_COUNTRIES:
        return "remote_eu"
    if country in _EMEA_COUNTRIES:
        return "remote_emea"
    # Region word without a concrete country ("Remote - EU").
    if re.search(r"\b(eu|e\.u\.|europe)\b", hay):
        return "remote_eu"
    if "emea" in hay:
        return "remote_emea"
    return "remote"


def _job_country(row: dict) -> str:
    """Best-effort country (locked decision Q8): job-location parse first,
    then the seed's target country when it names a concrete country."""
    loc = _norm_location(row.get("Job Location"))
    if loc:
        country = country_from_location(loc)
        if country:
            return country
    # F6 fix: fall back to Raw Location before the seed target — the raw
    # string often holds "Kuala Lumpur, MY" when Job Location is Unknown.
    raw_loc = _norm_location(row.get("Raw Location"))
    if raw_loc and raw_loc != loc:
        country = country_from_location(raw_loc)
        if country:
            return country
    target = str(row.get("Target Country") or "").strip()
    if target and target.lower() not in ("global", "unknown"):
        return target
    return ""


def _exp_min_years(value) -> float | None:
    """Scanner 'Experience Min Years' -> REAL (None = not stated)."""
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# Scanner experience vocabulary -> app canonical vocabulary.
#
# The scanners deliberately emit employer-facing words ("Internship",
# "Junior", "Executive") while the jobs table, its partial index
# (idx_jobs_experience) and the UI filter all use the short canonical set in
# ``db.EXPERIENCE_LEVELS`` / ``normalizer.detect_experience_level``:
# Intern / Entry / Mid / Senior / Lead / Exec.  Canonicalise exactly once,
# here at the ingestion boundary, so no scanner or UI has to know both.
_EXP_LEVEL_CANON = {
    "internship": "Intern",
    "intern": "Intern",
    "junior": "Entry",
    "entry": "Entry",
    "entry level": "Entry",
    "mid": "Mid",
    "mid level": "Mid",
    "senior": "Senior",
    "lead": "Lead",
    "executive": "Exec",
    "exec": "Exec",
}


def _exp_level(value) -> str:
    """Scanner 'Experience Level' -> canonical level ('' = unclassified).

    An unrecognised word yields '' rather than the raw token: the column is a
    soft enum, and '' is the documented "not yet classified" state.
    """
    raw = str(value or "").strip()
    if not raw or raw.lower() == "unknown":
        return ""
    return _EXP_LEVEL_CANON.get(raw.lower(), "")


def _exp_required(value) -> str:
    """Scanner 'Experience Required' -> stored text ('' = not stated)."""
    raw = str(value or "").strip()
    if not raw or raw.lower() == "unknown":
        return ""
    return raw


def _row_to_job(row: dict, *, source_subtype: str = "direct", run_id: str) -> dict | None:
    """Map one 39-column scanner output row to an ``upsert_job`` dict."""
    url = str(row.get("Job URL") or "").strip()
    title = str(row.get("Job Title") or "").strip()
    if not url or not title or title.lower() == "unknown":
        return None
    company = (str(row.get("Hiring Company") or "").strip()
               or str(row.get("Company Name") or "").strip()
               or str(row.get("Seed Name") or "").strip())
    if company.lower() == "unknown":
        # Recruiter rows carry Hiring Company='Unknown'; fall back to the
        # seed identity so the job is attributed to the scanned company.
        company = (str(row.get("Company Name") or "").strip()
                   or str(row.get("Seed Name") or "").strip() or "Unknown")
    visa = _as_verdict(row.get("Visa Sponsorship"))
    reloc = _as_verdict(row.get("Relocation Support"))
    blue = _as_verdict(row.get("EU Blue Card"))
    try:
        confidence = float(row.get("Support Confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    try:
        history = int(row.get("Sponsorship History Score") or 0)
    except (TypeError, ValueError):
        history = 0
    industry = str(row.get("Industry Type") or "").strip()
    if industry.lower() == "unknown":
        industry = ""
    return {
        "external_id": str(row.get("Canonical Job ID") or "").strip(),
        "title": title,
        "company": company,
        "country": _job_country(row),
        "location": _norm_location(row.get("Job Location")),
        "url": url,
        "ats_source": str(row.get("Provider") or "").strip(),
        "source_type": "verified",
        "source_subtype": source_subtype,
        "source_name": str(row.get("Seed Name") or "").strip(),
        "description": "",
        "trust_score": 80,
        "freshness_score": 0,
        "sponsorship_score": derive_sponsorship_score(visa, confidence, history),
        "match_score": 0,
        "verified_active": True,
        "is_expired": False,
        "remote_type": _remote_type(row),
        # Legacy derived booleans — strictly 'Y' -> 1 (Unknown is never a No).
        "eu_blue_card": _as_bool(blue),
        "has_relocation": _as_bool(reloc),
        # Authoritative three-state evidence (Q4 decision).
        "visa_sponsorship": visa,
        "relocation_support": reloc,
        "eu_blue_card_verdict": blue,
        "relocation_required": _as_verdict(row.get("Relocation Required")),
        "support_confidence": confidence,
        "support_evidence": str(row.get("Support Evidence") or "").strip(),
        "support_evidence_url": str(row.get("Support Evidence URL") or "").strip(),
        "support_evidence_type": str(row.get("Support Evidence Type") or "").strip(),
        "blue_card_evidence": str(row.get("Blue Card Evidence") or "").strip(),
        "canonical_job_id": str(row.get("Canonical Job ID") or "").strip(),
        "run_id": run_id,
        "industry": industry,
        "raw_location": str(row.get("Raw Location") or "").strip(),
        "country_source": "auto",
        # FIX P0-30: scanner-extracted experience columns (additive),
        # canonicalised to the app vocabulary here (see _EXP_LEVEL_CANON).
        "experience_level": _exp_level(row.get("Experience Level")),
        "experience_required": _exp_required(row.get("Experience Required")),
        "experience_min_years": _exp_min_years(row.get("Experience Min Years")),
        "experience_source": str(row.get("Experience Source") or "").strip(),
    }


# ── Ingestion ────────────────────────────────────────────────────────────────

def _ingest_output_csv(db_path, path: Path, run_id: str, source_subtype: str,
                       seen_canonical: set,
                       seen_fuzzy: set | None = None,
                       skip_box: list | None = None) -> tuple[int, int]:
    """Ingest accepted job rows from one scanner output CSV.

    Returns (ingested, duplicates).  Duplicates are rows whose canonical job
    ID was already ingested in this run (mirror URLs across scanners/files),
    plus rows with an empty canonical ID whose (company, title, country)
    fuzzy key was already seen (G3 fallback). Commits in batches of 500
    rows instead of once per row (F11).

    ``skip_box`` enables incremental tailing: pass a one-element list and the
    reader skips the records already consumed by earlier passes, recording the
    new record count back into it.  The live ingester uses this so a long scan
    no longer re-parses (and re-dedupes) the whole growing CSV every few
    seconds — that was O(n^2) work over a run and a major cause of the machine
    becoming sluggish during scans.  A plain call (``skip_box=None``) reads the
    file from the start, which the final bulk pass relies on for its counts.
    """
    if not path or not path.exists():
        return 0, 0
    if seen_fuzzy is None:
        seen_fuzzy = set()
    ingested = duplicates = 0
    pending = 0
    skip = int(skip_box[0]) if skip_box else 0
    consumed = 0
    conn = db.get_connection(db_path)
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            raw = f.read()
        # Both scanners append rows company-by-company while this may run, so
        # the file can end mid-record (OS buffer flush).  A half-written row
        # must NEVER be ingested as a truncated job: only complete,
        # newline-terminated records are considered — the partial tail is
        # simply left for the next pass.  csv.reader (rather than line
        # splitting) is still used so quoted fields containing newlines —
        # legitimately produced by the scanners — parse correctly.
        if raw and not raw.endswith("\n"):
            cut = raw.rfind("\n")
            raw = raw[:cut + 1] if cut >= 0 else ""
        if not raw:
            return 0, 0
        reader = csv.reader(io.StringIO(raw))
        fieldnames = next(reader, None)
        if not fieldnames:
            return 0, 0
        for values in reader:
            consumed += 1
            if consumed <= skip:
                continue
            if len(values) != len(fieldnames):
                # Never let an extra/missing column shift values into the
                # wrong field (the header is the contract).
                values = (values + [""] * len(fieldnames))[:len(fieldnames)]
            row = dict(zip(fieldnames, values))
            cid = str(row.get("Canonical Job ID") or "").strip()
            if cid and cid in seen_canonical:
                duplicates += 1
                continue
            job = _row_to_job(row, source_subtype=source_subtype, run_id=run_id)
            if job is None:
                db.record_scan_event(
                    db_path, run_id, level="warning", phase="ingest",
                    company=str(row.get("Company Name") or row.get("Seed Name") or ""),
                    message="Skipped row (no valid URL / unparsable): "
                            + str(row.get("Job Title") or "")[:120])
                continue
            # G3 fuzzy fallback: only for rows WITHOUT a canonical ID.
            # Rows with an ID keep trusting it — the same title+city can
            # legitimately be distinct openings at one company.
            if not cid:
                fuzzy = (
                    " ".join(str(job.get("company") or "").lower().split()),
                    re.sub(r"\W+", "", str(job.get("title") or "").lower()),
                    str(job.get("country") or ""),
                )
                if fuzzy in seen_fuzzy:
                    duplicates += 1
                    continue
                seen_fuzzy.add(fuzzy)
            try:
                persistence.upsert_job(conn, job, commit=False)
            except Exception:
                logger.exception("Failed to upsert job %s", job.get("url"))
                db.record_scan_event(
                    db_path, run_id, level="error", phase="ingest",
                    company=job.get("company", ""),
                    message=f"Failed to ingest job {job.get('url')}")
                continue
            if cid:
                seen_canonical.add(cid)
            ingested += 1
            pending += 1
            if pending >= 500:
                conn.commit()
                pending = 0
        if pending:
            conn.commit()
    finally:
        conn.close()
    if skip_box is not None:
        skip_box[0] = consumed
    return ingested, duplicates


def _ingest_scan_log(db_path, path: Path, run_id: str, scanner: str) -> tuple[int, dict]:
    """Copy one scanner's per-run scan-log CSV into the scan_log table.

    Returns ``(row_count, status_counts)`` where ``status_counts`` maps
    ``ok``/``empty``/``error`` target counts (``partial`` counts as ``ok``
    since it produced jobs) for the scan_runs summary columns.
    """
    if not path or not path.exists():
        return 0, {"ok": 0, "empty": 0, "error": 0}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    status_counts = {"ok": 0, "empty": 0, "error": 0}
    if rows:
        db.record_scan_log_rows(db_path, run_id, scanner, rows)
        # Elevate per-company failures into the event timeline so hidden errors
        # that reduce job yield are visible in the downloaded scan analysis.
        # Collected first and written in ONE transaction (batch).
        events: list[tuple[str, str, str, str]] = []
        for row in rows:
            status = str(row.get("Status") or row.get("status") or "").lower()
            err = (row.get("Error") or row.get("error") or "").strip()
            if status == "empty":
                status_counts["empty"] += 1
            elif status == "error" or (err and status not in ("ok", "partial")):
                status_counts["error"] += 1
            else:
                # "ok" and "partial" both produced jobs for the target.
                status_counts["ok"] += 1
            if status in ("error", "failed", "partial") or err:
                events.append((
                    "error", scanner,
                    str(row.get("Company") or row.get("Seed Name") or ""),
                    " | ".join(part for part in
                               (err, str(row.get("Diagnostics") or "")) if part)[:2000]))
        try:
            db.record_scan_events(db_path, run_id, events)
        except Exception:  # pragma: no cover - evidence logging must not crash
            logger.exception("Failed to record scan-log events")
    return len(rows), status_counts


def _ingest_error_csv(db_path, path: Path, run_id: str, scanner: str) -> int:
    """Copy one scanner's ``<output>_errors.csv`` rows into the run timeline.

    Both scanners write a dedicated errors artifact (crash-safe: appended
    immediately, so a mid-run abort still leaves evidence).  Those rows carry
    detail the scan-log's single ``Error`` column cannot hold — the phase
    (target / browser_fallback / detail / seed) and the exception type — so
    they are surfaced in ``scan_events`` where the Tools tab's
    "Download Scan Log" export can pick them up.

    Returns the number of rows ingested.
    """
    if not path or not path.exists():
        return 0
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        logger.exception("Failed to read error artifact %s", path)
        return 0
    count = 0
    events: list[tuple[str, str, str, str]] = []
    for row in rows:
        message = " | ".join(
            part for part in (
                str(row.get("Error Type") or "").strip(),
                str(row.get("Message") or "").strip(),
            ) if part)
        if not message:
            continue
        events.append((
            "error",
            f"{scanner}/{str(row.get('Phase') or 'error').strip()}",
            str(row.get("Seed Name") or ""),
            message[:2000],
        ))
        count += 1
    try:
        db.record_scan_events(db_path, run_id, events)
    except Exception:  # pragma: no cover - evidence logging must not crash
        logger.exception("Failed to record error events")
    return count


def _count_seed_rows(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            return max(0, sum(1 for _ in csv.DictReader(f)))
    except OSError:
        return 0


class _ScanProgress:
    """Count completed companies and emit machine-readable progress lines.

    The scanners already print one result line per finished company
    (``OK:/EMPTY:/ERROR: … wrote=…`` for ATS, ``OK <name>:/EMPTY …`` for
    career).  This wrapper sniffs those lines and emits
    ``PROGRESS: done/total:phase:label`` after each one, which the UI
    turns into QProgressBar updates.  No scanner logic is touched — pure
    transport.  Thread-safe: career workers complete concurrently.
    """

    PREFIX = "PROGRESS:"

    def __init__(self, n_ats: int, n_career: int,
                 base_ats: int = 0, base_career: int = 0):
        self.n_ats = max(0, int(n_ats))
        self.n_career = max(0, int(n_career))
        # Resume support: companies already finished in a previous run.
        # Ticks report the *overall* position (base + this run's count) so
        # the bar continues from the checkpoint instead of restarting at 0.
        self.base_ats = max(0, int(base_ats))
        self.base_career = max(0, int(base_career))
        self.total = self.n_ats + self.n_career + self.base_ats + self.base_career
        self._lock = threading.Lock()
        self._ats_done = 0
        self._career_done = 0

    def wrap(self, progress: ProgressFn) -> ProgressFn:
        """Wrap a progress callback so company completions also emit ticks."""
        tracker = self

        def _wrapped(msg) -> None:
            progress(msg)
            if tracker.total <= 0:
                return
            try:
                text = str(msg)
            except Exception:
                return
            ticks: list[str] = []
            with tracker._lock:
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith(tracker.PREFIX):
                        continue  # never count our own tick lines
                    upper = stripped.upper()
                    if ("WROTE=" not in upper):
                        continue
                    if (upper.startswith("OK:") or upper.startswith("EMPTY:")
                            or upper.startswith("ERROR:")):
                        # ATS result line: "OK:/EMPTY:/ERROR: wrote=…"
                        tracker._ats_done = min(
                            tracker._ats_done + 1, tracker.n_ats)
                        done = (tracker.base_ats + tracker._ats_done
                                + tracker.base_career + tracker._career_done)
                        phase_done = tracker.base_ats + tracker._ats_done
                        phase_total = tracker.base_ats + tracker.n_ats
                        ticks.append(
                            f"{tracker.PREFIX} {done}/{tracker.total}:ats:"
                            f"ATS {phase_done}/{phase_total}")
                    elif (upper.startswith("OK ") or upper.startswith("EMPTY ")
                          or upper.startswith("ERROR ")
                          or upper.startswith("PARTIAL ")):
                        # Career result line: "OK <name>: wrote=…"
                        tracker._career_done = min(
                            tracker._career_done + 1, tracker.n_career)
                        done = (tracker.base_ats + tracker._ats_done
                                + tracker.base_career + tracker._career_done)
                        phase_done = tracker.base_career + tracker._career_done
                        phase_total = tracker.base_career + tracker.n_career
                        ticks.append(
                            f"{tracker.PREFIX} {done}/{tracker.total}:career:"
                            f"Career {phase_done}/{phase_total}")
            for tick in ticks:
                try:
                    progress(tick)
                except Exception:
                    pass

        # Preserve the _EventTee.flush() API the pipeline calls directly.
        try:
            _wrapped.flush = progress.flush  # type: ignore[attr-defined]
        except AttributeError:
            pass
        return _wrapped


def _infer_level(msg: str) -> str:
    low = msg.lower()
    if "error" in low or "failed" in low or "exception" in low or "✗" in low:
        return "error"
    if low.startswith("warning") or " warn" in low:
        return "warning"
    return "info"


def _infer_phase(msg: str) -> str:
    low = msg.lower()
    if low.startswith("scan "):
        return "pipeline"
    if "ats" in low:
        return "ats"
    if "career" in low:
        return "career"
    if "ingest" in low:
        return "ingest"
    return "pipeline"


class _EventTee:
    """Progress wrapper that also persists each line to the run timeline.

    Batched on purpose.  The previous implementation called
    ``db.record_scan_event`` for every single line, which opens a fresh SQLite
    connection *and commits* per call — a chatty scan (thousands of per-company
    / per-page lines) turned that into thousands of write transactions and a
    serious I/O tax on low-end hardware.  Lines are now buffered and written in
    one transaction per batch.
    """

    FLUSH_EVERY = 25
    FLUSH_SECONDS = 2.0

    def __init__(self, db_path, run_id: str, progress: ProgressFn):
        self.db_path = db_path
        self.run_id = run_id
        self.progress = progress
        self._buffer: list[tuple[str, str, str, str]] = []
        self._lock = threading.Lock()
        self._last = time.monotonic()

    def __call__(self, msg: str) -> None:
        self.progress(msg)
        text = str(msg)
        rows: list[tuple[str, str, str, str]] = []
        with self._lock:
            self._buffer.append(
                (_infer_level(text), _infer_phase(text), "", text[:2000]))
            now = time.monotonic()
            if (len(self._buffer) >= self.FLUSH_EVERY
                    or (now - self._last) >= self.FLUSH_SECONDS):
                rows, self._buffer = self._buffer, []
                self._last = now
        if rows:
            self._write(rows)

    def flush(self) -> None:
        """Write any buffered lines (called before the run is finalised)."""
        with self._lock:
            rows, self._buffer = self._buffer, []
        if rows:
            self._write(rows)

    def _write(self, rows) -> None:
        try:
            db.record_scan_events(self.db_path, self.run_id, rows)
        except Exception:  # pragma: no cover - event logging must not crash scans
            pass


def _event_tee(db_path, run_id: str, progress: ProgressFn) -> _EventTee:
    """Wrap a progress callback so every line is also persisted to the run's
    scan_events timeline (level/phase inferred from the message text)."""
    return _EventTee(db_path, run_id, progress)


class _LiveIngester(threading.Thread):
    """Progressively ingest scanner CSV rows while the scan is still running.

    Both scanners append accepted rows to their output CSVs company by
    company, so tailing them lets the Dashboard show live numbers during a
    scan instead of only after ingestion.  Ingestion is idempotent
    (``upsert_job`` upserts on the normalized URL and shares the run's
    ``seen_canonical`` dedup set), and the final bulk ingest at the end of
    ``run_scan`` re-upserts everything, so no special bookkeeping is needed.
    """

    def __init__(self, db_path, run_id: str,
                 csv_specs: list[tuple[Path, str]],
                 seen_canonical: set,
                 interval: float = 5.0,
                 progress: ProgressFn | None = None):
        super().__init__(name="LiveIngester", daemon=True)
        self.db_path = db_path
        self.run_id = run_id
        self.csv_specs = csv_specs
        self.seen_canonical = seen_canonical
        self.interval = interval
        self.progress = progress or _noop_progress
        self._stop = threading.Event()
        # Per-CSV record counters so each cycle parses only newly appended
        # rows.  Without this the poller re-read the entire (constantly
        # growing) CSV every interval, which is O(n^2) across a scan and made
        # long scans progressively heavier on CPU and disk.
        self._offsets: dict[str, list[int]] = {}

    def stop(self):
        self._stop.set()

    def run(self):
        ingested_so_far = 0
        last_reported = 0
        while not self._stop.wait(self.interval):
            try:
                for path, subtype in self.csv_specs:
                    box = self._offsets.setdefault(str(path), [0])
                    ingested, _dups = _ingest_output_csv(
                        self.db_path, path, self.run_id,
                        source_subtype=subtype, seen_canonical=self.seen_canonical,
                        skip_box=box)
                    ingested_so_far += ingested
                # Throttle: the 5s poller used to emit the same count dozens
                # of times per company (e.g. ~200 identical Hays lines),
                # flooding the chunk queue, the event-tee DB writes and the
                # log widget.  Emit only when the count actually grew.
                if ingested_so_far and ingested_so_far != last_reported:
                    last_reported = ingested_so_far
                    self.progress(
                        f"Ingested live so far: {ingested_so_far} jobs "
                        "(Dashboard 'Refresh' reflects these)")
            except Exception:  # pragma: no cover - never kill the poller
                logger.exception("Live ingestion cycle failed")


# ── Orchestration ────────────────────────────────────────────────────────────

def _normalize_names(names) -> list[str]:
    """Trim a company-name list; empty / whitespace names are dropped."""
    return [c.strip() for c in (names or []) if c and c.strip()]


def run_scan(method: str = "full",
             db_path=None,
             cancel_event: threading.Event | None = None,
             only_companies: list | None = None,
             progress: ProgressFn | None = None,
             resume_from: str | None = None,
             only_ats: list | None = None,
             only_career: list | None = None,
             run_ats: bool = True,
             run_career: bool = True) -> dict:
    """Run a full scan campaign and ingest the results.

    method:
      * ``full``  — default.  ATS boards + career pages plus per-job
                    detail-page evidence enrichment, so each job row is
                    extracted as accurately as possible.
      * ``quick`` — same coverage without the detail-page enrichment pass
                    (dev CLI only; leaves some verdicts as ``Unknown``).

    only_companies: optional list of company names — when given, only those
      seed targets are scanned in BOTH phases (CLI --company).

    only_ats / only_career: optional per-phase whitelists (custom scan).
      ``only_ats`` filters the ATS phase, ``only_career`` the career phase.
      ``run_ats`` / ``run_career`` toggle each phase on/off independently.
      All default to the historical behaviour (both phases, all companies).
      ``only_companies`` fans out to both phases when the per-phase lists
      are not given.

    resume_from: optional run_id of a stopped run.  Companies already
      finished in that run (per its ``scan_log`` rows) are skipped — the
      scanners never revisit them — and the progress bar is offset so it
      continues from the checkpoint instead of restarting at 0.  Combines
      with ``only_companies`` (the intersection is scanned).  Completed
      rows were already ingested into jobs, and re-ingestion upserts on
      the canonical URL key, so nothing is lost or duplicated.

    Must be called from a worker thread (it performs network I/O); the UI
    layer receives progress via ``progress`` and cancellation via the shared
    ``cancel_event``.  Returns a summary dict for the Tools tab.
    """
    method = "full" if method == "full" else "quick"
    progress = progress or _noop_progress
    cancel_event = cancel_event or threading.Event()
    db_path = str(db_path or paths.DB_PATH)
    out_dir = paths.ensure_scan_output_dir()
    paths.ensure_user_data_dir()
    # Keep the desktop responsive while a long scan competes for CPU.
    lower_process_priority()

    # Reconcile bundled seeds with the user's mutable copies: append any
    # companies added to the bundled defaults that the user copy is missing
    # (existing / user-added rows are never modified or removed).
    added = seed_manager.merge_bundled_seeds(log_fn=progress)

    # Make sure the schema (incl. scan_runs/scan_log + evidence columns) exists.
    try:
        db.initialize(db_path)
    except TypeError:  # pragma: no cover - older signature fallback
        db.initialize()

    run_id = time.strftime("%Y%m%dT%H%M%S")
    # Tee every progress line into the run's scan_events timeline for later
    # download / analysis (phase + level inferred from the message text).
    progress = _event_tee(db_path, run_id, progress)
    ats_out = out_dir / f"{run_id}_ats_jobs.csv"
    career_out = out_dir / f"{run_id}_career_jobs.csv"
    detail = method == "full"

    phase_errors: list[str] = []
    cancelled = False
    ats_csv = career_csv = None

    summary = {
        "run_id": run_id,
        "method": method,
        "cancelled": False,
        "status": "completed",
        "ingested": 0,
        "duplicates": 0,
        "log_rows": 0,
        "artifacts": {},
        "errors": phase_errors,
    }

    n_ats = _count_seed_rows(seed_manager.user_ats_path())
    n_career = _count_seed_rows(seed_manager.user_career_path())
    # Resume (Stop-as-checkpoint): companies finished in the stopped run
    # are excluded before scanning starts — the scanners never revisit
    # them, and their rows are already in jobs via live ingestion.
    skip_ats: set[str] = set()
    skip_career: set[str] = set()
    if resume_from:
        try:
            _done = db.get_completed_scan_companies(db_path, resume_from)
            skip_ats = set(_done.get("ats", set()))
            skip_career = set(_done.get("career", set()))
        except Exception:
            logger.exception("Could not load resume checkpoint %s", resume_from)
            skip_ats = set()
            skip_career = set()
    skip_names = skip_ats | skip_career
    seed_note = ""
    total_added = added.get("ats", 0) + added.get("career", 0)
    if total_added:
        seed_note = f" (seed update: +{total_added} new companies)"

    # ── Custom scan scope ────────────────────────────────────────────────────
    # Per-phase whitelists and phase toggles.  ``None`` = no filter (full
    # phase); an empty set means "phase disabled / nothing selected" — the
    # phase is skipped with a progress note, never an error.
    ats_sel = _normalize_names(only_ats)
    career_sel = _normalize_names(only_career)
    shared_sel = _normalize_names(only_companies)
    wanted_ats: set[str] | None = None
    wanted_career: set[str] | None = None
    if not run_ats:
        wanted_ats = set()
    elif ats_sel or shared_sel:
        wanted_ats = {c.lower() for c in (ats_sel or shared_sel)}
    if not run_career:
        wanted_career = set()
    elif career_sel or shared_sel:
        wanted_career = {c.lower() for c in (career_sel or shared_sel)}
    run_method = ("custom"
                  if (only_ats is not None or only_career is not None
                      or not run_ats or not run_career) else method)
    summary["method"] = run_method

    def _remaining(path, names: set[str] | None, skip: set[str]) -> int:
        count = 0
        for r in seed_manager.read_seed_rows(path)["rows"]:
            name = (r.get("name") or "").strip()
            if not name:
                continue
            low = name.lower()
            if names is not None and low not in names:
                continue
            if low in skip:
                continue
            count += 1
        return count

    if skip_names or wanted_ats is not None or wanted_career is not None:
        # Remaining-to-scan after resume-skip and/or per-phase subset filter.
        n_ats = _remaining(seed_manager.user_ats_path(), wanted_ats,
                           skip_ats)
        n_career = _remaining(seed_manager.user_career_path(), wanted_career,
                              skip_career)
    # Structured PROGRESS: lines for the UI progress bar (one tick per
    # finished company).  Wrapped outside the event tee so ticks land in
    # the run timeline too.  base_* offsets resume runs so the bar
    # continues from the checkpoint (e.g. 54%) instead of restarting at
    # 0; _ScanProgress clamps each phase so a subset can never overflow.
    # NOTE: the offset applies only to resume skips — a pure subset scan
    # (no resume_from) keeps base 0 so its bar spans just the subset.
    full_ats = _count_seed_rows(seed_manager.user_ats_path())
    full_career = _count_seed_rows(seed_manager.user_career_path())
    progress = _ScanProgress(
        n_ats, n_career,
        base_ats=max(0, full_ats - n_ats) if skip_names else 0,
        base_career=max(0, full_career - n_career)
        if skip_names else 0).wrap(progress)
    db.start_scan_run(db_path, run_id, run_method, n_ats, n_career)

    # Live ingestion: scanners write accepted rows to their CSVs company by
    # company, so tail them into the DB while the scan runs. This makes the
    # Dashboard's Refresh button show live numbers mid-scan. The final bulk
    # ingest below re-upserts everything (idempotent), so counts stay exact.
    seen_canonical: set = set()
    live = _LiveIngester(
        db_path, run_id,
        csv_specs=[
            (ats_out, "direct"),
            (ats_out.with_name(ats_out.stem + "_recruiter.csv"), "recruiter"),
            (career_out, "direct"),
            (career_out.with_name(career_out.stem + "_recruiter.csv"), "recruiter"),
        ],
        seen_canonical=seen_canonical,
        progress=progress,
    )
    live.start()

    # Resume: the scanners each accept an only_companies whitelist, so
    # feed them exactly the remaining companies.  ``None`` means "scan
    # everything" — pass the computed remaining lists only when resuming.
    resume_only_ats: list[str] | None = None
    resume_only_career: list[str] | None = None
    if skip_names:
        try:
            ats_names = [
                (r.get("name") or "").strip()
                for r in seed_manager.read_seed_rows(
                    seed_manager.user_ats_path())["rows"]]
            career_names = [
                (r.get("name") or "").strip()
                for r in seed_manager.read_seed_rows(
                    seed_manager.user_career_path())["rows"]]
            if wanted_ats is not None:
                ats_names = [n for n in ats_names if n.lower() in wanted_ats]
            if wanted_career is not None:
                career_names = [n for n in career_names
                                if n.lower() in wanted_career]
            resume_only_ats = [n for n in ats_names
                               if n and n.lower() not in skip_ats]
            resume_only_career = [n for n in career_names
                                  if n and n.lower() not in skip_career]
        except Exception:
            logger.exception("Could not compute resume company list")
            resume_only_ats = None
            resume_only_career = None
    # Effective whitelist per phase: explicit subset, resume remainder, or
    # their intersection when both are given.
    if resume_only_ats is not None or resume_only_career is not None:
        if wanted_ats is not None:
            _rset = {n.lower() for n in (resume_only_ats or [])}
            scan_only_ats = [c for c in (ats_sel or shared_sel)
                             if c and c.strip().lower() in _rset]
        else:
            scan_only_ats = list(resume_only_ats or [])
        if wanted_career is not None:
            _rset = {n.lower() for n in (resume_only_career or [])}
            scan_only_career = [c for c in (career_sel or shared_sel)
                                if c and c.strip().lower() in _rset]
        else:
            scan_only_career = list(resume_only_career or [])
    else:
        scan_only_ats = (list(ats_sel) if ats_sel
                         else (list(shared_sel) if shared_sel else None))
        scan_only_career = (list(career_sel) if career_sel
                            else (list(shared_sel) if shared_sel else None))
    if resume_from and skip_names and (scan_only_ats or scan_only_career
                                       or (scan_only_ats is None
                                           and scan_only_career is None)):
        _skipped = len(skip_names)
        _note = (f" (resuming {resume_from}: skipping "
                 f"{_skipped} finished companies)")
        seed_note = f"{seed_note}{_note}" if seed_note else _note
    progress(f"Scan {run_id} started: method={run_method}, "
             f"ATS companies={n_ats}, career companies={n_career}{seed_note}")

    # 1 ─ ATS scan (API-first, fast) ------------------------------------------
    if wanted_ats is not None and not wanted_ats and not run_ats:
        progress("ATS phase disabled — skipping ATS scan")
    elif wanted_ats is not None and not n_ats:
        progress("No ATS companies selected — skipping ATS phase")
    elif n_ats > 0:
        try:
            scanner = ats_module.ATSScanner(
                seed_file=str(seed_manager.user_ats_path()),
                output_file=str(ats_out),
                cancel_event=cancel_event,
                only_companies=scan_only_ats,
            )
            scanner.run_id = run_id
            ats_module.progress_cb = progress
            scanner.run()
            ats_csv = ats_out
            if cancel_event.is_set():
                cancelled = True
        except Exception as exc:  # preflight failure, seed errors, network down
            logger.exception("ATS scan phase failed")
            phase_errors.append(f"ATS: {type(exc).__name__}: {exc}")
            progress(f"ATS scan failed: {exc}")
    else:
        progress("No ATS seed rows — skipping ATS phase")

    # 2 ─ Career crawl (browser-heavy) ----------------------------------------
    if wanted_career is not None and not run_career:
        progress("Career phase disabled — skipping career crawl")
    elif wanted_career is not None and not n_career:
        progress("No career companies selected — skipping career phase")
    elif n_career == 0:
        progress("No career seed rows — skipping career phase")
    elif cancel_event.is_set() and ats_csv is None:
        # Cancelled during ATS with nothing produced: honour the stop fully.
        cancelled = True
    else:
        # Pre-flight: one browser-availability check for the whole career
        # phase. Without it a packaged build missing the bundled `_playwright`
        # browsers used to emit the raw Playwright "Executable doesn't exist"
        # banner for every single company. Fail fast with one clear warning.
        try:
            from sponsorscout.services.browser_fetcher import (
                _ensure_playwright_browsers,
            )
            if not _ensure_playwright_browsers():
                progress(
                    "WARNING: Chromium browser is not available — JS-rendered "
                    "career portals (provider=auto / custom career pages) will "
                    "return 0 jobs. Reinstall the full installer package or run "
                    "'playwright install chromium' on this machine."
                )
        except Exception:  # pragma: no cover - pre-flight must not kill scans
            logger.exception("Browser pre-flight check failed")
        try:
            scanner = career_module.CareerPortalScanner(
                input_csv=str(seed_manager.user_career_path()),
                output_csv=str(career_out),
                detail_scan=detail,
                cancel_event=cancel_event,
                only_companies=scan_only_career,
                # Host-adaptive: the scanner sizes its own browser pool from
                # CPU/RAM when this is None (2-core / 8 GB machines must not
                # run several Chromium instances at once).
                max_workers=recommended_workers("browser"),
            )
            scanner.run_id = run_id
            career_module.progress_cb = progress
            scanner.execute_crawler()
            career_csv = career_out
            if cancel_event.is_set():
                cancelled = True
        except Exception as exc:
            logger.exception("Career scan phase failed")
            phase_errors.append(f"Career: {type(exc).__name__}: {exc}")
            progress(f"Career scan failed: {exc}")

    # 3 ─ Ingest accepted rows + scan logs into the DB ------------------------
    # Detach the UI progress callbacks: the module-level ``progress_cb`` hooks
    # would otherwise keep pointing at a window that may already be gone.
    ats_module.progress_cb = None
    career_module.progress_cb = None
    # Persist any buffered timeline lines before the run is finalised.
    try:
        progress.flush()
    except AttributeError:
        pass
    live.stop()
    live.join(timeout=15)
    # Deliberately a *fresh* dedup set: the final pass re-reads every row once
    # so the summary counts match the previous bulk-only behaviour exactly
    # (rows already ingested live are simply upserted again, idempotently).
    seen_canonical: set = set()
    ingested_total = dup_total = log_rows_total = 0
    targets_ok = targets_empty = targets_error = 0
    for csv_base, scanner_label in ((ats_csv, "ats"), (career_csv, "career")):
        if csv_base is None:
            continue
        try:
            ingested, dups = _ingest_output_csv(
                db_path, csv_base, run_id,
                source_subtype="direct", seen_canonical=seen_canonical)
            rec_ing, rec_dups = _ingest_output_csv(
                db_path, csv_base.with_name(csv_base.stem + "_recruiter.csv"),
                run_id, source_subtype="recruiter",
                seen_canonical=seen_canonical)
            log_rows, status_counts = _ingest_scan_log(
                db_path, csv_base.with_name(csv_base.stem + "_scan_log.csv"),
                run_id, scanner_label)
            # Crash-safe error artifact (new in the synced scanner versions):
            # per-phase exception detail that the scan log cannot hold.
            _ingest_error_csv(
                db_path, csv_base.with_name(csv_base.stem + "_errors.csv"),
                run_id, scanner_label)
        except Exception as exc:
            logger.exception("Ingestion failed for %s", csv_base)
            phase_errors.append(
                f"Ingest({scanner_label}): {type(exc).__name__}: {exc}")
            continue
        ingested_total += ingested + rec_ing
        dup_total += dups + rec_dups
        log_rows_total += log_rows
        targets_ok += status_counts["ok"]
        targets_empty += status_counts["empty"]
        targets_error += status_counts["error"]
        summary["artifacts"][scanner_label] = {
            "jobs": str(csv_base),
            "recruiter": str(csv_base.with_name(csv_base.stem + "_recruiter.csv")),
            "quarantine": str(csv_base.with_name(csv_base.stem + "_quarantine.csv")),
            "scan_log": str(csv_base.with_name(csv_base.stem + "_scan_log.csv")),
            "errors": str(csv_base.with_name(csv_base.stem + "_errors.csv")),
        }

    # F9 fix: quarantine totals come from the scan_log rows (the scanners
    # are the authority on what they filtered); the Tools tab reads this key.
    try:
        quarantined_total = db.sum_scan_log_quarantined(db_path, run_id)
    except Exception:
        logger.exception("Failed to sum quarantined rows")
        quarantined_total = 0

    summary["ingested"] = ingested_total
    summary["duplicates"] = dup_total
    summary["quarantined"] = quarantined_total
    summary["log_rows"] = log_rows_total
    summary["cancelled"] = cancelled
    summary["resume_from"] = resume_from or ""
    summary["resumed_skipped"] = len(skip_names) if resume_from else 0
    if phase_errors and ingested_total == 0:
        summary["status"] = "error"
    elif cancelled:
        summary["status"] = "cancelled"
    elif phase_errors or targets_error > 0:
        # F9 fix: dead/error targets make the run partial, not completed.
        summary["status"] = "partial"
    else:
        summary["status"] = "completed"

    # Resume linkage (no schema migration): encode the parent run_id in the
    # run's error/notes field so Scan History can show the chain.  Real
    # phase errors are preserved alongside (parsed back by
    # db.parse_resume_link).
    _resume_tag = f"resumed_from:{resume_from}" if resume_from else ""
    try:
        _err_text = "; ".join(phase_errors)[:1900]
        if _resume_tag:
            _err_text = f"{_err_text}; {_resume_tag}" if _err_text else _resume_tag
        db.finish_scan_run(
            db_path, run_id,
            targets_ok=targets_ok, targets_empty=targets_empty,
            targets_error=targets_error,
            jobs_found=ingested_total, jobs_quarantined=quarantined_total,
            jobs_duplicates=dup_total, status=summary["status"],
            error=_err_text[:2000],
        )
    except Exception:  # pragma: no cover - evidence logging must not crash
        logger.exception("Failed to finalise scan_runs row")

    # Parent promotion: a resume child that covered everything left turns
    # the stopped parent into "resumed" (completed via this child).  A
    # child that was itself stopped keeps the parent resumable.
    summary["parent_promoted"] = False
    if resume_from and summary["status"] in ("completed", "partial"):
        try:
            summary["parent_promoted"] = bool(
                db.mark_scan_resumed(db_path, resume_from, run_id))
        except Exception:
            logger.exception("Failed to promote resumed parent run")

    progress(f"Scan {run_id} finished: status={summary['status']}, "
             f"ingested={ingested_total}, duplicates={dup_total}, "
             f"quarantined={quarantined_total}, "
             f"targets ok/empty/error={targets_ok}/{targets_empty}/{targets_error}")
    # Flush the final lines into the run timeline.
    try:
        progress.flush()
    except AttributeError:
        pass
    return summary
