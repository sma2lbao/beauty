"""站内通知(通知持久化)。

通知原先只是 run 状态变化的一次性提醒投影(SSE 推完即弃);落表之后它变成
可回看的未读清单:关掉页面再回来仍能看到"哪些任务等你处理",Web 端未读角标
也不再依赖本次会话内的内存游标。

设计要点:
- **投影而非事件源**:通知由后台 sweeper 从 run 状态扫描生成,row 不可变
  (除了 read_at);run_events 仍是唯一事实来源,通知丢了可以扫回来。
- **幂等写入**:同一 (account_id, kind, dedupe_key) 只有一行,dedupe_key 对
  run 状态通知取 <run_id>:<status>。多副本同时扫描时靠唯一约束收敛。
- **按账号隔离**:混入 AccountMixin;保留期只清**已读**的旧行,未读是待办。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from database.base import AccountMixin, Base, TimestampMixin, UUIDMixin
from sqlalchemy import (
    JSON,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column


class Notification(UUIDMixin, AccountMixin, TimestampMixin, Base):
    """账号内的一条站内通知(当前只有 run 状态一类)。"""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "kind",
            "dedupe_key",
            name="uq_notifications_account_kind_dedupe",
        ),
        Index("ix_notifications_account_created_at", "account_id", "created_at"),
        Index("ix_notifications_account_read_at", "account_id", "read_at"),
    )

    # 通知类别:run.status = run 进入需要人关注的状态(便于将来扩展别的来源)。
    kind: Mapped[str] = mapped_column(String(32))
    # 幂等键:同一类别内业务去重(run 状态通知 = <run_id>:<status>)。
    dedupe_key: Mapped[str] = mapped_column(String(160))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    # 展示色调(info/success/warning/danger):落库时定死,客户端不必再映射状态。
    tone: Mapped[str] = mapped_column(String(16), default="info")
    # 点击跳转的前端路径(如 /?conversation=...);为空则不可跳转。
    to_path: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 通知指向的业务对象(可空,便于按 run/会话回溯)。
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 原始载荷(当前含 run_title/goal/status):列表渲染用,避免再回查 run。
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # 已读时间:NULL = 未读;只记第一次读的时间,不因"再读"更新。
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
