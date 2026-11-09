"""run 评分:人工反馈与规则基线(Langfuse Scores API + run 事件留痕)。

设计:

- 评分本身**不是** OTel 数据,不进 trace;写 Langfuse 走公开 Scores API,
  用 run 最近一次执行的 `trace_id` 关联到对应 trace;
- 本地以 `run.score.v1` 事件留痕(无需迁移):事件流/run 详情能回放,
  也方便以后换算子/导出;
- 上报是 best-effort 的提交后回调:评分已落库,网络失败只 warning,
  不回滚、不阻塞响应;
- **规则基线只在 Langfuse 启用时写**(`outcome`:completed=1 / failed=0),
  避免给未开 tracing 的环境塞噪声事件。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from common import langfuse
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.runs import Run, RunEvent
from agent_orchestrator.repositories import RunEventRepository
from agent_orchestrator.services.post_commit import register_after_commit, runtime_of
from agent_orchestrator.services.run_events import append_event

logger = logging.getLogger(__name__)

#: 评分事件类型(事件流可回放,无需新表)。
SCORE_EVENT = "run.score.v1"
#: 规则基线分数名:Langfuse 控制台按 name 聚合。
RULE_SCORE_NAME = "outcome"


@dataclass(frozen=True)
class ScoreDraft:
    """待写入的一条评分;source 区分人工反馈与规则基线。"""

    name: str
    value: float
    comment: str | None = None
    source: str = "human"


def _settings_of(session: AsyncSession) -> Any:
    """取会话绑定的运行时 settings;无绑定(单测/直调)返回 None。"""
    runtime = runtime_of(session)
    return runtime.settings if runtime is not None else None


def _schedule_push(session: AsyncSession, run: Run, draft: ScoreDraft) -> None:
    """登记提交后上报:只在 Langfuse 启用且有 trace_id 时执行。"""
    if not run.trace_id:
        return
    settings = _settings_of(session)
    if settings is None or not langfuse.is_enabled(settings):
        return
    trace_id = run.trace_id

    async def push() -> None:
        await langfuse.create_score(
            settings,
            trace_id=trace_id,
            name=draft.name,
            value=draft.value,
            comment=draft.comment,
        )

    register_after_commit(session, push)


async def record_score(session: AsyncSession, run: Run, draft: ScoreDraft) -> RunEvent:
    """写一条评分:先落 `run.score.v1` 事件,再登记 Langfuse 上报。"""
    event = await append_event(
        session,
        run,
        SCORE_EVENT,
        {
            "name": draft.name,
            "value": draft.value,
            "comment": draft.comment,
            "source": draft.source,
            "trace_id": run.trace_id,
        },
    )
    _schedule_push(session, run, draft)
    return event


async def record_rule_score(
    session: AsyncSession, run: Run, *, completed: bool
) -> RunEvent | None:
    """终态规则基线:completed=1 / failed=0;Langfuse 未启用时不写。"""
    settings = _settings_of(session)
    if settings is None or not langfuse.is_enabled(settings):
        return None
    return await record_score(
        session,
        run,
        ScoreDraft(
            name=RULE_SCORE_NAME,
            value=1.0 if completed else 0.0,
            source="rule",
        ),
    )


async def list_scores(
    session: AsyncSession, account_id: str, run_id: str
) -> list[RunEvent]:
    """按事件回放取评分(时间顺序,最新在后)。"""
    events = await RunEventRepository(session).list_for_run(account_id, run_id)
    return [event for event in events if event.type == SCORE_EVENT]
