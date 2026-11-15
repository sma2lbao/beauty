"""持久 shell 会话目录(跨实例共享的唯一真源)。

会话本体是 provider 侧的 pty 进程,它的输入输出与 ring buffer 仍只在**持有实例**
的内存里。这张表只登记「会话属于谁、在 provider 侧是哪个 pid」:

- `owner_instance` + `lease_expires_at`:所有者租约。持有实例每轮 sweep 续租;
  租约到期说明该实例已经消失(崩溃/重启/被摘掉),会话可以回收或由别的实例接管。
- `provider_pid`:provider 侧的进程号。provider 支持 attach(目前是 e2b 的
  `pty.connect`)时,别的实例可以凭它接管同一会话,输出流由新实例继续消费。
- `status`:running → closed(正常结束)/ lost(所有者消失且无法接管)。
  已关闭的行保留一段时间供排障,由 sweeper 清理。

不落库的东西:输出 ring buffer(只在本进程内存)、订阅者、seq 游标。
因此跨实例接管是「接着会话往下走」,不是「回放全部历史」。
"""

from __future__ import annotations

from datetime import datetime

from database.base import AccountMixin, Base, TimestampMixin, UUIDMixin
from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

# 生命周期:running(持有中)→ closed(正常结束)/ lost(所有者消失、无法接管)
SHELL_SESSION_STATUSES = ("running", "closed", "lost")
# 同一账号下 (沙箱, 会话) 唯一:session_id 是实例内生成的 uuid4 hex
ACCOUNT_SESSION_INDEX = "uq_shell_sessions_account_sandbox_session"
# 租约扫描索引:reaper 每轮按 (status, lease_expires_at) 找过期行
LEASE_INDEX = "ix_shell_sessions_lease"


class ShellSessionRecord(UUIDMixin, AccountMixin, TimestampMixin, Base):
    """一个持久 shell 会话的所有权登记(账号隔离)。"""

    __tablename__ = "shell_sessions"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "sandbox_id",
            "session_id",
            name=ACCOUNT_SESSION_INDEX,
        ),
        Index(LEASE_INDEX, "status", "lease_expires_at"),
    )

    sandbox_id: Mapped[str] = mapped_column(String(32), index=True)
    session_id: Mapped[str] = mapped_column(String(64))
    # provider 与它在控制面/运行时的全局名,接管时用来重新取回沙箱句柄
    provider: Mapped[str] = mapped_column(String(32))
    runtime_name: Mapped[str] = mapped_column(String(128))
    # provider 侧进程号;为 None 表示这个会话无法被别的实例接管
    provider_pid: Mapped[int | None] = mapped_column(Integer)
    cwd: Mapped[str] = mapped_column(String(512))
    cols: Mapped[int] = mapped_column(Integer, default=120)
    rows: Mapped[int] = mapped_column(Integer, default=32)
    tty: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    # 持有实例的身份(hostname 或显式 WORKSPACE__INSTANCE_ID)
    owner_instance: Mapped[str] = mapped_column(String(128), index=True)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
