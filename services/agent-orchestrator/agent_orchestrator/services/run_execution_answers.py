"""交互式提问的回答(设计 §10.5:POST /runs/{id}/answer 与 job 共用的执行入口)。

与审批/确认同构:API 侧只推进状态机并落事件,真正的图恢复经
Command(resume=...) 回到 ask_gate 节点;启用 jobs 时由 worker 走
from_job=True 路径(API 已把状态推到 running,守卫不重复校验)。

幂等:重复投递的 answer job 通过"最近一次 answered 事件的 question_id"
识别并直接收手;期间被取消或已终态的 run 同样不再执行。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from common.artifacts import ArtifactStore
from common.errors import ConflictError
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import (
    QUESTION_ANSWERED_EVENT,
    QUESTION_REQUIRED_EVENT,
    RunEventRepository,
)
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.llm_binding import bind_run_llm
from agent_orchestrator.services.run_events import (
    _delta_stream,
    _fail_run,
    _step_stream,
    append_event,
)
from agent_orchestrator.services.run_execution_approvals import (
    _TERMINAL_OR_CANCELLED,
    _apply_graph_result,
    _config,
    _tool_context,
)
from agent_orchestrator.services.run_lifecycle import require_run, require_transition
from agent_orchestrator.services.run_quota import _usage_boundary
from agent_orchestrator.services.workspace import Workspace

#: 挂起问题已过 expires_at(设计 §10.5):客户端应答一律 409,等 sweeper 自动继续。
QUESTION_EXPIRED_CODE = "run.question_expired"
#: 回答的 question_id 与当前挂起问题不一致(问题已被回答/已被新问题替换):
#: 客户端刷新事件流后重试,不能把旧答案套到新问题上。
QUESTION_MISMATCH_CODE = "run.question_mismatch"

MAX_REASON_CHARS = 500
MAX_ANSWERED_BY_CHARS = 64


def _expired(expires_at: Any) -> bool:
    """挂起问题是否已过期;无法解析的时间一律按未过期处理(不误伤)。"""
    if not expires_at:
        return False
    try:
        deadline = datetime.fromisoformat(str(expires_at))
    except ValueError:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return datetime.now(UTC) > deadline


def _event_payload(event: Any) -> dict[str, Any]:
    """事件载荷归一成 dict(缺失/非 dict 一律空)。"""
    if event is None:
        return {}
    payload = event.payload
    return dict(payload) if isinstance(payload, dict) else {}


def _normalize_answers(answers: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """答案只保留契约字段(index/labels/custom),避免把客户端杂项带进事件。"""
    normalized: list[dict[str, Any]] = []
    for item in answers or []:
        if not isinstance(item, dict):
            continue
        custom = item.get("custom")
        labels = item.get("labels") or []
        normalized.append(
            {
                "index": int(item.get("index") or 0),
                "labels": [str(label) for label in labels][:4],
                "custom": str(custom) if custom is not None else None,
            }
        )
    return normalized[:4]


async def answer_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    run_id: str,
    *,
    question_id: str,
    action: str,
    answers: list[dict[str, Any]] | None = None,
    reason: str | None = None,
    answered_by: str | None = None,
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
    jobs_enabled: bool = False,
    emit_answered: bool = True,
    from_job: bool = False,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
) -> Run:
    """回答/放弃挂起的交互式提问(设计 §10.5)。

    流程:状态机校验 → question_id 与挂起问题比对 → 过期校验 →
    事件流记 run.question.answered.v1 → 状态回到 running →
    jobs 入队(worker 恢复图)或直接 resume 图。
    """
    run = await require_run(session, tenant_id, run_id)
    events = RunEventRepository(session)
    required = await events.latest_of_type(run.id, QUESTION_REQUIRED_EVENT)
    pending = _event_payload(required)
    pending_id = str(pending.get("question_id") or "")
    if from_job:
        # worker 回来执行:API 侧已把状态推到 running;期间被取消或已终态
        # (例如取消后 job 才被领到)直接收手。
        if run.status in _TERMINAL_OR_CANCELLED:
            return run
        # 陈旧 job(问题已被回答、或图已挂到下一个问题):不把旧答案套到新问题,
        # 也不改状态——这是 from_job 重放幂等的兜底。
        if question_id != pending_id:
            return run
        if run.status == RunStatus.AWAITING_INPUT.value:
            # 超时 sweeper 直接入队:timed_out/answered 事件已由 sweeper 落库,
            # 这里负责把状态推进到 running 再恢复图。
            run.status = RunStatus.RUNNING.value
        elif run.status != RunStatus.RUNNING.value:
            return run
    else:
        require_transition(run, "answer")
        run.status = RunStatus.RUNNING.value
        if question_id != pending_id:
            raise ConflictError(
                QUESTION_MISMATCH_CODE,
                "提问已变化,请刷新后重新回答",
                details={"run_id": run.id, "question_id": question_id},
            )
        if _expired(pending.get("expires_at")):
            raise ConflictError(
                QUESTION_EXPIRED_CODE,
                "问题已超时,将自动继续执行",
                details={"run_id": run.id, "question_id": pending_id},
            )
    payload = {
        "question_id": pending_id,
        "answers": _normalize_answers(answers),
        "action": action,
        "reason": (reason or "")[:MAX_REASON_CHARS] or None,
        "answered_by": (answered_by or "")[:MAX_ANSWERED_BY_CHARS] or None,
    }
    if emit_answered:
        await append_event(session, run, QUESTION_ANSWERED_EVENT, payload)
    if jobs_enabled and not from_job:
        await jobs_service.enqueue(
            session,
            tenant_id,
            "answer",
            run.id,
            payload={
                "question_id": pending_id,
                "action": action,
                "answers": payload["answers"],
                "reason": payload["reason"],
                "answered_by": payload["answered_by"],
            },
        )
        return run
    try:
        with bind_run_llm(session, run), _tool_context(run):
            async with (
                _delta_stream(session, run) as projector,
                _step_stream(session, run) as step_projector,
                _usage_boundary(session, run),
            ):
                values = await graph.ainvoke(
                    Command(
                        resume={
                            "action": action,
                            "answers": payload["answers"],
                            "question_id": pending_id,
                            "reason": payload["reason"],
                        }
                    ),
                    config=_config(run, projector, step_projector),
                )
    except Exception as exc:
        await _fail_run(session, run, exc, stage="answer")
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
