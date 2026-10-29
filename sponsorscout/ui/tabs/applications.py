"""Applications tab: saved-application pipeline tracker.

Ported from the tkinter Applications tab (tree of saved applications with
an inline status/notes edit form) to PySide6, preserving behavior:
selecting a row reveals the edit form; Save updates status + notes;
Remove deletes the row after confirmation.

Layout: the table auto-fits its columns to their content on load, every
column is manually resizable, the Status cell is colour-coded by status,
and the latest saved note is shown in its own column. A read-only activity
log above the edit form records each status change made this session.
"""

from datetime import datetime

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from sponsorscout.db import database as db
from sponsorscout.ui.async_load import AsyncLoader
from sponsorscout.i18n import _

APP_STATUSES = ["saved", "applied", "interview", "offer", "rejected"]

# Status → (background, text) colours for the Status cell.
STATUS_COLORS = {
    "saved":     ("#E8EEF7", "#1F3A5F"),
    "applied":   ("#DCEBFF", "#0B4F9C"),
    "interview": ("#FFF1C2", "#7A5300"),
    "offer":     ("#D4F5DD", "#155B2A"),
    "rejected":  ("#FBD7D7", "#8B1A1A"),
}

# Canonical column order (defines the indexes); only the visible labels are
# translated — see _header_labels().
HEADERS = ["Company", "Title", "Status", "Saved on", "URL", "Latest notes"]
COL_STATUS = 2
COL_URL = 4
COL_NOTES = 5


def _header_labels() -> list:
    """HEADERS translated for display, in the same (fixed) column order."""
    return [_(h) for h in HEADERS]


class ApplicationsTab(QWidget):
    """Pipeline tracker for saved applications."""

    data_changed = Signal()          # dashboard stats may change
    status_message = Signal(str)

    def __init__(self, db_path: str, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self._loader = AsyncLoader(self, name="applications")
        self._render_generation = 0
        self._editing_url = None
        self._fitting = False            # True while we set widths ourselves
        self._user_resized = False       # becomes True once the user drags a border

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        # ── Toolbar row ──────────────────────────────────────────────────────
        tb = QHBoxLayout()
        title = QLabel(_("Saved applications"))
        title.setObjectName("SectionTitle")
        tb.addWidget(title)
        tb.addStretch(1)
        self.remove_btn = QPushButton(_("Remove Selected"))
        self.remove_btn.clicked.connect(self._remove_application)
        tb.addWidget(self.remove_btn)
        root.addLayout(tb)

        # ── Applications table ───────────────────────────────────────────────
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(_header_labels())
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.Interactive)   # manual resize on every column
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(60)
        header.setResizeContentsPrecision(200)
        header.sectionResized.connect(self._on_section_resized)
        self.table.itemSelectionChanged.connect(self._on_select)
        root.addWidget(self.table, 1)

        # ── Activity log (bottom area, session only) ─────────────────────────
        self.log_label = QLabel(_("Activity log"))
        self.log_label.setObjectName("SectionTitle")
        root.addWidget(self.log_label)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(96)
        self.log.setPlaceholderText(_("Status changes you save appear here."))
        root.addWidget(self.log)

        # ── Edit form (hidden until a row is selected) ───────────────────────
        self.form = QGroupBox(_(" Edit selected "))
        form_lay = QHBoxLayout(self.form)
        form_lay.addWidget(QLabel(_("Status:")))
        self.status_combo = QComboBox()
        self.status_combo.addItems(APP_STATUSES)
        form_lay.addWidget(self.status_combo)
        form_lay.addSpacing(12)
        form_lay.addWidget(QLabel(_("Notes:")))
        self.notes_edit = QLineEdit()
        self.notes_edit.setClearButtonEnabled(True)
        form_lay.addWidget(self.notes_edit, 1)
        self.save_btn = QPushButton(_("Save"))
        self.save_btn.setObjectName("Primary")
        self.save_btn.clicked.connect(self._update_application)
        form_lay.addWidget(self.save_btn)
        self.cancel_btn = QPushButton(_("Cancel"))
        self.cancel_btn.clicked.connect(self._hide_form)
        form_lay.addWidget(self.cancel_btn)
        self.form.hide()
        root.addWidget(self.form)

    # ── Column sizing ────────────────────────────────────────────────────────

    def _on_section_resized(self, _index, _old, _new):
        # Ignore resizes we caused ourselves while fitting content.
        if not self._fitting:
            self._user_resized = True

    def _fit_columns(self):
        """Size every column to its content (header text included)."""
        self._fitting = True
        try:
            self.table.resizeColumnsToContents()
            for col in range(self.table.columnCount()):
                self.table.setColumnWidth(
                    col, self.table.columnWidth(col) + 16)   # cell padding
        finally:
            self._fitting = False

    # ── Data loading ─────────────────────────────────────────────────────────

    def load_applications(self):
        path = self.db_path
        self._loader.request(lambda: [tuple(r) for r in db.list_applications(path)], self._apply_applications)

    def _apply_applications(self, rows):
        selected = self._selected_row_values()
        selected_url = selected[COL_URL] if selected else None
        self._render_generation += 1
        generation = self._render_generation
        self.table.setRowCount(0)
        def append(start=0):
            if generation != self._render_generation:
                return
            self.table.blockSignals(True)
            try:
                for r in rows[start:start+100]:
                    company, title, status, applied_at, _f, notes, job_url = r[:7]
                    row = self.table.rowCount()
                    self.table.insertRow(row)
                    for col, value in enumerate((company, title, status, (applied_at or '')[:10], job_url, notes)):
                        item = QTableWidgetItem(str(value or ''))
                        if col == COL_STATUS:
                            self._style_status(item, status)
                        self.table.setItem(row, col, item)
                    if job_url == selected_url:
                        self.table.selectRow(row)
            finally:
                self.table.blockSignals(False)
            if start+100 < len(rows):
                QTimer.singleShot(0, lambda: append(start+100))
            elif not self._user_resized:
                self._fit_columns()
        append()

    @staticmethod
    def _style_status(item: QTableWidgetItem, status):
        colors = STATUS_COLORS.get(str(status or "").strip().lower())
        if colors:
            bg, fg = colors
            item.setBackground(QBrush(QColor(bg)))
            item.setForeground(QBrush(QColor(fg)))

    # ── Selection / edit-form handling ───────────────────────────────────────

    def _selected_row_values(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        return [self.table.item(row, c).text() if self.table.item(row, c)
                else "" for c in range(self.table.columnCount())]

    def _on_select(self):
        v = self._selected_row_values()
        if not v:
            return
        self._editing_url = v[COL_URL]
        status = v[COL_STATUS] if v[COL_STATUS] in APP_STATUSES else "saved"
        self.status_combo.setCurrentText(status)
        self.notes_edit.clear()
        self.form.show()

    def _hide_form(self):
        self.form.hide()
        self._editing_url = None
        self.table.clearSelection()

    def _update_application(self):
        if not self._editing_url:
            return
        v = self._selected_row_values()
        if not v:
            return
        old_status = v[COL_STATUS]
        new_status = self.status_combo.currentText()
        notes = self.notes_edit.text().strip()
        db.upsert_application(
            self.db_path,
            job_url=self._editing_url,
            company=v[0],
            title=v[1],
            status=new_status,
            notes=notes,
        )
        self._log_change(v[0], v[1], old_status, new_status, notes)
        self.status_message.emit(_("Application updated."))
        self.load_applications()
        self._hide_form()
        self.data_changed.emit()

    def _log_change(self, company, title, old_status, new_status, notes):
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        if old_status == new_status:
            change = _("status {s} (unchanged)").format(s=new_status)
        else:
            change = _("{old} → {new}").format(old=old_status, new=new_status)
        line = f"[{stamp}]  {company} — {title}: {change}"
        if notes:
            line += f"  ·  {notes}"
        self.log.appendPlainText(line)

    def _remove_application(self):
        v = self._selected_row_values()
        if not v:
            QMessageBox.information(self, _("Select a row"),
                                    _("Click a row first, then click Remove."))
            return
        answer = QMessageBox.question(
            self, _("Remove"),
            _("Remove  {title}  at  {company}?").format(title=v[1],
                                                        company=v[0]),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            db.delete_application(self.db_path, v[COL_URL])
            self._hide_form()
            self.load_applications()
            self.data_changed.emit()

    # ── i18n ──────────────────────────────────────────────────────────────
    def retranslate(self):
        self.remove_btn.setText(_("Remove Selected"))
        self.log_label.setText(_("Activity log"))
        self.log.setPlaceholderText(_("Status changes you save appear here."))
        self.form.setTitle(_(" Edit selected "))
        self.save_btn.setText(_("Save"))
        self.cancel_btn.setText(_("Cancel"))
        self.table.setHorizontalHeaderLabels(_header_labels())
