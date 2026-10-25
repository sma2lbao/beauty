"""run 审批/确认/拒绝与图结果落库(自 run_execution.py 拆出)。"""

from __future__ import annotations

from typing import Any

from common.artifacts import ArtifactStore
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator import metrics
from agent_orchestrator.graph import delta
from agent_orchestrator.graph.ports import ApprovalDecision, GraphPorts
from agent_orchestrator.graph.runtime import GRAPH_RUNTIME_KEY, GraphRuntime
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import (
    TOOL_CALLED_EVENT,
    RunEventRepository,
)
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services import approval as approval_service
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services import usage as usage_service
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.run_artifacts import (
    _deliver_artifacts,
    _persist_artifact,
)
from agent_orchestrator.services.run_events import (
    _append_assistant_message,
    _delta_stream,
    _fail_run,
    append_event,
    is_cancelled,
)
from agent_orchestrator.services.run_lifecycle import (
    _consume_instructions,
    require_run,
    require_transition,
)
from agent_orchestrator.services.run_quota import _usage_boundary
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from agent_orchestrator.services.tools import ToolRegistry, ToolRisk
from agent_orchestrator.services.workspace import Workspace

#: from_job 路径下"无需再执行"的状态:终态或已取消(取消后的 job 直接作废)
_TERMINAL_OR_CANCELLED = frozenset(
    {
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.BLOCKED.value,
        RunStatus.CANCELLED.value,
    }
)


class _ApprovalAdapter:
    """graph.ports.ApprovalPort 的 services 实现:包装租户审批策略引擎。

    依赖方向是 services → graph:节点只认 graph 侧的值域,策略表求值仍留在
    services.approval(纯函数),适配层只做类型/枚举对齐。
    """

    def decide(
        self,
        policies: Any,
        tool: str,
        risk: str,
    ) -> ApprovalDecision:
        action = approval_service.decide(policies, tool, ToolRisk(risk))
        return ApprovalDecision(action.value)


def _graph_ports() -> GraphPorts:
    """装配 graph 侧端口:预算闸、审批策略、空工具注册表兜底。"""
    return GraphPorts(
        usage=usage_service,
        approval=_ApprovalAdapter(),
        empty_toolkit=lambda: ToolRegistry([]),
    )


_GRAPH_PORTS = _graph_ports()


def _tool_context(run: Run):
    """工具 handler 的来源上下文:stdio MCP 据此按对话/run 复用沙箱(P2)。"""
    return bind_tool_context(
        ToolContext(
            tenant_id=run.tenant_id,
            run_id=run.id,
            conversation_id=run.conversation_id,
            # run 创建时快照的 MCP 引用:工具装配(execution/extras)只暴露这些 server
            mcp_server_ids=tuple(run.mcp_server_ids or ()),
        )
    )


def _config(run: Run, projector: delta.DeltaProjector | None = None) -> dict[str, Any]:
    """graph 线程标识 + 本次调用的运行期依赖(端口/增量投影器显式注入)。

    同一 run 的每次 blocked→resume 周期一个新线程;运行期依赖随调用传递,
    节点不再读进程级上下文变量。
    """
    return {
        "configurable": {
            "thread_id": f"{run.id}#{run.attempts}",
            GRAPH_RUNTIME_KEY: GraphRuntime(
                ports=_GRAPH_PORTS,
                delta=projector or delta.DeltaProjector(),
            ),
        }
    }


async def _apply_graph_result(
    session: AsyncSession,
    run: Run,
    values: dict[str, Any],
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
    *,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
) -> Run:
    """图调用结果的统一后处理(§6.2/§6.3/§7)。

    顺序即语义:工具审计 → 危险操作确认中断 → 交付物 → 终态。

    注意:state.tool_calls 是 add reducer,恢复执行(如确认闸放行)后会带上
    历史累积记录——按已落库条数只发增量,避免事件重复。

    协作式取消(P0-2):执行期间收到取消请求时,结果一律丢弃——不改交付物、
    不写 completed/failed,只把本地对象同步成 cancelled 后返回。
    """
    if await is_cancelled(session, run.id):
        run.status = RunStatus.CANCELLED.value
        return run
    records = values.get("tool_calls") or []
    emitted = await RunEventRepository(session).count_of_type(run.id, TOOL_CALLED_EVENT)
    for record in records[emitted or 0 :]:
        await append_event(session, run, "run.tool.called.v1", record)
    if values.get("__interrupt__"):
        # 危险操作待人工确认:停在 awaiting_confirmation,等 POST /confirm
        run.status = RunStatus.AWAITING_CONFIRMATION.value
        run.rounds = values.get("rounds", 0)
        await append_event(
            session,
            run,
            "run.confirmation.required.v1",
            {"pending_writes": values.get("pending_writes") or []},
        )
        return run
    artifact = values.get("artifact")
    failed = values.get("final_status") == "failed"
    if artifact:
        if failed:
            # 失败终态不抓外链:不为一次失败执行消耗下载预算,只如实落报告件
            await _persist_artifact(session, run, artifact, store, workspace)
        else:
            await _deliver_artifacts(
                session,
                run,
                artifact,
                records=records,
                store=store,
                workspace=workspace,
                downloader=downloader,
                max_files=max_download_files,
            )
    run.rounds = values.get("rounds", 0)
    if failed:
        # 返工耗尽仍未验收:fail_node 的诚实终态(§6.1),事件流如实记录
        run.status = RunStatus.FAILED.value
        run.status_reason = (values.get("status_reason") or "验收未通过")[:500]
        metrics.observe_run_outcome(status=RunStatus.FAILED.value)
        await append_event(
            session,
            run,
            "run.failed.v1",
            {
                "stage": "verify",
                "reason": run.status_reason,
                "rounds": run.rounds,
            },
        )
        await _append_assistant_message(session, run, f"执行失败:{run.status_reason}")
        return run
    run.status = RunStatus.COMPLETED.value
    metrics.observe_run_outcome(status=RunStatus.COMPLETED.value)
    await append_event(
        session,
        run,
        "run.completed.v1",
        {"rounds": run.rounds, "results": values.get("results") or []},
    )
    # 对话里的回复 = 交付物正文(没有时用执行结果小结兜底)
    await _append_assistant_message(
        session,
        run,
        str(artifact.get("content") or "")
        or "\n".join(values.get("results") or [])
        or "任务完成 ✅",
    )
    return run


async def approve_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    run_id: str,
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
    *,
    jobs_enabled: bool = False,
    emit_approved: bool = True,
    from_job: bool = False,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
) -> Run:
    run = await require_run(session, tenant_id, run_id)
    if from_job:
        # worker 回来执行:API 侧已把状态推到 running,无法再过状态守卫;
        # 期间被取消或已终态(例如取消后 job 才被领到)则直接收手。
        if run.status in _TERMINAL_OR_CANCELLED:
            return run
    else:
        require_transition(run, "approve")
        # P0-2:审批通过即进入执行,同步与 jobs 两条路径都在这里推进状态。
        run.status = RunStatus.RUNNING.value
    if emit_approved:
        # 放行动作先入事件流,再恢复图:执行失败时顺序如实(approved → failed)
        await append_event(session, run, "run.approved.v1", {"attempt": run.attempts})
    if jobs_enabled:
        await jobs_service.enqueue(session, tenant_id, "approve", run.id)
        return run
    # §6.5:放行边界消费排队指令,经 resume 载荷由审批闸并入状态
    instructions = await _consume_instructions(session, run)
    try:
        with _tool_context(run):
            async with _delta_stream(session, run) as projector, _usage_boundary(run):
                values = await graph.ainvoke(
                    Command(resume={"action": "approve", "instructions": instructions}),
                    config=_config(run, projector),
                )
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


async def confirm_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    run_id: str,
    action: str,
    reason: str | None,
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
    *,
    jobs_enabled: bool = False,
    emit_confirmation: bool = True,
    from_job: bool = False,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
) -> Run:
    """危险操作确认(§6.2):awaiting_confirmation → 放行执行 / 拒绝跳过,图继续。"""
    run = await require_run(session, tenant_id, run_id)
    if from_job:
        # 同 approve_run:worker 路径状态已由 API 侧推进,守卫在此不重复校验。
        if run.status in _TERMINAL_OR_CANCELLED:
            return run
    else:
        require_transition(run, "confirm")
        # P0-2:确认后图继续执行,状态回到 running。
        run.status = RunStatus.RUNNING.value
    if emit_confirmation:
        await append_event(
            session,
            run,
            "run.confirmation.received.v1",
            {"action": action, "reason": reason},
        )
    if jobs_enabled:
        await jobs_service.enqueue(
            session,
            tenant_id,
            "confirm",
            run.id,
            payload={"action": action, "reason": reason},
        )
        return run
    # §6.5:确认边界消费排队指令,经 resume 载荷由确认闸并入状态
    instructions = await _consume_instructions(session, run)
    try:
        with _tool_context(run):
            async with _delta_stream(session, run) as projector, _usage_boundary(run):
                values = await graph.ainvoke(
                    Command(
                        resume={
                            "action": action,
                            "reason": reason,
                            "instructions": instructions,
                        }
                    ),
                    config=_config(run, projector),
                )
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
    tenant_id: str,
    run_id: str,
    reason: str,
) -> Run:
    run = await require_run(session, tenant_id, run_id)
    require_transition(run, "reject")
    with _tool_context(run):
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
