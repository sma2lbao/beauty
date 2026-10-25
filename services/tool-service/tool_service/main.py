"""tool-service 入口:service-kit 装配 + 沙箱运行时生命周期。"""

from __future__ import annotations

from common.auth import JwtVerifier
from common.service_kit import build_lifespan, create_service_app, install_database
from fastapi import FastAPI

from tool_service.api.health import health_router
from tool_service.api.v1.router import api_router
from tool_service.core.config import Settings
from tool_service.services.execution import ToolRunner
from tool_service.services.sandbox import SandboxProvider, SandboxRuntime


def create_app(
    settings: Settings | None = None,
    token_verifier: JwtVerifier | None = None,
    *,
    sandbox_provider: SandboxProvider | None = None,
) -> FastAPI:
    """构造应用;测试可注入 token_verifier 与假沙箱提供方。"""

    resolved = settings or Settings()

    async def startup(app: FastAPI) -> None:
        await install_database(app)
        runtime: SandboxRuntime | None = None
        provider: SandboxProvider | None = sandbox_provider
        if provider is None:
            runtime = SandboxRuntime(settings=resolved)
            provider = runtime.provider
        app.state.sandbox_runtime = runtime
        app.state.sandbox_provider = provider
        app.state.tool_runner = ToolRunner(settings=resolved, sandbox=provider)

    async def shutdown(app: FastAPI) -> None:
        runtime: SandboxRuntime | None = getattr(app.state, "sandbox_runtime", None)
        if runtime is not None:
            await runtime.aclose()

    lifespan = build_lifespan(startup=startup, shutdown=shutdown)
    return create_service_app(
        name="tool-service",
        settings=resolved,
        lifespan=lifespan,
        routers=[(health_router, ""), (api_router, "/v1")],
        state={"token_verifier": token_verifier},
    )


app = create_app()
