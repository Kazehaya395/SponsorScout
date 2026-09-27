"""Tools tab: scanner control, scan history, data quality, freshness checks.

Ported from the tkinter Tools tab (scan control + streaming log, dedup,
stale-data cleanup, freshness verification) with the addition of the
per-run scan history view backed by the scan_runs / scan_log tables.
"""

import threading

from PySide6.QtCore import QStandardPaths, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton,
    QProgressBar, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from sponsorscout import paths
from sponsorscout.application import seed_manager
from sponsorscout.application.scan_coordinator import ScanCoordinator
from sponsorscout.core.dedup import dedup_companies_in_db, dedup_jobs_in_db
from sponsorscout.db import database as db
from sponsorscout.i18n import _

# Canonical column order (defines the indexes); only the visible labels are
# translated — see _header_labels().
HEADERS_RUNS = ["Run ID", "Method", "Started", "Status", "Jobs", "Dups",
                "Quarantined", "Errors"]


def _runs_header_labels() -> list:
    """HEADERS_RUNS translated for display, in the same (fixed) order."""
    return [_(h) for h in HEADERS_RUNS]
HEADERS_LOG = ["Seed", "Company", "Source", "Target Country", "Status",
               "Provider", "Jobs", "Quar.", "Dups", "Scope Rej.",
               "Error", "Diagnostics", "Duration (s)", "Seed URL"]

# Single scan mode.  The app exposes exactly one scan action: the thorough
# campaign (ATS APIs + career crawls + per-job detail-page enrichment) so
# every job row is extracted with full detail and accurate verdicts.
# ``"quick"`` is still supported by the pipeline for the dev CLI only.
SCAN_METHOD = "full"

# Long scan-control strings live in ONE place so __init__ and retranslate()
# cannot drift: hover tooltips were previously set only at construction and
# stayed in the old language after a switch (e.g. an Italian tooltip on an
# English UI). Each constant is also the exact i18n key.
SCAN_BTN_TOOLTIP = (
    "Scan every seeded company (ATS boards + career pages) and "
    "enrich each job from its detail page, so no listing misses its evidence.")
CUSTOM_BTN_TOOLTIP = (
    "Choose specific companies and/or source types (ATS and/or "
    "career portals) to scan instead of every seeded company.")
RESUME_BTN_TOOLTIP = (
    "Continue the last stopped scan — only companies it did not finish "
    "are scanned, so no progress is lost.")
PAUSE_BTN_TOOLTIP = (
    "Pause the running scan in place — workers stop at the next company "
    "and browsers wait. Press Resume to continue instantly, no new scan "
    "is started.")
INMEMORY_RESUME_TOOLTIP = (
    "Continue the paused scan where it stopped — same run, no loss.")
STOP_BTN_TOOLTIP = (
    "Stop the scan now and keep everything found so far. The stopped run "
    "is checkpointed — Resume (the other button) starts a new scan for "
    "the companies that were not finished, even after an app restart.")


class ScanLogDialog(QDialog):
    """Per-company outcomes of one scan run (from the scan_log table)."""

    def __init__(self, db_path: str, run_id: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Scan log — {run_id}")
        self.resize(1280, 620)
        lay = QVBoxLayout(self)
        table = QTableWidget(0, len(HEADERS_LOG))
        table.setHorizontalHeaderLabels(HEADERS_LOG)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        tooltip_cols = {10, 11}  # Error / Diagnostics are long free-text cols
        for row in db.get_scan_log(db_path, run_id):
            r = table.rowCount()
            table.insertRow(r)
            for col, val in enumerate(row):
                text = str(val or "")
                item = QTableWidgetItem(text)
                if col in tooltip_cols and text:
                    item.setToolTip(text)
                table.setItem(r, col, item)
        table.resizeColumnsToContents()
        # Ensure very wide diagnostic columns don't dominate the window.
        extra = {2, 3, 13}  # Source / Target Country / Seed URL
        for col in list(range(len(HEADERS_LOG))):
            if header.sectionSize(col) > 420:
                header.resizeSection(col, 420)
            elif col not in extra and header.sectionSize(col) < 90:
                header.resizeSection(col, 90)
        lay.addWidget(table)


class QuarantineDialog(QDialog):
    """Browse run quarantine CSVs and promote legit rows into jobs (G4a).

    Quarantined rows are never auto-ingested; this dialog is the manual
    review path: filter by quarantine reason, inspect, and promote the
    rows that are real jobs. Promotion reuses the pipeline's _row_to_job
    mapping so promoted rows are identical to accepted ones.
    """

    HEADERS = ["Company", "Title", "Location", "Reason", "URL"]

    def __init__(self, db_path: str, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.setWindowTitle(_("Quarantine Review"))
        self.resize(1280, 620)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.file_combo = QComboBox()
        self.reason_combo = QComboBox()
        self.reason_combo.currentIndexChanged.connect(self._refilter)
        top.addWidget(QLabel(_("Artifact:")))
        top.addWidget(self.file_combo, stretch=1)
        top.addWidget(QLabel(_("Reason:")))
        top.addWidget(self.reason_combo)
        load_btn = QPushButton(_("Load"))
        load_btn.clicked.connect(self._load_file)
        top.addWidget(load_btn)
        lay.addLayout(top)
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        lay.addWidget(self.table, stretch=1)
        bottom = QHBoxLayout()
        self.info = QLabel("")
        promote_btn = QPushButton(_("Promote Selected to Jobs"))
        promote_btn.clicked.connect(self._promote_selected)
        bottom.addWidget(self.info, stretch=1)
        bottom.addWidget(promote_btn)
        lay.addLayout(bottom)
        self._rows: list = []
        self._discover_files()

    def _discover_files(self):
        self.file_combo.clear()
        try:
            out_dir = paths.SCAN_OUTPUT_DIR
        except Exception:
            out_dir = None
        import glob as _glob
        import os as _os
        files = (sorted(_glob.glob(
            str(_os.path.join(str(out_dir), "*quarantine.csv"))))
            if out_dir else [])
        if files:
            self.file_combo.addItems(files)
            self.file_combo.setCurrentIndex(len(files) - 1)  # newest run
            self._load_file()
        else:
            self.info.setText(_("No quarantine artifacts found."))

    def _load_file(self):
        import csv as _csv
        path = self.file_combo.currentText()
        self._rows = []
        if path:
            try:
                with open(path, "r", encoding="utf-8-sig", newline="") as f:
                    self._rows = list(_csv.DictReader(f))
            except OSError as exc:
                QMessageBox.critical(self, _("Error"), str(exc))
        reasons = sorted({(r.get("Quarantine Reason") or "?")
                          for r in self._rows})
        self.reason_combo.blockSignals(True)
        self.reason_combo.clear()
        self.reason_combo.addItem(_("All reasons"))
        self.reason_combo.addItems(reasons)
        self.reason_combo.blockSignals(False)
        self._refilter()

    def _refilter(self):
        want = self.reason_combo.currentText()
        show_all = want in (_("All reasons"), "")
        self.table.setRowCount(0)
        shown = 0
        for idx, r in enumerate(self._rows):
            reason = r.get("Quarantine Reason") or "?"
            if not show_all and reason != want:
                continue
            i = self.table.rowCount()
            self.table.insertRow(i)
            vals = [r.get("Company Name", ""), r.get("Job Title", ""),
                    r.get("Job Location", ""), reason,
                    r.get("Job URL", "")]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(str(val or ""))
                if col == 0:
                    item.setData(Qt.UserRole, idx)
                self.table.setItem(i, col, item)
            shown += 1
        self.info.setText(
            _("{shown} of {total} quarantined rows.").format(
                shown=shown, total=len(self._rows)))

    def _promote_selected(self):
        sel = sorted({i.row() for i in self.table.selectedIndexes()})
        if not sel:
            QMessageBox.information(
                self, _("Quarantine"), _("Select rows first."))
            return
        from sponsorscout.core import persistence
        from sponsorscout.scanning import pipeline
        conn = db.get_connection(self.db_path)
        promoted = 0
        try:
            for table_row in sel:
                src_idx = self.table.item(table_row, 0).data(Qt.UserRole)
                row = self._rows[src_idx]
                subtype = ("recruiter"
                           if (row.get("Source Type") or "") == "recruiter"
                           else "direct")
                job = pipeline._row_to_job(
                    row, source_subtype=subtype,
                    run_id=row.get("Run ID") or "manual-promote")
                if job is None:
                    continue
                persistence.upsert_job(conn, job, commit=False)
                promoted += 1
            conn.commit()
        finally:
            conn.close()
        QMessageBox.information(
            self, _("Quarantine"),
            _("{n} row(s) promoted into jobs.").format(n=promoted))


class _CompanyPicker(QWidget):
    """One phase's company picker: enable checkbox + filterable checklist."""

    selection_changed = Signal()

    def __init__(self, title, enabled_label, path, parent=None):
        super().__init__(parent)
        self.seed_path = path
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.enabled_cb = QCheckBox(enabled_label)
        self.enabled_cb.setChecked(True)
        self.enabled_cb.toggled.connect(self._on_toggle)
        lay.addWidget(self.enabled_cb)
        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.setSelectionMode(QAbstractItemView.NoSelection)
        lay.addWidget(self.list, stretch=1)
        brow = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(_("Filter companies…"))
        self.filter.textChanged.connect(self._apply_filter)
        brow.addWidget(self.filter, stretch=1)
        self.all_btn = QPushButton(_("Select all"))
        self.all_btn.clicked.connect(lambda: self._set_all(True))
        brow.addWidget(self.all_btn)
        self.none_btn = QPushButton(_("Clear"))
        self.none_btn.clicked.connect(lambda: self._set_all(False))
        brow.addWidget(self.none_btn)
        lay.addLayout(brow)
        self.count_lbl = QLabel("")
        lay.addWidget(self.count_lbl)
        self.reload()
        self.enabled_cb.toggled.connect(self.selection_changed)
        self.list.itemChanged.connect(lambda _i: self.selection_changed.emit())

    def _on_toggle(self, on: bool):
        self.list.setEnabled(on)
        self.all_btn.setEnabled(on)
        self.none_btn.setEnabled(on)
        self.filter.setEnabled(on)

    def reload(self):
        try:
            rows = seed_manager.read_seed_rows(self.seed_path)["rows"]
        except Exception:
            rows = []
        self.list.blockSignals(True)
        self.list.clear()
        for r in rows:
            name = (r.get("name") or "").strip()
            if not name:
                continue
            industry = (r.get("industry") or "").strip()
            label = f"{name}  ({industry})" if industry else name
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.selection_changed.emit()

    def _apply_filter(self, text):
        low = (text or "").lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(bool(low) and low not in item.text().lower())

    def _set_all(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            if not self.list.item(i).isHidden():
                self.list.item(i).setCheckState(state)
        self.list.blockSignals(False)
        self.selection_changed.emit()

    def is_enabled(self) -> bool:
        return self.enabled_cb.isChecked()

    def selected_names(self) -> list:
        return [self.list.item(i).data(Qt.UserRole)
                for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def total_count(self) -> int:
        return self.list.count()


class CustomScanDialog(QDialog):
    """Pick which companies / source types a custom scan covers."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Custom Scan"))
        self.resize(640, 560)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(_(
            "Choose the source types and companies to scan. Selected "
            "companies are scanned exactly like in a full scan — the full "
            "scan simply covers every seeded company.")))
        self.ats_picker = _CompanyPicker(
            _("ATS portals"), _("Scan ATS portals (API-based, fast)"),
            seed_manager.user_ats_path())
        self.career_picker = _CompanyPicker(
            _("Career portals"), _("Scan career portals (crawled, slower)"),
            seed_manager.user_career_path())
        lay.addWidget(self.ats_picker, stretch=1)
        lay.addWidget(self.career_picker, stretch=1)
        self.summary_lbl = QLabel("")
        lay.addWidget(self.summary_lbl)
        brow = QHBoxLayout()
        self.start_btn = QPushButton(_("Start Custom Scan"))
        self.start_btn.setObjectName("Primary")
        self.start_btn.clicked.connect(self.accept)
        self.cancel_btn = QPushButton(_("Cancel"))
        self.cancel_btn.clicked.connect(self.reject)
        brow.addStretch(1)
        brow.addWidget(self.cancel_btn)
        brow.addWidget(self.start_btn)
        lay.addLayout(brow)
        self.ats_picker.selection_changed.connect(self._refresh_summary)
        self.career_picker.selection_changed.connect(self._refresh_summary)
        self._refresh_summary()

    def _refresh_summary(self):
        n_ats = len(self.ats_picker.selected_names())
        n_career = len(self.career_picker.selected_names())
        parts = []
        if self.ats_picker.is_enabled():
            parts.append(_("ATS: {n}/{total}").format(
                n=n_ats, total=self.ats_picker.total_count()))
        if self.career_picker.is_enabled():
            parts.append(_("Career: {n}/{total}").format(
                n=n_career, total=self.career_picker.total_count()))
        self.summary_lbl.setText(_("Selected: {parts}").format(
            parts="  ·  ".join(parts) if parts else "—"))
        self.start_btn.setEnabled(bool(parts))

    def scope(self) -> dict:
        return {
            "run_ats": self.ats_picker.is_enabled(),
            "run_career": self.career_picker.is_enabled(),
            "ats": self.ats_picker.selected_names(),
            "career": self.career_picker.selected_names(),
        }


class ToolsTab(QWidget):
    """Scanner control + data-quality tools (mirrors the original Tools tab)."""

    scan_started = Signal()       # emitted at the beginning of start_scan
    scan_finished = Signal(dict)
    data_changed = Signal()        # jobs/companies data may have changed
    status_message = Signal(str)
    _freshness_done = Signal(str)  # marshals worker results to the UI thread
    # History-table refresh result: ``refresh()`` reads the DB on a worker
    # thread and only touches the widget when the payload arrives here — same
    # reason as the Resume lookup.  Without it the run table (another full
    # scan_runs read) froze the window at the exact moment a scan ended.
    _refresh_done = Signal(object)
    # Resume-checkpoint lookup result from the worker thread: carries the
    # ``db.get_resumable_scan`` dict (or ``None`` / error string) back to the
    # GUI thread.  The lookup reads scan_runs + both seed CSVs, which blocks
    # up to the SQLite busy_timeout while a scan writes — doing it on the UI
    # thread is what froze the window on Stop/Resume.
    _resume_lookup_done = Signal(object)

    def __init__(self, db_path: str, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.coordinator = ScanCoordinator(db_path=db_path)
        self.coordinator.progress.connect(self._on_scan_progress)
        self.coordinator.progress_tick.connect(self._on_scan_progress_tick)
        self.coordinator.finished.connect(self._on_scan_finished)
        self.coordinator.state_changed.connect(self._on_coordinator_state)
        self._freshness_done.connect(self._on_freshness_done)
        self._resume_lookup_done.connect(self._on_resume_lookup_done)
        self._refresh_done.connect(self._on_refresh_done)
        # Guard for the async resume-checkpoint lookup: the worker writes the
        # result through _resume_lookup_done and only the latest reply may
        # touch the buttons / dialogs.  A superseded slow reply (e.g. a
        # pre-scan probe that returns after the scan finished) is ignored.
        self._resume_lookup_seq = 0
        self._resume_dialog_armed = False
        # Same idea for the run-history read (see refresh()).
        self._refresh_seq = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── Scanner group ────────────────────────────────────────────────────
        root.addWidget(self._build_scanner_group())
        # ── Scan history group ───────────────────────────────────────────────
        root.addWidget(self._build_history_group(), stretch=1)
        # ── Data quality group ───────────────────────────────────────────────
        root.addWidget(self._build_quality_group())
        # ── Freshness check group ────────────────────────────────────────────
        root.addWidget(self._build_freshness_group())
        root.addStretch(1)
        # Resume availability depends on stopped runs in the DB — refresh
        # the button once the tab exists (safe if the DB is unreachable).
        try:
            self._refresh_resume_button()
        except Exception:
            pass

# ── Group builders ───────────────────────────────────────────────────────
    @staticmethod
    def _add_section_help(box: QGroupBox, text: str) -> None:
        """Add a small grey description label under a group-box title, and
        also set it as the group box tooltip (hover for the same text)."""
        desc = QLabel(text)
        desc.setObjectName("SectionHelp")
        desc.setWordWrap(True)
        # Insert the description as the first widget of the box layout,
        # which places it directly beneath the title row.
        lay = box.layout()
        if lay is not None:
            lay.insertWidget(0, desc)
        box.setToolTip(text)

    def _build_scanner_group(self):
        scanner = QGroupBox(_("Scanner"))
        self._scanner_box = scanner
        lay = QVBoxLayout(scanner)
        self._add_section_help(scanner, _("Scanner description"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.scan_btn = QPushButton(_("Scan Now"))
        self.scan_btn.setObjectName("Primary")
        self.scan_btn.setToolTip(_(SCAN_BTN_TOOLTIP))
        self.scan_btn.clicked.connect(self.start_scan)
        self.custom_btn = QPushButton(_("Custom Scan"))
        self.custom_btn.setToolTip(_(CUSTOM_BTN_TOOLTIP))
        self.custom_btn.clicked.connect(self.start_custom_scan)
        self.resume_btn = QPushButton(_("Resume"))
        self.resume_btn.setEnabled(False)
        self.resume_btn.setToolTip(_(RESUME_BTN_TOOLTIP))
        self.resume_btn.clicked.connect(self.resume_scan)
        # True in-memory Pause (suspends the running workers at the next
        # company — same run, instant continue) plus a separate Stop that
        # ends the run with a DB checkpoint for later Resume.  The old
        # single control conflated both, so pausing forced a Stop +
        # DB re-scan + new thread = the laggy pause/resume.
        self.pause_btn = QPushButton(_("Pause"))
        self.pause_btn.setEnabled(False)
        self.pause_btn.setToolTip(_(PAUSE_BTN_TOOLTIP))
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.stop_btn = QPushButton(_("Stop"))
        self.stop_btn.setEnabled(False)
        self.stop_btn.setToolTip(_(STOP_BTN_TOOLTIP))
        self.stop_btn.clicked.connect(self.confirm_stop)
        self.scan_status = QLabel(_("Idle"))
        for w in (self.scan_btn, self.custom_btn, self.resume_btn,
                  self.pause_btn, self.stop_btn, self.scan_status):
            row.addWidget(w)
        row.addStretch(1)
        lay.addLayout(row)
        # ── Visual scan progress (cheap: one setValue per company tick) ──
        prow = QHBoxLayout()
        prow.setSpacing(8)
        self.scan_bar = QProgressBar()
        self.scan_bar.setRange(0, 1000)
        self.scan_bar.setValue(0)
        self.scan_bar.setTextVisible(True)
        self.scan_bar.setFormat("%p%")
        self.scan_phase = QLabel("")
        self.scan_phase.setMinimumWidth(220)
        prow.addWidget(self.scan_bar, stretch=1)
        prow.addWidget(self.scan_phase)
        lay.addLayout(prow)
        self.scan_log = QPlainTextEdit()
        self.scan_log.setReadOnly(True)
        self.scan_log.setMaximumHeight(180)
        # Bound the widget's memory: a full scan emits many thousands of lines
        # and an unbounded QPlainTextEdit keeps every one of them.
        self.scan_log.setMaximumBlockCount(4000)
        self.scan_log.setPlaceholderText(_("Scan output appears here…"))
        lay.addWidget(self.scan_log)
        return scanner

    def _build_history_group(self):
        box = QGroupBox(_("Scan History"))
        self._history_box = box
        lay = QVBoxLayout(box)
        self._add_section_help(box, _("Scan History description"))
        self.runs_table = QTableWidget(0, len(HEADERS_RUNS))
        self.runs_table.setHorizontalHeaderLabels(_runs_header_labels())
        self.runs_table.verticalHeader().setVisible(False)
        self.runs_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.runs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.runs_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.runs_table.setAlternatingRowColors(True)
        self.runs_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        lay.addWidget(self.runs_table)
        row = QHBoxLayout()
        self.view_log_btn = QPushButton(_("View Per-Company Log"))
        self.view_log_btn.clicked.connect(self._view_run_log)
        row.addWidget(self.view_log_btn)
        self.download_log_btn = QPushButton(_("Download Scan Log"))
        self.download_log_btn.clicked.connect(self._export_run_log)
        row.addWidget(self.download_log_btn)
        row.addStretch(1)
        lay.addLayout(row)
        return box

    def _build_quality_group(self):
        box = QGroupBox(_("Data Quality"))
        self._quality_box = box
        lay = QHBoxLayout(box)
        self._add_section_help(box, _("Data Quality description"))
        self.dedup_btn = QPushButton(_("Run Dedup"))
        self.dedup_btn.clicked.connect(self._run_dedup)
        self.stale_btn = QPushButton(_("Clear Stale Data"))
        self.stale_btn.clicked.connect(self._clear_stale_data)
        self.clear_scan_btn = QPushButton(_("Clear Scan Data"))
        self.clear_scan_btn.clicked.connect(self._clear_scan_data)
        self.quarantine_btn = QPushButton(_("Review Quarantine"))
        self.quarantine_btn.clicked.connect(self._review_quarantine)
        lay.addWidget(self.dedup_btn)
        lay.addWidget(self.stale_btn)
        lay.addWidget(self.clear_scan_btn)
        lay.addWidget(self.quarantine_btn)
        lay.addStretch(1)
        return box

    def _build_freshness_group(self):
        box = QGroupBox(_("Freshness Check"))
        self._freshness_box = box
        lay = QHBoxLayout(box)
        self._add_section_help(box, _("Freshness Check description"))
        lay.setSpacing(8)
        lay.addWidget(QLabel(_("Check up to")))
        self.verify_n = QSpinBox()
        self.verify_n.setRange(5, 200)
        self.verify_n.setValue(25)
        self.verify_n.setToolTip(
            _("Maximum number of active jobs to re-verify per run."))
        lay.addWidget(self.verify_n)
        lay.addWidget(QLabel(_("jobs")))
        self.fresh_btn = QPushButton(_("Run"))
        self.fresh_btn.clicked.connect(self._run_freshness)
        lay.addWidget(self.fresh_btn)
        self.fresh_status = QLabel(_("Idle"))
        lay.addWidget(self.fresh_status)
        lay.addStretch(1)
        return box

    def _set_scan_running_ui(self, phase_text: str):
        """Shared button/label setup when a scan campaign starts or resumes."""
        self.scan_btn.setEnabled(False)
        self.custom_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.pause_btn.setText(_("Pause"))
        self.pause_btn.setToolTip(_(PAUSE_BTN_TOOLTIP))
        self.stop_btn.setEnabled(True)
        self.scan_status.setText(_("Running…"))
        self.scan_bar.setValue(0)
        self.scan_phase.setText(phase_text)

    def _on_coordinator_state(self, state: str):
        """Mirror coordinator pause/stop transitions without polling."""
        if state == "paused":
            self.pause_btn.setEnabled(True)
            self.pause_btn.setText(_("Resume"))
            self.pause_btn.setToolTip(_(INMEMORY_RESUME_TOOLTIP))
            self.scan_status.setText(_("Paused"))
            self.stop_btn.setEnabled(True)
        elif state == "running":
            self.pause_btn.setEnabled(True)
            self.pause_btn.setText(_("Pause"))
            self.pause_btn.setToolTip(_(PAUSE_BTN_TOOLTIP))
            self.scan_status.setText(_("Running…"))
            self.stop_btn.setEnabled(True)
        elif state == "stopping":
            # Buttons freeze so a second click cannot re-arm anything; the
            # worker emits "idle" when it actually lands.
            self.pause_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.scan_status.setText(_("Stopping…"))

    def toggle_pause(self):
        """Pause a live run in place, or resume it instantly (same run)."""
        if not self.coordinator.is_running():
            return
        if self.coordinator.is_paused():
            self.coordinator.resume()
            return
        # Optimistic flip FIRST: pause() acknowledges on the spot through
        # state_changed("paused") (same thread -> direct connection), so a
        # label set after the call would overwrite the acknowledgement and
        # leave "Pausing…" on screen for the whole pause.
        self.pause_btn.setText(_("Resume"))
        self.pause_btn.setToolTip(_(INMEMORY_RESUME_TOOLTIP))
        self.scan_status.setText(_("Pausing…"))
        # Workers honour the pause at the next company; the run keeps going
        # in memory, so nothing is checkpointed and no new scan is started.
        self.coordinator.pause()

    def confirm_stop(self):
        """Complete stop with confirmation: ends the run, keeps a checkpoint."""
        if not self.coordinator.is_running():
            return
        answer = QMessageBox.question(
            self, _("Stop"),
            _("Stop the running scan? Everything found so far is kept and "
              "can be resumed later."),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        # UI first, then the state machine: ``stop()`` acknowledges through
        # state_changed("stopping") synchronously, so a label written after the
        # call could hide a click that already landed (see toggle_pause).
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.scan_status.setText(_("Stopping…"))
        self.scan_phase.setText(_("Stopping…"))
        self.coordinator.stop()

# ── Scanner control ──────────────────────────────────────────────────────
    def start_scan(self):
        if self.coordinator.is_running():
            QMessageBox.information(self, _("SponsorScout"),
                                    _("A scan is already running."))
            return
        # Single scan mode (see SCAN_METHOD): no method choice is offered —
        # the app always runs the campaign that extracts the most accurate
        # data for every job.
        self.scan_log.clear()
        self._set_scan_running_ui(_("Starting scan…"))
        self.status_message.emit(_("Scan started"))
        self.coordinator.start(SCAN_METHOD)

    def start_custom_scan(self):
        """Open the picker dialog and start a scoped scan with it."""
        if self.coordinator.is_running():
            QMessageBox.information(self, _("SponsorScout"),
                                    _("A scan is already running."))
            return
        try:
            dlg = CustomScanDialog(self)
            if dlg.exec() != QDialog.Accepted:
                return
            scope = dlg.scope()
        except Exception:
            QMessageBox.critical(self, _("SponsorScout"),
                                 _("Could not open the custom scan dialog."))
            return
        if not (scope.get("run_ats") or scope.get("run_career")):
            QMessageBox.information(self, _("SponsorScout"),
                                    _("Select at least one source type."))
            return
        if not (scope.get("ats") or scope.get("career")):
            QMessageBox.information(self, _("SponsorScout"),
                                    _("Select at least one company."))
            return
        self.scan_log.clear()
        self._set_scan_running_ui(_("Starting custom scan…"))
        self.status_message.emit(_("Custom scan started"))
        self.coordinator.start(SCAN_METHOD, scan_scope=scope)

    def resume_scan(self):
        """Continue the newest stopped run (Stop-as-checkpoint).

        The DB lookup runs on a worker thread and the checkpoint dialog /
        scan starts when the result arrives — Resume never blocks the UI,
        even while a previous scan still holds the database.
        """
        if self.coordinator.is_running():
            QMessageBox.information(self, _("SponsorScout"),
                                    _("A scan is already running."))
            return
        self._start_resume_lookup(arm_dialog=True)

    def _refresh_resume_button(self):
        """Enable Resume only when a stopped run has unfinished companies.

        Always asynchronous: the lookup runs on a worker thread (it can
        block on the scan's SQLite writes) and _on_resume_lookup_done
        applies the result as a silent probe.  Safe to call from __init__,
        tab entry (showEvent) and the moment a scan finishes.
        """
        if self.coordinator.is_running():
            return
        self._start_resume_lookup(arm_dialog=False)

    def _start_resume_lookup(self, *, arm_dialog: bool):
        """Run get_resumable_scan() off the GUI thread (never blocks it).

        ``arm_dialog`` True = the user pressed Resume: the result opens the
        checkpoint dialog/starts the scan.  False = silent probe after
        construction/tab entry/finish that only refreshes the button.
        """
        self._resume_lookup_seq += 1
        seq = self._resume_lookup_seq
        self._resume_dialog_armed = arm_dialog
        if arm_dialog:
            self.resume_btn.setEnabled(False)
            self.scan_phase.setText(_("Looking up the last stopped scan…"))

        def worker():
            try:
                checkpoint = db.get_resumable_scan(self.db_path)
            except Exception as exc:  # defensive: marshal, never crash
                self._resume_lookup_done.emit((seq, "error", str(exc)))
            else:
                self._resume_lookup_done.emit((seq, "ok", checkpoint))

        threading.Thread(target=worker, name="ResumeLookup",
                         daemon=True).start()

    def _on_resume_lookup_done(self, payload: object):
        """Apply the newest resume lookup; drop superseded slow replies."""
        try:
            seq, kind, data = payload
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return
        if seq != self._resume_lookup_seq:
            return  # a newer lookup already replaced this one
        arm_dialog = self._resume_dialog_armed
        self._resume_dialog_armed = False
        if not arm_dialog:
            self._apply_resume_probe(data if kind == "ok" else None)
            return
        self.scan_phase.setText("")
        if kind == "error":
            QMessageBox.critical(
                self, _("SponsorScout"),
                _("Could not find a scan to resume:\n{error}")
                .format(error=str(data)))
            self._refresh_resume_button()
            return
        if not data:
            QMessageBox.information(
                self, _("SponsorScout"),
                _("Nothing to resume — no stopped scan with unfinished "
                  "companies."))
            self._refresh_resume_button()
            return
        self._begin_checkpoint_resume(data)

    def _apply_resume_probe(self, checkpoint):
        """Silent probe result: only enable/disable Resume + its tooltip."""
        if self.coordinator.is_running():
            return
        self.resume_btn.setEnabled(bool(checkpoint))
        if checkpoint:
            remaining = (len(checkpoint["remaining_ats"])
                         + len(checkpoint["remaining_career"]))
            total = checkpoint["total_ats"] + checkpoint["total_career"]
            done = total - remaining
            self.resume_btn.setToolTip(
                _("Resume {run} — {done}/{total} done, {remaining} "
                  "remaining.").format(run=checkpoint["run_id"], done=done,
                                        total=total, remaining=remaining))
        else:
            self.resume_btn.setToolTip(_(RESUME_BTN_TOOLTIP))

    def _begin_checkpoint_resume(self, checkpoint: dict):
        """Start a checkpoint continuation from an already-fetched lookup."""
        remaining = (len(checkpoint["remaining_ats"])
                     + len(checkpoint["remaining_career"]))
        total = checkpoint["total_ats"] + checkpoint["total_career"]
        done = total - remaining
        self.scan_log.clear()
        self.scan_log.appendPlainText(
            _("Resuming {run} — {done}/{total} companies already done, "
              "{remaining} remaining.").format(
                run=checkpoint["run_id"], done=done, total=total,
                remaining=remaining))
        if checkpoint.get("added_since_stop"):
            self.scan_log.appendPlainText(
                _("(+{n} companies added to seeds since the stop — "
                  "they are included.)").format(
                    n=checkpoint["added_since_stop"]))
        self._set_scan_running_ui(
            _("Resuming — {done}/{total} done.").format(done=done,
                                                        total=total))
        try:
            self.scan_bar.setValue(int(done * 1000 / total) if total else 0)
        except Exception:
            self.scan_bar.setValue(0)
        self.status_message.emit(_("Resuming scan"))
        self.coordinator.start(SCAN_METHOD,
                               resume_from=checkpoint["run_id"])

    def _on_scan_progress(self, chunk: str):
        from sponsorscout.application.scan_coordinator import PROGRESS_PREFIX
        visible = [ln for ln in str(chunk).splitlines()
                   if not ln.strip().startswith(PROGRESS_PREFIX)]
        if visible:
            self.scan_log.appendPlainText("\n".join(visible))

    def _on_scan_progress_tick(self, done: int, total: int,
                               phase: str, label: str):
        # Visual only: one integer setValue + one short label per company.
        # Guards keep stale/edge ticks (subset scans, zero totals) sane.
        try:
            if total <= 0:
                return
            done = max(0, min(int(done), int(total)))
            self.scan_bar.setValue(int(done * 1000 / total))
            # The tick label already carries phase name + phase-local count
            # ("ATS 1/46" / "Career 4/162"); prepending the overall count too
            # produced the duplicated "ATS 1/208 — ATS 1/46" text.  The bar
            # itself shows overall %, so render just the label.
            if label:
                self.scan_phase.setText(str(label))
            elif phase == "ats":
                self.scan_phase.setText(_("ATS"))
            elif phase == "career":
                self.scan_phase.setText(_("Career"))
            else:
                self.scan_phase.setText(str(phase or _("Scan")))
        except Exception:
            pass

    def _on_scan_finished(self, summary: dict):
        self.scan_btn.setEnabled(True)
        self.custom_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText(_("Pause"))
        self.stop_btn.setEnabled(False)
        status = summary.get("status", "error")
        stopped = bool(summary.get("cancelled"))
        if stopped:
            status = "cancelled"
        # Show the translated "Stopped." instead of the raw pipeline status
        # ("cancelled") — the label sits next to the buttons the user just
        # pressed, and app.py already reports "Scan stopped." in the status bar.
        self.scan_status.setText(_("Stopped.") if stopped else status)
        for err in summary.get("errors") or []:
            self.scan_log.appendPlainText(f"ERROR: {err}")
        self.scan_log.appendPlainText(
            f"--- {status}: ingested={summary.get('ingested', 0)}, "
            f"duplicates={summary.get('duplicates', 0)}, "
            f"quarantined={summary.get('quarantined', 0)} ---")
        try:
            if summary.get("cancelled"):
                self.scan_phase.setText(
                    _("Cancelled — partial progress shown."))
            elif status in ("completed", "partial"):
                self.scan_bar.setValue(self.scan_bar.maximum())
                self.scan_phase.setText(_("Finished."))
        except Exception:
            pass
        self.refresh()
        self._refresh_resume_button()
        self.data_changed.emit()
        self.scan_finished.emit(summary)
        self.status_message.emit(_("Scan finished: ") + status)

    def _view_run_log(self):
        row = self.runs_table.currentRow()
        if row < 0:
            QMessageBox.information(self, _("SponsorScout"),
                                    _("Select a scan run first."))
            return
        run_id = self.runs_table.item(row, 0).text()
        ScanLogDialog(self.db_path, run_id, self).exec()

    def _export_run_log(self):
        """Download the selected run's full scan analysis (summary + per-company
        log + event timeline) as a CSV file."""
        row = self.runs_table.currentRow()
        if row < 0:
            QMessageBox.information(self, _("SponsorScout"),
                                    _("Select a scan run first."))
            return
        run_id = self.runs_table.item(row, 0).text()
        docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        from pathlib import Path as _Path
        default_path = _Path(docs or ".") / f"sponsorscout_scan_log_{run_id}.csv"
        path, _filt = QFileDialog.getSaveFileName(
            self, _("Download Scan Log"), str(default_path),
            _("CSV files (*.csv);;All files (*.*)"))
        if not path:
            return
        try:
            content = db.export_scan_run_csv(self.db_path, run_id)
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(content)
        except Exception as exc:
            QMessageBox.critical(self, _("Error"),
                                 _("Could not save scan log:\n{error}")
                                 .format(error=str(exc)))
            return
        # Verify and report what the file actually contains.
        p = _Path(path)
        n_log = len(db.get_scan_log(self.db_path, run_id))
        n_events = len(db.get_scan_events(self.db_path, run_id))
        if not p.is_file() or p.stat().st_size <= 0:
            QMessageBox.critical(
                self, _("Error"),
                _("The file appears empty:\n{path}").format(path=path))
            return
        answer = QMessageBox.information(
            self, _("Scan log downloaded"),
            _("Scan log ({bytes} bytes, {rows} company row(s), {events} "
              "event(s)) saved to:\n{path}")
            .format(bytes=p.stat().st_size, rows=n_log, events=n_events,
                    path=path),
            QMessageBox.Ok | QMessageBox.Open,
            QMessageBox.Ok)
        if answer == QMessageBox.Open:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent)))

    def refresh(self):
        """Rebuild the run-history table without blocking the GUI thread.

        ``db.list_scan_runs`` opens its own connection; while a scan is
        writing, that read can block up to the SQLite busy_timeout.  Doing it
        inline is what froze the window at the exact moment a scan ended
        (``_on_scan_finished`` → ``refresh()``), and it is also called by
        ``app._refresh_all()``.  The rows come back through ``_refresh_done``
        so the widget itself is only ever touched on the GUI thread.
        """
        self._refresh_seq += 1
        seq = self._refresh_seq

        def worker():
            try:
                rows = db.list_scan_runs(self.db_path, limit=25)
            except Exception:  # defensive: a failed read must not kill the tab
                rows = []
            self._refresh_done.emit((seq, rows))

        threading.Thread(target=worker, name="RunsRefresh", daemon=True).start()

    def _on_refresh_done(self, payload: object):
        """Apply the newest history read; superseded slow replies are dropped."""
        try:
            seq, rows = payload
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return
        if seq != self._refresh_seq:
            return  # a newer refresh already replaced this one
        self._populate_runs_table(rows)
        try:
            self._refresh_resume_button()
        except Exception:
            pass

    def _populate_runs_table(self, rows):
        """Fill the run table (GUI thread only; rows come from the worker)."""
        self.runs_table.setRowCount(0)
        for r in rows:
            (run_id, method, started, _fin, status, _err, _ok, _empty,
             n_err, jobs, quarantined, dups, _ats, _career) = r[:14]
            row_idx = self.runs_table.rowCount()
            self.runs_table.insertRow(row_idx)
            # Resume chain: child rows carry "resumed_from:<parent>" and
            # promoted parents carry "resumed_by:<child>" in the error/notes
            # field — surface it so pause/resume is visible in history.
            try:
                _err_text = str(_err or "")
                _parent = db.parse_resume_link(_err_text)
                _child = db.parse_resumed_by(_err_text)
                if status == "resumed" and _child:
                    status_text = (
                        f"{_('resumed')} ↩ {_child}")
                elif _parent:
                    status_text = (
                        f"{status} ↩ {_('resumed from')} {_parent}")
                else:
                    status_text = str(status)
            except Exception:
                status_text = str(status)
            values = (run_id, method, (started or "")[:19], status_text,
                      jobs, dups, quarantined, n_err)
            for col, val in enumerate(values):
                self.runs_table.setItem(
                    row_idx, col, QTableWidgetItem(str(val if val is not None else "")))

    # ── Data-quality actions (ported from the original Tools tab) ───────────
    def _review_quarantine(self):
        QuarantineDialog(self.db_path, self).exec()
        self.data_changed.emit()

    def _run_dedup(self):
        try:
            conn = db.get_connection(self.db_path)
            try:
                jd = dedup_jobs_in_db(conn)
                cd = dedup_companies_in_db(conn)
            finally:
                conn.close()
            QMessageBox.information(
                self, _("Dedup complete"),
                _("Removed {jobs} duplicate job(s) and {companies} duplicate "
                  "company entry(ies).").format(jobs=jd, companies=cd))
            self.data_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, _("Error"), str(exc))

    def _clear_scan_data(self):
        """Wipe ALL scanned data (jobs, scan history, scan logs) from the DB.

        Seed CSVs (the source of truth) and the companies registry are never
        touched, so the user can start a completely fresh scan run.
        """
        try:
            conn = db.get_connection(self.db_path)
            try:
                jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                runs = conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0]
                logs = conn.execute("SELECT COUNT(*) FROM scan_log").fetchone()[0]
                if jobs == 0 and runs == 0 and logs == 0:
                    QMessageBox.information(
                        self, _("Clear Scan Data"),
                        _("The database already contains no scanned data."))
                    return
                answer = QMessageBox.question(
                    self, _("Clear Scan Data"),
                    _("This will permanently delete {jobs} scanned job(s), "
                      "{runs} scan run(s) and {logs} scan log row(s) from the "
                      "database.\nSeed CSVs and saved applications are NOT "
                      "affected. Continue?").format(
                          jobs=jobs, runs=runs, logs=logs),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    return
                conn.execute("DELETE FROM jobs")
                conn.execute("DELETE FROM scan_runs")
                conn.execute("DELETE FROM scan_log")
                conn.execute("DELETE FROM scan_events")
                conn.commit()
                conn.execute("VACUUM")
            finally:
                conn.close()
            QMessageBox.information(
                self, _("Scan data cleared"),
                _("Removed {jobs} job(s), {runs} scan run(s) and {logs} scan "
                  "log row(s). Seed CSVs were not touched.").format(
                      jobs=jobs, runs=runs, logs=logs))
            self.data_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, _("Error"), str(exc))

    def _clear_stale_data(self):
        try:
            conn = db.get_connection(self.db_path)
            try:
                cur = conn.execute("DELETE FROM jobs WHERE is_expired=1")
                deleted = cur.rowcount
                # Commit BEFORE VACUUM: Python's sqlite3 auto-starts a
                # transaction on the DELETE, and VACUUM cannot run inside one
                # ("cannot VACUUM from within a transaction"). Committing first
                # persists the delete and lets VACUUM run cleanly.
                conn.commit()
                if deleted > 0:
                    conn.execute("VACUUM")
            finally:
                conn.close()
            if deleted > 0:
                QMessageBox.information(
                    self, _("Stale data cleared"),
                    _("Removed {n} expired job(s) from the database.")
                    .format(n=deleted))
            else:
                QMessageBox.information(
                    self, _("Stale data cleared"),
                    _("No expired jobs to remove — the database is already "
                      "clean."))
            self.data_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, _("Error"), str(exc))

    # ── Freshness verification (worker thread + signal marshalling) ────────
    def _run_freshness(self):
        n = self.verify_n.value()
        self.status_message.emit(_("Verifying up to {n} jobs…").format(n=n))
        self.fresh_status.setText(_("Running…"))
        self.fresh_btn.setEnabled(False)
        threading.Thread(target=self._freshness_worker,
                         args=(n,), daemon=True).start()

    def _freshness_worker(self, n: int):
        """Background thread; results marshalled via _freshness_done."""
        try:
            from sponsorscout.core.persistence import upsert_job
            from sponsorscout.core.verification_service import verify_job
            conn = db.get_connection(self.db_path)
            try:
                rows = conn.execute("""
                    SELECT url FROM jobs
                    WHERE verified_active=1 AND is_expired=0
                      AND (last_verified_at IS NULL OR
                           last_verified_at < datetime('now','-7 days'))
                    ORDER BY last_verified_at ASC LIMIT ?""",
                    (n,)).fetchall()
                expired = checked = 0
                for row in rows:
                    jr = conn.execute(
                        "SELECT * FROM jobs WHERE url=?",
                        (row["url"],)).fetchone()
                    if not jr:
                        continue
                    result = verify_job(dict(jr))
                    upsert_job(conn, result, commit=False)
                    if result.get("is_expired"):
                        expired += 1
                    checked += 1
                conn.commit()
            finally:
                conn.close()
            self._freshness_done.emit(
                _("Checked {checked} — expired {expired}.").format(
                    checked=checked, expired=expired))
        except Exception as exc:
            self._freshness_done.emit(f"{_('Error')}: {exc}")

    def _on_freshness_done(self, msg: str):
        self.fresh_status.setText(msg)
        self.fresh_btn.setEnabled(True)
        self.status_message.emit(_("Freshness check done."))
        self.data_changed.emit()

    # ── i18n ──────────────────────────────────────────────────────────────
    def retranslate(self):
        self._scanner_box.setTitle(_("Scanner"))
        self._history_box.setTitle(_("Scan History"))
        self._quality_box.setTitle(_("Data Quality"))
        self._freshness_box.setTitle(_("Freshness Check"))
        for box in (self._scanner_box, self._history_box,
                    self._quality_box, self._freshness_box):
            help_lbl = box.findChild(QLabel, "SectionHelp")
            if help_lbl is not None:
                key = {
                    id(self._scanner_box): "Scanner description",
                    id(self._history_box): "Scan History description",
                    id(self._quality_box): "Data Quality description",
                    id(self._freshness_box): "Freshness Check description",
                }.get(id(box))
                if key:
                    txt = _(key)
                    help_lbl.setText(txt)
                    box.setToolTip(txt)
        self.scan_btn.setText(_("Scan Now"))
        self.custom_btn.setText(_("Custom Scan"))
        self.custom_btn.setToolTip(_(CUSTOM_BTN_TOOLTIP))
        self.scan_btn.setToolTip(_(SCAN_BTN_TOOLTIP))
        # The Pause/Resume toggle plus the separate Stop button (and their
        # hover texts) were previously missed here, leaving e.g. "Riprendi"
        # + an Italian Stop tooltip on an English UI after a language switch.
        self.pause_btn.setText(
            _("Resume") if self.coordinator.is_paused() else _("Pause"))
        self.pause_btn.setToolTip(
            _(INMEMORY_RESUME_TOOLTIP) if self.coordinator.is_paused()
            else _(PAUSE_BTN_TOOLTIP))
        self.stop_btn.setText(_("Stop"))
        self.stop_btn.setToolTip(_(STOP_BTN_TOOLTIP))
        self.resume_btn.setText(_("Resume"))
        self.resume_btn.setToolTip(_(RESUME_BTN_TOOLTIP))
        # Never clobber a transitional acknowledge ("Pausing…"/"Stopping…"
        # set by the button handlers): a language switch that rewrote them
        # to "Running…" would hide that the click already landed.
        current = self.scan_status.text()
        transitional = {_("Pausing…"), _("Stopping…"),
                        "Pausing…", "Stopping…",
                        "Pausa in corso…", "Arresto in corso…"}
        if current not in transitional:
            if self.coordinator.is_paused():
                self.scan_status.setText(_("Paused"))
            else:
                self.scan_status.setText(
                    _("Running…") if self.coordinator.is_running()
                    else _("Idle"))
        # The checkpoint-aware tooltip needs a DB read (scan_runs + seed CSVs,
        # ~25 ms idle and up to busy_timeout during a scan). A language switch
        # must stay instant, so it is NOT recomputed here: the base tooltip is
        # set above and the detailed one returns the next time the Tools tab
        # becomes visible (see showEvent) or a scan finishes.
        self.verify_n.setToolTip(
            _("Maximum number of active jobs to re-verify per run."))
        self.scan_log.setPlaceholderText(_("Scan output appears here…"))
        self.view_log_btn.setText(_("View Per-Company Log"))
        self.download_log_btn.setText(_("Download Scan Log"))
        self.dedup_btn.setText(_("Run Dedup"))
        self.stale_btn.setText(_("Clear Stale Data"))
        self.clear_scan_btn.setText(_("Clear Scan Data"))
        self.quarantine_btn.setText(_("Review Quarantine"))
        self.fresh_btn.setText(_("Run"))
        self.runs_table.setHorizontalHeaderLabels(_runs_header_labels())

    def showEvent(self, event):
        """Refresh locale-dependent state when the tab is actually shown.

        Re-running the whole retranslate() here (instead of on every language
        switch) keeps the switch instant; the cost lands on the tab change,
        where a ~25 ms DB read is imperceptible.
        """
        super().showEvent(event)
        self.retranslate()
        # Cheap for the labels; this is the one DB read, done on tab entry
        # (and after every scan) instead of on every language switch.
        self._refresh_resume_button()