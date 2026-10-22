"""服务入口:create_app 工厂 + lifespan。

启动(项目目录内):uv run uvicorn connector.main:app --port 8004
Nx(仓库根):npx nx run connector-service:serve

服务本身是连接器框架:当前没有内置连接器(邮件、日历能力均已下线),
只保留连接存储/凭证加密/租户隔离与健康探针。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from common.auth import JwtVerifier
from common.logging import setup_logging
from common.middleware import RequestContextMiddleware, install_app_error_handler
from database.base import Base
from database.session import create_session_factory
from fastapi import FastAPI

from connector.api.health import health_router
from connector.api.v1.router import api_router
from connector.core.config import Settings
from connector.models import (
    connections as connections_models,  # noqa: F401  注册 ORM 元数据
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    setup_logging(settings)
    factory = create_session_factory(settings.database_url)
    app.state.session_factory = factory
    if settings.auto_create_tables:
        # dev/test 直建表;生产一律 alembic 迁移
        async with factory.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # 鉴权装配(架构 §10):测试注入优先;否则 auth_enabled 时起 token 校验器
    verifier: JwtVerifier | None = app.state.token_verifier
    owns_verifier = False
    if verifier is None and settings.auth_enabled:
        verifier = JwtVerifier(
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            jwks_url=settings.auth_jwks_url,
            tenant_claim=settings.auth_tenant_claim,
        )
        owns_verifier = True
    app.state.token_verifier = verifier
    try:
        yield
    finally:
        if owns_verifier:
            await verifier.aclose()
        await factory.engine.dispose()


def create_app(
    settings: Settings | None = None,
    token_verifier: JwtVerifier | None = None,
) -> FastAPI:
    """注入点:token_verifier 供测试使用。"""
    app = FastAPI(title="connector-service", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or Settings()
    app.state.token_verifier = token_verifier

    install_app_error_handler(app)
    app.add_middleware(RequestContextMiddleware, settings=app.state.settings)

    app.include_router(health_router)
    app.include_router(api_router, prefix="/v1")
    return app


app = create_app()
