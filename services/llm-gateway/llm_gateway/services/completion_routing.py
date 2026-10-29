"""模型路由与 fallback 策略:选主模型、排候选链、判定能否切下一个候选。

模型选择与 fallback(``docs/design/model-routing.md`` §3/§5.3):

- 主模型 = 请求显式指定,否则网关默认;``AVAILABLE_MODELS`` 非空时做白名单校验;
- 候选链 = 主模型 + ``FALLBACK_MODELS``(去重、保序);
- 只有"临时性"故障才切下一个候选(限流/超时/连接失败/5xx),参数或鉴权类
  4xx 直接 502——重试另一个模型只会掩盖调用方的错误。
"""

from __future__ import annotations

from typing import Any

import litellm
from common.errors import AppError
from common.logging import get_logger

from llm_gateway import metrics
from llm_gateway.core.config import Settings
from llm_gateway.schemas.completions import FallbackHop

#: 可切换候选的 HTTP 状态码:限流/超时/上游 5xx(网关语义,不是调用方问题)
_FALLBACK_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

#: 可切换的 litellm 异常类型。``APIStatusError`` 在 litellm 顶层不稳定导出,
#: 所以状态码走鸭子判定(见 ``_is_switchable``)。
_SWITCHABLE_ERRORS = (
    litellm.RateLimitError,
    litellm.Timeout,
    litellm.APIConnectionError,
    litellm.InternalServerError,
    litellm.ServiceUnavailableError,
)

#: fallback 原因码:与 502 的错误码保持一致(契约示例)
_FALLBACK_CODE = "llm.upstream_error"


def _observe_upstream(model: str, code: str) -> None:
    """网关是模型上游的唯一出口:失败按 provider(模型前缀)+ code 计数。"""
    provider = str(model).split("/", 1)[0] or "unknown"
    metrics.observe_llm_error(provider=provider, code=code)


def resolve_model(settings: Settings, requested: str | None) -> str:
    """解析最终主模型并做白名单校验(model-routing §3)。

    显式模型 > 网关默认;``AVAILABLE_MODELS`` 非空时白名单外的模型返回 400,
    留空则不限制(只按默认模型语义校验)。
    """
    model = requested or settings.default_model
    if settings.available_models and model not in settings.available_models:
        raise AppError(
            "llm.model_not_allowed",
            f"模型不在允许列表内:{model}",
            details={"model": model, "available_models": settings.available_models},
        )
    return model


def candidates(settings: Settings, model: str) -> list[str]:
    """候选链 = 主模型 + 备用模型(去重、保序,主模型永远排第一)。"""
    chain = [model]
    for fallback in settings.fallback_models:
        if fallback and fallback not in chain:
            chain.append(fallback)
    return chain


def _is_switchable(exc: BaseException) -> bool:
    """临时性故障才可切换:限流/超时/连接失败/5xx(状态码鸭子判定)。"""
    if isinstance(exc, _SWITCHABLE_ERRORS):
        return True
    return getattr(exc, "status_code", None) in _FALLBACK_STATUS_CODES


def _record_fallback(from_model: str, to_model: str, exc: BaseException) -> FallbackHop:
    """记录一次 fallback:warning 日志 + 指标 + 响应里的 hop(§5.2/§5.3)。"""
    message = _clip_short(str(exc))
    log = get_logger("llm_gateway.completions")
    log.warning(
        "llm_fallback",
        from_model=from_model,
        to_model=to_model,
        code=_FALLBACK_CODE,
        error=message,
    )
    metrics.observe_llm_fallback(
        from_model=from_model,
        to_model=to_model,
        code=_FALLBACK_CODE,
    )
    return FallbackHop(
        from_model=from_model,
        to_model=to_model,
        code=_FALLBACK_CODE,
        message=message,
    )


def _clip_short(text: str) -> str:
    """fallback message 截断到 500 字以内(§5.2),避免上游长报错灌进响应。"""
    text = str(text or "")
    return text if len(text) <= 500 else text[:499] + "…"


def _failure_details(
    model: str,
    attempts: list[dict[str, str]],
    fallbacks: list[FallbackHop],
) -> dict[str, Any]:
    """502 的 details:主模型、已试候选与错误、以及切换记录。"""
    return {
        "model": model,
        "attempts": attempts,
        "fallbacks": [hop.model_dump() for hop in fallbacks],
    }
