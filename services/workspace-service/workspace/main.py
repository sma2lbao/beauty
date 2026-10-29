"""服务入口:create_app 工厂 + lifespan(装配统一走 common.service_kit)。

启动(项目目录内):uv run uvicorn workspace.main:app --port 8005
Nx(仓库根):npx nx run workspace-service:serve
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import structlog
from common.artifacts import ArtifactStore, build_artifact_store
from common.auth import JwtVerifier
from common.service_kit import build_lifespan, create_service_app, install_database
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
from workspace.services.shell_sessions import (
    ShellSessionManager,
    build_shell_session_manager,
)

logger = structlog.get_logger(__name__)

#: 后台回收任务在 app.state 上的属性名(启动建、关闭取消)。
_REAPER_TASK = "sandbox_reaper_task"
_SWEEPER_TASK = "shell_sweeper_task"


async def _sandbox_reaper_loop(app: FastAPI) -> None:
    """过期沙箱回收循环:单次失败只记日志,不退出、不阻塞启动。"""
    settings: Settings = app.state.settings
    provider: SandboxProvider = app.state.sandbox_provider
    factory = app.state.session_factory
    shell_sessions: ShellSessionManager = app.state.shell_sessions
    while True:
        try:
            reaped = await reap_expired(
                factory, provider, on_reaped=shell_sessions.close_sandbox
            )
            if reaped:
                logger.info("sandbox reaper swept expired sandboxes", count=reaped)
        except Exception:  # noqa: BLE001 - 回收循环不能因单次异常退出
            logger.exception("sandbox reaper loop error")
        await asyncio.sleep(settings.sandbox_reaper_interval_seconds)


async def _sandbox_shell_sweeper_loop(app: FastAPI) -> None:
    """shell 会话回收循环:空闲 TTL 关会话 + 清过保留期的已关闭会话。"""
    settings: Settings = app.state.settings
    manager: ShellSessionManager = app.state.shell_sessions
    while True:
        await asyncio.sleep(settings.sandbox_shell_sweep_interval_seconds)
        try:
            closed = await manager.sweep()
            if closed:
                logger.info("shell session sweeper closed idle sessions", count=closed)
        except Exception:  # noqa: BLE001 - 循环不能因单次异常退出
            logger.exception("shell session sweeper loop error")


async def _startup(app: FastAPI) -> None:
    """启动:建库连接 → 交付物存储 → 沙箱 provider → shell 会话 → 回收任务。

    测试注入优先(artifact_store/sandbox_provider/shell_sessions);未知后端
    /provider 在启动时报错。
    """
    settings: Settings = app.state.settings
    await install_database(app)

    # 交付物存储(§7)
    store: ArtifactStore | None = app.state.artifact_store
    if store is None:
        store = build_artifact_store(
            settings.artifact_store, root=settings.artifact_root
        )
    app.state.artifact_store = store

    # 沙箱运行时(§7 阶段 B / P1)
    provider: SandboxProvider | None = app.state.sandbox_provider
    if provider is None:
        provider = build_sandbox_provider(settings)
    app.state.sandbox_provider = provider
    if provider.provider == "local":
        # 本地 provider 在宿主上直接执行命令,没有隔离:启动时就明确告警
        logger.warning(
            "sandbox provider=local:命令直接在宿主执行、无隔离,"
            "仅限本地/单租户可信环境,生产请用 microsandbox"
        )

    # 持久 shell 会话(#2):进程内管理器
    shell_sessions: ShellSessionManager | None = app.state.shell_sessions
    if shell_sessions is None:
        shell_sessions = build_shell_session_manager(settings)
    app.state.shell_sessions = shell_sessions

    # 过期回收 + shell 会话回收:后台任务,失败不阻塞启动;shutdown 取消
    if settings.sandbox_reaper_enabled:
        setattr(
            app.state,
            _REAPER_TASK,
            asyncio.create_task(
                _sandbox_reaper_loop(app), name="workspace-sandbox-reaper"
            ),
        )
    setattr(
        app.state,
        _SWEEPER_TASK,
        asyncio.create_task(
            _sandbox_shell_sweeper_loop(app), name="workspace-shell-sweeper"
        ),
    )


async def _shutdown_before_verifier(app: FastAPI) -> None:
    """关闭:先取消回收任务、兜底关 shell 会话,再交给 kit 关校验器/引擎。"""
    reaper_task: asyncio.Task[None] | None = getattr(app.state, _REAPER_TASK, None)
    if reaper_task is not None:
        reaper_task.cancel()
        with suppress(asyncio.CancelledError):
            await reaper_task
    sweeper_task: asyncio.Task[None] | None = getattr(app.state, _SWEEPER_TASK, None)
    if sweeper_task is not None:
        sweeper_task.cancel()
        with suppress(asyncio.CancelledError):
            await sweeper_task
    # 进程退出兜底:关掉所有还活着的 shell 会话
    shell_sessions: ShellSessionManager | None = getattr(
        app.state, "shell_sessions", None
    )
    if shell_sessions is not None:
        await shell_sessions.shutdown()


_lifespan = build_lifespan(
    startup=_startup, shutdown_before_verifier=_shutdown_before_verifier
)


def create_app(
    settings: Settings | None = None,
    artifact_store: ArtifactStore | None = None,
    token_verifier: JwtVerifier | None = None,
    sandbox_provider: SandboxProvider | None = None,
    shell_sessions: ShellSessionManager | None = None,
) -> FastAPI:
    """测试注入:假存储 / 假 JWKS / provider / shell 会话管理器。"""
    return create_service_app(
        name="workspace-service",
        settings=settings or Settings(),
        lifespan=_lifespan,
        routers=[(health_router, ""), (api_router, "/v1")],
        state={
            "artifact_store": artifact_store,
            "token_verifier": token_verifier,
            "sandbox_provider": sandbox_provider,
            "shell_sessions": shell_sessions,
        },
    )


app = create_app()
