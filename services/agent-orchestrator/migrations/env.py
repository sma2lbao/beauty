"""alembic 迁移环境(async)。

约定:

- 元数据来自 database.Base——命名约定(NAMING_CONVENTION)保证迁移可回滚;
- DB URL 从服务配置读(AGENT_ORCHESTRATOR__DATABASE_URL),与运行服务同源,
  迁移永远对准目标库本身,不在 alembic.ini 里写死。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from database.base import Base
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from agent_orchestrator.core.config import Settings
from agent_orchestrator.models import (  # noqa: F401  注册 ORM(含对话表)
    conversations as conversations_models,
)
from agent_orchestrator.models import mcp as mcp_models  # noqa: F401  注册 ORM
from agent_orchestrator.models import runs as runs_models  # noqa: F401  注册 ORM
from events import outbox as outbox_models  # noqa: F401  注册 outbox/幂等表

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = Settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """offline 模式:不连库,直接生成 SQL。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
