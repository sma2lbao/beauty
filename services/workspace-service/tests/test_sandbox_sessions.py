"""持久 shell 会话(#2):HTTP 契约 + 会话管理器 + 两路 provider 实现。

覆盖面:

- HTTP:开会话 / 写输入 / SSE 流(回放、Last-Event-ID、响应头、坏游标)/
  关闭(explicit 与沙箱销毁联动)/ 未知会话 404 / 跨租户 404 / 过期 409 /
  输入上限 422 / provider 起不来 502。
- 管理器:ring buffer(含按字节淘汰)、多订阅者 fan-out、退出码事件、注入时钟
  的空闲回收、关闭保留期清理、订阅者上限、每沙箱会话上限、shutdown。
- provider:microsandbox 走 exec_stream(tty=True)(假 SDK)、shell 候选回退、
  失败事件映射;local provider 走真实 POSIX pty(当前环境不允许分配 pty 时
  退化为管道,或用 monkeypatch 直接覆盖退化路径)。

全用例零网络:micro 用假 SDK,local 的 shell 是宿主 /bin/sh(tmp_path 下)。
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from common.errors import ConflictError, NotFoundError, UpstreamError
from database.session import session_scope
from fastapi.testclient import TestClient

from tests.conftest import OTHER, TENANT, FakeSandboxProvider
from tests.support.sandbox_provider import (
    FakeExecEvent,
    FakeSdkError,
    _use_fake_sdk,
)
from workspace.models.sandbox import SandboxInstance
from workspace.services import sandbox_provider as provider_module
from workspace.services import shell_sessions as shell_sessions_module
from workspace.services.sandbox_provider import (
    LOCAL_WORKSPACE_SUBDIR,
    LocalSandboxProvider,
    MicrosandboxProvider,
    SandboxSpec,
    UnavailableSandboxProvider,
)
from workspace.services.shell_sessions import (
    ShellEvent,
    ShellSessionManager,
    iter_sse_frames,
)

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="持久 shell 依赖 POSIX pty / /bin/sh",
)


# --- HTTP 辅助 -------------------------------------------------------------


def _create_sandbox(client: TestClient, **payload: Any) -> dict[str, Any]:
    response = client.post("/v1/sandboxes", json=payload, headers=TENANT)
    assert response.status_code == 201, response.text
    return response.json()


def _sessions_url(sandbox_id: str, session_id: str | None = None) -> str:
    base = f"/v1/sandboxes/{sandbox_id}/sessions"
    return base if session_id is None else f"{base}/{session_id}"


def _open_session(
    client: TestClient, sandbox_id: str, **payload: Any
) -> dict[str, Any]:
    response = client.post(_sessions_url(sandbox_id), json=payload, headers=TENANT)
    assert response.status_code == 201, response.text
    return response.json()


def _input(client: TestClient, sandbox_id: str, session_id: str, data: str):
    return client.post(
        f"{_sessions_url(sandbox_id, session_id)}/input",
        json={"data": data},
        headers=TENANT,
    )


def _read_stream(client: TestClient, sandbox_id: str, session_id: str, **headers: str):
    """读一条会自己结束的 SSE 流(会话已关闭),返回 (response, 原始文本)。"""
    with client.stream(
        "GET",
        f"{_sessions_url(sandbox_id, session_id)}/stream",
        headers={**TENANT, **headers},
    ) as response:
        raw = b"".join(response.iter_raw()).decode("utf-8")
        return response, raw


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for block in raw.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        frame: dict[str, Any] = {"id": None, "event": None, "data": None}
        for line in block.splitlines():
            if line.startswith("id: "):
                frame["id"] = int(line[4:])
            elif line.startswith("event: "):
                frame["event"] = line[7:]
            elif line.startswith("data: "):
                frame["data"] = json.loads(line[6:])
        frames.append(frame)
    return frames


async def _set_sandbox(client: TestClient, sandbox_id: str, **fields: Any) -> None:
    factory = client.app.state.session_factory
    async with session_scope(factory) as session:
        record = await session.get(SandboxInstance, sandbox_id)
        assert record is not None
        for key, value in fields.items():
            setattr(record, key, value)


def _expire_soon(seconds: int = -60):
    from datetime import UTC, datetime, timedelta

    return datetime.now(UTC) + timedelta(seconds=seconds)


# --- HTTP:开 / 写 / 关 ----------------------------------------------------


def test_open_session_contract_and_provider_call(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create_sandbox(client)

    response = client.post(
        _sessions_url(sandbox["id"]),
        json={"cwd": "/workspace/src", "cols": 100, "rows": 40},
        headers=TENANT,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"session_id", "sandbox_id", "cwd", "created_at"}
    assert body["sandbox_id"] == sandbox["id"]
    assert body["cwd"] == "/workspace/src"
    assert len(body["session_id"]) == 32
    assert sandbox_provider.shell_calls == [
        {
            "name": sandbox["name"],
            "cwd": "/workspace/src",
            "env": None,
            "cols": 100,
            "rows": 40,
        }
    ]
    assert sandbox_provider.shells[0].tty is True


def test_open_session_defaults_to_workspace(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create_sandbox(client)

    body = _open_session(client, sandbox["id"])

    assert body["cwd"] == "/workspace"
    assert sandbox_provider.shell_calls[0]["cols"] == 120
    assert sandbox_provider.shell_calls[0]["rows"] == 32


def test_write_input_reaches_shell(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create_sandbox(client)
    session = _open_session(client, sandbox["id"])

    response = _input(client, sandbox["id"], session["session_id"], "echo 你好\n")

    assert response.status_code == 204, response.text
    assert sandbox_provider.shells[0].written == ["echo 你好\n".encode()]


def test_close_session_is_idempotent(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create_sandbox(client)
    session = _open_session(client, sandbox["id"])
    url = _sessions_url(sandbox["id"], session["session_id"])

    assert client.delete(url, headers=TENANT).status_code == 204
    assert sandbox_provider.shells[0].closed is True
    # 重复关闭仍 204(会话还在保留期里,只是已关闭)
    assert client.delete(url, headers=TENANT).status_code == 204


def test_input_after_close_conflicts(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create_sandbox(client)
    session = _open_session(client, sandbox["id"])
    client.delete(_sessions_url(sandbox["id"], session["session_id"]), headers=TENANT)

    response = _input(client, sandbox["id"], session["session_id"], "ls\n")

    assert response.status_code == 409
    assert response.json()["code"] == "sandbox.session_closed"


def test_input_unknown_session_404(client: TestClient) -> None:
    sandbox = _create_sandbox(client)

    response = _input(client, sandbox["id"], "nope", "ls\n")

    assert response.status_code == 404
    assert response.json()["code"] == "sandbox.session_not_found"


def test_child_shell_write_failure_maps_to_conflict(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create_sandbox(client)
    session = _open_session(client, sandbox["id"])
    sandbox_provider.shells[0].fail_write = UpstreamError(
        "sandbox.session_closed", "模拟写入失败"
    )

    response = _input(client, sandbox["id"], session["session_id"], "ls\n")

    assert response.status_code == 409
    assert response.json()["code"] == "sandbox.session_closed"


def test_child_shell_other_write_error_is_502(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create_sandbox(client)
    session = _open_session(client, sandbox["id"])
    sandbox_provider.shells[0].fail_write = UpstreamError(
        "sandbox.provider_error", "模拟管道断开"
    )

    response = _input(client, sandbox["id"], session["session_id"], "ls\n")

    assert response.status_code == 502
    assert response.json()["code"] == "sandbox.provider_error"


def test_input_over_limit_is_422(client: TestClient) -> None:
    sandbox = _create_sandbox(client)
    session = _open_session(client, sandbox["id"])

    response = _input(
        client, sandbox["id"], session["session_id"], "x" * (8 * 1024 + 1)
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"cwd": "relative/path"},
        {"cwd": "/workspace/../etc"},
        {"cols": 0},
        {"cols": 501},
        {"rows": 0},
        {"rows": 201},
    ],
)
def test_open_session_validates_input(
    client: TestClient, payload: dict[str, Any]
) -> None:
    sandbox = _create_sandbox(client)

    response = client.post(_sessions_url(sandbox["id"]), json=payload, headers=TENANT)

    assert response.status_code == 422


def test_open_session_provider_failure_is_502(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create_sandbox(client)
    sandbox_provider.fail_shell = True

    response = client.post(_sessions_url(sandbox["id"]), json={}, headers=TENANT)

    assert response.status_code == 502
    assert response.json()["code"] == "sandbox.provider_error"


def test_session_limit_per_sandbox(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create_sandbox(client)
    for _ in range(8):
        _open_session(client, sandbox["id"])

    response = client.post(_sessions_url(sandbox["id"]), json={}, headers=TENANT)

    assert response.status_code == 409
    assert response.json()["code"] == "sandbox.session_limit"


# --- HTTP:租户隔离 / 沙箱状态 --------------------------------------------


def test_cross_tenant_everything_is_404(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create_sandbox(client)
    session = _open_session(client, sandbox["id"])
    url = _sessions_url(sandbox["id"], session["session_id"])

    open_resp = client.post(_sessions_url(sandbox["id"]), json={}, headers=OTHER)
    input_resp = client.post(f"{url}/input", json={"data": "x"}, headers=OTHER)
    del_resp = client.delete(url, headers=OTHER)
    with client.stream("GET", f"{url}/stream", headers=OTHER) as stream_resp:
        body = b"".join(stream_resp.iter_raw())
        stream_status = stream_resp.status_code

    assert open_resp.status_code == 404
    assert input_resp.status_code == 404
    assert del_resp.status_code == 404
    assert stream_status == 404
    assert json.loads(body)["code"] == "sandbox.not_found"


async def test_expired_sandbox_409_and_closes_sessions(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create_sandbox(client)
    session = _open_session(client, sandbox["id"])
    await _set_sandbox(client, sandbox["id"], expires_at=_expire_soon())

    response = _input(client, sandbox["id"], session["session_id"], "ls\n")

    assert response.status_code == 409
    assert response.json()["code"] == "sandbox.expired"
    await asyncio.sleep(0)
    assert sandbox_provider.shells[0].closed is True


async def test_stopped_sandbox_409(client: TestClient) -> None:
    sandbox = _create_sandbox(client)
    await _set_sandbox(client, sandbox["id"], status="stopped")

    response = client.post(_sessions_url(sandbox["id"]), json={}, headers=TENANT)

    assert response.status_code == 409
    assert response.json()["code"] == "sandbox.stopped"


def test_delete_sandbox_closes_its_sessions(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create_sandbox(client)
    session = _open_session(client, sandbox["id"])

    assert (
        client.delete(f"/v1/sandboxes/{sandbox['id']}", headers=TENANT).status_code
        == 204
    )

    assert sandbox_provider.shells[0].closed is True
    # 沙箱没了 → 会话相关端点按资源不存在处理
    assert (
        _input(client, sandbox["id"], session["session_id"], "ls\n").status_code == 404
    )


def test_unknown_sandbox_is_404(client: TestClient) -> None:
    assert (
        client.post(_sessions_url("nope"), json={}, headers=TENANT).status_code == 404
    )
    assert client.delete(_sessions_url("nope", "s"), headers=TENANT).status_code == 404
    assert _input(client, "nope", "s", "ls\n").status_code == 404


# --- HTTP:SSE 输出流 ------------------------------------------------------


def test_stream_emits_output_and_exit_with_headers(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create_sandbox(client)
    session = _open_session(client, sandbox["id"])
    shell = sandbox_provider.shells[0]
    assert client.portal is not None
    client.portal.call(shell.emit, b"hello\r\n")
    client.portal.call(shell.finish, 0)
    client.delete(_sessions_url(sandbox["id"], session["session_id"]), headers=TENANT)

    response, raw = _read_stream(client, sandbox["id"], session["session_id"])

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    frames = _parse_sse(raw)
    assert [frame["event"] for frame in frames] == ["output", "exit"]
    assert frames[0]["data"] == {"seq": 0, "data": "hello\r\n"}
    assert frames[1]["data"] == {"seq": 1, "exit_code": 0}
    assert [frame["id"] for frame in frames] == [0, 1]


def test_stream_last_event_id_resumes(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create_sandbox(client)
    session = _open_session(client, sandbox["id"])
    shell = sandbox_provider.shells[0]
    assert client.portal is not None
    client.portal.call(shell.emit, b"first")
    client.portal.call(shell.finish, 0)
    client.delete(_sessions_url(sandbox["id"], session["session_id"]), headers=TENANT)

    _, raw = _read_stream(client, sandbox["id"], session["session_id"])
    first_frames = _parse_sse(raw)

    resumed, raw_resumed = _read_stream(
        client, sandbox["id"], session["session_id"], **{"Last-Event-ID": "0"}
    )

    assert resumed.status_code == 200
    resumed_frames = _parse_sse(raw_resumed)
    assert [frame["event"] for frame in first_frames] == ["output", "exit"]
    assert [frame["event"] for frame in resumed_frames] == ["exit"]


def test_stream_rejects_bad_last_event_id(client: TestClient) -> None:
    sandbox = _create_sandbox(client)
    session = _open_session(client, sandbox["id"])

    with client.stream(
        "GET",
        f"{_sessions_url(sandbox['id'], session['session_id'])}/stream",
        headers={**TENANT, "Last-Event-ID": "abc"},
    ) as response:
        payload = b"".join(response.iter_raw())

    assert response.status_code == 400
    assert json.loads(payload)["code"] == "sandbox.bad_cursor"


def test_stream_unknown_session_404(client: TestClient) -> None:
    sandbox = _create_sandbox(client)

    with client.stream(
        "GET", f"{_sessions_url(sandbox['id'], 'nope')}/stream", headers=TENANT
    ) as response:
        payload = b"".join(response.iter_raw())

    assert response.status_code == 404
    assert json.loads(payload)["code"] == "sandbox.session_not_found"


def test_idle_sweep_closes_session_and_frees_slot(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create_sandbox(client)
    session = _open_session(client, sandbox["id"])
    manager = client.app.state.shell_sessions
    assert client.portal is not None

    closed = client.portal.call(
        functools.partial(manager.sweep, now=time.monotonic() + 10**6)
    )

    assert closed == 1
    assert sandbox_provider.shells[0].closed is True
    assert (
        _input(client, sandbox["id"], session["session_id"], "ls\n").status_code == 409
    )


# --- 管理器单元测试(直接跑在测试自己的事件循环里)-------------------------


async def _open_managed(
    manager: ShellSessionManager,
    provider: FakeSandboxProvider,
    *,
    tenant_id: str = "t-1",
    sandbox_id: str = "sb-1",
    runtime_name: str = "bm-1",
    cwd: str = "/workspace",
):
    info = await manager.open(
        tenant_id=tenant_id,
        sandbox_id=sandbox_id,
        runtime_name=runtime_name,
        provider=provider,
        cwd=cwd,
    )
    return info, provider.shells[-1]


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


# --- microsandbox provider(假 SDK)---------------------------------------


async def test_micro_open_shell_uses_tty_exec_stream(monkeypatch) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    provider = MicrosandboxProvider(
        max_output_bytes=1024, default_exec_timeout_seconds=7
    )

    session = await provider.open_shell(
        "bm-abc", cwd="/workspace", env={"LANG": "C"}, cols=100, rows=40
    )

    call = sdk.exec_stream_calls[0]
    assert call["cmd"] == "/bin/sh"
    assert call["args"] == ["-i"]
    assert call["tty"] is True
    assert call["stdin_mode"] == "pipe"
    assert call["cwd"] == "/workspace"
    assert call["env"] == {"LANG": "C"}
    assert sdk.handle_resize_calls == [(40, 100)]
    assert session.tty is True


async def test_micro_shell_session_reads_events_and_exit(monkeypatch) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    provider = MicrosandboxProvider()
    session = await provider.open_shell("bm-abc")
    sdk.shell_events.extend(
        [
            FakeExecEvent("started"),
            FakeExecEvent("stdout", b"out"),
            FakeExecEvent("stderr", b"err"),
            FakeExecEvent("exited", code=7),
        ]
    )

    assert await session.read() == b"out"
    assert await session.read() == b"err"
    assert await session.read() is None
    assert session.exit_code == 7


async def test_micro_shell_session_write_and_close(monkeypatch) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    provider = MicrosandboxProvider()
    session = await provider.open_shell("bm-abc")

    await session.write(b"ls\n")
    assert sdk.sink_writes == [b"ls\n"]

    session._eof = True  # noqa: SLF001 - 模拟 shell 已退出
    with pytest.raises(UpstreamError) as exc:
        await session.write(b"x")
    assert exc.value.code == "sandbox.session_closed"
    session._eof = False  # noqa: SLF001
    await session.close()
    assert sdk.handle_kill_calls == 1


async def test_micro_shell_failure_event_maps_to_502(monkeypatch) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    provider = MicrosandboxProvider()
    session = await provider.open_shell("bm-abc")
    sdk.shell_events.append(FakeExecEvent("failed", code=9))

    with pytest.raises(UpstreamError) as exc:
        await session.read()

    assert exc.value.code == "sandbox.provider_error"
    assert exc.value.details["exit_code"] == 9


async def test_micro_shell_recv_error_maps_to_502(monkeypatch) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    provider = MicrosandboxProvider()
    session = await provider.open_shell("bm-abc")
    sdk.recv_error = FakeSdkError("stream broke")

    with pytest.raises(UpstreamError) as exc:
        await session.read()

    assert exc.value.code == "sandbox.provider_error"


async def test_micro_open_shell_falls_back_to_bash(monkeypatch) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.exec_stream_errors = [FakeSdkError("no /bin/sh")]
    provider = MicrosandboxProvider()

    await provider.open_shell("bm-abc")

    assert [call["cmd"] for call in sdk.exec_stream_calls] == ["/bin/sh", "/bin/bash"]


async def test_micro_open_shell_all_candidates_fail(monkeypatch) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.exec_stream_error = FakeSdkError("no shell")
    provider = MicrosandboxProvider()

    with pytest.raises(UpstreamError) as exc:
        await provider.open_shell("bm-abc")

    assert exc.value.code == "sandbox.provider_error"


async def test_micro_open_shell_tolerates_missing_stdin(monkeypatch) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.take_stdin_none = True
    provider = MicrosandboxProvider()
    session = await provider.open_shell("bm-abc")

    with pytest.raises(UpstreamError) as exc:
        await session.write(b"x")

    assert exc.value.code == "sandbox.session_closed"


# --- local provider:真 pty / 管道退化 ------------------------------------


def _local_provider(tmp_path: Path) -> LocalSandboxProvider:
    return LocalSandboxProvider(root=str(tmp_path / "sb"), max_output_bytes=8192)


async def _local_sandbox(provider: LocalSandboxProvider) -> None:
    await provider.create(
        SandboxSpec(
            tenant_id="t-1",
            name="bm-local",
            kind="generic",
            image="python",
            cpus=1.0,
            memory_mb=512,
            network="deny",
            ttl_seconds=600,
        )
    )


def _pty_available() -> bool:
    try:
        import pty

        master, slave = pty.openpty()
    except (ImportError, OSError):
        return False
    os.close(master)
    os.close(slave)
    return True


async def _drive_shell(session) -> tuple[str, int | None]:
    await session.write(b"echo shell-ok\nexit\n")
    chunks: list[bytes] = []
    while True:
        chunk = await asyncio.wait_for(session.read(), timeout=15)
        if chunk is None:
            break
        chunks.append(chunk)
    await session.close()
    return b"".join(chunks).decode("utf-8", "replace"), session.exit_code


@posix_only
@pytest.mark.skipif(not _pty_available(), reason="当前环境禁止分配 pty")
async def test_local_open_shell_uses_real_pty(tmp_path: Path) -> None:
    provider = _local_provider(tmp_path)
    await _local_sandbox(provider)

    session = await provider.open_shell("bm-local", cwd="/workspace")
    assert session.tty is True
    output, exit_code = await _drive_shell(session)

    assert "shell-ok" in output
    assert exit_code == 0


@posix_only
async def test_local_open_shell_falls_back_to_pipes(
    tmp_path: Path, monkeypatch
) -> None:
    """pty 不可用(或 Windows)时退化为管道:非真 TTY,但读写语义不变。"""
    monkeypatch.setattr(provider_module, "pty", None)
    provider = _local_provider(tmp_path)
    await _local_sandbox(provider)

    session = await provider.open_shell("bm-local", cwd="/workspace")
    assert session.tty is False
    output, exit_code = await _drive_shell(session)

    assert "shell-ok" in output
    assert exit_code == 0


@posix_only
async def test_local_open_shell_rejects_unknown_cwd(tmp_path: Path) -> None:
    provider = _local_provider(tmp_path)
    await _local_sandbox(provider)

    with pytest.raises(UpstreamError) as exc:
        await provider.open_shell("bm-local", cwd="/workspace/missing")

    assert exc.value.code == "sandbox.provider_error"
    assert (tmp_path / "sb" / "bm-local" / LOCAL_WORKSPACE_SUBDIR).exists()


# --- 覆盖率补充:管理器边界 / provider 退化 / unavailable -------------------


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


async def test_unavailable_provider_raises_for_every_operation() -> None:
    provider = UnavailableSandboxProvider()

    with pytest.raises(UpstreamError) as exec_exc:
        await provider.exec("bm-1", ["true"])
    assert exec_exc.value.code == "sandbox.provider_unavailable"

    with pytest.raises(UpstreamError) as shell_exc:
        await provider.open_shell("bm-1")
    assert shell_exc.value.code == "sandbox.provider_unavailable"

    with pytest.raises(UpstreamError):
        await provider.write_file("bm-1", "/workspace/a", b"x")
    with pytest.raises(UpstreamError):
        await provider.read_file("bm-1", "/workspace/a")
    with pytest.raises(UpstreamError):
        await provider.list_dir("bm-1", "/workspace")

    # 销毁是幂等清理:运行时本来就没有,视为已完成
    assert await provider.destroy("bm-1") is None


@posix_only
async def test_local_open_shell_returns_pty_session_when_available(
    tmp_path: Path, monkeypatch
) -> None:
    """pty 成功时的返回路径(用哨兵替代真 pty,避免依赖宿主设备)。"""
    provider = _local_provider(tmp_path)
    await _local_sandbox(provider)
    sentinel = object()

    async def fake_open_pty(name, workdir, env, cols, rows):  # noqa: ARG001
        return sentinel

    monkeypatch.setattr(provider, "_open_pty_shell", fake_open_pty)
    assert await provider.open_shell("bm-local", cwd="/workspace") is sentinel


@posix_only
async def test_local_open_shell_degrades_when_pty_devices_are_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """pty.openpty() 失败(受限容器/无设备)时退化为管道,而不是报错。"""

    class NoPty:
        @staticmethod
        def openpty():
            raise OSError("out of pty devices")

    monkeypatch.setattr(provider_module, "pty", NoPty)
    provider = _local_provider(tmp_path)
    await _local_sandbox(provider)

    session = await provider.open_shell("bm-local", cwd="/workspace")
    assert session.tty is False
    await session.close()


@posix_only
async def test_local_open_shell_maps_pipe_spawn_error(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(provider_module, "pty", None)
    monkeypatch.setattr(
        provider_module, "_pipe_shell_argv", lambda: ["/nonexistent/beauty-shell"]
    )
    provider = _local_provider(tmp_path)
    await _local_sandbox(provider)

    with pytest.raises(UpstreamError) as exc:
        await provider.open_shell("bm-local", cwd="/workspace")

    assert exc.value.code == "sandbox.provider_error"
