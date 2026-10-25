"""alembic 迁移环境(async,装配收敛至 common.alembic_kit)。

约定:元数据来自 database.Base(命名约定保证迁移可回滚);DB URL 从服务配置读
(AGENT_ORCHESTRATOR__DATABASE_URL),与运行服务同源,迁移永远对准目标库本身,
不在 alembic.ini 里写死。
"""

from __future__ import annotations

from alembic import context
from common.alembic_kit import apply_service_config, run_migrations
from database.base import Base
from events import outbox as outbox_models  # noqa: F401  注册 outbox/幂等表

from agent_orchestrator.core.config import Settings
from agent_orchestrator.models import approval as approval_models  # noqa: F401
from agent_orchestrator.models import (  # noqa: F401  注册 ORM(含对话表)
    conversations as conversations_models,
)
from agent_orchestrator.models import mcp as mcp_models  # noqa: F401  注册 ORM
from agent_orchestrator.models import runs as runs_models  # noqa: F401  注册 ORM
from agent_orchestrator.models import skills as skills_models  # noqa: F401  注册 ORM

config = context.config
apply_service_config(config, Settings())
run_migrations(config, Base.metadata)
