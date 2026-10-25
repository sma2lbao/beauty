"""run 执行:图调用边界(端口/增量/用量注入)、审批编排与结果投影。

依赖方向固定为 execution → {events, artifacts, quota, lifecycle};图调用携带
GraphRuntime(端口 + 正文增量投影器)经 invocation config 显式注入。
"""

from __future__ import annotations

from typing import Any

from common.artifacts import ArtifactStore
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import (
    RunRepository,
)
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services import approval as approval_service
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.run_events import (
    _delta_stream,
    _fail_run,
    append_event,
)
from agent_orchestrator.services.run_execution_approvals import (
    _GRAPH_PORTS as _GRAPH_PORTS,
)
from agent_orchestrator.services.run_execution_approvals import (
    _apply_graph_result as _apply_graph_result,
)
from agent_orchestrator.services.run_execution_approvals import (
    _ApprovalAdapter as _ApprovalAdapter,
)
from agent_orchestrator.services.run_execution_approvals import _config as _config
from agent_orchestrator.services.run_execution_approvals import (
    _graph_ports as _graph_ports,
)
from agent_orchestrator.services.run_execution_approvals import (
    _tool_context as _tool_context,
)
from agent_orchestrator.services.run_execution_approvals import (
    approve_run as approve_run,
)
from agent_orchestrator.services.run_execution_approvals import (
    confirm_run as confirm_run,
)
from agent_orchestrator.services.run_execution_approvals import reject_run as reject_run
from agent_orchestrator.services.run_lifecycle import (
    _consume_instructions,
    _conversation_history,
    _retry_title,
    require_run,
    require_transition,
)
from agent_orchestrator.services.run_quota import _usage_boundary
from agent_orchestrator.services.workspace import Workspace


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
