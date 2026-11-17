"""工具调用审计表。

每次工具执行(成功或失败)落一行:账号 + tool_call_id 唯一,支撑幂等重放与
运维查询;它不替代 orchestrator 的 run 事件流(run.tool.called.v1 仍是唯一
真源,见设计 8.4 / 12.3)。
"""

from __future__ import annotations

from datetime import UTC, datetime

from database.base import AccountMixin, Base, UUIDMixin
from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

DEFAULT_ARGUMENTS_CHARS = 2000

# ---- 执行期状态（S6：执行期预留） -----------------------------------------
#: 预留行已落库、执行方还在跑；此时同键请求收到 409 tool.call_in_progress。
STATUS_RUNNING = "running"
#: 终态三选一：成功、失败（含参数/配额拒绝）、被取消。
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
TERMINAL_STATUSES = frozenset({STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED})


class ToolCall(UUIDMixin, AccountMixin, Base):
    """一次工具调用的审计记录(行级账号隔离)。"""

    __tablename__ = "tool_calls"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "tool_call_id", name="uq_tool_calls_account_call"
        ),
        # 调用方算的稳定键：崩溃恢复后 tool_call_id 会变，靠它认出"同一次
        # 副作用"。NULL 在 Postgres/SQLite 里互不冲突，旧写入不受影响。
        UniqueConstraint(
            "account_id", "idempotency_key", name="uq_tool_calls_account_idempotency"
        ),
        Index("ix_tool_calls_run", "account_id", "run_id", "created_at"),
        Index("ix_tool_calls_tool", "account_id", "tool_name", "created_at"),
    )

    tool_call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # orchestrator 侧 "run + 节点 + 工具 + 规范化参数" 的稳定键（仅写操作）；
    # 为空表示调用方没提供，查重回落 tool_call_id。
    idempotency_key: Mapped[str | None] = mapped_column(
        String(96), nullable=True, default=None
    )
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    conversation_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None
    )
    # 连接器侧解析到的连接 id(仅 executor=connector 且连接已解析时非空);
    # 来自 execute 响应顶层字段,已从模型可见 result 中剥离(C2.4 审计)。
    connection_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None
    )
    # 主名(命中 alias 时也记主名,alias 只用在入站解析)。
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1")
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    # 先插 running 预留行再执行，终态只结算一次；旧数据由 server_default 标成
    # succeeded（迁移前只有执行完才落行，语义一致）。
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=STATUS_SUCCEEDED,
        server_default=text("'succeeded'"),
    )
    # 预留租约到期时间：执行方崩溃后行会停在 running，租约过期允许别的请求抢占。
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # 取消方（orchestrator）置位；执行方轮询到后放弃等待并结算成 cancelled。
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # 成功信封的 result 原样留存（只对带稳定幂等键的调用），重放时回真实内容。
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    arguments: Mapped[str] = mapped_column(Text, nullable=False, default="")
    arguments_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None
    )
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # write/edit 成功后写入的字节数(单 run 配额按此累计;其他工具为 0)。
    bytes_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # JSON 数组字符串(相对路径),为空时存 "[]"。
    paths: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    sha256_before: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None
    )
    sha256_after: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None
    )
    sandbox_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, default=None
    )
    # 写工具的服务端 unified diff:只落审计库,不回给编排器/模型。
    patch: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    lines_added: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    lines_removed: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    patch_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
