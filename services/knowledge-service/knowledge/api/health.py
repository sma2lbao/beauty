"""健康检查探针:挂载在应用根(不在 /v1 下),供网关/编排系统探测。

路由与响应体由 common.service_kit 工厂统一生成(存活 /healthz、就绪 /readyz 查 DB)。
"""

from __future__ import annotations

from common.service_kit import create_health_router

health_router = create_health_router()
