"""出站 HTTP:每次调用临时客户端,测试可注入 MockTransport 保持零网络。

真实调用(飞书 OpenAPI)与测试共用同一条代码路径;测试通过 create_app(
http_transport=...) 传入 httpx.MockTransport,永远不出网。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import Request

from connector.core.config import Settings


def get_http_transport(request: Request) -> httpx.AsyncBaseTransport | None:
    """测试注入点:真实部署恒为 None(走 httpx 默认网络传输)。"""
    return getattr(request.app.state, "http_transport", None)


@asynccontextmanager
async def open_client(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds, transport=transport
    ) as client:
        yield client
