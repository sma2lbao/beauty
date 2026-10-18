"""健康检查:根探针 + 同源 `/api/health`(M0-01 验收点)。"""

from __future__ import annotations

from fastapi import APIRouter

health_router = APIRouter()


@health_router.get("/healthz")
async def healthz() -> dict[str, str]:
    """存活探针:进程活着即 200。"""
    return {"status": "ok"}


@health_router.get("/api/health")
async def api_health() -> dict[str, str]:
    """部署 smoke 用:经 Nginx 同源访问的 BFF 健康检查。"""
    return {"status": "ok", "service": "bff"}
