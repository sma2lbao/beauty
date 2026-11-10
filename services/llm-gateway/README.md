# llm-gateway

模型网关(阶段 A 骨架):全平台**唯一的** LLM/embedding 调用出口(架构 §5)。
任何服务不得直连模型提供商——路由、限流、配额、审计、出口检查都收敛在这里。

- `POST /v1/completions`:OpenAI 风格消息 → litellm 统一路由(DeepSeek/OpenAI/…,
  `model` 用 litellm 的 `provider/model` 格式如 `deepseek/deepseek-chat`),
  不传 `model` 走默认路由;失败统一映射 502 `llm.upstream_error`;
  模型解析与 fallback 见下文「模型路由与 fallback」;
- 支持 function-calling:`tools` 原样透传给 litellm,响应的 `tool_calls`
  归一化为 OpenAI 嵌套形状(`function.name/arguments`);消息支持
  `assistant.tool_calls` 与 `tool` 角色(`tool_call_id`),历史回传无需转换;
- 支持多模态:消息 `content` 可以是字符串,也可以是 `ContentPart` 列表
  (`{"type":"text"}` 与 `{"type":"image_url"}`),限制与错误码见下文「多模态请求」;
- 支持结构化输出:`response_format` 只接受 `{"type":"json_object"}` 或
  `{"type":"json_schema","json_schema":{…}}`,合法值原样透传 litellm,
  其余返回 400 `llm.invalid_response_format`(模型服务商不支持时按上游错误 502);
- `POST /v1/completions/stream`:同上的 **SSE 流式**入口(litellm `stream=True`),
  正文每帧 `data: {"delta": "..."}`,收尾帧 `{"done": true, "content": "<全文>",
"model", "usage", "fallbacks"}`;function-calling 的 `tool_calls` 按 `index` 装配后
  **只随收尾帧**整包带出(中途失败不会吐出半截 JSON;无工具调用时省略该键);
  失败帧 `{"error": {"code", "message"}}`——HTTP 头已按 200 发出,错误只能换载体
  (与 complete 的 502 同 code);对话据此"边输出边显示";
- `POST /v1/embeddings`:文本批量向量化(litellm `aembedding`,默认
  `EMBEDDING_MODEL`);条数与输入不一致按 502 `llm.embedding_error` 处理——
  knowledge-service 等经此向量化,不直连模型提供商(§5);
- `/healthz` `/readyz`:存活/就绪探针(默认模型已配置即就绪,提供商连通性在调用时校验);
- 观测:每次调用打 `llm_request`(provider/model/stream/消息数/角色/提示字数/tool 数/
  图片数/温度)与 `llm_response`(解析后的 model/finish_reason/token 用量/字符数/耗时/
  切换次数);指标 `llm_tokens_total{direction}`、`llm_request_duration_seconds`、
  `llm_fallbacks_total` 见 `GET /internal/metrics`。正文默认不落日志,联调要看去 `.env` 里开
  `LLM_GATEWAY__LOG_CONTENT=true`(messages 与响应各截断 2KB,含用户数据,别在生产开);
- 平台凭据显式入参给 litellm(`LLM_GATEWAY__API_KEY`/`API_BASE`),不依赖进程
  环境变量;账号自带密钥(BYOK)按下文向 account-service 解析,命中时代替平台 key;
  网关不落盘密钥(密钥库方案见架构 §10)。

## BYOK(按账号注入密钥)

网关仍是全平台唯一模型出口,但密钥可以归属账号:调用方带上账号标识,网关向
account-service 换该账号配置的提供商密钥(共享内部密钥保护),拿不到就回退平台 key
(`LLM_GATEWAY__API_KEY`)。BYOK 是**增强**而不是前置条件——它坏了平台照常出模型。

- **内部鉴权**:三个模型出口(`POST /v1/completions`、`POST /v1/completions/stream`、
  `POST /v1/embeddings`)在配置了 `INTERNAL_SECRET` 后必须带 `X-Internal-Secret`
  (常量时间比较),否则 401 `auth.internal_forbidden`;留空 = 本地 dev 放行。
  `GET /v1/models` 与探针免鉴权(只读元数据,不烧 token)。**生产必须配**
  `INTERNAL_SECRET`,否则内网任意进程都能调模型(尤其注意:配了它 BYOK 才会启用)。
- **账号头**:`X-Account-Id`(可选;缺失 = 不做 BYOK 查询,直接用平台 key)。值由上游
  服务从自己的 JWT claim 解出后透传;网关不自行验证账号归属,只把它原样转给 account。
- **解析**:`GET {ACCOUNT_BASE_URL}/v1/byok/{provider}/secret`(带内部密钥与账号头),
  取 `api_key`/`key_hint`/`key_id`;`provider` 取 litellm 模型串前缀
  (`deepseek/deepseek-chat` → `deepseek`),没有前缀时不猜(直接回退平台 key);
  embedding 命中 BYOK 时额外把 provider 显式传给 litellm。
- **缓存**:正缓存 `BYOK_CACHE_TTL_SECONDS`(默认 300s,也是密钥轮换生效延迟上限);
  404(账号没配该 provider)走更短的负缓存 `BYOK_NEGATIVE_CACHE_TTL_SECONDS`
  (默认 30s);非 200/超时/网络错误**不缓存**,下一请求立即重试;条目上限
  `BYOK_CACHE_MAX_ENTRIES`(LRU 淘汰);未命中解析用单把锁串行化,并发只穿透一次。
- **自定义模型目录**:账号可在 BYOK 记录里声明每个 provider 的自定义模型 id(`models`,如
  `["openai/gpt-4o-mini"]`)。网关按账号缓存这份清单(内部端点
  `GET {ACCOUNT_BASE_URL}/v1/byok/catalog`,双头认证,`BYOK_CATALOG_CACHE_TTL_SECONDS`,
  默认 60s,失败不缓存),用于 ① 把自定义模型并入 `GET /v1/models`(仅当请求同时带
  `X-Account-Id` 且内部密钥校验通过,`owned_by="byok"`)② 扩宽白名单(见下节)。
- **回退与失败语义**:`BYOK_ENABLED=false`、`INTERNAL_SECRET` 为空、无账号头、账号
  没配 key(404)、account 超时/5xx/坏响应——一律回退平台 key,不因 BYOK 故障拖垮
  模型调用;只有内部鉴权失败才拒绝(401)。
- **观测**:命中/缓存命中打 `byok_resolved` 审计日志(account/provider/key_hint/source,
  **不含 api_key**);指标 `llm_byok_lookups_total{status}`,status ∈
  disabled/negative/error/byok/cache_hit。
- **安全边界**:账号只提供密钥、不接管路由终点(`api_base` 仍是平台配置,避免账号把
  平台流量引到任意地址);密钥不进日志/指标标签、网关不落盘;共享密钥只在服务间
  传递,前端不经手。account-service 侧按 `X-Account-Id` 返回自己的密钥,越权由它兜底。

## 模型路由与 fallback

模型解析顺序:**请求显式 `model` > 网关默认 `DEFAULT_MODEL`**。`AVAILABLE_MODELS`
非空时是**白名单**:解析结果不在列表内 → 400 `llm.model_not_allowed`(默认模型
同样要过白名单,不允许隐式越权);留空则不限制。带账号身份时允许集合 = `AVAILABLE_MODELS`
∪ 该账号 BYOK 声明的自定义模型(目录查询失败/无账号 → 只按平台列表,不会因此 400)。
`GET /v1/models` 返回平台列表(空时只有默认模型),带合法内部头 + `X-Account-Id` 时
额外合并该账号自定义模型,不受 fallback 影响。

`FALLBACK_MODELS`(有序,默认空 = 关闭)是备用模型链,候选链 = 主模型 + 备用模型
(去重保序)。**只有临时性故障才切换**:限流 429、超时、连接失败,以及
408/429/500/502/503/504;参数/鉴权类 4xx 是调用方问题,不切换、直接 502。

- 非流式:切换成功时响应体带 `fallbacks`(`[{from_model,to_model,code,message}]`,
  `message` 是上游错误截断 500 字);没切过为 `null`。全部候选失败 → 502
  `llm.upstream_error`,details 带 `attempts`(已试模型 + 错误)与 `fallbacks`;
- 流式:只允许"开流前"切换——已有正文帧下发后失败只发 error 帧、绝不重试
  (否则调用方会收到重复正文);成功收尾的 done 帧带 `fallbacks`,中间帧不带;
- 每次切换打 `llm_fallback` warning 日志,并计数
  `llm_fallbacks_total{service,from_provider,to_provider,code}`;
- fallback 候选沿用同一把请求级密钥(BYOK 命中的账号 key),跨提供商备用链路
  需自行确认该密钥可用。

## 多模态请求

`messages[].content` 可以是 `str | list[ContentPart] | null`;片段仅支持
`{"type":"text","text":str}` 与 `{"type":"image_url","image_url":{"url":str}}`
(形状错误由 pydantic 返回 422)。入口层在**返回流式响应之前**做业务校验
(流式入口同样先校验,非法参数拿 400 而不是一帧 error),违反 → 400
`llm.invalid_message`:

- 所有消息文本总长 ≤ 100000 字(图片不计字数);
- 图片张数 ≤ `MAX_IMAGES_PER_REQUEST`(默认 8);
- 单图 URL 只接受 `data:image/{png,jpeg,webp};base64,…` 或 `http(s)://`,
  且 data URL 长度 ≤ `MAX_IMAGE_DATA_CHARS`(默认 8,000,000)。
- `LOG_CONTENT=true` 时图片在日志里只记 `[image]` 占位符,不落 base64 正文。

视觉能力:litellm 的模型能力表是随版本发布的静态数据,上游能吃图的新模型常常
漏标 `supports_vision`;漏标时 litellm 的 DeepSeek 适配层会把 `image_url` 片段
**折叠成纯文本、静默丢弃**——调用方以为图片送到了,模型却答"我没看到图片"。
所以网关注册一份显式声明:

- `VISION_MODELS`(默认 `["deepseek/deepseek-chat"]`,即本平台默认模型)
  列出的模型在启动时补写 `supports_vision`(只补这一个字段,价格/上下文沿用
  litellm 原值);BYOK / 账号自定义的视觉模型也要写进该清单;
- 带图请求落到不支持视觉的模型 → 400 `llm.model_no_vision`(details 带
  `model` 与 `vision_models`),早于开流,**绝不静默丢图**;
- 带图请求的 fallback 链只保留视觉候选:主模型 5xx 而备用模型是文本模型时如实
  502,而不是切过去给一个"没看到图片"的假答案。

阶段 B 补齐:账号级配额与限流、模型路由按区域切换(§9)、调用审计落库、
LiteLLM proxy 配置化(guardrails)。

命令(仓库根):`npx nx run llm-gateway:serve`(:8003)/ `npx nx test llm-gateway`

## Vercel 部署（导出约定）

Vercel Python runtime 只自动识别根级 `app.py`/`main.py` 等入口；本服务的 `app` 在
`llm_gateway/main.py`，因此由 `pyproject.toml` 的 `[tool.vercel] entrypoint` 显式声明：

- 入口：`llm_gateway.main:app`（与 `vercel.json` 的 `functions` key `llm_gateway/main.py` 对应）
- Python 版本：`.python-version` 固定 3.12
- 函数配置：`vercel.json` 设 `maxDuration`，并排除 `tests/`、本地 `*.db`、`__pycache__`
  等（Python 包体上限 500MB，Vercel 不做 tree-shaking）

已知阻塞：服务依赖同仓库 uv workspace 内的 `common` / `database` / `events`，而
Vercel 以 `services/llm-gateway` 为部署根、不包含仓库其他目录；真实部署前需先把这三个包
发布到私有索引或构建期 vendor。本次只覆盖无状态短请求服务，`agent-orchestrator` /
`workspace-service` 含常驻后台循环，按设计文档外置。详见
[serverless 部署方案](../../docs/design/serverless-deployment.md)。
