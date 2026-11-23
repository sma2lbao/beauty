"""知识库契约:登记文档与检索。

检索的 `score` 语义**随 `mode` 变化**,响应里必须带上模式,调用方才能正确解释:

- `vector`:余弦相似度(0~1,<0 的命中直接丢弃);
- `keyword`:词法覆盖率(命中的查询词 / 全部查询词,0~1);
- `hybrid`:RRF 融合分(通常 0~0.03,只用于排序,不可跨查询比较)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from knowledge.services.rerank import RerankStatus

SearchMode = Literal["vector", "keyword", "hybrid"]


class DocumentCreate(BaseModel):
    """登记一篇文档:服务端负责分块与向量化。"""

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=1_000_000)
    source: str = Field(default="manual", max_length=64)


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    source: str
    size_bytes: int
    chunk_count: int
    embedding_model: str
    created_at: datetime


class DocumentContent(BaseModel):
    """按文档 id 取回可引用的正文(分块按 ordinal 拼接,可截断)。"""

    document_id: str
    title: str
    content: str
    truncated: bool
    chunk_count: int


class SearchRequest(BaseModel):
    """检索请求;不传 mode 时用服务端配置(search_default_mode,默认 hybrid)。"""

    query: str = Field(min_length=1, max_length=2000)
    limit: int | None = Field(default=None, ge=1, le=50)
    mode: SearchMode | None = None
    #: 仅 hybrid 生效:向量路权重(0~1),关键词路取 1 - vector_weight
    vector_weight: float | None = Field(default=None, ge=0, le=1)


class SearchHit(BaseModel):
    """一条命中:分块正文 + 来源文档 + 分数(语义随 `SearchResponse.mode`)。"""

    chunk_id: str
    document_id: str
    title: str
    ordinal: int
    content: str
    score: float


class SearchResponse(BaseModel):
    """检索结果:附提供方与模式,让调用方知道排序依据(§5 唯一出口)。

    `rerank` 是本次检索的重排状态:

    - `applied`:排序与 score 都来自重排模型(0~1 相关性,仍不可跨查询比较);
    - `skipped`:服务端启用了重排,但网关失败/候选不足,已退回 `mode` 的原始顺序;
    - `null`:服务端未启用重排(默认),响应与加功能前完全一致。
    """

    query: str
    provider: str
    model: str
    mode: SearchMode
    elapsed_ms: float
    rerank: RerankStatus | None = None
    rerank_model: str | None = None
    hits: list[SearchHit]
