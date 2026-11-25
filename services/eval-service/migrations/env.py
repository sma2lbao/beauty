"""alembic 迁移环境(async,装配收敛至 common.alembic_kit)。

DB URL 从服务配置读(EVAL__DATABASE_URL),与运行服务同源。
"""

from __future__ import annotations

from alembic import context
from common.alembic_kit import apply_service_config, run_migrations
from database.base import Base

from eval_service.core.config import Settings
from eval_service.models import (  # noqa: F401  注册 ORM
    eval_run as eval_run_models,
)

config = context.config
apply_service_config(config, Settings())
run_migrations(config, Base.metadata)
