"""沙箱运行时适配层:业务层只依赖 `SandboxProvider` 协议。

两种实现:

- `MicrosandboxProvider`:microsandbox microVM(默认,隔离);
- `LocalSandboxProvider`:宿主本地目录 + 子进程,**无隔离**,仅用于
  没有 microVM runtime 的本地开发/单租户可信环境。

microsandbox SDK 只在真正创建/执行时**惰性导入**——测试与没有 runtime 的
dev 启动不依赖它。所有 SDK 异常在这里统一收口为 `UpstreamError`,
不把堆栈/底层错误直接泄漏成 API 的 500。
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any, Protocol

from common.errors import AppError, NotFoundError, UpstreamError

from workspace.core.config import Settings
from workspace.schemas.sandbox import WORKSPACE_ROOT, ensure_workspace_path

DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024
DEFAULT_EXEC_TIMEOUT_SECONDS = 60
MAX_ERROR_DETAIL_CHARS = 300


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

    async def destroy(self, name: str) -> None:
        """销毁沙箱;运行时已不存在时静默成功(幂等)。"""
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

    async def write_file(self, name: str, path: str, data: bytes) -> None:
        raise self._unavailable()

    async def read_file(self, name: str, path: str) -> bytes:
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
