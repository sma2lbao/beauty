"""子代理委派(P1-E):父 run 用 ``spawn_subagent`` 起子 run,等结论回喂。

设计见 ``docs/design/subagents.md``:子 run 复用 runs 表与同一编排图
(``parent_kind="subagent"``),父 run 只新增一个过程项 ``kind="subagent"``;
执行是内联的(``jobs_enabled=False`` 语义,不进队列),handler 每 1s 轮询
子 run 终态 / 子 run 被取消 / 父 run 被取消 / 超时,然后把摘要与用量
回喂父模型。子 run 遇到审批闸时立即返回,由人工在其运行详情继续。

拆分后的实现见同目录兄弟模块:``subagents_spec``(常量与共享枚举)、
``subagents_policy``(参数校验/工具裁剪/深度与配额闸)、``subagents_runtime``
(内联执行/轮询/取消收尾)、``subagents_report``(父过程项落库与回喂 JSON)。
本文件保留 ``spawn_subagent`` 等入口,并原样重导出全部历史名字——调用方与
测试按 ``subagents.<name>`` 取用(``_wait_child`` 的 monkeypatch 也依赖入口
在本模块内解析这些名字)。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from common.errors import NotFoundError
from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.core.config import Settings
from agent_orchestrator.graph import items
from agent_orchestrator.models.runs import PARENT_KIND_SUBAGENT, Run
from agent_orchestrator.repositories import RunEventRepository, RunRepository
from agent_orchestrator.schemas.runs import RunStatus, RunUsageTotals, SubagentRead
from agent_orchestrator.services import usage_events
from agent_orchestrator.services.items import (
    append_item_event,
    latest_step_id,
    list_items,
    next_item_id,
    resolve_thread_id,
)
from agent_orchestrator.services.run_artifacts import get_artifact
from agent_orchestrator.services.run_events import append_event
from agent_orchestrator.services.run_execution import _invoke_plan
from agent_orchestrator.services.run_lifecycle import require_run
from agent_orchestrator.services.subagents_policy import (
    _check_limits,
    _parent_tools,
    _parse,
    _parse_timeout,
    _parse_tools,
    _Rejected,
    _Request,
    _required_text,
    _resolve_tools,
    _strip_self,
    _subagent_depth,
)
from agent_orchestrator.services.subagents_report import (
    _append_finish_item,
    _child_summary,
    _finish,
    _finish_aborted,
    _item_summaries,
    _usage_reply,
)
from agent_orchestrator.services.subagents_runtime import (
    _abort_child,
    _cancel_child,
    _cancel_text,
    _execute_child,
    _poll_cancelled,
    _wait_child,
)
from agent_orchestrator.services.subagents_spec import (
    _ABORT_CANCELLED,
    _ABORT_PARENT,
    _ABORT_TIMEOUT,
    _AWAITING_STATUSES,
    _TERMINAL_STATUSES,
    CANCELLED_REASON,
    DEFAULT_AGENT_NAME,
    MAX_AGENT_NAME_CHARS,
    MAX_INSTRUCTIONS_CHARS,
    MAX_TIMEOUT_SECONDS,
    MAX_TITLE_CHARS,
    MAX_WALK_DEPTH,
    MIN_TIMEOUT_SECONDS,
    PARENT_CANCELLED_REASON,
    POLL_SECONDS,
    SPAWN_TOOL_NAME,
    TIMEOUT_REASON,
)
from agent_orchestrator.services.tool_context import current_tool_context
from agent_orchestrator.services.tool_names import normalize_tool_names
from agent_orchestrator.services.tools import ToolRisk, ToolSpec

logger = logging.getLogger(__name__)

#: 对外保留的历史名字(调用方与测试按 ``subagents.<name>`` 取用)。
__all__ = [
    "CANCELLED_REASON",
    "DEFAULT_AGENT_NAME",
    "MAX_AGENT_NAME_CHARS",
    "MAX_INSTRUCTIONS_CHARS",
    "MAX_TIMEOUT_SECONDS",
    "MAX_TITLE_CHARS",
    "MAX_WALK_DEPTH",
    "MIN_TIMEOUT_SECONDS",
    "NotFoundError",
    "PARENT_CANCELLED_REASON",
    "RunEventRepository",
    "POLL_SECONDS",
    "RunUsageTotals",
    "SPAWN_TOOL_NAME",
    "TIMEOUT_REASON",
    "_ABORT_CANCELLED",
    "_ABORT_PARENT",
    "_ABORT_TIMEOUT",
    "_AWAITING_STATUSES",
    "_Request",
    "_Rejected",
    "_TERMINAL_STATUSES",
    "_abort_child",
    "_append_finish_item",
    "_cancel_child",
    "_cancel_text",
    "_check_limits",
    "_child_summary",
    "_error",
    "_execute_child",
    "_finish",
    "_finish_aborted",
    "_invoke_plan",
    "_item_summaries",
    "_parent_tools",
    "_parse",
    "_parse_timeout",
    "_parse_tools",
    "_poll_cancelled",
    "_reload",
    "_required_text",
    "_resolve_tools",
    "_strip_self",
    "_subagent_depth",
    "_timeout_value",
    "_usage_reply",
    "_wait_child",
    "get_artifact",
    "list_items",
    "normalize_tool_names",
    "build_spawn_tool",
    "list_subagents",
    "spawn_subagent",
]


def build_spawn_tool(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings,
    run_graph: Any = None,
    store: Any = None,
    workspace: Any = None,
    downloader: Any = None,
    context: Any = None,
) -> ToolSpec:
    """构造 ``spawn_subagent`` 工具(挂载点见 ``container_assemblies``)。"""

    async def spawn(args: dict[str, Any]) -> str:
        ctx = current_tool_context()
        if ctx is None or not ctx.run_id:
            return _dump(
                _error(
                    "subagent.invalid_arguments",
                    "spawn_subagent 只能在 run 执行上下文中使用",
                )
            )
        async with session_scope(session_factory) as session:
            parent = await session.get(Run, ctx.run_id)
            if parent is None or parent.tenant_id != ctx.tenant_id:
                return _dump(
                    _error(
                        "subagent.invalid_arguments",
                        "父 run 不存在或不属于当前租户",
                    )
                )
            result = await spawn_subagent(
                session,
                parent=parent,
                params=args,
                settings=settings,
                factory=session_factory,
                run_graph=run_graph,
                store=store,
                workspace=workspace,
                downloader=downloader,
                context=context,
            )
        return _dump(result)

    return ToolSpec(
        name=SPAWN_TOOL_NAME,
        description=(
            "派生一个子代理(独立子任务)去完成一件可自包含的工作,等它结束后"
            "拿到摘要与用量。适合与主任务解耦的调研/起草/校验;子代理不能再"
            "派生子代理。title 是短标题,task 写清目标与验收标准。"
        ),
        parameters=_parameters(settings),
        handler=spawn,
        risk=ToolRisk.WRITE,
    )


def _parameters(settings: Settings) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "子任务短标题(1..120 字,用做子 run 标题)",
            },
            "task": {
                "type": "string",
                "description": (
                    "子代理要完成的任务与验收标准"
                    f"(≤{settings.subagent_task_max_chars} 字)"
                ),
            },
            "agent_name": {
                "type": "string",
                "description": f"子代理名称(≤{MAX_AGENT_NAME_CHARS} 字,默认「子代理」)",
            },
            "instructions": {
                "type": "string",
                "description": (
                    "角色/边界/工作方式(注入子 run 系统提示,"
                    f"≤{MAX_INSTRUCTIONS_CHARS} 字)"
                ),
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": settings.subagent_max_tools,
                "description": "子代理可用工具白名单(只能收窄父级白名单)",
            },
            "timeout_seconds": {
                "type": "number",
                "minimum": int(MIN_TIMEOUT_SECONDS),
                "maximum": int(MAX_TIMEOUT_SECONDS),
                "description": "子代理超时秒数(默认 600)",
            },
        },
        "required": ["title", "task"],
    }


async def spawn_subagent(
    session: AsyncSession,
    *,
    parent: Run,
    params: dict[str, Any],
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    run_graph: Any = None,
    store: Any = None,
    workspace: Any = None,
    downloader: Any = None,
    context: Any = None,
) -> dict[str, Any]:
    """建子 run、内联执行、轮询收尾,返回回喂父模型的 JSON 摘要(§6)。

    与契约签名相比多一个必填 ``factory``:轮询与子 run 执行需要独立会话
    (父 run 的执行会话不能并发读写),见交付说明。
    """

    try:
        request = _parse(params, settings=settings)
        agent_tools = _resolve_tools(request, parent)
        await _check_limits(session, parent, settings)
    except _Rejected as exc:
        return _error(exc.code, exc.detail)
    if run_graph is None:
        return _error("subagent.invalid_arguments", "编排图未装配,无法执行子代理")

    started_at = datetime.now(UTC)
    child = Run(
        tenant_id=parent.tenant_id,
        title=request.title,
        goal=request.task,
        status=RunStatus.PENDING.value,
        acceptance_criteria=[],
        parent_run_id=parent.id,
        parent_kind=PARENT_KIND_SUBAGENT,
        agent_name=request.agent_name,
        agent_prompt=request.instructions,
        agent_tools=agent_tools,
        conversation_id=None,
        workspace_id=parent.workspace_id,
        mcp_server_ids=list(parent.mcp_server_ids or []),
    )
    await RunRepository(session).add(child)
    await append_event(
        session,
        child,
        "run.created.v1",
        {
            "title": child.title,
            "parent_run_id": parent.id,
            "parent_kind": PARENT_KIND_SUBAGENT,
            "agent_name": request.agent_name,
        },
    )

    thread_id = await resolve_thread_id(session, parent)
    item_id = await next_item_id(session, parent, items.KIND_SUBAGENT)
    step_id = await latest_step_id(session, parent, "execute")
    base_data = {
        "child_run_id": child.id,
        "agent_name": request.agent_name,
        "title": request.title,
        "tools": agent_tools,
        "timeout_seconds": _timeout_value(request.timeout_seconds),
    }
    await append_item_event(
        session,
        parent,
        thread_id=thread_id,
        item_id=item_id,
        kind=items.KIND_SUBAGENT,
        phase=items.PHASE_STARTED,
        title=request.title,
        data=base_data,
        node="execute",
        step_id=step_id,
        started_at=started_at,
    )
    child_id = child.id
    # 子 run 行与其 created 事件必须对执行会话可见,故先提交再起任务。
    await session.commit()

    task = asyncio.create_task(
        _execute_child(
            factory,
            child_id,
            run_graph=run_graph,
            store=store,
            workspace=workspace,
            downloader=downloader,
            context=context,
            settings=settings,
        )
    )
    abort = await _wait_child(
        factory,
        parent_id=parent.id,
        child_id=child_id,
        task=task,
        timeout_seconds=request.timeout_seconds,
    )
    if abort is not None:
        await _abort_child(task)
        # 取消与正常结束可能擦肩:任务已取消才算中止,否则按状态收尾。
        racing = await _reload(session, child_id)
        if racing is not None and racing.status not in _TERMINAL_STATUSES:
            child = await _cancel_child(factory, child_id, _cancel_text(abort))
            return await _finish_aborted(
                session,
                parent,
                child,
                thread_id=thread_id,
                item_id=item_id,
                request=request,
                base_data=base_data,
                started_at=started_at,
                abort=abort,
            )

    child = await _reload(session, child_id)
    if child is None:
        return _error("subagent.invalid_arguments", "子 run 在执行过程中丢失")
    return await _finish(
        session,
        parent,
        child,
        thread_id=thread_id,
        item_id=item_id,
        title=request.title,
        base_data=base_data,
        started_at=started_at,
        store=store,
        settings=settings,
    )


async def list_subagents(
    session: AsyncSession, tenant_id: str, run_id: str
) -> list[SubagentRead]:
    """GET /runs/{id}/subagents:先 require_run(跨租户 404),空数组合法。"""

    await require_run(session, tenant_id, run_id)
    children = await RunRepository(session).list_scoped(
        tenant_id,
        Run.parent_run_id == run_id,
        Run.parent_kind == PARENT_KIND_SUBAGENT,
        order_by=[Run.created_at, Run.id],
    )
    if not children:
        return []
    summaries = await _item_summaries(session, tenant_id, run_id)
    result: list[SubagentRead] = []
    for child in children:
        usage = await usage_events.usage_totals(session, tenant_id, child.id)
        result.append(
            SubagentRead(
                run_id=child.id,
                title=child.title,
                agent_name=child.agent_name,
                status=RunStatus(child.status),
                status_reason=child.status_reason,
                parent_run_id=child.parent_run_id,
                created_at=child.created_at,
                usage=usage,
                summary=summaries.get(child.id),
            )
        )
    return result


async def _reload(session: AsyncSession, child_id: str) -> Run | None:
    """取子 run 已提交的最新状态(绕过本会话 identity map 里的旧快照)。"""

    await session.commit()
    return await session.get(Run, child_id, populate_existing=True)


def _timeout_value(seconds: float) -> float | int:
    return int(seconds) if float(seconds).is_integer() else seconds


def _error(code: str, detail: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "detail": detail}


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)
