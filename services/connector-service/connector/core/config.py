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
    # 鉴权(架构 §10):enabled=false 时沿用 X-Tenant-Id 临时头(仅 dev/test)
    auth_enabled: bool = False
    auth_issuer: str = "http://127.0.0.1:3301/oidc"  # Logto 本地默认
    auth_audience: str | None = None
    auth_jwks_url: str | None = None  # 不填走 OIDC 发现文档
    auth_tenant_claim: str = "tenant_id"
