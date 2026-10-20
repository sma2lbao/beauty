"""Skill 端点(M1 内置只读 + P3 用户上传包)。

契约:

- GET /skills:内置 manifest + 每个上传包的最新版本合并列表;
- GET /skills/{skill_id}:先查内置,再查上传包 slug,都没有 404 skill.not_found;
- POST /skills/packages:multipart 上传 zip(校验/对象存储/版本化);
- GET /skills/packages、GET /skills/packages/{id}、DELETE /skills/packages/{id}。

注意路由顺序:/packages 系列必须先声明,否则会被 /{skill_id} 捕获。
"""

from __future__ import annotations

from typing import Annotated

from common.errors import NotFoundError
from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import (
    get_session,
    get_skills,
    get_tenant_id,
    get_workspace_client,
)
from agent_orchestrator.schemas.skills import SkillManifest, SkillPackageRead
from agent_orchestrator.services import skill_packages
from agent_orchestrator.services.skills import SkillRegistry
from agent_orchestrator.services.workspace import Workspace

router = APIRouter(prefix="/skills", tags=["skills"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
SkillsDep = Annotated[SkillRegistry, Depends(get_skills)]
WorkspaceDep = Annotated[Workspace | None, Depends(get_workspace_client)]


# ---- 上传包(P3):/packages 必须先于 /{skill_id} 声明 ----


@router.post(
    "/packages",
    response_model=SkillPackageRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_skill_package(
    file: Annotated[UploadFile, File(description="技能包 zip(根目录含 SKILL.md)")],
    session: DBSession,
    tenant_id: TenantId,
    skills: SkillsDep,
    workspace: WorkspaceDep,
) -> SkillPackageRead:
    """上传技能包:校验 → 对象存储 → 版本化,返回含全部版本的包详情。"""
    data = await file.read()
    return await skill_packages.create_package(
        session,
        tenant_id,
        data,
        file.filename,
        workspace=workspace,
        registry=skills,
    )


@router.get("/packages", response_model=list[SkillPackageRead])
async def list_skill_packages(
    session: DBSession, tenant_id: TenantId
) -> list[SkillPackageRead]:
    """当前租户的全部技能包(含各自全部版本与最新版本号)。"""
    return await skill_packages.list_packages(session, tenant_id)


@router.get("/packages/{package_id}", response_model=SkillPackageRead)
async def get_skill_package(
    package_id: str, session: DBSession, tenant_id: TenantId
) -> SkillPackageRead:
    """包详情:跨租户/不存在一律 404(skill.package_not_found)。"""
    return await skill_packages.get_package(session, tenant_id, package_id)


@router.delete("/packages/{package_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill_package(
    package_id: str,
    session: DBSession,
    tenant_id: TenantId,
    workspace: WorkspaceDep,
) -> None:
    """删除包/版本 + best-effort 删对象(对象删除失败不阻塞 DB 删除)。"""
    await skill_packages.delete_package(
        session, tenant_id, package_id, workspace=workspace
    )


# ---- 技能列表/详情:内置 + 上传包合并 ----


@router.get("", response_model=list[SkillManifest])
async def list_skills(
    session: DBSession, tenant_id: TenantId, skills: SkillsDep
) -> list[SkillManifest]:
    """内置 manifest + 上传包最新版本(按 id 排序;source 区分来源)。"""
    return await skill_packages.list_manifests(session, tenant_id, skills)


@router.get("/{skill_id}", response_model=SkillManifest)
async def get_skill(
    skill_id: str, session: DBSession, tenant_id: TenantId, skills: SkillsDep
) -> SkillManifest:
    """内置优先,其次上传包 slug;都没有 404 skill.not_found。"""
    definition = await skill_packages.resolve_skill(
        session, tenant_id, skill_id, skills
    )
    if definition is None:
        raise NotFoundError("skill.not_found", "Skill 不存在")
    return definition.manifest
