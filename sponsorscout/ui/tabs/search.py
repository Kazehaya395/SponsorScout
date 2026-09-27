"""Search tab: filter row + results table (ported from the tkinter Search tab).

Blue Card / Relocation are rendered as honest three-state values:
'Y' (confirmed), 'N' (explicitly excluded), '?' (unknown / no evidence).
"""

import re

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QHeaderView,
    QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from sponsorscout.db import database as db
from sponsorscout.i18n import _

# Canonical (untranslated) column order — this list defines the COLUMN
# INDEXES, so it must never be translated or reordered. Only the visible
# labels go through _() (see _header_labels): headers are UI chrome and
# follow the app language, while the cell VALUES stay canonical so that
# filter dropdowns keep matching the table exactly.
HEADERS = ["Title", "Company", "Country", "Location", "Experience", "Sponsor",
           "Blue Card", "Reloc", "Remote", "Posted"]


def _header_labels() -> list:
    """HEADERS translated for display, in the same (fixed) column order."""
    return [_(h) for h in HEADERS]

URL_ROLE = Qt.UserRole + 1

# Absolute filter sentinel: item 0 of every filter dropdown, added
# UNTRANSLATED in all languages ("All" even in Italian — never "Tutti").
# Rule for every filter option VALUE: canonical data strings (English /
# raw DB values), never translated — only UI chrome (labels, buttons,
# placeholders, tooltips) goes through _().  This keeps filters working
# across language switches and keeps dropdown values byte-identical to
# the rendered column values.
FILTER_ALL = "All"


# ── Column width policy ─────────────────────────────────────────────────────
# Qt's QHeaderView.sectionSizeHint() is NOT content aware for item views: it
# returns a character-count estimate of the HEADER LABEL ("Company" -> 88 px
# on the real dataset) while the widest company cell needs ~220 px. Sizing the
# sections from it is exactly what cut "Amazon Italia", "Hamburg, Germany" and
# "2026-05-12" down to "Amazon …" / "Hambur…" / "2026-0…" at a width the user
# could not correct. The Search tab therefore measures the TEXT it is about to
# show (see _content_widths) and keeps every column draggable.
_COL_PADDING = 16      # 6 px QSS cell padding on both sides + 1 px grid line
_COL_MAX_WIDTH = 520   # cap per column; a cut value keeps its full text as a
                       # tooltip (_update_clipped_tooltips), so no data is lost
_COL_MIN_WIDTH = 58    # keeps a (still draggable) empty column usable
#: Columns whose cells are always one character (Y / N / ?): their width can
#: never exceed their own header label, so the fit skips measuring them.
_FIXED_CELL_COLS = frozenset({5, 6, 7})


# ── Experience column rendering (FIX P0-30 display layer) ────────────────────
# The scanners store the ABSOLUTE requirement exactly as the JD states it
# ("4+ years", "3-5 years", "6 months", "None required", "Mentioned",
# "Unknown") — see the experience block in ats_scanner.py / career_scanner.py.
# The table shows it in compact form ("4+", "3-5", "6 mo") and the verbatim
# text stays in the cell tooltip, so nothing is lost. When the ad states
# NEITHER a figure NOR a level there is exactly one value (see
# _EXP_NO_STATEMENT) instead of two near-synonyms that read as two facts.
#
# Unit words are accepted in every language the seeds use (EN/DE/IT/NL/FR/ES),
# so a requirement string produced by a non-English board still compacts.
_EXP_YEAR_UNIT_RX = (r"(?:years?|yrs?\.?|jahre?n?|anni|anno|jaar|jaren|ans?|"
                     r"anos?|a[nñ]os?)")
_EXP_MONTH_UNIT_RX = (r"(?:months?|mon\.?|monate?n?|mesi|mese|maanden|maand|"
                      r"mois|meses|mes)")
_EXP_SPEC_RX = re.compile(
    r"^(?P<lo>\d{1,3}(?:[.,]\d)?)\s*(?P<plus>\+)?\s*"
    r"(?:(?:-|–|—|\bto\b|\bbis\b|\ba\b|\bà\b)\s*"
    r"(?P<hi>\d{1,3}(?:[.,]\d)?)\s*\+?\s*)?"
    r"(?P<unit>" + _EXP_YEAR_UNIT_RX + "|" + _EXP_MONTH_UNIT_RX + r")\.?$",
    re.I)
_EXP_MONTH_FULL_RX = re.compile(_EXP_MONTH_UNIT_RX, re.I)
# Values that mean "the JD carries no experience signal".  An EXPLICIT
# "no experience needed" statement is NOT absent — _EXP_NONE_RX renders it.
_EXP_ABSENT = {"", "unknown", "n/a", "na", "not specified", "unspecified",
               "not stated", "not mentioned"}
_EXP_NONE_RX = re.compile(
    r"^(?:none|no\s|not required|without|nessun|nessuna|keine[ns]?|geen|"
    r"aucune|sin experiencia|niet vereist)", re.I)
# The ONE value the column shows when the ad states neither a figure nor a
# level.  It replaces the old "Referenced" / "NA" pair: two labels for "no
# usable requirement" read as two different facts, and "Referenced" still left
# the user guessing WHAT was referenced.  "?" is the honesty marker the
# neighbouring Sponsor / Blue Card / Reloc columns already use for "no
# evidence", so the whole table speaks one vocabulary.  Nothing is lost: the
# cell tooltip still tells the two cases apart (_experience_tooltip), and the
# stored scanner value is untouched — this is a display decision only.
_EXP_NO_STATEMENT = "?"


def _compact_experience(text: str) -> str:
    """'4+ years' -> '4+' | '3-5 years' -> '3-5' | '6 months' -> '6 mo'.

    Anything that is not an explicit numeric spec is returned unchanged, so a
    level word or a genuine prose answer survives verbatim.
    """
    t = (text or "").strip()
    if t.lower() in _EXP_ABSENT:
        return _EXP_NO_STATEMENT
    if _EXP_NONE_RX.match(t):
        return "None"
    # The scanner stores the literal "Mentioned" for an ad that names
    # experience without quantifying it ("Customer support experience is a
    # plus").  That is not a requirement level, so it renders as the SAME
    # single value as "never mentioned" instead of inventing a second label.
    if t.lower() == "mentioned":
        return _EXP_NO_STATEMENT
    m = _EXP_SPEC_RX.match(t)
    if not m:
        return t
    lo, hi = m.group("lo"), m.group("hi")
    if hi:
        core = "%s-%s" % (lo, hi)
    elif m.group("plus"):
        core = "%s+" % lo
    else:
        core = lo
    if _EXP_MONTH_FULL_RX.fullmatch(m.group("unit")):
        core += " mo"
    return core


def _experience_cell(required: str, level: str, min_years) -> str:
    """Experience cell for the Search table.

    Priority (user-specified):
      1. the ABSOLUTE requirement stated in the JD  ('4+', '3-5', '6 mo')
      2. 'None' when the ad explicitly asks for no experience
      3. the seniority level when one is known   ('Senior', 'Lead')
      4. '_EXP_NO_STATEMENT' ('?') — ONE single value whether the ad merely
         names experience without a figure or never names it at all.  The
         three-state columns next to it already say '?' for "no evidence", so
         the column keeps a single, unambiguous vocabulary.
    """
    req = (required or "").strip()
    lvl = (level or "").strip()
    # Stored "Mentioned" is weaker than a level word: fall through to the level
    # so a level-only row keeps showing its level.
    if req and req.lower() not in _EXP_ABSENT and req.lower() != "mentioned":
        return _compact_experience(req)
    if lvl and lvl.lower() not in _EXP_ABSENT:
        return lvl
    try:
        years = float(min_years) if min_years is not None else None
    except (TypeError, ValueError):
        years = None
    if years:
        return ("%d+" % int(years)) if years == int(years) \
            else ("%g+" % years)
    return _EXP_NO_STATEMENT


def _experience_tooltip(row) -> str:
    """Hover detail for the Experience cell.

    This is where the single "?" value is explained: the tooltip distinguishes
    an ad that only NAME-DROPS experience from one that never mentions it, and
    says so when the cell shows a level that came from the job title rather
    than from the description.
    """
    raw = str(row.get("experience_required") or "").strip()
    if raw:
        if raw.lower() == "mentioned":
            return _("The ad mentions experience without stating a figure or "
                     "a level.")
        if raw.lower() not in _EXP_ABSENT:
            src = str(row.get("experience_source") or "").strip()
            return "%s (%s)" % (raw, src) if src and src != "none" else raw
    level = str(row.get("experience_level") or "").strip()
    if level and level.lower() not in _EXP_ABSENT:
        # Cell shows a level (board field / title inference): the old tooltip
        # claimed "no requirement found" next to a non-empty cell.
        return _("Seniority level \"{level}\" — the ad states no number of "
                 "years.").format(level=level)
    return _("No experience requirement found in the job description")



# Canonical level -> rank, so level-only rows still sort by seniority.
_EXP_LEVEL_RANK = {"intern": 0, "internship": 0, "entry": 1, "junior": 1,
                   "mid": 2, "senior": 3, "lead": 4, "exec": 5,
                   "executive": 5}


def _experience_sort_key(required: str, level: str, min_years):
    """Numeric-first sort: stated years, then level, then the single '?' value.

    Without this the table would sort the new column as text, so '11' would
    come before '3' and '?' would land between the numbers and the levels.
    """
    req = (required or "").strip()
    years = None
    if req and req.lower() not in _EXP_ABSENT:
        m = _EXP_SPEC_RX.match(req)
        if m:
            try:
                years = float(m.group("lo").replace(",", "."))
                if _EXP_MONTH_FULL_RX.fullmatch(m.group("unit")):
                    years /= 12.0  # months sort below any year figure
            except ValueError:
                years = None
    if years is None:
        try:
            years = float(min_years) if min_years not in (None, "") else None
        except (TypeError, ValueError):
            years = None
    rank = _EXP_LEVEL_RANK.get((level or "").strip().lower(), 9)
    if years is not None:
        return (0, years, rank)
    if rank != 9:
        return (1, 0.0, rank)
    # The single "?" tier: a bare mention and "never mentioned" are the same
    # statement about the ad, so they must not be split by the sort either.
    return (2, 0.0, 9)


class _CellItem(QTableWidgetItem):
    """Table cell that can sort on a hidden key instead of its display text."""

    def __init__(self, text, sort_key=None):
        super().__init__(text)
        self.sort_key = sort_key

    def __lt__(self, other):
        mine = getattr(self, "sort_key", None)
        theirs = getattr(other, "sort_key", None)
        if mine is not None and theirs is not None:
            return mine < theirs
        # NOTE: never call super().__lt__() here. PySide6's base implementation
        # re-enters this Python override while comparing the display text, which
        # overflows the C++ stack and crashes the process (0xC0000005) as soon
        # as a column that has no sort_key is sorted. Plain text comparison is
        # exactly what the base class does for the string cells we build
        # (see QAbstractItemModelPrivate::variantLessThan), so behaviour is
        # unchanged for every other column.
        return self.text() < other.text()


def _verdict_cell(value: str) -> str:
    v = (value or "").strip().lower()
    if v == "y":
        return "Y"
    if v == "n":
        return "N"
    return "?"


def _row_values(row) -> list:
    """The ten display strings of one result row, in HEADERS order.

    Single source of truth: _fill_rows() writes these into the table and the
    column-width fit (_content_widths) measures THE SAME strings, so a column
    can never be sized from something other than what it shows.
    """
    return [
        row["title"],
        row["company"],
        row["country"],
        row["location"],
        row["_exp_display"],                        # rendered once in run_search
        _verdict_cell(row["visa_sponsorship"]),
        _verdict_cell(row["eu_blue_card_verdict"]),
        _verdict_cell(row["relocation_support"]),
        row["remote_type"],
        (row["first_seen_at"] or "")[:10],
    ]


class _SearchHeader(QHeaderView):
    """Table header that keeps every column draggable and content-sized.

    Two Qt defaults fight the Search tab: a Stretch section cannot be dragged
    at all, and a double-click resizes a section to ``sectionSizeHint()``,
    which for an item view is a character-count estimate of the HEADER LABEL
    ("Company" -> 88 px). Double-clicking a column that shows "Amazon Italia"
    would therefore shrink it back to a clipped 88 px, i.e. the handler would
    undo the very fix the user asked for. This subclass turns the double-click
    into the tab's own measurement instead ("fit THIS column to its content").
    """

    fit_requested = Signal(int)  # logical section index

    def mouseDoubleClickEvent(self, event):
        col = self.logicalIndexAt(event.position().toPoint())
        if col >= 0:
            self.fit_requested.emit(col)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)



class SearchTab(QWidget):
    application_saved = Signal(str)  # job url

    def __init__(self, db_path: str, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        # Pagination state: run_search() caches the full filtered result list
        # here and the table renders ONE page of it (bounded widget count —
        # see _render_page).
        self._filtered_rows = []
        self._page = 0
        # Column-width state (see the "Column width policy" block above):
        #   _fitted_widths  widest content measured for each column
        #   _user_widths    widths the USER dragged - never auto-fitted again
        #   _granted_slack  Title's share of the leftover viewport width
        #   _fitting        True only inside our own resizeSection() calls, so
        #                   a programmatic fit is never mistaken for a drag
        #   *_widths caches memoise QFontMetrics.horizontalAdvance(): it costs
        #                  ~30 us per call through the bindings, and a page has
        #                  ~5,000 cells, so measuring each DISTINCT string once
        #                  is what makes content fitting affordable at all.
        self._fitted_widths = {}
        self._user_widths = {}
        self._granted_slack = 0
        self._fitting = False
        self._value_widths = {}
        self._label_widths = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── Filter row (same fields as the original UI) ──────────────────────
        filters = QHBoxLayout()
        filters.setSpacing(6)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText(_("Job title"))
        self.company_edit = QLineEdit()
        self.company_edit.setPlaceholderText(_("Company"))
        self.location_edit = QLineEdit()
        self.location_edit.setPlaceholderText(_("Location"))
        self.country_combo = QComboBox()
        self.remote_combo = QComboBox()
        self.experience_combo = QComboBox()
        self.experience_combo.setToolTip(_("Experience:"))
        # Descriptive labels so the user can tell the three dropdowns apart.
        self.country_label = QLabel(_("Country:"))
        self.remote_label = QLabel(_("Remote:"))
        self.experience_label = QLabel(_("Experience:"))
        self.sponsor_check = QCheckBox(_("Sponsor"))
        self.bluecard_check = QCheckBox(_("Blue Card"))
        self.reloc_check = QCheckBox(_("Reloc"))
        self.regex_check = QCheckBox(_("Regex"))
        self.regex_check.setToolTip(
            _("Enable regular-expression matching in Title / Company / Location "
              "filters (e.g. ^senior (backend|platform)$)."))
        self.search_btn = QPushButton(_("Search"))
        self.search_btn.setObjectName("Primary")
        self.clear_btn = QPushButton(_("Clear"))

        for widget in (self.title_edit, self.company_edit, self.location_edit):
            filters.addWidget(widget)
        filters.addWidget(self.country_label)
        filters.addWidget(self.country_combo)
        filters.addWidget(self.remote_label)
        filters.addWidget(self.remote_combo)
        filters.addWidget(self.experience_label)
        filters.addWidget(self.experience_combo)
        filters.addWidget(self.sponsor_check)
        filters.addWidget(self.bluecard_check)
        filters.addWidget(self.reloc_check)
        filters.addWidget(self.regex_check)
        filters.addWidget(self.search_btn)
        filters.addWidget(self.clear_btn)
        root.addLayout(filters)

        # ── Pager row ──────────────────────────────────────────────────────────
        # The table renders ONE page of the (possibly huge) result set, so
        # switching pages is instant and every row stays reachable via ◀ / ▶
        # (the count label shows where you are: "Showing 1–500 of 4,171").
        pager = QHBoxLayout()
        pager.setSpacing(6)
        self.page_info = QLabel("")
        self.page_prev_btn = QPushButton("◀")
        self.page_prev_btn.setToolTip(_("Previous page"))
        self.page_prev_btn.clicked.connect(self._prev_page)
        self.page_next_btn = QPushButton("▶")
        self.page_next_btn.setToolTip(_("Next page"))
        self.page_next_btn.clicked.connect(self._next_page)
        self.page_size_combo = QComboBox()
        self.page_size_combo.addItems(["100", "200", "500", "1000"])
        self.page_size_combo.setCurrentText("500")
        self.page_size_combo.setToolTip(_("Rows per page"))
        # Page size is a fixed choice, not free text. Every selection must cause
        # exactly ONE table rebuild, after the widget has settled.
        # run_search(), prev/next and closeEvent all stop the pending timer so
        # a delayed page-size change can never repaint a newer result set.
        self._page_size_timer = QTimer(self)
        self._page_size_timer.setSingleShot(True)
        # A short, explicit delay (not 0 ms) keeps rapid selection changes
        # from turning into consecutive full-page rebuilds.
        self._page_size_timer.setInterval(30)
        self._page_size_timer.timeout.connect(
            lambda: self._render_page(grow_only=True))
        self.page_size_combo.currentIndexChanged.connect(
            self._on_page_size_changed)
        self.page_prev_btn.setEnabled(False)
        self.page_next_btn.setEnabled(False)
        pager.addWidget(self.page_info)
        pager.addStretch(1)
        pager.addWidget(self.page_size_combo)
        pager.addWidget(self.page_prev_btn)
        pager.addWidget(self.page_next_btn)
        root.addLayout(pager)

        # ── Results table ────────────────────────────────────────────────────
        # The visible sort is applied to the ROW DATA in Python (_ordered_rows),
        # not by Qt. QTableWidgetItem sorting routes every comparison through a
        # Python __lt__ override (slow on large pages, and the code path that
        # crashed the process on PySide6), so Qt sorting stays OFF.
        self._user_sort_section = -1
        self._user_sort_order = Qt.AscendingOrder
        self.table = QTableWidget(0, len(HEADERS))
        # Install the content-aware header BEFORE anything is wired to it (the
        # old header is deleted by setHorizontalHeader): its double-click means
        # "fit this column to its content", and no column is Stretch any more,
        # so every divider is draggable — including Title, which used to be
        # frozen wide enough only for its own HEADER LABEL.
        self.table.setHorizontalHeader(_SearchHeader(Qt.Horizontal, self.table))
        self.table.setHorizontalHeaderLabels(_header_labels())
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        # QFontMetrics are built from the widget fonts once: the cells are
        # regular, the header labels are BOLD (QSS "QHeaderView::section"),
        # which the widget's own font object does not carry - measuring a
        # label with the regular font would leave "Blue Card" clipped.
        self._value_metrics = QFontMetrics(self.table.font())
        header_font = QFont(self.table.font())
        header_font.setBold(True)
        self._label_metrics = QFontMetrics(header_font)
        # Qt paints a sort arrow on column 0 (descending) by default even with
        # sorting disabled, which would claim "sorted by Title" while the rows
        # are actually in the SQL "best match" order. Clear it: a column shows
        # an arrow only after the user clicks it.
        header = self.table.horizontalHeader()
        header.setSortIndicator(-1, Qt.AscendingOrder)
        # NOT ResizeToContents: that mode re-measures every cell whenever an
        # item changes, so one page render triggered thousands of layout passes
        # (hundreds of ms). Interactive + one content fit per render instead.
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.sectionResized.connect(self._on_section_resized)
        header.fit_requested.connect(self._fit_column)
        # The VIEWPORT resize is the exact moment the leftover width changes
        # (window resize, vertical scrollbar appearing) — re-share the slack
        # there instead of re-measuring, which keeps it cheap.
        self.table.viewport().installEventFilter(self)
        # sectionClicked still fires with Qt sorting off, so header clicks work
        # as the user's sort intent and the sort indicator still updates.
        header.sectionClicked.connect(self._on_sort_section_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.doubleClicked.connect(self._open_selected)
        root.addWidget(self.table, stretch=1)

        # wiring
        self.search_btn.clicked.connect(self.run_search)
        self.clear_btn.clicked.connect(self.clear_filters)
        self.title_edit.returnPressed.connect(self.run_search)
        self.company_edit.returnPressed.connect(self.run_search)
        self.location_edit.returnPressed.connect(self.run_search)

    # ── Data ─────────────────────────────────────────────────────────────────
    def populate_static_filters(self):
        """(Re)fill the Country / Remote / Experience dropdowns from the DB.

        Each dropdown always holds the FULL unique value list of the data —
        it is never narrowed by the current selection — so switching from
        one value to another is always one click + Search (no All detour).

        Experience options are the rendered Experience-column strings
        (_experience_cell over the DB distinct specs), ordered like the
        column sorts: numeric requirements, then levels, then NA.  The
        FILTER_ALL sentinel is added untranslated in every language.

        Refresh points: app._refresh_all() calls this before run_search()
        on startup, on Refresh and after every scan finishes.
        """
        exp_rank = {}
        for spec in db.get_distinct_experience_specs(self.db_path):
            req, level, min_years = spec
            disp = _experience_cell(req or "", level or "", min_years)
            key = _experience_sort_key(req or "", level or "", min_years)
            if disp not in exp_rank or key < exp_rank[disp]:
                exp_rank[disp] = key
        experience = sorted(exp_rank, key=lambda d: (exp_rank[d], d))
        for combo, values in (
            (self.country_combo, db.get_distinct_job_countries(self.db_path)),
            (self.remote_combo, db.get_distinct_remote_types(self.db_path)),
            (self.experience_combo, experience),
        ):
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(FILTER_ALL)
            combo.addItems(values)
            if current:
                idx = combo.findText(current)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def run_search(self):
        # A pending page-size selection must not repaint the NEW search result
        # with the OLD page size when the timer fires.
        self._page_size_timer.stop()
        # Validate regex patterns up front; fall back to substring search and
        # inform the user on an invalid pattern instead of returning nothing.
        use_regex = self.regex_check.isChecked()
        if use_regex:
            import re as _re
            for pat in (self.title_edit.text(), self.company_edit.text(),
                        self.location_edit.text()):
                if pat.strip():
                    try:
                        _re.compile(pat)
                    except _re.error as exc:
                        QMessageBox.warning(
                            self, _("Invalid regular expression"),
                            _("Regex disabled — invalid pattern:\n{error}")
                            .format(error=str(exc)))
                        use_regex = False
                        break
        rows = db.search_jobs(
            self.db_path,
            title=self.title_edit.text().strip(),
            company=self.company_edit.text().strip(),
            location=self.location_edit.text().strip(),
            country=self.country_combo.currentText(),
            remote_filter=self.remote_combo.currentText(),
            # Combo item 0 is the absolute FILTER_ALL literal ("All"), never
            # translated, so SQL always receives the English sentinel it
            # understands — Italian "Tutti" can never leak into the query.
            # No experience_filter on purpose: the dropdown holds the RENDERED
            # column values (see the _exp_display block below), which the SQL
            # filter on raw experience_level cannot express — the Experience
            # filter is applied in Python after rendering each row.
            sponsorship_only=self.sponsor_check.isChecked(),
            eu_blue_card_only=self.bluecard_check.isChecked(),
            relocation_only=self.reloc_check.isChecked(),
            regex=use_regex,
        )
        # db.search_jobs() returns sqlite3.Row objects (row_factory in
        # db/database.py), which have no .get(). Convert to plain dicts so
        # the row.get(...) calls below (Experience cell, sort key, tooltip)
        # work; row["col"] indexing keeps working on dicts.
        rows = [dict(r) for r in rows]
        # Render the Experience cell once per row and keep it on the row: the
        # table cell, the dropdown options (populate_static_filters renders
        # _experience_cell over the DB distinct specs) and the filter below
        # all use the same canonical, untranslated strings — display, list
        # and filter can never drift apart, in any UI language.
        for row in rows:
            row["_exp_display"] = _experience_cell(
                row.get("experience_required", ""),
                row["experience_level"],
                row["experience_min_years"])
        exp_selected = self.experience_combo.currentText()
        if exp_selected and exp_selected != FILTER_ALL:
            rows = [r for r in rows if r["_exp_display"] == exp_selected]
        # Cache the full filtered list and render only the CURRENT page of it:
        # the table never holds more than one page of QTableWidgetItem objects
        # (~5k cells) — that bounded widget count is what keeps Search instant
        # on big datasets; ◀/▶ re-render from this cache without re-querying.
        self._filtered_rows = rows
        self._page = 0
        # A new result set invalidates the measured widths (different jobs,
        # different text), so the columns are measured again — once per search,
        # not once per page. Widths the user dragged by hand are a layout
        # preference and survive the search.
        self._fitted_widths.clear()
        self._render_page()

    # ── Pagination ──────────────────────────────────────────────────────────────
    def _page_size(self) -> int:
        """Rows per page, read from the fixed page-size choices."""
        return int(self.page_size_combo.currentText())

    def _render_page(self, grow_only=False):
        """Fill the table with ONE page of the cached filtered result list.

        Row ORDER is decided by ``_ordered_rows()`` (Python, on plain dicts), so
        the cells of row N always belong to the Nth visible job. Qt's own
        sorting is disabled: it would move QTableWidgetItem objects between rows
        and route every comparison through the Python ``_CellItem.__lt__``
        callback (slow, and the path that can overflow the C++ stack).

        Cells are reused in place and only their content is refreshed; creating
        ~10,000 new items and re-measuring every cell for ResizeToContents took
        0.3-0.5 s per page change on a 2-core machine — the freeze the user saw
        when switching the rows-per-page value.

        ``grow_only`` is set ONLY by a rows-per-page change on the first page,
        where the rows already on screen are provably the same rows (the result
        set did not change). It must never be used after a new search: a new
        result set can put a completely different job in row 0, and the
        already-filled cells would then show the PREVIOUS search's rows.
        """
        page_rows = self._page_rows()
        previous_count = self.table.rowCount()
        selected_url = self._selected_url()

        # Fast path: the FIRST page only GREW (e.g. 500 -> 1000 rows) and the
        # result set is unchanged, so only the appended rows need cells.
        if (grow_only and self._page == 0 and len(page_rows) > previous_count
                and self._user_sort_section < 0):
            self.table.setUpdatesEnabled(False)
            self.table.setRowCount(len(page_rows))
            self._fill_rows(page_rows, previous_count)
            self._fit_columns(page_rows)
            self.table.setUpdatesEnabled(True)
            self._refresh_pager_label()
            return

        # Everything else refills EVERY visible row, so no cell can ever show a
        # job from a previous search / page.
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(page_rows))
        # Cells are reused, so a stale row index would point at a DIFFERENT job
        # (double-click / Save-to-Applications would act on the wrong row).
        # Drop the selection and re-select by URL only if it is still on screen.
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self._fill_rows(page_rows, 0)
        self._fit_columns(page_rows)
        self._restore_selection(selected_url)
        self.table.setUpdatesEnabled(True)
        self._refresh_pager_label()

    def _refresh_pager_label(self):
        """Update the 'Showing X–Y of N results' label + prev/next state.

        Cheap (pure arithmetic over the cached list) so language switches and
        page-size edits never have to rebuild the table.
        """
        total = len(self._filtered_rows)
        size = self._page_size()
        pages = max(1, -(-total // size))  # ceil-div
        self._page = max(0, min(self._page, pages - 1))
        start = self._page * size
        # Count the rows of the ORDERED list (same source _page_rows uses), so
        # a user sort can never disagree with the label.
        count = max(0, min(len(self._ordered_rows()) - start, size))
        self.page_info.setText(
            _("Showing {a}–{b} of {n} results").format(
                a=(start + 1) if count else 0,
                b=start + count,
                n=total))
        self.page_prev_btn.setEnabled(self._page > 0)
        self.page_next_btn.setEnabled(self._page < pages - 1)

    def _on_sort_section_clicked(self, section):
        """Remember the user's sort choice so it survives page changes.

        Qt updates the header's sort indicator BEFORE emitting sectionClicked
        (verified with a real header click), so the indicator can be read here
        directly. The order toggles on every click of the same column.
        """
        header = self.table.horizontalHeader()
        self._user_sort_section = section
        self._user_sort_order = header.sortIndicatorOrder()
        self._page = 0
        self._render_page()

    def _row_sort_key(self, section, row):
        """Python sort key mirroring the column display values."""
        if section == 0:
            return (row.get("title") or "").casefold()
        if section == 1:
            return (row.get("company") or "").casefold()
        if section == 2:
            return (row.get("country") or "").casefold()
        if section == 3:
            return (row.get("location") or "").casefold()
        if section == 4:
            return _experience_sort_key(
                row.get("experience_required", ""),
                row.get("experience_level") or "",
                row.get("experience_min_years"))
        if section in (5, 6, 7):
            # Tri-state columns: Y before N before ? (see _verdict_cell).
            return {"Y": 0, "N": 1, "?": 2}.get(
                _verdict_cell(row.get(
                    ("visa_sponsorship", "eu_blue_card_verdict",
                     "relocation_support")[section - 5])), 3)
        if section == 8:
            return (row.get("remote_type") or "").casefold()
        return (row.get("first_seen_at") or "")[:10]

    def _ordered_rows(self):
        """The cached result rows, ordered for the CURRENT page/user sort.

        Sorting the DATA (not the QTableWidgetItems) is what keeps the table
        deterministic: Qt's sortItems() physically moves item objects between
        rows, so a later in-place refresh could address the wrong row — and
        every Qt sort goes through the Python ``_CellItem.__lt__`` callback,
        the path that can overflow the C++ stack on PySide6. Python sorts run
        on plain dicts and are ~100x cheaper.
        """
        section = self._user_sort_section
        if section < 0:
            return self._filtered_rows
        # The key itself is precomputed per row: row.get() per comparison would
        # be O(n log n) dict lookups, and the Experience key re-parses strings.
        keys = [self._row_sort_key(section, row) for row in self._filtered_rows]
        order = sorted(range(len(self._filtered_rows)), key=keys.__getitem__)
        if self._user_sort_order == Qt.DescendingOrder:
            order.reverse()
        return [self._filtered_rows[i] for i in order]

    def _page_rows(self) -> list:
        """The rows shown on the current page (slice of the ordered results)."""
        size = self._page_size()
        start = self._page * size
        return self._ordered_rows()[start:start + size]

    def _fill_rows(self, page_rows, start):
        """Create/update the cells of ``page_rows`` from row index ``start``.

        Each cell is only written when its content actually differs. PySide6
        charges ~20 us per setText/setData call, so blindly rewriting 5,000
        cells cost ~110 ms per page change; skipping the no-op writes makes a
        page-size change that keeps the same rows essentially free.
        """
        for r in range(start, len(page_rows)):
            row = page_rows[r]
            values = _row_values(row)
            url = row["url"]
            for col, val in enumerate(values):
                text = str(val if val is not None else "")
                item = self.table.item(r, col)
                if item is None:
                    item = _CellItem(text)
                    item.setTextAlignment(Qt.AlignCenter if col in (4, 5, 6, 7)
                                         else Qt.AlignLeft | Qt.AlignVCenter)
                    self.table.setItem(r, col, item)
                    item.setData(URL_ROLE, url)
                else:
                    # Mutating an item notifies the view and costs a layout
                    # pass, so only write what actually changed.
                    if item.text() != text:
                        item.setText(text)
                    if item.data(URL_ROLE) != url:
                        item.setData(URL_ROLE, url)
                if col == 4:
                    # Numeric sort + hover detail for the Experience column.
                    item.sort_key = _experience_sort_key(
                        row.get("experience_required", ""),
                        row["experience_level"],
                        row["experience_min_years"])
                    tooltip = _experience_tooltip(row)
                    if item.toolTip() != tooltip:
                        item.setToolTip(tooltip)

    # ── Column widths ────────────────────────────────────────────────────────
    def _value_width(self, text: str) -> int:
        """Pixel width of a cell value (table font), memoised per string."""
        width = self._value_widths.get(text)
        if width is None:
            width = self._value_metrics.horizontalAdvance(text)
            self._value_widths[text] = width
        return width

    def _label_width(self, text: str) -> int:
        """Pixel width of a header label (BOLD font), memoised per string."""
        width = self._label_widths.get(text)
        if width is None:
            width = self._label_metrics.horizontalAdvance(text)
            self._label_widths[text] = width
        return width

    def _content_widths(self, page_rows) -> dict:
        """How wide every column must be to show ``page_rows`` uncut.

        The header label is the FLOOR (a section narrower than its own title
        would elide "Blue Card"), the widest cell text the requirement. The
        values come from ``_row_values()`` — the exact strings the cells show —
        and every distinct string is measured at most once per session.
        """
        labels = _header_labels()
        widths = {col: self._label_width(labels[col]) + _COL_PADDING
                  for col in range(len(HEADERS))}
        for row in page_rows:
            for col, value in enumerate(_row_values(row)):
                if col in _FIXED_CELL_COLS or not value:
                    continue  # Y/N/? cells can never beat their own label
                width = self._value_width(str(value)) + _COL_PADDING
                if width > widths[col]:
                    widths[col] = width
        return {col: min(width, _COL_MAX_WIDTH)
                for col, width in widths.items()}

    def _fit_columns(self, page_rows):
        """Content-fit every column the user has NOT sized by hand.

        Runs once per page render, but a column never shrinks inside one result
        set: paging to a page with shorter text must not make the table jump
        around under the user's eyes.
        """
        for col, width in self._content_widths(page_rows).items():
            if width > self._fitted_widths.get(col, 0):
                self._fitted_widths[col] = width
        self._apply_widths()
        self._update_clipped_tooltips(page_rows)

    def _update_clipped_tooltips(self, page_rows):
        """Full text as a tooltip wherever a column cannot show all of it.

        The fit caps a column at _COL_MAX_WIDTH (one 1,400 px junk title must
        not push every other column off the window) and the user is free to
        squeeze a column further, so a value can still be cut. Nothing is lost:
        such a cell carries its full text as a tooltip, which is written only
        when it actually changes — the common path is one width lookup per cell.
        The Experience column is skipped: its tooltip is the evidence line
        built by _experience_tooltip().

        Cells are REUSED across pages, so the other half of the job is dropping
        the tooltip: otherwise row 0 could still advertise the previous page's
        job (the same class of bug as a stale row index).
        """
        header = self.table.horizontalHeader()
        widths = {col: header.sectionSize(col) for col in range(len(HEADERS))}
        for r, row in enumerate(page_rows):
            for col, value in enumerate(_row_values(row)):
                if col == 4:
                    continue
                item = self.table.item(r, col)
                if item is None:
                    continue
                text = str(value) if value else ""
                if text and self._value_width(text) + _COL_PADDING > widths[col]:
                    if item.toolTip() != text:
                        item.setToolTip(text)
                elif item.toolTip():
                    item.setToolTip("")

    def _apply_widths(self):
        """Push the current widths into the header and fill the viewport.

        The leftover viewport width goes to the Title column — that is what the
        old Stretch mode did — but Title stays draggable now ("Title takes the
        slack", its own content width being the base). When the columns already
        need more than the viewport nothing is shrunk: the horizontal scrollbar
        takes over so no column is cut off.
        """
        header = self.table.horizontalHeader()
        self._fitting = True
        try:
            total = 0
            for col in range(len(HEADERS)):
                width = self._user_widths.get(col)
                if width is None:
                    width = max(_COL_MIN_WIDTH,
                                self._fitted_widths.get(col, _COL_MIN_WIDTH))
                self._set_section_width(header, col, width)
                total += width
            self._granted_slack = max(
                0, self.table.viewport().width() - total)
            if self._granted_slack:
                base = self._user_widths.get(0)
                if base is None:
                    base = max(_COL_MIN_WIDTH,
                               self._fitted_widths.get(0, _COL_MIN_WIDTH))
                self._set_section_width(
                    header, 0, base + self._granted_slack)
        finally:
            self._fitting = False

    @staticmethod
    def _set_section_width(header, col, width):
        """resizeSection() only on a real change (it repaints and re-lays out)."""
        if header.sectionSize(col) != width:
            header.resizeSection(col, width)

    def _on_section_resized(self, col, _old, width):
        """Remember a divider the USER dragged — and never auto-fit it again.

        Re-fitting after a manual resize is what made the columns feel frozen:
        the next page render silently threw the user's width away. A drag is a
        decision, so it wins until the user double-clicks the divider.
        """
        if self._fitting:
            return  # our own fit, not a user decision
        # Title carries the shared slack on top of its width; store the width
        # the user actually chose, or every later render would add the slack
        # again on top of it.
        base = width - (self._granted_slack if col == 0 else 0)
        self._user_widths[col] = max(_COL_MIN_WIDTH, base)

    def _fit_column(self, col):
        """Make ONE column as wide as its content again (divider double-click).

        Drops the manual width, re-measures the column on the rows currently on
        screen and re-applies the layout.
        """
        if not 0 <= col < len(HEADERS):
            return
        self._user_widths.pop(col, None)
        self._fitted_widths[col] = self._content_widths(
            self._page_rows())[col]
        self._apply_widths()

    def eventFilter(self, watched, event):
        """Re-share the leftover viewport width when the table viewport resizes.

        Nothing is re-measured here (the content did not change), so a window
        resize or a scrollbar appearing costs no text measurement at all. The
        event is never swallowed.
        """
        if (watched is self.table.viewport()
                and event.type() == QEvent.Type.Resize
                and not self._fitting
                and (self._fitted_widths or self._user_widths)):
            self._apply_widths()
        return super().eventFilter(watched, event)

    def _restore_selection(self, url):
        """Keep the selected job selected after a page rebuild, if still shown."""
        if not url:
            return
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is not None and item.data(URL_ROLE) == url:
                self.table.selectRow(r)
                return

    def _on_page_size_changed(self, _index):
        """Rebuild once after a page-size selection settles.

        Return to the first page: a later page number means something different
        at every page size, so keeping it would silently change which results
        the user is looking at. A page-size change never re-queries and never
        re-filters, so the result set is unchanged — ``grow_only`` is safe.
        """
        self._page = 0
        self._page_size_timer.start()

    def _prev_page(self):
        self._page_size_timer.stop()
        if self._page > 0:
            self._page -= 1
            self._render_page()

    def _next_page(self):
        self._page_size_timer.stop()
        # _render_page clamps against the cached list, so this is safe even
        # when the last page is already showing.
        self._page += 1
        self._render_page()

    def closeEvent(self, event):
        """Never let a pending page-size timer fire while the tab is closing."""
        self._page_size_timer.stop()
        super().closeEvent(event)

    def clear_filters(self):
        for edit in (self.title_edit, self.company_edit, self.location_edit):
            edit.clear()
        self.country_combo.setCurrentIndex(0)
        self.remote_combo.setCurrentIndex(0)
        self.experience_combo.setCurrentIndex(0)
        for check in (self.sponsor_check, self.bluecard_check, self.reloc_check,
                      self.regex_check):
            check.setChecked(False)
        self.run_search()

    # ── Actions ──────────────────────────────────────────────────────────────
    def _selected_url(self):
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(URL_ROLE) if item else None

    def _open_selected(self):
        url = self._selected_url()
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _context_menu(self, pos):
        url = self._selected_url()
        if not url:
            return
        menu = QMenu(self)
        act_open = menu.addAction(_("Open in browser"))
        act_copy = menu.addAction(_("Copy URL"))
        act_save = menu.addAction(_("Save to Applications"))
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is act_open:
            self._open_selected()
        elif chosen is act_copy:
            QApplication.clipboard().setText(url)
        elif chosen is act_save:
            r = self.table.currentRow()
            db.upsert_application(
                self.db_path,
                job_url=url,
                company=self.table.item(r, 1).text(),
                title=self.table.item(r, 0).text(),
                status="saved",
            )
            QMessageBox.information(self, _("SponsorScout"),
                                    _("Job saved to Applications."))
            self.application_saved.emit(url)

    # ── i18n ─────────────────────────────────────────────────────────────────
    def retranslate(self):
        self.search_btn.setText(_("Search"))
        self.clear_btn.setText(_("Clear"))
        self.title_edit.setPlaceholderText(_("Job title"))
        self.company_edit.setPlaceholderText(_("Company"))
        self.location_edit.setPlaceholderText(_("Location"))
        self.sponsor_check.setText(_("Sponsor"))
        self.bluecard_check.setText(_("Blue Card"))
        self.reloc_check.setText(_("Reloc"))
        self.regex_check.setText(_("Regex"))
        self.country_label.setText(_("Country:"))
        self.remote_label.setText(_("Remote:"))
        self.experience_label.setText(_("Experience:"))
        self.experience_combo.setToolTip(_("Experience:"))
        self.regex_check.setToolTip(
            _("Enable regular-expression matching in Title / Company / Location "
              "filters (e.g. ^senior (backend|platform)$)."))
        self.page_prev_btn.setToolTip(_("Previous page"))
        self.page_next_btn.setToolTip(_("Next page"))
        self.page_size_combo.setToolTip(_("Rows per page"))
        self.table.setHorizontalHeaderLabels(_header_labels())
        # Cell tooltips are the only per-row text that depends on the locale.
        # They are updated in place (no rebuild, no re-sort) and ONLY when the
        # Search tab is actually visible — a full _render_page() here used to
        # rebuild every visible cell and run the Python-callback item sort
        # (~150 ms per language switch, even for a hidden tab, which froze the
        # whole window on large datasets). The pager label is derived from the
        # cached list, so it can be refreshed immediately.
        self._refresh_pager_label()
        if self.isVisible():
            self._retranslate_cell_tooltips()

    def _retranslate_cell_tooltips(self):
        """Re-localise the Experience cell tooltips of the current page only.

        Mutates the existing items in place — this must never call
        _render_page(), which would recreate thousands of QTableWidgetItem
        objects and re-sort (the sort callback is Python, so it is slow).
        """
        rows = self._page_rows()
        for r, row in enumerate(rows):
            if r >= self.table.rowCount():
                break
            item = self.table.item(r, 4)
            if item is not None:
                item.setToolTip(_experience_tooltip(row))
