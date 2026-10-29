"""健康检查探针:挂载在应用根(不在 /v1 下)。

/healthz 只探进程,/readyz 由 service_kit 统一做数据库 SELECT 1 检查。
"""

from __future__ import annotations

from common.service_kit import create_health_router

health_router = create_health_router()
