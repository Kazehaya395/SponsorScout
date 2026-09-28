"""Seed CSV management.

Canonical sources of truth are the bundled CSVs shipped in
``sponsorscout/data`` (synced from the project-root ``company_ATS_seed.csv`` /
``company_Career_seed.csv``).  On first run the app copies them into the
per-user data directory (``~/.sponsorscout/seeds``) and ALL edits (in-app Data
Management tab, or manual CSV editing by the end-user) happen on those mutable
copies.  This keeps a packaged build read-only and lets users add companies.

Both scanners read the v6-style simple schema:
    name, ats_type, careers_url, industry, sponsorship_history,
    english_friendly, remote_score
and both additionally understand the v7 optional columns
    seed_name, canonical_name, source_type, target_country, scope_policy,
    provider, board_slug, notes
which are preserved when present.  Under v7 a row may omit ``ats_type`` and
carry ``provider`` instead (``auto`` = resolve from the URL); the ATS scanner
applies the same resolution before dispatching to its adapters.
"""
from __future__ import annotations

import csv
import shutil
from pathlib import Path
from typing import List, Tuple

from sponsorscout.paths import SEEDS_DIR, ensure_user_data_dir

BASE_COLUMNS = [
    "name", "ats_type", "careers_url", "industry",
    "sponsorship_history", "english_friendly", "remote_score",
]

EXTRA_COLUMNS = [
    "seed_name", "canonical_name", "source_type", "target_country",
    "scope_policy", "provider", "board_slug", "notes",
]

SUPPORTED_ATS_TYPES = (
    "official_careers", "auto", "ashby", "greenhouse", "lever",
    "smartrecruiters", "personio", "recruitee", "workable", "workday",
)

SCOPE_POLICIES = ("global", "seed_url", "job_location")
SOURCE_TYPES = ("direct_employer", "recruiter")


def _bundled_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "seeds"


def bundled_ats_path() -> Path:
    return _bundled_dir() / "company_ATS_seed.csv"


def bundled_career_path() -> Path:
    return _bundled_dir() / "company_Career_seed.csv"


def user_ats_path() -> Path:
    return SEEDS_DIR / "company_ATS_seed.csv"


def user_career_path() -> Path:
    return SEEDS_DIR / "company_Career_seed.csv"


def ensure_user_seeds(force: bool = False) -> Tuple[Path, Path]:
    """Copy bundled seeds to the user data dir on first run.

    Returns the paths of the mutable user copies.  When ``force`` is set the
    user copies are replaced with the bundled defaults (used by the
    "Reset to bundled defaults" action).
    """
    ensure_user_data_dir()
    SEEDS_DIR.mkdir(parents=True, exist_ok=True)
    for bundled, user in (
        (bundled_ats_path(), user_ats_path()),
        (bundled_career_path(), user_career_path()),
    ):
        if force or not user.exists():
            shutil.copyfile(bundled, user)
    return user_ats_path(), user_career_path()


def _row_key(row: dict) -> tuple:
    """Identity key for a seed row: (lowercase name, lowercase URL)."""
    return ((row.get("name") or "").casefold(),
            (row.get("careers_url") or "").casefold())


def merge_bundled_seeds(log_fn=print) -> dict:
    """Reconcile bundled seeds with the user's mutable copies.

    On first run the bundled defaults are copied into the user data dir and
    everything thereafter reads those copies (so the packaged build stays
    read-only and the user can edit). That meant newer companies added to the
    bundled seeds never reached existing installs.

    This appends any bundled rows that are missing from the user copy
    (matched by name + careers_url). Existing / user-added rows are never
    modified or removed -- except for curated ``_SEED_REPAIRS``, which fix
    shipped rows that were wrong from the start (e.g. a recruiter scoped
    to the wrong country). Repairs only apply when the user's row still
    carries the exact old value, so a deliberate user edit is never clobbered.
    Returns {"ats": N, "career": N} = rows added + rows repaired.
    """
    ensure_user_seeds()
    added = {"ats": 0, "career": 0}
    seeds = {}
    for label, bundled, user in (
        ("ats", bundled_ats_path(), user_ats_path()),
        ("career", bundled_career_path(), user_career_path()),
    ):
        bundled_data = read_seed_rows(bundled)
        user_data = read_seed_rows(user)
        # Curated repairs first: fix wrong-from-the-start shipped values on
        # rows the user never touched (exact old-value match only).
        n_repaired = _apply_repairs_to_rows(
            label, user_data["rows"], log_fn=log_fn)
        if n_repaired:
            write_seed_rows(user, user_data["columns"], user_data["rows"])
            added[label] += n_repaired
        seeds[label] = (bundled_data, user_data, user)
    for label, (bundled_data, user_data, user) in seeds.items():
        existing = {_row_key(r) for r in user_data["rows"]}
        new_rows = [
            r for r in bundled_data["rows"] if _row_key(r) not in existing
        ]
        if not new_rows:
            continue
        # Preserve the user's column order; append any extra bundled columns.
        cols = list(user_data["columns"])
        for c in bundled_data["columns"]:
            if c not in cols:
                cols.append(c)
        merged = list(user_data["rows"]) + new_rows
        write_seed_rows(user, cols, merged)
        added[label] += len(new_rows)
        log_fn(f"Seed update: added {len(new_rows)} new {label} "
               f"companies from bundled seeds")
    return added


# ── Curated seed repairs ──────────────────────────────────────────────────
# Wrong-from-the-start bundled rows that existing installs already copied.
# merge_bundled_seeds() never touches existing user rows, so without this an
# old copy keeps the bad value forever (e.g. A2G scoped to Netherlands while
# its jobs are India/Pune -> permanent outside_or_unproven_target_country
# quarantine). A repair fires ONLY when the user's row still carries the exact
# old value -- a deliberate user edit is never clobbered.
#   {(file, name, careers_url): ({column: old_value}, {column: new_value})}
_SEED_REPAIRS = {
    ("career", "A2G Technologies", "https://a2gtechnologies.com/jobs"): (
        {"target_country": "Netherlands",
         "notes": "verified: A2G Consulting"},
        {"target_country": "India",
         "notes": "verified 2026-09-28: India/Pune recruiter "
                  "(was wrongly scoped Netherlands)"},
    ),
}


def _apply_repairs_to_rows(label: str, rows: list, log_fn=print) -> int:
    """Apply _SEED_REPAIRS for one seed file to in-memory rows."""
    n = 0
    for (rep_label, name, url), (old, new) in _SEED_REPAIRS.items():
        if rep_label != label:
            continue
        for row in rows:
            if ((row.get("name") or "").casefold() != name.casefold()
                    or (row.get("careers_url") or "").casefold()
                    != url.casefold()):
                continue
            if all((row.get(col) or "") == val for col, val in old.items()):
                row.update(new)
                n += 1
                try:
                    log_fn(f"Seed repair: {name} ({label}) retargeted "
                           f"{old.get('target_country')} -> "
                           f"{new.get('target_country')}")
                except Exception:
                    pass
    return n


def read_seed_rows(path: Path) -> dict:
    """Read a seed CSV into ``{"columns": [...], "rows": [dict, ...]}``.

    Row dicts contain only the columns actually present in the file (plus
    empty-string padding for missing BASE_COLUMNS).  No validation is
    performed here - callers use :func:`validate_row`.
    """
    if not path.exists():
        return {"columns": list(BASE_COLUMNS), "rows": []}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = [(h or "").strip() for h in (reader.fieldnames or [])]
        rows = []
        for raw in reader:
            row = {h: (raw.get(h) or "").strip() for h in headers}
            for col in BASE_COLUMNS:
                row.setdefault(col, "")
            rows.append(row)
        return {"columns": headers, "rows": rows}


def validate_row(row: dict) -> List[str]:
    """Return validation problems for one seed row (empty list = valid).

    Mirrors the rules enforced by both scanner scripts so the UI rejects a
    bad row before the scanners do.
    """
    errors: List[str] = []
    name = (row.get("name") or "").strip()
    # v7 rows may carry ``provider`` instead of ``ats_type`` ("auto" = the
    # scanner resolves the provider from the careers URL).
    ats_type = (row.get("ats_type") or row.get("provider") or "").strip().lower()
    url = (row.get("careers_url") or "").strip()
    scope = (row.get("scope_policy") or "").strip().lower()
    source_type = (row.get("source_type") or "").strip().lower()

    if not name:
        errors.append("name is required")
    if not ats_type:
        errors.append("ats_type (or provider) is required")
    elif ats_type not in SUPPORTED_ATS_TYPES:
        errors.append(f"ats_type must be one of: {', '.join(SUPPORTED_ATS_TYPES)}")
    if not url:
        errors.append("careers_url is required")
    elif url and not url.startswith(("http://", "https://")):
        errors.append("careers_url must start with http:// or https://")
    if scope and scope not in SCOPE_POLICIES:
        errors.append(f"scope_policy must be one of: {', '.join(SCOPE_POLICIES)}")
    if source_type and source_type not in SOURCE_TYPES:
        errors.append(f"source_type must be one of: {', '.join(SOURCE_TYPES)}")
    for col in ("sponsorship_history", "english_friendly", "remote_score"):
        raw = (row.get(col) or "").strip()
        if raw:
            try:
                value = int(raw)
                if not 0 <= value <= 100:
                    errors.append(f"{col} must be an integer between 0 and 100")
            except ValueError:
                errors.append(f"{col} must be an integer between 0 and 100")
    return errors
def validate_file(path: Path) -> Tuple[bool, List[str]]:
    """Validate every row of a seed file.

    Returns (ok, problems) where problems is a list of ``"line N: msg"``
    strings.  Duplicate (name + careers_url) pairs are also flagged.
    """
    data = read_seed_rows(path)
    problems: List[str] = []
    seen: set = set()
    for idx, row in enumerate(data["rows"], start=2):
        for msg in validate_row(row):
            problems.append(f"line {idx}: {msg}")
        key = ((row.get("name") or "").casefold(),
               (row.get("careers_url") or "").casefold())
        if key in seen:
            problems.append(f"line {idx}: duplicate (name, careers_url)")
        seen.add(key)
    return (not problems, problems)


def write_seed_rows(path: Path, columns: List[str], rows: List[dict]) -> int:
    """Write seed CSV.  Returns number of rows written."""
    cols = list(columns) if columns else list(BASE_COLUMNS)
    # Ensure all required columns exist in the header even if the input file
    # lacked them (scanners expect name/ats_type/careers_url).
    for col in BASE_COLUMNS:
        if col not in cols:
            cols.append(col)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in cols})
    return len(rows)


def auto_detect_ats_type(url: str) -> str:
    """Best-effort ATS type detection from a careers URL.

    Uses the ATS hostname/path fingerprinting rules from
    ``core/ats_detection``.  Falls back to ``official_careers`` when no known
    signature matches (conservative: the career crawler can still scan it).
    """
    from sponsorscout.core.ats_detection import detect_ats_from_links

    try:
        detected, _token = detect_ats_from_links([url])
        return detected or "official_careers"
    except Exception:
        return "official_careers"