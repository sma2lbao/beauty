"""健康检查探针(挂应用根,供网关/编排系统探测)。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from llm_gateway.core.config import Settings

health_router = APIRouter()


@health_router.get("/healthz")
async def healthz() -> dict[str, str]:
    """存活探针:进程活着即 200,不碰任何依赖。"""
    return {"status": "ok"}


@health_router.get("/readyz")
async def readyz(request: Request) -> dict[str, str]:
    """就绪探针:默认模型已配置才算就绪(提供商连通性在调用时校验)。"""
    settings: Settings = request.app.state.settings
    if not settings.default_model:
        raise HTTPException(status_code=503, detail="default_model 未配置")
    return {"status": "ready", "default_model": settings.default_model}
