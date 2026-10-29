"""tool-service 配置。"""

from common.service_kit import ServiceSettings
from pydantic_settings import SettingsConfigDict


class Settings(ServiceSettings):
    """工具服务配置;环境变量前缀为 TOOL__(双下划线)。

    internal_secret 与 account-service 共享,用于换发租户 JWT 后调用
    workspace-service 的沙箱 API 与 knowledge-service 的检索 API;
    workspace_service_url / knowledge_service_url 即两者基地址。
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
    # P2:search_knowledge 经 HTTP 执行器代理到知识库(只读)。
    knowledge_service_url: str = "http://127.0.0.1:8006"
    knowledge_timeout_seconds: float = 20.0
    tool_timeout_seconds: float = 30.0
    sandbox_tool_ttl_seconds: int = 1800
    # P1:写前必须先 read(file_reads 表),失败回 tool.read_required/tool.stale_read。
    read_before_write_enabled: bool = True
    # 单 run 写入配额(write/edit 成功次数与累计字节);超限回 tool.budget_exceeded。
    max_writes_per_run: int = 50
    max_write_bytes_per_run: int = 5 * 1024 * 1024
    max_tool_calls_per_run: int = 60
    # P3:fetch 只读抓取公网文本(http/https);内网/回环/链路本地地址一律拒绝。
    # 会发起外部 I/O,因此属于 NETWORK_SWITCHES:测试默认钉成 False(零网络)。
    fetch_enabled: bool = False
    fetch_timeout_seconds: float = 15.0
    fetch_max_bytes: int = 1024 * 1024
    fetch_max_redirects: int = 3
    #: 允许抓取的主机(逗号分隔;空 = 任意公网主机)。example.com 含其子域。
    fetch_allowed_hosts: str = ""
    # P1-L:exec 在工作区沙箱内执行 shell 命令(destructive,需人工确认)。
    # 一旦出现在启用目录里,orchestrator 的 plan 闸会对所有 run 触发确认,
    # 因此默认关闭;开启前请确认沙箱用 microsandbox + network=deny。
    exec_enabled: bool = False
