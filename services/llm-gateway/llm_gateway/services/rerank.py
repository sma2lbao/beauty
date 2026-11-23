"""重排业务逻辑:litellm `arerank` 是唯一出口(架构 §5);失败统一映射 502。

**凭据与嵌入同源**:百炼同时提供嵌入与重排,通常同一个 Base/Key,所以
`RERANK_API_KEY`/`RERANK_API_BASE` 留空时依次回退嵌入专用配置与平台配置;
BYOK 请求级密钥优先。显式传 key 意味着 litellm 不再读 `DASHSCOPE_API_KEY`
之类的原生环境变量——平台内配置必须自洽,不依赖进程环境。

**降级在调用方**:本层失败就 502,知识库负责退回 RRF 顺序(见
`knowledge/services/rerank.py`)——重排是增益,不能变成检索的单点故障。
"""

from __future__ import annotations

import litellm
from common.errors import UpstreamError
from common.logging import get_logger

from llm_gateway import metrics
from llm_gateway.core.config import Settings
from llm_gateway.schemas.completions import UsageOut
from llm_gateway.schemas.rerank import RerankRequest, RerankResponse, RerankResult
from llm_gateway.services.completion_request import _get, _provider


def _rerank_kwargs(settings: Settings, api_key: str | None) -> dict[str, object]:
    """重排的提供商凭据:BYOK > 重排专用 > 嵌入专用 > 平台配置。

    百炼嵌入与重排同源,所以没单独配重排时复用嵌入凭据;两者不同家时
    (如 Cohere 重排 + 百炼嵌入)必须显式配 `RERANK_API_KEY`/`RERANK_API_BASE`。
    """
    extra: dict[str, object] = {}
    effective_key = (
        api_key
        or settings.rerank_api_key
        or settings.embedding_api_key
        or settings.api_key
    )
    if effective_key:
        extra["api_key"] = effective_key
    effective_base = (
        settings.rerank_api_base or settings.embedding_api_base or settings.api_base
    )
    if effective_base:
        extra["api_base"] = effective_base
    return extra


def _normalize_results(payload: object, count: int) -> list[RerankResult]:
    """把 litellm 的 results 归一化成 (index, score),按分数降序、下标升序去平局。

    上游已经按 relevance_score 降序返回,这里再排一次:响应顺序是契约的一部分,
    不能依赖具体提供商的实现(打桩的测试与真实上游行为才可对照)。
    """
    items = payload if isinstance(payload, list) else []
    results: list[RerankResult] = []
    for item in items:
        index = _get(item, "index")
        score = _get(item, "relevance_score")
        if index is None or score is None:
            continue
        position = int(index)
        if position < 0 or position >= count:
            raise UpstreamError(
                "llm.rerank_error",
                "重排返回的下标超出候选范围",
                details={"index": position, "documents": count},
            )
        results.append(RerankResult(index=position, relevance_score=float(score)))
    results.sort(key=lambda result: (-result.relevance_score, result.index))
    return results


def _usage(payload: object) -> UsageOut | None:
    """litellm 把 rerank 用量放在 meta.billed_units.total_tokens;缺失就不编造。"""
    meta = _get(payload, "meta")
    billed = _get(meta, "billed_units")
    total = _get(billed, "total_tokens")
    if total is None:
        return None
    tokens = int(total)
    return UsageOut(prompt_tokens=tokens, completion_tokens=0, total_tokens=tokens)


async def rerank(
    settings: Settings,
    data: RerankRequest,
    *,
    api_key: str | None = None,
) -> RerankResponse:
    model = data.model or settings.default_rerank_model
    log = get_logger("llm_gateway.rerank")
    extra = _rerank_kwargs(settings, api_key)
    try:
        resp = await litellm.arerank(
            model=model,
            query=data.query,
            documents=list(data.documents),
            top_n=data.top_n,
            # 文档正文调用方本来就有;回传只会把响应体放大一倍
            return_documents=False,
            timeout=settings.request_timeout_seconds,
            **extra,
        )
    except Exception as exc:
        metrics.observe_llm_error(provider=_provider(model), code="llm.rerank_error")
        raise UpstreamError(
            "llm.rerank_error",
            f"重排调用失败:{exc}",
            details={"model": model, "documents": len(data.documents)},
        ) from exc

    results = _normalize_results(_get(resp, "results"), len(data.documents))
    if not results:
        raise UpstreamError(
            "llm.rerank_error",
            "重排返回空结果",
            details={"documents": len(data.documents)},
        )
    log.info(
        "llm_rerank",
        model=model,
        documents=len(data.documents),
        returned=len(results),
    )
    return RerankResponse(
        model=_get(resp, "model") or model,
        results=results,
        usage=_usage(resp),
    )
