"""服务入口:create_app 工厂 + lifespan。

启动(项目目录内):uv run uvicorn connector.main:app --port 8004
Nx(仓库根):npx nx run connector-service:serve
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from common.auth import JwtVerifier
from common.errors import AppError
from common.logging import setup_logging
from database.base import Base
from database.session import create_session_factory
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from connector.api.health import health_router
from connector.api.v1.router import api_router
from connector.core.config import Settings
from connector.models import (
    connections as connections_models,  # noqa: F401  注册 ORM 元数据
)
from connector.services.mail import build_transport


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
    if app.state.transport_factory is None:
        # 生产装配真实传输工厂;测试注入 FakeTransport 走同一属性
        app.state.transport_factory = partial(
            build_transport, credential_secret=settings.credential_secret
        )

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
    transport_factory=None,
    token_verifier: JwtVerifier | None = None,
) -> FastAPI:
    """注入点:transport_factory(假传输)、token_verifier(假 JWKS)供测试使用。"""
    app = FastAPI(title="connector-service", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or Settings()
    app.state.transport_factory = transport_factory
    app.state.token_verifier = token_verifier

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        """common 统一错误模型 → {"code","message","details"}(架构 §11)。"""
        return JSONResponse(status_code=exc.status, content=exc.to_dict())

    app.include_router(health_router)
    app.include_router(api_router, prefix="/v1")
    return app


app = create_app()
