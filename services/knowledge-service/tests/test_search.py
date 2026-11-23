"""检索:词法打分、RRF 融合、SQL 生成、三种模式的端到端排序。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from common.service_testkit.settings import sqlite_url
from database.base import Base
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from knowledge.core.vectors import EMBEDDING_DIM
from knowledge.models.knowledge import DocumentChunk, KnowledgeDocument
from knowledge.repositories import (
    DocumentChunkRepository,
    KnowledgeDocumentRepository,
    keyword_statement,
    vector_statement,
)
from knowledge.services import search as search_service
from knowledge.services.embeddings import LocalHashingEmbeddingProvider
from knowledge.services.lexical import lexical_score, lexical_terms
from tests.conftest import ACCOUNT, _ingest

CONTRACT = "本周与客户 A 的合同评审安排在周四下午,需要准备报价单。"
BUDGET = "财务预算上限说明:本季度市场费用不超过 50 万,超支需 VP 审批。"
PRODUCT = "产品路线图:Q3 上线知识库混合检索,顺带修复导出问题。"


# ---- 词法切分与打分 ----


def test_lexical_terms_splits_cjk_bigrams_and_latin_words() -> None:
    assert lexical_terms("知识库 RAGFlow-2") == ["知识", "识库", "ragflow", "2"]
    assert lexical_terms("库") == ["库"]
    assert lexical_terms("合同 合同") == ["合同"]


def test_lexical_terms_caps_length() -> None:
    # 每个二元组都不同,才能真的触发上限(重复字符会在去重阶段合并)
    text = "".join(chr(0x4E00 + index) for index in range(200))
    assert len(lexical_terms(text)) == 32


def test_lexical_score_prefers_coverage() -> None:
    terms = lexical_terms("预算审批")
    assert lexical_score(terms, "") == 0.0
    assert lexical_score(terms, "本周天气晴") == 0.0
    full = lexical_score(terms, "预算审批说明")
    partial = lexical_score(terms, "预算说明")
    assert full > partial > 0
    # 同覆盖下词频只做小幅加权,分数不超过 1
    assert lexical_score(terms, "预算审批 预算审批") <= 1.0


# ---- RRF 融合 ----


def _chunk(chunk_id: str, ordinal: int = 0):
    return SimpleNamespace(id=chunk_id, ordinal=ordinal)


def test_fuse_ranks_blocks_hit_by_both_routes_first() -> None:
    both = _chunk("both", 1)
    only_vector = _chunk("vec", 0)
    only_keyword = _chunk("kw", 2)

    hits = search_service.fuse(
        [(only_vector, "v", 0.9), (both, "v", 0.8)],
        [(only_keyword, "k", 0.9), (both, "k", 0.8)],
        rrf_k=60,
        vector_weight=0.5,
        limit=10,
    )

    assert [hit.chunk.id for hit in hits] == ["both", "vec", "kw"]
    assert hits[0].score == pytest.approx(1.0 / 62)


def test_fuse_respects_weights_and_limit() -> None:
    vector_only = _chunk("vec", 0)
    keyword_only = _chunk("kw", 0)

    vector_heavy = search_service.fuse(
        [(vector_only, "v", 1.0)],
        [(keyword_only, "k", 1.0)],
        rrf_k=60,
        vector_weight=0.9,
        limit=1,
    )
    assert [hit.chunk.id for hit in vector_heavy] == ["vec"]

    keyword_heavy = search_service.fuse(
        [(vector_only, "v", 1.0)],
        [(keyword_only, "k", 1.0)],
        rrf_k=60,
        vector_weight=0.0,
        limit=1,
    )
    assert [hit.chunk.id for hit in keyword_heavy] == ["kw"]


# ---- 模式解析 ----


def test_resolve_mode_defaults_and_rejects_unknown() -> None:
    assert search_service.resolve_mode(None) == "hybrid"
    assert search_service.resolve_mode(" VECTOR ") == "vector"
    assert search_service.resolve_mode(None, "keyword") == "keyword"
    with pytest.raises(ValueError):
        search_service.resolve_mode("semantic")


# ---- SQL 生成(不连 Postgres,只编译语句) ----


def test_vector_statement_uses_pgvector_distance_operator() -> None:
    statement = vector_statement("acct", [0.5] * EMBEDDING_DIM, 7)
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "<=>" in sql
    assert "ORDER BY distance" in sql
    assert "LIMIT" in sql


def test_keyword_statement_escapes_like_wildcards() -> None:
    sql = str(
        keyword_statement("acct", ["50%_off"], 5).compile(dialect=postgresql.dialect())
    )
    assert "ILIKE" in sql
    assert "ESCAPE" in sql


def test_embedding_column_compiles_to_vector_type() -> None:
    ddl = str(
        CreateTable(DocumentChunk.__table__).compile(dialect=postgresql.dialect())
    )
    assert f"VECTOR({EMBEDDING_DIM})" in ddl


# ---- Postgres 候选方法的 SQLite 侧等价路径 ----


@pytest.fixture
async def session(tmp_path: Path):
    engine = create_async_engine(sqlite_url(tmp_path, "search.db"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session
    await engine.dispose()


async def _seed_chunk(session, content: str, embedding: list[float] | None) -> None:
    document = KnowledgeDocument(
        account_id="acct-1",
        title="标题",
        source="manual",
        size_bytes=1,
        chunk_count=1,
        embedding_model="hashing-1024",
    )
    await KnowledgeDocumentRepository(session).add(document)
    await DocumentChunkRepository(session).add(
        DocumentChunk(
            account_id="acct-1",
            document_id=document.id,
            ordinal=0,
            content=content,
            embedding=embedding,
        )
    )


async def test_keyword_candidates_filter_and_escape(session) -> None:
    await _seed_chunk(session, "合同含 50% 折扣条款", None)
    await _seed_chunk(session, "财务预算说明", None)

    hits = await DocumentChunkRepository(session).keyword_candidates(
        "acct-1", ["50%"], 10
    )
    assert [chunk.content for chunk, _title in hits] == ["合同含 50% 折扣条款"]
    assert (
        await DocumentChunkRepository(session).keyword_candidates("acct-1", [], 10)
        == []
    )


async def test_search_skips_chunks_without_embedding(session) -> None:
    await _seed_chunk(session, "孤儿分块:没有向量", None)

    keyword_hits, mode = await search_service.search(
        session,
        "acct-1",
        "孤儿",
        5,
        None,  # type: ignore[arg-type]
        mode="keyword",
    )
    assert mode == "keyword"
    assert [hit.chunk.content for hit in keyword_hits] == ["孤儿分块:没有向量"]

    vector_hits, mode = await search_service.search(
        session,
        "acct-1",
        "孤儿",
        5,
        LocalHashingEmbeddingProvider(),
        mode="vector",
    )
    assert (mode, vector_hits) == ("vector", [])


# ---- 端到端:三种模式 ----


def test_search_echoes_mode_and_elapsed(client: TestClient) -> None:
    _ingest(client, "预算说明", BUDGET)

    body = client.post(
        "/v1/knowledge/search", json={"query": "预算"}, headers=ACCOUNT
    ).json()

    assert body["mode"] == "hybrid"  # 默认模式
    assert body["elapsed_ms"] >= 0
    assert body["hits"][0]["title"] == "预算说明"


def test_keyword_mode_hits_literal_terms_only(client: TestClient) -> None:
    _ingest(client, "客户 A 合同", CONTRACT)
    _ingest(client, "预算说明", BUDGET)

    body = client.post(
        "/v1/knowledge/search",
        json={"query": "预算", "mode": "keyword"},
        headers=ACCOUNT,
    ).json()

    assert body["mode"] == "keyword"
    assert [hit["title"] for hit in body["hits"]] == ["预算说明"]
    assert body["hits"][0]["score"] > 0


def test_hybrid_prefers_both_route_match(client: TestClient) -> None:
    _ingest(client, "客户 A 合同", CONTRACT)
    _ingest(client, "预算说明", BUDGET)
    _ingest(client, "产品路线图", PRODUCT)

    body = client.post(
        "/v1/knowledge/search", json={"query": "知识库混合检索"}, headers=ACCOUNT
    ).json()
    assert body["mode"] == "hybrid"
    assert body["hits"][0]["title"] == "产品路线图"


def test_vector_mode_ranks_by_cosine_similarity(client: TestClient) -> None:
    _ingest(client, "客户 A 合同", CONTRACT)
    _ingest(client, "预算说明", BUDGET)

    body = client.post(
        "/v1/knowledge/search",
        json={"query": "合同评审报价", "mode": "vector"},
        headers=ACCOUNT,
    ).json()
    assert body["mode"] == "vector"
    assert body["hits"][0]["title"] == "客户 A 合同"


def test_search_with_no_match_returns_empty(client: TestClient) -> None:
    _ingest(client, "客户 A 合同", CONTRACT)

    body = client.post(
        "/v1/knowledge/search", json={"query": "zzzz"}, headers=ACCOUNT
    ).json()
    assert body["hits"] == []


def test_search_rejects_invalid_mode_and_weight(client: TestClient) -> None:
    payload = {"query": "合同", "mode": "semantic"}
    assert (
        client.post("/v1/knowledge/search", json=payload, headers=ACCOUNT).status_code
        == 422
    )
    payload = {"query": "合同", "vector_weight": 1.5}
    assert (
        client.post("/v1/knowledge/search", json=payload, headers=ACCOUNT).status_code
        == 422
    )
