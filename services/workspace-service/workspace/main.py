"""服务入口:create_app 工厂 + lifespan。

启动(项目目录内):uv run uvicorn workspace.main:app --port 8005
Nx(仓库根):npx nx run workspace-service:serve
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from common.artifacts import ArtifactStore, build_artifact_store
from common.auth import JwtVerifier
from common.errors import AppError
from common.logging import setup_logging
from database.base import Base
from database.session import create_session_factory
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from workspace.api.health import health_router
from workspace.api.v1.router import api_router
from workspace.core.config import Settings
from workspace.models import (
    workspace as workspace_models,  # noqa: F401  注册 ORM 元数据
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

    # 交付物存储(§7):测试注入优先;未知后端在启动时报错
    store: ArtifactStore | None = app.state.artifact_store
    if store is None:
        store = build_artifact_store(
            settings.artifact_store, root=settings.artifact_root
        )
    app.state.artifact_store = store

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
        await factory.engine.dispose()


def create_app(
    settings: Settings | None = None,
    artifact_store: ArtifactStore | None = None,
    token_verifier: JwtVerifier | None = None,
) -> FastAPI:
    """测试注入:artifact_store(假存储)、token_verifier(假 JWKS)。"""
    app = FastAPI(title="workspace-service", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or Settings()
    app.state.artifact_store = artifact_store
    app.state.token_verifier = token_verifier

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        """common 统一错误模型 → {"code","message","details"}(架构 §11)。"""
        return JSONResponse(status_code=exc.status, content=exc.to_dict())

    app.include_router(health_router)
    app.include_router(api_router, prefix="/v1")
    return app


app = create_app()
