"""知识库契约:登记文档与向量检索。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentCreate(BaseModel):
    """登记一篇文档:服务端负责分块与向量化。"""

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=1_000_000)
    source: str = Field(default="manual", max_length=32)


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    source: str
    size_bytes: int
    chunk_count: int
    embedding_model: str
    created_at: datetime


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int | None = Field(default=None, ge=1, le=50)


class SearchHit(BaseModel):
    """一条命中:分块正文 + 来源文档 + 相似度分数。"""

    chunk_id: str
    document_id: str
    title: str
    ordinal: int
    content: str
    score: float


class SearchResponse(BaseModel):
    """检索结果:附提供方信息,让调用方知道排序依据(§5 唯一出口)。"""

    query: str
    provider: str
    model: str
    hits: list[SearchHit]
