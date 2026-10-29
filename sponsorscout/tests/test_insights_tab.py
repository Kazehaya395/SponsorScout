"""Tests for Insights tab data extraction, database storage, and query engine."""

import sqlite3
import pytest
from pathlib import Path

from sponsorscout.insights import terms, queries
from sponsorscout.db.database import initialize, get_connection
from sponsorscout.core.persistence import upsert_job


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_insights.db"
    initialize(str(db_file))
    return str(db_file)


def test_category_classification():
    # Title rule matches
    assert terms.classify_category("Senior Python Software Engineer") == "Engineering & IT"
    assert terms.classify_category("Lead Data Scientist (NLP)") == "Data & Analytics"
    assert terms.classify_category("Financial Controller") == "Finance & Accounting"
    assert terms.classify_category("Account Executive - Enterprise") == "Sales & Business Development"
    assert terms.classify_category("Digital Marketing Manager") == "Marketing & Communications"
    assert terms.classify_category("Supply Chain Coordinator") == "Operations & Logistics"
    assert terms.classify_category("Technical Support Specialist") == "Customer Service"
    assert terms.classify_category("Talent Acquisition Partner") == "HR & Recruitment"
    assert terms.classify_category("Registered Nurse") == "Healthcare & Life Sciences"
    assert terms.classify_category("Chef de Partie") == "Hospitality & Food Service"
    assert terms.classify_category("Store Manager") == "Retail & Store Operations"
    assert terms.classify_category("Legal Counsel EMEA") == "Legal & Regulatory"
    assert terms.classify_category("Maintenance Technician") == "Manufacturing, Trades & Maintenance"
    # Unmatched fallback to Other
    assert terms.classify_category("Special Operative Wizard") == "Other"


def test_domain_classification():
    assert terms.classify_domain("Fintech") == "Financial Services & Fintech"
    assert terms.classify_domain("SaaS") == "Tech & Software"
    assert terms.classify_domain("Pharma") == "Healthcare, Pharma & Life Sciences"
    assert terms.classify_domain("Retail/DIY") == "E-Commerce, Retail & Fashion"
    assert terms.classify_domain("Job Aggregator/Visa") == "Job Aggregator"
    assert terms.classify_domain("Unknown") == "Unknown"
    assert terms.classify_domain("") == "Unknown"


def test_skill_extraction():
    text = (
        "We are looking for a Senior Developer with strong Python, AWS, Docker, "
        "and PostgreSQL experience. Knowledge of React and Kubernetes is a plus."
    )
    skills = terms.extract_skills(text)
    assert "Python" in skills
    assert "AWS" in skills
    assert "Docker" in skills
    assert "PostgreSQL" in skills
    assert "React" in skills
    assert "Kubernetes" in skills
    # Ensure no false positive on short words
    assert "Go" not in skills  # lowercase 'go' in 'looking for' shouldn't match case-sensitive Go


def test_insights_db_ingestion_and_queries(test_db):
    # Ingest a sample job with terms
    with get_connection(test_db) as conn:
        job1 = {
            "title": "Backend Engineer",
            "company": "Adyen",
            "country": "Netherlands",
            "location": "Amsterdam, Netherlands",
            "url": "https://example.com/job1",
            "industry": "Fintech",
            "visa_sponsorship": "Y",
            "relocation_support": "Y",
            "category": "Engineering & IT",
            "domain": "Financial Services",
            "terms": [("skill", "Python"), ("skill", "AWS"), ("skill", "Docker")],
        }
        upsert_job(conn, job1, commit=True)

        job2 = {
            "title": "Data Analyst",
            "company": "Booking.com",
            "country": "Netherlands",
            "location": "Amsterdam, Netherlands",
            "url": "https://example.com/job2",
            "industry": "Travel",
            "visa_sponsorship": "Unknown",
            "relocation_support": "Unknown",
            "category": "Data & Analytics",
            "domain": "Travel & Hospitality",
            "terms": [("skill", "Python"), ("skill", "SQL")],
        }
        upsert_job(conn, job2, commit=True)

    # Test queries
    cov = queries.get_insights_coverage(test_db)
    assert cov["total_jobs"] == 2
    assert cov["categorized_jobs"] == 2
    assert cov["jobs_with_skills"] == 2

    cats = queries.get_jobs_by_category(test_db)
    cat_names = [c[0] for c in cats]
    assert "Engineering & IT" in cat_names
    assert "Data & Analytics" in cat_names

    top_skills = dict(queries.get_top_skills(test_db))
    assert top_skills.get("Python") == 2
    assert top_skills.get("AWS") == 1
    assert top_skills.get("SQL") == 1

    doms = dict(queries.get_jobs_by_domain(test_db))
    assert doms.get("Financial Services") == 1
    assert doms.get("Travel & Hospitality") == 1

    spon = queries.get_sponsorship_by_category(test_db)
    eng_spon = next(s for s in spon if s["category"] == "Engineering & IT")
    assert eng_spon["visa_yes"] == 1
    assert eng_spon["visa_unk"] == 0

    data_spon = next(s for s in spon if s["category"] == "Data & Analytics")
    assert data_spon["visa_yes"] == 0
    assert data_spon["visa_unk"] == 1
