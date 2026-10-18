"""会话附件本地最小版:内容进 DB,类型/大小/数量白名单(M0-12)。

生产多实例前需要换对象存储或共享盘;这里先把上传/下载/消息绑定闭环做出来。
"""

from __future__ import annotations

from pathlib import Path

from common.errors import AppError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.conversations import Attachment, ChatMessage
from agent_orchestrator.repositories import AttachmentRepository, ConversationRepository

MAX_FILENAME = 255
ALLOWED_TYPES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
}
EXTENSION_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _normalize_type(content_type: str | None, filename: str) -> str:
    candidate = (content_type or "").split(";", 1)[0].strip().lower()
    if candidate in ("", "application/octet-stream"):
        candidate = EXTENSION_TYPES.get(Path(filename).suffix.lower(), "")
    if candidate not in ALLOWED_TYPES:
        raise AppError(
            "attachment.type_not_allowed",
            "不支持的附件类型(允许文本/Markdown/CSV/JSON/PDF/图片)",
        )
    return candidate


def _normalize_filename(filename: str | None) -> str:
    name = Path((filename or "attachment").strip()).name
    if not name or name in (".", ".."):
        raise AppError("attachment.invalid_filename", "附件文件名无效")
    return name[:MAX_FILENAME]


async def create_attachment(
    session: AsyncSession,
    tenant_id: str,
    conversation_id: str,
    *,
    filename: str | None,
    content_type: str | None,
    data: bytes,
    max_bytes: int,
) -> Attachment:
    await ConversationRepository(session).require_active_for_tenant(
        tenant_id, conversation_id
    )
    if not data:
        raise AppError("attachment.empty", "附件内容为空")
    if len(data) > max_bytes:
        raise AppError(
            "attachment.too_large",
            f"附件超过 {max_bytes // (1024 * 1024)}MB 上限",
            details={"max_bytes": max_bytes},
        )
    safe_name = _normalize_filename(filename)
    normalized_type = _normalize_type(content_type, safe_name)
    attachment = Attachment(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        filename=safe_name,
        content_type=normalized_type,
        size_bytes=len(data),
        content=data,
    )
    await AttachmentRepository(session).add(attachment)
    return attachment


async def list_attachments(
    session: AsyncSession, tenant_id: str, conversation_id: str
) -> list[Attachment]:
    await ConversationRepository(session).require_active_for_tenant(
        tenant_id, conversation_id
    )
    return await AttachmentRepository(session).list_for_conversation(
        tenant_id, conversation_id
    )


async def require_attachment(
    session: AsyncSession, tenant_id: str, attachment_id: str
) -> Attachment:
    return await AttachmentRepository(session).require_for_tenant(
        tenant_id, attachment_id
    )


async def delete_attachment(
    session: AsyncSession, tenant_id: str, attachment_id: str
) -> None:
    repo = AttachmentRepository(session)
    attachment = await repo.require_for_tenant(tenant_id, attachment_id)
    await repo.delete(attachment)


async def link_attachments(
    session: AsyncSession,
    tenant_id: str,
    conversation_id: str,
    message: ChatMessage,
    attachment_ids: list[str],
    *,
    max_count: int,
) -> None:
    """把本次消息选中的附件绑定到用户消息;只允许本会话内、未绑定的附件。"""
    if not attachment_ids:
        return
    if len(attachment_ids) > max_count:
        raise AppError(
            "attachment.too_many",
            f"单条消息最多 {max_count} 个附件",
            details={"max_count": max_count},
        )
    if len(set(attachment_ids)) != len(attachment_ids):
        raise AppError("attachment.duplicate", "附件不能重复选择")
    repo = AttachmentRepository(session)
    for attachment_id in attachment_ids:
        attachment = await repo.require_for_tenant(tenant_id, attachment_id)
        if attachment.conversation_id != conversation_id:
            raise AppError("attachment.not_in_conversation", "附件不属于当前会话")
        if attachment.message_id is not None:
            raise AppError("attachment.already_bound", "附件已绑定到其他消息")
        attachment.message_id = message.id
