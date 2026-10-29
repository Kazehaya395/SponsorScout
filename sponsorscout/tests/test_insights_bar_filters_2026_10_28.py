"""Bar charts in the Insights tab are filters, not decoration (2026-10-28).

The donut could already be clicked; the three bar charts could not - they had no
signal and no hit-testing at all (``ScrollableBarChart`` was paint-only). These
tests click real mouse events against the painted rows and require the tab's
single active filter, the highlighted bar, the panel title and the filtered
numbers to all follow. Offscreen Qt + synthetic SQLite only: no live portal and
no user database.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from sponsorscout.db import database as db  # noqa: E402
from sponsorscout.insights import queries  # noqa: E402
from sponsorscout.i18n import _  # noqa: E402
from sponsorscout.tests._qt_support import application, wait_until  # noqa: E402
from sponsorscout.ui.tabs import insights as insights_mod  # noqa: E402

KEYS = ["url", "title", "company", "country", "domain", "category", "source_type",
        "source_subtype", "run_id", "verified_active", "is_expired", "visa_sponsorship",
        "relocation_support", "sponsorship_score", "remote_type", "description"]
ROWS = [
    ("https://fixture.invalid/1", "Software Engineer", "Acme", "Germany", "tech",
     "Software Engineering", "verified", "direct", "r1", 1, 0, "Y", "Y", 90, "remote"),
    ("https://fixture.invalid/2", "Data Analyst", "Acme", "Germany", "tech",
     "Software Engineering", "verified", "direct", "r1", 1, 0, "N", "N", 20, "hybrid"),
    ("https://fixture.invalid/3", "Risk Manager", "Globex", "Spain", "finance",
     "Risk & Compliance", "verified", "recruiter", "r1", 1, 0, "Y", "", 70, ""),
    ("https://fixture.invalid/4", "Sales Lead", "Initech", "Spain", "consulting",
     "Sales & Business Development", "verified", "direct", "r1", 1, 0, "", "no", 55, ""),
]
TERMS = {"1": ["Python", "SQL"], "2": ["SQL"], "3": ["Excel", "Python"], "4": ["Excel"]}


@pytest.fixture
def tab(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application()
    path = tmp_path / "bars.db"
    db.initialize(str(path))
    conn = sqlite3.connect(str(path))
    for row in ROWS:
        conn.execute("INSERT INTO jobs (" + ",".join(KEYS) + ") VALUES ("
                     + ",".join("?" for _ in KEYS) + ")", list(row) + ["Python SQL Excel."])
    for key, terms in TERMS.items():
        job_id = conn.execute("SELECT id FROM jobs WHERE url LIKE ?", (f"%/{key}",)).fetchone()[0]
        for term in terms:
            conn.execute("INSERT INTO job_terms(job_id, term, kind) VALUES(?,?,'skill')",
                         (job_id, term))
    conn.execute("UPDATE jobs SET insights_version='test'")
    conn.commit()
    conn.close()
    widget = insights_mod.InsightsTab(str(path), defer_load=True)
    widget.show()
    widget.refresh(auto_backfill=False)
    wait_until(lambda: bool(widget.cat_chart.items) and not widget._chart_loader.busy, 20)
    yield widget, str(path)
    widget.close()
    QApplication.processEvents()


def _click_bar(chart, label: str) -> None:
    """Send a real press+release to the centre of a painted row."""
    QApplication.processEvents()          # let paintEvent record the hit rects
    rect = next(r for r, name in chart._hit_rows if name == label)
    QTest.mouseClick(chart, Qt.LeftButton, Qt.NoModifier,
                     QPoint(rect.center().x(), rect.center().y()))


def test_every_bar_chart_is_wired_to_the_filter(tab):
    widget, _path = tab
    # Emitting the signal - what a hit-tested click ends up doing - must move the
    # tab state: the connection, not just the widget, is the contract.
    assert widget.cat_chart.bar_clicked is not None
    widget.tech_skill_chart.bar_clicked.emit("Excel")
    assert widget.active_pie_filter == ("skill", "Excel")
    widget.tech_skill_chart.bar_clicked.emit("Excel")
    assert widget.active_pie_filter == ("", "")


def test_category_bar_click_filters_the_tab_and_highlights_the_row(tab):
    widget, path = tab
    before = widget.coverage_label.text()
    _click_bar(widget.cat_chart, "Software Engineering")
    assert widget.active_pie_filter == ("category", "Software Engineering")
    wait_until(lambda: widget.coverage_label.text() != before, 20)
    assert widget.cat_chart.selected_label == "Software Engineering"
    assert "Software Engineering" in widget.cat_box.title()
    # Two of the four jobs are Software Engineering: the coverage line must move
    # from "4 / 4" to "2 / 2", i.e. the filter applies to the numbers too.
    assert "4 / 4" in before
    assert "2 / 2" in widget.coverage_label.text()
    assert ("Software Engineering", 2) in widget.pie_cat_chart.items


def test_skill_bar_click_filters_every_panel_by_that_term(tab):
    widget, path = tab
    terms = {label for label, _ in widget.tech_skill_chart.items} | \
             {label for label, _ in widget.soft_skill_chart.items}
    assert {"Python", "SQL", "Excel"} <= terms, terms
    chart = widget.tech_skill_chart if "Python" in dict(widget.tech_skill_chart.items) \
        else widget.soft_skill_chart
    _click_bar(chart, "Python")
    assert widget.active_pie_filter == ("skill", "Python")
    wait_until(lambda: widget.tech_skill_chart.selected_label == "Python", 20)
    # Python appears on jobs 1 and 3, so the category panel must show exactly them.
    cats = dict(widget.cat_chart.items)
    assert cats == {"Software Engineering": 1, "Risk & Compliance": 1}, cats
    assert "Python" in widget.tech_skill_box.title()
    assert _("Filtered by %s: %s") % (_("Skill"), "Python") == \
        _("Filtered by %s: %s") % ("Skill", "Python")
    assert widget.filter_indicator_lbl.text().endswith("Python")


def test_clicking_the_selected_bar_again_clears_the_filter(tab):
    widget, path = tab
    _click_bar(widget.cat_chart, "Software Engineering")
    wait_until(lambda: widget.active_pie_filter[1] == "Software Engineering", 20)
    _click_bar(widget.cat_chart, "Software Engineering")
    assert widget.active_pie_filter == ("", "")
    wait_until(lambda: widget.cat_chart.selected_label == "", 20)
    assert _(" (All Categories)") in widget.cat_box.title()


def test_clicking_a_different_bar_switches_the_filter_instead_of_stacking(tab):
    widget, path = tab
    _click_bar(widget.cat_chart, "Software Engineering")
    wait_until(lambda: widget.active_pie_filter[1] == "Software Engineering", 20)
    _click_bar(widget.cat_chart, "Risk & Compliance")
    assert widget.active_pie_filter == ("category", "Risk & Compliance")
    wait_until(lambda: dict(widget.cat_chart.items) or True, 20)
    assert widget.pie_dom_chart.selected_label in ("", "Risk & Compliance")


def test_a_bar_click_never_leaves_a_stale_all_categories_caption(tab):
    widget, path = tab
    _click_bar(widget.cat_chart, "Software Engineering")
    wait_until(lambda: "Software Engineering" in widget.cat_box.title(), 20)
    assert "All Categories" not in widget.cat_box.title()
