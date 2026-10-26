"""嵌入业务逻辑:litellm 是唯一出口(架构 §5);失败统一映射 502。"""

from __future__ import annotations

import litellm
from common.errors import UpstreamError
from common.logging import get_logger

from llm_gateway import metrics
from llm_gateway.core.config import Settings
from llm_gateway.schemas.completions import UsageOut
from llm_gateway.schemas.embeddings import EmbeddingRequest, EmbeddingResponse
from llm_gateway.services.completions import _get


def _normalize_embeddings(payload: object) -> list[list[float]]:
    """litellm 的 data 可能是对象或 dict;按顺序取出向量。"""
    items = payload if isinstance(payload, list) else []
    vectors: list[list[float]] = []
    for item in items:
        vector = _get(item, "embedding")
        if vector is None:
            continue
        vectors.append([float(value) for value in vector])
    return vectors


async def embed(
    settings: Settings,
    data: EmbeddingRequest,
    *,
    api_key: str | None = None,
) -> EmbeddingResponse:
    model = data.model or settings.default_embedding_model
    log = get_logger("llm_gateway.embeddings")
    extra: dict[str, object] = {}
    if api_key:
        # BYOK:请求级密钥覆盖平台凭据;provider 由 litellm 模型串前缀给出
        # (无前缀说明模型名没带提供商,不猜——交回 litellm 自行识别)。
        extra["api_key"] = api_key
        if "/" in str(model):
            extra["provider"] = str(model).split("/", 1)[0]
    try:
        resp = await litellm.aembedding(
            model=model,
            input=data.input,
            timeout=settings.request_timeout_seconds,
            **extra,
        )
    except Exception as exc:
        metrics.observe_llm_error(
            provider=str(model).split("/", 1)[0] or "unknown",
            code="llm.embedding_error",
        )
        raise UpstreamError(
            "llm.embedding_error",
            f"嵌入调用失败:{exc}",
            details={"model": model},
        ) from exc
    vectors = _normalize_embeddings(_get(resp, "data"))
    if len(vectors) != len(data.input):
        raise UpstreamError(
            "llm.embedding_error",
            "嵌入返回条数与输入不一致",
            details={"expected": len(data.input), "got": len(vectors)},
        )
    usage = getattr(resp, "usage", None)
    usage_obj = None
    if usage is not None:
        usage_obj = UsageOut(
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
        )
    log.info("llm_embedding", model=model, count=len(vectors))
    return EmbeddingResponse(
        model=getattr(resp, "model", None) or model,
        embeddings=vectors,
        usage=usage_obj,
    )
