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
    extra_instructions: list[str]  # 用户追加指令(§6.5),在下一个边界并入上下文
    conversation_history: list[dict[str, str]]  # 对话多轮上下文(services 层建图时装载)
    skill_id: str  # Skill 绑定(run 创建时快照)
    skill_system_prompt: str  # Skill 的系统提示词
    # 上传技能包快照(P3):version_id/object_key/sha256/entrypoint/runtime/
    # network/timeout_seconds;内置技能无此项
    skill_package: dict[str, Any]
    allowed_tools: list[str]  # Skill 工具白名单;缺省=不限制
    tool_history: list[dict[str, Any]]  # 工具对话历史(execute ↔ tool_exec 间传递)
    pending_writes: list[dict[str, Any]]  # 待人工确认的写操作(§6.2)
    writes_approved: bool  # 确认闸的判定结果
    confirm_cycles: Annotated[int, add]  # 确认轮次(上限见 nodes.MAX_CONFIRM_CYCLES)
    artifact: dict[str, Any]  # 交付物(title/kind/content),services 层落库
    verified: dict[str, bool]
    final_status: str
    status_reason: str  # blocked/failed 的原因(由 status 决定含义)
