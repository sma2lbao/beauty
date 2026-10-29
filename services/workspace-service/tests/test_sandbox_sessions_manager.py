"""持久 shell 会话管理器单元测试(直接跑在测试自己的事件循环里)。

覆盖 ring buffer(含按字节淘汰)、多订阅者 fan-out、退出码事件、注入时钟的
空闲回收、关闭保留期、订阅者上限、每沙箱会话上限与 shutdown。
"""

from __future__ import annotations

import asyncio

import pytest
from common.errors import ConflictError

from tests.conftest import FakeSandboxProvider
from tests.support.sandbox_sessions import _open_managed
from workspace.services.shell_sessions import (
    ShellEvent,
    ShellSessionManager,
    iter_sse_frames,
)

# --- 管理器单元测试(直接跑在测试自己的事件循环里)-------------------------


async def test_manager_fans_out_to_all_subscribers() -> None:
    provider = FakeSandboxProvider()
    manager = ShellSessionManager()
    info, shell = await _open_managed(manager, provider)
    backlog_a, queue_a = manager.subscribe(
        tenant_id="t-1", sandbox_id="sb-1", session_id=info.session_id
    )
    backlog_b, queue_b = manager.subscribe(
        tenant_id="t-1", sandbox_id="sb-1", session_id=info.session_id
    )
    assert backlog_a == [] and backlog_b == []

    shell.emit(b"hello")

    event_a = await asyncio.wait_for(queue_a.get(), timeout=2)
    event_b = await asyncio.wait_for(queue_b.get(), timeout=2)
    assert event_a.kind == "output"
    assert event_a.data == "hello"
    assert event_a is event_b
    await manager.shutdown()


async def test_manager_ring_buffer_replay_and_byte_eviction() -> None:
    provider = FakeSandboxProvider()
    manager = ShellSessionManager(ring_bytes=8)
    info, shell = await _open_managed(manager, provider)

    shell.emit(b"123456")
    await asyncio.sleep(0.05)
    shell.emit(b"7890")
    await asyncio.sleep(0.05)

    backlog, _ = manager.subscribe(
        tenant_id="t-1", sandbox_id="sb-1", session_id=info.session_id
    )
    # 8 字节上限只留得下最后一块,前一块按字节淘汰
    assert [event.data for event in backlog] == ["7890"]
    assert [event.seq for event in backlog] == [1]

    # after 是「最后收到的 seq」:0 仍在缓冲里就回放,1 已跟上就没有历史
    replay_after, _ = manager.subscribe(
        tenant_id="t-1", sandbox_id="sb-1", session_id=info.session_id, after=0
    )
    assert [event.data for event in replay_after] == ["7890"]
    replay_caught_up, _ = manager.subscribe(
        tenant_id="t-1", sandbox_id="sb-1", session_id=info.session_id, after=1
    )
    assert replay_caught_up == []
    await manager.shutdown()


async def test_manager_reports_exit_code() -> None:
    provider = FakeSandboxProvider()
    manager = ShellSessionManager()
    info, shell = await _open_managed(manager, provider)
    backlog, queue = manager.subscribe(
        tenant_id="t-1", sandbox_id="sb-1", session_id=info.session_id
    )
    assert backlog == []

    shell.emit(b"bye")
    shell.finish(3)

    events = []
    while True:
        event = await asyncio.wait_for(queue.get(), timeout=2)
        if event is None:
            break
        events.append(event)
    assert [event.kind for event in events] == ["output", "exit"]
    assert events[-1].exit_code == 3

    # 已关闭的会话仍可回放,并且订阅立刻结束
    replay, replay_queue = manager.subscribe(
        tenant_id="t-1", sandbox_id="sb-1", session_id=info.session_id
    )
    assert [event.kind for event in replay] == ["output", "exit"]
    assert await asyncio.wait_for(replay_queue.get(), timeout=1) is None
    await manager.shutdown()


async def test_manager_read_error_becomes_error_event() -> None:
    provider = FakeSandboxProvider()
    manager = ShellSessionManager()
    info, shell = await _open_managed(manager, provider)
    _, queue = manager.subscribe(
        tenant_id="t-1", sandbox_id="sb-1", session_id=info.session_id
    )

    shell.emit(b"partial")
    await asyncio.sleep(0.05)
    await shell.close()
    shell.exit_code = 1
    shell._queue.put_nowait(None)  # noqa: SLF001 - 模拟读循环异常前的 EOF

    events = []
    while True:
        event = await asyncio.wait_for(queue.get(), timeout=2)
        if event is None:
            break
        events.append(event)
    assert [event.kind for event in events][-1] == "exit"
    await manager.shutdown()


async def test_manager_sweeps_idle_sessions_with_injected_clock() -> None:
    now = [100.0]
    provider = FakeSandboxProvider()
    manager = ShellSessionManager(
        idle_seconds=30.0,
        closed_retention_seconds=5.0,
        clock=lambda: now[0],
    )
    info, shell = await _open_managed(manager, provider)

    now[0] += 10
    assert await manager.sweep() == 0

    now[0] += 30
    assert await manager.sweep() == 1
    assert shell.closed is True
    assert (
        await manager.close(
            tenant_id="t-1", sandbox_id="sb-1", session_id=info.session_id
        )
        is False
    )

    # 关闭后保留期内仍可回放
    assert manager.count == 1
    now[0] += 10
    assert await manager.sweep() == 0
    assert manager.count == 0
    await manager.shutdown()


async def test_manager_rejects_new_sessions_after_shutdown() -> None:
    provider = FakeSandboxProvider()
    manager = ShellSessionManager()
    await manager.shutdown()

    with pytest.raises(ConflictError) as exc:
        await manager.open(
            tenant_id="t-1",
            sandbox_id="sb-1",
            runtime_name="bm-1",
            provider=provider,
            cwd="/workspace",
        )
    assert exc.value.code == "sandbox.session_closed"


async def test_manager_enforces_subscriber_limit() -> None:
    provider = FakeSandboxProvider()
    manager = ShellSessionManager(max_subscribers=1)
    info, _ = await _open_managed(manager, provider)
    manager.subscribe(tenant_id="t-1", sandbox_id="sb-1", session_id=info.session_id)

    with pytest.raises(ConflictError) as exc:
        manager.subscribe(
            tenant_id="t-1", sandbox_id="sb-1", session_id=info.session_id
        )
    assert exc.value.code == "sandbox.session_busy"
    await manager.shutdown()


async def test_manager_close_sandbox_closes_every_session() -> None:
    provider = FakeSandboxProvider()
    manager = ShellSessionManager()
    await _open_managed(manager, provider)
    await _open_managed(manager, provider)
    await _open_managed(manager, provider, sandbox_id="sb-2", runtime_name="bm-2")

    assert await manager.close_sandbox("t-1", "sb-1") == 2
    assert provider.shells[0].closed is True
    assert provider.shells[1].closed is True
    assert provider.shells[2].closed is False
    await manager.shutdown()


async def test_manager_unknown_session_404() -> None:
    manager = ShellSessionManager()
    with pytest.raises(Exception) as exc:
        manager.subscribe(tenant_id="t-1", sandbox_id="sb-1", session_id="nope")
    assert exc.value.code == "sandbox.session_not_found"  # type: ignore[attr-defined]
    await manager.shutdown()


async def test_iter_sse_frames_heartbeat_and_close() -> None:
    first = ShellEvent(seq=0, kind="output", data="hi")
    queue: asyncio.Queue[ShellEvent | None] = asyncio.Queue()
    closed: list[bool] = []
    frames = iter_sse_frames(
        [first],
        queue,
        heartbeat_seconds=0.01,
        on_close=lambda: closed.append(True),
    )

    assert await anext(frames) == first.to_sse()
    assert await anext(frames) == ": ping\n\n"
    second = ShellEvent(seq=1, kind="exit", exit_code=0)
    queue.put_nowait(second)
    assert await anext(frames) == second.to_sse()
    queue.put_nowait(None)
    with pytest.raises(StopAsyncIteration):
        await anext(frames)
    assert closed == [True]
