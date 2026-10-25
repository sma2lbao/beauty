"""MCP 工具运行时:客户端构建、probe/调用执行与工具 handler 装配。

注册表 CRUD 与 test 端点留在 services/mcp.py;这里只放"怎么连、怎么调、
怎么把结果回喂模型",供 mcp.py 与上层复用(纯机制,不碰事务与 HTTP 路由)。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from common.errors import AppError
from common.logging import get_logger

from agent_orchestrator.models.mcp import MCPServer
from agent_orchestrator.schemas.mcp import (
    MCPTestRead,
    MCPToolRead,
)
from agent_orchestrator.services.mcp_client import (
    MCPAuthRequired,
    MCPClient,
)
from agent_orchestrator.services.mcp_crypto import decrypt_token, encrypt_token
from agent_orchestrator.services.mcp_stdio import StdioSandboxPool
from agent_orchestrator.services.tool_context import current_tool_context

logger = get_logger(__name__)


TRANSPORT_HTTP = "http"


TRANSPORT_STDIO = "stdio"

#: 调用前解析 OAuth token 的回调:第二参为 True 表示强制刷新(401 后重试)


TokenProvider = Callable[[str, bool], Awaitable[str | None]]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_") or "server"


def _wrap_tool(tool: dict[str, Any]) -> MCPToolRead:
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    return MCPToolRead(
        name=str(tool.get("name") or ""),
        description=str(tool.get("description") or ""),
        input_schema=schema if isinstance(schema, dict) else {},
    )


def _encrypt_env(secret: str, env: dict[str, str]) -> str | None:
    """env 整体加密成一段密文(空 env 不落库,读端自然 has_env=False)。"""
    if not env:
        return None
    return encrypt_token(secret, json.dumps(env, ensure_ascii=False))


def _decrypt_env(server: MCPServer, secret: str) -> dict[str, str]:
    if not server.encrypted_env:
        return {}
    try:
        value = json.loads(decrypt_token(secret, server.encrypted_env))
    except (AppError, ValueError) as exc:
        raise AppError("mcp.env_unreadable", "MCP env 无法解密") from exc
    if not isinstance(value, dict):
        raise AppError("mcp.env_unreadable", "MCP env 不是 JSON 对象")
    return {str(key): str(item) for key, item in value.items()}


def _env_keys(server: MCPServer, secret: str) -> list[str]:
    """读端只暴露 key 名;解密失败(密钥轮换)降级为空,不泄漏也不 500。"""
    try:
        return sorted(_decrypt_env(server, secret))
    except AppError:
        return []


def build_client(
    server: MCPServer,
    secret: str,
    *,
    token: str | None = None,
    transport=None,
) -> MCPClient:
    """建客户端:显式 token(OAuth 授权行)优先,否则解静态 encrypted_token。"""
    if token is None and server.encrypted_token:
        token = decrypt_token(secret, server.encrypted_token)
    return MCPClient(
        server.url,
        token=token,
        timeout_seconds=server.timeout_seconds,
        transport=transport,
    )


def _cache_tools(server: MCPServer, tools: list[MCPToolRead]) -> None:
    server.tools_cache = [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_schema,
        }
        for tool in tools
    ]


def _stdio_request(
    server: MCPServer,
    secret: str,
    *,
    action: str,
    tool: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """probe 的输入契约:{command, args, env, timeout, action, tool?, arguments?}。"""
    request: dict[str, Any] = {
        "command": server.command or "",
        "args": list(server.args or []),
        "env": _decrypt_env(server, secret),
        "timeout": int(server.timeout_seconds or 10),
        "action": action,
    }
    if tool is not None:
        request["tool"] = tool
        request["arguments"] = arguments or {}
    return request


async def _test_stdio_server(
    tenant_id: str,
    server: MCPServer,
    secret: str,
    pool: StdioSandboxPool | None,
    now: datetime,
) -> MCPTestRead:
    """临时沙箱上跑一次 tools/list:成功刷新 tools_cache,失败只记 last_error。"""
    if pool is None:
        detail = "stdio MCP 需要启用沙箱(mcp_stdio_enabled + sandbox_enabled)"
        server.last_status = "error"
        server.last_error = detail
        server.last_checked_at = now
        return MCPTestRead(ok=False, detail=detail, tools=[])
    key = f"test:{server.id}:{uuid.uuid4().hex}"
    try:
        result = await pool.run(
            tenant_id,
            key,
            server,
            _stdio_request(server, secret, action="tools/list"),
        )
        raw_tools = result.get("tools") if isinstance(result, dict) else None
        tools = [_wrap_tool(tool) for tool in raw_tools or [] if isinstance(tool, dict)]
        _cache_tools(server, tools)
        server.last_status = "ok"
        server.last_error = None
        server.last_checked_at = now
        return MCPTestRead(
            ok=True, detail=f"连接成功，发现 {len(tools)} 个工具", tools=tools
        )
    except Exception as exc:  # noqa: BLE001 - 测试端点必须返回可读失败
        server.last_status = "error"
        server.last_error = f"{type(exc).__name__}: {exc}"[:500]
        server.last_checked_at = now
        return MCPTestRead(ok=False, detail=server.last_error, tools=[])
    finally:
        await pool.release(tenant_id, key)


async def _provider_token(
    provider: TokenProvider | None, server_id: str, *, force: bool
) -> str | None:
    """解析 OAuth token;任何失败都按"无 token"处理(让调用走到 401 语义)。"""
    if provider is None:
        return None
    try:
        return await provider(server_id, force)
    except Exception as exc:  # noqa: BLE001 - token 解析失败不阻断调用
        logger.warning(
            "mcp oauth token 解析失败",
            server_id=server_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return None


def _client_with_token(
    server: MCPServer,
    secret: str,
    token: str | None,
    transport=None,
) -> MCPClient:
    """token 为 None 时走 build_client(server, secret) 的静态 token 路径。"""
    if token is not None:
        return build_client(server, secret, token=token, transport=transport)
    if transport is not None:
        return build_client(server, secret, transport=transport)
    return build_client(server, secret)


async def _call_tool_once(
    server: MCPServer,
    secret: str,
    token: str | None,
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    client = _client_with_token(server, secret, token)
    try:
        await client.initialize()
        return await client.call_tool(tool_name, arguments)
    finally:
        await client.aclose()


def _allowed(server: MCPServer, tool_name: str) -> bool:
    allowlist = list(server.tools_allowlist or [])
    return not allowlist or tool_name in allowlist


def _content_text(result: dict[str, Any]) -> str:
    """tools/call 的 result → 回喂文本(与 MCPClient.call_tool 同口径)。"""
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


def _make_http_handler(
    server: MCPServer,
    tool_name: str,
    secret: str,
    on_auth_required: Callable[[str, str], Awaitable[None]] | None = None,
    token_provider: TokenProvider | None = None,
):
    async def handler(args: dict[str, Any]) -> str:
        token = await _provider_token(token_provider, server.id, force=False)
        try:
            return await _call_tool_once(server, secret, token, tool_name, args)
        except MCPAuthRequired as first:
            # 401:若是浏览器授权 server,强制刷新一次 token 再重试(只重试一次)
            detail = str(first)
            refreshed = (
                await _provider_token(token_provider, server.id, force=True)
                if token_provider is not None
                else None
            )
            if refreshed is not None and refreshed != token:
                try:
                    return await _call_tool_once(
                        server, secret, refreshed, tool_name, args
                    )
                except MCPAuthRequired as again:
                    detail = str(again)
                except Exception as error:  # noqa: BLE001 - 工具失败回喂错误文本
                    return json.dumps(
                        {"error": f"{type(error).__name__}: {error}"},
                        ensure_ascii=False,
                    )
            # 刷新不可用/仍 401:结构化回喂模型(不执行工具),并标记需要授权
            await _report_auth_required(on_auth_required, server.id, detail)
            return json.dumps(
                {
                    "error": "该 MCP server 需要重新授权，工具未执行",
                    "code": "mcp.auth_required",
                    "detail": detail,
                    "hint": "请重新授权或更新该 server 的访问令牌后重试",
                },
                ensure_ascii=False,
            )
        except Exception as exc:  # noqa: BLE001 - 工具失败回喂错误文本
            return json.dumps(
                {"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False
            )

    return handler


async def _report_auth_required(
    callback: Callable[[str, str], Awaitable[None]] | None,
    server_id: str,
    detail: str,
) -> None:
    """调用期 401 上报给上层落 last_status;标记失败绝不影响工具返回。"""
    if callback is None:
        return
    try:
        await callback(server_id, detail)
    except Exception as exc:  # noqa: BLE001 - 状态标记是尽力而为
        logger.warning(
            "mcp auth_required 状态写库失败",
            server_id=server_id,
            error=f"{type(exc).__name__}: {exc}",
        )


def _make_stdio_handler(
    tenant_id: str,
    server: MCPServer,
    tool_name: str,
    secret: str,
    pool: StdioSandboxPool | None,
):
    """stdio 工具 handler:按调用上下文(对话/run)选沙箱,失败回喂错误文本。"""

    async def handler(args: dict[str, Any]) -> str:
        if pool is None:  # 防御:tool_specs 已过滤,理论上到不了这里
            return json.dumps({"error": "stdio MCP 需要启用沙箱"}, ensure_ascii=False)
        context = current_tool_context()
        scope = context.sandbox_scope if context is not None else "anonymous"
        key = f"{scope}:{server.id}"
        try:
            result = await pool.run(
                tenant_id,
                key,
                server,
                _stdio_request(
                    server,
                    secret,
                    action="tools/call",
                    tool=tool_name,
                    arguments=args,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - 工具失败回喂错误文本
            return json.dumps(
                {"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False
            )
        return _content_text(result)

    return handler
