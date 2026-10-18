"""Gmail / M365 日历 CRUD 端点(阶段 3B)。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from connector.api.deps import (
    get_calendar_factory,
    get_session,
    get_tenant_id,
)
from connector.models.connections import Connection
from connector.schemas.connections import (
    CalendarEventCreate,
    CalendarEventRead,
    CalendarEventUpdate,
)
from connector.services import connections as connections_service

router = APIRouter(prefix="/connections", tags=["calendar"])
DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
CalendarFactory = Annotated[
    Callable[[Connection], object], Depends(get_calendar_factory)
]


def _read(event) -> CalendarEventRead:
    return CalendarEventRead(
        id=event.id,
        title=event.title,
        start=event.start,
        end=event.end,
        description=event.description,
        html_link=event.html_link,
    )


@router.get("/{connection_id}/events", response_model=list[CalendarEventRead])
async def list_events(
    connection_id: str,
    session: DBSession,
    tenant_id: TenantId,
    factory: CalendarFactory,
    time_min: Annotated[str | None, Query(max_length=64)] = None,
    time_max: Annotated[str | None, Query(max_length=64)] = None,
) -> list[CalendarEventRead]:
    conn = await connections_service.require_active_connection(
        session, tenant_id, connection_id
    )
    events = await factory(conn).list_events(time_min, time_max)  # type: ignore[attr-defined]
    return [_read(event) for event in events]


@router.post(
    "/{connection_id}/events",
    response_model=CalendarEventRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_event(
    body: CalendarEventCreate,
    connection_id: str,
    session: DBSession,
    tenant_id: TenantId,
    factory: CalendarFactory,
) -> CalendarEventRead:
    conn = await connections_service.require_active_connection(
        session, tenant_id, connection_id
    )
    event = await factory(conn).create_event(  # type: ignore[attr-defined]
        body.title, body.start, body.end, body.description
    )
    return _read(event)


@router.patch("/{connection_id}/events/{event_id}", response_model=CalendarEventRead)
async def update_event(
    body: CalendarEventUpdate,
    connection_id: str,
    event_id: str,
    session: DBSession,
    tenant_id: TenantId,
    factory: CalendarFactory,
) -> CalendarEventRead:
    conn = await connections_service.require_active_connection(
        session, tenant_id, connection_id
    )
    event = await factory(conn).update_event(  # type: ignore[attr-defined]
        event_id, body.model_dump(exclude_unset=True)
    )
    return _read(event)


@router.delete(
    "/{connection_id}/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_event(
    connection_id: str,
    event_id: str,
    session: DBSession,
    tenant_id: TenantId,
    factory: CalendarFactory,
) -> Response:
    conn = await connections_service.require_active_connection(
        session, tenant_id, connection_id
    )
    await factory(conn).delete_event(event_id)  # type: ignore[attr-defined]
    return Response(status_code=status.HTTP_204_NO_CONTENT)
