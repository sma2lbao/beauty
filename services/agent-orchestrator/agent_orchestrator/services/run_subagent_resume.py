"""异步子代理唤醒后的父 run 恢复(L2,docs/design/subagents.md)。

谁调:jobs_worker 领到 kind="subagent" 的 job(container 侧 jobs_enabled=True),
或直接调用(测试/脚本)。语义:
- 父 run 必须停在 awaiting_subagent;子代理**全部**终态才真正 resume 图,
  否则只做一次检查就收手——后到的唤醒 job 会再检查一遍;
- 恢复前把父 run 过程项里每条 STARTED 的子代理 item 补成终态(与内联路径的
  _finish 同一套 phase/status 规则),并把子代理摘要作为回执交回图;
- 图恢复用的 resume 载荷 kind 必须与 graph.nodes.subagent_wait.INTERRUPT_KIND
  一致,回执变成 user 消息追加进 tool_history(节点负责)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from common.artifacts import ArtifactStore
from common.logging import get_logger
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.core.config import Settings
from agent_orchestrator.graph import items
from agent_orchestrator.graph.cancellation import RunCancelled
from agent_orchestrator.graph.nodes.subagent_wait import INTERRUPT_KIND
from agent_orchestrator.models.runs import PARENT_KIND_SUBAGENT, Run
from agent_orchestrator.repositories import SUBAGENT_RESUMED_EVENT, RunRepository
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services import usage_events
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.items import append_item_event, list_items
from agent_orchestrator.services.llm_binding import bind_run_llm
from agent_orchestrator.services.run_cancellation import settle_cancelled
from agent_orchestrator.services.run_events import (
    _delta_stream,
    _fail_run,
    _item_stream,
    _step_stream,
    append_event,
)
from agent_orchestrator.services.run_execution_approvals import (
    _apply_graph_result,
    _config,
    _tool_context,
)
from agent_orchestrator.services.run_lifecycle import (
    mark_started,
    require_run,
    require_transition,
)
from agent_orchestrator.services.run_quota import _usage_boundary
from agent_orchestrator.services.run_trace import run_trace
from agent_orchestrator.services.subagents_report import _child_summary
from agent_orchestrator.services.subagents_spec import _TERMINAL_STATUSES
from agent_orchestrator.services.workspace import Workspace

WAITING_STATUS = RunStatus.AWAITING_SUBAGENT.value
LOGGER_NAME = "agent_orchestrator.subagents"
logger = get_logger(LOGGER_NAME)


async def resume_subagent(
    session: AsyncSession,
    graph: CompiledStateGraph,
    account_id: str,
    run_id: str,
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
    *,
    jobs_enabled: bool = False,
    from_job: bool = False,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
    settings: Settings,
) -> Run:
    """子代理全部终态后恢复父 run;仍有在飞子代理时只做检查。"""

    run = await require_run(session, account_id, run_id)
    if run.status != WAITING_STATUS:
        if from_job:
            # 别的唤醒已经把父 run 推进了(或期间被取消/终态)
            return run
        require_transition(run, "subagent")
    children = await _children(session, run)
    if not children:
        logger.warning("subagent wait without children", run_id=run.id)
        run.status = RunStatus.FAILED.value
        run.status_reason = "子代理等待没有对应的子 run"
        await append_event(
            session,
            run,
            "run.failed.v1",
            {"stage": "subagent", "reason": run.status_reason},
        )
        return run
    if any(child.status not in _TERMINAL_STATUSES for child in children):
        # 还有子代理在飞:这次唤醒只做检查,等下一个子 run 终态再唤醒
        return run
    notices = await _notices(session, children, store=store, settings=settings)
    await _settle_items(session, run, children, notices)
    run.status = RunStatus.RUNNING.value
    run.rounds = run.rounds or 0
    mark_started(run)
    await append_event(
        session,
        run,
        SUBAGENT_RESUMED_EVENT,
        {"children": [notice["child_run_id"] for notice in notices]},
    )
    if jobs_enabled:
        await jobs_service.enqueue(
            session, account_id, "subagent", run.id, dedupe=False
        )
        return run
    try:
        with bind_run_llm(session, run), _tool_context(run):
            async with (
                _delta_stream(session, run, settings) as projector,
                _step_stream(session, run, projector) as step_projector,
                _item_stream(session, run, step_projector) as item_projector,
                _usage_boundary(session, run),
            ):
                with run_trace(run):
                    values = await graph.ainvoke(
                        Command(resume={"kind": INTERRUPT_KIND, "children": notices}),
                        config=_config(
                            run,
                            projector,
                            step_projector,
                            item_projector,
                            session=session,
                        ),
                    )
    except RunCancelled:
        return await settle_cancelled(session, run, stage="execute")
    except Exception as exc:
        await _fail_run(session, run, exc, stage="execute")
        return run
    return await _apply_graph_result(
        session,
        run,
        values,
        store,
        workspace,
        downloader=downloader,
        max_download_files=max_download_files,
    )


async def _children(session: AsyncSession, parent: Run) -> list[Run]:
    """父 run 直系子代理(按建立时间排序,回执照同一顺序)。"""

    repo = RunRepository(session)
    return await repo.list_scoped(
        parent.account_id,
        Run.parent_run_id == parent.id,
        Run.parent_kind == PARENT_KIND_SUBAGENT,
        order_by=[Run.created_at, Run.id],
    )


async def _notices(
    session: AsyncSession,
    children: list[Run],
    *,
    store: ArtifactStore | None,
    settings: Settings,
) -> list[dict[str, Any]]:
    """每个子 run 一条回执:completed 带摘要,其余带失败原因。"""

    notices: list[dict[str, Any]] = []
    for child in children:
        summary = await _child_summary(session, child, store=store, settings=settings)
        completed = child.status == RunStatus.COMPLETED.value
        notices.append(
            {
                "child_run_id": child.id,
                "title": child.title,
                "status": child.status,
                "summary": summary if completed else "",
                "reason": (child.status_reason or child.status)[:500],
            }
        )
    return notices


async def _settle_items(
    session: AsyncSession,
    parent: Run,
    children: list[Run],
    notices: list[dict[str, Any]],
) -> None:
    """把父 run 里仍是 started 的子代理 item 补成终态(内联路径由 _finish 做)。"""

    rows = await list_items(session, parent.account_id, parent.id)
    started: dict[str, Any] = {}
    for row in reversed(rows):
        if getattr(row, "kind", None) != items.KIND_SUBAGENT:
            continue
        data = getattr(row, "data", None) or {}
        child_id = data.get("child_run_id")
        if isinstance(child_id, str) and child_id not in started:
            started[child_id] = row
    by_id = {child.id: child for child in children}
    for notice in notices:
        row = started.get(notice["child_run_id"])
        child = by_id.get(notice["child_run_id"])
        if row is None or child is None:
            continue
        completed = child.status == RunStatus.COMPLETED.value
        usage = await usage_events.usage_totals(session, parent.account_id, child.id)
        await append_item_event(
            session,
            parent,
            thread_id=str(getattr(row, "thread_id", "") or ""),
            item_id=getattr(row, "item_id", None) or "",
            kind=items.KIND_SUBAGENT,
            phase=items.PHASE_COMPLETED if completed else items.PHASE_FAILED,
            title=getattr(row, "title", None) or notice["title"],
            data={
                **(getattr(row, "data", None) or {}),
                "child_status": child.status,
                "summary": notice["summary"],
                "reason": notice["reason"],
                "usage": usage.model_dump(),
            },
            node="execute",
            status=items.STATUS_COMPLETED if completed else items.STATUS_FAILED,
            started_at=getattr(row, "started_at", None) or datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
