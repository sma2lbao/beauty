"""revoke 存量连接(邮件与日历能力均已下线)

connector-service 收敛为连接器空框架:当前没有任何内置连接器,存量连接
不再对应任何可用操作,统一置 revoked 留痕(保留行,不物理删除)。
provider 列注释同步为「历史数据;无内置 provider」。

Revision ID: b2e4c7a91d33
Revises: cd993640a7eb
Create Date: 2026-10-21 23:20:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2e4c7a91d33"
down_revision: str | None = "cd993640a7eb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 不再有内置 provider:所有非 revoked 的存量连接统一撤销,保留行留痕(§8)
    op.execute("UPDATE connections SET status = 'revoked' WHERE status <> 'revoked'")
    # provider 列注释随能力变化同步(Postgres 支持列注释;sqlite 忽略)
    if op.get_bind().dialect.supports_comments:
        op.alter_column(
            "connections",
            "provider",
            comment="历史数据;无内置 provider",
        )


def downgrade() -> None:
    # 数据清理不可逆:无法区分"本次迁移撤销的连接"与"用户主动撤销的连接",
    # 且已下线的能力不会因迁移回滚而恢复,故 downgrade 有意为 no-op。
    pass
