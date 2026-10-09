"""Deterministic 0-100 match score with a human-readable explanation."""

from __future__ import annotations

from job_copilot.agents.skill_bank import SkillBank
from job_copilot.models import JDAnalysis, MatchResult, Profile, ScoreWeights


def experience_fit(yours: float, lo: float | None, hi: float | None) -> float:
    if lo is not None and yours < lo:
        return max(0.0, 1 - (lo - yours) / 2)  # lose 50% per missing year
    if hi is not None and yours > hi + 2:
        return 0.6  # clearly over-qualified
    return 1.0


def score_job(profile: Profile, analysis: JDAnalysis, bank: SkillBank, weights: ScoreWeights) -> MatchResult:
    matched, missing = [], []
    for req in analysis.mandatory_skills:
        hit = next((m for t in (req.skill, *req.alternatives) if (m := bank.match(t))), None)
        if hit:
            skill, via_eq = hit
            matched.append(f"{skill} (for {req.skill})" if via_eq or skill.lower() != req.skill.lower() else skill)
        else:
            missing.append(req.skill)

    nice = [m[0] for t in analysis.nice_to_have_skills if (m := bank.match(t))]

    total_m = len(analysis.mandatory_skills)
    mand_ratio = len(matched) / total_m if total_m else 1.0
    nice_ratio = len(nice) / len(analysis.nice_to_have_skills) if analysis.nice_to_have_skills else 1.0
    exp_ratio = experience_fit(profile.total_experience_years, analysis.min_experience_years, analysis.max_experience_years)

    score = round(100 * (weights.mandatory_skills * mand_ratio
                         + weights.nice_to_have_skills * nice_ratio
                         + weights.experience * exp_ratio))

    parts = [f"{score}%"]
    if missing:
        parts.append("missing " + ", ".join(missing))
    if matched:
        parts.append("strong in " + ", ".join(matched[:6]))
    if exp_ratio < 1:
        need = analysis.min_experience_years
        parts.append(f"experience {profile.total_experience_years:g}y vs required {need:g}y+" if need is not None
                     else "may be over-qualified")
    return MatchResult(
        score=score,
        matched_mandatory=matched,
        missing_mandatory=missing,
        matched_nice=nice,
        experience_ok=exp_ratio >= 1,
        explanation=": ".join([parts[0], "; ".join(parts[1:])]) if len(parts) > 1 else parts[0],
    )
