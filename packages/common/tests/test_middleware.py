"""请求上下文中间件:request_id 贯穿、访问日志分级、SSE 建连/关闭、异常日志。"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx
import structlog
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from common.config import BaseAppSettings
from common.errors import NotFoundError
from common.logging import (
    bind_request_context,
    clear_request_context,
    current_context,
    redact,
    setup_logging,
)
from common.middleware import (
    REQUEST_ID_HEADER,
    TRACEPARENT_HEADER,
    RequestContextMiddleware,
    inject_request_headers,
    install_app_error_handler,
)

TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
GENERATED_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class TestSettings(BaseAppSettings):
    model_config = {**BaseAppSettings.model_config, "env_prefix": "TEST_OBS__"}


def _settings(**overrides: Any) -> TestSettings:
    return TestSettings(service_name="svc", log_level="DEBUG", **overrides)


def _app(settings: TestSettings) -> FastAPI:
    app = FastAPI()
    app.state.settings = settings

    @app.get("/items/{item_id}")
    async def read_item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError("boom")

    @app.get("/missing")
    async def missing() -> dict[str, str]:
        raise NotFoundError("item.not_found", "找不到")

    @app.get("/events")
    async def events() -> StreamingResponse:
        async def frames() -> Any:
            yield b"data: 1\n\n"
            yield b"data: 2\n\n"

        return StreamingResponse(frames(), media_type="text/event-stream")

    install_app_error_handler(app)
    app.add_middleware(RequestContextMiddleware, settings=settings)
    return app


def _entries(logs: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry["event"] == event]


def test_access_log_carries_request_context() -> None:
    settings = _settings()
    setup_logging(settings)
    client = TestClient(_app(settings))
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        response = client.get("/items/42", headers={REQUEST_ID_HEADER: "abc12345"})
    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "abc12345"
    (entry,) = _entries(logs, "http_request")
    assert entry["route"] == "/items/{item_id}"
    assert entry["status"] == 200
    assert entry["method"] == "GET"
    assert entry["request_id"] == "abc12345"
    assert entry["log_level"] == "info"
    assert isinstance(entry["duration_ms"], float)


def test_generates_request_id_when_missing() -> None:
    settings = _settings()
    setup_logging(settings)
    client = TestClient(_app(settings))
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        response = client.get("/items/1")
    generated = response.headers[REQUEST_ID_HEADER]
    assert GENERATED_ID_RE.match(generated)
    (entry,) = _entries(logs, "http_request")
    assert entry["request_id"] == generated


def test_replaces_malformed_request_id() -> None:
    settings = _settings()
    setup_logging(settings)
    client = TestClient(_app(settings))
    response = client.get("/items/1", headers={REQUEST_ID_HEADER: "bad id!"})
    assert response.headers[REQUEST_ID_HEADER] != "bad id!"
    assert GENERATED_ID_RE.match(response.headers[REQUEST_ID_HEADER])


def test_traceparent_trace_id_is_logged() -> None:
    settings = _settings()
    setup_logging(settings)
    client = TestClient(_app(settings))
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        client.get("/items/1", headers={TRACEPARENT_HEADER: TRACEPARENT})
    (entry,) = _entries(logs, "http_request")
    assert entry["trace_id"] == TRACE_ID


def test_unmatched_route_keeps_template_placeholder() -> None:
    settings = _settings()
    setup_logging(settings)
    client = TestClient(_app(settings))
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        response = client.get("/nowhere")
    assert response.status_code == 404
    (entry,) = _entries(logs, "http_request")
    assert entry["route"] == "unmatched"
    assert entry["path"] == "/nowhere"
    assert entry["status"] == 404


def test_probe_paths_are_debug_level() -> None:
    settings = _settings()
    setup_logging(settings)
    client = TestClient(_app(settings))
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        client.get("/healthz")
    (entry,) = _entries(logs, "http_request")
    assert entry["log_level"] == "debug"


def test_slow_request_emits_warning() -> None:
    settings = _settings(slow_request_ms=0)
    setup_logging(settings)
    client = TestClient(_app(settings))
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        client.get("/items/1")
    (slow,) = _entries(logs, "http_request_slow")
    assert slow["log_level"] == "warning"
    assert slow["threshold_ms"] == 0
    assert slow["route"] == "/items/{item_id}"


def test_access_log_can_be_disabled() -> None:
    settings = _settings(access_log_enabled=False)
    setup_logging(settings)
    client = TestClient(_app(settings))
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        client.get("/items/1")
    assert _entries(logs, "http_request") == []


def test_unhandled_exception_logs_once_with_traceback() -> None:
    settings = _settings()
    setup_logging(settings)
    client = TestClient(_app(settings), raise_server_exceptions=False)
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        response = client.get("/boom")
    assert response.status_code == 500
    (failure,) = _entries(logs, "unhandled_exception")
    assert failure["log_level"] == "error"
    assert failure["exc_type"] == "RuntimeError"
    assert failure.get("exc_info")
    assert failure["route"] == "/boom"
    (access,) = _entries(logs, "http_request")
    assert access["status"] == 500
    assert access["log_level"] == "error"


def test_app_error_is_rendered_and_logged_as_warning() -> None:
    settings = _settings()
    setup_logging(settings)
    client = TestClient(_app(settings))
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        response = client.get("/missing")
    assert response.status_code == 404
    assert response.json()["code"] == "item.not_found"
    (app_error,) = _entries(logs, "app_error")
    assert app_error["log_level"] == "warning"
    assert app_error["error_code"] == "item.not_found"
    assert app_error["status"] == 404


def test_sse_logs_open_and_close_with_frame_count() -> None:
    settings = _settings()
    setup_logging(settings)
    client = TestClient(_app(settings))
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        response = client.get("/events")
    assert response.status_code == 200
    (opened,) = _entries(logs, "sse_opened")
    assert opened["route"] == "/events"
    (closed,) = _entries(logs, "sse_closed")
    assert closed["frames"] == 2
    assert closed["disconnected"] is False
    assert _entries(logs, "http_request") == []


def test_request_context_is_cleared_after_response() -> None:
    settings = _settings()
    setup_logging(settings)
    client = TestClient(_app(settings))
    client.get("/items/1")
    context = current_context()
    assert "request_id" not in context
    assert "tenant_id" not in context


def test_inject_request_headers_propagates_request_id() -> None:
    async def run() -> httpx.Request:
        bind_request_context(request_id="rid-12345678")
        request = httpx.Request("GET", "http://svc.local/v1/x")
        await inject_request_headers(request)
        return request

    request = asyncio.run(run())
    assert request.headers[REQUEST_ID_HEADER] == "rid-12345678"
    assert TRACEPARENT_HEADER not in request.headers
    clear_request_context()


def test_redact_masks_secrets() -> None:
    assert redact(None) == ""
    assert redact("short") == "***"
    assert redact("sk-live-abcdefghijklmnop") == "sk-l…op"
