"""审批策略端点(P0-4):读/整表更新租户级工具审批规则。

策略是"当前意图",因此更新语义是**整表替换**(PUT),不做逐条增量;
读返回规范顺序(按工具名),便于前端/运维对比。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import get_session, get_tenant_id
from agent_orchestrator.schemas.approval import ApprovalPolicyRead, ApprovalPolicyUpdate
from agent_orchestrator.services import approval as approval_service

router = APIRouter(prefix="/approval-policies", tags=["approval-policies"])


@router.get("", response_model=list[ApprovalPolicyRead])
async def list_policies(
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ApprovalPolicyRead]:
    rows = await approval_service.load_policies(session, tenant_id)
    return [ApprovalPolicyRead(**row) for row in rows]


@router.put("", response_model=list[ApprovalPolicyRead])
async def update_policies(
    payload: ApprovalPolicyUpdate,
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ApprovalPolicyRead]:
    items = [
        {"tool": item.tool.strip(), "action": item.action} for item in payload.policies
    ]
    rows = await approval_service.replace_policies(session, tenant_id, items)
    return [ApprovalPolicyRead(**row) for row in rows]
