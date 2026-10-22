"""分布式追踪(OTel,见 docs/observability.md §9/§11)。

设计:

- **默认零依赖出口**:没配 `OTLP_ENDPOINT` 时不挂导出器——span 只在进程内生成,
  用于日志里的 `trace_id` 串联与本地排查,不产生任何网络请求;
- 采样:`TRACE_SAMPLE_RATIO`(0~1,默认 0.1)走父级采样,入口决定下游是否采样;
- 服务端埋点用 FastAPI 原生 telemetry(FastAPI ≥ 0.143):`create_app` 里把
  `telemetry={"exclude": exclude_telemetry}` 传给 `FastAPI(...)`,探针与指标
  端点不产生 span;出站埋点与 `traceparent` 注入用官方 httpx 插桩;
- 与日志/中间件的关系:`common.middleware` 从当前 span 取 `trace_id`/`span_id`
  写进日志上下文,并把 span 的 `traceparent` 透传给下游,日志 ↔ 追踪 ↔ Sentry
  三边用同一个 trace id。
"""

from __future__ import annotations

import atexit
from collections.abc import MutableMapping
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased, TraceIdRatioBased
from opentelemetry.trace import SpanContext

from common.config import BaseAppSettings
from common.logging import get_logger
from common.metrics import METRICS_PATH

log = get_logger("tracing")

#: 不产生 span 的路径(探针与指标抓取会高频命中)。
EXCLUDED_PATHS = frozenset({"/healthz", "/readyz", METRICS_PATH})

_provider: TracerProvider | None = None
_instrumented_clients = False


def exclude_telemetry(scope: MutableMapping[str, Any]) -> bool:
    """FastAPI 原生 telemetry 的 `exclude` 回调:健康探针/指标抓取不打 span。"""
    return scope.get("path") in EXCLUDED_PATHS


def current_span_context() -> SpanContext | None:
    """当前 span 的上下文;没有活动 span 或未采样时返回 None。"""
    span = trace.get_current_span()
    context = span.get_span_context()
    if context is None or not context.is_valid:
        return None
    return context


def format_traceparent(context: SpanContext) -> str:
    """按 W3C Trace Context 拼 `traceparent`(采样位跟随 span 的 trace flags)。"""
    trace_id = format(context.trace_id, "032x")
    span_id = format(context.span_id, "016x")
    return f"00-{trace_id}-{span_id}-{int(context.trace_flags):02x}"


def current_traceparent() -> str | None:
    """当前 span 的 traceparent(供出站透传;没有 span 时返回 None)。"""
    context = current_span_context()
    return format_traceparent(context) if context is not None else None


def _build_provider(
    settings: BaseAppSettings, exporter: SpanExporter | None
) -> TracerProvider:
    resource = Resource.create(
        {
            "service.name": settings.service_name,
            "deployment.environment": settings.env,
            **(
                {"service.version": settings.sentry_release}
                if settings.sentry_release
                else {}
            ),
        }
    )
    ratio = settings.trace_sample_ratio
    sampler = ALWAYS_ON if ratio >= 1.0 else ParentBased(TraceIdRatioBased(ratio))
    provider = TracerProvider(resource=resource, sampler=sampler)

    target = exporter
    simple = exporter is not None
    if target is None and settings.otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        target = OTLPSpanExporter(
            endpoint=settings.otlp_endpoint,
            headers=settings.otlp_headers or None,
        )
    if target is not None:
        # 测试注入的 exporter 用同步处理器(便于断言);生产 OTLP 走批量。
        provider.add_span_processor(
            SimpleSpanProcessor(target) if simple else BatchSpanProcessor(target)  # type: ignore[arg-type]
        )
    return provider


def init_tracing(
    settings: BaseAppSettings,
    *,
    exporter: SpanExporter | None = None,
) -> bool:
    """初始化追踪:进程级 provider 只建一次,httpx 插桩幂等。"""
    global _provider, _instrumented_clients

    if not settings.tracing_enabled:
        return False

    if _provider is None:
        _provider = _build_provider(settings, exporter)
        trace.set_tracer_provider(_provider)
        atexit.register(shutdown_tracing)
        log.debug(
            "tracing_initialized",
            otlp=bool(settings.otlp_endpoint),
            sample_ratio=settings.trace_sample_ratio,
        )

    if not _instrumented_clients:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        _instrumented_clients = True

    return True


def shutdown_tracing() -> None:
    """冲刷并关闭 provider(进程退出;测试也可显式调用)。"""
    global _provider
    if _provider is None:
        return
    _provider.shutdown()
    _provider = None
