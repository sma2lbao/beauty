"""FastAPI 依赖:DB session、租户解析、配置与执行器。

租户来源与其余服务一致:默认(鉴权开启)校验 Bearer token 的 claim;
显式关闭鉴权(AUTH_ENABLED=false)才沿用 X-Tenant-Id 临时头,缺失即 401。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from common.auth import JwtVerifier
from common.errors import UnauthorizedError
from common.logging import bind_request_context
from database.session import session_scope
from fastapi import Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from tool_service.core.config import Settings
from tool_service.services.connectors import ConnectorGateway
from tool_service.services.execution import ToolRunner
from tool_service.services.sandbox import SandboxProvider


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
        bind_request_context(tenant_id=x_tenant_id)
        return x_tenant_id
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("auth.token_missing", "缺少 Bearer token")
    principal = await verifier.verify(authorization.split(" ", 1)[1].strip())
    bind_request_context(tenant_id=principal.tenant_id, user_id=principal.subject)
    return principal.tenant_id


async def get_optional_tenant_id(
    request: Request,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str | None:
    """可选租户解析(连接器目录用):不带凭据返回 None(只看内置目录)。

    带 Bearer 时严格校验(错 token 401,不静默降级成"只看内置");鉴权关闭
    (AUTH_ENABLED=false)时沿用 X-Tenant-Id 临时头。用于 GET /v1/tools:
    orchestrator 按租户带 Bearer 拉目录,运维/健康检查不带凭据仍可用。
    """

    verifier: JwtVerifier | None = request.app.state.token_verifier
    if verifier is None:
        if not x_tenant_id:
            return None
        bind_request_context(tenant_id=x_tenant_id)
        return x_tenant_id
    if not authorization:
        return None
    if not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("auth.token_missing", "缺少 Bearer token")
    principal = await verifier.verify(authorization.split(" ", 1)[1].strip())
    bind_request_context(tenant_id=principal.tenant_id, user_id=principal.subject)
    return principal.tenant_id


def get_settings(request: Request) -> Settings:
    """服务配置(启动时写进 app.state)。"""

    return request.app.state.settings


def get_tool_runner(request: Request) -> ToolRunner:
    """工具执行器(启动时装配,tenant 无关、可并发复用)。"""

    return request.app.state.tool_runner


def get_sandbox_provider(request: Request) -> SandboxProvider:
    """沙箱提供方(测试可能替换成假实现)。"""

    return request.app.state.sandbox_provider


def get_connector_gateway(request: Request) -> ConnectorGateway | None:
    """连接器网关(未配置内部密钥时为 None,目录只含内置工具)。"""

    return getattr(request.app.state, "connector_gateway", None)
