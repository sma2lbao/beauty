"""显式不可用实现:所有操作 502 `sandbox.provider_unavailable`,绝不静默成功。"""

from __future__ import annotations

from common.errors import UpstreamError

from .protocols import (
    DEFAULT_SHELL_COLS,
    DEFAULT_SHELL_ROWS,
    ExecResult,
    FileEntry,
    SandboxSpec,
    ShellSession,
)


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
