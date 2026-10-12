"""执行图节点(阶段 A 为确定性桩;LLM/工具接入点见各 docstring)。

约定(架构 §4 分层):节点是纯函数——只读 state、返回增量,
不碰数据库、不发事件;事务与事件在 services 层的图调用边界统一处理。
"""

from __future__ import annotations

from langgraph.types import interrupt

from agent_orchestrator.graph.state import RunGraphState


def plan_node(state: RunGraphState) -> dict:
    """生成计划 Markdown(§6.1:plan 是可编辑工件)。

    桩实现:按目标与验收标准拼装结构化计划。
    接入点:LLM 生成计划走 llm-gateway(阶段 B),仅替换本函数。
    resume 重试时保留用户已编辑的计划,不覆盖。
    """
    if state.get("plan_markdown"):
        return {}
    criteria = state.get("acceptance_criteria") or []
    checklist = "\n".join(f"- [ ] {c}" for c in criteria) or "- [ ] (无显式验收标准)"
    plan = f"""# 执行计划:{state["title"]}

## 目标

{state["goal"]}

## 步骤

1. 解析目标,拆解子任务
2. 执行子任务并记录结果
3. 逐项核对验收标准
4. 产出交付物

## 验收标准

{checklist}
"""
    return {"plan_markdown": plan}


def approval_gate_node(state: RunGraphState) -> dict:
    """审批闸门:plan 之后中断,等待 approve/reject(§6.2 人工确认)。"""
    answer = interrupt({"plan_markdown": state.get("plan_markdown", "")})
    if answer.get("action") != "approve":
        return {
            "final_status": "blocked",
            "blocked_reason": answer.get("reason") or "用户拒绝",
        }
    return {}


def execute_node(state: RunGraphState) -> dict:
    """执行计划。桩:产出确定性结果记录(工具调用/MCP 为阶段 B 接入点)。"""
    return {
        "results": ["目标已拆解(桩)", "子任务已执行(桩)", "结果已汇总(桩)"],
        "rounds": 1,
    }


def verify_node(state: RunGraphState) -> dict:
    """逐项核对验收标准(§6.1)。桩:全部通过;失败项驱动返工(见 builder 路由)。"""
    criteria = state.get("acceptance_criteria") or []
    return {"verified": {c: True for c in criteria}}


def deliver_node(state: RunGraphState) -> dict:
    """交付。桩:状态置 completed(交付物入对象存储为阶段 B)。"""
    return {"final_status": "completed"}
