"""LangGraph run 执行图的全局状态(§6 执行模型)。

节点只返回增量;results/rounds/tool_calls 用追加 reducer 聚合返工轮次与工具调用。
"""

from __future__ import annotations

from operator import add
from typing import Annotated, Any, TypedDict


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
    tool_calls: Annotated[list[dict[str, Any]], add]  # 工具调用审计(services 层落事件)
    verified: dict[str, bool]
    final_status: str
    blocked_reason: str
