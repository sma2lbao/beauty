"""run 执行:图调用边界(端口/增量/用量注入)、审批编排与结果投影。

依赖方向固定为 execution → {events, artifacts, quota, lifecycle};图调用携带
GraphRuntime(端口 + 正文增量投影器)经 invocation config 显式注入。
"""

from __future__ import annotations

from typing import Any

from common import metrics
from common.artifacts import ArtifactStore
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.graph import delta
from agent_orchestrator.graph.ports import ApprovalDecision, GraphPorts
from agent_orchestrator.graph.runtime import GRAPH_RUNTIME_KEY, GraphRuntime
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import (
    TOOL_CALLED_EVENT,
    RunEventRepository,
    RunRepository,
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
)
from agent_orchestrator.services.run_lifecycle import (
    _consume_instructions,
    _conversation_history,
    _retry_title,
    require_run,
    require_transition,
)
from agent_orchestrator.services.run_quota import _usage_boundary
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from agent_orchestrator.services.tools import ToolRegistry, ToolRisk
from agent_orchestrator.services.workspace import Workspace


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


async def _invoke_plan(
    session: AsyncSession,
    graph: CompiledStateGraph,
    run: Run,
    *,
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
) -> None:
    """起图跑到审批闸;resume 场景保留用户已编辑的计划。

    审批闸可能**不中断**(无 DANGER 工具时自动放行,见 graph/nodes):那种情况
    图会一路跑到终态,这里补记"计划 + 放行"事件后按统一终态处理收尾
    (交付物落库/工作区镜像/外链抓取与 completed/failed 投影)。
    """
    state: dict[str, Any] = {
        "run_id": run.id,
        "tenant_id": run.tenant_id,
        "title": run.title,
        "goal": run.goal,
        "acceptance_criteria": list(run.acceptance_criteria or []),
    }
    if run.plan_markdown:
        state["plan_markdown"] = run.plan_markdown
    if run.skill_id:
        state["skill_id"] = run.skill_id
    if run.skill_system_prompt:
        state["skill_system_prompt"] = run.skill_system_prompt
    if run.skill_tools is not None:
        state["allowed_tools"] = list(run.skill_tools)
    if run.skill_version_id:
        # 上传技能包快照(P3):P3-C 的 skill_run 工具据此取包/执行;
        # 不带 package_id(run 行按契约只冻结 version_id 与执行字段)。
        state["skill_package"] = {
            "version_id": run.skill_version_id,
            "object_key": run.skill_object_key,
            "sha256": run.skill_sha256,
            "entrypoint": list(run.skill_entrypoint or []),
            "runtime": run.skill_runtime,
            "network": run.skill_network,
            "timeout_seconds": run.skill_timeout_seconds,
        }
    if run.conversation_id:
        # 对话触发的 run:带上多轮上下文,模型才能理解"接着刚才的做"
        state["conversation_history"] = await _conversation_history(session, run)
    # P0-4:审批策略随状态进图(节点是纯函数,不能自己查库);空表即沿用风险默认值
    state["approval_policies"] = await approval_service.load_policies(
        session, run.tenant_id
    )
    # §6.5:规划边界消费排队指令,并入图状态供各节点遵循
    instructions = await _consume_instructions(session, run)
    if instructions:
        state["extra_instructions"] = instructions
    try:
        # 工具 handler 执行时读得到租户/run/对话(stdio MCP 沙箱作用域);
        # 正文增量通道也在这个边界绑定(节点只上报,落库在这里)
        with _tool_context(run):
            async with _delta_stream(session, run) as projector, _usage_boundary(run):
                values = await graph.ainvoke(state, config=_config(run, projector))
    except Exception as exc:
        await _fail_run(session, run, exc, stage="plan")
        return
    run.plan_markdown = values.get("plan_markdown", "")
    if values.get("__interrupt__"):
        run.status = RunStatus.AWAITING_APPROVAL.value
        await append_event(session, run, "run.planned.v1", {"attempt": run.attempts})
        return
    # 无危险工具:审批闸自动放行,图已跑到终态。事件仍按"计划 → 放行"两条记录,
    # 审计上能看出这次没有人工参与(auto_approved / auto 标记)。
    await append_event(
        session,
        run,
        "run.planned.v1",
        {"attempt": run.attempts, "auto_approved": True},
    )
    await append_event(
        session, run, "run.approved.v1", {"attempt": run.attempts, "auto": True}
    )
    await _apply_graph_result(
        session,
        run,
        values,
        store,
        workspace,
        downloader=downloader,
        max_download_files=max_download_files,
    )


async def plan_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    run_id: str,
    *,
    jobs_enabled: bool = False,
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
) -> Run:
    run = await require_run(session, tenant_id, run_id)
    require_transition(run, "plan")
    if jobs_enabled:
        await jobs_service.enqueue(session, tenant_id, "plan", run.id)
        return run
    await _invoke_plan(
        session,
        graph,
        run,
        store=store,
        workspace=workspace,
        downloader=downloader,
        max_download_files=max_download_files,
    )
    return run


async def update_plan(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    run_id: str,
    markdown: str,
) -> Run:
    run = await require_run(session, tenant_id, run_id)
    require_transition(run, "update_plan")
    await graph.aupdate_state(_config(run), {"plan_markdown": markdown}, as_node="plan")
    run.plan_markdown = markdown
    await append_event(session, run, "run.plan.edited.v1", {"chars": len(markdown)})
    return run


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
    """
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
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
) -> Run:
    run = await require_run(session, tenant_id, run_id)
    require_transition(run, "approve")
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
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
) -> Run:
    """危险操作确认(§6.2):awaiting_confirmation → 放行执行 / 拒绝跳过,图继续。"""
    run = await require_run(session, tenant_id, run_id)
    require_transition(run, "confirm")
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


async def retry_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    run_id: str,
    *,
    jobs_enabled: bool = False,
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
) -> Run:
    """failed → **另起一跑**并继承上下文(§6.2:failed 是终态,不重放旧图)。

    为什么不原地恢复:失败可能来自节点/模型/图本身,失败点的检查点不可信,
    原地重放会把同一处再炸一次;同时旧 run 的历史(failed + 原因)必须原样保留
    ——审计不能被"重试"篡改。

    继承的东西:目标/验收标准/**已批准的计划**(计划节点检测到已有计划会短路,
    所以只重走审批闸,不重复调用模型);不继承:轮次、失败原因、终止状态。
    """
    parent = await require_run(session, tenant_id, run_id)
    require_transition(parent, "retry")
    child = Run(
        tenant_id=tenant_id,
        title=_retry_title(parent.title),
        goal=parent.goal,
        status=RunStatus.PENDING.value,
        acceptance_criteria=list(parent.acceptance_criteria or []),
        plan_markdown=parent.plan_markdown,
        parent_run_id=parent.id,
        conversation_id=parent.conversation_id,
        workspace_id=parent.workspace_id,
    )
    await RunRepository(session).add(child)
    await append_event(
        session,
        child,
        "run.created.v1",
        {"title": child.title, "parent_run_id": parent.id},
    )
    await append_event(
        session,
        parent,
        "run.retry.requested.v1",
        {"new_run_id": child.id, "failures": 1, "reason": parent.status_reason},
    )
    # 继承计划 → 直接进审批闸:按同一判据放行(无危险工具自动、有危险工具人工;
    # 已产生的外部副作用不会被自动撤销)
    if jobs_enabled:
        await jobs_service.enqueue(session, tenant_id, "plan", child.id)
    else:
        await _invoke_plan(
            session,
            graph,
            child,
            store=store,
            workspace=workspace,
            downloader=downloader,
            max_download_files=max_download_files,
        )
    return child


async def resume_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    run_id: str,
    *,
    jobs_enabled: bool = False,
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
) -> Run:
    """blocked → 新 attempt 重新进审批闸,计划保留(§6.2 goal 语义)。"""
    run = await require_run(session, tenant_id, run_id)
    require_transition(run, "resume")
    run.attempts += 1
    run.status = RunStatus.PENDING.value
    run.status_reason = None
    await append_event(session, run, "run.resumed.v1", {"attempt": run.attempts})
    if jobs_enabled:
        await jobs_service.enqueue(session, tenant_id, "plan", run.id)
        return run
    await _invoke_plan(
        session,
        graph,
        run,
        store=store,
        workspace=workspace,
        downloader=downloader,
        max_download_files=max_download_files,
    )
    return run
