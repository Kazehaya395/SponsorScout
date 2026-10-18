"""AI Tailoring tab: build one paste-ready message for an AI chatbot.

Four editable boxes:
  * Job description (JD) — session only. Filled from the job selected in the
    Search tab (its stored description, or its job page), or from any JD URL
    the user pastes. Any read failure shows a popup and the user pastes the
    text by hand.
  * AI prompt, CV and cover letter (CL) — saved to the local database with
    their own Save buttons and kept between sessions.

Copy puts AI prompt + JD + CV + CL on the clipboard in that order, in full.
The cover letter is optional; the other three are required.
"""

import html.parser
import re
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)
import requests

from sponsorscout.db import database as db
from sponsorscout.i18n import _

KIND_PROMPT = "ai_prompt"
KIND_CV = "cv"
KIND_CL = "cover_letter"

MIN_JD_CHARS = 300          # less readable text than this = the page did not give a JD
FETCH_TIMEOUT_SEC = 20
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class JDFetchError(Exception):
    """A JD page could not be read; the message is shown to the user."""


# ── Clipboard text ──────────────────────────────────────────────────────────

def build_clipboard_text(prompt: str, jd: str, cv: str, cl: str) -> str:
    """AI prompt, JD, CV, then CL (only if given). Full text, never truncated.

    Raises ValueError naming the required parts that are empty.
    """
    missing = []
    if not prompt.strip():
        missing.append(_("AI prompt"))
    if not jd.strip():
        missing.append(_("Job description"))
    if not cv.strip():
        missing.append(_("CV"))
    if missing:
        raise ValueError(", ".join(missing))
    parts = [prompt.strip(), jd.strip(), cv.strip()]
    if cl.strip():
        parts.append(cl.strip())
    return "\n\n".join(parts)


# ── JD reading ──────────────────────────────────────────────────────────────

class _TextExtractor(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "head",
            "nav", "footer", "iframe", "button", "form"}
    BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6",
             "tr", "section", "article", "ul", "ol", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    """Readable plain text from an HTML page (scripts, styles, nav removed)."""
    extractor = _TextExtractor()
    extractor.feed(markup)
    extractor.close()
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip()
             for ln in "".join(extractor.parts).splitlines()]
    out, blank = [], False
    for ln in lines:
        if ln:
            out.append(ln)
            blank = False
        elif out and not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def fetch_jd_from_url(url: str) -> str:
    """Return the JD text at ``url`` or raise JDFetchError with the reason."""
    url = (url or "").strip()
    if not re.match(r"^https?://\S+$", url):
        raise JDFetchError(_("This is not a web address. It must start with http:// or https://."))
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT,
                                          "Accept": "text/html,application/xhtml+xml"},
                            timeout=FETCH_TIMEOUT_SEC, allow_redirects=True)
    except requests.Timeout:
        raise JDFetchError(_("The site did not answer in time."))
    except requests.RequestException as exc:
        raise JDFetchError(_("Could not connect to the site ({err}).").format(
            err=exc.__class__.__name__))
    if resp.status_code != 200:
        raise JDFetchError(_("The site answered HTTP {code}. It may be blocking automated reads.")
                           .format(code=resp.status_code))
    ctype = resp.headers.get("Content-Type", "").lower()
    if "html" not in ctype and "text" not in ctype:
        raise JDFetchError(_("The link is not a web page (type: {t})."). format(t=ctype or "?"))
    text = html_to_text(resp.text)
    if len(text) < MIN_JD_CHARS:
        raise JDFetchError(_("The page has too little readable text. It may need a login or "
                             "only load with JavaScript."))
    return text


class _JDLoader(QObject):
    """Carries the result of a background fetch back to the GUI thread."""
    done = Signal(bool, str)     # (ok, JD text or error message)


# ── Tab ─────────────────────────────────────────────────────────────────────

class TailoringTab(QWidget):

    status_message = Signal(str)

    def __init__(self, db_path: str, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self._selected_url = None
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        # ── Top bar: selected job, JD URL, Load, Copy ───────────────────────
        top = QHBoxLayout()
        self.selected_lbl = QLabel(_("Selected job:"))
        top.addWidget(self.selected_lbl)
        self.selected_edit = QLineEdit()
        self.selected_edit.setReadOnly(True)
        self.selected_edit.setPlaceholderText(_("Select a job in the Search tab"))
        top.addWidget(self.selected_edit, 2)
        self.or_lbl = QLabel(_("or JD URL:"))
        top.addWidget(self.or_lbl)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(_("Paste any job description link"))
        self.url_edit.setToolTip(_("If a link is given, it is read. Otherwise the selected "
                                   "job is used."))
        top.addWidget(self.url_edit, 3)
        self.load_btn = QPushButton(_("Load JD"))
        self.load_btn.clicked.connect(self._load_jd)
        top.addWidget(self.load_btn)
        top.addStretch(1)
        self.copy_btn = QPushButton(_("Copy prompt + JD + CV + CL"))
        self.copy_btn.setObjectName("Primary")
        self.copy_btn.clicked.connect(self._copy_all)
        top.addWidget(self.copy_btn)
        root.addLayout(top)

        # ── Three columns ───────────────────────────────────────────────────
        cols = QHBoxLayout()
        cols.setSpacing(10)

        # Left: job description (session only)
        left = self.left_box = QGroupBox(_("Job description (session only)"))
        left_lay = QVBoxLayout(left)
        self.jd_edit = self._make_box(_("Load a job, or paste the job description here."))
        left_lay.addWidget(self.jd_edit)
        cols.addWidget(left, 1)

        # Middle: AI prompt
        mid = self.mid_box = QGroupBox(_("AI prompt"))
        mid_lay = QVBoxLayout(mid)
        self.prompt_edit = self._make_box(_("Write the instructions for the AI here."))
        mid_lay.addWidget(self.prompt_edit)
        mid_lay.addLayout(self._save_row(lambda: self._save(KIND_PROMPT, self.prompt_edit)))
        cols.addWidget(mid, 1)

        # Right: CV over CL
        right = QVBoxLayout()
        right.setSpacing(10)
        cv = self.cv_box = QGroupBox(_("CV"))
        cv_lay = QVBoxLayout(cv)
        self.cv_edit = self._make_box(_("Paste your CV here."))
        cv_lay.addWidget(self.cv_edit)
        cv_lay.addLayout(self._save_row(lambda: self._save(KIND_CV, self.cv_edit)))
        right.addWidget(cv, 1)
        cl = self.cl_box = QGroupBox(_("Cover letter (optional)"))
        cl_lay = QVBoxLayout(cl)
        self.cl_edit = self._make_box(_("Paste your cover letter here (optional)."))
        cl_lay.addWidget(self.cl_edit)
        cl_lay.addLayout(self._save_row(lambda: self._save(KIND_CL, self.cl_edit)))
        right.addWidget(cl, 1)
        cols.addLayout(right, 1)

        root.addLayout(cols, 1)

        self._reload_saved()

    # ── widgets ─────────────────────────────────────────────────────────────

    @staticmethod
    def _make_box(placeholder: str) -> QPlainTextEdit:
        box = QPlainTextEdit()
        box.setPlaceholderText(placeholder)
        box.setLineWrapMode(QPlainTextEdit.NoWrap)   # horizontal scroll for long lines
        box.setMinimumHeight(120)
        return box

    def _save_row(self, on_save) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch(1)
        btn = QPushButton(_("Save"))
        btn.clicked.connect(on_save)
        row.addWidget(btn)
        return row

    # ── saved documents ─────────────────────────────────────────────────────

    def _reload_saved(self):
        self.prompt_edit.setPlainText(db.get_tailoring_document(self.db_path, KIND_PROMPT))
        self.cv_edit.setPlainText(db.get_tailoring_document(self.db_path, KIND_CV))
        self.cl_edit.setPlainText(db.get_tailoring_document(self.db_path, KIND_CL))

    def _save(self, kind: str, box: QPlainTextEdit):
        db.save_tailoring_document(self.db_path, kind, box.toPlainText())
        self.status_message.emit(_("Saved."))

    # ── selection from the Search tab ───────────────────────────────────────

    def set_selected_job(self, url: str):
        """Show the job picked in Search. The JD box is only filled on Load JD."""
        if not url:
            return
        self._selected_url = url
        rec = db.get_job_for_tailoring(self.db_path, url)
        if rec and (rec["title"] or rec["company"]):
            label = f"{rec['title']} — {rec['company']}".strip(" —")
        else:
            label = url
        self.selected_edit.setText(label)
        self.selected_edit.setCursorPosition(0)

    # ── loading the JD ──────────────────────────────────────────────────────

    def _load_jd(self):
        if self._loading:
            return
        typed_url = self.url_edit.text().strip()
        if typed_url:
            self._start_fetch(typed_url)
            return
        if not self._selected_url:
            QMessageBox.information(
                self, _("No job chosen"),
                _("Select a job in the Search tab, or paste a job description link here."))
            return
        rec = db.get_job_for_tailoring(self.db_path, self._selected_url)
        stored = (rec or {}).get("description", "").strip()
        if stored:
            self.jd_edit.setPlainText(stored)
            self.status_message.emit(_("Job description loaded."))
            return
        # No stored description: read the job's own page.
        self._start_fetch(self._selected_url)

    def _start_fetch(self, url: str):
        self._set_loading(True)
        loader = _JDLoader(self)
        loader.done.connect(self._on_fetched)

        def work():
            try:
                loader.done.emit(True, fetch_jd_from_url(url))
            except JDFetchError as exc:
                loader.done.emit(False, str(exc))
            except Exception as exc:       # never leave the UI waiting
                loader.done.emit(False, _("Unexpected error: {e}").format(e=exc))

        threading.Thread(target=work, name="JDFetch", daemon=True).start()

    def _on_fetched(self, ok: bool, payload: str):
        self._set_loading(False)
        if ok:
            self.jd_edit.setPlainText(payload)
            self.status_message.emit(_("Job description loaded."))
        else:
            QMessageBox.warning(
                self, _("Could not read the job description"),
                _("{reason}\n\nPlease paste the job description into the JD box.")
                .format(reason=payload))

    def _set_loading(self, busy: bool):
        self._loading = busy
        self.load_btn.setEnabled(not busy)
        self.load_btn.setText(_("Loading…") if busy else _("Load JD"))
        self.setCursor(Qt.WaitCursor if busy else Qt.ArrowCursor)

    # ── copy ────────────────────────────────────────────────────────────────

    def _copy_all(self):
        try:
            text = build_clipboard_text(self.prompt_edit.toPlainText(),
                                        self.jd_edit.toPlainText(),
                                        self.cv_edit.toPlainText(),
                                        self.cl_edit.toPlainText())
        except ValueError as exc:
            QMessageBox.warning(
                self, _("Nothing to copy yet"),
                _("These parts are empty: {parts}").format(parts=str(exc)))
            return
        QApplication.clipboard().setText(text)
        self.status_message.emit(
            _("Copied to clipboard ({n} characters).").format(n=len(text)))

    # ── i18n ────────────────────────────────────────────────────────────────

    def retranslate(self):
        """Re-apply translated labels. Typed or saved text is never touched."""
        self.selected_lbl.setText(_("Selected job:"))
        self.or_lbl.setText(_("or JD URL:"))
        self.selected_edit.setPlaceholderText(_("Select a job in the Search tab"))
        self.url_edit.setPlaceholderText(_("Paste any job description link"))
        self.load_btn.setText(_("Load JD"))
        self.copy_btn.setText(_("Copy prompt + JD + CV + CL"))
        self.left_box.setTitle(_("Job description (session only)"))
        self.mid_box.setTitle(_("AI prompt"))
        self.cv_box.setTitle(_("CV"))
        self.cl_box.setTitle(_("Cover letter (optional)"))
