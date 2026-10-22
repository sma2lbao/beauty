"""配置基类。

约定(docs/architecture.md §11):配置一律 pydantic-settings,环境变量前缀
= 服务名大写下划线,如 AGENT_ORCHESTRATOR__DATABASE_URL;
本地开发用项目根 ``.env``(不提交),生产用环境变量注入。
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseAppSettings(BaseSettings):
    """服务配置基类:子类设置自己的 env_prefix 与 service_name。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "unknown"
    env: str = Field(default="dev", pattern="^(dev|staging|prod)$")
    log_level: str = "INFO"
    # 观测开关(docs/observability.md §4/§6):access_log_enabled 关掉每请求
    # 一行访问日志;slow_request_ms 慢请求阈值;log_json 显式覆盖渲染器
    # (None = staging/prod 用 JSON,其余 Console)。
    access_log_enabled: bool = True
    slow_request_ms: int = 1500
    log_json: bool | None = None
    # Sentry(P1,docs/observability.md §8):DSN 为空即完全关闭;release 建议
    # 由部署侧注入镜像 tag 或 git sha;traces_sample_rate 默认 0(错误聚合优先,
    # 性能追踪等指标层稳定后再开)。
    sentry_dsn: str | None = None
    sentry_release: str | None = None
    sentry_traces_sample_rate: float = 0.0
    # 指标(P2,docs/observability.md §9):进程内 Prometheus 计数,
    # 只在 /internal/metrics 暴露,不进 /api/v1 公共契约。
    metrics_enabled: bool = True
    # 指标端点可选访问令牌:配了就先比 `X-Metrics-Token`(或 Bearer),不符返回 404;
    # 未配时仅靠网络边界(内网抓取)——公网入口(BFF)建议配一个。
    metrics_token: str | None = None
    # 追踪(P2,docs/observability.md §9):OTel 分布式追踪。没配 OTLP_ENDPOINT
    # 时不挂导出器(零网络出口),span 只用于日志 trace_id 串联与本地排查;
    # trace_sample_ratio 走父级采样(入口决定下游是否采样,默认 0.1)。
    tracing_enabled: bool = True
    otlp_endpoint: str | None = None
    otlp_headers: dict[str, str] | None = None
    trace_sample_ratio: float = 0.1

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"
