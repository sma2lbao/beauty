"""知识库仓储:账号过滤与 404 语义在此声明一次(§9)。

检索是"分块 join 文档"的复杂查询,作为**显式命名方法**留在分块仓储里——
比散在业务层的裸 SQL 更容易审计,也避免把 join 细节漏给上层。

两条检索路径(SQL 只粗筛,打分与融合在 `knowledge.services.search`):

- `vector_candidates`:Postgres 上用 `<=>` + ORDER BY + LIMIT 走 HNSW 索引;
- `keyword_candidates`:用 `ILIKE` 走 `pg_trgm` 索引做字面粗筛。

两条语句都恒定带账号过滤,且只取"非空向量"——换嵌入维度后没重建的块不参与向量检索。
"""

from __future__ import annotations

from database.repository import AccountRepository
from sqlalchemy import Select, bindparam, or_, select

from knowledge.models.knowledge import DocumentChunk, KnowledgeDocument

#: ILIKE 的转义符:用户查询里的 % 和 _ 必须当字面字符,否则会变成通配符。
_LIKE_ESCAPE = "\\"


def _escape_like(term: str) -> str:
    return (
        term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )


def vector_statement(
    account_id: str, query_vector: list[float], limit: int
) -> Select[tuple[DocumentChunk, str, float]]:
    """向量近邻语句:按余弦距离升序取前 limit 条(Postgres 走 HNSW)。

    余弦距离 ∈ [0, 2],上层用 `1 - distance` 还原成相似度。
    """
    # 显式给查询向量绑上列类型:asyncpg 把 vector 参数当文本处理,
    # 不标类型的话 SQLAlchemy 会把 list 原样交给驱动,而 pgvector 的 bind_processor
    # 才是负责把 list 转成 `[1,2,...]` 的那一步。
    query = bindparam("query_vector", query_vector, type_=DocumentChunk.embedding.type)
    distance = DocumentChunk.embedding.cosine_distance(query).label("distance")
    return (
        select(DocumentChunk, KnowledgeDocument.title, distance)
        .join(KnowledgeDocument, KnowledgeDocument.id == DocumentChunk.document_id)
        .where(
            DocumentChunk.account_id == account_id,
            DocumentChunk.embedding.is_not(None),
        )
        .order_by(distance)
        .limit(limit)
    )


def keyword_statement(
    account_id: str, terms: list[str], limit: int
) -> Select[tuple[DocumentChunk, str]]:
    """关键词粗筛语句:任一查询词字面出现即入选,精确排序交给进程内打分。"""
    conditions = [
        DocumentChunk.content.ilike(f"%{_escape_like(term)}%", escape=_LIKE_ESCAPE)
        for term in terms
    ]
    return (
        select(DocumentChunk, KnowledgeDocument.title)
        .join(KnowledgeDocument, KnowledgeDocument.id == DocumentChunk.document_id)
        .where(DocumentChunk.account_id == account_id, or_(*conditions))
        # ILIKE 只做粗筛,排序交给进程内打分;这里按 (ordinal, id) 取,
        # 保证同一查询在 Postgres 上可复现,不会因堆顺序变化换一批候选。
        .order_by(DocumentChunk.ordinal, DocumentChunk.id)
        .limit(limit)
    )


class KnowledgeDocumentRepository(AccountRepository[KnowledgeDocument]):
    model = KnowledgeDocument
    not_found_code = "knowledge.document_not_found"
    not_found_message = "文档不存在"
    id_detail_key = "document_id"


class DocumentChunkRepository(AccountRepository[DocumentChunk]):
    model = DocumentChunk
    not_found_code = "knowledge.chunk_not_found"
    not_found_message = "分块不存在"
    id_detail_key = "chunk_id"

    async def delete_for_document(self, account_id: str, document_id: str) -> int:
        """删除某文档的全部分块(删除文档时连带清理)。"""
        return await self.delete_where(
            account_id, DocumentChunk.document_id == document_id
        )

    async def list_chunks(
        self, account_id: str, document_id: str
    ) -> list[DocumentChunk]:
        """按 ordinal 取某文档的全部分块(引用正文用,恒带账号过滤)。"""
        rows = await self.session.execute(
            select(DocumentChunk)
            .where(
                DocumentChunk.account_id == account_id,
                DocumentChunk.document_id == document_id,
            )
            .order_by(DocumentChunk.ordinal, DocumentChunk.id)
        )
        return list(rows.scalars().all())

    async def searchable_rows(self, account_id: str) -> list[tuple[DocumentChunk, str]]:
        """检索用的一跳查询:返回 (分块, 文档标题),恒定带账号过滤。

        只在没有向量索引的环境(SQLite 测试/离线)使用:向量与关键词候选都在
        进程内从这批行里算。
        """
        rows = await self.session.execute(
            select(DocumentChunk, KnowledgeDocument.title)
            .join(KnowledgeDocument, KnowledgeDocument.id == DocumentChunk.document_id)
            .where(DocumentChunk.account_id == account_id)
        )
        return [(chunk, title) for chunk, title in rows.all()]

    async def vector_candidates(
        self, account_id: str, query_vector: list[float], limit: int
    ) -> list[tuple[DocumentChunk, str, float]]:
        """向量候选(相似度降序):SQL 只负责按 `<=>` 取近邻,相似度在此换算。"""
        result = await self.session.execute(
            vector_statement(account_id, query_vector, limit)
        )
        hits = [
            (chunk, title, 1.0 - float(distance))
            for chunk, title, distance in result.all()
        ]
        hits.sort(key=lambda item: (-item[2], item[0].ordinal, item[0].id))
        return hits

    async def keyword_candidates(
        self, account_id: str, terms: list[str], limit: int
    ) -> list[tuple[DocumentChunk, str]]:
        """关键词候选(未经打分,由上层按覆盖率排序)。"""
        if not terms:
            return []
        result = await self.session.execute(keyword_statement(account_id, terms, limit))
        return [(chunk, title) for chunk, title in result.all()]


__all__ = [
    "DocumentChunkRepository",
    "KnowledgeDocumentRepository",
    "keyword_statement",
    "vector_statement",
]
