"""进程内钩子配置的装配、读取与重置(可变全局只住在这里)。"""

from __future__ import annotations

import httpx

from agent_orchestrator.core.config import Settings
from agent_orchestrator.services import net_guard

from .constants import HookConfig

_DEFAULT_CONFIG = HookConfig()
_CONFIG = _DEFAULT_CONFIG


def configure_hooks(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: net_guard.Resolver | None = None,
) -> None:
    """按部署配置装一次 hooks(容器装配调用;测试可注入 transport/resolver)。"""
    global _CONFIG
    _CONFIG = HookConfig(
        enabled=settings.hooks_enabled,
        timeout_seconds=float(settings.hooks_timeout_seconds),
        fail_closed=settings.hooks_fail_closed,
        max_per_account=int(settings.hooks_max_per_account),
        allow_http=settings.hooks_allow_http,
        allowed_hosts=net_guard.parse_allowed_hosts(settings.mcp_allowed_hosts),
        transport=transport,
        resolver=resolver,
    )


def reset_hooks() -> None:
    """恢复默认(关闭 + 真实网络);容器关闭与测试清理用。"""
    global _CONFIG
    _CONFIG = _DEFAULT_CONFIG


def current_config() -> HookConfig:
    """当前进程级配置(只读;测试用于断言装配结果)。"""
    return _CONFIG
