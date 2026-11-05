"""连接生命周期事件:与状态变更同事务写 outbox(设计 §5.5/§8.2, C1.10)。

事件契约(消费方按 type 订阅,payload 字段稳定):

- connector.connection.created.v1:新建连接或重新授权成功(upsert 新建时);
- connector.connection.expired.v1:因「需要重新授权」类失败被置为 expired
  (刷新失败一次发一次;执行/探测路径同样在置 expired 时发);
- connector.connection.revoked.v1:用户撤销连接。

payload 至少包含 connection_id、provider、account、status、owner_subject;
status 是事件发生后的连接状态(created=active, expired=expired,
revoked=revoked)。

outbox 保证「状态改了事件一定发、事务回滚事件一定不发」:事件与连接行写在同一
事务里。outbox_enabled=False(本地/单进程部署)时降级为结构化日志,不写库。
"""

from __future__ import annotations

from typing import Any

from common.logging import get_logger
from events.envelope import EventEnvelope
from events.outbox import OutboxRepository
from sqlalchemy.ext.asyncio import AsyncSession

from connector.core.config import Settings
from connector.models.connections import Connection

#: 事件类型常量(版本段 v1 是契约的一部分,变化必须换新类型)
CONNECTION_CREATED = "connector.connection.created.v1"
CONNECTION_EXPIRED = "connector.connection.expired.v1"
CONNECTION_REVOKED = "connector.connection.revoked.v1"

_log = get_logger("connector.events")


def connection_event_payload(connection: Connection) -> dict[str, Any]:
    """事件的稳定 payload;不放凭证,也不放任何可能含密钥的 metadata。"""
    return {
        "connection_id": connection.id,
        "provider": connection.provider,
        "account": connection.account,
        "status": connection.status,
        "owner_subject": connection.owner_subject,
    }


async def publish_connection_event(
    session: AsyncSession,
    settings: Settings,
    connection: Connection,
    event_type: str,
) -> None:
    """登记一条连接生命周期事件。

    必须在「状态变更」的同一事务里调用(调用方随后提交),否则会出现
    「事件发了状态却回滚」的不一致;outbox 关闭时只落日志,调用方无须分支。
    """
    payload = connection_event_payload(connection)
    if not settings.outbox_enabled:
        _log.info(
            "connection_event",
            event_type=event_type,
            account_id=connection.account_id,
            **payload,
        )
        return
    await OutboxRepository(session).record(
        connection.account_id,
        EventEnvelope.create(event_type, payload, account_id=connection.account_id),
    )
