"""服务配置(env 前缀 ACCOUNT__,约定见 common.config)。"""

from common.config import BaseAppSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseAppSettings):
    model_config = SettingsConfigDict(
        env_prefix="ACCOUNT__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "account-service"
    database_url: str = "sqlite+aiosqlite:///./account.db"
    auto_create_tables: bool = False  # dev/test 直建表;生产走 alembic
    default_plan: str = "free"
    # 鉴权(架构 §10):认证交给 IdP;这里只解析"当前租户"
    auth_enabled: bool = False
    auth_issuer: str = "http://127.0.0.1:3301/oidc"
    auth_audience: str | None = None
    auth_jwks_url: str | None = None
    auth_tenant_claim: str = "tenant_id"
    # dev IdP 桩(core/dev_idp.py):开桩后本服务兼作签发方,登录端点 +
    # OIDC 发现/JWKS 端点可用;阶段 B 换 Logto 时各服务只改 AUTH_ISSUER
    dev_auth_enabled: bool = False
    dev_token_ttl_seconds: int = 3600
    dev_token_audience: str = "beauty-api"
    # 内部服务签发密钥(POST /v1/auth/issue 的 X-Internal-Secret):
    # 与服务间调用方(orchestrator)的 INTERNAL_AUTH_SECRET 一致;留空即关闭该端点
    dev_internal_secret: str = ""
