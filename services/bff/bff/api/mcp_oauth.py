"""MCP 浏览器 OAuth 回调(BFF 同源)。

第三方授权服务器只能把浏览器重定向回一个公开回调地址,而浏览器手里没有
服务间凭证:所以 BFF 收下 `?code&state`,用 X-Internal-Secret 转给
orchestrator 兑换 token(落库、加密),再把浏览器 302 回前端 /mcp 页。

必须注册在通用 `/api/{path}` 代理之前,否则请求会被代理吃掉。
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
from common.logging import get_logger
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from bff.core.config import Settings

logger = get_logger(__name__)

router = APIRouter(tags=["mcp-oauth"])

#: 兑换 token 是服务间调用,给足时间但不无限等
CALLBACK_TIMEOUT_SECONDS = 15.0


def _error_redirect(settings: Settings, message: str) -> RedirectResponse:
    query = urlencode({"oauth": "error", "error": message[:300]})
    return RedirectResponse(f"{settings.app_origin}/mcp?{query}", status_code=302)


@router.get("/api/mcp/oauth/callback")
async def mcp_oauth_callback(
    request: Request,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    """接收授权服务器重定向:成功则换 token 并回前端 /mcp,失败也回前端带原因。"""
    settings: Settings = request.app.state.settings
    if error:
        return _error_redirect(settings, error_description or error)
    if not state or not code:
        return _error_redirect(settings, "授权回调缺少 code 或 state")
    if not settings.internal_secret:
        return _error_redirect(settings, "BFF 未配置内部调用凭证,无法完成授权")

    client: httpx.AsyncClient = request.app.state.upstream_client
    try:
        response = await client.post(
            f"{settings.runtime_base_url}/internal/mcp/oauth/callback",
            json={"state": state, "code": code},
            headers={"X-Internal-Secret": settings.internal_secret},
            timeout=CALLBACK_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "mcp oauth callback upstream failed", error=f"{type(exc).__name__}: {exc}"
        )
        return _error_redirect(settings, "授权服务暂时不可用,请稍后重试")
    if response.status_code != 200:
        logger.warning("mcp oauth callback rejected", status=response.status_code)
        return _error_redirect(settings, "授权校验失败,请重新发起授权")
    try:
        redirect_path = str(response.json().get("redirect_path") or "/mcp?oauth=error")
    except ValueError:
        return _error_redirect(settings, "授权服务返回格式异常")
    return RedirectResponse(f"{settings.app_origin}{redirect_path}", status_code=302)
