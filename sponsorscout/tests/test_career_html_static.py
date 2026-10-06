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


def test_european_german_detail_paths_are_accepted_and_listing_path_is_rejected():
    """Regression for European employer boards using German job URL vocab."""
    from sponsorscout.scanning.career.career_scanner import CareerPortalScanner

    scanner = CareerPortalScanner(skip_preflight=True)

    detail_urls = [
        "https://www.3bankenit.at/jobangebote/test-strategy-manager-w-m-d/",
        "https://www.3bankenit.at/jobangebote/assetmanager-jira-service-management-w-m-d/",
        "https://www.3bankenit.at/jobangebote/senior-atlassian-developer-w-m-d/",
        "https://www.3bankenit.at/jobangebote/senior-devops-engineer-w-m-d/",
    ]
    for url in detail_urls:
        assert scanner.is_valid_job_url(url), url

    # The 3 Banken IT seed itself is a listing page and must not become a
    # synthetic job just because it matches the generic /jobs/<slug> rule.
    assert not scanner.is_valid_job_url(
        "https://www.3bankenit.at/jobs/offene-jobangebote/"
    )


def test_static_harvest_keeps_real_3banken_style_links(monkeypatch):
    """Static path must recover German detail URLs before Playwright fallback."""
    from sponsorscout.scanning.career.career_scanner import CareerPortalScanner

    scanner = CareerPortalScanner(skip_preflight=True)
    slugs = [
        "senior-datenbankadministrator-w-m-d",
        "test-strategy-manager-w-m-d",
        "senior-frontend-developer-w-m-d",
        "product-owner-data-platform-operations-w-m-d",
        "senior-devops-engineer-w-m-d",
        "senior-atlassian-developer-w-m-d",
    ]
    html = "<html><body>" + "".join(
        f'<a href="/jobangebote/{slug}/">Linz {slug.replace("-", " ").title()}</a>'
        for slug in slugs
    ) + "</body></html>"

    monkeypatch.setattr(
        career.urllib.request,
        "urlopen",
        lambda _req, timeout=None: _fake_response(html),
    )

    jobs, diag = career.fetch_static_jobs(
        "https://www.3bankenit.at/jobs/offene-jobangebote/",
        url_validator=scanner.is_valid_job_url,
        title_validator=scanner.is_valid_job_title,
    )

    assert diag == "static HTML: 6"
    assert len(jobs) == 6
    assert all("/jobangebote/" in job["job_url"] for job in jobs)


def test_browser_locale_is_local_first_for_european_seed_countries():
    from sponsorscout.scanning.career.career_scanner import CareerPortalScanner

    scanner = CareerPortalScanner(skip_preflight=True)
    cases = [
        ({"target_country": "Germany"}, "https://example.de/karriere", "de-DE", "de"),
        ({"target_country": "Austria"}, "https://example.at/jobs", "de-AT", "de"),
        ({"target_country": "Italy"}, "https://example.it/lavora-con-noi", "it-IT", "it"),
        ({"target_country": "Netherlands"}, "https://example.nl/vacatures", "nl-NL", "nl"),
        ({"target_country": "France"}, "https://example.fr/emplois", "fr-FR", "fr"),
    ]
    for row, url, expected_locale, expected_lang in cases:
        locale, accept = scanner._preferred_browser_locales(row, url)
        assert locale == expected_locale
        assert accept.split(",", 1)[0] == expected_locale
        assert expected_lang in accept


def test_explicit_url_locale_overrides_country_default():
    from sponsorscout.scanning.career.career_scanner import CareerPortalScanner

    scanner = CareerPortalScanner(skip_preflight=True)
    locale, accept = scanner._preferred_browser_locales(
        {"target_country": "Germany"},
        "https://example.com/it/offerte-di-lavoro/",
    )
    assert locale == "it-IT"
    assert accept.startswith("it-IT")


def test_multilingual_european_job_titles_are_not_rejected():
    from sponsorscout.scanning.career.career_scanner import CareerPortalScanner

    scanner = CareerPortalScanner(skip_preflight=True)
    titles = [
        "Sachbearbeiter (m/w/d)",
        "Koch (m/w/d)",
        "Addetto alle vendite", 
        "Responsabile amministrativo", 
        "Responsable administratif", 
        "Medewerker klantenservice", 
        "Verkoper", 
        "Technicien de maintenance", 
    ]
    for title in titles:
        assert scanner.is_valid_job_title(title), title


def test_direct_listing_seed_classifier_covers_european_listing_routes_but_not_ams_landing():
    from sponsorscout.scanning.career.career_scanner import CareerPortalScanner

    direct = [
        "https://www.3bankenit.at/jobs/offene-jobangebote/",
        "https://careers.cegedim.com/en/annonces",
        "https://www.adecco.be/nl-be/vacatures",
        "https://example.it/offerte-di-lavoro/",
        "https://example.fr/offres-d-emploi/",
        "https://example.nl/vacatures/",
        "https://example.de/stellenangebote/",
        "https://example.com/jobs/Analyst",
        "https://workindenmark.jobnet.dk/find-job",
        "https://clearmatics.com/careers/#jobs",
    ]
    for url in direct:
        assert CareerPortalScanner._is_direct_listing_seed_url(url), url

    # AMS /Arbeitsuchende is a landing page, not the job list itself. The
    # scanner must be allowed to use the landing/search recovery logic there.
    assert not CareerPortalScanner._is_direct_listing_seed_url(
        "https://www.ams.at/arbeitsuchende"
    )
    assert not CareerPortalScanner._is_direct_listing_seed_url(
        "https://example.com/careers"
    )


def test_static_anchor_parser_handles_nested_markup_and_unquoted_href():
    html = (
        '<a data-id="1" href="/stellenangebote/test"><span>Sach</span>'
        'bearbeiter <strong>(m/w/d)</strong></a>'
        '<a href=/offerte-di-lavoro/addetto><span>Addetto</span> alle vendite</a>'
    )
    rows = career._parse_static_anchors(html)
    assert rows == [
        ("/stellenangebote/test", "Sachbearbeiter (m/w/d)"),
        ("/offerte-di-lavoro/addetto", "Addetto alle vendite"),
    ]


def test_digitalrecruiters_careers_are_resolved_before_dom_fallback():
    assert career.sniff_provider(
        "",
        "https://careers.cegedim.com/en/annonces",
    ) == ("digitalrecruiters", "careers.cegedim.com", "")

    assert career.sniff_provider(
        "DigitalRecruiters BANKESS /fr/annonces",
        "https://example.be/fr/annonces",
    ) == ("digitalrecruiters", "example.be", "")


def test_detail_browser_fallback_is_hard_capped_for_slow_hosts():
    from sponsorscout.scanning.career.career_scanner import ProductionScannerConfig

    assert ProductionScannerConfig.DETAIL_BROWSER_FALLBACK_PER_COMPANY == 12
    assert ProductionScannerConfig.DETAIL_BROWSER_HOST_FAILURES == 2
    assert ProductionScannerConfig.DETAIL_BROWSER_TIMEOUT_MS == 8000
