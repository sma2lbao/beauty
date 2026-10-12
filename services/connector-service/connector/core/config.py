"""服务配置(env 前缀 CONNECTOR__,约定见 common.config)。"""

from common.config import BaseAppSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseAppSettings):
    model_config = SettingsConfigDict(
        env_prefix="CONNECTOR__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "connector-service"
    database_url: str = "sqlite+aiosqlite:///./connector.db"
    auto_create_tables: bool = False  # dev/test 直建表;生产走 alembic
