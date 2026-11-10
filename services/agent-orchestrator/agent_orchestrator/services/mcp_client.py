"""MCP Client:Streamable HTTP JSON-RPC(M1 本地版,不做 stdio)。

安全口径:返回内容视为不可信数据,由工具调用层作为普通文本回喂模型;
token 只在服务端解密后放进请求头,绝不进入日志/响应。**出站地址一律过 SSRF 校验**
(私网/回环/链路本地/元数据地址拒绝,见 services/net_guard.py;确需访问内网
server 时用 MCP_ALLOWED_HOSTS 白名单放行)。

传输口径(Streamable HTTP,双栈见 docs/design/mcp-dual-stack.md):
- 自动协商(protocol_version=None):首个请求前用 `server/discover` 探测
  `2026-07-28`;成功锁无状态栈,失败(400/404/405/JSON-RPC error/空 result/
  连接失败)warning 后回落 legacy;401 仍是 MCPAuthRequired,不算版本不兼容;
- 无状态栈:不发 initialize/notifications/initialized/Mcp-Session-Id,不 DELETE,
  每个请求都带 `MCP-Protocol-Version: 2026-07-28`;
- legacy 栈:initialize 响应头里的 Mcp-Session-Id 回填到后续每个请求(含
  notification);协商出的 protocolVersion 放进 MCP-Protocol-Version 头;
  会话失效(服务端回 404)时清掉本地状态,由 _request 重新 initialize 后重试一次;
- 强制版本(`2025-03-26`/`2026-07-28`)跳过/不回落:强制 stateless 时 discover
  失败直接抛 MCPError,避免把配置错误静默掩盖;
- 凭证失效(服务端回 401)抛 MCPAuthRequired:上层据此提示"需要重新授权",
  不再当成普通连接失败(P0;浏览器 OAuth 授权流是 P1);
- 关闭时尽力 DELETE 终止会话,服务端不支持/报错都不影响清理。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from contextlib import suppress
from typing import Any

import httpx
from common.logging import get_logger

from agent_orchestrator.services import net_guard

logger = get_logger(__name__)

#: 有状态会话栈(默认;不带版本头的老客户端也落这里)
PROTOCOL_VERSION_LEGACY = "2025-03-26"
#: 无状态自包含栈(每请求自带版本头,不建会话)
PROTOCOL_VERSION_STATELESS = "2026-07-28"
#: 兼容旧引用
PROTOCOL_VERSION = PROTOCOL_VERSION_LEGACY
SUPPORTED_PROTOCOL_VERSIONS = (PROTOCOL_VERSION_STATELESS, PROTOCOL_VERSION_LEGACY)
AUTO_PROTOCOL_VERSION = "auto"
STACK_LEGACY = "legacy"
STACK_STATELESS = "stateless"
SESSION_HEADER = "Mcp-Session-Id"
PROTOCOL_HEADER = "MCP-Protocol-Version"


def normalize_protocol_version(value: str | None) -> str | None:
    """配置值 → 客户端协商策略:None/`auto` = 自动协商,其余只接受两个版本。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == AUTO_PROTOCOL_VERSION:
        return None
    if text in SUPPORTED_PROTOCOL_VERSIONS:
        return text
    raise ValueError(
        f"不支持的 MCP 协议版本:{value}(可选 {AUTO_PROTOCOL_VERSION}/"
        f"{PROTOCOL_VERSION_LEGACY}/{PROTOCOL_VERSION_STATELESS})"
    )


#: 出站 SSRF 校验的进程级配置(容器装配时按 settings 覆盖;测试注入 fake resolver)
_ALLOWED_HOSTS: tuple[str, ...] = ()
_RESOLVER: net_guard.Resolver | None = None


def configure_url_guard(
    *,
    allowed_hosts: Sequence[str] = (),
    resolver: net_guard.Resolver | None = None,
) -> None:
    """配置 MCP 出站地址校验(容器装配调用一次;测试可注入 resolver)。"""
    global _ALLOWED_HOSTS, _RESOLVER
    _ALLOWED_HOSTS = net_guard.parse_allowed_hosts(allowed_hosts)
    _RESOLVER = resolver


def reset_url_guard() -> None:
    """恢复默认(空白名单 + 真实 DNS);测试清理用。"""
    configure_url_guard()


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
        protocol_version: str | None = None,
    ) -> None:
        self._url = url
        self._token = token
        self._client = httpx.AsyncClient(timeout=timeout_seconds, transport=transport)
        self._next_id = 0
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        #: None = auto(首个请求前 discover 探测);否则是强制版本
        self._requested_protocol = normalize_protocol_version(protocol_version)
        #: 强制 legacy 不需探测,直接锁栈;stateless/auto 在 _ensure_protocol 里定
        self._stack: str | None = (
            STACK_LEGACY
            if self._requested_protocol == PROTOCOL_VERSION_LEGACY
            else None
        )
        self._url_checked = False
        #: legacy 握手次数:initialize() 与 auto 回落共用 _handshake,避免重复握手
        self._handshakes = 0
        self._last_handshake: dict[str, Any] = {}

    @property
    def _stateless(self) -> bool:
        return self._stack == STACK_STATELESS

    async def aclose(self) -> None:
        # 规范允许客户端 DELETE 终止会话;无状态栈没有会话可删,不发 DELETE
        if self._session_id and not self._stateless:
            with suppress(Exception):
                await self._client.delete(self._url, headers=self._headers())
            self._session_id = None
        await self._client.aclose()

    def _headers(
        self,
        *,
        protocol_version: str | None = None,
        include_session: bool = True,
    ) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        # 无状态栈绝不带 session 头;legacy 有 session 的必须逐请求回带
        if include_session and self._session_id and not self._stateless:
            headers[SESSION_HEADER] = self._session_id
        version = protocol_version or self._protocol_version
        if version:
            headers[PROTOCOL_HEADER] = version
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _use_stateless(self) -> None:
        self._stack = STACK_STATELESS
        self._protocol_version = PROTOCOL_VERSION_STATELESS
        logger.info(
            "mcp.protocol.negotiated",
            protocol_version=PROTOCOL_VERSION_STATELESS,
            url=self._url,
        )

    async def _ensure_protocol(self) -> None:
        """确定协议栈(每个 client 生命周期只探测一次,不重试)。"""
        if self._stack is not None:
            return
        if self._requested_protocol is None:
            if await self._discover(required=False):
                self._use_stateless()
                return
            self._stack = STACK_LEGACY
            # 契约 §4.2:探测失败后回落 legacy initialize(与显式 initialize 同一条路径)
            await self._handshake()
            return
        # 强制 stateless:discover 失败要抛错,不静默降级
        await self._discover(required=True)
        self._use_stateless()

    async def _discover(self, *, required: bool) -> bool:
        """探测 `2026-07-28` 无状态栈;失败按调用方策略回落或抛错。"""
        self._next_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": "server/discover",
            "params": {},
        }
        try:
            event = await self._post(
                payload,
                protocol_version=PROTOCOL_VERSION_STATELESS,
                include_session=False,
            )
            if event is None:
                raise MCPError("MCP server/discover 没有返回结果")
            if "error" in event:
                error = event["error"] or {}
                raise MCPError(
                    str(error.get("message") or "MCP server/discover 返回错误"),
                    code=error.get("code"),
                )
            result = event.get("result")
            if not isinstance(result, dict):
                raise MCPError("MCP server/discover 返回的 result 不是对象")
            declared = result.get("protocolVersions")
            versions = (
                [str(item) for item in declared] if isinstance(declared, list) else []
            )
            single = result.get("protocolVersion")
            if isinstance(single, str) and single:
                versions.append(single)
            if PROTOCOL_VERSION_STATELESS not in versions:
                raise MCPError(
                    "MCP server/discover 未公告 "
                    f"{PROTOCOL_VERSION_STATELESS}:{', '.join(versions) or '空'}"
                )
        except MCPAuthRequired:
            # 401 是授权问题,不是版本不兼容:auto 也绝不回落 legacy
            raise
        except Exception as exc:  # noqa: BLE001 - 连接失败/超时/坏响应都算不兼容
            if required:
                raise MCPError(f"MCP server/discover 失败:{exc}") from exc
            logger.warning(
                "mcp.protocol.discover_failed",
                url=self._url,
                error=f"{type(exc).__name__}: {exc}",
            )
            return False
        return True

    async def _post(
        self,
        payload: dict[str, Any],
        *,
        protocol_version: str | None = None,
        include_session: bool = True,
    ) -> dict[str, Any] | None:
        if not self._url_checked:
            # P0-6:首个请求前做一次 SSRF 校验(探测 discover 也走这里,故仍然覆盖
            # initialize/test/list/call);结果 memo,同一 client 后续请求不重复解析。
            await net_guard.assert_url_allowed(
                self._url, allowed_hosts=_ALLOWED_HOSTS, resolver=_RESOLVER
            )
            self._url_checked = True
        had_session = bool(self._session_id)
        response = await self._client.post(
            self._url,
            json=payload,
            headers=self._headers(
                protocol_version=protocol_version, include_session=include_session
            ),
        )
        # 只有 legacy 栈才采纳服务端签发的会话;无状态栈没有会话语义
        if include_session and not self._session_id and not self._stateless:
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
        # 首个业务请求前确定协议栈:无状态栈不发 initialize,直接自包含请求
        await self._ensure_protocol()
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

    async def _handshake(self) -> dict[str, Any]:
        """legacy 栈的 initialize 握手(会话失效时也由 initialize() 重新走一遍)。"""
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
        self._last_handshake = result if isinstance(result, dict) else {}
        self._handshakes += 1
        return self._last_handshake

    async def initialize(self) -> dict[str, Any]:
        before = self._handshakes
        await self._ensure_protocol()
        if self._stateless:
            # 无状态栈:没有握手,也不发 notifications/initialized
            return {
                "protocolVersion": PROTOCOL_VERSION_STATELESS,
                "capabilities": {},
                "serverInfo": {"name": "beauty-moon", "version": "0.1.0"},
            }
        if self._handshakes != before:
            # auto 回落时 _ensure_protocol 已经握过手,不要重复发 initialize
            return self._last_handshake
        return await self._handshake()

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
