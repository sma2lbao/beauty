"""tool-service 配置。"""

from common.service_kit import ServiceSettings
from pydantic_settings import SettingsConfigDict


class Settings(ServiceSettings):
    """工具服务配置;环境变量前缀为 TOOL__(双下划线)。

    internal_secret 与 account-service 共享,用于换发账号 JWT 后调用
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
    # 观测:trace 里是否记录工具参数原文(默认关,只记元数据)。
    log_content: bool = False
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
    # P1-L:web_search 由服务端出站调用搜索提供方(tavily / brave),模型只传
    # query 与 max_results;提供方与密钥由部署方配置。会发起外部 I/O,同样
    # 属于 NETWORK_SWITCHES:测试默认钉成 False(零网络)。
    search_enabled: bool = False
    search_provider: str = "tavily"
    #: 提供方 API key;为空时工具不可见(与 fetch_enabled 同口径)。
    search_api_key: str = ""
    #: 覆盖提供方基地址(必须 https;空 = 用提供方默认地址)。
    search_base_url: str = ""
    search_timeout_seconds: float = 10.0
    search_default_results: int = 5
    search_max_results: int = 10
    search_max_bytes: int = 1024 * 1024
    # 连接器(C2.4):tool-service 是连接器工具的唯一执行面。catalog 从
    # connector-service 的内部端点拉取(按账号 TTL 缓存),执行则 POST 到
    # /v1/connectors/{provider}/execute。internal_secret 为空表示未启用连接器,
    # /v1/tools 只返回内置工具(与 fetch/exec 的开关口径一致)。
    connector_base_url: str = "http://127.0.0.1:8004"
    connector_internal_secret: str = ""
    connector_catalog_ttl_seconds: float = 30.0
    # P1-L:exec 在工作区沙箱内执行 shell 命令(destructive,需人工确认)。
    # 一旦出现在启用目录里,orchestrator 的 plan 闸会对所有 run 触发确认,
    # 因此默认关闭;开启前请确认沙箱用 microsandbox + network=deny。
    exec_enabled: bool = False
    # P5:run_code 在沙箱内执行 Python 3 代码(destructive,需人工确认)。
    # 与 exec 同款风险:一旦出现在启用目录里,orchestrator 的 plan 闸会对所有
    # run 触发确认,因此默认关闭;开启前请确认沙箱用 microsandbox + network=deny。
    run_code_enabled: bool = False
    # P1-L/M2:job_* 三项后台作业工具(启动/读输出/终止)。job_start 是
    # DESTRUCTIVE,一旦启用会让 orchestrator 的 plan 闸对所有 run 触发确认
    # (与 exec/run_code 同款),因此默认关闭;作业跑在既有工作区沙箱里,
    # 默认无网络,沙箱被回收时作业状态记为 lost(不保证 at-least-once)。
    jobs_enabled: bool = False
    #: 单个作业最长运行秒数;超过后在下一次 job_output/job_kill 时标记 timeout 并终止。
    job_max_runtime_seconds: int = 600
    #: 单账号并发 running 作业上限;超限 job_start 回 tool.budget_exceeded。
    job_max_concurrent_per_account: int = 5
    #: job_output 未传 tail_chars 时的默认尾部字符数与上限。
    job_output_default_chars: int = 8000
    job_output_max_chars: int = 20000
    #: 作业目录与终态记录的保留小时数;job_start 时 best-effort 清理更早的。
    job_retention_hours: int = 24
