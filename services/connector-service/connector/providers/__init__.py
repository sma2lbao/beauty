"""provider 注册表:代码 manifest 驱动,不引入 YAML/builder(设计 §4)。

新增 provider = 加一个模块 + 在 _REGISTRY 登记一行;启用与否由配置决定。
"""

from __future__ import annotations

from common.errors import NotFoundError

from connector.core.config import Settings
from connector.providers.base import BaseProvider
from connector.providers.notion import NotionProvider

# 内置 provider 登记表:仓库内置 Notion 只读连接器,新增 provider 时在此加一行。
_REGISTRY: dict[str, type[BaseProvider]] = {NotionProvider.name: NotionProvider}


def enabled_provider_names(settings: Settings) -> tuple[str, ...]:
    """配置里启用、且代码确实有 manifest 的 provider(保序去重)。"""
    return tuple(name for name in settings.provider_names if name in _REGISTRY)


def build_provider(settings: Settings, name: str) -> BaseProvider | None:
    cls = _REGISTRY.get((name or "").strip().lower())
    return cls(settings) if cls is not None else None


def require_provider(settings: Settings, name: str) -> BaseProvider:
    provider = build_provider(settings, name)
    if provider is None:
        raise NotFoundError(
            "connector.provider_not_found",
            f"未知连接器 provider: {name}",
            details={"provider": name},
        )
    return provider


__all__ = [
    "BaseProvider",
    "build_provider",
    "enabled_provider_names",
    "require_provider",
]
