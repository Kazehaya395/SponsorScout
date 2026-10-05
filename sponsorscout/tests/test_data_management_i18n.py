"""Data Management tab i18n regression: guidance hint + seed-grid headers.

Bug (screenshot): with the UI set to Italian the tab's guidance paragraph
("Manage the source URLs scanned by SponsorScout…") and every table column
header (``name, careers_url, provider, board_slug, source_type…``) stayed
English:

* the hint QLabel was built once in ``__init__`` and ``retranslate()``
  never touched it;
* the horizontal header received the raw snake_case CSV column names and
  never went through ``_()`` at all;
* the "File:" caption and the "N companies" count were set once at load.

Pin: after ``set_locale("it")`` + ``retranslate()`` the hint, the headers
and the count must all be Italian, and switching back must restore
English — with the raw column names used for data I/O left untouched.
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
        # English UI shows human labels, never the raw CSV column names.
        en_headers = _headers(tab.ats_editor)
        assert "Careers URL" in en_headers
        assert not any("_" in h for h in en_headers), en_headers
        assert tab.ats_editor.count_label.text().endswith("companies")

        # Switch to Italian: hint, headers AND count must all follow.
        set_locale("it")
        tab.retranslate()
        assert tab._hint.text() == LANGUAGES["it"][
            "Manage the source URLs scanned by SponsorScout.  ATS portals "
            "are scanned via their job-board APIs; career portals are "
            "crawled on the company site.  Edits are saved to your "
            "personal seed files and take effect on the next scan."]
        it_headers = _headers(tab.ats_editor)
        assert "URL carriera" in it_headers         # careers_url
        assert "Paese target" in it_headers         # target_country
        assert "Storico sponsorship" in it_headers  # sponsorship_history
        assert not any("_" in h for h in it_headers), it_headers
        assert tab.ats_editor.count_label.text().endswith("aziende")
        assert tab._tabs.tabText(0) == "Portali ATS"

        # …and back to English restores every one of them.
        set_locale("en")
        tab.retranslate()
        assert "Manage the source URLs" in tab._hint.text()
        assert _headers(tab.ats_editor) == en_headers
        assert tab.ats_editor.count_label.text().endswith("companies")
    finally:
        set_locale("en")


def test_column_labels_cover_every_seed_column():
    """Every v7 seed column must have an explicit translated label."""
    from sponsorscout.application import seed_manager as sm
    from sponsorscout.ui.tabs.data_management import _column_label

    for col in sm.SEED_COLUMNS:
        label = _column_label(col)
        assert label and label == label.strip()
        # The label is display-only; the raw column name must survive.
        assert "_" not in label, (col, label)
        if col != "industry":  # "Industry" is its own English label
            assert label != col, col
