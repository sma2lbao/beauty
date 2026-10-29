"""健康检查探针:挂载在应用根(不在 /api/v1 下),供网关/编排系统探测。

/healthz 只探进程,/readyz 由 service_kit 统一做数据库 SELECT 1 检查
(等价于原依赖 get_session 的连通性探测);OpenAPI 描述沿用历史文案。
"""

from __future__ import annotations

from common.service_kit import create_health_router

health_router = create_health_router(ready_description="就绪探针:数据库可用才算就绪。")
