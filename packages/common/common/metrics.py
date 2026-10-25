"""指标(可观测 P2,见 docs/observability.md §9)。

指标先按"低基数标签"设计:

- 全部指标只带 `service`/`method`/`route`/`status` 这类有限取值;
  **禁止** `tenant_id`、`run_id`、原始 path(404 扫描会把基数打爆);
- 暴露在 `/internal/metrics`(Prometheus 文本格式),不进 `/api/v1` 公共契约,
  部署侧不要把该路径暴露到公网;
- 请求级 RED 指标由 `common.middleware` 自动记录;业务指标(run/job/模型)
  **归属于各自服务**(agent-orchestrator / llm-gateway 的 `metrics` 模块),
  本模块不再定义域指标——避免所有服务同进程重复注册同名 Counter。
"""

from __future__ import annotations

import hmac
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.requests import Request
from starlette.responses import Response

from common.config import BaseAppSettings
from common.logging import current_context

#: 指标端点路径(探针级别,日志降为 debug)。
METRICS_PATH = "/internal/metrics"

_DURATION_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

http_requests_total = Counter(
    "http_requests_total",
    "HTTP 请求数(按服务/方法/路由模板/状态码)",
    ("service", "method", "route", "status"),
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP 请求耗时(秒,到响应头为止)",
    ("service", "route"),
    buckets=_DURATION_BUCKETS,
)
_default_service: str = "unknown"


def configure(*, service: str) -> None:
    """登记本进程的服务名(装配时调用一次,后台任务也能打对标签)。"""
    global _default_service
    _default_service = service


def service_label(service: str | None = None) -> str:
    """服务名标签:显式值 > 日志上下文 > 装配时登记的默认值。

    供各服务的业务指标模块复用(域指标已迁出 common,见 W4-R-40)。
    """
    if service:
        return service
    return str(current_context().get("service") or _default_service)


def observe_http(
    *,
    service: str | None = None,
    method: str,
    route: str,
    status: int,
    duration_seconds: float,
) -> None:
    """记录一次 HTTP 请求的 RED 指标。"""
    service = service_label(service)
    http_requests_total.labels(
        service=service, method=method, route=route, status=str(status)
    ).inc()
    http_request_duration_seconds.labels(service=service, route=route).observe(
        max(duration_seconds, 0.0)
    )


def metrics_payload() -> bytes:
    """渲染 Prometheus 文本格式(测试与端点共用)。"""
    return generate_latest()


def _authorized(request: Request, token: str) -> bool:
    """可选令牌校验:优先 `X-Metrics-Token`,兼容 `Authorization: Bearer`。"""
    supplied = request.headers.get("x-metrics-token")
    if not supplied:
        authorization = request.headers.get("authorization", "")
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            supplied = value.strip()
    return bool(supplied) and hmac.compare_digest(supplied, token)


def install_metrics_endpoint(app: Any, settings: BaseAppSettings) -> bool:
    """挂载 `/internal/metrics`;`metrics_enabled=false` 时不注册。

    配了 `metrics_token` 时要求同值令牌,不符按 404 处理(不暴露端点存在性)。
    """
    if not settings.metrics_enabled:
        return False
    token = settings.metrics_token

    @app.get(METRICS_PATH, include_in_schema=False)
    async def metrics(request: Request) -> Response:
        if token and not _authorized(request, token):
            return Response(
                content="Not Found", status_code=404, media_type="text/plain"
            )
        return Response(content=metrics_payload(), media_type=CONTENT_TYPE_LATEST)

    return True
