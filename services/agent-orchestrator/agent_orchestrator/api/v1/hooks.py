"""账号级应用 hooks 管理端点(design/hooks.md §8,P1-B)。

- 总开关 ``hooks_enabled=false`` 时所有端点 409 ``hooks.disabled``;
- secret 只在创建与轮换响应里返回一次,列表/更新不回显;
- target 在创建/更新时严格校验(https + 公网 + DNS 可解析),非法 422
  ``hooks.invalid_target``;
- 删除与更新不写 audit_logs(§12:非管理动作,只留结构化日志)。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import get_account_id, get_session, get_settings
from agent_orchestrator.core.config import Settings
from agent_orchestrator.schemas.hook import (
    HookCreate,
    HookEvent,
    HookRead,
    HookSecretRead,
    HookUpdate,
)
from agent_orchestrator.services import hooks as hooks_service

router = APIRouter(prefix="/hooks", tags=["hooks"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
AccountId = Annotated[str, Depends(get_account_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("", response_model=list[HookRead])
async def list_hooks(
    session: DBSession,
    account_id: AccountId,
    settings: SettingsDep,
    event: HookEvent | None = None,
) -> list[HookRead]:
    """列出账号 hooks(created_at, id 升序;event 可选过滤;不回显 secret)。"""
    return await hooks_service.list_hooks(
        session, account_id, event=event, settings=settings
    )


@router.post("", response_model=HookSecretRead, status_code=status.HTTP_201_CREATED)
async def create_hook(
    payload: HookCreate,
    session: DBSession,
    account_id: AccountId,
    settings: SettingsDep,
) -> HookSecretRead:
    """创建 hook:一次性返回 secret(之后无法再取,只能轮换)。"""
    return await hooks_service.create_hook(
        session, account_id, payload, settings=settings
    )


@router.patch("/{hook_id}", response_model=HookRead)
async def update_hook(
    hook_id: str,
    payload: HookUpdate,
    session: DBSession,
    account_id: AccountId,
    settings: SettingsDep,
) -> HookRead:
    """局部更新 matcher/target/timeout_seconds/enabled(省略=保持)。"""
    return await hooks_service.update_hook(
        session, account_id, hook_id, payload, settings=settings
    )


@router.delete("/{hook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_hook(
    hook_id: str,
    session: DBSession,
    account_id: AccountId,
    settings: SettingsDep,
) -> Response:
    """删除 hook(不入审计;返回 204)。"""
    await hooks_service.delete_hook(session, account_id, hook_id, settings=settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{hook_id}/rotate", response_model=HookSecretRead)
async def rotate_secret(
    hook_id: str,
    session: DBSession,
    account_id: AccountId,
    settings: SettingsDep,
) -> HookSecretRead:
    """轮换签名密钥:返回新 secret,旧 secret 立即失效。"""
    return await hooks_service.rotate_secret(
        session, account_id, hook_id, settings=settings
    )
