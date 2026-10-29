"""Regression tests for 2026-10-20 user-reported issues:
1. Adyen: EU-internal visa & relocation support is not foreign national sponsorship.
2. Astroscale: 'Visa sponsorship for employees considered' -> Yes (conditional).
3. Bot-detection challenge: 'Press & Hold' recognised as bot challenge.
4. Leonardo: 'Air Ambulance relocation support' -> non-candidate relocation context.
5. Prysmian: 'skilled worker who supports...' -> not visa sponsorship unless visa concept.
6. Philips: 'supports pédagogiques visant à...' -> not visa concept.
7. Catawiki: removed from company_ATS_seed.csv, added to company_Career_seed.csv.
"""

from pathlib import Path
import csv
from sponsorscout.scanning.jd_support import JDSupportDetector
from sponsorscout.scanning.ats.ats_scanner import apply_support_fp_guards as ats_guards
from sponsorscout.scanning.career.career_scanner import apply_support_fp_guards as car_guards
from sponsorscout.scanning.career.career_scanner import CareerPortalScanner

REPO = Path(__file__).resolve().parents[2]
SEEDS = REPO / "sponsorscout" / "data" / "seeds"


def test_issue1_adyen_eu_internal_mobility_guard():
    det = JDSupportDetector()
    text = "We offer visa and relocation support within the EU for this role."
    sup = det.detect(text)
    visa = sup["visa"]["verdict"]
    reloc = sup["relocation"]["verdict"]
    assert visa == "Yes"
    assert reloc == "Yes"

    # Guards must downgrade to Unknown because support is restricted within the EU
    v_ats, r_ats = ats_guards(visa, reloc, text, "Engineer")
    v_car, r_car = car_guards(visa, reloc, text, "Engineer")
    assert v_ats == "Unknown"
    assert r_ats == "Unknown"
    assert v_car == "Unknown"
    assert r_car == "Unknown"

    # But international support stays Yes
    intl_text = "We offer visa and relocation support for international candidates."
    s_intl = det.detect(intl_text)
    vi_ats, ri_ats = ats_guards(s_intl["visa"]["verdict"], s_intl["relocation"]["verdict"], intl_text, "Engineer")
    assert vi_ats == "Yes"
    assert ri_ats == "Yes"


def test_issue2_astroscale_sponsorship_considered():
    det = JDSupportDetector()
    text = "Visa sponsorship for employees considered. Relocation allowance."
    sup = det.detect(text)
    assert sup["visa"]["verdict"] == "Yes"
    assert "positive-but-conditional" in sup["visa"]["evidence"][0][2]
    assert sup["relocation"]["verdict"] == "Yes"

    # Must survive candidate offer fp-guards
    v_ats, r_ats = ats_guards(sup["visa"]["verdict"], sup["relocation"]["verdict"], text, "Ground Segment Lead")
    assert v_ats == "Yes"
    assert r_ats == "Yes"


def test_issue3_bot_challenge_press_and_hold():
    scanner = CareerPortalScanner.__new__(CareerPortalScanner)
    class FakePage:
        def title(self):
            return "Please verify you are a human"
        def inner_text(self, selector):
            return "Press & Hold\nAccess to this page has been denied because we believe you are using automation tools."
        def content(self):
            return "<html><body>Press & Hold</body></html>"

    reason = scanner._bot_challenge_reason(FakePage())
    assert "challenge page served instead of the job list" in reason


def test_issue4_leonardo_air_ambulance_relocation_not_candidate():
    det = JDSupportDetector()
    text = (
        "You will be principally responsible for delivering projects that cover "
        "external interests including the LHUK Solar Farm PPA, Dorset & Somerset "
        "Air Ambulance relocation support, iAero interests and other external tenants."
    )
    sup = det.detect(text)
    assert sup["relocation"]["verdict"] == "Unknown"
    assert "non-candidate-relocation-context" in sup["relocation"]["evidence"][0][2]


def test_issue5_prysmian_skilled_worker_job_description():
    det = JDSupportDetector()
    text = (
        "The Cable Jointing Helper is a skilled worker who supports the "
        "construction, maintenance, and repair of electrical distribution systems."
    )
    sup = det.detect(text)
    assert sup["visa"]["verdict"] == "Unknown"

    # Legitimate Skilled Worker visa still detected
    legit = "We offer skilled worker visa sponsorship for eligible candidates."
    sup_legit = det.detect(legit)
    assert sup_legit["visa"]["verdict"] == "Yes"


def test_issue6_philips_french_pedagogical_support_visant():
    det = JDSupportDetector()
    text = (
        "Concevoir et développer des modules de formation avancés ainsi que des "
        "supports pédagogiques visant à renforcer les compétences cliniques des utilisateurs."
    )
    sup = det.detect(text)
    assert sup["visa"]["verdict"] == "Unknown"

    # Legitimate French visa statement still detected
    french_visa = "Nous fournissons des visas de travail pour les candidats internationaux."
    sup_fr = det.detect(french_visa)
    assert sup_fr["visa"]["verdict"] == "Yes"


def test_issue7_catawiki_seed_placement():
    with open(SEEDS / "company_ATS_seed.csv", encoding="utf-8") as f:
        ats_lines = [l.strip() for l in f if l.startswith("Catawiki,")]
    with open(SEEDS / "company_Career_seed.csv", encoding="utf-8-sig") as f:
        career_lines = [l.strip() for l in f if l.startswith("Catawiki,")]

    assert len(ats_lines) == 0, "Catawiki must be removed from company_ATS_seed.csv"
    assert len(career_lines) == 1, "Catawiki must be present in company_Career_seed.csv"
    assert "https://catawiki.careers/vacancies" in career_lines[0]


def test_issue8_anthropic_affirmative_commitment():
    det = JDSupportDetector()
    text = (
        "We do sponsor visas! However, we aren't able to successfully sponsor visas "
        "for every role and every candidate. But if we make you an offer, we will make "
        "every reasonable effort to get you a visa, and we retain an immigration lawyer to help with this."
    )
    sup = det.detect(text)
    assert sup["visa"]["verdict"] == "Yes"

    v_ats, r_ats = ats_guards(sup["visa"]["verdict"], sup["relocation"]["verdict"], text, "Anthropic Role")
    v_car, r_car = car_guards(sup["visa"]["verdict"], sup["relocation"]["verdict"], text, "Anthropic Role")

    assert v_ats == "Yes"
    assert v_car == "Yes"
