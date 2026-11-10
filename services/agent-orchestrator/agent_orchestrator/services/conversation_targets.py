"""会话解析:存在性校验与发消息前的技能/MCP 绑定解析。

从 services/conversations.py 拆出(R-29 后继拆分):这些函数只依赖会话仓储与
skill/mcp 服务,不碰模型调用与流式产出,单独成模块便于复用与单测。

口径:未知 skill / 未知或停用的 MCP server 必须在落库(或开流)之前报错——
否则会留下半残引用,或对流式请求只能发一帧错误。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from common.errors import ConflictError, NotFoundError
from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.models.conversations import Conversation
from agent_orchestrator.repositories import ConversationRepository
from agent_orchestrator.services import attachments as attachments_service
from agent_orchestrator.services import mcp as mcp_service
from agent_orchestrator.services import skill_packages, triage
from agent_orchestrator.services.skill_packages import SkillDefinition

if TYPE_CHECKING:
    from agent_orchestrator.services.llm import LLMClient
    from agent_orchestrator.services.skills import SkillRegistry


async def require_conversation(
    session: AsyncSession, account_id: str, conversation_id: str
) -> Conversation:
    """取账号内未删除的对话;不存在/跨账号/已删 → NotFoundError(404)。"""
    return await ConversationRepository(session).require_active_for_account(
        account_id, conversation_id
    )


@dataclass(frozen=True)
class MessageTarget:
    """发消息前解析出的技能 / MCP 绑定。

    未知 skill 或未知/停用的 MCP server 必须在落库(或开流)之前报错,
    否则会留下半残引用、或对流式请求只能发一帧错误。
    """

    skill: SkillDefinition | None = None
    mcp_server_ids: list[str] = field(default_factory=list)
    mcp_reference_note: str = ""


async def resolve_message_target(
    session: AsyncSession,
    account_id: str,
    *,
    skills: SkillRegistry,
    settings: Any,
    skill_id: str | None,
    mcp_server_ids: list[str],
) -> MessageTarget:
    """在给定事务内解析技能与 MCP 绑定(未知 404、停用/未启用 409)。"""
    skill: SkillDefinition | None = None
    if skill_id:
        skill = await skill_packages.resolve_skill(
            session, account_id, skill_id, skills
        )
        if skill is None:
            raise NotFoundError("skill.not_found", "Skill 不存在")
    if not mcp_server_ids:
        return MessageTarget(skill=skill)
    if not settings.mcp_enabled:
        raise ConflictError("mcp.disabled", "MCP 能力未启用")
    servers = await mcp_service.resolve_selected(session, account_id, mcp_server_ids)
    return MessageTarget(
        skill=skill,
        mcp_server_ids=[server.id for server in servers],
        mcp_reference_note=mcp_service.reference_note(servers),
    )


async def precheck_image_mode(
    factory: async_sessionmaker[AsyncSession],
    account_id: str,
    *,
    mode: str,
    attachment_ids: list[str],
    llm: LLMClient | None,
) -> None:
    """开流前的图片/模式预检(独立短事务):带图任务/带图无模型 → 409。

    任务(run)路径本期不注入图片(model-routing §7):带图任务等于丢图,
    所以必须在 StreamingResponse 之前拒绝——开流后只能发一帧错误。
    """
    if not attachment_ids:
        triage.require_task_images(mode, False)
        return
    async with session_scope(factory) as session:
        has_images = await attachments_service.images_in(
            session, account_id, attachment_ids
        )
    triage.require_task_images(mode, has_images)
    triage.require_image_model(mode, llm, has_images)


async def resolve_stream_target(
    factory: async_sessionmaker[AsyncSession],
    account_id: str,
    conversation_id: str,
    *,
    skills: SkillRegistry,
    settings: Any,
    skill_id: str | None,
    mcp_server_ids: list[str],
) -> MessageTarget:
    """开流前的 404/409 预检(独立短事务)。

    流式响应一旦开始,状态码已经发出去了;所以会话存在性、技能与 MCP 引用
    必须在这之前校验完——预检事务不能复用长连接的读循环。
    """
    async with session_scope(factory) as session:
        await require_conversation(session, account_id, conversation_id)
        return await resolve_message_target(
            session,
            account_id,
            skills=skills,
            settings=settings,
            skill_id=skill_id,
            mcp_server_ids=mcp_server_ids,
        )
