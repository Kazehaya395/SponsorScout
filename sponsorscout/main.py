"""SponsorScout — verified visa-sponsorship job discovery.

Entry point for the desktop GUI. Bootstraps localization, applies database
schema migrations, then launches the PySide6 main window and its event loop.

Runtime data lives in the per-user application data directory
(%APPDATA%\\SponsorScout on Windows, ~/.sponsorscout elsewhere).  Override
with the ``SPONSORSCOUT_DATA_DIR`` or ``SPONSORSCOUT_DB_PATH`` environment
variables if needed.
"""

import logging
import os
import sys
from pathlib import Path

# Ensure the project root is importable when this file is executed directly
# (e.g. ``python3 sponsorscout/main.py``), since Python only adds the script's
# own directory to sys.path in that case — not the parent that contains the
# ``sponsorscout`` package.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

from sponsorscout.i18n import load_saved_locale  # noqa: E402
from sponsorscout.ui.style import build_light_palette, build_qss  # noqa: E402

logger = logging.getLogger(__name__)

SELF_CHECK_FLAG = "--self-check"
SELF_CHECK_BROWSER_FLAG = "--self-check-browser"


def _self_check_write(msg: str) -> None:
    """Print a self-check line, safely, even in a console-less windowed build."""
    for stream in (
        getattr(sys, "stdout", None),
        getattr(sys, "__stdout__", None),
        getattr(sys, "stderr", None),
        getattr(sys, "__stderr__", None),
    ):
        if stream is None:
            continue
        try:
            print(msg, file=stream, flush=True)
            return
        except Exception:
            continue


def self_check(try_browser: bool) -> int:
    """Headless smoke test for packaged builds (used by build_deb.sh/build_exe.ps1).

    Verifies that Playwright imports in the *frozen* app and, when
    ``try_browser`` is set, that the bundled Chromium actually launches — all
    without starting Qt. Returns a process exit code so the build scripts can
    abort instead of shipping a bundle that fails every DOM scan target with
    "Playwright is required for DOM fallback".
    """
    import sponsorscout.paths  # noqa: F401  (configures the bundled _playwright dir)

    _self_check_write(
        f"SponsorScout self-check (frozen={getattr(sys, 'frozen', False)})"
    )
    _self_check_write(f"python={sys.version.split()[0]} executable={sys.executable}")

    browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    _self_check_write(f"PLAYWRIGHT_BROWSERS_PATH={browsers or '<unset>'}")
    if browsers:
        _self_check_write(f"browsers_dir_exists={Path(browsers).is_dir()}")

    failures = 0
    sync_playwright = None
    try:
        from playwright.sync_api import sync_playwright as _sp

        sync_playwright = _sp
        _self_check_write("playwright import: OK")
    except Exception as exc:
        _self_check_write(f"playwright import: FAILED ({type(exc).__name__}: {exc})")
        failures += 1

    if try_browser and sync_playwright is not None:
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
                )
                browser.close()
            _self_check_write("chromium launch: OK")
        except Exception as exc:
            _self_check_write(f"chromium launch: FAILED ({type(exc).__name__}: {exc})")
            failures += 1

    _self_check_write("self-check: " + ("FAILED" if failures else "OK"))
    return 1 if failures else 0


def main() -> None:
    """Bootstrap and launch SponsorScout."""
    if SELF_CHECK_FLAG in sys.argv or SELF_CHECK_BROWSER_FLAG in sys.argv:
        raise SystemExit(self_check(SELF_CHECK_BROWSER_FLAG in sys.argv))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger.info("SponsorScout starting up")

    # Load the persisted language preference before building the UI so every
    # widget labels itself in the correct locale from the first frame.
    load_saved_locale()

    app = QApplication(sys.argv or ["sponsorscout"])
    # Fusion renders the QSS paddings/borders predictably across Windows
    # styles (and avoids the combo/spinbox text-overlap artifacts), while
    # the explicit light palette keeps every label legible even when the
    # OS is in dark mode — Qt >= 6.5 would otherwise inject the system
    # dark palette into this light-only stylesheet.
    app.setStyle("Fusion")
    app.setPalette(build_light_palette())
    app.setStyleSheet(build_qss())
    app.setApplicationName("SponsorScout")

    from sponsorscout.ui.app import SponsorScoutApp  # deferred: needs QApplication

    window = SponsorScoutApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()