from __future__ import annotations

import os
import sys
from pathlib import Path


APP_DIR_ENV = "SPONSORSCOUT_DATA_DIR"
DB_PATH_ENV = "SPONSORSCOUT_DB_PATH"


def _configure_bundled_playwright_browsers_path() -> None:
    """If running as a frozen build with a bundled ``_playwright`` directory
    next to the executable, point Playwright at it.

    All three build scripts (``build_exe.ps1``, ``build_deb.sh`` and
    ``build_rpm.sh``) bundle Chromium into a
    ``_playwright`` folder alongside the executable so JS-rendered career
    pages work without a separate ``playwright install`` step. The Linux
    .deb wraps the binary in a launcher script that exports
    ``PLAYWRIGHT_BROWSERS_PATH`` directly, so it's always correct there.

    On Windows, ``installer.iss`` instead writes ``PLAYWRIGHT_BROWSERS_PATH``
    to ``HKCU\\Environment``. That registry change does **not** propagate to
    the app launched immediately after install via Inno's ``[Run]`` section
    (new environment variables only reach processes started by a shell that
    re-reads ``HKCU\\Environment``, e.g. after the next login) — so the very
    first run after installing would have Playwright look in its default
    cache location, find nothing, and every SPA career portal would return 0
    jobs again.

    Setting it here, from the app's own code, makes the bundled Chromium
    discoverable on the first launch regardless of registry propagation
    timing, while still honoring an explicitly-set environment variable if
    the user has configured one themselves.
    """
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return  # respect an explicit override

    if not getattr(sys, "frozen", False):
        return  # only relevant for packaged builds

    exe_dir = Path(sys.executable).resolve().parent
    candidate = exe_dir / "_playwright"
    if candidate.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(candidate)


_configure_bundled_playwright_browsers_path()


def _get_windows_appdata_dir(app_name: str) -> Path:
    """Return the *per-user* application data directory on Windows.

    Uses %APPDATA% (e.g. C:\\Users\\<user>\\AppData\\Roaming) which is the
    standard location for per-user application data on Windows.  Falls back
    to the home directory if the environment variable is missing.
    """
    env_path = os.environ.get("APPDATA")
    if env_path:
        return Path(env_path) / app_name
    return Path.home() / f".{app_name.lower()}"


def get_user_data_dir() -> Path:
    """Return the single per-user directory for all mutable app data."""
    override = os.environ.get(APP_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    # On Windows, use the APPDATA standard directory
    if os.name == "nt":
        return _get_windows_appdata_dir("SponsorScout")
    return Path.home() / ".sponsorscout"


USER_DATA_DIR = get_user_data_dir()
DB_PATH = Path(os.environ.get(DB_PATH_ENV, "")).expanduser() if os.environ.get(DB_PATH_ENV) else USER_DATA_DIR / "sponsorscout.db"

# Per-user, user-editable copy of the seed CSVs.  Bundled seeds in
# sponsorscout/data are the defaults; the app copies them here on first run
# and all user edits (Data Management tab) target these mutable copies so a
# packaged build never writes into its own application bundle.
SEEDS_DIR = USER_DATA_DIR / "seeds"

# Raw scan-evidence artifacts (the algorithm scripts' CSV outputs and
# per-run scan logs).  Kept outside the DB so users can inspect them.
SCAN_OUTPUT_DIR = USER_DATA_DIR / "scan_output"


def ensure_user_data_dir() -> Path:
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return USER_DATA_DIR


def ensure_scan_output_dir() -> Path:
    SCAN_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return SCAN_OUTPUT_DIR