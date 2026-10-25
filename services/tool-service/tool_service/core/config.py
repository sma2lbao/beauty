"""tool-service 配置。"""

from common.service_kit import ServiceSettings
from pydantic_settings import SettingsConfigDict


class Settings(ServiceSettings):
    """工具服务配置;环境变量前缀为 TOOL__(双下划线)。

    internal_secret 与 account-service 共享,用于换发租户 JWT 后调用
    workspace-service 的沙箱 API;workspace_service_url 即其基地址。
    """

    model_config = SettingsConfigDict(
        env_prefix="TOOL__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "tool-service"
    database_url: str = "sqlite+aiosqlite:///./tool.db"
    internal_secret: str = ""
    workspace_service_url: str = "http://127.0.0.1:8005"
    workspace_timeout_seconds: float = 60.0
    tool_timeout_seconds: float = 30.0
    sandbox_tool_ttl_seconds: int = 1800
    tool_call_page_size: int = 50
    read_before_write_enabled: bool = False
