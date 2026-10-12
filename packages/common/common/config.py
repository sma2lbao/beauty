"""配置基类。

约定(docs/architecture.md §11):配置一律 pydantic-settings,环境变量前缀
= 服务名大写下划线,如 AGENT_ORCHESTRATOR__DATABASE_URL;
本地开发用项目根 ``.env``(不提交),生产用环境变量注入。
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseAppSettings(BaseSettings):
    """服务配置基类:子类设置自己的 env_prefix 与 service_name。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "unknown"
    env: str = Field(default="dev", pattern="^(dev|staging|prod)$")
    log_level: str = "INFO"

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"
