"""连接器浏览器 OAuth 同源回调(API 网关)。

第三方授权页只能把浏览器重定向回一个**公开**地址,而浏览器手里这时没有
Authorization 头(token 模式)也没有服务端能用的会话凭证——通用 `/api/{path}`
代理一律要求会话,会把回调直接 401 掉。所以这个回调不走代理:网关原样收下
`?code&state`(以及用户拒绝时的 `?error`),转给 connector-service 的
`/v1/oauth/{provider}/callback`,再把它的 302 Location 透传回浏览器
(connector-service 自己拼 `/connectors?provider=..&status=..`)。

必须注册在通用 `/api/{path}` 代理之前,否则请求会被代理吃掉。
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlencode

import httpx
from common.logging import get_logger
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from api_gateway.core.config import Settings

logger = get_logger(__name__)

router = APIRouter(tags=["connector-oauth"])

#: 兑换 token 是服务间调用,给足时间但不无限等
CALLBACK_TIMEOUT_SECONDS = 15.0

#: provider key 只允许 manifest 命名口径,挡掉路径穿越与奇怪输入
_PROVIDER_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def _error_redirect(settings: Settings, code: str) -> RedirectResponse:
    """回调失败也回前端页面:带契约里的 status/code,前端弹提示并可重试。"""
    query = urlencode({"status": "error", "code": code})
    return RedirectResponse(
        f"{settings.app_origin}/connectors?{query}", status_code=302
    )


@router.get("/api/connector/v1/oauth/{provider}/callback")
async def connector_oauth_callback(provider: str, request: Request) -> RedirectResponse:
    """接收 provider 重定向:转给 connector-service,透传它给的 302 目标。"""
    settings: Settings = request.app.state.settings
    if not _PROVIDER_KEY.fullmatch(provider):
        return _error_redirect(settings, "connector.oauth_failed")
    target = (
        f"{settings.connector_base_url.rstrip('/')}"
        f"/v1/oauth/{quote(provider, safe='')}/callback"
    )
    query = request.url.query
    if query:
        target = f"{target}?{query}"

    client: httpx.AsyncClient = request.app.state.upstream_client
    try:
        response = await client.get(target, timeout=CALLBACK_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        logger.warning(
            "connector oauth callback upstream failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        return _error_redirect(settings, "connector.upstream_unavailable")

    location = response.headers.get("location")
    if response.status_code in _REDIRECT_STATUSES and location:
        return RedirectResponse(location, status_code=302)
    logger.warning(
        "connector oauth callback rejected",
        status=response.status_code,
        provider=provider,
    )
    return _error_redirect(settings, "connector.oauth_failed")
