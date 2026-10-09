"""Extract structured requirements from a job description (LLM, with a heuristic fallback)."""

from __future__ import annotations

import logging
import re

from job_copilot.agents.skill_bank import SkillBank, find_term
from job_copilot.llm.chat import LLM
from job_copilot.models import JDAnalysis, JobPosting, SkillRequirement

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}\b")
EXP_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)\s*(?:yrs|years|yr)", re.I)
EXP_MIN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:yrs|years|yr)", re.I)

SYSTEM = """You are an expert technical recruiter who reads job descriptions precisely.
Extract only what the job description actually says. Do not guess or add skills that are not mentioned.
- mandatory_skills: concrete technical skills/tools that are required ("must have", "required", core stack).
  If the JD accepts alternatives (e.g. "AWS or GCP"), put one in `skill` and the rest in `alternatives`.
- nice_to_have_skills: skills marked as preferred, good to have, or a plus.
- Use short canonical skill names ("PostgreSQL", "Docker", "REST APIs"), not sentences.
- Experience: numbers in years; null if not stated.
- recruiter_email / recruiter_phone: only if literally present in the text, else null."""


def analyze_jd(posting: JobPosting, bank: SkillBank, llm: LLM | None = None) -> JDAnalysis:
    if llm is not None:
        prompt = (
            f"Job title: {posting.title}\nCompany: {posting.company}\n"
            f"Experience (from listing): {posting.experience_text or 'n/a'}\n"
            f"Tags: {', '.join(posting.tags) or 'n/a'}\n\n"
            f"Job description:\n{posting.description[:15000]}"
        )
        analysis = llm.generate_json(prompt, JDAnalysis, system=SYSTEM)
    else:
        log.info("No LLM configured; using heuristic JD analysis for %s", posting.title)
        analysis = heuristic_analysis(posting, bank)
    return _fill_from_text(analysis, posting)


def heuristic_analysis(posting: JobPosting, bank: SkillBank) -> JDAnalysis:
    """Offline fallback: portal tags are treated as mandatory, skill-bank hits in the text as nice-to-have."""
    text = f"{posting.title}\n{posting.description}"
    mandatory = [SkillRequirement(skill=t, alternatives=[]) for t in posting.tags]
    tagged = {t.lower() for t in posting.tags}
    nice = [
        s for s in bank.names
        if s.lower() not in tagged and any(find_term(text, f) for f in bank.surface_forms(s))
    ]
    return JDAnalysis(
        role_title=posting.title,
        mandatory_skills=mandatory,
        nice_to_have_skills=nice,
        keywords=[*posting.tags, *nice],
        summary=posting.description[:200],
    )


def _fill_from_text(a: JDAnalysis, p: JobPosting) -> JDAnalysis:
    text = p.description
    if not a.recruiter_email and (m := EMAIL_RE.search(text)):
        a.recruiter_email = m.group(0).rstrip(".")
    if not a.recruiter_phone and (m := PHONE_RE.search(text)):
        a.recruiter_phone = m.group(0)
    if a.min_experience_years is None:
        lo, hi = parse_experience(p.experience_text or text)
        a.min_experience_years, a.max_experience_years = lo, a.max_experience_years or hi
    return a


def parse_experience(text: str) -> tuple[float | None, float | None]:
    if m := EXP_RE.search(text):
        return float(m.group(1)), float(m.group(2))
    if m := EXP_MIN_RE.search(text):
        return float(m.group(1)), None
    return None, None
