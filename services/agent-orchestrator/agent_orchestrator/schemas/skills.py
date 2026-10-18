"""Skill manifest 契约(M1 本地版,架构 v2 §4.3)。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SkillDeliverable(BaseModel):
    kind: str = "markdown"
    template: str | None = None


class SkillManifest(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,80}$")
    version: str = Field(default="1.0.0", max_length=32)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    workspace_type: str = Field(default="generic", max_length=32)
    tools: list[str] = Field(default_factory=list)
    permissions: dict[str, str] = Field(default_factory=dict)
    system_prompt: str = Field(default="", max_length=20000)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    deliverables: list[SkillDeliverable] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
