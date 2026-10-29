"""统一应用生命周期装配(lifespan 钩子 + 鉴权校验器 + 资源回收)。

启动顺序与原各服务 lifespan 逐行等价:日志 → 观测 → 服务启动钩子 →
鉴权校验器(测试注入优先) → 请求处理;关闭顺序:verifier 前置钩子 →
校验器 → 服务关闭钩子 → engine.dispose()。

`shutdown_before_verifier` 用于需要在校验器之前收尾的自管资源
(account 的 BYOK KMS、workspace 的 reaper 任务/沙箱 shell 会话)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from common.auth import JwtVerifier
from common.logging import setup_logging
from common.observability import install_observability
from common.service_kit.settings import ServiceSettings

StartupHook = Callable[[FastAPI], Awaitable[None]]
ShutdownHook = Callable[[FastAPI], Awaitable[None]]
LifespanFactory = Callable[[FastAPI], Any]


async def install_database(app: FastAPI) -> None:
    """建 session 工厂,auto_create_tables 时直建表(dev/test;生产走 alembic)。

    延迟导入 database:database.repository 依赖 common.errors,common 若声明
    database 依赖会形成包级循环;本模块只在服务启动时执行,运行时环境
    (服务依赖 database)一定具备该包。
    """
    settings: ServiceSettings = app.state.settings
    from database.base import Base
    from database.session import create_session_factory

    factory = create_session_factory(settings.database_url)
    app.state.session_factory = factory
    if settings.auto_create_tables:
        async with factory.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)


def build_lifespan(
    *,
    startup: StartupHook | None = None,
    shutdown: ShutdownHook | None = None,
    shutdown_before_verifier: ShutdownHook | None = None,
) -> LifespanFactory:
    """生成 FastAPI lifespan;服务通过钩子注入自身启停逻辑。

    startup 在日志/观测装配之后、鉴权校验器之前执行(原服务顺序:先建
    DB/嵌入提供方,再装配校验器);shutdown 在校验器关闭之后、引擎释放之前;
    shutdown_before_verifier 在同一关闭链路里、校验器之前执行。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings: ServiceSettings = app.state.settings
        setup_logging(settings)
        # Sentry(P1)与 /internal/metrics(P2):DSN 为空/开关关闭时都是 no-op。
        install_observability(app, settings)

        # 鉴权装配(架构 §10):测试注入优先;否则 auth_enabled 时起 token 校验器。
        # BFF / llm-gateway 没有鉴权面(配置里无 auth_enabled),按无鉴权处理。
        verifier: JwtVerifier | None = getattr(app.state, "token_verifier", None)
        auth_enabled = bool(getattr(settings, "auth_enabled", False))
        owns_verifier = False
        try:
            if startup is not None:
                await startup(app)
            if verifier is None and auth_enabled:
                verifier = JwtVerifier(
                    issuer=settings.auth_issuer,
                    audience=settings.auth_audience,
                    jwks_url=settings.auth_jwks_url,
                    tenant_claim=settings.auth_tenant_claim,
                )
                owns_verifier = True
            app.state.token_verifier = verifier
            yield
        finally:
            if shutdown_before_verifier is not None:
                await shutdown_before_verifier(app)
            if owns_verifier and verifier is not None:
                await verifier.aclose()
            if shutdown is not None:
                await shutdown(app)
            factory = getattr(app.state, "session_factory", None)
            if factory is not None:
                await factory.engine.dispose()

    return lifespan
