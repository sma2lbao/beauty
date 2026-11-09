# Langfuse 观测接入（trace / 成本 / 评估）

> 状态：**Phase 1（common 基座）、Phase 2（orchestrator 节点 span、llm-gateway generation span）与 run 根 span / 工具 span 已实施；当前接入 Langfuse Cloud（EU 区域），8 个服务的本地 `.env` 已配置并对 Cloud 实测通过**（2026-11-09）。
> 已用真实 OTLP 导出验证：Cloud 侧能看到 trace/observation、observation type 正确（SPAN / GENERATION）、token 用量（`usageDetails`）、baggage 传播的 user/session 生效；**deepseek 成本已由网关 ingested cost 补齐**（litellm 本地价目，见 §9.1）。
> 状态：Phase 3 全部完成——导出前脱敏、缓存 token 成本口径、eval/scores、留存与容量策略（§9.3）已落地或已定策略；Web 端「在 Langfuse 查看」深链已完成（`runs.trace_id`）；尾部采样已提供可选的 OTel Collector 配置（错误/慢 trace 全留 + 10% 基线，见 §9.2），本机无 Docker 未实测启动。`apps/web` 前端 Sentry 也已接入，并通过 `traceparent` 与服务端共享同一 trace_id（脱敏/采集收紧细节见 [docs/observability.md](../observability.md) §8）。
> 本文是 Langfuse 接入的单一真源；日志/指标/Sentry 的既有规范仍在 [docs/observability.md](../observability.md)，两者不冲突。

---

## 1. 目标与非目标

**目标**

- 一次 agent run 就是一条 trace：orchestrator 的节点 span 作骨架，llm-gateway 的 generation span 作叶子，能按 run / 会话 / 账号回放。
- 每次模型调用带 model、token 用量与耗时，成本可 ingested（网关实测价目）或按 Langfuse 价目表推算；Cloud 默认落在 EU 区域（`https://cloud.langfuse.com`），需要数据不出内网时切自托管 `observability` profile（§8.2）。
- 保留现有 OTel 基座（`common.tracing`）：不引入 langfuse Python SDK，不抢占全局 `TracerProvider`，后端可替换。

**非目标**

- 不用 Langfuse 替代 Prometheus 做聚合告警（指标永不采样，Langfuse 不承担 SLO 告警）；
- 不用 Langfuse 替代 Sentry 做异常聚合（栈与 fingerprint 仍在 Sentry）；
- 不把日志搬进 Langfuse（日志仍是排障主线，trace 只补因果与成本）。

---

## 2. 分工

| 关注点                   | 工具                              | 说明                                                  |
| ------------------------ | --------------------------------- | ----------------------------------------------------- |
| 单次 run 的因果链、成本  | **Langfuse**                      | trace/observation、token 与 cost、session、评估与标注 |
| 异常栈、影响面、告警     | Sentry（`common.sentry`）         | 与 Langfuse 共享 `trace_id`，两边可互跳               |
| RED 指标、队列与上游健康 | Prometheus（`/internal/metrics`） | 聚合口径唯一来源；标签只放低基数字段                  |
| 请求/任务生命周期与审计  | structlog JSON（`request_id`）    | 全量、便宜、可长期留存；Langfuse 只留采样后的 trace   |

---

## 3. 架构

```
浏览器 / 客户端
   │  X-Request-ID + traceparent
   ▼
api-gateway ──► agent-orchestrator ──► llm-gateway ──► 模型提供商
   │                  │  │
   │                  │  └─► tool-service / workspace / knowledge / connector
   │                  │
   └──────── OTLP/HTTP（trace）+ 结构化日志 + /internal/metrics ────────┐
                                                                       ▼
                        默认: Langfuse Cloud（EU，https://cloud.langfuse.com）
                        可选: 自托管观测 profile（本地/离线，四件套见 §8.2）
```

写入端只有 OTel：`common.tracing.init_tracing` 建进程级 `TracerProvider`，Langfuse 与通用 OTLP 二选一（Langfuse 优先），导出到 `POST /api/public/otel/v1/traces`，Basic 认证 + `x-langfuse-ingestion-version: 4`。**不依赖 Langfuse SDK**，因此换后端（Tempo/Jaeger/Phoenix）只改配置；Cloud 与自托管只差 `langfuse_host`（Cloud：`https://cloud.langfuse.com`，EU；另有 US/JP/HIPAA 区域）。

---

## 4. 代码落点

### 4.1 `packages/common/common/langfuse.py`（基座）

- 属性常量：`langfuse.trace.*`（name/tags/metadata/user.id/session.id/release/version）、`langfuse.observation.*`（type/input/output/model/usage_details/cost_details/level/status_message/completion_start_time/metadata）、`gen_ai.*`（operation.name/provider.name/request.model/response.model/response.id/conversation.id/agent.name/tool.name/tool.call.id/usage.\*）。
- `is_enabled(settings)`：`langfuse_enabled` 且 public/secret key 齐全；缺一个就完全不上报。
- `export_target(settings)`：返回 `(endpoint, headers)`；endpoint = `langfuse_host + /api/public/otel/v1/traces`，headers = `Authorization: Basic base64(pk:sk)` + 摄取版本头。
- `trace_attributes(...)` / `observation_attributes(...)` / `gen_ai_usage_attributes(...)`：把业务语义翻译成上述键；`tags` 序列化成 JSON 字符串，`usage`/`cost` 用 Langfuse 文档要求的 JSON 字符串。
- `set_trace_attributes(span, ...)`：直接写当前 span；`langfuse_scope(**kwargs)`：写当前 span + 把同名属性塞进 OTel Baggage，退出时 detach。
- `LangfuseBaggageSpanProcessor`：on_start 从父 Context 的 Baggage 复制 `langfuse.` 前缀属性。**这是关键**：Langfuse 要求 user/session/tags 出现在每条 observation 上，否则过滤聚合会漏；HTTP span、gateway span 由该 processor 自动继承，业务代码不用逐层重设。
- `json_value(value)`：统一 JSON 编码（`ensure_ascii=False`、`default=str`、紧凑分隔符），保证非字符串值可入库。

### 4.2 `packages/common/common/tracing.py`

- Resource 增加 `deployment.environment.name`（Langfuse 用它做 environment 过滤）。
- `_build_provider`：启用 Langfuse 时用 `export_target` 建 `OTLPSpanExporter` 并挂 `LangfuseBaggageSpanProcessor`；未启用时退回 `<SERVICE>__OTLP_ENDPOINT` 的通用 OTLP 分支；两者都没有则只留进程内 span（零网络出口）。
- 测试注入 exporter 时（单测）保持原有 SimpleSpanProcessor 路径。

### 4.3 `llm-gateway/services/generation_trace.py`

- `generation_span(settings, data, model, *, stream)`：`chat {model}` 作 span 名，`langfuse.observation.type=generation`，`gen_ai.operation.name=chat`、`gen_ai.provider.name`、`gen_ai.request.model`；`LOG_CONTENT=true` 时才写 input（messages 截断预览）。
- `mark_completion_start`：首个流式 delta 到达时写 `langfuse.observation.completion_start_time`，Langfuse 据此算 TTFT。
- `finish_generation`：写 `gen_ai.response.model`、`gen_ai.usage.*`（input/output/total，含 cache/reasoning 可选位）、fallback 次数；`LOG_CONTENT=true` 时写 output。
- `record_error` / `record_stream_error`：`record_exception` + `Status(ERROR)` + observation level/message（截 500 字符）。流式错误不吞异常，照旧 `raise`/`yield error`。
- 业务口径不变：指标、`llm_response` 日志、fallback 链路原样保留。

### 4.4 `agent-orchestrator/graph/steps.py`

- `instrument_node` 的每个图节点包一层 `start_as_current_span(name)`，把 `run {run_id}` 作 trace.name、`account_id` 作 user.id、`agent-orchestrator` 作 tag，并把 run/node/phase/attempt/round/step_id 放进 observation metadata。
- 失败时写 ERROR level + status message 并 `set_status(ERROR)`，再按原逻辑抛给上层（run 失败事件、job 重试不变）。
- 既有的 `run.step.v1` 投影与 SSE 完全不动：OTel span 是旁路观测，不是业务状态。

---

## 5. 配置

`BaseAppSettings` 新增（各服务自动派生 `<SERVICE>__` 前缀）：

| 字段                      | 默认值                  | 说明                                                                                                                  |
| ------------------------- | ----------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `langfuse_enabled`        | `false`                 | 总开关；关闭时零初始化、零出口                                                                                        |
| `langfuse_host`           | `http://localhost:3000` | Langfuse 地址；Cloud 用 `https://cloud.langfuse.com`，自托管 compose 内为 `http://langfuse-web:3000`                  |
| `langfuse_public_key`     | `null`                  | 项目公钥 `pk-lf-*`                                                                                                    |
| `langfuse_secret_key`     | `null`                  | 项目私钥 `sk-lf-*`；只在服务端注入，禁止进前端或日志                                                                  |
| `otlp_collector_endpoint` | `null`                  | 非空则 trace 先发 OTel Collector（尾部采样，见 §9.2），优先级高于 Langfuse 直连；走它时 `trace_sample_ratio` 必须 1.0 |

### 5.1 当前默认：Langfuse Cloud（已配置到 8 个服务）

8 个服务的本地 `.env` 已写入同一段（`.env` 被 git 忽略；`.env.example` 里有留空模板）。**所有服务共用一个 Langfuse 项目密钥**——跨服务 trace 才串得起来。

```sh
# 前缀按服务替换：ACCOUNT / AGENT_ORCHESTRATOR / API_GATEWAY / CONNECTOR /
# KNOWLEDGE / LLM_GATEWAY / TOOL / WORKSPACE
<SERVICE>__LANGFUSE_ENABLED=true
<SERVICE>__LANGFUSE_HOST=https://cloud.langfuse.com
<SERVICE>__LANGFUSE_PUBLIC_KEY=pk-lf-...        # Cloud 项目 → Settings → API Keys
<SERVICE>__LANGFUSE_SECRET_KEY=sk-lf-...        # 只在服务端；不进前端/日志/git
<SERVICE>__TRACE_SAMPLE_RATIO=1.0               # 本地全采；生产按 §9 调到 0.1~0.2
# 可选：走 OTel Collector 尾部采样（§9.2），此时上面必须保持 1.0
# <SERVICE>__OTLP_COLLECTOR_ENDPOINT=http://otel-collector:4318/v1/traces
```

开关语义：`langfuse_enabled && public_key && secret_key` 三者齐全才会初始化导出，缺一项等于关闭（零网络）。公钥前缀 `pk-lf-`（可公开、可给前端）、私钥 `sk-lf-`（等同项目写权限，泄露即换）。

内容开关：`<SERVICE>__LOG_CONTENT`（仅 llm-gateway 有）默认 false，只记角色、条数、chars 等元数据；置 true 才会把 prompt/补全正文写进 generation 的 input/output，此时该 trace 视为含敏感内容（见 §9 脱敏）。

### 5.2 自托管（可选，见 §8.2）

字段与 Cloud 完全相同，只改地址与密钥：`langfuse_host=http://langfuse-web:3000`（compose 服务名），密钥用观测 profile 预建的 `pk-lf-beauty` / `sk-lf-beauty`。

---

## 6. span 模型（已实施部分）

| span                            | 来源                     | `langfuse.observation.type` | 关键属性                                                                                                        |
| ------------------------------- | ------------------------ | --------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `POST /api/v1/runs` 等 HTTP     | FastAPI 原生 telemetry   | `span`（默认）              | http.route、http.status_code；自动继承 baggage 里的 trace/user/session                                          |
| `run {run_id}`                  | `services/run_trace.py`  | `agent`                     | run_id、attempts、agent_name、workspace_id；trace 名/session/user 在这里定死                                    |
| 图节点：`plan`/`act`/`observe`… | `graph/steps.py`         | `span`                      | run_id、node、phase、attempt、round、step_id；失败写 ERROR                                                      |
| `chat {model}`（非流式）        | `generation_trace.py`    | `generation`                | request/response model、usage、fallback_count、input/output                                                     |
| `chat {model}`（流式）          | 同上                     | `generation`                | 同上 + completion_start_time（TTFT）                                                                            |
| `execute_tool {name}`           | `execution/tool_span.py` | `tool`                      | tool.name、tool.call.id、risk、decision、duration_ms、result_chars、paths、sandbox_id；失败 WARNING，异常 ERROR |

run 根 span 的 trace_id 会回写到 `runs.trace_id`（session_scope 提交）；一次 run 有多次执行（首次、approve、resume）时只保留**最近一次**。控制台 run 详情据此显示「在 Langfuse 查看」，跳 `${VITE_LANGFUSE_BASE_URL:-https://cloud.langfuse.com}/trace/<trace_id>`；地址由 `apps/web` 的构建期变量 `VITE_LANGFUSE_BASE_URL` 决定，自托管时改成内网控制台地址。

工具 span 的 `input` 默认空——参数原文与结果都属敏感内容，只在 `TOOL__LOG_CONTENT=true` 时写截断后的参数；审计表始终是完整事实源。跨进程续接已验证：FastAPI 原生 telemetry 从入站 `traceparent` 提取父上下文（入站 trace-id 与导出 span 的 trace-id 一致），出站 httpx 由插桩注入。

未映射的 OTel 属性会落到 `metadata.attributes`，`resource` 属性落到 `metadata.resourceAttributes`——**这两处不可过滤**；要可按它过滤/聚合，必须显式写 `langfuse.trace.metadata.<key>` 或 `langfuse.observation.metadata.<key>`。

### 6.1 评分(scores)

评分不走 OTLP,而是 **Langfuse Scores API**(POST /api/public/scores,Basic pk:sk)按 `trace_id` 挂到 trace 上;本仓库同时把评分落成 run 事件 `run.score.v1`(无新表、无迁移),因此**不开 Langfuse 也能本地回放**。

- 规则基线:run 进入终态时自动写 `outcome`(completed=1 / failed=0,source=rule);未开 Langfuse 时不写,避免制造无意义事件。
- 人工评分:控制台 run 详情提供「有用/无用」(value=1/0,source=human),`POST /runs/{id}/scores` 写入事件,列表走 `GET /runs/{id}/scores`。
- 上报时机:事件随业务事务提交,`langfuse.create_score` 注册为 **after-commit** 回调(best-effort,5s 超时,失败只 warning);无 `runs.trace_id`(未执行/tracing 关闭/采样丢弃)时跳过,不会产生死链。
- 口径:score 的 value 归一到 0~1;同名 score 多次提交时以 Langfuse 侧最新一条为准(面板可按 name 聚合)。
- 实测(2026-11-09,Cloud):`POST /api/public/scores` 返回 score id;读侧注意 v2 scores 已弃用(2026-11-16 下线),改用 `GET /api/public/v3/scores`。

---

## 7. 关键映射约定

已核实（2026-11，Langfuse v4）：

- Trace 名 = `langfuse.trace.name` 或根 span 名；user = `langfuse.user.id`；session = `langfuse.session.id`；tags = `langfuse.trace.tags`（string[]）；release/version/environment 由对应属性给出。
- Observation 类型 = `langfuse.observation.type`（显式优先，未识别则由 model 推断 generation，否则 span）；model = `langfuse.observation.model.name` 或 `gen_ai.request.model`/`gen_ai.response.model`。
- 用量 = `langfuse.observation.usage_details`（JSON）或 `gen_ai.usage.*`；成本 = `langfuse.observation.cost_details`（JSON）或 `gen_ai.usage.cost`，**ingested cost 优先于按价目表推算的 inferred cost**。
- TTFT = `langfuse.observation.completion_start_time`（ISO8601 UTC）。
- 会话串联：`gen_ai.conversation.id` 只用于 OTel 内部传播；Langfuse 的会话视图读 `langfuse.session.id`，两者不要混用。

---

## 8. 起栈与验证

### 8.1 Cloud（当前默认，零额外容器）

不需要起任何东西：服务本地 `.env` 已指向 Cloud，`npm run dev` 起服务后 trace 直接进 Cloud 项目。造一条 run 后验证：

```sh
curl -sS -u "$PK:$SK" \
  "https://cloud.langfuse.com/api/public/v2/observations?fields=core,basic,usage,io&limit=5"
```

已实测（2026-11-09）：`POST /api/public/otel/v1/traces` 返回 200/206，Cloud 侧可见 trace 与 `SPAN`/`GENERATION` 两类 observation，`usageDetails`（input/output/total）按预期落库，`environment=dev` 生效；baggage 携带的 user/session 见 §9.1。

### 8.2 自托管观测 profile（可选，本地/离线）

```sh
# 1) 只起观测栈（默认 profile 关闭，不影响主栈）
docker compose -f deploy/docker-compose.yml --profile observability up -d

# 2) 打开 http://localhost:3001，首用户 dev@beauty-moon.local / beauty-moon，
#    项目 beauty-moon agent 已由 LANGFUSE_INIT_* 预建，密钥 pk-lf-beauty / sk-lf-beauty

# 3) Cloud 默认值要显式改回自托管地址，再重建两个上报服务（环境变量变了必须重建容器）
LANGFUSE_ENABLED=true LANGFUSE_HOST=http://langfuse-web:3000 \
LANGFUSE_PUBLIC_KEY=pk-lf-beauty LANGFUSE_SECRET_KEY=sk-lf-beauty \
docker compose -f deploy/docker-compose.yml up -d agent-orchestrator llm-gateway

# 4) 造一条 run，Tracing 里应看到 trace "run <run_id>"，含节点 span 与 chat 子 span
```

端口：Langfuse UI `3001`、MinIO 控制台 `9091`（默认口令见 compose，仅演示）。其余组件不映射宿主机端口，与主栈的 5432/6379 隔离。数据卷 `langfuse-*` 独立，`down -v` 只清观测数据。

关闭：`LANGFUSE_ENABLED=false` 重建服务即可（不删数据）；彻底移除再 `--profile observability down -v`。

---

## 9. 采样、脱敏、留存

**采样**

- 两种口径，**不要混用**：
  - **直连 Langfuse（默认）**：`<SERVICE>__TRACE_SAMPLE_RATIO`（`ParentBased(TraceIdRatioBased)`）是头部采样，入口决定、下游跟随，避免半截链路。compose 生产默认 0.1，本地/演示 1.0。头部采样**做不到「错误全留」**——trace 开始时就已丢弃，不要这样宣称。
  - **经 OTel Collector（§9.2）**：服务侧一律 1.0（全量发给 Collector），由 Collector 等 trace 结束后按「错误 / 慢 / 10% 基线」决定保留。
- 错误、超时、HITL 的 trace 全留只能靠尾部采样（Collector，见 §9.2）；直连模式受限于头部采样。
- 指标永不采样（Prometheus 口径不受此开关影响）。

**脱敏（硬约束）**

- `LOG_CONTENT` 默认 false：不记 prompt/补全正文；打开时只在 input/output 记截断预览，且该 trace 视为含敏感内容。
- Baggage 会跨服务与第三方 HTTP 头传播，**只放标识符**（run_id/account_id/session），绝不放 prompt、token、路径。
- `langfuse_secret_key`、`Authorization`、Cookie、BYOK 密钥一律不进 span 属性。
- **导出前脱敏已实现**（`packages/common/common/redaction.py` 的 `AttributeRedactionSpanProcessor`）：`common.tracing._build_provider` 在**任何导出处理器之前**注册它，直连 Langfuse 与经 Collector 两条路都在离开进程前清洗——
  - 删除敏感键：Authorization/cookie/api_key/secret/password/credential、单数 token（复数 `*_tokens` 是计数，保留）、全部 `http.request.header.*` / `http.response.header.*`；
  - LLM 正文闸门：`langfuse.observation.input|output`、`gen_ai.input.*`、`gen_ai.output.*`、`gen_ai.system_instructions`、`gen_ai.tool.call.arguments|result` 等只在 `LOG_CONTENT=true` 时保留；HTTP body、`db.statement`、`url.query` 永远删除；
  - URL 属性（`url.full`/`http.url`/`http.target`）去 query/fragment 与 userinfo；字符串 > 2000 字符、序列 > 64 项截断。
  - `LOG_CONTENT` 仍是"业务侧是否主动记正文"的开关；处理器是**导出口径的强制兜底**，第三方 instrumentation（httpx/FastAPI/GenAI 库）顺手带上的正文与请求头也会被拦下。

**留存与成本**

- trace/observation 存 ClickHouse，events/media 存 MinIO；Postgres 只存项目与配置。Cloud 的保留期在项目计划里设，自托管才需要自己配 TTL/生命周期；容量估算与分层策略见 §9.3。
- 成本可 ingested（调用方给出）或 inferred（按价目表推算），**ingested 优先**；任何对外成本数字都要标注口径（厂商账单 / 价目表推算 / 网关实测），三者不可混算。

### 9.1 Cloud 成本口径（已实测的坑）

实测结论（2026-11-09，真实 OTLP 导出到 Cloud）：

- **Cloud 的托管价目表没有 deepseek 条目**。`GET /api/public/models` 返回 196 个模型定义，`modelName` 含 deepseek 的 **0** 个。因此 `gen_ai.request.model=deepseek/deepseek-chat` 的 generation 落库后 `costDetails={}`、`totalCost=null`——成本为空不是链路 bug，是价目表缺项。
- 对照实验：同一套属性把 model 换成 `gpt-4o-mini`（1000 input / 500 output token），`costDetails={"input":0.00015,"output":0.0003,"total":0.00045}`。价目表命中时 inferred cost 正常，证明 span 属性链路无误。
- `GET /api/public/v2/observations` 的 `model` / `modelId` 字段即使对命中价目的 gpt 也返回 null，**不要用它们判断成本是否算出**；看 `costDetails` / `totalCost`。
- Traces API 2026-11-16 下线；读侧用 v2 observations（`fields=core,basic,usage,io,trace`）或 UI。

**已解决：网关 ingested cost**

llm-gateway 每次调用都用 litellm 随包价目表算出 USD 成本，写在 generation span 的 `langfuse.observation.cost_details`（`generation_trace.ingested_cost`）；ingested 优先于 Cloud 的 inferred cost，因此 **Cloud 目录里没有 deepseek 也能出成本**。

- 价格来源：`LITELLM_LOCAL_MODEL_COST_MAP=True` 由 `llm_gateway/__init__.py` `setdefault`，用 litellm 随依赖发布的**本地价目表**——启动不发网络请求（离线/受限网络可启动），价格随依赖升级而更新，成本口径可复现；需要热更价目时显式设 False。
- 实测（2026-11-09，`deepseek/deepseek-chat` 1000 input / 500 output）：Cloud 返回 `costDetails={"input":0.00028,"output":0.00021,"total":0.00049}`、`totalCost=0.00049`。
- 未映射模型（BYOK 自定义名）→ 不写 `cost_details`，回落到 Cloud 目录/推断，不会因为算价失败影响请求。
- **缓存 token 按缓存价计（不再高估）**：`UsageOut` 增加 `cache_read_tokens` / `cache_write_tokens`——litellm 把 DeepSeek 的 `prompt_cache_hit_tokens`、Anthropic 的 `cache_read/cache_creation_input_tokens` 统一收进 `prompt_tokens_details`，网关的 `_usage` 直接读它。`ingested_cost` 先把缓存部分从全价输入里扣掉，再按价目表的 `cache_read_input_token_cost` / `cache_creation_input_token_cost` 计价（字段缺失时回落到 `input_cost_per_token`），剩余输入才按全价：

  ```text
  input_cost = (prompt_tokens - cache_read - cache_write) * input_price
             + cache_read * cache_read_price
             + cache_write * cache_write_price
  ```

  实测口径：`deepseek/deepseek-chat` 1000 input 里 800 命中缓存 → input `0.0000784`（全价口径是 `0.00028`，差 3.6 倍）；DeepSeek 的 `cache_creation_input_token_cost=0`，Anthropic 系写入价按价目表给。缓存 read/write 同时以 `gen_ai.usage.cache_read_input_tokens` / `gen_ai.usage.cache_creation_input_tokens` 出 span；Cloud 实测落库为 `usageDetails={"input":200,"output":500,"total":1500,"input_cached_tokens":800}`、`costDetails={"input":7.84e-05,"output":0.00021,"total":0.0002884}`（Langfuse 把命中部分从 input 明细里单列，不会重复计）。

- 兜底仍可用 Cloud 项目里的模型定义（Settings → Models → New）；但它只影响之后的新数据，且与网关价目表可能不一致，二者择一即可。

### 9.2 尾部采样（可选：OTel Collector）

直连模式受限于头部采样，做不到「错误/HITL 全留」。需要这条能力时加一层 Collector：

- **配置**：`deploy/otel-collector/config.yaml`（`otlp` receiver 4317/4318 → `tail_sampling` → `batch` → `otlphttp/langfuse`）。策略三选一命中即留：`status_code=ERROR`、`latency > 30s`、其余 10% 基线。认证用 `basicauth` 扩展读 `LANGFUSE_PUBLIC_KEY/SECRET_KEY`，并带 `x-langfuse-ingestion-version: 4`；`endpoint` 写到 `/api/public/otel`（otlphttp 自动补 `/v1/traces`）。
- **启动**：服务侧必须**全量**发给 Collector（否则错误 trace 在服务内就被头采样丢了），所以在宿主机环境显式传两个变量后起观测 profile：

  ```sh
  TRACE_SAMPLE_RATIO=1.0 OTEL_COLLECTOR_ENDPOINT=http://otel-collector:4318/v1/traces \
    docker compose -f deploy/docker-compose.yml --profile observability up -d
  ```

  服务容器内用服务名 `otel-collector`；宿主机调试用 `http://localhost:4318/v1/traces`。不传 `OTEL_COLLECTOR_ENDPOINT` 就仍直连 Langfuse，Collection 不参与。

- **代价与限制**：`decision_wait: 30s` 意味着 trace 在 Langfuse 出现会晚约 30 秒；内存按 `num_traces: 50000` 估算；同一条 trace 的 span 必须落到**同一个** Collector 实例（多副本需按 trace_id 负载均衡，本编排单副本）。
- **状态**：配置与 compose 服务已提交，通过 compose 静态校验与 YAML 解析；本机 Docker daemon 不可用，**未实测**「服务 → Collector → Cloud」链路。首次启用请同时看 Collector 日志与 Langfuse 是否收到数据。

### 9.3 留存与容量

**结论：当前默认栈（Cloud + 0.1 采样 + 正文关闭）不需要额外的清理任务；容量与保留期按下面的口径盯。** 自托管 profile 才需要运维侧 TTL / 生命周期，且保留期定制属 Langfuse EE。

分层留存（“哪份数据该留多久”由用途决定，不要让 trace 充当审计库）：

| 数据                                           | 位置                                  | 建议保留                        | 清理/控制手段                                                                  |
| ---------------------------------------------- | ------------------------------------- | ------------------------------- | ------------------------------------------------------------------------------ |
| trace / observation / generation               | Langfuse（Cloud 或自托管 ClickHouse） | 生产 30–90 天；排障期可临时拉长 | Cloud：项目计划里的数据保留设置；自托管：ClickHouse TTL / Langfuse EE 保留策略 |
| events / media（仅 `LOG_CONTENT=true` 时存在） | MinIO / S3                            | 7–30 天                         | 桶生命周期规则（`langfuse-media` 前缀）                                        |
| 应用日志（structlog JSON）                     | 日志平台                              | 14–30 天                        | 日志平台保留策略；按 `trace_id` 回查 Langfuse                                  |
| 业务事实：`run_events` / `tool_calls` 审计     | Postgres                              | 长期（按合规）                  | 业务库备份与归档，不依赖 Langfuse                                              |
| 评分（scores）                                 | Langfuse + `run.score.v1`             | 随 trace；事件侧长期            | `run.score.v1` 在 run_events 里可回放                                          |

容量估算（数量级判断，实测前先按上限规划）：

- 单条 trace 的 observation 数 ≈ 1（run 根）+ 图节点 5–20 + 模型调用 M + 工具 T；典型 10–20 个。
- 每个 observation 属性压缩后约 2–4 KB（含元数据、usage；正文关闭时不含 prompt/补全）。单 trace ≈ **30–80 KB**。
- 示例：1000 run/天 × 50 KB ≈ **50 MB/天**，30 天约 **1.5 GB**；采样 0.1 后 ≈ 5 MB/天、30 天约 **150 MB**。自托管时 ClickHouse 原始盘按 2–3 倍余量给（副本、合并、索引）。
- 打开 `LOG_CONTENT` 后，input/output 正文按 2000 字符截断（`redaction.py`），大对象走 MinIO media，**媒体体积才是大头**，要单独看桶增长。

按优先级使用容量旋钮（越靠前越省且越不伤排障）：

1. `LOG_CONTENT=false`（默认）：不存 prompt/补全正文，省得最多也最安全；
2. `TRACE_SAMPLE_RATIO=0.1`（compose 生产默认）：只留 10% 基线 trace；
3. 需要“错误全留”时改用 Collector 尾部采样（§9.2），把基线比例降到 0.05–0.1、错误/慢 trace 仍全留；
4. 最后才调保留期——保留期缩短会直接删掉历史排障证据。

成本/容量告警：

- **Cloud 侧**：在项目/组织的用量与账单页设 spend 告警；保留期与用量上限由计划决定，升级计划前先看 observation 摄入量。
- **应用侧**：token 预算已在 `services/*/services/usage.py`（预算阈值触发日志与用量上报）；Langfuse 不是计费真源，账单以厂商/网关口径为准。
- **自托管侧**：监控 ClickHouse 磁盘、MinIO 桶大小、Collector 内存（`memory_limiter` 400 MiB）；这些都是运维指标，不要塞进 trace。

---

## 10. 与既有观测的关系

- `X-Request-ID` 与 `trace_id` 双向可查：日志平台按 `trace_id` 跳到 Langfuse，Langfuse 的 trace 里也能拿到 HTTP span 上的 request_id；控制台 run 详情有「在 Langfuse 查看」深链（`runs.trace_id`，见 §6）。
- Sentry 事件与 Langfuse trace 共享同一 `trace_id`，异常点到对应 run 的链路可对上；前端 Sentry 采样命中时由 `lib/http.ts` 带 `traceparent`，后端 OTel 沿用该 trace_id，于是浏览器事务与 `run {run_id}` 根 span 同属一条 trace。
- 前端关闭 Sentry（`VITE_SENTRY_DSN` 为空）时不带 `traceparent`，后端照样按自身采样策略开 trace，互不影响。
- Langfuse 不注册 meter/logger provider，指标与日志口径不变；`/internal/metrics` 仍是唯一 Prometheus 出口。

---

## 11. 路线

**Phase 1 已完成**：`common/langfuse.py`、`config.py` 四个字段、`tracing.py` 接入、compose `observability` profile、`test_langfuse.py`（含 baggage 传播与 provider 选择）。

**Phase 2 已完成**：orchestrator 节点 span、llm-gateway generation span（含流式 TTFT、错误、fallback 计数），common/gateway/orchestrator 测试通过。

**Phase 3（进行中）**

1. ~~run 根 span~~ **已完成**：`services/run_trace.py` 的 `run_trace(run)` 包住 5 个 `graph.ainvoke` 边界（首次执行、approve、confirm、reject、answer），trace 名 = `run {run_id}`，session = conversation_id，user = account_id，observation 类型 `agent`；已有父 span（HTTP 同步触发）时挂在其下，不另起 trace。
2. ~~tool-service 工具 span~~ **已完成**：`execution/tool_span.py` 在 `POST /v1/tools/{name}/execute` 里按一次 tool_call 建 `execute_tool {name}`，元数据与 `tool_calls` 审计列对齐（risk/decision/duration_ms/result_chars/bytes_written/paths/sandbox_id/patch 行数）；默认不记参数原文，`TOOL__LOG_CONTENT=true` 才写截断后的 input。
3. ~~导出前脱敏~~ **已完成**：`common/redaction.py` 的 `AttributeRedactionSpanProcessor` 在导出前统一删敏感键、按 `LOG_CONTENT` 闸门保留正文、URL 去参与值截断；`_build_provider` 在导出处理器之前注册，见 §9。
4. ~~成本：Cloud 加 deepseek 模型定义，或网关算 ingested cost~~ **已完成**：网关 ingested cost（litellm 本地价目，见 §9.1）；缓存命中/写入 token 已按缓存价拆分，不再按全价高估。
5. ~~eval/scores~~ **已完成**：规则基线 `outcome`（completed=1 / failed=0）+ 人工点赞/点踩，落 `run.score.v1` 事件并经 after-commit best-effort 写 `POST /api/public/scores`；控制台 run 详情可评分、`GET /runs/{id}/scores` 回放（见 §6.1）。
6. ~~留存与容量~~ **已定策略**：分层留存表 + 容量估算 + 旋钮优先级 + 告警口径见 §9.3；Cloud 保留期在项目计划设置，自托管 TTL/生命周期待启用自托管时再配。

**Phase 4（剩余尾部采样实测）**

1. ~~Web 端 run 详情加「在 Langfuse 查看」深链~~ **已完成**：`runs.trace_id` 由 `run_trace` 回写，`RunRead` 暴露，`apps/web` 的 run 详情按 `VITE_LANGFUSE_BASE_URL`（默认 Cloud）拼 `/trace/<trace_id>`；未执行过/tracing 关闭时隐藏。
2. ~~采样升级为尾部采样（错误/HITL 全留）~~ **配置已提供**：`deploy/otel-collector/config.yaml` + compose `otel-collector` 服务（observability profile），策略见 §9.2；本机无 Docker 未实测。

---

## 12. 常见坑

- **v4 摄取延迟**：自托管必须带 `x-langfuse-ingestion-version: 4`（`common.langfuse` 已加），否则可能延迟到 10 分钟才可见。
- **仅 OTLP/HTTP**：不支持 gRPC；endpoint 是完整路径 `.../api/public/otel/v1/traces`，少写路径会 404。
- **trace 属性必须逐 span 存在**：只给根 span 设 user/session/tags，子 span 上就过滤不到；用 Baggage（本仓库已做）或 SDK 的 `propagate_attributes`。
- **OTel 全局 provider 一进程只能设一次**：测试里建独立 `TracerProvider`，不要反复 `init_tracing`。
- **metadata 默认不可过滤**：要过滤必须写 `langfuse.trace.metadata.*` / `langfuse.observation.metadata.*`。
- **Langfuse 不是日志库**：不要拿 trace 当审计或全量日志，体量与留存都会失控。
- **自托管依赖重**（Postgres + ClickHouse + Redis + MinIO 四件套）：默认 profile 关闭，本地只想看 span 时可直接配通用 `OTLP_ENDPOINT` 指向临时 collector。
- **Cloud 区域**：`https://cloud.langfuse.com` 是 EU；数据驻留要求更高时换 US/JP/HIPAA 端点，或切自托管。密钥按项目（不是按用户）签发，一个项目给全部服务用——多套密钥会把链路拆成几个项目、trace 串不起来。
- **Cloud 成本列默认可能是空的**：托管价目表不含 deepseek（见 §9.1），别把「成本 0」当成真实花费。
- **`langfuse_secret_key` 等同项目写权限**：只在服务端 `.env`，不进前端构建、不进日志、不进 span 属性；泄露立刻在 Cloud 轮换。
