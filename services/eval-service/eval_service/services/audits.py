"""报告下载审计:请求元数据快照 + 成功同事务 + 失败独立短事务。

下载是读取动作，但报告可能含 prompt/工具轨迹等敏感内容，因此每次尝试都留痕：
成功记录与读取同事务提交；失败（运行不存在、对象丢失、存储不可用）用独立短事务
补一条 failed，审计写不进去不能改变原错误码。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.logging import current_context, get_logger
from database.session import session_scope
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from eval_service.models import EvalReportDownload
from eval_service.repositories import EvalDownloadRepository
from eval_service.schemas.evals import EvalDownloadPageRead, EvalDownloadRead

log = get_logger("eval.downloads")

_MAX_USER_AGENT = 256
_MAX_REQUEST_ID = 64


@dataclass(frozen=True)
class DownloadContext:
    """一次下载请求的主体与来源快照；不记录 token 或内部密钥。"""

    actor: str | None
    actor_kind: str
    ip: str | None
    user_agent: str | None
    request_id: str | None


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return request.client.host if request.client is not None else None


def context_from_request(request: Request, actor: str | None) -> DownloadContext:
    request_id = getattr(request.state, "request_id", None) or current_context().get(
        "request_id"
    )
    user_agent = (request.headers.get("user-agent") or "").strip()
    return DownloadContext(
        actor=actor,
        actor_kind="account" if actor else "anonymous",
        ip=_client_ip(request),
        user_agent=user_agent[:_MAX_USER_AGENT] or None,
        request_id=str(request_id)[:_MAX_REQUEST_ID] if request_id else None,
    )


async def record_download(
    session: AsyncSession,
    ctx: DownloadContext,
    *,
    run_id: str,
    report_key: str | None,
    report_size: int,
    status: str = "success",
    error_code: str | None = None,
) -> EvalReportDownload:
    """把审计行加入调用方事务；由端点统一 commit。"""

    row = EvalReportDownload(
        run_id=run_id[:32],
        actor=ctx.actor,
        actor_kind=ctx.actor_kind,
        ip=ctx.ip,
        user_agent=ctx.user_agent,
        request_id=ctx.request_id,
        report_key=report_key,
        report_size=report_size,
        format="json",
        status=status,
        error_code=error_code,
    )
    session.add(row)
    await session.flush()
    return row


async def record_failed_download(
    session_factory: Any,
    *,
    run_id: str,
    actor: str | None,
    request: Request,
    error_code: str,
) -> None:
    """失败尝试用独立短事务补录；审计失败只告警，不掩盖原错误。"""

    ctx = context_from_request(request, actor)
    try:
        async with session_scope(session_factory) as session:
            await record_download(
                session,
                ctx,
                run_id=run_id,
                report_key=None,
                report_size=0,
                status="failed",
                error_code=error_code,
            )
    except Exception:  # noqa: BLE001 - 审计失败不该改变下载错误
        log.warning("eval.download_audit_failed", exc_info=True)


async def list_downloads(
    session: AsyncSession,
    *,
    run_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> EvalDownloadPageRead:
    repository = EvalDownloadRepository(session)
    rows = await repository.list_page(run_id=run_id, limit=limit, offset=offset)
    total = await repository.count_page(run_id=run_id)
    return EvalDownloadPageRead(
        items=[_to_read(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _to_read(row: EvalReportDownload) -> EvalDownloadRead:
    return EvalDownloadRead(
        id=row.id,
        run_id=row.run_id,
        actor=row.actor,
        actor_kind=row.actor_kind,
        ip=row.ip,
        user_agent=row.user_agent,
        request_id=row.request_id,
        report_key=row.report_key,
        report_size=row.report_size,
        format=row.format,
        status=row.status,
        error_code=row.error_code,
        created_at=row.created_at,
    )
