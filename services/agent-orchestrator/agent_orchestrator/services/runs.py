"""run 编排业务逻辑:不 import FastAPI,可独立单测(分层约定见架构 §4)。

graph 生命周期(架构 §6.2):

- plan:新 attempt 起图(thread = ``<run_id>#<attempt>``),跑到审批闸中断
  → awaiting_approval;
- approve:放行闸门,execute → verify(返工上限见 builder)→ deliver → completed;
- reject:拒绝计划,图终结 → blocked;resume:新 attempt 重新进闸,保留已编辑计划;
- 状态守卫:非法迁移 ConflictError(409),资源不存在 NotFoundError(404),
  对外渲染走 common 统一错误模型。
"""

from __future__ import annotations

from typing import Any

from common.artifacts import ArtifactStore
from common.errors import ConflictError
from events.envelope import EventEnvelope
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.conversations import ChatMessage
from agent_orchestrator.models.runs import (
    PendingInstruction,
    Run,
    RunArtifact,
    RunEvent,
)
from agent_orchestrator.repositories import (
    TOOL_CALLED_EVENT,
    ArtifactRepository,
    InstructionRepository,
    MessageRepository,
    RunEventRepository,
    RunRepository,
)
from agent_orchestrator.schemas.runs import RunCreate, RunStatus
from agent_orchestrator.services import artifacts, history
from agent_orchestrator.services.workspace import Workspace


async def append_event(
    session: AsyncSession, run: Run, type_: str, payload: dict[str, Any]
) -> RunEvent:
    """追加 run 事件:seq 每 run 单调递增;信封负责命名/版本校验(§6.3)。"""
    env = EventEnvelope.create(type_, payload)
    events = RunEventRepository(session)
    event = RunEvent(
        id=env.id,
        tenant_id=run.tenant_id,
        run_id=run.id,
        seq=await events.next_seq(run.id),
        type=env.type,
        payload=env.payload,
        occurred_at=env.occurred_at,
    )
    await events.add(event)
    # 事务性 outbox(§6.3):与 run_events 同事务登记待投递事件;
    # recorder 由 API 层挂在 session.info 上(见 api/deps.get_session)
    recorder = session.info.get("outbox")
    if recorder is not None:
        await recorder(session, run, env)
    return event


def _config(run: Run) -> dict[str, Any]:
    """graph 线程标识:同一 run 的每次 blocked→resume 周期一个新线程。"""
    return {"configurable": {"thread_id": f"{run.id}#{run.attempts}"}}


async def require_run(session: AsyncSession, tenant_id: str, run_id: str) -> Run:
    """取租户内 run;不存在 → NotFoundError(404)。"""
    return await RunRepository(session).require_for_tenant(tenant_id, run_id)


def _require_status(run: Run, *allowed: RunStatus) -> None:
    if run.status not in {status.value for status in allowed}:
        raise ConflictError(
            "run.invalid_state",
            f"run 状态 {run.status} 不允许该操作",
            details={"run_id": run.id, "status": run.status},
        )


async def _append_assistant_message(
    session: AsyncSession, run: Run, content: str
) -> None:
    """run 到达停点时把"回复"落进对话(控制台直建、无会话的 run 为空操作)。

    这是 agent 在对话里的持久化投影:交付物/失败原因/拒绝原因——
    对话历史因此双向完整,后续 run 的多轮上下文也由此而来。
    """
    if not run.conversation_id:
        return
    await MessageRepository(session).add(
        ChatMessage(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            role="assistant",
            content=content,
        )
    )


async def _fail_run(
    session: AsyncSession, run: Run, exc: Exception, stage: str
) -> None:
    """图执行异常 → failed 终态(§6.2):状态落库 + run.failed.v1,不向调用方裸奔。

    这是图调用边界的统一兜底——节点/LLM/图本身的异常都在这里收敛;
    failed 是终态,重试语义是新起 run。
    """
    run.status = RunStatus.FAILED.value
    run.status_reason = f"{type(exc).__name__}: {exc}"[:500]
    await append_event(
        session,
        run,
        "run.failed.v1",
        {"stage": stage, "reason": run.status_reason},
    )
    await _append_assistant_message(session, run, f"执行失败:{run.status_reason}")


async def _persist_artifact(
    session: AsyncSession,
    run: Run,
    artifact: dict[str, Any],
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
) -> RunArtifact:
    """交付物落库/落对象存储 + run.delivered.v1(§7)。

    workspace 启用时**同时**把交付物镜像到工作区(§7 归属方向):
    镜像失败不丢交付物,但会把原因如实记进事件 payload。
    """
    record = await artifacts.persist_artifact(session, run, artifact, store)
    payload: dict[str, Any] = {
        "artifact_id": record.id,
        "kind": record.kind,
        "title": record.title,
        "chars": len(str(artifact.get("content") or "")),  # 字符数(文本)
        "size_bytes": record.size_bytes,  # 存储字节数(UTF-8)
        "storage": record.storage,
    }
    if workspace is not None:
        try:
            mirrored = await workspace.create_artifact(
                run.tenant_id,
                title=record.title,
                kind=record.kind,
                content=str(artifact.get("content") or ""),
                run_id=run.id,
            )
        except Exception as exc:  # 镜像失败:run 照常交付,但如实记账
            payload["workspace_error"] = f"{type(exc).__name__}: {exc}"
        else:
            payload["workspace_artifact_id"] = mirrored.get("id")
    await append_event(session, run, "run.delivered.v1", payload)
    return record


async def get_artifact(
    session: AsyncSession,
    tenant_id: str,
    run_id: str,
    store: ArtifactStore | None = None,
) -> artifacts.ArtifactView:
    """取件:未交付(未 completed)→ 404;内容按存储后端装载(§7 阶段 B)。"""
    row = await ArtifactRepository(session).require_for_run(tenant_id, run_id)
    return await artifacts.read_artifact(row, store)


async def queue_instruction(
    session: AsyncSession, tenant_id: str, run_id: str, content: str
) -> PendingInstruction:
    """追加指令(§6.5):排队等下一个图边界消费;终态 run 拒绝。"""
    run = await require_run(session, tenant_id, run_id)
    if run.status in (RunStatus.COMPLETED.value, RunStatus.FAILED.value):
        raise ConflictError(
            "run.invalid_state",
            f"run 已 {run.status},不能再追加指令",
            details={"run_id": run.id, "status": run.status},
        )
    instruction = PendingInstruction(
        tenant_id=tenant_id, run_id=run.id, content=content, status="queued"
    )
    await InstructionRepository(session).add(instruction)
    await append_event(
        session,
        run,
        "run.instruction.queued.v1",
        {"instruction_id": instruction.id, "chars": len(content)},
    )
    return instruction


async def list_instructions(
    session: AsyncSession, tenant_id: str, run_id: str
) -> list[PendingInstruction]:
    """该 run 的指令队列(queued/consumed 都返回,控制台据此展示)。"""
    await require_run(session, tenant_id, run_id)  # 404 校验 + 租户隔离
    return await InstructionRepository(session).list_for_run(tenant_id, run_id)


async def _consume_instructions(session: AsyncSession, run: Run) -> list[str]:
    """消费排队指令(§6.5):标记 consumed + 落事件,返回内容供并入图状态。"""
    queued = await InstructionRepository(session).list_queued(run.tenant_id, run.id)
    contents: list[str] = []
    for instruction in queued:
        instruction.status = "consumed"
        contents.append(instruction.content)
        await append_event(
            session,
            run,
            "run.instruction.consumed.v1",
            {"instruction_id": instruction.id, "chars": len(instruction.content)},
        )
    if contents:
        await session.flush()
    return contents


async def _conversation_history(
    session: AsyncSession, run: Run
) -> list[dict[str, str]]:
    """对话多轮上下文:run 建立之前的消息(排除本 run 自己的消息)。

    排除口径见 MessageRepository.list_history——触发本 run 的用户消息
    (先落库再建 run)与并发到达的追加指令都不会重复进上下文;
    截断口径(条数/单条字数)与直接对话共用 services.history。
    """
    rows = await MessageRepository(session).list_history(
        run.tenant_id,
        run.conversation_id,
        before=run.created_at,
        exclude_run_id=run.id,
    )
    return history.to_prompt(rows)


async def create_run(session: AsyncSession, tenant_id: str, data: RunCreate) -> Run:
    run = Run(
        tenant_id=tenant_id,
        title=data.title,
        goal=data.goal,
        status=RunStatus.PENDING.value,
        acceptance_criteria=data.acceptance_criteria,
        conversation_id=data.conversation_id,
    )
    await RunRepository(session).add(run)
    await append_event(session, run, "run.created.v1", {"title": run.title})
    return run


async def list_runs(
    session: AsyncSession, tenant_id: str, limit: int = 50
) -> list[Run]:
    """租户内 run 列表:最新在前(控制台首页用);id 兜底保证顺序确定。"""
    return await RunRepository(session).list_recent(tenant_id, limit)


async def _invoke_plan(
    session: AsyncSession, graph: CompiledStateGraph, run: Run
) -> None:
    """起图跑到审批闸;resume 场景保留用户已编辑的计划。"""
    state: dict[str, Any] = {
        "run_id": run.id,
        "tenant_id": run.tenant_id,
        "title": run.title,
        "goal": run.goal,
        "acceptance_criteria": list(run.acceptance_criteria or []),
    }
    if run.plan_markdown:
        state["plan_markdown"] = run.plan_markdown
    if run.conversation_id:
        # 对话触发的 run:带上多轮上下文,模型才能理解"接着刚才的做"
        state["conversation_history"] = await _conversation_history(session, run)
    # §6.5:规划边界消费排队指令,并入图状态供各节点遵循
    instructions = await _consume_instructions(session, run)
    if instructions:
        state["extra_instructions"] = instructions
    try:
        values = await graph.ainvoke(state, config=_config(run))
    except Exception as exc:
        await _fail_run(session, run, exc, stage="plan")
        return
    run.plan_markdown = values.get("plan_markdown", "")
    run.status = RunStatus.AWAITING_APPROVAL.value
    await append_event(session, run, "run.planned.v1", {"attempt": run.attempts})


async def plan_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    run_id: str,
) -> Run:
    run = await require_run(session, tenant_id, run_id)
    _require_status(run, RunStatus.PENDING)
    await _invoke_plan(session, graph, run)
    return run


async def update_plan(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    run_id: str,
    markdown: str,
) -> Run:
    run = await require_run(session, tenant_id, run_id)
    _require_status(run, RunStatus.AWAITING_APPROVAL)
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
) -> Run:
    """图调用结果的统一后处理(§6.2/§6.3/§7)。

    顺序即语义:工具审计 → 写操作确认中断 → 交付物 → 终态。

    注意:state.tool_calls 是 add reducer,恢复执行(如确认闸放行)后会带上
    历史累积记录——按已落库条数只发增量,避免事件重复。
    """
    records = values.get("tool_calls") or []
    emitted = await RunEventRepository(session).count_of_type(run.id, TOOL_CALLED_EVENT)
    for record in records[emitted or 0 :]:
        await append_event(session, run, "run.tool.called.v1", record)
    if values.get("__interrupt__"):
        # 写操作待人工确认:停在 awaiting_confirmation,等 POST /confirm
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
    if artifact:
        await _persist_artifact(session, run, artifact, store, workspace)
    run.rounds = values.get("rounds", 0)
    if values.get("final_status") == "failed":
        # 返工耗尽仍未验收:fail_node 的诚实终态(§6.1),事件流如实记录
        run.status = RunStatus.FAILED.value
        run.status_reason = (values.get("status_reason") or "验收未通过")[:500]
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
) -> Run:
    run = await require_run(session, tenant_id, run_id)
    _require_status(run, RunStatus.AWAITING_APPROVAL)
    # 放行动作先入事件流,再恢复图:执行失败时顺序如实(approved → failed)
    await append_event(session, run, "run.approved.v1", {"attempt": run.attempts})
    # §6.5:放行边界消费排队指令,经 resume 载荷由审批闸并入状态
    instructions = await _consume_instructions(session, run)
    try:
        values = await graph.ainvoke(
            Command(resume={"action": "approve", "instructions": instructions}),
            config=_config(run),
        )
    except Exception as exc:
        await _fail_run(session, run, exc, stage="execute")
        return run
    return await _apply_graph_result(session, run, values, store, workspace)


async def confirm_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    run_id: str,
    action: str,
    reason: str | None,
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
) -> Run:
    """写操作确认(§6.2):awaiting_confirmation → 放行执行 / 拒绝跳过,图继续。"""
    run = await require_run(session, tenant_id, run_id)
    _require_status(run, RunStatus.AWAITING_CONFIRMATION)
    await append_event(
        session,
        run,
        "run.confirmation.received.v1",
        {"action": action, "reason": reason},
    )
    # §6.5:确认边界消费排队指令,经 resume 载荷由确认闸并入状态
    instructions = await _consume_instructions(session, run)
    try:
        values = await graph.ainvoke(
            Command(
                resume={
                    "action": action,
                    "reason": reason,
                    "instructions": instructions,
                }
            ),
            config=_config(run),
        )
    except Exception as exc:
        await _fail_run(session, run, exc, stage="confirm")
        return run
    return await _apply_graph_result(session, run, values, store, workspace)


async def reject_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    run_id: str,
    reason: str,
) -> Run:
    run = await require_run(session, tenant_id, run_id)
    _require_status(run, RunStatus.AWAITING_APPROVAL)
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


MAX_TITLE = 200
RETRY_SUFFIX = "(重试)"


def _retry_title(title: str) -> str:
    """派生 run 的标题:带 (重试) 标记便于在列表里区分,并守住列宽。"""
    return f"{title}{RETRY_SUFFIX}"[:MAX_TITLE]


async def retry_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    run_id: str,
) -> Run:
    """failed → **另起一跑**并继承上下文(§6.2:failed 是终态,不重放旧图)。

    为什么不原地恢复:失败可能来自节点/模型/图本身,失败点的检查点不可信,
    原地重放会把同一处再炸一次;同时旧 run 的历史(failed + 原因)必须原样保留
    ——审计不能被"重试"篡改。

    继承的东西:目标/验收标准/**已批准的计划**(计划节点检测到已有计划会短路,
    所以只重走审批闸,不重复调用模型);不继承:轮次、失败原因、终止状态。
    """
    parent = await require_run(session, tenant_id, run_id)
    _require_status(parent, RunStatus.FAILED)
    child = Run(
        tenant_id=tenant_id,
        title=_retry_title(parent.title),
        goal=parent.goal,
        status=RunStatus.PENDING.value,
        acceptance_criteria=list(parent.acceptance_criteria or []),
        plan_markdown=parent.plan_markdown,
        parent_run_id=parent.id,
        conversation_id=parent.conversation_id,
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
    # 继承计划 → 直接进审批闸(人工再确认后才执行;副作用如已发邮件不会被自动撤销)
    await _invoke_plan(session, graph, child)
    return child


async def resume_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    run_id: str,
) -> Run:
    """blocked → 新 attempt 重新进审批闸,计划保留(§6.2 goal 语义)。"""
    run = await require_run(session, tenant_id, run_id)
    _require_status(run, RunStatus.BLOCKED)
    run.attempts += 1
    run.status_reason = None
    await append_event(session, run, "run.resumed.v1", {"attempt": run.attempts})
    await _invoke_plan(session, graph, run)
    return run


async def list_events(
    session: AsyncSession, tenant_id: str, run_id: str, after: int = 0
) -> list[RunEvent]:
    """事件回放:按 seq 升序(§6.3,SSE 断线重放与审计共用);after 为续传游标。"""
    return await RunEventRepository(session).list_for_run(
        tenant_id, run_id, after=after
    )
