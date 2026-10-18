"""公开 MCP Server 端点(M3):Streamable HTTP JSON-RPC + API Key/OAuth 2.1。"""

from __future__ import annotations

import json
from typing import Annotated, Any
from urllib.parse import urlsplit, urlunsplit

from common.errors import AppError, UnauthorizedError
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from agent_orchestrator.services.mcp_server import (
    MCPPrincipal,
    handle_jsonrpc,
)

router = APIRouter(tags=["mcp-server"])


def _auth_header(authorization: str | None, x_api_key: str | None) -> str:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    raise UnauthorizedError("mcp.auth_missing", "缺少 API Key 或 OAuth token")


def _auth_challenge(request: Request) -> str:
    settings = request.app.state.settings
    parsed = urlsplit(settings.mcp_resource_url)
    metadata = urlunsplit(
        (parsed.scheme, parsed.netloc, "/.well-known/oauth-protected-resource", "", "")
    )
    return f'Bearer resource_metadata="{metadata}"'


async def _authenticate(request: Request, key: str) -> MCPPrincipal:
    if key.startswith("bm_live_"):
        verifier = getattr(request.app.state, "api_key_verifier", None)
        if verifier is None:
            raise UnauthorizedError("mcp.auth_unavailable", "API Key 鉴权未配置")
        return await verifier.verify(key)
    introspector = getattr(request.app.state, "oauth_introspector", None)
    if introspector is None:
        raise UnauthorizedError("mcp.auth_unavailable", "OAuth 鉴权未配置")
    principal = await introspector.introspect(key)
    if principal is None:
        raise UnauthorizedError("mcp.unauthorized", "OAuth token 无效或已过期")
    return principal


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


@router.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata(request: Request) -> dict[str, Any]:
    """RFC 9728:MCP 客户端据此发现授权服务器。"""
    settings = request.app.state.settings
    return {
        "resource": settings.mcp_resource_url,
        "authorization_servers": [settings.auth_issuer],
        "scopes_supported": ["mcp:tools:read", "mcp:tools:call", "artifacts:read"],
        "bearer_methods_supported": ["header"],
    }


@router.post("/mcp")
async def mcp_endpoint(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Response:
    try:
        key = _auth_header(authorization, x_api_key)
        principal = await _authenticate(request, key)
    except (UnauthorizedError, AppError):
        return JSONResponse(
            {"error": "unauthorized", "error_description": "认证失败"},
            status_code=401,
            headers={"WWW-Authenticate": _auth_challenge(request)},
        )

    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse(_rpc_error(None, -32700, "请求不是合法 JSON"))
    if not isinstance(payload, dict):
        return JSONResponse(_rpc_error(None, -32600, "请求必须是 JSON-RPC 对象"))
    if "id" not in payload:
        return Response(status_code=202)

    request_id = payload.get("id")
    try:
        result = await handle_jsonrpc(request, principal, payload)
    except AppError as exc:
        return JSONResponse(
            _rpc_error(request_id, -32000, exc.message), status_code=200
        )
    body = {"jsonrpc": "2.0", "id": request_id, "result": result}
    if "text/event-stream" in request.headers.get("accept", ""):
        return StreamingResponse(
            iter([f"data: {json.dumps(body, ensure_ascii=False)}\n\n"]),
            media_type="text/event-stream",
        )
    return JSONResponse(body)
