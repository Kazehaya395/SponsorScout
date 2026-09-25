"""Tools tab i18n regression: pause/resume labels and hover texts follow the
language switch.

Bug: button texts/tooltips were set once at construction and never refreshed
by ``retranslate()``, so switching language left stale strings (e.g.
"Riprendi" and an Italian Stop tooltip on an English UI).

Also pins the relabel: the stop control is "Pause" — stopping checkpoints
progress in the DB and Resume continues later (even after an app restart).
"""
import os


def _make_tools(db_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:  # pragma: no cover - PySide6 missing
        import pytest
        pytest.skip("PySide6 unavailable")
    app = QApplication.instance() or QApplication([])
    assert app is not None
    from sponsorscout.ui.tabs.tools import ToolsTab
    return ToolsTab(db_path)


def test_pause_resume_and_tooltips_follow_language(db_path):
    from sponsorscout.i18n import set_locale

    try:
        # Construct while Italian — this is how the stale strings appeared.
        set_locale("it")
        tab = _make_tools(db_path)
        assert tab.resume_btn.text() == "Riprendi"
        assert tab.stop_btn.text() == "Pausa"
        assert "Ferma ora la scansione" in tab.stop_btn.toolTip()

        # Switch to English: NOTHING may stay Italian.
        set_locale("en")
        tab.retranslate()
        assert tab.resume_btn.text() == "Resume"
        assert tab.stop_btn.text() == "Pause"
        assert "Riprendi" not in (tab.resume_btn.text()
                                  + (tab.resume_btn.toolTip() or ""))
        # Hover text is English too (the reported bug).
        assert "Press Resume later" in tab.stop_btn.toolTip()
        assert "Scan every seeded company" in tab.scan_btn.toolTip()
        assert "Scansiona" not in (tab.scan_btn.toolTip() or "")

        # ...and back to Italian re-applies Italian labels + hover texts.
        set_locale("it")
        tab.retranslate()
        assert tab.stop_btn.text() == "Pausa"
        assert "Ferma ora la scansione" in tab.stop_btn.toolTip()
        assert tab.resume_btn.text() == "Riprendi"
    finally:
        set_locale("en")


def test_retranslate_does_not_query_the_database(db_path, monkeypatch):
    """A language switch must not do synchronous DB work.

    Regression: retranslate() called _refresh_resume_button() ->
    db.get_resumable_scan() (opens a connection, reads scan_runs and both
    seed CSVs). That is ~25 ms when idle and can block up to the SQLite
    busy_timeout while a scan is writing, which froze the window on every
    language switch. The checkpoint tooltip is now refreshed on tab entry
    (showEvent) and after each scan instead.
    """
    from sponsorscout.db import database as db
    from sponsorscout.i18n import set_locale

    tab = _make_tools(db_path)
    tab.show()  # the tab-entry path is allowed to query
    calls = []
    original = db.get_resumable_scan

    def _spy(db_path_arg):
        calls.append(db_path_arg)
        return original(db_path_arg)

    monkeypatch.setattr(db, "get_resumable_scan", _spy)
    try:
        set_locale("en")
        tab.retranslate()
        set_locale("it")
        tab.retranslate()
        assert not calls, ("retranslate() queried the DB %d time(s); the "
                           "checkpoint tooltip must be refreshed on tab "
                           "entry / scan end, not on language switch" % len(calls))
    finally:
        set_locale("en")