"""Skill 端点(M1 本地版):只读内置 manifest 列表/详情。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from agent_orchestrator.api.deps import get_skills
from agent_orchestrator.schemas.skills import SkillManifest
from agent_orchestrator.services.skills import SkillRegistry

router = APIRouter(prefix="/skills", tags=["skills"])
SkillsDep = Annotated[SkillRegistry, Depends(get_skills)]


@router.get("", response_model=list[SkillManifest])
async def list_skills(skills: SkillsDep) -> list[SkillManifest]:
    return skills.list()


@router.get("/{skill_id}", response_model=SkillManifest)
async def get_skill(skill_id: str, skills: SkillsDep) -> SkillManifest:
    skill = skills.get(skill_id)
    if skill is None:
        from common.errors import NotFoundError

        raise NotFoundError("skill.not_found", "Skill 不存在")
    return skill
