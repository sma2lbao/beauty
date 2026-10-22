# 服务可观测方案（监控日志）

> 状态：**P0 已实施**（2026-10-22），P1 Sentry / P2 指标与追踪未开始。
> 对齐 `docs/architecture-v2.md` §9：可观测 = **结构化日志 + traceId + Sentry（前端/服务）**，OTel/Langfuse 后置。
> 本文定规范与实施计划；P0 的落地位置见 §11。

---

## 1. 目标与非目标

**目标**

- 线上任意一个请求/任务，能用 `request_id` 把 7 个服务的日志串成一条时间线；
- 每个 HTTP 请求有且只有一行结构化访问日志（状态、耗时、路由、租户）；
- 未捕获异常、上游失败、慢请求、后台 job 失败都能被日志与 Sentry 捕获，且不带 PII；
- 内网/离线形态（`architecture-v2.md` 部署形态 B）可以整层关掉，服务照常运行。

**非目标（本期）**

- 不做分布式追踪（OTel span）与指标端点（Prometheus）；见 §9 与 §11 的 P2；
- 不做日志平台自建（Loki/ELK）；服务只写 stdout，采集归部署侧；
- 不做 prompt/response 回放与 LLM 成本归因（属于 §11 的 P2 之后）。

---

## 2. 现状盘点

| 能力                    | 现状                                                                        | 位置                                                                                                                                   | 差距                                                           |
| ----------------------- | --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| 结构化日志装配          | ✅ structlog；dev 彩色、staging/prod JSON；`service`/`env` 绑进 contextvars | `packages/common/common/logging.py:16`、`:46`                                                                                          | 没有请求级上下文，日志之间无法关联                             |
| 日志级别配置            | ✅ `log_level`                                                              | `packages/common/common/config.py:26`                                                                                                  | —                                                              |
| 请求上下文 / request_id | ❌                                                                          | —                                                                                                                                      | 无 `X-Request-ID` 生成与回写，跨服务不可串                     |
| 访问日志                | ❌                                                                          | —                                                                                                                                      | 只有 uvicorn 默认 access log（格式不结构化、无租户、无法采集） |
| 异常日志                | ⚠️ 仅 `AppError` → JSON 响应，不打日志                                      | 各服务 `main.py` 的 `app.exception_handler(AppError)`（如 `services/agent-orchestrator/agent_orchestrator/main.py:469`）               | 5xx 无日志、未捕获异常只有 uvicorn 崩溃栈                      |
| 健康/就绪探针           | ✅ 每服务 `/healthz`、`/readyz`                                             | 如 `services/account-service/account/api/health.py:12`、`services/llm-gateway/llm_gateway/api/health.py:12`                            | 访问日志需要给探针降噪                                         |
| 业务日志                | ⚠️ 零散、无统一字段                                                         | 如 `services/llm-gateway/llm_gateway/services/completions.py:91`、`services/agent-orchestrator/agent_orchestrator/services/llm.py:110` | 无事件名约定、无耗时/用量字段                                  |
| 错误聚合（Sentry）      | ❌                                                                          | —                                                                                                                                      | 见 §8                                                          |
| 指标 / 追踪             | ❌                                                                          | —                                                                                                                                      | 见 §9 与 §11 的 P2                                             |

`docs/gap-2026-10/01-repo-inventory.md:169-175` 的结论与上表一致：只有 structlog 文本，无 traceId/metrics。

---

## 3. 分层与职责

| 层         | 承担                              | 本仓库落点                                     | 阶段   |
| ---------- | --------------------------------- | ---------------------------------------------- | ------ |
| 结构化日志 | 请求/任务生命周期、错误、慢路径   | `packages/common/common/logging.py` + 新中间件 | **P0** |
| 错误聚合   | 异常栈、影响面、告警              | `sentry-sdk`（服务 + Web），可选依赖           | **P1** |
| 指标       | RED（量/错/延迟）、队列与上游健康 | 先由日志派生；独立指标端点                     | P2     |
| 链路追踪   | 跨服务 span、模型调用             | OTel + GenAI semconv（v2 后置）                | P2     |

原则：**日志是主线**（Sentry 只做异常聚合，指标与追踪后置）。日志字段一旦定下就是契约，后续接 Sentry/OTel 时不改字段名，只加 `trace_id`/`span_id`。

---

## 4. 日志规范

### 4.1 输出与格式

- 一律写 **stdout**，一行一个 JSON（JSON Lines）；不写文件、不做轮转——采集交给部署侧（Docker/Fly 日志、云日志、Loki）。
- dev 保持 `ConsoleRenderer` 人读；staging/prod 用 `JSONRenderer(ensure_ascii=False)`（现状已如此）。
- 时间统一 **UTC ISO8601**（`TimeStamper(fmt="iso", utc=True)`）；排障时再换本地时区。
- 多行堆栈只出现在 `exception` 字段（JSON 字符串）里，避免一行日志变多行。
- 落地 P0 时给 uvicorn 启动参数加 `--no-access-log`（`services/*/project.json:60` 的 serve 命令），否则同一请求会打两条访问日志。

### 4.2 字段字典（保留字，业务代码不得复用）

| 字段                        | 类型   | 说明                                                                                                          |
| --------------------------- | ------ | ------------------------------------------------------------------------------------------------------------- |
| `timestamp`                 | string | UTC ISO8601，structlog `TimeStamper` 输出                                                                     |
| `level`                     | string | `debug`/`info`/`warning`/`error`/`critical`                                                                   |
| `event`                     | string | 事件名，`snake_case`（如 `http_request`、`run_failed`），**不把变量拼进事件名**                               |
| `service`                   | string | `settings.service_name`，启动时绑定                                                                           |
| `env`                       | string | `dev`/`staging`/`prod`，启动时绑定                                                                            |
| `logger`                    | string | 模块名，替代 structlog 默认的 `logger` 字段                                                                   |
| `request_id`                | string | 每个请求一个，跨服务透传（§5.1）                                                                              |
| `trace_id`                  | string | W3C traceparent 的 trace-id；P0 只有透传时存在，P2 由 OTel 补齐                                               |
| `tenant_id`                 | string | 鉴权后绑定（§5.3）；内部调用以 token claim 为准                                                               |
| `user_id`                   | string | 可选，鉴权后绑定                                                                                              |
| `method` / `route` / `path` | string | 访问日志用；`route` 是模板（`/api/v1/runs/{run_id}`），`path` 是原始路径（仅 `route` 为空时兜底，防基数爆炸） |
| `status`                    | int    | HTTP 状态码                                                                                                   |
| `duration_ms`               | number | 请求/调用耗时，保留 1 位小数                                                                                  |
| `client_ip`                 | string | 仅 BFF 记录（用户侧）；服务间调用记 `client` 服务名更值钱                                                     |
| `error_code`                | string | `AppError.code`（对外契约，如 `workspace.default_protected`）                                                 |
| `exception`                 | string | 异常栈（`format_exc_info` 输出）                                                                              |
| `msg`                       | string | 人类可读补充说明，**不承载结构化信息**                                                                        |

其余业务字段（`run_id`、`tool`、`model`、`job_kind`、`attempts`、`latency_ms`、`tokens_in`/`tokens_out` 等）按事件就近命名，进 §10 清单，不再回填本表。

### 4.3 事件命名与级别

- 事件名：`<领域>_<动作>`，过去式或名词短语；同一语义跨服务同名（`http_request`、`app_error`、`upstream_error`）。
- 级别约定：

| 级别      | 用于                                                             | 示例                                                                                        |
| --------- | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `error`   | 5xx、未捕获异常、上游不可用（502）、job 终态失败、数据不一致     | `http_request`(status≥500)、`unhandled_exception`、`upstream_error`、`job_finished`(failed) |
| `warning` | 4xx 中的“值得看”（401/403 鉴权失败、409 冲突、限流 429）、慢请求 | `http_request`(429/慢)、`app_error`(4xx 预期内)                                             |
| `info`    | 正常业务状态变迁、生命周期                                       | `http_request`(2xx/3xx)、`run_created`、`job_finished`(succeeded)、`sse_opened`             |
| `debug`   | 高频细节：探针、SSE 心跳、轮询、缓存命中                         | `http_request`(health/ready)、`sse_tick`                                                    |

- 4xx 的业务预期错误（校验失败、令牌过期）不打 `error`，避免把告警打哑。

### 4.4 脱敏（硬约束）

- **禁止落日志**：`Authorization`/`Cookie`/`Set-Cookie`、access/refresh token、`X-Tenant-Id` 外的凭证头、密码与验证码、BYOK 密钥、MCP OAuth code/state、请求与响应体全文（默认不记）。
- 允许记：用户 id、租户 id、邮箱（按需，且只记脱敏后 `a***@example.com`）、路径与文件名（交付物路径需截断）。
- 文件内容、prompt/response 正文一律不进日志字段——要回放走 P2 之后的 LLM 观测（见架构 §10），不进结构化日志。
- 实现约定：日志只接受白名单字段；`common.logging` 提供 `redact(value)` 供需要时使用（P0 加入），Sentry 侧用 `before_send` 再兜一层（§8）。

### 4.5 降噪与采样

- `/healthz`、`/readyz` 的访问日志降为 `debug`（prod 默认 info，等于不打）；探针失败（5xx）仍升级为 `warning`。
- SSE 长连接：只在**建立/关闭**打两行（`sse_opened`/`sse_closed`，带 `channel`、`duration_ms`、`frames`），心跳帧不打。
- 轮询型接口（run 状态 2s、通知 5s）按请求照常记 `http_request`，但运营侧告警不基于此口径（避免被轮询放大）。
- 高频 debug（缓存命中、工具轮次）默认关；本地排障用 `LOG_LEVEL=DEBUG`。
- 量级预算：每请求 1 行 ≈ 0.5–1 KB；SSE 每连接 2 行；单实例 10 rps 时 < 1 GB/月，可接受。

---

## 5. 请求上下文与跨服务关联

### 5.1 `request_id` 规则

- 入口（BFF、服务直连）读请求头 `X-Request-ID`：合法（`^[A-Za-z0-9._-]{8,64}$`）则沿用，否则生成 UUID4 的 hex（或 UUIDv7 若后续引入）。
- 响应回写 `X-Request-ID`（浏览器 Network 面板能直接拿到，报 bug 时贴这一条即可定位）。
- 所有出站 HTTP 调用带上该头（BFF 代理、orchestrator 的 `TokenBroker`/workspace/knowledge/llm-gateway 客户端、jobs worker 的下游调用）。
- 后台任务（jobs worker）没有入站请求：以 `job_id` 为种子生成 `request_id`，并记 `job_kind`、`run_id`，让 worker 日志与触发它的 API 日志能对上（API 侧 `job_enqueued` 记同一个 `job_id`）。

### 5.2 `traceparent` 透传

- 若请求带 W3C `traceparent`（`00-<trace-id>-<span-id>-<flags>`），解析出 `trace_id` 绑定到日志上下文并原样透传给下游；不带则不生成（P0 不伪造 trace，避免与真实追踪系统冲突）。
- P2 接 OTel 后，由 instrumentation 接管生成与透传，日志字段名不变。

### 5.3 绑定与清理

- 用 structlog `contextvars`：中间件入口 `bind_contextvars(request_id, method, path)`，响应后 `clear_contextvars()`（避免请求间串味——async 任务复用线程时这是必踩的坑）。
- 租户/用户在鉴权依赖解析后绑定：各服务 `get_tenant_id`（如 `services/workspace-service/workspace/api/deps.py:44`、`services/account-service/account/api/deps.py:27`、`services/agent-orchestrator/agent_orchestrator/api/deps.py:109`、`services/connector-service/connector/api/deps.py:26`、`services/knowledge-service/knowledge/api/deps.py:32`）里 `bind_contextvars(tenant_id=...)`；鉴权失败也记一条（`auth_failed`，带原因码，不带 token）。
- SSE 长连接：上下文在连接生命周期内保持；关闭时清理。注意 FastAPI 流式响应的生成器在响应返回后仍执行，清理必须放 `finally`。
- 后台 asyncio 任务（jobs worker、outbox dispatcher）：`contextvars` 不跨任务自动传播，worker 每轮自行 `clear_contextvars()` 再按 job 绑定。

### 5.4 透传清单

| 调用链                                                   | 需要带的头                    | 备注                                             |
| -------------------------------------------------------- | ----------------------------- | ------------------------------------------------ |
| Web → BFF                                                | 前端 `fetch` 无需显式带       | BFF 生成/透传后回写                              |
| BFF → orchestrator/account/workspace/knowledge           | `X-Request-ID`、`traceparent` | `services/bff/bff/api/proxy.py` 代理时透传       |
| orchestrator → workspace/knowledge/llm-gateway/connector | 同上                          | httpx 客户端统一注入                             |
| orchestrator → account-service `/v1/auth/issue`          | 同上                          | `TokenBroker` 内部调用                           |
| jobs worker → 下游                                       | 同上（用 job 种子）           | 队列里存触发时的 `request_id` 更佳（后续可加列） |

---

## 6. 访问日志规格

每个 HTTP 请求一行，`event=http_request`，字段：

```
timestamp, level, event, service, env, logger,
request_id, trace_id?, tenant_id?, user_id?,
method, route, status, duration_ms, client_ip?(仅 BFF), error_code?
```

- `route` 用 **路由模板**：`request.scope["route"].path`（如 `/api/v1/conversations/{conversation_id}/messages/stream`）；未命中路由（404）用 `method` + 固定占位 `unmatched`，不落原始 path 里可能带 id 的片段——这是控制日志基数的关键。
- 分级：`status >= 500` → `error`；`status in (401,403,409,429)` → `warning`；其余 2xx/3xx → `info`；探针路径 → `debug`。
- 慢请求：`duration_ms > <SERVICE>__SLOW_REQUEST_MS`（默认 1500，SSE 除外）额外打 `event=http_request_slow`（`warning`），带 route 与耗时，便于直接 grep 慢接口。
- SSE：`http_request` 记录的是**建立连接**的耗时（到响应头写出），不记整个流；连接关闭时另打 `sse_closed`（`duration_ms`、`frames`、`disconnected`）。涉及端点：`services/agent-orchestrator/agent_orchestrator/api/v1/conversations.py:407`、`services/agent-orchestrator/agent_orchestrator/api/v1/runs.py:452`、`services/agent-orchestrator/agent_orchestrator/api/v1/notifications.py:128`、`services/agent-orchestrator/agent_orchestrator/api/mcp_server.py:106`、`services/bff/bff/api/proxy.py:123`。
- 限流命中（BFF `services/bff/bff/main.py:60` 的 `rate_limit` 中间件）记 `event=rate_limited`（`warning`，`tenant_id`、`group`），访问日志本身仍是 429。

---

## 7. 异常日志规格

| 场景                                  | 事件                  | 级别      | 额外字段                                                   | 备注                                                                |
| ------------------------------------- | --------------------- | --------- | ---------------------------------------------------------- | ------------------------------------------------------------------- |
| 未捕获异常                            | `unhandled_exception` | `error`   | `exception`（栈）、route、request_id                       | 中间件 catch → `log.exception` → 重新抛出，交给 FastAPI 出 500      |
| `AppError`（4xx）                     | `app_error`           | `warning` | `error_code`、status                                       | 不打栈；参数错误类不打                                              |
| `AppError`（5xx，如 `UpstreamError`） | `app_error`           | `error`   | `error_code`、status、`upstream`                           | 打栈                                                                |
| 上游失败                              | `upstream_error`      | `error`   | `upstream`（服务名）、`status`、`duration_ms`、`retryable` | orchestrator/llm-gateway 已有 `UpstreamError`（`common/errors.py`） |
| job 失败                              | `job_finished`        | `error`   | `job_id`、`job_kind`、`attempts`、`run_id`                 | 重试中打 `warning`，终态 `error`                                    |
| DB 异常                               | `db_error`            | `error`   | `operation`、`table`?                                      | 不记 SQL 全文（可能含参数）                                         |

规则：**一次异常只打一条带栈的日志**（中间件或 handler 二选一，避免重复）；Sentry 只做聚合与告警，不替代日志（内网形态 Sentry 关闭时仍有完整栈）。

---

## 8. 错误聚合：Sentry（P1，可选依赖）

**状态：服务端已实施**（`packages/common/common/sentry.py`，由 `install_observability` 在 7 个服务的 `create_app` 统一调用）。Web 侧尚未接入（见本节末）。

- 依赖：`sentry-sdk[fastapi]` 收在 `packages/common`（7 个服务共用），**默认关闭**——`<SERVICE>__SENTRY_DSN` 为空直接 `return False`，不 `init`、不发任何网络请求，内网形态零副作用。
- 初始化参数：`environment=env`、`release=sentry_release`（部署侧注入镜像 tag 或 git sha）、`server_name=service_name`、`traces_sample_rate=sentry_traces_sample_rate`（默认 0）、FastAPI/Starlette 集成（`transaction_style="endpoint"`，路由模板而非原始路径）。
- 数据采集用 SDK 2.7x 的 `data_collection` 显式收紧（替代已弃用的 `send_default_pii`）：`user_info=False`、`cookies=off`、`http_bodies=[]`、`url_query_params=off`、`stack_frame_variables=False`。
- `before_send` 三道处理：① `AppError` 且 `status < 500` 直接丢弃（业务分支不占错误配额）；② 请求体/cookie 删除、敏感头（Authorization/Cookie/X-Api-Key…）值替换为 `***`、敏感 query 参数（token/code/password…）抹值；③ 把 `request_id`/`trace_id`/`tenant_id`/`user_id`/`route`/`error_code` 写进 `tags`——Sentry → 日志按同一个 id 串。
- 忽略：`/healthz`、`/readyz`（探针不产生异常事件；无需额外过滤）。
- 测试：`packages/common/tests/test_sentry.py`——无 DSN 时 no-op、4xx 丢弃、5xx 保留且脱敏、自定义 transport 实收事件验证 tag 与脱敏。
- 环境变量（`BaseAppSettings` 字段，按服务前缀生效，如 `WORKSPACE__SENTRY_DSN`）：`SENTRY_DSN`、`SENTRY_RELEASE`、`SENTRY_TRACES_SAMPLE_RATE`。
- **Web 侧（未做）**：`@sentry/react` 接入 `apps/web/src/main.tsx`，`beforeSend` 脱敏 + 上传 sourcemap + 与 BFF 共用 `release`；需要时按同一条规则落地。

---

## 9. 指标（P2）

**状态：已实施（进程内 Prometheus）**——`packages/common/common/metrics.py`；请求级 RED 指标由中间件自动记录，业务指标在各自边界上报。

- 暴露：`GET /internal/metrics`（Prometheus 文本），`metrics_enabled=false` 时整个端点不注册；**不在 `/api/v1` 公共契约里**，BFF 不代理它。
- 公网入口（BFF）建议配 `<SERVICE>__METRICS_TOKEN`：配了之后必须带同值令牌（`X-Metrics-Token` 或 `Authorization: Bearer`），否则按 404 响应（不暴露端点存在性）。
- 标签只放低基数字段（`service`/`method`/`route`/`status`/`kind`/`provider`/`code`），**禁** `tenant_id`/`run_id`/原始 path；未匹配路由统一记 `route="unmatched"`，避免 404 扫描打爆基数。
- 采集点：中间件（`http_requests_total`/`http_request_duration_seconds`，异常且响应未开始时补记 500）、orchestrator `runs`（`run_outcomes_total`：completed/failed）、jobs worker（`job_failures_total`）、llm-gateway 与 orchestrator 的模型调用（`llm_upstream_errors_total`，provider 取模型前缀）。
- 业务代码调用 `metrics.observe_*` 时不必传 service：默认从日志上下文（启动时绑定的 `service`）取。
- 若暂时不部署 Prometheus，也可用 `metrics_payload()` 定时落 `event=metrics_snapshot` 的 info 日志，效果等价。

指标设计如下（前 5 个已实施；`outbox_pending`、`llm_tokens_total` 仍从日志/运维端点派生，待后续补）：

| 指标                            | 类型      | 标签                           | 来源                                                                                    |
| ------------------------------- | --------- | ------------------------------ | --------------------------------------------------------------------------------------- |
| `http_requests_total`           | counter   | service, method, route, status | 访问日志中间件                                                                          |
| `http_request_duration_seconds` | histogram | service, route                 | 同上                                                                                    |
| `run_outcomes_total`            | counter   | status                         | orchestrator `run.completed.v1`/`run.failed.v1`                                         |
| `job_failures_total`            | counter   | kind                           | jobs worker（`services/agent-orchestrator/agent_orchestrator/services/jobs_worker.py`） |
| `outbox_pending`                | gauge     | —                              | outbox 计数（已有运维端点 `/api/v1/outbox/*`）                                          |
| `llm_upstream_errors_total`     | counter   | provider, code                 | llm-gateway                                                                             |
| `llm_tokens_total`              | counter   | model, direction               | orchestrator/llm-gateway（与用量计量同源）                                              |

---

## 10. 各服务埋点清单

| 服务               | 关键事件（`event` 名）                                                                                                                                                                                                                                                                                                   | 现有可复用点                                                                                                                                                                                                                                         |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 全部               | `http_request`、`http_request_slow`、`sse_opened`/`sse_closed`、`app_error`、`unhandled_exception`                                                                                                                                                                                                                       | 新中间件（P0）                                                                                                                                                                                                                                       |
| agent-orchestrator | `run_created`、`run_planned`、`run_approved`、`run_completed`、`run_failed`、`run_blocked`、`job_enqueued`、`job_finished`、`tool_invoked`（tool、duration、status）、`llm_call`（model、phase、latency_ms、tokens_in/out）、`sse_opened`（channel=run_events/notifications）、`outbox_dispatch`（event_type、attempts） | `services/agent-orchestrator/agent_orchestrator/services/runs.py`、`graph/nodes.py`、`services/agent-orchestrator/agent_orchestrator/services/llm.py:110`、`services/agent-orchestrator/agent_orchestrator/services/jobs_worker.py`、`events` 消费处 |
| llm-gateway        | `upstream_call`（provider、model、latency_ms、status、tokens）、`upstream_error`（code、retryable）                                                                                                                                                                                                                      | `services/llm-gateway/llm_gateway/services/completions.py:91`、`services/llm-gateway/llm_gateway/services/embeddings.py:29`                                                                                                                          |
| bff                | `proxy_call`（target、status、latency_ms、stream）、`rate_limited`、`session_refresh_failed`                                                                                                                                                                                                                             | `services/bff/bff/main.py:60`、`services/bff/bff/api/proxy.py:215`、`services/bff/bff/api/mcp_oauth.py:21`                                                                                                                                           |
| account-service    | `auth_login`（result）、`token_refresh`（result、reuse_detected）、`device_registered`                                                                                                                                                                                                                                   | `services/account-service/account/`（登录/刷新路由）                                                                                                                                                                                                 |
| workspace-service  | `sandbox_lifecycle`（state、duration）、`artifact_put`/`artifact_get`（bytes）、`workspace_created`/`renamed`/`deleted`                                                                                                                                                                                                  | `services/workspace-service/workspace/services/sandbox.py:38`、`services/workspace-service/workspace/services/workspaces.py:17`                                                                                                                      |
| knowledge-service  | `ingest_finished`（chunks、duration）、`search_finished`（k、latency_ms、hits）                                                                                                                                                                                                                                          | 入库/检索服务层                                                                                                                                                                                                                                      |
| connector-service  | `connection_list`、`connection_revoked`、`credential_stored`                                                                                                                                                                                                                                                             | 连接器服务层                                                                                                                                                                                                                                         |

写法约定：`log.info("run_failed", run_id=..., stage=..., error_code=...)`——事件名是第一个位置参数（structlog 的 `event`），其余一律关键字。

---

## 11. 实施计划

### P0 结构化访问日志 + 请求上下文（零新依赖）

**状态：已完成**（`packages/common/common/middleware.py`、`packages/common/tests/test_middleware.py`）。实际落点：

- `RequestContextMiddleware`（纯 ASGI，不吃 SSE 流）+ 三个导出：`install_app_error_handler(app)`（替掉 7 份同构 handler）、`inject_request_headers`（httpx 出站钩子）、`current_traceparent()`。
- 7 个服务 `create_app` 注册中间件（BFF 放最后注册当最外层，限流 429 也有访问日志）；`project.json` serve 命令加 `--no-access-log`。
- 5 个服务 `get_tenant_id` 绑定 `tenant_id`/`user_id`；BFF `proxy._headers` 与 orchestrator/knowledge 的 7 个内部 httpx 客户端透传 `X-Request-ID`/`traceparent`。
- 测试 14 例覆盖：id 透传/生成/畸形替换、trace_id、route 模板与 unmatched、探针降级、慢请求、500 补访问行 + 带栈异常、AppError 分级、SSE open/close 帧数、上下文清理、出站头注入、`redact`。

改动面（原始计划）：

1. `packages/common/common/logging.py`：补 `bind_request_context()`/`clear_context()` 辅助、`redact()`；`setup_logging` 加 `format` 覆盖开关（本地想强开 JSON 时可用）。
2. 新 `packages/common/common/middleware.py`：`RequestContextMiddleware`（request_id/traceparent 解析与回写、contextvars 绑定/清理、访问日志、慢请求、未捕获异常日志）+ `AccessLogSettings` 字段（`access_log_enabled`、`slow_request_ms`）。
3. `packages/common/common/config.py`：`BaseAppSettings` 增 `access_log_enabled: bool = True`、`slow_request_ms: int = 1500`、`log_json: bool | None = None`。
4. 7 个服务 `create_app` 各接一行中间件（`services/*/*/main.py`），并给 `project.json` 的 serve 命令加 `--no-access-log`。
5. 各服务 `get_tenant_id` 里 `bind_contextvars(tenant_id=...)`；BFF/httpx 客户端透传 `X-Request-ID`。
6. 测试：`packages/common/tests/test_middleware.py`（生成/透传/回写 id、上下文清理、访问日志字段与级别、异常日志、探针降噪——用 `structlog.testing.capture_logs`）；各服务补一条 `/healthz` 降噪断言。
7. 文档：本节完成情况回填，`docs/local-dev.md` 补一句“排障时按 request_id 串日志”。

验收：同一请求跨 BFF → orchestrator → workspace 的日志共享一个 `request_id`；`grep request_id` 能还原整条时间线；未捕获异常有一条带栈的 `error`；探针不刷屏。

### P1 错误聚合 Sentry（可选依赖）

**状态：服务端已完成**（`common/sentry.py` + `common/observability.py` 装配；7 服务 `create_app` 一行接入）。落地内容见 §8：DSN 为空 no-op、4xx 丢弃、`data_collection` 收紧、`before_send` 脱敏 + 上下文 tag。
待办：Web 侧 `@sentry/react` + sourcemap（`apps/web`，需要单独排期）。

### P2 指标与追踪

**状态：指标已完成，追踪未做**。指标见 §9（`common/metrics.py`，`/internal/metrics`）；追踪仍按 `architecture-v2.md` §9 的“OTel 后置”再评估（GenAI semconv 仍实验性，埋点前先封一层属性映射，避免返工）。

---

## 12. 检索与告警建议

- 排障入口：浏览器 Network 里的 `X-Request-ID` → 在日志平台 `request_id="..."` 过滤 → 得到跨服务时间线。
- 常用查询：`event=http_request AND status>=500`；`event=http_request_slow`；`event=job_finished AND level=error`；`event=upstream_error`；`run_id="..."`（任务全生命周期）。
- 告警草案（阈值待压测后定）：

| 告警            | 条件（示例）                                        | 级别 |
| --------------- | --------------------------------------------------- | ---- |
| 5xx 比例        | 5 分钟内 `http_request.status>=500` / 总请求 > 1%   | P1   |
| 接口延迟        | `http_request.p95(duration_ms)` > 1.5s 持续 10 分钟 | P2   |
| job 失败        | `job_finished.level=error` 5 分钟内 > 3 次          | P1   |
| outbox 积压     | `/api/v1/outbox/counts` 的 pending 连续 10 分钟增长 | P1   |
| LLM 上游错误    | `upstream_error`（llm-gateway）5 分钟 > 5 次        | P1   |
| Sentry 事件激增 | 同一 fingerprint 5 分钟 > 10 次                     | P2   |

---

## 13. 验收清单

- [x] 所有 HTTP 请求有且只有一行 `http_request`（含 `request_id`、`route`、`status`、`duration_ms`）
- [x] 响应头回写 `X-Request-ID`；BFF 与 orchestrator 出站请求透传
- [x] 跨服务用同一个 `request_id` 可串联（至少 BFF → orchestrator → workspace 一条链路验证）
- [x] 未捕获异常 / `UpstreamError` / job 终态失败都有一条带 `exception` 的 `error` 日志
- [x] `/healthz`、`/readyz` 在 prod 级别下不产生 info 日志
- [x] 日志里没有 token/cookie/密码/请求体全文（抽样审计 100 行）
- [x] `<SERVICE>__SENTRY_DSN` 为空时无网络请求、无初始化副作用（`init_sentry` 直接返回 False，测试覆盖）
- [x] 内网形态（`<SERVICE>__LOG_JSON=true`、无 Sentry）可启动：真机冒烟通过
- [x] `/internal/metrics` 可被抓取，指标标签无高基数字段
- [x] 业务 4xx 不进 Sentry；5xx/未捕获异常进 Sentry 且请求数据已脱敏
