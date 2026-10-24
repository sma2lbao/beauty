# beauty-moon 架构 v2（静态 SPA + FastAPI BFF + 容器 Runtime）

> 状态：**决策版**（2026-10-18）。本文件已按以下拍板结果收敛：
> Web 保留 **Vite SPA** + 独立 Next.js 官网；**静态 SPA + FastAPI BFF + 容器 runtime** 混合；
> **Free 额度 + 订阅 + BYOK**（Stripe 暂缓）；**coding agent + 办公多场景**；**开放平台 / MCP**。
>
> 关系：`docs/architecture.md` 是 v1（阶段 A 微服务设计），v2 评审通过前 v1 仍为实现事实源；
> 本文与 v1 冲突处标注「替代 v1 §x」。

---

## 0. 最终选型（本轮拍板）

| 领域              | 生产                                                    | 开发环境                                     | 本期状态                                              |
| ----------------- | ------------------------------------------------------- | -------------------------------------------- | ----------------------------------------------------- |
| Postgres          | **云数据库，提供 URL**（Neon/RDS/云 PG 均可）           | **本地 SQLite**（`AUTO_CREATE_TABLES=true`） | ✅ 实施                                               |
| Redis             | **云 Redis，提供 URL**                                  | **本地 Redis**                               | ✅ 实施（限流/锁/短期缓存；不作为关键队列）           |
| 对象存储          | **暂不引入**                                            | 本地文件系统 / DB 列                         | ⏸ 暂缓（产物先 DB/文件系统，二进制附件受大小限制）   |
| 队列 / 工作流     | **Postgres job 表 + worker（SKIP LOCKED）**             | 同左（SQLite 下用文件/内存 worker）          | ✅ 实施；Temporal / Inngest **暂不做**                |
| 沙箱              | **microsandbox provider（P1）**                         | 本地 microsandbox / fake provider            | ✅ P1 底座 + P2 stdio MCP + P3 技能包（见 §11.1）     |
| 身份              | **沿用自建 account-service**（邮箱+密码，补社交 OAuth） | 同左（token 模式兜底）                       | ✅ 实施；Logto **不用**                               |
| 可观测            | **Sentry 官方服务** + 结构化日志                        | 同左（本地日志）                             | ✅ 实施；Langfuse **暂不做**                          |
| 支付              | **不做 Stripe**                                         | —                                            | ⏸ 暂缓；先 usage ledger + Free 配额 + BYOK，订阅后续 |
| 事务邮件          | **暂不做 Resend**                                       | 本地 SMTP / 控制台日志                       | ⏸ 暂缓；站内通知优先                                 |
| Web 托管          | **Nginx 托管静态 SPA**（同域反代 `/api`）               | 本地 Vite                                    | ✅ 实施                                               |
| BFF               | **FastAPI 服务**（同域 `/api`）                         | 本地 uvicorn                                 | ✅ 实施                                               |
| LLM 推理          | **继续用 API**（litellm 网关）                          | 本地/API                                     | ✅ 实施                                               |
| 静态官网          | **Next.js 静态导出**（部署 Vercel，SSG/SEO）            | 本地 Next dev                                | ✅ 实施                                               |
| 多区域 / 数据驻留 | 不做                                                    | —                                            | ⏸ 后续                                               |

### 0.1 两种部署形态（同一套代码）

| 形态                         | 适用      | Web/BFF                                               | 数据           | 外部依赖                | 状态    |
| ---------------------------- | --------- | ----------------------------------------------------- | -------------- | ----------------------- | ------- |
| **A. SaaS（静态 SPA + 云）** | to C 公网 | Nginx 托管 SPA + FastAPI BFF；Next.js 官网部署 Vercel | 云 PG/云 Redis | LLM API、Sentry（可选） | ✅ 当前 |

| **B. 内网/离线（Intranet）** | 私有化/内网/无外网 | **Linux VM/裸机 + systemd 原生进程 + Nginx**（不用 Compose/K8s）；**仅 HTTP** | SQLite + 本地目录；无 Redis | 内网 OpenAI 兼容模型（支持 function calling）；或 `LLM_ENABLED=false` 桩；Sentry 关闭；**⏸ 暂缓** |

**硬性原则**

1. 内网形态**不依赖 Vercel/云 PG/云 Redis/Sentry/Logto/Stripe/Resend/Langfuse**；
2. BFF 用 FastAPI 实现并与 SPA 同域部署（Nginx 反代 `/api`），不依赖 Vercel Functions；
3. 前端产物必须可静态托管（无外部 CDN/字体/远程图片）；
4. 模型可指向内网 OpenAI 兼容端点；无模型时系统仍能启动（降级为确定性桩）；
5. 内网验收必须在**禁止外网 egress** 的环境下通过。

---

## 1. ADR（最终状态）

| ADR | 决策                                     | 状态                   | 说明                                                     |
| --- | ---------------------------------------- | ---------------------- | -------------------------------------------------------- |
| 001 | Vite SPA + Next.js 独立静态官网          | ✅ 采用                | SPA 静态托管；官网 Next.js 静态导出（Vercel）            |
| 002 | 静态 SPA + FastAPI BFF + 容器 runtime    | ✅ 采用                | 六业务服务跑 Fly/Cloud Run；BFF 为独立 FastAPI 服务      |
| 003 | 长任务用 Postgres job 表 + worker        | ✅ 采用                | 不引入 Temporal/Inngest；复用现有 outbox 与 checkpointer |
| 004 | Sandbox 用 microsandbox provider         | ✅ P1 + P2 + P3        | 见 §11.1；stdio MCP、技能包上传/执行已落地               |
| 005 | 数据：云 PG + 云 Redis；对象存储暂缓     | ✅ 采用                | 开发 SQLite/本地 Redis/本地文件                          |
| 006 | 身份：自建 account-service（不用 Logto） | ✅ 采用                | 需补 Google/GitHub 社交登录                              |
| 007 | BFF httpOnly Cookie + 短时 JWT           | ✅ 采用                | 防 XSS、收敛跨域；API Key 场景除外                       |
| 008 | 计费：usage ledger + Free 配额 + BYOK    | ✅ 采用（Stripe 暂缓） | 订阅/发票/税后续再接                                     |
| 009 | MCP Server + Client                      | ✅ 采用（M3）          | 开放平台核心                                             |
| 010 | Tool Registry + Skill + Workspace 类型   | ✅ 采用                | coding/办公共用运行时                                    |
| 011 | 内网/离线部署可启动                      | ⏸ 暂缓                | 当前只要求本地启动；方案见 §10.3，恢复时再实施           |

---

## 2. 目标拓扑

```text
Browser
├── app.beautymoon.com      Nginx: apps/web 静态产物
│      └── /api/*           FastAPI BFF（同域反代）
│            ├── 登录/登出/会话 Cookie（account-service 为 IdP）
│            ├── 代理 runtime（短请求 + 短流）
│            ├── 产物/附件下载（DB/本地存储 → 流式响应）
│            └── 限流/错误契约/审计头
├── www.beautymoon.com      Vercel Project: apps/site（Next.js 静态导出）
└── 长流直连                runtime.beautymoon.com（SSE，BFF 发短时 stream token）

容器平台（Fly.io / Cloud Run / 自有云主机）
├── agent-runtime           编排 + Tool Registry + Skill + MCP + run worker
├── llm-gateway             模型路由/降级/预算/usage 采集（API 上游）
├── connector-service       邮箱/日历/文档连接器 + 凭证管理
├── knowledge-service       摄取/检索/重排/记忆（JSON 向量，SQLite 兼容）
├── workspace-service       工作区/产物元数据 + 内容存储（DB/本地文件）
└── account-service         组织/成员/会话/配额/usage/API Key（自建 IdP）

云托管（只保留 URL 型依赖）
├── 云 Postgres             生产库（URL 注入；开发 SQLite）
├── 云 Redis                限流/锁/短期缓存（URL 注入；开发本地 Redis）
└── Sentry                  前端/服务错误（官方 SaaS）

开发环境
├── SQLite                  AUTO_CREATE_TABLES=true，零外部依赖
├── 本地 Redis              可选（未配置时降级为进程内限流）
├── 本地目录                artifact_store=filesystem（默认）
└── 本地 SMTP               事务邮件联调（生产暂不做）
```

**流量原则**

- 普通 CRUD / 短请求：浏览器 → Nginx → FastAPI BFF → 容器服务；
- 长任务事件流：BFF 发短时 token，浏览器直连 runtime SSE（避免 BFF/代理超时）；
- 公开 API / MCP：第三方 → api.beautymoon.com（API Key/OAuth，限流）；
- 服务间：内部 HTTP + 租户 JWT；禁止跨服务直连数据库。

---

## 3. 服务与职责（相对 v1 的变化）

| 服务              | 部署                 | 职责                                    | v2 变化                                                                             |
| ----------------- | -------------------- | --------------------------------------- | ----------------------------------------------------------------------------------- |
| apps/web          | Nginx 静态           | Vite SPA 控制台                         | 保留；新增模板、API Key、MCP、用量/账单、文件/终端/预览面板（终端的落地依赖 §11.1） |
| services/bff      | 容器                 | BFF：认证、代理、下载、限流             | 新增（FastAPI；同域 `/api`）                                                        |
| apps/site         | Vercel 静态          | Next.js 官网、文档、博客、Changelog     | 新增（静态导出/SEO）                                                                |
| agent-runtime     | 容器                 | 会话/run/tool registry/skill/MCP/worker | 由 agent-orchestrator 演进；工具面抽离；队列改为 PG job 表                          |
| sandbox adapter   | workspace-service 内 | microsandbox provider + 生命周期        | ✅ P1 + P2 stdio MCP + P3 技能包（见 §4.4）                                         |
| llm-gateway       | 容器                 | 模型出口、路由、降级、预算、usage       | 增加 BYOK、计量、预算                                                               |
| connector-service | 容器                 | 连接器框架（当前无内置连接器）          | 2026-10 邮箱/日历能力下线；保留健康探针/连接撤销/凭证加密                           |
| knowledge-service | 容器                 | 摄取、检索、重排、引用、记忆            | 增加 rerank/citation/memory                                                         |
| workspace-service | 容器                 | 工作区、产物、附件元数据                | 暂用 DB/本地文件；对象存储后置                                                      |
| account-service   | 容器                 | 租户/成员/配额/usage/API Key            | 增加社交 OAuth、用量账本（Stripe 后置）                                             |

> 拆分节奏：M0 保持六业务服务 + FastAPI BFF；M1 起把 tool registry 从 orchestrator 内拆出；M3 再拆 platform-api（先复用 runtime 路由）。

---

## 4. 运行时与执行模型

### 4.1 统一 run 生命周期

```text
message → triage → (chat | run)
run: pending → planning → awaiting_approval(按需:无 DANGER 工具时自动放行) → running
     → awaiting_confirmation → completed / failed / blocked
```

- 保留 v1 的 **LangGraph + PG checkpointer**（SQLite 开发用 InMemorySaver，现有代码已支持）；
- run 启动/恢复改为 **PG job 表 + worker**：BFF 入队 → worker 领取（`FOR UPDATE SKIP LOCKED`）→ 执行 → 心跳/重试；
- SSE 事件仍写 Postgres（seq），Redis 只做实时 fan-out 与短期缓存；断线用 `Last-Event-ID` 续传；
- 人审/确认/追加指令仍走事件 + 状态机，不依赖长连接存活；
- 同一 run 只允许一个 worker（行锁 + lease），崩溃后按 checkpoint 恢复。

### 4.2 Tool Registry（coding 与办公共用）

```text
ToolSpec {
  name, description, inputSchema, outputSchema,
  scopes, risk: read | write | destructive,
  requiresApproval: auto | confirm | deny,
  executor: connector | knowledge | workspace | sandbox?(P1 客户端已备) | mcp,
  cost: { tokens?, toolCalls?, sandboxMs? }
}
```

- 按 **Workspace 类型**注册工具集：`repo` / `mailbox` / `docs` / `kb`；
- 第三方工具（MCP）以 `mcp__<server>__<tool>` 命名空间接入；
- 权限策略：per-tenant / per-connector / per-tool 的 `auto|confirm|deny`，默认按风险分级（只读与受控写自动、危险操作 confirm）；
- 所有工具调用写 `run_events`（可回放/审计/计量），不改变 v1 语义。

### 4.3 Skill（场景模板）

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

首批：代码审查、仓库问答、补丁建议（内置 manifest，只读）；邮件摘要、回复草稿、分类去噪、邮件→周报、附件归档、日历排期。

P3 起支持**用户上传技能包**（SKILL.md + 可选 skill.json + scripts/）：

- 包在对象存储（workspace-service /v1/objects），DB 只存包/版本 + manifest/SKILL.md 快照；
- 版本化：租户内 slug + version 不可变；run 创建时冻结版本与执行字段，后传新版本不影响旧 run；
- 有 entrypoint 的包自动获得 skill_run 工具（受控写，在沙箱内自动执行、不打断审批），产物登记为 run 交付物；
- 当前仍不开放终端/预览；coding 类内置技能保持只读。

### 4.4 Sandbox（P1 底座 + P2 stdio MCP + P3 技能包执行已落地）

- **P1（2026-10-20）**：`workspace-service` 沙箱模块——`SandboxProvider` 抽象、microsandbox
  实现、`sandboxes` 实例表（租户/TTL/资源/状态）、生命周期 API（create/exec/files/touch/
  destroy）、过期回收 job；`agent-orchestrator` 增加沙箱 HTTP 客户端（默认关闭）。
- **P2（2026-10-20）**：stdio MCP 接入——命令在沙箱内拉起，`StdioSandboxPool` 按
  **对话级**复用（`ToolContext.conversation_id`），probe 走 stdio JSON-RPC；
  `env/token` 只存密文；Web MCP 面板支持 HTTP/stdio 切换。详见 §8.2 与
  `docs/gap-2026-10/04-sandbox-selfhost.md` 实施记录。
- **P3（2026-10-20）**：用户上传技能包——zip 校验/版本化/对象存储、run 快照；
  skill_run 在一次性沙箱内解包并执行 entrypoint，产物登记为 run 交付物；
  Web Skills 页支持上传/版本/删除。详见 §4.3 与
  docs/gap-2026-10/04-sandbox-selfhost.md 实施记录。
- **尚未落地（P4）**：出网 allowlist/凭据注入、配额与 sandboxMs 计量、审计事件、
  常驻 process bridge / 多实例沙箱池；node 技能镜像需含 python3（解包脚本）。
- **当前对外承诺不变**：coding agent 仍只做**只读代码分析 + 补丁建议**；沙箱只用于
  受控的 MCP/skill 执行，未开放终端与预览。

---

## 5. Web 架构（Vite SPA + BFF）

### 5.1 项目结构

```text
apps/web           Vite SPA（现有代码）
  └── src/         页面/组件/数据层
apps/site          Next.js App Router（官网、文档、博客；静态导出）
services/bff       FastAPI BFF（同域 /api）
packages/design    现有组件库
packages/console   控制台组件（可选抽出）
```

- SPA 静态产物由 Nginx 托管，`/api` 反代到 FastAPI BFF；
- 官网独立项目，Next.js 静态导出 + sitemap/RSS，部署 Vercel；
- 环境变量拆分：公开配置（`VITE_*` / `NEXT_PUBLIC_*`）与服务端 secret。

### 5.2 认证与会话

- 登录：account-service 邮箱密码 + Google/GitHub 社交 OAuth（不用 Logto）；
- BFF 回调后建立 **httpOnly, Secure, SameSite=Lax Cookie**；SPA 不接触 refresh token；
- BFF 调 runtime 时签发短时服务 JWT（tenant/org/sub/scope）；
- 开放平台/API Key 请求绕过 BFF，直连 public API，用 API Key → 短时 JWT；
- 本地开发保留现有 token 模式兜底。

### 5.3 流式

- BFF 以 FastAPI 实现，Nginx 同域反代 `/api`；一套实现同时服务 SaaS 与内网；
- 短对话/短流：BFF 代理 SSE（FastAPI StreamingResponse）；
- 长任务：BFF 发 `streamToken`（短时、单 run），浏览器直连 runtime SSE；
- 断线重连带 `Last-Event-ID`；Redis 做 fan-out（可选），Postgres 做重放；
- 不做 WebSocket；多端同步后续再评估。

### 5.4 页面地图（在现有 4 屏基础上扩展）

| 区域       | 页面                                                                                                            |
| ---------- | --------------------------------------------------------------------------------------------------------------- |
| 工作台     | 会话工作台、附件（受限）、@ 引用（只读文件/会话/知识库）、/ 命令、模型选择、执行面板（步骤/变更/文件/终端占位） |
| 任务编排   | 看板、运行详情、计划/Todo/Goal、审批                                                                            |
| 变更与产物 | Diff 评审、产物预览（Markdown/图片；PDF/Office 后置）、下载/分享                                                |
| 工作区     | 文件树（只读）、Git 状态、补丁建议（不执行）                                                                    |
| 知识库     | 文档/记忆/引用溯源                                                                                              |
| 模板/技能  | 场景模板库、Skill 详情                                                                                          |
| 开发者     | API Key、MCP、Webhook、调用日志、用量（M3）                                                                     |
| 设置       | 账户、安全、模型/BYOK、权限、用量；订阅/发票后置                                                                |

---

## 6. 数据与存储

| 数据                                                        | 生产                             | 开发               | 说明                                 |
| ----------------------------------------------------------- | -------------------------------- | ------------------ | ------------------------------------ |
| runs / run_events / conversations / messages / instructions | 云 Postgres                      | SQLite             | 保留 v1 模型；事件 append-only       |
| connections / credentials                                   | 云 Postgres                      | SQLite             | 凭证加密暂用应用层密钥；KMS 后置     |
| workspaces / artifacts                                      | 云 Postgres + 内容存 DB/文件系统 | SQLite + 本地目录  | 对象存储后置；单实例 filesystem 可用 |
| knowledge_documents / chunks                                | 云 Postgres（JSON 向量）         | SQLite             | 现有实现不依赖 pgvector，可直接跑    |
| orgs / members / quotas / usage_ledger / api_keys           | 云 Postgres                      | SQLite             | usage ledger 新增；Stripe 后置       |
| session / rate limit / locks                                | 云 Redis                         | 本地 Redis（可选） | 未配置时降级为进程内实现             |
| jobs                                                        | 云 Postgres（job 表）            | SQLite job 表      | 队列与重试；worker 轮询              |

**开发/生产一致性**

- 开发：SQLite + `AUTO_CREATE_TABLES=true`（现有默认），零外部依赖；
- 生产：云 Postgres + Alembic 迁移（现有迁移未使用 PG 专属类型，SQLite 兼容）；
- CI 必须对**云 PG**跑一遍迁移 + 集成测试，避免「SQLite 过、PG 挂」；
- 多实例部署时 **filesystem artifact store 不可用**，必须用 database 模式或共享盘；对象存储接入后替换。

---

## 7. 认证、配额、计费（Stripe 暂缓）

- **身份**：account-service 维持自建 IdP（邮箱密码 + 计划补 Google/GitHub OAuth）；各服务沿用 JWT/JWKS 验签；
- **会话**：BFF httpOnly Cookie；服务间短时 JWT；API Key 独立通道；
- **Free 配额**：`usage_ledger` 记录 token / tool call / 存储；按月聚合 → 配额检查（402/429）；
- **BYOK**：租户级 provider key（应用层加密，KMS 后置）；llm-gateway 按租户选 key；BYOK 不扣模型 credits；
- **订阅/支付**：Stripe 暂不做；先只支持 Free 档 + 手动开通内部档；付费自助后续补；
- **限流**：Redis（Upstash 兼容）或进程内降级；租户/IP/Key 三级；Turnstile 后置；
- **公开分享**：不可猜测 token、可撤销、可过期（后置）。

---

## 8. 开放平台与 MCP（M3）

### 8.1 Public API

- `/v1`：conversations、runs、events(SSE)、artifacts、skills、tools、usage；
- 鉴权：OAuth 2.1（第三方应用）或 API Key（服务端调用）；scope 最小化；
- 幂等键、分页 cursor、统一错误 `{code,message,details}`、限流头；
- Webhook：`run.completed`/`run.failed`/`artifact.created`/`usage.threshold`，签名 + 重试；
- SDK：TypeScript/Python（OpenAPI 生成）+ 示例仓库。

### 8.2 MCP

- **MCP Server**（对外）：streamable HTTP + OAuth 2.1；暴露 tools（email/calendar/docs/kb；沙箱工具 P2）与 resources（artifacts/files）；租户隔离、权限策略、审计；
- **MCP Client**（对内）：租户配置第三方 MCP server（GitHub、Notion、飞书、Slack…）；工具命名空间、超时、失败降级、审批；
  - **HTTP 型**（M1 已落地）：orchestrator 进程内 httpx（Streamable HTTP），后续补 SSRF/出网代理加固；
  - **stdio 型**（P2 已落地）：命令在 workspace-service 沙箱内拉起，经 stdin/stdout JSON-RPC
    执行（`stdin MCP` 不能进 orchestrator 进程）；沙箱按**对话级**复用
    （`ToolContext.conversation_id`，无对话退化为 run），命令白名单 + 参数不经 shell；
    `env/token` 只存密文，读端只回 `has_env/env_keys`；P2 出网 `public`（允许 npx/uvx 安装），
    P4 切 allowlist/凭据注入；
  - **审批策略**（已落地）：每个 server 一个 `auto_approve` 信任开关（Web `/mcp` 页
    「免确认执行」，默认关）。关：run 里逐次进确认闸、对话里只回喂「请改用任务」；
    开：run 不再确认、对话里可直接调用（对话本身没有审批流，见 §6.7）——对齐
    §6.4「操作策略是配置，不是硬编码」；
- **安全**：MCP 返回内容视为不可信数据；提示注入过滤；危险工具默认 confirm；凭证只存在服务端；
  stdio 进程只允许在沙箱内运行，沙箱内 env 明文对用户代码可见（最小 scope + P4 凭据代理）；
- **市场**：后续 MCP server 目录 + 一键安装（M4）。

### 8.3 Skill / Plugin 注册表

- manifest：id/version/description/workspaceType/tools/permissions/deliverables/pricing；
- 发布/审核/灰度/回滚；运行时按租户启用；
- 开放平台开发者可提交 Skill；与 MCP server 一起构成插件市场。
- **P3 已落地（上传包）**：租户上传 SKILL.md zip（可选 skill.json entrypoint），
  对象存储 + 版本表（slug+version 不可变），run 创建时冻结版本快照；
  有 entrypoint 的包在沙箱内执行（skill_run 工具，需确认），无 entrypoint 的包作为提示词技能；
  Web Skills 页支持上传/版本查看/删除。开放平台的审核/灰度/计费仍在 M4。

---

## 9. 观测、质量与安全

- **错误**：Sentry 官方（前端 + 服务），PII 脱敏后上报；
- **日志**：结构化日志 + traceId（沿用 `common.logging`）；OTel/Langfuse 后置；
- **观测规范**：字段字典、`request_id` 跨服务贯穿、访问/异常日志规格、Sentry 接入与告警草案见 [observability.md](./observability.md)；
- **产品分析**：PostHog 后置（可用 Sentry + 自建事件表过渡）；
- **Evals**：golden dataset + grader；PR 门禁（M4）；
- **安全**：沙箱 P1/P2 受控使用（stdio MCP 有开关 + 命令白名单 + 沙箱隔离）；用户上传 skill 包执行 P3 再开；凭证加密；租户隔离测试；输入/输出审查后置；
- **审计**：run_events 即审计流水；导出后置。

---

## 10. 部署与迁移

### 10.1 环境

| 环境    | Web                              | Runtime           | 数据                                           |
| ------- | -------------------------------- | ----------------- | ---------------------------------------------- |
| dev     | 本地 Vite + 本地 BFF             | 本地容器/进程     | SQLite + 本地 Redis + 本地目录                 |
| staging | Nginx SPA + Vercel preview(site) | Fly staging + BFF | 云 PG（staging URL）+ 云 Redis + DB artifact   |
| prod    | Nginx SPA + Vercel(site)         | Fly prod + BFF    | 云 PG + 云 Redis + DB artifact（对象存储后置） |

- CI：lint/typecheck/test/build → **对云 PG 跑迁移与集成测试** → 部署 preview → smoke/e2e；
- 服务镜像：每服务 Dockerfile + 健康检查 + 迁移 job（部署前执行）；
- 回滚：镜像 tag + Vercel 即时回滚 + DB 迁移前进/后退检查；
- 成本护栏：Fly scale-to-zero、Free 配额、预算告警。

### 10.2 从当前仓库迁移（M0 路径）

1. 新增 `services/bff`（FastAPI）与 `apps/site`（Next.js）；`apps/web` 只构建静态产物，由 Nginx 托管；
2. 六个服务补 Dockerfile + Fly app 配置（SaaS）；内网另加 systemd 单元 + 发布包（M0-13）；本地开发保留 compose；
3. 接入云 PG/云 Redis URL；开发保持 SQLite/本地 Redis；产物改 database 模式以支持多实例；
4. account-service 补 Google/GitHub OAuth + BFF Cookie 会话（保留 token 模式开发兜底）；
5. 新增 `jobs` 表 + worker，run 启动/恢复走队列（不引入 Temporal）；
6. 前端 SSE 带 `Last-Event-ID` + 自动重连；
7. 补 Markdown/会话管理/产物预览（DB/文件版）等体验闭环；
8. 灰度：内部 dogfood → Free 邀请制 → 公开注册。

### 10.3 内网/离线部署（Intranet Profile）——⏸ 暂缓，不纳入 M0

> 当前只要求**本地可启动**；本节保留为后续内网交付参考，恢复时再按 M0-13 子任务执行。

**目标**：在完全无外网（或仅允许内网模型/依赖源）的环境中，用 **Linux VM/裸机 + systemd 原生进程 + Nginx** 启动完整系统；**不使用 Docker Compose / Kubernetes**。

**组成**

```text
/opt/beauty/releases/<version>/       发布包（服务代码 + venv/wheels + web/node 产物）
/opt/beauty/current -> releases/<v>   当前版本软链（升级/回滚切换）
/etc/beauty/*.env                     EnvironmentFile（600）
/etc/beauty/secrets/                  JWT/内部密钥/BFF session 密钥（600）
/var/lib/beauty/<service>/*.db        SQLite
/var/lib/beauty/artifacts/            产物/附件（filesystem）
/var/backups/beauty/<date>/           DB + artifacts 备份
/etc/systemd/system/beauty-*.service  六业务服务 + BFF + 迁移（oneshot）
/etc/nginx/conf.d/beauty.conf         HTTP 静态站 + /api 反代（SSE 不缓冲）
```

**必须满足**

1. **无 Vercel/容器编排**：BFF 以 FastAPI 进程由 systemd 管理；Nginx 托管 SPA 静态产物并反代 `/api`；SPA 深链 rewrite 由 Nginx 完成；
2. **无外部 CDN/字体/图片**：构建产物自包含；
3. **模型可配**：`LLM_GATEWAY__API_BASE`、`LLM_GATEWAY__API_KEY`、`LLM_GATEWAY__DEFAULT_MODEL`（litellm 透传 `api_base`/`api_key`）；未配置且 `LLM_ENABLED=false` 时可启动（桩）；内网模型**支持 function calling**；
4. **数据本地**：SQLite + 本地目录；Redis 不配置（进程内限流/锁）；产物 `ARTIFACT_STORE=filesystem|database`；
5. **可选依赖默认关闭**：Sentry、社交 OAuth、Stripe、Resend、Langfuse；
6. **鉴权本地可用**：仅 account-service 邮箱密码（无内网 OIDC/LDAP）；**HTTP 内网 Cookie `BFF_COOKIE_SECURE=false`** + SameSite=Lax + CSRF + 短会话 + 防火墙/IP allowlist；
7. **内网构建/交付**：Python 走内网 PyPI（或 wheelhouse），Node 走内网 npm；发布包 + 校验和；无容器镜像依赖；
8. **验收**：在禁止外网 egress 的 VM 上，安装脚本执行后 `systemctl status beauty-*` 全绿，登录 → 会话 → 流式（桩或内网模型）→ 上传 → 产物 → 搜索可用；日志无外网请求失败。

**注意**

- **不允许 Compose/K8s**：用 systemd 管进程；若连 systemd 也不允许，需要改为 supervisor/手工守护（待确认）；
- **不支持 HTTPS**：Cookie Secure=false，HTTP 下凭证/Cookie 明文传输；必须靠内网隔离、Nginx 仅内网监听、CSRF、短会话、防火墙/IP allowlist 补偿，并在文档中声明风险；
- **单机 SQLite 无 HA**：systemd 自动重启 + 备份/快速恢复；多机/高并发需 PG（后续 I3）；
- 无对象存储时附件限制 10MB；内网邮件/日历连接器指向内网 IMAP/SMTP/ICS。

**环境变量补充（内网）**

```text
BFF__COOKIE_SECURE=false       # 无 HTTPS 内网；上线 HTTPS 后改 true
LLM_GATEWAY__API_BASE=...      # 内网 OpenAI 兼容地址
LLM_GATEWAY__API_KEY=...
LLM_GATEWAY__DEFAULT_MODEL=openai/<model>
SENTRY_ENABLED=false
SOCIAL_LOGIN_ENABLED=false
ARTIFACT_STORE=filesystem      # 单机；多机改 database/共享盘
DATABASE_URL=sqlite+aiosqlite:////var/lib/beauty/app/app.db
REDIS_URL=                      # 留空 = 进程内降级
```

---

## 11. 暂缓项与影响（必须显式管理）

### 11.1 沙箱：microsandbox 路线（P1/P2/P3 已落地）

- **决策（2026-10-20）**：引擎选 **microsandbox**（Apache-2.0，microVM，Python SDK），
  自建的是 **provider 抽象 + 生命周期 + 租户配额**，不自建隔离内核；
  备选 E2B / Fly Machines 通过同一 `SandboxProvider` 接口接入。
- **P1 已交付**：`workspace-service` 沙箱模块（`sandboxes` 表、create/exec/files/touch/destroy、
  过期回收、错误码与租户隔离）、orchestrator 沙箱 HTTP 客户端（默认关闭、不暴露工具）、
  假 provider 单测 + 真实 SDK 适配测试。**测试零网络**；真实 microVM 需宿主可写 `MSB_HOME`
  且允许 OCI registry 出网。
- **P2 已交付**：stdio MCP 在沙箱内拉起（命令白名单、env/token 密文、对话级沙箱池），
  Web MCP 面板支持 HTTP/stdio；共 196 orchestrator tests + 219 web tests。
  出网为 public-only（不含宿主/私网），P4 再切 allowlist/凭据注入。
- **P3 已交付**：用户上传 SKILL.md 包（zip 校验、版本化、对象存储、run 快照），
  skill_run 在一次性沙箱内解包执行、产物登记为交付物，Web Skills 页上传/版本/删除；
  264 orchestrator tests + 226 web tests。仍不开放终端/预览；coding 内置技能只读。
- **仍未交付（P4）**：出网 allowlist/凭据注入、配额计量、审计事件、常驻 process bridge、
  多实例沙箱池；node 技能镜像需含 python3（解包/产物清单脚本）。
- **已知环境要求**：部署时 `WORKSPACE__SANDBOX_MSB_HOME` 指向可写目录；如需拉镜像，
  沙箱宿主必须能访问 OCI registry（内网可配镜像仓库）。

### 11.2 Temporal/Inngest 暂缓

- 用 Postgres job 表 + worker 替代；适合 MVP，但缺少可视化工作流/复杂重试编排；
- 任务量/复杂度上来后再评估 Temporal Cloud / Inngest，接口按队列抽象隔离。

### 11.3 对象存储暂缓

- 产物/附件先存 DB 列或本地文件；建议单文件上限（如 10MB）并在 UI 提示；
- **多实例部署时 filesystem 不可用**：生产必须 `artifact_store=database` 或共享盘；
- PDF/Office/图片的大文件预览推迟到对象存储接入后。

### 11.4 Logto 不用

- 自建 account-service 需补社交 OAuth（Google/GitHub；Microsoft 后置）；
- 邮箱验证、找回密码、风控需要自行实现（或后置）。

### 11.5 Stripe 暂缓

- 付费订阅/发票/税/催缴不能上线；先 Free 配额 + 手动开通；
- usage ledger、配额校验、BYOK 与支付解耦，先做；Stripe 接入时只加支付与套餐同步。

### 11.6 Resend 暂缓

- 事务邮件（验证/重置/通知）先走本地 SMTP 或控制台日志；
- 站内通知优先；营销邮件后置。

### 11.7 Langfuse 暂缓

- 没有 prompt/response trace 与数据集回放；先用 Sentry + 结构化日志；
- 评估/数据集能力后置到 M4。

### 11.8 静态托管与 BFF 边界

- Vercel 只部署 `apps/site`（Next.js 静态导出），不承载 SPA/BFF；
- SPA 静态产物由 Nginx 托管，`/api` 反代到 FastAPI BFF；长流仍直连 runtime，长任务走 worker；
- BFF 不受 Vercel 函数时长限制，但代理 SSE 时必须关闭缓冲并设置足够 read timeout。

---

## 12. 风险与待定

| 风险                                     | 缓解                                                                |
| ---------------------------------------- | ------------------------------------------------------------------- |
| coding agent 无执行环境（E2B 暂缓）      | M1 前拍板 Fly Machines/自建 runner/只读方案；未定前不承诺执行类能力 |
| 多实例 + filesystem 产物                 | 生产强制 database 模式；对象存储接入后替换                          |
| 云 PG 与 SQLite 行为差异（时区/JSON/锁） | CI 对云 PG 跑迁移+集成；SQLite 仅开发                               |
| Redis 成为单点                           | 只用于限流/锁/缓存；关键状态落 PG；Redis 不可用时降级               |
| PG job 队列吞吐                          | 小团队/MVP 足够；量级上来换专业队列                                 |
| 自建认证的安全负担                       | 沿用现有旋转刷新令牌/JWKS；补社交 OAuth 时做安全评审                |
| 自建邮件送达率                           | 暂不做事务邮件；上线前选择 SMTP/SES 并做 SPF/DKIM/DMARC             |
| BFF/代理 SSE 超时                        | 长流直连 runtime；BFF 仅短流，Nginx 关闭缓冲并放宽 read timeout     |
| BYOK 密钥安全（无 KMS）                  | 应用层加密 + 密钥轮换 + 不落日志；KMS 后置                          |

**待定**

1. coding 执行替代方案（Fly Machines / 自建 runner / 只读）；
2. 产物多实例存储（database 先行 or 尽快接对象存储）；
3. 社交 OAuth 范围（Google/GitHub 先，Microsoft 后）；
4. Free 配额与 BYOK 的具体额度；
5. 是否在 M2 前引入对象存储（影响附件/Office 预览）。
