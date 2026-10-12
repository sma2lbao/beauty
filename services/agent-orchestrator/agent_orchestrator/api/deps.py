"""FastAPI 依赖:DB session、run 执行图与租户解析。

租户来源(架构 §10):
- 鉴权开启(`AUTH_ENABLED=true`):校验 `Authorization: Bearer <access token>`,
  租户从 token claim 取——**不可信则 401,绝不回退到请求头**;
- 鉴权关闭(dev/test):沿用 `X-Tenant-Id` 临时头,缺失即 401。

接入 IdP 后端点零改动:只换这里的实现。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from common.artifacts import ArtifactStore
from common.auth import JwtVerifier
from common.errors import UnauthorizedError
from database.session import session_scope
from fastapi import Header, Request
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.services.workspace import Workspace


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with session_scope(factory) as session:
        yield session


def get_run_graph(request: Request) -> CompiledStateGraph:
    """lifespan 装配的 run 执行图(app.state.run_graph)。"""
    return request.app.state.run_graph


def get_workspace_client(request: Request) -> Workspace | None:
    """lifespan 按配置装配的工作区客户端;未开启为 None(不镜像)。"""
    return request.app.state.workspace_client


def get_artifact_store(request: Request) -> ArtifactStore | None:
    """lifespan 按配置装配的交付物存储;database 模式为 None(内容在行里)。"""
    return request.app.state.artifact_store


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
