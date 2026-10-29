"""健康探针路由工厂(存活/就绪分离,响应契约与历史一致)。

默认就绪检查复用 database.session.session_scope 执行 SELECT 1;自定义检查
抛任何异常都统一映射为 503(默认 detail 保持 "database unavailable",
已有服务契约不变)。自定义检查还可以返回额外字段(如 llm-gateway 的
default_model),它们会并入就绪响应;需要自定义 detail 时直接抛
HTTPException(503),不会被统一改写。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from fastapi import APIRouter, HTTPException, Request

ReadyCheck = Callable[[Request], Awaitable[Mapping[str, str] | None]]
"""就绪检查:无返回值/返回 None 表示就绪;返回映射则并入就绪响应字段。"""


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
    ready_description: str | None = None,
) -> APIRouter:
    """生成健康探针路由:/healthz 只探进程,/readyz 探依赖。

    ready_description 用于覆盖 OpenAPI 里就绪探针的描述:已有服务的
    OpenAPI 契约快照里写的是各自原文案,收敛到 kit 后必须原样保留。
    """
    check = ready_check or _check_database
    router = APIRouter()

    @router.get(health_path)
    async def healthz() -> dict[str, str]:
        """存活探针:进程活着即 200,不碰任何依赖。"""
        return {"status": "ok"}

    @router.get(ready_path, description=ready_description)
    async def readyz(request: Request) -> dict[str, str]:
        """就绪探针:依赖可用才算就绪。"""
        try:
            extra = await check(request)
        except HTTPException:
            # 检查方自己声明了 503 明细(如 BFF 的配置缺失/上游不可达)。
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail=failure_detail) from exc
        payload = {"status": "ready"}
        if extra:
            payload.update(extra)
        return payload

    return router
