"""MCP Client 业务:注册表 CRUD、测试/刷新工具、工具规格生成(M1 + P2 stdio)。

两种传输共用同一张表与同一套读端契约,分支只出现在"怎么连"上:

- http:进程内 httpx(M1 现状,不动);
- stdio:命令在沙箱里拉起(P2),经 StdioSandboxPool 执行 probe。

安全口径:env/token 只存密文,读端只回 has_*/尾号/key 名;stdio 命令必须命中
白名单(能力即权限边界)。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from common.errors import AppError, ConflictError
from common.logging import get_logger
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.core.config import DEFAULT_MCP_STDIO_COMMAND_ALLOWLIST
from agent_orchestrator.models.mcp import MCPServer
from agent_orchestrator.repositories import MCPServerRepository
from agent_orchestrator.schemas.mcp import (
    MCPServerCreate,
    MCPServerRead,
    MCPServerUpdate,
    MCPTestRead,
    MCPToolRead,
)
from agent_orchestrator.services.mcp_client import MCPClient, MCPError
from agent_orchestrator.services.mcp_crypto import decrypt_token, encrypt_token
from agent_orchestrator.services.mcp_stdio import StdioSandboxPool
from agent_orchestrator.services.tool_context import current_tool_context
from agent_orchestrator.services.tools import ToolSpec

logger = get_logger(__name__)

TRANSPORT_HTTP = "http"
TRANSPORT_STDIO = "stdio"


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


def _to_read(server: MCPServer, secret: str) -> MCPServerRead:
    return MCPServerRead(
        id=server.id,
        name=server.name,
        transport=server.transport or TRANSPORT_HTTP,
        url=server.url or "",
        command=server.command,
        args=list(server.args or []),
        runtime=server.runtime or "python",
        image=server.image,
        enabled=server.enabled,
        auto_approve=bool(server.auto_approve),
        has_token=bool(server.encrypted_token),
        token_hint=server.token_hint,
        has_env=bool(server.encrypted_env),
        env_keys=_env_keys(server, secret),
        tools_allowlist=list(server.tools_allowlist or []),
        tools=[_wrap_tool(tool) for tool in server.tools_cache or []],
        timeout_seconds=server.timeout_seconds,
        last_status=server.last_status,
        last_checked_at=server.last_checked_at,
        last_error=server.last_error,
        created_at=server.created_at,
    )


def build_client(
    server: MCPServer,
    secret: str,
    *,
    transport=None,
) -> MCPClient:
    token = (
        decrypt_token(secret, server.encrypted_token)
        if server.encrypted_token
        else None
    )
    return MCPClient(
        server.url,
        token=token,
        timeout_seconds=server.timeout_seconds,
        transport=transport,
    )


def _require_command_allowed(
    command: str | None, allowlist: Sequence[str] | None
) -> None:
    """stdio 命令白名单:只允许已知包管理/解释器入口,挡掉任意宿主机命令。"""
    allowed = sorted(allowlist or DEFAULT_MCP_STDIO_COMMAND_ALLOWLIST)
    name = (command or "").strip()
    if name not in allowed:
        raise ConflictError(
            "mcp.command_denied",
            f"stdio MCP 命令不在白名单:{name or '(空)'};允许:{', '.join(allowed)}",
        )


async def list_servers(
    session: AsyncSession, tenant_id: str, *, secret: str
) -> list[MCPServerRead]:
    servers = await MCPServerRepository(session).list_scoped(
        tenant_id, order_by=[MCPServer.created_at]
    )
    return [_to_read(server, secret) for server in servers]


async def _require(session: AsyncSession, tenant_id: str, server_id: str) -> MCPServer:
    return await MCPServerRepository(session).require_for_tenant(tenant_id, server_id)


async def create_server(
    session: AsyncSession,
    tenant_id: str,
    data: MCPServerCreate,
    *,
    secret: str,
    allowlist: Sequence[str] | None = None,
) -> MCPServerRead:
    repo = MCPServerRepository(session)
    if await repo.find_by_name(tenant_id, data.name) is not None:
        raise ConflictError("mcp.server_exists", "同名 MCP server 已存在")
    stdio = data.transport == TRANSPORT_STDIO
    token = "" if stdio else (data.token or "").strip()
    if stdio:
        _require_command_allowed(data.command, allowlist)
    env = dict(data.env) if stdio else {}
    server = MCPServer(
        tenant_id=tenant_id,
        name=data.name,
        transport=data.transport,
        # url 列非空:stdio 存空字符串(见 models/mcp.py)
        url="" if stdio else (data.url or "").strip(),
        command=data.command if stdio else None,
        args=list(data.args) if stdio else [],
        runtime=data.runtime,
        image=data.image if stdio else None,
        encrypted_token=encrypt_token(secret, token) if token else None,
        token_hint=token[-4:] if token else None,
        encrypted_env=_encrypt_env(secret, env),
        enabled=data.enabled,
        auto_approve=data.auto_approve,
        tools_allowlist=list(data.tools_allowlist),
        tools_cache=[],
        timeout_seconds=data.timeout_seconds,
    )
    await repo.add(server)
    return _to_read(server, secret)


def _decrypt_token_hint(server: MCPServer, secret: str) -> str | None:
    if not server.encrypted_token:
        return None
    try:
        return decrypt_token(secret, server.encrypted_token)
    except AppError:
        return ""


def _merged_payload(
    server: MCPServer, data: MCPServerUpdate, secret: str
) -> dict[str, Any]:
    """现有行 + PATCH 字段 → MCPServerCreate 入参(跨字段约束复用同一套校验)。"""
    payload: dict[str, Any] = {
        "name": server.name,
        "transport": server.transport or TRANSPORT_HTTP,
        "url": server.url or None,
        "token": _decrypt_token_hint(server, secret),
        "command": server.command,
        "args": list(server.args or []),
        "env": _env_keys_for_update(server, secret),
        "runtime": server.runtime or "python",
        "image": server.image,
        "enabled": server.enabled,
        "auto_approve": bool(server.auto_approve),
        "tools_allowlist": list(server.tools_allowlist or []),
        "timeout_seconds": server.timeout_seconds,
    }
    for field in (
        "name",
        "transport",
        "url",
        "token",
        "command",
        "args",
        "env",
        "runtime",
        "image",
        "enabled",
        "auto_approve",
        "tools_allowlist",
        "timeout_seconds",
    ):
        value = getattr(data, field)
        if value is not None:
            payload[field] = value
    return payload


def _env_keys_for_update(server: MCPServer, secret: str) -> dict[str, str]:
    """合并校验只需要"env 是否为空的形状";真值在应用阶段才加密写回。"""
    try:
        return _decrypt_env(server, secret)
    except AppError:
        return {}


def _validation_message(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        text = str(error.get("msg") or "").removeprefix("Value error, ").strip()
        if text:
            parts.append(text)
    return ";".join(parts)[:500] or "MCP 配置不合法"


async def update_server(
    session: AsyncSession,
    tenant_id: str,
    server_id: str,
    data: MCPServerUpdate,
    *,
    secret: str,
    allowlist: Sequence[str] | None = None,
) -> MCPServerRead:
    server = await _require(session, tenant_id, server_id)
    try:
        merged = MCPServerCreate.model_validate(_merged_payload(server, data, secret))
    except ValidationError as exc:
        raise AppError("mcp.invalid_config", _validation_message(exc)) from exc
    if data.name is not None:
        duplicate = await MCPServerRepository(session).find_by_name(
            tenant_id, data.name
        )
        if duplicate is not None and duplicate.id != server.id:
            raise ConflictError("mcp.server_exists", "同名 MCP server 已存在")
        server.name = data.name
    stdio = merged.transport == TRANSPORT_STDIO
    if stdio:
        _require_command_allowed(merged.command, allowlist)
    server.transport = merged.transport
    server.runtime = merged.runtime
    if stdio:
        server.url = ""
        server.command = merged.command
        server.args = list(merged.args)
        server.image = merged.image
        # stdio 不接受 token;env 是整体替换语义(传 {} 即清空)
        server.encrypted_token = None
        server.token_hint = None
        if data.env is not None:
            server.encrypted_env = _encrypt_env(secret, data.env)
    else:
        server.url = (merged.url or "").strip()
        server.command = None
        server.args = []
        server.image = None
        if data.token is not None:
            token = data.token.strip()
            server.encrypted_token = encrypt_token(secret, token) if token else None
            server.token_hint = token[-4:] if token else None
    if data.enabled is not None:
        server.enabled = data.enabled
    if data.auto_approve is not None:
        server.auto_approve = data.auto_approve
    if data.tools_allowlist is not None:
        server.tools_allowlist = list(data.tools_allowlist)
    if data.timeout_seconds is not None:
        server.timeout_seconds = data.timeout_seconds
    return _to_read(server, secret)


async def delete_server(session: AsyncSession, tenant_id: str, server_id: str) -> None:
    server = await _require(session, tenant_id, server_id)
    await MCPServerRepository(session).delete(server)


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


async def test_server(
    session: AsyncSession,
    tenant_id: str,
    server_id: str,
    *,
    secret: str,
    pool: StdioSandboxPool | None = None,
) -> MCPTestRead:
    server = await _require(session, tenant_id, server_id)
    now = datetime.now(UTC)
    if (server.transport or TRANSPORT_HTTP) == TRANSPORT_STDIO:
        return await _test_stdio_server(tenant_id, server, secret, pool, now)
    client = build_client(server, secret)
    try:
        await client.initialize()
        raw_tools = await client.list_tools()
        tools = [_wrap_tool(tool) for tool in raw_tools]
        _cache_tools(server, tools)
        server.last_status = "ok"
        server.last_error = None
        server.last_checked_at = now
        return MCPTestRead(
            ok=True, detail=f"连接成功，发现 {len(tools)} 个工具", tools=tools
        )
    except (MCPError, Exception) as exc:  # noqa: BLE001 - 测试端点必须返回可读失败
        server.last_status = "error"
        server.last_error = f"{type(exc).__name__}: {exc}"[:500]
        server.last_checked_at = now
        return MCPTestRead(ok=False, detail=server.last_error, tools=[])
    finally:
        await client.aclose()


def _allowed(server: MCPServer, tool_name: str) -> bool:
    allowlist = list(server.tools_allowlist or [])
    return not allowlist or tool_name in allowlist


async def tool_specs(
    session: AsyncSession,
    tenant_id: str,
    *,
    secret: str,
    pool: StdioSandboxPool | None = None,
) -> list[ToolSpec]:
    """把已测试/启用的 MCP server 工具并入 ToolRegistry(命名空间隔离)。

    tools_cache 只由 test 端点刷新:P2 不在每次 run 前重新 list(冷启动代价高);
    P4 再做 tools_cache 的哈希校验(TODO)。
    """
    servers = await MCPServerRepository(session).list_scoped(
        tenant_id, MCPServer.enabled.is_(True), order_by=[MCPServer.created_at]
    )
    specs: list[ToolSpec] = []
    for server in servers:
        stdio = (server.transport or TRANSPORT_HTTP) == TRANSPORT_STDIO
        if stdio and pool is None:
            logger.warning(
                "stdio MCP 已跳过:沙箱未启用(mcp_stdio_enabled/sandbox_enabled)",
                server=server.name,
            )
            continue
        slug = _slug(server.name)
        for tool in server.tools_cache or []:
            wrapped = _wrap_tool(tool)
            if not wrapped.name or not _allowed(server, wrapped.name):
                continue
            handler = (
                _make_stdio_handler(tenant_id, server, wrapped.name, secret, pool)
                if stdio
                else _make_http_handler(server, wrapped.name, secret)
            )
            specs.append(
                ToolSpec(
                    name=f"mcp__{slug}__{wrapped.name}",
                    description=wrapped.description or f"MCP {server.name} 工具",
                    parameters=wrapped.input_schema or {"type": "object"},
                    handler=handler,
                    # 可信 server(auto_approve)免确认;默认仍逐次确认。
                    # 对话路径没有审批流,所以只有这里 False 的工具能在 chat 执行
                    requires_confirmation=not server.auto_approve,
                )
            )
    return specs


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


def _make_http_handler(server: MCPServer, tool_name: str, secret: str):
    async def handler(args: dict[str, Any]) -> str:
        client = build_client(server, secret)
        try:
            await client.initialize()
            return await client.call_tool(tool_name, args)
        except Exception as exc:  # noqa: BLE001 - 工具失败回喂错误文本
            return json.dumps(
                {"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False
            )
        finally:
            await client.aclose()

    return handler


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
