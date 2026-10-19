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
import json
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
    return _fix_mojibake("\n".join(out).strip())


_BROWSER_NAMES = {"google chrome", "microsoft edge", "apple safari", "mozilla firefox",
                  "safari", "firefox", "chrome", "edge", "opera"}
_BROWSER_NOTICE_LINES = ("please update to one of the following browsers",
                         "you can find details about supported web browsers")


def strip_browser_notice(text: str) -> str:
    """Remove the 'browser not supported' notice that some job pages show.

    SmartRecruiters pages carry an 'Internet Explorer 11 is no longer supported'
    banner followed by a bullet list of browser names. The scanner stores it
    as the start of the JD. Only the notice, its wording and runs of browser
    names are removed; the rest of the text is untouched.
    """
    lines = (text or "").split("\n")
    out = []
    i = 0
    while i < len(lines):
        low = lines[i].strip().lstrip("#-*• ").strip().lower()
        if ("internet explorer" in low and "no longer supported" in low) or \
                low.startswith(_BROWSER_NOTICE_LINES):
            i += 1
            while i < len(lines):
                nxt = lines[i].strip().lstrip("-*• ").strip().lower()
                if not nxt or nxt in _BROWSER_NAMES or nxt.startswith(_BROWSER_NOTICE_LINES):
                    i += 1
                else:
                    break
            continue
        # A run of two or more lines that are only browser names is never JD text.
        if low in _BROWSER_NAMES:
            j = i
            while j < len(lines) and (not lines[j].strip() or
                                      lines[j].strip().lstrip("-*• ").strip().lower() in _BROWSER_NAMES):
                j += 1
            names = [ln for ln in lines[i:j] if ln.strip()]
            if len(names) >= 2:
                i = j
                continue
        out.append(lines[i])
        i += 1
    result = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


_LD_JSON_RE = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                         re.S | re.I)


def _jsonld_nodes(data):
    """Yield every dict in a JSON-LD value (handles lists and @graph)."""
    if isinstance(data, list):
        for item in data:
            yield from _jsonld_nodes(item)
    elif isinstance(data, dict):
        yield data
        yield from _jsonld_nodes(data.get("@graph", []))


_RUNON_LOWER_UPPER_RE = re.compile(r"(?<=[a-z]{3})(?=[A-Z])")
_RUNON_SENTENCE_RE = re.compile(r"(?<=[a-z][.:!?])(?=[A-Z][a-z])")
# Common JD section headings. Run-on text loses the list breaks before these.
_RUNON_HEADING_RE = re.compile(
    r"\s+(?=(?:How You(?:’|')?d Spend Your Day|Your role|The Role|About the team|"
    r"Primary Tasks|Responsibilities|Qualifications|Requirements|You Will Have|"
    r"You May Also Have|Working Pattern|Contract Length|Salary|Benefits|"
    r"What You|Who You|Why Join)\b)")


def _fix_mojibake(text: str) -> str:
    """Undo UTF-8 text that was decoded as Windows-1252 ("Youâ€™d" -> "You’d")."""
    if not any(m in text for m in ("â€", "Ã", "Â")):
        return text
    try:
        return text.encode("cp1252").decode("utf-8")
    except UnicodeError:
        return text


def _split_runon(text: str) -> str:
    """Restore line breaks lost from a one-paragraph JSON-LD description.

    Workday joins list items and sections with no separator, so "complexesDesign"
    and "applications.Your role" arrive as single words. A break is inserted at
    a lowercase-to-capital join (three or more lowercase letters before it) and
    at a sentence end followed directly by a capitalised word.
    """
    text = _RUNON_LOWER_UPPER_RE.sub("\n", text)
    text = _RUNON_SENTENCE_RE.sub("\n", text)
    text = _RUNON_HEADING_RE.sub("\n", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def jobposting_text(markup: str) -> str:
    """JD text from a schema.org JobPosting block in the page's JSON-LD.

    Workday, Workable and SmartRecruiters publish the full job text here even when
    the visible page is built by JavaScript.
    """
    best = ""
    for raw in _LD_JSON_RE.findall(markup or ""):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for node in _jsonld_nodes(data):
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "JobPosting" not in kinds:
                continue
            desc = node.get("description") or ""
            if not isinstance(desc, str):
                continue
            text = html_to_text(html.unescape(desc))
            if "\n" not in text:
                text = _split_runon(text)
            if len(text) > len(best):
                best = text
    return best


def best_jd_text(markup: str) -> str:
    """JD text from a fetched page: JSON-LD JobPosting first, then the visible text."""
    text = strip_browser_notice(jobposting_text(markup))
    if len(text) >= MIN_JD_CHARS:
        return text
    return strip_browser_notice(html_to_text(markup))


def _read_rendered_html(url: str) -> str:
    """Render the page in a browser (JS-built job pages) and return its HTML."""
    try:
        from sponsorscout.services.browser_fetcher import fetch_rendered_html
        rendered = fetch_rendered_html(url, wait_ms=4000, timeout=30, force_browser=True)
    except Exception:
        return ""
    return (rendered or {}).get("html") or ""


def workday_cxs_url(url: str) -> str:
    """Workday job page URL -> its CXS detail API URL, or "" if not a Workday job."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    m = re.match(r"^([^.]+)\.wd\d+\.myworkdayjobs\.com$", parsed.netloc, re.I)
    segs = [x for x in parsed.path.split("/") if x]
    if not m or "job" not in segs:
        return ""
    i = segs.index("job")
    if i < 1:
        return ""
    site = segs[i - 1]
    rest = "/".join(segs[i:])
    return f"{parsed.scheme}://{parsed.netloc}/wday/cxs/{m.group(1)}/{site}/{rest}"


def workday_jd_text(url: str) -> str:
    """JD from the Workday CXS API. Its description is HTML with <p>/<li> tags,
    so the list items and headings keep their line breaks (the public page
    gives the same text as one run-on paragraph). "" if unavailable."""
    api = workday_cxs_url(url)
    if not api:
        return ""
    try:
        resp = requests.get(api, headers={"User-Agent": USER_AGENT,
                                          "Accept": "application/json"},
                            timeout=FETCH_TIMEOUT_SEC)
        if resp.status_code != 200:
            return ""
        info = (resp.json() or {}).get("jobPostingInfo") or {}
    except (requests.RequestException, ValueError):
        return ""
    desc = info.get("jobDescription") or ""
    if not isinstance(desc, str) or not desc.strip():
        return ""
    return html_to_text(desc)


def fetch_jd_from_url(url: str) -> str:
    """Return the JD text at ``url`` or raise JDFetchError with the reason."""
    url = (url or "").strip()
    if not re.match(r"^https?://\S+$", url):
        raise JDFetchError(_("This is not a web address. It must start with http:// or https://."))
    wd_text = workday_jd_text(url)
    if len(wd_text) >= MIN_JD_CHARS:
        return wd_text
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT,
                                          "Accept": "text/html,application/xhtml+xml"},
                            timeout=FETCH_TIMEOUT_SEC, allow_redirects=True)
    except requests.Timeout:
        raise JDFetchError(_("The site did not answer in time."))
    except requests.RequestException as exc:
        raise JDFetchError(_("Could not connect to the site ({err}).").format(
            err=exc.__class__.__name__))
    ctype = resp.headers.get("Content-Type", "").lower()
    text = ""
    if resp.status_code == 200 and ("html" in ctype or "text" in ctype):
        text = best_jd_text(resp.text)
    if len(text) < MIN_JD_CHARS:
        # Many career pages (Workday, Workable, SmartRecruiters) build the job text
        # with JavaScript, and some block plain requests with HTTP 403/429.
        # Render the page in a browser before giving up.
        text = best_jd_text(_read_rendered_html(url))
    if len(text) >= MIN_JD_CHARS:
        return text
    if resp.status_code != 200:
        raise JDFetchError(_("The site answered HTTP {code}. It may be blocking automated reads.")
                           .format(code=resp.status_code))
    if "html" not in ctype and "text" not in ctype:
        raise JDFetchError(_("The link is not a web page (type: {t}).").format(t=ctype or "?"))
    raise JDFetchError(_("The page has too little readable text. It may need a login or "
                         "only load with JavaScript."))


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
        box.setLineWrapMode(QPlainTextEdit.WidgetWidth)   # wrap long lines to the box width
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
        stored = strip_browser_notice((rec or {}).get("description", "")).strip()
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
