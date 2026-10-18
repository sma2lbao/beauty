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
    # 鉴权(架构 §10):默认开启——调用方(orchestrator 经 TokenBroker)一律带
    # Bearer;X-Tenant-Id 临时头仅保留为显式关闭鉴权的部署语义
    auth_enabled: bool = True
    auth_issuer: str = "http://127.0.0.1:8001"  # 自建 IdP:account-service
    auth_audience: str | None = "beauty-api"  # 与 account 的 TOKEN_AUDIENCE 一致
    auth_jwks_url: str | None = None  # 不填走 OIDC 发现文档
    auth_tenant_claim: str = "tenant_id"
    # 连接凭证应用层加密密钥(生产必须换随机长密钥/KMS)
    credential_secret: str = "dev-connector-secret-change-me"
    # OAuth 邮件连接(Gmail/M365;client id/secret 留空即该 provider 关闭)
    google_client_id: str = ""
    google_client_secret: str = ""
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    oauth_redirect_base: str = "http://127.0.0.1:8004/v1/oauth"
    oauth_web_base: str = "http://localhost:5173"
    oauth_state_secret: str = ""  # 留空回退 credential_secret
