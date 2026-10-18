"""BFF 配置(env 前缀 BFF__,约定见 common.config)。"""

from common.config import BaseAppSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseAppSettings):
    model_config = SettingsConfigDict(
        env_prefix="BFF__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "bff"
    # 本地跨端口调试用;生产 SPA 与 BFF 同源,不需要 CORS
    app_origin: str = "http://localhost:5173"
    # M0-06 代理目标
    runtime_base_url: str = "http://127.0.0.1:8002"
    account_base_url: str = "http://127.0.0.1:8001"
    workspace_base_url: str = "http://127.0.0.1:8005"
    knowledge_base_url: str = "http://127.0.0.1:8006"
    connector_base_url: str = "http://127.0.0.1:8004"
    llm_gateway_base_url: str = "http://127.0.0.1:8003"
    # 浏览器直连 SSE 的公开 runtime 地址(M0-06 stream token)
    runtime_public_url: str = "http://127.0.0.1:8002"
    # M0-05 Cookie 会话:值必须用环境变量覆盖;默认仅供本地
    session_secret: str = "dev-bff-session-secret-change-me"
    cookie_secure: bool = False
    cookie_name: str = "bm_session"
    csrf_cookie_name: str = "bm_csrf"
    cookie_ttl_seconds: int = 7 * 24 * 3600
    # 内部签发(stream token)使用的共享密钥;为空则 /api/stream-token 503
    internal_secret: str = ""
    # 进程内限流(Redis 在 M0-03 接入);每租户/IP 每分钟请求数
    rate_limit_per_minute: int = 120
