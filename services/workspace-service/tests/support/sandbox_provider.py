"""MicrosandboxProvider 测试夹具:用假 SDK 验证调用形状与异常映射。

不 import 真实 microsandbox、不启动 microVM:monkeypatch
`workspace.services.sandbox_provider._import_sdk` 返回内存假模块。
"""

from __future__ import annotations

from typing import Any

import pytest

from workspace.services import sandbox_provider
from workspace.services.sandbox_provider import SandboxSpec


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
