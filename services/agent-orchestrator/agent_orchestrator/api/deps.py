"""FastAPI 依赖:DB session、run 执行图与(临时)租户头。

租户当前来自 X-Tenant-Id 请求头;接入 IdP(Logto)后改为从 JWT claims 提取,
依赖签名不变,端点零改动。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from database.session import session_scope
from fastapi import Header, Request
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with session_scope(factory) as session:
        yield session


def get_run_graph(request: Request) -> CompiledStateGraph:
    """lifespan 装配的 run 执行图(app.state.run_graph)。"""
    return request.app.state.run_graph


async def get_tenant_id(x_tenant_id: str = Header(alias="X-Tenant-Id")) -> str:
    """临时实现:租户来自请求头;IdP 接入后从 JWT 解出。"""
    return x_tenant_id
