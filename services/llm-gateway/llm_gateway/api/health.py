"""健康检查探针(挂应用根,供网关/编排系统探测)。"""

from __future__ import annotations

from common.service_kit import create_health_router
from fastapi import HTTPException, Request

from llm_gateway.core.config import Settings


async def _ready(request: Request) -> dict[str, str]:
    """就绪检查:默认模型已配置才算就绪(提供商连通性在调用时校验)。

    就绪响应除 status 外还带 default_model,便于网关侧核对路由目标。
    """
    settings: Settings = request.app.state.settings
    if not settings.default_model:
        raise HTTPException(status_code=503, detail="default_model 未配置")
    return {"default_model": settings.default_model}


health_router = create_health_router(ready_check=_ready)
