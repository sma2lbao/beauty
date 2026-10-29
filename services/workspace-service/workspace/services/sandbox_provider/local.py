"""宿主本地实现:每个沙箱就是一个目录 + 直接起子进程,**无隔离**。

只适用于单租户可信环境(本地开发、桌面"本地电脑模式");多租户/生产禁止
切到 local。默认 provider 仍是 microsandbox。
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import time
from contextlib import suppress
from pathlib import Path, PurePosixPath

from common.errors import AppError, NotFoundError, UpstreamError

from workspace.schemas.sandbox import WORKSPACE_ROOT, ensure_workspace_path

from .local_shell import (
    LocalPipeShellSession,
    LocalPtyShellSession,
    _pipe_shell_argv,
    _pty_child_setup,
    _set_winsize,
    _terminate_process,
    fcntl,
    pty,
    termios,
)
from .protocols import (
    _SHELL_CANDIDATES,
    DEFAULT_EXEC_TIMEOUT_SECONDS,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_SHELL_COLS,
    DEFAULT_SHELL_ROWS,
    MAX_ERROR_DETAIL_CHARS,
    ExecResult,
    FileEntry,
    SandboxSpec,
    ShellSession,
)
from .utils import _child_path, _error_details, _truncate_text

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
