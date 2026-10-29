"""持久 shell 会话(#2):HTTP 契约(开会话 / 写输入 / SSE 流 / 关闭 / 隔离与状态)。

由原 `tests/test_sandbox_sessions.py` 按主题拆分而来:管理器单测在
`tests/test_sandbox_sessions_manager.py`(及 `_manager_edges`),两路 provider
实现与退化路径在 `tests/test_sandbox_sessions_provider.py`。

全用例零网络:micro 用假 SDK,local 的 shell 是宿主 /bin/sh(tmp_path 下)。
"""

from __future__ import annotations

import asyncio
import functools
import json
import time
from typing import Any

import pytest
from common.errors import UpstreamError
from database.session import session_scope
from fastapi.testclient import TestClient

from tests.conftest import OTHER, TENANT, FakeSandboxProvider
from workspace.models.sandbox import SandboxInstance

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
