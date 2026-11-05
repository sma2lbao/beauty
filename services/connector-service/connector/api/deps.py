"""FastAPI 依赖:DB session、调用方主体与账号解析。

账号来源(架构 §10):默认(鉴权开启)校验 Bearer token 的 claim;显式关闭
鉴权(AUTH_ENABLED=false)才沿用 X-Account-Id 临时头,缺失即 401。
与 agent-orchestrator 同一套约定。内部端点(tool-service 调目录/执行)另走
X-Internal-Secret,账号由 X-Account-Id 显式传递。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from common.auth import JwtVerifier, Principal
from common.errors import UnauthorizedError
from common.logging import bind_request_context
from common.security.internal_secret import ACCOUNT_HEADER
from database.session import session_scope
from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with session_scope(factory) as session:
        yield session


async def get_principal(
    request: Request,
    x_account_id: Annotated[str | None, Header(alias="X-Account-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """解析调用方:IdP token 优先,鉴权关闭时才认临时头。"""
    verifier: JwtVerifier | None = request.app.state.token_verifier
    if verifier is None:
        if not x_account_id:
            raise UnauthorizedError(
                "auth.account_missing",
                "缺少 X-Account-Id(鉴权未开启时的临时账号头)",
            )
        bind_request_context(account_id=x_account_id)
        # 无 token 场景没有用户身份;owner_subject 留空
        return Principal(subject="", account_id=x_account_id, scopes=(), raw_claims={})
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("auth.token_missing", "缺少 Bearer token")
    principal = await verifier.verify(authorization.split(" ", 1)[1].strip())
    bind_request_context(account_id=principal.account_id, user_id=principal.subject)
    return principal


async def get_account_id(
    principal: Annotated[Principal, Depends(get_principal)],
) -> str:
    return principal.account_id


async def get_owner_subject(
    principal: Annotated[Principal, Depends(get_principal)],
) -> str | None:
    """授权归属人:token 的 subject;无鉴权模式下为空。"""
    return principal.subject or None


async def get_internal_account_id(
    x_account_id: Annotated[str | None, Header(alias=ACCOUNT_HEADER)] = None,
) -> str:
    """内部端点账号:显式头,X-Internal-Secret 已由端点依赖校验。"""
    if not x_account_id:
        raise UnauthorizedError("auth.account_missing", "内部调用缺少 X-Account-Id")
    bind_request_context(account_id=x_account_id)
    return x_account_id
