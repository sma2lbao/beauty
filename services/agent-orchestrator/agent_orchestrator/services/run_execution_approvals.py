"""run 审批/确认/拒绝与图结果落库(自 run_execution.py 拆出)。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from common.artifacts import ArtifactStore
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator import metrics
from agent_orchestrator.graph import delta
from agent_orchestrator.graph.items import ItemProjector
from agent_orchestrator.graph.ports import ApprovalDecision, GraphPorts
from agent_orchestrator.graph.runtime import GRAPH_RUNTIME_KEY, GraphRuntime
from agent_orchestrator.graph.steps import StepProjector
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import (
    QUESTION_REQUIRED_EVENT,
    TOOL_CALLED_EVENT,
    RunEventRepository,
)
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services import approval as approval_service
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services import memory_extraction
from agent_orchestrator.services import usage as usage_service
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.llm_binding import bind_run_llm
from agent_orchestrator.services.run_artifacts import (
    _deliver_artifacts,
    _persist_artifact,
)
from agent_orchestrator.services.run_events import (
    _append_assistant_message,
    _delta_stream,
    _fail_run,
    _item_stream,
    _step_stream,
    append_event,
    is_cancelled,
)
from agent_orchestrator.services.run_lifecycle import (
    _consume_instructions,
    require_run,
    require_transition,
)
from agent_orchestrator.services.run_quota import _usage_boundary
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from agent_orchestrator.services.tools import ToolRegistry, ToolRisk
from agent_orchestrator.services.workspace import Workspace

#: from_job 路径下"无需再执行"的状态:终态或已取消(取消后的 job 直接作废)
_TERMINAL_OR_CANCELLED = frozenset(
    {
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.BLOCKED.value,
        RunStatus.CANCELLED.value,
    }
)


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

#: 单条 run.tool.called.v1 的 payload 序列化上限(设计 §15.3:≤16KB)。
MAX_TOOL_EVENT_BYTES = 16 * 1024
#: payload 里 arguments 的展示上限(超长参数只留前段,避免事件体积爆炸)。
MAX_EVENT_ARGUMENTS_CHARS = 2000
#: payload 里 paths 的条数/单条长度上限(§11.1:事件只留前 20 条)。
MAX_EVENT_PATHS = 20
MAX_EVENT_PATH_CHARS = 512
#: sha256 只留原值长度(64 位十六进制),更长的一律截断。
MAX_EVENT_SHA_CHARS = 64


def _event_text(value: Any, limit: int) -> str:
    """任意值 -> 限长字符串(None/空值统一成空串)。"""

    return str(value or "")[:limit]


def _event_int(value: Any) -> int:
    """只接受真整数(bool 不算),其余归零(审计字段容错)。"""

    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _event_paths(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        _event_text(item, MAX_EVENT_PATH_CHARS)
        for item in value[:MAX_EVENT_PATHS]
        if item
    ]


def _event_sha(value: Any) -> str | None:
    text = _event_text(value, MAX_EVENT_SHA_CHARS)
    return text or None


def _payload_bytes(payload: dict[str, Any]) -> int:
    """payload 实际序列化后的字节数(ensure_ascii=False,与落库口径一致)。"""

    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _fit_tool_event(payload: dict[str, Any]) -> dict[str, Any]:
    """保证单条 payload ≤16KB:先截 arguments,再截 paths,最后收 detail。"""

    if _payload_bytes(payload) <= MAX_TOOL_EVENT_BYTES:
        return payload
    for limit in (1000, 500, 200, 0):
        payload["arguments"] = payload["arguments"][:limit]
        if _payload_bytes(payload) <= MAX_TOOL_EVENT_BYTES:
            return payload
    while payload["paths"] and _payload_bytes(payload) > MAX_TOOL_EVENT_BYTES:
        payload["paths"] = payload["paths"][:-1]
    while payload["detail"] and _payload_bytes(payload) > MAX_TOOL_EVENT_BYTES:
        payload["detail"] = payload["detail"][: len(payload["detail"]) // 2]
    return payload


def _tool_event_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    """把 tool_calls 记录整理成 run.tool.called.v1 的 payload。

    P0 字段 name/arguments/ok/detail 原样保留(detail 仍截 200 字符),
    P1 扩充审计字段(§11.1/§12.3):tool_call_id、risk、decision、
    error_code、duration_ms、result_chars、truncated、paths、
    sha256_before/after。P1-F 再加 offloaded/offload_path/original_chars:
    工具结果被 offload 成文件时,事件能回答"原文落在哪个文件、多大"。
    事件 type 与 seq 语义不变,新字段全部可选。
    """

    arguments = _event_text(record.get("arguments"), MAX_EVENT_ARGUMENTS_CHARS)
    payload: dict[str, Any] = {
        "name": _event_text(record.get("name"), 200),
        "arguments": arguments,
        "ok": bool(record.get("ok")),
        "detail": _event_text(record.get("detail"), 200),
        "tool_call_id": _event_text(record.get("tool_call_id"), 128),
        "risk": _event_text(record.get("risk"), 16) or "danger",
        "decision": _event_text(record.get("decision"), 16) or "auto",
        "error_code": _event_text(record.get("error_code"), 64) or None,
        "duration_ms": _event_int(record.get("duration_ms")),
        "result_chars": _event_int(record.get("result_chars")),
        "truncated": bool(record.get("truncated")),
        "paths": _event_paths(record.get("paths")),
        "sha256_before": _event_sha(record.get("sha256_before")),
        "sha256_after": _event_sha(record.get("sha256_after")),
        # P1-F:大结果 offload 后事件里留引用(模型只看到信封,审计要看全文去哪了)
        "offloaded": bool(record.get("offloaded")),
        "offload_path": _event_text(record.get("offload_path"), MAX_EVENT_PATH_CHARS)
        or None,
        "original_chars": _event_int(record.get("original_chars")),
    }
    return _fit_tool_event(payload)


def _tool_context(run: Run):
    """工具 handler 的来源上下文:stdio MCP 据此按对话/run 复用沙箱(P2)。"""
    return bind_tool_context(
        ToolContext(
            tenant_id=run.tenant_id,
            run_id=run.id,
            conversation_id=run.conversation_id,
            # 记忆工具按工作区归属读写(与 run 的工作区一致,§6.7)
            workspace_id=run.workspace_id,
            # run 创建时快照的 MCP 引用:工具装配(execution/extras)只暴露这些 server
            mcp_server_ids=tuple(run.mcp_server_ids or ()),
        )
    )


def _config(
    run: Run,
    projector: delta.DeltaProjector | None = None,
    step_projector: StepProjector | None = None,
    item_projector: ItemProjector | None = None,
) -> dict[str, Any]:
    """graph 线程标识 + 本次调用的运行期依赖(端口/增量/步骤/过程项投影器显式注入)。

    同一 run 的每次 blocked→resume 周期一个新线程;运行期依赖随调用传递,
    节点不再读进程级上下文变量。
    """
    return {
        "configurable": {
            "thread_id": f"{run.id}#{run.attempts}",
            GRAPH_RUNTIME_KEY: GraphRuntime(
                ports=_GRAPH_PORTS,
                delta=projector or delta.DeltaProjector(),
                steps=step_projector or StepProjector(),
                items=item_projector or ItemProjector(),
            ),
        }
    }


def _interrupt_payload(values: Mapping[str, Any]) -> dict[str, Any]:
    """取本次图调用的中断载荷(设计 §10.3/§6.2)。

    LangGraph 在状态里放的是 Interrupt 对象列表,载荷在 .value;
    checkpoint 恢复/测试桩也可能直接给 dict。两种都归一成 dict,
    非 Mapping 一律 {} 交给"未知中断"的旧语义分支兜底。
    """
    interrupts = values.get("__interrupt__") or ()
    if not interrupts:
        return {}
    first = interrupts[0]
    value = getattr(first, "value", first)
    if isinstance(value, Mapping):
        return dict(value)
    return {}


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

    协作式取消(P0-2):执行期间收到取消请求时,结果一律丢弃——不改交付物、
    不写 completed/failed,只把本地对象同步成 cancelled 后返回。
    """
    if await is_cancelled(session, run.id):
        run.status = RunStatus.CANCELLED.value
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
    # 提交后抽取(P1-3):run 完成写入进程内回调,由会话边界提交成功后执行;
    # outbox 开启时交给 outbox 消费者,这里只登记不等待
    memory_extraction.schedule_run_extraction(session, run)
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
    from_job: bool = False,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
) -> Run:
    run = await require_run(session, tenant_id, run_id)
    if from_job:
        # worker 回来执行:API 侧已把状态推到 running,无法再过状态守卫;
        # 期间被取消或已终态(例如取消后 job 才被领到)则直接收手。
        if run.status in _TERMINAL_OR_CANCELLED:
            return run
    else:
        require_transition(run, "approve")
        # P0-2:审批通过即进入执行,同步与 jobs 两条路径都在这里推进状态。
        run.status = RunStatus.RUNNING.value
    if emit_approved:
        # 放行动作先入事件流,再恢复图:执行失败时顺序如实(approved → failed)
        await append_event(session, run, "run.approved.v1", {"attempt": run.attempts})
    if jobs_enabled:
        await jobs_service.enqueue(session, tenant_id, "approve", run.id)
        return run
    # §6.5:放行边界消费排队指令,经 resume 载荷由审批闸并入状态
    instructions = await _consume_instructions(session, run)
    try:
        with bind_run_llm(session, run), _tool_context(run):
            async with (
                _delta_stream(session, run) as projector,
                _step_stream(session, run) as step_projector,
                _item_stream(session, run, step_projector) as item_projector,
                _usage_boundary(session, run),
            ):
                values = await graph.ainvoke(
                    Command(resume={"action": "approve", "instructions": instructions}),
                    config=_config(run, projector, step_projector, item_projector),
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
    from_job: bool = False,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
) -> Run:
    """危险操作确认(§6.2):awaiting_confirmation → 放行执行 / 拒绝跳过,图继续。"""
    run = await require_run(session, tenant_id, run_id)
    if from_job:
        # 同 approve_run:worker 路径状态已由 API 侧推进,守卫在此不重复校验。
        if run.status in _TERMINAL_OR_CANCELLED:
            return run
    else:
        require_transition(run, "confirm")
        # P0-2:确认后图继续执行,状态回到 running。
        run.status = RunStatus.RUNNING.value
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
        with bind_run_llm(session, run), _tool_context(run):
            async with (
                _delta_stream(session, run) as projector,
                _step_stream(session, run) as step_projector,
                _item_stream(session, run, step_projector) as item_projector,
                _usage_boundary(session, run),
            ):
                values = await graph.ainvoke(
                    Command(
                        resume={
                            "action": action,
                            "reason": reason,
                            "instructions": instructions,
                        }
                    ),
                    config=_config(run, projector, step_projector, item_projector),
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
    with bind_run_llm(session, run), _tool_context(run):
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
