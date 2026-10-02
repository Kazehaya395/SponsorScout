"""Post-classification integrity checks for sponsorship / relocation / Blue Card.

WHY THIS EXISTS
---------------
``jd_support.JDSupportDetector`` reads the JD TEXT only.  It has no idea where
the job is.  That is correct in isolation, but it means a text-level "we sponsor
visas" in a posting whose location is, say, Pune, India produces
``Visa Sponsorship = Yes`` on an Indian job — the screenshot case that started
this module:

    Security Project Manager | A2G Technologies | India | Pune, India
    Experience 5+ | Sponsor Y | Blue Card ? | Reloc ? | onsite

A visa sponsorship offer is only *actionable* when the employer sits in a
jurisdiction where the candidate actually needs one.  For a role performed
onsite in India, sponsorship is at best irrelevant boilerplate and at worst a
false positive that sends the user chasing a benefit that cannot apply.

This module applies the location-aware guards that the text detector cannot.
It never upgrades a verdict and never invents one: the only transition it can
make is ``Yes -> Unknown``, i.e. it can only REMOVE false confidence, which is
the safe direction (Unknown renders as "?" in the UI, never as a hard "No").

THE RULES
---------
1. ``visa_sponsorship`` may only be ``Yes`` when the job's country is one where
   a foreign applicant would plausibly need sponsorship — an EEA/Swiss/UK
   region.  Elsewhere it is demoted to ``Unknown``.
2. ``eu_blue_card_verdict`` may only be ``Yes`` when the country is an actual
   EU member state.  The Blue Card is an EU instrument; it cannot be granted
   from India, the UK, Switzerland, or the US.  Demoted to ``Unknown``.
3. The legacy booleans (``eu_blue_card`` / ``has_relocation``) are re-derived
   from the (possibly demoted) verdicts so they can never disagree with the
   authoritative three-state columns.

An UNKNOWN country is deliberately permissive: absence of a location is not
evidence against sponsorship, so rule 1 does not fire.  Only a *positively
identified* non-EEA country demotes a verdict.
"""
from __future__ import annotations

#: EU member states (the Blue Card is an EU instrument).
EU_COUNTRIES: frozenset[str] = frozenset({
    "Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic",
    "Denmark", "Estonia", "Finland", "France", "Germany", "Greece",
    "Hungary", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg",
    "Malta", "Netherlands", "Poland", "Portugal", "Romania", "Slovakia",
    "Slovenia", "Spain", "Sweden",
})

#: Countries where visa sponsorship is a meaningful candidate benefit even
#: though the Blue Card does not apply (EFTA + the UK + the two EU-outer
#: arrangements commonly advertised alongside EU postings).
SPONSORSHIP_REGIONS: frozenset[str] = EU_COUNTRIES | frozenset({
    "United Kingdom", "Switzerland", "Norway", "Iceland", "Liechtenstein",
})

_VERDICT_YES = "Yes"
_VERDICT_NO = "No"
_VERDICT_UNKNOWN = "Unknown"

#: The vocabulary actually stored in the ``jobs`` table. ``pipeline._as_verdict``
#: normalises every scanner cell to 'Y' / 'N' / 'Unknown' (see
#: tests/test_pipeline.py::test_row_to_job_maps_verdicts), and the Search tab
#: renders that same trio. The long 'Yes'/'No' spellings are accepted too so
#: this module is safe to call on a hand-built dict.
_STORED_YES = frozenset({"y", "yes", "true", "1"})
_STORED_NO = frozenset({"n", "no", "false", "0"})


def _verdict_is_yes(value) -> bool:
    """True when a verdict cell means Yes, in EITHER vocabulary.

    BUGFIX: this guard compared against the literal ``"Yes"`` only, while the
    pipeline stores ``"Y"``. The two never matched, so ``check_integrity`` was
    dead code in production: it demoted nothing, and every text-level "we
    sponsor visas" survived even on a posting in a country where sponsorship
    cannot apply. It is called once per ingested row, so the failure was silent
    and total -- the whole module was inert.
    """
    return str(value or "").strip().casefold() in _STORED_YES


def sponsorship_region_applies(country: str) -> bool:
    """True when ``country`` is one where visa sponsorship can plausibly apply.

    An empty/unknown country returns True on purpose: a missing location is not
    evidence against sponsorship, so the guard must not fire on thin data.
    """
    c = (country or "").strip()
    if not c:
        return True
    return c in SPONSORSHIP_REGIONS


def eu_blue_card_applies(country: str) -> bool:
    """True when ``country`` is an EU member state (Blue Card instrument)."""
    c = (country or "").strip()
    if not c:
        return True
    return c in EU_COUNTRIES


def check_integrity(job: dict) -> tuple[dict, list[str]]:
    """Return ``(job, violations)`` after applying the location-aware guards.

    ``job`` is the same mapping object (mutated in place) so callers can keep
    using their existing plumbing.  ``violations`` is a list of short, stable
    rule identifiers ("visa-sponsorship-outside-region", ...) suitable for the
    scan log / diagnostics column.

    Only ``Yes -> Unknown`` demotions are performed.  ``No`` is left untouched:
    an explicit refusal in the text is real evidence regardless of location.
    """
    violations: list[str] = []
    country = (job.get("country") or "").strip()

    visa = job.get("visa_sponsorship")
    if _verdict_is_yes(visa) and not sponsorship_region_applies(country):
        job["visa_sponsorship"] = _VERDICT_UNKNOWN
        violations.append(
            f"visa-sponsorship-outside-region:{country}")

    blue = job.get("eu_blue_card_verdict")
    if _verdict_is_yes(blue) and not eu_blue_card_applies(country):
        job["eu_blue_card_verdict"] = _VERDICT_UNKNOWN
        violations.append(
            f"blue-card-outside-eu:{country}")

    # Re-derive the legacy booleans so they can never contradict the
    # authoritative three-state columns after a demotion.
    job["eu_blue_card"] = 1 if _verdict_is_yes(
        job.get("eu_blue_card_verdict")) else 0
    job["has_relocation"] = 1 if _verdict_is_yes(
        job.get("relocation_support")) else 0

    return job, violations