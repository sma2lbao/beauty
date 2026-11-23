"""嵌入业务逻辑:litellm 是唯一出口(架构 §5);失败统一映射 502。

**分批在网关侧兜住**:提供商对单请求行数限制很紧(百炼 text-embedding-v4/v3
只收 10 行,超了直接 400),而知识库会把整篇文档的所有块一次性提交
(1MB 正文按 400 字分块可达数千块)。这里按 `embedding_batch_size` 切开逐批
调用,结果按原顺序拼接、usage 累加;调用方不感知批大小。

**凭据与 completions 同源**:`_provider_kwargs` 显式把平台 `API_KEY`/`API_BASE`
(或 BYOK 请求级密钥)传给 litellm,不依赖进程环境变量——否则百炼这类
provider 只能靠 `DASHSCOPE_API_KEY` 兜底,平台配置形同虚设。
"""

from __future__ import annotations

import litellm
from common.errors import UpstreamError
from common.logging import get_logger

from llm_gateway import metrics
from llm_gateway.core.config import Settings
from llm_gateway.schemas.completions import UsageOut
from llm_gateway.schemas.embeddings import EmbeddingRequest, EmbeddingResponse
from llm_gateway.services.completion_request import _get, _provider


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


def _batches(values: list[str], size: int) -> list[list[str]]:
    """按单请求行数上限切批(保持原顺序,末批可短);size 非法时退化为 1。"""
    width = max(1, size)
    return [values[start : start + width] for start in range(0, len(values), width)]


def _accumulate(acc: list[int], usage: object) -> None:
    """把一批的 token 用量累加进 `[prompt, completion, total]`(兼容 dict 形状)。"""
    acc[0] += int(_get(usage, "prompt_tokens", 0) or 0)
    acc[1] += int(_get(usage, "completion_tokens", 0) or 0)
    acc[2] += int(_get(usage, "total_tokens", 0) or 0)


def _embedding_kwargs(settings: Settings, api_key: str | None) -> dict[str, object]:
    """嵌入的提供商凭据:BYOK 请求级密钥 > 嵌入专用配置 > 平台 API_KEY/API_BASE。

    平台对话密钥与嵌入密钥可能不是同一家(DeepSeek 对话 + 百炼嵌入),所以
    嵌入有独立的 `EMBEDDING_API_KEY`/`EMBEDDING_API_BASE`;只在没配时才回退平台。
    """
    extra: dict[str, object] = {}
    effective_key = api_key or settings.embedding_api_key or settings.api_key
    if effective_key:
        extra["api_key"] = effective_key
    effective_base = settings.embedding_api_base or settings.api_base
    if effective_base:
        extra["api_base"] = effective_base
    return extra


async def embed(
    settings: Settings,
    data: EmbeddingRequest,
    *,
    api_key: str | None = None,
) -> EmbeddingResponse:
    model = data.model or settings.default_embedding_model
    log = get_logger("llm_gateway.embeddings")
    extra = _embedding_kwargs(settings, api_key)
    batches = _batches(list(data.input), settings.embedding_batch_size)
    vectors: list[list[float]] = []
    resolved_model = model
    usage_acc = [0, 0, 0]
    usage_seen = False
    for index, batch in enumerate(batches):
        try:
            resp = await litellm.aembedding(
                model=model,
                input=batch,
                timeout=settings.request_timeout_seconds,
                **extra,
            )
        except Exception as exc:
            metrics.observe_llm_error(
                provider=_provider(model),
                code="llm.embedding_error",
            )
            raise UpstreamError(
                "llm.embedding_error",
                f"嵌入调用失败:{exc}",
                details={"model": model, "batch": index, "batch_size": len(batch)},
            ) from exc
        got = _normalize_embeddings(_get(resp, "data"))
        if len(got) != len(batch):
            raise UpstreamError(
                "llm.embedding_error",
                "嵌入返回条数与输入不一致",
                details={"expected": len(batch), "got": len(got)},
            )
        vectors.extend(got)
        resolved_model = _get(resp, "model") or resolved_model
        usage = _get(resp, "usage")
        if usage is not None:
            usage_seen = True
            _accumulate(usage_acc, usage)
    usage_obj = None
    if usage_seen:
        usage_obj = UsageOut(
            prompt_tokens=usage_acc[0],
            completion_tokens=usage_acc[1],
            total_tokens=usage_acc[2],
        )
    log.info(
        "llm_embedding",
        model=model,
        count=len(vectors),
        batches=len(batches),
    )
    return EmbeddingResponse(
        model=resolved_model,
        embeddings=vectors,
        usage=usage_obj,
    )
