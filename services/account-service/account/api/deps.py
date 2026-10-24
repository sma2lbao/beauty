"""FastAPI 依赖:DB session、租户解析(§10)与配置。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from common.auth import JwtVerifier
from common.errors import UnauthorizedError
from common.logging import bind_request_context
from common.security import require_internal_secret
from database.session import session_scope
from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from account.core.config import Settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with session_scope(factory) as session:
        yield session


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_internal_tenant_id(
    _: Annotated[None, Depends(require_internal_secret)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
) -> str:
    """内部调用租户解析:共享密钥(common.security)+ 显式 X-Tenant-Id。

    服务间调用(orchestrator 上报用量、读配额快照)没有用户上下文,
    凭 X-Internal-Secret 认证;密钥不匹配/缺租户头都是 401。
    """
    if not x_tenant_id:
        raise UnauthorizedError("auth.tenant_missing", "内部调用缺少 X-Tenant-Id")
    bind_request_context(tenant_id=x_tenant_id)
    return x_tenant_id


async def get_tenant_id(
    request: Request,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """租户解析:IdP token 优先(默认);显式关闭鉴权才认临时头(与兄弟服务同约定)。"""
    verifier: JwtVerifier | None = request.app.state.token_verifier
    if verifier is None:
        if not x_tenant_id:
            raise UnauthorizedError(
                "auth.header_missing",
                "缺少 X-Tenant-Id(鉴权未开启时的临时租户头)",
            )
        bind_request_context(tenant_id=x_tenant_id)
        return x_tenant_id
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("auth.token_missing", "缺少 Bearer token")
    principal = await verifier.verify(authorization.split(" ", 1)[1].strip())
    bind_request_context(tenant_id=principal.tenant_id, user_id=principal.subject)
    return principal.tenant_id
