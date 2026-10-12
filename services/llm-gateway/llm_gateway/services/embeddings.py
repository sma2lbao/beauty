"""嵌入业务逻辑:litellm 是唯一出口(架构 §5);失败统一映射 502。"""

from __future__ import annotations

import litellm
from common.errors import UpstreamError
from common.logging import get_logger

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


async def embed(settings: Settings, data: EmbeddingRequest) -> EmbeddingResponse:
    model = data.model or settings.default_embedding_model
    log = get_logger("llm_gateway.embeddings")
    try:
        resp = await litellm.aembedding(
            model=model,
            input=data.input,
            timeout=settings.request_timeout_seconds,
        )
    except Exception as exc:
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
