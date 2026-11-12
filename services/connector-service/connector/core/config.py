"""服务配置(env 前缀 CONNECTOR__,公共字段见 common.service_kit)。

连接器平台:provider 由代码 manifest 注册(见 connector.providers),配置只负责
「启用哪些 provider」与各自的应用凭证。当前仓库没有内置 provider(Notion 等产品
改走 MCP 接入);新增 provider 后由它自己声明配置字段。未配置应用凭证时 provider
仍出现在目录里但标记未配置。
"""

from __future__ import annotations

from common.service_kit import ServiceSettings
from pydantic_settings import SettingsConfigDict


class Settings(ServiceSettings):
    model_config = SettingsConfigDict(
        env_prefix="CONNECTOR__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "connector-service"
    # 本地默认连 deploy/docker-compose.yml 的 postgres:16(每服务独立库);
    # 测试走临时 SQLite、离线场景用 SQLite 覆盖,生产用 CONNECTOR__DATABASE_URL 覆盖
    database_url: str = "postgresql+asyncpg://beauty:beauty@localhost:5432/beauty_connector"
    # 连接凭证应用层加密密钥(生产必须换随机长密钥/KMS)
    credential_secret: str = "dev-connector-secret-change-me"

    # ── 连接器平台 ────────────────────────────────────────────────────────
    # 启用的 provider(逗号分隔);代码里没有对应 manifest 的名字会被忽略。
    # 当前仓库没有内置 provider,默认空;新增 provider 后在此登记 key。
    providers_enabled: str = ""
    # 服务间内部调用鉴权(X-Internal-Secret);留空 = 内部端点整体关闭(401)。
    internal_secret: str = ""
    # OAuth 回调基址:redirect_uri = {base}/v1/oauth/{provider}/callback,
    # 必须与 provider 后台登记的地址逐字一致。
    oauth_redirect_base: str = "http://127.0.0.1:8004"
    # 授权结束 302 回前端的地址(附 provider/status/code 查询参数)。
    oauth_success_redirect: str = "http://127.0.0.1:5173/connectors"
    # 一次性授权会话 TTL(秒):超时后 nonce 作废,必须重新发起授权。
    oauth_session_ttl_seconds: int = 600
    # 提前刷新余量(秒):access token 剩余寿命小于该值时惰性刷新。
    refresh_margin_seconds: int = 300
    request_timeout_seconds: float = 10.0

    # ── 事务性 outbox(C1.10)────────────────────────────────────────────
    # 开启后连接生命周期事件与状态变更同事务写入 outbox_events,再由后台
    # dispatcher 投递;关闭时降级为结构化日志(单进程/本地开发默认)。
    outbox_enabled: bool = False
    outbox_dispatch_interval_seconds: float = 1.0
    outbox_batch_size: int = 50
    outbox_max_attempts: int = 5

    # ── 24h 刷新巡检(§5.5)───────────────────────────────────────────────
    # 生产默认开启;测试/单进程部署可关。巡检跨账号扫 active+enabled 且
    # 「从未成功刷新」或「上次刷新早于 refresh_stale_seconds」的连接。
    refresh_patrol_enabled: bool = True
    refresh_patrol_interval_seconds: int = 3600
    refresh_stale_seconds: int = 86400

    @property
    def provider_names(self) -> tuple[str, ...]:
        """启用的 provider 名(去空、去重、保序)。"""
        parts = (part.strip().lower() for part in self.providers_enabled.split(","))
        return tuple(dict.fromkeys(part for part in parts if part))
