"""AI Tailoring tab: clipboard text, JD reading errors, saved documents."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sponsorscout.db import database as db
from sponsorscout.ui.tabs import tailoring as T


# ── clipboard text ──────────────────────────────────────────────────────────

def test_copy_text_order_prompt_jd_cv_cl():
    text = T.build_clipboard_text("PROMPT", "JD", "CV", "CL")
    assert text == "PROMPT\n\nJD\n\nCV\n\nCL"


def test_cover_letter_is_optional():
    assert T.build_clipboard_text("P", "J", "C", "   ") == "P\n\nJ\n\nC"


@pytest.mark.parametrize("args, missing", [
    (("", "J", "C", ""), "AI prompt"),
    (("P", "  ", "C", ""), "Job description"),
    (("P", "J", "", ""), "CV"),
])
def test_required_parts_are_named_when_empty(args, missing):
    with pytest.raises(ValueError) as exc:
        T.build_clipboard_text(*args)
    assert missing in str(exc.value)


def test_no_length_limit_on_copied_text():
    big = "x" * 300_000
    text = T.build_clipboard_text("P", big, big, big)
    assert len(text) >= 900_000


# ── HTML to text ────────────────────────────────────────────────────────────

def test_html_to_text_keeps_content_and_drops_scripts_and_nav():
    page = """<html><head><title>T</title><style>.a{}</style></head>
    <body><nav>Home | Jobs</nav><script>var x=1;</script>
    <h1>Account Manager</h1><p>We need &amp; you &nbsp;have fun.</p>
    <ul><li>Sell B2B software</li></ul></body></html>"""
    text = T.html_to_text(page)
    assert "Account Manager" in text
    assert "We need & you have fun." in text
    assert "Sell B2B software" in text
    assert "var x" not in text and "Home | Jobs" not in text and ".a{}" not in text


# ── JD fetch and its errors ─────────────────────────────────────────────────

class _Resp:
    def __init__(self, status=200, ctype="text/html; charset=utf-8", text=""):
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        self.text = text


def _patch_get(monkeypatch, resp=None, exc=None):
    def fake_get(url, **kwargs):
        if exc is not None:
            raise exc
        return resp
    monkeypatch.setattr(T.requests, "get", fake_get)
    # This legacy suite tests HTTP/errors. Rendered fallback has its own suite;
    # never let a mocked HTTP response launch a real external browser here.
    monkeypatch.setattr(T,"_read_rendered_html",lambda url:"")


LONG_BODY = "<p>" + ("Responsibilities and requirements for the role. " * 20) + "</p>"


def test_fetch_returns_text_for_a_normal_page(monkeypatch):
    _patch_get(monkeypatch, _Resp(text="<html><body>" + LONG_BODY + "</body></html>"))
    assert "Responsibilities" in T.fetch_jd_from_url("https://jobs.example.com/1")


def test_fetch_reports_http_block(monkeypatch):
    _patch_get(monkeypatch, _Resp(status=403))
    with pytest.raises(T.JDFetchError, match="HTTP 403"):
        T.fetch_jd_from_url("https://jobs.example.com/1")


def test_fetch_reports_thin_page(monkeypatch):
    _patch_get(monkeypatch, _Resp(text="<html><body><p>Enable JavaScript</p></body></html>"))
    with pytest.raises(T.JDFetchError, match="too little readable text"):
        T.fetch_jd_from_url("https://jobs.example.com/1")


def test_fetch_reports_non_html(monkeypatch):
    _patch_get(monkeypatch, _Resp(ctype="application/pdf", text="%PDF"))
    with pytest.raises(T.JDFetchError, match="not a web page"):
        T.fetch_jd_from_url("https://jobs.example.com/1")


def test_fetch_reports_connection_failure(monkeypatch):
    import requests
    _patch_get(monkeypatch, exc=requests.ConnectionError("boom"))
    with pytest.raises(T.JDFetchError, match="Could not connect"):
        T.fetch_jd_from_url("https://jobs.example.com/1")


def test_fetch_rejects_non_web_address(monkeypatch):
    _patch_get(monkeypatch, _Resp())
    with pytest.raises(T.JDFetchError, match="not a web address"):
        T.fetch_jd_from_url("ftp://x")


# ── saved documents and job lookup ──────────────────────────────────────────

@pytest.fixture()
def db_path(tmp_path):
    path = str(tmp_path / "t.db")
    db.initialize(path)
    return path


def test_saved_documents_round_trip(db_path):
    assert db.get_tailoring_document(db_path, T.KIND_CV) == ""
    db.save_tailoring_document(db_path, T.KIND_CV, "my CV")
    db.save_tailoring_document(db_path, T.KIND_CV, "my CV v2")
    assert db.get_tailoring_document(db_path, T.KIND_CV) == "my CV v2"
    assert db.get_tailoring_document(db_path, T.KIND_PROMPT) == ""


def test_job_lookup_returns_stored_description(db_path):
    conn = db.get_connection(db_path)
    conn.execute("INSERT INTO jobs (url, title, company, description) VALUES (?,?,?,?)",
                 ("https://j.example/1", "Account Manager", "Adyen", "Full JD text"))
    conn.commit()
    conn.close()
    rec = db.get_job_for_tailoring(db_path, "https://j.example/1")
    assert rec["title"] == "Account Manager"
    assert rec["company"] == "Adyen"
    assert rec["description"] == "Full JD text"
    assert db.get_job_for_tailoring(db_path, "https://missing.example") is None


# ── the tab ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QCoreApplication,QEvent
    app=QApplication.instance() or QApplication([])
    yield app
    # Test-owned top-level widgets/clipboard must be released before the Qt
    # application. Cyclic signal callbacks otherwise outlive function locals.
    app.clipboard().clear()
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
    app.processEvents()


def test_tab_copy_puts_full_text_on_clipboard(qapp, db_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    tab = T.TailoringTab(db_path)
    tab.prompt_edit.setPlainText("PROMPT")
    tab.jd_edit.setPlainText("JD")
    tab.cv_edit.setPlainText("CV")
    tab.cl_edit.setPlainText("")
    tab._copy_all()
    assert QApplication.clipboard().text() == "PROMPT\n\nJD\n\nCV"


def test_tab_save_persists_prompt(qapp, db_path):
    tab = T.TailoringTab(db_path)
    tab.prompt_edit.setPlainText("Tailor my CV to the JD")
    tab._save(T.KIND_PROMPT, tab.prompt_edit)
    tab2 = T.TailoringTab(db_path)
    assert tab2.prompt_edit.toPlainText() == "Tailor my CV to the JD"


def test_tab_loads_selected_job_label(qapp, db_path):
    conn = db.get_connection(db_path)
    conn.execute("INSERT INTO jobs (url, title, company) VALUES (?,?,?)",
                 ("https://j.example/2", "Sales Lead", "Acme"))
    conn.commit()
    conn.close()
    tab = T.TailoringTab(db_path)
    tab.set_selected_job("https://j.example/2")
    assert tab.selected_edit.text() == "Sales Lead — Acme"
