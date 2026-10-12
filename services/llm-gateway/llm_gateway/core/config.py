"""服务配置(env 前缀 LLM_GATEWAY__);提供商密钥由 litellm 直接读环境变量。"""

from common.config import BaseAppSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseAppSettings):
    model_config = SettingsConfigDict(
        env_prefix="LLM_GATEWAY__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "llm-gateway"
    default_model: str = "deepseek/deepseek-chat"  # litellm 路由格式 provider/model(§9)
    request_timeout_seconds: float = 60.0
