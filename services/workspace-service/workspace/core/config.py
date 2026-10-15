"""服务配置(env 前缀 WORKSPACE__,约定见 common.config)。"""

from common.config import BaseAppSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseAppSettings):
    model_config = SettingsConfigDict(
        env_prefix="WORKSPACE__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "workspace-service"
    database_url: str = "sqlite+aiosqlite:///./workspace.db"
    auto_create_tables: bool = False  # dev/test 直建表;生产走 alembic
    # 交付物内容存储(§7):database = DB 列;filesystem = 本地对象目录
    artifact_store: str = "filesystem"
    artifact_root: str = "./.artifacts"
    default_workspace_name: str = "默认工作区"
    # 鉴权(架构 §10):默认开启,与其他服务同一套 token→租户约定(自建 IdP)
    auth_enabled: bool = True
    auth_issuer: str = "http://127.0.0.1:8001"  # 自建 IdP:account-service
    auth_audience: str | None = "beauty-api"  # 与 account 的 TOKEN_AUDIENCE 一致
    auth_jwks_url: str | None = None
    auth_tenant_claim: str = "tenant_id"
