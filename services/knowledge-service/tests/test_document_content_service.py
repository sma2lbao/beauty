"""document_content 的单元测试:不经过 TestClient 线程,直接测拼接与截断。"""

from __future__ import annotations

from pathlib import Path

import pytest
from common.service_testkit.settings import sqlite_url
from database.base import Base
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from knowledge.models.knowledge import DocumentChunk, KnowledgeDocument
from knowledge.repositories import DocumentChunkRepository, KnowledgeDocumentRepository
from knowledge.services import knowledge as knowledge_service


@pytest.fixture
async def session(tmp_path: Path):
    engine = create_async_engine(sqlite_url(tmp_path, "unit.db"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session
    await engine.dispose()


async def _seed(
    session, account_id: str = "acct-1", parts: list[str] | None = None
) -> str:
    document = KnowledgeDocument(
        account_id=account_id,
        title="长文",
        source="manual",
        size_bytes=1,
        chunk_count=len(parts or []),
        embedding_model="hashing-256",
    )
    await KnowledgeDocumentRepository(session).add(document)
    chunks = DocumentChunkRepository(session)
    for ordinal, content in enumerate(parts or []):
        await chunks.add(
            DocumentChunk(
                account_id=account_id,
                document_id=document.id,
                ordinal=ordinal,
                content=content,
                embedding=[],
            )
        )
    await session.flush()
    return document.id


async def test_document_content_joins_chunks(session) -> None:
    document_id = await _seed(session, parts=["第一段", "第二段"])

    document, content, truncated = await knowledge_service.document_content(
        session, "acct-1", document_id, max_chars=100
    )

    assert document.id == document_id
    assert content == "第一段\n\n第二段"
    assert truncated is False


async def test_document_content_truncates_within_budget(session) -> None:
    document_id = await _seed(session, parts=["甲" * 40, "乙" * 40])

    _, content, truncated = await knowledge_service.document_content(
        session, "acct-1", document_id, max_chars=50
    )

    assert truncated is True
    assert len(content) <= 50
    assert content.startswith("甲" * 40)


async def test_document_content_empty_and_account_isolation(session) -> None:
    empty_id = await _seed(session, parts=[])
    _, content, truncated = await knowledge_service.document_content(
        session, "acct-1", empty_id, max_chars=100
    )
    assert (content, truncated) == ("", False)

    other_id = await _seed(session, account_id="acct-2", parts=["别人的资料"])
    with pytest.raises(Exception) as error:
        await knowledge_service.document_content(
            session, "acct-1", other_id, max_chars=100
        )
    assert getattr(error.value, "code", "") == "knowledge.document_not_found"
