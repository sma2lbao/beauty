"""E2bSandboxProvider 适配层:控制面调用形状、异常映射与文件/pty 行为。"""

from __future__ import annotations

import shlex

import pytest
from common.errors import NotFoundError, UpstreamError

from tests.support.sandbox_provider_e2b import (
    AuthenticationException,
    CommandExitException,
    FakeE2bError,
    FakeEntry,
    FakeHandle,
    FakeResult,
    FileNotFoundException,
    SandboxNotFoundException,
    TimeoutException,
    _spec,
    _use_fake_e2b,
    _utc_now,
)
from workspace.services.sandbox_provider import E2bSandboxProvider


def _provider(**overrides: object) -> E2bSandboxProvider:
    kwargs: dict[str, object] = {
        "api_key": "e2b-test-key",
        "domain": "",
        "template": "",
    }
    kwargs.update(overrides)
    return E2bSandboxProvider(**kwargs)  # type: ignore[arg-type]


async def test_create_passes_control_plane_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use_fake_e2b(monkeypatch)
    provider = _provider()

    await provider.create(_spec(ttl_seconds=900))

    assert len(fake.AsyncSandbox.created) == 1
    call = fake.AsyncSandbox.created[0]
    assert call["template"] == "base"  # microsandbox 的 python 别名不能当模板
    assert call["timeout"] == 900
    assert call["metadata"] == {
        "beauty_sandbox_name": "bm-abc",
        "beauty_account_id": "acc-1",
    }
    assert call["allow_internet_access"] is False  # network=deny
    assert call["api_key"] == "e2b-test-key"


async def test_create_public_network_allows_internet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use_fake_e2b(monkeypatch)
    await _provider().create(_spec(network="public"))
    assert fake.AsyncSandbox.created[0]["allow_internet_access"] is True


async def test_create_template_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _use_fake_e2b(monkeypatch)
    await _provider(template="beauty-python").create(_spec(image="alpine"))
    assert fake.AsyncSandbox.created[0]["template"] == "beauty-python"

    await _provider().create(_spec(image="alpine"))
    assert fake.AsyncSandbox.created[1]["template"] == "alpine"

    await _provider().create(_spec(image="python"))
    assert fake.AsyncSandbox.created[2]["template"] == "base"


async def test_create_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _use_fake_e2b(monkeypatch)
    monkeypatch.delenv("E2B_API_KEY", raising=False)

    with pytest.raises(UpstreamError) as exc_info:
        await _provider(api_key="").create(_spec())

    assert exc_info.value.code == "sandbox.provider_unavailable"
    assert fake.AsyncSandbox.created == []


async def test_create_authentication_error_maps_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use_fake_e2b(monkeypatch)
    fake.AsyncSandbox.create_error = AuthenticationException("bad key")

    with pytest.raises(UpstreamError) as exc_info:
        await _provider().create(_spec())

    assert exc_info.value.code == "sandbox.provider_unavailable"
    assert "AuthenticationException" in exc_info.value.details["error"]


async def test_exec_joins_argv_with_shlex(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _use_fake_e2b(monkeypatch)
    provider = _provider()
    await provider.create(_spec())
    instance = fake.AsyncSandbox.sandboxes["e2b-1"]

    result = await provider.exec(
        "bm-abc", ["python3", "-c", "print('a b')"], cwd="/workspace", env={"A": "1"}
    )

    call = instance.commands.runs[0]
    assert call["cmd"] == shlex.join(["python3", "-c", "print('a b')"])
    assert call["cwd"] == "/workspace"
    assert call["envs"] == {"A": "1"}
    assert call["timeout"] == 60.0
    assert result.exit_code == 0


async def test_exec_nonzero_exit_is_returned_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use_fake_e2b(monkeypatch)
    provider = _provider()
    await provider.create(_spec())
    instance = fake.AsyncSandbox.sandboxes["e2b-1"]
    instance.commands.error = CommandExitException(
        exit_code=2, stdout="out", stderr="err"
    )

    result = await provider.exec("bm-abc", ["false"])

    assert result.exit_code == 2
    assert (result.stdout, result.stderr) == ("out", "err")


async def test_exec_timeout_maps_to_pipeline_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use_fake_e2b(monkeypatch)
    provider = _provider()
    await provider.create(_spec())
    fake.AsyncSandbox.sandboxes["e2b-1"].commands.error = TimeoutException("slow")

    with pytest.raises(UpstreamError) as exc_info:
        await provider.exec("bm-abc", ["sleep", "10"], timeout_seconds=3)

    assert exc_info.value.code == "sandbox.exec_timeout"
    assert exc_info.value.details["timeout_seconds"] == 3


async def test_exec_with_stdin_streams_then_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use_fake_e2b(monkeypatch)
    provider = _provider()
    await provider.create(_spec())
    instance = fake.AsyncSandbox.sandboxes["e2b-1"]
    handle = FakeHandle(result=FakeResult(stdout="ok"))
    instance.commands.background_handle = handle

    result = await provider.exec("bm-abc", ["python3", "-"], stdin="print(1)")

    call = instance.commands.runs[0]
    assert call["background"] is True
    assert call["stdin"] is True
    assert handle.stdin == b"print(1)"
    assert handle.stdin_closed is True
    assert result.stdout == "ok"


async def test_exec_with_stdin_returns_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use_fake_e2b(monkeypatch)
    provider = _provider()
    await provider.create(_spec())
    instance = fake.AsyncSandbox.sandboxes["e2b-1"]
    instance.commands.background_handle = FakeHandle(
        error=CommandExitException(exit_code=7, stdout="o", stderr="e")
    )

    result = await provider.exec("bm-abc", ["false"], stdin="")

    assert (result.exit_code, result.stdout, result.stderr) == (7, "o", "e")


async def test_resolve_after_restart_uses_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use_fake_e2b(monkeypatch)
    await _provider().create(_spec())

    provider = _provider()  # 进程重启:缓存为空
    result = await provider.exec("bm-abc", ["echo", "hi"])

    assert fake.AsyncSandbox.connect_calls == ["e2b-1"]
    assert result.exit_code == 0


async def test_resolve_missing_sandbox_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_fake_e2b(monkeypatch)
    provider = _provider()

    with pytest.raises(UpstreamError) as exc_info:
        await provider.exec("bm-abc", ["true"])

    assert exc_info.value.code == "sandbox.provider_error"
    assert "不存在" in exc_info.value.message


async def test_connect_not_found_maps_to_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use_fake_e2b(monkeypatch)
    provider = _provider()
    await provider.create(_spec())
    fake.AsyncSandbox.connect_error = SandboxNotFoundException("gone")

    with pytest.raises(UpstreamError) as exc_info:
        await provider.exec("bm-abc", ["true"])

    assert exc_info.value.code == "sandbox.provider_error"


async def test_write_file_creates_missing_parent_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use_fake_e2b(monkeypatch)
    provider = _provider()
    await provider.create(_spec())
    files = fake.AsyncSandbox.sandboxes["e2b-1"].files
    files.write_errors = [FakeE2bError("no such dir")]

    await provider.write_file("bm-abc", "/workspace/src/a.py", b"x=1")

    assert files.make_dirs == ["/workspace/src"]
    assert files.writes == [("/workspace/src/a.py", b"x=1")]


async def test_read_file_missing_maps_to_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use_fake_e2b(monkeypatch)
    provider = _provider()
    await provider.create(_spec())
    fake.AsyncSandbox.sandboxes["e2b-1"].files.read_error = FileNotFoundException()

    with pytest.raises(NotFoundError) as exc_info:
        await provider.read_file("bm-abc", "/workspace/none.txt")

    assert exc_info.value.code == "sandbox.file_not_found"


async def test_read_file_returns_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _use_fake_e2b(monkeypatch)
    provider = _provider()
    await provider.create(_spec())
    files = fake.AsyncSandbox.sandboxes["e2b-1"].files

    data = await provider.read_file("bm-abc", "/workspace/a.py")

    assert data == b"content"
    assert files.reads == [("/workspace/a.py", "bytes")]


async def test_list_dir_normalizes_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _use_fake_e2b(monkeypatch)
    provider = _provider()
    await provider.create(_spec())
    files = fake.AsyncSandbox.sandboxes["e2b-1"].files
    files.entries = [
        FakeEntry("/workspace/src", type="dir"),
        FakeEntry("/workspace/a.py", type="file", size=12, modified_time=_utc_now()),
        FakeEntry("rel.txt", type="file", size=1),
        FakeEntry("/workspace/link", type="symlink", size=3),
        FakeEntry("/workspace/odd", type="socket", size=0),
    ]

    entries = await provider.list_dir("bm-abc", "/workspace", depth=2)

    assert files.lists == [("/workspace", 2)]
    by_path = {entry.path: entry for entry in entries}
    assert by_path["/workspace/a.py"].kind == "file"
    assert by_path["/workspace/a.py"].size == 12
    assert by_path["/workspace/a.py"].modified == _utc_now().timestamp()
    assert by_path["/workspace/src"].kind == "dir"
    assert by_path["/workspace/src"].size == 0
    assert by_path["/workspace/rel.txt"].path == "/workspace/rel.txt"
    assert by_path["/workspace/link"].kind == "symlink"
    assert by_path["/workspace/odd"].kind == "other"
    assert [entry.path for entry in entries] == sorted(by_path)


async def test_open_shell_reads_writes_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use_fake_e2b(monkeypatch)
    provider = _provider()
    await provider.create(_spec())
    pty = fake.AsyncSandbox.sandboxes["e2b-1"].pty

    session = await provider.open_shell("bm-abc", cols=100, rows=40)

    created = pty.created[0]
    assert (created["size"].cols, created["size"].rows) == (100, 40)
    assert created["timeout"] == 0  # 长驻 shell 不吃流空闲超时

    await pty.push(b"hello")
    assert await session.read() == b"hello"

    await session.write(b"ls\n")
    assert pty.sent == [(4242, b"ls\n")]  # FakeHandle.pid

    await session.close()
    assert pty.killed == [4242]  # FakeHandle.pid
    assert await session.read() is None


async def test_destroy_kills_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _use_fake_e2b(monkeypatch)
    provider = _provider()
    await provider.create(_spec())

    await provider.destroy("bm-abc")
    assert fake.AsyncSandbox.kill_calls == ["e2b-1"]

    fake.AsyncSandbox.infos = []  # 控制面已无该沙箱
    await provider.destroy("bm-abc")  # 幂等:不抛
    assert fake.AsyncSandbox.kill_calls == ["e2b-1"]
