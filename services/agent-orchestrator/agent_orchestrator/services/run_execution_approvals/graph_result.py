"""图结果落库:事件/消息/工件/评分与终态处理。"""

from __future__ import annotations

from typing import Any

from common.artifacts import ArtifactStore
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator import metrics
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import (
    QUESTION_REQUIRED_EVENT,
    SUBAGENT_WAITING_EVENT,
    TOOL_CALLED_EVENT,
    RunEventRepository,
)
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services import memory_extraction, run_scores, subagent_wake
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.run_artifacts import (
    _deliver_artifacts,
    _persist_artifact,
)
from agent_orchestrator.services.run_events import (
    _append_assistant_message,
    append_event,
    is_superseded,
)
from agent_orchestrator.services.run_execution_tool_events import _tool_event_payload
from agent_orchestrator.services.workspace import Workspace

from .support import _interrupt_payload


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

    协作式收尾(P0-2/P1-C):执行期间 run 被取消或被超时 sweeper 判 failed 时,
    结果一律丢弃——不改交付物、不写 completed/failed,只把本地对象同步成库里
    的终态后返回,保住取消/超时给出的 status_reason。
    """
    superseded = await is_superseded(session, run.id)
    if superseded is not None:
        run.status = superseded
        return run
    records = values.get("tool_calls") or []
    emitted = await RunEventRepository(session).count_of_type(run.id, TOOL_CALLED_EVENT)
    for record in records[emitted or 0 :]:
        # P1 审计字段进 payload;超长结果按 16KB 上限裁剪(§15.3)。
        await append_event(
            session, run, "run.tool.called.v1", _tool_event_payload(record)
        )
    if values.get("__interrupt__"):
        payload = _interrupt_payload(values)
        if payload.get("kind") == "question":
            # 交互式提问待用户回答(§10.3):停在 awaiting_input,等 POST /answer
            # 或 sweeper 超时自动 decline;答案随 resume 载荷回到 ask_gate。
            run.status = RunStatus.AWAITING_INPUT.value
            run.rounds = values.get("rounds", 0)
            await append_event(
                session,
                run,
                QUESTION_REQUIRED_EVENT,
                {
                    "question_id": payload.get("question_id"),
                    "questions": payload.get("questions") or [],
                    "allow_custom": bool(payload.get("allow_custom", True)),
                    "expires_at": payload.get("expires_at"),
                },
            )
            return run
        if payload.get("kind") == "subagent_wait":
            # 异步子代理(L2):父 run 挂起等子代理终态,不占 worker。
            # 子 run 落终态时 services.subagent_wake 入队 subagent job 唤醒。
            run.status = RunStatus.AWAITING_SUBAGENT.value
            run.rounds = values.get("rounds", 0)
            await append_event(
                session,
                run,
                SUBAGENT_WAITING_EVENT,
                {"children": payload.get("children") or []},
            )
            return run
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
        await run_scores.record_rule_score(session, run, completed=False)
        # 子 run 失败也要唤醒父 run(L2):回执带失败原因,父 run 决定降级/重排
        await subagent_wake.notify_parent(session, run)
        return run
    run.status = RunStatus.COMPLETED.value
    metrics.observe_run_outcome(status=RunStatus.COMPLETED.value)
    await append_event(
        session,
        run,
        "run.completed.v1",
        {"rounds": run.rounds, "results": values.get("results") or []},
    )
    await run_scores.record_rule_score(session, run, completed=True)
    # 对话里的回复 = 交付物正文(没有时用执行结果小结兜底)
    await _append_assistant_message(
        session,
        run,
        str(artifact.get("content") or "")
        or "\n".join(values.get("results") or [])
        or "任务完成 ✅",
    )
    # 提交后抽取(P1-3):run 完成写入进程内回调,由会话边界提交成功后执行;
    # outbox 开启时交给 outbox 消费者,这里只登记不等待
    memory_extraction.schedule_run_extraction(session, run)
    # 子 run 完成 → 唤醒挂起的父 run(L2);父 run 不在等待时静默 no-op
    await subagent_wake.notify_parent(session, run)
    return run
