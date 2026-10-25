"""MCP server 管理:列表/创建/更新/删除/测试与请求体转换(自 mcp.py 拆出)。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from common.errors import AppError, ConflictError
from common.logging import get_logger
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.core.config import (
    DEFAULT_MCP_STDIO_COMMAND_ALLOWLIST,
    Settings,
)
from agent_orchestrator.models.mcp import MCPServer, MCPServerAuth
from agent_orchestrator.repositories import (
    MCPServerAuthRepository,
    MCPServerRepository,
)
from agent_orchestrator.schemas.mcp import (
    MCPServerCreate,
    MCPServerRead,
    MCPServerUpdate,
    MCPTestRead,
)
from agent_orchestrator.services import mcp_oauth
from agent_orchestrator.services.mcp_client import (
    MCPAuthRequired,
    MCPError,
)
from agent_orchestrator.services.mcp_crypto import decrypt_token, encrypt_token
from agent_orchestrator.services.mcp_runtime import (
    TRANSPORT_HTTP,
    TRANSPORT_STDIO,
    _cache_tools,
    _client_with_token,
    _decrypt_env,
    _encrypt_env,
    _env_keys,
    _slug,
    _test_stdio_server,
    _wrap_tool,
)
from agent_orchestrator.services.mcp_stdio import StdioSandboxPool

logger = get_logger(__name__)


def reference_note(servers: Sequence[MCPServer]) -> str:
    """显式引用的提示词片段:告诉模型本次只能用这些 server 的工具。

    命名前缀与 tool_specs 一致(mcp__<slug>__<tool>),模型据此对号入座。
    """
    if not servers:
        return ""
    refs = "、".join(
        f"{server.name}(工具名前缀 mcp__{_slug(server.name)}__)" for server in servers
    )
    return (
        f"用户在本次消息里明确引用了 MCP server:{refs}。"
        "请优先使用这些被引用的 server 提供的工具;未列出的 MCP server 本次不可用。"
    )


def _oauth_fields(auth: MCPServerAuth | None, server: MCPServer) -> dict[str, Any]:
    """授权行 → 读端字段;只回状态与过期时间,绝不含 token/client_secret。

    has_oauth 是"这个 server 支持/需要浏览器授权"的信号:还没发起过授权时,
    由 401 落下的 auth_required 兜底——否则前端在首次授权前没有入口。
    """
    if auth is None:
        return {
            "has_oauth": server.last_status == "auth_required",
            "oauth_authorized": False,
        }
    return {
        "has_oauth": bool(auth.client_id) or server.last_status == "auth_required",
        "oauth_authorized": bool(
            auth.encrypted_access_token or auth.encrypted_refresh_token
        ),
        "oauth_scopes": list(auth.scopes or []),
        "oauth_expires_at": auth.expires_at,
        "oauth_authorization_server": auth.authorization_server,
        "oauth_error": auth.last_error,
    }


def _to_read(
    server: MCPServer, secret: str, auth: MCPServerAuth | None = None
) -> MCPServerRead:
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
        **_oauth_fields(auth, server),
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
    auths = await MCPServerAuthRepository(session).list_for_servers(
        tenant_id, [server.id for server in servers]
    )
    return [_to_read(server, secret, auths.get(server.id)) for server in servers]


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
    auth = await MCPServerAuthRepository(session).get_for_server(tenant_id, server_id)
    return _to_read(server, secret, auth)


async def delete_server(session: AsyncSession, tenant_id: str, server_id: str) -> None:
    server = await _require(session, tenant_id, server_id)
    # 授权行与 server 同事务清理:留着孤儿 token 既不安全也没人再用
    auth = await MCPServerAuthRepository(session).get_for_server(tenant_id, server_id)
    if auth is not None:
        await MCPServerAuthRepository(session).delete(auth)
    await MCPServerRepository(session).delete(server)


async def test_server(
    session: AsyncSession,
    tenant_id: str,
    server_id: str,
    *,
    secret: str,
    pool: StdioSandboxPool | None = None,
    settings: Settings | None = None,
    transport=None,
) -> MCPTestRead:
    server = await _require(session, tenant_id, server_id)
    now = datetime.now(UTC)
    if (server.transport or TRANSPORT_HTTP) == TRANSPORT_STDIO:
        return await _test_stdio_server(tenant_id, server, secret, pool, now)
    token: str | None = None
    if settings is not None:
        try:
            token = await mcp_oauth.access_token(
                session,
                tenant_id,
                server_id,
                settings=settings,
                secret=secret,
                transport=transport,
            )
        except Exception as exc:  # noqa: BLE001 - 测试端点优先返回可读失败
            logger.warning(
                "mcp oauth token 解析失败",
                server_id=server_id,
                error=f"{type(exc).__name__}: {exc}",
            )
    client = _client_with_token(server, secret, token, transport)
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
    except MCPAuthRequired as exc:
        # 401 是"需要授权"而非"连接坏了":单独落状态,让 /mcp 页给出重新授权指引
        server.last_status = "auth_required"
        server.last_error = f"{type(exc).__name__}: {exc}"[:500]
        server.last_checked_at = now
        return MCPTestRead(
            ok=False,
            detail="服务端要求授权(HTTP 401)：请重新授权或更新该 server 的 token",
            tools=[],
            auth_required=True,
        )
    except (MCPError, Exception) as exc:  # noqa: BLE001 - 测试端点必须返回可读失败
        server.last_status = "error"
        server.last_error = f"{type(exc).__name__}: {exc}"[:500]
        server.last_checked_at = now
        return MCPTestRead(ok=False, detail=server.last_error, tools=[])
    finally:
        await client.aclose()
