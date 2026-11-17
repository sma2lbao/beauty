"""subagent_wait 节点(L2):父 run 挂起等子代理终态,完成后把回执写回对话。

异步子代理的最小闭环:
- execute 收到 spawn_subagent 的 pending 回执后返回 pending_subagents,路由到本节点;
- 本节点 interrupt 挂起父 run(状态 awaiting_subagent),不占 worker;
- 子 run 到终态时 services 侧入队 subagent job,唤醒父 run 并带
  Command(resume) 回到本节点;
- 回执以 **user 角色**消息写进 tool_history:spawn 的 tool 消息已被入队 ack
  占用,OpenAI 协议下同一次 tool_call_id 不能有第二条 tool 消息。

节点保持纯函数约定:只读 state、返回增量,不查库、不发事件。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from langgraph.types import interrupt

from agent_orchestrator.graph.state import RunGraphState

#: execute 与 services 之间的契约键:待等待的子代理(子 run id + 标题)。
PENDING_KEY = "pending_subagents"
#: 中断载荷 kind(services 按它把 run 落成 awaiting_subagent)。
INTERRUPT_KIND = "subagent_wait"
#: 回执消息角色:user——tool 消息需要 tool_call_id 配对,ack 已占用那一次。
NOTICE_ROLE = "user"


def pending_spawn_ack(output: str) -> dict[str, Any] | None:
    """从工具输出里认出"子代理已入队"的回执(services → graph 的契约)。

    services.subagents 在 jobs_enabled 时不再内联等子 run,而是回一条
    {"ok": true, "pending": true, "subagent_run_id": ..., "title": ...};
    这里只认 pending 标记,非 JSON/内联路径的普通回执一律返回 None。
    """

    text = (output or "").strip()
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("pending") is not True:
        return None
    child_id = payload.get("subagent_run_id")
    if not isinstance(child_id, str) or not child_id:
        return None
    title = payload.get("title")
    return {"child_run_id": child_id, "title": title if isinstance(title, str) else ""}


def make_subagent_wait_node():
    """构造等待节点:一次 interrupt 只等当前这批子代理,回执写回后回 execute。"""

    def subagent_wait_node(state: RunGraphState, config=None) -> dict[str, Any]:
        del config
        pending = [
            item
            for item in (state.get(PENDING_KEY) or [])
            if isinstance(item, Mapping) and item.get("child_run_id")
        ]
        if not pending:
            # 没有待等待项(旧 checkpoint/多入口):直接放行,避免空转
            return {PENDING_KEY: [], "subagent_notices": []}
        # interrupt 之前零副作用:恢复时本节点会被重放,回执只从 resume 载荷取
        answer = interrupt(
            {
                "kind": INTERRUPT_KIND,
                "children": [
                    {
                        "child_run_id": str(item["child_run_id"]),
                        "title": str(item.get("title") or ""),
                    }
                    for item in pending
                ],
            }
        )
        notices = _notices(answer, pending)
        history = list(state.get("tool_history") or [])
        text = _notice_text(notices)
        if text:
            history.append({"role": NOTICE_ROLE, "content": text})
        return {
            PENDING_KEY: [],
            "subagent_notices": notices,
            "tool_history": history,
        }

    return subagent_wait_node


def _notices(answer: Any, pending: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """把 resume 载荷归一成回执列表;缺项按待等待项补一条 unknown。"""

    raw = answer.get("children") if isinstance(answer, Mapping) else None
    by_id = {str(item["child_run_id"]): item for item in pending}
    notices: list[dict[str, Any]] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, Mapping):
            continue
        child_id = str(entry.get("child_run_id") or "")
        if not child_id:
            continue
        base = by_id.get(child_id) or {}
        notices.append(
            {
                "child_run_id": child_id,
                "title": str(entry.get("title") or base.get("title") or ""),
                "status": str(entry.get("status") or ""),
                "summary": str(entry.get("summary") or ""),
                "reason": str(entry.get("reason") or ""),
            }
        )
    known = {notice["child_run_id"] for notice in notices}
    for child_id, base in by_id.items():
        if child_id in known:
            continue
        notices.append(
            {
                "child_run_id": child_id,
                "title": str(base.get("title") or ""),
                "status": "",
                "summary": "",
                "reason": "",
            }
        )
    return notices


def _notice_text(notices: list[dict[str, Any]]) -> str:
    if not notices:
        return ""
    lines = ["[子代理结果回执]"]
    for index, notice in enumerate(notices, start=1):
        title = notice["title"] or "子代理"
        status = notice["status"] or "unknown"
        lines.append(f"{index}. {title}({status})")
        detail = notice["summary"] or notice["reason"]
        lines.append(detail if detail else "(无摘要)")
    lines.append(
        "以上子代理已结束;请基于这些结果继续当前任务,需要时再调用 spawn_subagent。"
    )
    return "\n".join(lines)
