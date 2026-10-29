"""Regression pins for the 2026-10-26 Assisted Ingestion evidence review.

The uploaded DB showed 581 observed jobs, 88 readable JDs and 493 silently
unattempted JDs. It also exposed a Wwft false positive, contaminated listing
page descriptions, and an incomplete UI coverage report. These tests use
synthetic text and source-level pins only; they do not certify a live portal.
"""
from __future__ import annotations

import pathlib

from sponsorscout.core import blocked_portals as bp
from sponsorscout.core.support_application import apply_support_to_job
from sponsorscout.scanning.career.career_scanner import CareerPortalScanner
from sponsorscout.scanning.jd_support import JDSupportDetector

CORE = pathlib.Path(bp.__file__)
UI = CORE.parents[1] / "ui" / "tabs" / "assisted_ingestion.py"
I18N = CORE.parents[1] / "i18n.py"

BASE = (
    "Responsibilities: Build reliable backend services, maintain automated "
    "tests and collaborate with product teams. Requirements: Strong "
    "engineering communication and practical experience operating distributed "
    "systems. "
) * 8
WWFT = "Samenstellen van klantacceptatiedossiers volgens de geldende Wwft- en WTT-richtlijnen."
INTERNAL = "We offer visa and relocation support within the EU for this role."
JOB = dict(
    url="https://fixture.invalid/jobs/123456-backend-engineer.html",
    title="Backend Engineer",
    company="Fixture",
    location="Berlin, Germany",
    country="Germany",
    country_source="job_location",
    ats_source="assisted_browser",
    source_type="recruiter",
    source_subtype="recruiter",
)


def scanner():
    return CareerPortalScanner(skip_preflight=True)


# ---------------------------------------------------------------------------
# Support detector / application
# ---------------------------------------------------------------------------

def test_wwft_is_not_a_visa_concept():
    det = JDSupportDetector()
    assert det.VISA_CONCEPTS.search("Wwft- en WTT-richtlijnen.") is None
    assert det.VISA_CONCEPTS.search("wetten overplichting") is not None
    verdict = det.detect(WWFT)["visa"]
    assert verdict["verdict"] == "Unknown"
    assert verdict["confidence"] == 0.0
    assert verdict["evidence"] == []


def test_wwft_sentence_does_not_create_support_evidence_or_required_no():
    result = apply_support_to_job(scanner(), JOB, BASE + WWFT)
    assert result["visa_sponsorship"] == "Unknown"
    assert result["relocation_support"] == "Unknown"
    assert result["eu_blue_card_verdict"] == "Unknown"
    assert result["relocation_required"] == "Unknown"
    assert result["support_confidence"] == 0.0
    assert result["support_evidence"] == ""
    assert result["support_evidence_url"] == ""
    assert "Wwft" not in result["support_evidence"]
    assert "jd_read_status=readable" in result["support_evidence_type"]


def test_context_guarded_unknown_mentions_are_not_a_relocation_decision():
    result = apply_support_to_job(scanner(), JOB, BASE + INTERNAL)
    assert result["visa_sponsorship"] == "Unknown"
    assert result["relocation_support"] == "Unknown"
    assert result["relocation_required"] == "Unknown"
    assert result["support_confidence"] == 0.0
    assert result["support_evidence"] == ""


def test_explicit_support_verdicts_still_produce_evidence():
    text = BASE + " We offer visa sponsorship for this role. We offer relocation assistance."
    result = apply_support_to_job(scanner(), JOB, text)
    assert result["visa_sponsorship"] == "Y"
    assert result["relocation_support"] == "Y"
    assert result["support_confidence"] > 0
    assert result["support_evidence"]
    assert result["support_evidence_url"] == JOB["url"]


# ---------------------------------------------------------------------------
# Assisted JD-loop accounting and card extraction
# ---------------------------------------------------------------------------

def test_defer_unattempted_details_marks_only_unread_rows():
    rows = [
        {"jd_read_status": "readable"},
        {"jd_read_status": "unattempted"},
        {},
        {"jd_read_status": "blocked"},
        {"jd_read_status": "budget_skipped"},
    ]
    count = bp._defer_unattempted_details(rows, "assisted browser closed during JD enrichment")
    assert count == 2
    assert rows[0]["jd_read_status"] == "readable"
    assert rows[1]["jd_read_status"] == "unattempted"
    assert rows[1]["jd_reason"] == "assisted browser closed during JD enrichment"
    assert rows[1]["jd_defer_kind"] == "session_closed"
    assert rows[2]["jd_read_status"] == "unattempted"
    assert rows[2]["jd_defer_kind"] == "session_closed"
    assert rows[3]["jd_read_status"] == "blocked"
    assert rows[4]["jd_read_status"] == "budget_skipped"


class _FakeCardElement:
    def __init__(self, text):
        self._text = text

    def as_element(self):
        return self

    def inner_text(self):
        return self._text


class _FakeAnchor:
    def __init__(self, text="", fail=False):
        self._text = text
        self._fail = fail

    def evaluate_handle(self, _script):
        if self._fail:
            raise RuntimeError("closed")
        return _FakeCardElement(self._text)


def test_bounded_card_text_uses_individual_card_container():
    card = "Backend Engineer\nLocation: Berlin, Germany\nWork with a collaborative team."
    assert bp._bounded_card_text(_FakeAnchor(card)) == card


def test_bounded_card_text_fails_closed():
    assert bp._bounded_card_text(_FakeAnchor(fail=True)) == ""


class _RecordingAnchor(_FakeAnchor):
    def __init__(self, text="", fail=False):
        super().__init__(text, fail)
        self.script = ""

    def evaluate_handle(self, script):
        self.script = script
        return super().evaluate_handle(script)


def test_bounded_card_text_script_rejects_multi_card_outer_container():
    anchor = _RecordingAnchor("Backend Engineer\nBerlin, Germany\nGreat team.")
    assert bp._bounded_card_text(anchor) == "Backend Engineer\nBerlin, Germany\nGreat team."
    assert "found.length > 1" in anchor.script
    assert "return null" in anchor.script


def test_assisted_core_uses_bounded_card_text_and_records_run_id():
    src = CORE.read_text(encoding="utf-8")
    assert "_bounded_card_text(" in src
    assert "found.length > 1" in src
    assert "len(text) <= 4000" in src
    assert '"run_id": run_id' in src
    assert "detail_interrupted" in src
    assert "assisted browser closed during JD enrichment" in src


# ---------------------------------------------------------------------------
# UI honesty pins (source-level; do not require a display or PySide6 here)
# ---------------------------------------------------------------------------

def test_outcome_reports_unattempted_count_and_accurate_unknown_label():
    src = UI.read_text(encoding="utf-8")
    assert "not attempted: {unattempted}" in src
    assert "deferred/protected: {protected}" in src
    assert "Readable JDs with Unknown support verdict: {unknown}." in src
    assert "coverage.get('unattempted',0)" in src
    assert "coverage.get('protected',0)" in src


def test_attention_label_has_neutral_idle_state():
    src = UI.read_text(encoding="utf-8")
    i18n = I18N.read_text(encoding="utf-8")
    assert "Verification controls are inactive." in src
    assert "Verification controls are inactive." in i18n
    assert "info.get('url')" in src


def test_partial_dialog_explains_resume_path():
    src = UI.read_text(encoding="utf-8")
    i18n = I18N.read_text(encoding="utf-8")
    assert "Ingestion Partial" in src
    assert "Ingestion Partial" in i18n
    assert "Use Resume unread JDs to continue" in src
    assert "Use Resume unread JDs to continue" in i18n


def test_session_details_uses_stable_content_read():
    src = (CORE.parent / "session_details.py").read_text(encoding="utf-8")
    assert "_page_content_stable(" in src
    assert "verification_retry" in src
