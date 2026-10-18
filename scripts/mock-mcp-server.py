"""本地 MCP demo server(M1):Streamable HTTP JSON-RPC,零外部依赖。

用法:
    uv run python scripts/mock-mcp-server.py
    # 默认 http://127.0.0.1:8010/mcp
"""

from __future__ import annotations

from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

app = FastAPI(title="mock-mcp-server")

TOOLS = [
    {
        "name": "echo",
        "description": "回显输入文本",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "两个数字相加",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
    },
]


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/mcp")
async def mcp(request: Request) -> Response:
    payload: dict[str, Any] = await request.json()
    if "id" not in payload:  # notification
        return Response(status_code=202)
    method = payload.get("method")
    params = payload.get("params") or {}
    rpc_id = payload.get("id")
    if method == "initialize":
        result: Any = {
            "protocolVersion": params.get("protocolVersion") or "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mock-mcp", "version": "0.1.0"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo":
            text = f"echo: {args.get('text', '')}"
        elif name == "add":
            text = str(float(args.get("a", 0)) + float(args.get("b", 0)))
        else:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {"code": -32601, "message": f"unknown tool: {name}"},
                }
            )
        result = {"content": [{"type": "text", "text": text}]}
    else:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32601, "message": f"unknown method: {method}"},
            }
        )
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8010)
