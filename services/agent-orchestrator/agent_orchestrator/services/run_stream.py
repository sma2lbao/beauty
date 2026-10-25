"""run 事件流(SSE):帧生成、断线续传与 keep-alive。

三条契约(读端行为,改传输不改契约):
1. 帧格式:普通事件为 `id: <seq>\nevent: <type>\ndata: <JSON>\n\n` 三段;
   `stream.end` 帧不带 id,data 为 `{"status": <run 终态>}`。
2. 终态:run 状态为 completed/failed/cancelled(TERMINAL_STATUSES)时,先发完已读到的事件,
   再发 `stream.end` 并结束生成器——客户端据此停止重连。
3. keep-alive:空轮询时按 `sse_keepalive_polls` 周期发注释帧 `: keep-alive\n\n`,
   仅为保活、不承载数据,客户端解析时应跳过(见 lib/sse.ts 的注释行跳过)。

读端契约:重放与增量一律读 run_events(§6.3);Redis Streams 直播是阶段 B 的
传输优化,换传输不改读端,也不改上述三条契约。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from common.errors import AppError
from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.core.config import Settings
from agent_orchestrator.core.sse import comment_frame, run_end_frame, run_event_frame
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services.run_events import list_events
from agent_orchestrator.services.run_lifecycle import require_run

TERMINAL_STATUSES = (
    RunStatus.COMPLETED.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
)


async def ensure_stream_target(
    factory: async_sessionmaker[AsyncSession], tenant_id: str, run_id: str
) -> None:
    """开流前的 404 预检。

    SSE 是长连接:预检必须用**独立短事务**,不能占着请求级 session——
    游标非法(400)与 run 不存在(404)都在建流之前返回,不产生半开流。
    """
    async with session_scope(factory) as session:
        await require_run(session, tenant_id, run_id)


def parse_last_event_id(last_event_id: str | None) -> int:
    """解析断线续传游标:缺省从 0 开始;非整数 → 400 `sse.bad_cursor`。"""
    if not last_event_id:
        return 0
    try:
        return int(last_event_id)
    except ValueError:
        raise AppError("sse.bad_cursor", "Last-Event-ID 必须是整数") from None


async def event_stream(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    tenant_id: str,
    run_id: str,
    after: int,
) -> AsyncIterator[str]:
    """先重放 after 之后的事件,再轮询增量;run 终态后发 stream.end 收尾。"""
    last_seq = after
    idle_polls = 0
    while True:
        async with session_scope(factory) as session:
            events = await list_events(session, tenant_id, run_id, after=last_seq)
            run = await require_run(session, tenant_id, run_id)
        for event in events:
            last_seq = event.seq
            yield run_event_frame(event)
        if run.status in TERMINAL_STATUSES:
            yield run_end_frame(run.status)
            return
        if not events:
            idle_polls += 1
            if idle_polls % settings.sse_keepalive_polls == 0:
                yield comment_frame("keep-alive")
            await asyncio.sleep(settings.sse_poll_interval_seconds)
