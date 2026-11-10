"""管理面 API(admin-plane §3.3):账号检索/详情、配额/角色/状态、审计日志。

鉴权一律走 require_admin(角色在库里,每请求判定);读端点不审计,
三个写端点与业务同事务写 audit_logs;非法查询参数由 FastAPI 返 422。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from account.api.deps import get_session, require_admin
from account.models.account import User
from account.schemas.admin import (
    AdminAccount,
    AdminAccountDetail,
    AdminAccountList,
    AuditLogList,
    QuotaUpdate,
    RoleUpdate,
    StatusUpdate,
)
from account.services import admin as admin_service
from account.services import audit

router = APIRouter(prefix="/admin", tags=["admin"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
AdminUser = Annotated[User, Depends(require_admin)]


@router.get("/accounts", response_model=AdminAccountList)
async def list_accounts(
    session: DBSession,
    _: AdminUser,
    q: Annotated[str | None, Query(max_length=320)] = None,
    status: Annotated[Literal["active", "disabled"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminAccountList:
    """账号分页列表:q 对 email/display_name 大小写不敏感包含,含本月用量。"""
    return await admin_service.list_accounts(
        session, q=q, status=status, limit=limit, offset=offset
    )


@router.get("/accounts/{account_id}", response_model=AdminAccountDetail)
async def get_account_detail(
    account_id: str, session: DBSession, _: AdminUser
) -> AdminAccountDetail:
    """账号详情:档案 + 与 /usage/summary 同口径的完整用量汇总。"""
    return await admin_service.get_account_detail(session, account_id)


@router.patch("/accounts/{account_id}/quota", response_model=AdminAccount)
async def update_quota(
    account_id: str,
    data: QuotaUpdate,
    request: Request,
    session: DBSession,
    actor: AdminUser,
) -> AdminAccount:
    """改配额:省略 = 保持,显式 null = 清除,整数 >= 1。"""
    ctx = audit.context_from_request(request, actor)
    return await admin_service.update_quota(
        session, actor=actor, account_id=account_id, data=data, ctx=ctx
    )


@router.patch("/accounts/{account_id}/role", response_model=AdminAccount)
async def update_role(
    account_id: str,
    data: RoleUpdate,
    request: Request,
    session: DBSession,
    actor: AdminUser,
) -> AdminAccount:
    """改角色(禁止改自己:409 admin.self_role)。"""
    ctx = audit.context_from_request(request, actor)
    return await admin_service.update_role(
        session, actor=actor, account_id=account_id, data=data, ctx=ctx
    )


@router.patch("/accounts/{account_id}/status", response_model=AdminAccount)
async def update_status(
    account_id: str,
    data: StatusUpdate,
    request: Request,
    session: DBSession,
    actor: AdminUser,
) -> AdminAccount:
    """改状态:停用同事务吊销该账号全部 refresh token 与 sessions。"""
    ctx = audit.context_from_request(request, actor)
    return await admin_service.update_status(
        session, actor=actor, account_id=account_id, data=data, ctx=ctx
    )


@router.get("/audit-logs", response_model=AuditLogList)
async def list_audit_logs(
    session: DBSession,
    _: AdminUser,
    actor_account_id: Annotated[str | None, Query(max_length=32)] = None,
    action: Annotated[str | None, Query(max_length=64)] = None,
    target_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditLogList:
    """审计日志分页:created_at desc, id desc。"""
    return await admin_service.list_audit_logs(
        session,
        limit=limit,
        offset=offset,
        actor_account_id=actor_account_id,
        action=action,
        target_id=target_id,
    )
