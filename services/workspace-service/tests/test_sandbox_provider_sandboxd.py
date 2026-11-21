"""SandboxdProvider:控制面形状、stdin 适配、文件树与异常映射(零网络)。"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from common.errors import NotFoundError, UpstreamError

from tests.support.sandbox_provider_sandboxd import (
    DEFAULT_EXEC,
    FakePtyConnector,
    FakeWebSocket,
    PtyHandshakeError,
    SandboxdStub,
    _spec,
)
from workspace.core.config import Settings
from workspace.services.sandbox_provider import (
    SandboxdProvider,
    build_sandbox_provider,
)


def _provider(
    stub: SandboxdStub | None = None, **overrides: object
) -> SandboxdProvider:
    kwargs: dict[str, object] = {
        "base_url": "http://sandboxd.test:8088",
        "token": "sandboxd-test-token",
        "default_image": "python:3.11-slim",
        "request_timeout_seconds": 5.0,
        "transport": (httpx.MockTransport(stub.handler) if stub is not None else None),
    }
    kwargs.update(overrides)
    return SandboxdProvider(**kwargs)  # type: ignore[arg-type]


def _json(request: httpx.Request) -> dict[str, object]:
    return json.loads(request.content)


async def test_create_maps_spec_to_control_plane() -> None:
    stub = SandboxdStub()
    provider = _provider(stub)

    await provider.create(_spec())

    request = stub.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/sandboxes"
    assert request.headers["Authorization"] == "Bearer sandboxd-test-token"
    assert _json(request) == {
        "image": "python:3.11-slim",  # microsandbox 的 python 别名换成 Docker 镜像
        "ttl_seconds": 900,
        "network": False,
        "owner": "bm-abc",
        "memory_mb": 512,
        "cpus": 1.0,
    }
    assert provider._ids == {"bm-abc": "sbx-1"}
    assert provider._ttls == {"bm-abc": 900}


async def test_create_passes_image_network_and_resources() -> None:
    stub = SandboxdStub()
    provider = _provider(stub)

    await provider.create(
        _spec(
            image="alpine:3.20",
            network="public",
            ttl_seconds=600,
            cpus=2.0,
            memory_mb=1024,
        )
    )

    body = _json(stub.requests[0])
    assert body["image"] == "alpine:3.20"
    assert body["network"] is True
    assert body["ttl_seconds"] == 600
    assert body["cpus"] == 2.0
    assert body["memory_mb"] == 1024


async def test_create_auth_failure_is_provider_unavailable() -> None:
    stub = SandboxdStub()
    stub.create_status = 401
    stub.create_payload = {"detail": "bad token"}
    provider = _provider(stub)

    with pytest.raises(UpstreamError) as excinfo:
        await provider.create(_spec())

    assert excinfo.value.code == "sandbox.provider_unavailable"
    assert excinfo.value.details["status"] == 401
    assert excinfo.value.details["error"] == "bad token"


async def test_create_without_id_is_provider_error() -> None:
    stub = SandboxdStub()
    stub.create_payload = {"sandbox": {"state": "starting"}}
    provider = _provider(stub)

    with pytest.raises(UpstreamError) as excinfo:
        await provider.create(_spec())

    assert excinfo.value.code == "sandbox.provider_error"
    assert "未返回 id" in excinfo.value.message


async def test_create_connection_error_is_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _provider(transport=httpx.MockTransport(handler))

    with pytest.raises(UpstreamError) as excinfo:
        await provider.create(_spec())

    assert excinfo.value.code == "sandbox.provider_error"
    assert "ConnectError" in excinfo.value.details["error"]


async def test_exec_passes_argv_env_and_extends_ttl() -> None:
    stub = SandboxdStub()
    provider = _provider(stub)
    await provider.create(_spec())

    result = await provider.exec(
        "bm-abc",
        ["python3", "-c", "print(1)"],
        cwd="/workspace/src",
        env={"A": "1"},
        timeout_seconds=90,
    )

    body = _json(stub.calls("POST", "/v1/sandboxes/sbx-1/exec")[0])
    assert body == {
        "cmd": ["python3", "-c", "print(1)"],
        "cwd": "/workspace/src",
        "env": {"A": "1"},
        "detach": False,
        "shell": False,
        "timeout_seconds": 90,
    }
    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert result.duration_ms == 12
    extend = _json(stub.calls("POST", "/v1/sandboxes/sbx-1/extend")[0])
    assert extend == {"ttl_seconds": 900}


async def test_exec_timed_out_flag_is_exec_timeout() -> None:
    stub = SandboxdStub()
    stub.exec_payload = dict(DEFAULT_EXEC, timed_out=True)
    provider = _provider(stub)
    await provider.create(_spec())

    with pytest.raises(UpstreamError) as excinfo:
        await provider.exec("bm-abc", ["sleep", "60"], timeout_seconds=1)

    assert excinfo.value.code == "sandbox.exec_timeout"
    assert excinfo.value.details["timeout_seconds"] == 1
    assert stub.calls("POST", "/v1/sandboxes/sbx-1/extend") == []


async def test_exec_http_504_is_exec_timeout() -> None:
    stub = SandboxdStub()
    stub.exec_status = 504
    stub.exec_payload = {"detail": "upstream timeout"}
    provider = _provider(stub)
    await provider.create(_spec())

    with pytest.raises(UpstreamError) as excinfo:
        await provider.exec("bm-abc", ["sleep", "60"], timeout_seconds=2)

    assert excinfo.value.code == "sandbox.exec_timeout"
    assert excinfo.value.details["timeout_seconds"] == 2


async def test_exec_truncates_output() -> None:
    stub = SandboxdStub()
    stub.exec_payload = dict(DEFAULT_EXEC, stdout="abcdefgh", stderr="1234567")
    provider = _provider(stub, max_output_bytes=5)
    await provider.create(_spec())

    result = await provider.exec("bm-abc", ["cat", "big"])

    assert result.stdout == "abcde"
    assert result.stderr == "12345"
    assert result.truncated is True


async def test_exec_stdin_uploads_wrapper_and_cleans_up() -> None:
    stub = SandboxdStub()
    provider = _provider(stub)
    await provider.create(_spec())

    await provider.exec("bm-abc", ["python3", "-"], stdin="print(1)\n")

    upload = stub.calls("PUT")[0]
    temp_path = stub.query(upload)["path"]
    assert temp_path.startswith("/tmp/.beauty-stdin-")
    assert upload.content == b"print(1)\n"

    body = _json(stub.calls("POST", "/v1/sandboxes/sbx-1/exec")[0])
    assert body["cmd"] == [
        "/bin/sh",
        "-c",
        'exec "$@" < "$BEAUTY_SANDBOX_STDIN"',
        "beauty-stdin",
        "python3",
        "-",
    ]
    assert body["env"] == {"BEAUTY_SANDBOX_STDIN": temp_path}

    delete = stub.calls("DELETE")[0]
    assert stub.query(delete)["path"] == temp_path


async def test_restart_resolves_id_from_control_plane_owner() -> None:
    stub = SandboxdStub()
    first = _provider(stub)
    await first.create(_spec())

    stub.list_payload = {
        "data": {
            "sandboxes": [
                {"id": "sbx-9", "owner": "someone-else"},
                {"id": "sbx-1", "owner": "bm-abc"},
            ]
        }
    }
    restarted = _provider(stub)

    await restarted.exec("bm-abc", ["true"])

    assert stub.calls("GET", "/v1/sandboxes")
    assert stub.calls("POST", "/v1/sandboxes/sbx-1/exec")
    assert restarted._ids == {"bm-abc": "sbx-1"}


async def test_read_file_404_is_not_found() -> None:
    stub = SandboxdStub()
    stub.download_status = 404
    provider = _provider(stub)
    await provider.create(_spec())

    with pytest.raises(NotFoundError) as excinfo:
        await provider.read_file("bm-abc", "/workspace/missing.txt")

    assert excinfo.value.code == "sandbox.file_not_found"
    assert excinfo.value.details["path"] == "/workspace/missing.txt"
    assert excinfo.value.details["sandbox_name"] == "bm-abc"


async def test_write_file_creates_parent_then_retries() -> None:
    stub = SandboxdStub()
    stub.upload_statuses = [500]
    provider = _provider(stub)
    await provider.create(_spec())

    await provider.write_file("bm-abc", "/workspace/dir/file.txt", b"data")

    uploads = stub.calls("PUT")
    assert len(uploads) == 2
    assert uploads[0].content == b"data"
    assert uploads[1].content == b"data"
    mkdir = _json(stub.calls("POST", "/v1/sandboxes/sbx-1/exec")[0])
    assert mkdir["cmd"] == ["mkdir", "-p", "/workspace/dir"]


async def test_list_dir_recurses_to_depth_and_skips_symlinks() -> None:
    stub = SandboxdStub()
    stub.file_lists = {
        "/workspace": {
            "entries": [
                {"path": "/workspace/a.txt", "type": "file", "size": 3},
                {"path": "/workspace/src", "type": "directory"},
            ]
        },
        "/workspace/src": {
            "entries": [
                {
                    "name": "b.py",
                    "kind": "file",
                    "size": 5,
                    "modified": "2024-01-01T00:00:00Z",
                },
                {"name": "sub", "is_dir": True},
                {"name": "link", "type": "symlink"},
            ]
        },
        "/workspace/src/sub": {
            "entries": [{"name": "c.txt", "type": "file", "size": 1}]
        },
    }
    provider = _provider(stub)
    await provider.create(_spec())

    entries = await provider.list_dir("bm-abc", "/workspace", depth=2)

    assert [entry.path for entry in entries] == [
        "/workspace/a.txt",
        "/workspace/src",
        "/workspace/src/b.py",
        "/workspace/src/link",
        "/workspace/src/sub",
    ]
    kinds = {entry.path: entry.kind for entry in entries}
    assert kinds["/workspace/src"] == "dir"
    assert kinds["/workspace/src/link"] == "symlink"
    assert entries[2].size == 5
    assert entries[2].modified is not None
    assert (
        stub.calls("GET", "/v1/sandboxes/sbx-1/files/list")[0].url.params["path"]
        == "/workspace"
    )
    assert not [
        request
        for request in stub.calls("GET", "/v1/sandboxes/sbx-1/files/list")
        if stub.query(request)["path"] == "/workspace/src/sub"
    ]

    deeper = await provider.list_dir("bm-abc", "/workspace", depth=3)
    assert "/workspace/src/sub/c.txt" in [entry.path for entry in deeper]


async def test_list_dir_missing_base_is_not_found() -> None:
    stub = SandboxdStub()
    stub.file_list_status = 404
    provider = _provider(stub)
    await provider.create(_spec())

    with pytest.raises(NotFoundError) as excinfo:
        await provider.list_dir("bm-abc", "/workspace")

    assert excinfo.value.code == "sandbox.file_not_found"
    assert excinfo.value.details["path"] == "/workspace"


async def test_destroy_treats_missing_runtime_as_done() -> None:
    stub = SandboxdStub()
    stub.delete_status = 404
    provider = _provider(stub)
    await provider.create(_spec())

    await provider.destroy("bm-abc")

    assert provider._ids == {}
    assert provider._ttls == {}


async def test_destroy_after_restart_does_not_call_delete() -> None:
    stub = SandboxdStub()
    stub.list_payload = {"sandboxes": []}
    provider = _provider(stub)

    await provider.destroy("bm-abc")

    assert stub.calls("GET", "/v1/sandboxes")
    assert stub.calls("DELETE") == []


async def test_open_shell_connects_pty_and_streams() -> None:
    stub = SandboxdStub()
    websocket = FakeWebSocket()
    connector = FakePtyConnector(websocket)
    provider = _provider(stub, ws_connect=connector)
    await provider.create(_spec())

    shell = await provider.open_shell(
        "bm-abc",
        cwd="/workspace/src",
        env={"A": "1", "B": "two words"},
        cols=100,
        rows=40,
    )

    assert shell.tty is True
    call = connector.calls[0]
    parsed = urlparse(call["url"])
    assert parsed.scheme == "ws"
    assert parsed.netloc == "sandboxd.test:8088"
    assert parsed.path == "/v1/sandboxes/sbx-1/pty"
    assert parse_qs(parsed.query) == {
        "cmd": ["env A=1 'B=two words' /bin/sh -i"],
        "cwd": ["/workspace/src"],
        "cols": ["100"],
        "rows": ["40"],
    }
    assert call["additional_headers"] == {
        "Authorization": "Bearer sandboxd-test-token"
    }
    assert call["proxy"] is None
    assert call["max_size"] is None

    websocket.push("hello ")
    websocket.push(b"world")
    assert await shell.read() == b"hello "
    assert await shell.read() == b"world"

    await shell.write(b"echo hi\n")
    assert websocket.sent == ["echo hi\n"]

    websocket.push('{"type":"exit","exit_code":7}')
    assert await shell.read() is None
    assert shell.exit_code == 7
    with pytest.raises(UpstreamError) as excinfo:
        await shell.write(b"x")
    assert excinfo.value.code == "sandbox.session_closed"

    await shell.close()
    assert websocket.closed is True


async def test_open_shell_eof_without_exit_frame_keeps_exit_code_none() -> None:
    websocket = FakeWebSocket()
    provider = _provider(SandboxdStub(), ws_connect=FakePtyConnector(websocket))
    await provider.create(_spec())

    shell = await provider.open_shell("bm-abc")
    websocket.finish()

    assert await shell.read() is None
    assert shell.exit_code is None
    await shell.close()


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "sandbox.provider_unavailable"),
        (404, "sandbox.provider_error"),
        (500, "sandbox.provider_error"),
    ],
)
async def test_open_shell_maps_handshake_status(status: int, code: str) -> None:
    provider = _provider(
        SandboxdStub(),
        ws_connect=FakePtyConnector(error=PtyHandshakeError(status)),
    )
    await provider.create(_spec())

    with pytest.raises(UpstreamError) as excinfo:
        await provider.open_shell("bm-abc")

    assert excinfo.value.code == code
    assert excinfo.value.details["status"] == status


async def test_open_shell_unknown_handshake_error_has_no_status() -> None:
    provider = _provider(
        SandboxdStub(),
        ws_connect=FakePtyConnector(error=RuntimeError("boom")),
    )
    await provider.create(_spec())

    with pytest.raises(UpstreamError) as excinfo:
        await provider.open_shell("bm-abc")

    assert excinfo.value.code == "sandbox.provider_error"
    assert "status" not in excinfo.value.details
    assert "RuntimeError" in excinfo.value.details["error"]


async def test_attach_shell_is_unsupported() -> None:
    provider = _provider(SandboxdStub())

    with pytest.raises(UpstreamError) as attach_exc:
        await provider.attach_shell("bm-abc", 42)

    assert attach_exc.value.code == "sandbox.provider_unsupported"
    assert attach_exc.value.details == {"provider": "sandboxd"}


def test_pty_url_uses_wss_and_escapes_id() -> None:
    provider = _provider(base_url="https://sandboxd.example")

    url = provider._pty_url(
        "sbx 1", cmd="/bin/sh -i", cwd="/workspace", cols=80, rows=24
    )

    assert url.startswith(
        "wss://sandboxd.example/v1/sandboxes/sbx%201/pty?cmd=%2Fbin%2Fsh+-i"
    )


async def test_missing_config_is_provider_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SANDBOXD_URL", raising=False)
    monkeypatch.delenv("SANDBOXD_TOKEN", raising=False)
    provider = SandboxdProvider(base_url="", token="")

    with pytest.raises(UpstreamError) as excinfo:
        await provider.create(_spec())

    assert excinfo.value.code == "sandbox.provider_unavailable"
    assert "SANDBOXD_URL" in excinfo.value.message


async def test_missing_token_is_provider_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SANDBOXD_TOKEN", raising=False)
    provider = SandboxdProvider(base_url="http://sandboxd.test", token="")

    with pytest.raises(UpstreamError) as excinfo:
        await provider.create(_spec())

    assert excinfo.value.code == "sandbox.provider_unavailable"
    assert "SANDBOXD_TOKEN" in excinfo.value.message


def test_factory_assembles_sandboxd_provider() -> None:
    settings = Settings(
        _env_file=None,
        env="dev",
        sandbox_provider="sandboxd",
        sandbox_sandboxd_url="http://sandboxd.test:8088",
        sandbox_sandboxd_token="factory-token",
        sandbox_sandboxd_image="python:3.11",
        sandbox_sandboxd_request_timeout_seconds=7.5,
        sandbox_exec_timeout_seconds=9,
        sandbox_max_output_bytes=1234,
    )

    provider = build_sandbox_provider(settings)

    assert isinstance(provider, SandboxdProvider)
    assert provider.provider == "sandboxd"
    assert provider._base_url == "http://sandboxd.test:8088"
    assert provider._token == "factory-token"
    assert provider._default_image == "python:3.11"
    assert provider._request_timeout_seconds == 7.5
    assert provider._default_exec_timeout_seconds == 9
    assert provider._max_output_bytes == 1234
