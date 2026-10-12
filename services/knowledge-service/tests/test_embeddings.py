"""嵌入提供方:本地确定性向量、余弦函数、网关提供方(打桩 HTTP)、配置校验。"""

import httpx
import pytest
from common.errors import UpstreamError

from knowledge.services.embeddings import (
    GatewayEmbeddingProvider,
    LocalHashingEmbeddingProvider,
    build_embedder,
    cosine_similarity,
)


async def test_local_provider_is_deterministic_and_normalized() -> None:
    provider = LocalHashingEmbeddingProvider()
    first, second = await provider.embed(["客户 A 合同评审", "客户 A 合同评审"])
    assert first == second
    assert len(first) == 256
    norm = sum(value * value for value in first) ** 0.5
    assert norm == pytest.approx(1.0)


async def test_local_provider_ranks_lexical_overlap() -> None:
    provider = LocalHashingEmbeddingProvider()
    query, contract, budget = await provider.embed(
        ["合同评审", "本周与客户 A 的合同评审安排", "财务预算上限说明"]
    )
    assert cosine_similarity(query, contract) > cosine_similarity(query, budget)


def test_cosine_handles_degenerate_inputs() -> None:
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([1.0, 0.0], [0.0, 0.0]) == 0.0
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


async def test_gateway_provider_posts_and_parses() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"model": "bge-m3", "embeddings": [[0.1, 0.2], [0.3, 0.4]]},
        )

    provider = GatewayEmbeddingProvider(
        "http://gateway", "bge-m3", transport=httpx.MockTransport(handler)
    )
    try:
        vectors = await provider.embed(["甲", "乙"])
    finally:
        await provider.aclose()

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert captured["path"] == "/v1/embeddings"
    assert captured["body"] == {"model": "bge-m3", "input": ["甲", "乙"]}


async def test_gateway_provider_empty_input_skips_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("不应发起请求")

    provider = GatewayEmbeddingProvider(
        "http://gateway", "bge-m3", transport=httpx.MockTransport(handler)
    )
    try:
        assert await provider.embed([]) == []
    finally:
        await provider.aclose()


async def test_gateway_provider_maps_errors_to_502() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502, json={"code": "llm.embedding_error", "message": "模型调用失败"}
        )

    provider = GatewayEmbeddingProvider(
        "http://gateway", "bge-m3", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(UpstreamError) as exc_info:
            await provider.embed(["甲"])
    finally:
        await provider.aclose()

    assert exc_info.value.status == 502
    assert exc_info.value.code == "llm.embedding_error"


async def test_gateway_provider_count_mismatch_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[0.1]]})

    provider = GatewayEmbeddingProvider(
        "http://gateway", "bge-m3", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(UpstreamError) as exc_info:
            await provider.embed(["甲", "乙"])
    finally:
        await provider.aclose()

    assert exc_info.value.code == "knowledge.embedding_mismatch"


def test_build_embedder_validates_provider() -> None:
    assert build_embedder("local", model="m", gateway_url="http://x").name == "local"
    assert (
        build_embedder("gateway", model="m", gateway_url="http://x").name == "gateway"
    )
    with pytest.raises(ValueError):
        build_embedder("pinecone", model="m", gateway_url="http://x")
