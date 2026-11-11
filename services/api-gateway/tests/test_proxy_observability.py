"""代理观测:proxy_call 事件、upstream 指标、归一化 route 贯通访问日志。"""

from __future__ import annotations

from typing import Any

import structlog
from common.metrics import http_upstream_requests_total
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from tests.conftest import login

RUN_ID = "0123456789abcdef0123456789abcdef"
DETAIL_ROUTE = "/api/v1/runs/{id}"
STREAM_ROUTE = "/api/v1/runs/{id}/events/stream"


def _entries(logs: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry["event"] == event]


def _hits(upstream: str, route: str, status: str) -> float:
    return http_upstream_requests_total.labels(
        service="api-gateway", upstream=upstream, route=route, status=status
    )._value.get()  # type: ignore[attr-defined]


def test_proxy_call_carries_upstream_route_and_status(client: TestClient) -> None:
    login(client)
    before = _hits("runtime", DETAIL_ROUTE, "200")
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        response = client.get(f"/api/v1/runs/{RUN_ID}")
    assert response.status_code == 200

    (call,) = _entries(logs, "proxy_call")
    assert call["upstream"] == "runtime"
    assert call["route"] == DETAIL_ROUTE
    assert call["method"] == "GET"
    assert call["status"] == 200
    assert call["stream"] is False
    assert call["duration_ms"] >= 0
    assert _hits("runtime", DETAIL_ROUTE, "200") == before + 1

    # 访问日志的 route/upstream 与 proxy_call 一致(不再是 /api/{path:path})。
    (access,) = _entries(logs, "http_request")
    assert access["route"] == DETAIL_ROUTE
    assert access["upstream"] == "runtime"


def test_proxy_call_marks_stream_and_ttfb(client: TestClient) -> None:
    login(client)
    before = _hits("runtime", STREAM_ROUTE, "200")
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        response = client.get(f"/api/v1/runs/{RUN_ID}/events/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    (call,) = _entries(logs, "proxy_call")
    assert call["stream"] is True
    assert call["route"] == STREAM_ROUTE
    assert _hits("runtime", STREAM_ROUTE, "200") == before + 1

    (closed,) = _entries(logs, "sse_closed")
    assert closed["route"] == STREAM_ROUTE
    assert closed["upstream"] == "runtime"


def test_proxy_call_records_each_401_retry_attempt(client: TestClient) -> None:
    """401 会打两次上游:两次都进指标,proxy_call 也各一条(便于定位续期抖动)。"""
    login(client)
    route = "/api/v1/runs/expired"
    before_401 = _hits("runtime", route, "401")
    before_200 = _hits("runtime", route, "200")
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        response = client.get(route)
    assert response.status_code == 200

    calls = _entries(logs, "proxy_call")
    assert [call["status"] for call in calls] == [401, 200]
    assert _hits("runtime", route, "401") == before_401 + 1
    assert _hits("runtime", route, "200") == before_200 + 1
