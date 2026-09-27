"""Playwright failures must be diagnosable, and builds must smoke-test them.

Two production defects are covered here:

1. The ``except ModuleNotFoundError`` guards discarded the exception, so a
   frozen bundle that lost Playwright reported only an opaque
   "Playwright is required for DOM fallback" for every target — with no way to
   tell whether ``playwright``, ``greenlet`` or ``pyee`` was missing.
2. Neither build script verified the produced bundle, so it shipped broken.

Run ``SponsorScout --self-check --self-check-browser`` on an installed build to
reproduce the packaged-runtime check locally.
"""

import inspect
import sys
from pathlib import Path

import pytest

from sponsorscout.scanning.ats import ats_scanner
from sponsorscout.scanning.career import career_scanner as career
from sponsorscout.services import browser_fetcher

ROOT = Path(__file__).resolve().parents[2]
DEB = (ROOT / "build_deb.sh").read_text(encoding="utf-8")
EXE = (ROOT / "build_exe.ps1").read_text(encoding="utf-8")
PIPELINE = (ROOT / "sponsorscout" / "scanning" / "pipeline.py").read_text(
    encoding="utf-8"
)


def test_import_error_is_recorded_not_swallowed():
    for mod in (career, ats_scanner):
        assert hasattr(mod, "PLAYWRIGHT_IMPORT_ERROR")
        assert mod.PLAYWRIGHT_IMPORT_ERROR is None or isinstance(
            mod.PLAYWRIGHT_IMPORT_ERROR, str
        )
        assert isinstance(mod._playwright_unavailable_reason(), str)
        assert mod._playwright_unavailable_reason()


def test_reason_is_surfaced_in_the_raised_error(monkeypatch):
    monkeypatch.setattr(
        career,
        "PLAYWRIGHT_IMPORT_ERROR",
        "ModuleNotFoundError: No module named 'greenlet'",
    )
    assert "greenlet" in career._playwright_unavailable_reason()

    src = inspect.getsource(career)
    assert "Playwright is required for DOM fallback ({_pw_reason})" in src
    assert "Playwright is required for --detail ({_pw_reason})" in src
    assert 'diagnostics.append(f"playwright unavailable: {_pw_reason}")' in src


def test_browser_fetcher_exposes_the_import_reason():
    # The dev environment has Playwright installed.
    assert browser_fetcher._playwright_import_error() is None
    assert browser_fetcher._playwright_available() is True


def test_pipeline_warning_distinguishes_missing_package_from_missing_browser():
    assert "_playwright_import_error" in PIPELINE
    assert "NOT importable" in PIPELINE


def test_main_exposes_self_check_and_it_passes():
    from sponsorscout import main as main_mod

    assert callable(main_mod.self_check)
    # Import-only: Playwright is present in the dev venv, so this must pass.
    assert main_mod.self_check(try_browser=False) == 0


def test_main_routes_the_self_check_flag(monkeypatch):
    from sponsorscout import main as main_mod

    monkeypatch.setattr(sys, "argv", ["sponsorscout", "--self-check"])
    with pytest.raises(SystemExit) as excinfo:
        main_mod.main()
    assert excinfo.value.code == 0


def test_build_scripts_collect_playwright_and_smoke_test_the_bundle():
    for script in (DEB, EXE):
        assert "--collect-all playwright" in script
        assert "--hidden-import greenlet" in script
        assert "--hidden-import pyee" in script
        assert "--self-check" in script
        assert "--self-check-browser" in script
        # Verify the build venv BEFORE PyInstaller runs, so a broken dependency
        # fails in seconds instead of producing a bundle that fails at runtime.
        assert "from playwright.sync_api import sync_playwright" in script
        # The old mode collected submodules but not data/binaries.
        assert "--collect-submodules playwright" not in script
