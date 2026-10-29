"""Regression pins for the 2026-10-27 Insights/Dashboard screenshot bugs.

Synthetic SQLite + offscreen Qt only. No live portal, network or user DB.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from sponsorscout.db import database as db  # noqa: E402
from sponsorscout.insights import queries  # noqa: E402
from sponsorscout.i18n import LANGUAGES  # noqa: E402
from sponsorscout.tests._qt_support import application, wait_until  # noqa: E402
from sponsorscout.ui.tabs import insights as insights_mod  # noqa: E402
from sponsorscout.ui.tabs.dashboard import DashboardTab  # noqa: E402


def _synthetic_db(tmp_path: Path, monkeypatch) -> str:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application()
    path = tmp_path / "insights.db"
    db.initialize(str(path))
    conn = db.get_connection(str(path))
    keys = [
        "url", "title", "company", "country", "source_type", "source_subtype",
        "verified_active", "is_expired", "description", "industry",
        "visa_sponsorship", "relocation_support", "eu_blue_card_verdict",
        "sponsorship_score", "remote_type",
    ]
    for i in range(8):
        row = dict(
            url=f"https://fixture.invalid/jobs/{i}.html",
            title=["Software Engineer", "Data Analyst", "Financial Controller", "Sales Manager"][i % 4],
            company=["Acme", "Globex", "Initech", "Umbrella"][i % 4],
            country=["Germany", "Spain", "France", "Netherlands"][i % 4],
            source_type="verified", source_subtype="direct",
            verified_active=1, is_expired=0,
            description="Python SQL Excel collaboration leadership visa sponsorship relocation support. " * 20,
            industry=["Tech & Software", "Financial Services & Fintech", "E-Commerce, Retail & Fashion", "Staffing, HR & Recruiting"][i % 4],
            visa_sponsorship="Unknown", relocation_support="Unknown",
            eu_blue_card_verdict="Unknown", sponsorship_score=35,
            remote_type="remote" if i % 3 == 0 else "hybrid" if i % 3 == 1 else "",
        )
        conn.execute(
            "INSERT INTO jobs (" + ",".join(keys) + ") VALUES (" + ",".join("?" for _ in keys) + ")",
            [row[k] for k in keys],
        )
    conn.commit()
    conn.close()
    queries.backfill_existing_job_terms(str(path), force_all=True, batch_size=128)
    return str(path)


def test_insights_donut_charts_receive_data(tmp_path, monkeypatch):
    path = _synthetic_db(tmp_path, monkeypatch)
    tab = insights_mod.InsightsTab(path, defer_load=True)
    tab.refresh(auto_backfill=False)
    wait_until(lambda: not tab._chart_loader.busy, 10)
    assert tab.cat_chart.items, "granular category bar chart should have data"
    assert tab.pie_cat_chart.items, "category donut was empty (cat_filter bug)"
    assert tab.pie_dom_chart.items, "domain donut was empty (dom_filter bug)"
    assert tab.pie_cat_chart._slices, "category donut should compute slices"
    assert tab.pie_dom_chart._slices, "domain donut should compute slices"
    tab.deleteLater()
    QApplication.processEvents()


def test_dashboard_labels_and_tooltips_are_honest(tmp_path, monkeypatch):
    path = _synthetic_db(tmp_path, monkeypatch)
    tab = DashboardTab(path)
    assert "Sponsor Score" in tab.companies_title.text()
    header = tab.companies_table.horizontalHeaderItem(3)
    assert header is not None and "Max Sponsor Score" in header.text()
    assert "Unknown is not No" in tab._value_labels["Sponsored Jobs"].toolTip()
    assert "Unknown is not No" in tab._value_labels["EU Blue Card"].toolTip()

    tab.retranslate()
    assert "Sponsor Score" in tab.companies_title.text()
    header = tab.companies_table.horizontalHeaderItem(3)
    assert header is not None and "Max Sponsor Score" in header.text()
    assert "Unknown is not No" in tab._value_labels["Sponsored Jobs"].toolTip()
    tab.deleteLater()
    QApplication.processEvents()


def test_dashboard_sponsor_strings_have_italian_translations():
    for key in (
        "Top Employers by Sponsor Score",
        "Max Sponsor Score",
        "Counts jobs with an explicit Yes visa-sponsorship verdict. Unknown is not No; unread JDs stay Unknown.",
        "Counts jobs with an explicit Yes EU Blue Card verdict. Unknown is not No; unread JDs stay Unknown.",
    ):
        assert key in LANGUAGES["en"]
        assert LANGUAGES["it"].get(key), f"missing Italian translation: {key}"


def test_insights_progress_row_hides_after_completion(tmp_path, monkeypatch):
    path = _synthetic_db(tmp_path, monkeypatch)
    monkeypatch.setattr(insights_mod.QTimer, "singleShot", lambda msec, fn: fn())
    tab = insights_mod.InsightsTab(path, defer_load=True)
    tab.insights_progress_row.show()

    class Event:
        def is_set(self):
            return False

    class Worker:
        cancel_event = Event()
        error = ""
        count = 3

        def deleteLater(self):
            return None

    worker = Worker()
    tab._insights_worker = worker
    tab._finish_insights_worker(worker)
    QApplication.processEvents()
    assert tab.insights_progress_row.isHidden(), "completed progress row should be hidden"
    tab.deleteLater()
    QApplication.processEvents()
