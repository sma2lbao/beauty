"""账号端点:当前账号档案与设备会话。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from account.api.deps import get_account_id, get_session
from account.schemas.account import (
    AccountRead,
    AccountUpdate,
    SessionCreate,
    SessionRead,
)
from account.services import account as account_service

router = APIRouter(tags=["account"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
AccountId = Annotated[str, Depends(get_account_id)]


@router.get("/me", response_model=AccountRead)
async def get_me(session: DBSession, account_id: AccountId) -> AccountRead:
    """当前账号档案(account_id = users.id)。"""
    account = await account_service.require_account(session, account_id)
    return AccountRead.model_validate(account)


@router.patch("/me", response_model=AccountRead)
async def update_me(
    body: AccountUpdate, session: DBSession, account_id: AccountId
) -> AccountRead:
    """改显示名(其余字段由服务端维护)。"""
    account = await account_service.update_account(session, account_id, body)
    return AccountRead.model_validate(account)


@router.post(
    "/sessions", response_model=SessionRead, status_code=status.HTTP_201_CREATED
)
async def create_session(
    body: SessionCreate, session: DBSession, account_id: AccountId
) -> SessionRead:
    """登记设备会话(app 预留,§7);账号取当前 token 所属账号。"""
    record = await account_service.create_session(session, account_id, body.device)
    return SessionRead.model_validate(record)


@router.get("/sessions", response_model=list[SessionRead])
async def list_sessions(session: DBSession, account_id: AccountId) -> list[SessionRead]:
    records = await account_service.list_sessions(session, account_id)
    return [SessionRead.model_validate(record) for record in records]


@router.post("/sessions/{session_id}/revoke", response_model=SessionRead)
async def revoke_session(
    session_id: str, session: DBSession, account_id: AccountId
) -> SessionRead:
    """吊销会话(重复吊销 409;跨账号 404)。"""
    record = await account_service.revoke_session(session, account_id, session_id)
    return SessionRead.model_validate(record)
