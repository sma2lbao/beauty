"""alembic 迁移环境(async,装配收敛至 common.alembic_kit)。

约定:元数据来自 database.Base(命名约定保证迁移可回滚);DB URL 从服务配置读
(ACCOUNT__DATABASE_URL),与运行服务同源,不在 alembic.ini 里写死。
"""

from __future__ import annotations

from alembic import context
from common.alembic_kit import apply_service_config, run_migrations
from database.base import Base

from account.core.config import Settings
from account.models import account as account_models  # noqa: F401  注册 ORM

config = context.config
apply_service_config(config, Settings())
run_migrations(config, Base.metadata)
