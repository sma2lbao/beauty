"""执行面:agent 经 tool-service 调到这里,凭证只在本服务内存里出现。

契约(与 tool-service 冻结):
- 成功 {"ok": true, "provider", "tool", "run_id", "data"};
- 工具级失败仍是 HTTP 200 + {"ok": false, "error": {code,message,hint,retryable}};
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
from connector.providers import require_provider
from connector.providers.errors import REAUTH_ERROR_CODES, ProviderError
from connector.repositories import ConnectionRepository
from connector.schemas.connections import ConnectionStatus
from connector.services import connections as connections_service


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
    provider = require_provider(settings, provider_name)
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
        return _failure(provider.name, tool_name, run_id, exc)

    credentials = decrypt_credentials(settings.credential_secret, conn.credentials)
    async with open_client(settings, transport) as client:
        try:
            data = await provider.call(tool_name, arguments, credentials, client)
        except ProviderError as exc:
            conn.last_error_code = exc.code
            if exc.code in REAUTH_ERROR_CODES:
                conn.status = ConnectionStatus.EXPIRED.value
            await session.flush()
            return _failure(provider.name, tool_name, run_id, exc)
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
            )
    conn.last_error_code = None
    conn.last_checked_at = utcnow()
    await session.flush()
    return {
        "ok": True,
        "provider": provider.name,
        "tool": tool_name,
        "run_id": run_id,
        "data": data,
    }


def _failure(
    provider: str, tool: str, run_id: str | None, exc: ProviderError
) -> dict[str, Any]:
    return {
        "ok": False,
        "provider": provider,
        "tool": tool,
        "run_id": run_id,
        "error": exc.to_error_payload(),
    }
