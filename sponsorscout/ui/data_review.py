"""Explicit, asynchronous backed/audited data review; never a startup action."""
from __future__ import annotations
from PySide6.QtCore import Qt,QAbstractTableModel,Signal
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QTableView,QHeaderView,QMessageBox
from sponsorscout.i18n import _
from sponsorscout.ui.async_load import AsyncLoader
from sponsorscout.core.data_review import preview_data_review,apply_data_review


class ReviewModel(QAbstractTableModel):
    def __init__(self,rows,parent=None):super().__init__(parent);self.rows=rows
    def rowCount(self,parent=None):return len(self.rows)
    def columnCount(self,parent=None):return 7
    def data(self,index,role=Qt.DisplayRole):
        if not index.isValid()or role not in(Qt.DisplayRole,Qt.ToolTipRole):return None
        row=self.rows[index.row()]
        labels={'fill_country':_('Fill missing country'),'exclude_non_job':_('Exclude non-job action'),'protected':_('Protected'),'unresolved':_('Still unresolved')}
        values=(labels.get(row.get('action')or row.get('status'),''),row.get('company'),row.get('title'),row.get('location'),row.get('country')or'Unknown',row.get('new_country')or'',row.get('reason'))
        return str(values[index.column()]or'')
    def headerData(self,section,orientation,role=Qt.DisplayRole):
        if role==Qt.DisplayRole and orientation==Qt.Horizontal:
            return [_('Action'),_('Company'),_('Title'),_('Location'),_('Current Country'),_('Proposed Country'),_('Reason')][section]
        return super().headerData(section,orientation,role)


class DataReviewDialog(QDialog):
    busy_changed=Signal(bool)
    changed=Signal()
    def __init__(self,path,parent=None):
        super().__init__(parent);self.path=path;self.plan=None;self._applying=False
        self.reader=AsyncLoader(self,name='explicit data review preview')
        self.writer=AsyncLoader(self,read_only=False,name='explicit data review apply')
        self.setWindowTitle(_('Review data quality'));self.resize(1150,620)
        layout=QVBoxLayout(self)
        info=QLabel(_('Preview only until you confirm. Only unresolved observed countries and confirmed non-job actions are proposed. Manual countries and saved applications are protected. No rows or scan history are deleted.'))
        info.setWordWrap(True);layout.addWidget(info)
        controls=QHBoxLayout();self.company=QLineEdit();self.company.setPlaceholderText(_('Optional company filter (blank = all)'))
        self.preview_btn=QPushButton(_('Preview'));self.preview_btn.clicked.connect(self.preview)
        controls.addWidget(self.company,1);controls.addWidget(self.preview_btn);layout.addLayout(controls)
        self.status=QLabel(_('Ready'));self.status.setWordWrap(True);layout.addWidget(self.status)
        self.table=QTableView();self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        layout.addWidget(self.table,1)
        row=QHBoxLayout();self.apply_btn=QPushButton(_('Apply reviewed changes'));self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self.apply);self.close_btn=QPushButton(_('Close'));self.close_btn.clicked.connect(self.reject)
        row.addWidget(self.apply_btn);row.addStretch();row.addWidget(self.close_btn);layout.addLayout(row)
        self.company.textChanged.connect(self._invalidate)

    def _invalidate(self,*a):
        if not self._applying:
            self.plan=None;self.apply_btn.setEnabled(False);self.reader.invalidate()

    def preview(self):
        if self._applying:return
        self._invalidate();company=self.company.text().strip();self.status.setText(_('Loading preview...'))
        self.reader.request(lambda:preview_data_review(self.path,company),self._show_preview,self._error)

    def _show_preview(self,plan):
        self.plan=plan
        rows=plan['actions']+[dict(row,status='protected')for row in plan['protected']]+[dict(row,status='unresolved')for row in plan['unresolved']]
        self.model=ReviewModel(rows,self);self.table.setModel(self.model)
        for col,width in enumerate((150,160,230,170,130,130,300)):self.table.setColumnWidth(col,width)
        self.status.setText(_('Country fills: {countries}; non-job exclusions: {excluded}; protected: {protected}; still unresolved: {unknown}.').format(countries=plan['country_fills'],excluded=plan['non_job_exclusions'],protected=len(plan['protected']),unknown=len(plan['unresolved'])))
        self.apply_btn.setEnabled(bool(plan['actions']))

    def apply(self):
        if self._applying or not self.plan:return
        question=_('Apply exactly this preview? A full SQLite backup, SHA256 and audit are saved before changes. Non-job records are hidden from active search, not deleted. Historical counters and applications stay unchanged.')
        if QMessageBox.question(self,_('Confirm data review'),question,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        plan=self.plan;self._applying=True;self.busy_changed.emit(True)
        self.apply_btn.setEnabled(False);self.preview_btn.setEnabled(False);self.company.setEnabled(False);self.close_btn.setEnabled(False)
        self.status.setText(_('Saving backup and applying reviewed changes...'))
        self.writer.request(lambda:apply_data_review(self.path,company=plan['company_filter'],plan_sha256=plan['plan_sha256'],confirm=True),self._done,self._error)

    def _unlock(self):
        self._applying=False;self.busy_changed.emit(False)
        self.preview_btn.setEnabled(True);self.company.setEnabled(True);self.close_btn.setEnabled(True)

    def _done(self,result):
        self._unlock();self.plan=None;self.apply_btn.setEnabled(False)
        self.status.setText(_('Changed {count} records. Backup: {backup}\nAudit: {audit}').format(count=result['changed'],backup=result['backup'],audit=result['audit'])+('\n'+result['audit_warning']if result.get('audit_warning')else''))
        self.changed.emit()

    def _error(self,exc):
        self._unlock();self.plan=None;self.apply_btn.setEnabled(False)
        self.status.setText(_('Review not applied: {error}').format(error=exc))

    def reject(self):
        if self._applying:return
        self.reader.close();super().reject()

    def closeEvent(self,event):
        if self._applying:event.ignore();return
        self.reader.close();super().closeEvent(event)
