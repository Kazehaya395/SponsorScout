"""Static board reader: card titles, navigation filtering and paging (2026-10-17).

Offline: the page fetch is replaced by an in-memory dict, so these run without
network access. Each case mirrors a real board seen in the 2026-10-17 scan.
"""
from sponsorscout.scanning.career import career_scanner as cs


def _card(href, title, location):
    return (f'<a class="job-card" href="{href}"><div><h2 class="job-title">{title}</h2></div>'
            f'<p class="job-location"><span>{location}</span></p></a>')


def _link(href, text):
    return f'<a href="{href}">{text}</a>'


def _accept_all(url):
    return True


class _FakeResp:
    def __init__(self, body):
        self._body = body.encode("utf-8")
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def read(self, _n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve(monkeypatch, pages):
    """Serve `pages` (url -> html) for every urlopen call; unknown URLs fail."""
    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url not in pages:
            raise OSError(f"no fixture for {url}")
        return _FakeResp(pages[url])
    monkeypatch.setattr(cs.urllib.request, "urlopen", fake_urlopen)


def _run(pages, seed_url, monkeypatch):
    _serve(monkeypatch, pages)
    return cs.fetch_static_jobs(seed_url, timeout_sec=5, min_jobs=1,
                                url_validator=_accept_all,
                                title_validator=lambda t, u: bool(t))


def test_card_heading_is_the_title_not_the_whole_card():
    inner = ('<h2 class="job-title">Formulator</h2><p class="job-company">Vitaquest</p>'
             '<p class="job-location"><span>West Caldwell, NJ, United States</span></p>')
    title, loc = cs._static_card_fields(inner)
    assert title == "Formulator"
    assert loc == "West Caldwell, NJ, United States"


def test_anchor_without_heading_keeps_plain_text_title():
    assert cs._static_card_fields("Data Analyst")[0] == ""


def test_navigation_links_are_not_jobs():
    for path in ("/about", "/pages/benefit", "/departments/risorse-umane", "/terms",
                 "/jobs/health-and-care-industry", "/jobs/canada-country", "/"):
        assert cs._static_is_navigation("https://x.test" + path, "Some Title", False), path


def test_job_slugs_that_start_like_nav_words_are_kept():
    for path in ("/contact-center-agent", "/news-anchor-jobs", "/jobs/123456-x", "/job/formulator"):
        assert not cs._static_is_navigation("https://x.test" + path, "Some Title", False), path


def test_social_and_pager_text_are_navigation():
    assert cs._static_is_navigation("https://www.facebook.com/x", "Visita il nostro Facebook", False)
    assert cs._static_is_navigation("https://x.test/jobs?page=2", "2", False)
    assert cs._static_is_navigation("https://x.test/jobs?page=2", "»", False)
    assert not cs._static_is_navigation("https://x.test/jobs/1", "Home Care Assistant", True)


def test_next_page_by_rel_and_by_load_more_control():
    html = '<a rel="next" href="/jobs?page=2">Next</a>'
    assert cs._static_next_page_url(html, "https://x.test/jobs") == "https://x.test/jobs?page=2"
    html = ('<a id="show_more_button" href="/jobs/show_more?page=2" class="btn">'
            '<span>Mostra ancora 20</span></a>')
    assert cs._static_next_page_url(html, "https://x.test/jobs") == "https://x.test/jobs/show_more?page=2"


def test_job_titled_next_is_not_a_pager():
    html = _card("/job/1", "Next Generation Engineer", "Roma")
    assert cs._static_next_page_url(html, "https://x.test/jobs") == ""


def test_show_more_board_is_read_to_the_last_page(monkeypatch):
    p1 = "".join(_card(f"/jobs/{i:07d}-role-{i}", f"Role {i}", "Milano") for i in range(1, 4))
    p1 += '<a id="show_more_button" href="/jobs/show_more?page=2"><span>Mostra ancora 3</span></a>'
    p2 = "".join(_card(f"/jobs/{i:07d}-role-{i}", f"Role {i}", "Roma") for i in range(4, 6))
    pages = {"https://x.test/jobs": p1,
             "https://x.test/jobs/show_more?page=2": p2}
    jobs, diag = _run(pages, "https://x.test/jobs", monkeypatch)
    assert [j["job_title"] for j in jobs] == [f"Role {i}" for i in range(1, 6)]
    assert "2 page(s)" in diag


def test_paging_stops_on_a_repeat_page(monkeypatch):
    same = "".join(_card(f"/job/item-{i}", f"Item {i}", "Torino") for i in range(1, 4))
    same += _link("/jobs?page=2", "»")
    jobs, _ = _run({"https://x.test/jobs": same, "https://x.test/jobs?page=2": same},
                   "https://x.test/jobs", monkeypatch)
    assert len(jobs) == 3


def test_paging_is_capped(monkeypatch):
    def page(n):
        return ("".join(_card(f"/job/p{n}-{i}", f"P{n} {i}", "Bari") for i in range(2))
                + _link(f"/jobs?page={n + 1}", "»"))
    pages = {"https://x.test/jobs": page(1)}
    pages.update({f"https://x.test/jobs?page={n}": page(n) for n in range(2, 60)})
    jobs, diag = _run(pages, "https://x.test/jobs", monkeypatch)
    assert f"{cs._STATIC_MAX_PAGES} page(s)" in diag
    assert len(jobs) == 2 * cs._STATIC_MAX_PAGES
