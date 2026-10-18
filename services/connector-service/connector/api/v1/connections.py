"""连接端点:参数校验 → 调 service → 返回 schema;凭证只进不出。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from connector.api.deps import (
    get_session,
    get_settings,
    get_tenant_id,
    get_transport_factory,
)
from connector.core.config import Settings
from connector.models.connections import Connection
from connector.schemas.connections import (
    ConnectionCreate,
    ConnectionRead,
    EmailRead,
    SendEmailRequest,
    SendEmailResult,
    TestResultRead,
)
from connector.services import connections as connections_service
from connector.services.mail import MailTransport

router = APIRouter(prefix="/connections", tags=["connections"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
TransportFactory = Annotated[
    Callable[[Connection], MailTransport], Depends(get_transport_factory)
]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.post("", response_model=ConnectionRead, status_code=status.HTTP_201_CREATED)
async def create_connection(
    body: ConnectionCreate,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
) -> ConnectionRead:
    conn = await connections_service.create_connection(
        session, tenant_id, body, credential_secret=settings.credential_secret
    )
    return ConnectionRead.model_validate(conn)


@router.get("", response_model=list[ConnectionRead])
async def list_connections(
    session: DBSession, tenant_id: TenantId
) -> list[ConnectionRead]:
    conns = await connections_service.list_connections(session, tenant_id)
    return [ConnectionRead.model_validate(conn) for conn in conns]


@router.get("/{connection_id}", response_model=ConnectionRead)
async def get_connection(
    connection_id: str, session: DBSession, tenant_id: TenantId
) -> ConnectionRead:
    conn = await connections_service.require_connection(
        session, tenant_id, connection_id
    )
    return ConnectionRead.model_validate(conn)


@router.delete("/{connection_id}", response_model=ConnectionRead)
async def revoke_connection(
    connection_id: str, session: DBSession, tenant_id: TenantId
) -> ConnectionRead:
    """撤销连接(留痕不物理删,§8);仅 active 可撤,重复撤销 409。"""
    conn = await connections_service.revoke_connection(
        session, tenant_id, connection_id
    )
    return ConnectionRead.model_validate(conn)


@router.post("/{connection_id}/test", response_model=TestResultRead)
async def test_connection(
    connection_id: str,
    session: DBSession,
    tenant_id: TenantId,
    transport_factory: TransportFactory,
) -> TestResultRead:
    """连通性探活:无论成败都刷新 last_checked_at,结果如实返回。"""
    conn = await connections_service.require_active_connection(
        session, tenant_id, connection_id
    )
    result = await transport_factory(conn).test()
    connections_service.mark_checked(conn)
    return TestResultRead(ok=result.ok, detail=result.detail)


@router.get("/{connection_id}/emails", response_model=list[EmailRead])
async def fetch_emails(
    connection_id: str,
    session: DBSession,
    tenant_id: TenantId,
    transport_factory: TransportFactory,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[EmailRead]:
    """收件箱最近 N 封(只读);传输失败统一 502 connector.mail_unavailable。"""
    conn = await connections_service.require_active_connection(
        session, tenant_id, connection_id
    )
    emails = await transport_factory(conn).fetch(limit)
    return [
        EmailRead(
            subject=e.subject,
            sender=e.sender,
            sent_at=e.sent_at,
            snippet=e.snippet,
            id=e.id,
            thread_id=e.thread_id,
            has_attachments=e.has_attachments,
        )
        for e in emails
    ]


@router.post("/{connection_id}/emails", response_model=SendEmailResult)
async def send_email(
    body: SendEmailRequest,
    connection_id: str,
    session: DBSession,
    tenant_id: TenantId,
    transport_factory: TransportFactory,
) -> SendEmailResult:
    """发信(写操作):调用方必须先经人工确认闸(§6.2),服务端不做二次判断。"""
    conn = await connections_service.require_active_connection(
        session, tenant_id, connection_id
    )
    detail = await transport_factory(conn).send(body.to, body.subject, body.body)
    return SendEmailResult(ok=True, detail=detail)
