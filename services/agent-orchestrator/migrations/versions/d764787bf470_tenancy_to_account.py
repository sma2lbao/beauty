"""tenancy to account

账号隔离列从 ``tenant_id`` 改为 ``account_id``(0.1.0 未发布,允许破坏式改造):
**只重命名列、索引与唯一约束,不删数据**;旧库里的历史值原样留在新列里,
本地执行 ``npm run dev:reset`` 重建即可。SQLite 走 batch 模式重写表,
Postgres 上 batch 退化为原生 ALTER。

Revision ID: d764787bf470
Revises: a9c4e2f7b8d1
Create Date: 2026-11-05 23:19:42.918130

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d764787bf470"
down_revision: str | None = "a9c4e2f7b8d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 带账号隔离列的表(每表一个 ``ix_<表>_account_id`` 单列索引)
_TABLES: tuple[str, ...] = (
    "approval_policies",
    "attachments",
    "chat_messages",
    "chat_tool_confirmations",
    "conversation_summaries",
    "conversations",
    "jobs",
    "mcp_server_auth",
    "mcp_servers",
    "memories",
    "outbox_events",
    "pending_instructions",
    "processed_events",
    "run_artifacts",
    "run_events",
    "runs",
    "skill_packages",
    "skill_versions",
)

#: (表名, 旧唯一约束名, 新唯一约束名, 新约束列):首列是账号列的唯一约束
_UNIQUE_CONSTRAINTS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "approval_policies",
        "uq_approval_policies_tenant_tool",
        "uq_approval_policies_account_tool",
        ("account_id", "tool"),
    ),
    (
        "chat_tool_confirmations",
        "uq_chat_tool_confirmations_tenant_id",
        "uq_chat_tool_confirmations_account_id",
        ("account_id", "tool_call_id"),
    ),
    (
        "conversation_summaries",
        "uq_conversation_summaries_tenant_id",
        "uq_conversation_summaries_account_id",
        ("account_id", "conversation_id"),
    ),
    (
        "mcp_server_auth",
        "uq_mcp_server_auth_tenant_id",
        "uq_mcp_server_auth_account_id",
        ("account_id", "server_id"),
    ),
    (
        "mcp_servers",
        "uq_mcp_servers_tenant_id",
        "uq_mcp_servers_account_id",
        ("account_id", "name"),
    ),
    (
        "processed_events",
        "uq_processed_events_tenant_id",
        "uq_processed_events_account_id",
        ("account_id", "consumer", "event_id"),
    ),
    (
        "skill_packages",
        "uq_skill_packages_tenant_id",
        "uq_skill_packages_account_id",
        ("account_id", "slug"),
    ),
)

#: (表名, 旧索引名, 新索引名, 旧索引列, 新索引列):账号列在首位的复合索引
_COMPOSITE_INDEXES: tuple[
    tuple[str, str, str, tuple[str, ...], tuple[str, ...]], ...
] = (
    (
        "chat_tool_confirmations",
        "ix_chat_tool_confirmations_tenant_conversation",
        "ix_chat_tool_confirmations_account_conversation",
        ("tenant_id", "conversation_id"),
        ("account_id", "conversation_id"),
    ),
)


def _rename_column(table: str, old: str, new: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.alter_column(old, new_column_name=new)


def upgrade() -> None:
    """tenant_id → account_id:列、索引与唯一约束一起改名(保留数据)。"""
    for table in _TABLES:
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
    for table, old_name, _new_name, _old_columns, _new_columns in _COMPOSITE_INDEXES:
        op.drop_index(old_name, table_name=table)

    for table in _TABLES:
        if table in {item[0] for item in _UNIQUE_CONSTRAINTS}:
            continue
        _rename_column(table, "tenant_id", "account_id")
    for table, old_name, _new_name, _columns in _UNIQUE_CONSTRAINTS:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(old_name, type_="unique")
            batch.alter_column("tenant_id", new_column_name="account_id")

    for table in _TABLES:
        op.create_index(f"ix_{table}_account_id", table, ["account_id"])
    for table, _old_name, new_name, columns in _UNIQUE_CONSTRAINTS:
        with op.batch_alter_table(table) as batch:
            batch.create_unique_constraint(new_name, list(columns))
    for table, _old_name, new_name, _old_columns, new_columns in _COMPOSITE_INDEXES:
        op.create_index(new_name, table, list(new_columns))

    # 发件箱信封里的账号列(消费方按它写自己的库)
    _rename_column("outbox_events", "envelope_tenant_id", "envelope_account_id")


def downgrade() -> None:
    """回退:account_id → tenant_id(同样只改名)。"""
    _rename_column("outbox_events", "envelope_account_id", "envelope_tenant_id")

    for table, _old_name, new_name, _old_columns, _new_columns in _COMPOSITE_INDEXES:
        op.drop_index(new_name, table_name=table)
    for table, _old_name, new_name, _columns in _UNIQUE_CONSTRAINTS:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(new_name, type_="unique")
    for table in _TABLES:
        op.drop_index(f"ix_{table}_account_id", table_name=table)

    for table in _TABLES:
        if table in {item[0] for item in _UNIQUE_CONSTRAINTS}:
            continue
        _rename_column(table, "account_id", "tenant_id")
    for table, old_name, _new_name, _columns in _UNIQUE_CONSTRAINTS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column("account_id", new_column_name="tenant_id")
            batch.create_unique_constraint(old_name, list(_columns))
    for table in _TABLES:
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
    for table, old_name, _new_name, old_columns, _new_columns in _COMPOSITE_INDEXES:
        op.create_index(old_name, table, list(old_columns))
