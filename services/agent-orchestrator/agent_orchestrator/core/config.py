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
