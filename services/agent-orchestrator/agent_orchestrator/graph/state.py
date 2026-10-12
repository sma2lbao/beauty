"""LangGraph run 执行图的全局状态(§6 执行模型)。

节点只返回增量;results/rounds 用追加 reducer 聚合返工轮次。
"""

from __future__ import annotations

from operator import add
from typing import Annotated, TypedDict


class RunGraphState(TypedDict, total=False):
    """graph 全流程共享状态。"""

    run_id: str
    tenant_id: str
    title: str
    goal: str
    acceptance_criteria: list[str]
    plan_markdown: str
    results: Annotated[list[str], add]
    rounds: Annotated[int, add]
    verified: dict[str, bool]
    final_status: str
    blocked_reason: str
