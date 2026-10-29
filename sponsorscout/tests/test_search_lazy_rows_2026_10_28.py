"""Search result rows are read lazily, and the Sponsor checkbox means one thing.

Issue 2026-10-28 (performance, option A). `run_search()` used to copy every
matching row into a dict and pre-build the experience display string before
rendering one page: 35,555 rows cost 590 ms of which ~310 ms was that copy. The
rows are now wrapped (`_LazyRow`) and the display cell is built for the rows the
user can actually see (280 ms, and 1.7 ms for a 500-row page).

The second contract here is the one that used to be able to disagree with
itself: the checkbox filtered `visa_sponsorship = 'Y'` in SQL while the cell
displayed `_verdict_cell(...)`, which also accepts `yes/true/1`. Both now use
the same set, so a row cannot be filtered out while showing "Sponsor = Y".
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sponsorscout.db import database as db  # noqa: E402
from sponsorscout.i18n import _  # noqa: E402
from sponsorscout.tests._qt_support import application, wait_until  # noqa: E402
from sponsorscout.ui.tabs.search import (  # noqa: E402
    _experience_cell, _LazyRow, _row_values, _verdict_cell, FILTER_ALL)

COLUMNS = ["url", "title", "company", "country", "source_type", "verified_active",
           "is_expired", "visa_sponsorship", "relocation_support", "eu_blue_card_verdict",
           "eu_blue_card", "has_relocation", "experience_required", "experience_level",
           "experience_min_years", "remote_type", "sponsorship_score"]

# url suffix, title, visa verdict, relocation verdict, experience level
ROWS = [
    ("1", "Data Engineer", "Y", "Y", "Senior"),
    ("2", "Legacy Sponsor Row", "yes", "true", ""),
    ("3", "No Sponsor Row", "N", "no", "Junior"),
    ("4", "Unknown Row", "Unknown", "", ""),
]


@pytest.fixture
def board(tmp_path):
    path = tmp_path / "search.db"
    db.initialize(str(path))
    conn = sqlite3.connect(str(path))
    for suffix, title, visa, reloc, level in ROWS:
        values = [f"https://fixture.invalid/jobs/{suffix}", title, "Acme", "Germany",
                  "verified", 1, 0, visa, reloc, visa, 1 if str(visa).lower() in ("y", "yes") else 0,
                  1 if str(reloc).lower() in ("y", "yes", "true") else 0, "", level, None,
                  "hybrid", 60]
        conn.execute("INSERT INTO jobs (" + ",".join(COLUMNS) + ") VALUES ("
                     + ",".join("?" for _ in COLUMNS) + ")", values)
    conn.commit()
    conn.close()
    return str(path)


def _query(path, **extra):
    return list(db.search_jobs(path, title="", company="", location="", country="All",
                              remote_filter="All", regex=False, run_filter="All", **extra))


def test_lazy_row_matches_the_dict_it_replaced(board):
    row = _LazyRow(_query(board)[0])
    raw = dict(_query(board)[0])
    assert row["title"] == raw["title"]
    assert row.get("company") == raw["company"]
    assert row.get("not_a_column", "fallback") == "fallback"
    assert row.get("experience_min_years", 0) == (raw["experience_min_years"] or 0)
    assert set(row.keys()) == set(raw.keys())
    expected = _experience_cell(raw["experience_required"], raw["experience_level"],
                                raw["experience_min_years"])
    assert row["_exp_display"] == expected
    assert row["_exp_display"] == expected          # cached, not recomputed
    # the display helper the table uses must accept the wrapper unchanged
    assert _row_values(row)[:2] == [raw["title"], raw["company"]]


def test_experience_cell_is_only_built_for_rows_that_need_it(board, monkeypatch):
    from sponsorscout.ui.tabs import search as search_mod
    calls = []

    def counting(required, level, years):
        calls.append((required, level, years))
        return _experience_cell(required, level, years)

    monkeypatch.setattr(search_mod, "_experience_cell", counting)
    rows = [_LazyRow(r) for r in _query(board)]
    assert calls == [], "wrapping the result set must not format every row"
    first = rows[0]._row
    assert rows[0]["_exp_display"] == _experience_cell(first["experience_required"],
                                                       first["experience_level"],
                                                       first["experience_min_years"])
    assert len(calls) == 1
    rows[0]["_exp_display"]
    assert len(calls) == 1, "the display cell has to be cached on the row"


@pytest.mark.parametrize("value,cell_matches_checkbox", [
    ("Y", True), ("yes", True), ("true", True), ("1", True),
    ("N", False), ("no", False), ("Unknown", False), ("", False),
])
def test_sponsor_checkbox_and_sponsor_cell_agree(tmp_path, value, cell_matches_checkbox):
    path = tmp_path / f"visa-{value or 'blank'}.db"
    db.initialize(str(path))
    conn = sqlite3.connect(str(path))
    values = [f"https://fixture.invalid/{value}", "Role", "Acme", "Germany", "verified", 1, 0,
              value, "", value, 0, 0, "", "", None, "", 60]
    conn.execute("INSERT INTO jobs (" + ",".join(COLUMNS) + ") VALUES ("
                 + ",".join("?" for _ in COLUMNS) + ")", values)
    conn.commit()
    conn.close()
    shown_as_yes = _verdict_cell(value) == "Y"
    returned = _query(str(path), sponsorship_only=True)
    assert shown_as_yes == cell_matches_checkbox
    # The filter and the cell cannot disagree about the same stored value.
    assert bool(returned) == cell_matches_checkbox
    assert (not returned) or _verdict_cell(returned[0]["visa_sponsorship"]) == "Y"


def test_checkbox_filter_narrows_without_a_python_pass_over_every_row(board):
    everyone = _query(board)
    sponsored = _query(board, sponsorship_only=True)
    relocating = _query(board, relocation_only=True)
    assert len(everyone) == 4
    # rows 1 ('Y') and 2 ('yes' + 'true'): the legacy spelling is a Yes verdict
    assert {r["title"] for r in sponsored} == {"Data Engineer", "Legacy Sponsor Row"}
    assert {r["title"] for r in relocating} == {"Data Engineer", "Legacy Sponsor Row"}
    assert all(_verdict_cell(r["visa_sponsorship"]) == "Y" for r in sponsored)


def test_tab_search_renders_from_the_lazy_rows(tmp_path, monkeypatch, board):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application()
    from sponsorscout.tests._qt_support import wait_loaders
    from sponsorscout.ui.tabs.search import SearchTab

    class AwaitedSearchTab(SearchTab):
        # Test-only wait adapter: the tab loads on a background thread, and the
        # assertions below are about the rendered result, not about asynchrony.
        def run_search(self):
            super().run_search()
            wait_loaders(self)

    tab = AwaitedSearchTab(board)
    tab.show()
    tab.run_search()
    wait_until(lambda: tab.table.rowCount() > 0, 15)
    assert tab.page_info.text().startswith("Showing 1")
    titles = {tab.table.item(r, 0).text() for r in range(tab.table.rowCount())}
    assert "Data Engineer" in titles and "No Sponsor Row" in titles
    tab.sponsor_check.setChecked(True)
    tab.run_search()
    assert {tab.table.item(r, 0).text() for r in range(tab.table.rowCount())} == \
        {"Data Engineer", "Legacy Sponsor Row"}
    assert "of 2 results" in tab.page_info.text()
    tab.sponsor_check.setChecked(False)
    tab.run_search()
    assert tab.table.rowCount() == 4
    tab.close()
