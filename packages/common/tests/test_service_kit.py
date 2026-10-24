"""service-kit(R-22):配置校验、探针契约、应用装配、生命周期顺序、内部鉴权。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import ValidationError

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
    routers: list[Any] | None = None,
    state: dict[str, Any] | None = None,
) -> Any:
    return create_service_app(
        name="svc",
        settings=settings or _settings(),
        lifespan=build_lifespan(startup=startup, shutdown=shutdown),
        routers=routers or [],
        state=state,
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
