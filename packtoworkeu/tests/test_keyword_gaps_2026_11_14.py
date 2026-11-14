"""User keyword audit (2026-11-14): expat packages + international talent.

Verified before this change: work authorisation, work visa, sponsorship,
relocation support/package and immigration services/support were already
concepts; "expat package" standalone and "international talent" were not.
Expat packages are now a relocation concept (benefit noun required);
international talent/hires phrasing surfaces only as a low-confidence
evidence mention and can never produce a verdict on its own.
"""
from packtoworkeu.scanning.jd_support import JDSupportDetector

D = JDSupportDetector()


def _v(text, kind):
    r = D.detect(text)[kind]
    return r["verdict"], r["confidence"], [e[2] for e in r["evidence"]]


def test_expat_package_is_a_relocation_offer():
    assert _v("Expat package included for this role.", "relocation")[:2] == ("Yes", 0.9)
    assert _v("We offer an expatriate benefits package to senior hires.",
              "relocation")[0] == "Yes"
    assert _v("Expat package", "relocation")[:2] == ("Yes", 0.8)  # bare list entry
    assert _v("Expat package: Yes", "relocation")[:2] == ("Yes", 0.95)  # label field


def test_expat_package_keeps_polarity_and_context_guards():
    assert _v("No expat package is provided for this role.", "relocation")[0] == "No"
    # Equipment/office-move context is not a candidate benefit (existing guard).
    verdict, conf, _ = _v(
        "Relocation of servers and expat packages for the office move.", "relocation")
    assert (verdict, conf) == ("Unknown", 0.0)
    # Bare mention with no offer verb stays a mention.
    assert _v("Competitive salary and expat packages for relocating employees.",
              "relocation")[:2] == ("Unknown", 0.2)


def test_international_talent_is_evidence_only():
    verdict, conf, flags = _v(
        "International talent is welcome to apply for this position.", "visa")
    assert (verdict, conf) == ("Unknown", 0.2)
    assert flags == [["international-talent-mention"]]
    # A positive verb must NOT promote the phrase to a sponsorship Yes.
    assert _v("We support international talent to grow their careers here.",
              "visa")[:2] == ("Unknown", 0.2)
    # "International team" (pure ambiance) does not even surface.
    assert _v("Join our international team of engineers from 40 countries.",
              "visa")[:2] == ("Unknown", 0.0)


def test_international_talent_with_real_offer_still_yes():
    assert _v("We hire international talent and offer visa sponsorship.",
              "visa")[:2] == ("Yes", 0.9)


def test_preexisting_keywords_unaffected():
    assert _v("We provide work authorisation support for successful candidates.",
              "visa")[:2] == ("Yes", 0.9)
    assert _v("You must have work authorisation in Germany.", "visa")[0] == "No"
    assert _v("We will sponsor your work visa and handle the application.",
              "visa")[:2] == ("Yes", 0.9)
    assert _v("Our immigration services team will handle your paperwork.",
              "visa")[:2] == ("Yes", 0.9)
    assert _v("Relocation package: Yes", "relocation")[:2] == ("Yes", 0.95)
