"""SponsorScout main window (PySide6 restart).

Replaces the Tkinter-era monolith.  Each tab is an independent QWidget in
``sponsorscout.ui.tabs``; this file only builds the header, assembles the
5 tabs, and relays cross-tab signals (status bar, language switching,
data refreshed, scan requested) between them.
"""

from __future__ import annotations

import logging
import sys
import time

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from sponsorscout.ui.async_load import AsyncLoader
from sponsorscout.db import database as db
from sponsorscout.i18n import (
    _, get_available_locales, get_locale, get_locale_name, load_saved_locale,
    set_locale,
)
from sponsorscout.paths import DB_PATH
from sponsorscout.ui.tabs import (
    ApplicationsTab, DashboardTab, DataManagementTab, SearchTab, TailoringTab,
    ToolsTab, InsightsTab, AssistedIngestionTab,
)

logger = logging.getLogger(__name__)


# Windows: give the process an explicit AppUserModelID so the taskbar / Start
# menu groups it under OUR icon rather than the generic Python/PyInstaller one.
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "com.sponsorscout.SponsorScout")
    except Exception:  # noqa: BLE001 - cosmetic, best-effort
        pass


class SponsorScoutApp(QMainWindow):
    """Main window: navy header + 5-tab workspace (Dashboard / Search /
    Applications / Tools / Data Management)."""

    def __init__(self, db_path: str | None = None):
        super().__init__()
        load_saved_locale()
        self.db_path = db_path or str(DB_PATH)
        self._dirty_views = set()
        self._closing = False
        self._first_run_loader = AsyncLoader(self, name='first-run summary')
        db.initialize(self.db_path)

        self.setWindowTitle(_("SponsorScout"))
        self.resize(1380, 840)
        self._logo_pixmap = None

        root = QWidget()
        root.setObjectName("AppBody")
        self.setCentralWidget(root)
        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        root_lay.addWidget(self._build_header())
        root_lay.addWidget(self._build_tabs(), 1)

        self._load_icon()

        # Initial data load
        self._refresh_all()
        self.insights_tab.request_auto_refresh()  # cheap dirty check on a reader, not chart SQL
        QTimer.singleShot(500, self._check_first_run)

        # While a scan is in flight the DB is being written to incrementally
        # (commit after every row / every 500 rows).  Poll the coordinator so
        # the Dashboard shows freshly ingested jobs without waiting for the
        # scan to fully finish.  5-second interval (was 2 s): still feels
        # live while halving the GUI-thread query load on slower machines
        # (8 GB, 2-core); the tick also skips work when Dashboard is hidden.
        self._scan_refresh_timer = QTimer(self)
        self._scan_refresh_timer.setInterval(5000)
        self._scan_refresh_timer.timeout.connect(self._on_scan_refresh_tick)
        # Arm the live-refresh loop immediately; _on_scan_refresh_tick checks
        # is_running() so the timer is harmless when no scan is in flight.
        self._scan_refresh_timer.start()

    # ── Scan-live refresh ─────────────────────────────────────────────────────
    def _on_scan_refresh_tick(self):
        """Light-weight periodic refresh while the background scan is alive.

        Only the cheap Dashboard COUNT queries run here.  A full
        ``_refresh_all()`` would also rebuild the Search results table, the
        Applications table and the Tools runs table from scratch every 2
        seconds — thousands of QTableWidgetItem allocations that visibly stall
        a 2-core / 8 GB machine mid-scan.  Every view still receives the
        complete, freshly-ingested data once the scan ends via
        ``_on_scan_finished``.
        """
        try:
            scanning = (self.tools_tab is not None
                        and self.tools_tab.coordinator is not None
                        and self.tools_tab.coordinator.is_running())
            # Only recompute while the user can actually SEE the Dashboard:
            # rebuilding KPI/chart tables for a hidden tab just steals event-
            # loop time from the active tab. A scan ending still triggers a
            # full _refresh_all(), so nothing goes stale on tab switch.
            if scanning and self.tabs.currentWidget() is self.dashboard_tab:
                self.dashboard_tab.refresh()
        except Exception:  # noqa: BLE001 - never let the timer thread die silently
            logger.exception("Scan-live-refresh tick failed")

    # ── Header ────────────────────────────────────────────────────────────────

    # ── Header ──────────────────────────────────────────────────────────────
    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("Header")
        lay = QHBoxLayout(header)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(10)

        self.logo_label = QLabel()
        self.logo_label.setFixedSize(28, 28)
        lay.addWidget(self.logo_label)

        title = QLabel(_("SponsorScout"))
        title.setObjectName("AppTitle")
        lay.addWidget(title)

        self.subtitle = QLabel()
        self.subtitle.setObjectName("AppSubtitle")
        self.subtitle.setTextFormat(Qt.RichText)
        self.subtitle.setText(self._subtitle_text())
        lay.addWidget(self.subtitle)

        lay.addStretch(1)

        # Language selector
        self.lang_combo = QComboBox()
        for code in get_available_locales():
            self.lang_combo.addItem(get_locale_name(code), code)
        idx = self.lang_combo.findData(get_locale())
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        self.lang_combo.currentIndexChanged.connect(self._on_language_change)
        lay.addWidget(self.lang_combo)

        # Status label (right-aligned)
        self.status_label = QLabel(_("Ready"))
        self.status_label.setObjectName("StatusLabel")
        lay.addWidget(self.status_label)

        return header

    def _load_icon(self):
        icon_dir = Path(__file__).resolve().parent.parent / "data" / "icons"
        png = icon_dir / "sponsorscout_256.png"
        try:
            if png.exists():
                pix = QPixmap(str(png))
                if not pix.isNull():
                    self.logo_label.setPixmap(
                        pix.scaled(28, 28, Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation))
                    # Build a multi-size icon so the OS can pick the best
                    # resolution for the taskbar / Start menu / panel.
                    icon = QIcon()
                    for size in (16, 24, 32, 48, 64, 128, 256):
                        p = icon_dir / f"sponsorscout_{size}.png"
                        if p.exists():
                            icon.addFile(str(p))
                    icon.addPixmap(pix)  # fallback
                    self.setWindowIcon(icon)
                    # Set on the application too — required on Windows for the
                    # taskbar icon and on Linux for the panel / app list.
                    app = QApplication.instance()
                    if app is not None:
                        app.setWindowIcon(icon)
        except Exception as exc:  # noqa: BLE001 - icon is cosmetic
            logger.debug("Failed to load icon: %s", exc)

    # ── Tabs ────────────────────────────────────────────────────────────────
    def _build_tabs(self) -> QWidget:
        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        self.dashboard_tab = DashboardTab(self.db_path)
        self.search_tab = SearchTab(self.db_path)
        self.applications_tab = ApplicationsTab(self.db_path)
        self.tools_tab = ToolsTab(self.db_path)
        self.data_tab = DataManagementTab()
        self.tailoring_tab = TailoringTab(self.db_path)
        self.insights_tab = InsightsTab(self.db_path, defer_load=True)
        self.assisted_tab = AssistedIngestionTab(self.db_path)

        tabs.addTab(self.dashboard_tab, _("Dashboard"))
        tabs.addTab(self.search_tab, _("Search"))
        tabs.addTab(self.applications_tab, _("Applications"))
        tabs.addTab(self.tools_tab, _("Tools"))
        tabs.addTab(self.data_tab, _("Data Management"))
        tabs.addTab(self.tailoring_tab, _("AI Prompt Kit"))
        tabs.addTab(self.insights_tab, _("Insights"))
        tabs.addTab(self.assisted_tab, _("Assisted Ingestion"))
        self.tabs = tabs
        tabs.currentChanged.connect(self._on_tab_changed)

        # ── Cross-tab wiring ─────────────────────────────────────────────────
        self.dashboard_tab.refresh_requested.connect(self._refresh_all)
        self.assisted_tab.ingestion_finished.connect(self._on_assisted_ingestion_finished)
        self.assisted_tab.ingestion_started.connect(lambda: self.tools_tab.set_assisted_running(True))
        self.tools_tab.scan_started.connect(lambda: self.assisted_tab.set_scan_running(True))
        self.tools_tab.blocked_portals_changed.connect(self.assisted_tab.refresh_queue)
        self.tools_tab.assisted_tab_requested.connect(lambda: self.tabs.setCurrentWidget(self.assisted_tab))
        self.dashboard_tab.rescan_requested.connect(self._rescan_from_dashboard)

        self.search_tab.application_saved.connect(
            lambda _url: self.applications_tab.load_applications())

        self.tools_tab.data_changed.connect(self._on_tools_data_changed)
        self.tools_tab.scan_finished.connect(self._on_scan_finished)
        self.tools_tab.status_message.connect(self._set_status)

        self.applications_tab.data_changed.connect(self._on_applications_changed)
        self.applications_tab.status_message.connect(self._set_status)
        self.search_tab.job_selected.connect(self.tailoring_tab.set_selected_job)
        self.tailoring_tab.status_message.connect(self._set_status)

        self.data_tab.seeds_changed.connect(lambda: self._set_status(
            _("Seed files changed — they will be used on the next scan.")))

        return tabs

    # ── Actions ─────────────────────────────────────────────────────────────
    def _on_applications_changed(self):
        self._dirty_views.update(('dashboard', 'insights'))
        self._refresh_visible()

    def _on_tab_changed(self, _index):
        self._refresh_visible()

    def _refresh_visible(self):
        views = {'dashboard': self.dashboard_tab, 'search': self.search_tab,
                 'applications': self.applications_tab, 'tools': self.tools_tab,
                 'insights': self.insights_tab, 'assisted': self.assisted_tab}
        current = self.tabs.currentWidget()
        for name, view in views.items():
            if current is not view or name not in self._dirty_views:
                continue
            self._dirty_views.discard(name)
            if name == 'search':
                view.populate_static_filters(refresh_search=True)
            elif name == 'applications':
                view.load_applications()
            elif name == 'assisted':
                view.refresh_queue()
            else:
                if name == 'insights':
                    view._populate_countries()
                view.refresh()

    def _refresh_all(self):
        """Invalidate once; only the visible view requests a worker snapshot."""
        self._dirty_views.update(('dashboard', 'search', 'applications', 'tools', 'insights', 'assisted'))
        self._refresh_visible()

    def _on_tools_data_changed(self):
        self._refresh_all()  # explicit maintenance; scan completion has its own single event
        self.insights_tab.request_auto_refresh()

    def _on_assisted_ingestion_finished(self):
        self.tools_tab.set_assisted_running(False)
        self._refresh_all()
        self.tools_tab.refresh()  # small history read already backgrounded
        self._dirty_views.discard('tools')
        self.insights_tab.request_auto_refresh()

    def _on_scan_finished(self, summary: dict):
        self.assisted_tab.set_scan_running(False)
        self.assisted_tab.refresh_queue()
        self._dirty_views.discard('assisted')
        if self._scan_refresh_timer.isActive():
            self._scan_refresh_timer.stop()
        changed = any(int(summary.get(k, 0) or 0) for k in ('ingested', 'duplicates', 'merged'))
        self._dirty_views.add('dashboard')  # run/empty/error statistics can change with0jobs
        if changed:
            self._dirty_views.update(('search', 'applications', 'insights'))
            self.insights_tab.request_auto_refresh()
        self._dirty_views.discard('tools')  # Tools already requested history once
        self._refresh_visible()
        if summary.get('cancelled'):
            self._set_status(_("Scan stopped."))
        else:
            text = (_("Finished — partial results retained.") if summary.get('status') in ('partial', 'error')
                    else _("Scan complete."))
            self._set_status(text + '  ' + _("{n} jobs ingested").format(n=summary.get('ingested', 0)))

    def _rescan_from_dashboard(self):
        """Dashboard 'Rescan Companies' → Tools scan (single scan mode)."""
        self.tabs.setCurrentWidget(self.tools_tab)
        self.tools_tab.start_scan()

    def _set_status(self, msg: str):
        self.status_label.setText(msg)

    # ── Language switching ──────────────────────────────────────────────────
    def _subtitle_text(self):
        """Return the header subtitle, with "Per Hamliee ❤ !!" bold + larger
        font highlighted in the Italian version (single contiguous line)."""
        subtitle_text = _("Verified sponsorship-focused jobs from official career pages "
                          "and ATS boards")
        if get_locale() == "it":
            subtitle_text = subtitle_text.replace(
                "Per Hamliee \u2764 !!",
                '<b><span style="font-size: 14pt;">Per Hamliee \u2764 !!</span></b>')
        return subtitle_text

    def _on_language_change(self):
        code = self.lang_combo.currentData()
        if code and code != get_locale():
            set_locale(code)
            self.retranslate()
            self._set_status(_("Language: ") + get_locale_name(code))

    def retranslate(self):
        self.setWindowTitle(_("SponsorScout"))
        self.subtitle.setText(self._subtitle_text())
        self.tabs.setTabText(0, _("Dashboard"))
        self.tabs.setTabText(1, _("Search"))
        self.tabs.setTabText(2, _("Applications"))
        self.tabs.setTabText(3, _("Tools"))
        self.tabs.setTabText(4, _("Data Management"))
        self.tabs.setTabText(5, _("AI Prompt Kit"))
        self.tabs.setTabText(6, _("Insights"))
        self.tabs.setTabText(7, _("Assisted Ingestion"))
        for tab in (self.dashboard_tab, self.search_tab,
                    self.applications_tab, self.tools_tab, self.data_tab,
                    self.tailoring_tab, self.insights_tab, self.assisted_tab):
            retranslate = getattr(tab, "retranslate", None)
            if callable(retranslate):
                retranslate()

    def closeEvent(self,event):
        # Do not destroy a live QObject/QThread while it is committing jobs.
        if getattr(self.tools_tab,'_review_busy',False):
            self._set_status(_("Data review is applying. Wait for the backup/audit result before closing."))
            event.ignore()
            return
        if self.tools_tab.coordinator.is_running():
            self.tools_tab.coordinator.stop()
            self._set_status(_("Stopping scan. Close the application again after the scan has stopped."))
            event.ignore()
            return
        # Never wait on the GUI thread for a network/SQLite worker to stop.
        assisted_idle = self.assisted_tab.shutdown(wait_ms=0)
        insights_idle = self.insights_tab.shutdown(wait_ms=0)
        if self.tools_tab._queue_writer.busy:
            self._set_status(_("Stopping background work. Saved batches are retained; close again when it finishes."))
            event.ignore()
            return
        if not assisted_idle or not insights_idle:
            self._set_status(_("Stopping background work. Saved batches are retained; close again when it finishes."))
            event.ignore()
            return
        self._closing = True
        self._first_run_loader.close()
        for view in (self.dashboard_tab, self.search_tab, self.applications_tab, self.assisted_tab):
            for name in ('_loader', '_filter_loader', '_search_loader', '_queue_loader'):
                loader = getattr(view, name, None)
                if loader is not None:
                    loader.close()
        super().closeEvent(event)

    # ── First-run ───────────────────────────────────────────────────────────
    def _check_first_run(self):
        if not self._closing and not self.tools_tab.coordinator.is_running():
            path = self.db_path
            self._first_run_loader.request(lambda: db.get_dashboard_stats(path), self._apply_first_run)

    def _apply_first_run(self, stats):
        if self._closing or self.tools_tab.coordinator.is_running() or self.assisted_tab._worker is not None:
            return
        try:
            if stats.get("companies", 0) == 0 and stats.get("verified_jobs", 0) == 0:
                answer = QMessageBox.question(
                    self, _("Welcome to SponsorScout"),
                    _("No data yet.\n\nRun the first scan now? It fetches jobs "
                      "from each company's official career page and ATS board "
                      "(1–3 minutes)."),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if answer == QMessageBox.Yes:
                    self._rescan_from_dashboard()
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Failed to determine whether this is the first run: %s", exc)