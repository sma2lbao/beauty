"""run 交付物:报告件落库、工具外链抓取、工作区镜像与取件(§7)。"""

from __future__ import annotations

from typing import Any

from common.artifacts import ArtifactStore
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.runs import Run, RunArtifact
from agent_orchestrator.repositories import ArtifactRepository
from agent_orchestrator.services import artifacts
from agent_orchestrator.services.file_capture import FileDownloader, extract_file_urls
from agent_orchestrator.services.run_events import append_event
from agent_orchestrator.services.workspace import Workspace


def _file_refs(records: list[dict[str, Any]], *, limit: int) -> list[Any]:
    """从工具调用记录里按扩展名白名单提取待抓取的文件链接(去重保序)。

    只看成功调用("ok" 为真)的 detail:失败调用的输出里即便有链接也不可信;
    limit 是全局上限(跨记录累计),避免一次执行把大量链接全抓下来。
    """
    if limit <= 0:
        return []
    refs: list[Any] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or not record.get("ok", False):
            continue
        detail = record.get("detail")
        if not isinstance(detail, str) or not detail:
            continue
        for ref in extract_file_urls(detail, limit=limit):
            if ref.url in seen:
                continue
            seen.add(ref.url)
            refs.append(ref)
            if len(refs) >= limit:
                return refs
    return refs


async def _capture_tool_files(
    session: AsyncSession,
    run: Run,
    records: list[dict[str, Any]],
    store: ArtifactStore | None,
    downloader: FileDownloader | None,
    *,
    max_files: int,
) -> tuple[list[RunArtifact], list[dict[str, str]]]:
    """工具/MCP 返回的外链 → 真实交付物(执行时抓取,§7)。

    链接带签名会过期,所以必须在执行阶段落库,不能只把 URL 留在报告正文里。
    单个链接失败不拖垮交付:错误如实进事件 payload 的 download_errors。
    按 source_url 去重(同一 run 的重放/追加执行不会重复落件)。
    """
    if downloader is None or max_files <= 0:
        return [], []
    repo = ArtifactRepository(session)
    rows: list[RunArtifact] = []
    errors: list[dict[str, str]] = []
    for ref in _file_refs(records, limit=max_files):
        if await repo.find_by_source_url(run.tenant_id, run.id, ref.url) is not None:
            continue
        try:
            downloaded = await downloader.fetch(ref.url)
        except Exception as exc:  # 下载失败:留痕,继续交付其余件
            errors.append({"url": ref.url, "error": f"{type(exc).__name__}: {exc}"})
            continue
        rows.append(
            await artifacts.persist_file_artifact(
                session,
                run,
                filename=downloaded.filename,
                content_type=downloaded.content_type,
                data=downloaded.data,
                store=store,
                source=artifacts.ARTIFACT_MCP_SOURCE,
                source_url=ref.url,
            )
        )
    return rows, errors


def _artifact_meta(row: RunArtifact) -> dict[str, Any]:
    """交付物元数据(事件 payload 与前端列表共用口径,不含正文)。"""
    return {
        "id": row.id,
        "title": row.title,
        "kind": row.kind,
        "filename": row.filename,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "source": row.source or artifacts.ARTIFACT_REPORT_SOURCE,
    }


async def _mirror_artifacts(
    session: AsyncSession,
    run: Run,
    rows: list[RunArtifact],
    store: ArtifactStore | None,
    workspace: Workspace | None,
) -> tuple[list[str], list[dict[str, str]]]:
    """把本次交付物镜像到工作区(§7 归属方向);返回 (工作区 id 列表, 错误)。

    镜像失败不丢交付物,只把原因记进事件;已镜像过的行(列上留了
    workspace_artifact_id)直接复用,重放不会在工作区里堆重复件。
    """
    if workspace is None:
        return [], []
    mirrored_ids: list[str] = []
    errors: list[dict[str, str]] = []
    for row in rows:
        mirrored_id = getattr(row, "workspace_artifact_id", None)
        if mirrored_id:
            mirrored_ids.append(mirrored_id)
            continue
        try:
            data = await artifacts.read_artifact_bytes(row, store)
            content_type = row.content_type or artifacts.CONTENT_TYPE
            if artifacts.is_text_artifact(row):
                mirrored = await workspace.create_artifact(
                    run.tenant_id,
                    title=row.title,
                    kind=row.kind,
                    content=data.decode("utf-8", "replace"),
                    filename=row.filename,
                    content_type=content_type,
                    run_id=run.id,
                    workspace_id=run.workspace_id,
                )
            else:
                mirrored = await workspace.create_artifact(
                    run.tenant_id,
                    title=row.title,
                    kind=row.kind,
                    data=data,
                    filename=row.filename or artifacts.artifact_download_name(row),
                    content_type=content_type,
                    run_id=run.id,
                    workspace_id=run.workspace_id,
                )
        except Exception as exc:  # 镜像失败:run 照常交付,但如实记账
            errors.append(
                {"artifact_id": row.id, "error": f"{type(exc).__name__}: {exc}"}
            )
            continue
        new_id = mirrored.get("id")
        if new_id:
            mirrored_ids.append(new_id)
            if "workspace_artifact_id" in RunArtifact.__table__.columns:
                row.workspace_artifact_id = new_id
    if mirrored_ids:
        await session.flush()
    return mirrored_ids, errors


async def _deliver_artifacts(
    session: AsyncSession,
    run: Run,
    artifact: dict[str, Any],
    *,
    records: list[dict[str, Any]],
    store: ArtifactStore | None,
    workspace: Workspace | None,
    downloader: FileDownloader | None,
    max_files: int,
) -> RunArtifact:
    """交付阶段:落报告 → 抓取工具产出文件 → 镜像工作区 → 一条 run.delivered.v1。

    多件交付物(run_artifacts 一行一件,source 区分 report/skill/mcp);
    报告件不再覆盖既有行——技能产物与报告各自成件。
    """
    report = await artifacts.persist_artifact(session, run, artifact, store)
    files, download_errors = await _capture_tool_files(
        session, run, records, store, downloader, max_files=max_files
    )
    rows = [report, *files]
    mirrored_ids, mirror_errors = await _mirror_artifacts(
        session, run, rows, store, workspace
    )
    payload: dict[str, Any] = {
        "artifact_id": report.id,
        "kind": report.kind,
        "title": report.title,
        "chars": len(str(artifact.get("content") or "")),  # 字符数(文本)
        "size_bytes": report.size_bytes,  # 存储字节数(UTF-8)
        "storage": report.storage,
        "artifact_ids": [row.id for row in rows],
        "artifacts": [_artifact_meta(row) for row in rows],
        "download_errors": download_errors,
    }
    if mirrored_ids:
        payload["workspace_artifact_id"] = mirrored_ids[0]
        payload["workspace_artifact_ids"] = mirrored_ids
    if mirror_errors:
        payload["workspace_errors"] = mirror_errors
    await append_event(session, run, "run.delivered.v1", payload)
    return report


async def _persist_artifact(
    session: AsyncSession,
    run: Run,
    artifact: dict[str, Any],
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
) -> RunArtifact:
    """兼容薄包装:只落报告件(不抓取外链文件),语义同旧版交付。"""
    return await _deliver_artifacts(
        session,
        run,
        artifact,
        records=[],
        store=store,
        workspace=workspace,
        downloader=None,
        max_files=0,
    )


async def get_artifact(
    session: AsyncSession,
    tenant_id: str,
    run_id: str,
    store: ArtifactStore | None = None,
) -> artifacts.ArtifactView:
    """取件:未交付(未 completed)→ 404;内容按存储后端装载(§7 阶段 B)。"""
    row = await ArtifactRepository(session).require_for_run(tenant_id, run_id)
    return await artifacts.read_artifact(row, store)
