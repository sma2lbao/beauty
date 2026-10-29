"""会话管理器的边界覆盖率补充:关闭 / 保留期 / 空读 / 部分 UTF-8 / 取消。"""

from __future__ import annotations

import asyncio
import json

import pytest
from common.errors import NotFoundError, UpstreamError

from tests.conftest import FakeSandboxProvider
from tests.support.sandbox_sessions import _open_managed
from workspace.services import shell_sessions as shell_sessions_module
from workspace.services.shell_sessions import (
    ShellEvent,
    ShellSessionManager,
)

# --- 覆盖率补充:管理器边界 ----------------------------------------------


async def _drain(queue: asyncio.Queue) -> list[ShellEvent]:
    """把订阅队列读到 None(会话结束)为止。"""
    events: list[ShellEvent] = []
    while True:
        event = await asyncio.wait_for(queue.get(), timeout=2)
        if event is None:
            return events
        events.append(event)


def test_shell_event_to_sse_fills_defaults() -> None:
    """error/exit/output 事件缺字段时的 SSE 兜底值。"""
    error = ShellEvent(seq=0, kind="error")
    frame = error.to_sse()
    assert frame.startswith("id: 0\nevent: error\ndata: ")
    payload = json.loads(frame.split("data: ", 1)[1])
    assert payload == {"seq": 0, "code": "sandbox.session_error", "message": ""}

    output = ShellEvent(seq=1, kind="output")
    assert json.loads(output.to_sse().split("data: ", 1)[1]) == {
        "seq": 1,
        "data": "",
    }

    exit_event = ShellEvent(seq=2, kind="exit")
    assert json.loads(exit_event.to_sse().split("data: ", 1)[1]) == {
        "seq": 2,
        "exit_code": -1,
    }


async def test_manager_close_unknown_session_raises() -> None:
    manager = ShellSessionManager()
    with pytest.raises(NotFoundError) as exc:
        await manager.close(tenant_id="t-1", sandbox_id="sb-1", session_id="nope")
    assert exc.value.code == "sandbox.session_not_found"


async def test_manager_close_sandbox_skips_already_closed() -> None:
    provider = FakeSandboxProvider()
    manager = ShellSessionManager()
    first, _ = await _open_managed(manager, provider)
    _second, _ = await _open_managed(manager, provider)

    assert (
        await manager.close(
            tenant_id="t-1", sandbox_id="sb-1", session_id=first.session_id
        )
        is True
    )
    # 已关闭的会话再关是幂等 False,close_sandbox 也只数真正被关掉的那个
    assert (
        await manager.close(
            tenant_id="t-1", sandbox_id="sb-1", session_id=first.session_id
        )
        is False
    )
    assert await manager.close_sandbox("t-1", "sb-1") == 1
    await manager.shutdown()


async def test_manager_unsubscribe_unknown_session_is_silent() -> None:
    manager = ShellSessionManager()
    manager.unsubscribe(
        tenant_id="t-1",
        sandbox_id="sb-1",
        session_id="nope",
        queue=asyncio.Queue(),
    )


async def test_manager_sweep_keeps_closed_sessions_during_retention() -> None:
    clock = [0.0]
    provider = FakeSandboxProvider()
    manager = ShellSessionManager(clock=lambda: clock[0], closed_retention_seconds=10.0)
    info, shell = await _open_managed(manager, provider)
    shell.finish(0)
    await asyncio.sleep(0.05)

    # 已关闭但还在回放保留期内:继续留着,供前端断线重连回放
    assert await manager.sweep(now=5.0) == 0
    assert manager.count == 1

    # 超过保留期:从内存里删掉,之后按「会话不存在」处理
    assert await manager.sweep(now=20.0) == 0
    assert manager.count == 0
    with pytest.raises(NotFoundError):
        manager.subscribe(
            tenant_id="t-1", sandbox_id="sb-1", session_id=info.session_id
        )


async def test_manager_ignores_empty_reads() -> None:
    provider = FakeSandboxProvider()
    manager = ShellSessionManager()
    info, shell = await _open_managed(manager, provider)
    _, queue = manager.subscribe(
        tenant_id="t-1", sandbox_id="sb-1", session_id=info.session_id
    )

    shell.emit(b"")
    shell.emit(b"ok")

    event = await asyncio.wait_for(queue.get(), timeout=2)
    assert event.kind == "output"
    assert event.data == "ok"
    await manager.shutdown()


async def test_manager_buffers_partial_utf8_and_flushes_tail() -> None:
    provider = FakeSandboxProvider()
    manager = ShellSessionManager()
    info, shell = await _open_managed(manager, provider)
    _, queue = manager.subscribe(
        tenant_id="t-1", sandbox_id="sb-1", session_id=info.session_id
    )

    shell.emit(b"\xe4")  # 半个汉字:增量解码暂不输出
    shell.emit(b"\xbd\xa0")  # 补齐 → 你
    shell.emit(b"\xe4")  # 尾巴不完整,EOF 时用 U+FFFD 收尾
    shell.finish(0)

    events = await _drain(queue)
    assert [event.kind for event in events] == ["output", "output", "exit"]
    assert events[0].data == "你"
    assert events[1].data == "\ufffd"
    await manager.shutdown()


async def test_manager_read_app_error_becomes_error_event() -> None:
    provider = FakeSandboxProvider()
    manager = ShellSessionManager()
    info, shell = await _open_managed(manager, provider)
    _, queue = manager.subscribe(
        tenant_id="t-1", sandbox_id="sb-1", session_id=info.session_id
    )

    shell.emit_error(UpstreamError("sandbox.provider_error", "沙箱没了"))

    events = await _drain(queue)
    assert [event.kind for event in events] == ["error", "exit"]
    assert events[0].code == "sandbox.provider_error"
    assert events[0].message == "沙箱没了"
    # 读循环异常时 shell 还没报退出码 → 按 -1 记
    assert events[1].exit_code == -1
    await manager.shutdown()


async def test_manager_read_unexpected_error_is_logged() -> None:
    provider = FakeSandboxProvider()
    manager = ShellSessionManager()
    info, shell = await _open_managed(manager, provider)
    _, queue = manager.subscribe(
        tenant_id="t-1", sandbox_id="sb-1", session_id=info.session_id
    )

    shell.emit_error(RuntimeError("boom"))

    events = await _drain(queue)
    assert [event.kind for event in events] == ["error", "exit"]
    assert events[0].code == "sandbox.session_error"
    assert events[0].message == "boom"
    await manager.shutdown()


async def test_manager_close_tolerates_shell_close_failure() -> None:
    provider = FakeSandboxProvider()
    manager = ShellSessionManager()
    info, shell = await _open_managed(manager, provider)
    shell.fail_close = RuntimeError("close boom")

    assert (
        await manager.close(
            tenant_id="t-1", sandbox_id="sb-1", session_id=info.session_id
        )
        is True
    )
    assert shell.closed is True
    await manager.shutdown()


async def test_manager_close_cancels_reader_that_ignores_eof(monkeypatch) -> None:
    monkeypatch.setattr(shell_sessions_module, "CLOSE_GRACE_SECONDS", 0)
    provider = FakeSandboxProvider()
    manager = ShellSessionManager()
    info, shell = await _open_managed(manager, provider)
    shell.close_ends_read = False  # close 不叫醒读循环 → 宽限期后强制 cancel

    assert (
        await manager.close(
            tenant_id="t-1", sandbox_id="sb-1", session_id=info.session_id
        )
        is True
    )
    assert shell.closed is True
    await manager.shutdown()
