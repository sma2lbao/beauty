"""可用模型目录与白名单校验(P1-G,见 docs/design/model-routing.md §6.3)。

数据源是 llm-gateway 的 ``GET /v1/models``,进程内缓存 ``llm_models_cache_seconds``
(默认 60s)——模型列表变化不频繁,没必要每个请求都打网关。网关不可达/未启用时
回退配置 ``LLM_AVAILABLE_MODELS``(空列表 = 不做白名单校验,不阻断老部署)。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from common.errors import AppError
from common.logging import get_logger

from agent_orchestrator.core.config import Settings
from agent_orchestrator.services.llm import LLMClient

logger = get_logger(__name__)

#: 模型不在可用列表时的错误码(契约 §6.3:HTTP 400)
MODEL_NOT_ALLOWED = "llm.model_not_allowed"


@dataclass(frozen=True)
class ModelOption:
    """路由可选项:网关模型 id + 归属(前端只展示/选择,不做语义解释)。"""

    id: str
    owned_by: str


@dataclass(frozen=True)
class _CacheEntry:
    at: float
    options: tuple[ModelOption, ...]


_cache: _CacheEntry | None = None


def reset_models_cache() -> None:
    """清空进程缓存(测试用;配置热更新时也可显式清一次)。"""
    global _cache
    _cache = None


def _from_settings(settings: Settings) -> tuple[ModelOption, ...]:
    return tuple(
        ModelOption(id=name, owned_by="settings")
        for name in settings.llm_available_models
    )


def _normalize(models: list[dict[str, str]]) -> tuple[ModelOption, ...]:
    options: list[ModelOption] = []
    for item in models:
        model_id = str(item.get("id") or "")
        if not model_id:
            continue
        options.append(
            ModelOption(
                id=model_id, owned_by=str(item.get("owned_by") or "beauty-moon")
            )
        )
    return tuple(options)


async def available_models(
    llm: LLMClient | None, settings: Settings
) -> tuple[ModelOption, ...]:
    """可用模型(60s 缓存;网关不可达回退配置,配置为空即"不校验")。"""
    global _cache
    ttl = settings.llm_models_cache_seconds
    if _cache is not None and ttl > 0 and time.monotonic() - _cache.at < ttl:
        return _cache.options
    options: tuple[ModelOption, ...] | None = None
    # 只有 GatewayClient 有 list_models;测试替身(FakeLLM)没有 → 直接走配置回退
    list_models = getattr(llm, "list_models", None)
    if callable(list_models):
        try:
            raw = await list_models()
        except Exception as exc:  # noqa: BLE001 - 网关故障只降级,不阻断对话
            logger.warning(
                "llm models lookup failed", error=f"{type(exc).__name__}: {exc}"
            )
            raw = None
        if raw is not None:
            options = _normalize(raw)
    if options is None:
        options = _from_settings(settings)
    _cache = _CacheEntry(at=time.monotonic(), options=options)
    return options


async def validate_model(
    model: str | None, *, llm: LLMClient | None, settings: Settings
) -> None:
    """校验模型在白名单内(未给模型 / 白名单为空时不做校验)。"""
    if not model:
        return
    options = await available_models(llm, settings)
    if not options:
        return
    if model not in {option.id for option in options}:
        raise AppError(
            MODEL_NOT_ALLOWED,
            f"模型不可用: {model}",
            details={"model": model},
        )
