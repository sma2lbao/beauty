"""eval-service 配置。"""

from common.service_kit import ServiceSettings
from pydantic_settings import SettingsConfigDict


class Settings(ServiceSettings):
    """评测服务配置;环境变量前缀为 EVAL__(双下划线)。"""

    model_config = SettingsConfigDict(
        env_prefix="EVAL__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "eval-service"
    database_url: str = "postgresql+asyncpg://beauty:beauty@localhost:5432/beauty_eval"
    # CI 上报密钥;为空时 POST /v1/runs 直接 503（fail closed），不会静默开放写入口。
    internal_secret: str = ""
