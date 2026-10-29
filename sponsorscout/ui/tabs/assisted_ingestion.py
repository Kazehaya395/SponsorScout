"""Explicit-only assisted ingestion with the persistent, actually blocked queue."""
from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Qt, Signal, QThread, Slot
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from sponsorscout.ui.async_load import AsyncLoader
from sponsorscout.i18n import _
from sponsorscout.db import database as db
from sponsorscout.core.job_identity import portal_queue_key

logger = logging.getLogger(__name__)


def _outcome(count, error, report):
    pages, duplicates = report.get('pages_visited', 0), report.get('duplicates', 0)
    reason = str(report.get('stop_reason') or error or '')
    complete = bool(report.get('completed')) and not error and not report.get('persistence_errors')
    if error:
        return 'failed', _("Failed: {error}").format(error=error), False
    total=report.get('harvested',count+duplicates)
    resolved=report.get('country_resolved',0)
    message = _("Observed {total} valid vacancies: {count} new, {duplicates} refreshed. Listing pages/batches: {pages}; browser read passes: {loads}. Country resolved: {resolved}; unresolved: {unknown}. {reason}").format(
        total=total,count=count,duplicates=duplicates,pages=pages,loads=report.get('loads',pages),
        resolved=resolved,unknown=report.get('country_unresolved',max(0,total-resolved)),reason=reason)
    if report.get('resumed_details'):
        message=_("Resumed JD enrichment for {total} saved vacancies. No listing pages were crawled. {reason}").format(total=total,reason=reason)
    coverage=report.get('jd_coverage')
    if coverage is not None:
        message+='\n'+_("JD text: {readable}/{total} readable; not attempted: {unattempted}; reused: {reused}; blocked: {blocked}; failed/thin/empty: {unreadable}; budget skipped: {skipped}; deferred/protected: {protected}. Readable JDs with Unknown support verdict: {unknown}.").format(
            readable=coverage.get('readable',0),total=coverage.get('observed',0),
            unattempted=coverage.get('unattempted',0),protected=coverage.get('protected',0),
            reused=report.get('session_detail_activity',{}).get('reused',0),blocked=coverage.get('blocked',0),
            unreadable=sum(coverage.get(key,0)for key in ('failed','thin','empty','truncated')),
            skipped=coverage.get('budget_skipped',0),unknown=coverage.get('readable_unknown_support',0))
    activity=report.get('session_detail_activity')
    if activity is not None:
        message+='\n'+_("Detail transport: HTTP requests {http}; HTTP access walls {walls}; browser attempts {browser}; preferred-browser reads {preferred}; browser retries {retries}.").format(
            http=activity.get('http',0),walls=activity.get('http_blocked',0),browser=activity.get('browser',0),
            preferred=activity.get('browser_preferred',0),retries=activity.get('browser_retries',0))
    if 'detail_parallel_tabs' in report:
        message+='\n'+_("Performance: up to {tabs} parallel detail tabs; {prefetched} JDs pre-navigated; HTTP re-probe cleared {cleared} origin(s).").format(
            tabs=report.get('detail_parallel_tabs',3),prefetched=report.get('parallel_prefetch',0),
            cleared=report.get('http_reprobe_cleared',0))
    if report.get('resumed_listing'):
        message+='\n'+_('Listing collection continued from recorded state; earlier pages were not restarted.')
    if 'verification_cleared'in report:
        message+='\n'+_('Accepted clearance transitions: {cleared}; deferred by an access gate: {deferred}. A detector timeout is not a claim that you did not solve a check.').format(cleared=report['verification_cleared'],deferred=report.get('access_deferred',0))
    if report.get('resume_checkpoint_error'):
        message+='\n'+_("Resume checkpoint could not be saved: {error}").format(error=report['resume_checkpoint_error'])
    return ('resolved' if complete else 'partial'), message, complete


class AssistedWorker(QThread):
    finished_signal = Signal(int,str)  # retained for integrations; UI waits for QThread.finished
    progress_signal = Signal(str)
    queue_signal = Signal(object)
    attention_signal = Signal(object)

    def __init__(self,url: str,seed_name: str,db_path: str,max_pages: int = 500,parent=None,source_subtype='direct',resume_details=False,resume_listing=False,resume_run_id=''):
        super().__init__(parent)
        self.url,self.seed_name,self.db_path = url,seed_name,db_path
        self.max_pages = max_pages
        self.source_subtype = source_subtype
        self.resume_details = bool(resume_details)
        self.resume_listing,self.resume_run_id=bool(resume_listing),str(resume_run_id)
        from sponsorscout.core.assisted_control import AssistedControl
        self.control=AssistedControl(self.attention_signal.emit)
        self.count,self.error,self.report = 0,'',{}
        self.cancel_event = threading.Event()

    def run(self):
        from sponsorscout.core import blocked_portals
        blocked_portals.LAST_INGESTION_REPORT.clear()
        conn = None
        self.queue_error, self.queue_rows = '', None
        try:
            # Registration/status writes must not delay a GUI button callback.
            rows = db.list_blocked_portals(self.db_path, include_resolved=True)
            if not any(row['url_key'] == portal_queue_key(self.url) for row in rows):
                db.enqueue_blocked_portals(self.db_path, [{'url': self.url, 'seed_name': self.seed_name,
                    'phase': 'assisted', 'reason': _('Manually requested assisted scan'),
                    'source_subtype': self.source_subtype}])
            db.update_blocked_portal(self.db_path, self.url, status='running', result='')
            self.queue_signal.emit([dict(row) for row in db.list_blocked_portals(self.db_path)])
            if self.cancel_event.is_set():
                self.report = dict(cancelled=True, completed=False, stop_reason='cancelled by user', pages_visited=0, duplicates=0)
            else:
                conn = db.get_connection(self.db_path)
                self.count = blocked_portals.run_assisted_ingestion(
                    self.url, self.seed_name, conn, max_pages=self.max_pages,
                    db_path=self.db_path, progress_callback=self.progress_signal.emit,
                    cancel_event=self.cancel_event, source_subtype=self.source_subtype,
                    **(dict(resume_details=self.resume_details,resume_listing=self.resume_listing,resume_run_id=self.resume_run_id,control=self.control)))
                self.report = dict(blocked_portals.LAST_INGESTION_REPORT or {})
        except Exception as exc:
            logger.exception('Assisted ingestion error')
            self.error = str(exc)
            self.report = dict(blocked_portals.LAST_INGESTION_REPORT or self.report)
        finally:
            if conn is not None:
                conn.close()
            status, message, _complete = _outcome(self.count, self.error, self.report)
            try:
                db.update_blocked_portal(self.db_path, self.url, status=status,
                    pages=self.report.get('pages_visited', 0), jobs=self.count+self.report.get('duplicates', 0), result=message)
                self.queue_rows = [dict(row) for row in db.list_blocked_portals(self.db_path)]
            except Exception as exc:
                self.queue_error = str(exc)
                logger.exception('Could not save assisted queue outcome')
        self.finished_signal.emit(self.count, self.error)


class AssistedIngestionTab(QWidget):
    """No launch at refresh/startup. Exactly one explicitly requested worker."""
    ingestion_started = Signal()
    ingestion_finished = Signal()

    def __init__(self,db_path: str,parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self._queue_loader = AsyncLoader(self, name='blocked queue read')
        self._batch_loader=AsyncLoader(self,name='selected saved batches')
        self._batch_scope='';self._batch_rows=[];self._attention_waiting=False
        self._worker: AssistedWorker | None = None
        self._scan_running = False
        self._closing = False
        self._queue_rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12,12,12,12)
        layout.setSpacing(12)

        info = QGroupBox(_("Assisted Browser Ingestion"))
        info_layout = QVBoxLayout(info)
        description = QLabel(_("Regular scans never open an interactive browser. Real verification-blocked portals from ATS and Career scans appear below and survive restart. Choose a portal explicitly, then complete any human verification in Chromium."))
        description.setWordWrap(True)
        info_layout.addWidget(description)
        layout.addWidget(info)

        portal_box = QGroupBox(_("Launch Assisted Ingestion for Portal"))
        portal_layout = QVBoxLayout(portal_box)
        inputs = QHBoxLayout()
        inputs.addWidget(QLabel(_("Company / Seed Name:")))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("Company or portal name"))
        inputs.addWidget(self.name_edit)
        self.source_type_combo = QComboBox()
        self.source_type_combo.addItem(_('Direct employer'),'direct')
        self.source_type_combo.addItem(_('Recruiter / agency'),'recruiter')
        inputs.addWidget(self.source_type_combo)
        inputs.addWidget(QLabel(_("Portal Careers URL:")))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText('https://careers.example/jobs')
        inputs.addWidget(self.url_edit,1)
        portal_layout.addLayout(inputs)
        buttons = QHBoxLayout()
        self.launch_btn = QPushButton(_("Launch Assisted Browser and Ingest"))
        self.launch_btn.setObjectName('Primary')
        self.launch_btn.clicked.connect(self._start_ingestion)
        buttons.addWidget(self.launch_btn)
        self.resume_btn = QPushButton(_("Resume unread JDs"))
        self.resume_btn.setToolTip(_("Continue the saved batch for this exact portal URL; keep fresh proven JDs and do not crawl listing pages again."))
        self.resume_btn.clicked.connect(lambda checked=False:self._start_ingestion(resume_details=True))
        buttons.addWidget(self.resume_btn)
        self.resume_listing_btn=QPushButton(_('Resume listing collection'))
        self.resume_listing_btn.setToolTip(_('Continue only from an actually recorded listing URL; this is separate from unread JD continuation.'))
        self.resume_listing_btn.clicked.connect(lambda checked=False:self._start_ingestion(resume_listing=True))
        buttons.addWidget(self.resume_listing_btn)
        self.open_ext_btn = QPushButton(_("Open in External Browser"))
        self.open_ext_btn.clicked.connect(self._open_external)
        buttons.addWidget(self.open_ext_btn)
        self.stop_btn = QPushButton(_("Stop assisted ingestion"))
        self.stop_btn.clicked.connect(self.cancel_ingestion)
        self.stop_btn.setEnabled(False)
        buttons.addWidget(self.stop_btn)
        buttons.addStretch(1)
        portal_layout.addLayout(buttons)
        saved_row=QHBoxLayout()
        saved_row.addWidget(QLabel(_('Saved batch:')))
        self.batch_combo=QComboBox();self.batch_combo.addItem(_('Latest saved batch'),'')
        saved_row.addWidget(self.batch_combo,1)
        self.batch_load_btn=QPushButton(_('Load saved batches'))
        self.batch_load_btn.clicked.connect(self.load_saved_batches);saved_row.addWidget(self.batch_load_btn)
        portal_layout.addLayout(saved_row)
        self.name_edit.textChanged.connect(self._invalidate_batches)
        self.url_edit.textChanged.connect(self._invalidate_batches)
        self.gate_lbl=QLabel(_('Verification is performed by you in Chromium; Continue only requests a fresh content check.'))
        self.gate_lbl.setWordWrap(True);portal_layout.addWidget(self.gate_lbl)
        gate_row=QHBoxLayout()
        self.show_browser_btn=QPushButton(_('Show verification browser'))
        self.continue_btn=QPushButton(_('Continue after verification'))
        self.save_partial_btn=QPushButton(_('Save partial and close'))
        self.show_browser_btn.clicked.connect(lambda:self._send_gate('show_browser'))
        self.continue_btn.clicked.connect(lambda:self._send_gate('continue_check'))
        self.save_partial_btn.clicked.connect(lambda:self._send_gate('save_partial'))
        for button in(self.show_browser_btn,self.continue_btn,self.save_partial_btn):button.setEnabled(False);gate_row.addWidget(button)
        gate_row.addStretch(1);portal_layout.addLayout(gate_row)
        self.status_lbl = QLabel(_("Ready"))
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet('color: #4B5563; font-weight: bold;')
        portal_layout.addWidget(self.status_lbl)
        layout.addWidget(portal_box)

        queue_box = QGroupBox(_("Blocked portals from your scans"))
        queue_layout = QVBoxLayout(queue_box)
        queue_controls = QHBoxLayout()
        self.queue_count_lbl = QLabel()
        queue_controls.addWidget(self.queue_count_lbl)
        queue_controls.addStretch(1)
        self.show_resolved = QCheckBox(_("Show resolved portals"))
        self.show_resolved.toggled.connect(self.refresh_queue)
        queue_controls.addWidget(self.show_resolved)
        refresh = QPushButton(_("Refresh queue"))
        refresh.clicked.connect(self.refresh_queue)
        queue_controls.addWidget(refresh)
        queue_layout.addLayout(queue_controls)
        self.portals_table = QTableWidget(0,5)
        self.portals_table.setHorizontalHeaderLabels(
            [_("Company"),_("Target URL"),_("Reason / Phase"),_("Status"),_("Action")])
        self.portals_table.verticalHeader().setVisible(False)
        header = self.portals_table.horizontalHeader()
        header.setSectionResizeMode(0,QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1,QHeaderView.Stretch)
        header.setSectionResizeMode(2,QHeaderView.Interactive)
        header.resizeSection(2,240)
        header.setSectionResizeMode(3,QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4,QHeaderView.Interactive)
        probe = QPushButton(_("Load and Ingest"))
        metrics = QFontMetrics(probe.font())
        header.resizeSection(4,max(metrics.horizontalAdvance(probe.text())+40,170))
        self.portals_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.portals_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.portals_table.itemSelectionChanged.connect(self._load_selected_inputs)
        queue_layout.addWidget(self.portals_table)
        layout.addWidget(queue_box,1)
        self._populate_known_portals()

    def _populate_known_portals(self):
        # Compatibility method name, but there are NO static demo portals.
        self.refresh_queue()

    def refresh_queue(self, *_args):
        path, include = self.db_path, self.show_resolved.isChecked()
        self._queue_loader.request(
            lambda: [dict(row) for row in db.list_blocked_portals(path, include_resolved=include)],
            self._apply_queue, self._queue_read_error)

    def _queue_read_error(self, exc):
        self.status_lbl.setText(_("Could not load blocked queue: {error}").format(error=exc))
        logger.warning('Could not load blocked queue: %s', exc)

    def _accept_worker_queue(self, rows):
        self._queue_loader.invalidate()  # older pending/read snapshots must not regress running/result status
        self._apply_queue(rows)

    def _apply_queue(self, rows):
        selected = self.portals_table.currentRow()
        selected_key = self._queue_rows[selected].get('url_key') if 0 <= selected < len(self._queue_rows) else None
        self._queue_rows = [row for row in rows if self.show_resolved.isChecked() or row['status'] not in ('resolved', 'dismissed')]
        self.portals_table.setRowCount(len(self._queue_rows))
        status_labels = {'pending':_("Pending"),'running':_("Running"),'partial':_("Partial"),
                         'resolved':_("Resolved"),'failed':_("Failed"),'dismissed':_("Dismissed")}
        for index,row in enumerate(self._queue_rows):
            reason = f"{row['phase'].upper()}: {row['reason']}".strip(': ')
            status = status_labels.get(row['status'],row['status'])
            if row['status']=='running' and self._worker is None:
                status = _('Interrupted — retry explicitly')
            if row.get('pages') or row.get('jobs'):
                status += ' — ' + _("{jobs} jobs / {pages} pages").format(jobs=row['jobs'],pages=row['pages'])
            values = [row['seed_name'],row['url'],reason,status]
            for column,value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(row.get('result') or value) if column==3 else str(value))
                self.portals_table.setItem(index,column,item)
            button = QPushButton(_("Load and Ingest"))
            fm = QFontMetrics(button.font())
            button.setMinimumWidth(fm.horizontalAdvance(button.text())+28)
            button.clicked.connect(lambda checked=False,r=dict(row): self._select_portal(r['seed_name'],r['url'],r.get('source_subtype','direct')))
            self.portals_table.setCellWidget(index,4,button)
        if selected_key is not None:
            for index, row in enumerate(self._queue_rows):
                if row.get('url_key') == selected_key:
                    self.portals_table.selectRow(index)
                    break
        self.queue_count_lbl.setText(_("{count} queued portals").format(count=len(self._queue_rows)))
        self._set_controls()

    def set_scan_running(self,running: bool):
        self._scan_running = bool(running)
        self._set_controls()

    def _set_controls(self):
        busy = self._worker is not None
        enabled = not busy and not self._scan_running
        self.launch_btn.setEnabled(enabled)
        self.open_ext_btn.setEnabled(enabled)
        self.resume_btn.setEnabled(enabled)
        self.resume_listing_btn.setEnabled(enabled)
        self.batch_combo.setEnabled(enabled);self.batch_load_btn.setEnabled(enabled)
        for button in(self.show_browser_btn,self.continue_btn,self.save_partial_btn):button.setEnabled(busy and self._attention_waiting)
        self.name_edit.setEnabled(not busy)
        self.source_type_combo.setEnabled(not busy)
        self.url_edit.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)
        for index in range(self.portals_table.rowCount()):
            button = self.portals_table.cellWidget(index,4)
            if button is not None:
                button.setEnabled(enabled)

    def _invalidate_batches(self,*_args):
        self._batch_loader.invalidate();self._batch_scope='';self._batch_rows=[]
        self.batch_combo.clear();self.batch_combo.addItem(_('Latest saved batch'),'')

    def load_saved_batches(self):
        if self._worker is not None or self._scan_running:return
        url,name,path=self.url_edit.text().strip(),self.name_edit.text().strip(),self.db_path
        key=portal_queue_key(url)
        if not key:return
        def load():
            from sponsorscout.core.assisted_resume import list_batches
            with db.get_connection(path)as conn:return list_batches(conn,url,name)
        def apply(rows):
            if key!=portal_queue_key(self.url_edit.text().strip())or name!=self.name_edit.text().strip():return
            self._batch_scope=key;self._batch_rows=rows;self._render_batches()
        self._batch_loader.request(load,apply,lambda exc:self.status_lbl.setText(_('Could not load saved batches: {error}').format(error=exc)))

    def _render_batches(self):
        selected=self.batch_combo.currentData()
        self.batch_combo.clear();self.batch_combo.addItem(_('Latest saved batch'),'')
        for row in self._batch_rows:
            state=_('Listing complete')if row['listing_completed']else _('Listing incomplete')
            self.batch_combo.addItem(_('{run}: {rows} saved jobs / {pages} pages — {state}').format(run=row['run_id'],rows=row['rows'],pages=row['pages'],state=state),row['run_id'])
        index=self.batch_combo.findData(selected)
        if index>=0:self.batch_combo.setCurrentIndex(index)

    def _send_gate(self,command):
        worker=self._worker
        if worker is None or not self._attention_waiting:return
        getattr(worker.control,command)()
        if command=='continue_check':self.gate_lbl.setText(_('Checking current visible content; a button click does not certify verification.'))
        elif command=='save_partial':self.gate_lbl.setText(_('Saving partial results and closing the verification session...'))

    def _on_attention(self,info):
        if self._worker is None:return
        self._attention_waiting=bool(info.get('waiting'))
        if self._attention_waiting:
            label=_('Awaiting your verification in Chromium. You may continue checking this page or save partial results.')
        elif info.get('url'):
            label=_('Current content check finished; authentic primary content is still required.')
        else:
            # close() sends an empty URL; do not leave a stale "still required"
            # warning on a session that has already finished.
            label=_('Verification controls are inactive.')
        self.gate_lbl.setText(label)
        self.gate_lbl.setStyleSheet('color: #D97706; font-weight: bold;'if self._attention_waiting else'')
        self._set_controls()

    def _load_selected_inputs(self):
        if self._worker is not None or self._scan_running:return
        index=self.portals_table.currentRow()
        if 0<=index<len(self._queue_rows):
            row=self._queue_rows[index]
            self.name_edit.setText(row['seed_name']);self.url_edit.setText(row['url'])
            self.source_type_combo.setCurrentIndex(1 if row.get('source_subtype')=='recruiter'else 0)

    def _select_portal(self,comp: str,url: str,source_subtype='direct'):
        if self._worker is not None or self._scan_running:
            return
        self.name_edit.setText(comp)
        self.source_type_combo.setCurrentIndex(1 if source_subtype=='recruiter' else 0)
        self.url_edit.setText(url)
        self._start_ingestion()  # explicit row action only

    def _open_external(self):
        if self._worker is not None or self._scan_running:
            return
        url = self.url_edit.text().strip()
        if not portal_queue_key(url):
            QMessageBox.warning(self,_("Missing URL"),_("Please provide a valid HTTP or HTTPS portal URL."))
            return
        from sponsorscout.core.blocked_portals import open_in_default_browser
        open_in_default_browser(url)

    def _start_ingestion(self,resume_details=False,resume_listing=False):
        if self._worker is not None and self._worker.isRunning():
            return
        if self._worker is not None or self._scan_running:
            return
        url = self.url_edit.text().strip()
        name = self.name_edit.text().strip() or _("Career Portal")
        if not portal_queue_key(url):
            QMessageBox.warning(self,_("Missing URL"),_("Please provide a valid HTTP or HTTPS portal URL."))
            return
        self.status_lbl.setStyleSheet('color: #2563EB; font-weight: bold;')
        self.status_lbl.setText(_("Opening interactive browser. Complete human verification if requested."))
        worker = AssistedWorker(url,name,self.db_path,max_pages=500,parent=self,
                                source_subtype=self.source_type_combo.currentData(),resume_details=bool(resume_details),
                                resume_listing=bool(resume_listing),resume_run_id=(self.batch_combo.currentData()or'')if self._batch_scope==portal_queue_key(url)else'')
        self._worker = worker
        worker.progress_signal.connect(self._on_ingestion_progress)
        worker.attention_signal.connect(self._on_attention)
        worker.queue_signal.connect(self._accept_worker_queue)
        worker.finished.connect(self._on_worker_finished)
        self._set_controls()
        self.refresh_queue()
        self.ingestion_started.emit()
        worker.start()

    def cancel_ingestion(self):
        if self._worker is not None:
            self._worker.cancel_event.set()
            self.stop_btn.setEnabled(False)
            self.status_lbl.setText(_("Stopping assisted ingestion... Saved jobs are retained."))

    def _on_ingestion_progress(self,msg: str):
        attention = any(token in msg.casefold() for token in ('action needed','azione richiesta','verification','verifica'))
        self.status_lbl.setStyleSheet('color: ' + ('#D97706' if attention else '#2563EB') + '; font-weight: bold;')
        self.status_lbl.setText(msg)

    @Slot()
    def _on_worker_finished(self):
        worker = self._worker
        if worker is not None:
            self._on_ingestion_done(worker.count, worker.error)

    def _on_ingestion_done(self, count: int, err: str):
        worker = self._worker
        if worker is None:
            return
        report = dict(worker.report or {})
        _status, message, complete = _outcome(count, err, report)
        queue_error = getattr(worker, 'queue_error', '')
        if queue_error:
            message += '\n' + _("Could not save queue outcome: {error}").format(error=queue_error)
        self.status_lbl.setText(message)
        rows = getattr(worker, 'queue_rows', None)
        self._worker = None
        self._attention_waiting=False
        worker.deleteLater()
        self._set_controls()
        if rows is not None:
            self._accept_worker_queue(rows)
        else:
            self.refresh_queue()
        # Also emit for 0-new refreshes, partial writes and failures, so the
        # other tabs unlock and show every successfully committed job.
        self.ingestion_finished.emit()
        if not self._closing:
            coverage = report.get('jd_coverage') or {}
            missing = sum(coverage.get(key, 0) for key in (
                'unattempted', 'empty', 'thin', 'failed', 'blocked',
                'budget_skipped', 'truncated', 'protected'))
            if not complete and not err and missing:
                message += '\n' + _("Some full job descriptions were not read. Use Resume unread JDs to continue; no Unknown support verdict has been converted to No.")
            title = (_("Ingestion Complete") if complete else
                     _("Ingestion Partial") if not err and missing else
                     _("Ingestion Incomplete"))
            QMessageBox.information(self, title, message)

    def retranslate(self):
        from sponsorscout.i18n import retranslate_widget_literals
        retranslate_widget_literals(self)
        # Historical '&'/'and' aliases share an Italian value; keep this
        # control's exact canonical key instead of reverse-inferring it.
        self.launch_btn.setText(_('Launch Assisted Browser and Ingest'))
        self.resume_btn.setText(_('Resume unread JDs'))
        self.resume_listing_btn.setText(_('Resume listing collection'))
        self.batch_load_btn.setText(_('Load saved batches'))
        self.show_browser_btn.setText(_('Show verification browser'))
        self.continue_btn.setText(_('Continue after verification'))
        self.save_partial_btn.setText(_('Save partial and close'))
        self._render_batches()
        self.name_edit.setPlaceholderText(_('Company or portal name'))
        self.source_type_combo.setItemText(0,_('Direct employer'))
        self.source_type_combo.setItemText(1,_('Recruiter / agency'))
        self.portals_table.setHorizontalHeaderLabels(
            [_('Company'),_('Target URL'),_('Reason / Phase'),_('Status'),_('Action')])
        self.refresh_queue()  # data only; never starts a worker
        probe=QPushButton(_('Load and Ingest'))
        width=QFontMetrics(probe.font()).horizontalAdvance(probe.text())+48
        self.portals_table.horizontalHeader().resizeSection(4,max(170,width))

    def shutdown(self,wait_ms=3000):
        self._closing = True
        self._queue_loader.close()
        self._batch_loader.close()
        self.cancel_ingestion()
        idle = self._worker is None or self._worker.wait(wait_ms)
        if idle and self._worker is not None:
            # Persist the queue outcome even if the event loop has not yet
            # delivered QThread.finished while the window is closing.
            self._on_ingestion_done(self._worker.count,self._worker.error)
        return idle
