"""run 编排业务逻辑:不 import FastAPI,可独立单测(分层约定见架构 §4)。

graph 生命周期(架构 §6.2):

- plan:新 attempt 起图(thread = ``<run_id>#<attempt>``)。审批闸按工具风险
  判定:无 DANGER 工具自动放行,一次跑到终态;有危险工具才中断 → awaiting_approval;
- approve:放行闸门,execute → verify(返工上限见 builder)→ deliver → completed;
- reject:拒绝计划,图终结 → blocked;resume:新 attempt 重新进闸,保留已编辑计划;
- 状态守卫:非法迁移 ConflictError(409),资源不存在 NotFoundError(404),
  对外渲染走 common 统一错误模型。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from common import metrics
from common.artifacts import ArtifactStore
from common.errors import ConflictError, QuotaExceededError
from events.envelope import EventEnvelope
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.graph import delta
from agent_orchestrator.graph.ports import ApprovalDecision, GraphPorts
from agent_orchestrator.graph.runtime import GRAPH_RUNTIME_KEY, GraphRuntime
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
    ConversationRepository,
    InstructionRepository,
    MessageRepository,
    RunEventRepository,
    RunRepository,
)
from agent_orchestrator.schemas.runs import RunCreate, RunStatus
from agent_orchestrator.services import approval as approval_service
from agent_orchestrator.services import artifacts, history
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services import quota as quota_service
from agent_orchestrator.services import usage as usage_service
from agent_orchestrator.services.file_capture import FileDownloader, extract_file_urls
from agent_orchestrator.services.skill_packages import SkillDefinition
from agent_orchestrator.services.tool_context import (
    ToolContext,
    bind_tool_context,
)
from agent_orchestrator.services.tools import ToolRegistry, ToolRisk
from agent_orchestrator.services.workspace import Workspace


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


def _tool_context(run: Run):
    """工具 handler 的来源上下文:stdio MCP 据此按对话/run 复用沙箱(P2)。"""
    return bind_tool_context(
        ToolContext(
            tenant_id=run.tenant_id,
            run_id=run.id,
            conversation_id=run.conversation_id,
            # run 创建时快照的 MCP 引用:工具装配(execution/extras)只暴露这些 server
            mcp_server_ids=tuple(run.mcp_server_ids or ()),
        )
    )


async def append_event(
    session: AsyncSession,
    run: Run,
    type_: str,
    payload: dict[str, Any],
    *,
    record_outbox: bool = True,
) -> RunEvent:
    """追加 run 事件:seq 每 run 单调递增;信封负责命名/版本校验(§6.3)。

    ``record_outbox=False`` 用于纯展示的瞬时事件(正文增量):它们只走
    DB 回放,不必再进 outbox 投递给外部消费方。
    """
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
    recorder = session.info.get("outbox") if record_outbox else None
    if recorder is not None:
        await recorder(session, run, env)
    return event


class _RunDeltaSink:
    """把节点上报的正文增量攒成 ``token.delta.v1`` 事件(§6.3)。

    与图调用共用同一个 session 并逐批提交:每批因此有各自可见的 seq 时刻,
    SSE 回放能按序拼回整段正文,前端也能边收边渲染。攒批口径是"字数或时长
    先到者"(默认 240 字 / 0.25 秒)——逐字写库会把一次 run 变成成百上千行
    事件,轮询回放也扛不住。
    """

    def __init__(self, session: AsyncSession, run: Run) -> None:
        self._session = session
        self._run = run
        self._flush_chars = delta.flush_chars()
        self._flush_seconds = delta.flush_seconds()
        self._buffer: list[str] = []
        self._phase = delta.PHASE_EXECUTE
        self._chars = 0
        self._last_flush = time.monotonic()

    async def push(self, text: str, phase: str) -> None:
        if phase != self._phase and self._buffer:
            # 阶段切换先吐干净上一段:前端按 phase 分组,不能混在一段里
            await self._flush()
        self._phase = phase
        self._buffer.append(text)
        self._chars += len(text)
        if (
            self._chars >= self._flush_chars
            or time.monotonic() - self._last_flush >= self._flush_seconds
        ):
            await self._flush()

    async def close(self) -> None:
        await self._flush()

    async def _flush(self) -> None:
        if not self._buffer:
            return
        text = "".join(self._buffer)
        self._buffer.clear()
        self._chars = 0
        self._last_flush = time.monotonic()
        await append_event(
            self._session,
            self._run,
            delta.EVENT_TYPE,
            {"text": text, "phase": self._phase},
            record_outbox=False,
        )
        await self._session.commit()


@asynccontextmanager
async def _delta_stream(
    session: AsyncSession, run: Run
) -> AsyncIterator[delta.DeltaProjector]:
    """图调用边界:构造本次 run 的正文增量投影器(未开启时为空投影器)。

    投影器经 graph invocation config 显式注入节点(不再用 ContextVar 绑定),
    图内节点只上报、落库与提交仍在这里完成。
    """
    if not delta.enabled():
        yield delta.DeltaProjector()
        return
    sink = _RunDeltaSink(session, run)
    # 开跑前的写入(计划/放行/指令消费)先落库:增量事件才有独立的可见时刻,
    # 也避免同一 session 里未提交的旧事务挡住 flush 提交。
    await session.commit()
    projector = delta.DeltaProjector(sink)
    try:
        yield projector
    finally:
        await sink.close()


@asynccontextmanager
async def _usage_boundary(run: Run) -> AsyncIterator[None]:
    """图调用边界:绑定用量采集器,退出时上报 account(P0-1)。

    上报失败只打 warning + 计数(在 usage.report 内部兜住),绝不影响 run——
    用量是观测数据,不是执行路径。
    """
    collector = usage_service.UsageCollector()
    token = usage_service.bind(collector)
    try:
        yield
    finally:
        usage_service.unbind(token)
        await usage_service.report(
            tenant_id=run.tenant_id,
            run_id=run.id,
            samples=collector.samples,
        )


def _config(run: Run, projector: delta.DeltaProjector | None = None) -> dict[str, Any]:
    """graph 线程标识 + 本次调用的运行期依赖(端口/增量投影器显式注入)。

    同一 run 的每次 blocked→resume 周期一个新线程;运行期依赖随调用传递,
    节点不再读进程级上下文变量。
    """
    return {
        "configurable": {
            "thread_id": f"{run.id}#{run.attempts}",
            GRAPH_RUNTIME_KEY: GraphRuntime(
                ports=_GRAPH_PORTS,
                delta=projector or delta.DeltaProjector(),
            ),
        }
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
    metrics.observe_run_outcome(status=RunStatus.FAILED.value)
    await append_event(
        session,
        run,
        "run.failed.v1",
        {"stage": stage, "reason": run.status_reason},
    )
    await _append_assistant_message(session, run, f"执行失败:{run.status_reason}")


def _file_refs(records: list[dict[str, Any]], *, limit: int) -> list[Any]:
    """从工具调用记录里按扩展名白名单提取待抓取的文件链接(去重保序)。

    只看成功调用("ok" 为真)的 detail:失败调用的输出里即便有链接也不可信;
    limit 是全局上限(跨记录累计),避免一次执行把大量链接全抓下来。
    """
    if limit <= 0:
        return []
    refs: list[Any] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or not record.get("ok", False):
            continue
        detail = record.get("detail")
        if not isinstance(detail, str) or not detail:
            continue
        for ref in extract_file_urls(detail, limit=limit):
            if ref.url in seen:
                continue
            seen.add(ref.url)
            refs.append(ref)
            if len(refs) >= limit:
                return refs
    return refs


async def _capture_tool_files(
    session: AsyncSession,
    run: Run,
    records: list[dict[str, Any]],
    store: ArtifactStore | None,
    downloader: FileDownloader | None,
    *,
    max_files: int,
) -> tuple[list[RunArtifact], list[dict[str, str]]]:
    """工具/MCP 返回的外链 → 真实交付物(执行时抓取,§7)。

    链接带签名会过期,所以必须在执行阶段落库,不能只把 URL 留在报告正文里。
    单个链接失败不拖垮交付:错误如实进事件 payload 的 download_errors。
    按 source_url 去重(同一 run 的重放/追加执行不会重复落件)。
    """
    if downloader is None or max_files <= 0:
        return [], []
    repo = ArtifactRepository(session)
    rows: list[RunArtifact] = []
    errors: list[dict[str, str]] = []
    for ref in _file_refs(records, limit=max_files):
        if await repo.find_by_source_url(run.tenant_id, run.id, ref.url) is not None:
            continue
        try:
            downloaded = await downloader.fetch(ref.url)
        except Exception as exc:  # 下载失败:留痕,继续交付其余件
            errors.append({"url": ref.url, "error": f"{type(exc).__name__}: {exc}"})
            continue
        rows.append(
            await artifacts.persist_file_artifact(
                session,
                run,
                filename=downloaded.filename,
                content_type=downloaded.content_type,
                data=downloaded.data,
                store=store,
                source=artifacts.ARTIFACT_MCP_SOURCE,
                source_url=ref.url,
            )
        )
    return rows, errors


def _artifact_meta(row: RunArtifact) -> dict[str, Any]:
    """交付物元数据(事件 payload 与前端列表共用口径,不含正文)。"""
    return {
        "id": row.id,
        "title": row.title,
        "kind": row.kind,
        "filename": row.filename,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "source": row.source or artifacts.ARTIFACT_REPORT_SOURCE,
    }


async def _mirror_artifacts(
    session: AsyncSession,
    run: Run,
    rows: list[RunArtifact],
    store: ArtifactStore | None,
    workspace: Workspace | None,
) -> tuple[list[str], list[dict[str, str]]]:
    """把本次交付物镜像到工作区(§7 归属方向);返回 (工作区 id 列表, 错误)。

    镜像失败不丢交付物,只把原因记进事件;已镜像过的行(列上留了
    workspace_artifact_id)直接复用,重放不会在工作区里堆重复件。
    """
    if workspace is None:
        return [], []
    mirrored_ids: list[str] = []
    errors: list[dict[str, str]] = []
    for row in rows:
        mirrored_id = getattr(row, "workspace_artifact_id", None)
        if mirrored_id:
            mirrored_ids.append(mirrored_id)
            continue
        try:
            data = await artifacts.read_artifact_bytes(row, store)
            content_type = row.content_type or artifacts.CONTENT_TYPE
            if artifacts.is_text_artifact(row):
                mirrored = await workspace.create_artifact(
                    run.tenant_id,
                    title=row.title,
                    kind=row.kind,
                    content=data.decode("utf-8", "replace"),
                    filename=row.filename,
                    content_type=content_type,
                    run_id=run.id,
                    workspace_id=run.workspace_id,
                )
            else:
                mirrored = await workspace.create_artifact(
                    run.tenant_id,
                    title=row.title,
                    kind=row.kind,
                    data=data,
                    filename=row.filename or artifacts.artifact_download_name(row),
                    content_type=content_type,
                    run_id=run.id,
                    workspace_id=run.workspace_id,
                )
        except Exception as exc:  # 镜像失败:run 照常交付,但如实记账
            errors.append(
                {"artifact_id": row.id, "error": f"{type(exc).__name__}: {exc}"}
            )
            continue
        new_id = mirrored.get("id")
        if new_id:
            mirrored_ids.append(new_id)
            if "workspace_artifact_id" in RunArtifact.__table__.columns:
                row.workspace_artifact_id = new_id
    if mirrored_ids:
        await session.flush()
    return mirrored_ids, errors


async def _deliver_artifacts(
    session: AsyncSession,
    run: Run,
    artifact: dict[str, Any],
    *,
    records: list[dict[str, Any]],
    store: ArtifactStore | None,
    workspace: Workspace | None,
    downloader: FileDownloader | None,
    max_files: int,
) -> RunArtifact:
    """交付阶段:落报告 → 抓取工具产出文件 → 镜像工作区 → 一条 run.delivered.v1。

    多件交付物(run_artifacts 一行一件,source 区分 report/skill/mcp);
    报告件不再覆盖既有行——技能产物与报告各自成件。
    """
    report = await artifacts.persist_artifact(session, run, artifact, store)
    files, download_errors = await _capture_tool_files(
        session, run, records, store, downloader, max_files=max_files
    )
    rows = [report, *files]
    mirrored_ids, mirror_errors = await _mirror_artifacts(
        session, run, rows, store, workspace
    )
    payload: dict[str, Any] = {
        "artifact_id": report.id,
        "kind": report.kind,
        "title": report.title,
        "chars": len(str(artifact.get("content") or "")),  # 字符数(文本)
        "size_bytes": report.size_bytes,  # 存储字节数(UTF-8)
        "storage": report.storage,
        "artifact_ids": [row.id for row in rows],
        "artifacts": [_artifact_meta(row) for row in rows],
        "download_errors": download_errors,
    }
    if mirrored_ids:
        payload["workspace_artifact_id"] = mirrored_ids[0]
        payload["workspace_artifact_ids"] = mirrored_ids
    if mirror_errors:
        payload["workspace_errors"] = mirror_errors
    await append_event(session, run, "run.delivered.v1", payload)
    return report


async def _persist_artifact(
    session: AsyncSession,
    run: Run,
    artifact: dict[str, Any],
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
) -> RunArtifact:
    """兼容薄包装:只落报告件(不抓取外链文件),语义同旧版交付。"""
    return await _deliver_artifacts(
        session,
        run,
        artifact,
        records=[],
        store=store,
        workspace=workspace,
        downloader=None,
        max_files=0,
    )


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


async def _enforce_quota(session: AsyncSession, tenant_id: str) -> None:
    """P0-2 准入闸:月度 token 用尽 / 并发 run 超限 → 429。

    快照取不到(=account 未配置或不可用)时不拦:配额是保护性限流,
    不能被观测依赖的抖动连带打死。判定语义:
    - 月度:``used_tokens >= monthly_tokens`` 即拒绝(边界值按用尽算);
    - 并发:未终结 run 数(排除 completed/failed/blocked)``>= max_concurrent_runs``。
    """
    snapshot = await quota_service.fetch(tenant_id)
    if snapshot is None:
        return
    if snapshot.monthly_tokens_exceeded:
        raise QuotaExceededError(
            "quota.monthly_tokens_exceeded",
            "本月模型用量已超出配额,请升级或等待下月重置",
            details={
                "limit": snapshot.monthly_tokens,
                "used": snapshot.used_tokens,
            },
        )
    limit = snapshot.max_concurrent_runs
    if limit is not None:
        active = await RunRepository(session).count_active(tenant_id)
        if active >= limit:
            raise QuotaExceededError(
                "quota.concurrent_runs_exceeded",
                "同时进行的任务数已达上限,请等待现有任务结束",
                details={"limit": limit, "active": active},
            )


async def create_run(
    session: AsyncSession,
    tenant_id: str,
    data: RunCreate,
    *,
    skill: SkillDefinition | None = None,
) -> Run:
    """建 run;skill 定义(内置或上传包)在此**冻结**为快照列。

    上传包额外冻结 version_id/object_key/sha256 与执行字段——之后用户上传
    新版本不影响本 run(P3-C 的 skill_run 按 run 上的快照取包执行)。
    工作区归属:显式给的就用,否则从所属对话继承(对话在哪个工作区,run 就在哪)。
    配额(P0-2):建 run 前先过准入闸,超限直接 429,不留下半残 run。
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
    return run


async def list_runs(
    session: AsyncSession, tenant_id: str, limit: int = 50
) -> list[Run]:
    """租户内 run 列表:最新在前(控制台首页用);id 兜底保证顺序确定。"""
    return await RunRepository(session).list_recent(tenant_id, limit)


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
    _require_status(run, RunStatus.PENDING)
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
    *,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
) -> Run:
    """图调用结果的统一后处理(§6.2/§6.3/§7)。

    顺序即语义:工具审计 → 危险操作确认中断 → 交付物 → 终态。

    注意:state.tool_calls 是 add reducer,恢复执行(如确认闸放行)后会带上
    历史累积记录——按已落库条数只发增量,避免事件重复。
    """
    records = values.get("tool_calls") or []
    emitted = await RunEventRepository(session).count_of_type(run.id, TOOL_CALLED_EVENT)
    for record in records[emitted or 0 :]:
        await append_event(session, run, "run.tool.called.v1", record)
    if values.get("__interrupt__"):
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
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
) -> Run:
    run = await require_run(session, tenant_id, run_id)
    _require_status(run, RunStatus.AWAITING_APPROVAL)
    if emit_approved:
        # 放行动作先入事件流,再恢复图:执行失败时顺序如实(approved → failed)
        await append_event(session, run, "run.approved.v1", {"attempt": run.attempts})
    if jobs_enabled:
        await jobs_service.enqueue(session, tenant_id, "approve", run.id)
        return run
    # §6.5:放行边界消费排队指令,经 resume 载荷由审批闸并入状态
    instructions = await _consume_instructions(session, run)
    try:
        with _tool_context(run):
            async with _delta_stream(session, run) as projector, _usage_boundary(run):
                values = await graph.ainvoke(
                    Command(resume={"action": "approve", "instructions": instructions}),
                    config=_config(run, projector),
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
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
) -> Run:
    """危险操作确认(§6.2):awaiting_confirmation → 放行执行 / 拒绝跳过,图继续。"""
    run = await require_run(session, tenant_id, run_id)
    _require_status(run, RunStatus.AWAITING_CONFIRMATION)
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
        with _tool_context(run):
            async with _delta_stream(session, run) as projector, _usage_boundary(run):
                values = await graph.ainvoke(
                    Command(
                        resume={
                            "action": action,
                            "reason": reason,
                            "instructions": instructions,
                        }
                    ),
                    config=_config(run, projector),
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
    _require_status(run, RunStatus.AWAITING_APPROVAL)
    with _tool_context(run):
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
    _require_status(run, RunStatus.BLOCKED)
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


async def list_events(
    session: AsyncSession, tenant_id: str, run_id: str, after: int = 0
) -> list[RunEvent]:
    """事件回放:按 seq 升序(§6.3,SSE 断线重放与审计共用);after 为续传游标。"""
    return await RunEventRepository(session).list_for_run(
        tenant_id, run_id, after=after
    )
