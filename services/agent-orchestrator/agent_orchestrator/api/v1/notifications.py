"""通知端点:未读清单 + 已读 + SSE 增量流(P1-9b / 通知持久化)。

通知的**写入**在后台 sweeper(services/notifications.py 的 sweep_once):它扫最近
变化的 run,幂等落 notifications 表,所以关掉页面再回来也能看到未读清单。这里只读:

- GET /notifications:通知列表(未读+已读,时间倒序,游标分页);
- POST /notifications/{id}/read:单条已读;
- POST /notifications/read-all:全部已读;
- GET /notifications/stream:SSE 增量尾巴;带 Last-Event-ID 时从游标之后回放,
  断线重连不漏(旧实现不回放,因为通知当时只是内存 diff)。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse

from agent_orchestrator.api.deps import get_account_id, get_container
from agent_orchestrator.core.config import Settings
from agent_orchestrator.schemas.notifications import (
    NotificationList,
    NotificationRead,
    NotificationReadAll,
)
from agent_orchestrator.services import notifications as notifications_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationList)
async def list_notifications(
    request: Request,
    account_id: Annotated[str, Depends(get_account_id)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before: Annotated[str | None, Query()] = None,
) -> NotificationList:
    """账号内通知列表(最新在前);before 传上一页的 next_cursor。"""
    factory = _session_factory(request)
    return NotificationList.model_validate(
        await notifications_service.list_notifications(
            factory, account_id, limit=limit, before=before
        )
    )


@router.post("/{notification_id}/read", response_model=NotificationRead)
async def mark_notification_read(
    request: Request,
    notification_id: str,
    account_id: Annotated[str, Depends(get_account_id)],
) -> NotificationRead:
    """标记单条已读;不存在或不属于本账号一律 404。"""
    factory = _session_factory(request)
    return NotificationRead.model_validate(
        await notifications_service.mark_read(factory, account_id, notification_id)
    )


@router.post("/read-all", response_model=NotificationReadAll)
async def mark_all_notifications_read(
    request: Request,
    account_id: Annotated[str, Depends(get_account_id)],
) -> NotificationReadAll:
    """把账号内未读通知全部置为已读。"""
    factory = _session_factory(request)
    return NotificationReadAll.model_validate(
        await notifications_service.mark_all_read(factory, account_id)
    )


@router.get("/stream")
async def stream_notifications(
    request: Request,
    account_id: Annotated[str, Depends(get_account_id)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """订阅账号内新通知(SSE);Last-Event-ID 是事件 id(游标),服务端据此回放。"""
    container = get_container(request)
    assert container.session_factory is not None
    settings: Settings = container.settings
    return StreamingResponse(
        notifications_service.notification_stream(
            container.session_factory, settings, account_id, last_event_id
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _session_factory(request: Request):
    container = get_container(request)
    assert container.session_factory is not None
    return container.session_factory
