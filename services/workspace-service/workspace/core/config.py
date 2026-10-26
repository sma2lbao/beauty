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
    database_url: str = "sqlite+aiosqlite:///./workspace.db"
    auto_create_tables: bool = False  # dev/test 直建表;生产走 alembic
    # 交付物内容存储(§7):database = DB 列;filesystem = 本地对象目录
    artifact_store: str = "filesystem"
    artifact_root: str = "./.artifacts"
    # 通用二进制对象(P3:skill 包 zip)单对象字节上限;超限返回 400 object.too_large
    object_max_bytes: int = 20 * 1024 * 1024
    default_workspace_name: str = "默认工作区"
    # 鉴权(架构 §10):默认开启,与其他服务同一套 token→租户约定(自建 IdP)
    auth_enabled: bool = True
    auth_issuer: str = "http://127.0.0.1:8001"  # 自建 IdP:account-service
    auth_audience: str | None = "beauty-api"  # 与 account 的 TOKEN_AUDIENCE 一致
    auth_jwks_url: str | None = None
    auth_tenant_claim: str = "tenant_id"
    # 沙箱(§7 阶段 B / P1):provider=microsandbox 用真 microVM;
    # provider=local 直接在宿主目录/宿主进程上跑(无隔离,仅本地单租户可信环境);
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
    # microsandbox runtime home(空 = 用 SDK 默认 ~/.microsandbox);
    # 部署时必须指向可写目录,且 runtime 需要能访问 OCI registry
    sandbox_msb_home: str = ""
    # local provider 的沙箱根目录:每个沙箱一个 <root>/<name>/ 目录,
    # /workspace 映射到其中的 workspace/;默认落在服务目录下并被 gitignore
    sandbox_local_root: str = "./.sandboxes"
