"""Sentry(P1):默认关闭、4xx 丢弃、请求数据脱敏、上下文 tag、事件可达。"""

from __future__ import annotations

from typing import Any

import sentry_sdk
from sentry_sdk.transport import Transport

from common.config import BaseAppSettings
from common.errors import NotFoundError, UpstreamError
from common.logging import bind_request_context, clear_request_context
from common.sentry import REDACTED, before_send, init_sentry


class SentrySettings(BaseAppSettings):
    model_config = {**BaseAppSettings.model_config, "env_prefix": "TEST_SENTRY__"}


class _CollectingTransport(Transport):
    """把信封留在内存里;不发网络请求(零网络测试)。"""

    def __init__(self, options: Any = None) -> None:
        super().__init__(options)
        self.envelopes: list[Any] = []

    def capture_envelope(self, envelope: Any) -> None:
        self.envelopes.append(envelope)


def _settings(**overrides: Any) -> SentrySettings:
    return SentrySettings(service_name="svc-sentry", log_level="DEBUG", **overrides)


def test_init_sentry_is_noop_without_dsn() -> None:
    assert init_sentry(_settings()) is False


def test_before_send_drops_business_4xx() -> None:
    exc = NotFoundError("item.not_found", "找不到")
    assert before_send({"event_id": "1"}, {"exc_info": (type(exc), exc, None)}) is None


def test_before_send_keeps_5xx_and_scrubs_request() -> None:
    exc = UpstreamError("llm.upstream_error", "上游挂了")
    bind_request_context(request_id="req-1", tenant_id="t-1", trace_id="trace-1")
    try:
        event: dict[str, Any] = {
            "request": {
                "headers": {"Authorization": "Bearer secret", "Accept": "*/*"},
                "data": {"password": "hunter2"},
                "cookies": {"session": "abc"},
                "query_string": "token=abc&page=2",
            },
            "extra": {"password": "hunter2", "note": "ok"},
        }
        sent = before_send(event, {"exc_info": (type(exc), exc, None)})
    finally:
        clear_request_context()

    assert sent is not None
    request = sent["request"]
    assert "data" not in request
    assert "cookies" not in request
    assert request["headers"]["Authorization"] == REDACTED
    assert request["headers"]["Accept"] == "*/*"
    assert request["query_string"] == f"token={REDACTED}&page=2"
    assert sent["extra"]["password"] == REDACTED
    assert sent["extra"]["note"] == "ok"
    assert sent["tags"] == {
        "request_id": "req-1",
        "trace_id": "trace-1",
        "tenant_id": "t-1",
    }


def test_init_sentry_captures_event_with_scrubbed_payload() -> None:
    """真 init 一次:自定义 transport 收事件,确认脱敏与 tag 生效。"""
    transport = _CollectingTransport()
    try:
        assert (
            init_sentry(
                _settings(sentry_dsn="https://public@example.com/1"),
                transport=transport,
            )
            is True
        )
        bind_request_context(request_id="req-cap", tenant_id="t-cap")
        try:
            sentry_sdk.capture_exception(RuntimeError("boom"))
        finally:
            clear_request_context()
        sentry_sdk.get_client().flush(timeout=2)

        events = []
        for envelope in transport.envelopes:
            for item in envelope.items:
                if item.headers.get("type") == "event":
                    events.append(item.payload.json)
        assert events, "没有捕获到事件"
        event = events[0]
        assert event["tags"]["request_id"] == "req-cap"
        assert event["tags"]["tenant_id"] == "t-cap"
        assert "data" not in event.get("request", {})
    finally:
        sentry_sdk.get_client().close(timeout=1)
