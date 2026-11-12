"""E2bSandboxProvider 测试夹具:用假 SDK 验证调用形状与异常映射。

不 import 真实 e2b、不发任何网络请求:monkeypatch
`workspace.services.sandbox_provider.e2b_sandbox._import_e2b` 返回内存假模块。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from workspace.services.sandbox_provider import SandboxSpec
from workspace.services.sandbox_provider import e2b_sandbox as provider_impl


class FakeE2bError(Exception):
    """假 SDK 异常基类。"""


class AuthenticationException(FakeE2bError):
    pass


class SandboxNotFoundException(FakeE2bError):
    pass


class FileNotFoundException(FakeE2bError):
    pass


class TimeoutException(FakeE2bError):
    pass


class CommandExitException(FakeE2bError):
    def __init__(
        self,
        exit_code: int = 1,
        stdout: str = "",
        stderr: str = "",
        error: str | None = None,
    ) -> None:
        super().__init__(f"exit {exit_code}")
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.error = error


class FakePtySize:
    def __init__(self, cols: int = 80, rows: int = 24) -> None:
        self.cols = cols
        self.rows = rows


class FakeResult:
    def __init__(self, stdout: str = "", stderr: str = "", exit_code: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.error = None


class FakeEntry:
    def __init__(
        self,
        path: str,
        type: str = "file",
        size: int = 0,
        modified_time: datetime | None = None,
    ) -> None:
        self.path = path
        self.type = type
        self.size = size
        self.modified_time = modified_time


class FakeFiles:
    def __init__(self) -> None:
        self.writes: list[tuple[str, bytes]] = []
        self.reads: list[tuple[str, str]] = []
        self.lists: list[tuple[str, int]] = []
        self.make_dirs: list[str] = []
        self.entries: list[FakeEntry] = []
        self.read_error: BaseException | None = None
        self.write_errors: list[BaseException] = []
        self.list_error: BaseException | None = None

    async def write(self, path: str, data: bytes) -> None:
        if self.write_errors:
            raise self.write_errors.pop(0)
        self.writes.append((path, bytes(data)))

    async def read(self, path: str, format: str = "text") -> Any:  # noqa: A002
        self.reads.append((path, format))
        if self.read_error is not None:
            raise self.read_error
        return b"content"

    async def list(self, path: str, depth: int = 1) -> list[FakeEntry]:
        self.lists.append((path, depth))
        if self.list_error is not None:
            raise self.list_error
        return list(self.entries)

    async def make_dir(self, path: str) -> None:
        self.make_dirs.append(path)


class FakePty:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.sent: list[tuple[int, bytes]] = []
        self.killed: list[int] = []
        self._on_data: Any = None

    async def create(
        self,
        size: FakePtySize,
        on_data: Any = None,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> FakeHandle:
        self.created.append(
            {
                "size": size,
                "on_data": on_data,
                "cwd": cwd,
                "envs": envs,
                "timeout": timeout,
            }
        )
        self._on_data = on_data
        return FakeHandle()

    async def push(self, data: bytes) -> None:
        await self._on_data(data)

    async def send_stdin(self, pid: int, data: bytes) -> None:
        self.sent.append((pid, bytes(data)))

    async def kill(self, pid: int) -> None:
        self.killed.append(pid)


class FakeHandle:
    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self.pid = 4242
        self._result = result if result is not None else FakeResult()
        self._error = error
        self._done = asyncio.Event()
        self.killed = 0

    def release(self) -> None:
        self._done.set()

    async def wait(self) -> Any:
        await self._done.wait()
        if self._error is not None:
            raise self._error
        return self._result

    async def send_stdin(self, data: bytes) -> None:
        self.stdin = data

    async def close_stdin(self) -> None:
        self.stdin_closed = True

    async def kill(self) -> None:
        self.killed += 1
        self.release()


class FakeCommands:
    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []
        self.result = FakeResult()
        self.error: BaseException | None = None
        self.background_handle = FakeHandle()

    async def run(self, cmd: str, **kwargs: Any) -> Any:
        self.runs.append({"cmd": cmd, **kwargs})
        if kwargs.get("background"):
            self.background_handle.release()
            return self.background_handle
        if self.error is not None:
            raise self.error
        return self.result


class FakeSandboxInstance:
    def __init__(self, sandbox_id: str) -> None:
        self.sandbox_id = sandbox_id
        self.files = FakeFiles()
        self.pty = FakePty()
        self.commands = FakeCommands()
        self.killed = 0

    async def kill(self) -> bool:
        self.killed += 1
        return True


class FakeSandboxInfo:
    def __init__(self, sandbox_id: str, metadata: dict[str, str]) -> None:
        self.sandbox_id = sandbox_id
        self.metadata = metadata


class FakePaginator:
    def __init__(self, pages: list[list[FakeSandboxInfo]]) -> None:
        self._pages = pages
        self.has_next = bool(pages)

    async def next_items(self) -> list[FakeSandboxInfo]:
        items = self._pages.pop(0)
        self.has_next = bool(self._pages)
        return items


class SandboxQuery:
    def __init__(self, metadata: dict[str, str] | None = None) -> None:
        self.metadata = metadata or {}


class FakeAsyncSandbox:
    """假 `e2b.AsyncSandbox`:create/connect/list/kill 都是类方法变体。"""

    created: list[dict[str, Any]] = []
    sandboxes: dict[str, FakeSandboxInstance] = {}
    infos: list[FakeSandboxInfo] = []
    connect_calls: list[str] = []
    connect_error: BaseException | None = None
    kill_calls: list[str] = []
    kill_error: BaseException | None = None
    create_error: BaseException | None = None
    list_error: BaseException | None = None

    @classmethod
    async def create(cls, **kwargs: Any) -> FakeSandboxInstance:
        if cls.create_error is not None:
            raise cls.create_error
        cls.created.append(kwargs)
        sandbox_id = f"e2b-{len(cls.created)}"
        instance = FakeSandboxInstance(sandbox_id)
        cls.sandboxes[sandbox_id] = instance
        cls.infos.append(FakeSandboxInfo(sandbox_id, kwargs.get("metadata", {})))
        return instance

    @classmethod
    async def connect(cls, sandbox_id: str, **kwargs: Any) -> FakeSandboxInstance:
        cls.connect_calls.append(sandbox_id)
        if cls.connect_error is not None:
            raise cls.connect_error
        if sandbox_id not in cls.sandboxes:
            raise SandboxNotFoundException(sandbox_id)
        return cls.sandboxes[sandbox_id]

    @classmethod
    def list(cls, query: SandboxQuery | None = None, **kwargs: Any) -> FakePaginator:
        if cls.list_error is not None:
            raise cls.list_error
        metadata = (query.metadata if query is not None else None) or {}
        matched = [
            info
            for info in cls.infos
            if all(
                str(info.metadata.get(key, "")) == value
                for key, value in metadata.items()
            )
        ]
        return FakePaginator([matched])

    @classmethod
    async def kill(cls, sandbox_id: str, **kwargs: Any) -> bool:
        cls.kill_calls.append(sandbox_id)
        if cls.kill_error is not None:
            raise cls.kill_error
        return True


class FakeE2bModule:
    AsyncSandbox = FakeAsyncSandbox
    SandboxQuery = SandboxQuery
    PtySize = FakePtySize
    AuthenticationException = AuthenticationException
    SandboxNotFoundException = SandboxNotFoundException
    FileNotFoundException = FileNotFoundException
    TimeoutException = TimeoutException
    CommandExitException = CommandExitException


def _reset_fake() -> None:
    FakeAsyncSandbox.created = []
    FakeAsyncSandbox.sandboxes = {}
    FakeAsyncSandbox.infos = []
    FakeAsyncSandbox.connect_calls = []
    FakeAsyncSandbox.connect_error = None
    FakeAsyncSandbox.kill_calls = []
    FakeAsyncSandbox.kill_error = None
    FakeAsyncSandbox.create_error = None
    FakeAsyncSandbox.list_error = None


def _use_fake_e2b(monkeypatch: pytest.MonkeyPatch) -> FakeE2bModule:
    _reset_fake()
    fake = FakeE2bModule()
    fake_module = fake  # 真实 SDK 是模块,假对象只需属性形状一致
    monkeypatch.setattr(provider_impl, "_import_e2b", lambda: fake_module)
    return fake


def _spec(**overrides: Any) -> SandboxSpec:
    base: dict[str, Any] = {
        "account_id": "acc-1",
        "name": "bm-abc",
        "kind": "python",
        "image": "python",
        "cpus": 1.0,
        "memory_mb": 512,
        "network": "deny",
        "ttl_seconds": 900,
    }
    base.update(overrides)
    return SandboxSpec(**base)


def _utc_now() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)
