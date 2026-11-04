"""后台作业容器:outbox 派发器与 24h 刷新巡检的启停(lifespan 钩子调用)。

两者都可以通过配置关闭(测试与单进程部署默认关):此时 start() 不创建任何
asyncio 任务,容器是空壳;stop() 仍是幂等空操作——测试靠这一点断言没有任务泄漏。

顺序约定:start 在 install_database 之后(需要 session_factory);stop 在
engine.dispose 之前(service-kit 在 shutdown 钩子之后才 dispose),先 set stop
事件再 cancel,最后 await 吞掉 CancelledError。
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import httpx
from events.bus import InMemoryEventBus
from events.outbox import OutboxDispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from connector.core.config import Settings
from connector.services import patrol


class ConnectorRuntime:
    """持有后台任务句柄;由 connector.main 的 lifespan 创建/关闭。"""

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        transport: httpx.AsyncBaseTransport | None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.transport = transport
        self.bus: InMemoryEventBus | None = None
        self.dispatcher: OutboxDispatcher | None = None
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def task_count(self) -> int:
        """当前后台任务数(测试断言「关闭时不泄漏任务」用)。"""
        return len(self._tasks)

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    async def start(self) -> None:
        """按开关装配并启动后台任务;关闭时不创建任何任务。"""
        if self.settings.outbox_enabled:
            self.bus = InMemoryEventBus()
            self.dispatcher = OutboxDispatcher(
                self.session_factory,
                self.bus,
                batch_size=self.settings.outbox_batch_size,
                max_attempts=self.settings.outbox_max_attempts,
            )
            self._tasks.append(
                asyncio.create_task(
                    self.dispatcher.run_forever(
                        interval_seconds=self.settings.outbox_dispatch_interval_seconds,
                        stop=self._stop,
                    ),
                    name="connector-outbox-dispatcher",
                )
            )
        if self.settings.refresh_patrol_enabled:
            self._tasks.append(
                asyncio.create_task(
                    patrol.run_forever(
                        self.session_factory,
                        self.settings,
                        self.transport,
                        self._stop,
                    ),
                    name="connector-refresh-patrol",
                )
            )

    async def stop(self) -> None:
        """停掉所有后台任务:先发停止信号再 cancel,await 吞 CancelledError。"""
        self._stop.set()
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
