"""服务入口:create_app 工厂 + lifespan。

启动(项目目录内):uv run uvicorn account.main:app --port 8001
Nx(仓库根):npx nx run account-service:serve
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from common.auth import JwtVerifier
from common.logging import setup_logging
from common.middleware import RequestContextMiddleware, install_app_error_handler
from common.observability import install_observability
from common.tracing import exclude_telemetry
from database.base import Base
from database.session import create_session_factory
from fastapi import FastAPI

from account.api.health import health_router
from account.api.oauth_metadata import router as oauth_metadata_router
from account.api.v1 import auth as auth_api
from account.api.v1 import oauth as oauth_api
from account.api.v1 import oidc as oidc_api
from account.api.v1.router import api_router
from account.core.config import Settings
from account.core.idp import IdPSigner, load_or_create_signer
from account.core.ratelimit import SlidingWindowLimiter
from account.models import account as account_models  # noqa: F401  注册 ORM 元数据


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    setup_logging(settings)
    # Sentry(P1)与 /internal/metrics(P2):DSN 为空/开关关闭时都是 no-op。
    install_observability(app, settings)
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
    oauth_transport=None,
) -> FastAPI:
    """测试注入:token_verifier(假 JWKS)、oauth_transport(假 OAuth HTTP)。"""
    app = FastAPI(
        title="account-service",
        version="0.1.0",
        lifespan=lifespan,
        # 服务端 span 用 FastAPI 原生 telemetry;探针与指标端点不采样。
        telemetry={"exclude": exclude_telemetry},
    )
    settings = settings or Settings()
    app.state.settings = settings
    app.state.token_verifier = token_verifier
    app.state.oauth_transport = oauth_transport
    app.state.oauth_register_limiter = SlidingWindowLimiter(
        settings.oauth21_register_limit_per_hour
    )

    install_app_error_handler(app)
    app.add_middleware(RequestContextMiddleware, settings=app.state.settings)

    app.include_router(health_router)
    app.include_router(api_router, prefix="/v1")
    # 自建认证(由 dev 桩转正):注册/登录/刷新/登出/内部签发 + OIDC 发现/JWKS
    # 始终挂载——本服务就是签发方,兄弟服务的 JwtVerifier 靠这些端点验签
    if settings.signing_key_path:
        app.state.signer = load_or_create_signer(
            settings.signing_key_path,
            issuer=settings.auth_issuer,
            audience=settings.token_audience,
            ttl_seconds=settings.token_ttl_seconds,
        )
    else:
        app.state.signer = IdPSigner(
            issuer=settings.auth_issuer,
            audience=settings.token_audience,
            ttl_seconds=settings.token_ttl_seconds,
        )
    app.include_router(auth_api.router, prefix="/v1")
    app.include_router(oauth_api.router, prefix="/v1")
    app.include_router(oidc_api.router)
    app.include_router(oauth_metadata_router)
    return app


app = create_app()
