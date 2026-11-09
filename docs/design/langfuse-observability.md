# Langfuse 观测接入（trace / 成本 / 评估）

> 状态：**Phase 1（common 基座）、Phase 2（orchestrator 节点 span、llm-gateway generation span）与 run 根 span / 工具 span 已实施；当前接入 Langfuse Cloud（EU 区域），8 个服务的本地 `.env` 已配置并对 Cloud 实测通过**（2026-11-09）。
> 已用真实 OTLP 导出验证：Cloud 侧能看到 trace/observation、observation type 正确（SPAN / GENERATION）、token 用量（`usageDetails`）、baggage 传播的 user/session 生效；**deepseek 成本已由网关 ingested cost 补齐**（litellm 本地价目，见 §9.1）。
> 剩余：eval/scores、导出前脱敏、留存策略（Phase 3）与 Web 深链、尾部采样（Phase 4）待排期，见 §11。
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

| 字段                  | 默认值                  | 说明                                                                                                 |
| --------------------- | ----------------------- | ---------------------------------------------------------------------------------------------------- |
| `langfuse_enabled`    | `false`                 | 总开关；关闭时零初始化、零出口                                                                       |
| `langfuse_host`       | `http://localhost:3000` | Langfuse 地址；Cloud 用 `https://cloud.langfuse.com`，自托管 compose 内为 `http://langfuse-web:3000` |
| `langfuse_public_key` | `null`                  | 项目公钥 `pk-lf-*`                                                                                   |
| `langfuse_secret_key` | `null`                  | 项目私钥 `sk-lf-*`；只在服务端注入，禁止进前端或日志                                                 |

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

工具 span 的 `input` 默认空——参数原文与结果都属敏感内容，只在 `TOOL__LOG_CONTENT=true` 时写截断后的参数；审计表始终是完整事实源。跨进程续接已验证：FastAPI 原生 telemetry 从入站 `traceparent` 提取父上下文（入站 trace-id 与导出 span 的 trace-id 一致），出站 httpx 由插桩注入。

未映射的 OTel 属性会落到 `metadata.attributes`，`resource` 属性落到 `metadata.resourceAttributes`——**这两处不可过滤**；要可按它过滤/聚合，必须显式写 `langfuse.trace.metadata.<key>` 或 `langfuse.observation.metadata.<key>`。

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

- 沿用 `<SERVICE>__TRACE_SAMPLE_RATIO`（`ParentBased(TraceIdRatioBased)`）：入口决定是否采样，下游跟随，避免半截链路。
- 默认 0.1 会丢掉 90% 的 run；本地/演示建议 1.0，生产按成本调 0.1~0.2。
- 错误、超时、HITL 的 trace 想全留需要**尾部采样**（先全采、按结果丢弃），当前未做；不要用头部比例采样宣称"错误全留"。
- 指标永不采样（Prometheus 口径不受此开关影响）。

**脱敏（硬约束）**

- `LOG_CONTENT` 默认 false：不记 prompt/补全正文；打开时只在 input/output 记截断预览，且该 trace 视为含敏感内容。
- Baggage 会跨服务与第三方 HTTP 头传播，**只放标识符**（run_id/account_id/session），绝不放 prompt、token、路径。
- `langfuse_secret_key`、`Authorization`、Cookie、BYOK 密钥一律不进 span 属性。
- 仅 OTel 接入时没有 SDK 的 `mask_otel_spans`：导出前脱敏需自定义 SpanProcessor/Exporter（Phase 3）。

**留存与成本**

- trace/observation 存 ClickHouse，events/media 存 MinIO；Postgres 只存项目与配置。自托管按卷估算容量，保留期策略（TTL/清理）属 Phase 3。
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
- **口径注意**：`UsageOut` 只有 prompt/completion 两个计数，缓存命中 token（deepseek cache hit 单价低一个量级）无法区分，缓存占比高时会**高估**成本；Phase 3 若要精确，需要 usage 里带 cache_read/cache_write。
- 兜底仍可用 Cloud 项目里的模型定义（Settings → Models → New）；但它只影响之后的新数据，且与网关价目表可能不一致，二者择一即可。

---

## 10. 与既有观测的关系

- `X-Request-ID` 与 `trace_id` 双向可查：日志平台按 `trace_id` 跳到 Langfuse，Langfuse 的 trace 里也能拿到 HTTP span 上的 request_id（Phase 4 再加 UI 深链）。
- Sentry 事件与 Langfuse trace 共享同一 `trace_id`，异常点到对应 run 的链路可对上。
- Langfuse 不注册 meter/logger provider，指标与日志口径不变；`/internal/metrics` 仍是唯一 Prometheus 出口。

---

## 11. 路线

**Phase 1 已完成**：`common/langfuse.py`、`config.py` 四个字段、`tracing.py` 接入、compose `observability` profile、`test_langfuse.py`（含 baggage 传播与 provider 选择）。

**Phase 2 已完成**：orchestrator 节点 span、llm-gateway generation span（含流式 TTFT、错误、fallback 计数），common/gateway/orchestrator 测试通过。

**Phase 3（进行中）**

1. ~~run 根 span~~ **已完成**：`services/run_trace.py` 的 `run_trace(run)` 包住 5 个 `graph.ainvoke` 边界（首次执行、approve、confirm、reject、answer），trace 名 = `run {run_id}`，session = conversation_id，user = account_id，observation 类型 `agent`；已有父 span（HTTP 同步触发）时挂在其下，不另起 trace。
2. ~~tool-service 工具 span~~ **已完成**：`execution/tool_span.py` 在 `POST /v1/tools/{name}/execute` 里按一次 tool_call 建 `execute_tool {name}`，元数据与 `tool_calls` 审计列对齐（risk/decision/duration_ms/result_chars/bytes_written/paths/sandbox_id/patch 行数）；默认不记参数原文，`TOOL__LOG_CONTENT=true` 才写截断后的 input。
3. 导出前脱敏：自定义 SpanProcessor 白名单化属性，替代 `LOG_CONTENT` 粗粒度开关。
4. ~~成本：Cloud 加 deepseek 模型定义，或网关算 ingested cost~~ **已完成**：网关 ingested cost（litellm 本地价目，见 §9.1）；剩余口径缺口是缓存 token 未区分。
5. eval/scores：run 结束后按事件写 `POST /api/public/scores`（人工标注 + 规则评分），回填 `/runs` 详情。
6. 留存与容量：ClickHouse TTL、MinIO 生命周期、成本告警。

**Phase 4（待做）**

1. Web 端 run 详情加「在 Langfuse 查看」深链（`${LANGFUSE_HOST}/trace/<trace_id>`），trace_id 从 run 事件或 span 上下文取。
2. 采样升级为尾部采样（错误/HITL 全留）。

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
