"""按配置装配 provider 的工厂。"""

from __future__ import annotations

from common.errors import UpstreamError

from workspace.core.config import Settings

from .local import LocalSandboxProvider
from .microsandbox import MicrosandboxProvider
from .protocols import SandboxProvider
from .unavailable import UnavailableSandboxProvider


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
