"""审批决定与恢复执行:approve/confirm/reject。"""

from __future__ import annotations

from collections.abc import Mapping

from common.artifacts import ArtifactStore
from common.logging import get_logger
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.core.config import Settings
from agent_orchestrator.graph.cancellation import RunCancelled
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import RunEventRepository
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services import approval as approval_service
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.llm_binding import bind_run_llm
from agent_orchestrator.services.run_cancellation import (
    settle_cancelled,
)
from agent_orchestrator.services.run_events import (
    _append_assistant_message,
    _delta_stream,
    _fail_run,
    _item_stream,
    _step_stream,
    append_event,
)
from agent_orchestrator.services.run_lifecycle import (
    _consume_instructions,
    mark_started,
    require_run,
    require_transition,
)
from agent_orchestrator.services.run_quota import _usage_boundary
from agent_orchestrator.services.run_trace import run_trace
from agent_orchestrator.services.workspace import Workspace

from .graph_result import _apply_graph_result
from .support import _TERMINAL_OR_CANCELLED, LOGGER_NAME, _config, _tool_context

logger = get_logger(LOGGER_NAME)


async def approve_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    account_id: str,
    run_id: str,
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
    *,
    jobs_enabled: bool = False,
    emit_approved: bool = True,
    from_job: bool = False,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
    settings: Settings,
) -> Run:
    run = await require_run(session, account_id, run_id)
    if from_job:
        # worker 回来执行:API 侧已把状态推到 running,无法再过状态守卫;
        # 期间被取消或已终态(例如取消后 job 才被领到)则直接收手。
        if run.status in _TERMINAL_OR_CANCELLED:
            return run
    else:
        require_transition(run, "approve")
        # P0-2:审批通过即进入执行,同步与 jobs 两条路径都在这里推进状态。
        run.status = RunStatus.RUNNING.value
        mark_started(run)
    if emit_approved:
        # 放行动作先入事件流,再恢复图:执行失败时顺序如实(approved → failed)
        await append_event(session, run, "run.approved.v1", {"attempt": run.attempts})
    if jobs_enabled:
        await jobs_service.enqueue(session, account_id, "approve", run.id)
        return run
    # §6.5:放行边界消费排队指令,经 resume 载荷由审批闸并入状态
    instructions = await _consume_instructions(session, run)
    try:
        with bind_run_llm(session, run), _tool_context(run):
            async with (
                _delta_stream(session, run, settings) as projector,
                _step_stream(session, run) as step_projector,
                _item_stream(session, run, step_projector) as item_projector,
                _usage_boundary(session, run),
            ):
                with run_trace(run):
                    values = await graph.ainvoke(
                        Command(
                            resume={
                                "action": "approve",
                                "instructions": instructions,
                            }
                        ),
                        config=_config(
                            run,
                            projector,
                            step_projector,
                            item_projector,
                            session=session,
                        ),
                    )
    except RunCancelled:
        # S3:取消在节点边界生效,终态已由取消方写好,这里只刷新并收手
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


async def _pending_write_tools(session: AsyncSession, run: Run) -> list[str]:
    """本次确认闸里待写入的工具名(去重保序);事件缺失时返回空列表。

    授权对象是"用户刚刚放行的那批写操作",因此从最近一条
    run.confirmation.required.v1 的载荷里取 pending_writes,而不是重新推理。
    """
    event = await RunEventRepository(session).latest_of_type(
        run.id, "run.confirmation.required.v1"
    )
    payload = event.payload if event is not None else None
    pending = payload.get("pending_writes") if isinstance(payload, Mapping) else None
    tools: list[str] = []
    for item in pending or []:
        name = item.get("name") if isinstance(item, Mapping) else None
        if name and str(name) not in tools:
            tools.append(str(name))
    return tools


async def _apply_grants(session: AsyncSession, run: Run, grant: str) -> None:
    """按用户选择的授权范围落会话授权/账号策略(§5.2,与状态推进同一事务)。"""
    tools = await _pending_write_tools(session, run)
    if not tools:
        logger.warning(
            "approval.grant_without_pending_writes",
            run_id=run.id,
            account_id=run.account_id,
            grant=grant,
        )
        return
    for tool in tools:
        await approval_service.record_grant(
            session,
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            tool=tool,
            grant=grant,
        )


async def confirm_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    account_id: str,
    run_id: str,
    action: str,
    reason: str | None,
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
    *,
    grant: str = "once",
    jobs_enabled: bool = False,
    emit_confirmation: bool = True,
    from_job: bool = False,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
    settings: Settings,
) -> Run:
    """危险操作确认(§6.2):awaiting_confirmation → 放行执行 / 拒绝跳过,图继续。

    grant 只在 action="approve" 且非 once 时生效(§5.2):session 把本次确认闸里的
    工具写成会话授权(无会话时降级 once),always 写成账号级 auto 策略;reject 忽略。
    """
    run = await require_run(session, account_id, run_id)
    if from_job:
        # 同 approve_run:worker 路径状态已由 API 侧推进,守卫在此不重复校验。
        if run.status in _TERMINAL_OR_CANCELLED:
            return run
    else:
        require_transition(run, "confirm")
        # P0-2:确认后图继续执行,状态回到 running。
        run.status = RunStatus.RUNNING.value
        mark_started(run)
    if emit_confirmation:
        await append_event(
            session,
            run,
            "run.confirmation.received.v1",
            {"action": action, "reason": reason},
        )
    # §5.2:放行时按用户选择的授权范围落库;reject 永不放行,不落授权。
    # from_job 路径的授权已由 API 侧在同一事务里落库,worker 不重复写。
    if action == "approve" and grant != approval_service.GRANT_ONCE and not from_job:
        await _apply_grants(session, run, grant)
    if jobs_enabled:
        await jobs_service.enqueue(
            session,
            account_id,
            "confirm",
            run.id,
            payload={"action": action, "reason": reason},
        )
        return run
    # §6.5:确认边界消费排队指令,经 resume 载荷由确认闸并入状态
    instructions = await _consume_instructions(session, run)
    try:
        with bind_run_llm(session, run), _tool_context(run):
            async with (
                _delta_stream(session, run, settings) as projector,
                _step_stream(session, run) as step_projector,
                _item_stream(session, run, step_projector) as item_projector,
                _usage_boundary(session, run),
            ):
                with run_trace(run):
                    values = await graph.ainvoke(
                        Command(
                            resume={
                                "action": action,
                                "reason": reason,
                                "instructions": instructions,
                            }
                        ),
                        config=_config(
                            run,
                            projector,
                            step_projector,
                            item_projector,
                            session=session,
                        ),
                    )
    except RunCancelled:
        return await settle_cancelled(session, run, stage="confirm")
    except Exception as exc:
        await _fail_run(session, run, exc, stage="confirm")
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


async def reject_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    account_id: str,
    run_id: str,
    reason: str,
) -> Run:
    run = await require_run(session, account_id, run_id)
    require_transition(run, "reject")
    with bind_run_llm(session, run), _tool_context(run), run_trace(run):
        await graph.ainvoke(
            Command(resume={"action": "reject", "reason": reason}),
            config=_config(run),
        )
    run.status = RunStatus.BLOCKED.value
    run.status_reason = reason
    await append_event(session, run, "run.rejected.v1", {"reason": reason})
    await append_event(
        session,
        run,
        "run.blocked.v1",
        {"reason": reason, "attempt": run.attempts},
    )
    await _append_assistant_message(session, run, f"计划被拒绝:{reason}")
    return run
