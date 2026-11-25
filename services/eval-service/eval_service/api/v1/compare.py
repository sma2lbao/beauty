"""跨运行对比端点:按用例计算回归/修复/新增/移除。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from eval_service.api.deps import get_session, get_viewer_account
from eval_service.schemas.evals import EvalCompareRead
from eval_service.services import compare_runs

router = APIRouter(prefix="/compare", tags=["compare"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ViewerDep = Annotated[str | None, Depends(get_viewer_account)]


@router.get("", response_model=EvalCompareRead)
async def compare(
    session: SessionDep,
    _: ViewerDep,
    base: Annotated[str, Query(min_length=1)],
    candidate: Annotated[str, Query(min_length=1)],
) -> EvalCompareRead:
    """对比两次运行;base 是旧运行,candidate 是新运行。"""

    return await compare_runs(session, base, candidate)
