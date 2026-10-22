"""服务入口:create_app 工厂 + lifespan。

启动(项目目录内):uv run uvicorn workspace.main:app --port 8005
Nx(仓库根):npx nx run workspace-service:serve
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import structlog
from common.artifacts import ArtifactStore, build_artifact_store
from common.auth import JwtVerifier
from common.logging import setup_logging
from common.middleware import RequestContextMiddleware, install_app_error_handler
from common.observability import install_observability
from common.tracing import exclude_telemetry
from database.base import Base
from database.session import create_session_factory
from fastapi import FastAPI

from workspace.api.health import health_router
from workspace.api.v1.router import api_router
from workspace.core.config import Settings
from workspace.models import sandbox as sandbox_models  # noqa: F401  注册 ORM 元数据
from workspace.models import workspace as workspace_models  # noqa: F401
from workspace.services.sandbox import reap_expired
from workspace.services.sandbox_provider import (
    SandboxProvider,
    build_sandbox_provider,
)

logger = structlog.get_logger(__name__)


async def _sandbox_reaper_loop(app: FastAPI) -> None:
    """过期沙箱回收循环:单次失败只记日志,不退出、不阻塞启动。"""
    settings: Settings = app.state.settings
    provider: SandboxProvider = app.state.sandbox_provider
    factory = app.state.session_factory
    while True:
        try:
            reaped = await reap_expired(factory, provider)
            if reaped:
                logger.info("sandbox reaper swept expired sandboxes", count=reaped)
        except Exception:  # noqa: BLE001 - 回收循环不能因单次异常退出
            logger.exception("sandbox reaper loop error")
        await asyncio.sleep(settings.sandbox_reaper_interval_seconds)


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

    # 沙箱运行时(§7 阶段 B / P1):测试注入优先;未知 provider 启动即报错
    provider: SandboxProvider | None = app.state.sandbox_provider
    if provider is None:
        provider = build_sandbox_provider(settings)
    app.state.sandbox_provider = provider

    # 过期回收:后台任务,失败不阻塞启动;shutdown 取消
    reaper_task: asyncio.Task[None] | None = None
    if settings.sandbox_reaper_enabled:
        reaper_task = asyncio.create_task(
            _sandbox_reaper_loop(app), name="workspace-sandbox-reaper"
        )

    try:
        yield
    finally:
        if reaper_task is not None:
            reaper_task.cancel()
            with suppress(asyncio.CancelledError):
                await reaper_task
        if owns_verifier:
            await verifier.aclose()
        await factory.engine.dispose()


def create_app(
    settings: Settings | None = None,
    artifact_store: ArtifactStore | None = None,
    token_verifier: JwtVerifier | None = None,
    sandbox_provider: SandboxProvider | None = None,
) -> FastAPI:
    """测试注入:artifact_store(假存储)、token_verifier(假 JWKS)、sandbox_provider。"""
    app = FastAPI(
        title="workspace-service",
        version="0.1.0",
        lifespan=lifespan,
        # 服务端 span 用 FastAPI 原生 telemetry;探针与指标端点不采样。
        telemetry={"exclude": exclude_telemetry},
    )
    app.state.settings = settings or Settings()
    app.state.artifact_store = artifact_store
    app.state.token_verifier = token_verifier
    app.state.sandbox_provider = sandbox_provider

    install_app_error_handler(app)
    app.add_middleware(RequestContextMiddleware, settings=app.state.settings)

    app.include_router(health_router)
    app.include_router(api_router, prefix="/v1")
    return app


app = create_app()
