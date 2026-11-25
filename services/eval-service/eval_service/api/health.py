"""健康检查探针:挂载在应用根(不在 /v1 下)。"""

from __future__ import annotations

from common.service_kit import create_health_router

health_router = create_health_router()
