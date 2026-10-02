"""Viability corpus for the sponsorship / relocation / Blue Card classifier.

`scanning/jd_support.py` is the app's main feature: it decides whether a job
offers visa sponsorship, relocation support or the EU Blue Card. The audit
below found it silently mis-classifying the two things that matter most for a
Europe-targeted sponsorship tool:

  * a HEDGED offer ("visa sponsorship is not guaranteed") returned a hard
    ``No`` — hiding real jobs from the user;
  * "right to work" / "Aufenthaltserlaubnis" (the standard UK/IE and DE
    phrasings) matched no concept at all, so the most common European
    pre-condition produced ``Unknown`` with confidence 0.0.

These cases are the regression net for that. Each one is a real phrasing from
a European job advert, not a synthetic keyword.

Keep the tri-state contract: ``Unknown`` must never be collapsed into ``No``,
and a hedged offer must never be reported as a refusal.
"""
import pytest

from sponsorscout.scanning.jd_support import JDSupportDetector, detect_blue_card


@pytest.fixture(scope="module")
def det():
    return JDSupportDetector()


def _visa(det, text):
    return det.detect(text)["visa"]["verdict"]


# ── Positive: the employer offers sponsorship ─────────────────────────────────

@pytest.mark.parametrize("text", [
    "We offer visa sponsorship for this role.",
    "Visa sponsorship is available for this position.",
    "We will sponsor your visa application.",
    "Sponsorship is provided for highly skilled candidates.",
    "Skilled Worker visa sponsorship available.",
    "We sponsor Tier 2 visas.",
    # DE
    "Wir bieten Visumsponsoring an.",
    "Wir stellen ein Visum bereit.",
    # IT
    "Offriamo supporto per il visto.",
    # NL — "mogelijk" is how a Dutch ad says it is on offer.
    "Visumsponsoring is mogelijk.",
    # FR — every conjugation of sponsoriser/parrainer.
    "Nous sponsorisons votre visa.",
    "Nous parrains votre visa.",
    "Nous prenons en charge le visa.",
    # ES
    "Ofrecemos patrocinio de visado.",
])
def test_detects_sponsorship_offers(det, text):
    assert _visa(det, text) == "Yes", text


# ── Negative: the employer will not sponsor ──────────────────────────────────

@pytest.mark.parametrize("text", [
    "We do not offer visa sponsorship.",
    "No visa sponsorship is available.",
    "Unfortunately we cannot sponsor visas.",
    "We do not sponsor visas.",
    "Applicants who need sponsorship will not be considered.",
])
def test_detects_explicit_refusals(det, text):
    assert _visa(det, text) == "No", text



# ── Pre-condition: the candidate must already be authorised ──────────────────

@pytest.mark.parametrize("text", [
    # requirement stated BEFORE the noun
    "Applicants must have the right to work in the UK.",
    "You must have the right to work in Germany.",
    "You must be authorized to work in the EU.",
    "You must have your own work permit.",
    "Applicants must be eligible to work in the EU.",
    # requirement stated AFTER it (copula-less in the DE phrasing)
    "A valid work permit is required.",
    "Aufenthaltserlaubnis required.",
    "Anmeldebescheinigung required.",
])
def test_candidate_must_already_be_authorised(det, text):
    assert _visa(det, text) == "No", text


# ── Hedges stay Unknown — the single most damaging regression ────────────────

@pytest.mark.parametrize("text", [
    "Sponsorship may be available on a case-by-case basis.",
    "Visa sponsorship is not guaranteed.",
    "Sponsoring is not guaranteed.",
])
def test_hedged_offers_are_never_a_hard_refusal(det, text):
    verdict = _visa(det, text)
    assert verdict == "Unknown", (
        "%r was reported as %r: a hedged offer must not be shown as a refusal"
        % (text, verdict))


# ── No evidence at all stays Unknown ─────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "You will be part of a global team in Berlin.",
    "You will be working on our payments platform.",
])
def test_no_evidence_is_unknown_not_no(det, text):
    assert _visa(det, text) == "Unknown", text


# ── Relocation is judged independently of sponsorship ────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Relocation package offered.", "Yes"),
    ("We offer relocation assistance.", "Yes"),
    ("Umzugskosten werden erstattet.", "Yes"),
    ("You must be willing to relocate at your own cost.", "No"),
    ("Relocation may be available on request.", "Unknown"),
])
def test_relocation_detection(det, text, expected):
    assert det.detect(text)["relocation"]["verdict"] == expected, text


# ── Blue Card is independent of visa sponsorship ─────────────────────────────

def test_blue_card_is_judged_on_its_own_terms(det):
    assert detect_blue_card(det, "We support the EU Blue Card.") == "Yes"
    assert detect_blue_card(det, "We do not offer the EU Blue Card.") == "No"
    # Merely welcoming existing holders is not a claim to sponsor anyone.
    assert detect_blue_card(det, "Applicants with an EU Blue Card are welcome.") \
        == "Unknown"


# ── Pattern-hygiene guards (2026-09 false-positive incident) ─────────────────

"""Independent guard against the 2026-09 empty-alternation false positives.

An edit to VISA_CONCEPTS left a `||` between two wrapped source lines, which is
an EMPTY alternation branch. That makes `\\b(...)\\b` match zero characters at
every word boundary, so EVERY sentence counted as a visa mention and any
nearby positive verb ("support", "provide", "cover") turned it into a
confident "Yes".

These tests deliberately avoid calling detect() so they cannot pass merely
because the verdict logic happens to compensate. They interrogate the compiled
regex directly.
"""

import random

import pytest

# Ordinary job prose that contains no visa, work-authorisation or relocation
# vocabulary whatsoever. If an empty branch is ever reintroduced, this matches.
_VISA_FREE_VOCABULARY = (
    "the role support customer manager team we offer excellent benefits and you "
    "will work with our international partners to deliver results a b c d e 1 2 3 "
    "p ul li amp , ; : ! ? - _ / anmeldecommuting verblijf aufenthalt provide "
    "assist cover handle ensure guarantee relocating people meeting engineering "
    "design product marketing sales finance hr legal operations"
).split()


@pytest.mark.parametrize("rx_name", [
    "VISA_CONCEPTS", "RELOCATION_CONCEPTS", "POSITIVE_VERBS", "NEGATION",
    "REQUIREMENT", "CONDITIONAL", "SCOPE", "HEDGED_NEGATION",
])
def test_no_detector_pattern_has_an_empty_alternative(det, rx_name):
    """Structural: inspect the source pattern, not just observed behaviour."""
    pattern = getattr(det, rx_name).pattern
    assert "||" not in pattern, (
        "%s contains '||' -- an empty alternation branch" % rx_name)
    assert not pattern.rstrip().rstrip(")").endswith("|"), (
        "%s ends with a trailing '|' -- another empty branch" % rx_name)


def test_visa_concepts_never_matches_zero_width(det):
    """Every match must consume at least one character."""
    for text in [
        "Support of the country manager",
        "Meet Your Team: Japan Operations",
        "plain text with no visa words at all",
        "", " ", "   ", "\n", ".", "|", "||", "<p></p>", "-", "the",
    ]:
        m = det.VISA_CONCEPTS.search(text)
        assert m is None or m.group(0), (
            "VISA_CONCEPTS matched %r in %r -- an empty alternation branch is "
            "present, so every sentence will read as a visa mention"
            % (m.group(0) if m else "", text))


def test_visa_concepts_ignores_a_large_visa_free_corpus(det):
    """Fuzz a visa-free vocabulary; expect no match at all.

    Seeded so a failure is reproducible rather than intermittent.
    """
    rng = random.Random(20260910)
    vocabulary = _VISA_FREE_VOCABULARY
    offenders = [w for w in sorted(set(vocabulary))
                 if det.VISA_CONCEPTS.search(w) is not None]
    assert not offenders, (
        "visa-free words matched VISA_CONCEPTS: %r" % offenders)

    for _ in range(20000):
        text = " ".join(rng.choice(vocabulary)
                        for _ in range(rng.randint(1, 14)))
        m = det.VISA_CONCEPTS.search(text)
        assert m is None or m.group(0), (
            "zero-width match inside visa-free text: %r" % text)


def test_visa_concepts_still_matches_real_vocabulary(det):
    """Positive control -- the fix must not simply disable the concept list."""
    for term in [
        "visa", "visas", "visa sponsorship", "work permit", "right to work",
        "Aufenthaltserlaubnis", "verblijfsvergunning", "permis de travail",
        "sponsoring", "sponsorizzazione", "residence permit", "blue card",
        "H-1B", "permesso di soggiorno", "tarjeta azul", "wetten overplichting",
    ]:
        m = det.VISA_CONCEPTS.search(term)
        assert m is not None and m.group(0), (
            "%r is no longer recognised as a visa concept -- the fix over-"
            "corrected" % term)


def test_visa_concepts_rejects_ordinary_job_prose(det):
    """Real sentences captured from the bad scan must not read as sponsorship."""
    for text in [
        "Support of the country manager in the development of new business fields",
        "You'll manage both, and over time help us bring accounting back in-house",
        "Meet Your Team: Japan Operations & Support; supporting our Sales team",
        "Coordinate and support cross-functional projects by aligning stakeholders",
        "We will support you with the implementation",
        "Provide customer support across all channels",
    ]:
        verdict = det.detect(text)["visa"]
        assert verdict["verdict"] != "Yes", (
            "%r was classified as a sponsorship offer (conf %s)"
            % (text, verdict["confidence"]))
        assert verdict["confidence"] == 0.0, (
            "%r produced evidence with no visa concept present" % text)


# ── One definition of worker sizing (2026-09 audit) ───────────────────────────

def test_career_scanner_uses_the_shared_worker_sizer():
    """career_scanner must not shadow common.recommended_workers.

    It used to define its own copy LATER in the file, which shadowed the
    import — so the scanner ran on different numbers than the pipeline sized
    it with. Measured on a 2-core / 8 GB box: the browser pool agreed (1 == 1)
    but the HTTP detail pool diverged 3 vs 6, i.e. six concurrent fetches on
    top of a Chromium on two cores.
    """
    from sponsorscout.scanning.career import career_scanner as career
    from sponsorscout.scanning import common

    assert career.recommended_workers is common.recommended_workers
    assert career.check_control is common.check_control
    assert career.BROWSER_ARGS is common.BROWSER_ARGS
    assert career.JDSupportDetector.__module__ == "sponsorscout.scanning.jd_support"
    assert career.country_from_location.__module__ == \
        "sponsorscout.core.location_country"


def test_shared_sizer_preserves_the_requested_cap():
    """career_scanner's copy accepted requested=; the shared one must too."""
    from sponsorscout.scanning import common
    base = common.recommended_workers("http")
    assert common.recommended_workers("http", requested=1) == 1
    assert common.recommended_workers("http", requested=99) <= base
    assert common.recommended_workers("browser", requested=1) == 1


def test_host_budget_reports_memory_on_every_platform():
    """The self-diagnosis line must not say "unknown" on Windows.

    It used to fall back to os.sysconf (POSIX-only), so on the app's primary
    platform the run header always printed "unknown available RAM" — useless
    precisely on the low-end machines the line exists to help.
    """
    from sponsorscout.scanning.career import career_scanner as career
    line = career.describe_host_budget()
    assert "unknown" not in line, line
    assert "MB available RAM" in line
    assert "browser workers=" in line and "http workers=" in line


# ── Contract guards ──────────────────────────────────────────────────────────

def test_hedged_negation_pattern_is_actually_reachable(det):
    """CONDITIONAL lists "not guaranteed"; the negation branch used to win.

    If the two ever disagree again, a hedged offer silently becomes a refusal
    and real jobs vanish from the results with no visible error.
    """
    text = "Visa sponsorship is not guaranteed."
    assert det.CONDITIONAL.search(text), "hedge wording missing from CONDITIONAL"
    assert det.NEGATION.search(text), "hedge wording is a negation too"
    assert det.HEDGED_NEGATION.search(text), (
        "HEDGED_NEGATION must cover the phrasing CONDITIONAL already lists")


@pytest.mark.parametrize("text", [
    "right to work", "eligible to work", "work permit", "visa",
    "aufenthaltserlaubnis", "arbeitserlaubnis",
    "verblijfsvergunning", "permis de travail", "sponsoring", "sponsorship",
])
def test_european_work_authorisation_concepts_are_recognised(det, text):
    """The pre-condition vocabulary every European ad uses.

    A miss here means the sentence is invisible to the detector and the row
    degrades to Unknown with confidence 0.0 — the most common European case.

    Note the concept list pairs the permit NAME with its consequence:
    "residence permit" is both a European work-authorisation term (EU
    long-term resident, UK settlement) and, via the guard, the signal that the
    employer will not sponsor ("a residence permit is required").
    """
    assert det.VISA_CONCEPTS.search(text), "%r is not a visa concept" % text


@pytest.mark.parametrize("text", [
    "A residence permit is required",
    "Aufenthaltserlaubnis required",
    "Anmeldebescheinigung required",
    "A valid work permit is required",
    "You must have the right to work in the UK",
])
def test_precondition_wording_is_treated_as_no_sponsorship(det, text):
    """Post-posed and post-nominal requirements must read as 'No', not Unknown."""
    assert _visa(det, text) == "No", text


def test_detector_is_deterministic():
    """run_scan re-detects on every pass; a drifting verdict would be a bug."""
    a = JDSupportDetector()
    b = JDSupportDetector()
    text = ("Relocation package offered. Visa sponsorship is not guaranteed. "
            "Applicants must have the right to work in the UK.")
    for _ in range(5):
        assert a.detect(text) == b.detect(text)