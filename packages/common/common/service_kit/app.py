"""统一应用工厂:装配顺序集中一处,服务不再各写一份 create_app。

顺序(与原服务一致):FastAPI 构造(telemetry 排除探针) → state →
统一错误处理 → CORS(可选) → 请求上下文中间件(最后注册 = 最外层) →
路由(按 prefix 挂载)。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from common.middleware import RequestContextMiddleware, install_app_error_handler
from common.service_kit.lifespan import LifespanFactory
from common.service_kit.settings import ServiceSettings
from common.tracing import exclude_telemetry

RouterBinding = tuple[APIRouter, str]
"""路由与挂载前缀:(health_router, "")、(api_router, "/v1")。"""


def create_service_app(
    *,
    name: str,
    settings: ServiceSettings,
    lifespan: LifespanFactory,
    routers: Sequence[RouterBinding] = (),
    version: str = "0.1.0",
    state: Mapping[str, Any] | None = None,
    cors_origins: Sequence[str] | None = None,
) -> FastAPI:
    """构造服务 FastAPI 实例;state 用于测试注入(token_verifier 等)。"""
    app = FastAPI(
        title=name,
        version=version,
        lifespan=lifespan,
        # 服务端 span 用 FastAPI 原生 telemetry;探针与指标端点不采样。
        telemetry={"exclude": exclude_telemetry},
    )
    app.state.settings = settings
    for key, value in (state or {}).items():
        setattr(app.state, key, value)

    install_app_error_handler(app)
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    # 最后注册 = 最外层:request_id/traceparent/访问日志要覆盖其余中间件。
    app.add_middleware(RequestContextMiddleware, settings=settings)
    for router, prefix in routers:
        app.include_router(router, prefix=prefix)
    return app
