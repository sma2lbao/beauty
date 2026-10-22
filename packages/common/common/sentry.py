"""Sentry 错误聚合(可观测 P1,见 docs/observability.md §8)。

- `sentry_dsn` 为空即完全关闭:内网/本地形态默认不初始化,不产生任何网络请求;
- 只上报 5xx 与未捕获异常:`AppError` 的 4xx 是业务分支,不占用错误配额;
- 事件里的请求体、cookie、敏感头一律脱敏(`send_default_pii=False` 兜底);
- `request_id`/`trace_id`/`tenant_id`/`user_id` 以 tag 带过去,排查时
  Sentry → 日志按同一个 id 串(字段来源是请求上下文,见 §5)。
"""

from __future__ import annotations

from typing import Any

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from common.config import BaseAppSettings
from common.errors import AppError
from common.logging import current_context, get_logger

log = get_logger("sentry")

REDACTED = "***"

#: 敏感请求头:值替换为 ***,保留键名便于判断"是否带过凭证"。
_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "proxy-authorization",
        "x-api-key",
        "x-auth-token",
        "x-csrf-token",
    }
)

#: 可能承载凭证的 query/header 字段名(小写)。
_SENSITIVE_FIELDS = frozenset(
    {
        "password",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "client_secret",
        "code",
        "api_key",
        "authorization",
    }
)

#: 进 Sentry tag 的请求上下文字段(都不是高基数值)。
_TAG_FIELDS = ("request_id", "trace_id", "tenant_id", "user_id", "route", "error_code")


def _scrub_headers(headers: Any) -> Any:
    if not isinstance(headers, dict):
        return headers
    return {
        key: (REDACTED if str(key).lower() in _SENSITIVE_HEADERS else value)
        for key, value in headers.items()
    }


def _scrub_request(request: dict[str, Any]) -> None:
    """就地脱敏:Sentry 默认会带 headers/data/cookies/query_string。"""
    request.pop("data", None)  # 请求体可能含正文、口令、文件内容
    request.pop("cookies", None)
    request["headers"] = _scrub_headers(request.get("headers"))
    query = request.get("query_string")
    if isinstance(query, str) and query:
        parts = []
        for piece in query.split("&"):
            key, _, value = piece.partition("=")
            if key.lower() in _SENSITIVE_FIELDS:
                parts.append(f"{key}={REDACTED}")
            else:
                parts.append(piece)
        request["query_string"] = "&".join(parts)


def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """丢弃业务 4xx 错误,脱敏请求数据,并补上请求上下文 tag。"""
    exc_info = hint.get("exc_info")
    if exc_info:
        exc = exc_info[1] if len(exc_info) > 1 else None
        if isinstance(exc, AppError) and exc.status < 500:
            return None

    request = event.get("request")
    if isinstance(request, dict):
        _scrub_request(request)
    extra = event.get("extra")
    if isinstance(extra, dict):
        event["extra"] = {
            key: (REDACTED if str(key).lower() in _SENSITIVE_FIELDS else value)
            for key, value in extra.items()
        }

    context = current_context()
    tags = event.get("tags")
    tags = dict(tags) if isinstance(tags, dict) else {}
    for key in _TAG_FIELDS:
        value = context.get(key)
        if value is not None:
            tags[key] = str(value)
    if tags:
        event["tags"] = tags
    return event


def init_sentry(settings: BaseAppSettings, **options: Any) -> bool:
    """按配置初始化 Sentry;返回是否真正启用(DSN 为空则 no-op)。"""
    if not settings.sentry_dsn:
        log.debug("sentry_disabled")
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.env,
        release=settings.sentry_release,
        server_name=settings.service_name,
        # 数据采集显式收紧(SDK 2.7x 的 data_collection,替代 send_default_pii):
        # 身份/正文字段一律不采,请求头保留键名但值仍由 before_send 脱敏。
        data_collection={
            "user_info": False,
            "cookies": {"mode": "off"},
            "http_bodies": [],
            "url_query_params": {"mode": "off"},
            "stack_frame_variables": False,
        },
        traces_sample_rate=settings.sentry_traces_sample_rate,
        before_send=before_send,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
        **options,
    )
    log.info(
        "sentry_initialized",
        release=settings.sentry_release,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )
    return True
