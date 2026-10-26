"""服务配置(env 前缀 KNOWLEDGE__,公共字段见 common.service_kit)。"""

from common.service_kit import ServiceSettings
from pydantic_settings import SettingsConfigDict


class Settings(ServiceSettings):
    model_config = SettingsConfigDict(
        env_prefix="KNOWLEDGE__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "knowledge-service"
    database_url: str = "sqlite+aiosqlite:///./knowledge.db"
    # 嵌入(架构 §5 唯一出口):local = 无依赖词法近似;gateway = 经 llm-gateway
    embedding_provider: str = "local"
    embedding_model: str = "bge-m3"
    llm_gateway_url: str = "http://127.0.0.1:8003"
    embedding_timeout_seconds: float = 60.0
    # 服务间内部密钥:须与 account-service / llm-gateway 的 INTERNAL_SECRET 一致;
    # 留空则 llm-gateway 拒绝 BYOK 解析(gateway 提供方在生产必须配置)
    internal_secret: str = ""
    # 分块与检索
    chunk_size: int = 400
    chunk_overlap: int = 60
    search_default_limit: int = 5
    search_max_limit: int = 20
