"""服务配置(env 前缀 KNOWLEDGE__,公共字段见 common.service_kit)。"""

from common.service_kit import ServiceSettings
from pydantic import Field
from pydantic_settings import SettingsConfigDict


class Settings(ServiceSettings):
    model_config = SettingsConfigDict(
        env_prefix="KNOWLEDGE__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "knowledge-service"
    # 本地默认连 deploy/docker-compose.yml 的 postgres:16(每服务独立库);
    # 测试走临时 SQLite、离线场景用 SQLite 覆盖,生产用 KNOWLEDGE__DATABASE_URL 覆盖
    database_url: str = (
        "postgresql+asyncpg://beauty:beauty@localhost:5432/beauty_knowledge"
    )
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
    # 检索模式:vector(纯向量兼容旧行为)/ keyword(纯关键词)/ hybrid(向量 + 关键词 RRF)
    search_default_mode: str = "hybrid"
    # 每路先取多少候选再融合(过小会丢召回,过大会拖慢融合;50 在两路各取一次足够)
    search_candidate_limit: int = Field(default=50, ge=1)
    # RRF 的平滑常数:常用 60(排名衰减更平缓,避免头部两路同时命中就碾压)
    rrf_k: int = Field(default=60, ge=1)
    # 融合权重(仅 hybrid 用):向量占 vector_weight,关键词占 1 - vector_weight
    search_vector_weight: float = Field(default=0.5, ge=0, le=1)
