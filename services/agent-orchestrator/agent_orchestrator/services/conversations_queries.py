"""conversation 查询与生命周期(自 conversations.py 拆出)。"""

from __future__ import annotations

import base64
from datetime import UTC, datetime

from common.errors import AppError, NotFoundError
from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.conversations import ChatMessage, Conversation
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import (
    ConversationRepository,
    MessageRepository,
    RunRepository,
)
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services.conversation_targets import (
    MessageTarget as MessageTarget,
)
from agent_orchestrator.services.conversation_targets import (
    require_conversation as require_conversation,
)
from agent_orchestrator.services.conversation_targets import (
    resolve_message_target as resolve_message_target,
)
from agent_orchestrator.services.conversation_targets import (
    resolve_stream_target as resolve_stream_target,
)
from agent_orchestrator.services.workspace import Workspace

logger = get_logger(__name__)


INACTIVE_STATUSES = frozenset(
    {
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.BLOCKED.value,
    }
)


async def _default_workspace_id(
    workspace: Workspace | None, tenant_id: str, *, warn: bool = True
) -> str | None:
    """租户默认工作区 id;客户端未启用/取不到/上游故障 → None(不阻断对话)。

    warn=False 用于列表接口的懒回填:未启用工作区是合法部署形态,不必每次
    拉列表都刷警告;建对话时(WARN)则要留下"归属缺失"的唯一线索。
    """
    if workspace is None:
        if warn:
            logger.warning(
                "workspace client disabled, conversation has no workspace",
                tenant_id=tenant_id,
            )
        return None
    try:
        default = await workspace.default_workspace(tenant_id)
    except Exception as exc:  # noqa: BLE001 - 工作区故障只降级,不阻断建对话
        if warn:
            logger.warning(
                "default workspace resolution failed",
                tenant_id=tenant_id,
                error=f"{type(exc).__name__}: {exc}",
            )
        return None
    if not default:
        if warn:
            logger.warning(
                "workspace service returned no default workspace",
                tenant_id=tenant_id,
            )
        return None
    resolved = default.get("id")
    return str(resolved) if resolved else None


async def resolve_workspace_id(
    workspace: Workspace | None,
    tenant_id: str,
    workspace_id: str | None = None,
) -> str | None:
    """确定对话所属工作区:显式 id 先校验,未给则取租户默认工作区。

    - 显式 id:经 workspace 客户端校验存在性,不存在 → 404 workspace.not_found;
      客户端不可达 → 502 workspace.upstream_error(显式指定就不静默改归属);
    - 显式 id + 客户端未启用:无法校验存在性,按原样接受并记 warning——
      "工作区集成没开"不应该让建对话失败;
    - 未给 id:落租户默认工作区;客户端未启用/上游故障 → None + warning 降级,
      对话照常建(归属缺失由列表接口的懒回填兜底)。
    """
    if workspace_id:
        if workspace is None:
            logger.warning(
                "workspace client disabled, workspace_id accepted unverified",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            return workspace_id
        found = await workspace.get_workspace(tenant_id, workspace_id)
        if found is None:
            raise NotFoundError("workspace.not_found", "工作区不存在")
        return workspace_id
    return await _default_workspace_id(workspace, tenant_id)


def encode_cursor(conversation_id: str) -> str:
    """把分页锚点编码成不透明游标(base64url,无填充)。"""
    raw = base64.urlsafe_b64encode(conversation_id.encode()).decode()
    return raw.rstrip("=")


def decode_cursor(cursor: str) -> str:
    """解码游标;非法游标给 400(不泄漏内部格式)。"""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return base64.urlsafe_b64decode(padded.encode()).decode()
    except Exception as exc:  # noqa: BLE001 - 任何解码失败都视为坏游标
        raise AppError("conversation.bad_cursor", "分页游标无效") from exc


async def _backfill_legacy_workspace(
    repo: ConversationRepository, tenant_id: str, workspace_id: str
) -> None:
    """把归属为空的存量会话补到该工作区;失败只记 warning(兼容性补丁)。"""
    try:
        filled = await repo.backfill_workspace_id(tenant_id, workspace_id)
    except Exception as exc:  # noqa: BLE001 - 回填失败不影响列表
        logger.warning(
            "conversation workspace backfill failed",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return
    if filled:
        logger.info(
            "conversations backfilled with default workspace",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            count=filled,
        )


async def list_conversations(
    session: AsyncSession,
    tenant_id: str,
    limit: int = 50,
    *,
    q: str | None = None,
    archived: bool = False,
    cursor: str | None = None,
    workspace_id: str | None = None,
    workspace: Workspace | None = None,
) -> tuple[list[Conversation], str | None]:
    """租户内最近活跃在前的对话列表(消息会顶 updated_at)。

    keyset 分页:多取一条判断 has_more,用最后一条 id 作下一页游标。

    存量空归属的回填(一次 UPDATE,失败只记 warning,不拖挂列表):旧会话按
    "属于租户默认工作区"处理。未给 workspace_id 时总是回填;给了过滤时只在
    过滤的就是默认工作区时回填——默认工作区首屏因此能看到历史会话,而按其他
    工作区过滤时既不动归属、也不会把会话泄漏到别的过滤结果里。
    """
    repo = ConversationRepository(session)
    # 空串按“未给”处理:?workspace_id= 不应变成“过滤一个空工作区”
    workspace_id = workspace_id or None
    # 尽力解析默认工作区:失败只是 None(仅影响回填,不影响过滤结果)
    default_id = await _default_workspace_id(workspace, tenant_id, warn=False)
    if default_id is not None and (workspace_id is None or workspace_id == default_id):
        await _backfill_legacy_workspace(repo, tenant_id, default_id)
    before_id = decode_cursor(cursor) if cursor else None
    rows = await repo.list_recent(
        tenant_id,
        limit + 1,
        q=q,
        archived=archived,
        before_id=before_id,
        workspace_id=workspace_id,
    )
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1].id) if has_more and items else None
    return items, next_cursor


async def rename_conversation(
    session: AsyncSession, tenant_id: str, conversation_id: str, title: str
) -> Conversation:
    """重命名会话(标题来自用户编辑,保留首尾空白清理)。"""
    conversation = await require_conversation(session, tenant_id, conversation_id)
    cleaned = title.strip()
    if not cleaned:
        raise AppError("conversation.invalid_title", "会话标题不能为空")
    conversation.title = cleaned
    conversation.updated_at = datetime.now(UTC)
    return conversation


async def set_model(
    session: AsyncSession, tenant_id: str, conversation_id: str, model: str | None
) -> Conversation:
    """设置会话级模型偏好(P1-G);None 保留原值(局部更新语义)。"""
    conversation = await require_conversation(session, tenant_id, conversation_id)
    if model is not None:
        conversation.model = model
        conversation.updated_at = datetime.now(UTC)
    return conversation


async def set_archived(
    session: AsyncSession, tenant_id: str, conversation_id: str, archived: bool
) -> Conversation:
    """归档 / 取消归档。"""
    conversation = await require_conversation(session, tenant_id, conversation_id)
    conversation.archived_at = datetime.now(UTC) if archived else None
    conversation.updated_at = datetime.now(UTC)
    return conversation


async def delete_conversation(
    session: AsyncSession, tenant_id: str, conversation_id: str
) -> None:
    """软删除:保留审计数据,列表/详情/消息随即按 404 处理。"""
    conversation = await require_conversation(session, tenant_id, conversation_id)
    conversation.deleted_at = datetime.now(UTC)
    conversation.updated_at = datetime.now(UTC)


async def active_run(
    session: AsyncSession, tenant_id: str, conversation_id: str
) -> Run | None:
    """会话当前进行中的 run:最新一跑仍在进行 → 它;否则 None。

    只看最新一跑:新 run 只在上一跑进入 inactive 状态后才会创建,
    所以"最新且进行中"即"当前对话正在执行的任务"。
    """
    runs = await RunRepository(session).list_for_conversation(
        tenant_id, conversation_id, limit=1
    )
    if not runs:
        return None
    return None if runs[0].status in INACTIVE_STATUSES else runs[0]


async def list_messages(
    session: AsyncSession, tenant_id: str, conversation_id: str
) -> list[ChatMessage]:
    """对话消息回放:按时间升序(user/assistant 混排)。"""
    await require_conversation(session, tenant_id, conversation_id)
    return await MessageRepository(session).list_for_conversation(
        tenant_id, conversation_id
    )
