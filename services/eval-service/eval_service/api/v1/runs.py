"""运行上报与只读查询端点。"""

from __future__ import annotations

import secrets
from typing import Annotated

from common.artifacts import ArtifactStore
from common.errors import AppError, PermissionDeniedError
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from eval_service.api.deps import (
    get_account_id,
    get_artifact_store,
    get_session,
    get_settings,
    get_viewer_account,
)
from eval_service.core.config import Settings
from eval_service.schemas.evals import (
    EvalCasePageRead,
    EvalCaseRead,
    EvalIndexRead,
    EvalRunRead,
    EvalRunUpload,
)
from eval_service.services import (
    ingest_run,
    load_case_detail,
    load_case_page,
    load_index,
    load_report,
    load_report_download,
    load_run_summary,
    record_download,
    report_bytes,
    run_to_read,
)
from eval_service.services.audits import context_from_request, record_failed_download

router = APIRouter(prefix="/runs", tags=["runs"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AccountDep = Annotated[str, Depends(get_account_id)]
ViewerDep = Annotated[str | None, Depends(get_viewer_account)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
StoreDep = Annotated[ArtifactStore | None, Depends(get_artifact_store)]


class ServiceUnavailableError(AppError):
    """写入口未配置密钥时 fail closed。"""

    status = 503


def require_internal_secret(
    settings: SettingsDep,
    x_internal_secret: Annotated[str | None, Header(alias="X-Internal-Secret")] = None,
) -> None:
    """CI 上报只认内部密钥；未配置密钥时入口直接 503，不静默开放。"""

    if not settings.internal_secret:
        raise ServiceUnavailableError(
            "eval.ingest_disabled",
            "评测上报未启用(未配置 EVAL__INTERNAL_SECRET)",
        )
    if not x_internal_secret or not secrets.compare_digest(
        x_internal_secret, settings.internal_secret
    ):
        raise PermissionDeniedError("eval.forbidden", "内部密钥不正确")


@router.post("", response_model=EvalRunRead, status_code=201)
async def create_run(
    body: EvalRunUpload,
    session: SessionDep,
    settings: SettingsDep,
    store: StoreDep,
    _: Annotated[None, Depends(require_internal_secret)],
) -> EvalRunRead:
    """上报一次评测运行（同 external_id 幂等）。"""

    run = await ingest_run(
        session,
        body,
        store=store,
        inline_limit=settings.report_inline_max_bytes,
    )
    await session.commit()
    return run_to_read(run)


@router.get("", response_model=EvalIndexRead)
async def list_runs(
    session: SessionDep,
    _: ViewerDep,
    suite: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query(pattern="^(passed|failed|drift)$")] = None,
    branch: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EvalIndexRead:
    """运行索引:默认最近 20 条,可按套件/结果/分支过滤。"""

    return await load_index(
        session,
        suite=suite,
        status=status,
        branch=branch,
        limit=limit,
        offset=offset,
    )


@router.get("/{run_id}/summary", response_model=EvalRunRead)
async def get_run_summary(
    run_id: str,
    session: SessionDep,
    _: ViewerDep,
) -> EvalRunRead:
    """运行摘要:元数据 + 套件汇总，不含用例明细。"""

    return await load_run_summary(session, run_id)


@router.get("/{run_id}/case", response_model=EvalCaseRead)
async def get_case(
    run_id: str,
    session: SessionDep,
    _: ViewerDep,
    suite: Annotated[str, Query(min_length=1)],
    case_id: Annotated[str, Query(min_length=1)],
) -> EvalCaseRead:
    """单个用例详情;case_id 含 ``/``，因此用查询参数传递。"""

    return await load_case_detail(session, run_id, suite, case_id)


@router.get("/{run_id}/download")
async def download_run(
    run_id: str,
    request: Request,
    session: SessionDep,
    viewer: ViewerDep,
    store: StoreDep,
) -> Response:
    """下载完整报告 JSON，并把每次成功/失败尝试写入下载审计。"""

    try:
        report, run = await load_report_download(session, run_id, store=store)
    except AppError as exc:
        await record_failed_download(
            request.app.state.session_factory,
            run_id=run_id,
            actor=viewer,
            request=request,
            error_code=exc.code,
        )
        raise
    payload = report_bytes(report)
    await record_download(
        session,
        context_from_request(request, viewer),
        run_id=run.id,
        report_key=run.report_key,
        report_size=len(payload),
    )
    await session.commit()
    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="eval-{run.id}.json"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{run_id}")
async def get_run(
    run_id: str,
    session: SessionDep,
    _: ViewerDep,
    store: StoreDep,
) -> dict:
    """完整报告快照(漂移已回填到 suites/cases)。"""

    return await load_report(session, run_id, store=store)


@router.get("/{run_id}/suites/{suite}/cases", response_model=EvalCasePageRead)
async def list_cases(
    run_id: str,
    suite: str,
    session: SessionDep,
    _: ViewerDep,
    status: Annotated[str | None, Query(pattern="^(passed|failed)$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EvalCasePageRead:
    """用例服务端分页;500+ 用例的套件不把整份报告推给浏览器。"""

    return await load_case_page(
        session,
        run_id,
        suite,
        status=status,
        limit=limit,
        offset=offset,
    )
