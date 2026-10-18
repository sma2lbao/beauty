"""orchestrator 仓储:租户过滤与 404 语义在此声明一次(§6.3/§7)。

事件溯源相关的复杂查询(seq 取号、按类型计数、事件回放)以**显式命名方法**
留在仓储里——业务层不再出现裸 `select`,但也没把 SQL 藏进魔法抽象。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from database.repository import TenantRepository
from sqlalchemy import and_, func, or_, select

from agent_orchestrator.models.conversations import (
    Attachment,
    ChatMessage,
    Conversation,
)
from agent_orchestrator.models.runs import (
    Job,
    PendingInstruction,
    Run,
    RunArtifact,
    RunEvent,
)

QUEUED = "queued"
CONSUMED = "consumed"
TOOL_CALLED_EVENT = "run.tool.called.v1"


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
    ) -> list[Conversation]:
        """租户内最近活跃在前的对话列表(活跃 = updated_at,消息会顶上去)。

        - 默认排除已归档/已软删;
        - q 按标题模糊搜索;
        - before_id 是游标锚点(按 updated_at + id 做 keyset,避免 offset 漂移)。
        """
        criteria: list = [Conversation.deleted_at.is_(None)]
        criteria.append(
            Conversation.archived_at.is_not(None)
            if archived
            else Conversation.archived_at.is_(None)
        )
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

    async def require_active_for_tenant(
        self, tenant_id: str, conversation_id: str
    ) -> Conversation:
        """取未删除的对话;已软删与不存在一律 404(不泄漏存在性)。"""
        conversation = await self.get_for_tenant(tenant_id, conversation_id)
        if conversation is None or conversation.deleted_at is not None:
            raise self._not_found(conversation_id)
        return conversation


class JobRepository(TenantRepository[Job]):
    model = Job
    not_found_code = "job.not_found"
    not_found_message = "任务不存在"
    id_detail_key = "job_id"

    async def find_queued(
        self, tenant_id: str, kind: str, run_id: str
    ) -> Job | None:
        """同一 run 的同类 job 去重(避免用户连点产生重复执行)。"""
        return await self.session.scalar(
            self.scoped(
                tenant_id,
                Job.kind == kind,
                Job.run_id == run_id,
                Job.status.in_(("queued", "running")),
            ).order_by(Job.created_at)
        )

    async def claim_next(
        self, worker_id: str, now: datetime, lease_seconds: int
    ) -> Job | None:
        """本地轮询领取:queued 到期或 running 租约过期;PG 后续换 SKIP LOCKED。"""
        job = await self.session.scalar(
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
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.locked_by = worker_id
        job.lease_until = now + timedelta(seconds=lease_seconds)
        return job


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


class ArtifactRepository(TenantRepository[RunArtifact]):
    model = RunArtifact
    not_found_code = "artifact.not_found"
    not_found_message = "交付物不存在"
    id_detail_key = "artifact_id"

    async def require_for_run(self, tenant_id: str, run_id: str) -> RunArtifact:
        """按 run 取交付物(每个 run 至多一件);未交付 → 404。

        details 用 `run_id`:取件入口是 run,调用方关心的是"这个 run 有没有件"。
        """
        from common.errors import NotFoundError

        found = await self.list_scoped(tenant_id, RunArtifact.run_id == run_id)
        if not found:
            raise NotFoundError(
                "artifact.not_found",
                "交付物不存在",
                details={"run_id": run_id},
            )
        return found[0]
