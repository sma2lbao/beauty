"""AppContainer 的事件总线/outbox 装配(自 container.py 拆出):消费者注册、
dispatcher 后台任务与停止逻辑。
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

from events.bus import EventBus, InMemoryEventBus
from events.outbox import OutboxDispatcher

from agent_orchestrator.services.knowledge import KnowledgeClient
from agent_orchestrator.services.outbox import record_run_event, register_consumers


class OutboxAssemblyMixin:
    """事件总线/outbox 装配扩展:由 AppContainer 混入。"""

    async def _assemble_outbox(self) -> None:
        settings = self.settings
        bus: EventBus = self.event_bus or InMemoryEventBus()
        self._publish("event_bus", bus)
        recorder = None
        dispatcher: OutboxDispatcher | None = None
        self._outbox_stop: asyncio.Event | None = None
        if settings.outbox_enabled:
            recorder = record_run_event
            ingest_knowledge: KnowledgeClient | None = None
            if settings.knowledge_ingest_enabled:
                ingest_knowledge = KnowledgeClient(
                    settings.knowledge_service_url,
                    settings.knowledge_timeout_seconds,
                    token_provider=self._token_provider,
                )
                self._closers.append(("ingest knowledge", ingest_knowledge.aclose))
            # 消费者按各自开关在 register_consumers 内筛选(知识入库 / 记忆抽取),
            # 同一份总线、同一套 outbox 去重语义;返回名字只用于启动日志
            consumers = register_consumers(
                bus,
                knowledge=ingest_knowledge,
                session_factory=self.session_factory,
                store=self.artifact_store,
                llm=self.llm_client,
                settings=settings,
            )
            self._publish("outbox_consumers", consumers)
            dispatcher = OutboxDispatcher(
                self.session_factory,
                bus,
                batch_size=settings.outbox_batch_size,
                max_attempts=settings.outbox_max_attempts,
            )
            self._outbox_stop = asyncio.Event()
            self._outbox_task = asyncio.create_task(
                dispatcher.run_forever(
                    interval_seconds=settings.outbox_dispatch_interval_seconds,
                    stop=self._outbox_stop,
                )
            )
            self._closers.append(("outbox task", self._stop_outbox))
        self._publish("outbox_recorder", recorder)
        self._publish("outbox_dispatcher", dispatcher)

    async def _stop_outbox(self) -> None:
        if self._outbox_stop is not None:
            self._outbox_stop.set()
        task = getattr(self, "_outbox_task", None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
