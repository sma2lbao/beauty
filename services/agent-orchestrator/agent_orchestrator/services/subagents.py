"""子代理委派(P1-E):父 run 用 ``spawn_subagent`` 起子 run,等结论回喂。

设计见 ``docs/design/subagents.md``:子 run 复用 runs 表与同一编排图
(``parent_kind="subagent"``),父 run 只新增一个过程项 ``kind="subagent"``;
执行是内联的(``jobs_enabled=False`` 语义,不进队列),handler 每 1s 轮询
子 run 终态 / 子 run 被取消 / 父 run 被取消 / 超时,然后把摘要与用量
回喂父模型。子 run 遇到审批闸时立即返回,由人工在其运行详情继续。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from common.errors import NotFoundError
from database.session import session_scope
from sqlalchemy import select
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
from agent_orchestrator.services.tool_context import current_tool_context
from agent_orchestrator.services.tool_names import normalize_tool_names
from agent_orchestrator.services.tools import ToolRisk, ToolSpec

logger = logging.getLogger(__name__)

#: 工具名/血缘枚举(与契约 §5/§3 一致)。
SPAWN_TOOL_NAME = "spawn_subagent"
DEFAULT_AGENT_NAME = "子代理"

MAX_TITLE_CHARS = 120
MAX_AGENT_NAME_CHARS = 40
MAX_INSTRUCTIONS_CHARS = 4000
MIN_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 1800.0
#: 轮询间隔(契约 §6:"每 1s 轮询")。
POLL_SECONDS = 1.0
#: 血缘上溯深度上限(防环,与 thread 解析同一口径)。
MAX_WALK_DEPTH = 8

TIMEOUT_REASON = "子代理超时"
CANCELLED_REASON = "已取消"
PARENT_CANCELLED_REASON = "父 run 已取消"

_AWAITING_STATUSES = {
    RunStatus.AWAITING_APPROVAL.value,
    RunStatus.AWAITING_CONFIRMATION.value,
    RunStatus.AWAITING_INPUT.value,
}
_TERMINAL_STATUSES = {
    RunStatus.COMPLETED.value,
    RunStatus.FAILED.value,
    RunStatus.BLOCKED.value,
    RunStatus.CANCELLED.value,
}

# 轮询中止原因(超时/取消的区分只影响 item 的 data.reason 与返回内容)
_ABORT_TIMEOUT = "timeout"
_ABORT_CANCELLED = "cancelled"
_ABORT_PARENT = "parent_cancelled"


@dataclass(frozen=True)
class _Request:
    """校验后的 spawn 参数(长度/范围都已在解析时收敛)。"""

    title: str
    task: str
    agent_name: str
    instructions: str | None
    tools: list[str] | None
    timeout_seconds: float


class _Rejected(Exception):
    """参数/上限校验失败:转成 ``subagent.*`` 工具结果回喂模型。"""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


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


def _parse(params: dict[str, Any], *, settings: Settings) -> _Request:
    """工具参数校验(长度/枚举/范围越界一律 invalid_arguments,§5)。"""

    title = _required_text(params.get("title"), field="title")
    if len(title) > MAX_TITLE_CHARS:
        raise _Rejected(
            "subagent.invalid_arguments", f"title 不能超过 {MAX_TITLE_CHARS} 字"
        )
    task = _required_text(params.get("task"), field="task")
    if len(task) > settings.subagent_task_max_chars:
        raise _Rejected(
            "subagent.invalid_arguments",
            f"task 不能超过 {settings.subagent_task_max_chars} 字",
        )

    raw_name = params.get("agent_name")
    if raw_name is None:
        agent_name = DEFAULT_AGENT_NAME
    elif isinstance(raw_name, str):
        agent_name = raw_name.strip() or DEFAULT_AGENT_NAME
        if len(agent_name) > MAX_AGENT_NAME_CHARS:
            raise _Rejected(
                "subagent.invalid_arguments",
                f"agent_name 不能超过 {MAX_AGENT_NAME_CHARS} 字",
            )
    else:
        raise _Rejected("subagent.invalid_arguments", "agent_name 必须是字符串")

    raw_instructions = params.get("instructions")
    instructions: str | None = None
    if raw_instructions is not None:
        if not isinstance(raw_instructions, str):
            raise _Rejected("subagent.invalid_arguments", "instructions 必须是字符串")
        instructions = raw_instructions.strip() or None
        if instructions is not None and len(instructions) > MAX_INSTRUCTIONS_CHARS:
            raise _Rejected(
                "subagent.invalid_arguments",
                f"instructions 不能超过 {MAX_INSTRUCTIONS_CHARS} 字",
            )

    tools = _parse_tools(params.get("tools"), settings=settings)
    timeout = _parse_timeout(params.get("timeout_seconds"), settings=settings)
    return _Request(
        title=title,
        task=task,
        agent_name=agent_name,
        instructions=instructions,
        tools=tools,
        timeout_seconds=timeout,
    )


def _parse_tools(raw: Any, *, settings: Settings) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise _Rejected("subagent.invalid_arguments", "tools 必须是字符串数组")
    # 剔除自身防递归(契约 §5):即使父白名单里意外带了也不允许再下放。
    tools = normalize_tool_names([item.strip() for item in raw if item.strip()])
    tools = [name for name in tools if name != SPAWN_TOOL_NAME]
    if len(tools) > settings.subagent_max_tools:
        raise _Rejected(
            "subagent.invalid_arguments",
            f"tools 最多 {settings.subagent_max_tools} 个",
        )
    return tools


def _parse_timeout(raw: Any, *, settings: Settings) -> float:
    if raw is None:
        value = float(settings.subagent_timeout_seconds)
    else:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise _Rejected("subagent.invalid_arguments", "timeout_seconds 必须是数字")
        value = float(raw)
        if not MIN_TIMEOUT_SECONDS <= value <= MAX_TIMEOUT_SECONDS:
            raise _Rejected(
                "subagent.invalid_arguments",
                f"timeout_seconds 必须在 {MIN_TIMEOUT_SECONDS:.0f}.."
                f"{MAX_TIMEOUT_SECONDS:.0f} 之间",
            )
    # 配置值也走 clamp(契约 §6 的 effective 语义),避免环境变量把超时放开。
    return min(max(value, MIN_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS)


def _required_text(raw: Any, *, field: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise _Rejected("subagent.invalid_arguments", f"{field} 必填且必须是非空字符串")
    return raw.strip()


def _parent_tools(parent: Run) -> list[str] | None:
    """父级有效白名单:``agent_tools ?? skill_tools ?? null``(§5)。

    继承时同样剔除 ``spawn_subagent`` 自身:白名单里意外带了这个名字时
    也不该把「继续派生」的能力下放给子代理(深度闸是第二道防线)。
    """

    if parent.agent_tools is not None:
        return _strip_self(normalize_tool_names(parent.agent_tools))
    if parent.skill_tools is not None:
        return _strip_self(normalize_tool_names(parent.skill_tools))
    return None


def _strip_self(names: list[str]) -> list[str]:
    return [name for name in names if name != SPAWN_TOOL_NAME]


def _resolve_tools(request: _Request, parent: Run) -> list[str] | None:
    """子代理工具面:只收窄;未传则原样继承父级有效白名单(可为 None)。"""

    parent_tools = _parent_tools(parent)
    if request.tools is None:
        return parent_tools
    if parent_tools is not None:
        allowed = set(parent_tools)
        extra = [name for name in request.tools if name not in allowed]
        if extra:
            raise _Rejected(
                "subagent.tools_not_subset",
                "以下工具不在父级白名单内:" + ", ".join(extra),
            )
    return request.tools


async def _check_limits(session: AsyncSession, parent: Run, settings: Settings) -> None:
    depth = await _subagent_depth(session, parent)
    if depth + 1 > settings.subagent_max_depth:
        raise _Rejected(
            "subagent.depth_exceeded",
            f"子代理最多嵌套 {settings.subagent_max_depth} 层",
        )
    used = await RunRepository(session).count(
        Run.tenant_id == parent.tenant_id,
        Run.parent_run_id == parent.id,
        Run.parent_kind == PARENT_KIND_SUBAGENT,
    )
    if used >= settings.subagent_max_per_run:
        raise _Rejected(
            "subagent.limit_exceeded",
            f"每个 run 最多 {settings.subagent_max_per_run} 个子代理",
        )


async def _subagent_depth(session: AsyncSession, run: Run) -> int:
    """当前 run 的子代理层数:顶层=0,子代理再派生=1(用于深度上限判定)。"""

    depth = 0
    current = run
    while current.parent_kind == PARENT_KIND_SUBAGENT and current.parent_run_id:
        if depth >= MAX_WALK_DEPTH:
            break
        parent = await session.get(Run, current.parent_run_id)
        if parent is None:
            break
        depth += 1
        current = parent
    return depth


async def _execute_child(
    factory: async_sessionmaker[AsyncSession],
    child_id: str,
    *,
    run_graph: Any,
    store: Any,
    workspace: Any,
    downloader: Any,
    context: Any,
    settings: Settings,
) -> None:
    """在独立会话里跑子 run 的 plan 语义(``jobs_enabled=False``,不进队列)。"""

    async with session_scope(factory) as session:
        child = await session.get(Run, child_id)
        if child is None:
            logger.warning("子 run 不存在,跳过执行(child_id=%s)", child_id)
            return
        await _invoke_plan(
            session,
            run_graph,
            child,
            store=store,
            workspace=workspace,
            downloader=downloader,
            settings=settings,
            context=context,
        )


async def _wait_child(
    factory: async_sessionmaker[AsyncSession],
    *,
    parent_id: str,
    child_id: str,
    task: asyncio.Task[None],
    timeout_seconds: float,
) -> str | None:
    """每秒轮询子 run 终态/取消与父 run 取消;超时返回 abort 原因(§6)。

    完成判定用 ``task.done()`` 而不是 DB 状态:子 run 的终态写在子执行会话里,
    只在会话退出时提交——轮询 DB 看不到"刚结束"的瞬间。
    """

    deadline = time.monotonic() + timeout_seconds
    while True:
        done, _ = await asyncio.wait({task}, timeout=POLL_SECONDS)
        if task in done:
            return None
        parent_cancelled, child_cancelled = await _poll_cancelled(
            factory, parent_id=parent_id, child_id=child_id
        )
        if parent_cancelled:
            return _ABORT_PARENT
        if child_cancelled:
            return _ABORT_CANCELLED
        if time.monotonic() >= deadline:
            return _ABORT_TIMEOUT


async def _poll_cancelled(
    factory: async_sessionmaker[AsyncSession], *, parent_id: str, child_id: str
) -> tuple[bool, bool]:
    """只读 status 列判取消(用独立会话,不碰父 run 执行会话)。"""

    async with session_scope(factory) as session:
        parent_status = await session.scalar(
            select(Run.status).where(Run.id == parent_id)
        )
        child_status = await session.scalar(
            select(Run.status).where(Run.id == child_id)
        )
    cancelled = RunStatus.CANCELLED.value
    return parent_status == cancelled, child_status == cancelled


async def _abort_child(task: asyncio.Task[None]) -> None:
    """取消子任务并吞掉取消/异常:子 run 的收尾由调用方按 DB 状态决定。"""

    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001 - 子 run 自己的异常已由 _fail_run 落库
        logger.warning("子 run 执行任务异常结束", exc_info=True)


async def _cancel_child(
    factory: async_sessionmaker[AsyncSession], child_id: str, reason: str
) -> Run | None:
    """把子 run 落成 cancelled(超时/父取消/外部取消共用,§6)。"""

    async with session_scope(factory) as session:
        child = await session.get(Run, child_id)
        if child is None:
            return None
        if child.status in _TERMINAL_STATUSES:
            return child
        child.status = RunStatus.CANCELLED.value
        child.status_reason = reason[:500]
        await append_event(session, child, "run.cancelled.v1", {"reason": reason})
        return child


def _cancel_text(abort: str) -> str:
    if abort == _ABORT_TIMEOUT:
        return TIMEOUT_REASON
    if abort == _ABORT_PARENT:
        return PARENT_CANCELLED_REASON
    return CANCELLED_REASON


async def _reload(session: AsyncSession, child_id: str) -> Run | None:
    """取子 run 已提交的最新状态(绕过本会话 identity map 里的旧快照)。"""

    await session.commit()
    return await session.get(Run, child_id, populate_existing=True)


async def _finish_aborted(
    session: AsyncSession,
    parent: Run,
    child: Run | None,
    *,
    thread_id: str,
    item_id: str,
    request: _Request,
    base_data: dict[str, Any],
    started_at: datetime,
    abort: str,
) -> dict[str, Any]:
    """超时/取消收尾:父 item failed,返回 ok=false。"""

    reason = "timeout" if abort == _ABORT_TIMEOUT else "cancelled"
    data = {
        **base_data,
        "child_status": RunStatus.CANCELLED.value,
        "summary": "",
        "reason": reason,
    }
    await append_item_event(
        session,
        parent,
        thread_id=thread_id,
        item_id=item_id,
        kind=items.KIND_SUBAGENT,
        phase=items.PHASE_FAILED,
        title=request.title,
        data=data,
        node="execute",
        status=items.STATUS_FAILED,
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )
    await session.commit()
    return {
        "ok": False,
        "error": f"subagent.{reason}",
        "status": RunStatus.CANCELLED.value,
        "detail": _cancel_text(abort),
        "subagent_run_id": child.id if child is not None else None,
    }


async def _finish(
    session: AsyncSession,
    parent: Run,
    child: Run,
    *,
    thread_id: str,
    item_id: str,
    title: str,
    base_data: dict[str, Any],
    started_at: datetime,
    store: Any,
    settings: Settings,
) -> dict[str, Any]:
    """按子 run 终态落父 item 并组装回喂父模型的 JSON(§6 状态映射)。"""

    status = child.status
    usage = await usage_events.usage_totals(session, parent.tenant_id, child.id)
    usage_data = usage.model_dump()

    if status in _AWAITING_STATUSES:
        # 待人工:立即返回,不阻塞父 run;人工处理后可续跑子 run。
        await _append_finish_item(
            session,
            parent,
            thread_id=thread_id,
            item_id=item_id,
            title=title,
            data={
                **base_data,
                "child_status": status,
                "summary": "",
                "usage": usage_data,
            },
            phase=items.PHASE_COMPLETED,
            status=items.STATUS_COMPLETED,
            started_at=started_at,
        )
        await session.commit()
        return {
            "ok": True,
            "subagent_run_id": child.id,
            "status": status,
            "summary": "",
            "note": "子代理等待人工审批,可在运行详情继续",
            "usage": _usage_reply(usage),
        }

    summary = await _child_summary(session, child, store=store, settings=settings)

    if status == RunStatus.COMPLETED.value:
        await _append_finish_item(
            session,
            parent,
            thread_id=thread_id,
            item_id=item_id,
            title=title,
            data={
                **base_data,
                "child_status": status,
                "summary": summary,
                "usage": usage_data,
            },
            phase=items.PHASE_COMPLETED,
            status=items.STATUS_COMPLETED,
            started_at=started_at,
        )
        await session.commit()
        return {
            "ok": True,
            "subagent_run_id": child.id,
            "status": status,
            "summary": summary,
            "usage": _usage_reply(usage),
        }

    # failed / blocked / cancelled:父 item 落失败项,data.reason 用状态值。
    await _append_finish_item(
        session,
        parent,
        thread_id=thread_id,
        item_id=item_id,
        title=title,
        data={
            **base_data,
            "child_status": status,
            "summary": summary,
            "usage": usage_data,
            "reason": status,
        },
        phase=items.PHASE_FAILED,
        status=items.STATUS_FAILED,
        started_at=started_at,
    )
    await session.commit()
    return {
        "ok": False,
        "error": f"subagent.{status}",
        "status": status,
        "detail": child.status_reason or summary or f"子代理状态:{status}",
        "subagent_run_id": child.id,
        "usage": _usage_reply(usage),
    }


async def _append_finish_item(
    session: AsyncSession,
    parent: Run,
    *,
    thread_id: str,
    item_id: str,
    title: str,
    data: dict[str, Any],
    phase: str,
    status: str,
    started_at: datetime,
) -> None:
    await append_item_event(
        session,
        parent,
        thread_id=thread_id,
        item_id=item_id,
        kind=items.KIND_SUBAGENT,
        phase=phase,
        title=title,
        data=data,
        node="execute",
        status=status,
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )


async def _child_summary(
    session: AsyncSession,
    child: Run,
    *,
    store: Any,
    settings: Settings,
) -> str:
    """子 run 结论摘要:交付物正文 → run.completed.v1.results → status_reason。"""

    text = ""
    try:
        view = await get_artifact(session, child.tenant_id, child.id, store)
        text = view.content or ""
    except NotFoundError:
        text = ""
    if not text:
        events = await RunEventRepository(session).list_for_run(
            child.tenant_id, child.id
        )
        for event in reversed(events):
            if event.type != "run.completed.v1":
                continue
            results = (event.payload or {}).get("results") or []
            text = "\n".join(str(item) for item in results)
            break
    if not text:
        text = child.status_reason or ""
    return text[: settings.subagent_summary_max_chars]


async def _item_summaries(
    session: AsyncSession, tenant_id: str, parent_id: str
) -> dict[str, str]:
    """父 run 里每个子代理 item 最近一次完成摘要(列表页 summary 列)。"""

    summaries: dict[str, str] = {}
    for item in await list_items(session, tenant_id, parent_id):
        if item.kind != items.KIND_SUBAGENT:
            continue
        child_id = item.data.get("child_run_id")
        summary = item.data.get("summary")
        if isinstance(child_id, str) and isinstance(summary, str):
            summaries[child_id] = summary
    return summaries


def _usage_reply(usage: RunUsageTotals) -> dict[str, int]:
    return {"total_tokens": usage.total_tokens, "calls": usage.calls}


def _timeout_value(seconds: float) -> float | int:
    return int(seconds) if float(seconds).is_integer() else seconds


def _error(code: str, detail: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "detail": detail}


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)
