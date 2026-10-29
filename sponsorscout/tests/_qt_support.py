"""Permanent asynchronous Qt test helpers (never imported by application)."""
import time
_APP = None


def application():
    global _APP
    from PySide6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    return _APP


def wait_until(condition, timeout=8):
    app = application()
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert condition(), 'Asynchronous Qt condition not reached'


def wait_loaders(widget, timeout=8):
    from sponsorscout.ui.async_load import AsyncLoader
    wait_until(lambda: all(not loader.busy for loader in widget.findChildren(AsyncLoader)), timeout)


def notice_widget(path):
    from PySide6.QtWidgets import QWidget
    from sponsorscout.ui.tabs.tools import ToolsTab
    from sponsorscout.ui.async_load import AsyncLoader
    application()
    widget = ToolsTab.__new__(ToolsTab)
    QWidget.__init__(widget)
    widget.db_path = path
    widget._blocked_notice_runs = set()
    widget._blocked_notice_pending = set()
    widget._queue_writer = AsyncLoader(widget, read_only=False, name='notice fixture')
    return widget


def dispose(widget):
    from PySide6.QtCore import QCoreApplication, QEvent
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application().processEvents()
