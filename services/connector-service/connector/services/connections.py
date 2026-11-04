"""连接业务逻辑:凭证只进不出;状态守卫与租户隔离同 orchestrator 约定。

刷新是单飞:先按连接行加锁(with_for_update),锁内二次检查「是否仍需刷新」再
换 token 写回。部分 provider 的 refresh_token 是一次性的,并发各自刷新的后到者
必然失败,所以锁必须覆盖整个「检查 -> 换 token -> 写回」。
"""

from __future__ import annotations

from typing import Any

import httpx
from common.errors import AppError, ConflictError
from sqlalchemy.ext.asyncio import AsyncSession

from connector.core.clock import is_expiring, utcnow
from connector.core.config import Settings
from connector.core.crypto import decrypt_credentials, encrypt_credentials
from connector.core.http import open_client
from connector.models.connections import Connection
from connector.providers.base import Credentials, Identity
from connector.providers.errors import REAUTH_ERROR_CODES, ProviderError
from connector.repositories import ConnectionRepository
from connector.schemas.connections import ConnectionStatus
from connector.services.apps import provider_for_tenant
from connector.services.events import (
    CONNECTION_CREATED,
    CONNECTION_EXPIRED,
    CONNECTION_REVOKED,
    publish_connection_event,
)

_UNSET: Any = object()


async def require_connection(
    session: AsyncSession, tenant_id: str, connection_id: str
) -> Connection:
    """取租户内连接;不存在 → 404。"""
    return await ConnectionRepository(session).require_for_tenant(
        tenant_id, connection_id
    )


async def list_connections(
    session: AsyncSession,
    tenant_id: str,
    *,
    provider: str | None = None,
    status: str | None = None,
    enabled: bool | None = None,
) -> list[Connection]:
    return await ConnectionRepository(session).list_filtered(
        tenant_id, provider=provider, status=status, enabled=enabled
    )


def _require_active(conn: Connection) -> None:
    """只有 active 连接可撤销/刷新;其余状态一律 409。"""
    if conn.status != ConnectionStatus.ACTIVE.value:
        raise ConflictError(
            "connector.invalid_state",
            f"连接状态 {conn.status} 不允许该操作",
            details={"connection_id": conn.id, "status": conn.status},
        )


async def revoke_connection(
    session: AsyncSession,
    settings: Settings,
    tenant_id: str,
    connection_id: str,
) -> Connection:
    """撤销而非物理删除:凭证与审计需要留痕(§8);revoked 事件同事务入 outbox。"""
    connections = ConnectionRepository(session)
    conn = await connections.require_for_tenant(tenant_id, connection_id)
    _require_active(conn)
    conn.status = ConnectionStatus.REVOKED.value
    conn.enabled = False
    conn.revoked_at = utcnow()
    conn.revoked_by = conn.owner_subject
    conn = await connections.add(conn)
    await publish_connection_event(session, settings, conn, CONNECTION_REVOKED)
    return conn


async def update_connection(
    session: AsyncSession,
    tenant_id: str,
    connection_id: str,
    *,
    display_name: str | None = _UNSET,
    enabled: bool | None = _UNSET,
) -> Connection:
    """PATCH:只改传了的字段;关闭启用 != 解绑。"""
    connections = ConnectionRepository(session)
    conn = await connections.require_for_tenant(tenant_id, connection_id)
    if display_name is not _UNSET:
        conn.display_name = display_name
    if enabled is not _UNSET and enabled is not None:
        conn.enabled = bool(enabled)
    return await connections.add(conn)


async def upsert_authorized_connection(
    session: AsyncSession,
    *,
    settings: Settings,
    tenant_id: str,
    provider_name: str,
    owner_subject: str | None,
    credentials: Credentials,
    identity: Identity,
    scopes: list[str],
    auth_kind: str = "user",
) -> Connection:
    """授权回调/令牌连接落库:同租户同 provider 同 account 复用行,否则新建。

    新建(首次授权)时在同一事务里登记 connector.connection.created.v1;
    复用已有行属于重新授权,不改 created 语义(其状态变更由 refreshed 埋点覆盖)。
    """
    connections = ConnectionRepository(session)
    conn = await connections.find_by_account(tenant_id, provider_name, identity.account)
    created = conn is None
    if created:
        conn = Connection(
            tenant_id=tenant_id,
            provider=provider_name,
            account=identity.account,
            status=ConnectionStatus.ACTIVE.value,
            credentials={},
        )
    _apply_authorized(
        conn,
        settings=settings,
        owner_subject=owner_subject,
        credentials=credentials,
        identity=identity,
        scopes=scopes,
        auth_kind=auth_kind,
    )
    conn = await connections.add(conn)
    if created:
        await publish_connection_event(session, settings, conn, CONNECTION_CREATED)
    return conn

async def connect_with_token(
    session: AsyncSession,
    settings: Settings,
    tenant_id: str,
    *,
    owner_subject: str | None,
    provider_name: str,
    token: str,
    transport: httpx.AsyncBaseTransport | None,
    display_name: str | None = None,
) -> Connection:
    """静态令牌连接:校验令牌 -> 加密落库;不经过 OAuth 会话与回调。

    provider 必须声明 token_connectable(如 Notion 的内部集成令牌);令牌无效或
    缺权限时由 provider 抛 ProviderError,API 层翻成 400 connector.invalid_credentials。
    """
    provider = await provider_for_tenant(session, settings, tenant_id, provider_name)
    if not provider.token_connectable:
        raise AppError(
            "connector.token_not_supported",
            f"{provider.display_name} 不支持粘贴令牌连接",
            details={"provider": provider.name},
        )
    async with open_client(settings, transport) as client:
        credentials, identity = await provider.connect_with_token(token, client)
    conn = await upsert_authorized_connection(
        session,
        settings=settings,
        tenant_id=tenant_id,
        provider_name=provider.name,
        owner_subject=owner_subject,
        credentials=credentials,
        identity=identity,
        scopes=list(provider.scopes()),
        auth_kind="token",
    )
    if display_name:
        conn.display_name = display_name
    return conn

def _apply_authorized(
    conn: Connection,
    *,
    settings: Settings,
    owner_subject: str | None,
    credentials: Credentials,
    identity: Identity,
    scopes: list[str],
    auth_kind: str,
) -> None:
    _apply_credentials(conn, settings, credentials)
    conn.status = ConnectionStatus.ACTIVE.value
    conn.enabled = True
    conn.owner_subject = owner_subject
    conn.auth_kind = auth_kind
    conn.account = identity.account
    conn.provider_account_id = identity.provider_account_id
    conn.external_workspace = identity.external_workspace
    conn.display_name = identity.display_name
    conn.scopes = scopes
    conn.provider_metadata = identity.metadata
    conn.last_refreshed_at = utcnow()
    conn.last_checked_at = utcnow()
    conn.last_error_code = None
    conn.revoked_at = None
    conn.revoked_by = None


def _apply_credentials(
    conn: Connection, settings: Settings, credentials: Credentials
) -> None:
    conn.credentials = encrypt_credentials(
        settings.credential_secret, credentials.to_payload()
    )
    conn.expires_at = credentials.expires_at
    conn.refresh_expires_at = credentials.refresh_expires_at
    if credentials.scope:
        conn.scopes = [scope for scope in credentials.scope.split() if scope]


def _decrypt(conn: Connection, settings: Settings) -> Credentials:
    return Credentials.from_payload(
        decrypt_credentials(settings.credential_secret, conn.credentials)
    )


async def refresh_connection(
    session: AsyncSession,
    settings: Settings,
    tenant_id: str,
    connection_id: str,
    transport: httpx.AsyncBaseTransport | None,
) -> Connection:
    """手动刷新:强制换一次 token(仍走行锁,和惰性刷新同一条路径)。"""
    repo = ConnectionRepository(session)
    conn = await repo.require_for_update(tenant_id, connection_id)
    _require_active(conn)
    await _refresh_locked(session, settings, conn, transport)
    return conn


async def refresh_if_expiring(
    session: AsyncSession,
    settings: Settings,
    tenant_id: str,
    connection: Connection,
    transport: httpx.AsyncBaseTransport | None,
) -> Connection:
    """执行前惰性刷新:锁内二次检查,避免并发重复刷新(单飞)。"""
    if not is_expiring(connection.expires_at, settings.refresh_margin_seconds):
        return connection
    repo = ConnectionRepository(session)
    conn = await repo.require_for_update(tenant_id, connection.id)
    if not is_expiring(conn.expires_at, settings.refresh_margin_seconds):
        return conn  # 等锁期间已被别的请求刷新
    if conn.status != ConnectionStatus.ACTIVE.value:
        return conn
    await _refresh_locked(session, settings, conn, transport)
    return conn


async def _refresh_locked(
    session: AsyncSession,
    settings: Settings,
    conn: Connection,
    transport: httpx.AsyncBaseTransport | None,
) -> None:
    """调用方已持有该连接行锁;失败时按错误码决定是否置 expired。

    provider 按租户解析(tenant_apps 优先,factory 回退全局 Settings),否则租户
    自带应用密钥的连接会拿全局应用去换/刷 token,直接 invalid_grant,
    并与连接实际归属的应用不一致。
    """
    provider = await provider_for_tenant(
        session, settings, conn.tenant_id, conn.provider
    )
    credentials = _decrypt(conn, settings)
    async with open_client(settings, transport) as client:
        try:
            fresh = await provider.refresh(credentials, client)
        except ProviderError as exc:
            conn.last_error_code = exc.code
            if exc.code in REAUTH_ERROR_CODES:
                conn.status = ConnectionStatus.EXPIRED.value
                # 每失败一次发一次:消费方(通知/前端)据此提示重新授权
                await publish_connection_event(
                    session, settings, conn, CONNECTION_EXPIRED
                )
            await session.flush()
            raise
    _apply_credentials(conn, settings, fresh)
    conn.last_refreshed_at = utcnow()
    conn.last_error_code = None
    await session.flush()


async def check_connection(
    session: AsyncSession,
    settings: Settings,
    tenant_id: str,
    connection_id: str,
    transport: httpx.AsyncBaseTransport | None,
) -> Connection:
    """连通性探测:失败不 500,只把错误码写到连接上(前端展示)。"""
    repo = ConnectionRepository(session)
    conn = await repo.require_for_tenant(tenant_id, connection_id)
    provider = await provider_for_tenant(
        session, settings, conn.tenant_id, conn.provider
    )
    credentials = _decrypt(conn, settings)
    async with open_client(settings, transport) as client:
        try:
            identity = await provider.fetch_identity(credentials, client)
        except (ProviderError, ValueError) as exc:
            conn.last_error_code = (
                exc.code if isinstance(exc, ProviderError) else "connector.auth_expired"
            )
            if conn.last_error_code in REAUTH_ERROR_CODES:
                conn.status = ConnectionStatus.EXPIRED.value
                await publish_connection_event(
                    session, settings, conn, CONNECTION_EXPIRED
                )
            conn.last_checked_at = utcnow()
            await session.flush()
            return conn
    conn.display_name = identity.display_name or conn.display_name
    conn.provider_account_id = identity.provider_account_id
    conn.external_workspace = identity.external_workspace
    conn.last_error_code = None
    conn.last_checked_at = utcnow()
    await session.flush()
    return conn
