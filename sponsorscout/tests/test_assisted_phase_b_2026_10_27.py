"""Synthetic regression pins for Assisted Ingestion Phase B performance work.

No live portal, CAPTCHA, Chromium download or native Windows behavior is
certified here. The fakes only verify bounded lookahead navigation, HTTP
re-probe, shared budgets, order preservation and honest deferral.
"""
from __future__ import annotations

import pathlib
import time as real_time
from types import SimpleNamespace

import pytest

from sponsorscout.core import blocked_portals as bp
from sponsorscout.core import session_details as sd
from sponsorscout.core.session_details import DetailResult, SessionDetailEnricher
from sponsorscout.scanning.career.career_scanner import ProductionScannerConfig
from sponsorscout.scanning.detail_state import DetailRequestState

CORE = pathlib.Path(bp.__file__)
SESSION = pathlib.Path(sd.__file__)
UI = CORE.parents[1] / "ui" / "tabs" / "assisted_ingestion.py"
I18N = CORE.parents[1] / "i18n.py"
ORIGIN = "https://fixture.invalid"
TEXT = "Authentic synthetic primary job description. " * 20


def _url(n):
    return f"{ORIGIN}/jobs/{8100000 + n}-engineer.html"


class FakePage:
    def __init__(self, wall=False):
        self.url = "about:blank"
        self.frames = []
        self._closed = False
        self._wall = wall
        self._html = ""

    def is_closed(self):
        return self._closed

    def goto(self, url, **kwargs):
        self.url = url
        self._html = ("<title>Human verification</title><main>WALL</main>" if self._wall
                      else "<title>Synthetic Engineer</title><main>" + TEXT + "</main>")
        return SimpleNamespace(status=200)

    def wait_for_load_state(self, *args, **kwargs):
        return None

    def wait_for_function(self, *args, **kwargs):
        return None

    def content(self):
        return self._html

    def title(self):
        return "Human verification" if self._wall else "Synthetic Engineer"

    def bring_to_front(self):
        return None

    def close(self):
        self._closed = True


class FakeRequest:
    def __init__(self, wall=False):
        self.wall = wall
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        html = ("<title>Human verification</title><main>WALL</main>" if self.wall
                else "<title>Synthetic Engineer</title><main>" + TEXT + "</main>")
        return SimpleNamespace(status=200, url=url, text=lambda: html, dispose=lambda: None)


class FakeContext:
    def __init__(self, request_wall=False, page_walls=None):
        self.request = FakeRequest(request_wall)
        self.pages = []
        self._page_walls = list(page_walls or [])

    def new_page(self):
        wall = bool(self._page_walls) and self._page_walls.pop(0)
        page = FakePage(wall=wall)
        self.pages.append(page)
        return page


class FakeControl:
    def __init__(self):
        self.attention_events = []
        self.save_requested = False

    def attention(self, info):
        self.attention_events.append(info)

    def poll(self, page):
        return ""


def _scanner():
    state = DetailRequestState()
    scanner = SimpleNamespace(
        config=ProductionScannerConfig(),
        _detail_state=state,
        store_captured_jd=lambda url, text: None,
        is_valid_job_url=lambda url: True,
    )
    return scanner, state


def _patch(monkeypatch, *, wall=False):
    monkeypatch.setattr(sd, "watch_page_responses", lambda page: None)
    monkeypatch.setattr(sd, "inspect_page", lambda *args, **kwargs: None)
    monkeypatch.setattr(sd, "response_metadata", lambda *args, **kwargs: {
        "title": "Synthetic Engineer", "transport": "HTTP (assisted session)"})
    monkeypatch.setattr(sd, "challenge_reason",
                        lambda html, *args, **kwargs: "synthetic wall" if wall and "WALL" in html else None)

    def parse(text, url, final, scanner, title="", browser_evidence=None):
        return DetailResult(
            status="readable", text=TEXT, method="synthetic", title=title or "Synthetic Engineer",
            transport="browser (assisted session)" if browser_evidence is not None else "HTTP (assisted session)",
            requested_url=url, final_url=final, phase="synthetic")
    monkeypatch.setattr(sd, "parse_detail", parse)
    monkeypatch.setattr(sd, "time", SimpleNamespace(sleep=lambda *_: None, monotonic=real_time.monotonic))


def _reader(monkeypatch, *, context=None, control=None, clear_wait_s=0.01, browser_first=True, reprobe=False):
    scanner, state = _scanner()
    context = context or FakeContext()
    reader = SessionDetailEnricher(context, scanner, "scope", clear_wait_s=clear_wait_s, control=control)
    reader.http_reprobe_enabled = bool(reprobe)
    reader.http_reprobe_cooldown = 0
    if browser_first:
        reader.preferred_origins.add(ORIGIN)
        reader._http_failed_origins.add(ORIGIN)
    return reader, state, context


def test_phase_b_config_defaults_are_bounded_and_default_on():
    cfg = ProductionScannerConfig()
    assert cfg.ASSISTED_DETAIL_TABS == 3
    assert cfg.ASSISTED_HTTP_REPROBE is True
    assert cfg.ASSISTED_HTTP_REPROBE_AFTER == 5
    assert cfg.ASSISTED_HTTP_REPROBE_COOLDOWN_SEC == 60
    cfg.ASSISTED_DETAIL_TABS = 9
    scanner, _ = _scanner()
    scanner.config = cfg
    reader = SessionDetailEnricher(FakeContext(), scanner, "scope")
    assert reader.max_tabs == 3


def test_prime_lookahead_prefetches_at_most_three_tabs_and_preserves_order(monkeypatch):
    _patch(monkeypatch)
    reader, state, context = _reader(monkeypatch)
    urls = [_url(n) for n in range(5)]
    reader.prime_lookahead(urls)
    results = [reader.read(url) for url in urls]
    assert [r.status for r in results] == ["readable"] * 5
    assert [r.text for r in results] == [TEXT] * 5
    assert len(context.pages) == 3
    assert reader.counts["parallel_prefetch"] == 5
    assert reader.counts["browser"] == 5
    assert state.total == 5
    assert not reader.batch_interrupted
    reader.close()


def test_http_readable_origin_does_not_open_parallel_tabs(monkeypatch):
    _patch(monkeypatch)
    context = FakeContext()
    reader, state, context = _reader(monkeypatch, context=context, browser_first=False)
    urls = [_url(n) for n in range(4)]
    reader.prime_lookahead(urls)
    results = [reader.read(url) for url in urls]
    assert all(r.status == "readable" for r in results)
    assert len(context.pages) == 0
    assert reader.counts["parallel_prefetch"] == 0
    assert state.total == 4
    reader.close()


def test_read_many_defers_remaining_urls_after_a_real_wall(monkeypatch):
    _patch(monkeypatch, wall=True)
    context = FakeContext(page_walls=[False, True, False, False])
    control = FakeControl()
    reader, state, context = _reader(monkeypatch, context=context, control=control)
    urls = [_url(n) for n in range(4)]
    results = reader.read_many(urls, should_stop=lambda: False)
    assert results[urls[0]].status == "readable"
    assert results[urls[1]].status == "blocked"
    assert all(results[url].status == "budget_skipped" for url in urls[2:])
    assert reader.batch_interrupted
    assert "verification" in reader.batch_interrupt_reason
    assert any(info.get("waiting") for info in control.attention_events)
    reader.close()


def test_read_many_should_stop_defers_all_remaining_urls(monkeypatch):
    _patch(monkeypatch)
    reader, state, context = _reader(monkeypatch)
    urls = [_url(n) for n in range(3)]
    results = reader.read_many(urls, should_stop=lambda: True)
    assert all(result.status == "budget_skipped" for result in results.values())
    assert reader.batch_interrupted
    assert "browser closed" in reader.batch_interrupt_reason
    reader.close()


def test_http_reprobe_clears_browser_preference_only_on_readable_primary_jd(monkeypatch):
    _patch(monkeypatch)
    reader, state, context = _reader(monkeypatch, reprobe=True)
    url = _url(1)
    assert reader._maybe_reprobe_origin(url, force=True) is True
    assert ORIGIN not in reader.preferred_origins
    assert ORIGIN not in reader._http_failed_origins
    assert reader.counts["http_reprobe"] == 1
    assert reader.counts["http_reprobe_cleared"] == 1
    assert state.total == 1
    reader.close()


def test_http_reprobe_wall_keeps_browser_preference(monkeypatch):
    _patch(monkeypatch, wall=True)
    context = FakeContext(request_wall=True)
    reader, state, context = _reader(monkeypatch, context=context, reprobe=True)
    url = _url(1)
    assert reader._maybe_reprobe_origin(url, force=True) is False
    assert ORIGIN in reader.preferred_origins
    assert reader.counts["http_reprobe_cleared"] == 0
    reader.close()


def test_phase_b_source_pins_for_reporting_and_translations():
    core = CORE.read_text(encoding="utf-8")
    session = SESSION.read_text(encoding="utf-8")
    ui = UI.read_text(encoding="utf-8")
    i18n = I18N.read_text(encoding="utf-8")
    assert "prime_lookahead(" in core
    assert "detail_parallel_tabs" in core
    assert "http_reprobe_cleared" in core
    assert "read_many(" in session
    assert "_maybe_reprobe_origin(" in session
    assert "ASSISTED_DETAIL_TABS" in session or "ASSISTED_DETAIL_TABS" in i18n
    perf = "Performance: up to {tabs} parallel detail tabs; {prefetched} JDs pre-navigated; HTTP re-probe cleared {cleared} origin(s)."
    assert perf in ui
    assert perf in i18n
    assert "ASSISTED_DETAIL_TABS" in pathlib.Path(
        CORE.parents[1] / "scanning" / "career" / "career_scanner.py").read_text(encoding="utf-8")
