"""run 执行:图调用边界(端口/增量/用量注入)、审批编排与结果投影。

依赖方向固定为 execution → {events, artifacts, quota, lifecycle};图调用携带
GraphRuntime(端口 + 正文增量投影器)经 invocation config 显式注入。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from common.artifacts import ArtifactStore
from common.logging import get_logger
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.context.pipeline import ContextPipeline
from agent_orchestrator.core.config import Settings
from agent_orchestrator.graph import items
from agent_orchestrator.graph.todos import sanitize_todos
from agent_orchestrator.models.runs import PARENT_KIND_RETRY, PARENT_KIND_SUBAGENT, Run
from agent_orchestrator.repositories import (
    RunRepository,
)
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services import approval as approval_service
from agent_orchestrator.services import context_service
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services import memory as memory_service
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.items import (
    append_item_event,
    latest_step_id,
    next_plan_version,
    plan_content,
    resolve_thread_id,
)
from agent_orchestrator.services.llm_binding import bind_run_llm
from agent_orchestrator.services.run_events import (
    _delta_stream,
    _fail_run,
    _item_stream,
    _step_stream,
    append_event,
)
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
from agent_orchestrator.services.run_lifecycle import (
    _consume_instructions,
    _conversation_history,
    _retry_title,
    require_run,
    require_transition,
)
from agent_orchestrator.services.run_quota import _usage_boundary
from agent_orchestrator.services.tool_names import normalize_tool_names
from agent_orchestrator.services.workspace import Workspace

logger = get_logger(__name__)


def _allowed_tools_from_snapshot(run: Run) -> list[str] | None:
    """快照读取边界:技能/子代理白名单里的旧工具名归一到 tool-service 正式名。

    None 表示创建时未冻结白名单(=不限制);空表保持空表(=不用工具)。
    子代理(P1-E)的工具面是自身 ``agent_tools``(spawn 时已按父级白名单收窄并
    落列),优先于技能快照;``agent_tools`` 为 None 时回落到 skill_tools。
    只读快照时归一,图节点与 RemoteToolkit.restricted() 仍只认正式名。
    """
    if run.parent_kind == PARENT_KIND_SUBAGENT and run.agent_tools is not None:
        return normalize_tool_names(run.agent_tools)
    if run.skill_tools is None:
        return None
    return normalize_tool_names(run.skill_tools)


def _memory_query(run: Run) -> str:
    """记忆召回查询词:目标为主、标题为辅(标题常含用户原话,更易命中偏好)。"""
    return " ".join(part for part in (run.goal, run.title) if part).strip()


async def _recall_memory_block(
    session: AsyncSession, run: Run, settings: Settings
) -> str:
    """召回长期记忆并拼成提示块(§6.7);失败降级空串,不阻断 run。

    两块内容:程序性约定(kind=procedure)无条件注入——它是"这个工作区的
    做法",不依赖查询词命中;相关性命中按 goal+title 检索。记忆是"锦上添花"
    的上下文:嵌入/检索/库任一处抖动都只该让交付少一点个性化,不该让用户
    看到 failed——所以这里吞异常只留 warning。
    """
    try:
        convention = await memory_service.convention_block(
            session,
            run.tenant_id,
            workspace_id=run.workspace_id,
            max_chars=settings.memory_convention_max_chars,
        )
        query = _memory_query(run)
        recall = ""
        if query:
            recall = await memory_service.recall_block(
                session,
                run.tenant_id,
                query,
                workspace_id=run.workspace_id,
                limit=settings.memory_recall_limit,
                max_chars=settings.memory_recall_max_chars,
            )
        return "\n\n".join(part for part in (convention, recall) if part)
    except Exception as exc:  # noqa: BLE001 - 记忆故障不阻断执行
        logger.warning(
            "memory recall failed",
            run_id=run.id,
            tenant_id=run.tenant_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return ""


async def _invoke_plan(
    session: AsyncSession,
    graph: CompiledStateGraph,
    run: Run,
    *,
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
    settings: Settings | None = None,
    context: ContextPipeline | None = None,
) -> None:
    """起图跑到审批闸;resume 场景保留用户已编辑的计划。

    审批闸可能**不中断**(无 DANGER 工具时自动放行,见 graph/nodes):那种情况
    图会一路跑到终态,这里补记"计划 + 放行"事件后按统一终态处理收尾
    (交付物落库/工作区镜像/外链抓取与 completed/failed 投影)。
    """
    # P0-2:进图即 planning(前端/SSE 可见),不落事件——事件流保持
    # created → planned 的既有契约(计划真正产出后才发 planned)。
    run.status = RunStatus.PLANNING.value
    # P1-D:计划是一等过程项——先落 started,成功后补 completed(§4/§7);
    # version 跨 attempt 连续推进,item_id 因而在重试/编辑后仍唯一可寻址。
    plan_thread_id = await resolve_thread_id(session, run)
    plan_version, plan_supersedes = await next_plan_version(session, run)
    plan_item_id = f"{run.id}:plan:{plan_version}"
    plan_title = f"计划(第 {plan_version} 版)"
    plan_started_at = datetime.now(UTC)
    await append_item_event(
        session,
        run,
        thread_id=plan_thread_id,
        item_id=plan_item_id,
        kind=items.KIND_PLAN,
        phase=items.PHASE_STARTED,
        title=plan_title,
        node="plan",
        data={"version": plan_version, "supersedes": plan_supersedes},
        started_at=plan_started_at,
    )
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
    allowed_tools = _allowed_tools_from_snapshot(run)
    if allowed_tools is not None:
        state["allowed_tools"] = allowed_tools
    if run.parent_kind == PARENT_KIND_SUBAGENT:
        # 子代理(P1-E):角色名/角色提示随状态进图,plan/execute/verify/deliver
        # 的系统提示统一注入(节点纯函数,不查库)。
        if run.agent_name:
            state["agent_name"] = run.agent_name
        if run.agent_prompt:
            state["agent_prompt"] = run.agent_prompt
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
    if settings is not None and settings.memory_enabled:
        # 记忆只在起图时召回一次:resume 走 checkpoint 恢复,state 里带出同一块
        # (召回结果进 state 而不是每次调用重算,审批/确认恢复时上下文保持一致)。
        state["memory_block"] = await _recall_memory_block(session, run, settings)
    if context is not None:
        # P1-F:按策略快照装配上下文,注入 state 并落 run.context.assembled.v1;
        # 失败降级为 None——节点读不到 context_block 时回退 legacy 注入
        built = await context_service.build_run_context(
            session,
            run,
            pipeline=context,
            memory_block=state.get("memory_block", ""),
        )
        if built is not None:
            state["context_block"] = built.block
            state["context_snapshot"] = built.snapshot
            await context_service.append_context_event(session, run, built)
    # 任务清单(M4):快照随状态进图(节点是纯函数,不查库);resume 时同一条
    # 装载路径会重新读 runs.todos,checkpoint 里没有清单也不影响显示。
    loaded_todos = sanitize_todos(run.todos)
    if loaded_todos:
        state["todos"] = loaded_todos
    try:
        # 工具 handler 执行时读得到租户/run/对话(stdio MCP 沙箱作用域);
        # 正文增量通道也在这个边界绑定(节点只上报,落库在这里)
        with bind_run_llm(session, run), _tool_context(run):
            async with (
                _delta_stream(session, run) as projector,
                _step_stream(session, run) as step_projector,
                _item_stream(
                    session, run, step_projector, thread_id=plan_thread_id
                ) as item_projector,
                _usage_boundary(session, run),
            ):
                values = await graph.ainvoke(
                    state,
                    config=_config(run, projector, step_projector, item_projector),
                )
    except Exception as exc:
        await append_item_event(
            session,
            run,
            thread_id=plan_thread_id,
            item_id=plan_item_id,
            kind=items.KIND_PLAN,
            phase=items.PHASE_FAILED,
            status=items.STATUS_FAILED,
            title=plan_title,
            node="plan",
            data={
                "version": plan_version,
                "supersedes": plan_supersedes,
                "reason": f"{type(exc).__name__}: {exc}",
            },
            started_at=plan_started_at,
            finished_at=datetime.now(UTC),
        )
        await _fail_run(session, run, exc, stage="plan")
        return
    run.plan_markdown = values.get("plan_markdown", "") or ""
    plan_text, plan_chars, plan_truncated = plan_content(run.plan_markdown)
    await append_item_event(
        session,
        run,
        thread_id=plan_thread_id,
        item_id=plan_item_id,
        kind=items.KIND_PLAN,
        phase=items.PHASE_COMPLETED,
        status=items.STATUS_COMPLETED,
        title=plan_title,
        node="plan",
        step_id=await latest_step_id(session, run, "plan"),
        data={
            "version": plan_version,
            "supersedes": plan_supersedes,
            "content": plan_text,
            "chars": plan_chars,
            "truncated": plan_truncated,
        },
        started_at=plan_started_at,
        finished_at=datetime.now(UTC),
    )
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
    # P0-2:自动放行后图已在执行段,落 running 再投影终态(时间线如实)。
    run.status = RunStatus.RUNNING.value
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
