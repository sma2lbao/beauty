"""健康检查探针:挂载在应用根(不在 /api/v1 下),供网关/编排系统探测。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import get_session

health_router = APIRouter()

DBSession = Annotated[AsyncSession, Depends(get_session)]


@health_router.get("/healthz")
async def healthz() -> dict[str, str]:
    """存活探针:进程活着即 200,不碰任何依赖。"""
    return {"status": "ok"}


@health_router.get("/readyz")
async def readyz(session: DBSession) -> dict[str, str]:
    """就绪探针:数据库可用才算就绪。"""
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ready"}
