"""run 执行:图调用边界(端口/增量/用量注入)、审批编排与结果投影。

依赖方向固定为 execution → {events, artifacts, quota, lifecycle};图调用携带
GraphRuntime(端口 + 正文增量投影器)经 invocation config 显式注入。

拆分说明:起图/审批闸边界与记忆召回分别在 run_execution_invoke、
run_execution_memory,本模块保留门面重导出,并承载 plan/retry/resume 编排。
"""

from __future__ import annotations

from datetime import UTC, datetime

from common.artifacts import ArtifactStore
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.context.pipeline import ContextPipeline
from agent_orchestrator.core.config import Settings
from agent_orchestrator.graph import items
from agent_orchestrator.models.runs import PARENT_KIND_RETRY, Run
from agent_orchestrator.repositories import (
    RunRepository,
)
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services import jobs as jobs_service

# 兼容旧门面:tests 直接 monkeypatch run_execution.memory_service,与
# run_execution_memory 内引用的是同一个模块对象。
from agent_orchestrator.services import memory as memory_service  # noqa: F401
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.items import (
    append_item_event,
    next_plan_version,
    plan_content,
    resolve_thread_id,
)
from agent_orchestrator.services.run_events import append_event
from agent_orchestrator.services.run_execution_answers import (
    answer_run as answer_run,
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
from agent_orchestrator.services.run_execution_invoke import (
    _allowed_tools_from_snapshot as _allowed_tools_from_snapshot,
)
from agent_orchestrator.services.run_execution_invoke import (
    _invoke_plan as _invoke_plan,
)
from agent_orchestrator.services.run_execution_memory import (
    _memory_query as _memory_query,
)
from agent_orchestrator.services.run_execution_memory import (
    _recall_memory_block as _recall_memory_block,
)
from agent_orchestrator.services.run_lifecycle import (
    _retry_title,
    require_run,
    require_transition,
)
from agent_orchestrator.services.workspace import Workspace


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
    settings: Settings | None = None,
    context: ContextPipeline | None = None,
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
        settings=settings,
        context=context,
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
    # 编辑落新版 plan item(supersedes 上一版),与生成共用同一版本序列。
    version, supersedes = await next_plan_version(session, run)
    content, chars, truncated = plan_content(markdown)
    edited_at = datetime.now(UTC)
    await append_item_event(
        session,
        run,
        thread_id=await resolve_thread_id(session, run),
        item_id=f"{run.id}:plan:{version}",
        kind=items.KIND_PLAN,
        phase=items.PHASE_COMPLETED,
        status=items.STATUS_COMPLETED,
        title=f"计划(第 {version} 版)",
        data={
            "version": version,
            "supersedes": supersedes,
            "content": content,
            "chars": chars,
            "truncated": truncated,
        },
        started_at=edited_at,
        finished_at=edited_at,
    )
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
    settings: Settings | None = None,
    context: ContextPipeline | None = None,
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
        parent_kind=PARENT_KIND_RETRY,
        conversation_id=parent.conversation_id,
        workspace_id=parent.workspace_id,
        # 重试沿用父 run 的模型快照(P1-G):换模型重试不是这里的目标
        model=parent.model,
    )
    await RunRepository(session).add(child)
    await append_event(
        session,
        child,
        "run.created.v1",
        {
            "title": child.title,
            "parent_run_id": parent.id,
            "parent_kind": PARENT_KIND_RETRY,
        },
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
            settings=settings,
            context=context,
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
    settings: Settings | None = None,
    context: ContextPipeline | None = None,
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
        settings=settings,
        context=context,
    )
    return run
