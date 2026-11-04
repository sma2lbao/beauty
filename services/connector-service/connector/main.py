"""服务入口:service-kit 统一装配(create_service_app + build_lifespan)。

启动(项目目录内):uv run uvicorn connector.main:app --port 8004
Nx(仓库根):npx nx run connector-service:serve

仓库内置 Notion 只读连接器;provider 由代码 manifest 注册(connector.providers)。

lifespan 顺序:建 DB -> 按开关启动 outbox 派发器/巡检任务 -> 鉴权装配 -> 服务;
关闭时先停后台任务,再关注入的 http transport,最后 service-kit 释放鉴权/DB。
"""

from __future__ import annotations

import events.outbox  # noqa: F401  注册 outbox/processed_events ORM(create_all 用)
import httpx
from common.auth import JwtVerifier
from common.service_kit import build_lifespan, create_service_app, install_database
from fastapi import FastAPI

from connector.api.health import health_router
from connector.api.v1.router import api_router
from connector.core.config import Settings
from connector.core.runtime import ConnectorRuntime
from connector.models import (  # noqa: F401  注册 ORM 元数据
    connections as connections_models,
)


async def _startup(app: FastAPI) -> None:
    """先建 DB,再按配置启动后台作业(outbox 派发、24h 巡检)。"""
    await install_database(app)
    runtime = ConnectorRuntime(
        settings=app.state.settings,
        session_factory=app.state.session_factory,
        transport=getattr(app.state, "http_transport", None),
    )
    await runtime.start()
    app.state.connector_runtime = runtime


async def _shutdown(app: FastAPI) -> None:
    """先停后台任务(否则 engine.dispose 后任务还会去取连接),再关 transport。"""
    runtime: ConnectorRuntime | None = getattr(app.state, "connector_runtime", None)
    if runtime is not None:
        await runtime.stop()
        app.state.connector_runtime = None
    transport = getattr(app.state, "http_transport", None)
    if transport is not None:
        await transport.aclose()


# 生命周期:startup 在前、shutdown 在后,鉴权校验器由 service-kit 插在两者之间。
_lifespan = build_lifespan(startup=_startup, shutdown=_shutdown)


def create_app(
    settings: Settings | None = None,
    token_verifier: JwtVerifier | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """注入点:token_verifier / http_transport 供测试使用(零网络)。"""
    return create_service_app(
        name="connector-service",
        settings=settings or Settings(),
        lifespan=_lifespan,
        routers=[(health_router, ""), (api_router, "/v1")],
        state={
            "token_verifier": token_verifier,
            "http_transport": http_transport,
        },
    )


app = create_app()
