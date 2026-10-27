"""服务配置(env 前缀 AGENT_ORCHESTRATOR__,约定见 common.config)。"""

from common.config import BaseAppSettings
from pydantic_settings import SettingsConfigDict

#: stdio MCP 允许拉起的命令(包管理/解释器入口);租户可配置但默认收口在这里
DEFAULT_MCP_STDIO_COMMAND_ALLOWLIST: list[str] = [
    "npx",
    "uvx",
    "node",
    "python",
    "python3",
    "bunx",
]


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
    # 正文增量流(§6.3 token.delta.v1):run 执行时把模型正文边产出边落成事件,
    # 会话页据此显示实时进度。攒批口径是"字数或时长先到者",逐字写库会把
    # 一次 run 变成成百上千行事件。
    run_delta_streaming: bool = True
    run_delta_flush_chars: int = 240
    run_delta_flush_seconds: float = 0.25
    # 通知流(P1-9b):按租户轮询 run 状态变化并推送;扫描间隔与单次查询上限
    notifications_poll_interval_seconds: float = 2.0
    notifications_lookback_seconds: int = 120  # 只看最近 N 秒内有状态变更的 run
    notifications_batch_limit: int = 100
    # 交付物存储(§7 阶段 B):database = 内容留在 DB 列;filesystem = 落本地目录
    artifact_store: str = "database"
    artifact_root: str = "./.artifacts"
    # 外链文件抓取:工具/MCP 返回的 http(s) 文件链接(按扩展名白名单识别)下载成
    # 真实交付物,而不是只留一段 URL 文本。三个上限即内存/耗时边界:
    # 单文件字节、单个 URL 请求超时、一次最多抓几个(超出部分忽略)。
    artifact_download_enabled: bool = True
    artifact_download_max_bytes: int = 10 * 1024 * 1024
    artifact_download_timeout_seconds: float = 20.0
    artifact_download_max_files: int = 10
    # 附件本地最小版(M0-12):内容进 DB;单文件上限与单条消息附件数上限
    max_attachment_bytes: int = 10 * 1024 * 1024
    max_attachments_per_message: int = 5
    # 本地 worker(M0-04):run 的 plan/approve/resume/retry 入队执行。
    # 测试夹具显式关闭以保留同步语义。
    jobs_enabled: bool = True
    # 计划审批(§6.2):本 run 可用的工具里没有任何 DANGER 级工具时自动放行,
    # 有危险工具才中断等人工审批;执行阶段的危险动作确认闸不受此开关影响。
    plan_auto_approve: bool = True
    jobs_poll_interval_seconds: float = 0.5
    jobs_lease_seconds: int = 60
    jobs_max_attempts: int = 3
    # 中断超时(P0-2):awaiting_approval / awaiting_confirmation 停留超过阈值后
    # 自动按"拒绝"处理——审批超时 → blocked(可 resume);确认超时 → 跳过危险写
    # 继续验收。0 = 关闭该类超时;sweeper 仅在 jobs_enabled 且至少一类 > 0 时启动。
    approval_timeout_seconds: int = 0
    confirmation_timeout_seconds: int = 0
    # 交互提问超时(tool-service §10.6):awaiting_input 停留过久自动按 decline
    # 收场。默认 900(开启)——提问无人回答会让 run 永久挂起、checkpoint 堆积;
    # 审批/确认默认 0 是因为它们面向运维动作,暴露面不同。0 = 关闭。
    question_timeout_seconds: int = 900
    interrupt_sweep_interval_seconds: float = 15.0
    # Skill manifest 目录(M0 本地:仓库 skills/;为空时自动定位仓库根)
    skills_dir: str = ""
    # MCP Client(M1 本地版):允许租户配置第三方 MCP server
    mcp_enabled: bool = True
    mcp_secret: str = "dev-mcp-secret-change-me"
    mcp_timeout_seconds: float = 10.0
    # stdio 型 MCP(P2):在沙箱内拉起 MCP 进程,经 stdio JSON-RPC 调工具。
    # 需要 sandbox_enabled 同时为真(没有沙箱客户端时 test 端点明确报错、
    # tool_specs 跳过 stdio server);network=public 是 P2 的临时口径,
    # P4 再用出网 allowlist 收口。
    mcp_stdio_enabled: bool = False
    mcp_stdio_network: str = "public"
    mcp_stdio_sandbox_ttl_seconds: int = 1800
    mcp_stdio_python_image: str = "python"
    mcp_stdio_node_image: str = "node"
    mcp_stdio_command_allowlist: list[str] = list(DEFAULT_MCP_STDIO_COMMAND_ALLOWLIST)
    # 公开 MCP Server(M3 本地版):API Key 入口
    mcp_server_enabled: bool = True
    account_service_url: str = "http://127.0.0.1:8001"
    mcp_resource_url: str = "http://127.0.0.1:8002/mcp"
    # 远程 MCP 的浏览器 OAuth 授权(P1):云端 agent 不能自己开浏览器——用户在
    # 浏览器里完成授权码 + PKCE,服务端只在 BFF 同源回调里换 token(加密落库)。
    # redirect_uri 必须与 BFF 实际回调地址一致(DCR 注册时原样声明)。
    mcp_oauth_enabled: bool = True
    mcp_oauth_redirect_uri: str = "http://127.0.0.1:8007/api/mcp/oauth/callback"
    # state 签名密钥:留空回退 mcp_secret;独立配置便于轮换/多环境隔离
    mcp_oauth_state_secret: str = ""
    mcp_oauth_state_ttl_seconds: int = 600
    mcp_oauth_timeout_seconds: float = 10.0
    mcp_oauth_client_name: str = "beauty-moon agent"
    # 模型网关(架构 §5 唯一出口):enabled=false 时 plan/execute 走确定性桩
    llm_enabled: bool = False
    llm_gateway_url: str = "http://127.0.0.1:8003"
    llm_model: str | None = None  # None:走网关默认路由
    llm_timeout_seconds: float = 60.0
    # 网关调用重试(P0-7):429/5xx/连接错误按指数退避重试;4xx 业务错误不重试。
    # 流式只在"开流前"重试——增量一旦发出,重发会拼出重复正文。
    llm_retry_attempts: int = 2
    llm_retry_backoff_seconds: float = 1.5
    # P0-3 token 预算:单次执行(plan 或 approve 的一次图调用)允许的最大模型用量,
    # 0 = 不限制。用尽时该次执行 failed(可在新 run 里续做)。
    run_token_budget: int = 0
    # 工具(架构 §8):execute 走 function-calling 调各能力提供方(文档/知识库;
    # 代码类工具由 tool-service 提供,见下方的 tool_service_enabled)
    tools_enabled: bool = False
    tool_timeout_seconds: float = 30.0
    # 内置工具服务(docs/design/tool-service.md):开启后内置工具的目录与执行
    # 全部委托给 tool-service(:8008);关闭时退回进程内注册表(仅文档/知识库;
    # 代码类工具只由 tool-service 提供)。
    tool_service_enabled: bool = False
    tool_service_url: str = "http://127.0.0.1:8008"
    tool_service_timeout_seconds: float = 35.0
    # 技能提供方:未开启的能力不暴露给模型(能力即权限边界)
    documents_enabled: bool = False
    documents_root: str = "./.documents"
    documents_max_bytes: int = 200_000
    # 企业知识库(§9):search_knowledge 工具经 knowledge-service 检索
    knowledge_enabled: bool = False
    knowledge_service_url: str = "http://127.0.0.1:8006"
    knowledge_timeout_seconds: float = 30.0
    # 长期记忆(§6.7 记忆模块):跨会话保留的用户偏好/事实/项目笔记。
    # 与知识库的分工:知识库是"文档"检索(显式工具),记忆是"对话沉淀"(自动召回)。
    # 存储走 orchestration 自己的 memories 表(租户隔离 + 工作区归属),
    # recall 结果按相关性拼进系统提示,不是把整库塞给模型。
    memory_enabled: bool = True
    memory_recall_limit: int = 5
    memory_recall_max_chars: int = 2000
    # 程序性记忆(kind=procedure)常驻注入的字符预算;0 = 关闭常驻约定块。
    # 与召回互补:约定不按相关性过滤(否则模型不知道去查),但仍受预算约束。
    memory_convention_max_chars: int = 2000
    memory_max_content_chars: int = 8000
    memory_max_entries_per_tenant: int = 2000
    # 0 = 不过期(只按容量与手动删除治理);>0 时写入时算 expires_at
    memory_default_ttl_days: int = 0
    memory_embedding_dimensions: int = 256
    # 记忆二期(P2):自动抽取/近重复巩固/周期清理。抽取需要 LLM 且会把对话
    # 内容写进长期记忆(默认 tenant 级、跨工作区可见),属隐私敏感行为,**默认关**;
    # 显式开启后受 memory_extraction_min_messages 节流(每累计 N 条会话消息抽一次,
    # 0=关闭该调用方)。巩固按词面相似度合并近重复,pinned 永不删除;sweeper
    # 周期清理过期记忆。run 完成路径不依赖 outbox(提交后回调),见 memory_extraction。
    memory_auto_extract_enabled: bool = False
    memory_extraction_min_messages: int = 6
    memory_extraction_max_items: int = 5
    memory_extraction_max_chars: int = 1200
    memory_dedupe_threshold: float = 0.9
    memory_consolidation_enabled: bool = True
    memory_consolidation_threshold: float = 0.92
    # 周期清理/巩固间隔秒;<=0 = 不起后台 sweeper(测试里钉 0 关掉)。
    memory_sweep_interval_seconds: float = 3600.0
    # 语义嵌入(P2):local = 进程内 256 维确定哈希(零网络);gateway 走
    # llm-gateway 的 /v1/embeddings(默认模型 bge-m3),失败自动回退 local。
    memory_embedding_provider: str = "local"
    memory_embedding_model: str = "bge-m3"
    memory_embedding_timeout_seconds: float = 10.0
    # 短期上下文压缩(§6.7):早期轮次折叠成摘要,原文仍保留(审计/回放不受影响)。
    # 阈值按估算 token 计(中文约 1 字/token),超过后把"最老的一批"折叠进摘要。
    context_compaction_enabled: bool = True
    context_compact_threshold_tokens: int = 6000
    context_keep_recent_messages: int = 8
    context_summary_max_chars: int = 2000
    # 上下文工程管线(P1-F,§7.1):把「AGENTS.md 注入 + 技能卡片 + 记忆段 +
    # 短期压缩 + 大结果 offload」收敛成一条可插拔管线。默认全开;缺沙箱/
    # 缺模块时各段自行降级(不阻断 run),总开关关闭时退回旧提示词装配。
    context_pipeline_enabled: bool = True
    # 分段注入顺序;值域 agents_md/skills/memory,未知段忽略(空表回退默认序)
    context_section_order: list[str] = ["agents_md", "skills", "memory"]
    # AGENTS.md(§6.4):从沙箱工作区按相对深度发现并注入,多文件合并限预算
    context_agents_md_enabled: bool = True
    context_agents_md_names: list[str] = ["AGENTS.md"]
    # 全部命中文件合并后的注入预算(超出按文件顺序截断,末尾标注)
    context_agents_md_max_chars: int = 4000
    # 单文件注入预算:防止一个超大 AGENTS.md 挤掉后面的文件
    context_agents_md_file_max_chars: int = 2000
    # 发现范围:相对工作区根的最大目录深度与最多文件数(防遍历失控)
    context_agents_md_max_depth: int = 2
    context_agents_md_max_files: int = 8
    # 大工具结果 offload(§6.6):超过阈值时完整内容写入沙箱工作区文件,
    # 消息里只留引用信封 + 预览;模型需要细节时用 read_offloaded 分段回读。
    # 阈值取 8000:低于工具服务 25K 的硬上限,覆盖"长但还能塞进上下文"区间。
    context_offload_enabled: bool = True
    context_offload_threshold_chars: int = 8000
    context_offload_preview_chars: int = 1200
    # offload 落盘目录(相对沙箱工作区根,只允许在其下读写)
    context_offload_dir: str = ".agent/offload"
    # 单次落盘硬上限:超出部分截断并在信封里标注(防撑爆沙箱配额)
    context_offload_max_chars: int = 200_000
    # SKILL.md 渐进加载(§6.5):系统提示常驻短卡片,正文由 load_skill 分页拉取
    context_skill_loading_enabled: bool = True
    # 卡片预算(~100 token):保证多个技能同时可见仍不挤占正文上下文
    context_skill_card_max_chars: int = 400
    # load_skill 单页正文大小(过小会多次往返,过大等于没做渐进加载)
    context_skill_body_page_chars: int = 4000
    # 上下文相关沙箱操作(AGENTS.md 发现/offload 读写)复用沙箱的 TTL(秒)
    context_sandbox_ttl_seconds: int = 900
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
    # 沙箱(接入 workspace-service 内部 /v1/sandboxes;P1 仅基础设施,
    # 不向模型暴露工具)。enabled=false 时不起客户端,行为与之前完全一致
    sandbox_enabled: bool = False
    sandbox_service_url: str = "http://127.0.0.1:8005"
    sandbox_timeout_seconds: float = 60.0
    # 上传技能包执行(P3-C:skill_run 工具在沙箱内跑 entrypoint)。
    # 需要 sandbox_enabled + workspace_enabled 同时为真;sandbox 内网络默认 deny,
    # 由 run 快照里的 skill_network 决定(上传校验只允许 deny/public)。
    skill_execution_enabled: bool = False
    skill_python_image: str = "python"
    skill_node_image: str = "node"
    skill_sandbox_ttl_seconds: int = 900
    skill_exec_timeout_seconds: int = 600
    skill_max_output_files: int = 10
    skill_max_output_bytes: int = 100_000
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
