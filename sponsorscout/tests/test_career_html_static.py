"""HTML helpers in the career scanner must never raise NameError.

Regression for the refactor that dropped ``from html import unescape`` out of
``sponsorscout/scanning/career/career_scanner.py`` (the pre-refactor file still
carried it as ``# FIX P0-30``). In production that broke:

* ``_jd_plain`` — Ashby/Greenhouse/Workable API job-description parsing, so the
  provider API path failed with ``NameError``; and
* ``_static_strip_tags`` / ``fetch_static_jobs`` — the browser-free HTML
  harvest, so 138 of 222 scan targets recorded ``static: error NameError``.

Both then fell through to the Playwright DOM path, where the build was broken
too, which is why the run produced 0 usable rows for those targets.
"""

from sponsorscout.scanning.career import career_scanner as career


def test_jd_plain_decodes_html_entities():
    # Ashby/Greenhouse return HTML-ESCAPED markup ("&lt;p&gt;...").
    out = career._jd_plain("&lt;p&gt;Senior &amp; Backend&lt;/p&gt;")
    assert "&lt;" not in out
    assert "&amp;" not in out
    assert "<p>" not in out
    assert "Senior & Backend" in out


def test_jd_plain_returns_empty_for_empty_input():
    assert career._jd_plain("") == ""


def test_static_strip_tags_decodes_entities():
    assert (
        career._static_strip_tags("<b>Principal</b> Engineer &amp; Lead")
        == "Principal Engineer & Lead"
    )


def _fake_response(body: str):
    class _Resp:
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def __init__(self):
            self._body = body.encode("utf-8")

        def read(self, *_args):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    return _Resp()


def test_static_harvest_decodes_href_and_title(monkeypatch):
    """Exercises the exact line (2634) that raised NameError for 138 targets."""
    html = "<html><body>" + "".join(
        f'<a href="/careers/job-{i}&amp;ref=x">Senior Engineer &amp; Lead {i}</a>'
        for i in range(5)
    ) + "</body></html>"

    monkeypatch.setattr(
        career.urllib.request,
        "urlopen",
        lambda _req, timeout=None: _fake_response(html),
    )

    jobs, diag = career.fetch_static_jobs(
        "https://example.com/careers",
        min_jobs=1,
        url_validator=lambda _u: True,
        title_validator=lambda _t: True,
    )

    assert diag.startswith("static HTML:"), diag
    assert len(jobs) == 5
    assert jobs[0]["job_title"] == "Senior Engineer & Lead 0"
    assert jobs[0]["job_url"] == "https://example.com/careers/job-0&ref=x"
    assert all(j["extraction_method"] == "static_html" for j in jobs)


def test_static_harvest_does_not_error_on_entity_only_page(monkeypatch):
    html = "<html><body><p>Nothing here &amp; no jobs yet.</p></body></html>"
    monkeypatch.setattr(
        career.urllib.request,
        "urlopen",
        lambda _req, timeout=None: _fake_response(html),
    )
    jobs, diag = career.fetch_static_jobs(
        "https://example.com/careers", min_jobs=1
    )
    assert not jobs
    # Must be the "too few links" diagnostic, never `static: error NameError`.
    assert diag.startswith("static: only 0 job link(s)"), diag
