"""orchestrator 仓储:租户过滤与 404 语义在此声明一次(§6.3/§7)。

事件溯源相关的复杂查询(seq 取号、按类型计数、事件回放)以**显式命名方法**
留在仓储里——业务层不再出现裸 `select`,但也没把 SQL 藏进魔法抽象。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from database.repository import TenantRepository
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from agent_orchestrator.models.approval import ApprovalPolicy
from agent_orchestrator.models.conversations import (
    Attachment,
    ChatMessage,
    Conversation,
)
from agent_orchestrator.models.mcp import MCPServer, MCPServerAuth
from agent_orchestrator.models.runs import (
    Job,
    PendingInstruction,
    Run,
    RunEvent,
)
from agent_orchestrator.repositories_artifacts import (
    _TEXT_CONTENT_TYPE_EXTRA as _TEXT_CONTENT_TYPE_EXTRA,
)
from agent_orchestrator.repositories_artifacts import (
    REPORT_ARTIFACT_SOURCE as REPORT_ARTIFACT_SOURCE,
)
from agent_orchestrator.repositories_artifacts import (
    ArtifactRepository as ArtifactRepository,
)
from agent_orchestrator.repositories_artifacts import (
    SkillPackageRepository as SkillPackageRepository,
)
from agent_orchestrator.repositories_artifacts import (
    SkillVersionRepository as SkillVersionRepository,
)
from agent_orchestrator.repositories_artifacts import (
    _is_text_artifact_row as _is_text_artifact_row,
)
from agent_orchestrator.schemas.runs import RunStatus

QUEUED = "queued"
CONSUMED = "consumed"
TOOL_CALLED_EVENT = "run.tool.called.v1"

# 报告件的来源标记:与 services.artifacts.ARTIFACT_REPORT_SOURCE 同值,
# 仓储不反向 import service 层(artifact service 已依赖本模块,反向会循环)。


class RunRepository(TenantRepository[Run]):
    model = Run
    not_found_code = "run.not_found"
    not_found_message = "run 不存在"
    id_detail_key = "run_id"

    async def list_recent(self, tenant_id: str, limit: int = 50) -> list[Run]:
        """租户内最新在前的 run 列表(id 兜底保证顺序确定)。"""
        return await self.list_scoped(
            tenant_id,
            limit=limit,
            order_by=[Run.created_at.desc(), Run.id.desc()],
        )

    async def list_for_conversation(
        self, tenant_id: str, conversation_id: str, limit: int = 50
    ) -> list[Run]:
        """会话内最新在前的 run 列表(post_message 据此找当前 run)。"""
        return await self.list_scoped(
            tenant_id,
            Run.conversation_id == conversation_id,
            limit=limit,
            order_by=[Run.created_at.desc(), Run.id.desc()],
        )

    async def count_active(self, tenant_id: str) -> int:
        """租户内未终结的 run 数(配额并发闸用)。"""
        terminal = (
            RunStatus.COMPLETED.value,
            RunStatus.FAILED.value,
            RunStatus.BLOCKED.value,
            RunStatus.CANCELLED.value,
        )
        return await self.count(Run.tenant_id == tenant_id, Run.status.not_in(terminal))

    async def list_changed_since(
        self, tenant_id: str, since: datetime, limit: int = 100
    ) -> list[Run]:
        """最近有变更的 run(通知流用):updated_at >= since,按变更时间倒序。

        只扫活跃窗口内的行,避免全表扫描;租户隔离由 list_scoped 保证。
        """
        return await self.list_scoped(
            tenant_id,
            Run.updated_at >= since,
            limit=limit,
            order_by=[Run.updated_at.desc(), Run.id.desc()],
        )

    async def list_stale_interrupts(
        self,
        now: datetime,
        *,
        approval_seconds: int,
        confirmation_seconds: int,
        limit: int = 100,
    ) -> list[Run]:
        """扫描停留超时的待审批/待确认 run(中断超时 sweeper 用)。

        这是**跨租户的系统维护查询**(后台任务没有请求租户上下文),因此不用
        list_scoped;逐 run 处理时仍回到各自的租户作用域。阈值 <= 0 表示关闭该类。
        """
        criteria = []
        if approval_seconds > 0:
            criteria.append(
                and_(
                    Run.status == RunStatus.AWAITING_APPROVAL.value,
                    Run.updated_at < now - timedelta(seconds=approval_seconds),
                )
            )
        if confirmation_seconds > 0:
            criteria.append(
                and_(
                    Run.status == RunStatus.AWAITING_CONFIRMATION.value,
                    Run.updated_at < now - timedelta(seconds=confirmation_seconds),
                )
            )
        if not criteria:
            return []
        statement = (
            select(Run)
            .where(or_(*criteria))
            .order_by(Run.updated_at, Run.id)
            .limit(limit)
        )
        return list(await self.session.scalars(statement))


class ConversationRepository(TenantRepository[Conversation]):
    model = Conversation
    not_found_code = "conversation.not_found"
    not_found_message = "对话不存在"
    id_detail_key = "conversation_id"

    async def list_recent(
        self,
        tenant_id: str,
        limit: int = 50,
        *,
        q: str | None = None,
        archived: bool = False,
        before_id: str | None = None,
        workspace_id: str | None = None,
    ) -> list[Conversation]:
        """租户内最近活跃在前的对话列表(活跃 = updated_at,消息会顶上去)。

        - 默认排除已归档/已软删;
        - q 按标题模糊搜索;
        - workspace_id 非空时只取该工作区的会话(归属为空的历史行不混进来);
        - before_id 是游标锚点(按 updated_at + id 做 keyset,避免 offset 漂移)。
        """
        criteria: list = [Conversation.deleted_at.is_(None)]
        criteria.append(
            Conversation.archived_at.is_not(None)
            if archived
            else Conversation.archived_at.is_(None)
        )
        if workspace_id is not None:
            criteria.append(Conversation.workspace_id == workspace_id)
        if q and q.strip():
            criteria.append(Conversation.title.ilike(f"%{q.strip()}%"))
        if before_id:
            anchor = await self.get_for_tenant(tenant_id, before_id)
            if anchor is None or anchor.deleted_at is not None:
                return []
            criteria.append(
                or_(
                    Conversation.updated_at < anchor.updated_at,
                    and_(
                        Conversation.updated_at == anchor.updated_at,
                        Conversation.id < anchor.id,
                    ),
                )
            )
        return await self.list_scoped(
            tenant_id,
            *criteria,
            limit=limit,
            order_by=[Conversation.updated_at.desc(), Conversation.id.desc()],
        )

    async def backfill_workspace_id(self, tenant_id: str, workspace_id: str) -> int:
        """把租户内 workspace_id 仍为空的会话一次性归入默认工作区。

        历史数据(本次改造前建的会话,以及工作区集成未启用期间建的会话)没有
        归属,列表接口首次访问时补一次;单条 UPDATE,不逐行处理。返回更新行数。
        """
        result = await self.session.execute(
            update(Conversation)
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.workspace_id.is_(None),
            )
            .values(workspace_id=workspace_id)
        )
        return int(result.rowcount or 0)

    async def require_active_for_tenant(
        self, tenant_id: str, conversation_id: str
    ) -> Conversation:
        """取未删除的对话;已软删与不存在一律 404(不泄漏存在性)。"""
        conversation = await self.get_for_tenant(tenant_id, conversation_id)
        if conversation is None or conversation.deleted_at is not None:
            raise self._not_found(conversation_id)
        return conversation


class MCPServerRepository(TenantRepository[MCPServer]):
    model = MCPServer
    not_found_code = "mcp.server_not_found"
    not_found_message = "MCP server 不存在"
    id_detail_key = "mcp_server_id"

    async def find_by_name(self, tenant_id: str, name: str) -> MCPServer | None:
        return await self.session.scalar(self.scoped(tenant_id, MCPServer.name == name))


class MCPServerAuthRepository(TenantRepository[MCPServerAuth]):
    """MCP server 的浏览器 OAuth 授权行(与 server 一对一)。"""

    model = MCPServerAuth
    not_found_code = "mcp.auth_not_found"
    not_found_message = "该 MCP server 没有授权记录"
    id_detail_key = "mcp_server_id"

    async def get_for_server(
        self, tenant_id: str, server_id: str
    ) -> MCPServerAuth | None:
        return await self.session.scalar(
            self.scoped(tenant_id, MCPServerAuth.server_id == server_id)
        )

    async def list_for_servers(
        self, tenant_id: str, server_ids: Sequence[str]
    ) -> dict[str, MCPServerAuth]:
        """列表页一次取回所有授权行,避免 N+1。"""
        if not server_ids:
            return {}
        rows = await self.session.scalars(
            self.scoped(tenant_id, MCPServerAuth.server_id.in_(list(server_ids)))
        )
        return {row.server_id: row for row in rows}


def _claim_statement(now: datetime):
    """job 领取条件:queued 已到期,或 running 但租约过期(worker 崩溃后重领)。"""
    return (
        select(Job)
        .where(
            or_(
                and_(Job.status == "queued", Job.run_at <= now),
                and_(
                    Job.status == "running",
                    Job.lease_until.is_not(None),
                    Job.lease_until < now,
                ),
            )
        )
        .order_by(Job.run_at, Job.created_at)
        .limit(1)
    )


def _is_postgres(session: Session) -> bool:
    """领取策略按方言分叉:PG 用行锁跳过,SQLite 没有 FOR UPDATE。"""
    bind = session.get_bind()
    return getattr(getattr(bind, "dialect", None), "name", "") == "postgresql"


class JobRepository(TenantRepository[Job]):
    model = Job
    not_found_code = "job.not_found"
    not_found_message = "任务不存在"
    id_detail_key = "job_id"

    async def find_queued(self, tenant_id: str, kind: str, run_id: str) -> Job | None:
        """同一 run 的同类 job 去重(避免用户连点产生重复执行)。"""
        return await self.session.scalar(
            self.scoped(
                tenant_id,
                Job.kind == kind,
                Job.run_id == run_id,
                Job.status.in_(("queued", "running")),
            ).order_by(Job.created_at)
        )

    async def cancel_queued_for_run(self, tenant_id: str, run_id: str) -> int:
        """把该 run 还没被领取的 job 置为 cancelled(取消 run 时用)。

        只动 queued:running 的 job 由执行边界自己收尾(协作式取消),
        把 running 改成 cancelled 会和 worker 的 succeeded 回写打架。
        返回取消条数,便于事件/指标记录。
        """
        rows = await self.session.scalars(
            self.scoped(
                tenant_id,
                Job.run_id == run_id,
                Job.status == QUEUED,
            )
        )
        cancelled = 0
        for job in rows:
            job.status = "cancelled"
            job.lease_until = None
            cancelled += 1
        return cancelled

    async def claim_next(
        self, worker_id: str, now: datetime, lease_seconds: int
    ) -> Job | None:
        """领取一个到期的 job,并立刻占住租约。

        SQLite(单进程 dev)靠先查后改即可;PostgreSQL 多实例部署时同一时刻会有
        多个 worker 抢同一行,因此套 `FOR UPDATE SKIP LOCKED`:被占住的行直接跳过,
        各 worker 领到不同 job,不互相等锁也不重复执行。
        """
        statement = _claim_statement(now)
        if _is_postgres(self.session):
            statement = statement.with_for_update(skip_locked=True)
        job = await self.session.scalar(statement)
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.locked_by = worker_id
        job.lease_until = now + timedelta(seconds=lease_seconds)
        return job


class ApprovalPolicyRepository(TenantRepository[ApprovalPolicy]):
    model = ApprovalPolicy
    not_found_code = "approval_policy.not_found"
    not_found_message = "审批策略不存在"
    id_detail_key = "policy_id"


class AttachmentRepository(TenantRepository[Attachment]):
    model = Attachment
    not_found_code = "attachment.not_found"
    not_found_message = "附件不存在"
    id_detail_key = "attachment_id"

    async def list_for_conversation(
        self, tenant_id: str, conversation_id: str
    ) -> list[Attachment]:
        return await self.list_scoped(
            tenant_id,
            Attachment.conversation_id == conversation_id,
            order_by=[Attachment.created_at, Attachment.id],
        )

    async def list_for_message(
        self, tenant_id: str, message_id: str
    ) -> list[Attachment]:
        return await self.list_scoped(
            tenant_id,
            Attachment.message_id == message_id,
            order_by=[Attachment.created_at, Attachment.id],
        )


class MessageRepository(TenantRepository[ChatMessage]):
    model = ChatMessage
    not_found_code = "conversation.not_found"
    not_found_message = "消息不存在"
    id_detail_key = "message_id"

    async def list_for_conversation(
        self, tenant_id: str, conversation_id: str
    ) -> list[ChatMessage]:
        """对话消息回放:按时间升序(id 兜底保证顺序确定)。"""
        return await self.list_scoped(
            tenant_id,
            ChatMessage.conversation_id == conversation_id,
            order_by=[ChatMessage.created_at, ChatMessage.id],
        )

    async def has_any_message(self, tenant_id: str, conversation_id: str) -> bool:
        """对话里是否已有消息(判定首条消息 → 自动定题)。"""
        return await self.exists_for_tenant(
            tenant_id, ChatMessage.conversation_id == conversation_id
        )

    async def list_history(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        before: datetime,
        exclude_run_id: str,
    ) -> list[ChatMessage]:
        """多轮上下文:run 建立之前的对话消息,排除本 run 自己的消息。

        触发本 run 的用户消息(先落库、再建 run)会被两个条件同时排除:
        created_at 早于 run 是主判据,run_id 相同是兜底——并发到达的
        追加指令(建 run 之后落库)两条都能拦住,不会重复进上下文。
        """
        return await self.list_scoped(
            tenant_id,
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.created_at < before,
            (ChatMessage.run_id != exclude_run_id) | (ChatMessage.run_id.is_(None)),
            order_by=[ChatMessage.created_at, ChatMessage.id],
        )

    async def list_before(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        before: datetime,
        exclude_id: str,
    ) -> list[ChatMessage]:
        """直接对话的多轮上下文:这条消息之前的全部轮次(不分 run)。

        与 list_history 的区别:那里按 run 排除(触发消息与追加指令不进上下文),
        这里只排除消息自身——闲聊要看得到此前所有往来。
        """
        return await self.list_scoped(
            tenant_id,
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.created_at < before,
            ChatMessage.id != exclude_id,
            order_by=[ChatMessage.created_at, ChatMessage.id],
        )


class RunEventRepository(TenantRepository[RunEvent]):
    """run_events 是 append-only 审计流水(§6.3):只追加、按 seq 读。"""

    model = RunEvent
    not_found_code = "run.not_found"
    not_found_message = "run 不存在"
    id_detail_key = "event_id"

    async def next_seq(self, run_id: str) -> int:
        """该 run 的下一个 seq(每 run 单调递增)。"""
        current = await self.session.scalar(
            select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id)
        )
        return int(current or 0) + 1

    async def count_of_type(self, run_id: str, type_: str) -> int:
        """某类事件已落库条数(用于只发增量、避免恢复执行后重复记事件)。"""
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.type == type_)
            )
            or 0
        )

    async def list_for_run(
        self, tenant_id: str, run_id: str, *, after: int = 0
    ) -> list[RunEvent]:
        """事件回放:按 seq 升序;after 为 SSE 续传游标。"""
        return await self.list_scoped(
            tenant_id,
            RunEvent.run_id == run_id,
            RunEvent.seq > after,
            order_by=[RunEvent.seq],
        )


class InstructionRepository(TenantRepository[PendingInstruction]):
    model = PendingInstruction
    not_found_code = "run.not_found"
    not_found_message = "指令不存在"
    id_detail_key = "instruction_id"

    async def list_for_run(
        self, tenant_id: str, run_id: str
    ) -> list[PendingInstruction]:
        """该 run 的指令队列(queued/consumed 都返回,控制台据此展示)。"""
        return await self.list_scoped(
            tenant_id,
            PendingInstruction.run_id == run_id,
            order_by=[PendingInstruction.created_at],
        )

    async def list_queued(
        self, tenant_id: str, run_id: str
    ) -> list[PendingInstruction]:
        """待消费指令(按入队顺序)。"""
        return await self.list_scoped(
            tenant_id,
            PendingInstruction.run_id == run_id,
            PendingInstruction.status == QUEUED,
            order_by=[PendingInstruction.created_at],
        )
