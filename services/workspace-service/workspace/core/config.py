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
    database_url: str = (
        "postgresql+asyncpg://beauty:beauty@localhost:5432/beauty_workspace"
    )
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
    # 每个 microVM 的 /workspace 挂到宿主上的私有目录根:空 = <msb_home>/workspaces。
    # 必须每沙箱一份(不能共享),否则模型会读到上一轮/别的账号留下的文件;
    # destroy 时由 provider 连带删除。
    sandbox_workspace_root: str = ""
    # E2B 云沙箱(provider=e2b):microVM 跑在 E2B 控制面。官方云与兼容 E2B 的
    # 厂商(如阿里云 Agent Sandbox 极速版)走同一套代码,只换 Key / Domain;
    # API Key 建议用环境变量 E2B_API_KEY 注入,不要写进仓库文件。
    sandbox_e2b_api_key: str | None = None
    sandbox_e2b_domain: str | None = None  # 兼容厂商填其 E2B_DOMAIN,官方云留空
    # 模板名(镜像);空 = 优先用请求里的 image,再退化 base
    sandbox_e2b_template: str = ""
    # sandboxd(gVisor + Docker,部署在另一台云服务器):provider=sandboxd 时
    # 通过 REST 控制面管理远端沙箱、WebSocket 提供 PTY;地址/token 必填,
    # token 建议走 SANDBOXD_TOKEN 环境变量或部署侧密钥管理,不要写进仓库。
    sandbox_sandboxd_url: str = ""
    sandbox_sandboxd_token: str | None = None
    # 请求里 image 为 microsandbox 别名(如 python)时换成这个 Docker 镜像
    sandbox_sandboxd_image: str = "python:3.11-slim"
    # 控制面普通请求(创建/列目录/续期)超时;文件与 exec 另有更宽松的超时
    sandbox_sandboxd_request_timeout_seconds: float = 30.0
    # local provider 的沙箱根目录:每个沙箱一个 <root>/<name>/ 目录,
    # /workspace 映射到其中的 workspace/;默认落在服务目录下并被 gitignore
    sandbox_local_root: str = "./.sandboxes"
    # 持久 shell 会话(#2):会话表仍在进程内,但会话的"归属"落库
    # (shell_sessions 表),因此多副本时能给出明确 409、持有实例消失后还能
    # 由 provider 接管(e2b 的 pty.connect)。见 docs/design/persistent-shell.md。
    # 实例身份:显式配置优先;留空时用 "主机名:pid",同机多 worker 也不会
    # 同时认为自己持有同一个 pty。注意 pid 参与身份 → 重启后要等租约过期
    # (sandbox_shell_lease_seconds)才能接管自己重启前的会话。
    instance_id: str = ""
    sandbox_shell_lease_seconds: int = 120
    # 空闲 TTL 到点由 sweeper 关闭;关闭后的会话在内存里保留一段时间,
    # 让晚到的 SSE 连接仍能回放 ring buffer 与退出码。
    sandbox_shell_idle_seconds: int = 1800
    sandbox_shell_closed_retention_seconds: int = 300
    sandbox_shell_sweep_interval_seconds: int = 30
    sandbox_shell_ring_bytes: int = 65_536
    sandbox_shell_heartbeat_seconds: float = 15.0
    sandbox_shell_max_per_sandbox: int = 8
    sandbox_shell_max_subscribers: int = 32
