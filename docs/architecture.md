# beauty-moon 架构（现行唯一真源）

> **本文是唯一现行架构真源**（2026-10-24 起生效，取代 v1《微服务架构设计》与 v2《架构 v2》）。
> 旧版已归档至 [docs/archive/2026-10/](./archive/2026-10/)（architecture-v2.md 为本文基底、feature-gap.md、tasks-m0\*.md）。
> 实现与本文冲突时，先修订本文达成共识，再动代码。架构决策记录（ADR 001-011）见 §1。
> 部署与平台缺口（云 PG/Redis 接入等）在 [docs/roadmap.md](./roadmap.md) 跟踪，不在本文重复展开。

## 0. 最终选型（2026-10 拍板）

| 领域              | 生产                                                  | 开发环境                                     | 状态                                                                                                                 |
| ----------------- | ----------------------------------------------------- | -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Postgres          | **云数据库，提供 URL**（Neon/RDS/云 PG 均可）         | **本机 compose Postgres:16**（每服务独立库） | ✅ 已实施（生产 Neon 接入为 M0-03 待办；SQLite 仍可显式覆盖）                                                        |
| Redis             | **云 Redis，提供 URL**                                | **本地 Redis**                               | ⚠️ compose 已起、业务未接（限流仍进程内；R-P1-8 缺口）                                                               |
| 对象存储          | **暂不引入**                                          | 本地文件系统 / DB 列                         | ⏸ 暂缓（产物先 DB/文件系统，二进制附件受大小限制）                                                                  |
| 队列 / 工作流     | **Postgres job 表 + worker（SKIP LOCKED）**           | 同左（SQLite 覆盖时用文件/内存 worker）      | ✅ 已实施；Temporal / Inngest **暂不做**                                                                             |
| 沙箱              | **microsandbox provider（P1）**；local 仅本地兜底     | 本地 microsandbox / local / fake provider    | ✅ P1 底座 + P2 stdio MCP + P3 技能包（见 §4.4）                                                                     |
| 身份              | **自建 account-service**（邮箱+密码，补社交 OAuth）   | 同左（token 模式兜底）                       | ✅ 已实施；Logto **不用**（前端保留可选 logto 模式）                                                                 |
| 可观测            | **Sentry 官方服务** + 结构化日志 + **Langfuse Cloud** | 同左（本地日志）                             | ✅ 已实施；Langfuse 已接入（trace/generation span、ingested cost、scores）；Web 前端 Sentry 已接入                   |
| 支付              | **不做 Stripe**                                       | —                                            | ⏸ 暂缓；先 usage ledger + Free 配额；BYOK 加密存储 + 按账号注入已接（[design/byok.md](./design/byok.md)），订阅后续 |
| 事务邮件          | **暂不做 Resend**                                     | 本地 SMTP / 控制台日志                       | ⏸ 暂缓；站内通知优先                                                                                                |
| Web 托管          | **Nginx 托管静态 SPA**（同域反代 `/api`）             | 本地 Vite                                    | ✅ 已实施                                                                                                            |
| API 网关          | **FastAPI 服务**（同域 `/api`，:8007）                | 本地 uvicorn                                 | ✅ 已实施                                                                                                            |
| LLM 推理          | **继续用 API**（litellm 网关 :8003）                  | 本地/API                                     | ✅ 已实施                                                                                                            |
| 静态官网          | **Next.js 静态导出**（部署 Vercel，SSG/SEO）          | 本地 Next dev                                | ✅ 已实施                                                                                                            |
| 多区域 / 数据驻留 | 不做                                                  | —                                            | ⏸ 后续                                                                                                              |

### 0.1 两种部署形态（同一套代码）

| 形态                         | 适用               | Web/API 网关                                                                  | 数据                        | 外部依赖                                                                              | 状态                 |
| ---------------------------- | ------------------ | ----------------------------------------------------------------------------- | --------------------------- | ------------------------------------------------------------------------------------- | -------------------- |
| **A. SaaS（静态 SPA + 云）** | to C 公网          | Nginx 托管 SPA + FastAPI 网关；Next.js 官网部署 Vercel                        | 云 PG/云 Redis              | LLM API、Sentry（可选）                                                               | ✅ 当前              |
| **B. 内网/离线（Intranet）** | 私有化/内网/无外网 | **Linux VM/裸机 + systemd 原生进程 + Nginx**（不用 Compose/K8s）；**仅 HTTP** | SQLite + 本地目录；无 Redis | 内网 OpenAI 兼容模型（支持 function calling）；或 `LLM_ENABLED=false` 桩；Sentry 关闭 | **⏸ 暂缓（M0-13）** |

**硬性原则**

1. 内网形态**不依赖 Vercel/云 PG/云 Redis/Sentry/Logto/Stripe/Resend/Langfuse**；
2. API 网关用 FastAPI 实现并与 SPA 同域部署（Nginx 反代 `/api`），不依赖 Vercel Functions；
3. 前端产物必须可静态托管（无外部 CDN/字体/远程图片）；
4. 模型可指向内网 OpenAI 兼容端点；无模型时系统仍能启动（降级为确定性桩）；
5. 内网验收必须在**禁止外网 egress** 的环境下通过。

---

## 1. ADR（最终状态）

| ADR | 决策                                                                            | 状态                   | 说明                                                                                                                                                                                    |
| --- | ------------------------------------------------------------------------------- | ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 001 | Vite SPA + Next.js 独立静态官网                                                 | ✅ 采用                | SPA 静态托管；官网 Next.js 静态导出（Vercel）                                                                                                                                           |
| 002 | 静态 SPA + FastAPI 网关 + 容器 runtime                                          | ✅ 采用                | 八个 Python 服务（七业务 + API 网关）；API 网关为独立 FastAPI 服务；容器化已落地——统一镜像 `deploy/docker/python-service.Dockerfile` + `deploy/docker-compose.yml`（R-36 关闭）         |
| 003 | 长任务用 Postgres job 表 + worker                                               | ✅ 采用                | 不引入 Temporal/Inngest；复用现有 outbox 与 checkpointer                                                                                                                                |
| 004 | Sandbox 用 microsandbox provider                                                | ✅ P1 + P2 + P3        | 见 §4.4；stdio MCP、技能包上传/执行已落地                                                                                                                                               |
| 005 | 数据：云 PG + 云 Redis；对象存储暂缓                                            | ✅ 采用                | 开发 SQLite/本地 Redis/本地文件（2026-11-12 起本地默认改为 compose Postgres，SQLite 降为显式覆盖）；**Redis 业务接入未完成（限流仍进程内，R-P1-8）**                                    |
| 006 | 身份：自建 account-service（不用 Logto）                                        | ✅ 采用                | 需补 Google/GitHub 社交登录；前端鉴权保留可选 logto 模式（`VITE_AUTH_MODE=logto`）作为切换开关                                                                                          |
| 007 | API 网关 httpOnly Cookie + 短时 JWT                                             | ✅ 采用                | 防 XSS、收敛跨域；API Key 场景除外                                                                                                                                                      |
| 008 | 计费：usage ledger + Free 配额 + BYOK                                           | ✅ 采用（Stripe 暂缓） | 订阅/发票/税后续再接；BYOK 已接入：gateway 按账号解析、失败回退平台 key（06 P0-6 ✅ 2026-10-26，见 [design/byok.md](./design/byok.md)）                                                 |
| 009 | MCP Server + Client                                                             | ✅ 采用（M3）          | 开放平台核心                                                                                                                                                                            |
| 010 | Tool Registry + Skill + Workspace 类型                                          | ✅ 采用                | coding/办公共用运行时                                                                                                                                                                   |
| 011 | 内网/离线部署可启动                                                             | ⏸ 暂缓                | 当前只要求本地启动；方案见 §10.3，恢复时再实施                                                                                                                                          |
| 012 | 部署主线：Vercel 静态层 + 腾讯云轻量（首尔）单机 Compose + Neon；沙箱外置 Modal | ✅ 采用                | 2026-11-11 定稿，理由/改造/成本见 [design/hybrid-deployment.md](./design/hybrid-deployment.md)；Cloud Run/Fly 降为备选（[serverless-deployment.md](./design/serverless-deployment.md)） |

---

## 2. 目标拓扑

```text
Browser
├── app.beautymoon.cn       Nginx: apps/web 静态产物
│      └── /api/*           FastAPI 网关 :8007（同域反代）
│            ├── 登录/登出/会话 Cookie（account-service 为 IdP）
│            ├── 代理 runtime（短请求 + 短流）
│            ├── 产物/附件下载（DB/本地存储 → 流式响应）
│            └── 限流/错误契约/审计头
├── site.beautymoon.cn      Vercel Project: apps/site（Next.js 静态导出）
└── 长流直连                runtime.beautymoon.cn（SSE，API 网关发短时 stream token）

容器平台（Fly.io / Cloud Run / 自有云主机；容器化已落地，见 deploy/，R-36 关闭）
├── agent-runtime（agent-orchestrator）  编排 + Skill + MCP + run worker                  :8002
├── tool-service            内置工具服务：catalog/执行/审计/ask_question（沙箱经 workspace） :8008
├── llm-gateway             模型路由/白名单/降级/预算/usage 采集（API 上游）              :8003
├── connector-service       连接器平台:OAuth/静态令牌两种连接方式、凭证加密/连接生命周期(当前无内置 provider)（design/connector.md） :8004
├── workspace-service       工作区/产物元数据 + 沙箱生命周期 + 内容存储（DB/本地文件）  :8005
├── knowledge-service       摄取/检索/重排（JSON 向量，无需 pgvector）                :8006
└── account-service         账号/会话/配额/usage/API Key（自建 IdP）                 :8001

云托管（只保留 URL 型依赖）
├── 云 Postgres             生产库（URL 注入；开发用本机 compose Postgres）
├── 云 Redis                限流/锁/短期缓存（URL 注入；开发本地 Redis；业务接入待办）
└── Sentry                  前端/服务错误（官方 SaaS）

开发环境
├── compose Postgres:16     每服务独立库（deploy/docker-compose.yml；SQLite 可显式覆盖）
├── 本地 Redis              可选（未配置时降级为进程内限流）
├── 本地目录                artifact_store=filesystem（默认）
└── 本地 SMTP               事务邮件联调（生产暂不做）
```

**流量原则**

- 普通 CRUD / 短请求：浏览器 → Nginx → FastAPI 网关 → 容器服务；
- 长任务事件流：API 网关发短时 token，浏览器直连 runtime SSE（避免 API 网关/代理超时）；
- 公开 API / MCP：第三方 → api.beautymoon.cn（API Key/OAuth，限流）；
- 服务间：内部 HTTP + 账号 JWT；禁止跨服务直连数据库。

---

## 3. 服务与职责（八个 Python 服务 + 三个共享库 + 前端三应用）

| 服务 / 应用          | 部署 / 端口           | 职责                                                                                                                                                                   | 备注                                                                                                                                                                             |
| -------------------- | --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| apps/web             | Nginx 静态 / dev 5173 | Vite SPA 控制台（暗色控制台：工作台/任务编排/变更产物/知识库/工作区中心/事件队列/设置）                                                                                | 鉴权 token/session/logto 三模式（`VITE_AUTH_MODE`，默认 token）                                                                                                                  |
| apps/site            | Vercel 静态           | Next.js 官网、文档、博客、Changelog                                                                                                                                    | 静态导出（`output: 'export'`）                                                                                                                                                   |
| apps/desktop         | Electron 44           | 桌面应用（本地电脑模式：进程/文件/命令在本机执行）                                                                                                                     | 与 web 云端模式并存可切换                                                                                                                                                        |
| services/api-gateway | 容器 :8007            | API 网关：认证、代理、下载、限流                                                                                                                                       | FastAPI 同源 `/api`；限流目前进程内（Redis 后接）                                                                                                                                |
| agent-orchestrator   | 容器 :8002            | 会话/run/skill/MCP/worker/对话内写操作确认(confirm/cancel);MCP 预设(Notion 一键授权)与工具 annotations 风险分级                                                        | 队列为 PG job 表；事件溯源 + SSE 断线续传                                                                                                                                        |
| tool-service         | 容器 :8008            | 内置工具服务：工具目录/执行/审计/ask_question（沙箱在 workspace-service 中执行）                                                                                       | catalog 真源 `common/tools.py` 的 `ToolDescriptor`；设计见 [tool-service.md](./design/tool-service.md)                                                                           |
| llm-gateway          | 容器 :8003            | 模型唯一出口、白名单、路由、降级（fallback）、预算、usage                                                                                                              | BYOK（按账号注入）、计量、预算；`response_format`/多模态/`FallbackHop` 校验见 §11.5（P1-G）                                                                                      |
| connector-service    | 容器 :8004            | 连接器平台：OAuth 授权会话、凭证加密、按账号连接、刷新单飞 + 24h 巡检、outbox 生命周期事件；支持 OAuth 授权与静态令牌两种连接方式；**当前无内置 provider**（目录为空） | 2026-10 邮箱/日历能力下线；2026-11 平台完成、内置 Notion provider 上线后于 2026-11-04 二次规划移除（Notion 改走官方 MCP，见 §8.2）；设计见 [connector.md](./design/connector.md) |
| knowledge-service    | 容器 :8006            | 摄取、检索、重排、引用                                                                                                                                                 | 嵌入提供方可插拔（本地词法/网关）；记忆能力在 agent-orchestrator，见 [memory.md](./design/memory.md)                                                                             |
| workspace-service    | 容器 :8005            | 工作区、产物、附件元数据、沙箱生命周期                                                                                                                                 | 暂用 DB/本地文件；对象存储后置                                                                                                                                                   |
| account-service      | 容器 :8001            | 账号/配额/usage/API Key/自建认证                                                                                                                                       | 注册/邮箱密码登录/旋转刷新令牌/内部签发 + JWKS；社交 OAuth 待补                                                                                                                  |
| packages/common      | 库                    | 错误模型/配置/日志/JWT/存储/观测                                                                                                                                       | Python 共享库                                                                                                                                                                    |
| packages/database    | 库                    | Base/mixin/session_scope/Repository/UoW                                                                                                                                | 仓储层账号隔离 + 统一 404                                                                                                                                                        |
| packages/events      | 库                    | 事件信封/总线/outbox/dispatcher                                                                                                                                        | 事务性 outbox + 消费幂等                                                                                                                                                         |
| packages/design      | 库                    | React 组件库（bd- BEM + --bd-\* 令牌）                                                                                                                                 | web/site/desktop 共用                                                                                                                                                            |

> 拆分节奏：M0 保持七业务服务 + FastAPI 网关（tool-service 已按 [tool-service.md](./design/tool-service.md) 拆出，承接 orchestrator 的 tool registry；沙箱生命周期仍归 workspace-service）；M3 再拆 platform-api（先复用 runtime 路由）。

---

## 4. Monorepo 结构与命名

```text
beauty-moon/
├── apps/                     # ★ 终端 UI（npm workspaces）
│   ├── web/                  #   Vite SPA 控制台（React 19 + react-router v8 + Tailwind v4）
│   ├── site/                 #   Next.js 官网（App Router，静态导出，部署 Vercel）
│   └── desktop/              #   Electron 桌面应用（本地电脑模式）
├── services/                 # ★ 微服务：部署单元（uv workspace 成员，FastAPI）
│   ├── account-service/      #   :8001
│   ├── agent-orchestrator/   #   :8002
│   ├── llm-gateway/           #   :8003
│   ├── connector-service/    #   :8004
│   ├── workspace-service/    #   :8005
│   ├── knowledge-service/    #   :8006
│   ├── tool-service/         #   :8008 内置工具服务（catalog/执行/审计）
│   └── api-gateway/          #   :8007 同源 /api 门面
├── packages/                 # ★ 共享库：实名命名（不用 py- 前缀）
│   ├── common/               #   错误模型/配置/日志/JWT/存储/观测（Python）
│   ├── database/             #   Base/命名约定/Repository/UoW/testcontainers（Python）
│   ├── events/               #   事件信封 + 总线 + outbox（Python）
│   └── design/               #   React 组件库（JS，bd- BEM + --bd-* 令牌）
├── tools/
│   └── eslint-config/        # ESLint 9 flat config 工厂
├── scripts/                  # dev.sh / seed-local.sh / reset-local.sh / refactor-gate.sh
├── deploy/                   # docker-compose.yml（pg/redis）+ web/nginx.conf
└── docs/                      # 文档（README.md 为索引）
```

**放置判据**：

- `services/`：独立部署单元——有 Dockerfile（8 个 Python 服务复用 `deploy/docker/python-service.Dockerfile`）、被 compose/k8s 编排的长驻进程（暴露 API 或消费队列，纯 worker 也算）；
- `apps/`：人直接使用的终端 UI；
- `packages/`：被 import 的库（不部署）。

**命名护栏**（Python 顶层 import 没有作用域）：

- 避开 stdlib 模块名（如 `platform`）与主流库顶层名（如 `agents` 已被 openai-agents 占用）；
- 逃生舱：将来可用 PEP 420 命名空间包 `beauty.*`（对应 JS 侧 `@beauty/` 作用域语义）。

**uv workspace 注意**：members 为**显式清单**——`apps/` 与 `packages/` 混有 npm 项目（web/site/desktop/design 无 `pyproject.toml`），glob 会命中无 pyproject 的目录导致 uv 报错（见 [uv workspace 文档](https://docs.astral.sh/uv/concepts/projects/workspaces/)）。新 Python 项目必须在根 `pyproject.toml` 登记 members + dependencies + `[tool.uv.sources]`，随后 `npx nx run <项目>:sync`（注意：`npx nx sync <项目>` 会被 Nx 内置命令抢占而不执行 uv sync，必须用显式 run 形式）。

---

## 5. Web 架构（Vite SPA + API 网关）

### 5.1 项目结构

```text
apps/web             Vite SPA（暗色控制台；src/pages + src/components/console + src/lib 数据层）
  └── src/          页面/组件/数据层（@beauty/design 组件 + @beauty/design/tailwind.css 令牌）
apps/site            Next.js App Router（官网、文档、博客；静态导出）
apps/desktop         Electron（主进程 src/main/local/，本地电脑模式）
services/api-gateway FastAPI 网关（同域 /api）
packages/design      组件库（bd- 前缀 BEM + --bd-* 令牌）
```

- SPA 静态产物由 Nginx 托管，`/api` 反代到 FastAPI 网关；
- 官网独立项目，Next.js 静态导出 + sitemap/RSS，部署 Vercel；
- 环境变量拆分：公开配置（`VITE_*` / `NEXT_PUBLIC_*`）与服务端 secret；
- **模型唯一出口是 llm-gateway**（P1-G）：前端只经 `GET /api/v1/models` 读可用模型（60s 缓存），orchestrator 内网直连网关；白名单、主备 fallback chain、`response_format` 与多模态（图片 `image_url` data URL）校验都在网关完成，调用方无感，见 §11.5。

### 5.2 认证与会话

- 登录：account-service 自建 IdP——注册 + 邮箱密码登录（argon2id）+ **旋转式刷新令牌**（旧令牌一次性，重用即吊销该用户全部）+ 内部签发 `POST /v1/auth/issue` + OIDC 发现/JWKS（签名密钥经 `SIGNING_KEY_PATH` 持久化）；**不用 Logto**（前端保留 `VITE_AUTH_MODE=logto` 作为可选模式）；社交 OAuth（Google/GitHub）待补；
- API 网关回调后建立 **httpOnly, Secure, SameSite=Lax Cookie**；SPA 不接触 refresh token（session 模式）；
- API 网关调 runtime 时签发短时服务 JWT（account/sub/scope）；服务间内部调用一律经 TokenBroker（账号来自签名 claim，无 `X-Account-Id` 裸调路径）；
- 开放平台/API Key 请求绕过 API 网关，直连 public API，用 API Key → 短时 JWT；
- 本地开发保留 token 模式兜底（`VITE_AUTH_MODE=token|session|logto`，默认 token）；`AUTH_ENABLED=false` 时认 `X-Account-Id` 仅保留为显式关闭鉴权的部署语义。

### 5.3 流式

- API 网关以 FastAPI 实现，Nginx 同域反代 `/api`；一套实现同时服务 SaaS 与内网；
- 短对话/短流：API 网关代理 SSE（FastAPI StreamingResponse）；
- 长任务：API 网关发 `streamToken`（短时、单 run），浏览器直连 runtime SSE；
- 断线重连带 `Last-Event-ID`；Redis 做 fan-out（可选），Postgres 做重放；
- 不做 WebSocket；多端同步后续再评估。

### 5.4 页面地图（现行控制台）

| 区域       | 页面                                                                                                              |
| ---------- | ----------------------------------------------------------------------------------------------------------------- |
| 工作台     | 会话工作台（SSE 流式：meta/delta/done；审批就地确认）、附件（受限）、@ 引用（只读）、/ 命令、模型选择、执行面板   |
| 任务编排   | 看板、运行详情、计划/Todo/Goal、审批（/runs）                                                                     |
| 变更与产物 | Diff 评审、产物预览（Markdown/图片；PDF/Office 后置）、下载/分享（/changes）                                      |
| 工作区     | 工作区中心（清单/新建/重命名/删除、会话与交付物归属、一行切换器；/workspace）、文件树（只读）、Git 状态、补丁建议 |
| 知识库     | 文档入库/清单/检索预览（/knowledge）、引用溯源                                                                    |
| 开发者     | API Key、MCP（HTTP/stdio 面板）、Skills（上传/版本/删除）、Webhook、调用日志、用量（/outbox 事件队列运维）        |
| 设置       | 账号、安全、模型/BYOK、权限、会话（/settings）；订阅/发票后置                                                     |
| 官网       | 独立部署（apps/site，深链 /landing 跳转过去）                                                                     |

---

## 6. 数据与存储

| 数据                                                             | 生产                             | 开发               | 说明                                                   |
| ---------------------------------------------------------------- | -------------------------------- | ------------------ | ------------------------------------------------------ |
| runs / run_events / conversations / chat_messages / instructions | 云 Postgres                      | 本机 PG            | 事件 append-only；seq 单调；SSE 断线回放从 Postgres 读 |
| connections / credentials                                        | 云 Postgres                      | 本机 PG            | 凭证加密暂用应用层密钥；KMS 后置                       |
| workspaces / artifacts / sandboxes                               | 云 Postgres + 内容存 DB/文件系统 | 本机 PG + 本地目录 | 对象存储后置；单实例 filesystem 可用                   |
| knowledge_documents / chunks                                     | 云 Postgres（JSON 向量）         | 本机 PG            | 现有实现不依赖 pgvector，可直接跑                      |
| orgs / members / quotas / usage_ledger / api_keys                | 云 Postgres                      | 本机 PG            | usage ledger 新增；Stripe 后置                         |
| session / rate limit / locks                                     | 云 Redis（**业务未接，R-P1-8**） | 本地 Redis（可选） | 未配置时降级为进程内实现                               |
| jobs                                                             | 云 Postgres（job 表）            | 本机 PG job 表     | 队列与重试；worker 轮询                                |

**开发/生产一致性**

- 开发：本机 compose Postgres:16，每服务独立库（见 `deploy/docker-compose.yml`）；离线/测试可显式改回 SQLite，`AUTO_CREATE_TABLES=true` 仅作兜底建表；
- 生产：云 Postgres + Alembic 迁移（现有迁移未使用 PG 专属类型，SQLite 兼容）；
- CI 必须对**云 PG**跑一遍迁移 + 集成测试，避免「SQLite 过、PG 挂」；
- 多实例部署时 **filesystem artifact store 不可用**，必须用 database 模式或共享盘；对象存储接入后替换。

---

## 7. 认证、配额、计费（Stripe 暂缓）

- **身份**：account-service 维持自建 IdP（邮箱密码 + 计划补 Google/GitHub OAuth）；各服务沿用 JWT/JWKS 验签（同一个 `JwtVerifier`，不设后门；换 IdP 时各服务只改 `AUTH_ISSUER`）；
- **会话**：API 网关 httpOnly Cookie；服务间短时 JWT；API Key 独立通道；
- **Free 配额**：`usage_ledger` 记录 token 用量（tool call / 存储维度待补，见 06 P0-1）；按月聚合 → 配额检查（402/429）；
- **BYOK**：账号级 provider key 经**信封 v2 加密**存储，KMS 可切 `local` / Vault Transit；llm-gateway 按 `X-Account-Id` + `X-Internal-Secret` 向 account 的内部端点解析并短时缓存（300s / 负缓存 30s），**失败一律回退平台 `settings.api_key`**；账号 key 只覆盖 `api_key`、不覆盖 `api_base`；BYOK 不扣模型 credits（用量来源尚未在 `usage_events` 区分，属 06 P0-1）。契约见 [design/byok.md](./design/byok.md)；
- **订阅/支付**：Stripe 暂不做；先只支持 Free 档 + 手动开通内部档；付费自助后续补；
- **限流**：Redis（**当前进程内实现，Redis 接入待办 R-P1-8**）或进程内降级；账号/IP/Key 三级；Turnstile 后置；
- **公开分享**：不可猜测 token、可撤销、可过期（后置）。

---

## 8. 开放平台与 MCP（M3）

### 8.1 Public API

> 状态（2026-10-25）：以下为**目标态**。当前只有基础 runs/events/artifacts/usage 只读接口——runs 列表无 cursor、无幂等键/Webhook/SDK（见 06 §2.3 #8）。

- `/v1`：conversations、runs、events(SSE)、artifacts、skills、tools、usage；
- 鉴权：OAuth 2.1（第三方应用）或 API Key（服务端调用）；scope 最小化；
- 幂等键、分页 cursor、统一错误 `{code,message,details}`、限流头；
- Webhook：`run.completed`/`run.failed`/`artifact.created`/`usage.threshold`，签名 + 重试；
- SDK：TypeScript/Python（OpenAPI 生成）+ 示例仓库。

### 8.2 MCP

- **MCP Server**（对外）：streamable HTTP + OAuth 2.1；暴露 tools（docs/kb；沙箱工具 P2）与 resources（artifacts/files）；账号隔离、权限策略已实现；**审计与提示注入过滤未实现**（见 06 §2.3 #9）；
- **MCP Client**（对内）：账号配置第三方 MCP server（GitHub、Notion、飞书、Slack…）；工具命名空间、超时、失败降级、审批；
  - **内置预设 + 一键授权**（2026-11-04）：`GET /v1/mcp/presets` 暴露静态预设（当前 `notion` → `https://mcp.notion.com/mcp`），`POST /v1/mcp/presets/{key}` 幂等 ensure server，`POST /v1/mcp/servers/{id}/oauth/start` 接受 `next`（主路径 `/connectors`；`/mcp` 仅兼容直链）决定回调落点；`/connectors` 页是 MCP 唯一入口（预设卡片 +「我的 MCP 服务」区 + 旧 provider 目录区，侧栏不再单列 MCP，旧 provider 区只在存在 provider 时渲染）；
  - **HTTP 型**（M1 已落地）：orchestrator 进程内 httpx（Streamable HTTP），后续补 SSRF/出网代理加固；
  - **stdio 型**（P2 已落地）：命令在 workspace-service 沙箱内拉起，经 stdin/stdout JSON-RPC
    执行（`stdin MCP` 不能进 orchestrator 进程）；沙箱按**对话级**复用
    （`ToolContext.conversation_id`，无对话退化为 run），命令白名单 + 参数不经 shell；
    `env/token` 只存密文，读端只回 `has_env/env_keys`；P2 出网 `public`（允许 npx/uvx 安装），
    P4 切 allowlist/凭据注入；
  - **风险分级**（2026-11-04，工具粒度）：`annotations.destructiveHint=true` → DANGER；
    `annotations.readOnlyHint=true`，或 server.url 命中内置预设且工具名命中预设只读前缀 → READ；
    server.auto_approve → WRITE；其余 → DANGER。对话路径按 `kind=function && risk!=danger` 裁剪工具面，
    WRITE 落 `chat_tool_confirmations` 出就地确认卡（§6.x）；
  - **审批策略**（已落地）：每个 server 一个 `auto_approve` 信任开关（连接器页「我的 MCP 服务」区
    「免确认执行」，默认关）。关：未判为只读的工具在对话里不暴露、run 里按 DANGER 走确认闸；
    开：这些工具降为 WRITE——run 不再逐次确认、对话里出确认卡后执行——对齐
    操作策略「是配置，不是硬编码」；
- **安全**：MCP 返回内容视为不可信数据；提示注入过滤**待补**；危险工具默认 confirm；`annotations` 由 server 自述、不可全信（预设只读前缀只对内置预设生效，其余 server 仍需显式信任 + 确认）；凭证只存在服务端；
  stdio 进程只允许在沙箱内运行，沙箱内 env 明文对用户代码可见（最小 scope + P4 凭据代理）；
- **市场**：后续 MCP server 目录 + 一键安装（M4）。

### 8.3 Skill / Plugin 注册表

- manifest：id/version/description/workspaceType/tools/permissions/deliverables/pricing；
- 发布/审核/灰度/回滚；运行时按账号启用；
- 开放平台开发者可提交 Skill；与 MCP server 一起构成插件市场。
- **P3 已落地（上传包）**：账号上传 SKILL.md zip（可选 skill.json entrypoint），
  对象存储 + 版本表（slug+version 不可变），run 创建时冻结版本快照；
  有 entrypoint 的包在沙箱内执行（skill_run 工具，WRITE 级受控自动执行），无 entrypoint 的包作为提示词技能；
  Web Skills 页支持上传/版本查看/删除。开放平台的审核/灰度/计费仍在 M4。

---

## 9. 观测、质量与安全

- **错误**：Sentry 官方（前端 + 服务），PII 脱敏后上报；
- **日志**：结构化日志 + traceId（沿用 `common.logging`）；OTel 已接入（`packages/common/common/tracing.py`，OTLP + httpx 插桩，GenAI semconv 部分）；**Langfuse Cloud 已接入**（`common/langfuse.py` + `common/tracing.py`，见 `docs/design/langfuse-observability.md`）；
- **观测规范**：字段字典、`request_id` 跨服务贯穿、访问/异常日志规格、Sentry 接入与告警草案见 [observability.md](./observability.md)；
- **产品分析**：PostHog 后置（可用 Sentry + 自建事件表过渡）；
- **Evals**：golden dataset + grader；PR 门禁（M4）；
- **安全**：沙箱 P1/P2 受控使用（stdio MCP 有开关 + 命令白名单 + 沙箱隔离）；用户上传 skill 包执行 P3 已开；凭证加密；账号隔离测试；输入/输出审查后置；
- **审计**：run_events 即审计流水（append-only、seq 单调、可回放）；导出后置。

---

## 10. 部署与迁移

### 10.1 环境

| 环境    | Web                                    | Runtime                                | 数据                                                |
| ------- | -------------------------------------- | -------------------------------------- | --------------------------------------------------- |
| dev     | 本地 Vite + 本地 API 网关              | 本地进程（dev.sh）                     | 本机 compose Postgres + 本地 Redis(可选) + 本地目录 |
| staging | Vercel preview（SPA + `/api` rewrite） | 腾讯云轻量（首尔）单机 Compose + Caddy | Neon staging 6 库 + 本地卷 artifact                 |
| prod    | Vercel（SPA + `/api` rewrite）         | 腾讯云轻量（首尔）单机 Compose + Caddy | Neon prod 6 库 + 本地卷 artifact（COS 后置）        |

- CI：lint/typecheck/test/build → **对云 PG 跑迁移与集成测试** → 部署 preview → smoke/e2e；
- 服务镜像：8 个 Python 服务复用 `deploy/docker/python-service.Dockerfile`（`SERVICE` 选入口）+ `deploy/docker/web.Dockerfile`；compose 含 migrate job 与健康检查；CI 的 `docker-build` job 验证构建；
- 回滚：镜像 tag + Vercel 即时回滚 + DB 迁移前进/后退检查；
- 成本护栏：单机固定成本 + Neon 按量计费、Free 配额、预算告警。

### 10.2 从当前仓库迁移（M0 路径）

1. ✅ 新增 `services/api-gateway`（FastAPI）与 `apps/site`（Next.js）；`apps/web` 只构建静态产物，由 Nginx 托管；
2. ✅ 八个服务已容器化（统一镜像 + compose 全量编排，:8080）并纳入 CI `docker-build`；生产编排见 `deploy/tencent/`（轻量单机 + Caddy + 8 服务 Compose，2026-11-11）；内网另加 systemd 单元 + 发布包（M0-13 暂缓）；本地开发保留 compose；
3. ⏳ 接入云 PG（Neon 6 库）/ 云 Redis URL——**M0-03 进行中**，配置样例见 `deploy/tencent/.env.example`；开发默认本机 compose Postgres/本地 Redis；产物改 database 模式以支持多实例；
4. ⏳ account-service 补 Google/GitHub OAuth + API 网关 Cookie 会话（保留 token 模式开发兜底）——API 网关 Cookie 会话已落地，社交 OAuth 待补；
5. ✅ `jobs` 表 + worker，run 启动/恢复走队列（不引入 Temporal）；
6. ✅ 前端 SSE 带 `Last-Event-ID` + 自动重连；
7. ✅ 补 Markdown/会话管理/产物预览（DB/文件版）等体验闭环；
8. ⏳ 灰度：内部 dogfood → Free 内测 → 公开注册。

### 10.3 内网/离线部署（Intranet Profile）——⏸ 暂缓，不纳入 M0

> 当前只要求**本地可启动**；本节保留为后续内网交付参考，恢复时再按 M0-13 子任务执行
> （完整方案见归档的 [tasks-m0-13.md](./archive/2026-10/tasks-m0-13.md)）。

**目标**：在完全无外网（或仅允许内网模型/依赖源）的环境中，用 **Linux VM/裸机 + systemd 原生进程 + Nginx** 启动完整系统；**不使用 Docker Compose / Kubernetes**。

**组成**

```text
/opt/beauty/releases/<version>/       发布包（服务代码 + venv/wheels + web/node 产物）
/opt/beauty/current -> releases/<v>   当前版本软链（升级/回滚切换）
/etc/beauty/*.env                     EnvironmentFile（600）
/etc/beauty/secrets/                  JWT/内部密钥/API 网关 session 密钥（600）
/var/lib/beauty/<service>/*.db        SQLite
/var/lib/beauty/artifacts/            产物/附件（filesystem）
/var/backups/beauty/<date>/           DB + artifacts 备份
/etc/systemd/system/beauty-*.service  七业务服务 + API 网关 + 迁移（oneshot）
/etc/nginx/conf.d/beauty.conf         HTTP 静态站 + /api 反代（SSE 不缓冲）
```

**必须满足**

1. **无 Vercel/容器编排**：API 网关以 FastAPI 进程由 systemd 管理；Nginx 托管 SPA 静态产物并反代 `/api`；SPA 深链 rewrite 由 Nginx 完成；
2. **无外部 CDN/字体/图片**：构建产物自包含；
3. **模型可配**：`LLM_GATEWAY__API_BASE`、`LLM_GATEWAY__API_KEY`、`LLM_GATEWAY__DEFAULT_MODEL`（litellm 透传 `api_base`/`api_key`）；未配置且 `LLM_ENABLED=false` 时可启动（桩）；内网模型**支持 function calling**；
4. **数据本地**：SQLite + 本地目录；Redis 不配置（进程内限流/锁）；产物 `ARTIFACT_STORE=filesystem|database`；
5. **可选依赖默认关闭**：Sentry、社交 OAuth、Stripe、Resend、Langfuse；
6. **鉴权本地可用**：仅 account-service 邮箱密码（无内网 OIDC/LDAP）；**HTTP 内网 Cookie `API_GATEWAY__COOKIE_SECURE=false`** + SameSite=Lax + CSRF + 短会话 + 防火墙/IP allowlist；
7. **内网构建/交付**：Python 走内网 PyPI（或 wheelhouse），Node 走内网 npm；发布包 + 校验和；无容器镜像依赖；
8. **验收**：在禁止外网 egress 的 VM 上，安装脚本执行后 `systemctl status beauty-*` 全绿，登录 → 会话 → 流式（桩或内网模型）→ 上传 → 产物 → 搜索可用；日志无外网请求失败。

**注意**

- **不允许 Compose/K8s**：用 systemd 管进程；若连 systemd 也不允许，需要改为 supervisor/手工守护（待确认）；
- **不支持 HTTPS**：Cookie Secure=false，HTTP 下凭证/Cookie 明文传输；必须靠内网隔离、Nginx 仅内网监听、CSRF、短会话、防火墙/IP allowlist 补偿，并在文档中声明风险；
- **单机 SQLite 无 HA**：systemd 自动重启 + 备份/快速恢复；多机/高并发需 PG（后续 I3）；
- 无对象存储时附件限制 10MB。

**环境变量补充（内网）**

```text
API_GATEWAY__COOKIE_SECURE=false  # 无 HTTPS 内网；上线 HTTPS 后改 true
LLM_GATEWAY__API_BASE=...         # 内网 OpenAI 兼容地址
LLM_GATEWAY__API_KEY=...
LLM_GATEWAY__DEFAULT_MODEL=openai/<model>
SENTRY_ENABLED=false
SOCIAL_LOGIN_ENABLED=false
ARTIFACT_STORE=filesystem         # 单机；多机改 database/共享盘
DATABASE_URL=sqlite+aiosqlite:////var/lib/beauty/app/app.db
REDIS_URL=                      # 留空 = 进程内降级
```

### 10.4 部署边界与账号

- **账号即隔离边界**：`account_id = users.id`（32 位 hex）；一个部署可服务多个账号，但账号之间没有共享业务数据；
- 账号私有表一律混入 `AccountMixin`，查询经 `AccountRepository` 按 `account_id` 过滤，**跨账号一律 404**（不泄漏存在性）；
- 账号只来自签名 claim（服务 JWT 的 `account_id`）或内部调用头 `X-Account-Id`（仅 `AUTH_ENABLED=false`）；前端从不传账号标识；
- 部署层面仍是同一套代码的两种形态（SaaS / 内网单机）：**账号是数据边界，部署是运行边界**——不再有组织、成员、邀请、角色、套餐等产品层概念。

---

## 11. Agent 执行模型（核心）

### 11.1 统一 run 生命周期

```text
message → triage → (chat | run)
run: pending → planning → awaiting_approval(按需:无 DANGER 工具时自动放行) → running
     → awaiting_confirmation → completed / failed / blocked
     → awaiting_input（模型提问）── answer/超时 ──> running → …
     任一非终态 ──cancel──> cancelled
```

> 状态注记（2026-10-25，P0-2 已落地）：`planning` 在进入规划节点时赋值、`running` 在放行执行（人工审批放行/确认通过）时赋值，两者**不落独立事件**；`cancelled` 为新增终态，由 `POST /api/v1/runs/{id}/cancel` 触发。取消是**协作式**：立刻落终态并撤掉排队中的 job，不打断执行中的图，图的结果在下一个执行边界（`run_events.is_cancelled`）丢弃；取消与完成同刻的竞态仍可能让结果先落库（并入 06 P0-3 多实例残余）。中断超时由 sweeper 兜底（`APPROVAL_TIMEOUT_SECONDS` / `CONFIRMATION_TIMEOUT_SECONDS` / `QUESTION_TIMEOUT_SECONDS`，前两者默认 0=关闭、生产建议审批 86400s / 确认 600s，提问默认 900s，依赖 `JOBS_ENABLED`）：审批超时自动拒绝计划转 blocked，确认超时自动拒绝危险写并继续验收，提问超时按 decline 自动回答并继续执行。完整状态时间线（`run.status_changed.v1` 事件）仍缺，见 07-run-step-model.md；节点级步骤投影（S1）已于 2026-10-25 落地，见 §11.5。

- 执行引擎 **LangGraph + PG checkpointer**（Postgres 默认用 `AsyncPostgresSaver`；SQLite 覆盖/未配置时用 InMemorySaver）；
- run 启动/恢复走 **PG job 表 + worker**：入队 → worker 领取（`FOR UPDATE SKIP LOCKED`）→ 执行；**心跳与 lease 续租未实现**（job 60s lease 无续租，多实例下存在重复领取风险，见 06 P0-3）；
- 同一 run 同一时刻只由一个 worker 执行（领取时行锁；lease 无续租），崩溃后按 checkpoint 恢复；
- **plan**：产出可编辑的 Markdown 计划 artifact（存对象存储，画布上直接改），不是 yes/no 门；
- **confirm**：按需人审中断——装配工具里没有 DANGER 级（未信任 MCP、对外不可逆写）时计划生成后自动放行（`AGENT_ORCHESTRATOR__PLAN_AUTO_APPROVE`，默认 true），有 DANGER 工具才挂起；
- **cancel**：`POST /api/v1/runs/{id}/cancel`（body `{reason?}`）——非终态均可取消，落 `cancelled` + `status_reason`、撤掉 queued job、写 `run.cancelled.v1` 事件；终态再取消回 409 `run.invalid_state`；
- **ask_question**：模型提问走图内 `ask_gate` 中断 → run 进 `awaiting_input` + `run.question.required.v1`；`POST /api/v1/runs/{id}/answer`（`{question_id, action: answer|decline, answers}`）恢复图；一次 assistant 回合只能提问且独占，提问期间其余工具不执行；
- **中断超时**：`interrupts` sweeper 周期扫描 `awaiting_approval` / `awaiting_confirmation` / `awaiting_input` 超时项，按 `run.interrupt.timeout.v1` 留痕后复用 reject/confirm/answer job 走正常状态机（不直接改状态，保留 resume 语义）；确认超时同时补写 `run.confirmation.received.v1`，提问超时补写 `run.question.timeout.v1` 与 `run.question.answered.v1`（decline/timeout/system）；
- **verify**：对 `acceptance_criteria` 逐项校验，不达标自动返工，上限 3 轮；
- **deliver**：交付物注册（一个 run 多件：报告/技能产物/工具外链文件）；写操作按风险等级执行。

### 11.2 Tool Registry（coding 与办公共用）

```text
ToolDescriptor {   # v2 契约，真源 packages/common/common/tools.py
  name, description,
  input_schema, output_schema?,
  risk: read | write | destructive,
  kind: function | interaction,
  executor: sandbox | http | none,
  timeout_seconds,
  scopes, aliases, enabled, version
}
```

> 状态注记（2026-10-25，P2 收口）：v2 契约真源是 `packages/common/common/tools.py` 的 `ToolDescriptor`（设计见 [tool-service.md](./design/tool-service.md) §6.1）；内置文件/知识/联网工具已迁到独立 `tool-service`（:8008，`read`/`write`/`edit`/`glob`/`grep`/`list_dir`/`search_knowledge`，P3 起可选只读 `fetch`，默认 `TOOL__FETCH_ENABLED=false`），orchestrator 经 `RemoteToolkit` 消费该契约，装配开关 `AGENT_ORCHESTRATOR__TOOL_SERVICE_ENABLED`；旧 `code_*` 进程内实现与 alias 已于 P2 删除，进程内只剩 `list_documents`/`read_document`/`search_knowledge` 与 MCP/skill 工具。审批策略仍由 `approval_policies` 表控制（见 06 §2.3 #6）。

- 按 **Workspace 类型**注册工具集：`repo` / `mailbox` / `docs` / `kb`；
- 第三方工具（MCP）以 `mcp__<server>__<tool>` 命名空间接入；
- `ask_question` 是唯一的 `kind=interaction` 工具：目录与参数校验在 tool-service，中断/答案回填在 orchestrator 图内（`ask_gate` + `awaiting_input`）；chat 模式与公开 MCP Server 均不暴露；
- 权限策略：per-account / per-connector / per-tool 的 `auto|confirm|deny`（`approval_policies` 表，求值是一层纯函数），默认按风险分级（只读与受控写自动、危险操作 confirm；MCP 工具的档位由工具 annotations + 预设只读前缀计算，见 §8.2）；策略是配置，不是硬编码；
- 所有工具调用写 `run_events`（可回放/审计/计量）。

### 11.3 Skill（场景模板）

```text
Skill {
  id, version, title, description,
  workspaceType, tools[], permissions,
  systemPrompt, inputSchema,
  deliverables: { kind, template? },
  triggers: manual | schedule(后续),
  billing: { free?, creditsPerRun? }
}
```

首批内置：代码审查、仓库问答、补丁建议（`skills/coding.repo-readonly-review.json`，只读）；邮件/日历类内置技能已于 2026-10 随连接器下线移除。

P3 起支持**用户上传技能包**（SKILL.md + 可选 skill.json + scripts/）：

- 包在对象存储（workspace-service /v1/objects），DB 只存包/版本 + manifest/SKILL.md 快照；
- 版本化：账号内 slug + version 不可变；run 创建时冻结版本与执行字段，后传新版本不影响旧 run；
- 有 entrypoint 的包自动获得 skill_run 工具（WRITE 级受控写，在沙箱内自动执行、不打断审批；确认闸只拦 DANGER），产物登记为 run 交付物；
- 当前仍不开放终端/预览；coding 类内置技能保持只读。

### 11.4 Sandbox（P1 底座 + P2 stdio MCP + P3 技能包执行已落地）

- **引擎选型（2026-10-20 拍板）**：**microsandbox**（Apache-2.0，microVM，Python SDK）——自建的是 **provider 抽象 + 生命周期 + 账号配额**，不自建隔离内核；备选 E2B / Fly Machines 通过同一 `SandboxProvider` 接口接入；
- **P1（2026-10-20）**：`workspace-service` 沙箱模块——`SandboxProvider` 抽象、microsandbox 实现、`sandboxes` 实例表（账号/TTL/资源/状态）、生命周期 API（create/exec/files/touch/destroy）、过期回收 job；`agent-orchestrator` 增加沙箱 HTTP 客户端（默认关闭）。测试零网络；真实 microVM 需宿主可写 `MSB_HOME` 且允许 OCI registry 出网；
- **P2（2026-10-20）**：stdio MCP 接入——命令在沙箱内拉起，`StdioSandboxPool` 按对话级复用（`ToolContext.conversation_id`），probe 走 stdio JSON-RPC；`env/token` 只存密文；Web MCP 面板支持 HTTP/stdio 切换；
- **P3（2026-10-20）**：用户上传技能包——zip 校验/版本化/对象存储、run 快照；skill_run 在一次性沙箱内解包并执行 entrypoint，产物登记为 run 交付物；Web Skills 页支持上传/版本/删除；
- **本地兜底 local provider（2026-10-26）**：`WORKSPACE__SANDBOX_PROVIDER=local` 用「宿主目录 + 宿主子进程」实现同一 `SandboxProvider` 协议——每个沙箱是 `<SANDBOX_LOCAL_ROOT>/<name>/{workspace,home,tmp}`，沙箱内 `/workspace` 映射到其中的 `workspace/`；**没有隔离**，镜像/CPU/内存/网络策略无法强制执行，仅供没有 microVM runtime 的本地开发与单账号可信环境（桌面本地模式），启动即打 warning；生产/多账号仍只用 microsandbox；
- **尚未落地（P4）**：出网 allowlist/凭据注入、配额与 sandboxMs 计量、审计事件、常驻 process bridge / 多实例沙箱池；node 技能镜像需含 python3（解包脚本）；
- **当前对外承诺不变**：coding agent 仍只做**只读代码分析 + 补丁建议**；沙箱只用于受控的 MCP/skill 执行，未开放终端与预览。

### 11.5 事件溯源与对话

- `run_events` 表：append-only、`seq` 单调编号、`type` + payload（JSONB），落 Postgres；Redis Streams 只做实时分发；**SSE 断线回放从 Postgres 读**（`Last-Event-ID`）；
- **节点步骤（S1，2026-10-25）**：`plan/execute/tool_exec/verify/deliver` 每个节点执行完落一条 `run.step.v1`（payload `{step_id, node, phase, attempt, round, status, started_at, finished_at, duration_ms}`，`record_outbox=False`），`step_id = {run_id}:{attempt}:{node}:{round}` 是可寻址身份；该事件**不进** `GET /runs/{id}/events` 与 SSE（避免淹没用户事件流，可见事件 seq 因此有空洞，只能当单调游标），专用只读端点 `GET /api/v1/runs/{id}/steps` 按 seq 升序返回投影，前端「执行步骤」时间线读它；采集发生在一个 `ainvoke` 边界（`graph/steps.py` 的 `StepProjector` + `instrument_node`，由 services 层 `_step_stream` flush），所以执行中的节点要等该次图调用返回后才出现（未接 `astream`），工具明细仍走 `run.tool.called.v1`；S2（usage 的 step 维度）与 S3（step ↔ checkpoint / rewind）未做，见 `07-run-step-model.md`；
- `token.delta.v1` 是正文增量（payload `{text, phase}`，phase ∈ plan/execute/verify/deliver）：执行图四个正文节点把模型流式输出攒批落成事件，前端订阅 run 事件流就能在 run 还没终态时看到正文；增量事件不进 outbox，开关 `RUN_DELTA_STREAMING`（默认 true）；
