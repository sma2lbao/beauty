"""run 恢复语义(P1-C,见 docs/design/recovery.md §6/§7/§8):续跑、回退重跑与分叉。

recover:执行中途进程崩溃后,从该 run 最近的 checkpoint 继续跑(attempts 不变,
线程不变)。崩溃时"状态已推进到 planning/running"可能已随增量提交落库,而正在
执行的 job 事务回滚——重领旧 job 按原动作只会在状态守卫上 409,所以这里不重放
动作,直接把图从 checkpoint 拉起来跑完。没有 checkpoint 时才退化为新 attempt
的重新规划(旧行为)。

rewind:把已终态(completed/failed/cancelled/blocked)的 run 拉回 running,从指定
checkpoint 继续跑**同一条**线程(attempts 不变)。它不重置已经发生的外部副作用
(已被工具改掉的东西不会回滚),只重放 checkpoint 之后的节点。

fork:以父 run 为模板新起一条 run(状态 pending),可选择性把父 checkpoint 的
状态种进子线程,让子 run 从"父当时的进度"继续;不复制事件/交付物/用量。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from common.artifacts import ArtifactStore
from common.errors import ConflictError
from common.logging import get_logger
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.context.pipeline import ContextPipeline
from agent_orchestrator.core.config import Settings
from agent_orchestrator.graph.cancellation import RunCancelled
from agent_orchestrator.graph.state import RunGraphState
from agent_orchestrator.models.runs import PARENT_KIND_FORK, Run
from agent_orchestrator.repositories import JobRepository, RunRepository
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.llm_binding import bind_run_llm
from agent_orchestrator.services.run_cancellation import (
    settle_cancelled,
)
from agent_orchestrator.services.run_checkpoints import (
    checkpoint_id_of,
    find_checkpoint,
    thread_config,
)
from agent_orchestrator.services.run_events import (
    _delta_stream,
    _fail_run,
    _item_stream,
    _step_stream,
    append_event,
)
from agent_orchestrator.services.run_execution_approvals import (
    _TERMINAL_OR_CANCELLED,
    _apply_graph_result,
    _config,
    _interrupt_payload,
    _tool_context,
)
from agent_orchestrator.services.run_execution_invoke import _invoke_plan
from agent_orchestrator.services.run_lifecycle import (
    MAX_TITLE,
    mark_started,
    require_run,
    require_transition,
)
from agent_orchestrator.services.run_quota import _usage_boundary
from agent_orchestrator.services.run_side_effects import (
    SideEffect,
    require_acknowledged,
    side_effect_summary,
)
from agent_orchestrator.services.run_trace import run_trace
from agent_orchestrator.services.workspace import Workspace

logger = get_logger(__name__)

#: checkpoint 不存在或不属于该线程(§6:409,不泄漏别的线程是否存在)
CHECKPOINT_NOT_FOUND_CODE = "run.checkpoint_not_found"
CHECKPOINT_NOT_FOUND_MESSAGE = "checkpoint 不存在或不属于当前 run"
FORK_TITLE_SUFFIX = "（分叉）"
REWIND_EVENT = "run.rewind.v1"
#: 崩溃续跑:resumed 表示从 checkpoint 接着跑;fallback="plan" 表示没有 checkpoint,
#: 退化为新 attempt 的重新规划
RECOVER_EVENT = "run.recovered.v1"
#: 只有"执行中"的 run 才需要续跑:其余状态要么尚未起图(pending/awaiting_*),
#: 要么已有明确归属(终态、被取消)
RECOVERABLE_STATUSES = frozenset({RunStatus.PLANNING.value, RunStatus.RUNNING.value})


def _checkpoint_conflict(run: Run, checkpoint_id: str) -> ConflictError:
    return ConflictError(
        CHECKPOINT_NOT_FOUND_CODE,
        CHECKPOINT_NOT_FOUND_MESSAGE,
        details={"run_id": run.id, "checkpoint_id": checkpoint_id},
    )


def _rewind_config(
    run: Run,
    checkpoint_id: str,
    projector: object | None = None,
    step_projector: object | None = None,
    item_projector: object | None = None,
    *,
    session: AsyncSession | None = None,
) -> dict[str, object]:
    """在图配置上钉住起点 checkpoint(其余 configurable 与常规执行一致)。"""
    config = _config(  # type: ignore[arg-type]
        run, projector, step_projector, item_projector, session=session
    )
    configurable = config.setdefault("configurable", {})
    configurable["checkpoint_id"] = checkpoint_id
    return config


async def recover_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    account_id: str,
    run_id: str,
    *,
    jobs_enabled: bool = False,
    from_job: bool = True,
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
    settings: Settings,
    context: ContextPipeline | None = None,
) -> Run:
    """从最近 checkpoint 续跑一个"执行中"的 run(崩溃恢复,§8)。

    from_job 时状态已由崩溃前的执行推成 planning/running,worker 只负责把图
    拉起来跑完;状态已被推进(或被取消/超时收尾)则本 job 无事可做,直接返回。

    三种现场:
    - 图停在人工闸口(tasks 带 interrupt):状态没来得及落库,按既有闸口语义补落,
      不重跑已中断的节点;
    - 图已跑完(next 为空):进程死在结果落库前,直接用快照 values 投影终态;
    - 图还有下一步(next 非空):ainvoke(None) 从 checkpoint 续跑。

    没有任何 checkpoint 时退化为"新 attempt 重新规划":崩溃发生在第一次落
    checkpoint 之前,没有可续的进度。
    """
    run = await require_run(session, account_id, run_id)
    from_status = run.status
    if from_status not in RECOVERABLE_STATUSES:
        if from_job:
            return run
        require_transition(run, "recover")
    snapshot = await graph.aget_state(thread_config(run))
    values: dict[str, Any] = dict(getattr(snapshot, "values", None) or {})
    next_nodes = [str(node) for node in (getattr(snapshot, "next", None) or ())]
    checkpoint_id = checkpoint_id_of(snapshot) if snapshot is not None else None
    if snapshot is None or (not next_nodes and not values):
        return await _recover_without_checkpoint(
            session,
            graph,
            run,
            from_status=from_status,
            account_id=account_id,
            jobs_enabled=jobs_enabled,
            store=store,
            workspace=workspace,
            downloader=downloader,
            max_download_files=max_download_files,
            settings=settings,
            context=context,
        )
    interrupts = tuple(
        interrupt
        for task in (getattr(snapshot, "tasks", None) or ())
        for interrupt in (getattr(task, "interrupts", None) or ())
    )
    resumed = bool(next_nodes) and not interrupts
    await append_event(
        session,
        run,
        RECOVER_EVENT,
        {
            "attempt": run.attempts,
            "from_status": from_status,
            "checkpoint_id": checkpoint_id,
            "next": next_nodes,
            "resumed": resumed,
            "fallback": None,
        },
    )
    if interrupts:
        # 图停在闸口但状态未落库:把 interrupt 补进 values,交给既有闸口语义
        values["__interrupt__"] = interrupts
        return await _settle_recovered_run(
            session,
            run,
            values,
            store=store,
            workspace=workspace,
            downloader=downloader,
            max_download_files=max_download_files,
        )
    if not next_nodes:
        # 图已跑完,只是结果没落库:快照 values 就是终态
        return await _settle_recovered_run(
            session,
            run,
            values,
            store=store,
            workspace=workspace,
            downloader=downloader,
            max_download_files=max_download_files,
        )
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
                        None,
                        config=_config(
                            run,
                            projector,
                            step_projector,
                            item_projector,
                            session=session,
                        ),
                    )
    except RunCancelled:
        return await settle_cancelled(session, run, stage="recover")
    except Exception as exc:
        await _fail_run(session, run, exc, stage="recover")
        return run
    return await _settle_recovered_run(
        session,
        run,
        values,
        store=store,
        workspace=workspace,
        downloader=downloader,
        max_download_files=max_download_files,
    )


async def _recover_without_checkpoint(
    session: AsyncSession,
    graph: CompiledStateGraph,
    run: Run,
    *,
    from_status: str,
    account_id: str,
    jobs_enabled: bool,
    store: ArtifactStore | None,
    workspace: Workspace | None,
    downloader: FileDownloader | None,
    max_download_files: int,
    settings: Settings,
    context: ContextPipeline | None,
) -> Run:
    """没有可续的 checkpoint:新 attempt 从计划重跑(旧语义,显式记 fallback)。"""
    run.attempts += 1
    run.status = RunStatus.PENDING.value
    run.status_reason = None
    await append_event(
        session,
        run,
        RECOVER_EVENT,
        {
            "attempt": run.attempts,
            "from_status": from_status,
            "checkpoint_id": None,
            "next": [],
            "resumed": False,
            "fallback": "plan",
        },
    )
    if jobs_enabled:
        await jobs_service.enqueue(session, account_id, "plan", run.id)
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


async def _settle_recovered_run(
    session: AsyncSession,
    run: Run,
    values: dict[str, Any],
    *,
    store: ArtifactStore | None,
    workspace: Workspace | None,
    downloader: FileDownloader | None,
    max_download_files: int,
) -> Run:
    """续跑结果的收尾:计划闸中断与普通终态各走既有语义。"""
    interrupt = _interrupt_payload(values)
    if interrupt.get("kind") == "plan":
        run.plan_markdown = values.get("plan_markdown") or run.plan_markdown or ""
        run.status = RunStatus.AWAITING_APPROVAL.value
        await append_event(
            session,
            run,
            "run.planned.v1",
            {"attempt": run.attempts, "recovered": True},
        )
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


async def rewind_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    account_id: str,
    run_id: str,
    checkpoint_id: str,
    reason: str | None = None,
    *,
    acknowledge_side_effects: bool = False,
    jobs_enabled: bool = False,
    emit_rewound: bool = True,
    from_job: bool = False,
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
    settings: Settings,
) -> Run:
    """从 checkpoint 回退重跑(§6):终态 → running,同一线程继续。

    from_job 时状态与事件已由 API 侧在同一请求里推进,worker 只负责执行;
    执行前仍复核 checkpoint 归属,失效则把 run 落 failed(不让它挂死在 running)。
    """
    run = await require_run(session, account_id, run_id)
    if from_job:
        if run.status in _TERMINAL_OR_CANCELLED:
            return run
    else:
        require_transition(run, "rewind")
    snapshot = await find_checkpoint(graph, run, checkpoint_id)
    if snapshot is None:
        conflict = _checkpoint_conflict(run, checkpoint_id)
        if from_job:
            await _fail_run(session, run, conflict, stage="rewind")
            return run
        raise conflict
    # S5:回退会重放 checkpoint 之后的节点,已发生的外部写无法回滚(参数变了
    # 就是新的写)。发起方未显式确认时直接拒绝,并把台账摘要放进 409 details。
    effects: list[SideEffect] = []
    if not from_job:
        effects = await require_acknowledged(
            session,
            account_id,
            run.id,
            acknowledge=acknowledge_side_effects,
            action="rewind",
        )
    from_status = run.status
    if not from_job:
        # 排队中的旧 job(例如上一轮的 plan/confirm)已无意义,直接作废
        await JobRepository(session).cancel_queued_for_run(run.account_id, run.id)
        run.status = RunStatus.RUNNING.value
        run.status_reason = None
        # rewind 是终态后的又一次真实执行:重置超时基准,否则旧 started_at 会让
        # 超时 sweeper 立刻把刚拉起的 run 再判一次超时(见报告"偏离")。
        run.started_at = datetime.now(UTC)
        mark_started(run)
    if emit_rewound:
        await append_event(
            session,
            run,
            REWIND_EVENT,
            {
                "checkpoint_id": checkpoint_id,
                "from_status": from_status,
                "reason": reason,
                "side_effects": side_effect_summary(effects),
            },
        )
    if jobs_enabled:
        await jobs_service.enqueue(
            session,
            account_id,
            "rewind",
            run.id,
            payload={"checkpoint_id": checkpoint_id},
        )
        return run
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
                        None,
                        config=_rewind_config(
                            run,
                            checkpoint_id,
                            projector,
                            step_projector,
                            item_projector,
                            session=session,
                        ),
                    )
    except RunCancelled:
        return await settle_cancelled(session, run, stage="rewind")
    except Exception as exc:
        await _fail_run(session, run, exc, stage="rewind")
        return run
    # 回退到计划节点之后的 checkpoint 时,图会重放审批闸并再次中断(kind=plan):
    # 这条分支与 _invoke_plan 的"计划完成待审批"语义一致,不能落到确认闸分支。
    interrupt = _interrupt_payload(values)
    if interrupt.get("kind") == "plan":
        run.plan_markdown = values.get("plan_markdown") or run.plan_markdown or ""
        run.status = RunStatus.AWAITING_APPROVAL.value
        await append_event(
            session,
            run,
            "run.planned.v1",
            {"attempt": run.attempts, "rewound": True},
        )
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


def _fork_title(parent: Run, title: str | None) -> str:
    text = (title or "").strip() or f"{parent.title}{FORK_TITLE_SUFFIX}"
    return text[:MAX_TITLE]


def _fork_child(parent: Run, title: str) -> Run:
    """按父 run 快照构造子 run:只继承配置/目标,不继承执行痕迹。"""
    return Run(
        account_id=parent.account_id,
        title=title,
        goal=parent.goal,
        status=RunStatus.PENDING.value,
        acceptance_criteria=list(parent.acceptance_criteria or []),
        plan_markdown=parent.plan_markdown,
        conversation_id=parent.conversation_id,
        workspace_id=parent.workspace_id,
        skill_id=parent.skill_id,
        skill_system_prompt=parent.skill_system_prompt,
        skill_tools=list(parent.skill_tools or []),
        skill_version_id=parent.skill_version_id,
        skill_object_key=parent.skill_object_key,
        skill_sha256=parent.skill_sha256,
        skill_entrypoint=parent.skill_entrypoint,
        skill_runtime=parent.skill_runtime,
        skill_network=parent.skill_network,
        skill_timeout_seconds=parent.skill_timeout_seconds,
        mcp_server_ids=list(parent.mcp_server_ids or []),
        connector_providers=list(parent.connector_providers or []),
        model=parent.model,
        parent_run_id=parent.id,
        parent_kind=PARENT_KIND_FORK,
        priority=parent.priority,
        timeout_seconds=parent.timeout_seconds,
    )


async def _seed_fork_state(
    graph: CompiledStateGraph,
    parent: Run,
    child: Run,
    checkpoint_id: str,
) -> None:
    """把父 checkpoint 的状态种进子线程;无法安全定位 as_node 时降级不种。"""
    history = [state async for state in graph.aget_state_history(thread_config(parent))]
    index = next(
        (
            i
            for i, state in enumerate(history)
            if checkpoint_id_of(state) == checkpoint_id
        ),
        None,
    )
    if index is None:
        raise _checkpoint_conflict(parent, checkpoint_id)
    target = history[index]
    values = dict(getattr(target, "values", None) or {})
    allowed = set(RunGraphState.__annotations__)
    seed = {key: value for key, value in values.items() if key in allowed}
    older = history[index + 1] if index + 1 < len(history) else None
    candidates = list(getattr(older, "next", ()) or ()) if older is not None else []
    if not seed or len(candidates) != 1:
        # as_node 不定或状态为空:宁可继承配置从起点重跑,也不猜错节点
        logger.warning(
            "fork.seed_checkpoint_skipped",
            run_id=parent.id,
            new_run_id=child.id,
            checkpoint_id=checkpoint_id,
            next=list(candidates),
        )
        return
    try:
        await graph.aupdate_state(thread_config(child), seed, as_node=candidates[0])
    except Exception as exc:  # noqa: BLE001 - 种入失败不阻塞 fork 本身
        logger.warning(
            "fork.seed_checkpoint_failed",
            run_id=parent.id,
            new_run_id=child.id,
            checkpoint_id=checkpoint_id,
            error=str(exc),
        )
        return
    logger.info(
        "fork.seeded_checkpoint",
        run_id=parent.id,
        new_run_id=child.id,
        checkpoint_id=checkpoint_id,
        as_node=candidates[0],
    )


async def fork_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    account_id: str,
    run_id: str,
    *,
    checkpoint_id: str | None = None,
    title: str | None = None,
    acknowledge_side_effects: bool = False,
) -> Run:
    """以父 run 为模板新建 run(§7);源 run 状态不限,也不被改动。

    带 checkpoint 种入时子 run 会重放父 run 在该快照之后的节点:父 run 已发生的
    外部写不会被子线程的幂等键命中(键含 run_id),所以同样要求显式确认(S5)。
    """
    parent = await require_run(session, account_id, run_id)
    effects: list[SideEffect] = []
    if checkpoint_id:
        effects = await require_acknowledged(
            session,
            account_id,
            parent.id,
            acknowledge=acknowledge_side_effects,
            action="fork",
        )
    child = _fork_child(parent, _fork_title(parent, title))
    await RunRepository(session).add(child)
    payload: dict[str, object] = {
        "title": child.title,
        "skill_id": child.skill_id,
        "parent_run_id": parent.id,
        "parent_kind": PARENT_KIND_FORK,
        "forked_from": parent.id,
    }
    if checkpoint_id:
        payload["checkpoint_id"] = checkpoint_id
        payload["side_effects"] = side_effect_summary(effects)
    await append_event(session, child, "run.created.v1", payload)
    if checkpoint_id:
        await _seed_fork_state(graph, parent, child, checkpoint_id)
    return child
