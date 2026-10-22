"""可观测装配入口(P1 Sentry + P2 指标,见 docs/observability.md)。

每个服务的 `create_app` 调用一次:

- Sentry:DSN 为空则完全关闭,不产生网络请求;
- 指标:挂载 `/internal/metrics`(`metrics_enabled=false` 时跳过)。
"""

from __future__ import annotations

from typing import Any

from common import metrics
from common.config import BaseAppSettings
from common.metrics import install_metrics_endpoint
from common.sentry import init_sentry


def install_observability(app: Any, settings: BaseAppSettings) -> None:
    """初始化错误聚合与指标端点(幂等,单进程只应调用一次)。"""
    metrics.configure(service=settings.service_name)
    init_sentry(settings)
    install_metrics_endpoint(app, settings)
