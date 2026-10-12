"""服务配置(env 前缀 AGENT_ORCHESTRATOR__,约定见 common.config)。"""

from common.config import BaseAppSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseAppSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_ORCHESTRATOR__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "agent-orchestrator"
    database_url: str = "sqlite+aiosqlite:///./agent_orchestrator.db"
    auto_create_tables: bool = False  # dev/test 直建表;生产走 alembic
    sse_poll_interval_seconds: float = 0.5  # SSE 活跃轮询间隔(测试可调小)
    sse_keepalive_polls: int = 30  # 每 N 次空轮询发一次 keep-alive 注释
    # 交付物存储(§7 阶段 B):database = 内容留在 DB 列;filesystem = 落本地目录
    artifact_store: str = "database"
    artifact_root: str = "./.artifacts"
    # 模型网关(架构 §5 唯一出口):enabled=false 时 plan/execute 走确定性桩
    llm_enabled: bool = False
    llm_gateway_url: str = "http://127.0.0.1:8003"
    llm_model: str | None = None  # None:走网关默认路由
    llm_timeout_seconds: float = 60.0
    # 工具(架构 §8):execute 走 function-calling 调 connector-service
    tools_enabled: bool = False
    connector_service_url: str = "http://127.0.0.1:8004"
    tool_timeout_seconds: float = 30.0
    # 技能提供方:未开启的能力不暴露给模型(能力即权限边界)
    documents_enabled: bool = False
    documents_root: str = "./.documents"
    documents_max_bytes: int = 200_000
    calendar_enabled: bool = False
    calendar_ics_path: str = "./.calendar.ics"
    # 浏览器直连时的跨域白名单(dev 默认 Vite;prod 用 JSON 数组覆盖)
    cors_allow_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    # 鉴权(架构 §10):enabled=false 时沿用 X-Tenant-Id 临时头(仅 dev/test)
    auth_enabled: bool = False
    auth_issuer: str = "http://127.0.0.1:3301/oidc"  # Logto 本地默认
    auth_audience: str | None = None
    auth_jwks_url: str | None = None  # 不填走 OIDC 发现文档
    auth_tenant_claim: str = "tenant_id"
