"""outbox 装配(§6.3/§8):run 事件进发件箱 + 交付物自动入知识库。

三段职责:

- **记录**(`record_run_event`):与业务状态**同一事务**落库。挂在
  `session.info["outbox"]` 上由 `append_event` 取用,所以 run_events 与
  outbox_events 要么一起提交、要么一起回滚——不会出现"状态改了但事件没发"。
  事件 payload 额外带上 `run_id`、信封带 `tenant_id`,让**跨服务消费者
  不依赖外部上下文**就能自洽处理;
- **投递**:lifespan 起 `OutboxDispatcher` 后台轮询,经进程内总线路由;
  失败退避重试,超过上限落 failed 留痕(可查、可人工重放);
- **消费**(`KnowledgeIngestConsumer`):首个真实消费者——run 交付后把成品
  写进 knowledge-service(§8 摄取),之后 agent 的 search_knowledge 就能查到。
  至少一次投递 + `source=run:<id>` 去重,避免重投导致重复入库。
"""

from __future__ import annotations

from typing import Any

from common.logging import get_logger
from database.session import session_scope
from events.bus import EventBus
from events.envelope import EventEnvelope
from events.outbox import OutboxRepository, mark_processed
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.models.runs import Run
from agent_orchestrator.services import runs as runs_service
from agent_orchestrator.services.knowledge import Knowledge

DELIVERED_EVENT = "run.delivered.v1"
KNOWLEDGE_CONSUMER = "knowledge-ingest"
INGEST_LIST_LIMIT = 200


async def record_run_event(
    session: AsyncSession, run: Run, envelope: EventEnvelope
) -> None:
    """把 run 事件写进发件箱(同事务;调用方提交时一起生效)。"""
    outbox_envelope = EventEnvelope(
        id=envelope.id,
        type=envelope.type,
        occurred_at=envelope.occurred_at,
        payload={**envelope.payload, "run_id": run.id},
        tenant_id=run.tenant_id,
    )
    await OutboxRepository(session).record(run.tenant_id, outbox_envelope)


class KnowledgeIngestConsumer:
    """交付物入库消费者:`run.delivered.v1` → knowledge-service(§8)。

    幂等策略(至少一次投递下必须的):

    1. 事件级去重:`processed_events` 记 (租户, 消费者, 事件 id);
    2. 内容级去重:入库前先按 `source=run:<id>` 查一次,已存在就跳过——
       覆盖"投递成功但进程在登记已处理前崩溃"的窗口。
    """

    name = KNOWLEDGE_CONSUMER

    def __init__(
        self,
        knowledge: Knowledge,
        session_factory: async_sessionmaker[AsyncSession],
        store: Any = None,
    ) -> None:
        self._knowledge = knowledge
        self._factory = session_factory
        self._store = store
        self._log = get_logger("orchestrator.outbox")

    async def __call__(self, envelope: EventEnvelope) -> None:
        if envelope.type != DELIVERED_EVENT:
            return
        tenant_id = envelope.tenant_id
        run_id = str(envelope.payload.get("run_id") or "")
        if not tenant_id or not run_id:
            # 事件不自洽 = 记录侧的问题,按失败处理让 outbox 留痕
            raise ValueError(f"{DELIVERED_EVENT} 缺少 tenant_id/run_id")

        if await self._already_processed(tenant_id, envelope.id):
            return
        if await self._already_ingested(tenant_id, run_id):
            await self._remember(tenant_id, envelope.id)
            return

        async with session_scope(self._factory) as session:
            artifact = await runs_service.get_artifact(
                session, tenant_id, run_id, self._store
            )
        source = f"run:{run_id}"
        await self._knowledge.ingest_document(
            tenant_id,
            title=artifact.title,
            content=artifact.content,
            source=source,
        )
        await self._remember(tenant_id, envelope.id)
        self._log.info(
            "artifact_ingested",
            run_id=run_id,
            title=artifact.title,
            chars=len(artifact.content),
        )

    async def _already_processed(self, tenant_id: str, event_id: str) -> bool:
        from events.outbox import ProcessedEvent
        from sqlalchemy import select

        async with session_scope(self._factory) as session:
            found = await session.scalar(
                select(ProcessedEvent.id).where(
                    ProcessedEvent.tenant_id == tenant_id,
                    ProcessedEvent.consumer == self.name,
                    ProcessedEvent.event_id == event_id,
                )
            )
        return found is not None

    async def _already_ingested(self, tenant_id: str, run_id: str) -> bool:
        documents = await self._knowledge.list_documents(
            tenant_id, limit=INGEST_LIST_LIMIT
        )
        source = f"run:{run_id}"
        return any(document.get("source") == source for document in documents)

    async def _remember(self, tenant_id: str, event_id: str) -> None:
        async with session_scope(self._factory) as session:
            await mark_processed(
                session,
                tenant_id=tenant_id,
                consumer=self.name,
                event_id=event_id,
            )


def register_consumers(
    bus: EventBus,
    *,
    knowledge: Knowledge | None,
    session_factory: async_sessionmaker[AsyncSession],
    store: Any = None,
) -> list[str]:
    """把已启用的消费者挂到总线上,返回消费者名(便于启动日志)。"""
    registered: list[str] = []
    if knowledge is not None:
        consumer = KnowledgeIngestConsumer(knowledge, session_factory, store)
        bus.subscribe(DELIVERED_EVENT, consumer)
        registered.append(consumer.name)
    return registered
