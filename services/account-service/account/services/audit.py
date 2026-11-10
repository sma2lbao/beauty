"""管理面审计:请求上下文快照 + 业务同事务写入 + 拒绝访问的独立短事务。

- 读端点不审计,只有写动作与拒绝访问落 audit_logs(admin-plane §3.4);
- 业务写审计用调用方的 session(与业务同事务:业务回滚审计也回滚);
- 拒绝访问发生在业务之前,用 app.state.session_factory 开独立短事务,
  失败只记 warning——审计写不进去不能改变 403/401 的结论。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.logging import current_context, get_logger
from database.session import session_scope
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from account.models.account import AuditLog, User
from account.repositories import AuditLogRepository

log = get_logger("account.audit")

DENIED_ACTION = "admin.access.denied"
_MAX_USER_AGENT = 256
_MAX_TARGET_ID = 64


@dataclass(frozen=True)
class AuditContext:
    """一次管理请求的审计主体(actor 与请求元数据快照)。"""

    actor_account_id: str
    actor_email: str
    request_id: str | None
    ip: str | None
    user_agent: str | None


def _client_ip(request: Request) -> str | None:
    """客户端 IP:优先 X-Forwarded-For 首段(反代场景),否则连接对端。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return request.client.host if request.client is not None else None


def context_from_request(request: Request, actor: User) -> AuditContext:
    """从请求 + 当前管理员构造审计上下文(不含任何密钥/令牌)。"""
    # 优先 request.state 快照:验签拉 JWKS 的嵌套请求会清掉 contextvars。
    request_id = getattr(request.state, "request_id", None) or current_context().get(
        "request_id"
    )
    user_agent = (request.headers.get("user-agent") or "").strip()
    return AuditContext(
        actor_account_id=actor.id,
        actor_email=actor.email,
        request_id=str(request_id) if request_id else None,
        ip=_client_ip(request),
        user_agent=user_agent[:_MAX_USER_AGENT] or None,
    )


def _entry(
    ctx: AuditContext,
    *,
    action: str,
    target_type: str,
    target_id: str,
    detail: dict[str, Any],
    status: str,
) -> AuditLog:
    return AuditLog(
        actor_account_id=ctx.actor_account_id,
        actor_email=ctx.actor_email,
        action=action,
        target_type=target_type,
        target_id=target_id[:_MAX_TARGET_ID],
        status=status,
        detail=detail,
        request_id=ctx.request_id,
        ip=ctx.ip,
        user_agent=ctx.user_agent,
    )


async def record(
    session: AsyncSession,
    ctx: AuditContext,
    *,
    action: str,
    target_type: str,
    target_id: str,
    detail: dict[str, Any],
    status: str = "success",
) -> AuditLog:
    """业务同事务写审计:由调用方的事务边界统一提交/回滚。"""
    return await AuditLogRepository(session).add(
        _entry(
            ctx,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            status=status,
        )
    )


async def record_denied(
    session_factory: Any,
    ctx: AuditContext,
    *,
    method: str,
    path: str,
) -> None:
    """无权限访问的拒绝审计:独立短事务,失败只告警。"""
    entry = _entry(
        ctx,
        action=DENIED_ACTION,
        target_type="endpoint",
        target_id=path,
        detail={"method": method, "required": "admin"},
        status="denied",
    )
    try:
        async with session_scope(session_factory) as session:
            await AuditLogRepository(session).add(entry)
    except Exception:  # noqa: BLE001 - 审计失败不该改变鉴权结论
        log.warning("admin.denied_audit_failed", exc_info=True)
