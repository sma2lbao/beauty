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
    # 鉴权(架构 §10):本服务即自建 IdP(issuer 指向自身),这里只解析"当前租户";
    # audience 与下方 token_audience 保持一致
    auth_enabled: bool = True
    auth_issuer: str = "http://127.0.0.1:8001"
    auth_audience: str | None = "beauty-api"
    auth_jwks_url: str | None = None
    auth_tenant_claim: str = "tenant_id"
    # 自建认证(自 IdP 桩转正):注册/登录/刷新/内部签发 + OIDC 发现/JWKS
    token_ttl_seconds: int = 3600
    token_audience: str = "beauty-api"
    refresh_ttl_seconds: int = 2_592_000  # 刷新令牌 30 天,旋转式
    # 签名密钥持久化(PEM):设置后跨重启不轮换 kid;留空 = 进程内生成
    # (dev/test);生产(多实例/频繁重启)必须设置并挂持久卷
    signing_key_path: str = ""
    # 内部服务签发密钥(POST /v1/auth/issue 的 X-Internal-Secret):
    # 与服务间调用方(orchestrator)的 INTERNAL_AUTH_SECRET 一致;留空即关闭该端点
    internal_secret: str = ""
    # 社交 OAuth(Google/GitHub);client id/secret 留空即该 provider 关闭
    oauth_state_secret: str = ""  # 留空回退 internal_secret;两者都空则 OAuth 关闭
    oauth_state_ttl_seconds: int = 600
    # BYOK 应用层加密密钥(M0-11 占位;生产必须换随机长密钥/KMS)
    byok_secret: str = "dev-byok-secret-change-me"
    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
