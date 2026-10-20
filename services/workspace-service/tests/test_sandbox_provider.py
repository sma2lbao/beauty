"""MicrosandboxProvider 适配层:用假 SDK 验证调用形状与异常映射。

不 import 真实 microsandbox、不启动 microVM:monkeypatch
`workspace.services.sandbox_provider._import_sdk` 返回内存假模块,
从而覆盖 create/exec/fs/destroy 传参与错误收口。
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from common.errors import NotFoundError, UpstreamError

from workspace.core.config import Settings
from workspace.services import sandbox_provider
from workspace.services.sandbox_provider import (
    MicrosandboxProvider,
    SandboxSpec,
    build_sandbox_provider,
)


class FakeSdkError(Exception):
    """假 SDK 异常基类。"""


class SandboxNotFoundError(FakeSdkError):
    pass


class PathNotFoundError(FakeSdkError):
    pass


class RuntimeNotInstalledError(FakeSdkError):
    pass


class RuntimeIncompleteError(FakeSdkError):
    pass


class ExecTimeoutError(FakeSdkError):
    pass


class FakeExecOutput:
    def __init__(self, stdout: str, stderr: str, exit_code: int) -> None:
        self.stdout_text = stdout
        self.stderr_text = stderr
        self.exit_code = exit_code


class FakeFs:
    def __init__(self, sdk: FakeSdk) -> None:
        self._sdk = sdk

    async def write(self, path: str, data: bytes) -> None:
        self._sdk.write_calls.append((path, data))
        error = self._sdk.next_write_error()
        if error is not None:
            raise error
        self._sdk.files[path] = data

    async def read(self, path: str) -> bytes:
        if self._sdk.read_error is not None:
            raise self._sdk.read_error
        return self._sdk.files[path]

    async def mkdir(self, path: str) -> None:
        self._sdk.mkdir_calls.append(path)


class FakeSandbox:
    def __init__(self, sdk: FakeSdk, name: str) -> None:
        self._sdk = sdk
        self._name = name
        self.fs = FakeFs(sdk)

    async def detach(self) -> None:
        self._sdk.detach_calls.append(self._name)
        if self._sdk.detach_error is not None:
            raise self._sdk.detach_error

    async def stop(self) -> None:
        self._sdk.stop_calls.append(self._name)
        if self._sdk.stop_error is not None:
            raise self._sdk.stop_error

    async def exec(
        self,
        cmd: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        stdin: bytes | None = None,
        timeout: float | None = None,
    ) -> FakeExecOutput:
        self._sdk.exec_calls.append(
            {
                "name": self._name,
                "cmd": cmd,
                "args": args,
                "cwd": cwd,
                "env": env,
                "stdin": stdin,
                "timeout": timeout,
            }
        )
        if self._sdk.exec_error is not None:
            raise self._sdk.exec_error
        return FakeExecOutput(
            stdout=self._sdk.stdout,
            stderr=self._sdk.stderr,
            exit_code=self._sdk.exit_code,
        )


class FakeHandle:
    def __init__(self, sdk: FakeSdk, name: str) -> None:
        self._sdk = sdk
        self._name = name

    async def connect(self) -> FakeSandbox:
        return FakeSandbox(self._sdk, self._name)

    async def destroy(self, *, force: bool = False) -> None:
        self._sdk.destroy_calls.append((self._name, force))
        if self._sdk.destroy_error is not None:
            raise self._sdk.destroy_error


class FakeSandboxApi:
    def __init__(self, sdk: FakeSdk) -> None:
        self._sdk = sdk

    async def create(self, name: str, **kwargs: Any) -> FakeSandbox:
        self._sdk.created.append({"name": name, **kwargs})
        if self._sdk.create_error is not None:
            raise self._sdk.create_error
        return FakeSandbox(self._sdk, name)

    async def get(self, name: str) -> FakeHandle:
        self._sdk.get_calls.append(name)
        if self._sdk.get_error is not None:
            raise self._sdk.get_error
        return FakeHandle(self._sdk, name)

    async def remove(self, name: str) -> None:
        self._sdk.removed.append(name)
        if self._sdk.remove_error is not None:
            raise self._sdk.remove_error


class FakeNetworkProfile:
    PUBLIC = "public"


class FakeNetworkApi:
    def __init__(self, sdk: FakeSdk) -> None:
        self._sdk = sdk

    def none(self) -> object:
        return self._sdk.network_none

    def allow_all(self) -> object:
        return self._sdk.network_allow_all

    def from_profiles(self, *profiles: object) -> object:
        self._sdk.profile_calls.append(profiles)
        return self._sdk.network_public


class FakeSdk:
    """最小假 SDK:只实现 provider 用到的属性,记录所有调用。"""

    SandboxNotFoundError = SandboxNotFoundError
    PathNotFoundError = PathNotFoundError
    RuntimeNotInstalledError = RuntimeNotInstalledError
    RuntimeIncompleteError = RuntimeIncompleteError
    ExecTimeoutError = ExecTimeoutError
    NetworkProfile = FakeNetworkProfile

    def __init__(self) -> None:
        self.network_none = object()
        self.network_allow_all = object()
        self.network_public = object()
        self.profile_calls: list[tuple[object, ...]] = []
        self.created: list[dict[str, Any]] = []
        self.exec_calls: list[dict[str, Any]] = []
        self.get_calls: list[str] = []
        self.detach_calls: list[str] = []
        self.stop_calls: list[str] = []
        self.destroy_calls: list[tuple[str, bool]] = []
        self.removed: list[str] = []
        self.write_calls: list[tuple[str, bytes]] = []
        self.mkdir_calls: list[str] = []
        self.files: dict[str, bytes] = {}
        self.stdout = "ok\n"
        self.stderr = ""
        self.exit_code = 0
        self.exec_error: BaseException | None = None
        self.create_error: BaseException | None = None
        self.detach_error: BaseException | None = None
        self.stop_error: BaseException | None = None
        self.get_error: BaseException | None = None
        self.destroy_error: BaseException | None = None
        self.remove_error: BaseException | None = None
        self.read_error: BaseException | None = None
        self.write_errors: list[BaseException] = []
        self.Network = FakeNetworkApi(self)
        self.Sandbox = FakeSandboxApi(self)

    def next_write_error(self) -> BaseException | None:
        return self.write_errors.pop(0) if self.write_errors else None


def _use_fake_sdk(monkeypatch: pytest.MonkeyPatch) -> FakeSdk:
    sdk = FakeSdk()
    monkeypatch.setattr(sandbox_provider, "_import_sdk", lambda: sdk)
    return sdk


def _spec(**overrides: Any) -> SandboxSpec:
    defaults: dict[str, Any] = {
        "tenant_id": "t-1",
        "name": "bm-abc",
        "kind": "generic",
        "image": "python",
        "cpus": 1.0,
        "memory_mb": 512,
        "network": "deny",
        "ttl_seconds": 1800,
    }
    defaults.update(overrides)
    return SandboxSpec(**defaults)


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


async def test_read_file_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.files["/workspace/a.txt"] = b"hello"

    data = await MicrosandboxProvider().read_file("bm-abc", "/workspace/a.txt")

    assert data == b"hello"


async def test_read_file_missing_maps_to_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.read_error = PathNotFoundError("missing")

    with pytest.raises(NotFoundError) as exc_info:
        await MicrosandboxProvider().read_file("bm-abc", "/workspace/a.txt")

    assert exc_info.value.code == "sandbox.file_not_found"
    assert exc_info.value.status == 404


async def test_write_file_retries_after_mkdir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.write_errors = [OSError("no such directory")]

    await MicrosandboxProvider().write_file("bm-abc", "/workspace/sub/a.txt", b"data")

    assert sdk.mkdir_calls == ["/workspace/sub"]
    assert [path for path, _ in sdk.write_calls] == ["/workspace/sub/a.txt"] * 2
    assert sdk.files["/workspace/sub/a.txt"] == b"data"


async def test_write_file_second_failure_maps_to_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.write_errors = [OSError("first"), OSError("second")]

    with pytest.raises(UpstreamError) as exc_info:
        await MicrosandboxProvider().write_file(
            "bm-abc", "/workspace/sub/a.txt", b"data"
        )

    assert exc_info.value.code == "sandbox.provider_error"
    assert "OSError" in exc_info.value.details["error"]


async def test_destroy_calls_destroy_and_remove(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)

    await MicrosandboxProvider().destroy("bm-abc")

    assert sdk.destroy_calls == [("bm-abc", True)]
    assert sdk.removed == ["bm-abc"]


async def test_destroy_is_idempotent_when_sandbox_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.get_error = SandboxNotFoundError("gone")

    await MicrosandboxProvider().destroy("bm-abc")

    assert sdk.destroy_calls == []
    assert sdk.removed == []


async def test_destroy_suppresses_remove_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.remove_error = SandboxNotFoundError("already gone")

    await MicrosandboxProvider().destroy("bm-abc")

    assert sdk.destroy_calls == [("bm-abc", True)]
    assert sdk.removed == ["bm-abc"]


@pytest.mark.parametrize(
    "error_class", [RuntimeNotInstalledError, RuntimeIncompleteError]
)
async def test_create_runtime_errors_map_to_unavailable(
    monkeypatch: pytest.MonkeyPatch, error_class: type[FakeSdkError]
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.create_error = error_class("runtime missing")

    with pytest.raises(UpstreamError) as exc_info:
        await MicrosandboxProvider().create(_spec())

    assert exc_info.value.code == "sandbox.provider_unavailable"


def test_build_unknown_provider_raises() -> None:
    settings = Settings(env="dev", sandbox_provider="bogus")

    with pytest.raises(UpstreamError) as exc_info:
        build_sandbox_provider(settings)

    assert exc_info.value.code == "sandbox.provider_unknown"


def test_msb_home_set_before_sdk_import(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MSB_HOME", raising=False)

    MicrosandboxProvider(msb_home="/tmp/ws-msb-home")

    assert os.environ["MSB_HOME"] == "/tmp/ws-msb-home"


def test_msb_home_keeps_existing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MSB_HOME", "/existing")

    MicrosandboxProvider(msb_home="/other")

    assert os.environ["MSB_HOME"] == "/existing"


def test_empty_msb_home_leaves_env_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MSB_HOME", raising=False)

    MicrosandboxProvider()

    assert "MSB_HOME" not in os.environ
