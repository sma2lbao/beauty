"""知识库替身(FakeKnowledge):内存命中,记录检索/摄取调用(零网络)。"""

from __future__ import annotations


class FakeKnowledge:
    """假知识库:内存命中,记录检索调用(零网络)。"""

    def __init__(
        self,
        hits: list[dict] | None = None,
        fail: bool = False,
        provider: str = "local",
    ):
        self.hits = (
            hits
            if hits is not None
            else [
                {
                    "title": "客户 A 合同",
                    "score": 0.93,
                    "content": "合同评审安排在周四,需要准备报价单。",
                }
            ]
        )
        self.fail = fail
        self.provider = provider
        self.queries: list[tuple[str, str, int]] = []
        self.ingested: list[dict] = []
        self.documents: list[dict] = []

    async def search(self, tenant_id: str, query: str, limit: int) -> dict:
        self.queries.append((tenant_id, query, limit))
        if self.fail:
            raise RuntimeError("知识库挂了")
        return {
            "query": query,
            "provider": self.provider,
            "model": "hashing-256",
            "hits": self.hits[:limit],
        }

    # ---- 摄取(§8):outbox 消费者用 ----

    async def ingest_document(
        self,
        tenant_id: str,
        *,
        title: str,
        content: str,
        source: str = "manual",
    ) -> dict:
        if self.fail:
            raise RuntimeError("知识库挂了")
        self.ingested.append(
            {
                "tenant_id": tenant_id,
                "title": title,
                "content": content,
                "source": source,
            }
        )
        document = {
            "id": f"doc-{len(self.ingested)}",
            "title": title,
            "source": source,
        }
        self.documents.append(document)
        return document

    async def list_documents(self, tenant_id: str, *, limit: int = 50) -> list[dict]:
        if self.fail:
            raise RuntimeError("知识库挂了")
        return [
            doc for doc in self.documents if doc.get("tenant_id") in (None, tenant_id)
        ][:limit]
