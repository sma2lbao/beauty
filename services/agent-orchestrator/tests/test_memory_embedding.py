"""语义嵌入 Provider 单测:本地哈希、网关客户端(打桩)、回退与记忆服务集成。

零网络:网关一律用 httpx.MockTransport;库相关用例用临时 sqlite 文件库
(与 test_memory_store 一致)。provider 是进程级配置,每个用例前后复位,
避免用例之间串味。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from common.logging import bind_request_context, clear_request_context
from common.security import SECRET_HEADER, TENANT_HEADER
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.models.memory import Memory
from agent_orchestrator.services import embeddings as embedding_service
from agent_orchestrator.services import memory as memory_service
from tests.conftest import _settings

#: 记忆服务只认 tenant_id 字符串;这里与 conftest 的 bearer 租户保持一致。
TENANT = "t-1"


@pytest.fixture(autouse=True)
def _reset_embeddings() -> Iterator[None]:
    """每个用例前后复位进程级 provider,避免用例之间互相影响。"""
    embedding_service.reset_embedding_provider()
    yield
    embedding_service.reset_embedding_provider()


@pytest.fixture
async def factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """临时 sqlite 文件库 + 建表;用例结束 dispose,避免句柄残留。"""
    settings = _settings(tmp_path, "memory-embedding.db")
    session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await session_factory.engine.dispose()


class _ConstantVectorProvider:
    """固定向量 provider:用来证明检索确实用配置项产出的查询向量。

    不产生任何网络请求;记录每次 embed 的输入,便于断言"只查了一次"。
    """

    model = "fake-semantic-1"

    def __init__(self, vector: Sequence[float]) -> None:
        self._vector = list(vector)
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [list(self._vector) for _ in texts]


def _gateway_provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    internal_secret: str = "",
) -> tuple[embedding_service.GatewayEmbeddingProvider, httpx.AsyncClient]:
    """构造走 MockTransport 的网关 provider,调用方负责关闭 client。"""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = embedding_service.GatewayEmbeddingProvider(
        "http://gateway.test",
        "bge-m3",
        3.0,
        client=client,
        internal_secret=internal_secret,
    )
    return provider, client


# ---- 本地哈希 provider(零网络、确定性) ----


async def test_local_provider_matches_sync_embed_text() -> None:
    provider = embedding_service.LocalHashProvider()
    assert provider.model == memory_service.EMBEDDING_MODEL
    vectors = await provider.embed(["用户偏好简洁回答", "   "])
    repeated = await provider.embed(["用户偏好简洁回答"])
    assert vectors[0] == memory_service.embed_text("用户偏好简洁回答")
    assert vectors[0] == repeated[0]
    assert vectors[1] == [0.0] * memory_service.EMBEDDING_DIMENSIONS
    assert len(vectors[0]) == memory_service.EMBEDDING_DIMENSIONS
    assert sum(value * value for value in vectors[0]) == pytest.approx(1.0)


def test_local_provider_satisfies_protocol() -> None:
    assert isinstance(
        embedding_service.LocalHashProvider(), embedding_service.EmbeddingProvider
    )


async def test_embed_one_defaults_to_local_hash() -> None:
    vector, model = await embedding_service.embed_one("偏好中文回答")
    assert model == embedding_service.EMBEDDING_MODEL_LOCAL
    assert vector == memory_service.embed_text("偏好中文回答")


async def test_embed_many_empty_input_keeps_current_model() -> None:
    vectors, model = await embedding_service.embed_many([])
    assert vectors == []
    assert model == embedding_service.EMBEDDING_MODEL_LOCAL


# ---- llm-gateway /v1/embeddings 客户端(httpx.MockTransport) ----


async def test_gateway_provider_posts_model_and_inputs() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("http://gateway.test/v1/embeddings")
        payload = json.loads(request.content)
        requests.append(payload)
        inputs = payload["input"]
        return httpx.Response(
            200,
            json={
                "model": "bge-m3",
                "embeddings": [[float(len(text)), 1.0] for text in inputs],
                "usage": {"prompt_tokens": 3, "total_tokens": 3},
            },
        )

    provider, client = _gateway_provider(handler)
    async with client:
        vectors = await provider.embed(["a", "bb"])
    assert requests == [{"model": "bge-m3", "input": ["a", "bb"]}]
    assert provider.model == "bge-m3"
    assert vectors == [[1.0, 1.0], [2.0, 1.0]]


async def test_gateway_provider_splits_input_into_batches_of_64() -> None:
    batch_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["input"]
        batch_sizes.append(len(inputs))
        return httpx.Response(
            200,
            json={"model": "bge-m3", "embeddings": [[1.0] for _ in inputs]},
        )

    provider, client = _gateway_provider(handler)
    async with client:
        vectors = await provider.embed([f"文本{index}" for index in range(130)])
    assert batch_sizes == [64, 64, 2]
    assert len(vectors) == 130


async def test_gateway_provider_rejects_non_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="upstream failed")

    provider, client = _gateway_provider(handler)
    async with client:
        with pytest.raises(embedding_service.EmbeddingUnavailable, match="502"):
            await provider.embed(["a"])


async def test_gateway_provider_rejects_count_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "bge-m3", "embeddings": [[1.0, 0.0]]})

    provider, client = _gateway_provider(handler)
    async with client:
        with pytest.raises(embedding_service.EmbeddingUnavailable, match="条数"):
            await provider.embed(["a", "b"])


async def test_gateway_provider_rejects_non_numeric_vector() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"model": "bge-m3", "embeddings": [["不是数字"]]}
        )

    provider, client = _gateway_provider(handler)
    async with client:
        with pytest.raises(embedding_service.EmbeddingUnavailable, match="非数值"):
            await provider.embed(["a"])


async def test_gateway_provider_maps_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider, client = _gateway_provider(handler)
    async with client:
        with pytest.raises(embedding_service.EmbeddingUnavailable, match="不可达"):
            await provider.embed(["a"])


async def test_gateway_provider_injects_tenant_and_secret_headers() -> None:
    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(200, json={"model": "bge-m3", "embeddings": [[1.0]]})

    provider, client = _gateway_provider(handler, internal_secret="s3cret")
    bind_request_context(tenant_id="t-1")
    try:
        async with client:
            await provider.embed(["a"])
    finally:
        clear_request_context()
    assert seen[0][TENANT_HEADER] == "t-1"
    assert seen[0][SECRET_HEADER] == "s3cret"


async def test_gateway_provider_without_identity_sends_no_headers() -> None:
    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(200, json={"model": "bge-m3", "embeddings": [[1.0]]})

    provider, client = _gateway_provider(handler)
    clear_request_context()
    async with client:
        await provider.embed(["a"])
    assert TENANT_HEADER not in seen[0]
    assert SECRET_HEADER not in seen[0]


def test_build_embedding_provider_passes_internal_secret(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        "embed-secret.db",
        memory_embedding_provider="gateway",
        llm_gateway_url="http://gateway.test",
        internal_auth_secret="s3cret",
    )
    provider = embedding_service.build_embedding_provider(settings)
    assert isinstance(provider, embedding_service.GatewayEmbeddingProvider)
    assert provider.internal_secret == "s3cret"


# ---- 回退与配置 ----


async def test_embed_one_falls_back_to_local_when_provider_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    provider, client = _gateway_provider(handler)
    async with client:
        embedding_service.configure_embedding(provider)
        vector, model = await embedding_service.embed_one("用户偏好简洁回答")
    assert model == embedding_service.EMBEDDING_MODEL_LOCAL
    assert vector == memory_service.embed_text("用户偏好简洁回答")


async def test_embed_many_without_fallback_propagates_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    provider, client = _gateway_provider(handler)
    async with client:
        embedding_service.configure_embedding(provider)
        with pytest.raises(embedding_service.EmbeddingUnavailable):
            await embedding_service.embed_many(["a"], fallback_local=False)


async def test_configure_and_reset_swap_provider() -> None:
    vector = [1.0] + [0.0] * (embedding_service.EMBEDDING_DIMENSIONS_LOCAL - 1)
    provider = _ConstantVectorProvider(vector)
    embedding_service.configure_embedding(provider)
    assert embedding_service.current_provider() is provider
    assert await embedding_service.embed_many(["a"]) == ([vector], provider.model)

    embedding_service.configure_embedding(None)
    assert isinstance(
        embedding_service.current_provider(), embedding_service.LocalHashProvider
    )

    embedding_service.configure_embedding(provider)
    embedding_service.reset_embedding_provider()
    assert isinstance(
        embedding_service.current_provider(), embedding_service.LocalHashProvider
    )


def test_build_embedding_provider_defaults_to_local(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "embed-default.db")
    provider = embedding_service.build_embedding_provider(settings)
    assert isinstance(provider, embedding_service.LocalHashProvider)


def test_build_embedding_provider_builds_gateway_when_configured(
    tmp_path: Path,
) -> None:
    settings = _settings(
        tmp_path,
        "embed-gateway.db",
        memory_embedding_provider=" Gateway ",
        memory_embedding_model="text-embedding-3",
        llm_gateway_url="http://gateway.test/",
    )
    provider = embedding_service.build_embedding_provider(settings)
    assert isinstance(provider, embedding_service.GatewayEmbeddingProvider)
    assert provider.model == "text-embedding-3"


def test_build_embedding_provider_without_gateway_url_is_local(
    tmp_path: Path,
) -> None:
    settings = _settings(
        tmp_path,
        "embed-no-url.db",
        memory_embedding_provider="gateway",
        llm_gateway_url="",
    )
    provider = embedding_service.build_embedding_provider(settings)
    assert isinstance(provider, embedding_service.LocalHashProvider)


def test_build_embedding_provider_unknown_name_is_local(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        "embed-unknown.db",
        memory_embedding_provider="mem0",
    )
    provider = embedding_service.build_embedding_provider(settings)
    assert isinstance(provider, embedding_service.LocalHashProvider)


# ---- 记忆服务集成:写入/检索都用 provider 的向量 ----


async def test_write_memory_stores_provider_embedding(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    vector = [0.0] * (embedding_service.EMBEDDING_DIMENSIONS_LOCAL - 1) + [1.0]
    provider = _ConstantVectorProvider(vector)
    embedding_service.configure_embedding(provider)

    async with session_scope(factory) as session:
        memory = await memory_service.write_memory(
            session, TENANT, content="用户偏好简洁回答", kind="preference"
        )
    assert memory.embedding == vector
    assert memory.embedding_model == provider.model
    assert provider.calls == [["用户偏好简洁回答"]]


async def test_search_memories_uses_provider_query_vector(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    # 词面完全不重合:只有 provider 给出的同一向量能让余弦命中。
    vector = [1.0] + [0.0] * (embedding_service.EMBEDDING_DIMENSIONS_LOCAL - 1)
    provider = _ConstantVectorProvider(vector)
    embedding_service.configure_embedding(provider)

    async with session_scope(factory) as session:
        memory = await memory_service.write_memory(
            session, TENANT, content="用户偏好简洁回答", kind="preference"
        )
    async with session_scope(factory) as session:
        hits = await memory_service.search_memories(session, TENANT, "完全无关的主题词")
        block = await memory_service.recall_block(session, TENANT, "完全无关的主题词")
    assert [hit.id for hit in hits] == [memory.id]
    assert memory.content in block


async def test_write_memory_falls_back_to_local_embedding(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="upstream failed")

    provider, client = _gateway_provider(handler)
    async with client:
        embedding_service.configure_embedding(provider)
        async with session_scope(factory) as session:
            memory = await memory_service.write_memory(
                session, TENANT, content="项目代号是蓝鲸"
            )
    assert memory.embedding_model == embedding_service.EMBEDDING_MODEL_LOCAL
    assert memory.embedding == memory_service.embed_text("项目代号是蓝鲸")


def test_score_memory_ignores_mismatched_embedding_dimensions() -> None:
    now = datetime(2026, 10, 25, tzinfo=UTC)
    content = "用户偏好简洁回答"
    query_tokens = set(embedding_service.tokenize(content))
    query_embedding = memory_service.embed_text(content)

    mismatched = Memory(tenant_id=TENANT, content=content, embedding=[1.0, 0.0, 0.0])
    relevance, _score = memory_service._score_memory(
        query_tokens, query_embedding, mismatched, now
    )
    # 维度不一致:只剩词面分量(词元集合相同 = 1.0)。
    assert relevance == pytest.approx(0.65)

    matched = Memory(tenant_id=TENANT, content=content, embedding=query_embedding)
    relevance, _score = memory_service._score_memory(
        query_tokens, query_embedding, matched, now
    )
    # 同维度同向量:词面 1.0 + 向量余弦 1.0。
    assert relevance == pytest.approx(1.0)
