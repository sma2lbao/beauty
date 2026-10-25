"""run 生命周期与状态机:创建/列表/指令队列/多轮上下文/状态守卫。

状态迁移表 TRANSITIONS 是唯一事实来源:各对外动作只声明动作名,
非法迁移统一 ConflictError(409),错误码与文案与旧版一致。
"""

from __future__ import annotations

from common.errors import ConflictError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.runs import PendingInstruction, Run
from agent_orchestrator.repositories import (
    ConversationRepository,
    InstructionRepository,
    MessageRepository,
    RunRepository,
)
from agent_orchestrator.schemas.runs import RunCreate, RunStatus
from agent_orchestrator.services import compaction, history
from agent_orchestrator.services.compaction import CompactionResult
from agent_orchestrator.services.run_events import append_event
from agent_orchestrator.services.run_quota import _enforce_quota
from agent_orchestrator.services.skill_packages import SkillDefinition

#: 动作 → 允许的源状态(状态迁移表)。
TRANSITIONS: dict[str, frozenset[RunStatus]] = {
    "plan": frozenset({RunStatus.PENDING}),
    "update_plan": frozenset({RunStatus.AWAITING_APPROVAL}),
    "approve": frozenset({RunStatus.AWAITING_APPROVAL}),
    "confirm": frozenset({RunStatus.AWAITING_CONFIRMATION}),
    "reject": frozenset({RunStatus.AWAITING_APPROVAL}),
    "retry": frozenset({RunStatus.FAILED}),
    "resume": frozenset({RunStatus.BLOCKED}),
    # 交互提问回答(tool-service §10.2):只有挂起提问时能回答;answer API 专用。
    "answer": frozenset({RunStatus.AWAITING_INPUT}),
    # 取消(P0-2):非终态都可取消。running/planning 是协作式——已发出的图调用
    # 不被打断,执行边界回来读库发现 cancelled 后跳过终态覆盖(见 _apply_graph_result)。
    "cancel": frozenset(
        {
            RunStatus.PENDING,
            RunStatus.PLANNING,
            RunStatus.RUNNING,
            RunStatus.AWAITING_APPROVAL,
            RunStatus.AWAITING_CONFIRMATION,
            RunStatus.AWAITING_INPUT,
            RunStatus.BLOCKED,
        }
    ),
}


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


def require_transition(run: Run, action: str) -> None:
    """状态机守卫:按迁移表校验当前状态是否允许该动作(非法迁移 → 409)。"""
    _require_status(run, *TRANSITIONS[action])


async def create_run(
    session: AsyncSession,
    tenant_id: str,
    data: RunCreate,
    *,
    skill: SkillDefinition | None = None,
    compaction: CompactionResult | None = None,
) -> Run:
    """建 run;skill 定义(内置或上传包)在此**冻结**为快照列。

    上传包额外冻结 version_id/object_key/sha256 与执行字段——之后用户上传
    新版本不影响本 run(P3-C 的 skill_run 按 run 上的快照取包执行)。
    工作区归属:显式给的就用,否则从所属对话继承(对话在哪个工作区,run 就在哪)。
    配额(P0-2):建 run 前先过准入闸,超限直接 429,不留下半残 run。
    compaction 非空表示建 run 前刚压缩过对话历史:摘要已落库(执行图据此
    只读摘要),这里补一条 run.compacted.v1 事件,让事件流/前端能看到
    "这轮上下文被折叠过"——压缩必须可观测,否则排查上下文丢失时没有依据。
    """
    await _enforce_quota(session, tenant_id)
    manifest = skill.manifest if skill else None
    workspace_id = data.workspace_id
    if workspace_id is None and data.conversation_id:
        conversation = await ConversationRepository(session).get_for_tenant(
            tenant_id, data.conversation_id
        )
        if conversation is not None:
            workspace_id = conversation.workspace_id
    run = Run(
        tenant_id=tenant_id,
        title=data.title,
        goal=data.goal,
        status=RunStatus.PENDING.value,
        acceptance_criteria=data.acceptance_criteria,
        conversation_id=data.conversation_id,
        workspace_id=workspace_id,
        skill_id=manifest.id if manifest else data.skill_id,
        skill_system_prompt=manifest.system_prompt if manifest else None,
        skill_tools=list(manifest.tools) if manifest else None,
        skill_version_id=skill.version_id if skill else None,
        skill_object_key=skill.object_key if skill else None,
        skill_sha256=skill.sha256 if skill else None,
        skill_entrypoint=list(skill.entrypoint) if skill and skill.entrypoint else None,
        skill_runtime=skill.runtime if skill else None,
        skill_network=skill.network if skill else None,
        skill_timeout_seconds=skill.timeout_seconds if skill else None,
        mcp_server_ids=list(data.mcp_server_ids or []),
    )
    await RunRepository(session).add(run)
    await append_event(
        session,
        run,
        "run.created.v1",
        {"title": run.title, "skill_id": run.skill_id},
    )
    if compaction is not None:
        # 纯展示类事件(同 token.delta.v1/run.step.v1):只进 run 事件流供排查,
        # 不进 outbox——HTTP 路径挂了 recorder 而 job worker 路径没挂,进 outbox
        # 会让同一事件在两条路径上投递行为不一致;将来要跨服务消费再打开。
        await append_event(
            session,
            run,
            "run.compacted.v1",
            {
                "summary_chars": len(compaction.summary),
                "covered_messages": compaction.covered_messages,
                "tokens_before": compaction.tokens_before,
                "tokens_after": compaction.tokens_after,
            },
            record_outbox=False,
        )
    return run


async def list_runs(
    session: AsyncSession, tenant_id: str, limit: int = 50
) -> list[Run]:
    """租户内 run 列表:最新在前(控制台首页用);id 兜底保证顺序确定。"""
    return await RunRepository(session).list_recent(tenant_id, limit)


async def queue_instruction(
    session: AsyncSession, tenant_id: str, run_id: str, content: str
) -> PendingInstruction:
    """追加指令(§6.5):排队等下一个图边界消费;终态 run 拒绝。"""
    run = await require_run(session, tenant_id, run_id)
    if run.status in (
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    ):
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

    已压缩的消息不再重复带入:摘要覆盖点之前的原文由摘要块代表,只回放
    覆盖点之后的原文(压缩在发消息/建 run 之前完成,执行图这里不调模型)。
    """
    summary, covered_until = await compaction.summary_for(
        session, run.tenant_id, run.conversation_id
    )
    rows = await MessageRepository(session).list_history(
        run.tenant_id,
        run.conversation_id,
        before=run.created_at,
        exclude_run_id=run.id,
    )
    return history.to_prompt(history.filter_after(rows, covered_until), summary=summary)


MAX_TITLE = 200
RETRY_SUFFIX = "(重试)"


def _retry_title(title: str) -> str:
    """派生 run 的标题:带 (重试) 标记便于在列表里区分,并守住列宽。"""
    return f"{title}{RETRY_SUFFIX}"[:MAX_TITLE]
