"""服务入口:create_app 工厂 + lifespan。

启动(项目目录内):uv run uvicorn knowledge.main:app --port 8006
Nx(仓库根):npx nx run knowledge-service:serve
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

from knowledge.api.health import health_router
from knowledge.api.v1.router import api_router
from knowledge.core.config import Settings
from knowledge.models import (
    knowledge as knowledge_models,  # noqa: F401  注册 ORM 元数据
)
from knowledge.services.embeddings import EmbeddingProvider, build_embedder


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

    # 嵌入提供方(§5 唯一出口):测试注入优先;配置错误在此快速失败
    embedder: EmbeddingProvider = app.state.embedder or build_embedder(
        settings.embedding_provider,
        model=settings.embedding_model,
        gateway_url=settings.llm_gateway_url,
        timeout_seconds=settings.embedding_timeout_seconds,
    )
    app.state.embedder = embedder

    # 鉴权装配(§10):测试注入优先;否则 auth_enabled 时起 token 校验器
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
        closer = getattr(embedder, "aclose", None)
        if closer is not None:
            await closer()
        await factory.engine.dispose()


def create_app(
    settings: Settings | None = None,
    embedder: EmbeddingProvider | None = None,
    token_verifier: JwtVerifier | None = None,
) -> FastAPI:
    """测试注入:embedder(假嵌入/本地提供方)、token_verifier(假 JWKS)。"""
    app = FastAPI(title="knowledge-service", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or Settings()
    app.state.embedder = embedder
    app.state.token_verifier = token_verifier

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        """common 统一错误模型 → {"code","message","details"}(架构 §11)。"""
        return JSONResponse(status_code=exc.status, content=exc.to_dict())

    app.include_router(health_router)
    app.include_router(api_router, prefix="/v1")
    return app


app = create_app()
