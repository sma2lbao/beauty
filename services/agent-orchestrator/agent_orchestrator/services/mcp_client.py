"""MCP Client:Streamable HTTP JSON-RPC(M1 本地版,不做 stdio)。

安全口径:返回内容视为不可信数据,由工具调用层作为普通文本回喂模型;
token 只在服务端解密后放进请求头,绝不进入日志/响应。

传输口径(Streamable HTTP):
- initialize 响应头里的 Mcp-Session-Id 回填到后续每个请求(含 notification);
- initialize 协商出的 protocolVersion 之后放进 MCP-Protocol-Version 头;
- 会话失效(服务端回 404)时清掉本地状态,由 _request 重新 initialize 后重试一次;
- 凭证失效(服务端回 401)抛 MCPAuthRequired:上层据此提示"需要重新授权",
  不再当成普通连接失败(P0;浏览器 OAuth 授权流是 P1);
- 关闭时尽力 DELETE 终止会话,服务端不支持/报错都不影响清理。
"""

from __future__ import annotations

import json
from contextlib import suppress
from typing import Any

import httpx

PROTOCOL_VERSION = "2025-03-26"
SESSION_HEADER = "Mcp-Session-Id"
PROTOCOL_HEADER = "MCP-Protocol-Version"


class MCPError(Exception):
    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class MCPSessionExpired(MCPError):
    """服务端已不认识当前会话(Streamable HTTP:未知 session 回 404)。"""


class MCPAuthRequired(MCPError):
    """服务端要求(重新)授权(HTTP 401)。

    `challenge` 保存 `WWW-Authenticate` 头原文:未来的 P1 要用它定位授权服务器
    (RFC 9728 protected resource metadata),现在只作为排障细节回传。
    """

    def __init__(
        self, message: str, *, code: int | None = 401, challenge: str | None = None
    ) -> None:
        super().__init__(message, code=code)
        self.challenge = challenge


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
        self._client = httpx.AsyncClient(timeout=timeout_seconds, transport=transport)
        self._next_id = 0
        self._session_id: str | None = None
        self._protocol_version: str | None = None

    async def aclose(self) -> None:
        # 规范允许客户端 DELETE 终止会话;部分 server 不支持,失败不报错
        if self._session_id:
            with suppress(Exception):
                await self._client.delete(self._url, headers=self._headers())
            self._session_id = None
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        # 无状态 server 不返回 session,保持现状即可;有 session 的必须逐请求回带
        if self._session_id:
            headers[SESSION_HEADER] = self._session_id
        if self._protocol_version:
            headers[PROTOCOL_HEADER] = self._protocol_version
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        had_session = bool(self._session_id)
        response = await self._client.post(
            self._url, json=payload, headers=self._headers()
        )
        if not self._session_id:
            session_id = response.headers.get(SESSION_HEADER)
            if session_id:
                self._session_id = session_id
        if (
            response.status_code == 404
            and had_session
            and payload.get("method") != "initialize"
        ):
            # 会话被服务端回收(重启/过期):清掉本地状态,交给 _request 重新握手
            self._session_id = None
            self._protocol_version = None
            raise MCPSessionExpired(
                "MCP 会话已失效(HTTP 404)，需要重新 initialize", code=404
            )
        if response.status_code == 401:
            # 401 = 当前凭证不被接受(缺失/过期/scope 不足):区别于普通连接失败,
            # 上层要据此提示"重新授权"而不是"网络/配置错误"。
            challenge = (response.headers.get("www-authenticate") or "").strip()
            detail = challenge or response.text[:200].strip()
            raise MCPAuthRequired(
                "MCP 服务端要求授权(HTTP 401)" + (f": {detail}" if detail else ""),
                challenge=challenge or None,
            )
        if response.status_code >= 400:
            raise MCPError(f"MCP HTTP {response.status_code}: {response.text[:200]}")
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
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        _retry_on_session_expired: bool = True,
    ) -> Any:
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
            "params": params or {},
        }
        try:
            event = await self._post(payload)
        except MCPSessionExpired:
            if not _retry_on_session_expired:
                raise
            await self.initialize()
            return await self._request(method, params, _retry_on_session_expired=False)
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
        await self._post({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def initialize(self) -> dict[str, Any]:
        # 重新握手前清掉旧会话:旧 session id 不能再出现在 initialize 上
        self._session_id = None
        self._protocol_version = None
        result = await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "beauty-moon", "version": "0.1.0"},
            },
            _retry_on_session_expired=False,
        )
        negotiated = result.get("protocolVersion") if isinstance(result, dict) else None
        if isinstance(negotiated, str) and negotiated:
            self._protocol_version = negotiated
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
