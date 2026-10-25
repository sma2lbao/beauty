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

from common.errors import NotFoundError
from common.logging import get_logger
from database.session import session_scope
from events.bus import EventBus
from events.envelope import EventEnvelope
from events.outbox import (
    DISPATCHED,
    FAILED,
    PENDING,
    OutboxRepository,
    is_processed,
    mark_processed,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.runs import Run, RunArtifact
from agent_orchestrator.repositories import MessageRepository, RunRepository
from agent_orchestrator.services import artifacts as artifacts_service
from agent_orchestrator.services import memory_extraction
from agent_orchestrator.services.knowledge import Knowledge
from agent_orchestrator.services.llm import LLMClient

DELIVERED_EVENT = "run.delivered.v1"
KNOWLEDGE_CONSUMER = "knowledge-ingest"
MEMORY_CONSUMER = "memory-extraction"
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


class MemoryExtractionConsumer:
    """run 交付后自动抽取长期记忆(run.delivered.v1)。

    只处理绑定对话的 run:消息正文在对话里,抽取器按 transcript 提炼候选。
    与知识入库不同,抽取是**增强能力**:缺消息/模型异常都只记 warning 并登记
    已处理,避免同一条事件被反复重投成死循环(失败留痕也便于排查)。
    """

    name = MEMORY_CONSUMER

    def __init__(
        self,
        llm: LLMClient,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._llm = llm
        self._factory = session_factory
        self._settings = settings
        self._log = get_logger("orchestrator.outbox")

    async def __call__(self, envelope: EventEnvelope) -> None:
        if envelope.type != DELIVERED_EVENT:
            return
        tenant_id = envelope.tenant_id
        run_id = str(envelope.payload.get("run_id") or "")
        if not tenant_id or not run_id:
            self._log.warning("memory_extract_bad_event", event_id=envelope.id)
            return
        if await self._already_processed(tenant_id, envelope.id):
            return
        try:
            await self._extract(tenant_id, run_id)
        except Exception as exc:  # noqa: BLE001 - 抽取失败不影响交付主链路
            self._log.warning("memory_extract_failed", run_id=run_id, error=str(exc))
        await self._remember(tenant_id, envelope.id)

    async def _extract(self, tenant_id: str, run_id: str) -> None:
        async with session_scope(self._factory) as session:
            run = await RunRepository(session).get_for_tenant(tenant_id, run_id)
            if run is None or not run.conversation_id:
                return
            # 与 chat 路径同口径节流:每累计 N 条会话消息才抽一次,避免每个 run
            # 交付事件都触发一次模型调用(节流命中时事件仍标记 processed)。
            min_messages = int(
                getattr(self._settings, "memory_extraction_min_messages", 0) or 0
            )
            count = await MessageRepository(session).count_for_conversation(
                tenant_id, run.conversation_id
            )
            if min_messages > 0 and count % min_messages != 0:
                self._log.debug(
                    "run_memory_extract_throttled",
                    run_id=run_id,
                    count=count,
                    every=min_messages,
                )
                return
            messages = await MessageRepository(session).list_for_conversation(
                tenant_id, run.conversation_id
            )
            memories = await memory_extraction.extract_from_messages(
                session,
                self._llm,
                tenant_id=tenant_id,
                messages=messages,
                workspace_id=run.workspace_id,
                source_type="run",
                source_id=run.id,
                settings=self._settings,
            )
            if memories:
                self._log.info(
                    "run_memories_extracted", run_id=run_id, count=len(memories)
                )

    async def _already_processed(self, tenant_id: str, event_id: str) -> bool:
        async with session_scope(self._factory) as session:
            return await is_processed(
                session,
                tenant_id=tenant_id,
                consumer=self.name,
                event_id=event_id,
            )

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
    knowledge: Knowledge | None = None,
    session_factory: async_sessionmaker[AsyncSession],
    store: Any = None,
    llm: LLMClient | None = None,
    settings: Settings | None = None,
) -> list[str]:
    """把已启用的消费者挂到总线上,返回消费者名(便于启动日志)。"""
    registered: list[str] = []
    if knowledge is not None:
        consumer = KnowledgeIngestConsumer(knowledge, session_factory, store)
        bus.subscribe(DELIVERED_EVENT, consumer)
        registered.append(consumer.name)
    if (
        llm is not None
        and settings is not None
        and settings.memory_enabled
        and settings.memory_auto_extract_enabled
    ):
        memory_consumer = MemoryExtractionConsumer(llm, session_factory, settings)
        bus.subscribe(DELIVERED_EVENT, memory_consumer)
        registered.append(memory_consumer.name)
    return registered


# --- 运维查询(§6.3):API 端点的仓储访问收敛在这里 ---
# api 层只做参数校验与响应构造,事件表怎么查/怎么重放由 service 决定。


async def list_events(
    session: AsyncSession,
    tenant_id: str,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Any]:
    """列本租户事件(最新在前),可按状态过滤。"""
    return await OutboxRepository(session).list_events(
        tenant_id=tenant_id, status=status, limit=limit, offset=offset
    )


async def stats(session: AsyncSession, tenant_id: str) -> dict[str, int]:
    """本租户各状态计数(缺省 0,便于端点直接取三项)。"""
    counts = await OutboxRepository(session).stats(tenant_id=tenant_id)
    return {
        PENDING: counts.get(PENDING, 0),
        DISPATCHED: counts.get(DISPATCHED, 0),
        FAILED: counts.get(FAILED, 0),
    }


async def requeue_event(session: AsyncSession, tenant_id: str, event_id: str) -> Any:
    """人工重放单条:回到 pending,等 dispatcher 下一轮投递。

    跨租户一律 404(不泄漏存在性),与其它资源同一约定。
    """
    repository = OutboxRepository(session)
    event = await repository.get_by_id(event_id)
    if event is None or event.tenant_id != tenant_id:
        raise NotFoundError(
            "outbox.event_not_found",
            "outbox 事件不存在",
            details={"event_id": event_id},
        )
    await repository.requeue(event)
    return event


async def requeue_failed(session: AsyncSession, tenant_id: str) -> int:
    """批量重放本租户所有 failed 事件(消费者恢复后一键补投)。"""
    return await OutboxRepository(session).requeue_failed(tenant_id=tenant_id)
