"""skill 包读取与清单管理(自 skill_packages.py 拆出)。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.errors import AppError
from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.skills import SkillPackage, SkillVersion
from agent_orchestrator.repositories import (
    SkillPackageRepository,
    SkillVersionRepository,
)
from agent_orchestrator.schemas.skills import (
    SkillManifest,
    SkillPackageRead,
    SkillVersionRead,
)
from agent_orchestrator.services.skills import SkillRegistry
from agent_orchestrator.services.workspace import Workspace

logger = get_logger(__name__)


SKILL_RUN_TOOL = "skill_run"


ACTIVE = "active"


class SkillStorageUnavailable(AppError):
    """对象存储未装配/不可用:明确报不可用,不静默降级。"""

    status = 503


@dataclass(frozen=True)
class ParsedPackage:
    """校验/归一化后的上传包(纯数据,不碰 DB/网络)。"""

    slug: str
    version: str
    title: str
    description: str
    entrypoint: list[str] | None
    runtime: str
    network: str
    timeout_seconds: int
    tools: list[str]
    skill_md: str
    file_count: int
    manifest: dict[str, Any]


@dataclass(frozen=True)
class SkillDefinition:
    """run 创建时冻结的技能定义(P3-C 执行 skill 包用)。"""

    manifest: SkillManifest
    package_id: str | None = None
    version_id: str | None = None
    object_key: str | None = None
    sha256: str | None = None
    entrypoint: list[str] | None = None
    runtime: str | None = None
    network: str | None = None
    timeout_seconds: int | None = None


def _version_read(version: SkillVersion) -> SkillVersionRead:
    return SkillVersionRead(
        id=version.id,
        version=version.version,
        size_bytes=version.size_bytes,
        sha256=version.sha256,
        entrypoint=list(version.entrypoint) if version.entrypoint else None,
        runtime=version.runtime,
        network=version.network,
        timeout_seconds=version.timeout_seconds,
        has_scripts=bool(version.entrypoint),
        created_at=version.created_at,
    )


def _package_read(
    package: SkillPackage, versions: list[SkillVersion]
) -> SkillPackageRead:
    return SkillPackageRead(
        id=package.id,
        slug=package.slug,
        title=package.title,
        description=package.description,
        latest_version=versions[0].version if versions else None,
        created_at=package.created_at,
        updated_at=package.updated_at,
        versions=[_version_read(version) for version in versions],
    )


def _manifest_for(package: SkillPackage, version: SkillVersion) -> SkillManifest:
    snapshot = version.manifest or {}
    tools = list(snapshot.get("tools") or [])
    if version.entrypoint and SKILL_RUN_TOOL not in tools:
        tools.append(SKILL_RUN_TOOL)
    return SkillManifest(
        id=package.slug,
        version=version.version,
        title=package.title,
        description=package.description or "",
        tools=tools,
        system_prompt=version.skill_md,
        source="uploaded",
        has_scripts=bool(version.entrypoint),
        package_id=package.id,
        version_id=version.id,
    )


def _definition(package: SkillPackage, version: SkillVersion) -> SkillDefinition:
    return SkillDefinition(
        manifest=_manifest_for(package, version),
        package_id=package.id,
        version_id=version.id,
        object_key=version.object_key,
        sha256=version.sha256,
        entrypoint=list(version.entrypoint) if version.entrypoint else None,
        runtime=version.runtime,
        network=version.network,
        timeout_seconds=version.timeout_seconds,
    )


async def list_packages(
    session: AsyncSession, tenant_id: str
) -> list[SkillPackageRead]:
    """租户内全部技能包(含各自全部版本,最新版本在 versions[0])。"""
    packages = await SkillPackageRepository(session).list_for_tenant(tenant_id)
    versions = SkillVersionRepository(session)
    result: list[SkillPackageRead] = []
    for package in packages:
        result.append(
            _package_read(
                package, await versions.list_for_package(tenant_id, package.id)
            )
        )
    return result


async def get_package(
    session: AsyncSession, tenant_id: str, package_id: str
) -> SkillPackageRead:
    """按 id 取包(跨租户 404,由仓储统一语义)。"""
    package = await SkillPackageRepository(session).require_for_tenant(
        tenant_id, package_id
    )
    versions = await SkillVersionRepository(session).list_for_package(
        tenant_id, package.id
    )
    return _package_read(package, versions)


async def delete_package(
    session: AsyncSession,
    tenant_id: str,
    package_id: str,
    *,
    workspace: Workspace | None = None,
) -> None:
    """删除 DB 包/版本,再 best-effort 删对象(对象删除失败不阻塞 DB 删除)。"""
    packages = SkillPackageRepository(session)
    versions = SkillVersionRepository(session)
    package = await packages.require_for_tenant(tenant_id, package_id)
    rows = await versions.list_for_package(tenant_id, package.id)
    object_keys = [row.object_key for row in rows]
    for row in rows:
        await versions.delete(row)
    await packages.delete(package)
    if workspace is None:
        return
    for key in object_keys:
        try:
            await workspace.delete_object(tenant_id, key)
        except Exception as exc:  # noqa: BLE001 - best-effort:DB 已删,留日志
            logger.warning(
                "skill package object delete failed",
                tenant_id=tenant_id,
                object_key=key,
                error=f"{type(exc).__name__}: {exc}",
            )


async def resolve_skill(
    session: AsyncSession,
    tenant_id: str,
    skill_id: str,
    registry: SkillRegistry,
) -> SkillDefinition | None:
    """解析 run 要绑定的技能:内置优先,其次租户上传包的最新版本。"""
    builtin = registry.get(skill_id)
    if builtin is not None:
        return SkillDefinition(manifest=builtin)
    package = await SkillPackageRepository(session).find_by_slug(tenant_id, skill_id)
    if package is None or package.status != ACTIVE:
        return None
    version = await SkillVersionRepository(session).latest_for_package(
        tenant_id, package.id
    )
    if version is None:
        return None
    return _definition(package, version)


async def list_manifests(
    session: AsyncSession, tenant_id: str, registry: SkillRegistry
) -> list[SkillManifest]:
    """GET /skills:内置 manifest + 每个上传包的最新版本(按 id 排序)。"""
    manifests = list(registry.list())
    packages = await SkillPackageRepository(session).list_for_tenant(tenant_id)
    versions = SkillVersionRepository(session)
    for package in packages:
        if package.status != ACTIVE:
            continue
        version = await versions.latest_for_package(tenant_id, package.id)
        if version is not None:
            manifests.append(_manifest_for(package, version))
    return sorted(manifests, key=lambda manifest: manifest.id)
