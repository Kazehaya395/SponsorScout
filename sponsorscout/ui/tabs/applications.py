"""Applications tab: saved-application pipeline tracker.

Ported from the tkinter Applications tab (tree of saved applications with
an inline status/notes edit form) to PySide6, preserving behavior:
selecting a row reveals the edit form; Save updates status + notes;
Remove deletes the row after confirmation.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from sponsorscout.db import database as db
from sponsorscout.i18n import _

APP_STATUSES = ["saved", "applied", "interview", "offer", "rejected"]
# Canonical column order (defines the indexes); only the visible labels are
# translated — see _header_labels().
HEADERS = ["Company", "Title", "Status", "Saved on", "URL"]


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
        self._editing_url = None

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
        self.refresh_btn = QPushButton(_("↻ Refresh"))
        self.refresh_btn.clicked.connect(self.load_applications)
        tb.addWidget(self.refresh_btn)
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
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 170)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 130)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._on_select)
        root.addWidget(self.table, 1)

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

    # ── Data loading ─────────────────────────────────────────────────────────

    def load_applications(self):
        rows = db.list_applications(self.db_path)
        self.table.setRowCount(0)
        for r in rows:
            company, title, status, applied_at, _f, _n, job_url = r[:7]
            row = self.table.rowCount()
            self.table.insertRow(row)
            saved_on = (applied_at or "")[:10]
            for col, val in enumerate(
                    (company, title, status, saved_on, job_url)):
                self.table.setItem(
                    row, col, QTableWidgetItem(str(val or "")))

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
        self._editing_url = v[4]
        status = v[2] if v[2] in APP_STATUSES else "saved"
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
        db.upsert_application(
            self.db_path,
            job_url=self._editing_url,
            company=v[0],
            title=v[1],
            status=self.status_combo.currentText(),
            notes=self.notes_edit.text().strip(),
        )
        self.status_message.emit(_("Application updated."))
        self.load_applications()
        self._hide_form()
        self.data_changed.emit()

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
            db.delete_application(self.db_path, v[4])
            self._hide_form()
            self.load_applications()
            self.data_changed.emit()

    # ── i18n ──────────────────────────────────────────────────────────────
    def retranslate(self):
        self.remove_btn.setText(_("Remove Selected"))
        self.refresh_btn.setText(_("↻ Refresh"))
        self.form.setTitle(_(" Edit selected "))
        self.save_btn.setText(_("Save"))
        self.cancel_btn.setText(_("Cancel"))
        self.table.setHorizontalHeaderLabels(
            [_("Company"), _("Title"), _("Status"), _("Saved on"),
             _("URL")])
