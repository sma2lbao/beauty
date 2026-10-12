"""健康检查探针:挂载在应用根(不在 /v1 下)。"""

from __future__ import annotations

from database.session import session_scope
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

health_router = APIRouter()


@health_router.get("/healthz")
async def healthz() -> dict[str, str]:
    """存活探针:进程活着即 200。"""
    return {"status": "ok"}


@health_router.get("/readyz")
async def readyz(request: Request) -> dict[str, str]:
    """就绪探针:数据库可用才算就绪。"""
    try:
        async with session_scope(request.app.state.session_factory) as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ready"}
