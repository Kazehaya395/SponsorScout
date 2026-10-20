"""Blocked portals: list bot-blocked seeds after a run and hand them to the browser.

The scanner never gets past bot protection. These tests cover only reading the
errors CSV and opening the URL in the user's default browser (stubbed here, so
no browser opens and nothing contacts the site).
"""
import csv

from sponsorscout.core import blocked_portals as bp

HEADER = ["Run ID", "Timestamp", "Seed Name", "Phase", "Error Type", "Message", "Seed URL"]


def _write_errors(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in HEADER})


def test_only_bot_blocks_are_listed_and_urls_are_deduplicated(tmp_path):
    errs = tmp_path / "run_errors.csv"
    _write_errors(errs, [
        {"Seed Name": "Robert Walters Germany", "Error Type": "RuntimeError",
         "Message": "blocked by bot protection: PerimeterX challenge page",
         "Seed URL": "https://www.robertwalters.de/en/jobs.html"},
        {"Seed Name": "Robert Walters Germany", "Error Type": "RuntimeError",
         "Message": "blocked by bot protection: PerimeterX challenge page",
         "Seed URL": "https://www.robertwalters.de/en/jobs.html"},
        {"Seed Name": "Clearmatics", "Error Type": "TimeoutError",
         "Message": "navigation timeout", "Seed URL": "https://clearmatics.example/jobs"},
    ])
    found = bp.blocked_portals_from_errors_csv(errs)
    assert [f["url"] for f in found] == ["https://www.robertwalters.de/en/jobs.html"]
    assert found[0]["seed_name"] == "Robert Walters Germany"
    assert "PerimeterX" in found[0]["reason"]


def test_missing_errors_file_gives_empty_list(tmp_path):
    assert bp.blocked_portals_from_errors_csv(tmp_path / "nope.csv") == []
    assert bp.blocked_portals_from_errors_csv(None) == []


def test_summary_lists_only_career_bot_blocks_never_ats(tmp_path):
    career = tmp_path / "career_errors.csv"
    ats = tmp_path / "ats_errors.csv"
    _write_errors(career, [{"Seed Name": "Robert Walters Germany",
                            "Message": "blocked by bot protection: PerimeterX challenge page",
                            "Seed URL": "https://www.robertwalters.de/en/jobs.html"}])
    # Even if an ATS errors file contained the same text, it must not be offered.
    _write_errors(ats, [{"Seed Name": "Some ATS board",
                         "Message": "blocked by bot protection: PerimeterX challenge page",
                         "Seed URL": "https://boards.example/some"},
                        {"Seed Name": "Greenhouse board", "Message": "HTTP 403",
                         "Seed URL": "https://boards.greenhouse.io/x"}])
    summary = {"artifacts": {"career": {"errors": str(career)}, "ats": {"errors": str(ats)}}}
    found = bp.blocked_portals_from_summary(summary)
    assert [f["seed_name"] for f in found] == ["Robert Walters Germany"]


def test_no_career_artifact_means_nothing_offered(tmp_path):
    ats = tmp_path / "ats_errors.csv"
    _write_errors(ats, [{"Message": "blocked by bot protection: X", "Seed URL": "https://a.example/"}])
    assert bp.blocked_portals_from_summary({"artifacts": {"ats": {"errors": str(ats)}}}) == []


def test_generic_blocked_text_without_bot_marker_is_not_offered(tmp_path):
    errs = tmp_path / "e.csv"
    _write_errors(errs, [{"Message": "static: blocked (HTTP 403, bot protection likely)",
                          "Seed URL": "https://a.example/jobs"}])
    assert bp.blocked_portals_from_errors_csv(errs) == []


def test_non_web_urls_are_never_offered(tmp_path):
    errs = tmp_path / "e.csv"
    _write_errors(errs, [{"Message": "blocked by bot protection: X", "Seed URL": "file:///etc/passwd"},
                         {"Message": "blocked by bot protection: X", "Seed URL": "javascript:alert(1)"}])
    assert bp.blocked_portals_from_errors_csv(errs) == []


def test_open_in_default_browser_uses_webbrowser_for_web_urls_only(monkeypatch):
    opened = []
    monkeypatch.setattr(bp.webbrowser, "open_new_tab", lambda url: opened.append(url) or True)
    assert bp.open_in_default_browser("https://www.robertwalters.de/en/jobs.html") is True
    assert bp.open_in_default_browser("file:///etc/passwd") is False
    assert bp.open_in_default_browser("") is False
    assert opened == ["https://www.robertwalters.de/en/jobs.html"]
