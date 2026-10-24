# M0 任务清单：平台基础 + 体验闭环

> **已归档，2026-10-24**；被 [docs/architecture.md](../../architecture.md)（v2 基底）与 [docs/roadmap.md](../../roadmap.md) 取代。本文仅作历史留痕，不再更新、不再作为依据。

> 状态：**可执行任务清单（草案）** · 2026-10-18
> 依据：[architecture-v2.md](./architecture-v2.md)、[feature-gap.md](./feature-gap.md)
> 周期目标：**2–3 周**；并行轨道见第 3 节。
> 前置决策：云 PG + 云 Redis（开发 SQLite/本地 Redis）、对象存储暂缓、Temporal/E2B 暂缓、
> Logto 不用（account-service 自建）、Sentry 官方、Stripe/Resend/Langfuse 暂缓、Vercel 托管 Web、LLM 用 API。

---

## 0. M0 目标与完成定义

**一句话目标**：把现有 SPA + 六业务服务搬到「Nginx/Vercel 静态托管 + 容器 + 云 PG/Redis + FastAPI BFF」，并补齐上线前必须的登录、会话、附件、产物、Markdown、可观测与部署闭环。

**M0 Definition of Done（全局）**

1. `app.staging.beautymoon.com` 与 `www.staging.beautymoon.com` 两个 Vercel 域名可用；
2. 六个服务部署到 staging 容器平台，健康检查全绿，数据库迁移可一键执行；
3. 真实登录（邮箱密码 + Google/GitHub）→ 新建会话 → SSE 流式 → 上传附件 → 会话重命名/搜索 → 查看产物/下载 → 退出，全链路可跑；
4. 生产使用云 PG + 云 Redis；开发默认 SQLite + 本地 Redis（可选）+ 本地目录，零外部依赖；
5. Sentry 能收到前端/服务错误；用量页/模型列表有真实数据或明确空态；
6. 所有任务有单测/集成/E2E/安全测试，CI 门禁通过；回滚演练通过；
7. 文档更新：环境变量清单、部署手册、回滚手册、已知限制；
8. **本地启动验证：六个服务 + web 健康检查通过（SQLite + LLM 桩/可选真实模型）。**

**当前进度（2026-10-18）**：M0-01（静态 SPA + FastAPI BFF + Next.js 官网）、M0-04（SQLite job 队列 + worker 本地版）、M0-05（社交 OAuth + BFF Cookie 会话）、M0-06（BFF 代理/限流/下载/stream token）、M0-07（SSE 断线续传/重连）、M0-08（Markdown 渲染）、M0-09（会话管理）、M0-10（产物预览/下载）、M0-11（用量/模型/BYOK 本地版）、M0-12（附件本地最小版）已完成并通过测试；**本地开发基线**（一键启动/健康检查/重置/seed + 文档）也已完成。剩余 M0-02/03/13 为部署/云基础设施，按本地优先暂缓。

**M0 本地启动基线（当前已可用）**

- 后端：`npx nx run account-service:serve`（:8001）、`agent-orchestrator:serve`（:8002）、`llm-gateway:serve`（:8003）、`connector-service:serve`（:8004）、`workspace-service:serve`（:8005）、`knowledge-service:serve`（:8006）；
- 前端：`npx nx dev @beauty/web`（:5173，Vite 代理各服务）；
- 环境：复制各服务 `.env.example` 为 `.env`，保持 `AUTO_CREATE_TABLES=true` 即用 SQLite；
- 模型：默认 `LLM_ENABLED=false` 走确定性桩；需要真实模型时再配 llm-gateway；
- 验证：六个 `/healthz` + web 首页均 200（当前实测已通过）。

**M0 明确不做**

- 不做内网/离线部署（M0-13 暂缓，见文档）；
- E2B/任意代码执行、终端、预览 URL；
- 对象存储、PDF/Office 预览（仅 Markdown/图片 + 文本回退）；
- Temporal/Inngest、Logto、Stripe、Resend、Langfuse；
- 多区域、开放平台 API Key、MCP（M3）。

---

## 1. 任务总览

| ID    | 任务                                                      | 端          | 规模 | 依赖     | 建议角色         |
| ----- | --------------------------------------------------------- | ----------- | ---- | -------- | ---------------- |
| M0-01 | 部署拆分：静态 SPA(Nginx) + FastAPI BFF + Next.js 官网    | Web/Infra   | M    | —        | Web/Platform     |
| M0-02 | 七服务容器化（六业务 + BFF）+ 容器平台部署 + 迁移 job     | Infra       | M    | —        | Platform         |
| M0-03 | 云 PG/云 Redis 接入 + 开发 SQLite/本地 Redis + CI PG 迁移 | Service     | M    | M0-02    | Platform/Service |
| M0-04 | PG job 表 + worker（替代 Temporal）                       | Service     | M    | M0-02    | Service          |
| M0-05 | account-service 社交 OAuth + BFF Cookie 会话              | Auth/Web    | M    | M0-01    | Service/Web      |
| M0-06 | BFF：代理、错误契约、限流、下载、stream token             | Web         | M    | M0-01/05 | Web              |
| M0-07 | SSE `Last-Event-ID` + 自动重连 + 断点续传                 | Web         | S    | —        | Web              |
| M0-08 | Markdown/代码/表格渲染（流式安全）                        | Web         | S    | —        | Web              |
| M0-09 | 会话管理：重命名/删除/归档/搜索/分页                      | Web/Service | M    | —        | Service/Web      |
| M0-10 | 产物预览（Markdown/图片）+ 下载（DB/文件版）              | Web/Service | M    | M0-03    | Web/Service      |
| M0-11 | Sentry + 用量只读页 + 模型列表 + BYOK 占位                | Web/Service | M    | M0-02    | Service/Web      |
| M0-12 | 附件最小版：DB/本地文件 + 类型/大小限制                   | Web/Service | M    | M0-03    | Service/Web      |

总计 12 项（M=9 / S=3），按第 3 节排期可在 3 周内完成（含 3 天缓冲）。

---

## 2. 任务详情

### M0-01 部署拆分：静态 SPA（Nginx）+ FastAPI BFF + Next.js 官网

> ✅ 已完成（2026-10-18）：`apps/web` 保持纯 Vite SPA；新增 `services/bff`（FastAPI，:8007，`/healthz` + `/api/health`，3 个 pytest 用例）；新增 `apps/site`（Next.js App Router，静态导出，Landing/About/Pricing/Docs/Changelog + sitemap/robots/OG）；新增 `deploy/web/nginx.conf`（SPA 深链 + `/api` 反代）；本地 Vite `/api/health` 代理到 BFF 已验证；SPA 内重复的 `/landing` 页面已删除，旧深链跳转到 `apps/site`，官网内容单一来源。

**目标**：web 不走 Vercel；`apps/web` 只出静态产物由 Nginx 托管；新增 FastAPI BFF 与独立 Next.js 官网。

**范围**

- `apps/web`：保持 Vite 构建；`/api` 不再放 Node Functions；新增 `deploy/web/nginx.conf`（`try_files` 深链回退 + `/api` 反代 + SSE 不缓冲）；本地 Vite 仅把 `/api/health` 代理到 BFF，其余 `/api` 等 M0-06 统一切到 BFF。
- `services/bff`：FastAPI 骨架，`/healthz`、`/api/health`；CORS 仅本地跨端口兜底；pyproject/uv workspace/Nx target/pytest/.env.example；M0-05 装配 Cookie 会话，M0-06 装配代理/限流/下载/stream token。
- `apps/site`：Next.js App Router + `output: 'export'`；页面 Landing、About、Pricing（占位）、Docs（占位/入口）、Changelog；复用 `@beauty/design` 令牌与品牌组件；metadata/OG、sitemap、robots；控制台跳转由 `NEXT_PUBLIC_APP_URL` 配置。
- 部署：web/BFF 同域（Nginx），官网部署 Vercel；官网 PR 自动 preview，web/BFF 走 staging 发布。

**验收**

- [x] `/api/health` 返回 200（BFF 直连 + 本地 Vite 同源代理已验证）；
- [ ] `app.*` 深链刷新不 404（Nginx 配置已提供，待部署环境 `nginx -t` + 刷新验证）；
- [x] `www.*` 首页为预渲染 HTML（view-source 有正文/meta/OG）；sitemap.xml/robots.txt 可访问（Next 静态产物已验证）；
- [x] 客户端 bundle 无服务端 secret（web 只保留 `VITE_*`，BFF secret 走环境变量）；
- [ ] 官网首页 Lighthouse Performance/SEO ≥ 90（待部署后跑）；
- [ ] 控制台无 console error（待 BFF 代理接入后回归）。

**测试**：CI build；preview smoke（curl 200 + 关键文案）；SEO 静态检查；BFF pytest。

**备注**：Vercel 只部署 `apps/site`（Next.js 静态导出），不承载 SPA/BFF；长流由 BFF 发 stream token 后浏览器直连 runtime（M0-06/07）。

---

### M0-02 七服务容器化 + 容器平台部署 + 迁移 job

**目标**：六个业务服务 + BFF 可在 Fly.io / Cloud Run（建议 Fly 起步）一键部署，支持滚动/回滚与迁移。

**范围**

- 每服务 `Dockerfile`（多阶段、非 root、只装 runtime 依赖、healthcheck）；`deploy/` 增加 Fly app 或 Cloud Run 配置；
- `deploy/docker-compose.yml` 补服务本体（**仅本地开发**）；
- 迁移 job：部署前 `alembic upgrade head`；失败即停止发布；
- secrets：云 PG/Redis URL、Sentry DSN、服务间密钥；staging/prod 分离；
- 日志：stdout 结构化 JSON；健康检查 `/healthz` `/readyz`。

**验收**

- [ ] staging 七个服务（六业务 + BFF）健康检查全绿；`/readyz` 在 DB 不可用时失败；
- [ ] 发布新镜像 ≤10 分钟；回滚上一镜像 ≤10 分钟；
- [ ] 迁移 job 失败会阻断发布；有回退演练记录；
- [ ] 服务以非 root 运行；容器内无本地持久状态。

**测试**：staging 冒烟（健康检查 + 关键 API）；故障注入（停 DB/Redis）；回滚演练。

---

### M0-03 云 PG/云 Redis 接入 + 开发 SQLite/本地 Redis + CI PG 迁移

**目标**：生产用云 Postgres/Redis URL；开发保持 SQLite/本地 Redis；消除「SQLite 过、PG 挂」风险。

**范围**

- 环境变量：`*_DATABASE_URL`、`REDIS_URL`；staging/prod 注入云 URL；dev 默认 SQLite + `AUTO_CREATE_TABLES=true`；
- 连接池：云 PG 用 pooler（Neon pooled / PgBouncer），限制每服务连接数；超时/重试；
- Redis 适配层（新，薄）：`get/set/expire/incr` + 限流/锁；未配置时进程内降级；不承载关键状态；
- CI：云 PG（或 testcontainers）跑 `alembic upgrade head` + 集成测试；SQLite 单测保留；
- 开发文档：一键起本地 SQLite/Redis。

**验收**

- [ ] dev 无外部依赖可跑全量单测；自动建表正常；
- [ ] staging/prod 云 PG 迁移通过，连接数在限额内；
- [ ] CI 同时跑 SQLite 单测 + PG 集成/迁移；
- [ ] Redis 未配置时限流降级可接受（有测试）。

**测试**：迁移 up/down；PG 集成；连接耗尽/超时；Redis 断连降级。

---

### M0-04 PG job 表 + worker（替代 Temporal）

> ✅ 本地版已完成（2026-10-18）：`jobs` 表与迁移 `a3d7f2b9c1e5`；plan/approve/confirm/resume/retry 入队；SQLite 轮询 worker、lease 过期重领、attempts/指数退避、失败留痕、优雅退出；默认开启，测试夹具关闭保留同步语义。PG `SKIP LOCKED`、取消、100 并发压测未做（部署阶段再补）。

**目标**：run 的启动/恢复/取消改为队列驱动，避免 serverless 时长与进程崩溃丢任务。

**范围**

- 新表 `jobs`（orchestrator）：`id, tenant_id, kind, payload json, status(queued|running|succeeded|failed), attempts, max_attempts, run_at, lease_until, locked_by, last_error, created_at, updated_at`；索引 `(status, run_at)`；
- worker：批量领取（`FOR UPDATE SKIP LOCKED`）、lease + 心跳、指数退避、死信/失败留痕、优雅退出；
- 入队点：会话创建 run、retry/resume、SSE 恢复（幂等键，只入队一次）；
- 取消：写 cancelled；worker 在边界检查；
- 可观测：队列深度/延迟/失败数日志或指标。

**验收**

- [ ] 消息创建 run 后 1s 内被 worker 领取；
- [ ] 杀 worker 后任务在 lease 过期被重新领取，不重复执行；
- [ ] 100 并发 run 压测无丢失、无明显饿死；
- [ ] 取消/retry 与现有状态机一致；
- [ ] SQLite dev 有 fallback（简单轮询），PG 用 SKIP LOCKED。

**测试**：并发领取、lease 过期、重试、取消、崩溃恢复、压测。

---

### M0-05 account-service 社交 OAuth + BFF Cookie 会话

> ✅ 已完成（2026-10-18）：account-service 新增 Google/GitHub authorize/exchange（HMAC state + PKCE、`oauth_identities` 表与迁移、首次登录按 email 复用成员/新建组织）；BFF 提供 `/api/auth/*` 登录/注册/刷新/登出/session、加密 httpOnly Cookie、CSRF double-submit、OAuth start/callback；前端新增 `session` 模式与 OAuth 按钮。真实 provider 调用用 MockTransport 测试，未用外部凭据。

**目标**：不用 Logto，沿用 account-service 作为 IdP 并补 Google/GitHub 登录；BFF 用 httpOnly Cookie 管会话。

**范围**

- account-service：
  - `GET /v1/auth/oauth/{provider}/start`（state + PKCE）与 `callback`；
  - `oauth_identities` 表（provider/subject/user_id），首次登录自动建用户/组织；
  - 配置 `GOOGLE_CLIENT_ID/SECRET`、`GITHUB_CLIENT_ID/SECRET`、回调白名单；
  - 现有邮箱密码/刷新令牌/JWKS 不变。
- BFF（FastAPI）：登录/回调/登出/session；**HttpOnly + Secure + SameSite=Lax** Cookie；不向 SPA 暴露 token；静默续期；CSRF double-submit；本地开发保留 token 兜底。
- Web：登录页加 Google/GitHub；`auth.ts` 适配 session 模式。

**验收**

- [ ] 首次 OAuth 登录自动建租户/用户；再次登录复用身份；
- [ ] Cookie 属性正确，XSS 无法读取 token；
- [ ] 刷新/登出/过期/CSRF 行为正确；
- [ ] 邮箱密码登录不回归；测试覆盖 state/回调/失败。

**测试**：OAuth mock；Cookie 属性；CSRF；refresh 单飞；租户隔离。

**开放问题**：加密 Cookie（无状态）vs Redis 会话（可撤销）——建议加密 Cookie + 短 TTL + 撤销黑名单，M0-05 内拍板。

---

### M0-06 BFF：代理、错误契约、限流、下载、stream token

> ✅ 已完成（2026-10-18）：`/api/v1/*`→runtime、`/api/account|workspace|knowledge/*`→对应服务；下游 401 自动刷新并重试一次；进程内限流 429；`/api/files/{id}` 下载；`/api/stream-token` 短时 token；SSE 流式转发并保留 Last-Event-ID。开发 token 模式保留 Bearer 直通兜底。

> 技术栈：随 M0-01 使用 **FastAPI（`services/bff`）**，不再使用 Vercel Functions/Node BFF。

**目标**：BFF 成为 SPA 唯一同源入口，收敛鉴权、错误、限流与文件下载。

**范围**

- 路由：`/api/auth/*`；`/api/runtime/*`（allowlist）；`POST /api/stream-token`；`GET /api/files/:artifactId`；`POST /api/attachments`；`GET /api/models`；`GET /api/usage/summary`；`GET /api/health`；
- 统一错误 `{code,message,details}`；`X-Request-Id`；超时/重试；429 + `Retry-After`；CORS 仅 app 域；
- 限流：租户/IP/路由三级；Redis + 进程内降级；
- 日志：请求 id、租户、耗时、状态；不记录 token/正文。

**验收**

- [ ] SPA 请求同源；未登录 401 且前端跳登录；
- [ ] 429 可测；下游超时映射 502/504；
- [ ] 大文件流式下载不占内存；非法 artifact id 404；
- [ ] stream token 仅指定 run、短时过期。

**测试**：契约（状态码）；限流；下载；token；CORS/CSRF。

---

### M0-07 SSE `Last-Event-ID` + 自动重连 + 断点续传

> ✅ 已完成（2026-10-18）：`streamRunEvents` 支持 `Last-Event-ID`；无 `stream.end` 的关闭视为中断；`useRunEvents` 指数退避重连(1s→10s)并续传；执行面板/状态栏显示「SSE 重连中」。

**目标**：断线不丢事件、不重复渲染。

**范围**

- `apps/web/src/lib/runs-api.ts`：重连带 `Last-Event-ID`（最后 seq）；指数退避 + jitter；手动重试；
- `components/runs/use-run-events.ts`：连接状态、去重（已有 `mergeEvents`）、切 run 清理；
- 长流直连 runtime（BFF 只发 token）；短流走 BFF；
- UI：状态栏/执行面板显示连接态；失败提示。

**验收**

- [ ] 断网 30s 恢复：事件不重复、不丢（seq 连续）；
- [ ] 切页/切 run 不串事件；
- [ ] 非法游标可恢复（重连不带游标兜底）；
- [ ] 单测覆盖重连/去重/错误分支。

**测试**：单元（mock fetch/SSE）+ 手工弱网/E2E。

---

### M0-08 Markdown/代码/表格渲染（流式安全）

> ✅ 已完成（2026-10-18）：新增 `components/console/markdown.tsx`（react-markdown + GFM + rehype-sanitize），接入会话消息、计划、产物预览、工作区正文；流式保留光标。

**目标**：消息、计划、产物统一富文本渲染，流式输出安全。

**范围**

- 选型：`react-markdown` + `remark-gfm` + `rehype-sanitize`（代码高亮后置）；
- 组件 `components/console/markdown.tsx`：链接 target/rel、代码复制、表格横向滚动、空行处理；
- 接入：`pages/chat.tsx`、`run-detail.tsx`、`changes.tsx`；
- 流式：未闭合代码块/表格不崩，增量渲染。

**验收**

- [ ] 标题/列表/表格/代码块/链接正确渲染；
- [ ] `<script>`、`javascript:`、`onerror` 被清洗（有测试）；
- [ ] 流式 50 段增量无闪烁/崩溃；
- [ ] 纯文本换行/空白不回归。

**测试**：渲染快照 + XSS + 流式增量。

---

### M0-09 会话管理：重命名/删除/归档/搜索/分页

> ✅ 已完成（2026-10-18）：后端 `GET /conversations` 支持 q/archived/cursor(keyset) 分页，新增 PATCH(重命名/归档) 与 DELETE(软删除)；侧边栏支持搜索、归档视图、重命名、删除、加载更多。

**目标**：从「只能新建/选中」升级到可日常使用的会话列表。

**范围**

- 后端：`PATCH/DELETE /api/v1/conversations/{id}`；`GET /api/v1/conversations?cursor&limit&q&archived`；软删除保留审计；越权 404；
- Web：搜索框、重命名（双击/菜单）、归档/删除确认、分页加载更多；与 `?conversation=` 同步；乐观更新 + 失败回滚；
- 消息列表分页（先 limit，历史消息后续 cursor）。

**验收**

- [ ] 重命名即时生效且刷新保留；软删除后列表不可见、审计可查；
- [ ] 搜索命中标题（大小写/中文）；cursor 翻页无重复/遗漏；
- [ ] 归档会话默认不出现，可切换查看；
- [ ] 越权 404。

**测试**：API 集成（SQLite + PG）；Web 交互；分页边界。

---

### M0-10 产物预览（Markdown/图片）+ 下载（DB/文件版）

> ✅ 已完成（2026-10-18）：新增 `ArtifactPreview`（图片 data URL / Markdown / 文本回退）与 `lib/artifacts`（类型识别、文件名、Blob 下载），接入变更页、会话成品、运行详情、工作区；图片仅接受内联 data URL，远程 URL 不渲染。

**目标**：产物可读、可下载；对象存储前用 DB/文件系统过渡。

**范围**

- 内容类型识别；Markdown 渲染、图片预览（鉴权下载）、纯文本回退；
- 下载：BFF 流式返回（Content-Type/Disposition），鉴权；文件名清洗；
- 存储：staging 单实例可用 filesystem；**生产多实例必须 `ARTIFACT_STORE=database`**；大小上限；
- PDF/Office 显示「暂不支持预览，可下载」，不伪造预览；
- 清理当前非 diff 内容的误导性行号展示。

**验收**

- [ ] Markdown/图片/纯文本正确预览；大文件不阻塞页面；
- [ ] 下载文件名/类型正确；未登录 401；跨租户 404；
- [ ] 多实例 database 模式一致；
- [ ] 现有 Diff 查看不回归。

**测试**：预览组件；下载 API；鉴权；两种 store。

---

### M0-11 Sentry + 用量只读页 + 模型列表 + BYOK 占位

> ✅ 本地版已完成（2026-10-18，Sentry 暂缓）：llm-gateway `GET /v1/models` 静态模型列表；account-service `usage_events`/`byok_keys` 表与迁移、`GET /v1/usage/summary`、内部 `POST /v1/usage/events`、BYOK 加密存取（掩码返回）；BFF `/api/models`、`/api/usage/summary`、`/api/byok`；Web 新增 `/usage` 只读页、顶栏真实模型 chip、设置页 BYOK 占位。

**目标**：上线可观测与最小成本控制入口，为 M3 计费/BYOK 打底。

**范围**

- Sentry：`@sentry/react`（web）+ `sentry-sdk`（服务）；DSN env；release/version；PII 脱敏；sourcemap 可选；
- 模型列表：llm-gateway `GET /v1/models`（静态配置起步）；BFF `GET /api/models`；
- BYOK：设置页表单 + account-service 加密存储（应用层密钥；KMS 后置）；llm-gateway 按租户取 key（可先只存不用）；
- 用量：`usage_events` 表（account-service）+ `GET /v1/usage/summary`；M0 先接 run 级 token（失败可空）；Web 只读页（今日/本月 tokens、次数、空态）；
- 顶部模型 chip 接真实模型名，去掉静态假下拉。

**验收**

- [ ] 前端/服务错误在 Sentry 可见，含 release/请求 id；正文/PII 不上报；
- [ ] 模型列表可用，chip 显示真实模型；
- [ ] BYOK 保存后再次读取显示掩码，不落日志/前端明文；
- [ ] 用量页有真实数据或明确空态；聚合 <300ms。

**测试**：Sentry 脱敏；模型列表；BYOK 存取/掩码；用量聚合。

**开放问题**：usage 归属与上报协议——M0 先 account-service 聚合、orchestrator 内部写入。

---

### M0-12 附件最小版（DB/本地文件 + 类型/大小限制）

> ✅ 已完成（2026-10-18）：orchestrator 新增 `attachments` 表（内容存 DB，迁移 `e2b5c9d3f7a1`）、上传/列表/下载/删除、类型与 10MB 大小限制、单条消息最多 5 个附件并绑定 `message_id`；聊天页支持选择/移除/下载附件，BFF 代理 multipart 与下载透传；测试覆盖上传/下载/类型/大小/绑定/租户隔离。

**目标**：消息可带附件，先满足图片/小文档；对象存储后置。

**范围**

- workspace-service：`POST /v1/workspaces/{id}/attachments`（multipart）、`GET/DELETE /v1/attachments/{id}`；元数据 `filename/content_type/size/checksum`；filesystem（dev/staging）/database（多实例）；
- orchestrator：`MessageCreate.attachment_ids[]`、`MessageRead.attachments[]`；发送时校验归属；
- BFF：`POST /api/attachments`、`GET /api/files/:id`；
- Web：附件按钮/拖拽、草稿附件条、消息附件展示、图片预览；单文件 ≤10MB，白名单（图片/PDF/txt/docx/markdown；解析后置）；
- 清理：删除会话/消息时软删 + 后续 GC。

**验收**

- [ ] 上传/预览/下载/删除闭环；类型/大小拦截；
- [ ] 跨租户不可访问；历史消息展示附件；
- [ ] 多实例 database store 可用；filesystem 仅单实例（文档注明）；
- [ ] 失败/超时/重复上传错误明确。

**测试**：上传 API；消息关联；鉴权；Web 交互。

**开放问题**：附件归属——建议 workspace-service（已有 artifact store 抽象），M0 按此实现。

---

### M0-13 内网/离线部署（已移出 M0，暂缓）

> 状态：**暂缓，不纳入 M0**。当前只要求本地可启动。
> 未来若恢复内网交付，参考 [tasks-m0-13.md](./tasks-m0-13.md)（Linux VM + systemd 方案，10 个子任务）。

---

## 3. 排期与并行轨道

| 周  | Platform/Service                            | Web                              | Auth/BFF                       | QA/文档                 |
| --- | ------------------------------------------- | -------------------------------- | ------------------------------ | ----------------------- |
| W1  | M0-02 容器化、M0-03 云 PG/Redis、M0-04 起步 | M0-07 SSE、M0-08 Markdown        | M0-01 Vercel 骨架、M0-05 spike | 测试计划、环境清单      |
| W2  | M0-04 完成、M0-11 后端                      | M0-09 会话、M0-10 产物、M0-12 UI | M0-06 BFF、M0-05 收尾          | E2E、迁移 CI            |
| W3  | 联调、压测、修复                            | 体验收尾、空态/错误态            | 安全评审（Cookie/CSRF/XSS）    | E2E/安全/回滚演练、文档 |

**关键路径**：M0-01 → M0-05 → M0-06 → 全链路联调；M0-02 → M0-03 → M0-04 → M0-10/12。
**可并行**：M0-07/08/09 不依赖平台；M0-10/12 只依赖 M0-03 存储模式。

---

## 4. 契约与配置清单

### 4.1 BFF 端点（新增）

| 方法 | 路径                                 | 说明                    | 鉴权 |
| ---- | ------------------------------------ | ----------------------- | ---- |
| POST | `/api/auth/login`                    | 邮箱密码登录，设 Cookie | 公开 |
| GET  | `/api/auth/oauth/:provider/start`    | 302 到 IdP              | 公开 |
| GET  | `/api/auth/oauth/:provider/callback` | 回调，设 Cookie         | 公开 |
| POST | `/api/auth/logout`                   | 清 Cookie + 吊销        | 会话 |
| GET  | `/api/auth/session`                  | 当前用户/租户           | 会话 |
| POST | `/api/auth/refresh`                  | 静默续期                | 会话 |
| ALL  | `/api/runtime/*`                     | allowlist 代理          | 会话 |
| POST | `/api/stream-token`                  | 短时 SSE token          | 会话 |
| GET  | `/api/files/:artifactId`             | 鉴权流式下载            | 会话 |
| POST | `/api/attachments`                   | multipart 上传          | 会话 |
| GET  | `/api/models`                        | 模型列表                | 会话 |
| GET  | `/api/usage/summary`                 | 用量汇总                | 会话 |
| GET  | `/api/health`                        | 健康检查                | 公开 |

### 4.2 后端新增/变更端点

| 服务               | 端点                                                     | 说明             |
| ------------------ | -------------------------------------------------------- | ---------------- | ---------- |
| account-service    | `GET /v1/auth/oauth/{provider}/start                     | callback`        | 社交 OAuth |
| account-service    | `GET /v1/usage/summary`、`POST /v1/usage/events`（内部） | 用量读写         |
| account-service    | `PUT/GET/DELETE /v1/credentials/byok`（内部）            | BYOK 占位        |
| agent-orchestrator | `PATCH/DELETE /api/v1/conversations/{id}`                | 重命名/归档/软删 |
| agent-orchestrator | `GET /api/v1/conversations?cursor&q&archived`            | 搜索/分页        |
| agent-orchestrator | `POST /api/v1/runs/{id}/cancel`（如需）                  | 取消 job         |
| llm-gateway        | `GET /v1/models`                                         | 模型列表         |
| workspace-service  | `POST /v1/workspaces/{id}/attachments` 等                | 附件             |

### 4.3 数据表（新增）

- `jobs`（orchestrator）：队列/重试/lease；
- `oauth_identities`（account）：provider/subject/user_id；
- `usage_events`（account）：tenant/user/run/model/tokens/tool_calls/cost；
- `attachments`（workspace）：workspace_id/message_id/filename/content_type/size/checksum/object_key；
- `byok_credentials`（account）：tenant/provider/encrypted_key/last4/created_at；
- 迁移要求：PG + SQLite 双兼容；CI 对 PG 跑 `alembic upgrade head`。

### 4.4 环境变量（示例）

| 变量                                                 | 用途                                       | 环境         |
| ---------------------------------------------------- | ------------------------------------------ | ------------ |
| `*_DATABASE_URL`                                     | 云 PG（生产）/ SQLite（开发）              | 全部         |
| `REDIS_URL`                                          | 云 Redis（生产）/ 本地 Redis（开发，可选） | 全部         |
| `SENTRY_DSN`                                         | Sentry                                     | staging/prod |
| `BFF_SESSION_SECRET`                                 | Cookie 加密/签名                           | staging/prod |
| `BFF_COOKIE_DOMAIN`                                  | Cookie 域                                  | staging/prod |
| `RUNTIME_URL` / `RUNTIME_STREAM_SECRET`              | runtime 与 SSE token                       | staging/prod |
| `GOOGLE_CLIENT_ID/SECRET`、`GITHUB_CLIENT_ID/SECRET` | 社交 OAuth                                 | staging/prod |
| `LLM_GATEWAY_URL`、`ACCOUNT_SERVICE_URL`             | 服务地址                                   | 全部         |
| `ARTIFACT_STORE`                                     | `database`（多实例）/`filesystem`（本地）  | 全部         |
| `VITE_*`                                             | SPA 公开配置（不含 secret）                | 全部         |

---

## 5. 测试与质量门禁

- **单测**：Markdown 清洗、SSE 重连、Cookie/CSRF、job lease、usage 聚合；
- **集成**：TestClient + SQLite；关键链路另跑云 PG（CI）；
- **E2E（Playwright）**：
  1. 登录（邮箱 + Google mock）→ 新建会话 → SSE 流式 → 刷新恢复；
  2. 上传附件 → 发送 → 历史附件可见 → 下载；
  3. 重命名/搜索/归档/删除会话；
  4. 产物预览/下载；
  5. 断网重连（route abort）→ 事件不丢；
- **安全**：Cookie 属性、CSRF、XSS（Markdown）、越权（跨租户 404）、上传类型/大小、SSRF（BFF allowlist）；
- **性能**：100 并发 run 压测；大文件下载内存曲线；会话列表 1k 条分页；
- **本地启动**：六业务服务 + BFF + web 一键启动、健康检查全绿；SQLite 自动建表；LLM 桩可用；
- **CI 门禁**：lint/typecheck/test/build、PG 迁移、E2E smoke、Sentry release（可选）。

---

## 6. 发布与回滚

1. **staging**：PR preview + staging 部署；内部 dogfood 2 天；
2. **prod preview**：域名/allowlist 小流量（Free 邀请）；
3. **prod**：全量；Sentry/日志观察 24h；
4. **回滚**：Vercel 上一 deployment；容器上一镜像 tag/revision；DB 只做向前兼容变更，不可逆迁移需双写/回填；
5. **Feature flags**：附件、Markdown、BYOK 可先用环境开关灰度。

---

## 7. 风险与开放问题

| 风险/问题                | 处理                                                    |
| ------------------------ | ------------------------------------------------------- |
| Vercel 函数时长限制      | 长流直连 runtime；BFF 只代理短流                        |
| 多实例 + filesystem 产物 | 生产强制 `ARTIFACT_STORE=database`；staging 可文件系统  |
| SQLite/PG 差异           | CI 对 PG 跑迁移/集成；SQLite 仅开发                     |
| Redis 断连               | 仅限流/锁/缓存；进程内降级；关键状态在 PG               |
| 无对象存储 → 附件/大文件 | 10MB 限制 + 白名单；PDF/Office 不承诺预览；M2 前评估    |
| 自建 OAuth 安全          | state/PKCE、回调白名单、CSRF、Cookie 安全属性；安全评审 |
| PG job 队列吞吐          | 100 并发压测；lease/重试/死信；必要时换专业队列         |
| BYOK 无 KMS              | 应用层加密 + 掩码 + 不落日志；KMS 后置                  |
| Sentry PII               | 脱敏白名单；不传正文/token；采样                        |

**待拍板**

1. BFF 会话：加密 Cookie vs Redis 会话 → M0-05；
2. `apps/site` 技术选型：Vite + vite-react-ssg vs Astro → M0-01（1 天 spike）；
3. 附件归属：workspace-service（建议）vs orchestrator → M0-12；
4. usage 归属与上报协议：account-service 聚合（建议）+ orchestrator 内部写入 → M0-11；
5. 社交登录范围：Google/GitHub 先，Microsoft 后 → M0-05；
6. 本地模型是否启用（默认 `LLM_ENABLED=false` 桩；需要真实模型时再配 `api_base`）。

---

## 8. 每个任务的完成定义（DoD 模板）

- [ ] 代码合并 + 单测/集成测试通过；
- [ ] 文档/环境变量/迁移说明更新；
- [ ] staging 验收（对应任务验收项）；
- [ ] 回滚/降级路径明确；
- [ ] 监控/日志可定位（Sentry/请求 id）；
- [ ] 无 P0/P1 已知缺陷；遗留问题登记到 M0 风险表。
