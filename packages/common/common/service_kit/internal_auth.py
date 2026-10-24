"""内部路由鉴权装配:把 R-25 的 require_internal_secret 包成端点依赖别名。

各服务原本手写 X-Internal-Secret 比较(R-25 已收敛到 common.security);
这里再收敛"如何声明成 FastAPI 依赖":

    InternalAuth = auth_dependencies()

    @router.post("/internal/reindex")
    async def reindex(_: InternalAuth) -> dict[str, str]: ...
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends

from common.security import make_internal_secret_dependency


def auth_dependencies(
    *,
    settings_attr: str = "internal_secret",
    error_code: str = "auth.internal_forbidden",
    error_message: str = "内部调用未开放",
) -> Any:
    """生成可直接用于端点参数的 Annotated[None, Depends(...)] 别名。"""
    dependency = make_internal_secret_dependency(
        settings_attr=settings_attr,
        error_code=error_code,
        error_message=error_message,
    )
    return Annotated[None, Depends(dependency)]
