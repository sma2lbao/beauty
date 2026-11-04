"""连接端点:同租户列表/详情/撤销/改配置/刷新/探测;凭证只进不出。"""

from __future__ import annotations

from typing import Annotated

from common.errors import AppError
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from connector.api.deps import get_session, get_tenant_id
from connector.core.config import Settings
from connector.core.http import get_http_transport
from connector.providers.errors import ProviderError
from connector.schemas.connections import ConnectionRead
from connector.schemas.connectors import ConnectionUpdate
from connector.services import connections as connections_service

router = APIRouter(prefix="/connections", tags=["connections"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]

#: ProviderError → HTTP 状态,与 docs/design/connector.md §5.7 的错误码表一致。
#: 瞬时故障(限流/超时/5xx)用可重试状态码,凭据类问题用 409 引导重连。
_FAILURE_STATUS: dict[str, int] = {
    "connector.auth_expired": 409,
    "connector.auth_revoked": 409,
    "connector.oauth_failed": 409,
    "connector.scope_missing": 409,
    "connector.upstream_rate_limited": 429,
    "connector.upstream_timeout": 504,
    "connector.upstream_unavailable": 502,
    "connector.upstream_error": 502,
}


class _ProviderFailure(AppError):
    """把 provider 错误码映射成协议层状态码(响应体仍是标准错误信封)。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int,
        details: dict[str, object],
    ) -> None:
        super().__init__(code, message, details=details)
        self.status = status


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.get("", response_model=list[ConnectionRead])
async def list_connections(
    session: DBSession,
    tenant_id: TenantId,
    provider: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
) -> list[ConnectionRead]:
    conns = await connections_service.list_connections(
        session, tenant_id, provider=provider, status=status, enabled=enabled
    )
    return [ConnectionRead.model_validate(conn) for conn in conns]


@router.get("/{connection_id}", response_model=ConnectionRead)
async def get_connection(
    connection_id: str, session: DBSession, tenant_id: TenantId
) -> ConnectionRead:
    conn = await connections_service.require_connection(
        session, tenant_id, connection_id
    )
    return ConnectionRead.model_validate(conn)


@router.patch("/{connection_id}", response_model=ConnectionRead)
async def update_connection(
    connection_id: str,
    payload: ConnectionUpdate,
    session: DBSession,
    tenant_id: TenantId,
) -> ConnectionRead:
    """只改传了的字段;关闭启用 != 解绑(解绑用 DELETE)。"""
    fields = payload.model_dump(exclude_unset=True)
    conn = await connections_service.update_connection(
        session, tenant_id, connection_id, **fields
    )
    return ConnectionRead.model_validate(conn)


@router.delete("/{connection_id}", response_model=ConnectionRead)
async def revoke_connection(
    connection_id: str,
    request: Request,
    session: DBSession,
    tenant_id: TenantId,
) -> ConnectionRead:
    """撤销连接(留痕不物理删,§8);仅 active 可撤,重复撤销 409。"""
    conn = await connections_service.revoke_connection(
        session, _settings(request), tenant_id, connection_id
    )
    return ConnectionRead.model_validate(conn)


@router.post("/{connection_id}/refresh", response_model=ConnectionRead)
async def refresh_connection(
    connection_id: str,
    request: Request,
    session: DBSession,
    tenant_id: TenantId,
) -> ConnectionRead:
    """手动刷新凭证(仍走行锁单飞);凭证失效 400,前端据此提示重连。"""
    try:
        conn = await connections_service.refresh_connection(
            session,
            _settings(request),
            tenant_id,
            connection_id,
            get_http_transport(request),
        )
    except ProviderError as exc:
        # 失败也要留痕:expired/last_error_code 在 service 里已写,提交后再转错误。
        await session.commit()
        raise _ProviderFailure(
            exc.code,
            exc.message,
            status=_FAILURE_STATUS.get(exc.code, 400),
            details={"connection_id": connection_id, "retryable": exc.retryable},
        ) from exc
    return ConnectionRead.model_validate(conn)


@router.post("/{connection_id}/check", response_model=ConnectionRead)
async def check_connection(
    connection_id: str,
    request: Request,
    session: DBSession,
    tenant_id: TenantId,
) -> ConnectionRead:
    """连通性探测:失败不 500,只把 last_error_code 写在连接上。"""
    conn = await connections_service.check_connection(
        session,
        _settings(request),
        tenant_id,
        connection_id,
        get_http_transport(request),
    )
    return ConnectionRead.model_validate(conn)
