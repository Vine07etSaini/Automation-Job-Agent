from datetime import datetime, timedelta

import pytest

from job_copilot.agents.freshness import is_fresh, parse_posted
from job_copilot.agents.honesty_guard import guard
from job_copilot.agents.matcher import score_job
from job_copilot.agents.skill_bank import SkillBank
from job_copilot.config import IST, get_profile
from job_copilot.models import JDAnalysis, ProjectRewrite, ScoreWeights, SkillRequirement, TailoredContent

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=IST)


@pytest.mark.parametrize("text,days", [
    ("Just now", 0), ("Today", 0), ("Few hours ago", 0), ("1 day ago", 1), ("3 Days Ago", 3),
    ("Posted: 5 days ago", 5), ("30+ days ago", 31), ("a week ago", 7), ("2 weeks ago", 14),
])
def test_parse_relative(text, days):
    assert (NOW - parse_posted(text, NOW)).days == days


def test_parse_absolute_and_unknown():
    assert parse_posted("03 Oct 2026", NOW).date().isoformat() == "2026-10-03"
    assert parse_posted("", NOW) is None
    assert parse_posted("whenever", NOW) is None


def test_is_fresh():
    assert is_fresh(NOW - timedelta(days=6), 7, NOW) is True
    assert is_fresh(NOW - timedelta(days=8), 7, NOW) is False
    assert is_fresh(None, 7, NOW) is None


@pytest.fixture
def profile():
    return get_profile()


@pytest.fixture
def bank(profile):
    return SkillBank(profile)


def test_skill_bank_aliases_and_equivalents(bank):
    assert bank.canonical("python3") == "Python"
    assert bank.canonical("RESTful API") == "REST APIs"
    assert bank.match("GCP") == ("AWS", True)
    assert bank.match("Kubernetes") is None


def jd(mandatory, nice=(), lo=None, hi=None):
    return JDAnalysis(
        role_title="x",
        mandatory_skills=[SkillRequirement(skill=s, alternatives=alts) for s, alts in mandatory],
        nice_to_have_skills=list(nice), min_experience_years=lo, max_experience_years=hi,
        keywords=[], summary="",
    )


def test_score_good_and_bad(profile, bank):
    good = score_job(profile, jd([("Python", []), ("GCP", ["AWS"]), ("PostgreSQL", [])], ["Docker"], 3, 6),
                     bank, ScoreWeights())
    assert good.score == 100 and not good.missing_mandatory

    bad = score_job(profile, jd([("Java", []), ("Spring Boot", []), ("Python", [])], [], 6, 10), bank, ScoreWeights())
    assert bad.score < 70
    assert bad.missing_mandatory == ["Java", "Spring Boot"]
    assert "missing Java" in bad.explanation


def test_guard_blocks_invented_skills(profile, bank):
    analysis = jd([("Python", []), ("Kafka", []), ("Kubernetes", [])])
    draft = TailoredContent(
        summary="Python engineer experienced with Kafka.",
        skills=["Python", "Kafka", "AWS"],
        projects=[
            ProjectRewrite(project_id="order-service",
                           description="Order API in Python and FastAPI deployed with Kubernetes."),
            ProjectRewrite(project_id="job-copilot", description="Python agent using Playwright and an LLM."),
            ProjectRewrite(project_id="made-up", description="Nope"),
        ],
        keywords_used=["Python"],
    )
    cleaned, report = guard(draft, profile, analysis, bank)
    assert not report.ok
    assert "Kafka" not in cleaned.skills and cleaned.skills[:2] == ["Python", "AWS"]
    assert cleaned.summary == profile.summary  # reverted
    by_id = {p.project_id: p.description for p in cleaned.projects}
    assert "Kubernetes" not in by_id["order-service"]  # reverted to original
    assert by_id["job-copilot"] == "Python agent using Playwright and an LLM."  # honest rewrite kept
    assert "made-up" not in by_id


def test_guard_blocks_keyword_stuffing(profile, bank):
    analysis = jd([("Python", [])])
    draft = TailoredContent(summary=profile.summary, skills=["Python"], keywords_used=[], projects=[
        ProjectRewrite(project_id="job-copilot", description="Python Python Python agent in Python."),
    ])
    cleaned, report = guard(draft, profile, analysis, bank, max_repeats=2)
    assert any("stuffing" in v for v in report.violations)
