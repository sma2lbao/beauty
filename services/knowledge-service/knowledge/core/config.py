"""服务配置(env 前缀 KNOWLEDGE__,约定见 common.config)。"""

from common.config import BaseAppSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseAppSettings):
    model_config = SettingsConfigDict(
        env_prefix="KNOWLEDGE__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "knowledge-service"
    database_url: str = "sqlite+aiosqlite:///./knowledge.db"
    auto_create_tables: bool = False  # dev/test 直建表;生产走 alembic
    # 嵌入(架构 §5 唯一出口):local = 无依赖词法近似;gateway = 经 llm-gateway
    embedding_provider: str = "local"
    embedding_model: str = "bge-m3"
    llm_gateway_url: str = "http://127.0.0.1:8003"
    embedding_timeout_seconds: float = 60.0
    # 分块与检索
    chunk_size: int = 400
    chunk_overlap: int = 60
    search_default_limit: int = 5
    search_max_limit: int = 20
    # 鉴权(架构 §10):与其他服务同一套 token→租户约定
    auth_enabled: bool = False
    auth_issuer: str = "http://127.0.0.1:3301/oidc"
    auth_audience: str | None = None
    auth_jwks_url: str | None = None
    auth_tenant_claim: str = "tenant_id"
