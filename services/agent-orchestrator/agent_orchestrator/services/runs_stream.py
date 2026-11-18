"""账号级 run 状态流(SSE):把"定时拉列表"换成"变化时推摘要"。

为什么要有这条流:控制台侧栏与 /runs 看板都吃 GET /runs?limit=50,而**账号级
没有事件源**——单 run 有 /runs/{id}/events/stream,账号级只有通知流,且通知流
只覆盖"需要提醒"的状态(终态/待人工),不含 planning→running 这类进度态。于是
侧栏只能每 5s 轮询。这条流把"定时问"换成"变化时推":

1. 帧:`id: <updated_at>|<run_id>\nevent: run.state.v1\ndata: {"cursor", "run"}\n\n`
   data 里直接给整份 RunRead 摘要(与 GET /runs 的列表项同形),前端 upsert 进
   ['runs'] 缓存即可,不必"收到变化再拉列表"——否则等于变化时轮询,省不掉请求;
2. 游标:`(updated_at, id)` 复合键,严格递增;重连时客户端回传 Last-Event-ID,
   服务端从该点之后续传(同一时间戳的多行靠 id 兜底);
3. 不签发 stream.end:这是账号级长连接,没有"终态"可收尾;核心逻辑仍是
   **扫 runs 表**,不是事件总线——多实例各自扫库,不依赖 Redis 扇出。

如实边界(见 docs/design/event-taxonomy.md §12):
- 状态变化到帧有 1 个轮询周期(`sse_poll_interval_seconds`)的延迟;
- runs.updated_at 没有索引,单账号行数小才这么扫;
- 服务端不补发断线窗口的事件,断线由客户端回落轮询兜底。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from common.errors import AppError
from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.core.config import Settings
from agent_orchestrator.core.sse import comment_frame, cursor_event_frame
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories.run import RunRepository
from agent_orchestrator.schemas.runs import RunRead

#: 帧事件名:一次 run 状态变化(载荷 = 整份 RunRead 摘要 + 游标)。
RUN_STATE_EVENT = "run.state.v1"
#: 没有游标时的回看窗口:覆盖"客户端拉完列表 → 建流"之间漏掉的变化。
RUNS_STREAM_LOOKBACK = timedelta(seconds=60)
#: 单轮最多推多少行:游标逐行推进,超量下一轮接着推,不丢也不撑爆单帧。
RUNS_STREAM_LIMIT = 200


def _aware(value: datetime) -> datetime:
    """sqlite 读回来是 naive(UTC);比较/编码前统一补时区。"""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def encode_runs_cursor(run: Run) -> str:
    """游标编码:`<updated_at ISO>|<run_id>`(左边可能有 ':' 与 '+',故用 '|')。"""
    return f"{_aware(run.updated_at).isoformat()}|{run.id}"


def parse_runs_cursor(last_event_id: str | None) -> tuple[datetime, str] | None:
    """解析断线续传游标;缺省返回 None(按回看窗口开始)。

    非法游标 → 400 `sse.bad_cursor`:在返回 StreamingResponse **之前**抛,
    不会产生半开流。
    """
    if not last_event_id:
        return None
    updated_at, separator, run_id = last_event_id.partition("|")
    try:
        parsed = datetime.fromisoformat(updated_at)
    except ValueError:
        raise AppError(
            "sse.bad_cursor", "Last-Event-ID 必须是 <更新时间>|<run_id>"
        ) from None
    if not separator or not run_id:
        raise AppError("sse.bad_cursor", "Last-Event-ID 必须是 <更新时间>|<run_id>")
    return _aware(parsed), run_id


def _start_cursor(
    cursor: tuple[datetime, str] | None,
) -> tuple[datetime, str]:
    """起点:有游标用它,否则回看 RUNS_STREAM_LOOKBACK(空 id 即"该时刻之后全部")。"""
    if cursor is not None:
        return cursor
    return datetime.now(UTC) - RUNS_STREAM_LOOKBACK, ""


async def runs_stream(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    account_id: str,
    cursor: tuple[datetime, str] | None = None,
) -> AsyncIterator[str]:
    """持续推送账号内 run 的最新摘要:先 `: ready`,再逐行推,空转发 keep-alive。"""
    yield comment_frame("ready")
    last = _start_cursor(cursor)
    idle_polls = 0
    while True:
        async with session_scope(factory) as session:
            runs = await RunRepository(session).list_changed_after(
                account_id, last[0], last[1], limit=RUNS_STREAM_LIMIT
            )
            # 在会话内把 ORM 行序列化成帧:出会话后属性可能已过期。
            frames: list[str] = []
            for run in runs:
                last = (_aware(run.updated_at), run.id)
                frames.append(
                    cursor_event_frame(
                        encode_runs_cursor(run),
                        RUN_STATE_EVENT,
                        {
                            "cursor": encode_runs_cursor(run),
                            "run": RunRead.model_validate(run).model_dump(mode="json"),
                        },
                    )
                )
        if frames:
            idle_polls = 0
            for frame in frames:
                yield frame
            # 一批拉满说明可能还有积压:立刻接着读下一页,不睡。
            continue
        idle_polls += 1
        if idle_polls % settings.sse_keepalive_polls == 0:
            yield comment_frame("keep-alive")
        await asyncio.sleep(settings.sse_poll_interval_seconds)
