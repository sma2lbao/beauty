"""沙箱运行时适配层:业务层只依赖 `SandboxProvider` 协议。

两种实现:

- `MicrosandboxProvider`:microsandbox microVM(默认,隔离);
- `LocalSandboxProvider`:宿主本地目录 + 子进程,**无隔离**,仅用于
  没有 microVM runtime 的本地开发/单租户可信环境。

microsandbox SDK 只在真正创建/执行时**惰性导入**——测试与没有 runtime 的
dev 启动不依赖它。所有 SDK 异常在这里统一收口为 `UpstreamError`,
不把堆栈/底层错误直接泄漏成 API 的 500。

除一次性 exec 外,协议还提供 **持久 shell 会话**(`open_shell`):microsandbox
用 `exec_stream(tty=True)` 拿真 TTY,local provider 用 POSIX pty(pty 不可用的
环境退化为管道,不是 TTY),输出/输入以字节流暴露给上层的会话管理器。
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import struct
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any, Protocol

try:  # POSIX pty 支持;Windows 上导入失败 → local provider 退化管道 shell
    import fcntl
    import pty
    import termios
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]
    pty = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]

from common.errors import AppError, NotFoundError, UpstreamError

from workspace.core.config import Settings
from workspace.schemas.sandbox import WORKSPACE_ROOT, ensure_workspace_path

DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024
DEFAULT_EXEC_TIMEOUT_SECONDS = 60
MAX_ERROR_DETAIL_CHARS = 300
# 持久 shell 会话:每次从流里读多少字节、默认终端尺寸
SHELL_READ_CHUNK = 65_536
DEFAULT_SHELL_COLS = 120
DEFAULT_SHELL_ROWS = 32
# 沙箱镜像里常见的交互式 shell;按顺序尝试,取第一个能起来的
_SHELL_CANDIDATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("/bin/sh", ("-i",)),
    ("/bin/bash", ("-i",)),
    ("sh", ("-i",)),
)


@dataclass(frozen=True)
class SandboxSpec:
    """创建沙箱所需的全部参数(provider 无关)。"""

    tenant_id: str
    name: str
    kind: str
    image: str
    cpus: float
    memory_mb: int
    network: str
    ttl_seconds: int


@dataclass(frozen=True)
class ExecResult:
    """一次命令执行的结果(stdout/stderr 已按上限截断)。"""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool


@dataclass(frozen=True)
class FileEntry:
    """文件树里的一个条目(扁平列表的一项)。

    - `path` 是**沙箱内**绝对路径(如 `/workspace/src/a.ts`);
    - `kind` 只取 file / dir / symlink / other;
    - `size` 是字节数,目录恒为 0(目录大小按文件系统语义没有可比性);
    - `modified` 是 mtime(epoch 秒),取不到时为 None。
    """

    path: str
    kind: str
    size: int
    modified: float | None = None


class ShellSession(Protocol):
    """一个持久交互式 shell(真 TTY,或退化后的管道)。

    - `read()` 返回下一段原始输出字节,EOF 返回 None;UTF-8 解码由上层做;
    - `write()` 把调用方输入**原样**写入(不补换行);
    - `close()` 幂等,尽力结束子进程/句柄;
    - `exit_code` 在进程退出后才有值,之前为 None;
    - `tty` 表示这个会话是否真的拿到了终端(local 退化管道时为 False)。
    """

    tty: bool
    exit_code: int | None

    async def read(self) -> bytes | None:
        """读下一段输出;EOF 返回 None(不抛异常)。"""
        ...

    async def write(self, data: bytes) -> None:
        """写输入;会话已结束时抛 `sandbox.session_closed`。"""
        ...

    async def close(self) -> None:
        """关闭会话并结束子进程(幂等)。"""
        ...


class SandboxProvider(Protocol):
    """沙箱运行时协议:创建/执行/文件读写/销毁,全部 async。"""

    provider: str

    async def create(self, spec: SandboxSpec) -> None:
        """按 spec 创建沙箱(已存在视为失败,由业务层生成唯一名)。"""
        ...

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
        """执行命令;argv 不经过 shell,超时抛 `sandbox.exec_timeout`。"""
        ...

    async def write_file(self, name: str, path: str, data: bytes) -> None:
        """把二进制内容写到沙箱内 path。"""
        ...

    async def read_file(self, name: str, path: str) -> bytes:
        """读取沙箱内 path;不存在抛 `sandbox.file_not_found`。"""
        ...

    async def list_dir(
        self, name: str, path: str, *, depth: int = 1
    ) -> list[FileEntry]:
        """列目录:返回扁平列表(含各级嵌套项);depth=1 只列直接子项。

        符号链接只标记 `kind=symlink`,**不递归进入**(避免环与越界)。
        """
        ...

    async def destroy(self, name: str) -> None:
        """销毁沙箱;运行时已不存在时静默成功(幂等)。"""
        ...

    async def open_shell(
        self,
        name: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        cols: int = DEFAULT_SHELL_COLS,
        rows: int = DEFAULT_SHELL_ROWS,
    ) -> ShellSession:
        """在沙箱内开一个持久交互式 shell;起不来抛 `sandbox.provider_error`。"""
        ...


def _import_sdk() -> ModuleType:
    """惰性导入 microsandbox:未安装时给出明确的 502 错误码。"""
    try:
        import microsandbox
    except ImportError as exc:  # pragma: no cover - 依赖已声明,仅防御性兜底
        raise UpstreamError(
            "sandbox.provider_unavailable",
            "microsandbox SDK 未安装,无法提供沙箱运行时",
        ) from exc
    return microsandbox


def _error_details(name: str, exc: BaseException) -> dict[str, Any]:
    """只暴露异常类型与短消息,不带 traceback。"""
    return {
        "sandbox_name": name,
        "error": f"{type(exc).__name__}: {str(exc)[:MAX_ERROR_DETAIL_CHARS]}",
    }


def _truncate_text(text: str, limit: int) -> tuple[str, bool]:
    """按 UTF-8 字节数截断文本,返回 (文本, 是否截断)。"""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", "ignore"), True


# microsandbox FsEntry.kind → 契约 kind;未知取值一律 other(不猜)
FS_ENTRY_KINDS = {
    "FILE": "file",
    "DIRECTORY": "dir",
    "SYMLINK": "symlink",
}


def _child_path(parent: str, name: str) -> str:
    """拼沙箱内绝对路径;parent 末尾有无 / 都归一(`/workspace` → /a)。"""
    return f"{parent.rstrip('/')}/{name}" if name else parent


def _fs_entry_path(entry: Any, parent: str) -> str:
    """推导条目在沙箱内的绝对路径,兼容 SDK 的 path 语义差异。

    microsandbox 的 `FsEntry` 只有 `path`(没有 `name`),而 Rust 实现没把
    `path` 语义写进文档:可能是绝对路径、相对列举目录的子路径,或只是名字。
    这里按形状归一——只取 basename 再拼会把 `/workspace/src/a.ts` 摊平成
    `/workspace/a.ts`,整棵子树的层级都会错。
    """
    raw = str(getattr(entry, "path", "") or "")
    if not raw or ".." in PurePosixPath(raw).parts:
        return ""
    candidate = raw if raw.startswith("/") else _child_path(parent, raw)
    normalized = PurePosixPath(candidate).as_posix()
    return normalized if normalized.startswith("/") else ""


def _fs_entry_kind(entry: Any) -> str:
    """FsEntry.kind 可能是字符串或枚举,统一取 .name 后查表。"""
    raw = getattr(entry, "kind", None)
    key = str(getattr(raw, "name", raw) or "").upper()
    return FS_ENTRY_KINDS.get(key, "other")


def _fs_entry_size(entry: Any) -> int:
    return max(0, int(getattr(entry, "size", 0) or 0))


def _fs_entry_modified(entry: Any) -> float | None:
    value = getattr(entry, "modified", None)
    return None if value is None else float(value)


class MicroShellSession:
    """microsandbox `exec_stream(tty=True)` 上的真 PTY shell。

    输出走 SDK 的事件流:STDOUT/STDERR 事件带 data 字节(已合流,顺序即终端
    实际写出的顺序),EXITED 事件的 code 即退出码;写输入走 `take_stdin()`
    拿到的 ExecSink。`read()` 用 `recv()` 逐帧消费,SDK 异常统一转 502。
    """

    tty = True

    def __init__(self, sandbox_name: str, handle: Any, sink: Any) -> None:
        self._sandbox_name = sandbox_name
        self._handle = handle
        self._sink = sink
        self._exit_code: int | None = None
        self._closed = False
        self._eof = False

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    async def read(self) -> bytes | None:
        if self._eof:
            return None
        while True:
            try:
                event = await self._handle.recv()
            except Exception as exc:
                raise UpstreamError(
                    "sandbox.provider_error",
                    "读取 shell 输出失败",
                    details=_error_details(self._sandbox_name, exc),
                ) from exc
            if event is None:
                await self._finish(None)
                return None
            kind = str(getattr(event, "event_type", ""))
            if kind in ("stdout", "stderr"):
                data = bytes(getattr(event, "data", None) or b"")
                if data:
                    return data
                continue
            if kind == "exited":
                await self._finish(getattr(event, "code", None))
                return None
            if kind == "failed":
                code = getattr(event, "code", None)
                self._exit_code = int(code) if code is not None else None
                raise UpstreamError(
                    "sandbox.provider_error",
                    "沙箱内 shell 异常退出",
                    details={
                        "sandbox_name": self._sandbox_name,
                        "exit_code": self._exit_code,
                    },
                )
            # started / stdin_error 等事件不产生输出,继续等下一帧

    async def write(self, data: bytes) -> None:
        if self._closed or self._eof or self._sink is None:
            raise UpstreamError("sandbox.session_closed", "shell 会话已结束,无法再写入")
        try:
            await self._sink.write(data)
        except Exception as exc:
            self._eof = True
            raise UpstreamError(
                "sandbox.session_closed",
                "shell 会话已结束,无法再写入",
                details=_error_details(self._sandbox_name, exc),
            ) from exc

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._eof = True
        if self._sink is not None:
            with suppress(Exception):
                await self._sink.close()
            self._sink = None
        # kill 对已退出的 handle 是 no-op;SDK 各版本都可能抛,一律静默
        with suppress(Exception):
            await self._handle.kill()
        with suppress(Exception):
            code, _forced = await asyncio.wait_for(self._handle.wait(), timeout=5)
            if self._exit_code is None:
                self._exit_code = int(code)

    async def _finish(self, code: Any) -> None:
        if code is not None:
            self._exit_code = int(code)
        if self._eof:
            return
        self._eof = True
        if self._sink is not None:
            with suppress(Exception):
                await self._sink.close()


class MicrosandboxProvider:
    """microsandbox 0.7.x 实现:detached 创建 + 按名重连执行。

    选择 detached 是为了让沙箱活过单次 HTTP 请求;创建成功后必须
    `await sandbox.detach()` 释放句柄生命周期,否则服务重启/句柄释放时
    沙箱会被停掉。之后每次操作按名取回 `SandboxHandle`,再 `connect()`
    成可执行的 `Sandbox`。
    """

    provider = "microsandbox"

    def __init__(
        self,
        *,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        default_exec_timeout_seconds: int = DEFAULT_EXEC_TIMEOUT_SECONDS,
        msb_home: str = "",
    ) -> None:
        self._max_output_bytes = max_output_bytes
        self._default_exec_timeout_seconds = default_exec_timeout_seconds
        self._msb_home = msb_home
        # 必须在 SDK 惰性导入前设置:runtime home 默认 ~/.microsandbox,
        # 容器/受限环境下需要指向可写目录(显式环境变量优先,不覆盖)
        if msb_home:
            os.environ.setdefault("MSB_HOME", msb_home)

    async def create(self, spec: SandboxSpec) -> None:
        sdk = _import_sdk()
        # public 只放公网:不能用 allow_all()——它会连宿主/私网一起放行,
        # 多租户下等于把用户 MCP/脚本接到平台内网。
        network = (
            sdk.Network.from_profiles(sdk.NetworkProfile.PUBLIC)
            if spec.network == "public"
            else sdk.Network.none()
        )
        try:
            sandbox = await sdk.Sandbox.create(
                spec.name,
                image=spec.image,
                cpus=max(1, int(round(spec.cpus))),
                memory=spec.memory_mb,
                network=network,
                detached=True,
                # 双保险:绝对寿命与空闲寿命都由运行时兜底回收
                max_duration=float(spec.ttl_seconds),
                idle_timeout=float(spec.ttl_seconds),
            )
        except (sdk.RuntimeNotInstalledError, sdk.RuntimeIncompleteError) as exc:
            raise UpstreamError(
                "sandbox.provider_unavailable",
                "本机未安装/未配置 microsandbox runtime",
                details={"sandbox_name": spec.name},
            ) from exc
        except Exception as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "microsandbox 创建沙箱失败",
                details=_error_details(spec.name, exc),
            ) from exc
        try:
            # create 返回的句柄仍 owns_lifecycle(即使 detached=True):
            # 必须 detach,否则进程退出/句柄释放时沙箱会被停掉,
            # 之后按名 Sandbox.get(name).connect() 也就取不回来了。
            await sandbox.detach()
        except Exception as exc:
            # best-effort 回收,绝不返回一个"看似创建成功但会被回收"的实例
            with suppress(Exception):
                await sandbox.stop()
            with suppress(Exception):
                await sdk.Sandbox.remove(spec.name)
            raise UpstreamError(
                "sandbox.provider_error",
                "创建沙箱后 detach 失败,已回收",
                details=_error_details(spec.name, exc),
            ) from exc

    async def _connect(self, name: str) -> Any:
        """按名取回可执行句柄;不存在/连接失败统一 502。"""
        sdk = _import_sdk()
        try:
            handle = await sdk.Sandbox.get(name)
            return await handle.connect()
        except sdk.SandboxNotFoundError as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "沙箱运行时实例不存在(可能已被回收)",
                details={"sandbox_name": name},
            ) from exc
        except Exception as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "连接沙箱运行时失败",
                details=_error_details(name, exc),
            ) from exc

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
        sdk = _import_sdk()
        sandbox = await self._connect(name)
        timeout = float(
            timeout_seconds
            if timeout_seconds is not None
            else self._default_exec_timeout_seconds
        )
        started = time.monotonic()
        try:
            output = await sandbox.exec(
                argv[0],
                list(argv[1:]),
                cwd=cwd,
                env=env or None,
                stdin=stdin.encode("utf-8") if stdin is not None else None,
                timeout=timeout,
            )
        except sdk.ExecTimeoutError as exc:
            raise UpstreamError(
                "sandbox.exec_timeout",
                f"沙箱命令执行超时({timeout:.0f} 秒)",
                details={"sandbox_name": name, "timeout_seconds": int(timeout)},
            ) from exc
        except Exception as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "沙箱执行命令失败",
                details=_error_details(name, exc),
            ) from exc
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout, stdout_cut = _truncate_text(output.stdout_text, self._max_output_bytes)
        stderr, stderr_cut = _truncate_text(output.stderr_text, self._max_output_bytes)
        return ExecResult(
            exit_code=int(output.exit_code),
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            truncated=stdout_cut or stderr_cut,
        )

    async def open_shell(
        self,
        name: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        cols: int = DEFAULT_SHELL_COLS,
        rows: int = DEFAULT_SHELL_ROWS,
    ) -> ShellSession:
        """用 `exec_stream` + `tty=True` 起一个真 PTY 的交互式 shell。"""
        sdk = _import_sdk()
        sandbox = await self._connect(name)
        last_error: BaseException | None = None
        for shell, shell_args in _SHELL_CANDIDATES:
            try:
                handle = await sandbox.exec_stream(
                    shell,
                    list(shell_args),
                    cwd=cwd,
                    env=env or None,
                    stdin=sdk.Stdin.pipe(),
                    tty=True,
                )
            except Exception as exc:
                last_error = exc
                continue
            sink: Any = None
            with suppress(Exception):
                sink = handle.take_stdin()
            # 终端尺寸只是 best-effort:拿不到不影响读写
            with suppress(Exception):
                await handle.resize(rows, cols)
            return MicroShellSession(name, handle, sink)
        raise UpstreamError(
            "sandbox.provider_error",
            "沙箱内无法启动交互式 shell",
            details=_error_details(name, last_error or RuntimeError("没有可用 shell")),
        )

    async def write_file(self, name: str, path: str, data: bytes) -> None:
        sandbox = await self._connect(name)
        try:
            await sandbox.fs.write(path, data)
        except Exception as first_exc:
            # 常见场景:目标目录尚未创建 → 建一层父目录后重试一次
            parent = str(PurePosixPath(path).parent)
            try:
                await sandbox.fs.mkdir(parent)
                await sandbox.fs.write(path, data)
            except Exception as exc:
                raise UpstreamError(
                    "sandbox.provider_error",
                    "写入沙箱文件失败",
                    details=_error_details(name, first_exc),
                ) from exc

    async def read_file(self, name: str, path: str) -> bytes:
        sdk = _import_sdk()
        sandbox = await self._connect(name)
        try:
            return await sandbox.fs.read(path)
        except sdk.PathNotFoundError as exc:
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

    async def list_dir(
        self, name: str, path: str, *, depth: int = 1
    ) -> list[FileEntry]:
        sdk = _import_sdk()
        sandbox = await self._connect(name)
        try:
            return await self._walk(sandbox, path, max(1, depth))
        except AppError:
            raise
        except sdk.PathNotFoundError as exc:
            raise NotFoundError(
                "sandbox.file_not_found",
                f"沙箱内目录不存在:{path}",
                details={"sandbox_name": name, "path": path},
            ) from exc
        except Exception as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "列举沙箱目录失败",
                details=_error_details(name, exc),
            ) from exc

    async def _walk(
        self, sandbox: Any, path: str, depth: int, out: list[FileEntry] | None = None
    ) -> list[FileEntry]:
        """DFS 递归收集条目:同级按名字排序,目录项先于其子项。"""
        collected = [] if out is None else out
        entries = await sandbox.fs.list(path)
        derived = [(entry, _fs_entry_path(entry, path)) for entry in entries]
        for entry, child in sorted(derived, key=lambda pair: pair[1]):
            if not child or child == path:  # 无名/自指条目:跳过,避免自己套自己
                continue
            kind = _fs_entry_kind(entry)
            collected.append(
                FileEntry(
                    path=child,
                    kind=kind,
                    size=0 if kind == "dir" else _fs_entry_size(entry),
                    modified=_fs_entry_modified(entry),
                )
            )
            if kind == "dir" and depth > 1:
                await self._walk(sandbox, child, depth - 1, collected)
        return collected

    async def destroy(self, name: str) -> None:
        sdk = _import_sdk()
        try:
            handle = await sdk.Sandbox.get(name)
        except sdk.SandboxNotFoundError:
            return  # 幂等:运行时已不存在
        except Exception as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "查找待销毁沙箱失败",
                details=_error_details(name, exc),
            ) from exc
        try:
            await handle.destroy(force=True)
        except sdk.SandboxNotFoundError:
            pass
        except Exception as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "销毁沙箱失败",
                details=_error_details(name, exc),
            ) from exc
        # destroy 已移除运行时;某些版本会残留持久索引,remove 兜底
        with suppress(sdk.SandboxNotFoundError):
            await sdk.Sandbox.remove(name)


LOCAL_WORKSPACE_SUBDIR = "workspace"
LOCAL_HOME_SUBDIR = "home"
LOCAL_TMP_SUBDIR = "tmp"
# 沙箱名会直接变成宿主目录名:只允许安全字符,且不允许 .. 逃逸
LOCAL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _check_local_name(name: str) -> str:
    """校验沙箱名可安全地当目录名使用。"""
    if not LOCAL_NAME_PATTERN.match(name) or ".." in name:
        raise UpstreamError(
            "sandbox.provider_error",
            "非法的本地沙箱名",
            details={"sandbox_name": str(name)[:MAX_ERROR_DETAIL_CHARS]},
        )
    return name


def _local_entry_meta(entry: os.DirEntry[str], parent: str) -> tuple[FileEntry, Path]:
    """把一个 DirEntry 翻译成 FileEntry(不跟随符号链接),并给出宿主路径。"""
    child = _child_path(parent, entry.name)
    stat = entry.stat(follow_symlinks=False)
    if entry.is_symlink():
        kind = "symlink"
    elif entry.is_dir(follow_symlinks=False):
        kind = "dir"
    elif entry.is_file(follow_symlinks=False):
        kind = "file"
    else:
        kind = "other"
    size = 0 if kind == "dir" else max(0, stat.st_size)
    return FileEntry(
        path=child,
        kind=kind,
        size=size,
        modified=float(stat.st_mtime),
    ), Path(entry.path)


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    """结束子进程:POSIX 按进程组杀,避免 shell 的孙进程残留。"""
    if process.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    with suppress(Exception):
        await asyncio.wait_for(process.wait(), timeout=5)


async def _process_exit_code(
    process: asyncio.subprocess.Process, *, timeout: float = 5
) -> int:
    """等子进程退出并取退出码;超时先杀进程组,再取不到就用 -1。"""
    try:
        code = await asyncio.wait_for(process.wait(), timeout=timeout)
    except TimeoutError:
        await _terminate_process(process)
        with suppress(Exception):
            await asyncio.wait_for(process.wait(), timeout=timeout)
        code = process.returncode
    return int(code) if code is not None else -1


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    """设置 pty 窗口大小(TIOCSWINSZ);失败不影响读写。"""
    if fcntl is None or termios is None:
        return
    with suppress(OSError):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _pty_child_setup(slave_fd: int) -> Any:
    """子进程 fork 后:新建会话并把这个 pty 设为控制终端(拿到真 TTY 语义)。"""
    if fcntl is None or termios is None:
        return None

    def _setup() -> None:
        os.setsid()
        with suppress(OSError):
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

    return _setup


def _pipe_shell_argv() -> list[str]:
    """没有 pty 时的退化 shell 命令(POSIX 用 sh -i,Windows 用 COMSPEC)。"""
    if sys.platform == "win32":
        return [os.environ.get("COMSPEC", "cmd.exe")]
    return ["/bin/sh", "-i"]


class LocalPipeShellSession:
    """管道版 shell:stdin/stdout 是普通管道,**不是 TTY**。

    pty 不可用时(Windows、没有 /dev/ptmx 的受限容器)的退化实现:没有行编辑、
    补全、回显与作业控制,`isatty()` 为 False(很多程序因此切到非交互模式)。
    stderr 已合流到 stdout,所以 read() 只有一条流。
    """

    tty = False

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process
        self._exit_code: int | None = None
        self._closed = False
        self._eof = False

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    async def read(self) -> bytes | None:
        if self._eof:
            return None
        stdout = self._process.stdout
        if stdout is None:
            self._eof = True
            return None
        data = await stdout.read(SHELL_READ_CHUNK)
        if not data:
            self._eof = True
            if self._exit_code is None:
                self._exit_code = await _process_exit_code(self._process)
            return None
        return bytes(data)

    async def write(self, data: bytes) -> None:
        stdin = self._process.stdin
        if self._closed or stdin is None:
            raise UpstreamError("sandbox.session_closed", "shell 会话已结束,无法再写入")
        try:
            stdin.write(data)
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
            raise UpstreamError(
                "sandbox.session_closed", "shell 会话已结束,无法再写入"
            ) from exc

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._eof = True
        if self._process.stdin is not None:
            with suppress(Exception):
                self._process.stdin.close()
        await _terminate_process(self._process)
        if self._exit_code is None:
            self._exit_code = await _process_exit_code(self._process)


class LocalPtyShellSession:
    """POSIX pty 上的真 TTY shell:有行编辑/补全/回显,`isatty()` 为 True。

    master fd 交给事件循环的 add_reader,读到 EOF(对端全关,Linux 上 EIO)
    时投递 None 作为流结束;写输入按 pty 缓冲分块并等待可写,避免阻塞事件循环。
    """

    tty = True

    # 单次 os.write 的字节数:pty 缓冲通常 4KB,分块写避免一次塞满阻塞
    _WRITE_CHUNK = 1024
    _WRITE_TIMEOUT_SECONDS = 5

    def __init__(self, process: asyncio.subprocess.Process, master_fd: int) -> None:
        self._process = process
        self._master_fd = master_fd
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._exit_code: int | None = None
        self._closed = False
        self._eof = False
        self._loop.add_reader(master_fd, self._on_readable)

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    def _on_readable(self) -> None:
        try:
            chunk = os.read(self._master_fd, SHELL_READ_CHUNK)
        except BlockingIOError:
            return
        except OSError:
            chunk = b""
        if chunk:
            self._queue.put_nowait(bytes(chunk))
            return
        self._remove_reader()
        self._queue.put_nowait(None)

    def _remove_reader(self) -> None:
        with suppress(Exception):
            self._loop.remove_reader(self._master_fd)

    async def read(self) -> bytes | None:
        if self._eof:
            return None
        chunk = await self._queue.get()
        if chunk is None:
            self._eof = True
            if self._exit_code is None:
                self._exit_code = await _process_exit_code(self._process)
            return None
        return chunk

    async def write(self, data: bytes) -> None:
        if self._closed or self._eof:
            raise UpstreamError("sandbox.session_closed", "shell 会话已结束,无法再写入")
        remaining = memoryview(data)
        deadline = time.monotonic() + self._WRITE_TIMEOUT_SECONDS
        while remaining:
            try:
                written = os.write(self._master_fd, remaining[: self._WRITE_CHUNK])
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise UpstreamError(
                        "sandbox.session_closed", "shell 输入写入超时"
                    ) from None
                await asyncio.sleep(0.005)
                continue
            except OSError as exc:
                raise UpstreamError(
                    "sandbox.session_closed", "shell 会话已结束,无法再写入"
                ) from exc
            remaining = remaining[written:]

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._eof = True
        self._remove_reader()
        with suppress(OSError):
            os.close(self._master_fd)
        await _terminate_process(self._process)
        if self._exit_code is None:
            self._exit_code = await _process_exit_code(self._process)
        self._queue.put_nowait(None)


class LocalSandboxProvider:
    """宿主本地实现:每个沙箱就是一个目录 + 直接起子进程。

    **没有隔离**:命令以 workspace-service 进程身份运行、能读写宿主文件。
    只适用于单租户可信环境(本地开发、桌面"本地电脑模式");默认 provider
    仍是 microsandbox,多租户/生产禁止切到 local。

    - 沙箱目录 = `<root>/<name>`,`/workspace` 映射到其中的 `workspace/`
      子目录,`HOME`/`TMPDIR` 分别指向 `home`/`tmp`;
    - exec 的 argv 不经 shell;超时按进程组 SIGKILL(Windows 退化为单进程);
    - 镜像/CPU/内存/网络策略无法在宿主上强制执行,只留在 DB 行里供审计。
    """

    provider = "local"

    def __init__(
        self,
        *,
        root: str = "./.sandboxes",
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        default_exec_timeout_seconds: int = DEFAULT_EXEC_TIMEOUT_SECONDS,
    ) -> None:
        self._root = Path(root).expanduser().resolve()
        self._max_output_bytes = max_output_bytes
        self._default_exec_timeout_seconds = default_exec_timeout_seconds

    def _sandbox_dir(self, name: str) -> Path:
        return self._root / _check_local_name(name)

    def _workspace_dir(self, name: str) -> Path:
        return self._sandbox_dir(name) / LOCAL_WORKSPACE_SUBDIR

    def _require_sandbox(self, name: str) -> Path:
        sandbox_dir = self._sandbox_dir(name)
        if not sandbox_dir.is_dir():
            raise UpstreamError(
                "sandbox.provider_error",
                "本地沙箱不存在(可能已被回收)",
                details={"sandbox_name": name},
            )
        return sandbox_dir

    def _host_path(self, name: str, path: str) -> Path:
        """把沙箱内 `/workspace/...` 映射成宿主路径,并挡住符号链接逃逸。"""
        safe = ensure_workspace_path(path)
        base = self._workspace_dir(name).resolve()
        relative = PurePosixPath(safe).relative_to(WORKSPACE_ROOT)
        candidate = base.joinpath(*relative.parts)
        resolved = candidate.resolve()
        if resolved != base and base not in resolved.parents:
            raise AppError("sandbox.path_invalid", f"路径越出沙箱工作区:{path}")
        return candidate

    def _resolve_cwd(self, name: str, cwd: str | None) -> Path:
        """解析工作目录:`/workspace/...` 映射进沙箱目录,其余循宿主语义。

        microsandbox 里 cwd 是沙箱内路径;本地实现不重写任意宿主绝对路径
        (与"无隔离"一致),只有 `/workspace` 前缀被映射,相对路径落在
        沙箱 workspace 下。
        """
        if cwd is None:
            return self._workspace_dir(name)
        if cwd == WORKSPACE_ROOT or cwd.startswith(WORKSPACE_ROOT + "/"):
            return self._host_path(name, cwd)
        path = Path(cwd)
        return path if path.is_absolute() else self._workspace_dir(name) / path

    def _child_env(self, name: str, env: dict[str, str] | None) -> dict[str, str]:
        """最小宿主环境 + 沙箱内 HOME/TMPDIR,再叠加调用方环境变量。"""
        sandbox_dir = self._sandbox_dir(name)
        child_env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(sandbox_dir / LOCAL_HOME_SUBDIR),
            "TMPDIR": str(sandbox_dir / LOCAL_TMP_SUBDIR),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        if env:
            child_env.update(env)
        return child_env

    async def create(self, spec: SandboxSpec) -> None:
        sandbox_dir = self._sandbox_dir(spec.name)

        def _prepare() -> None:
            try:
                # mkdir 不做 exist_ok:同名已存在直接报错,且失败时只清理本次
                # 创建出来的目录(不会误删并发创建成功的沙箱)
                sandbox_dir.mkdir(parents=True)
            except FileExistsError as exc:
                raise UpstreamError(
                    "sandbox.provider_error",
                    "本地沙箱目录已存在",
                    details={"sandbox_name": spec.name, "path": str(sandbox_dir)},
                ) from exc
            except OSError as exc:
                raise UpstreamError(
                    "sandbox.provider_error",
                    "创建本地沙箱目录失败",
                    details=_error_details(spec.name, exc),
                ) from exc
            try:
                for subdir in (
                    LOCAL_WORKSPACE_SUBDIR,
                    LOCAL_HOME_SUBDIR,
                    LOCAL_TMP_SUBDIR,
                ):
                    (sandbox_dir / subdir).mkdir()
            except OSError as exc:
                shutil.rmtree(sandbox_dir, ignore_errors=True)
                raise UpstreamError(
                    "sandbox.provider_error",
                    "创建本地沙箱目录失败",
                    details=_error_details(spec.name, exc),
                ) from exc

        await asyncio.to_thread(_prepare)

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        """超时/取消时尽力结束子进程:POSIX 按进程组杀,避免孙进程残留。"""
        await _terminate_process(process)

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
        self._require_sandbox(name)
        workdir = self._resolve_cwd(name, cwd)
        if not workdir.is_dir():
            raise UpstreamError(
                "sandbox.provider_error",
                f"沙箱内工作目录不存在:{cwd if cwd is not None else WORKSPACE_ROOT}",
                details={"sandbox_name": name, "cwd": str(workdir)},
            )
        timeout = float(
            timeout_seconds
            if timeout_seconds is not None
            else self._default_exec_timeout_seconds
        )
        stdin_data = stdin.encode("utf-8") if stdin is not None else None
        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(workdir),
                env=self._child_env(name, env),
                stdin=(
                    asyncio.subprocess.PIPE
                    if stdin_data is not None
                    else asyncio.subprocess.DEVNULL
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=sys.platform != "win32",
            )
        except OSError as exc:
            # 命令本身起不来(二进制缺失/无执行权限)按 shell 约定给 126/127,
            # 不把调用方的错误伪装成平台故障 502。
            return ExecResult(
                exit_code=126 if isinstance(exc, PermissionError) else 127,
                stdout="",
                stderr=f"命令无法启动:{exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
                truncated=False,
            )
        # shield:超时只取消等待、不取消 communicate,杀进程后还能把管道收干净
        communicate = asyncio.ensure_future(process.communicate(stdin_data))
        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(
                asyncio.shield(communicate), timeout
            )
        except TimeoutError:
            await self._terminate(process)
            with suppress(Exception):
                await communicate
            raise UpstreamError(
                "sandbox.exec_timeout",
                f"沙箱命令执行超时({timeout:.0f} 秒)",
                details={"sandbox_name": name, "timeout_seconds": int(timeout)},
            ) from None
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout, stdout_cut = _truncate_text(
            stdout_raw.decode("utf-8", "replace"), self._max_output_bytes
        )
        stderr, stderr_cut = _truncate_text(
            stderr_raw.decode("utf-8", "replace"), self._max_output_bytes
        )
        return ExecResult(
            exit_code=process.returncode if process.returncode is not None else 0,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            truncated=stdout_cut or stderr_cut,
        )

    async def open_shell(
        self,
        name: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        cols: int = DEFAULT_SHELL_COLS,
        rows: int = DEFAULT_SHELL_ROWS,
    ) -> ShellSession:
        """优先 POSIX pty(真 TTY);pty 不可用时退化为管道 shell(不是 TTY)。"""
        self._require_sandbox(name)
        workdir = self._resolve_cwd(name, cwd)
        if not workdir.is_dir():
            raise UpstreamError(
                "sandbox.provider_error",
                f"沙箱内工作目录不存在:{cwd if cwd is not None else WORKSPACE_ROOT}",
                details={"sandbox_name": name, "cwd": str(workdir)},
            )
        child_env = self._child_env(name, env)
        pty_session = await self._open_pty_shell(name, workdir, child_env, cols, rows)
        if pty_session is not None:
            return pty_session
        return await self._open_pipe_shell(name, workdir, child_env)

    async def _open_pty_shell(
        self, name: str, workdir: Path, env: dict[str, str], cols: int, rows: int
    ) -> ShellSession | None:
        """试着用 pty 起真 TTY shell;pty 设备拿不到时返回 None(由调用方退化)。"""
        if pty is None or fcntl is None or termios is None or sys.platform == "win32":
            return None
        last_error: BaseException | None = None
        for shell, shell_args in _SHELL_CANDIDATES:
            master_fd = -1
            slave_fd = -1
            try:
                master_fd, slave_fd = pty.openpty()
            except OSError:
                # 这台机器/这个沙箱没有 pty 设备(如受限容器),整体退化为管道
                return None
            try:
                _set_winsize(slave_fd, cols, rows)
                os.set_blocking(master_fd, False)
                process = await asyncio.create_subprocess_exec(
                    shell,
                    *shell_args,
                    cwd=str(workdir),
                    env=env,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    preexec_fn=_pty_child_setup(slave_fd),
                )
            except OSError as exc:
                last_error = exc
                with suppress(OSError):
                    os.close(master_fd)
                with suppress(OSError):
                    os.close(slave_fd)
                continue
            # 子进程已 dup slave fd,父进程侧关掉,EOF 语义才正确
            with suppress(OSError):
                os.close(slave_fd)
            return LocalPtyShellSession(process, master_fd)
        raise UpstreamError(
            "sandbox.provider_error",
            "本地沙箱无法启动交互式 shell",
            details=_error_details(name, last_error or RuntimeError("没有可用 shell")),
        )

    async def _open_pipe_shell(
        self, name: str, workdir: Path, env: dict[str, str]
    ) -> ShellSession:
        argv = _pipe_shell_argv()
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(workdir),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=sys.platform != "win32",
            )
        except OSError as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "本地沙箱无法启动交互式 shell",
                details=_error_details(name, exc),
            ) from exc
        return LocalPipeShellSession(process)

    async def write_file(self, name: str, path: str, data: bytes) -> None:
        self._require_sandbox(name)
        target = self._host_path(name, path)

        def _write() -> None:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            except OSError as exc:
                raise UpstreamError(
                    "sandbox.provider_error",
                    "写入沙箱文件失败",
                    details=_error_details(name, exc),
                ) from exc

        await asyncio.to_thread(_write)

    async def read_file(self, name: str, path: str) -> bytes:
        self._require_sandbox(name)
        target = self._host_path(name, path)

        def _read() -> bytes:
            try:
                return target.read_bytes()
            except FileNotFoundError as exc:
                raise NotFoundError(
                    "sandbox.file_not_found",
                    f"沙箱内文件不存在:{path}",
                    details={"sandbox_name": name, "path": path},
                ) from exc
            except OSError as exc:
                raise UpstreamError(
                    "sandbox.provider_error",
                    "读取沙箱文件失败",
                    details=_error_details(name, exc),
                ) from exc

        return await asyncio.to_thread(_read)

    async def list_dir(
        self, name: str, path: str, *, depth: int = 1
    ) -> list[FileEntry]:
        self._require_sandbox(name)
        target = self._host_path(name, path)
        levels = max(1, depth)
        collected: list[FileEntry] = []

        def _scan(root: Path, parent: str, remaining: int) -> None:
            with os.scandir(root) as iterator:
                children = sorted(
                    (_local_entry_meta(entry, parent) for entry in iterator),
                    key=lambda item: item[0].path,
                )
            for entry, host_path in children:
                collected.append(entry)
                # 只递归真实目录:符号链接由 kind 标记,不进入(防环/防越界)
                if entry.kind == "dir" and remaining > 1:
                    _scan(host_path, entry.path, remaining - 1)

        try:
            await asyncio.to_thread(_scan, target, path, levels)
        except AppError:
            raise
        except FileNotFoundError as exc:
            raise NotFoundError(
                "sandbox.file_not_found",
                f"沙箱内目录不存在:{path}",
                details={"sandbox_name": name, "path": path},
            ) from exc
        except OSError as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "列举沙箱目录失败",
                details=_error_details(name, exc),
            ) from exc
        return collected

    async def destroy(self, name: str) -> None:
        sandbox_dir = self._sandbox_dir(name)

        def _remove() -> None:
            if not sandbox_dir.exists():
                return  # 幂等:运行时已不存在
            try:
                shutil.rmtree(sandbox_dir)
            except OSError as exc:
                raise UpstreamError(
                    "sandbox.provider_error",
                    "删除本地沙箱目录失败",
                    details=_error_details(name, exc),
                ) from exc

        await asyncio.to_thread(_remove)


class UnavailableSandboxProvider:
    """显式不可用实现:所有操作 502 `sandbox.provider_unavailable`,绝不静默成功。

    用于没有 microVM runtime 的 dev 环境:接口形态完整,调用即得到明确错误。
    """

    provider = "unavailable"

    @staticmethod
    def _unavailable() -> UpstreamError:
        return UpstreamError(
            "sandbox.provider_unavailable",
            "沙箱运行时未启用(provider=unavailable);"
            "请配置 WORKSPACE__SANDBOX_PROVIDER=microsandbox 或 local",
        )

    async def create(self, spec: SandboxSpec) -> None:
        raise self._unavailable()

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
        raise self._unavailable()

    async def open_shell(
        self,
        name: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        cols: int = DEFAULT_SHELL_COLS,
        rows: int = DEFAULT_SHELL_ROWS,
    ) -> ShellSession:
        raise self._unavailable()

    async def write_file(self, name: str, path: str, data: bytes) -> None:
        raise self._unavailable()

    async def read_file(self, name: str, path: str) -> bytes:
        raise self._unavailable()

    async def list_dir(
        self, name: str, path: str, *, depth: int = 1
    ) -> list[FileEntry]:
        raise self._unavailable()

    async def destroy(self, name: str) -> None:
        # 销毁是幂等清理:运行时本来就没有,视为已完成
        return None


def build_sandbox_provider(settings: Settings) -> SandboxProvider:
    """按配置装配 provider;未知配置直接报错,避免悄悄降级。"""
    if settings.sandbox_provider == "unavailable":
        return UnavailableSandboxProvider()
    if settings.sandbox_provider == "microsandbox":
        return MicrosandboxProvider(
            max_output_bytes=settings.sandbox_max_output_bytes,
            default_exec_timeout_seconds=settings.sandbox_exec_timeout_seconds,
            msb_home=settings.sandbox_msb_home,
        )
    if settings.sandbox_provider == "local":
        return LocalSandboxProvider(
            root=settings.sandbox_local_root,
            max_output_bytes=settings.sandbox_max_output_bytes,
            default_exec_timeout_seconds=settings.sandbox_exec_timeout_seconds,
        )
    raise UpstreamError(
        "sandbox.provider_unknown",
        f"未知沙箱 provider:{settings.sandbox_provider}",
        details={"provider": settings.sandbox_provider},
    )
