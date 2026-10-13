"""知识库仓储:租户过滤与 404 语义在此声明一次(§9)。

检索是"分块 join 文档"的复杂查询,作为**显式命名方法**留在分块仓储里——
比散在业务层的裸 SQL 更容易审计,也避免把 join 细节漏给上层。
"""

from __future__ import annotations

from database.repository import TenantRepository
from sqlalchemy import select

from knowledge.models.knowledge import DocumentChunk, KnowledgeDocument


class KnowledgeDocumentRepository(TenantRepository[KnowledgeDocument]):
    model = KnowledgeDocument
    not_found_code = "knowledge.document_not_found"
    not_found_message = "文档不存在"
    id_detail_key = "document_id"


class DocumentChunkRepository(TenantRepository[DocumentChunk]):
    model = DocumentChunk
    not_found_code = "knowledge.chunk_not_found"
    not_found_message = "分块不存在"
    id_detail_key = "chunk_id"

    async def delete_for_document(self, tenant_id: str, document_id: str) -> int:
        """删除某文档的全部分块(删除文档时连带清理)。"""
        return await self.delete_where(
            tenant_id, DocumentChunk.document_id == document_id
        )

    async def searchable_rows(self, tenant_id: str) -> list[tuple[DocumentChunk, str]]:
        """检索用的一跳查询:返回 (分块, 文档标题),恒定带租户过滤。

        余弦相似度在 Python 侧算,所以这里只负责把候选块取全。
        """
        rows = await self.session.execute(
            select(DocumentChunk, KnowledgeDocument.title)
            .join(KnowledgeDocument, KnowledgeDocument.id == DocumentChunk.document_id)
            .where(DocumentChunk.tenant_id == tenant_id)
        )
        return [(chunk, title) for chunk, title in rows.all()]
