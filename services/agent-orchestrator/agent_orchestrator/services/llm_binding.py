"""模型路由绑定层(P1-G,见 docs/design/model-routing.md §6.1/§6.2)。

run 执行与直接对话都会把"本次调用链用哪个模型、网关回退记到哪里"绑定到
contextvar;调用点只关心 ``with bind_run_llm(session, run)`` / ``with
bind_dialogue_llm(...)``,不必各自拼装 token 与观察者。

- run 路径:回退落 ``run.llm.fallback.v1`` 事件(纯展示,不进 outbox);
- 对话路径:没有 run 可挂,只记 orchestrator 日志 ``llm_fallback``。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any

from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.conversations import Conversation
from agent_orchestrator.models.runs import Run
from agent_orchestrator.services.llm import (
    FallbackHop,
    FallbackObserver,
    bind_fallback_observer,
    bind_model,
    reset_fallback_observer,
    reset_model,
)
from agent_orchestrator.services.run_events import append_event

logger = get_logger(__name__)

#: 模型回退事件类型(规格见 docs/design/model-routing.md §6.2)
FALLBACK_EVENT = "run.llm.fallback.v1"


def model_for_run(run: Run) -> str | None:
    """run 的模型快照:创建时确定,重试/审批/回答都沿用它(§4)。"""
    return run.model


def model_for_conversation(conversation: Conversation) -> str | None:
    """会话默认模型;消息级 model 由调用方优先取(§6.3 优先级)。"""
    return conversation.model


def fallback_payload(run: Run, hops: tuple[FallbackHop, ...]) -> dict[str, Any]:
    """事件载荷:请求的模型(run 快照)、最终实际模型、逐跳明细。

    没有跳数时不会走到这里(观察者只在 fallbacks 非空时被调用)。
    """
    resolved = hops[-1].to_model or None if hops else run.model
    return {
        "requested_model": run.model,
        "resolved_model": resolved,
        "hops": [asdict(hop) for hop in hops],
    }


def run_fallback_events(session: AsyncSession, run: Run) -> FallbackObserver:
    """run 路径的回退观察者:每次网关回退追加一条事件(同事务,随 run 提交)。"""

    async def _record(hops: tuple[FallbackHop, ...]) -> None:
        await append_event(
            session,
            run,
            FALLBACK_EVENT,
            fallback_payload(run, hops),
            record_outbox=False,
        )

    return _record


def log_fallback_events(*, tenant_id: str, conversation_id: str) -> FallbackObserver:
    """对话路径的回退观察者:没有 run,只留一条可检索的日志。"""

    async def _log(hops: tuple[FallbackHop, ...]) -> None:
        logger.warning(
            "llm_fallback",
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            hops=[asdict(hop) for hop in hops],
        )

    return _log


@contextmanager
def bind_run_llm(session: AsyncSession, run: Run) -> Iterator[None]:
    """run 执行边界:绑定模型快照 + 回退事件观察者,退出时还原。"""
    model_token = bind_model(run.model)
    observer_token = bind_fallback_observer(run_fallback_events(session, run))
    try:
        yield
    finally:
        reset_fallback_observer(observer_token)
        reset_model(model_token)


@contextmanager
def bind_dialogue_llm(
    *, tenant_id: str, conversation_id: str, model: str | None
) -> Iterator[None]:
    """对话边界:绑定模型(消息/会话解析结果)+ 回退日志观察者。"""
    model_token = bind_model(model)
    observer_token = bind_fallback_observer(
        log_fallback_events(tenant_id=tenant_id, conversation_id=conversation_id)
    )
    try:
        yield
    finally:
        reset_fallback_observer(observer_token)
        reset_model(model_token)
