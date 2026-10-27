"""run 级任务清单(M4):`todo_write` 工具 + 只读查询(设计见 §13)。

真源三层(为什么是这三层见 §13.1):

- `runs.todos`:覆盖写的最新快照,起图/resume 时装载进 state;
- `run.todo.updated.v1`:每次变更一条事件(审计 + 前端可观测),
  revision 就是该事件条数,天然的单调版本号;
- `state["todos"]`:图内提示镜像,execute 每轮重建消息时注入(替换式)。

工具放在 orchestrator 而不是 tool-service:清单要 run 上下文(快照列、事件、
state),tool-service 是无状态执行面;同层先例见 services/memory_tools.py
与 context/skills.py 的 load_skill(同样按 ctx.run_id 挂载)。
"""

from __future__ import annotations

import json
from typing import Any

from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.graph.todos import (
    TODO_STATUSES,
    TODO_TOOL_NAME,
    sanitize_todos,
    todo_counts,
    validate_todos,
)
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import RunEventRepository
from agent_orchestrator.schemas.runs import TodoRead
from agent_orchestrator.services.run_events import append_event
from agent_orchestrator.services.run_lifecycle import require_run
from agent_orchestrator.services.tool_context import current_tool_context
from agent_orchestrator.services.tools import ToolRisk, ToolSpec

#: 清单变更事件(§13.3);P1-D 事件分类学落地时提升为 item kind=todo。
TODO_UPDATED_EVENT = "run.todo.updated.v1"
#: 清单规模上限:够拆一个可执行计划,又不会把上下文变成任务管理系统。
DEFAULT_MAX_ITEMS = 20
DEFAULT_MAX_CONTENT_CHARS = 200

SessionFactory = async_sessionmaker[AsyncSession]


def _error(message: str) -> str:
    """工具错误统一回喂 JSON,模型能读懂并自行修正参数。"""

    return json.dumps({"error": message}, ensure_ascii=False)


def _payload(
    todos: list[dict[str, str]], *, revision: int, updated: bool
) -> dict[str, Any]:
    """工具返回信封:清单 + 计数 + 版本,模型据此确认写成功。"""

    counts = todo_counts(todos)
    return {
        "ok": True,
        "updated": updated,
        "todos": todos,
        "counts": counts,
        "revision": revision,
        "summary": (
            f"清单已更新:{counts['completed']}/{len(todos)} 完成,"
            f"{counts['in_progress']} 项进行中"
        ),
    }


async def list_todos(session: AsyncSession, tenant_id: str, run_id: str) -> TodoRead:
    """读端:快照 + 最新 revision(未写过清单时 revision=0,数组为空)。"""

    run = await require_run(session, tenant_id, run_id)
    todos = sanitize_todos(run.todos)
    revision = 0
    latest = await RunEventRepository(session).latest_of_type(
        run.id, TODO_UPDATED_EVENT
    )
    if latest is not None and isinstance(latest.payload, dict):
        value = latest.payload.get("revision")
        if isinstance(value, int):
            revision = value
    return TodoRead(
        run_id=run.id,
        todos=todos,
        counts=todo_counts(todos),
        revision=revision,
    )


def build_todo_tool(
    session_factory: SessionFactory,
    *,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
) -> ToolSpec:
    """构造 `todo_write` 工具(WRITE):覆盖写本 run 的任务清单。

    risk=WRITE 而不是 READ:它会写 run 状态;若标 READ,chat 路径的只读工具
    裁剪(_read_only_toolkit)会把它留下来——对话里就多出一个永远不可用的工具。
    """

    async def todo_write(args: dict[str, Any]) -> str:
        context = current_tool_context()
        if context is None or not context.run_id:
            return _error("缺少工具上下文(run),无法更新任务清单")
        try:
            todos = validate_todos(
                args.get("todos"),
                max_items=max_items,
                max_content_chars=max_content_chars,
            )
        except ValueError as exc:
            return _error(str(exc))
        async with session_scope(session_factory) as session:
            run = await session.get(Run, context.run_id)
            if run is None or run.tenant_id != context.tenant_id:
                return _error("run 不存在或不属于当前租户")
            events = RunEventRepository(session)
            revision = await events.count_of_type(run.id, TODO_UPDATED_EVENT) + 1
            if sanitize_todos(run.todos) == todos:
                # 幂等:模型常把整段清单原样重发(尤其 just 改一项时),
                # 内容没变就不写库、不落事件,revision 也不推进。
                return json.dumps(
                    _payload(todos, revision=revision - 1, updated=False),
                    ensure_ascii=False,
                )
            run.todos = todos
            await append_event(
                session,
                run,
                TODO_UPDATED_EVENT,
                {
                    "todos": todos,
                    "counts": todo_counts(todos),
                    "revision": revision,
                    "source": "tool",
                },
            )
        return json.dumps(
            _payload(todos, revision=revision, updated=True), ensure_ascii=False
        )

    return ToolSpec(
        name=TODO_TOOL_NAME,
        description=(
            "覆盖写本 run 的任务清单(每次传完整清单,不是增量)。开工前先把计划拆成"
            " 3-8 项写入;开始某项前把它设为 in_progress(同时最多一项),做完立刻设为"
            " completed;清单变化后随时重发完整清单。不要用它记录执行结果或日志。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": max_items,
                    "description": "完整清单(覆盖写):按执行顺序排列",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "一项任务(动词开头、可判定完成)",
                            },
                            "status": {
                                "type": "string",
                                "enum": list(TODO_STATUSES),
                                "description": (
                                    "pending=未开始,in_progress=进行中,completed=已完成"
                                ),
                            },
                        },
                        "required": ["content", "status"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["todos"],
            "additionalProperties": False,
        },
        handler=todo_write,
        risk=ToolRisk.WRITE,
    )
