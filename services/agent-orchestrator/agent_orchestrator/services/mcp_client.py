"""MCP Client:Streamable HTTP JSON-RPC(M1 本地版,不做 stdio)。

安全口径:返回内容视为不可信数据,由工具调用层作为普通文本回喂模型;
token 只在服务端解密后放进请求头,绝不进入日志/响应。
"""

from __future__ import annotations

import json
from contextlib import suppress
from typing import Any

import httpx

PROTOCOL_VERSION = "2025-03-26"


class MCPError(Exception):
    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class MCPClient:
    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = url
        self._token = token
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds, transport=transport
        )
        self._next_id = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        response = await self._client.post(
            self._url, json=payload, headers=self._headers()
        )
        if response.status_code >= 400:
            raise MCPError(
                f"MCP HTTP {response.status_code}: {response.text[:200]}"
            )
        if response.status_code == 202 or not response.content:
            return None
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            for line in response.text.splitlines():
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if not data:
                    continue
                event = json.loads(data)
                if "id" in payload and event.get("id") == payload["id"]:
                    return event
            raise MCPError("MCP SSE 响应中没有匹配的 JSON-RPC 结果")
        return response.json()

    async def _request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
            "params": params or {},
        }
        event = await self._post(payload)
        if event is None:
            raise MCPError(f"MCP {method} 没有返回结果")
        if "error" in event:
            error = event["error"] or {}
            raise MCPError(
                str(error.get("message") or "MCP 调用失败"),
                code=error.get("code"),
            )
        return event.get("result")

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._post(
            {"jsonrpc": "2.0", "method": method, "params": params or {}}
        )

    async def initialize(self) -> dict[str, Any]:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "beauty-moon", "version": "0.1.0"},
            },
        )
        # 通知失败不阻断后续 tools/list(部分 server 可能不实现)
        with suppress(Exception):
            await self._notify("notifications/initialized")
        return result if isinstance(result, dict) else {}

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._request("tools/list", {})
        tools = result.get("tools") if isinstance(result, dict) else None
        return [tool for tool in tools or [] if isinstance(tool, dict)]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = await self._request(
            "tools/call", {"name": name, "arguments": arguments}
        )
        if not isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)
        if result.get("isError"):
            return json.dumps(
                {"error": "MCP 工具返回错误", "content": result.get("content")},
                ensure_ascii=False,
            )
        content = result.get("content")
        if isinstance(content, list):
            texts = [
                str(item.get("text"))
                for item in content
                if isinstance(item, dict) and item.get("text") is not None
            ]
            if texts:
                return "\n".join(texts)
        return json.dumps(result, ensure_ascii=False)
