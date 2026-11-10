"""对话内写操作确认链路(§6.7 就地确认):pending 落库、确认执行与取消。

背景:对话路径没有任务图的审批闸,原先 chat 一律拒绝写工具(提示改用「任务」)。
产品改为「就地确认」:

1. 模型在 chat 里发起 risk=write 的调用 → 本模块落一行 pending 确认(不执行工具),
   工具帧带上 confirmation_id / expires_at,同时把"请用户确认"回喂给模型;
2. 前端卡片让用户点确认 → 确认端点以 approved=True 重放同一个调用
   (arguments / tool_call_id 原样),结果落一条 assistant 消息并回填结果;
3. 取消或过期则什么都不执行;重复确认返回 409,不重复执行。

过期口径:TTL 10 分钟,按 expires_at 时间判定(读取时若已过期直接 409,
不依赖状态位被改写——409 会回滚请求事务,状态位留给巡检清理)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from common.errors import ConflictError, NotFoundError
from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.graph.tool_results import is_tool_error, parse_tool_error
from agent_orchestrator.models.conversations import ChatMessage, ChatToolConfirmation
from agent_orchestrator.repositories import ChatToolConfirmationRepository
from agent_orchestrator.services import approval as approval_service
from agent_orchestrator.services.conversations_persist import _persist_assistant_content

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from agent_orchestrator.services.tools import ToolSpec

    ExtrasFactory = Callable[[str], Awaitable[list[ToolSpec]]]

#: 确认有效期:10 分钟(前端按 expires_at 倒计时,过期卡片置灰)
CONFIRMATION_TTL = timedelta(minutes=10)

#: 契约错误码(前端已接入,不得改名)
ERROR_CONFIRMATION_REQUIRED = "tool.confirmation_required"
ERROR_NOT_FOUND = "tool_confirmation.not_found"
ERROR_EXPIRED = "tool_confirmation.expired"
ERROR_NOT_PENDING = "tool_confirmation.not_pending"
#: 确认时工具已不可用(工具被删/连接器停用):契约外的兜底码,409
ERROR_TOOL_UNAVAILABLE = "tool_confirmation.tool_unavailable"

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"


@dataclass(frozen=True)
class PendingConfirmation:
    """pending 确认的对外投影:工具帧只带 id 与过期时间。"""

    id: str
    expires_at: datetime

    def frame_fields(self) -> dict[str, str]:
        return {
            "confirmation_id": self.id,
            "expires_at": isoformat_utc(self.expires_at),
        }


@dataclass(frozen=True)
class ConfirmationOutcome:
    """确认执行的产物:确认行 + 新落的 assistant 消息。"""

    confirmation: ChatToolConfirmation
    message: ChatMessage

    @property
    def ok(self) -> bool:
        return not is_tool_error(self.confirmation.result_text or "")

    @property
    def error_code(self) -> str | None:
        return self.confirmation.error_code


class ConfirmationRecorder(Protocol):
    """chat 工具循环写 pending 的入口(非流式/流式两种事务姿势)。"""

    async def record(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        risk: str,
        arguments: str,
    ) -> PendingConfirmation: ...


def _as_utc(value: datetime) -> datetime:
    """sqlite 读回的时间是 naive 的,统一按 UTC 处理后再比较/输出。"""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def isoformat_utc(value: datetime) -> str:
    """ISO8601 UTC(带 +00:00 偏移),契约前端直接 new Date() 解析。"""
    return _as_utc(value).isoformat()


def pending_tool_output(tool_name: str) -> str:
    """回喂给模型的 tool 消息:写操作已生成确认卡片,等用户点击。

    形状与工具错误一致({"error": ...}),模型据此转述给用户;
    这里不暴露 arguments/结果,避免模型"以为自己已经写成功"。
    """
    return json.dumps(
        {
            "error": (
                f"写操作 {tool_name} 需要用户确认:已在对话里生成确认请求,"
                "请提示用户在确认卡片上点击确认后再查看结果,不要重复调用该工具"
            )
        },
        ensure_ascii=False,
    )


async def create_pending(
    session: AsyncSession,
    *,
    account_id: str,
    conversation_id: str,
    tool_call_id: str,
    tool_name: str,
    risk: str,
    arguments: str,
    now: datetime | None = None,
) -> ChatToolConfirmation:
    """落一行 pending 确认;同一 (account_id, tool_call_id) 幂等返回既有行。

    幂等是流式重放/客户端重试的安全网:唯一约束兜底,这里先读一遍避免
    IntegrityError(读不到再插入,并发窗口由唯一约束兜住,不额外处理)。
    """
    repository = ChatToolConfirmationRepository(session)
    existing = await repository.get_by_tool_call(account_id, tool_call_id)
    if existing is not None:
        return existing
    moment = now or datetime.now(UTC)
    confirmation = ChatToolConfirmation(
        account_id=account_id,
        conversation_id=conversation_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        risk=risk,
        arguments=arguments,
        status=STATUS_PENDING,
        error_code=ERROR_CONFIRMATION_REQUIRED,
        expires_at=moment + CONFIRMATION_TTL,
    )
    return await repository.add(confirmation)


class ChatConfirmationRecorder:
    """非流式 chat:与当前请求共用事务(assistant 消息与 pending 一起提交)。"""

    def __init__(
        self, session: AsyncSession, *, account_id: str, conversation_id: str
    ) -> None:
        self._session = session
        self._account_id = account_id
        self._conversation_id = conversation_id

    async def record(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        risk: str,
        arguments: str,
    ) -> PendingConfirmation:
        confirmation = await create_pending(
            self._session,
            account_id=self._account_id,
            conversation_id=self._conversation_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            risk=risk,
            arguments=arguments,
        )
        return PendingConfirmation(
            id=confirmation.id, expires_at=confirmation.expires_at
        )


class StreamConfirmationRecorder:
    """流式 chat:工具循环不占长事务,每条 pending 用独立短事务落库。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        account_id: str,
        conversation_id: str,
    ) -> None:
        self._factory = factory
        self._account_id = account_id
        self._conversation_id = conversation_id

    async def record(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        risk: str,
        arguments: str,
    ) -> PendingConfirmation:
        async with session_scope(self._factory) as session:
            confirmation = await create_pending(
                session,
                account_id=self._account_id,
                conversation_id=self._conversation_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                risk=risk,
                arguments=arguments,
            )
            return PendingConfirmation(
                id=confirmation.id, expires_at=confirmation.expires_at
            )


async def require_pending_confirmation(
    session: AsyncSession,
    *,
    account_id: str,
    conversation_id: str,
    confirmation_id: str,
    now: datetime | None = None,
) -> ChatToolConfirmation:
    """取待确认行并校验状态:不存在/跨账号 404,过期或已处理 409。

    取行即加排他锁(Postgres 有真实语义,sqlite 为 no-op):本函数只被
    confirm / cancel 调用,同一确认被两个请求并发处理时,后到者等锁,
    提交后读到非 pending → 409,保证"不重复执行"。
    """
    repository = ChatToolConfirmationRepository(session)
    confirmation = await repository.get_for_conversation_for_update(
        account_id, conversation_id, confirmation_id
    )
    if confirmation is None:
        raise NotFoundError(
            ERROR_NOT_FOUND,
            "工具确认不存在",
            details={"confirmation_id": confirmation_id},
        )
    moment = now or datetime.now(UTC)
    if confirmation.status == STATUS_EXPIRED or (
        confirmation.status == STATUS_PENDING
        and _as_utc(confirmation.expires_at) <= moment
    ):
        raise ConflictError(ERROR_EXPIRED, "工具确认已过期,请重新发起")
    if confirmation.status != STATUS_PENDING:
        raise ConflictError(ERROR_NOT_PENDING, "该工具确认已被处理")
    return confirmation


def _has_tool(toolkit: Any, name: str) -> bool:
    """工具箱里是否还有这个名字(确认瞬间工具可能已被删除/停用)。"""
    if toolkit is None:
        return False
    names = getattr(toolkit, "names", None)
    if names is None:
        return True
    return name in list(names)


async def confirm(
    session: AsyncSession,
    *,
    account_id: str,
    conversation_id: str,
    confirmation_id: str,
    toolkit: Any,
    now: datetime | None = None,
    grant: str = "once",
) -> ConfirmationOutcome:
    """执行确认的写操作:approved=True 重放调用,结果落 assistant 消息。

    重复确认/已取消/过期都在 require_pending_confirmation 里挡掉(409),
    因此这里不会重复执行;工具执行抛错由上层按 UpstreamError 处理,不落结果。

    `grant` 是用户选择的授权范围(once/session/always):确认成功后按它落
    会话授权或账号 auto 策略,与确认结果同一事务;`once` 不落库。
    """
    confirmation = await require_pending_confirmation(
        session,
        account_id=account_id,
        conversation_id=conversation_id,
        confirmation_id=confirmation_id,
        now=now,
    )
    if not _has_tool(toolkit, confirmation.tool_name):
        raise ConflictError(
            ERROR_TOOL_UNAVAILABLE,
            "工具已不可用,请重新发起",
            details={"tool": confirmation.tool_name},
        )
    output = await toolkit.execute(
        confirmation.tool_name,
        confirmation.arguments,
        approved=True,
        tool_call_id=confirmation.tool_call_id,
    )
    message = await _persist_assistant_content(
        session,
        account_id=account_id,
        conversation_id=conversation_id,
        content=output,
    )
    moment = now or datetime.now(UTC)
    confirmation.status = STATUS_CONFIRMED
    confirmation.result_text = output
    confirmation.error_code = parse_tool_error(output)
    confirmation.message_id = message.id
    confirmation.decided_at = moment
    await session.flush()
    # 授权落库与确认结果同一事务:确认成功才授权,回滚则授权也不生效。
    await approval_service.record_grant(
        session,
        account_id=account_id,
        conversation_id=conversation_id,
        tool=confirmation.tool_name,
        grant=grant,
    )
    return ConfirmationOutcome(confirmation=confirmation, message=message)


async def cancel(
    session: AsyncSession,
    *,
    account_id: str,
    conversation_id: str,
    confirmation_id: str,
    now: datetime | None = None,
) -> ChatToolConfirmation:
    """取消待确认的写调用:只改状态,不执行工具、不落 assistant 消息。"""
    confirmation = await require_pending_confirmation(
        session,
        account_id=account_id,
        conversation_id=conversation_id,
        confirmation_id=confirmation_id,
        now=now,
    )
    confirmation.status = STATUS_CANCELLED
    confirmation.decided_at = now or datetime.now(UTC)
    await session.flush()
    return confirmation
