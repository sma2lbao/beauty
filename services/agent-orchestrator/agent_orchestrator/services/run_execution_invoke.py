"""起图与审批闸边界:组装图状态、注入记忆/上下文、投影计划与终态。

从 services/run_execution.py 拆出(门面仍由 run_execution.py 全量重导出);
依赖方向仍是 invoke → {approvals, memory, run_events, items},不反向引用
run_execution,避免与门面的循环 import。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from common.artifacts import ArtifactStore
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.context.pipeline import ContextPipeline
from agent_orchestrator.core.config import Settings
from agent_orchestrator.graph import items
from agent_orchestrator.graph.todos import sanitize_todos
from agent_orchestrator.models.runs import PARENT_KIND_SUBAGENT, Run
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services import approval as approval_service
from agent_orchestrator.services import context_service
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
from agent_orchestrator.services.run_execution_approvals import (
    _apply_graph_result,
    _config,
    _tool_context,
)
from agent_orchestrator.services.run_execution_memory import _recall_memory_block
from agent_orchestrator.services.run_lifecycle import (
    _consume_instructions,
    _conversation_history,
)
from agent_orchestrator.services.run_quota import _usage_boundary
from agent_orchestrator.services.tool_names import normalize_tool_names
from agent_orchestrator.services.workspace import Workspace


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
