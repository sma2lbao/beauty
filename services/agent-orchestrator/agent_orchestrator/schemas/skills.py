"""Skill manifest 与上传技能包契约(M1 内置 + P3 上传包)。

SkillManifest 同时服务两条来源:

- builtin:仓库 skills/*.json(随代码版本化),新增字段全部有默认值,旧 JSON 兼容;
- uploaded:用户上传 zip 归一化后的 manifest(source=uploaded,带 package_id/
  version_id 与 has_scripts)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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
    # --- P3 上传包扩展:内置 manifest 无这些字段,默认值保证旧 JSON 兼容 ---
    source: Literal["builtin", "uploaded"] = "builtin"
    has_scripts: bool = False
    package_id: str | None = Field(default=None, max_length=32)
    version_id: str | None = Field(default=None, max_length=32)


class SkillVersionRead(BaseModel):
    """包内一个不可变版本(对外只暴露读契约,不泄漏对象键)。"""

    id: str
    version: str
    size_bytes: int
    sha256: str
    entrypoint: list[str] | None = None
    runtime: str | None = None
    network: str | None = None
    timeout_seconds: int | None = None
    has_scripts: bool = False
    created_at: datetime


class SkillPackageRead(BaseModel):
    """技能包详情/列表项(Web 技能面板直接用)。"""

    id: str
    slug: str
    title: str
    description: str
    latest_version: str | None = None
    created_at: datetime
    updated_at: datetime
    versions: list[SkillVersionRead] = Field(default_factory=list)
