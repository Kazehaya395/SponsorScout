# Changelog

## Unreleased (2026-10-17)

### Fixed
- **Static portals — card titles (P95).** Card-style anchors now take the title from
  the card's heading and the location from its location element, not the whole card text.
- **Static portals — load-more controls (P96).** "Show more" / "Mostra ancora" style
  controls are recognised by id, class or href, so long listings are read to the end.
- **Static portals — paging (P97).** Static pagination follows next-page and load-more
  controls, stops on a repeated page, and is capped by `SPONSORSCOUT_STATIC_MAX_PAGES`
  (default 12). The scan diagnostic reports the pages read.
- **Static portals — navigation is not a job (P98).** Pager numbers, symbols, nav labels,
  social links, the homepage and non-job paths are no longer quarantined as jobs.
  Path rules match whole segments.
- **Static portals — clean title attribute (P99).** A card's `title` attribute is used only
  when the visible text contains it.
- **Scan diagnostics.** The dead-end message now says "since company start".

### Packaging (bundle slimming)
- Playwright is pinned to `>=1.49.0,<2.0.0` (requirements.txt, pyproject.toml).
- Playwright installs only the headless Chromium shell (`--only-shell`). The app only launches
  headless, so the full browser (~400 MB) is no longer bundled.
- Qt plugin folders the app never loads are removed from all bundles: image formats, TLS,
  network info, generic input and SQL drivers. Qt translation files are kept, so Qt's stock
  dialogs show in Italian and English.
- Windows installer compression is `lzma2/ultra64`. Linux DEB and RPM use `xz -1`.
- Fixed: `build_rpm.sh` now creates the app directory before copying files (the RPM build
  failed with `cp: target ... No such file or directory`).
- Fixed: `build_deb.sh` passed the compression level with `-S` (strategy), so `xz -1` was never
  applied. It now uses `-z`.
- DEB `Depends` now lists the libraries headless Chromium and the Qt XCB plugin need (ATK, NSS,
  NSPR, cups, pango, xcb helpers, and others). The RPM gets these automatically.
- Build scripts are executable (`build_rpm.sh`, `build_deb.sh`).
- Windows build removes a leftover full `PySide6` / `PySide6-Addons` install from the build venv.
  Pip does not uninstall packages that are no longer listed, so an older venv still bundled
  Qt WebEngine (a 194 MB DLL), Quick, QML, PDF and FFmpeg. The build excludes those modules and
  fails if `Qt6WebEngineCore.dll` is still in the bundle.

### Seeds
- Elior Italia: replaced the dead TalentSoft URL with the eliorgroup.com job-offers listing.
- Robert Walters Germany: `target_country` changed from `Global` to `Germany`.
- Tigotà: empty fields filled (`direct_employer`, Italy, `seed_url`, Retail/Beauty).

### Packaging
- Runtime dependency changed from `PySide6` to `PySide6-Essentials` (requirements.txt,
  pyproject.toml). The full meta-package also installed WebEngine, Addons and PDF wheels that
  the app never imports. Essentials provides the QtCore, QtGui and QtWidgets modules the app uses.
- PyInstaller excludes for the unused QtQuick and QtQml modules (build_exe.ps1,
  build_rpm.sh, build_deb.sh, SponsorScout.spec). Measured on Linux: the PyInstaller bundle
  before Playwright Chromium fell from 800 MB to 370 MB; the frozen app starts offscreen cleanly.

### Documentation
- Codebase guide cleaned up: descriptions of code no longer in the tree were removed,
  and the env-var table lists only knobs read in code.

### Tests
- New: `sponsorscout/tests/test_static_paging_2026_10_17.py` (offline, stubbed `urlopen`).
- Full suite: 379 passed, 9 failed (pre-existing), 4 skipped.
