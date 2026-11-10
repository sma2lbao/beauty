"""公开 MCP Server 端点(M3):Streamable HTTP JSON-RPC + API Key/OAuth 2.1。"""

from __future__ import annotations

import json
from typing import Annotated, Any
from urllib.parse import urlsplit, urlunsplit

from common.errors import AppError, UnauthorizedError
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from agent_orchestrator.api.deps import get_container
from agent_orchestrator.services.mcp_server import (
    PROTOCOL_VERSION_LEGACY,
    PROTOCOL_VERSION_STATELESS,
    SUPPORTED_PROTOCOL_VERSIONS,
    MCPPrincipal,
    handle_jsonrpc,
)

router = APIRouter(tags=["mcp-server"])

PROTOCOL_HEADER = "MCP-Protocol-Version"


def _resolve_version(raw: str | None) -> str | None:
    """请求头 → 协议栈版本;缺失落 legacy,不支持的版本返回 None。"""
    if raw is None or not raw.strip():
        return PROTOCOL_VERSION_LEGACY
    version = raw.strip()
    return version if version in SUPPORTED_PROTOCOL_VERSIONS else None


def _version_headers(version: str) -> dict[str, str]:
    """无状态栈的响应必须回告版本;旧栈保持原样(不加头)。"""
    if version == PROTOCOL_VERSION_STATELESS:
        return {PROTOCOL_HEADER: PROTOCOL_VERSION_STATELESS}
    return {}


def _auth_header(authorization: str | None, x_api_key: str | None) -> str:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    raise UnauthorizedError("mcp.auth_missing", "缺少 API Key 或 OAuth token")


def _auth_challenge(request: Request) -> str:
    settings = get_container(request).settings
    parsed = urlsplit(settings.mcp_resource_url)
    metadata = urlunsplit(
        (parsed.scheme, parsed.netloc, "/.well-known/oauth-protected-resource", "", "")
    )
    return f'Bearer resource_metadata="{metadata}"'


async def _authenticate(request: Request, key: str) -> MCPPrincipal:
    container = get_container(request)
    if key.startswith("bm_live_"):
        verifier = container.api_key_verifier
        if verifier is None:
            raise UnauthorizedError("mcp.auth_unavailable", "API Key 鉴权未配置")
        return await verifier.verify(key)
    introspector = container.oauth_introspector
    if introspector is None:
        raise UnauthorizedError("mcp.auth_unavailable", "OAuth 鉴权未配置")
    principal = await introspector.introspect(key)
    if principal is None:
        raise UnauthorizedError("mcp.unauthorized", "OAuth token 无效或已过期")
    return principal


def _rpc_error(
    request_id: Any, code: int, message: str, *, data: dict[str, Any] | None = None
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


@router.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata(request: Request) -> dict[str, Any]:
    """RFC 9728:MCP 客户端据此发现授权服务器。"""
    settings = get_container(request).settings
    return {
        "resource": settings.mcp_resource_url,
        "authorization_servers": [settings.auth_issuer],
        "scopes_supported": ["mcp:tools:read", "mcp:tools:call", "artifacts:read"],
        "bearer_methods_supported": ["header"],
    }


def _unauthorized(request: Request, version: str) -> JSONResponse:
    return JSONResponse(
        {"error": "unauthorized", "error_description": "认证失败"},
        status_code=401,
        headers={
            "WWW-Authenticate": _auth_challenge(request),
            **_version_headers(version),
        },
    )


@router.post("/mcp")
async def mcp_endpoint(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Response:
    # 版本分流先于鉴权:未知版本是确定性的协议错误(HTTP 400),不必打授权面
    version = _resolve_version(request.headers.get(PROTOCOL_HEADER))
    if version is None:
        return JSONResponse(
            _rpc_error(
                None,
                -32600,
                "不支持的 MCP 协议版本",
                data={"supported_versions": list(SUPPORTED_PROTOCOL_VERSIONS)},
            ),
            status_code=400,
        )
    headers = _version_headers(version)
    try:
        key = _auth_header(authorization, x_api_key)
        principal = await _authenticate(request, key)
    except (UnauthorizedError, AppError):
        return _unauthorized(request, version)

    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse(
            _rpc_error(None, -32700, "请求不是合法 JSON"), headers=headers
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            _rpc_error(None, -32600, "请求必须是 JSON-RPC 对象"), headers=headers
        )
    if "id" not in payload:
        return Response(status_code=202, headers=headers)

    request_id = payload.get("id")
    try:
        result = await handle_jsonrpc(
            request, principal, payload, protocol_version=version
        )
    except AppError as exc:
        # 无状态栈的未知方法带 -32601(见 MCPMethodNotFoundError);其余维持 -32000
        code = getattr(exc, "rpc_code", -32000)
        return JSONResponse(
            _rpc_error(request_id, code, exc.message), status_code=200, headers=headers
        )
    body = {"jsonrpc": "2.0", "id": request_id, "result": result}
    if "text/event-stream" in request.headers.get("accept", ""):
        return StreamingResponse(
            iter([f"data: {json.dumps(body, ensure_ascii=False)}\n\n"]),
            media_type="text/event-stream",
            headers=headers,
        )
    return JSONResponse(body, headers=headers)


@router.delete("/mcp", include_in_schema=False)
async def mcp_endpoint_delete(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Response:
    """无状态栈:无会话可删,保留幂等语义(204);旧栈维持既有 405。"""
    version = _resolve_version(request.headers.get(PROTOCOL_HEADER))
    if version != PROTOCOL_VERSION_STATELESS:
        return Response(status_code=405)
    try:
        key = _auth_header(authorization, x_api_key)
        await _authenticate(request, key)
    except (UnauthorizedError, AppError):
        return _unauthorized(request, version)
    return Response(status_code=204, headers=_version_headers(version))
