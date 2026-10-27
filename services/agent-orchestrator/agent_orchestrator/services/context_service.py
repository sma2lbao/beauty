"""上下文工程管线的服务层边界:装配结果落 state 与事件(P1-F §7.4)。

分层约定:context 包只做纯值对象与装配(不碰 DB、不碰模型);本模块负责取
run 快照、调管线、把结果写进执行图 state,并落 run.context.assembled.v1 事件。
执行图节点因此仍是纯函数——它们只读 state 里的 context_block/context_snapshot。

失败一律降级:上下文增强不能阻断 run。管线抛错只记 warning 返回 None,
执行图退回 legacy 的 skill/memory 注入(见 graph/plan_nodes.py 的回退分支)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.context.pipeline import ContextPipeline, ContextRequest
from agent_orchestrator.models.runs import Run
from agent_orchestrator.services.run_events import append_event

logger = get_logger(__name__)

#: run 上下文装配事件(§8):进图前落一次,payload 含策略快照与各段摘要。
CONTEXT_ASSEMBLED_EVENT = "run.context.assembled.v1"


@dataclass(frozen=True, slots=True)
class RunContext:
    """一次 run 的上下文装配结果(可序列化,同时进 state 与事件)。"""

    block: str
    snapshot: dict[str, Any]
    tokens_estimate: int
    section_ids: tuple[str, ...]

    @property
    def injected(self) -> bool:
        """是否真的注入了内容(空块视为未注入,节点回退 legacy 路径)。"""
        return bool(self.block.strip())


def _request_for(run: Run, memory_block: str) -> ContextRequest:
    """从 run 行与召回记忆拼装配请求:管线不认识 ORM,入参一律是快照值。"""
    return ContextRequest(
        tenant_id=run.tenant_id,
        run_id=run.id,
        conversation_id=run.conversation_id,
        workspace_id=run.workspace_id,
        goal=run.goal,
        title=run.title,
        skill_id=run.skill_id,
        skill_markdown=run.skill_system_prompt,
        memory_block=memory_block,
    )


async def build_run_context(
    session: AsyncSession,
    run: Run,
    *,
    pipeline: ContextPipeline,
    memory_block: str = "",
) -> RunContext | None:
    """按策略装配本 run 的上下文;关闭或失败时返回 None(退回 legacy 注入)。

    策略关闭时管线本身会给出全 skipped 的空计划,这里统一折叠成 None,
    免得执行图拿着空块以为"上下文由管线负责"而漏掉 legacy 注入。
    """
    del session  # 预留:后续段(如记忆检索)需要会话时在此接入
    if not pipeline.policy.enabled:
        return None
    try:
        plan = await pipeline.build(_request_for(run, memory_block))
    except Exception:  # noqa: BLE001 - 上下文增强不阻断 run,只降级
        logger.warning("context.build_failed", run_id=run.id, exc_info=True)
        return None
    context = RunContext(
        block=plan.render(),
        snapshot=plan.snapshot(),
        tokens_estimate=plan.tokens_estimate,
        section_ids=tuple(section.id for section in plan.sections),
    )
    if not context.injected:
        return None
    return context


async def append_context_event(
    session: AsyncSession, run: Run, context: RunContext
) -> None:
    """落 run.context.assembled.v1:哪个策略、注入了哪些段、跳过了哪些段。

    事件 payload 即策略快照本身——同一 run 的上下文可复现(改配置只影响新 run)。
    事件写失败同样只降级:审计缺一条,不能让 run 停在 planning。
    """
    payload = dict(context.snapshot)
    payload["attempt"] = run.attempts
    payload["tokens_estimate"] = context.tokens_estimate
    payload["section_ids"] = list(context.section_ids)
    try:
        await append_event(session, run, CONTEXT_ASSEMBLED_EVENT, payload)
    except Exception:  # noqa: BLE001 - 事件是观测面,失败不阻断执行
        logger.warning("context.event_failed", run_id=run.id, exc_info=True)
