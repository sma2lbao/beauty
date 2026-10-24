"""服务配置(env 前缀 CONNECTOR__,公共字段见 common.service_kit)。

连接器框架:当前没有内置连接器(邮件、日历能力均已下线),因此没有
任何 provider 凭据配置;新连接器按需自行增加配置项。
"""

from common.service_kit import ServiceSettings
from pydantic_settings import SettingsConfigDict


class Settings(ServiceSettings):
    model_config = SettingsConfigDict(
        env_prefix="CONNECTOR__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "connector-service"
    database_url: str = "sqlite+aiosqlite:///./connector.db"
    # 连接凭证应用层加密密钥(生产必须换随机长密钥/KMS)
    credential_secret: str = "dev-connector-secret-change-me"
