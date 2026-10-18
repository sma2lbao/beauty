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
    # 附件本地最小版(M0-12):内容进 DB;单文件上限与单条消息附件数上限
    max_attachment_bytes: int = 10 * 1024 * 1024
    max_attachments_per_message: int = 5
    # 本地 worker(M0-04):run 的 plan/approve/resume/retry 入队执行。
    # 测试夹具显式关闭以保留同步语义。
    jobs_enabled: bool = True
    jobs_poll_interval_seconds: float = 0.5
    jobs_lease_seconds: int = 60
    jobs_max_attempts: int = 3
    # Skill manifest 目录(M0 本地:仓库 skills/;为空时自动定位仓库根)
    skills_dir: str = ""
    # MCP Client(M1 本地版):允许租户配置第三方 MCP server
    mcp_enabled: bool = True
    mcp_secret: str = "dev-mcp-secret-change-me"
    mcp_timeout_seconds: float = 10.0
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
    # 企业知识库(§9):search_knowledge 工具经 knowledge-service 检索
    knowledge_enabled: bool = False
    knowledge_service_url: str = "http://127.0.0.1:8006"
    knowledge_timeout_seconds: float = 30.0
    # 事务性 outbox(§6.3/§8):run 事件与业务同事务入发件箱,再由 dispatcher
    # 至少一次投递(失败退避重试);阶段 A 用进程内总线
    outbox_enabled: bool = False
    outbox_dispatch_interval_seconds: float = 1.0
    outbox_batch_size: int = 50
    outbox_max_attempts: int = 5
    # 交付物自动入库(§8 摄取):消费者把 run.delivered.v1 推进 knowledge-service
    knowledge_ingest_enabled: bool = False
    # 交付物镜像到 workspace-service(§7:artifacts 归属 workspace)
    workspace_enabled: bool = False
    workspace_service_url: str = "http://127.0.0.1:8005"
    workspace_timeout_seconds: float = 30.0
    # 浏览器直连时的跨域白名单(dev 默认 Vite;prod 用 JSON 数组覆盖)
    cors_allow_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    # 鉴权(架构 §10):默认开启——租户来自签名 claim(自建 IdP 即 account-service);
    # X-Tenant-Id 临时头仅保留为显式关闭鉴权(AUTH_ENABLED=false)的部署语义
    auth_enabled: bool = True
    auth_issuer: str = "http://127.0.0.1:8001"  # 自建 IdP:account-service
    auth_audience: str | None = "beauty-api"  # 与 account 的 TOKEN_AUDIENCE 一致
    auth_jwks_url: str | None = None  # 不填走 OIDC 发现文档
    auth_tenant_claim: str = "tenant_id"
    # 服务间调用(§5):一律经 account 的内部 issue 端点换租户 token——
    # 租户来自签名 claim,不再有 X-Tenant-Id 裸调路径;
    # 必须与 account 的 ACCOUNT__INTERNAL_SECRET 一致,未配置时调用下游即报错
    internal_auth_secret: str = ""
