"""Round 7 (2026-10-19): ATS TLS trust store and mobility-role false Yes.

1. Spendesk ERROR on a user's machine: urllib raised CERTIFICATE_VERIFY_FAILED
   ("unable to get local issuer certificate"). The ATS fetch now uses the OS
   certificate store via truststore; verification is never turned off.
2. Mistral "International mobility expert" was Yes on generic benefit text
   ("relocation support" in a perks list). Guard 4 (job is the function) did
   not fire, because the role list had no "expert".
"""
import ssl
import sys
from pathlib import Path

from sponsorscout.scanning import common
from sponsorscout.scanning.ats import ats_scanner as ats
from sponsorscout.scanning.career import career_scanner as cs

REPO = Path(__file__).resolve().parents[1]


def test_tls_context_always_verifies_certificates():
    ctx = common.tls_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_tls_context_falls_back_to_default_without_truststore(monkeypatch):
    monkeypatch.setitem(sys.modules, "truststore", None)  # import raises ImportError
    ctx = common.tls_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_ats_fetch_paths_use_tls_context():
    src = Path(ats.__file__).read_text(encoding="utf-8")
    assert "context=ssl.create_default_context()" not in src
    assert src.count("context=_tls_context()") >= 3


def test_truststore_is_declared_as_a_dependency():
    req = (REPO.parent / "requirements.txt").read_text(encoding="utf-8")
    pyproject = (REPO.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert "truststore" in req
    assert "truststore" in pyproject


def test_mobility_expert_with_generic_benefits_is_not_a_yes():
    text = ("Coordinate immigration, tax and payroll topics with internal teams. "
            "Benefits may include healthcare, relocation support and meal allowances.")
    visa, reloc = ats.apply_support_fp_guards(
        ats.VERDICT_YES, ats.VERDICT_YES, text, "International mobility expert")
    assert visa == ats.VERDICT_UNKNOWN
    assert reloc == ats.VERDICT_UNKNOWN


def test_career_guard_has_the_same_role_list():
    assert cs._SUPPORT_FUNCTION_ROLE_RE.search("international mobility expert")
    assert ats._SUPPORT_FUNCTION_ROLE_RE.search("international mobility expert")


def test_real_sponsorship_offer_still_yes_for_a_normal_title():
    visa, _ = ats.apply_support_fp_guards(
        ats.VERDICT_YES, ats.VERDICT_UNKNOWN,
        "We provide full visa sponsorship for the right candidate.", "Senior Engineer")
    assert visa == ats.VERDICT_YES


def test_real_sponsorship_offer_still_yes_for_a_mobility_title_without_the_function():
    visa, _ = ats.apply_support_fp_guards(
        ats.VERDICT_YES, ats.VERDICT_UNKNOWN,
        "We provide full visa sponsorship for the right candidate.", "Software Engineer")
    assert visa == ats.VERDICT_YES
