"""Data Management tab: in-app editor for the ATS / Career seed CSVs.

New capability added with the PySide6 restart (locked decision #2): users
can add, edit, and remove source URLs (companies) directly in the app.
Edits target the per-user seed copies under ~/.sponsorscout/seeds (never
the packaged bundle) and take effect on the next scan.  Validation mirrors
the rules enforced by both scanner scripts (seed_manager.validate_row).
"""

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
    QWidget,
)

from sponsorscout.application import seed_manager as sm
from sponsorscout.i18n import _

# FIX UI-5: the grid was hard-coded to the v6 column list (base + extra, 15
# names). The seeds have 12 columns and none of them is "ats_type", so the
# tab rendered three permanently empty columns (ats_type, seed_name,
# canonical_name) while the value people were looking for -- provider:
# greenhouse / workday / bamboohr -- sat off the right edge. Worse, Save
# wrote those 15 hard-coded names back, silently reordering the file and
# injecting the three empties. The grid now follows the FILE's own header
# and writes it back unchanged.
FALLBACK_COLUMNS = sm.SEED_COLUMNS


class SeedRowDialog(QDialog):
    """Add/edit form for one seed row, grouped into base + advanced fields."""

    def __init__(self, row: dict, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        form = QFormLayout(self)

        self.name_edit = QLineEdit((row.get("name") or "").strip())
        self.ats_combo = QComboBox()
        self.ats_combo.addItems(sm.SUPPORTED_ATS_TYPES)
        # FIX UI-5: this read and wrote the dead "ats_type" key, so a company
        # added here reached the scanner with an EMPTY provider and had to be
        # sniffed over the network -- when the dialog already knew the answer.
        self.ats_combo.setCurrentText(
            (row.get("provider") or "official_careers").strip())
        self.url_edit = QLineEdit((row.get("careers_url") or "").strip())
        self.url_edit.setPlaceholderText("https://…")
        # Best-effort ATS detection while the user types a URL.
        self.url_edit.textChanged.connect(self._autodetect)
        self.industry_edit = QLineEdit((row.get("industry") or "").strip())
        self.score_edits = {}
        form.addRow(_("Company name"), self.name_edit)
        form.addRow(_("Provider"), self.ats_combo)
        form.addRow(_("Careers URL"), self.url_edit)
        form.addRow(_("Industry"), self.industry_edit)
        for col in ("sponsorship_history", "english_friendly", "remote_score"):
            edit = QLineEdit(str(row.get(col) or "").strip())
            edit.setPlaceholderText("0–100")
            self.score_edits[col] = edit
            form.addRow(_(col.replace("_", " ").title()), edit)

        advanced = QGroupBox(_("Advanced (optional)"))
        adv_form = QFormLayout(advanced)
        self.adv_edits = {}
        self.adv_combos = {}
        # provider has its own combo above; seed_name / canonical_name are
        # optional overrides both scanners honour -- offered here, never
        # forced into a file that does not use them.
        for col in (*sm.OPTIONAL_COLUMNS, "target_country",
                    "board_slug", "notes"):
            edit = QLineEdit(str(row.get(col) or "").strip())
            self.adv_edits[col] = edit
            adv_form.addRow(_(col.replace("_", " ").title()), edit)
        source_combo = QComboBox()
        source_combo.addItems(["", *sm.SOURCE_TYPES])
        source_combo.setCurrentText(str(row.get("source_type") or "").strip())
        self.adv_combos["source_type"] = source_combo
        adv_form.addRow(_("Source type"), source_combo)
        scope_combo = QComboBox()
        scope_combo.addItems(["", *sm.SCOPE_POLICIES])
        scope_combo.setCurrentText(str(row.get("scope_policy") or "").strip())
        self.adv_combos["scope_policy"] = scope_combo
        adv_form.addRow(_("Scope policy"), scope_combo)
        form.addRow(advanced)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _autodetect(self, text: str):
        """Pre-fill the ATS type from the URL when the user hasn't chosen."""
        url = text.strip()
        if url.startswith(("http://", "https://")):
            detected = sm.auto_detect_provider(url)
            if detected:
                self.ats_combo.setCurrentText(detected)

    def row(self) -> dict:
        data = {
            "name": self.name_edit.text().strip(),
            "provider": self.ats_combo.currentText().strip(),
            "careers_url": self.url_edit.text().strip(),
            "industry": self.industry_edit.text().strip(),
        }
        for col, edit in self.score_edits.items():
            data[col] = edit.text().strip()
        for col, edit in self.adv_edits.items():
            data[col] = edit.text().strip()
        for col, combo in self.adv_combos.items():
            data[col] = combo.currentText().strip()
        return data

class SeedEditor(QWidget):
    """Editable table over one per-user seed CSV file."""

    data_changed = Signal()

    def __init__(self, path: Path, title: str, parent=None,
                 bundled_path: Path | None = None):
        super().__init__(parent)
        self.path = path
        self.title = title
        self.bundled_path = bundled_path
        self.rows: list[dict] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        info = QHBoxLayout()
        self.file_label = QLabel(str(path))
        self.file_label.setObjectName("MutedLabel")
        info.addWidget(QLabel(_("File:")))
        info.addWidget(self.file_label, 1)
        self.count_label = QLabel("")
        info.addWidget(self.count_label)
        lay.addLayout(info)

        self.columns = list(FALLBACK_COLUMNS)
        self.table = QTableWidget(0, len(self.columns))
        self.table.setHorizontalHeaderLabels(self.columns)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(2, 260)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        # Wide/numerous columns: size-to-content so long field names such
        # as "sponsorship_history" / "english_friendly" are never clipped
        # mid-word (the horizontal scrollbar handles the overflow).
        for col in range(3, self.table.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        lay.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.add_btn = QPushButton(_("Add…"))
        self.add_btn.setObjectName("Primary")
        self.add_btn.clicked.connect(self._add_row)
        self.edit_btn = QPushButton(_("Edit…"))
        self.edit_btn.clicked.connect(self._edit_row)
        self.delete_btn = QPushButton(_("Delete"))
        self.delete_btn.clicked.connect(self._delete_row)
        buttons.addWidget(self.add_btn)
        buttons.addWidget(self.edit_btn)
        buttons.addWidget(self.delete_btn)
        buttons.addStretch(1)
        self.save_btn = QPushButton(_("Save to CSV"))
        self.save_btn.clicked.connect(self.save)
        self.reload_btn = QPushButton(_("Reload"))
        self.reload_btn.clicked.connect(self.load)
        self.reset_btn = QPushButton(_("Reset to bundled defaults"))
        self.reset_btn.setToolTip(_("Discard all edits and restore the seed file "
                               "shipped with the application."))
        self.reset_btn.clicked.connect(self._reset_to_bundled)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.reload_btn)
        buttons.addWidget(self.reset_btn)
        lay.addLayout(buttons)
        self.load()

    # ── i18n ─────────────────────────────────────────────────────────────────
    def retranslate(self):
        self.add_btn.setText(_("Add…"))
        self.edit_btn.setText(_("Edit…"))
        self.delete_btn.setText(_("Delete"))
        self.save_btn.setText(_("Save to CSV"))
        self.reload_btn.setText(_("Reload"))
        self.reset_btn.setText(_("Reset to bundled defaults"))
        self.reset_btn.setToolTip(_("Discard all edits and restore the seed "
                                    "file shipped with the application."))

    # ── Data operations ──────────────────────────────────────────────────────
    def load(self):
        data = sm.read_seed_rows(self.path)
        self.rows = data["rows"]
        self.columns = list(data.get("columns") or FALLBACK_COLUMNS)
        self.table.setColumnCount(len(self.columns))
        self.table.setHorizontalHeaderLabels(self.columns)
        self.file_label.setText(str(self.path))
        # An old personal seed still carrying the removed v6 column is
        # migrated on read; say so rather than let it look like data loss.
        migrated = data.get("migrated") or []
        if migrated:
            QMessageBox.information(
                self, _("Seed file updated"),
                _("This file uses a removed column ({}). It has been read "
                  "correctly and will be written in the current format the "
                  "next time you save.").format(", ".join(migrated)))
        self._populate()

    def _populate(self):
        self.table.setRowCount(len(self.rows))
        bold_cols = {"name", "provider", "careers_url"}
        companies = 0
        for r, row in enumerate(self.rows):
            # FIX UI-5: "# ----- AUSTRIA (23) -----" is a group banner, not a
            # company. It was rendered as an ordinary row and counted as one,
            # which is why this tab claimed 373 companies on a seed holding
            # 328. It now spans the width as a non-selectable header.
            if sm.is_comment_row(row):
                label = (row.get("name") or "").lstrip("# ").strip().strip("-").strip()
                item = QTableWidgetItem(label or "\u2014")
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setFlags(Qt.NoItemFlags)
                self.table.setItem(r, 0, item)
                self.table.setSpan(r, 0, 1, max(len(self.columns), 1))
                continue
            self.table.setSpan(r, 0, 1, 1)
            companies += 1
            for c, col in enumerate(self.columns):
                item = QTableWidgetItem(str(row.get(col, "") or ""))
                if col in bold_cols:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(r, c, item)
        self.count_label.setText(_("{} companies").format(companies))

    def _current_index(self):
        idx = self.table.currentRow()
        if not (0 <= idx < len(self.rows)):
            return None
        # A banner is not editable or deletable; selecting one and pressing
        # Delete used to drop a country header out of the seed.
        return None if sm.is_comment_row(self.rows[idx]) else idx

    def _add_row(self):
        dlg = SeedRowDialog({}, _("Add source"), self)
        if dlg.exec() != QDialog.Accepted:
            return
        row = dlg.row()
        errors = sm.validate_row(row)
        if errors:
            QMessageBox.warning(self, _("Invalid data"), "\n".join(errors))
            return
        key = (row["name"].casefold(), row["careers_url"].casefold())
        if any((r.get("name", "").casefold(), r.get("careers_url", "").casefold()) == key
               for r in self.rows):
            QMessageBox.warning(self, _("Duplicate source"),
                                _("A source with this name and URL already exists."))
            return
        self.rows.append(row)
        self._populate()
        self.table.selectRow(len(self.rows) - 1)
        self.data_changed.emit()

    def _edit_row(self):
        idx = self._current_index()
        if idx is None:
            QMessageBox.information(self, _("No selection"),
                                    _("Select a row to edit first."))
            return
        dlg = SeedRowDialog(self.rows[idx], _("Edit source"), self)
        if dlg.exec() != QDialog.Accepted:
            return
        row = dlg.row()
        errors = sm.validate_row(row)
        if errors:
            QMessageBox.warning(self, _("Invalid data"), "\n".join(errors))
            return
        key = (row["name"].casefold(), row["careers_url"].casefold())
        for other_idx, other in enumerate(self.rows):
            if other_idx == idx:
                continue
            if ((other.get("name", "").casefold(),
                 other.get("careers_url", "").casefold()) == key):
                QMessageBox.warning(self, _("Duplicate source"),
                                    _("A source with this name and URL already exists."))
                return
        self.rows[idx] = row
        self._populate()
        self.table.selectRow(idx)
        self.data_changed.emit()

    def _delete_row(self):
        idx = self._current_index()
        if idx is None:
            QMessageBox.information(self, _("No selection"),
                                    _("Select a row to delete first."))
            return
        name = (self.rows[idx].get("name") or "").strip()
        answer = QMessageBox.question(
            self, _("Delete source"),
            _("Delete '{}' from this seed file? (Not written until you save.)").format(name),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        del self.rows[idx]
        self._populate()
        self.data_changed.emit()

    def save(self):
        """Validate every row, then write the CSV back to the per-user copy."""
        for idx, row in enumerate(self.rows, start=2):
            errors = sm.validate_row(row)
            if errors:
                QMessageBox.warning(
                    self, _("Cannot save"),
                    _("Row {} has problems:").format(idx) + "\n" + "\n".join(errors))
                return
        n = sm.write_seed_rows(self.path, self.columns, self.rows)
        n -= sum(1 for r in self.rows if sm.is_comment_row(r))
        QMessageBox.information(
            self, _("Saved"),
            _("{} companies written to\n{}").format(n, self.path))
        self.data_changed.emit()

    def _reset_to_bundled(self):
        if self.bundled_path is None or not self.bundled_path.exists():
            QMessageBox.warning(self, _("Reset failed"),
                                _("No bundled default file is available."))
            return
        answer = QMessageBox.question(
            self, _("Reset to bundled defaults"),
            _("Discard all edits and restore the seed file shipped with the application?"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        shutil.copyfile(self.bundled_path, self.path)
        self.load()
        self.data_changed.emit()

class DataManagementTab(QWidget):
    """Two seed editors (ATS portals / Career portals) with guidance text."""

    seeds_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Make sure per-user copies exist before the editors read them.
        sm.ensure_user_seeds()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        hint = QLabel(_(
            "Manage the source URLs scanned by SponsorScout.  ATS portals are "
            "scanned via their job-board APIs; career portals are crawled on "
            "the company site.  Edits are saved to your personal seed files "
            "and take effect on the next scan."))
        hint.setWordWrap(True)
        hint.setObjectName("MutedLabel")
        lay.addWidget(hint)

        tabs = QTabWidget()
        self.ats_editor = SeedEditor(sm.user_ats_path(), _("ATS portals"),
                                     bundled_path=sm.bundled_ats_path())
        self.career_editor = SeedEditor(sm.user_career_path(), _("Career portals"),
                                        bundled_path=sm.bundled_career_path())
        tabs.addTab(self.ats_editor, _("ATS portals"))
        tabs.addTab(self.career_editor, _("Career portals"))
        lay.addWidget(tabs, 1)

        self.ats_editor.data_changed.connect(self.seeds_changed)
        self.career_editor.data_changed.connect(self.seeds_changed)

        self._tabs = tabs
        self._hint = hint

    # ── i18n ──────────────────────────────────────────────────────────────
    def retranslate(self):
        self._tabs.setTabText(0, _("ATS portals"))
        self._tabs.setTabText(1, _("Career portals"))
        self.ats_editor.retranslate()
        self.career_editor.retranslate()
