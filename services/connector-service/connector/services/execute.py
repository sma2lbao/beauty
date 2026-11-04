"""执行面:agent 经 tool-service 调到这里,凭证只在本服务内存里出现。

契约(与 tool-service 冻结):
- 成功 {"ok": true, "provider", "tool", "run_id", "connection_id", "data"};
- 工具级失败仍是 HTTP 200 + {"ok": false, "provider", "tool", "run_id",
  "connection_id", "error": {code,message,hint,retryable}};
- connection_id 供 tool-service 审计/回放定位到具体连接;只有「连接已解析」时才有,
  工具不存在或无可用连接的错误信封里没有(那时也不存在连接)。
- 404 只用于「没有任何可用连接」,401 只用于内部鉴权失败。
"""

from __future__ import annotations

from typing import Any

import httpx
from common.errors import AppError, NotFoundError
from sqlalchemy.ext.asyncio import AsyncSession

from connector.core.clock import utcnow
from connector.core.config import Settings
from connector.core.crypto import decrypt_credentials
from connector.core.http import open_client
from connector.providers.errors import REAUTH_ERROR_CODES, ProviderError
from connector.repositories import ConnectionRepository
from connector.schemas.connections import ConnectionStatus
from connector.services import connections as connections_service
from connector.services.apps import provider_for_tenant
from connector.services.events import CONNECTION_EXPIRED, publish_connection_event


async def execute_tool(
    session: AsyncSession,
    settings: Settings,
    *,
    tenant_id: str,
    provider_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    transport: httpx.AsyncBaseTransport | None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """执行一个连接器工具。

    provider 按租户解析:tenant_apps 有可用行时用租户自带应用(刷新/换 token 与
    连接归属一致),否则回退全局 Settings。响应的 connection_id 只在连接已解析
    之后才带,供 tool-service 审计。
    """
    provider = await provider_for_tenant(session, settings, tenant_id, provider_name)
    try:
        provider.tool_spec(tool_name)
    except KeyError as exc:
        raise AppError(
            "connector.tool_not_found",
            f"{provider.display_name} 没有工具 {tool_name}",
            details={"provider": provider.name, "tool": tool_name},
        ) from exc

    conn = await ConnectionRepository(session).latest_active(tenant_id, provider.name)
    if conn is None:
        raise NotFoundError(
            "connector.connection_missing",
            f"尚未连接 {provider.display_name}",
            details={"provider": provider.name},
        )

    try:
        conn = await connections_service.refresh_if_expiring(
            session, settings, tenant_id, conn, transport
        )
    except ProviderError as exc:
        # 刷新失败时状态已由 _refresh_locked 落库(REAUTH 已置 expired 并发事件)
        return _failure(provider.name, tool_name, run_id, exc, connection_id=conn.id)

    credentials = decrypt_credentials(settings.credential_secret, conn.credentials)
    async with open_client(settings, transport) as client:
        try:
            data = await provider.call(tool_name, arguments, credentials, client)
        except ProviderError as exc:
            conn.last_error_code = exc.code
            if exc.code in REAUTH_ERROR_CODES:
                conn.status = ConnectionStatus.EXPIRED.value
                await publish_connection_event(
                    session, settings, conn, CONNECTION_EXPIRED
                )
            await session.flush()
            return _failure(
                provider.name, tool_name, run_id, exc, connection_id=conn.id
            )
        except httpx.HTTPError:
            await session.flush()
            return _failure(
                provider.name,
                tool_name,
                run_id,
                ProviderError(
                    "connector.upstream_unavailable",
                    "连接器上游不可达",
                    hint="稍后重试",
                    retryable=True,
                ),
                connection_id=conn.id,
            )
    conn.last_error_code = None
    conn.last_checked_at = utcnow()
    await session.flush()
    return {
        "ok": True,
        "provider": provider.name,
        "tool": tool_name,
        "run_id": run_id,
        "connection_id": conn.id,
        "data": data,
    }


def _failure(
    provider: str,
    tool: str,
    run_id: str | None,
    exc: ProviderError,
    *,
    connection_id: str | None = None,
) -> dict[str, Any]:
    """工具级失败信封;连接已解析时带 connection_id 便于审计。"""
    return {
        "ok": False,
        "provider": provider,
        "tool": tool,
        "run_id": run_id,
        "connection_id": connection_id,
        "error": exc.to_error_payload(),
    }
