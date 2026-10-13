"""服务入口:create_app 工厂 + lifespan。

启动(项目目录内):uv run uvicorn account.main:app --port 8001
Nx(仓库根):npx nx run account-service:serve
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from common.auth import JwtVerifier
from common.errors import AppError
from common.logging import setup_logging
from database.base import Base
from database.session import create_session_factory
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from account.api.health import health_router
from account.api.v1.router import api_router
from account.core.config import Settings
from account.core.dev_idp import DevIdP
from account.models import account as account_models  # noqa: F401  注册 ORM 元数据


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

    # 鉴权装配(§10):认证交给 IdP,这里只解析当前租户
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
    """测试注入:token_verifier(假 JWKS)。"""
    app = FastAPI(title="account-service", version="0.1.0", lifespan=lifespan)
    settings = settings or Settings()
    app.state.settings = settings
    app.state.token_verifier = token_verifier

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        """common 统一错误模型 → {"code","message","details"}(架构 §11)。"""
        return JSONResponse(status_code=exc.status, content=exc.to_dict())

    app.include_router(health_router)
    app.include_router(api_router, prefix="/v1")
    if settings.dev_auth_enabled:
        # dev IdP 桩(core/dev_idp.py):登录 + OIDC 发现/JWKS;
        # 关桩时端点不存在(404),生产部署不受影响
        from account.api.v1 import auth as auth_api
        from account.api.v1 import oidc as oidc_api

        app.state.dev_idp = DevIdP(
            issuer=settings.auth_issuer,
            audience=settings.dev_token_audience,
            ttl_seconds=settings.dev_token_ttl_seconds,
        )
        app.include_router(auth_api.router, prefix="/v1")
        app.include_router(oidc_api.router)
    return app


app = create_app()
