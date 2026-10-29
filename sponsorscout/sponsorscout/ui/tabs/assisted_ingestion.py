"""Dedicated tab for Assisted Ingestion: resolve anti-bot protection and ingest jobs directly."""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sponsorscout.i18n import _

logger = logging.getLogger(__name__)


class AssistedWorker(QThread):
    finished_signal = Signal(int, str)

    def __init__(self, url: str, seed_name: str, db_path: str, max_pages: int = 60, parent=None):
        super().__init__(parent)
        self.url = url
        self.seed_name = seed_name
        self.db_path = db_path
        self.max_pages = max_pages

    def run(self):
        try:
            from sponsorscout.core.blocked_portals import run_assisted_ingestion
            from sponsorscout.db.database import get_connection
            with get_connection(self.db_path) as conn:
                count = run_assisted_ingestion(self.url, self.seed_name, conn, max_pages=self.max_pages, db_path=self.db_path)
            self.finished_signal.emit(count, "")
        except Exception as e:
            logger.exception("Assisted ingestion error")
            self.finished_signal.emit(0, str(e))


class AssistedIngestionTab(QWidget):
    """Dedicated interactive tab for solving anti-bot challenges and direct job harvesting."""

    ingestion_finished = Signal()

    def __init__(self, db_path: str, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self._worker: AssistedWorker | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(12)

        # Header description
        info_box = QGroupBox(_("Assisted Browser Ingestion"))
        info_lay = QVBoxLayout(info_box)
        info_text = QLabel(
            _(
                "Certain portals enforce Cloudflare, PerimeterX, or bot verification (e.g. 'Press & Hold'). "
                "Use this tab to open an interactive Chromium window, complete human verification, and automatically "
                "harvest all vacancy pages directly into SponsorScout's database."
            )
        )
        info_text.setWordWrap(True)
        info_lay.addWidget(info_text)
        lay.addWidget(info_box)

        # Direct Portal Ingestion Controls
        portal_box = QGroupBox(_("Launch Assisted Ingestion for Portal"))
        p_lay = QVBoxLayout(portal_box)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel(_("Company / Seed Name:")))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("e.g. Robert Walters IE"))
        row1.addWidget(self.name_edit)

        row1.addWidget(QLabel(_("Portal Careers URL:")))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(_("https://www.robertwalters.ie/jobs.html"))
        row1.addWidget(self.url_edit, 1)

        p_lay.addLayout(row1)

        # Action Buttons
        btn_row = QHBoxLayout()
        self.launch_btn = QPushButton(_("Launch Assisted Browser and Ingest"))
        self.launch_btn.setObjectName("Primary")
        self.launch_btn.setStyleSheet("font-weight: bold; background-color: #2563EB; color: white; padding: 6px 14px; border-radius: 4px;")
        self.launch_btn.clicked.connect(self._start_ingestion)
        btn_row.addWidget(self.launch_btn)

        self.open_ext_btn = QPushButton(_("Open in External Browser"))
        self.open_ext_btn.clicked.connect(self._open_external)
        btn_row.addWidget(self.open_ext_btn)

        btn_row.addStretch(1)
        p_lay.addLayout(btn_row)

        self.status_lbl = QLabel(_("Ready"))
        self.status_lbl.setStyleSheet("color: #4B5563; font-weight: bold;")
        p_lay.addWidget(self.status_lbl)

        lay.addWidget(portal_box)

        # Known Blocked Portals Quick Launcher
        quick_box = QGroupBox(_("Recently Blocked / Known Bot-Guarded Portals"))
        q_lay = QVBoxLayout(quick_box)

        self.portals_table = QTableWidget(0, 3)
        self.portals_table.setHorizontalHeaderLabels([_("Company"), _("Target URL"), _("Action")])
        # F-23: the row-number gutter read as a stray blank column and Company
        # truncated while the URL stretched. Hide the gutter, fit Company to
        # its content, let the URL take the remaining width.
        self.portals_table.verticalHeader().setVisible(False)
        self.portals_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents)
        self.portals_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        # F-29 (2026-10-22): the Action column kept its default ~100 px width, so
        # the per-row button rendered as "oad and Inge". ResizeToContents would
        # not help - it measures cell TEXT and the action column holds a
        # cellWidget - so size the section from the button's own font metrics.
        _act_txt = _("Load and Ingest")
        _act_hdr = self.portals_table.horizontalHeader()
        _act_hdr.setSectionResizeMode(2, QHeaderView.Interactive)
        _probe = QPushButton(_act_txt)
        _fm = QFontMetrics(_probe.font())
        _act_w = max(_fm.horizontalAdvance(_act_txt) + 40,
                     _fm.horizontalAdvance(_("Action")) + 24, 170)
        _act_hdr.resizeSection(2, _act_w)
        self.portals_table.setColumnWidth(2, _act_w)
        self.portals_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.portals_table.setEditTriggers(QTableWidget.NoEditTriggers)
        q_lay.addWidget(self.portals_table)

        lay.addWidget(quick_box, 1)

        self._populate_known_portals()

    def _populate_known_portals(self):
        known = [
            ("Robert Walters NL", "https://www.robertwalters.nl/en/jobs.html"),
            ("Robert Walters IE", "https://www.robertwalters.ie/jobs.html"),
            ("Robert Walters DE", "https://www.robertwalters.de/en/jobs.html"),
            ("Catawiki Careers", "https://catawiki.careers/vacancies"),
        ]
        self.portals_table.setRowCount(len(known))
        for r, (comp, url) in enumerate(known):
            self.portals_table.setItem(r, 0, QTableWidgetItem(comp))
            self.portals_table.setItem(r, 1, QTableWidgetItem(url))
            btn = QPushButton(_("Load and Ingest"))
            # F-29: never let the button clip its own label, in any language.
            _bfm = QFontMetrics(btn.font())
            btn.setMinimumWidth(_bfm.horizontalAdvance(btn.text()) + 28)
            btn.clicked.connect(lambda _, c=comp, u=url: self._select_portal(c, u))
            self.portals_table.setCellWidget(r, 2, btn)

    def _select_portal(self, comp: str, url: str):
        self.name_edit.setText(comp)
        self.url_edit.setText(url)
        self._start_ingestion()

    def _open_external(self):
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, _("Missing URL"), _("Please specify a portal URL."))
            return
        from sponsorscout.core.blocked_portals import open_in_default_browser
        open_in_default_browser(url)

    def _start_ingestion(self):
        url = self.url_edit.text().strip()
        name = self.name_edit.text().strip() or "Career Portal"
        if not url:
            QMessageBox.warning(self, _("Missing URL"), _("Please provide the portal careers URL."))
            return

        # F-25: no host-name rewrites here. A locale-prefixed path that 404s
        # is recovered generically by blocked_portals.strip_locale_segment().

        self.launch_btn.setEnabled(False)
        self.status_lbl.setText(_("Interactive browser opened. Please complete the human-verification / captcha if prompted in the Chromium window..."))

        self._worker = AssistedWorker(url, name, self.db_path, max_pages=60, parent=self)
        self._worker.finished_signal.connect(self._on_ingestion_done)
        self._worker.start()

    def _on_ingestion_done(self, count: int, err: str):
        self.launch_btn.setEnabled(True)
        if err:
            self.status_lbl.setText(_("Failed: ") + err)
            QMessageBox.warning(self, _("Ingestion Error"), err)
            return
        # F-22: report honestly. A harvest that stopped early must say so,
        # with the pages actually read and why the pagination loop gave up —
        # a 10-of-169 harvest must never read as a clean success again.
        # F-36: and a re-run must distinguish new rows from refreshed ones.
        from sponsorscout.core import blocked_portals
        report = dict(getattr(blocked_portals, "LAST_INGESTION_REPORT", {}) or {})
        pages = report.get("pages_visited", 0)
        reason = report.get("stop_reason", "")
        dups = report.get("duplicates", 0)
        if count == 0:
            if dups:
                msg = _("Ingestion completed: %d jobs were harvested but every one of them was already in the database, so 0 new rows were added. The existing rows were refreshed.") % dups
                self.status_lbl.setText(msg)
                QMessageBox.information(self, _("Ingestion Complete"), msg)
            else:
                msg = _("Ingestion completed, but 0 jobs were harvested. Please ensure the human verification was completed and job listings were visible before the browser window closed.")
                self.status_lbl.setText(_("Harvested 0 jobs. Verification may not have been completed."))
                QMessageBox.information(self, _("Ingestion Incomplete"), msg)
            return
        msg = _("Successfully harvested and ingested %d jobs directly into the database!") % count
        if dups:
            msg += "\n" + _("%d of the harvested jobs were already in the database - they were refreshed, not duplicated.") % dups
        if reason:
            msg += "\n" + _("Pages read: %d. Pagination stopped because: %s") % (pages, reason)
        self.status_lbl.setText(msg)
        QMessageBox.information(self, _("Ingestion Complete"), msg)
        self.ingestion_finished.emit()
