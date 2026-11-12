"""服务配置(env 前缀 WORKSPACE__,约定见 common.config)。"""

from common.config import BaseAppSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseAppSettings):
    model_config = SettingsConfigDict(
        env_prefix="WORKSPACE__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "workspace-service"
    # 本地默认连 deploy/docker-compose.yml 的 postgres:16(每服务独立库);
    # 测试走临时 SQLite、离线场景用 SQLite 覆盖,生产用 WORKSPACE__DATABASE_URL 覆盖
    database_url: str = "postgresql+asyncpg://beauty:beauty@localhost:5432/beauty_workspace"
    auto_create_tables: bool = False  # dev/test 直建表;生产走 alembic
    # 单库多迁移链(多服务共用一个库)时的独立版本表;留空即默认 alembic_version
    alembic_version_table: str | None = None
    # 交付物内容存储(§7):database = DB 列;filesystem = 本地对象目录
    artifact_store: str = "filesystem"
    artifact_root: str = "./.artifacts"
    # 通用二进制对象(P3:skill 包 zip)单对象字节上限;超限返回 400 object.too_large
    object_max_bytes: int = 20 * 1024 * 1024
    default_workspace_name: str = "默认工作区"
    # 鉴权(架构 §10):默认开启,与其他服务同一套 token→账号约定(自建 IdP)
    auth_enabled: bool = True
    auth_issuer: str = "http://127.0.0.1:8001"  # 自建 IdP:account-service
    auth_audience: str | None = "beauty-api"  # 与 account 的 TOKEN_AUDIENCE 一致
    auth_jwks_url: str | None = None
    auth_account_claim: str = "account_id"
    # 沙箱(§7 阶段 B / P1):provider=microsandbox 用真 microVM;
    # provider=local 直接在宿主目录/宿主进程上跑(无隔离,仅本地单账号可信环境);
    # provider=unavailable 供没有 runtime 的 dev 使用(调用即得到明确 502,不静默)
    sandbox_provider: str = "microsandbox"
    sandbox_default_image: str = "python"  # microsandbox 镜像别名
    sandbox_default_ttl_seconds: int = 1800
    sandbox_max_ttl_seconds: int = 7200
    sandbox_default_cpus: float = 1.0
    sandbox_default_memory_mb: int = 512
    sandbox_network: str = "deny"  # deny|public,请求可覆盖
    sandbox_exec_timeout_seconds: int = 60
    sandbox_max_output_bytes: int = 262_144  # 单条 stdout/stderr 截断上限
    sandbox_reaper_enabled: bool = True
    sandbox_reaper_interval_seconds: int = 60
    # 回收器多副本互斥(P0-4c):领取后把 expires_at 推后这么久,期间其它副本
    # 看不到这批行;进程崩溃后租约到期会被下一轮重新领取(destroy 需幂等)。
    sandbox_reap_lease_seconds: int = 300
    # 销毁失败后的重试延迟:避免每轮锤同一个坏容器。
    sandbox_reap_retry_delay_seconds: int = 30
    # microsandbox runtime home(空 = 用 SDK 默认 ~/.microsandbox);
    # 部署时必须指向可写目录,且 runtime 需要能访问 OCI registry
    sandbox_msb_home: str = ""
    # local provider 的沙箱根目录:每个沙箱一个 <root>/<name>/ 目录,
    # /workspace 映射到其中的 workspace/;默认落在服务目录下并被 gitignore
    sandbox_local_root: str = "./.sandboxes"
    # 持久 shell 会话(#2):进程内管理器,单实例假设(见 README 限制)。
    # 空闲 TTL 到点由 sweeper 关闭;关闭后的会话在内存里保留一段时间,
    # 让晚到的 SSE 连接仍能回放 ring buffer 与退出码。
    sandbox_shell_idle_seconds: int = 1800
    sandbox_shell_closed_retention_seconds: int = 300
    sandbox_shell_sweep_interval_seconds: int = 30
    sandbox_shell_ring_bytes: int = 65_536
    sandbox_shell_heartbeat_seconds: float = 15.0
    sandbox_shell_max_per_sandbox: int = 8
    sandbox_shell_max_subscribers: int = 32
