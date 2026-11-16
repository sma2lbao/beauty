"""microsandbox microVM 实现:一次性 exec + `exec_stream(tty=True)` 持久 shell。

SDK 只在这里**惰性导入**(`_import_sdk`),测试夹具 monkeypatch 的正是本模块的
`_import_sdk`;所有 SDK 异常统一收口成 `UpstreamError`。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any

from common.errors import AppError, NotFoundError, UpstreamError

from .protocols import (
    _SHELL_CANDIDATES,
    DEFAULT_EXEC_TIMEOUT_SECONDS,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_SHELL_COLS,
    DEFAULT_SHELL_ROWS,
    ExecResult,
    FileEntry,
    SandboxSpec,
    ShellSession,
)
from .utils import (
    _error_details,
    _fs_entry_kind,
    _fs_entry_modified,
    _fs_entry_path,
    _fs_entry_size,
    _import_sdk,
    _truncate_text,
)


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


#: 沙箱内工作区路径:文件工具与 exec 的 cwd 都以它为根
WORKSPACE_MOUNT_PATH = "/workspace"


def _unsafe_dir_name(name: str) -> bool:
    """沙箱名要当宿主目录名用:挡住空/隐藏/带分隔符的值,防越界。"""
    return (
        not name
        or name.startswith(".")
        or "/" in name
        or "\\" in name
        or name != os.path.basename(name)
    )


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
        workspace_root: str = "",
    ) -> None:
        self._max_output_bytes = max_output_bytes
        self._default_exec_timeout_seconds = default_exec_timeout_seconds
        self._msb_home = msb_home
        self._workspace_root = workspace_root
        # 必须在 SDK 惰性导入前设置:runtime home 默认 ~/.microsandbox,
        # 容器/受限环境下需要指向可写目录(显式环境变量优先,不覆盖)
        if msb_home:
            os.environ.setdefault("MSB_HOME", msb_home)

    def _workspace_dir(self, name: str) -> Path:
        """沙箱私有 `/workspace` 的宿主目录。

        没有显式配置根目录时落在 MSB_HOME 旁的 `workspaces/` 下;沙箱名由
        服务生成(`bm-<hex>`),天然可做目录名。每个沙箱一份,互不可见——
        共享同一目录会让模型读到别的 run/别的账号留下的文件。
        """
        if _unsafe_dir_name(name):
            raise UpstreamError(
                "sandbox.provider_error",
                "沙箱名不能用作工作区目录名",
                details={"sandbox_name": name},
            )
        root = self._workspace_root
        if not root:
            home = self._msb_home or os.path.join(
                os.path.expanduser("~"), ".microsandbox"
            )
            root = os.path.join(home, "workspaces")
        return Path(root) / name

    def _workspace_volumes(self, sdk: Any, name: str) -> dict[str, Any]:
        """给新沙箱挂一份空的私有工作区,返回 SDK 的 `volumes=` 入参。"""
        directory = self._workspace_dir(name)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "创建沙箱工作区目录失败",
                details={"sandbox_name": name, "error": f"{type(exc).__name__}: {exc}"},
            ) from exc
        # 宿主路径必须真实化:msb 拒绝经过符号链接的挂载源(/var、/tmp 在
        # macOS 上都是软链),会报 InvalidConfigError。
        return {
            WORKSPACE_MOUNT_PATH: sdk.MountConfig(
                kind=sdk.MountKind.BIND, bind=os.path.realpath(directory)
            )
        }

    def _discard_workspace_dir(self, name: str) -> None:
        """删掉宿主侧工作区:运行时只管挂载,目录必须我们自己回收。"""
        if _unsafe_dir_name(name):
            return
        try:
            shutil.rmtree(self._workspace_dir(name), ignore_errors=True)
        except UpstreamError:  # pragma: no cover - 名字已在上面的守卫挡掉
            return

    async def create(self, spec: SandboxSpec) -> None:
        sdk = _import_sdk()
        # public 只放公网:不能用 allow_all()——它会连宿主/私网一起放行,
        # 多账号下等于把用户 MCP/脚本接到平台内网。
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
                # 每个沙箱一份私有 /workspace:跨沙箱/跨账号不共享文件
                volumes=self._workspace_volumes(sdk, spec.name),
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
            self._discard_workspace_dir(spec.name)
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

    async def attach_shell(
        self,
        name: str,
        pid: int,
        *,
        cols: int = DEFAULT_SHELL_COLS,
        rows: int = DEFAULT_SHELL_ROWS,
    ) -> ShellSession:
        """microsandbox 的 stdin/stdout 流句柄不在进程间共享,暂不支持接管。"""
        raise UpstreamError(
            "sandbox.provider_unsupported",
            "microsandbox provider 暂不支持接管已存在的 shell 会话",
            details={"sandbox_name": name, "pid": pid},
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
            # 幂等:运行时已不存在,但宿主工作区可能残留,一并清理
            self._discard_workspace_dir(name)
            return
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
        self._discard_workspace_dir(name)
