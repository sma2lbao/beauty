"""service-kit(R-22):配置校验、探针契约、应用装配、生命周期顺序、内部鉴权。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware

from common.middleware import REQUEST_ID_HEADER
from common.service_kit import (
    ServiceSettings,
    auth_dependencies,
    build_lifespan,
    create_health_router,
    create_service_app,
)


def _settings(**overrides: Any) -> ServiceSettings:
    values: dict[str, Any] = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "auth_enabled": False,
        "tracing_enabled": False,
        "metrics_enabled": False,
        "sentry_dsn": None,
        "log_level": "WARNING",
        "_env_file": None,
    }
    values.update(overrides)
    return ServiceSettings(**values)


class _InternalSettings(ServiceSettings):
    internal_secret: str = "s3cret"


_INTERNAL_AUTH = auth_dependencies()


async def _internal_ping(_: _INTERNAL_AUTH) -> dict[str, str]:
    return {"status": "ok"}


def _build_app(
    settings: ServiceSettings | None = None,
    *,
    startup: Any = None,
    shutdown: Any = None,
    shutdown_before_verifier: Any = None,
    routers: list[Any] | None = None,
    state: dict[str, Any] | None = None,
    middlewares: list[Any] | None = None,
    cors_origins: list[str] | None = None,
) -> Any:
    return create_service_app(
        name="svc",
        settings=settings or _settings(),
        lifespan=build_lifespan(
            startup=startup,
            shutdown=shutdown,
            shutdown_before_verifier=shutdown_before_verifier,
        ),
        routers=routers or [],
        state=state,
        middlewares=middlewares or [],
        cors_origins=cors_origins,
    )


def test_service_settings_defaults_and_overrides() -> None:
    settings = _settings()
    assert settings.auto_create_tables is False
    assert settings.auth_enabled is False  # 显式覆盖,验证字段可改
    assert ServiceSettings.model_fields["auth_issuer"].default == (
        "http://127.0.0.1:8001"
    )
    assert ServiceSettings.model_fields["auth_tenant_claim"].default == "tenant_id"


def test_service_settings_requires_database_url() -> None:
    with pytest.raises(ValidationError):
        ServiceSettings(auth_enabled=False, _env_file=None)


def test_service_settings_validates_auth_fields() -> None:
    with pytest.raises(ValidationError):
        _settings(database_url="   ")
    with pytest.raises(ValidationError):
        _settings(auth_enabled=True, auth_issuer="")
    with pytest.raises(ValidationError):
        _settings(auth_enabled=True, auth_tenant_claim="")


def test_health_router_keeps_response_contract() -> None:
    checked: list[str] = []

    async def ready(request: Any) -> None:
        checked.append(request.url.path)

    router = create_health_router(ready_check=ready)
    app = _build_app(routers=[(router, "")])
    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
        readyz = client.get("/readyz")
        assert readyz.status_code == 200
        assert readyz.json() == {"status": "ready"}
    assert checked == ["/readyz"]


def test_health_router_custom_paths_and_503() -> None:
    async def not_ready(request: Any) -> None:
        raise RuntimeError("dependency down")

    router = create_health_router(
        health_path="/live",
        ready_path="/ready",
        ready_check=not_ready,
        failure_detail="database unavailable",
    )
    app = _build_app(routers=[(router, "")])
    with TestClient(app) as client:
        assert client.get("/live").json() == {"status": "ok"}
        failed = client.get("/ready")
        assert failed.status_code == 503
        assert failed.json() == {"detail": "database unavailable"}


def test_create_service_app_assembles_middlewares_and_routers() -> None:
    router = APIRouter()

    @router.get("/items")
    async def items() -> dict[str, bool]:
        return {"ok": True}

    @router.get("/boom")
    async def boom() -> None:
        from common.errors import UnauthorizedError

        raise UnauthorizedError("auth.invalid", "无效凭证")

    app = create_service_app(
        name="svc",
        settings=_settings(),
        lifespan=build_lifespan(),
        routers=[(router, "/v1")],
        cors_origins=["http://localhost:5173"],
    )
    with TestClient(app) as client:
        ok = client.get("/v1/items")
        assert ok.status_code == 200
        assert ok.headers.get(REQUEST_ID_HEADER)  # 中间件回写 request_id
        denied = client.get("/v1/boom")
        assert denied.status_code == 401
        assert denied.json()["code"] == "auth.invalid"
        preflight = client.options(
            "/v1/items",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers.get("access-control-allow-origin") == (
            "http://localhost:5173"
        )


def test_build_lifespan_runs_hooks_in_order_and_disposes_engine() -> None:
    events: list[str] = []

    class _Engine:
        async def dispose(self) -> None:
            events.append("engine.dispose")

    class _Factory:
        engine = _Engine()

    async def startup(app: Any) -> None:
        events.append("startup")
        app.state.session_factory = _Factory()

    async def shutdown(app: Any) -> None:
        events.append("shutdown")

    app = _build_app(startup=startup, shutdown=shutdown)
    with TestClient(app):
        assert events == ["startup"]
    assert events == ["startup", "shutdown", "engine.dispose"]


def test_build_lifespan_keeps_injected_verifier() -> None:
    class _Verifier:
        async def aclose(self) -> None:
            raise AssertionError("测试注入的校验器不应由 lifespan 关闭")

    verifier = _Verifier()
    app = _build_app(state={"token_verifier": verifier})
    with TestClient(app):
        assert app.state.token_verifier is verifier


def test_auth_dependencies_checks_internal_secret() -> None:
    settings = _InternalSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        auth_enabled=False,
        tracing_enabled=False,
        metrics_enabled=False,
        _env_file=None,
    )
    router = APIRouter()
    router.add_api_route("/internal/ping", _internal_ping, methods=["GET"])

    app = _build_app(settings=settings, routers=[(router, "")])
    with TestClient(app) as client:
        denied = client.get("/internal/ping")
        assert denied.status_code == 401
        assert denied.json()["code"] == "auth.internal_forbidden"
        allowed = client.get("/internal/ping", headers={"X-Internal-Secret": "s3cret"})
        assert allowed.status_code == 200
        assert allowed.json() == {"status": "ok"}


def test_health_router_merges_extra_fields_into_ready_response() -> None:
    """就绪检查可返回额外字段:并入 {"status": "ready"} 而非替换。"""

    async def ready(request: Any) -> dict[str, str]:
        return {"default_model": "demo"}

    router = create_health_router(ready_check=ready)
    app = _build_app(routers=[(router, "")])
    with TestClient(app) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "default_model": "demo"}


def test_health_router_keeps_custom_http_exception_detail() -> None:
    """检查方自己抛的 503 明细原样透传,不被统一 detail 覆盖。"""

    async def not_ready(request: Any) -> None:
        raise HTTPException(status_code=503, detail="default_model 未配置")

    router = create_health_router(ready_check=not_ready)
    app = _build_app(routers=[(router, "")])
    with TestClient(app) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json() == {"detail": "default_model 未配置"}


def test_health_router_ready_description_keeps_openapi_contract() -> None:
    """ready_description 覆盖 OpenAPI 描述:已有服务的契约快照可原样保留。"""

    async def ready(request: Any) -> None:
        return None

    custom = create_health_router(
        ready_check=ready,
        ready_description="就绪探针:数据库可用才算就绪。",
    )
    default = create_health_router(ready_check=ready)
    custom_app = _build_app(routers=[(custom, "")])
    default_app = _build_app(routers=[(default, "")])
    custom_spec = custom_app.openapi()["paths"]["/readyz"]["get"]
    default_spec = default_app.openapi()["paths"]["/readyz"]["get"]
    assert custom_spec["description"] == "就绪探针:数据库可用才算就绪。"
    assert default_spec["description"] == "就绪探针:依赖可用才算就绪。"
    # 路径名/operationId/响应 schema 不受描述覆盖影响。
    assert custom_spec["operationId"] == default_spec["operationId"]
    assert custom_spec["responses"]["200"] == default_spec["responses"]["200"]


class _HeaderMiddleware(BaseHTTPMiddleware):
    """测试用自定义中间件:回写固定响应头。"""

    async def dispatch(self, request: Any, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["X-Service-Middleware"] = "on"
        return response


def test_create_service_app_registers_custom_middlewares() -> None:
    """服务特有中间件按序注册在统一错误处理之后、请求上下文之前。"""

    router = APIRouter()

    @router.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    app = _build_app(
        routers=[(router, "")],
        middlewares=[(_HeaderMiddleware, {})],
    )
    with TestClient(app) as client:
        resp = client.get("/ping")
    assert resp.status_code == 200
    assert resp.headers.get("X-Service-Middleware") == "on"
    # 请求上下文中间件仍是最外层:自定义中间件不影响 request_id 回写。
    assert resp.headers.get(REQUEST_ID_HEADER)


def test_create_service_app_custom_cors_without_credentials() -> None:
    """自定义 CORS 可覆盖默认策略(如不带 credentials 的浏览器直连)。"""

    router = APIRouter()

    @router.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    app = _build_app(
        routers=[(router, "")],
        middlewares=[
            (
                CORSMiddleware,
                {
                    "allow_origins": ["http://localhost:5173"],
                    "allow_methods": ["*"],
                    "allow_headers": ["*"],
                },
            )
        ],
    )
    with TestClient(app) as client:
        preflight = client.options(
            "/ping",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert preflight.status_code == 200
    assert preflight.headers.get("access-control-allow-origin") == (
        "http://localhost:5173"
    )
    assert preflight.headers.get("access-control-allow-credentials") is None


def test_build_lifespan_runs_shutdown_before_verifier_hook() -> None:
    """shutdown_before_verifier 在 verifier/引擎处置之前执行(资源先于连接池)。"""
    events: list[str] = []

    class _Engine:
        async def dispose(self) -> None:
            events.append("engine.dispose")

    class _Factory:
        engine = _Engine()

    async def startup(app: Any) -> None:
        events.append("startup")
        app.state.session_factory = _Factory()

    async def before_verifier(app: Any) -> None:
        events.append("shutdown_before_verifier")

    async def shutdown(app: Any) -> None:
        events.append("shutdown")

    app = _build_app(
        startup=startup,
        shutdown=shutdown,
        shutdown_before_verifier=before_verifier,
    )
    with TestClient(app):
        assert events == ["startup"]
    assert events == [
        "startup",
        "shutdown_before_verifier",
        "shutdown",
        "engine.dispose",
    ]
