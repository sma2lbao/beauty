"""持久 shell 会话管理器:进程内会话表 + 跨实例所有权登记与接管。

单实例语义与原来一致(会话在内存、ring buffer 回放、空闲回收);有登记表时
额外获得三件事:

- **明确契约**:非持有实例收到 `sandbox.session_elsewhere`,而不是含糊的 404;
- **重启/换实例可接管**:owner 租约过期(或就是自己重启前的行)且 provider 支持
  attach(e2b 的 `pty.connect`)时,新实例直接接管同一 pty,输出接着往下走;
- **孤儿回收**:provider 不支持接管或接管失败时标 `lost`,并尽力在 provider 侧
  结束孤儿 pty,避免沙箱里堆僵尸 shell。

回放不是持久化:ring buffer 仍只在本进程内存,接管后只能看到之后的新输出。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import structlog
from common.errors import ConflictError, NotFoundError, UpstreamError

from workspace.core.config import Settings
from workspace.services.sandbox_provider import SandboxProvider
from workspace.services.shell_sessions.events import (
    ShellEvent,
    ShellSessionInfo,
)
from workspace.services.shell_sessions.placement import _PlacementMixin
from workspace.services.shell_sessions.pump import _PumpMixin
from workspace.services.shell_sessions.registry import ShellSessionRegistry
from workspace.services.shell_sessions.state import _new_decoder, _ShellSessionState

logger = structlog.get_logger(__name__)


class ShellSessionManager(_PlacementMixin, _PumpMixin):
    """进程内会话表 + 登记表(键 = account_id / sandbox_id / session_id)。

    没有登记表时退化为纯进程内管理器(单实例开发/单元测试)。
    `clock` 可注入,便于测试空闲回收而不真的等 30 分钟。
    """

    def __init__(
        self,
        *,
        idle_seconds: float = 1800.0,
        closed_retention_seconds: float = 300.0,
        ring_bytes: int = 65_536,
        max_per_sandbox: int = 8,
        max_subscribers: int = 32,
        clock: Callable[[], float] = time.monotonic,
        registry: ShellSessionRegistry | None = None,
        provider: SandboxProvider | None = None,
        lease_seconds: float = 120.0,
    ) -> None:
        self._idle_seconds = idle_seconds
        self._closed_retention_seconds = closed_retention_seconds
        self._ring_bytes = ring_bytes
        self._max_per_sandbox = max_per_sandbox
        self._max_subscribers = max_subscribers
        self._clock = clock
        self._registry = registry
        self._provider = provider
        self._lease_seconds = lease_seconds
        self._instance_id = registry.instance_id if registry is not None else ""
        self._sessions: dict[tuple[str, str, str], _ShellSessionState] = {}
        self._persist: set[asyncio.Task[None]] = set()
        self._shutdown = False

    @property
    def count(self) -> int:
        """当前内存里的会话数(含已关闭、等待回放保留期的)。"""
        return len(self._sessions)

    @property
    def instance_id(self) -> str:
        """本实例身份(无登记表时为空串)。"""
        return self._instance_id

    async def open(
        self,
        *,
        account_id: str,
        sandbox_id: str,
        runtime_name: str,
        provider: SandboxProvider,
        cwd: str,
        env: dict[str, str] | None = None,
        cols: int = 120,
        rows: int = 32,
    ) -> ShellSessionInfo:
        if self._shutdown:
            raise ConflictError("sandbox.session_closed", "服务正在关闭,暂不接受新会话")
        if await self._active_count(account_id, sandbox_id) >= self._max_per_sandbox:
            raise ConflictError(
                "sandbox.session_limit",
                f"沙箱内活跃 shell 会话已达上限({self._max_per_sandbox})",
            )
        shell = await provider.open_shell(
            runtime_name, cwd=cwd, env=env, cols=cols, rows=rows
        )
        session_id = uuid.uuid4().hex
        now = self._clock()
        state = _ShellSessionState(
            account_id=account_id,
            sandbox_id=sandbox_id,
            session_id=session_id,
            runtime_name=runtime_name,
            provider_name=getattr(provider, "provider", ""),
            cwd=cwd,
            cols=cols,
            rows=rows,
            created_at=datetime.now(UTC),
            shell=shell,
            tty=bool(getattr(shell, "tty", False)),
            decoder=_new_decoder(),
            last_activity=now,
        )
        await self._register(state)
        self._sessions[state.key] = state
        state.reader = asyncio.create_task(
            self._pump(state), name=f"shell-session-{session_id}"
        )
        logger.info(
            "shell_session.opened",
            account_id=account_id,
            sandbox_id=sandbox_id,
            session_id=session_id,
            tty=state.tty,
            cols=cols,
            rows=rows,
        )
        return ShellSessionInfo(
            session_id=session_id,
            sandbox_id=sandbox_id,
            cwd=cwd,
            created_at=state.created_at,
            tty=state.tty,
        )

    async def write(
        self, *, account_id: str, sandbox_id: str, session_id: str, data: bytes
    ) -> None:
        # 不在本进程时先按登记表判归属:别人的会话给出明确 409,而不是 404
        state = await self._ensure(account_id, sandbox_id, session_id)
        if state.closed:
            raise ConflictError("sandbox.session_closed", "shell 会话已结束")
        try:
            await state.shell.write(data)
        except UpstreamError as exc:
            if exc.code == "sandbox.session_closed":
                raise ConflictError(
                    "sandbox.session_closed", "shell 会话已结束"
                ) from exc
            raise
        state.last_activity = self._clock()

    async def ensure(
        self, *, account_id: str, sandbox_id: str, session_id: str
    ) -> None:
        """确认会话在本实例可继续(必要时接管);不能继续时抛出明确错误。

        SSE 端点用的是同步 `subscribe`,所以先单独走这一步:订阅前就知道会话
        属于谁,跨实例时返回 409 而不是"回放空、然后静默结束"。
        """
        await self._ensure(account_id, sandbox_id, session_id)

    async def close(self, *, account_id: str, sandbox_id: str, session_id: str) -> bool:
        """关闭会话;返回本次是否真的从"运行中"被关掉(幂等)。"""
        state = self._sessions.get((account_id, sandbox_id, session_id))
        if state is None:
            state = await self._ensure(account_id, sandbox_id, session_id)
        if state.closed:
            return False
        await self._close_state(state)
        return True

    async def close_sandbox(self, account_id: str, sandbox_id: str) -> int:
        """关闭某个沙箱的全部会话(沙箱被销毁/过期时调用),返回关闭个数。"""
        states = [
            state
            for state in list(self._sessions.values())
            if state.account_id == account_id and state.sandbox_id == sandbox_id
        ]
        closed = 0
        for state in states:
            if state.closed:
                continue
            await self._close_state(state)
            closed += 1
        # 沙箱没了,别的实例上还标着手持的行也一并结束(重连也必然失败)
        if self._registry is not None:
            with contextlib.suppress(Exception):
                await self._registry.close_sandbox(account_id, sandbox_id)
        return closed

    def subscribe(
        self,
        *,
        account_id: str,
        sandbox_id: str,
        session_id: str,
        after: int | None = None,
    ) -> tuple[list[ShellEvent], asyncio.Queue[ShellEvent | None]]:
        """订阅会话:返回(回放缓冲, 实时队列)。

        回放 + 注册队列在同一同步段完成,不会与读循环交错,因此不丢事件。
        会话已关闭时队列里直接放一个 None(回放完就结束)。
        """
        state = self._require(account_id, sandbox_id, session_id)
        backlog = [
            event for event in state.buffer if after is None or event.seq > after
        ]
        queue: asyncio.Queue[ShellEvent | None] = asyncio.Queue()
        if state.closed:
            queue.put_nowait(None)
        else:
            if len(state.subscribers) >= self._max_subscribers:
                raise ConflictError(
                    "sandbox.session_busy", "该会话的订阅者过多,请稍后重试"
                )
            state.subscribers.add(queue)
        state.last_activity = self._clock()
        return backlog, queue

    def unsubscribe(
        self,
        *,
        account_id: str,
        sandbox_id: str,
        session_id: str,
        queue: asyncio.Queue[ShellEvent | None],
    ) -> None:
        """断开订阅(不关会话)。"""
        state = self._sessions.get((account_id, sandbox_id, session_id))
        if state is not None:
            state.subscribers.discard(queue)

    async def sweep(self, *, now: float | None = None) -> int:
        """一轮后台维护:空闲回收 + 租约续期 + 孤儿会话回收。"""
        moment = self._clock() if now is None else now
        closed_count = 0
        for state in list(self._sessions.values()):
            if state.closed:
                if (
                    state.closed_at is not None
                    and not state.subscribers
                    and moment - state.closed_at >= self._closed_retention_seconds
                ):
                    self._sessions.pop(state.key, None)
                continue
            if moment - state.last_activity >= self._idle_seconds:
                logger.info(
                    "shell_session.idle_reaped",
                    account_id=state.account_id,
                    sandbox_id=state.sandbox_id,
                    session_id=state.session_id,
                    idle_seconds=self._idle_seconds,
                )
                await self._close_state(state)
                closed_count += 1
        await self._renew_leases()
        closed_count += await self._reclaim_orphans()
        return closed_count

    async def shutdown(self) -> None:
        """进程关停兜底:关掉所有会话,之后不再接受新会话。"""
        self._shutdown = True
        for state in list(self._sessions.values()):
            await self._close_state(state)
        self._sessions.clear()
        if self._persist:
            await asyncio.gather(*self._persist, return_exceptions=True)

    def _require(
        self, account_id: str, sandbox_id: str, session_id: str
    ) -> _ShellSessionState:
        state = self._sessions.get((account_id, sandbox_id, session_id))
        if state is None:
            raise NotFoundError(
                "sandbox.session_not_found",
                "shell 会话不存在",
                details={"session_id": session_id},
            )
        return state


def build_shell_session_manager(
    settings: Settings,
    *,
    registry: ShellSessionRegistry | None = None,
    provider: SandboxProvider | None = None,
) -> ShellSessionManager:
    """按配置装配会话管理器;有登记表时启用跨实例归属判定与接管。"""
    return ShellSessionManager(
        idle_seconds=float(settings.sandbox_shell_idle_seconds),
        closed_retention_seconds=float(settings.sandbox_shell_closed_retention_seconds),
        ring_bytes=settings.sandbox_shell_ring_bytes,
        max_per_sandbox=settings.sandbox_shell_max_per_sandbox,
        max_subscribers=settings.sandbox_shell_max_subscribers,
        registry=registry,
        provider=provider,
        lease_seconds=float(settings.sandbox_shell_lease_seconds),
    )
