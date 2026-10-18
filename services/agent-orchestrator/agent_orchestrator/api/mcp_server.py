"""公开 MCP Server 端点(M3 本地版):Streamable HTTP JSON-RPC + API Key。"""

from __future__ import annotations

import json
from typing import Annotated, Any

from common.errors import AppError, UnauthorizedError
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from agent_orchestrator.services.mcp_server import handle_jsonrpc

router = APIRouter(tags=["mcp-server"])


def _auth_header(authorization: str | None, x_api_key: str | None) -> str:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    raise UnauthorizedError("mcp.auth_missing", "缺少 API Key")


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


@router.post("/mcp")
async def mcp_endpoint(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Response:
    verifier = getattr(request.app.state, "api_key_verifier", None)
    if verifier is None:
        raise UnauthorizedError("mcp.auth_unavailable", "公开 MCP 鉴权未配置")
    key = _auth_header(authorization, x_api_key)
    principal = await verifier.verify(key)

    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse(_rpc_error(None, -32700, "请求不是合法 JSON"))
    if not isinstance(payload, dict):
        return JSONResponse(_rpc_error(None, -32600, "请求必须是 JSON-RPC 对象"))
    if "id" not in payload:
        return Response(status_code=202)  # notification

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
