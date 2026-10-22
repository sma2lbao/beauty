"""可观测装配入口(P1 Sentry + P2 指标与追踪,见 docs/observability.md)。

每个服务的 `lifespan` 在 `setup_logging` 之后调用一次:

- Sentry:DSN 为空则完全关闭,不产生网络请求;
- 指标:挂载 `/internal/metrics`(`metrics_enabled=false` 时跳过);
- 追踪:装配 OTel(FastAPI/httpx 官方插桩),没配 `OTLP_ENDPOINT` 时不挂
  导出器(零网络出口),span 只用于日志 `trace_id` 串联与本地排查。
"""

from __future__ import annotations

from typing import Any

from common import metrics
from common.config import BaseAppSettings
from common.metrics import install_metrics_endpoint
from common.sentry import init_sentry
from common.tracing import init_tracing


def install_observability(app: Any, settings: BaseAppSettings) -> None:
    """初始化错误聚合、指标端点与追踪(幂等,单进程只应调用一次)。"""
    metrics.configure(service=settings.service_name)
    init_sentry(settings)
    install_metrics_endpoint(app, settings)
    init_tracing(settings)
