"""Dashboard tab: stat cards, top sponsoring companies, jobs by country."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from sponsorscout.db import database as db
from sponsorscout.i18n import _


def _stat_card(title_key: str):
    """Return (card_frame, value_label) for one KPI card."""
    card = QFrame()
    card.setObjectName("Card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(14, 10, 14, 10)
    lay.setSpacing(2)
    title = QLabel(_(title_key))
    title.setObjectName("CardTitle")
    title.setAlignment(Qt.AlignCenter)
    value = QLabel("--")
    value.setObjectName("CardValue")
    value.setAlignment(Qt.AlignCenter)
    lay.addWidget(title)
    lay.addWidget(value)
    return card, value


def _make_table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setWordWrap(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.Stretch)
    for col in range(1, len(headers)):
        header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
    return table


class DashboardTab(QWidget):
    """Mirrors the original Dashboard tab: 6 stat cards + 2 tables."""

    rescan_requested = Signal()
    refresh_requested = Signal()

    CARD_KEYS = ("Total Companies", "Verified Jobs", "Sponsored Jobs",
                 "Remote Jobs", "EU Blue Card", "Applications")

    def __init__(self, db_path: str, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Header row: title + action buttons
        top = QHBoxLayout()
        self.title = QLabel(_("Dashboard"))
        self.title.setObjectName("SectionHeader")
        top.addWidget(self.title)
        top.addStretch(1)
        self.rescan_btn = QPushButton(_("Rescan Companies"))
        self.rescan_btn.setObjectName("Primary")
        self.rescan_btn.clicked.connect(self.rescan_requested)
        top.addWidget(self.rescan_btn)
        self.refresh_btn = QPushButton(_("Refresh"))
        self.refresh_btn.clicked.connect(self.refresh_requested)
        top.addWidget(self.refresh_btn)
        root.addLayout(top)

        # Stat cards
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        self._value_labels: dict[str, QLabel] = {}
        for key in self.CARD_KEYS:
            card, value = _stat_card(key)
            self._value_labels[key] = value
            cards_row.addWidget(card, stretch=1)
        root.addLayout(cards_row)

        # Two tables side by side
        tables_row = QHBoxLayout()
        tables_row.setSpacing(10)

        companies_col = QVBoxLayout()
        self.companies_title = QLabel(_("Top Companies by Sponsorship"))
        self.companies_title.setObjectName("SectionHeader")
        companies_col.addWidget(self.companies_title)
        self.companies_table = _make_table(
            [_("Company"), _("Country"), _("Jobs"), _("Top Sponsor")])
        companies_col.addWidget(self.companies_table)

        country_col = QVBoxLayout()
        self.country_title = QLabel(_("Jobs by Country"))
        self.country_title.setObjectName("SectionHeader")
        country_col.addWidget(self.country_title)
        self.country_table = _make_table([_("Country"), _("Jobs")])
        country_col.addWidget(self.country_table)

        tables_row.addLayout(companies_col, stretch=3)
        tables_row.addLayout(country_col, stretch=2)
        root.addLayout(tables_row, stretch=1)

    # ── Data ─────────────────────────────────────────────────────────────────
    def refresh(self):
        # Suspended updates: both table rebuilds repaint once at the end
        # instead of once per inserted row (this runs on every scan tick).
        self.setUpdatesEnabled(False)
        conn = db.get_connection(self.db_path)
        try:
            stats = db.get_dashboard_stats(self.db_path, _conn=conn)
            mapping = {
                "Total Companies": stats.get("companies", 0),
                "Verified Jobs": stats.get("verified_jobs", 0),
                "Sponsored Jobs": stats.get("sponsored_jobs", 0),
                "Remote Jobs": stats.get("remote_jobs", 0),
                "EU Blue Card": stats.get("eu_blue_card_jobs", 0),
                "Applications": stats.get("applications", 0),
            }
            for key, value in mapping.items():
                self._value_labels[key].setText(str(value))

            rows = db.get_dashboard_top_companies(self.db_path, limit=10,
                                                  _conn=conn)
            self.companies_table.setRowCount(0)
            for company, country, job_count, max_sponsor, _max_match in rows:
                r = self.companies_table.rowCount()
                self.companies_table.insertRow(r)
                display_country = (country or "").strip() or _("Unknown")
                for col, val in enumerate(
                        (company, display_country, job_count, max_sponsor)):
                    item = QTableWidgetItem(str(val if val is not None else ""))
                    if col >= 2:
                        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.companies_table.setItem(r, col, item)

            rows = db.get_dashboard_country_counts(self.db_path, _conn=conn)
            self.country_table.setRowCount(0)
            for country, count in rows:
                r = self.country_table.rowCount()
                self.country_table.insertRow(r)
                self.country_table.setItem(r, 0, QTableWidgetItem(str(country)))
                item = QTableWidgetItem(str(count))
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.country_table.setItem(r, 1, item)
        finally:
            conn.close()
            self.setUpdatesEnabled(True)

    # ── i18n ─────────────────────────────────────────────────────────────────
    def retranslate(self):
        self.title.setText(_("Dashboard"))
        self.rescan_btn.setText(_("Rescan Companies"))
        self.refresh_btn.setText(_("Refresh"))
        self.companies_title.setText(_("Top Companies by Sponsorship"))
        self.country_title.setText(_("Jobs by Country"))
        for key in self.CARD_KEYS:
            card = self._value_labels[key].parentWidget()
            card.findChild(QLabel, "CardTitle").setText(_(key))
        self.companies_table.setHorizontalHeaderLabels(
            [_("Company"), _("Country"), _("Jobs"), _("Top Sponsor")])
        self.country_table.setHorizontalHeaderLabels([_("Country"), _("Jobs")])
