"""下载审计查询:运维/安全复核用，只认内部密钥。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from eval_service.api.deps import get_session
from eval_service.api.v1.runs import require_internal_secret
from eval_service.schemas.evals import EvalDownloadPageRead
from eval_service.services import list_downloads

router = APIRouter(prefix="/downloads", tags=["downloads"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=EvalDownloadPageRead)
async def list_download_audits(
    session: SessionDep,
    _: Annotated[None, Depends(require_internal_secret)],
    run_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EvalDownloadPageRead:
    """按时间倒序返回下载审计；可按 run_id 过滤。"""

    return await list_downloads(
        session,
        run_id=run_id,
        limit=limit,
        offset=offset,
    )
