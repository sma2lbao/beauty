"""服务间内部调用鉴权(X-Internal-Secret)依赖。

共享密钥从 request.app.state.settings 读取(字段名可配),不耦合各服务
Settings 类型;未配置密钥时一律拒绝(关闭面),比较用
secrets.compare_digest 保持常量时间。各服务只需在端点参数声明本依赖,
不得再手写密钥比较。
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Header, Request

from common.errors import UnauthorizedError

SECRET_HEADER = "X-Internal-Secret"
#: 内部调用的租户头(服务间按租户取资源,如 llm-gateway 的 BYOK 解析)
TENANT_HEADER = "X-Tenant-Id"


def make_internal_secret_dependency(
    *,
    settings_attr: str = "internal_secret",
    error_code: str = "auth.internal_forbidden",
    error_message: str = "内部调用未开放",
) -> Callable[..., Awaitable[None]]:
    """生成内部调用鉴权依赖;Settings 字段名、错误码与文案按服务注入。"""

    async def require_internal_secret(
        request: Request,
        x_internal_secret: Annotated[
            str | None, Header(alias=SECRET_HEADER)
        ] = None,
    ) -> None:
        expected = getattr(request.app.state.settings, settings_attr, "")
        if (
            not expected
            or not x_internal_secret
            or not secrets.compare_digest(x_internal_secret, expected)
        ):
            raise UnauthorizedError(error_code, error_message)

    return require_internal_secret


require_internal_secret = make_internal_secret_dependency()
