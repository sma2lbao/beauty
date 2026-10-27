"""MicrosandboxProvider 测试夹具:用假 SDK 验证调用形状与异常映射。

不 import 真实 microsandbox、不启动 microVM:monkeypatch
`workspace.services.sandbox_provider._import_sdk` 返回内存假模块。
"""

from __future__ import annotations

from enum import Enum
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


class FakeExecEventType:
    """真 SDK 是 StrEnum;provider 只做 str() 比较,这里给等值字符串。"""

    STARTED = "started"
    STDOUT = "stdout"
    STDERR = "stderr"
    EXITED = "exited"
    FAILED = "failed"
    STDIN_ERROR = "stdin_error"


class FakeExecEvent:
    def __init__(
        self,
        event_type: str,
        data: bytes | None = None,
        code: int | None = None,
    ) -> None:
        self.event_type = event_type
        self.pid = 4242
        self.data = data
        self.code = code


class FakeStdin:
    """真 SDK 的 Stdin;provider 只把 pipe() 的结果透传给 exec_stream。"""

    def __init__(self, mode: str) -> None:
        self.mode = mode

    @classmethod
    def pipe(cls) -> FakeStdin:
        return cls("pipe")

    @classmethod
    def null(cls) -> FakeStdin:
        return cls("null")


class FakeExecSink:
    def __init__(self, sdk: FakeSdk) -> None:
        self._sdk = sdk
        self.closed = False

    async def write(self, data: bytes) -> None:
        if self.closed:
            raise FakeSdkError("sink closed")
        self._sdk.sink_writes.append(data)

    async def close(self) -> None:
        self.closed = True
        self._sdk.sink_close_calls += 1


class FakeExecHandle:
    """假 ExecHandle:事件从 sdk.shell_events 逐帧取,耗尽后返回 None(=EOF)。"""

    def __init__(self, sdk: FakeSdk, name: str) -> None:
        self._sdk = sdk
        self._name = name
        self.id = "exec-1"
        self.sink = FakeExecSink(sdk)

    def take_stdin(self) -> FakeExecSink | None:
        return None if self._sdk.take_stdin_none else self.sink

    async def recv(self) -> FakeExecEvent | None:
        if self._sdk.recv_error is not None:
            raise self._sdk.recv_error
        if self._sdk.shell_events:
            return self._sdk.shell_events.pop(0)
        return None

    async def wait(self) -> tuple[int, bool]:
        return (self._sdk.shell_wait_code, True)

    async def kill(self) -> None:
        self._sdk.handle_kill_calls += 1

    async def resize(self, rows: int, cols: int) -> None:
        self._sdk.handle_resize_calls.append((rows, cols))

    def __aiter__(self) -> FakeExecHandle:
        return self

    async def __anext__(self) -> FakeExecEvent:
        event = await self.recv()
        if event is None:
            raise StopAsyncIteration
        return event


class FsEntryKind(Enum):
    """假 SDK 的条目类型(真 SDK 是 Enum,provider 只读 .name)。"""

    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    OTHER = "other"


class FsEntry:
    """假 SDK 的目录条目;name 留空时 provider 会从 path 尾段取名。"""

    def __init__(
        self,
        path: str,
        kind: FsEntryKind = FsEntryKind.FILE,
        size: int = 0,
        modified: float | None = None,
        name: str | None = None,
    ) -> None:
        self.path = path
        self.kind = kind
        self.size = size
        self.modified = modified
        self.name = name


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

    async def list(self, path: str) -> list[FsEntry]:
        self._sdk.list_calls.append(path)
        if self._sdk.list_error is not None:
            raise self._sdk.list_error
        return list(self._sdk.list_entries.get(path, []))


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

    async def exec_stream(
        self,
        cmd: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        user: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        stdin: FakeStdin | bytes | None = None,
        tty: bool = False,
        rlimits: Any = None,
    ) -> FakeExecHandle:
        self._sdk.exec_stream_calls.append(
            {
                "name": self._name,
                "cmd": cmd,
                "args": args,
                "cwd": cwd,
                "user": user,
                "env": env,
                "timeout": timeout,
                "stdin_mode": getattr(stdin, "mode", "bytes"),
                "tty": tty,
            }
        )
        if self._sdk.exec_stream_errors:
            error = self._sdk.exec_stream_errors.pop(0)
            if error is not None:
                raise error
        if self._sdk.exec_stream_error is not None:
            raise self._sdk.exec_stream_error
        return FakeExecHandle(self._sdk, self._name)


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
    ExecEventType = FakeExecEventType
    ExecEvent = FakeExecEvent
    Stdin = FakeStdin
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
        self.list_calls: list[str] = []
        self.list_entries: dict[str, list[FsEntry]] = {}
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
        self.list_error: BaseException | None = None
        self.write_errors: list[BaseException] = []
        # 持久 shell:exec_stream 入参、事件队列、sink/handle 调用记录
        self.exec_stream_calls: list[dict[str, Any]] = []
        self.shell_events: list[FakeExecEvent] = []
        self.exec_stream_error: BaseException | None = None
        # 按调用顺序注入异常(None = 该次正常),用于覆盖 shell 候选回退
        self.exec_stream_errors: list[BaseException | None] = []
        self.recv_error: BaseException | None = None
        self.take_stdin_none = False
        self.sink_writes: list[bytes] = []
        self.sink_close_calls = 0
        self.handle_kill_calls = 0
        self.handle_resize_calls: list[tuple[int, int]] = []
        self.shell_wait_code = 0
        self.Network = FakeNetworkApi(self)
        self.Sandbox = FakeSandboxApi(self)

    def next_write_error(self) -> BaseException | None:
        return self.write_errors.pop(0) if self.write_errors else None

    def add_entries(self, path: str, *entries: FsEntry) -> None:
        """给某个目录预置子条目(list 返回固定顺序,provider 负责排序)。"""
        self.list_entries.setdefault(path, []).extend(entries)


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
