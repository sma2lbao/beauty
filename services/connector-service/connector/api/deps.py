"""FastAPI 依赖:DB session、传输工厂与租户解析。

租户来源(架构 §10):默认(鉴权开启)校验 Bearer token 的 claim;显式关闭
鉴权(AUTH_ENABLED=false)才沿用 X-Tenant-Id 临时头,缺失即 401。
与 agent-orchestrator 同一套约定。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Annotated

from common.auth import JwtVerifier
from common.errors import UnauthorizedError
from database.session import session_scope
from fastapi import Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from connector.core.config import Settings
from connector.models.connections import Connection
from connector.services.mail import MailTransport


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with session_scope(factory) as session:
        yield session


async def get_tenant_id(
    request: Request,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """租户解析:IdP token 优先,鉴权关闭时才认临时头。"""
    verifier: JwtVerifier | None = request.app.state.token_verifier
    if verifier is None:
        if not x_tenant_id:
            raise UnauthorizedError(
                "auth.header_missing",
                "缺少 X-Tenant-Id(鉴权未开启时的临时租户头)",
            )
        return x_tenant_id
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("auth.token_missing", "缺少 Bearer token")
    principal = await verifier.verify(authorization.split(" ", 1)[1].strip())
    return principal.tenant_id


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_transport_factory(
    request: Request,
) -> Callable[[Connection], MailTransport]:
    """lifespan 装配的传输工厂(测试注入 FakeTransport 走同一入口)。"""
    return request.app.state.transport_factory
