"""HTTP 观测装配(docs/observability.md P0)。

- `RequestContextMiddleware`:纯 ASGI 中间件(不用 BaseHTTPMiddleware,
  避免它把 SSE 的 body 流缓冲起来)。职责:解析/生成 request_id、
  解析 traceparent、绑定日志上下文、回写 X-Request-ID、打访问日志、
  记录 SSE 建连/关闭、未捕获异常带栈打一条、
  请求结束后清理 contextvars。
- `install_app_error_handler`:统一 AppError → JSON 响应,并按 4xx/5xx
  打一条 error_code 日志(5xx 带栈)。替换各服务同构的 handler。
- `inject_request_headers`:httpx 出站事件钩子,把 request_id/traceparent
  透传给下游服务。
- 每个响应结束前顺手记 RED 指标(common.metrics,见 §9),业务指标由各服务自报。
"""

from __future__ import annotations

import asyncio
import contextvars
import re
import time
import uuid
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from common import metrics
from common.config import BaseAppSettings
from common.errors import AppError
from common.logging import bind_request_context, clear_request_context, get_logger
from common.metrics import METRICS_PATH

REQUEST_ID_HEADER = "x-request-id"
TRACEPARENT_HEADER = "traceparent"
SSE_MEDIA_TYPE = "text/event-stream"

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_TRACEPARENT_RE = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace_id>[0-9a-f]{32})-"
    r"(?P<span_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)
_PROBE_PATHS = frozenset({"/healthz", "/readyz", METRICS_PATH})
_ALERT_STATUSES = frozenset({401, 403, 409, 429})
# 出站透传用:当前请求的 traceparent 原文(不进日志上下文,避免噪声)。
_traceparent_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "traceparent", default=None
)


def current_traceparent() -> str | None:
    """当前请求的 traceparent 原文(出站透传用;未携带时为 None)。"""
    return _traceparent_var.get()


def _request_id_from(headers: Headers) -> str | None:
    candidate = (headers.get(REQUEST_ID_HEADER) or "").strip()
    if candidate and _REQUEST_ID_RE.match(candidate):
        return candidate
    return None


def _trace_id_from(traceparent: str | None) -> str | None:
    if not traceparent:
        return None
    match = _TRACEPARENT_RE.match(traceparent.strip().lower())
    return match.group("trace_id") if match else None


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return "unmatched"


class _ResponseState:
    """一次请求内 send 包装需要记住的状态。"""

    __slots__ = (
        "status",
        "route",
        "started",
        "is_sse",
        "frames",
        "completed",
        "sse_logged",
    )

    def __init__(self) -> None:
        self.status = 0
        self.route = ""
        self.started = 0.0
        self.is_sse = False
        self.frames = 0
        self.completed = False
        self.sse_logged = False


class RequestContextMiddleware:
    """请求上下文与访问日志中间件。"""

    def __init__(self, app: ASGIApp, settings: BaseAppSettings) -> None:
        self.app = app
        self.settings = settings
        self.log = get_logger("http")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = _request_id_from(headers) or uuid.uuid4().hex
        traceparent = headers.get(TRACEPARENT_HEADER)
        trace_id = _trace_id_from(traceparent)
        method = scope.get("method", "")
        path = scope.get("path", "")
        began = time.perf_counter()

        bind_request_context(request_id=request_id, method=method, path=path)
        if trace_id:
            bind_request_context(trace_id=trace_id)
        token = _traceparent_var.set(traceparent)
        state = _ResponseState()
        try:

            async def send_wrapper(message: Message) -> None:
                if message["type"] == "http.response.start":
                    await self._on_response_start(
                        message, scope, state, request_id, began
                    )
                elif message["type"] == "http.response.body" and state.is_sse:
                    state.frames += (message.get("body") or b"").count(b"\n\n")
                    if not message.get("more_body", False):
                        state.completed = True
                await send(message)

            await self.app(scope, receive, send_wrapper)
        except asyncio.CancelledError:
            # 客户端断开(SSE 常见):不算错误,交给 finally 记 sse_closed。
            raise
        except Exception as exc:  # noqa: BLE001 - 记录后原样抛出
            route = state.route or _route_template(scope)
            self.log.exception(
                "unhandled_exception",
                status=state.status or 500,
                route=route,
                exc_type=type(exc).__name__,
            )
            # 响应还没开始 => 外层 ServerErrorMiddleware 会兜底 500,
            # 这里补一条访问日志,保证"每请求一行"不缺口。
            if not state.started:
                duration = time.perf_counter() - began
                self._observe(scope, route, 500, duration)
                self._log_access(
                    500,
                    route,
                    scope.get("path", ""),
                    _ms(duration),
                    scope.get("method", ""),
                )
            raise
        finally:
            self._on_request_end(state, began)
            _traceparent_var.reset(token)
            clear_request_context()

    async def _on_response_start(
        self,
        message: Message,
        scope: Scope,
        state: _ResponseState,
        request_id: str,
        began: float,
    ) -> None:
        state.status = int(message["status"])
        state.started = time.perf_counter()
        state.route = _route_template(scope)
        headers = Headers(raw=message.get("headers", []))
        state.is_sse = SSE_MEDIA_TYPE in (headers.get("content-type") or "")
        # 回写 request id:先摘掉应用自己写的同名头,避免重复。
        raw = [
            (key, value)
            for key, value in message.get("headers", [])
            if key.lower() != REQUEST_ID_HEADER.encode()
        ]
        raw.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
        message["headers"] = raw
        if state.route != "unmatched":
            bind_request_context(route=state.route)
        duration = state.started - began
        self._observe(scope, state.route, state.status, duration)
        if state.is_sse:
            self.log.info(
                "sse_opened",
                route=state.route,
                status=state.status,
                duration_ms=_ms(duration),
            )
            return
        self._log_access(
            state.status,
            state.route,
            scope.get("path", ""),
            _ms(duration),
            scope.get("method", ""),
        )
        duration_ms = _ms(duration)
        if (
            duration_ms >= self.settings.slow_request_ms
            and self.settings.access_log_enabled
        ):
            self.log.warning(
                "http_request_slow",
                route=state.route,
                status=state.status,
                duration_ms=duration_ms,
                threshold_ms=self.settings.slow_request_ms,
            )

    def _observe(self, scope: Scope, route: str, status: int, duration: float) -> None:
        metrics.observe_http(
            service=self.settings.service_name,
            method=scope.get("method", ""),
            route=route,
            status=status,
            duration_seconds=duration,
        )

    def _log_access(
        self,
        status: int,
        route: str,
        path: str,
        duration_ms: float,
        method: str = "",
    ) -> None:
        if not self.settings.access_log_enabled:
            return
        fields: dict[str, Any] = {
            "route": route,
            "status": status,
            "duration_ms": duration_ms,
        }
        if route == "unmatched":
            fields["path"] = path
        if status >= 500:
            self.log.error("http_request", **fields)
        elif path in _PROBE_PATHS:
            self.log.debug("http_request", **fields)
        elif status in _ALERT_STATUSES:
            self.log.warning("http_request", **fields)
        else:
            self.log.info("http_request", **fields)

    def _on_request_end(self, state: _ResponseState, began: float) -> None:
        if state.is_sse and not state.sse_logged:
            state.sse_logged = True
            self.log.info(
                "sse_closed",
                route=state.route,
                status=state.status,
                duration_ms=_ms(time.perf_counter() - began),
                frames=state.frames,
                disconnected=not state.completed,
            )


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


def install_app_error_handler(app: FastAPI) -> None:
    """把 AppError 统一渲染成 JSON,并记录 error_code 日志。"""
    log = get_logger("http")

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        bind_request_context(error_code=exc.code)
        if exc.status >= 500:
            log.error(
                "app_error",
                error_code=exc.code,
                status=exc.status,
                exc_info=exc,
            )
        else:
            log.warning("app_error", error_code=exc.code, status=exc.status)
        return JSONResponse(status_code=exc.status, content=exc.to_dict())


async def inject_request_headers(request: httpx.Request) -> None:
    """httpx 出站钩子:透传 request_id 与 traceparent(下游沿用同一条链路)。"""
    from common.logging import current_context

    request_id = current_context().get("request_id")
    if request_id:
        request.headers.setdefault(REQUEST_ID_HEADER, str(request_id))
    traceparent = current_traceparent()
    if traceparent:
        request.headers.setdefault(TRACEPARENT_HEADER, traceparent)
