"""运维告警事件 ORM(见 docs/design/alerting.md)。

`alerts` 是**运维级**表,不带 account_id:告警说的是"这个部署"的健康度
(outbox 投递失败、run 卡死、job 连续失败),不是某个账号的私有数据。

表是 append-only 的事件流,**一行 = 某规则在某时刻的一次状态切换**
(firing / resolved)。当前状态由"每个 code 的最新一行"给出,这样多副本同时
评估时不需要对同一行做状态机写:唯一约束 (code, status, bucket) 只挡住
同一去重桶里的重复写入。
"""

from __future__ import annotations

from database.base import Base, TimestampMixin, UUIDMixin
from sqlalchemy import Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

#: 告警状态:进入告警 / 已恢复。
FIRING = "firing"
RESOLVED = "resolved"
#: 严重度:需要人立刻处理(critical)/ 需要人看一眼(warning)。
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"


class Alert(UUIDMixin, TimestampMixin, Base):
    """一条告警状态切换事件(firing / resolved)。"""

    __tablename__ = "alerts"
    __table_args__ = (
        # 去重桶:同一规则、同一状态、同一时间桶只落一条——多副本同时评估时
        # 靠这条约束收敛,后到的副本拿到 IntegrityError 后放弃写入。
        UniqueConstraint(
            "code", "status", "bucket", name="uq_alerts_code_status_bucket"
        ),
        # 端点按"每 code 最新一行"取当前状态;保留期清理按 created_at 删。
        Index("ix_alerts_code_created_at", "code", "created_at"),
        Index("ix_alerts_created_at", "created_at"),
    )

    code: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(
        String(16), default=SEVERITY_WARNING, server_default=SEVERITY_WARNING
    )
    status: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    # 触发时的度量值与阈值:复盘时不用再猜"当时是多少"。
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 去重桶序号 = epoch(now) // alerts_cooldown_seconds(写入时由服务层算好)。
    bucket: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
