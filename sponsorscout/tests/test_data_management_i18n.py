"""Data Management tab i18n: guidance hint follows the language, seed-grid
headers follow the FILE.

Contract (v0.2.0 merge): the grid shows the seed file's own header — raw
column names, identical in every language, written back unchanged on Save
(that was the fix for the 15-hard-coded-columns bug with three permanently
empty ones).  Column headers are therefore NOT translated and the old
``_column_label`` helper is gone.  Still translated and pinned here: the
guidance hint (``retranslate()`` must refresh it — the redesigned tab
dropped that once), the editor buttons and the tab captions.
"""
import os


# Keep the tab referenced for the whole session (see test_tools_i18n).
_TABS: list = []


def _make_tab():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:  # pragma: no cover - PySide6 missing
        import pytest
        pytest.skip("PySide6 unavailable")
    app = QApplication.instance() or QApplication([])
    assert app is not None
    from sponsorscout.ui.tabs.data_management import DataManagementTab
    tab = DataManagementTab()
    _TABS.append(tab)
    return tab


def _headers(editor):
    return [editor.table.horizontalHeaderItem(c).text()
            for c in range(editor.table.columnCount())]


def test_hint_and_seed_headers_follow_language():
    from sponsorscout.i18n import LANGUAGES, set_locale

    try:
        set_locale("en")
        tab = _make_tab()
        assert "Manage the source URLs" in tab._hint.text()
        # The grid shows the FILE's own header: raw column names — they are
        # the data I/O contract, so no "_"-free translated labels any more.
        en_headers = _headers(tab.ats_editor)
        assert "careers_url" in en_headers
        assert tab.ats_editor.count_label.text().endswith("companies")

        # Switch to Italian: hint, buttons and tab captions must follow;
        # the raw headers must NOT change.
        set_locale("it")
        tab.retranslate()
        assert tab._hint.text() == LANGUAGES["it"][
            "Manage the source URLs scanned by SponsorScout.  ATS portals "
            "are scanned via their job-board APIs; career portals are "
            "crawled on the company site.  Edits are saved to your "
            "personal seed files and take effect on the next scan."]
        it = LANGUAGES["it"]
        assert tab.ats_editor.save_btn.text() == it["Save to CSV"]
        assert tab.ats_editor.add_btn.text() == it["Add\u2026"]
        assert tab._tabs.tabText(0) == it["ATS portals"]
        assert _headers(tab.ats_editor) == en_headers

        # …and back to English restores the hint.
        set_locale("en")
        tab.retranslate()
        assert "Manage the source URLs" in tab._hint.text()
    finally:
        set_locale("en")


def test_grid_columns_follow_the_seed_file():
    """The fallback column list IS the v7 seed contract; the translated
    ``_column_label`` helper was removed together with the hard-coded
    15-column header."""
    try:
        import PySide6  # noqa: F401
    except Exception:  # pragma: no cover - PySide6 missing
        import pytest
        pytest.skip("PySide6 unavailable")
    from sponsorscout.application import seed_manager as sm
    from sponsorscout.ui.tabs import data_management as dm

    assert dm.FALLBACK_COLUMNS == sm.SEED_COLUMNS
    assert not hasattr(dm, "_column_label")
