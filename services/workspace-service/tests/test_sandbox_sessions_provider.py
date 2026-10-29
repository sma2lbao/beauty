"""持久 shell 会话的两路 provider 实现:microsandbox(假 SDK)与 local pty/管道。"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from common.errors import UpstreamError

from tests.support.sandbox_provider import (
    FakeExecEvent,
    FakeSdkError,
    _use_fake_sdk,
)
from workspace.services import sandbox_provider as provider_module
from workspace.services.sandbox_provider import (
    LOCAL_WORKSPACE_SUBDIR,
    LocalSandboxProvider,
    MicrosandboxProvider,
    SandboxSpec,
    UnavailableSandboxProvider,
)

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="持久 shell 依赖 POSIX pty / /bin/sh",
)


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
    monkeypatch.setattr(provider_module.local, "pty", None)
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

    monkeypatch.setattr(provider_module.local, "pty", NoPty)
    provider = _local_provider(tmp_path)
    await _local_sandbox(provider)

    session = await provider.open_shell("bm-local", cwd="/workspace")
    assert session.tty is False
    await session.close()


@posix_only
async def test_local_open_shell_maps_pipe_spawn_error(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(provider_module.local, "pty", None)
    monkeypatch.setattr(
        provider_module.local, "_pipe_shell_argv", lambda: ["/nonexistent/beauty-shell"]
    )
    provider = _local_provider(tmp_path)
    await _local_sandbox(provider)

    with pytest.raises(UpstreamError) as exc:
        await provider.open_shell("bm-local", cwd="/workspace")

    assert exc.value.code == "sandbox.provider_error"
