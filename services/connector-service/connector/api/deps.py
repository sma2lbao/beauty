"""FastAPI 依赖:DB session、传输工厂与(临时)租户头。

租户当前来自 X-Tenant-Id 请求头;接入 IdP(Logto)后改为从 JWT claims 提取,
依赖签名不变,端点零改动(与 orchestrator 同约定)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from database.session import session_scope
from fastapi import Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from connector.models.connections import Connection
from connector.services.mail import MailTransport


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with session_scope(factory) as session:
        yield session


def get_tenant_id(x_tenant_id: str = Header(alias="X-Tenant-Id")) -> str:
    """临时实现:租户来自请求头;IdP 接入后从 JWT 解出。"""
    return x_tenant_id


def get_transport_factory(
    request: Request,
) -> Callable[[Connection], MailTransport]:
    """lifespan 装配的传输工厂(测试注入 FakeTransport 走同一入口)。"""
    return request.app.state.transport_factory
