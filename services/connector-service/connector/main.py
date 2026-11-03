"""服务入口:service-kit 统一装配(create_service_app + build_lifespan)。

启动(项目目录内):uv run uvicorn connector.main:app --port 8004
Nx(仓库根):npx nx run connector-service:serve

连接器平台内置飞书只读连接器;provider 由代码 manifest 注册(connector.providers)。
"""

from __future__ import annotations

import httpx
from common.auth import JwtVerifier
from common.service_kit import build_lifespan, create_service_app, install_database
from fastapi import FastAPI

from connector.api.health import health_router
from connector.api.v1.router import api_router
from connector.core.config import Settings
from connector.models import (  # noqa: F401  注册 ORM 元数据
    connections as connections_models,
)

# 生命周期:启动建 DB(dev/test 直建表),随后由 service-kit 装配鉴权校验器。
_lifespan = build_lifespan(startup=install_database)


def create_app(
    settings: Settings | None = None,
    token_verifier: JwtVerifier | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """注入点:token_verifier / http_transport 供测试使用(零网络)。"""
    return create_service_app(
        name="connector-service",
        settings=settings or Settings(),
        lifespan=_lifespan,
        routers=[(health_router, ""), (api_router, "/v1")],
        state={
            "token_verifier": token_verifier,
            "http_transport": http_transport,
        },
    )


app = create_app()
