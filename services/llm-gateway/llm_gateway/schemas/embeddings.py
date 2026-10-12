"""嵌入契约:文本进,向量出(内部服务不感知提供商)。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from llm_gateway.schemas.completions import UsageOut


class EmbeddingRequest(BaseModel):
    model: str | None = None  # 不传走 default_embedding_model(§5 路由)
    input: list[str] = Field(min_length=1, max_length=64)


class EmbeddingResponse(BaseModel):
    model: str
    embeddings: list[list[float]]
    usage: UsageOut | None = None
