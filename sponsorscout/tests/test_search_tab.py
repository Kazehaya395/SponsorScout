"""Regression tests for the Search tab filter/render pipeline.

Guards four bugs:

1. ``db.search_jobs()`` returns ``sqlite3.Row`` objects (row_factory in
   ``db/database.py``), which have no ``.get()`` — run_search crashed with
   AttributeError on the first row, leaving the table blank and aborting
   ``_refresh_all()`` before the Applications/Tools tabs refreshed.

2. The Experience dropdown must contain EXACTLY the unique values shown in
   the Experience column (rendered by ``_experience_cell``: ``4+``,
   ``Senior``, ``NA``, ...) — not raw ``experience_level`` distincts.

3. Dropdowns must always show the FULL unique value list of the data —
   never collapse to ["All", <selected>] — so switching values is always
   one click + Search (no All -> Search detour).

4. Filter option values are ABSOLUTE (English / raw data) in every app
   language: the sentinel stays "All" (never "Tutti") and Experience
   values stay "None"/"Mentioned"/"mo", so filters keep working after a
   language switch.
"""
import os

from sponsorscout.core import persistence


def _seed_searchable_job(db_path, _conn=None, **overrides):
    from sponsorscout.db import database as db
    conn = _conn if _conn is not None else db.get_connection(db_path)
    job = {
        "title": "Backend Engineer",
        "company": "Acme",
        "url": "https://jobs.example/10",
        "country": "Germany",
        "location": "Berlin",
        "experience_level": "Senior",
        "experience_required": "4+ years",
        "experience_min_years": 4.0,
        "experience_source": "detail_text",
        "visa_sponsorship": "Y",
        "relocation_support": "Unknown",
        "eu_blue_card_verdict": "Unknown",
    }
    job.update(overrides)
    # upsert_job defaults verified_active=True / is_expired=False, so the
    # row passes search_jobs' default verified+active filters.
    persistence.upsert_job(conn, job)
    if _conn is None:
        conn.close()


def _make_tab(db_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:  # pragma: no cover - PySide6 missing
        import pytest
        pytest.skip("PySide6 unavailable")
    app = QApplication.instance() or QApplication([])
    assert app is not None

    from sponsorscout.ui.tabs.search import SearchTab
    return SearchTab(db_path)


def _combo_values(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def test_run_search_renders_sqlite_rows(db_path):
    """run_search must render rows returned by db.search_jobs().

    Regression: sqlite3.Row has no .get(); the Experience column used to
    raise AttributeError on the very first row and leave the table blank.
    """
    _seed_searchable_job(db_path)
    tab = _make_tab(db_path)
    tab.populate_static_filters()  # app._refresh_all flow
    tab.run_search()  # raised AttributeError before the fix

    assert tab.table.rowCount() == 1
    assert tab.table.item(0, 0).text() == "Backend Engineer"
    assert tab.table.item(0, 1).text() == "Acme"
    # Experience column: compacted absolute requirement from the JD.
    assert tab.table.item(0, 4).text() == "4+"
    # Verdict cells: tri-state Y / ? rendering.
    assert tab.table.item(0, 5).text() == "Y"
    assert tab.table.item(0, 6).text() == "?"

    # Tooltip goes through _experience_tooltip(), which also used row.get().
    assert "4+ years" in tab.table.item(0, 4).toolTip()


def test_experience_dropdown_is_exact_unique_column_values(db_path):
    """Dropdown = exact unique column values AND always the FULL list.

    Regression: the combo used to be rebuilt from the FILTERED result set,
    collapsing to ["All", <selected>] after every pick and forcing the
    All -> Search -> pick -> Search detour.
    """
    # Four rows: rendered Experience "4+" (DE), "Senior" (DE), "NA" (DE)
    # and "4+" (FR) — the France row drives the direct country-switch check.
    _seed_searchable_job(db_path)  # required="4+ years", level="Senior"
    _seed_searchable_job(
        db_path,
        title="Staff Engineer", url="https://jobs.example/11",
        experience_level="Senior", experience_required="Unknown",
        experience_min_years=None, experience_source="",
    )
    _seed_searchable_job(
        db_path,
        title="Graduate Engineer", url="https://jobs.example/12",
        experience_level="", experience_required="",
        experience_min_years=None, experience_source="",
    )
    _seed_searchable_job(
        db_path,
        title="Paris Backend", url="https://jobs.example/13",
        country="France", location="Paris",
    )

    tab = _make_tab(db_path)
    tab.populate_static_filters()
    tab.run_search()

    assert tab.table.rowCount() == 4
    combo = tab.experience_combo
    # Unfiltered: dropdown is EXACTLY the unique text shown in the column,
    # ordered like the column sorts (numbers, levels, then NA).
    column_values = {tab.table.item(r, 4).text() for r in range(4)}
    assert column_values == {"4+", "Senior", "NA"}
    assert _combo_values(combo) == ["All", "4+", "Senior", "NA"]
    assert _combo_values(tab.country_combo) == ["All", "France", "Germany"]

    # Pick a value: the table filters, the dropdowns keep the FULL list.
    combo.setCurrentText("Senior")
    tab.run_search()
    assert tab.table.rowCount() == 1
    assert tab.table.item(0, 0).text() == "Staff Engineer"
    assert _combo_values(combo) == ["All", "4+", "Senior", "NA"]  # no collapse
    assert _combo_values(tab.country_combo) == ["All", "France", "Germany"]

    # Direct switch Senior -> "4+" WITHOUT the All -> Search detour.
    combo.setCurrentText("4+")
    tab.run_search()
    assert {tab.table.item(r, 0).text() for r in range(tab.table.rowCount())} \
        == {"Backend Engineer", "Paris Backend"}

    # Country: France is pickable directly while Germany is still listed.
    tab.country_combo.setCurrentText("France")
    tab.run_search()
    assert tab.table.rowCount() == 1
    assert tab.table.item(0, 0).text() == "Paris Backend"
    assert _combo_values(tab.country_combo) == ["All", "France", "Germany"]
    tab.country_combo.setCurrentText("Germany")  # still listed, no All detour
    tab.run_search()
    assert tab.table.rowCount() == 1
    assert tab.table.item(0, 0).text() == "Backend Engineer"

    # Back to All restores the full result set.
    tab.country_combo.setCurrentIndex(0)
    combo.setCurrentIndex(0)
    tab.run_search()
    assert tab.table.rowCount() == 4


def test_italian_locale_keeps_absolute_filter_values(db_path):
    """Filters must be language-independent (regression: 'Tutti')."""
    _seed_searchable_job(db_path)  # -> "4+"
    _seed_searchable_job(
        db_path, title="No Exp Role", url="https://jobs.example/14",
        experience_required="None required", experience_min_years=None,
    )
    from sponsorscout.i18n import set_locale

    try:
        set_locale("it")
        tab = _make_tab(db_path)
        tab.populate_static_filters()
        # The sentinel is ABSOLUTE: "All" even in Italian, never "Tutti".
        for combo in (tab.country_combo, tab.remote_combo,
                      tab.experience_combo):
            assert combo.itemText(0) == "All"
        tab.run_search()
        # Regression: SQL used to receive 'Tutti' -> zero rows.
        assert tab.table.rowCount() == 2

        # Experience values are absolute English in every language...
        options = _combo_values(tab.experience_combo)
        assert options[0] == "All"
        assert "4+" in options and "None" in options
        assert "Nessuna" not in options and "mesi" not in options
        column_values = {tab.table.item(r, 4).text()
                         for r in range(tab.table.rowCount())}
        assert column_values == {"4+", "None"}

        # ...so filtering by the absolute value works under Italian too.
        tab.experience_combo.setCurrentText("None")
        tab.run_search()
        assert tab.table.rowCount() == 1
        assert tab.table.item(0, 0).text() == "No Exp Role"
    finally:
        set_locale("en")


def test_language_switch_between_populate_and_search(db_path):
    """Mixed state: populate in English, switch to Italian, then search."""
    _seed_searchable_job(db_path)
    from sponsorscout.i18n import set_locale

    tab = _make_tab(db_path)
    tab.populate_static_filters()  # English state: item 0 == "All"
    try:
        set_locale("it")
        tab.retranslate()  # labels switch; combo VALUES stay absolute
        assert tab.country_combo.itemText(0) == "All"
        tab.run_search()
        # Regression: the old code compared currentText() against
        # _("All") == "Tutti" and filtered every row away in this state.
        assert tab.table.rowCount() == 1
    finally:
        set_locale("en")


def _seed_searchable_jobs(db_path, count, url_prefix):
    """Insert ``count`` jobs over ONE connection (fast enough for page tests)."""
    from sponsorscout.db import database as db
    conn = db.get_connection(db_path)
    for i in range(count):
        _seed_searchable_job(
            db_path, title=f"Engineer {i}",
            url=f"{url_prefix}{i}", _conn=conn)
    conn.close()


def test_pagination_pages_through_every_row(db_path):
    """One page renders at a time; ◀/▶ reach EVERY row (nothing hidden)."""
    titles = {f"Engineer {i}" for i in range(250)}
    _seed_searchable_jobs(db_path, 250, "https://jobs.example/2")
    tab = _make_tab(db_path)
    tab.populate_static_filters()
    # Page size is a fixed preset list (100 / 200 / 500 / 1000); the smallest
    # keeps this dataset spread over three pages.
    tab.page_size_combo.setCurrentIndex(0)
    assert tab._page_size() == 100
    # The rebuild is debounced; run_search() applies the selected size directly.
    tab._page_size_timer.stop()
    tab.run_search()

    seen = []
    # Page 1 of 3: prev disabled, next enabled, label shows the slice.
    assert tab.table.rowCount() == 100
    assert tab.page_info.text() == "Showing 1–100 of 250 results"
    assert not tab.page_prev_btn.isEnabled()
    assert tab.page_next_btn.isEnabled()
    seen += [tab.table.item(r, 0).text() for r in range(tab.table.rowCount())]

    # Page 2 via▶ — re-rendered from the cached list, no re-query.
    tab.page_next_btn.click()
    assert tab.table.rowCount() == 100
    assert tab.page_info.text() == "Showing 101–200 of 250 results"
    assert tab.page_prev_btn.isEnabled()
    assert tab.page_next_btn.isEnabled()
    seen += [tab.table.item(r, 0).text() for r in range(tab.table.rowCount())]

    # Last page: ▶ disabled, its remaining rows still shown.
    tab.page_next_btn.click()
    assert tab.table.rowCount() == 50
    assert tab.page_info.text() == "Showing 201–250 of 250 results"
    assert tab.page_prev_btn.isEnabled()
    assert not tab.page_next_btn.isEnabled()
    seen += [tab.table.item(r, 0).text() for r in range(tab.table.rowCount())]

    # Union of the three pages == every seeded row, no duplicates.
    assert len(seen) == 250 and set(seen) == titles

    # ◀ back to page 1.
    tab.page_prev_btn.click()
    assert tab.page_info.text() == "Showing 101–200 of 250 results"
    tab.page_prev_btn.click()
    assert tab.page_info.text() == "Showing 1–100 of 250 results"

    # A new search resets to the first page.
    tab.run_search()
    assert tab.table.rowCount() == 100
    assert tab.page_info.text() == "Showing 1–100 of 250 results"
    assert not tab.page_prev_btn.isEnabled()


def test_page_rebuild_reuses_cells_and_keeps_selection(db_path):
    """A page change must reuse cells, not recreate the whole table.

    Regression: every render allocated a fresh QTableWidgetItem per cell and
    wrote URL data on all of them (~200 ms per 5,000 cells on this machine),
    which is what made switching page size feel like a freeze. Existing items
    must be updated in place, and the selected job must stay selected when it
    is still on the page.
    """
    from sponsorscout.ui.tabs.search import URL_ROLE

    _seed_searchable_jobs(db_path, 120, "https://jobs.example/5")
    tab = _make_tab(db_path)
    tab.populate_static_filters()
    tab.page_size_combo.setCurrentIndex(0)
    tab._page_size_timer.stop()
    tab.run_search()
    assert tab.table.rowCount() == 100

    # Select a row, then move to the next page (same cells, different rows).
    tab.table.selectRow(2)
    selected_url = tab.table.item(2, 0).data(URL_ROLE)
    assert selected_url
    tab.page_next_btn.click()
    assert tab.table.rowCount() == 20
    # Cells are reused, so the stale row index must NOT be left pointing at a
    # different job on the new page.
    assert tab._selected_url() is None
    assert tab.table.currentRow() == -1

    # Back to page 1, then re-render the SAME page: items must be REUSED
    # (same Python objects), not recreated.
    tab.page_prev_btn.click()
    assert tab.table.rowCount() == 100
    assert tab._selected_url() is None
    identity = id(tab.table.item(0, 4))
    tab._render_page()
    assert id(tab.table.item(0, 4)) == identity, "page render replaced the cells"

    # Grow the first page only ADDS rows: the existing cells must survive.
    # (A page-size change never re-queries, so grow_only is safe here.)
    tab.page_size_combo.setCurrentText("500")
    tab._page_size_timer.stop()
    tab._render_page(grow_only=True)
    assert tab.table.rowCount() == 120
    assert id(tab.table.item(0, 4)) == identity, "growing the page replaced cells"

    # A NEW search must refill every visible row, even when it needs MORE rows
    # than before: the new result set can put a different job in row 0.
    # Regression: the grow-only fast path used to fire for searches too, so the
    # table kept showing the PREVIOUS search's top rows (stale jobs) merged
    # with the new ones.
    tab.title_edit.setText("Unique Target Role")
    tab.run_search()
    assert tab.table.rowCount() == 0, "no seeded job matches this title"
    assert tab.page_info.text() == "Showing 0–0 of 0 results"

    # Widen back to everything: the single leftover cell from the empty result
    # set must be replaced, not kept as a stale job.
    tab.title_edit.clear()
    tab.run_search()
    assert tab.table.rowCount() == 120
    assert tab.table.item(0, 0).text() == "Engineer 0"

    # Re-select the SAME job and re-render the same page: the selection is
    # restored, because the selected URL is still visible.
    tab.table.selectRow(0)
    kept = tab.table.item(0, 0).data(URL_ROLE)
    tab._render_page()
    assert tab._selected_url() == kept
    assert tab.table.currentRow() == 0


def test_page_size_change_is_safe_and_returns_to_first_page(db_path):
    """Changing rows-per-page must not freeze or crash the Search table.

    Regression: the editable page-size combo rebuilt and re-sorted the whole
    page on EVERY keystroke, and every Qt sort goes through
    ``_CellItem.__lt__`` — the exact code path that can overflow the C++ stack.
    The combo is now a fixed preset list, so each choice causes exactly one
    debounced rebuild from cached rows (no query), it returns to the first
    page, and an active user sort is preserved.
    """
    import time
    from PySide6.QtCore import Qt

    _seed_searchable_jobs(db_path, 600, "https://jobs.example/4")
    tab = _make_tab(db_path)
    tab.populate_static_filters()
    tab.run_search()
    assert tab.table.rowCount() == 500

    # Sort by Title the way the user does (header click sets the indicator
    # before sectionClicked fires, exactly like a real click).
    header = tab.table.horizontalHeader()
    header.setSortIndicator(0, Qt.DescendingOrder)
    header.sectionClicked.emit(0)  # handler records the sort AND re-renders
    assert tab._user_sort_section == 0
    assert tab._user_sort_order == Qt.DescendingOrder
    titles = [tab.table.item(r, 0).text() for r in range(tab.table.rowCount())]
    assert titles == sorted(titles, reverse=True), "user sort was not applied"

    for text, expected_rows, expected_label in (
            ("100", 100, "Showing 1–100 of 600 results"),
            ("200", 200, "Showing 1–200 of 600 results"),
            ("1000", 600, "Showing 1–600 of 600 results")):
        t0 = time.perf_counter()
        tab.page_size_combo.setCurrentText(text)
        # The debounce timer must NOT be doing synchronous GUI work per
        # keystroke: selecting a value returns immediately.
        assert (time.perf_counter() - t0) < 0.05, "page-size change blocked the UI"
        assert tab._page_size_timer.isActive(), "rebuild was not debounced"

        # Run the single debounced rebuild deterministically.
        tab._page_size_timer.stop()
        t0 = time.perf_counter()
        tab._render_page()
        elapsed = time.perf_counter() - t0
        assert elapsed < 2.0, f"page rebuild too slow: {elapsed:.2f}s"
        assert tab.table.rowCount() == expected_rows
        assert tab.page_info.text() == expected_label
        assert tab._page == 0, "page size change must return to the first page"
        # The user's sort is still the one driving the row order.
        assert tab._user_sort_section == 0
        titles = [tab.table.item(r, 0).text() for r in range(tab.table.rowCount())]
        assert titles == sorted(titles, reverse=True)
        # Every visible row must be filled: a Qt sort would have shuffled the
        # item objects and left holes in the table.
        for r in range(tab.table.rowCount()):
            for col in range(tab.table.columnCount()):
                assert tab.table.item(r, col) is not None, (r, col)


def test_header_sort_orders_data_and_keeps_columns_aligned(db_path):
    """Sorting a column must reorder whole ROWS, never shuffle cells.

    Regression: Qt's ``sortItems()`` moves QTableWidgetItem objects between
    rows, so an in-place refresh could address the wrong row (and a row could
    end up with a missing cell). The table now sorts the row DATA in Python,
    so column N of row N always belongs to the same job.
    """
    from PySide6.QtCore import Qt
    from sponsorscout.ui.tabs.search import URL_ROLE

    _seed_searchable_job(
        db_path, title="Zeta Engineer", url="https://jobs.example/z",
        experience_required="4+ years", company="Acme", country="Germany")
    _seed_searchable_job(
        db_path, title="Alpha Engineer", url="https://jobs.example/a",
        experience_required="3-5 years", company="Zeta", country="France")
    _seed_searchable_job(
        db_path, title="Mid Engineer", url="https://jobs.example/m",
        experience_required="", company="Mid", country="Italy",
        experience_level="", experience_min_years=None)
    tab = _make_tab(db_path)
    tab.populate_static_filters()
    tab.run_search()
    assert tab.table.rowCount() == 3

    header = tab.table.horizontalHeader()

    # Sort by Company ascending: rows move, columns stay together.
    # Expected order is by COMPANY (Acme, Mid, Zeta), each paired with its own
    # job's title/country — this is what proves rows move as whole units.
    header.setSortIndicator(1, Qt.AscendingOrder)
    header.sectionClicked.emit(1)
    assert [(tab.table.item(r, 0).text(), tab.table.item(r, 1).text(),
             tab.table.item(r, 2).text()) for r in range(3)] == [
        ("Zeta Engineer", "Acme", "Germany"),
        ("Mid Engineer", "Mid", "Italy"),
        ("Alpha Engineer", "Zeta", "France"),
    ]
    assert tab.table.item(0, 0).data(URL_ROLE) == "https://jobs.example/z"
    assert tab.table.item(2, 0).data(URL_ROLE) == "https://jobs.example/a"

    # Sort by Experience: the numeric column uses the numeric key, not text.
    header.setSortIndicator(4, Qt.AscendingOrder)
    header.sectionClicked.emit(4)
    assert [tab.table.item(r, 4).text() for r in range(3)] == ["3-5", "4+", "NA"]
    # Still row-aligned after the second sort.
    assert tab.table.item(0, 0).text() == "Alpha Engineer"
    assert tab.table.item(1, 0).text() == "Zeta Engineer"
    assert tab.table.item(2, 0).text() == "Mid Engineer"

    # Qt's own item sorting must stay off: it is the slow/crash-prone path.
    assert not tab.table.isSortingEnabled()


def test_headers_translate_but_column_order_stays_fixed(db_path):
    """Table headers are UI chrome: they follow the app language.

    Regression: the Search table set its labels from the raw HEADERS list, so
    an Italian UI showed "Title / Company / Country ..." — the filter/column
    values are canonical on purpose, but the HEADERS themselves are chrome and
    must be translated. The column INDEXES must never change: they are what the
    sort keys, tooltips and tests address.
    """
    from sponsorscout.i18n import _, set_locale
    from sponsorscout.ui.tabs.search import HEADERS, _header_labels

    try:
        tab = _make_tab(db_path)
        english = [tab.table.horizontalHeaderItem(i).text()
                   for i in range(len(HEADERS))]
        assert english == HEADERS, "English headers must be the canonical keys"

        set_locale("it")
        tab.retranslate()
        italian = [tab.table.horizontalHeaderItem(i).text()
                   for i in range(len(HEADERS))]
        assert italian == [_(h) for h in HEADERS]
        assert italian != HEADERS, "headers did not translate in Italian"
        # A header that is identical in both languages (e.g. "Sponsor") is
        # legitimate; every OTHER header must actually change.
        untranslated = [en for en, it in zip(english, italian)
                        if en == it and en != "Sponsor"]
        assert not untranslated, (
            "headers still English in Italian: %s" % untranslated)
        # Same number of columns, same (fixed) order — just relabelled.
        assert len(italian) == len(HEADERS)

        # Back to English: the canonical labels return.
        set_locale("en")
        tab.retranslate()
        assert [tab.table.horizontalHeaderItem(i).text()
                for i in range(len(HEADERS))] == HEADERS
    finally:
        set_locale("en")


def test_retranslate_does_not_rebuild_the_table(db_path):
    """Language switch must not re-create every cell / re-sort the table.

    Regression: retranslate() used to call _render_page(), which rebuilt all
    500 cells and ran the Python-callback item sort on EVERY language switch —
    ~150 ms of blocking GUI-thread work that froze the window even when the
    Search tab was hidden and nothing visible had changed.
    """
    import time
    _seed_searchable_jobs(db_path, 60, "https://jobs.example/3")
    tab = _make_tab(db_path)
    tab.populate_static_filters()
    tab.page_size_combo.setCurrentIndex(0)
    tab._page_size_timer.stop()
    tab.run_search()
    assert tab.table.rowCount() == 60

    # Keep the exact item objects: retranslate() may re-localise tooltips in
    # place, but must never replace the cells themselves.
    before = [id(tab.table.item(r, 4)) for r in range(tab.table.rowCount())]
    tab.retranslate()
    after = [id(tab.table.item(r, 4)) for r in range(tab.table.rowCount())]
    assert before == after, "retranslate() rebuilt the table cells"
    assert tab.table.rowCount() == 60

    # The pager label must still be localised after the switch.
    from sponsorscout.i18n import set_locale
    try:
        set_locale("it")
        tab.retranslate()
        assert tab.page_info.text() == "Mostrati 1–60 di 60 risultati"
    finally:
        set_locale("en")

    # Timing guard: a retranslate on a full page must stay far below the
    # previous ~150 ms rebuild (a rebuild+sort costs well over 50 ms here).
    tab.retranslate()
    t0 = time.perf_counter()
    tab.retranslate()
    assert (time.perf_counter() - t0) < 0.05, "retranslate() got slow again"

