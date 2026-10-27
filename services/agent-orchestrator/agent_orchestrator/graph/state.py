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
    auto_approved: bool  # 审批闸自动放行(工具面里没有 DANGER 级工具)
    results: Annotated[list[str], add]
    rounds: Annotated[int, add]
    tool_calls: Annotated[list[dict[str, Any]], add]  # 工具调用审计(services 层落事件)
    extra_instructions: list[str]  # 用户追加指令(§6.5),在下一个边界并入上下文
    conversation_history: list[dict[str, str]]  # 对话多轮上下文(services 层建图时装载)
    # 长期记忆召回(§6.7):services 层起图时按目标检索并拼成提示块,节点只读不改;
    # 缺省=没召回(旧 checkpoint 兼容,节点统一用 .get 读)。
    memory_block: str
    # 上下文工程管线装配结果(§7.3):services 层起图前把渲染好的注入块与
    # 分段/策略快照写进 state,节点只读不改;缺省=没装配(旧 checkpoint 兼容)。
    context_block: str
    context_snapshot: dict[str, Any]
    # 任务清单(M4):services 层起图/resume 时从 runs.todos 装载,execute 节点在
    # 工具结果里拿到新清单后回写;缺省=无清单(旧 checkpoint 兼容,.get 读)。
    todos: list[dict[str, str]]
    skill_id: str  # Skill 绑定(run 创建时快照)
    skill_system_prompt: str  # Skill 的系统提示词
    # 上传技能包快照(P3):version_id/object_key/sha256/entrypoint/runtime/
    # network/timeout_seconds;内置技能无此项
    skill_package: dict[str, Any]
    allowed_tools: list[str]  # Skill 工具白名单;缺省=不限制,空表=不用工具
    # P0-4 审批策略快照:[{"tool": "name"|"*", "action": "auto"|"confirm"|"deny"}]
    # 由 services 层在起图时加载(节点纯函数,不查库);空表=按风险等级默认
    approval_policies: list[dict[str, str]]
    tool_history: list[dict[str, Any]]  # 工具对话历史(execute ↔ tool_exec 间传递)
    pending_writes: list[dict[str, Any]]  # 待人工确认的写操作(§6.2)
    writes_approved: bool  # 确认闸的判定结果
    confirm_cycles: Annotated[int, add]  # 确认轮次(上限见 nodes.MAX_CONFIRM_CYCLES)
    # 交互提问(docs/design/tool-service.md §10.2):execute 遇到 interaction 工具
    # 调用后置待答问题,ask_gate 是唯一 interrupt 点;全部用 .get 读以兼容旧
    # checkpoint(发布说明要求 drain 在途 run,这里是兜底)。
    pending_question: dict[str, Any]
    question_answers: Annotated[list[dict[str, Any]], add]  # 每次回答一条
    ask_cycles: Annotated[int, add]  # 提问轮次(上限见 nodes.MAX_ASK_CYCLES)
    artifact: dict[str, Any]  # 交付物(title/kind/content),services 层落库
    verified: dict[str, bool]
    final_status: str
    status_reason: str  # blocked/failed 的原因(由 status 决定含义)
