"""Honesty Guard: rejects any tailored content that claims skills you don't have.

Rules
1. Skills listed on the resume must be in the Verified Skill Bank.
2. A JD skill may only be *newly* introduced into a project description / summary if it is in
   the skill bank (or was already in that project's tech/description). Learning goals never count.
3. No JD term may appear more than `max_keyword_repeats` times in one project (keyword stuffing).
4. Only existing, on-resume projects may be rewritten (new projects need human approval).
Offending sections are reverted to the original text, never "fixed" by guessing.
"""

from __future__ import annotations

from pydantic import BaseModel

from job_copilot.agents.skill_bank import SkillBank, find_term
from job_copilot.models import JDAnalysis, Profile, ProjectRewrite, TailoredContent


class GuardReport(BaseModel):
    ok: bool
    violations: list[str] = []
    dropped_skills: list[str] = []
    reverted_sections: list[str] = []


def jd_skill_terms(analysis: JDAnalysis) -> list[str]:
    terms = {s for r in analysis.mandatory_skills for s in (r.skill, *r.alternatives)}
    terms.update(analysis.nice_to_have_skills)
    return sorted(t for t in terms if t.strip())


def _unsupported_claims(new: str, original: str, allowed_extra: list[str], terms: list[str], bank: SkillBank) -> list[str]:
    bad = []
    for term in terms:
        if not find_term(new, term) or find_term(original, term):
            continue  # not mentioned, or already there before tailoring
        if any(term.lower() == a.lower() for a in allowed_extra):
            continue
        if bank.is_learning(term) or bank.canonical(term) is None:
            bad.append(term)
    return bad


def guard(content: TailoredContent, profile: Profile, analysis: JDAnalysis, bank: SkillBank,
          max_repeats: int = 2) -> tuple[TailoredContent, GuardReport]:
    report = GuardReport(ok=True)
    terms = jd_skill_terms(analysis)

    # 1. skills
    skills, seen = [], set()
    for s in content.skills:
        canon = bank.canonical(s)
        if canon is None:
            report.dropped_skills.append(s)
            report.violations.append(f"skill '{s}' is not in the Verified Skill Bank")
        elif canon not in seen:
            seen.add(canon)
            skills.append(canon)
    for s in bank.names:  # never lose a verified skill, just order it last
        if s not in seen:
            skills.append(s)

    # 2. summary
    summary = content.summary
    if bad := _unsupported_claims(summary, profile.summary, [], terms, bank):
        report.violations.append(f"summary claims unverified skills: {', '.join(bad)}")
        report.reverted_sections.append("summary")
        summary = profile.summary

    # 3 + 4. projects
    projects_by_id = {p.id: p for p in profile.projects if p.on_resume}
    rewrites: dict[str, ProjectRewrite] = {}
    for rw in content.projects:
        proj = projects_by_id.get(rw.project_id)
        if proj is None:
            report.violations.append(f"project '{rw.project_id}' is not an approved on-resume project")
            continue
        problem = None
        if bad := _unsupported_claims(rw.description, proj.description, proj.tech, terms, bank):
            problem = f"claims unverified skills: {', '.join(bad)}"
        elif stuffed := [t for t in terms if find_term(rw.description, t) > max_repeats]:
            problem = f"keyword stuffing: {', '.join(stuffed)}"
        elif not rw.description.strip() or len(rw.description) > 3 * len(proj.description) + 300:
            problem = "rewrite is empty or far longer than the original"
        if problem:
            report.violations.append(f"project '{proj.id}' {problem}")
            report.reverted_sections.append(f"project:{proj.id}")
            rw = ProjectRewrite(project_id=proj.id, description=proj.description)
        rewrites[proj.id] = rw
    for pid, proj in projects_by_id.items():  # untouched projects keep their original text
        rewrites.setdefault(pid, ProjectRewrite(project_id=pid, description=proj.description))

    report.ok = not report.violations
    cleaned = TailoredContent(
        summary=summary,
        skills=skills,
        projects=list(rewrites.values()),
        keywords_used=[k for k in content.keywords_used if bank.match(k) or not bank.is_learning(k)],
    )
    return cleaned, report
