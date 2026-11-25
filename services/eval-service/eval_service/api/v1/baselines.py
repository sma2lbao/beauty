"""已批准基线:读取与替换（替换即批准）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from eval_service.api.deps import get_account_id, get_session, get_viewer_account
from eval_service.schemas.evals import (
    EvalBaselineApproveBody,
    EvalBaselineListRead,
    EvalBaselineRead,
)
from eval_service.services import (
    approve_baseline,
    get_baseline_payload,
    list_baselines,
)

router = APIRouter(prefix="/baselines", tags=["baselines"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AccountDep = Annotated[str, Depends(get_account_id)]
ViewerDep = Annotated[str | None, Depends(get_viewer_account)]


@router.get("", response_model=EvalBaselineListRead)
async def list_baselines_endpoint(
    session: SessionDep,
    _: ViewerDep,
) -> EvalBaselineListRead:
    return EvalBaselineListRead(items=await list_baselines(session))


@router.get("/{suite}")
async def get_baseline_endpoint(
    suite: str,
    session: SessionDep,
    _: ViewerDep,
) -> dict:
    return await get_baseline_payload(session, suite)


@router.post("/{suite}", response_model=EvalBaselineRead)
async def approve_baseline_endpoint(
    suite: str,
    body: EvalBaselineApproveBody,
    session: SessionDep,
    account_id: AccountDep,
) -> EvalBaselineRead:
    """批准基线；审计字段记录批准账号。"""

    record = await approve_baseline(
        session,
        suite,
        payload=body.payload,
        dataset_version=body.dataset_version,
        evaluator_version=body.evaluator_version,
        git_sha=body.git_sha,
        approved_by=account_id,
    )
    await session.commit()
    return EvalBaselineRead(
        suite=record.suite,
        dataset_version=record.dataset_version,
        evaluator_version=record.evaluator_version,
        git_sha=record.git_sha,
        approved_by=record.approved_by,
        approved_at=record.approved_at,
    )
