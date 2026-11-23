"""排序(rerank)契约:查询 + 候选文档进,逐条相关性分数出(§5 唯一出口)。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from llm_gateway.schemas.completions import UsageOut


class RerankRequest(BaseModel):
    """不传 model 走 default_rerank_model(§5 路由)。

    documents 只放候选分块的正文:知识库先用混合检索粗排 50 条以内再送进来,
    不是为了替代召回。百炼 qwen3-rerank 单请求最多 500 条(单条 4k token),
    这里的上限只挡离谱请求体,不代替上游限制。
    """

    model: str | None = None
    query: str = Field(min_length=1)
    documents: list[str] = Field(min_length=1, max_length=500)
    #: 只保留前 N 条;不传 = 全部返回(上游默认)
    top_n: int | None = Field(default=None, ge=1)


class RerankResult(BaseModel):
    """一条候选的排序结果:index 指向请求 documents 的下标。"""

    index: int
    relevance_score: float


class RerankResponse(BaseModel):
    model: str
    results: list[RerankResult]
    usage: UsageOut | None = None
