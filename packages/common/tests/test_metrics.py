"""指标(P2):计数器、中间件 RED 指标、/internal/metrics 端点与开关。"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.config import BaseAppSettings
from common.logging import clear_request_context
from common.metrics import (
    METRICS_PATH,
    configure,
    http_request_duration_seconds,
    http_requests_total,
    install_metrics_endpoint,
    job_failures_total,
    llm_upstream_errors_total,
    metrics_payload,
    observe_http,
    observe_job_failure,
    observe_llm_error,
    observe_run_outcome,
    run_outcomes_total,
)
from common.middleware import RequestContextMiddleware


class MetricsSettings(BaseAppSettings):
    model_config = {**BaseAppSettings.model_config, "env_prefix": "TEST_METRICS__"}


def _settings(**overrides: Any) -> MetricsSettings:
    return MetricsSettings(service_name="svc-metrics", **overrides)


def _app(settings: MetricsSettings) -> FastAPI:
    app = FastAPI()
    app.state.settings = settings

    @app.get("/items/{item_id}")
    async def read_item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    app.add_middleware(RequestContextMiddleware, settings=settings)
    install_metrics_endpoint(app, settings)
    return app


def _value(counter: Any, **labels: str) -> float:
    return counter.labels(**labels)._value.get()  # type: ignore[attr-defined]


def test_observe_http_counts_request_and_duration() -> None:
    before = _value(
        http_requests_total, service="svc-x", method="GET", route="/x", status="200"
    )
    observe_http(
        service="svc-x", method="GET", route="/x", status=200, duration_seconds=0.02
    )
    assert (
        _value(
            http_requests_total,
            service="svc-x",
            method="GET",
            route="/x",
            status="200",
        )
        == before + 1
    )
    assert (
        http_request_duration_seconds.labels(service="svc-x", route="/x")._sum.get()  # type: ignore[attr-defined]
        >= 0.02
    )


def test_middleware_records_red_metrics() -> None:
    client = TestClient(_app(_settings()))
    before = _value(
        http_requests_total,
        service="svc-metrics",
        method="GET",
        route="/items/{item_id}",
        status="200",
    )
    assert client.get("/items/42").status_code == 200
    assert (
        _value(
            http_requests_total,
            service="svc-metrics",
            method="GET",
            route="/items/{item_id}",
            status="200",
        )
        == before + 1
    )


def test_middleware_records_metric_for_unhandled_route() -> None:
    client = TestClient(_app(_settings()))
    before = _value(
        http_requests_total,
        service="svc-metrics",
        method="GET",
        route="unmatched",
        status="404",
    )
    assert client.get("/nope").status_code == 404
    assert (
        _value(
            http_requests_total,
            service="svc-metrics",
            method="GET",
            route="unmatched",
            status="404",
        )
        == before + 1
    )


def test_metrics_endpoint_exposes_prometheus_text() -> None:
    client = TestClient(_app(_settings()))
    response = client.get(METRICS_PATH)
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    assert "http_requests_total" in body
    assert "run_outcomes_total" in body


def test_metrics_endpoint_requires_token_when_configured() -> None:
    """公网入口(BFF)可配令牌:不带/带错按 404,带对才给内容。"""
    client = TestClient(_app(_settings(metrics_token="s3cret-token")))
    assert client.get(METRICS_PATH).status_code == 404
    assert (
        client.get(METRICS_PATH, headers={"X-Metrics-Token": "nope"}).status_code == 404
    )
    ok = client.get(METRICS_PATH, headers={"X-Metrics-Token": "s3cret-token"})
    assert ok.status_code == 200
    bearer = client.get(METRICS_PATH, headers={"Authorization": "Bearer s3cret-token"})
    assert bearer.status_code == 200


def test_metrics_endpoint_can_be_disabled() -> None:
    client = TestClient(_app(_settings(metrics_enabled=False)))
    assert client.get(METRICS_PATH).status_code == 404


def test_domain_counters() -> None:
    observe_run_outcome(status="completed", service="svc-domain")
    observe_job_failure(kind="plan", service="svc-domain")
    observe_llm_error(
        provider="openai", code="llm.upstream_error", service="svc-domain"
    )
    assert _value(run_outcomes_total, service="svc-domain", status="completed") == 1
    assert _value(job_failures_total, service="svc-domain", kind="plan") == 1
    assert (
        _value(
            llm_upstream_errors_total,
            service="svc-domain",
            provider="openai",
            code="llm.upstream_error",
        )
        == 1
    )


def test_service_name_defaults_to_log_context() -> None:
    """业务埋点不传 service 时,用日志上下文里启动时绑定的服务名。"""
    structlog.contextvars.bind_contextvars(service="svc-ctx")
    try:
        observe_job_failure(kind="approve")
    finally:
        clear_request_context()
    assert _value(job_failures_total, service="svc-ctx", kind="approve") == 1


def test_service_name_falls_back_to_configured_default() -> None:
    """后台任务没有请求上下文时,用装配时登记的默认服务名。"""
    structlog.contextvars.clear_contextvars()
    configure(service="svc-configured")
    try:
        observe_job_failure(kind="resume")
        assert _value(job_failures_total, service="svc-configured", kind="resume") == 1
    finally:
        configure(service="unknown")


def test_metrics_payload_is_prometheus_text() -> None:
    payload = metrics_payload()
    assert isinstance(payload, bytes)
    assert b"# TYPE http_requests_total counter" in payload
