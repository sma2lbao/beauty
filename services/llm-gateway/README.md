# llm-gateway

模型网关(阶段 A 骨架):全平台**唯一的** LLM/embedding 调用出口(架构 §5)。
任何服务不得直连模型提供商——路由、限流、配额、审计、出口检查都收敛在这里。

- `POST /v1/completions`:OpenAI 风格消息 → litellm 统一路由(DeepSeek/OpenAI/…,
  `model` 用 litellm 的 `provider/model` 格式如 `deepseek/deepseek-chat`),
  不传 `model` 走默认路由;失败统一映射 502 `llm.upstream_error`;
- 支持 function-calling:`tools` 原样透传给 litellm,响应的 `tool_calls`
  归一化为 OpenAI 嵌套形状(`function.name/arguments`);消息支持
  `assistant.tool_calls` 与 `tool` 角色(`tool_call_id`),历史回传无需转换;
- `POST /v1/completions/stream`:同上的 **SSE 流式**入口(litellm `stream=True`),
  正文每帧 `data: {"delta": "..."}`,收尾帧 `{"done": true, "content": "<全文>",
  "model", "usage"}`;function-calling 的 `tool_calls` 按 `index` 装配后**只随收尾帧**
  整包带出(中途失败不会吐出半截 JSON;无工具调用时省略该键);
  失败帧 `{"error": {"code", "message"}}`——HTTP 头已按 200 发出,错误只能换载体
  (与 complete 的 502 同 code);对话据此"边输出边显示";
- `POST /v1/embeddings`:文本批量向量化(litellm `aembedding`,默认
  `EMBEDDING_MODEL`);条数与输入不一致按 502 `llm.embedding_error` 处理——
  knowledge-service 等经此向量化,不直连模型提供商(§5);
- `/healthz` `/readyz`:存活/就绪探针(默认模型已配置即就绪,提供商连通性在调用时校验);
- 观测:每次调用打 `llm_request`(provider/model/stream/消息数/角色/提示字数/tool 数/温度)
  与 `llm_response`(解析后的 model/finish_reason/token 用量/字符数/耗时);
  指标 `llm_tokens_total{direction}` 与 `llm_request_duration_seconds` 见
  `GET /internal/metrics`。正文默认不落日志,联调要看去 `.env` 里开
  `LLM_GATEWAY__LOG_CONTENT=true`(messages 与响应各截断 2KB,含用户数据,别在生产开);
- 平台凭据显式入参给 litellm(`LLM_GATEWAY__API_KEY`/`API_BASE`),不依赖进程
  环境变量;租户自带密钥(BYOK)按下文向 account-service 解析,命中时代替平台 key;
  网关不落盘密钥(密钥库方案见架构 §10)。

## BYOK(按租户注入密钥)

网关仍是全平台唯一模型出口,但密钥可以归属租户:调用方带上租户标识,网关向
account-service 换该租户配置的提供商密钥(共享内部密钥保护),拿不到就回退平台 key
(`LLM_GATEWAY__API_KEY`)。BYOK 是**增强**而不是前置条件——它坏了平台照常出模型。

- **内部鉴权**:三个模型出口(`POST /v1/completions`、`POST /v1/completions/stream`、
  `POST /v1/embeddings`)在配置了 `INTERNAL_SECRET` 后必须带 `X-Internal-Secret`
  (常量时间比较),否则 401 `auth.internal_forbidden`;留空 = 本地 dev 放行。
  `GET /v1/models` 与探针免鉴权(只读元数据,不烧 token)。**生产必须配**
  `INTERNAL_SECRET`,否则内网任意进程都能调模型(尤其注意:配了它 BYOK 才会启用)。
- **租户头**:`X-Tenant-Id`(可选;缺失 = 不做 BYOK 查询,直接用平台 key)。值由上游
  服务从自己的 JWT claim 解出后透传;网关不自行验证租户归属,只把它原样转给 account。
- **解析**:`GET {ACCOUNT_BASE_URL}/v1/byok/{provider}/secret`(带内部密钥与租户头),
  取 `api_key`/`key_hint`/`key_id`;`provider` 取 litellm 模型串前缀
  (`deepseek/deepseek-chat` → `deepseek`),没有前缀时不猜(直接回退平台 key);
  embedding 命中 BYOK 时额外把 provider 显式传给 litellm。
- **缓存**:正缓存 `BYOK_CACHE_TTL_SECONDS`(默认 300s,也是密钥轮换生效延迟上限);
  404(租户没配该 provider)走更短的负缓存 `BYOK_NEGATIVE_CACHE_TTL_SECONDS`
  (默认 30s);非 200/超时/网络错误**不缓存**,下一请求立即重试;条目上限
  `BYOK_CACHE_MAX_ENTRIES`(LRU 淘汰);未命中解析用单把锁串行化,并发只穿透一次。
- **回退与失败语义**:`BYOK_ENABLED=false`、`INTERNAL_SECRET` 为空、无租户头、租户
  没配 key(404)、account 超时/5xx/坏响应——一律回退平台 key,不因 BYOK 故障拖垮
  模型调用;只有内部鉴权失败才拒绝(401)。
- **观测**:命中/缓存命中打 `byok_resolved` 审计日志(tenant/provider/key_hint/source,
  **不含 api_key**);指标 `llm_byok_lookups_total{status}`,status ∈
  disabled/negative/error/byok/cache_hit。
- **安全边界**:租户只提供密钥、不接管路由终点(`api_base` 仍是平台配置,避免租户把
  平台流量引到任意地址);密钥不进日志/指标标签、网关不落盘;共享密钥只在服务间
  传递,前端不经手。account-service 侧按 `X-Tenant-Id` 返回自己的密钥,越权由它兜底。

阶段 B 补齐:租户级配额与限流、模型路由按区域切换(§9)、调用审计落库、
LiteLLM proxy 配置化(fallback/guardrails)。

命令(仓库根):`npx nx run llm-gateway:serve`(:8003)/ `npx nx test llm-gateway`
