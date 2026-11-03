"""FastAPI 依赖:DB session、调用方主体与租户解析。

租户来源(架构 §10):默认(鉴权开启)校验 Bearer token 的 claim;显式关闭
鉴权(AUTH_ENABLED=false)才沿用 X-Tenant-Id 临时头,缺失即 401。
与 agent-orchestrator 同一套约定。内部端点(tool-service 调目录/执行)另走
X-Internal-Secret,租户由 X-Tenant-Id 显式传递。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from common.auth import JwtVerifier, Principal
from common.errors import UnauthorizedError
from common.logging import bind_request_context
from common.security.internal_secret import TENANT_HEADER
from database.session import session_scope
from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with session_scope(factory) as session:
        yield session


async def get_principal(
    request: Request,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """解析调用方:IdP token 优先,鉴权关闭时才认临时头。"""
    verifier: JwtVerifier | None = request.app.state.token_verifier
    if verifier is None:
        if not x_tenant_id:
            raise UnauthorizedError(
                "auth.header_missing",
                "缺少 X-Tenant-Id(鉴权未开启时的临时租户头)",
            )
        bind_request_context(tenant_id=x_tenant_id)
        # 无 token 场景没有用户身份;owner_subject 留空(组织共享)
        return Principal(subject="", tenant_id=x_tenant_id, scopes=(), raw_claims={})
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("auth.token_missing", "缺少 Bearer token")
    principal = await verifier.verify(authorization.split(" ", 1)[1].strip())
    bind_request_context(tenant_id=principal.tenant_id, user_id=principal.subject)
    return principal


async def get_tenant_id(
    principal: Annotated[Principal, Depends(get_principal)],
) -> str:
    return principal.tenant_id


async def get_owner_subject(
    principal: Annotated[Principal, Depends(get_principal)],
) -> str | None:
    """授权归属人:token 的 subject;无鉴权模式下为空(视作组织共享)。"""
    return principal.subject or None


async def get_internal_tenant_id(
    x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
) -> str:
    """内部端点租户:显式头,X-Internal-Secret 已由端点依赖校验。"""
    if not x_tenant_id:
        raise UnauthorizedError("auth.tenant_missing", "内部调用缺少 X-Tenant-Id")
    bind_request_context(tenant_id=x_tenant_id)
    return x_tenant_id
