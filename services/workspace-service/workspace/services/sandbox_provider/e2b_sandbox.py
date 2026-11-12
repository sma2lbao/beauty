"""E2B 云沙箱实现:microVM 跑在 E2B 控制面,支持官方云与兼容 E2B 的厂商。

与 `microsandbox` provider 的差异:

- 沙箱不在宿主运行,SDK 通过 HTTPS/envd 远程操作(异步 `AsyncSandbox`);
- `exec` 由 SDK 拼成 `/bin/bash -lc <cmd>`,argv 先经 `shlex.join` 转义;
- CPU/内存由 E2B 模板决定,`SandboxSpec.cpus/memory_mb` 仅记录、不下发;
- 名称 → `sandbox_id` 映射缓存在进程内,进程重启后用 metadata 过滤从控制面
  查回,保证 exec/destroy 在重启后仍可用;
- 持久 shell 用 `sandbox.pty`(真 PTY),输出经回调进内存队列。

兼容厂商(如阿里云 Agent Sandbox 极速版)只换 `E2B_API_KEY`/`E2B_DOMAIN`
即可,代码路径完全一致。

SDK 惰性导入:测试 monkeypatch 的正是本模块的 `_import_e2b`。
"""

from __future__ import annotations

import asyncio
import os
import shlex
import time
from contextlib import suppress
from datetime import datetime
from pathlib import PurePosixPath
from types import ModuleType
from typing import Any

from common.errors import AppError, NotFoundError, UpstreamError

from .protocols import (
    DEFAULT_EXEC_TIMEOUT_SECONDS,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_SHELL_COLS,
    DEFAULT_SHELL_ROWS,
    ExecResult,
    FileEntry,
    SandboxSpec,
    ShellSession,
)
from .utils import _error_details, _truncate_text

# 沙箱名称/账号写进 E2B metadata:重启后按名字查回 sandbox_id 的唯一依据。
_META_NAME = "beauty_sandbox_name"
_META_ACCOUNT = "beauty_account_id"
# 控制面列沙箱时的每页大小与最多翻页数(按 metadata 精确过滤,通常一页命中)
_LIST_PAGE_SIZE = 50
_LIST_MAX_PAGES = 10
_ENTRY_KINDS = {"file": "file", "dir": "dir", "symlink": "symlink"}


def _import_e2b() -> ModuleType:
    """惰性导入 e2b:未安装时给出明确的 502 错误码。"""
    try:
        import e2b
    except ImportError as exc:  # pragma: no cover - 依赖已声明,仅防御性兜底
        raise UpstreamError(
            "sandbox.provider_unavailable",
            "e2b SDK 未安装,无法提供 E2B 云沙箱运行时",
        ) from exc
    return e2b


def _entry_path(entry: Any, parent: str) -> str:
    """条目在沙箱内的绝对路径;SDK 偶尔只回相对路径,按列举目录归一。"""
    raw = str(getattr(entry, "path", "") or "")
    if not raw or ".." in PurePosixPath(raw).parts:
        return ""
    candidate = raw if raw.startswith("/") else f"{parent.rstrip('/')}/{raw}"
    normalized = PurePosixPath(candidate).as_posix()
    return normalized if normalized.startswith("/") else ""


def _entry_kind(entry: Any) -> str:
    """FileType 枚举/字符串 → 契约 kind;未知取值一律 other(不猜)。"""
    raw = getattr(entry, "type", None)
    key = str(getattr(raw, "value", raw) or "").lower()
    return _ENTRY_KINDS.get(key, "other")


def _entry_size(entry: Any) -> int:
    return max(0, int(getattr(entry, "size", 0) or 0))


def _entry_modified(entry: Any) -> float | None:
    value = getattr(entry, "modified_time", None)
    if isinstance(value, datetime):
        return value.timestamp()
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pty_size(sdk: ModuleType, cols: int, rows: int) -> Any:
    """PtySize 不在 e2b 顶层导出;测试假模块可直接提供 `PtySize`。"""
    size_cls = getattr(sdk, "PtySize", None)
    if size_cls is None:  # pragma: no cover - 真 SDK 的兜底路径
        from e2b.sandbox.commands.command_handle import PtySize as size_cls
    return size_cls(cols=cols, rows=rows)


class E2bShellSession:
    """`sandbox.pty` 上的持久交互式 shell(真 TTY)。

    输出由 pty 的 on_data 回调写入 `asyncio.Queue`;会话结束时 watcher 塞
    `None` 作为 EOF 标记,读端据此返回 None。
    """

    tty = True

    def __init__(
        self,
        sandbox: Any,
        handle: Any,
        *,
        name: str,
        queue: asyncio.Queue[bytes | None],
    ) -> None:
        self._sandbox = sandbox
        self._handle = handle
        self._name = name
        self._queue = queue
        self._closed = False
        self._eof = False
        self._exit_code: int | None = None
        self._watcher = asyncio.create_task(self._watch())

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    async def _watch(self) -> None:
        """等 pty 进程退出;正常/异常退出都只记录退出码,不向上抛。"""
        try:
            result = await self._handle.wait()
            code = getattr(result, "exit_code", None)
            if code is not None:
                self._exit_code = int(code)
        except BaseException as exc:  # noqa: BLE001 - 退出码是可选信息
            code = getattr(exc, "exit_code", None)
            if code is not None:
                self._exit_code = int(code)
        finally:
            self._eof = True
            with suppress(Exception):
                self._queue.put_nowait(None)

    async def read(self) -> bytes | None:
        if self._eof and self._queue.empty():
            return None
        chunk = await self._queue.get()
        if chunk is None:
            self._eof = True
            return None
        return chunk

    async def write(self, data: bytes) -> None:
        if self._closed or self._eof:
            raise UpstreamError("sandbox.session_closed", "shell 会话已结束,无法再写入")
        try:
            await self._sandbox.pty.send_stdin(self._handle.pid, data)
        except Exception as exc:
            raise UpstreamError(
                "sandbox.session_closed",
                "shell 会话已结束,无法再写入",
                details=_error_details(self._name, exc),
            ) from exc

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._eof = True
        with suppress(Exception):
            await self._sandbox.pty.kill(self._handle.pid)
        self._watcher.cancel()
        with suppress(BaseException):
            await self._watcher
        with suppress(Exception):
            self._queue.put_nowait(None)


class E2bSandboxProvider:
    """E2B 0.6+/2.x 实现:控制面创建沙箱,按 metadata 名字查回执行。"""

    provider = "e2b"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        domain: str | None = None,
        template: str = "",
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        default_exec_timeout_seconds: int = DEFAULT_EXEC_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key or os.environ.get("E2B_API_KEY") or ""
        self._domain = domain or os.environ.get("E2B_DOMAIN") or ""
        self._template = template.strip()
        self._max_output_bytes = max_output_bytes
        self._default_exec_timeout_seconds = default_exec_timeout_seconds
        # name → sandbox_id;进程重启后由 _resolve 从控制面查回
        self._ids: dict[str, str] = {}

    def _api_opts(self) -> dict[str, Any]:
        opts: dict[str, Any] = {}
        if self._api_key:
            opts["api_key"] = self._api_key
        if self._domain:
            opts["domain"] = self._domain
        return opts

    def _require_key(self) -> None:
        if self._api_key:
            return
        raise UpstreamError(
            "sandbox.provider_unavailable",
            "未配置 E2B API Key:设置 WORKSPACE__SANDBOX_E2B_API_KEY 或 E2B_API_KEY",
            details={"provider": "e2b"},
        )

    def _template_for(self, spec: SandboxSpec) -> str:
        """模板优先级:显式配置 > 请求里非 microsandbox 别名的 image > base。"""
        if self._template:
            return self._template
        image = (spec.image or "").strip()
        if image and image != "python":
            return image
        return "base"

    async def create(self, spec: SandboxSpec) -> None:
        self._require_key()
        sdk = _import_e2b()
        metadata = {_META_NAME: spec.name, _META_ACCOUNT: spec.account_id}
        try:
            sandbox = await sdk.AsyncSandbox.create(
                template=self._template_for(spec),
                # E2B 的 timeout 是控制面侧的绝对寿命:沙箱到点自动回收,
                # 与业务行的 expires_at 对齐(回收器仍会提前 destroy)。
                timeout=int(spec.ttl_seconds),
                metadata=metadata,
                allow_internet_access=spec.network == "public",
                **self._api_opts(),
            )
        except sdk.AuthenticationException as exc:
            raise UpstreamError(
                "sandbox.provider_unavailable",
                "E2B 鉴权失败:检查 API Key 与控制面域名",
                details=_error_details(spec.name, exc),
            ) from exc
        except Exception as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "E2B 创建沙箱失败",
                details=_error_details(spec.name, exc),
            ) from exc
        sandbox_id = str(getattr(sandbox, "sandbox_id", "") or "")
        if not sandbox_id:
            raise UpstreamError(
                "sandbox.provider_error",
                "E2B 创建沙箱未返回 sandbox_id",
                details={"sandbox_name": spec.name},
            )
        self._ids[spec.name] = sandbox_id

    async def _resolve(self, name: str) -> Any:
        """取回可执行沙箱实例:先查进程缓存,再按 metadata 从控制面查回。"""
        self._require_key()
        sdk = _import_e2b()
        sandbox_id = self._ids.get(name)
        if sandbox_id is None:
            sandbox_id = await self._find_sandbox_id(sdk, name)
            self._ids[name] = sandbox_id
        try:
            return await sdk.AsyncSandbox.connect(sandbox_id, **self._api_opts())
        except sdk.SandboxNotFoundException as exc:
            self._ids.pop(name, None)
            raise UpstreamError(
                "sandbox.provider_error",
                "沙箱运行时实例不存在(可能已被回收)",
                details={"sandbox_name": name},
            ) from exc
        except Exception as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "连接 E2B 沙箱失败",
                details=_error_details(name, exc),
            ) from exc

    async def _find_sandbox_id(self, sdk: ModuleType, name: str) -> str:
        """按 metadata 名字翻页查回 sandbox_id;查不到按实例不存在处理。"""
        paginator = sdk.AsyncSandbox.list(
            query=sdk.SandboxQuery(metadata={_META_NAME: name}),
            limit=_LIST_PAGE_SIZE,
            **self._api_opts(),
        )
        pages = 0
        while paginator.has_next and pages < _LIST_MAX_PAGES:
            pages += 1
            try:
                infos = await paginator.next_items()
            except Exception as exc:
                raise UpstreamError(
                    "sandbox.provider_error",
                    "列举 E2B 沙箱失败",
                    details=_error_details(name, exc),
                ) from exc
            for info in infos:
                metadata = getattr(info, "metadata", None) or {}
                if str(metadata.get(_META_NAME, "")) != name:
                    continue
                sandbox_id = str(getattr(info, "sandbox_id", "") or "")
                if sandbox_id:
                    return sandbox_id
        raise UpstreamError(
            "sandbox.provider_error",
            "沙箱运行时实例不存在(可能已被回收)",
            details={"sandbox_name": name},
        )

    async def exec(
        self,
        name: str,
        argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> ExecResult:
        sdk = _import_e2b()
        sandbox = await self._resolve(name)
        timeout = float(
            timeout_seconds
            if timeout_seconds is not None
            else self._default_exec_timeout_seconds
        )
        command = shlex.join(argv)
        started = time.monotonic()
        try:
            if stdin is None:
                result = await sandbox.commands.run(
                    command,
                    cwd=cwd or None,
                    envs=env or None,
                    timeout=timeout,
                )
                exit_code = int(result.exit_code)
                stdout_text = result.stdout or ""
                stderr_text = result.stderr or ""
            else:
                exit_code, stdout_text, stderr_text = await self._run_with_stdin(
                    sandbox, command, stdin, cwd=cwd, env=env, timeout=timeout
                )
        except sdk.TimeoutException as exc:
            raise UpstreamError(
                "sandbox.exec_timeout",
                f"沙箱命令执行超时({timeout:.0f} 秒)",
                details={"sandbox_name": name, "timeout_seconds": int(timeout)},
            ) from exc
        except sdk.CommandExitException as exc:
            exit_code = int(exc.exit_code)
            stdout_text = exc.stdout or ""
            stderr_text = exc.stderr or ""
        except Exception as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "E2B 执行命令失败",
                details=_error_details(name, exc),
            ) from exc
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout, stdout_cut = _truncate_text(stdout_text, self._max_output_bytes)
        stderr, stderr_cut = _truncate_text(stderr_text, self._max_output_bytes)
        return ExecResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            truncated=stdout_cut or stderr_cut,
        )

    async def _run_with_stdin(
        self,
        sandbox: Any,
        command: str,
        stdin: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout: float,
    ) -> tuple[int, str, str]:
        """后台起进程 → 写 stdin → 关 stdin → 等退出;非零退出不算异常。"""
        sdk = _import_e2b()
        handle = await sandbox.commands.run(
            command,
            background=True,
            stdin=True,
            cwd=cwd or None,
            envs=env or None,
            timeout=timeout,
        )
        try:
            await handle.send_stdin(stdin.encode("utf-8"))
            await handle.close_stdin()
            result = await asyncio.wait_for(handle.wait(), timeout=timeout + 5)
        except sdk.CommandExitException as exc:
            return int(exc.exit_code), exc.stdout or "", exc.stderr or ""
        except TimeoutError as exc:
            with suppress(Exception):
                await handle.kill()
            raise UpstreamError(
                "sandbox.exec_timeout",
                f"沙箱命令执行超时({timeout:.0f} 秒)",
            ) from exc
        return int(result.exit_code), result.stdout or "", result.stderr or ""

    async def open_shell(
        self,
        name: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        cols: int = DEFAULT_SHELL_COLS,
        rows: int = DEFAULT_SHELL_ROWS,
    ) -> ShellSession:
        sdk = _import_e2b()
        sandbox = await self._resolve(name)
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def on_data(chunk: bytes) -> None:
            await queue.put(bytes(chunk))

        try:
            handle = await sandbox.pty.create(
                _pty_size(sdk, cols, rows),
                on_data=on_data,
                cwd=cwd or None,
                envs=env or None,
                # 长驻 shell:0 = 不因流空闲超时断开(由会话管理器/沙箱寿命兜底)
                timeout=0,
            )
        except Exception as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "沙箱内无法启动交互式 shell",
                details=_error_details(name, exc),
            ) from exc
        return E2bShellSession(sandbox, handle, name=name, queue=queue)

    async def write_file(self, name: str, path: str, data: bytes) -> None:
        sandbox = await self._resolve(name)
        try:
            await sandbox.files.write(path, data)
        except Exception as first_exc:
            # 常见场景:目标目录尚未创建 → 建一层父目录后重试一次
            parent = str(PurePosixPath(path).parent)
            try:
                await sandbox.files.make_dir(parent)
                await sandbox.files.write(path, data)
            except Exception as exc:
                raise UpstreamError(
                    "sandbox.provider_error",
                    "写入沙箱文件失败",
                    details=_error_details(name, first_exc),
                ) from exc

    async def read_file(self, name: str, path: str) -> bytes:
        sdk = _import_e2b()
        sandbox = await self._resolve(name)
        try:
            data = await sandbox.files.read(path, format="bytes")
        except sdk.FileNotFoundException as exc:
            raise NotFoundError(
                "sandbox.file_not_found",
                f"沙箱内文件不存在:{path}",
                details={"sandbox_name": name, "path": path},
            ) from exc
        except Exception as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "读取沙箱文件失败",
                details=_error_details(name, exc),
            ) from exc
        return bytes(data)

    async def list_dir(
        self, name: str, path: str, *, depth: int = 1
    ) -> list[FileEntry]:
        sdk = _import_e2b()
        sandbox = await self._resolve(name)
        try:
            entries = await sandbox.files.list(path, depth=max(1, depth))
        except sdk.FileNotFoundException as exc:
            raise NotFoundError(
                "sandbox.file_not_found",
                f"沙箱内目录不存在:{path}",
                details={"sandbox_name": name, "path": path},
            ) from exc
        except AppError:
            raise
        except Exception as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "列举沙箱目录失败",
                details=_error_details(name, exc),
            ) from exc
        collected: list[FileEntry] = []
        for entry in entries:
            child = _entry_path(entry, path)
            if not child or child == path:
                continue
            kind = _entry_kind(entry)
            collected.append(
                FileEntry(
                    path=child,
                    kind=kind,
                    size=0 if kind == "dir" else _entry_size(entry),
                    modified=_entry_modified(entry),
                )
            )
        return sorted(collected, key=lambda item: item.path)

    async def destroy(self, name: str) -> None:
        self._require_key()
        sdk = _import_e2b()
        sandbox_id = self._ids.get(name)
        if sandbox_id is None:
            try:
                sandbox_id = await self._find_sandbox_id(sdk, name)
            except UpstreamError:
                return  # 幂等:控制面已没有这个沙箱
            except Exception as exc:  # pragma: no cover - 防御性兜底
                raise UpstreamError(
                    "sandbox.provider_error",
                    "查找待销毁沙箱失败",
                    details=_error_details(name, exc),
                ) from exc
        try:
            await sdk.AsyncSandbox.kill(sandbox_id, **self._api_opts())
        except sdk.SandboxNotFoundException:
            pass
        except Exception as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "销毁 E2B 沙箱失败",
                details=_error_details(name, exc),
            ) from exc
        finally:
            self._ids.pop(name, None)
