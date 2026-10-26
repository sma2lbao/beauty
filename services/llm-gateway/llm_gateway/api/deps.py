"""API 依赖:内部调用鉴权 + 按租户解析 BYOK key。

内部鉴权(与其它服务的 common.security 依赖差异见下):本服务未配置
internal_secret 时**放行**(本地 dev 直接 curl 调试);配置后必须带正确的
X-Internal-Secret,比较走 secrets.compare_digest(常量时间)。
"""

from __future__ import annotations

import secrets
from typing import Annotated

from common.errors import UnauthorizedError
from common.security import SECRET_HEADER
from fastapi import Header, Request

from llm_gateway.services.byok import ByokResolver


async def require_internal_secret(
    request: Request,
    x_internal_secret: Annotated[str | None, Header(alias=SECRET_HEADER)] = None,
) -> None:
    """内部调用鉴权;密钥为空 = 本地 dev 放行,非空 = 常量时间比对。"""
    expected = getattr(request.app.state.settings, "internal_secret", "")
    if not expected:
        return
    if not x_internal_secret or not secrets.compare_digest(x_internal_secret, expected):
        raise UnauthorizedError("auth.internal_forbidden", "内部调用未开放")


async def resolve_byok_api_key(
    request: Request,
    *,
    provider: str,
    tenant_id: str | None,
) -> str | None:
    """按租户解析 BYOK key;未装配/未启用/未命中/解析失败都返回 None。

    None 的语义 = 服务层回退平台 key,不是错误——BYOK 故障不能拖垮模型调用。
    """
    resolver: ByokResolver | None = getattr(request.app.state, "byok_resolver", None)
    if resolver is None:
        return None
    key = await resolver.resolve(tenant_id, provider)
    return key.api_key if key is not None else None
