"""沙箱运行时适配层:业务层只依赖 `SandboxProvider` 协议。

microsandbox SDK 只在真正创建/执行时**惰性导入**——测试与没有 runtime 的
dev 启动不依赖它。所有 SDK 异常在这里统一收口为 `UpstreamError`,
不把堆栈/底层错误直接泄漏成 API 的 500。
"""

from __future__ import annotations

import os
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import ModuleType
from typing import Any, Protocol

from common.errors import NotFoundError, UpstreamError

from workspace.core.config import Settings

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
        network = (
            sdk.Network.allow_all() if spec.network == "public" else sdk.Network.none()
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
            "请配置 WORKSPACE__SANDBOX_PROVIDER=microsandbox",
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
    raise UpstreamError(
        "sandbox.provider_unknown",
        f"未知沙箱 provider:{settings.sandbox_provider}",
        details={"provider": settings.sandbox_provider},
    )
