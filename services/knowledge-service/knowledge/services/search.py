"""检索编排:向量近邻 + 关键词匹配 → RRF 融合。

分工:
- **SQL 负责粗筛**:向量候选在 Postgres 上走 HNSW(`<=>` + ORDER BY + LIMIT),
  关键词候选走 `pg_trgm`(ILIKE);两路各自只捞 `candidate_limit` 条;
- **进程内负责打分与融合**:词法覆盖率、RRF 融合都可复现,不依赖数据库扩展的
  ranking 实现,SQLite(测试/离线)与 Postgres 走同一套排序代码。

SQLite 没有向量索引:向量候选退化为"取本账号全部块在进程内算余弦"(改造前的行为),
排序结果一致,只是数据量大时更慢——这条分支只用于测试与离线覆盖库。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from knowledge.models.knowledge import DocumentChunk
from knowledge.repositories import DocumentChunkRepository
from knowledge.services.embeddings import EmbeddingProvider, cosine_similarity
from knowledge.services.lexical import lexical_score, lexical_terms

VECTOR_MODE = "vector"
KEYWORD_MODE = "keyword"
HYBRID_MODE = "hybrid"
SEARCH_MODES = (VECTOR_MODE, KEYWORD_MODE, HYBRID_MODE)


@dataclass(frozen=True)
class Hit:
    """一条检索命中:分块 + 文档标题 + 本次排序口径下的分数。"""

    chunk: DocumentChunk
    title: str
    score: float


def resolve_mode(mode: str | None, default: str = HYBRID_MODE) -> str:
    """规范化检索模式;未知值直接报错(配置写错就要在启动/首请求时暴露)。"""
    candidate = (mode or default or HYBRID_MODE).strip().lower()
    if candidate not in SEARCH_MODES:
        raise ValueError(f"未知的检索模式:{candidate}(支持 {'/'.join(SEARCH_MODES)})")
    return candidate


def _sort_key(item: tuple[DocumentChunk, str, float]) -> tuple[float, int, str]:
    chunk = item[0]
    return (-item[2], chunk.ordinal, chunk.id)


def _is_postgres(session: AsyncSession) -> bool:
    return session.get_bind().dialect.name == "postgresql"


async def _vector_candidates(
    session: AsyncSession,
    account_id: str,
    query_vector: list[float],
    limit: int,
) -> list[tuple[DocumentChunk, str, float]]:
    """向量候选:PG 用 ANN 索引;SQLite 退化为进程内余弦(全量取回)。"""
    repository = DocumentChunkRepository(session)
    if _is_postgres(session):
        scored = await repository.vector_candidates(account_id, query_vector, limit)
    else:
        rows = await repository.searchable_rows(account_id)
        scored = [
            (
                chunk,
                title,
                cosine_similarity(query_vector, list(chunk.embedding or [])),
            )
            for chunk, title in rows
        ]
    # 相似度 <= 0 的块与查询无关(本地哈希下常见),不参与融合,否则会平白挤进结果。
    # 两条分支都过滤,保证 SQLite 与 Postgres 的召回口径一致。
    scored = [item for item in scored if item[2] > 0]
    scored.sort(key=_sort_key)
    return scored[:limit]


async def _keyword_candidates(
    session: AsyncSession,
    account_id: str,
    terms: list[str],
    limit: int,
    *,
    fallback_rows: list[tuple[DocumentChunk, str]] | None = None,
) -> list[tuple[DocumentChunk, str, float]]:
    """关键词候选:PG 用 pg_trgm 粗筛;SQLite 直接在全量行上打分。"""
    repository = DocumentChunkRepository(session)
    if _is_postgres(session):
        rows = await repository.keyword_candidates(account_id, terms, limit)
    else:
        rows = fallback_rows or await repository.searchable_rows(account_id)
    scored = [
        (chunk, title, lexical_score(terms, chunk.content)) for chunk, title in rows
    ]
    hits = [item for item in scored if item[2] > 0]
    hits.sort(key=_sort_key)
    return hits[:limit]


def fuse(
    vector_hits: list[tuple[DocumentChunk, str, float]],
    keyword_hits: list[tuple[DocumentChunk, str, float]],
    *,
    rrf_k: int,
    vector_weight: float,
    limit: int,
) -> list[Hit]:
    """Reciprocal Rank Fusion:只用排名、不用原始分数,两路分数不可比也能融合。

    向量分是余弦(0~1),关键词分是覆盖率(0~1),尺度相近但分布不同;
    RRF 让"两路都排前"的块胜出,单路极高的块也不会被埋没。
    """
    keyword_weight = 1.0 - vector_weight
    merged: dict[str, list] = {}
    for rank, (chunk, title, _score) in enumerate(vector_hits, start=1):
        entry = merged.setdefault(chunk.id, [chunk, title, 0.0])
        entry[2] += vector_weight / (rrf_k + rank)
    for rank, (chunk, title, _score) in enumerate(keyword_hits, start=1):
        entry = merged.setdefault(chunk.id, [chunk, title, 0.0])
        entry[2] += keyword_weight / (rrf_k + rank)
    ordered = sorted(
        merged.values(), key=lambda item: (-item[2], item[0].ordinal, item[0].id)
    )
    return [
        Hit(chunk=chunk, title=title, score=score)
        for chunk, title, score in ordered[:limit]
    ]


async def search(
    session: AsyncSession,
    account_id: str,
    query: str,
    limit: int,
    embedder: EmbeddingProvider,
    *,
    mode: str | None = None,
    default_mode: str = HYBRID_MODE,
    rrf_k: int = 60,
    candidate_limit: int = 50,
    vector_weight: float = 0.5,
) -> tuple[list[Hit], str]:
    """按模式检索,返回 (命中, 实际使用的模式)。

    向量模式下 score 是余弦相似度,关键词模式是词法覆盖率,混合模式是 RRF 融合分
    ——调用方必须连同 `mode` 一起解释 score(见 `SearchResponse`)。
    """
    resolved = resolve_mode(mode, default_mode)
    limit = max(1, limit)
    candidate_limit = max(limit, candidate_limit)
    terms = lexical_terms(query)

    query_vector: list[float] | None = None
    if resolved in (VECTOR_MODE, HYBRID_MODE):
        vectors = await embedder.embed([query])
        query_vector = vectors[0] if vectors else None

    vector_hits: list[tuple[DocumentChunk, str, float]] = []
    if query_vector:
        vector_hits = await _vector_candidates(
            session, account_id, query_vector, candidate_limit
        )

    if resolved == VECTOR_MODE:
        hits = [
            Hit(chunk=chunk, title=title, score=score)
            for chunk, title, score in vector_hits
        ]
        return hits[:limit], resolved

    fallback_rows = None
    if not _is_postgres(session):
        fallback_rows = await DocumentChunkRepository(session).searchable_rows(
            account_id
        )
    keyword_hits = (
        await _keyword_candidates(
            session, account_id, terms, candidate_limit, fallback_rows=fallback_rows
        )
        if terms
        else []
    )

    if resolved == KEYWORD_MODE:
        hits = [
            Hit(chunk=chunk, title=title, score=score)
            for chunk, title, score in keyword_hits
        ]
        return hits[:limit], resolved

    return (
        fuse(
            vector_hits,
            keyword_hits,
            rrf_k=rrf_k,
            vector_weight=vector_weight,
            limit=limit,
        ),
        resolved,
    )
