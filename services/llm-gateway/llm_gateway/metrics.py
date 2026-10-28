"""llm-gateway 业务指标(W4-R-40:从 `common.metrics` 迁出)。

指标名与标签与迁移前完全一致(docs/observability.md §9):

- `llm_upstream_errors_total{service,provider,code}`:上游错误计数;
- `llm_tokens_total{service,model,direction}`:token 用量(input/output);
- `llm_request_duration_seconds{service,provider,model}`:调用耗时(含上游排队);
- `llm_fallbacks_total{service,from_provider,to_provider,code}`:fallback 切换计数
  (model-routing §5.3);
- `llm_byok_lookups_total{service,status}`:BYOK 密钥解析计数
  (status=disabled/negative/error/byok/cache_hit)。

`service` 标签统一走 `common.metrics.service_label`。
"""

from __future__ import annotations

from common.metrics import service_label
from prometheus_client import Counter, Histogram

llm_upstream_errors_total = Counter(
    "llm_upstream_errors_total",
    "模型网关上游错误计数",
    ("service", "provider", "code"),
)
llm_tokens_total = Counter(
    "llm_tokens_total",
    "模型 token 用量(input=提示 / output=补全)",
    ("service", "model", "direction"),
)
llm_request_duration_seconds = Histogram(
    "llm_request_duration_seconds",
    "模型调用耗时(秒,含上游排队)",
    ("service", "provider", "model"),
    buckets=(0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0),
)
llm_byok_lookups_total = Counter(
    "llm_byok_lookups_total",
    "BYOK 密钥解析计数(按结果状态)",
    ("service", "status"),
)
llm_fallbacks_total = Counter(
    "llm_fallbacks_total",
    "模型 fallback 切换计数",
    ("service", "from_provider", "to_provider", "code"),
)


def _provider(model: str) -> str:
    """litellm 模型串的 provider 前缀(``deepseek/deepseek-chat`` → ``deepseek``)。"""
    return str(model).split("/", 1)[0] or "unknown"


def observe_llm_error(*, provider: str, code: str, service: str | None = None) -> None:
    """记录一次上游错误(provider/code 空值归一为 unknown)。"""
    llm_upstream_errors_total.labels(
        service=service_label(service),
        provider=provider or "unknown",
        code=code or "unknown",
    ).inc()


def observe_llm_tokens(
    *,
    model: str,
    direction: str,
    tokens: int,
    service: str | None = None,
) -> None:
    """记录一次模型调用的 token 用量(为 0 的上游不记,避免空样本)。"""
    if tokens <= 0:
        return
    llm_tokens_total.labels(
        service=service_label(service),
        model=model or "unknown",
        direction=direction,
    ).inc(tokens)


def observe_byok_lookup(*, status: str, service: str | None = None) -> None:
    """记录一次 BYOK 解析结果(disabled/negative/error/byok/cache_hit)。"""
    llm_byok_lookups_total.labels(
        service=service_label(service),
        status=status or "unknown",
    ).inc()


def observe_llm_latency(
    *,
    provider: str,
    model: str,
    seconds: float,
    service: str | None = None,
) -> None:
    """记录一次模型调用耗时(负值夹到 0)。"""
    llm_request_duration_seconds.labels(
        service=service_label(service),
        provider=provider or "unknown",
        model=model or "unknown",
    ).observe(max(seconds, 0.0))


def observe_llm_fallback(
    *,
    from_model: str,
    to_model: str,
    code: str,
    service: str | None = None,
) -> None:
    """记录一次 fallback 切换:主模型临时故障后换到备用模型。"""
    llm_fallbacks_total.labels(
        service=service_label(service),
        from_provider=_provider(from_model),
        to_provider=_provider(to_model),
        code=code or "unknown",
    ).inc()
