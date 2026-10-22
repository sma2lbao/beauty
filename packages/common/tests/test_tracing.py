"""分布式追踪(OTel):provider/资源、span 上下文、日志 trace_id、出站透传。

注意 OTel 的全局 provider 一个进程只能 set 一次,所以这里把 provider 建一次
(第一次 `_ensure_tracing()`),后续用例复用;exporter 用内存实现。
"""

from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from structlog.testing import capture_logs

from common import tracing
from common.config import BaseAppSettings
from common.logging import setup_logging
from common.middleware import RequestContextMiddleware

TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")
TRACEPARENT_RE = re.compile(
    r"^00-(?P<trace_id>[0-9a-f]{32})-(?P<span_id>[0-9a-f]{16})-[0-9a-f]{2}$"
)

_EXPORTER = InMemorySpanExporter()
_initialized = False


class TracingSettings(BaseAppSettings):
    model_config = {**BaseAppSettings.model_config, "env_prefix": "TEST_TRACING__"}


def _settings(**overrides: Any) -> TracingSettings:
    # 单测固定全采样:默认 10% 抽样会让根 span 随机不落,断言不稳定。
    return TracingSettings(
        service_name="svc-tracing", trace_sample_ratio=1.0, **overrides
    )


def _ensure_tracing() -> InMemorySpanExporter:
    global _initialized
    if not _initialized:
        assert tracing.init_tracing(_settings(), exporter=_EXPORTER) is True
        _initialized = True
    return _EXPORTER


def _app(settings: TracingSettings) -> FastAPI:
    app = FastAPI(telemetry={"exclude": tracing.exclude_telemetry})
    app.state.settings = settings

    @app.get("/items/{item_id}")
    async def read_item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(RequestContextMiddleware, settings=settings)
    return app


def _entries(logs: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry["event"] == event]


def test_init_tracing_respects_disabled_switch() -> None:
    # 关闭开关时不建 provider(此时进程内还没建,先用一次清理状态)
    assert tracing._provider is None  # noqa: SLF001 - 断言模块状态
    assert tracing.init_tracing(_settings(tracing_enabled=False)) is False
    assert tracing._provider is None  # noqa: SLF001


def test_init_tracing_sets_resource_and_span_context() -> None:
    _ensure_tracing()
    provider = tracing._provider  # noqa: SLF001
    assert provider is not None
    attributes = provider.resource.attributes
    assert attributes["service.name"] == "svc-tracing"
    assert attributes["deployment.environment"] == "dev"

    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("unit") as span:
        context = tracing.current_span_context()
        assert context is not None
        traceparent = tracing.current_traceparent()
        assert traceparent is not None
        match = TRACEPARENT_RE.match(traceparent)
        assert match is not None
        assert match.group("trace_id") == f"{context.trace_id:032x}"
        assert match.group("span_id") == f"{context.span_id:016x}"
        span_id = context.span_id
    assert span.get_span_context().span_id == span_id


def test_request_logs_carry_span_ids_and_creates_span() -> None:
    exporter = _ensure_tracing()
    settings = _settings()
    setup_logging(settings)
    app = _app(settings)
    assert tracing.init_tracing(settings) is True

    exporter.clear()
    client = TestClient(app)
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        response = client.get("/items/42", headers={"x-request-id": "abc12345"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "abc12345"
    (entry,) = _entries(logs, "http_request")
    assert TRACE_ID_RE.match(entry["trace_id"])
    assert SPAN_ID_RE.match(entry["span_id"])
    assert entry["request_id"] == "abc12345"

    spans = exporter.get_finished_spans()
    assert any("/items" in (span.name or "") for span in spans), [
        span.name for span in spans
    ]


def test_probe_paths_are_not_traced() -> None:
    exporter = _ensure_tracing()
    settings = _settings()
    app = _app(settings)
    tracing.init_tracing(settings)

    exporter.clear()
    client = TestClient(app)
    assert client.get("/healthz").status_code == 200
    assert exporter.get_finished_spans() == ()


def test_http_client_injects_current_traceparent() -> None:
    # httpx 官方插桩挂在 HTTPTransport 上(MockTransport 不经过它),
    # 所以这里起一个本地回环 server 收头,验证真实的出站注入。
    _ensure_tracing()
    captured: list[dict[str, str]] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server 约定
            captured.append({key.lower(): value for key, value in self.headers.items()})
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    provider = tracing._provider  # noqa: SLF001
    assert provider is not None
    tracer = provider.get_tracer("test")
    try:
        with tracer.start_as_current_span("caller") as span:
            context = span.get_span_context()
            with httpx.Client() as client:
                response = client.get(f"http://127.0.0.1:{server.server_port}/v1/ping")
            assert response.status_code == 200
    finally:
        server.shutdown()
        server.server_close()

    assert len(captured) == 1
    match = TRACEPARENT_RE.match(captured[0]["traceparent"])
    assert match is not None
    assert match.group("trace_id") == f"{context.trace_id:032x}"


def test_shutdown_tracing_is_idempotent() -> None:
    _ensure_tracing()
    tracing.shutdown_tracing()
    assert tracing._provider is None  # noqa: SLF001
    tracing.shutdown_tracing()
