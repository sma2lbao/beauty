"""alembic 迁移环境(async,装配收敛至 common.alembic_kit)。

约定:元数据来自 database.Base(命名约定保证迁移可回滚);DB URL 从服务配置读
(TOOL__DATABASE_URL),与运行服务同源,不在 alembic.ini 里写死。
"""

from __future__ import annotations

from alembic import context
from common.alembic_kit import apply_service_config, run_migrations
from database.base import Base

from tool_service.core.config import Settings
from tool_service.models import file_read as file_read_models  # noqa: F401  注册 ORM
from tool_service.models import tool_call as tool_call_models  # noqa: F401  注册 ORM

config = context.config
apply_service_config(config, Settings())
run_migrations(config, Base.metadata)
