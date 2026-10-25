"""MicrosandboxProvider 适配层:create / exec 的调用形状、截断与错误收口。"""

from __future__ import annotations

import pytest
from common.errors import UpstreamError

from tests.support.sandbox_provider import (
    ExecTimeoutError,
    FakeNetworkProfile,
    _spec,
    _use_fake_sdk,
)
from workspace.services.sandbox_provider import MicrosandboxProvider


async def test_create_passes_sdk_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    provider = MicrosandboxProvider()

    await provider.create(
        _spec(cpus=1.6, memory_mb=768, image="alpine", ttl_seconds=900)
    )

    assert len(sdk.created) == 1
    call = sdk.created[0]
    assert call["name"] == "bm-abc"
    assert call["image"] == "alpine"
    assert call["cpus"] == 2  # 1.6 → int(round()) = 2,SDK 只收 int
    assert isinstance(call["cpus"], int)
    assert call["memory"] == 768
    assert call["network"] is sdk.network_none
    assert call["detached"] is True
    assert call["max_duration"] == 900.0
    assert call["idle_timeout"] == 900.0


async def test_create_public_network_is_public_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """public 只能放公网:不能用 allow_all(会连宿主/私网),必须走 PUBLIC profile。"""
    sdk = _use_fake_sdk(monkeypatch)
    await MicrosandboxProvider().create(_spec(network="public"))
    assert sdk.created[0]["network"] is sdk.network_public
    assert sdk.profile_calls == [(FakeNetworkProfile.PUBLIC,)]


async def test_create_detaches_returned_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create 的句柄仍 owns_lifecycle,必须 detach 才能活过进程/句柄释放。"""
    sdk = _use_fake_sdk(monkeypatch)

    await MicrosandboxProvider().create(_spec())

    assert len(sdk.created) == 1
    assert sdk.detach_calls == ["bm-abc"]
    assert sdk.stop_calls == []


async def test_create_detach_failure_cleans_up_and_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """detach 失败不能算创建成功:best-effort 回收并抛 provider_error。"""
    sdk = _use_fake_sdk(monkeypatch)
    sdk.detach_error = RuntimeError("detach boom")

    with pytest.raises(UpstreamError) as exc_info:
        await MicrosandboxProvider().create(_spec())

    assert exc_info.value.code == "sandbox.provider_error"
    assert "RuntimeError" in exc_info.value.details["error"]
    assert sdk.stop_calls == ["bm-abc"]
    assert sdk.removed == ["bm-abc"]


async def test_exec_passes_argv_and_default_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.stdout = "hi\n"
    sdk.exit_code = 3
    provider = MicrosandboxProvider(default_exec_timeout_seconds=45)

    result = await provider.exec(
        "bm-abc",
        ["python3", "-c", "print(1)"],
        cwd="/workspace",
        env={"A": "B"},
        stdin="input",
        timeout_seconds=None,
    )

    call = sdk.exec_calls[0]
    assert call["cmd"] == "python3"
    assert call["args"] == ["-c", "print(1)"]
    assert call["cwd"] == "/workspace"
    assert call["env"] == {"A": "B"}
    assert call["stdin"] == b"input"
    assert call["timeout"] == 45.0
    assert result.exit_code == 3
    assert result.stdout == "hi\n"
    assert result.stderr == ""
    assert result.truncated is False
    assert result.duration_ms >= 0

    await provider.exec("bm-abc", ["true"], timeout_seconds=3)
    assert sdk.exec_calls[-1]["timeout"] == 3.0
    assert sdk.exec_calls[-1]["stdin"] is None


async def test_exec_truncates_oversized_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.stdout = "x" * 100
    sdk.stderr = "y" * 100
    provider = MicrosandboxProvider(max_output_bytes=10)

    result = await provider.exec("bm-abc", ["true"])

    assert result.stdout == "x" * 10
    assert result.stderr == "y" * 10
    assert result.truncated is True


async def test_exec_timeout_maps_to_upstream_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.exec_error = ExecTimeoutError("timed out")
    provider = MicrosandboxProvider()

    with pytest.raises(UpstreamError) as exc_info:
        await provider.exec("bm-abc", ["sleep", "10"])

    assert exc_info.value.code == "sandbox.exec_timeout"
    assert exc_info.value.status == 502


async def test_exec_other_error_maps_to_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.exec_error = RuntimeError("boom")
    provider = MicrosandboxProvider()

    with pytest.raises(UpstreamError) as exc_info:
        await provider.exec("bm-abc", ["true"])

    assert exc_info.value.code == "sandbox.provider_error"
    assert "RuntimeError" in exc_info.value.details["error"]
