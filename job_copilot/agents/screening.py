"""Answer a portal's screening questions from the profile; anything it can't answer from facts returns None."""

from __future__ import annotations

import re

from job_copilot.agents.skill_bank import SkillBank, find_term
from job_copilot.models import Profile

EXPERIENCE_WORDS_RE = re.compile(r"\b(experience|experienced|worked|working|hands-on|proficien\w*|familiar\w*)\b", re.I)
YEARS_RE = re.compile(r"\bhow many years\b|\bnumber of years\b|\byears of\b", re.I)
TOTAL_EXP_RE = re.compile(r"\btotal\b.*\bexperience\b|\boverall experience\b", re.I)
NOTICE_RE = re.compile(r"\bnotice period\b", re.I)
EXPECTED_CTC_RE = re.compile(r"\bexpected\b.*\b(ctc|salary|compensation)\b|\b(ctc|salary)\b.*\bexpect", re.I)
CURRENT_CTC_RE = re.compile(r"\bcurrent\b.*\b(ctc|salary|compensation)\b", re.I)
RELOCATE_RE = re.compile(r"\brelocat", re.I)
EDUCATION_RE = re.compile(r"\b(highest|qualification)\b.*\beducation\b|\bhighest\b.*\b(degree|qualification)\b", re.I)
OR_RE = re.compile(r"\bor\b", re.I)
# degree text -> words that identify the matching radio option, checked in order
DEGREE_LEVELS = [
    (re.compile(r"\bph\.?d\b|doctor", re.I), ["doctorate", "phd"]),
    (re.compile(r"\bmba\b", re.I), ["mba"]),
    (re.compile(r"master|\bm\.?\s?tech\b|\bmca\b|\bm\.?\s?sc\b", re.I), ["master"]),
    (re.compile(r"bachelor|\bb\.?\s?tech\b|\bb\.?e\b|\bbca\b|\bb\.?\s?sc\b|\bbba\b|\bb\.?\s?com\b", re.I), ["bachelor"]),
    (re.compile(r"diploma", re.I), ["diploma"]),
]
SKILL_TOKEN = "\x00"
# A skill next to a list connector ("Python and Java") means the question asks for more than one thing.
ALL_CONNECTORS = r",|/|&|\band\b|\bor\b"
# With alternatives ("AWS, Azure, or GCP") any one skill you have is a true "Yes"; only "and" demands every one.
AND_CONNECTORS = r"&|\band\b"


def _asks_for_several(masked: str) -> bool:
    connectors = AND_CONNECTORS if OR_RE.search(masked) else ALL_CONNECTORS
    pattern = rf"{SKILL_TOKEN}\s*({connectors})\s*(?!{SKILL_TOKEN})\w|\w\s*({connectors})\s*{SKILL_TOKEN}"
    return bool(re.search(pattern, masked, flags=re.I))


def _number(value: float) -> str:
    return f"{value:g}"


def _pick(answer: str, options: list[str]) -> str | None:
    """With fixed options (radio buttons) the answer must be one of them; with a text box it is free."""
    if not options:
        return answer
    return next((o for o in options if o.strip().lower() == answer.lower()), None)


def _asked_skills(question: str, bank: SkillBank) -> list[str]:
    return [s for s in bank.names if any(find_term(question, t) for t in bank.surface_forms(s))]


def _education_option(profile: Profile, options: list[str]) -> str | None:
    """The radio option for your highest degree (latest year in the profile)."""
    if not profile.education or not options:
        return None
    degree = max(profile.education, key=lambda e: e.year).degree
    for pattern, words in DEGREE_LEVELS:
        if pattern.search(degree):
            return next((o for o in options if any(w in o.lower() for w in words)), None)
    return None


def _skill_question(question: str, options: list[str], profile: Profile, bank: SkillBank) -> str | None:
    skills = _asked_skills(question, bank)
    if not skills:
        return None
    masked = question
    for skill in skills:
        for term in bank.surface_forms(skill):
            masked = re.sub(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9+#])", SKILL_TOKEN, masked, flags=re.I)
    if _asks_for_several(masked):
        return None
    if YEARS_RE.search(question):
        years = next((s.years for s in profile.skills if s.name == skills[0]), None) if len(skills) == 1 else None
        return _pick(_number(years), options) if years else None
    if EXPERIENCE_WORDS_RE.search(question):
        return _pick("Yes", options)
    return None


def answer_question(question: str, options: list[str], profile: Profile, bank: SkillBank) -> str | None:
    """The answer to one screening question, or None when the profile has no verified fact for it.

    Facts come only from profile.yaml; never guess. Skill questions are answered "Yes" only for skills in the bank.
    """
    prefs = profile.preferences
    if NOTICE_RE.search(question):
        days = prefs.notice_period_days
        return _pick(str(days), options) if days is not None else None
    if EXPECTED_CTC_RE.search(question):
        return _pick(_number(prefs.expected_ctc_lpa), options) if prefs.expected_ctc_lpa else None
    if CURRENT_CTC_RE.search(question):
        return _pick(_number(prefs.current_ctc_lpa), options) if prefs.current_ctc_lpa is not None else None
    if RELOCATE_RE.search(question):
        return None if prefs.relocate is None else _pick("Yes" if prefs.relocate else "No", options)
    if EDUCATION_RE.search(question):
        return _education_option(profile, options)
    if TOTAL_EXP_RE.search(question):
        return _pick(_number(profile.total_experience_years), options)
    return _skill_question(question, options, profile, bank)
