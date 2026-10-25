"""交付物与 skill 包仓储(自 repositories.py 拆出):统一租户过滤与 404 语义。"""

from __future__ import annotations

from database.repository import TenantRepository

from agent_orchestrator.models.runs import (
    RunArtifact,
)
from agent_orchestrator.models.skills import SkillPackage, SkillVersion

REPORT_ARTIFACT_SOURCE = "report"


_TEXT_CONTENT_TYPE_EXTRA = frozenset(
    {
        "application/json",
        "application/xml",
        "application/x-yaml",
        "application/yaml",
        "application/javascript",
        "application/x-ndjson",
    }
)


def _is_text_artifact_row(row: RunArtifact) -> bool:
    """文本交付物判定(二进制件不当主文本件);旧行没有 content_type 视为文本。"""
    if row.content_bytes is not None:
        return False
    content_type = str(row.content_type or "").split(";", 1)[0].strip().lower()
    if not content_type:
        return True
    return content_type.startswith("text/") or content_type in _TEXT_CONTENT_TYPE_EXTRA


class ArtifactRepository(TenantRepository[RunArtifact]):
    """交付物:一个 run 可以有多件(文本报告 + 技能/工具产出的文件)。

    主交付物(旧 `/artifact` 契约)优先取报告,其次第一条文本件,再次第一条;
    报告件的来源标记见 `services.artifacts.ARTIFACT_REPORT_SOURCE`(同为 "report",
    这里不反向 import service 层以免循环依赖)。
    """

    model = RunArtifact
    not_found_code = "artifact.not_found"
    not_found_message = "交付物不存在"
    id_detail_key = "artifact_id"

    async def list_for_run(self, tenant_id: str, run_id: str) -> list[RunArtifact]:
        """该 run 的全部交付物(created_at, id 升序;未交付返回空列表)。"""
        return await self.list_scoped(
            tenant_id,
            RunArtifact.run_id == run_id,
            order_by=[RunArtifact.created_at, RunArtifact.id],
        )

    async def find_for_run(self, tenant_id: str, run_id: str) -> RunArtifact | None:
        """按 run 取**主交付物**:优先报告,否则第一条文本件,否则第一条。"""
        found = await self.list_for_run(tenant_id, run_id)
        for row in found:
            if (row.source or "") == REPORT_ARTIFACT_SOURCE:
                return row
        for row in found:
            if _is_text_artifact_row(row):
                return row
        return found[0] if found else None

    async def require_for_run(self, tenant_id: str, run_id: str) -> RunArtifact:
        """按 run 取主交付物;未交付 → 404。

        details 用 `run_id`:取件入口是 run,调用方关心的是"这个 run 有没有件"。
        """
        from common.errors import NotFoundError

        found = await self.find_for_run(tenant_id, run_id)
        if found is None:
            raise NotFoundError(
                "artifact.not_found",
                "交付物不存在",
                details={"run_id": run_id},
            )
        return found

    async def find_artifact(
        self, tenant_id: str, run_id: str, artifact_id: str
    ) -> RunArtifact | None:
        """取该 run 下的指定交付物;不属于该 run/租户返回 None(调用方统一 404)。"""
        return await self.session.scalar(
            self.scoped(
                tenant_id,
                RunArtifact.run_id == run_id,
                RunArtifact.id == artifact_id,
            )
        )

    async def find_by_source_url(
        self, tenant_id: str, run_id: str, source_url: str
    ) -> RunArtifact | None:
        """按来源 URL 找已登记的交付物(抓取类工具重跑时去重,不重复落件)。"""
        return await self.session.scalar(
            self.scoped(
                tenant_id,
                RunArtifact.run_id == run_id,
                RunArtifact.source_url == source_url,
            )
        )


class SkillPackageRepository(TenantRepository[SkillPackage]):
    """上传技能包(租户内 slug 唯一;跨租户一律 404)。"""

    model = SkillPackage
    not_found_code = "skill.package_not_found"
    not_found_message = "技能包不存在"
    id_detail_key = "package_id"

    async def find_by_slug(self, tenant_id: str, slug: str) -> SkillPackage | None:
        return await self.session.scalar(
            self.scoped(tenant_id, SkillPackage.slug == slug)
        )

    async def list_for_tenant(self, tenant_id: str) -> list[SkillPackage]:
        """租户内包列表:最近上传/更新在前(id 兜底保证顺序确定)。"""
        return await self.list_scoped(
            tenant_id,
            order_by=[SkillPackage.updated_at.desc(), SkillPackage.id.desc()],
        )


class SkillVersionRepository(TenantRepository[SkillVersion]):
    """技能包版本:创建后不可变(包内容在对象存储)。"""

    model = SkillVersion
    not_found_code = "skill.package_not_found"
    not_found_message = "技能包版本不存在"
    id_detail_key = "version_id"

    async def list_for_package(
        self, tenant_id: str, package_id: str
    ) -> list[SkillVersion]:
        """包内全部版本:最新在前(列表首项即 latest)。"""
        return await self.list_scoped(
            tenant_id,
            SkillVersion.package_id == package_id,
            order_by=[SkillVersion.created_at.desc(), SkillVersion.id.desc()],
        )

    async def find_version(
        self, tenant_id: str, package_id: str, version: str
    ) -> SkillVersion | None:
        return await self.session.scalar(
            self.scoped(
                tenant_id,
                SkillVersion.package_id == package_id,
                SkillVersion.version == version,
            )
        )

    async def latest_for_package(
        self, tenant_id: str, package_id: str
    ) -> SkillVersion | None:
        versions = await self.list_for_package(tenant_id, package_id)
        return versions[0] if versions else None
