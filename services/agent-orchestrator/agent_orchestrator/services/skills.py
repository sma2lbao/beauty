"""Skill 注册表:启动时加载仓库 skills/ 下的 manifest。

内置技能随代码版本化;租户启用/市场发布后续进 DB。未知/坏 manifest 在
启动阶段直接报错,避免运行到一半才发现缺失。
"""

from __future__ import annotations

from pathlib import Path

import structlog

from agent_orchestrator.schemas.skills import SkillManifest

logger = structlog.get_logger(__name__)
DEFAULT_SKILLS_DIR = Path(__file__).resolve().parents[4] / "skills"


class SkillRegistry:
    def __init__(self, skills: list[SkillManifest]) -> None:
        self._skills = {skill.id: skill for skill in skills}

    def list(self) -> list[SkillManifest]:
        return sorted(self._skills.values(), key=lambda skill: skill.id)

    def get(self, skill_id: str) -> SkillManifest | None:
        return self._skills.get(skill_id)


def load_skills(directory: str | None = None) -> SkillRegistry:
    root = Path(directory) if directory else DEFAULT_SKILLS_DIR
    if not root.is_dir():
        logger.warning("skills directory missing", path=str(root))
        return SkillRegistry([])
    skills: list[SkillManifest] = []
    for path in sorted(root.glob("*.json")):
        try:
            skills.append(SkillManifest.model_validate_json(path.read_text()))
        except Exception as exc:  # noqa: BLE001 - 启动期明确报错
            raise ValueError(f"invalid skill manifest {path}: {exc}") from exc
    logger.info("skills loaded", count=len(skills), path=str(root))
    return SkillRegistry(skills)
