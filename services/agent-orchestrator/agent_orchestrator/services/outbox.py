"""outbox 装配(§6.3/§8):run 事件进发件箱 + 交付物自动入知识库。

三段职责:

- **记录**(`record_run_event`):与业务状态**同一事务**落库。挂在
  `session.info["outbox"]` 上由 `append_event` 取用,所以 run_events 与
  outbox_events 要么一起提交、要么一起回滚——不会出现"状态改了但事件没发"。
  事件 payload 额外带上 `run_id`、信封带 `tenant_id`,让**跨服务消费者
  不依赖外部上下文**就能自洽处理;
- **投递**:lifespan 起 `OutboxDispatcher` 后台轮询,经进程内总线路由;
  失败退避重试,超过上限落 failed 留痕(可查、可人工重放);
- **消费**(`KnowledgeIngestConsumer`):首个真实消费者——run 交付后把它名下
  **全部文本交付物**逐件写进 knowledge-service(§8 摄取),二进制件跳过,
  之后 agent 的 search_knowledge 就能查到。
  至少一次投递 + 逐件 `source=run:<id>:<artifact 前缀>` 去重,避免重投重复入库。
"""

from __future__ import annotations

from typing import Any

from common.logging import get_logger
from database.session import session_scope
from events.bus import EventBus
from events.envelope import EventEnvelope
from events.outbox import OutboxRepository, is_processed, mark_processed
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.models.runs import Run, RunArtifact
from agent_orchestrator.services import artifacts as artifacts_service
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


def artifact_source(run_id: str, artifact: RunArtifact) -> str:
    """逐件入库的 source 标识:run 前缀 + artifact id 前缀(稳定、≤64 字符)。"""
    return f"run:{run_id}:{artifact.id[:8]}"


class KnowledgeIngestConsumer:
    """交付物入库消费者:`run.delivered.v1` → knowledge-service(§8)。

    一个 run 可能有多件交付物:文本件**逐件**入库(标题取各自 title),
    二进制件(截图/PDF 等)跳过——知识库只收可检索文本。

    幂等策略(至少一次投递下必须的):

    1. 事件级去重:`processed_events` 记 (租户, 消费者, 事件 id);
    2. 内容级去重:入库前先按 source 查一次,已存在就跳过——覆盖
       "投递成功但进程在登记已处理前崩溃"的窗口。
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

        pending = await self._pending_artifacts(tenant_id, run_id)
        if pending:
            ingested_sources = await self._ingested_sources(tenant_id)
            for artifact, text, source in pending:
                if source in ingested_sources:
                    continue
                await self._knowledge.ingest_document(
                    tenant_id,
                    title=artifact.title,
                    content=text,
                    source=source,
                )
                self._log.info(
                    "artifact_ingested",
                    run_id=run_id,
                    artifact_id=artifact.id,
                    title=artifact.title,
                    chars=len(text),
                )
        await self._remember(tenant_id, envelope.id)
        self._log.info("run_artifacts_ingested", run_id=run_id, count=len(pending))

    async def _pending_artifacts(
        self, tenant_id: str, run_id: str
    ) -> list[tuple[RunArtifact, str, str]]:
        """该 run 名下需要入库的文本交付物(二进制件与空正文跳过)。"""
        async with session_scope(self._factory) as session:
            rows = await artifacts_service.list_artifacts(session, tenant_id, run_id)
            pending: list[tuple[RunArtifact, str, str]] = []
            for row in rows:
                text = await artifacts_service.read_artifact_text(row, self._store)
                if text is None:
                    continue
                pending.append((row, text, artifact_source(run_id, row)))
            return pending

    async def _already_processed(self, tenant_id: str, event_id: str) -> bool:
        async with session_scope(self._factory) as session:
            return await is_processed(
                session,
                tenant_id=tenant_id,
                consumer=self.name,
                event_id=event_id,
            )

    async def _ingested_sources(self, tenant_id: str) -> set[str]:
        """已入库文档的 source 集合(逐件去重;也兼容旧版 `run:<id>`)。"""
        documents = await self._knowledge.list_documents(
            tenant_id, limit=INGEST_LIST_LIMIT
        )
        return {str(document.get("source") or "") for document in documents}

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
