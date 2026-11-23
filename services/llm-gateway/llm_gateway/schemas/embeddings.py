"""嵌入契约:文本进,向量出(内部服务不感知提供商)。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from llm_gateway.schemas.completions import UsageOut


class EmbeddingRequest(BaseModel):
    model: str | None = None  # 不传走 default_embedding_model(§5 路由)
    # 一次可以提交很多行:知识库把整篇文档的所有块打包发来(1MB 正文按 400 字
    # 分块 ≈ 2900 块)。真正的单请求行数上限由网关按 embedding_batch_size 切开
    # 逐批调上游,这里的上限只挡住离谱的请求体(不做业务分批)。
    input: list[str] = Field(min_length=1, max_length=8192)


class EmbeddingResponse(BaseModel):
    model: str
    embeddings: list[list[float]]
    usage: UsageOut | None = None
