"""Profile endpoints: read the profile, edit a project description through the Honesty Guard."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from backend.deps import get_tracker
from backend.schemas import ProjectUpdate
from job_copilot.agents.honesty_guard import _unsupported_claims
from job_copilot.agents.skill_bank import SkillBank
from job_copilot.config import get_profile, save_profile
from job_copilot.db import Tracker

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("")
def read_profile() -> dict:
    return get_profile().model_dump(mode="json")


@router.put("/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdate, tracker: Annotated[Tracker, Depends(get_tracker)]) -> dict:
    """Edit one project's description; rejected (422) if it introduces unverified skills."""
    profile = get_profile()
    proj = next((p for p in profile.projects if p.id == project_id), None)
    if proj is None:
        raise HTTPException(404, f"Unknown project '{project_id}'. Known: {[p.id for p in profile.projects]}")
    bank = SkillBank(profile)
    known_terms = {req["skill"] for j in tracker.list(limit=1000) if j.get("analysis")
                   for req in j["analysis"]["mandatory_skills"]}
    known_terms.update(profile.learning_goals)
    if bad := _unsupported_claims(body.description, proj.description, proj.tech, sorted(known_terms), bank):
        raise HTTPException(422, f"Rejected by Honesty Guard - unverified skills: {', '.join(bad)}")
    proj.description = body.description.strip()
    save_profile(profile)
    return {"project_id": project_id, "description": proj.description}
