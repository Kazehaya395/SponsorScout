"""F-21 pins (2026-10-22, revision 3): indicators must be visible AND look like
the native checkbox the user asked for.

History pinned here:
* original bug — ``image:`` pointed at a hard-coded developer path for an asset
  that never shipped, paired with a transparent background, so checked controls
  were invisible (Custom Scan showed no tick marks at all);
* revision 1 — pure-QSS solid accent block: visible but "way uglier & big";
* revision 2 — accent fill + white tick glyph: still not a checkbox to the user;
* revision 3 (current) — native look: white box, dark rim, **dark tick glyph**
  (``data/icons/tick.png``), no coloured fill. Path resolved at import time from
  the package, so the dangling/developer-path bug class stays closed.

Pins:
1. every ``url(...)`` in the QSS resolves to a file that exists *inside the
   package*;
2. the tick asset exists and is covered by the packaging globs;
3. a checked control shows the dark tick glyph on a white box — and shows **no
   accent fill** (that would mean regression to revision 2);
4. an unchecked control shows an empty white box with no glyph;
5. checkable list items behave the same way.
"""
import os
import pathlib
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sponsorscout.ui.style import ACCENT, ACCENT_DARK  # noqa: E402

PACKAGE_DIR = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parent
TICK_ASSET = PACKAGE_DIR / "data" / "icons" / "tick.png"

TICK_RGB = (0x1F, 0x29, 0x37)          # the dark glyph colour
ACCENT_RGB = tuple(int(ACCENT[i:i + 2], 16) for i in (1, 3, 5))
ACCENT_DARK_RGB = tuple(int(ACCENT_DARK[i:i + 2], 16) for i in (1, 3, 5))
WHITE = (255, 255, 255)
TOL = 24
MIN_TICK_PX = 12


def _qss() -> str:
    from sponsorscout.ui.style import build_qss
    return build_qss()


def _close(color, target) -> bool:
    return all(abs(c - t) <= TOL for c, t in zip(color, target))


def _counts(pixmap):
    img = pixmap.toImage()
    tick = white = accent = 0
    for x in range(img.width()):
        for y in range(img.height()):
            c = img.pixelColor(x, y)
            if c.alpha() < 8:
                continue
            rgb = (c.red(), c.green(), c.blue())
            if _close(rgb, TICK_RGB):
                tick += 1
            elif _close(rgb, ACCENT_RGB) or _close(rgb, ACCENT_DARK_RGB):
                accent += 1
            elif _close(rgb, WHITE):
                white += 1
    return tick, white, accent


@pytest.fixture()
def styled_qapp():
    from PySide6.QtWidgets import QApplication
    from sponsorscout.ui.style import build_qss

    app = QApplication.instance() or QApplication([])
    previous = app.styleSheet()
    app.setStyleSheet(build_qss())
    try:
        yield app
    finally:
        app.setStyleSheet(previous)


def test_every_qss_image_url_resolves_inside_the_package():
    targets = re.findall(r'url\("([^"]+)"\)', _qss())
    assert targets, "the checked indicator must carry its tick glyph"
    for t in targets:
        p = pathlib.Path(t)
        assert p.is_file(), f"QSS references a missing asset: {t}"
        assert PACKAGE_DIR in p.resolve().parents, (
            f"QSS asset must ship inside the package, not at {t}")


def test_tick_asset_exists_and_is_packaged():
    assert TICK_ASSET.is_file(), "data/icons/tick.png must ship in the repo"
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "data/icons/*.png" in pyproject, "package-data must cover the tick"
    assert "*.png" in manifest, "sdist manifest must cover the tick"


def test_checked_checkbox_is_a_dark_tick_on_a_white_box(styled_qapp):
    from PySide6.QtWidgets import QCheckBox

    box = QCheckBox()
    box.resize(24, 24)
    box.show()

    box.setChecked(False)
    tick_off, white_off, accent_off = _counts(box.grab())
    box.setChecked(True)
    tick_on, white_on, accent_on = _counts(box.grab())

    assert tick_off == 0 and accent_off == 0, "unchecked must be an empty box"
    assert white_off > 20, "unchecked must show a white box"
    assert tick_on >= MIN_TICK_PX, (
        f"checked must draw the dark tick glyph (got {tick_on} glyph px)")
    assert white_on > 20, "checked must stay a white box (native look)"
    assert accent_on == 0, (
        "no accent fill: that was revision 2, which the user rejected")


def test_checked_list_item_shows_the_tick(styled_qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidget, QListWidgetItem

    lw = QListWidget()
    lw.resize(260, 64)
    # Empty label on purpose: the item text is dark like the glyph and would
    # pollute the pixel count. Only the indicator may contribute here.
    item = QListWidgetItem("")
    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
    item.setCheckState(Qt.Checked)
    lw.addItem(item)
    lw.show()

    tick, _, accent = _counts(lw.grab())
    assert tick >= MIN_TICK_PX, (
        "a checked list item must show the tick (Custom Scan lists)")
    assert accent == 0, "no accent fill in the native look"

    item.setCheckState(Qt.Unchecked)
    tick, _, _ = _counts(lw.grab())
    assert tick == 0, "an unchecked list item must show no glyph"
