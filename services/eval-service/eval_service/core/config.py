"""eval-service 配置。"""

from common.service_kit import ServiceSettings
from pydantic import SecretStr
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
    # 数据集目录;为空时从 cwd 向上查找 evals/datasets（本地/容器同一布局）。
    datasets_root: str = ""
    # 报告对象存储：report_json 超过 report_inline_max_bytes 时转存对象存储，
    # DB 只留摘要与 report_key；database 表示不拆分（本地/测试默认）。
    artifact_store: str = "database"
    artifact_root: str = "./.artifacts"
    report_inline_max_bytes: int = 64 * 1024
    # 保留期：维护命令删除超过 retention_days 的运行及其对象；0 表示不清理。
    retention_days: int = 90
    # 常驻清理循环间隔（秒）；maintenance loop 默认读取，--once 供 cron 使用。
    retention_interval_seconds: int = 86_400
    # COS 桶生命周期兜底：对象存储里的报告在 retention 之外再按桶规则过期。
    artifact_lifecycle_days: int = 180
    artifact_lifecycle_prefix: str = "eval-runs/"
    # COS 后端（artifact_store=cos），字段与 workspace-service 保持一致。
    artifact_cos_bucket: str = ""
    artifact_cos_region: str = ""
    artifact_cos_secret_id: SecretStr | None = None
    artifact_cos_secret_key: SecretStr | None = None
    artifact_cos_token: SecretStr | None = None
    artifact_cos_scheme: str = "https"
    artifact_cos_endpoint: str = ""
    artifact_cos_timeout_seconds: float = 30.0
    artifact_cos_retry_times: int = 3
    artifact_cos_pool_max_size: int = 10
