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
    app_origin: str = "http://127.0.0.1:5173"
    # M0-06 代理目标
    runtime_base_url: str = "http://127.0.0.1:8002"
    account_base_url: str = "http://127.0.0.1:8001"
    workspace_base_url: str = "http://127.0.0.1:8005"
    # M0-05 Cookie 会话
    cookie_secure: bool = False
    cookie_name: str = "bm_session"
