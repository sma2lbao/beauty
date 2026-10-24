"""服务入口:service-kit 统一装配(create_service_app + build_lifespan)。

启动(项目目录内):uv run uvicorn connector.main:app --port 8004
Nx(仓库根):npx nx run connector-service:serve

服务本身是连接器框架:当前没有内置连接器(邮件、日历能力均已下线),
只保留连接存储/凭证加密/租户隔离与健康探针。
"""

from __future__ import annotations

from common.auth import JwtVerifier
from common.service_kit import build_lifespan, create_service_app, install_database
from fastapi import FastAPI

from connector.api.health import health_router
from connector.api.v1.router import api_router
from connector.core.config import Settings
from connector.models import (
    connections as connections_models,  # noqa: F401  注册 ORM 元数据
)

# 生命周期:启动建 DB(dev/test 直建表),随后由 service-kit 装配鉴权校验器。
_lifespan = build_lifespan(startup=install_database)


def create_app(
    settings: Settings | None = None,
    token_verifier: JwtVerifier | None = None,
) -> FastAPI:
    """注入点:token_verifier 供测试使用。"""
    return create_service_app(
        name="connector-service",
        settings=settings or Settings(),
        lifespan=_lifespan,
        routers=[(health_router, ""), (api_router, "/v1")],
        state={"token_verifier": token_verifier},
    )


app = create_app()
