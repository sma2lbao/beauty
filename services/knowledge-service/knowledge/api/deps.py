"""FastAPI 依赖:DB session、租户解析(§10)、嵌入提供方与配置。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from common.auth import JwtVerifier
from common.errors import UnauthorizedError
from database.session import session_scope
from fastapi import Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge.core.config import Settings
from knowledge.services.embeddings import EmbeddingProvider


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with session_scope(factory) as session:
        yield session


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_embedder(request: Request) -> EmbeddingProvider:
    return request.app.state.embedder


async def get_tenant_id(
    request: Request,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """租户解析:IdP token 优先,鉴权关闭时才认临时头(与兄弟服务同约定)。"""
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
