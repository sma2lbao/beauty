"""健康探针路由工厂(存活/就绪分离,响应契约与历史一致)。

默认就绪检查复用 database.session.session_scope 执行 SELECT 1;自定义检查
抛任何异常都统一映射为 503(默认 detail 保持 "database unavailable",
已有服务契约不变)。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request

ReadyCheck = Callable[[Request], Awaitable[None]]


async def _check_database(request: Request) -> None:
    """默认就绪检查:数据库能执行 SELECT 1。

    延迟导入 database/sqlalchemy(原因见 lifespan.install_database):
    common 不声明 database 依赖,避免与 database.repository→common.errors 循环。
    """
    from database.session import session_scope
    from sqlalchemy import text

    async with session_scope(request.app.state.session_factory) as session:
        await session.execute(text("SELECT 1"))


def create_health_router(
    *,
    health_path: str = "/healthz",
    ready_path: str = "/readyz",
    ready_check: ReadyCheck | None = None,
    failure_detail: str = "database unavailable",
) -> APIRouter:
    """生成健康探针路由:/healthz 只探进程,/readyz 探依赖。"""
    check = ready_check or _check_database
    router = APIRouter()

    @router.get(health_path)
    async def healthz() -> dict[str, str]:
        """存活探针:进程活着即 200,不碰任何依赖。"""
        return {"status": "ok"}

    @router.get(ready_path)
    async def readyz(request: Request) -> dict[str, str]:
        """就绪探针:依赖可用才算就绪。"""
        try:
            await check(request)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=failure_detail) from exc
        return {"status": "ready"}

    return router
