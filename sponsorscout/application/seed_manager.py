"""Seed CSV management.

Canonical sources of truth are the bundled CSVs shipped in
``sponsorscout/data`` (synced from the project-root ``company_ATS_seed.csv`` /
``company_Career_seed.csv``).  On first run the app copies them into the
per-user data directory (``~/.sponsorscout/seeds``) and ALL edits (in-app Data
Management tab, or manual CSV editing by the end-user) happen on those mutable
copies.  This keeps a packaged build read-only and lets users add companies.

Both scanners read the v7 schema:
    name, careers_url, provider, board_slug, source_type, target_country,
    scope_policy, industry, sponsorship_history, english_friendly,
    remote_score, notes
plus the two optional columns ``seed_name`` / ``canonical_name``, preserved
when present.  ``provider=auto`` means "resolve it from the careers URL".

The v6 column ``ats_type`` has been REMOVED.  It is folded into ``provider``
when an old personal seed is read, reported through ``read_seed_rows()["migrated"]``
so the UI can say so, and never written back.
"""
from __future__ import annotations

import csv
import shutil
from pathlib import Path
from typing import List, Tuple

from sponsorscout.paths import SEEDS_DIR, ensure_user_data_dir

# The schema the shipped seeds actually use, in file order. ``ats_type`` is
# GONE: it was the v6 name for what is now ``provider``, no seed has carried
# it since the v7 flip, and leaving it in this list meant the Data Management
# grid rendered a permanently empty "ats_type" column on all 373 career rows
# and all 65 ATS rows -- while the real value (greenhouse / workday /
# bamboohr …) sat in ``provider``, off the right edge of the window.
SEED_COLUMNS = [
    "name", "careers_url", "provider", "board_slug", "source_type",
    "target_country", "scope_policy", "industry", "sponsorship_history",
    "english_friendly", "remote_score", "notes",
]

# Honoured by both scanners when present, absent from the shipped seeds:
# a display name that differs from the scan label, and a canonical company
# name (career_scanner L3463-3464). Offered in the editor dialog, never
# forced into a file that does not use them.
OPTIONAL_COLUMNS = ["seed_name", "canonical_name"]

# Removed columns, still tolerated on read so an old personal seed under
# %APPDATA% is migrated rather than silently mis-read. Never written back.
LEGACY_COLUMNS = {"ats_type": "provider"}

# Every value here must have a real adapter behind it in at least one
# scanner, and every adapter must appear here -- checked 2026-10-03 against
# career_scanner._fetch_provider_jobs and ats_scanner.scan_target.
# Previously missing: oracle, digitalrecruiters, pam, teamtailor (all four
# dispatched by the career scanner) and bamboohr (adapter added 2026-10-03).
# That gap made the Data tab mark 6 perfectly good seed rows invalid --
# American Express, Decathlon Italia Retail, Pam Panorama x2, Poste Italiane
# and Teamtailor.
SUPPORTED_ATS_TYPES = (
    "official_careers", "auto", "ashby", "greenhouse", "lever",
    "smartrecruiters", "personio", "recruitee", "workable", "workday",
    "bamboohr", "oracle", "teamtailor", "pam", "digitalrecruiters",
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
        # Comment / group-header lines are not companies: without this a
        # bundled "# ----- NETHERLANDS (16) -----" header counted as a new
        # company and was appended to the user's seed (and reported in the
        # "added N new career companies" line).
        new_rows = [
            r for r in bundled_data["rows"]
            if not is_comment_row(r) and _row_key(r) not in existing
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
# old copy keeps the bad value forever. A repair fires ONLY when the user's row
# still carries the exact old value -- a deliberate user edit is never clobbered.
#   {(file, name, careers_url): [(old_values, new_values), ...]}
#
# The value is a LIST of transitions applied IN ORDER, so a row can be walked
# forward through every value we have ever shipped wrongly and still land on the
# correct one. Each transition re-checks the row's current value, so a row
# already on the final value matches nothing and is left alone.
#
# Only the SEMANTIC column is matched on. The first version also pinned the
# free-text ``notes`` field, which made the repair silently inert the moment
# anything rewrote that sentence -- and a repair that cannot fire is worse than
# no repair, because the wrong value looks handled. A deliberate user edit is
# still protected, because the user edits ``target_country``, which is exactly
# what the match tests.
_SEED_REPAIRS = {
    # FIX P0-58: this table used to walk A2G Technologies
    # Netherlands -> India -> Global on EVERY startup, and both transitions
    # fired in the same pass, so the seed value could not be set at all:
    #
    #     Seed repair: A2G Technologies (career) retargeted Netherlands -> India
    #     Seed repair: A2G Technologies (career) retargeted India -> Global
    #
    # The scan log showed both lines back to back, then the scanner's own
    # P0-44 advisory two lines later correctly reporting the consequence:
    # "A2G Technologies  scope_policy=job_location is never applied".
    # target_country=Global short-circuits _scope_allows() before it reads
    # the policy, so the country scope was dead, all 12 rows were accepted
    # instead of the 2 Dutch ones, and the Dashboard filed the company under
    # India.
    #
    # The old step (2) reasoned that "Global" is the only scope describing a
    # board that posts in both NL and IN. That is true of the BOARD and wrong
    # for this product: the seed's target_country is what the scope filter
    # uses to decide which postings to keep, and the standing requirement is
    # that results are filtered to the office country. Global does not
    # express "keep the Dutch postings", it expresses "keep everything".
    #
    # The transitions now walk BOTH stale values FORWARD to the intended one
    # instead of away from it, so copies already corrupted in
    # %APPDATA%\SponsorScout\seeds\ are repaired on the next launch rather
    # than needing a manual delete. scope_policy is set alongside, because a
    # country scope with the wrong policy is just as dead as no country.
    # A row the user has deliberately pointed somewhere else matches neither
    # transition and is left alone, which is the protection this table has
    # always relied on.
    ("career", "A2G Technologies", "https://a2gtechnologies.com/jobs"): [
        ({"target_country": "India"},
         {"target_country": "Netherlands",
          "scope_policy": "job_location",
          "notes": "verified 2026-10-03: recruiting office Eindhoven NL; "
                   "scope to the office country, not the whole board"}),
        ({"target_country": "Global"},
         {"target_country": "Netherlands",
          "scope_policy": "job_location",
          "notes": "verified 2026-10-03: recruiting office Eindhoven NL; "
                   "Global made scope_policy dead (see scanner P0-44)"}),
    ],
}


def _apply_repairs_to_rows(label: str, rows: list, log_fn=print) -> int:
    """Apply _SEED_REPAIRS for one seed file to in-memory rows."""
    n = 0
    for (rep_label, name, url), transitions in _SEED_REPAIRS.items():
        if rep_label != label:
            continue
        for row in rows:
            if ((row.get("name") or "").casefold() != name.casefold()
                    or (row.get("careers_url") or "").casefold()
                    != url.casefold()):
                continue
            for old, new in transitions:
                if all((row.get(col) or "") == val for col, val in old.items()):
                    row.update(new)
                    n += 1
                    try:
                        # FIX P0-58: report scope_policy too. The old line
                        # showed only the country, so a repair that also
                        # rewrote the policy looked like it had done less
                        # than it had.
                        _extra = ""
                        if new.get("scope_policy"):
                            _extra = f", scope_policy={new['scope_policy']}"
                        log_fn(f"Seed repair: {name} ({label}) retargeted "
                               f"{old.get('target_country')} -> "
                               f"{new.get('target_country')}{_extra}")
                    except Exception:
                        pass
    return n


def read_seed_rows(path: Path) -> dict:
    """Read a seed CSV into ``{"columns": [...], "rows": [dict, ...]}``.

    Row dicts contain only the columns actually present in the file (plus
    empty-string padding for missing SEED_COLUMNS).  No validation is
    performed here - callers use :func:`validate_row`.

    A legacy ``ats_type`` column is folded into ``provider`` on the way in
    and reported through ``migrated`` so the UI can say so out loud.
    """
    if not path.exists():
        return {"columns": list(SEED_COLUMNS), "rows": [], "migrated": []}
    # Comment lines are kept VERBATIM (including the trailing comma padding
    # the CSV writer gave them) so that read -> write reproduces the file
    # byte for byte. The seed is a file the user keeps format-stable.
    raw_comments = [
        ln.rstrip("\r\n")
        for ln in path.open(encoding="utf-8-sig", newline="").read().splitlines()
        if ln.lstrip().startswith("#")
    ]
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = [(h or "").strip() for h in (reader.fieldnames or [])]
        rows, migrated = [], []
        for raw in reader:
            row = {h: (raw.get(h) or "").strip() for h in headers}
            for old, new in LEGACY_COLUMNS.items():
                if row.get(old) and not row.get(new):
                    row[new] = row.pop(old)
                    migrated.append(f"{old} -> {new}")
                else:
                    row.pop(old, None)
            for col in SEED_COLUMNS:
                row.setdefault(col, "")
            if is_comment_row(row) and raw_comments:
                row["__raw__"] = raw_comments.pop(0)
            rows.append(row)
        headers = [h for h in headers if h not in LEGACY_COLUMNS]
        for old, new in LEGACY_COLUMNS.items():
            if new not in headers and any(r.get(new) for r in rows):
                headers.append(new)
        return {"columns": headers, "rows": rows,
                "migrated": sorted(set(migrated))}


def is_comment_row(row) -> bool:
    """True for a seed comment / group-header line such as

        # ----- NETHERLANDS (16) -----

    Both scanners skip these (career_scanner.py L3230, ats_scanner.py L1467);
    this module did not, so on the HQ-grouped seed every one of the 21 headers
    was reported as a company missing a provider and a URL -- 42 phantom
    errors, and validate_file() called the user's own seed invalid.

    The rows are still RETURNED by read_seed_rows so that a UI round-trip
    cannot silently delete the grouping; they are only skipped where a row is
    treated as a company.
    """
    return (row.get("name") or "").strip().startswith("#")


def validate_row(row: dict) -> List[str]:
    """Return validation problems for one seed row (empty list = valid).

    Mirrors the rules enforced by both scanner scripts so the UI rejects a
    bad row before the scanners do.
    """
    errors: List[str] = []
    if is_comment_row(row):
        return errors          # a comment line is not a company
    name = (row.get("name") or "").strip()
    # ``provider`` is the field. "auto" = let the scanner resolve it from the
    # careers URL. A legacy ``ats_type`` has already been folded in by
    # read_seed_rows; accepting it here too keeps a hand-edited file working.
    ats_type = (row.get("provider") or row.get("ats_type") or "").strip().lower()
    url = (row.get("careers_url") or "").strip()
    scope = (row.get("scope_policy") or "").strip().lower()
    source_type = (row.get("source_type") or "").strip().lower()

    if not name:
        errors.append("name is required")
    if not ats_type:
        errors.append("provider is required (use \"auto\" to detect it)")
    elif ats_type not in SUPPORTED_ATS_TYPES:
        errors.append(f"provider must be one of: {', '.join(SUPPORTED_ATS_TYPES)}")
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
    cols = [c for c in (columns or SEED_COLUMNS) if c not in LEGACY_COLUMNS]
    # Only name/careers_url/provider are structurally required; everything
    # else is appended only if the caller already had it. A save must not
    # invent columns -- the seed is a file the user keeps byte-stable.
    for col in ("name", "careers_url", "provider"):
        if col not in cols:
            cols.append(col)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            # A comment / group header is written back as the single line it
            # was, not as a CSV record: the DictWriter would turn
            # "# ----- NETHERLANDS (16) -----" into
            # "# ----- NETHERLANDS (16) -----,,,,,,,,,,," on every save.
            if is_comment_row(row):
                fh.write((row.get("__raw__")
                          or (row.get("name") or "").rstrip()) + "\r\n")
                continue
            writer.writerow({k: row.get(k, "") for k in cols})
    return len(rows)


def auto_detect_provider(url: str) -> str:
    """Best-effort provider detection from a careers URL.

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