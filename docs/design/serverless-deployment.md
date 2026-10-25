# Serverless 部署方案对比与改造清单

> 状态：设计定稿（2026-10-25）。本文回答两个问题：**把 beauty-moon 从 docker compose 迁到 serverless 平台，选哪个平台**、**仓库需要改哪些地方**。
> 关联文档：[architecture.md](../architecture.md)（§10 部署与迁移）、[tool-service.md](./tool-service.md)（工具服务设计）、[local-dev.md](../local-dev.md)。

## 1. 摘要

### 1.1 结论先行

用户目标原话是「不打算部署到 docker 上，打算部署到 serverless 上」。这句话有两种可行解释，对应两类平台：

| 解释                 | 含义                                      | 平台类型                                 | 本仓库改造成本                |
| -------------------- | ----------------------------------------- | ---------------------------------------- | ----------------------------- |
| A. 不自己运维 Docker | 不再用 compose/裸机容器；镜像构建仍可保留 | Cloud Run / ACA / Fly / Modal 等托管容器 | 小（主要改配置与后台任务）    |
| B. 完全不用容器镜像  | 代码打包成函数/模块，平台负责构建         | Lambda zip / Cloud Functions / Workers   | 极大（8 个 FastAPI 服务重写） |

**本方案推荐 A**：8 个 Python 服务全部依赖 FastAPI 的 lifespan、SSE 流式响应、长任务与后台循环，改成函数模型（B）等于重写。A 类平台里，**GCP Cloud Run 是唯一同时满足「SSE 长请求 + 常驻后台 worker + 定时/一次性任务 + scale-to-zero + 现成 PG/对象存储/密钥/IaC」且改造成本可控的**。

推荐目标架构（详见 §4）：

| 层       | 选型                                                                                                                | 说明                                                 |
| -------- | ------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| 计算     | **Cloud Run services** ×8 + **Cloud Run worker pools** + **Cloud Run Jobs**                                         | 8 个服务原样上；后台循环单独 worker；迁移/清理用 Job |
| 数据库   | **Cloud SQL for PostgreSQL 16**（6 个库）或 **Neon**（serverless PG，连接池更省心）                                 | 保留一服务一库；见 §5.4                              |
| 交付物   | **GCS**（新增 ArtifactStore 实现）；过渡期继续用 DB 列                                                              | 见 §5.5                                              |
| 密钥     | **Secret Manager**（签名 PEM、INTERNAL_SECRET、session secret、供应商 key）                                         | 见 §5.8                                              |
| 沙箱     | **E2B**（Firecracker microVM，首选）或 **Modal Sandboxes**；microsandbox 在 Cloud Run 不可用（无 /dev/kvm）         | 只换 SandboxProvider 实现，接口不变；见 §5.6         |
| 前端     | `apps/web` 静态产物 → **GCS + Cloud CDN**；同一 Global LB 把 `/api/*` 路由到 BFF                                    | 或退化为 Cloud Run + nginx（多一个服务，见 §5.9）    |
| 入口     | **Global External HTTPS LB + Cloud Armor**（限流、WAF、TLS）                                                        | 同源 Cookie、SSE 穿透；见 §4.3                       |
| CI/CD    | GitHub Actions + Workload Identity Federation → Artifact Registry → `gcloud run deploy`；**Terraform** 管理基础设施 | 见 §6                                                |
| 可观测性 | Cloud Logging（结构化 stdout）+ Cloud Trace（OTel）+ Cloud Monitoring 指标 + Sentry                                 | 见 §5.10                                             |

次选与适用条件（详见 §3.3）：

- **Fly.io**：改造最小（同一镜像、长连接无请求时长上限、scale-to-zero 用 auto_stop/suspend），代价是它更像「托管 VM」而非函数平台，且背景任务/数据库仍要自己拼。
- **Modal**：Python 原生 + 内置 Sandboxes，可把 orchestrator/tool-service/沙箱放一个平台；代价是与现有 8 服务 + BFF 的容器编排模型不同，需重写部署描述（不是代码）。
- **AWS（Lambda Web Adapter + ECS Fargate）**：只在组织已经重仓 AWS 且能接受「SSE 15 分钟断流 + 常驻进程另开 Fargate」时选。
- **不推荐**：Cloudflare Workers（Python/FastAPI 不匹配、CPU 5 分钟上限、128MB）、Vercel Functions（30 分钟上限、无后台进程）、Render/Railway 常驻型（没有真正的 scale-to-zero）。

### 1.2 改造分级（全量见 §5）

| 级别       | 数量 | 内容（摘要）                                                                                                                                                                               |
| ---------- | ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **必须改** | 12   | PORT 支持、镜像入口与启动探针、API/worker 角色拆分、Cloud SQL 连接与迁移 Job、GCS 交付物、沙箱 provider 替换、Secret Manager、SSE 超时、Cookie/issuer 配置、LB 与静态托管、CI/CD、限流迁移 |
| **建议改** | 8    | 连接池参数、min/max 实例与并发、日志与 OTel 出口、告警、制品保留策略、工具调用保留清理、预算与配额、runbook                                                                                |
| **可不动** | 6    | FastAPI 业务代码、LangGraph 图与 checkpointer、SQLAlchemy 模型、alembic 迁移脚本、前端页面（除部署环境变量）、本地 compose 开发                                                            |

### 1.3 分期与估算

| 阶段 | 内容                                                          | 产出                                | 人日（1 人） |
| ---- | ------------------------------------------------------------- | ----------------------------------- | ------------ |
| P0   | 基础设施即代码骨架 + 镜像可部署（PORT/探针/角色开关）         | staging Cloud Run 能跑起 `/healthz` | 2–3          |
| P1   | 无状态服务上云（account/bff/llm-gateway/connector/knowledge） | staging 全链路 HTTP 可用            | 2–3          |
| P2   | orchestrator（API + worker pool + Jobs 迁移）+ SSE 验证       | run 全流程可用，SSE 断线可续        | 3–4          |
| P3   | workspace + tool-service + 外部沙箱 + GCS 交付物              | 文件工具 E2E、交付物下载            | 4–6          |
| P4   | 前端静态托管 + LB 切流 + 可观测性/告警 + 回滚演练             | prod 切换完成                       | 2–3          |

合计约 **13–19 人日**（不含沙箱供应商的商务流程）。若选 Fly.io 可压缩到 **6–10 人日**；若选 AWS Lambda 方案则 **25+ 人日**。

## 2. 现状盘点：哪些地方和「服务器」绑定了

### 2.1 部署单元

| 单元                 | 入口                                                       | 端口 | 状态性                                                 |
| -------------------- | ---------------------------------------------------------- | ---- | ------------------------------------------------------ |
| `apps/web`           | 静态 SPA（Vite 构建）                                      | 80   | 无状态（构建期注入 `VITE_*`）                          |
| `apps/site`          | Next.js 静态导出                                           | 80   | 无状态（可继续 Vercel）                                |
| `services/bff`       | FastAPI + httpx 代理                                       | 8007 | 无状态（Cookie 用共享 secret 签名）；限流在进程内      |
| `agent-orchestrator` | FastAPI + LangGraph + job worker                           | 8002 | **有状态任务**（jobs 表 + PG checkpointer + 后台循环） |
| `account-service`    | FastAPI + 自建 IdP                                         | 8001 | 半状态（RSA 签名密钥需持久）                           |
| `llm-gateway`        | FastAPI + litellm                                          | 8003 | 无状态                                                 |
| `connector-service`  | FastAPI                                                    | 8004 | 无状态（凭证密钥来自配置）                             |
| `knowledge-service`  | FastAPI + 嵌入提供方                                       | 8006 | 无状态（数据在 DB）                                    |
| `workspace-service`  | FastAPI + 沙箱 provider + 交付物文件                       | 8005 | **有状态**（沙箱 microVM、本地交付物目录、回收循环）   |
| `tool-service`       | FastAPI + 沙箱执行器 + 知识库代理                          | 8008 | 无状态（审计在 DB，执行在沙箱）                        |
| PostgreSQL 16        | 6 个库（account/agent/connector/knowledge/workspace/tool） | 5432 | 有状态                                                 |
| Redis 7              | compose 已起                                               | 6379 | **业务未使用**（可直接去掉）                           |
| migrate 一次性作业   | 顺序对 6 个库 `alembic upgrade head`                       | —    | 一次性                                                 |
| web（nginx）         | SPA + 反代 `/api`                                          | 8080 | 无状态                                                 |

### 2.2 运行时耦合点（serverless 迁移必须逐条处理）

| #   | 耦合点             | 现状（file:line）                                                                                                                                                                                                          | 为什么在 serverless 上会坏                                              | 处理        |
| --- | ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- | ----------- |
| 1   | 固定端口           | `deploy/docker/python-service.Dockerfile:77` CMD 硬编码每服务端口；无 `PORT` 读取                                                                                                                                          | Cloud Run 通过 `PORT` 注入端口，忽略会 502                              | §5.1        |
| 2   | Docker HEALTHCHECK | `python-service.Dockerfile:71-72`                                                                                                                                                                                          | Cloud Run 不执行 Dockerfile HEALTHCHECK，需 startup probe 打 `/healthz` | §5.1        |
| 3   | 单镜像多服务       | `python-service.Dockerfile:49-77` `ARG SERVICE` + case 分发                                                                                                                                                                | 可用（每服务一个 tag/一次 build），但冷启动共享大镜像                   | §5.1        |
| 4   | 后台常驻循环       | orchestrator：`core/container.py:429` outbox、`:486` jobs worker、`:493` interrupts sweeper；workspace：`workspace/main.py:95-97` sandbox reaper                                                                           | Cloud Run service 会缩到 0，进程被杀→任务不执行、超时不触发             | §5.3        |
| 5   | PG 连接方式        | 各服务 `database_url` 默认 `sqlite+aiosqlite:///./*.db`；compose 用 `postgresql+asyncpg://postgres:5432`                                                                                                                   | serverless 需要 Cloud SQL/Neon 连接方式与连接池上限                     | §5.4        |
| 6   | 数据库迁移         | compose `migrate` 作业（`deploy/docker-compose.yml:72-96`）                                                                                                                                                                | 无 compose；需 Cloud Run Job / CI 步骤                                  | §5.4        |
| 7   | 交付物存储         | orchestrator `artifact_store=database`（`core/config.py:41`）；workspace `artifact_store=filesystem`（`workspace/core/config.py:19-20`）+ 本地卷                                                                           | 本地卷在 serverless 不持久、不共享；GCS 实现缺失                        | §5.5        |
| 8   | 沙箱               | `workspace/core/config.py:30-45` `sandbox_provider=microsandbox`，需要 `/dev/kvm`；MCP stdio 也走沙箱                                                                                                                      | Cloud Run/Fargate/Workers 无 KVM，microsandbox 起不来                   | §5.6        |
| 9   | 签名密钥           | account-service `signing_key_path` 空则进程内生成（`account/core/idp.py:99-118`，注释已写明生产必须持久）                                                                                                                  | 多实例/重启→已发 token 全失效                                           | §5.8        |
| 10  | 内部密钥           | `INTERNAL_SECRET` 经 compose 环境注入（`docker-compose.yml:228` 等）                                                                                                                                                       | 需 Secret Manager 注入，且不能进镜像                                    | §5.8        |
| 11  | 进程内限流         | `bff/main.py:67-89` `SlidingWindowLimiter`                                                                                                                                                                                 | 多实例各算各的，限流失效                                                | §5.8 / §4.3 |
| 12  | Cookie 会话        | `bff/core/config.py:17,28-29`：`app_origin=http://localhost:5173`、`cookie_secure=False`                                                                                                                                   | 生产必须 https + 正确 origin，否则 Cookie 不下发/CSRF 失败              | §5.8        |
| 13  | SSE 长连接         | orchestrator `api/v1/runs.py:169` 支持 `Last-Event-ID`；BFF `proxy.py:226-245` 流式转发；BFF 上游客户端 `main.py:42-43` 超时 30s（httpx 的 30s 是**每块读超时**，orchestrator keepalive 15s 一帧，当前恰好安全但依赖巧合） | 平台单请求时长上限会切断流；BFF 应把流式 client 的读超时显式设长/关闭   | §5.7        |
| 14  | 服务间地址         | 默认 `http://127.0.0.1:80xx`（`bff/core/config.py:19-24`、orchestrator `core/config.py`）                                                                                                                                  | serverless 无 localhost 拓扑，需服务 URL/内部 ingress 或 VPC            | §5.2        |
| 15  | 静态托管           | web 镜像内 nginx（`deploy/web/nginx.conf`）+ 同源反代                                                                                                                                                                      | 需改成 bucket/CDN + LB 路由，或保留 nginx 容器                          | §5.9        |
| 16  | 日志/追踪          | 结构化 stdout + OTel/Sentry（`common/observability.py`）                                                                                                                                                                   | 只需配置出口，不改代码                                                  | §5.10       |

### 2.3 已具备的 serverless 友好性（不用改的部分）

- **配置全部走环境变量**：每个服务一个前缀（`ACCOUNT__`/`AGENT_ORCHESTRATOR__`/`TOOL__`…），无配置文件路径依赖。
- **数据库抽象干净**：SQLAlchemy async + asyncpg，PG 与 SQLite 同一套 URL；`AUTO_CREATE_TABLES=false` 生产契约已存在。
- **交付物有存储抽象**：`ArtifactStore` Protocol（`packages/common/common/artifacts.py:44`）只有 put/get/delete，加 GCS/S3 实现不影响调用方。
- **沙箱有 Provider 抽象**：`SandboxProvider`（`services/workspace-service/workspace/services/sandbox_provider.py`）已支持替换（`docs/architecture.md:460` 写明备选 E2B/Fly Machines）；tool-service 只经 HTTP 调 workspace，不直连 runtime。
- **SSE 断线续传已实现**：`GET /v1/runs/{id}/events/stream` 接受 `Last-Event-ID`（`api/v1/runs.py:169-175`），前端 EventSource 自动重连；run 状态可从 PG 重建。
- **pause/resume 可跨实例**：审批/确认/提问的中断（`interrupt`）与 LangGraph Postgres checkpointer 绑定，resume 请求落到哪个实例都能继续。
- **零网络单测**：`--disable-socket` + Fake 适配器，不依赖外部服务（迁移后 CI 仍可零网络跑单测）。

### 2.4 硬约束（serverless 方案必须满足，不能为了省事降级）

1. **SSE 不能变成轮询**：run 事件流是产品核心体验（token 增量、工具调用、审批/提问卡片）。平台必须支持流式 HTTP；单流可能持续到 run 结束（人工审批/提问可停很久），平台上限用完必须能靠 `Last-Event-ID` 透明续流。
2. **人工暂停期间不能烧 CPU**：审批/确认/提问是「挂起」而非「忙等」，持久化检查点必须跨实例可恢复；不能用「保持一个进程/连接一直活着」来实现。
3. **文件工具必须在隔离环境**：read/write/grep 由 tool-service 委托 workspace 沙箱执行（[tool-service.md](./tool-service.md) §8.1）；serverless 平台若无 KVM，必须换成外部 microVM provider，**不能退化为在服务进程里直读宿主文件系统**。
4. **一服务一库不能合并**：6 个库各自 alembic 迁移历史独立（`deploy/postgres/initdb/01-create-databases.sql` 注释写明 `alembic_version` 冲突），合并会破坏迁移。
5. **同源 Cookie 会话**：BFF 的 httpOnly Cookie + CSRF 依赖前端与 `/api` 同源；拆成两个域名要重新设计（不推荐）。
6. **本地开发保持不变**：compose 仍是本地一键启动方式；serverless 只影响 staging/prod。

## 3. 选型：平台对比与结论

### 3.1 选型维度与权重

权重 5 = 一票否决级（不满足就不能选），4 = 需要改造才可用，3 = 影响成本/体验，2 = 可接受妥协。

| 维度             | 关注点                                                     | 权重 | 为什么重要（仓库证据）                                                                                                               |
| ---------------- | ---------------------------------------------------------- | ---- | ------------------------------------------------------------------------------------------------------------------------------------ |
| SSE / 流式响应   | 平台是否原样透传 `text/event-stream`，是否有缓冲或压缩干预 | 5    | run 事件流是产品核心（token 增量/工具调用/审批/提问），BFF `proxy.py:226-245` 逐块转发，nginx `proxy_buffering off`                  |
| 单请求时长上限   | 单条 HTTP 连接最长能活多久，超时后行为                     | 5    | 一次 run 可能持续数十分钟；审批/提问可挂起到用户回来（默认提问超时 900s，可关闭）                                                    |
| 后台常驻任务     | 是否支持进程内常驻循环（非请求驱动）                       | 5    | outbox dispatcher `container.py:429`、jobs worker `:486`、interrupts sweeper `:493`、workspace 沙箱 reaper `workspace/main.py:95-97` |
| 缩容到 0         | 空闲时是否停止计费                                         | 3    | 内网/早期用户量低，常驻 8 服务成本不划算；但 worker 缩到 0 会停后台循环，需区分角色                                                  |
| 隔离沙箱         | 能否跑任意 argv、有独立文件系统、无宿主直读                | 4    | 文件工具必须隔离（[tool-service.md](./tool-service.md) §2.4 硬约束 3）；`microsandbox` 需要 `/dev/kvm`                               |
| 镜像原地复用     | 现有 Dockerfile/镜像能否直接部署                           | 4    | 8 服务共用 `deploy/docker/python-service.Dockerfile`，容器化已完成，重写成本最高                                                     |
| 托管 Postgres    | 是否有托管 PG 16、连接池、备份                             | 4    | 6 个库、5 个 alembic 迁移链；LangGraph checkpointer 依赖 PG                                                                          |
| 对象存储         | 是否有 S3/GCS 兼容 API + 预签名 URL                        | 4    | 交付物存储抽象只有 database/filesystem 两种实现（`common/artifacts.py`），serverless 必须补对象存储                                  |
| 密钥管理         | 密钥/证书能否注入且不落盘                                  | 4    | `INTERNAL_SECRET`、`SESSION_SECRET`、account 签名密钥（`idp.py:99-118`）、connector `credential_secret`                              |
| 静态托管与同源   | SPA + `/api` 同源（Cookie 会话）                           | 3    | BFF httpOnly Cookie + CSRF 依赖同源（§2.4 约束 5）                                                                                   |
| 可观测性         | 日志/指标/追踪接入                                         | 2    | 已接 OTel/Sentry（`common/observability.py`），只需出口                                                                              |
| 锁定与运维复杂度 | 是否引入新 DSL/专有框架                                    | 3    | 团队规模小，长期可维护性优先                                                                                                         |

### 3.2 平台对比矩阵

矩阵只列「能承载 8 个 FastAPI 服务」的候选；纯函数平台（Vercel/Workers）单独在 §3.3 说明为什么排除。数值来自各平台官方文档（2026-10 抓取；本环境访问受限的条目已标注以官方为准）。

| 平台                                 | 形态                         | SSE / 流式                                 | 单请求上限                                                            | 后台循环                                                      | 缩容到 0                      | 镜像复用                       | 隔离沙箱                                        | 结论                   |
| ------------------------------------ | ---------------------------- | ------------------------------------------ | --------------------------------------------------------------------- | ------------------------------------------------------------- | ----------------------------- | ------------------------------ | ----------------------------------------------- | ---------------------- |
| **Cloud Run**（services）            | 托管容器                     | 原生支持，逐块透传                         | 请求超时最长 60 分钟（服务级配置，官方文档为准）                      | 需 worker pools（request 驱动不可靠）                         | 是（min-instances 0）         | 是                             | 平台无 KVM；2026 新增 code execution 能力待验证 | **首选**               |
| **Cloud Run**（worker pools + Jobs） | 托管容器池 / 批处理          | worker pools 无外部 HTTP 入口；Jobs 无 SSE | Jobs 任务超时上限 24 小时                                             | **是**（worker pools 承载常驻循环）                           | worker pools 可 0（会停循环） | 是                             | 同上                                            | **首选方案的后台平面** |
| **AWS Lambda + Web Adapter**         | 函数                         | LWA 支持 response streaming                | 15 分钟（异步/事件源 90 分钟的 Managed Instances 不适用于 HTTP 同步） | 否                                                            | 是                            | 需打包 LWA                     | 无（Lambda MicroVMs 是另一产品）                | 仅备选                 |
| **AWS ECS Fargate / App Runner**     | 托管容器                     | 原生                                       | 无硬上限（ALB 空闲超时可调）                                          | 是                                                            | 否（Fargate 常驻计费）        | 是                             | 无 KVM                                          | 仅备选                 |
| **AWS Lambda MicroVMs**              | 有状态 microVM（2026-10 新） | 需自建网关                                 | 以官方为准                                                            | 否                                                            | 是                            | 需镜像适配                     | 有（microVM）                                   | 沙箱候选               |
| **Azure Container Apps**             | 托管容器                     | 原生                                       | ingress 默认 240s，可配到 2400s（40 分钟）                            | Jobs 支持定时/事件触发；无 ingress 的常驻循环需 minReplicas≥1 | 是（min 0）                   | 是                             | 无 KVM                                          | 备选（Azure 亲和时）   |
| **Fly.io Machines**                  | serverless VM（Firecracker） | 无请求时长上限（长连接友好）               | 无                                                                    | 是（进程常驻，自动停/启）                                     | 是（auto_stop=suspend）       | 是                             | **Machines 本身是 microVM**，可作为沙箱池       | **次选（改造最小）**   |
| **Modal**                            | Python 原生 serverless       | Web Functions / Streaming endpoints        | 以官方为准（函数有超时上限，长流需设计）                              | 是（定时/队列/常驻容器）                                      | 是                            | 部分（Python 代码，ASGI 可挂） | **Sandboxes 一等公民**（argv/文件/快照）        | **沙箱与批处理补充**   |
| **E2B**                              | 沙箱 PaaS                    | 不适用（只做沙箱）                         | 不适用                                                                | 不适用                                                        | 不适用                        | 不适用                         | **有**（Firecracker，SDK exec）                 | 沙箱 provider 候选     |

> 说明：Cloud Run 的「请求超时 60 分钟 / Jobs 24 小时 / 默认并发 80」本环境无法访问官方正文（`cloud.google.com` 抓取失败），文档按官方文档既有口径记录并在验收阶段复核；Azure ACA 的 2400s 上限来自官方 `requestTimeoutSeconds` 文档与 Microsoft Q&A。

### 3.3 逐平台结论

**Cloud Run（首选）**。8 个服务全部是 FastAPI + uvicorn，镜像可直接推 Artifact Registry 部署；SSE 通过 HTTP/2 逐块透传，配合 `Last-Event-ID` 可在请求超时后无缝续流；后端平面拆成三类部署单元：

- `services`：bff、account、llm-gateway、connector、knowledge、workspace、tool-service（请求驱动，可缩到 0）；
- `worker pools`：orchestrator 的 outbox dispatcher / jobs worker / interrupts sweeper、workspace 的 sandbox reaper（常驻循环，不接 HTTP 或不依赖 HTTP）；
- `Jobs`：6 库 alembic 迁移、本地到云的数据迁移、过期 tool_calls 清理（一次执行或定时触发）。

缺口与对策：① 沙箱不可用（无 KVM）→ 接 E2B 或 Modal Sandboxes 作为 `SandboxProvider` 实现（§5.6）；② 请求超时上限 → SSE 客户端续流 + run 状态机幂等（§5.7）；③ 进程内限流（`bff/main.py:67-89`）多实例失效 → 用 Cloud Armor + 实例级限流或 Redis（§5.8）；④ 服务间 URL 从 `127.0.0.1` 改为 Cloud Run 服务 URL，内网走 Serverless VPC Connector / Direct VPC egress（§5.2）。

**Fly.io（次选，改造最小）**。同一个镜像 `fly deploy` 即可跑；Machines 是 Firecracker microVM，天然支持长连接（无请求时长上限），`auto_stop_machines = suspend` + `min_machines_running` 兼顾成本与后台循环；对象存储用 Tigris（S3 兼容）；Postgres 用 Managed Postgres 或 Neon。最大优势是**可以把 workspace-service 的 SandboxProvider 直接实现成 Fly Machines provider**（自建沙箱池，替代 microsandbox），不需要引入 E2B/Modal。代价：多区域/高可用与合规能力弱于 GCP/AWS；Machines 的磁盘是临时盘，交付物仍要对象存储。

**Modal（沙箱与批处理补充，也可整体托管）**。Python 原生，`@modal.asgi_app()` 可挂现成 FastAPI，`@modal.concurrent(max_inputs)` 提供单容器并发，Secrets/Volumes/Queues 齐备；**Sandboxes 可直接实现 `SandboxProvider`**（exec argv、文件读写、快照、idle timeout），与本仓库 tool-service 的调用形态几乎一一对应。适合两种用法：① 只把沙箱切到 Modal；② 整体托管（orchestrator 与工具服务用 ASGI app，后台循环用定时函数）。缺点是绑定 Modal 生态，PG/对象存储需外部服务。

**AWS（组织已有 AWS 时）**。Lambda 同步 HTTP 上限 15 分钟，直接跑 orchestrator SSE 必须靠 LWA response streaming + `Last-Event-ID` 续流；后台循环（outbox/jobs/sweeper）不能放 Lambda，需 ECS Fargate 常驻；迁移用一次性任务；PG 用 Aurora Serverless v2 或 RDS；对象存储 S3；密钥 Secrets Manager；沙箱可评估 2026-10 新增的 Lambda MicroVMs（有状态 microVM，支持挂起/恢复）。整体改造量最大（LWA 打包、双层网络、IAM），只有在已有 AWS 组织/合规要求时才选。

**Azure Container Apps（Azure 亲和时）**。镜像复用、scale-to-zero、Jobs 做迁移、KEDA 规则；SSE 受 ingress 40 分钟上限约束，需续流；常驻后台循环放在无 ingress 的容器且 `minReplicas≥1`（无法缩到 0），成本略高；沙箱需外部 provider。

**Vercel**：只继续托管 `apps/site`（现状 [architecture.md:517](../architecture.md) 已写明 Vercel 只部署官网）。Functions 虽支持流式，但最长 30 分钟、无后台循环、无长连接常驻进程，不能承载 BFF/orchestrator。可作为前端静态托管的可选项，但 `/api` 同源反代仍需 LB 或自定义域路由。

**Cloudflare Workers/Containers**：CPU 时间 Paid 上限 5 分钟、内存 128MB、Python Workers 不支持 FastAPI/ASGI（不是 CPython 全量运行时），与现有服务不兼容；Containers 可跑镜像但等于托管容器且受 CPU/内存/网络约束更严；R2 可作对象存储。结论：不用它承载后端，如需要边缘缓存可在 CDN 层叠加。

**Render / Railway**：本质托管容器，无 Jobs/worker pool 原语、scale-to-zero 有限、PG 与对象存储能力弱，只适合 demo/预览环境。

**自建 Kubernetes**：与「不想自己运维 Docker/主机」的诉求冲突，且需要维护 ingress/证书/存储/节点，直接排除（保留为内网离线部署形态，见 [architecture.md](../architecture.md) §0.1 形态 B）。

### 3.4 分场景推荐

| 场景                                                        | 推荐                                                                                   | 理由                                                                                  |
| ----------------------------------------------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| 最小改造、单人/小团队维护、要尽快上线                       | **Fly.io Machines 全量**                                                               | 镜像不变、SSE 无请求上限、Machines 兼作沙箱、`auto_stop=suspend` 控成本；约 6-10 人日 |
| 标准云、需要托管 PG/对象存储/密钥/审计、团队 3 人以上       | **Cloud Run（services + worker pools + Jobs）+ Cloud SQL/Neon + GCS + E2B/Modal 沙箱** | 生态完整、按请求计费、与 GitHub Actions/WIF 集成好；约 13-19 人日                     |
| 已深度使用 AWS（IAM/VPC/账单）                              | **ECS Fargate 常驻 + Lambda Web Adapter 接 HTTP/SSE + Aurora Serverless v2 + S3**      | 避免跨云；代价是双层部署与 LWA 适配，25 人日以上                                      |
| 主要在 Azure                                                | **Azure Container Apps + Azure Database for PostgreSQL + Blob Storage**                | ACA 与现有镜像兼容；注意 40 分钟 ingress 上限与常驻 worker 成本                       |
| 想保留 Python 原生开发体验、重沙箱/批处理（评测、批量生成） | **Modal 托管 orchestrator/tool-service + Sandboxes；前端与 BFF 仍在 Cloud Run/Fly**    | Modal 的 Sandbox/Queue/定时能力最贴合 agent 场景                                      |
| 只托管官网与静态前端                                        | **Vercel / Cloudflare Pages + 独立 API 域名**                                          | 与后端解耦；注意 Cookie 同源约束需 LB 路由 `/api`                                     |

## 4. 目标架构（以 Cloud Run 主线为例）

### 4.1 分层图

```text
                       Internet / 企业内网
                              |
                 Global HTTPS LB + Cloud Armor (WAF/限流)
                    |                         |
        /api/*, /healthz                /* 静态资源
                    |                         |
            BFF (Cloud Run service :8007)   GCS bucket + Cloud CDN
                    |                         (apps/web dist)
      +-------------+-------------+------------------+
      |             |             |                  |
 account-service  orchestrator  workspace-service  connector/knowledge/llm-gateway
 (Cloud Run)      (Cloud Run)   (Cloud Run)        (Cloud Run，内部 ingress)
      |             |             |
      |      LangGraph checkpointer|
      |             +------+------+
      |                    |
      |           Cloud SQL for PostgreSQL 16 (6 个库)
      |                    |
      |           Secret Manager（密钥）/ GCS（交付物）
      |
      +--> account 内部签发 JWT <- 服务间 TokenBroker（全部服务）

  orchestrator 的常驻循环（独立 worker pool，不接公网）:
      outbox dispatcher | jobs worker | interrupts sweeper
  workspace 的 sandbox reaper（worker pool）

  一次性 / 定时 Jobs:
      6 库 alembic upgrade head | 数据初始化 | tool_calls 过期清理

  沙箱平面（Cloud Run 无 KVM）:
      tool-service -> workspace-service -> SandboxProvider -> E2B / Modal Sandboxes
```

### 4.2 部署单元映射

| 部署单元                | 现状（compose）      | 目标形态                                    | 角色                                                 | 入口                                | 伸缩                                     | 备注                                             |
| ----------------------- | -------------------- | ------------------------------------------- | ---------------------------------------------------- | ----------------------------------- | ---------------------------------------- | ------------------------------------------------ |
| `apps/web` 静态产物     | nginx 容器 :8080     | **GCS + Cloud CDN + LB 路由**               | SPA                                                  | 公网 `/`                            | CDN                                      | 构建产物 `dist/` 上传 bucket；`/index.html` 回退 |
| `apps/site`             | Vercel               | Vercel（不变）                              | 官网                                                 | 公网                                | 平台托管                                 | 静态导出（`output: export`）                     |
| bff                     | 容器 :8007           | Cloud Run **service**                       | 同源 API 门面、Cookie 会话、SSE 代理                 | 公网（仅经 LB `/api`）              | 0..N                                     | 限流需外置（§5.8）                               |
| agent-orchestrator HTTP | 容器 :8002           | Cloud Run **service**（内部 ingress）       | run 编排 API、SSE、审批/回答                         | 仅 BFF 与浏览器直连（stream token） | 0..N                                     | LangGraph checkpointer = PG                      |
| agent-orchestrator 循环 | 同进程               | Cloud Run **worker pool**                   | outbox dispatcher / jobs worker / interrupts sweeper | 无                                  | **max-instances=1**（先单实例，见 §4.6） | 需要能读 PG、调 llm-gateway                      |
| account-service         | 容器 :8001           | Cloud Run service（内部）                   | 用户/组织/配额/JWT 签发                              | 仅 BFF 与服务间                     | 0..N                                     | 签名密钥必须持久（GCS KMS/Secret Manager 挂载）  |
| llm-gateway             | 容器 :8003           | Cloud Run service（内部）                   | 唯一模型出口                                         | 仅内部                              | 0..N                                     | 上游超时 60s，注意 Cloud Run 超时配置            |
| connector-service       | 容器 :8004           | Cloud Run service（内部）                   | 连接器框架                                           | 仅内部                              | 0..N                                     | 当前无内置连接器                                 |
| knowledge-service       | 容器 :8006           | Cloud Run service（内部）                   | 文档知识库                                           | 仅内部                              | 0..N                                     | 本地 embedding 模型随镜像                        |
| workspace-service HTTP  | 容器 :8005           | Cloud Run service（内部）                   | 工作区/交付物/沙箱生命周期                           | 仅内部                              | 0..N                                     | 沙箱 provider 换外部（§5.6）                     |
| workspace 沙箱回收      | 同进程               | worker pool                                 | reaper 定时回收过期沙箱                              | 无                                  | 1                                        | 也可改 Cloud Scheduler + Job                     |
| tool-service            | 容器 :8008           | Cloud Run service（内部）                   | 内置工具目录与执行面                                 | 仅 orchestrator                     | 0..N                                     | 纯 HTTP，无后台循环                              |
| PostgreSQL 16（6 库）   | postgres 容器        | **Cloud SQL for PostgreSQL 16** 或 **Neon** | 业务库                                               | 私有 IP / connector                 | 托管                                     | 一服务一库保留                                   |
| Redis 7                 | compose 起但业务未用 | 不部署                                      | -                                                    | -                                   | -                                        | 若后续做限流/缓存再上 Memorystore                |
| migrate 作业            | compose `migrate`    | Cloud Run **Job**                           | 6 库 alembic upgrade head                            | 无                                  | 一次                                     | 部署流水线中先于服务发布执行                     |

### 4.3 请求路径与 SSE 续流

普通请求：浏览器 → LB（`/api/*`）→ BFF（Cookie/CSRF 校验、租户解析）→ 目标服务（内部 JWT）→ 返回。

长任务与事件流：

1. 浏览器 `POST /api/v1/conversations/{id}/messages/stream` 命中 BFF，BFF 判 `text/event-stream` 后走流式代理（`services/bff/bff/api/proxy.py:226-245`），不缓冲、不压缩。
2. LB 会把 SSE 连接保持到平台上限；Cloud Run 请求超时到点后连接被切，前端 `EventSource` 自动重连并带 `Last-Event-ID`。
3. orchestrator 的 `GET /v1/runs/{id}/events/stream` 从 PG 按 `seq > Last-Event-ID` 回放（`services/agent-orchestrator/agent_orchestrator/api/v1/runs.py:169-175`），run 状态完全由 PG 推导，**不依赖粘性会话**。
4. 人工审批/提问挂起期间没有活动连接：状态落在 `Run.status=awaiting_approval/awaiting_confirmation/awaiting_input` + LangGraph checkpointer；用户回来时 `POST /runs/{id}/approve|confirm|answer` 命中任意实例都能 resume。

因此平台只需保证两件事：**SSE 能流式透传**、**单连接超时后客户端可重连续流**。不需要 WebSocket，也不需要粘性会话。

### 4.4 数据平面

- **Postgres**：Cloud SQL for PostgreSQL 16（私有 IP + Cloud SQL Auth Proxy / Python Connector），或 Neon（连接串 + `sslmode=require`）。保留 6 个独立库（`beauty_account`/`beauty_agent`/`beauty_connector`/`beauty_knowledge`/`beauty_workspace`/`beauty_tool`），`alembic_version` 各自独立。
- **连接数**：Cloud Run 多实例 + PG 连接数上限 → 必须用连接池（Cloud SQL 内置 connector + `pool_size` 限制，或 PgBouncer/Neon pooler）；orchestrator 的 PG checkpointer 另有 1 个连接池，需单独限额。
- **迁移**：`alembic upgrade head` 在 Cloud Run Job 中按顺序执行 6 个库；部署流水线里 Job 成功后才发布服务（对应 compose 的 `migrate` 作业）。
- **交付物**：orchestrator 与 workspace 的 `artifact_store` 从 `database|filesystem` 改为 `gcs`（新增 `GcsArtifactStore`，`common/artifacts.py` 已有 Protocol），下载走预签名 URL，避免大文件穿 BFF。
- **备份/保留**：Cloud SQL 自动备份 + PITR；`tool_calls` 与 `run_events` 保留期策略由 Job 执行（设计文档 [tool-service.md](./tool-service.md) §17.1 的 30 天保留期）。

### 4.5 沙箱平面

Cloud Run/Fargate 不提供 `/dev/kvm`，`microsandbox` 起不来，而且是四道硬墙叠加：① microsandbox 本地 runtime 要 Linux + KVM（`msb doctor` 自检，Alpine/musl 不支持）；② Cloud Run/Lambda/Fargate 的容器本身已被平台放进 gVisor/Firecracker microVM，不暴露 `/dev/kvm`、不允许特权容器、无嵌套虚拟化；③ microsandbox 沙箱必须跨 HTTP 请求常驻（实现里 `detached=True` 再按名重连，`workspace/services/sandbox_provider.py:115-122,151-178`），而 serverless 实例短命、缩容即销毁；④ 还需要可写 `MSB_HOME` 且能访问 OCI registry（`workspace/core/config.py:43-45`）。microsandbox 官方托管仍是 private beta，不能作为生产依赖。改造点收敛在 workspace-service 的 `SandboxProvider` 接口（`create/exec/write_file/read_file/destroy`，exec 支持 `argv` + `stdin` + `timeout`）：

| Provider 方案                             | 隔离                           | argv/stdin                                                 | 文件系统                             | 生命周期                                                   | 适配量                                                | 结论                                               |
| ----------------------------------------- | ------------------------------ | ---------------------------------------------------------- | ------------------------------------ | ---------------------------------------------------------- | ----------------------------------------------------- | -------------------------------------------------- |
| **E2B**                                   | Firecracker microVM            | 命令是**字符串**（shell 语义），无 argv 数组；stdin 需实测 | 沙箱内 FS + 模板                     | `timeout`/onTimeout kill 或 pause（暂停不计费）/autoResume | 中（要写 wrapper 适配 argv，契约保真度打折）          | 备选（隔离硬、单价更低）                           |
| **Modal Sandboxes**                       | gVisor 默认，可切 `runtime=vm` | `sb.exec(argv 变参)` + stdin 可写                          | `sb.filesystem.read_text/write_text` | `idle_timeout` 滑动 + 绝对 timeout（≤24h）+ FS 快照        | 小（官方 Python SDK，映射 5 个方法）                  | **首选 v1**：契约最贴合、出网 allowlist 能落 deny  |
| **Fly Machines**                          | Firecracker microVM            | REST `command: string[]`（argv）+ stdin + timeout          | exec + volume                        | suspend 快照/auto_stop                                     | 中（无官方 Python SDK，httpx 直连；无出网 allowlist） | 已选 Fly 全量时首选（最便宜）                      |
| **Cloud Run code execution（2026 新增）** | 待验证                         | 待验证                                                     | 待验证                               | 待验证                                                     | 待评估                                                | 观察项：若支持任意 argv + 文件读写即可替代外部沙箱 |
| **Lambda MicroVMs（2026-10）**            | microVM                        | 待验证（API 有 RunMicrovm/Suspend/Resume）                 | 有状态                               | 挂起/恢复                                                  | 待评估                                                | AWS 路线候选                                       |
| **自建 gVisor/nsjail**                    | gVisor/nsjail                  | 可自控                                                     | 自控                                 | 自控                                                       | 大（要特权节点/K8s）                                  | 排除：serverless 给不了隔离内核                    |

沙箱与文件工具的关系不变：tool-service 每个工具调用 `ensure(scope_key)` → `exec(python3 -c helper)`（请求 JSON 走 stdin、结果走 stdout，见 [tool-service.md](./tool-service.md) §8.1），workspace-service 负责 provider 与 TTL；**serverless 只替换 provider 实现**，不改工具协议。

### 4.6 后台任务平面（最容易踩坑的部分）

三个常驻循环与两个定时任务：

| 任务               | 现状                                                                           | serverless 形态                      | 多实例要求                                                                                                                                                             |
| ------------------ | ------------------------------------------------------------------------------ | ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| outbox dispatcher  | `container.py:398-446` asyncio task，1s 轮询                                   | worker pool 常驻                     | **必须单实例**：`events/outbox.py:119-133` 的 claim 是普通 SELECT 无锁，多实例会同时取走同一批事件（至少一次语义可容忍，但重复率上升）                                 |
| jobs worker        | `container.py:485-493`，PG `SKIP LOCKED`（`repositories.py:337-356`）          | worker pool 常驻（可多实例）         | 领取安全，但**租约无续期**：`jobs_lease_seconds` 默认 60s（`core/config.py:60`）小于 skill 超时 600s（`:156`），长 run 会被另一实例重领重跑 → 先 max=1，或先补心跳续租 |
| interrupts sweeper | `container.py:493`，15s 扫描（`interrupts.py` docstring 自述 P0-3 未彻底幂等） | worker pool 常驻                     | 幂等不彻底（P0-3），超时事件可能重复一两次 → 先单实例                                                                                                                  |
| 沙箱 reaper        | `workspace/main.py:38-50,93-98`，60s 扫描                                      | worker pool 或 Cloud Scheduler + Job | 单实例即可；切托管沙箱后可由 provider TTL 接管，直接关闭                                                                                                               |
| 迁移               | compose 一次性                                                                 | Cloud Run Job                        | 一次                                                                                                                                                                   |

原则：**先单实例常驻，把正确性交给后续「claim 锁 + 心跳」改造**；不要为了缩容到 0 把后台循环改成「请求触发」，那样 outbox/job 会停摆。worker pool 的 `min-instances=1`（常驻）是这一期的成本底线。

### 4.7 成本与伸缩（量级估算）

以 Cloud Run + Cloud SQL 为例，只给量级（具体单价以云厂商账单为准）：

- 8 个 HTTP 服务 min-instances=0：空闲时几乎无计算费用，冷启动 1-3s（Python + uvicorn；镜像精简后可更低）。
- orchestrator worker pool：1 个 0.5 vCPU/512MiB 常驻实例 → 月成本约 10-20 美元量级（远低于常驻 8 服务）。
- Cloud SQL：db-custom-1-3840 起（约 30-60 美元/月）+ 存储；开发环境可先用 Neon free/scale-to-zero。
- GCS/CDN：静态资源与交付物按量；沙箱（E2B/Modal）按执行秒数计费。
- 对比现状单机 compose：云上固定成本更高（PG + worker 常驻），但免去了主机运维、弹性与备份；如果只是自用/内网演示，Fly.io 的 `auto_stop=suspend` 成本更低。

## 5. 逐项改造清单

每项给出：现状（file:line）→ 改法 → 验收。改造尽量落在「配置/适配器/部署文件」，不改业务领域逻辑。

### 5.1 镜像与进程入口（PORT、探针、启动命令）

**现状**：`deploy/docker/python-service.Dockerfile:77` 用 `case $SERVICE` 把 8 个服务硬编码到 8001-8008；HEALTHCHECK（`:71-72`）用 urllib 打固定端口 `/healthz`；镜像里没有读取 `PORT`。

**改法**：

1. 入口改为读取平台注入的 `PORT`（Cloud Run 必给 `PORT`，默认 8080）：

```dockerfile
# deploy/docker/python-service.Dockerfile（示意）
ENV PORT=8080
# CMD 保持单入口脚本，端口取 PORT
CMD ["sh", "-c", "exec uvicorn ${SERVICE_MODULE}.main:app --host 0.0.0.0 --port ${PORT:-8080} --no-access-log"]
```

不要用 `$PORT` 反引号误写成构建参数；`PORT` 是运行时环境变量。服务模块名仍由 `SERVICE` build arg 决定（保留单镜像多服务）。

2. 健康检查：Cloud Run 用 **startup probe + liveness probe**（HTTP GET `/healthz`），`/readyz` 作为 readiness（会查 DB，避免冷启动时 LB 打流量）。Dockerfile 的 `HEALTHCHECK` 保留给本地 compose，云上忽略即可。

3. 启动时间：uvicorn 单 worker；不要在 Cloud Run 里开 `--workers N`（CPU 少、连接池翻倍），并发靠实例数。

**验收**：本地 `docker run -e PORT=9090` 起服务，`curl :9090/healthz` 通过；Cloud Run 部署后 revision 状态 Ready，startup probe 成功。

### 5.2 配置分层与服务间寻址

**现状**：所有服务默认 `127.0.0.1:80xx`（`services/bff/bff/core/config.py:19-24`、orchestrator/others 的 `*_SERVICE_URL`），compose 用容器名覆盖；BFF 还有 `app_origin`、`ready_probe_upstreams` 等本地默认。

**改法**：

1. 新增 `deploy/serverless/` 目录（与 `deploy/docker/` 并列），每个环境一份 `.env.yaml` 或 Terraform 变量：
   - 服务间地址：`BFF__RUNTIME_BASE_URL=https://orchestrator-xxx.a.run.app`（内部 ingress 时同 URL，靠 IAM 限制调用方）；
   - 账户签发：`*__AUTH_ISSUER=https://account-xxx.a.run.app`；
   - 内部密钥：`*__INTERNAL_SECRET` 从 Secret Manager 注入；
   - `*__DATABASE_URL` 用 Cloud SQL 实例连接名或 Neon 连接串；
   - `*__AUTO_CREATE_TABLES=false`（生产契约不变）。

2. 内部服务用 **Cloud Run internal ingress + IAM**（或 `--no-allow-unauthenticated`），只有 BFF 的 service account 被授予 `roles/run.invoker`；或统一走 Serverless VPC Connector 走私网。

3. 不要引入服务发现组件：服务 URL 是稳定的，直接配环境变量（compose 的容器名 === Cloud Run 服务名，配置形态一致）。

**验收**：在 Cloud Run 上 `curl` 内部服务 URL（无 IAM 令牌）返回 403；带 BFF SA 令牌返回 200；服务间日志里不再出现 `127.0.0.1`。

### 5.3 API 与 worker 角色拆分（orchestrator 关键改造）

**现状**：`core/container.py:398-446/485-493` 在 HTTP 服务进程里启动 outbox dispatcher、jobs worker、interrupts sweeper；多副本部署会重复跑循环：outbox `claim` 是无锁 SELECT（`packages/events/events/outbox.py:119-133`），jobs 租约 60s 无续期而 run 可能跑 600s（`core/config.py:60,156`），sweeper 自述幂等未彻底（`services/interrupts.py:1-16`）。

**改法**：给 orchestrator 增加角色开关（只影响生命周期装配，不改节点逻辑）：

```python
# services/agent-orchestrator/agent_orchestrator/core/config.py（新增）
role: Literal["all", "api", "worker"] = "all"  # 默认 all 保持本地/compose 行为
```

- `role=api`：只起 FastAPI（含 SSE），**不启动** dispatcher/worker/sweeper；
- `role=worker`：起 uvicorn 仅暴露 `/healthz`（Cloud Run worker pool 需要端口做健康检查），启动三个循环，不接收业务流量；
- `role=all`：现状（默认），本地开发/compose 不变。

装配处只加一处判断（`AppContainer.start` 里 / `_assemble_graph` 尾部）：`if settings.role in ("all", "worker"): start_worker(...); start_sweeper(...)`。

workspace-service 同理：`workspace/core/config.py` 加 `role`，reaper 只在 worker 角色启动（`workspace/main.py:95-97`）。

**验收**：`ROLE=api` 时进程内无 `job`/`outbox`/`sweeper` 日志；`ROLE=worker` 时 `/healthz` 可用、日志有 dispatcher 心跳；本地默认 `all` 行为与现在完全一致（黄金 run 9/9）。

### 5.4 数据库、连接池与迁移 Job

**现状**：compose 用 `postgres:16` + 一次性 `migrate` 作业（`deploy/docker-compose.yml:72-96`）按顺序对 6 个库 `alembic upgrade head`。

**改法**：

1. 用同一条命令做成 **Cloud Run Job**（`deploy/serverless/migrate.sh` 或容器内脚本），入口按顺序跑 6 个库：`account-service`/`agent-orchestrator`/`connector-service`/`knowledge-service`/`workspace-service`/`tool-service`，每个库对应 `<PREFIX>__DATABASE_URL`。
2. 连接串：加 `?ssl=require`（Neon）或使用 Cloud SQL Python Connector（`asyncpg` + `cloud-sql-python-connector` 的 `create_async_connector()`）；不建议裸露公网 IP + 密码。
3. 连接池：每个服务显式设 `pool_size`/`max_overflow`（如 5/5），orchestrator 的 LangGraph checkpointer 单独限额；总连接数 = 实例数 × 池大小 必须小于 PG 上限，必要时上 PgBouncer/Neon pooler。
4. 迁移顺序：流水线 `build → migrate Job → 发布服务`；Job 失败则终止发布。
5. 6 库 initial：全新环境用 `deploy/postgres/initdb/01-create-databases.sql` 的等价 SQL（Cloud SQL 里执行一次）或改为「每个库独立实例」，本项目保持一实例多库。
6. **禁止启动自动迁移**：现有 `AUTO_CREATE_TABLES` 只建表不跑 alembic；新增 `MIGRATE_ON_STARTUP=false` 并在启动路径显式拒绝（多实例并发迁移会打架）。
7. 迁移顺序与 compose 一致：account → orchestrator → connector → knowledge → workspace → tool（`deploy/docker-compose.yml:83-88`）；新增 `deploy/serverless/migrate.sh` 读统一 URL 矩阵，本地与流水线共用（结构照抄 compose 的 migrate 段）。
8. 并发保护三件套：平台 Job `parallelism=1` / `maxRetries=0`、流水线全局 `concurrency` 串行、脚本开头对每个库取 PG advisory lock（`SELECT pg_advisory_lock(hashtext('beauty:<service>'))`，结束释放）——防止「自动重试 + 人工重跑」撞车。
9. 失败与超时：聚合 Job 总超时 600s（6 库通常 <60s）；失败即流水线红，不自动重试到成功；失败时旧 revision 继续服务，可安全回滚部署。
10. 回滚语义：镜像回滚到上一 digest，数据库只前滚（`alembic downgrade` 仅 dev 用）；破坏性变更拆 expand/contract 两个版本，发布前打快照/PITR 标记。

**验收**：Job 日志显示 6 个库 `upgrade head` 成功；重复执行幂等；`alembic check` 在 CI 仍通过；服务启动日志无 `AUTO_CREATE_TABLES` 建表记录。

### 5.5 交付物存储：对象存储适配器

**现状**：只有 `database`（字节进 PG）与 `filesystem`（本地目录）两种（`packages/common/common/artifacts.py`，`build_artifact_store(backend, root)`）；orchestrator 默认 `database`，workspace 默认 `filesystem` + 本地卷。

**现状的关键细节**：`load_bytes` 不按 row 的 storage 字段分派（`packages/common/common/artifacts.py:138` 只在写入时记录 storage，`:162-180` 读取时只用注入的那个 store）——直接切 `ARTIFACT_STORE` 会让历史 `database` 对象读不到。所以切换前必须「按原 key 回填/复制对象」，或给读取路径补 storage 分派（推荐 v1.5 做，这才支持零停机回退）。

**改法**：v1 只做 **S3 兼容** 后端（AWS S3 / Cloudflare R2 / MinIO / GCS S3 interop 一份实现覆盖；GCS 原生留 v1.5）。在 `packages/common/common/` 增加 `S3ArtifactStore`，实现现有 Protocol：

```python
class ArtifactStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
```

- 用 `boto3` 同步客户端 + `anyio.to_thread`（不引 aiobotocore，少一个依赖）；`backend=s3` + `bucket`/`region`/`endpoint_url`/`prefix`/`presign_ttl_seconds` 配置，key 继续用 `build_artifact_key`，S3 层只加 prefix；
- 新增 **预签名下载 URL**（`presigned_url(key, expires)`，TTL ≤300s）；两种接法：BFF 透传 307（省带宽、暴露 bucket 域）或 BFF 跟随 307 拉流转发（行为不变、多一跳）——v1 选后者，前者作带宽优化；
- 凭据不用静态 key：走 workload identity（Cloud Run service account / ECS task role / ACA managed identity），boto3 默认 credential chain；bucket 全 public access block、禁 ACL、默认 SSE；
- `build_artifact_store` 增加 `s3` 分支；orchestrator/workspace 的 `ARTIFACT_STORE=s3`；
- 本地 compose 仍默认 `database|filesystem`（保持零云依赖的本地开发）。

**验收**：上传交付物后 bucket 有对象；下载走预签名 URL（不带租户 JWT 也能在有效期内下载，过期 403）；单测用 `moto`（dev 依赖）+ botocore Stubber 的 fake S3（零网络）；切后端前对象已回填，历史交付物可读。

### 5.6 沙箱：替换 SandboxProvider（不改工具协议）

**现状**：`workspace/core/config.py:30-45` 的 `sandbox_provider=microsandbox` 需要 `/dev/kvm`；`workspace/services/sandbox_provider.py` 定义协议（`create/exec/write_file/read_file/destroy`，exec 支持 argv/stdin/timeout）；tool-service 只经 HTTP 调 workspace（`/v1/sandboxes:ensure`、`/exec`、`/files`）。

**改法**：新增 `E2BSandboxProvider`（首选）或 `ModalSandboxProvider`：

```text
create(spec)         -> e2b.Sandbox.create(template=spec.image, timeout=spec.ttl_seconds)
exec(name, argv, ...) -> sandbox.commands.run(" ".join(shlex.quote(a) for a in argv), timeout=..., stdin=stdin)
write_file/read_file -> sandbox.files.write/read
destroy(name)        -> sandbox.kill()
```

要点：

1. **argv 语义**：E2B/Modal 的 exec 接口是「命令字符串」；provider 内部用 `shlex.quote` 拼接，仍不引入 shell 解释（没有管道/重定向/变量展开）；
2. **stdin**：tool-service 的 helper 协议要求请求 JSON 走 stdin；E2B commands.run 支持 stdin（Modal `sb.exec` 同理），需在 provider 层透传；若某平台不支持，退化为把请求写入沙箱内临时文件（`/tmp/req.json`）再 `python3 -c ... < /tmp/req.json`，协议层不变；
3. **镜像模板**：沙箱镜像需预装 `python3` 并创建 `/workspace` 目录（tool-service helper 的 `BM_WORKSPACE_ROOT` 默认 `/workspace`）；E2B 用 sandbox template 构建，Modal 用 image 构建；
4. **TTL**：provider 的 `timeout` 与 workspace 的滑动 TTL 对齐（`TOOL__SANDBOX_TOOL_TTL_SECONDS` 默认 1800s，上限由 provider 决定），touch 续期映射到 provider 的 keepalive/续期 API；
5. **失败语义**：`sandbox.exec_timeout` 等错误码保持不变（provider 抛 `UpstreamError`），tool-service 已映射为 `tool.timeout`。

**验收**：tool-service 的 199 个测试中工具协议层不变；E2E 在云上跑 `read`/`write`/`grep` 各一次，确认文件落在沙箱内、沙箱销毁后不可访问；单测用 fake provider（不联网）。

### 5.7 SSE 超时与断线续传

**现状**：前端已经用 fetch 手写 SSE（`apps/web/src/lib/sse.ts`，带 `credentials: include` 与 `Last-Event-ID` 头），run 事件流在断线后自动重连并带最大 `seq`（`apps/web/src/components/runs/use-run-events.ts:11,36-64`），`streamRunEvents` 把「没有 `stream.end` 就断开」视为网络中断交给上层重连（`apps/web/src/lib/runs-api.ts:287-309`）。服务端 `GET /v1/runs/{id}/events/stream` 按 `Last-Event-ID` 从 PG 回放（`api/v1/runs.py:169-175`）。**这一层在 serverless 下不需要重写**。

**需要改的是三处超时配置**：

1. **Cloud Run 请求超时**：服务级 `--timeout`（默认 300s，可配到 3600s）。orchestrator 与 BFF 设 3600s；其他内部服务 60s 足够。
2. **负载均衡后端超时**：Global LB 的 backend service `timeoutSec` 默认 30s，必须调到与 Cloud Run 一致（3600），否则 LB 先断。
3. **BFF 上游客户端**：`services/bff/bff/main.py:42-47` 是 `httpx.Timeout(30.0, connect=5.0)`（httpx 的 30s 同时是连接/读/写/池超时，读超时是**每次 read 的间隔上限**，不是整条流的总时长）。orchestrator 的 SSE keepalive 每 `sse_keepalive_polls(30) × sse_poll_interval(0.5s) = 15s` 发一帧（`core/config.py:29-30`、`services/run_stream.py:81-83`），15s < 30s，因此**当前长流不会因 BFF 超时断**——但这是两个默认值的巧合。改法：新增 `BFF__STREAM_READ_TIMEOUT_SECONDS`（默认 0=不限制），流式代理单独建 `httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0))`；`proxy.py:226-245` 的 `_proxy_stream` 已用 `stream=True` + `BackgroundTask aclose`，只换 client 即可。

另：`POST /conversations/{id}/messages/stream`（chat 模式直答）没有事件 `seq`，断线不能精确续传。现状可接受：assistant 消息由服务端落库，前端重连失败后重新拉会话消息即可恢复最终正文；若要逐字恢复，后续给 chat 流加单调 `seq` 并复用同一套游标（列为 P2 增强，不阻塞上云）。

**验收**：把 Cloud Run 超时临时调成 60s、制造超过 60s 的 run，确认前端自动重连、事件不重复不丢失（按 seq 去重）；BFF 日志无 `ReadTimeout`。

### 5.8 认证、密钥、Cookie 与限流

**现状与改法**：

| 项               | 现状                                                                                          | serverless 改法                                                                                                                                                                          |
| ---------------- | --------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| account 签名密钥 | `core/idp.py:99-118` 空则进程内生成（`account/main.py:95-107` 注释已写明多实例/重启必须持久） | 生成一次写入 Secret Manager：优先支持新增 `ACCOUNT__SIGNING_KEY_PEM`（直接注入 PEM 字符串，代码优先级 PEM > path），或 `SIGNING_KEY_PATH` 指向只读挂载；JWT 才能跨实例一致               |
| 内部签发密钥     | `INTERNAL_SECRET`（compose 明文 env，`docker-compose.yml:228` 等）                            | Secret Manager + `--set-secrets` 注入；轮换用双密钥过渡（`internal_secret` 支持列表后需要改造，先单密钥 + 变更窗口）                                                                     |
| BFF 会话         | `session_secret` 默认 dev、`cookie_secure=False`、`app_origin=http://localhost:5173`          | 生产 `COOKIE_SECURE=true`、`APP_ORIGIN=https://app.example.com`（与 LB 同源）、`SESSION_SECRET` 走 Secret Manager                                                                        |
| CSRF             | 双提交 Cookie + header（现状已实现）                                                          | 不变；同源约束由 LB 路由 `/api/*` 保证                                                                                                                                                   |
| 限流             | `bff/main.py:67-89` 进程内滑动窗口                                                            | ① Cloud Armor 按 IP/路径限流（边缘）；② 需要按租户限流时用 Memorystore Redis 或 account-service 的配额（已有 usage ledger）；进程内限流保留为兜底（多实例时放大 N 倍，不可作为唯一手段） |
| CORS             | 当前同源，无 CORS 配置                                                                        | 保持同源；若前端与 API 分域，必须补 CORS + `SameSite=None; Secure`，不推荐                                                                                                               |

**验收**：两个实例重启后旧 access token 仍可校验（密钥持久）；`Set-Cookie` 带 `Secure; HttpOnly; SameSite=Lax`；连续请求超过限流阈值返回 429（边缘或服务端一致）。

### 5.9 前端静态托管与同源路由

**现状**：`apps/web` 构建产物由 nginx 容器托管（`deploy/web/nginx.conf`），`/api` 反代 BFF；本地 dev 用 Vite 代理（`vite.config.ts:11-16`）。

**改法（Cloud Run 路线）**：

1. `npx nx build @beauty/web` 产物上传 GCS bucket，开启 Cloud CDN；
2. Global LB 配置两条 path matcher：`/api/*`、`/healthz`、`/readyz` → BFF 的 serverless NEG；`/*` → GCS backend bucket；
3. SPA 回退：GCS 无法像 nginx `try_files` 那样回退 `index.html`；用 LB 的 custom error response（404 → `/index.html`，200）或 Cloud CDN 的 SPA 模式；
4. 缓存：`assets/*`（带 hash）`Cache-Control: public, max-age=31536000, immutable`；`index.html` `no-cache`；
5. 前端环境：`VITE_AUTH_MODE=session`、`VITE_RUNS_API_URL` 留空（同源），构建时注入；不要在前端打包任何密钥。

Fly.io 路线可保留 nginx 容器（镜像不变），只把 `bff:8007` 改成公网/internal 服务地址；Modal/其他平台同理，静态层可继续用 Vercel/Cloudflare Pages + 自定义域把 `/api` 反代到 BFF（需确认该平台支持 SSE 反代）。

**验收**：直接访问 `https://app.example.com/runs` 返回 SPA（不是 404）；`https://app.example.com/api/health` 返回 BFF 健康；Cookie 在 https 下正常下发。

### 5.10 可观测性

**现状**：结构化日志 + OTel/Sentry 已接（`packages/common/common/observability.py`、`sentry.py`、`tracing.py`、`metrics.py`），compose 里靠本地 stdout。

**改法**：

1. 日志：Cloud Run 自动采集 stdout/stderr 到 Cloud Logging，无需 agent；确认 `LOG_LEVEL` 与 JSON 结构化输出（现有 structlog 配置）。
2. 追踪：设 `OTEL_EXPORTER_OTLP_ENDPOINT`（Cloud Trace 或自建 Collector）；Cloud Run 提供 `traceparent` 头，BFF 透传即可串起全链路。
3. 指标：保留进程内 `/metrics`（若有）；serverless 实例频繁重建，指标以 Cloud Monitoring 为主，业务指标（tool_calls_total 等）通过 OTel metrics exporter 上报。
4. Sentry：`SENTRY_DSN` 走 Secret Manager；release 用镜像 tag。
5. 告警：Cloud Monitoring 告警策略（5xx 比例、p95 延迟、Cloud SQL 连接数、Job 失败）替代 compose 的人工观察。

**验收**：一次请求在 Cloud Trace 里能看到 BFF → orchestrator → llm-gateway 的 span；错误日志能在 Cloud Logging 按 trace id 检索；Sentry 有 release 标记。

### 5.11 CI/CD 与 IaC

**现状**：GitHub Actions 9 个 job（`.github/workflows/ci.yml`），无部署流水线；本地 compose 手工起。

**改法**：

1. 新增 `.github/workflows/deploy.yml`（手动触发或 main 分支），五个 job：`build-push`（复用 Dockerfile，按 SERVICE 矩阵 buildx 构建并推 Artifact Registry/ECR，输出 digest）→ `migrate`（平台 Job 等成功）→ `deploy`（按 digest 更新 8 个服务 + BFF + 静态前端）→ `smoke`（`/healthz` 全服务、`/readyz`、关键 API、前端首页）→ `rollback`（smoke 失败自动重部署上一 digest，DB 不 downgrade）。
2. 认证：GitHub OIDC + Workload Identity Federation（不落长期密钥）；Secret Manager 存运行时密钥；prod 用 GitHub Environment 审批 + 全局 `concurrency: deploy-<env>`。
3. IaC：`deploy/serverless/terraform/`（modules + envs/staging|prod）管理 Cloud Run 服务/worker pool/Jobs/LB/Cloud SQL/GCS/Secret/IAM；镜像按 digest 部署（不可变），tag 另打 `sha-<git sha>` 与 `v<semver>`。
4. 现有 9 个质量 job 不动；新增 `deploy.yml` 与 CI 门禁解耦（合并到 main 只跑质量门禁，部署由 tag/手动触发）。
5. 环境：`staging` / `prod` 两套变量文件；数据库与 bucket 隔离；staging 每次 main 自动、prod 用 tag。
6. 回滚：Cloud Run revision 流量切换（`gcloud run services update-traffic`）或按旧 digest 重部署；保留最近 N 个 revision 与镜像。

**验收**：从 main 合并到 staging 全自动；migrate 失败则 deploy 不执行；回滚一条命令切回上一 revision。

### 5.12 本地开发保持不变

- `deploy/docker-compose.yml` + `npm run dev` 仍是唯一本地启动方式（含 SQLite 开发模式、零云依赖）；
- 新增本地验证：`docker compose -f deploy/docker-compose.yml config -q`、`scripts/golden-run/golden-run.sh`（进程内 ASGI、零网络）继续作为变更门禁；
- serverless 相关文件（`deploy/serverless/`、`.github/workflows/deploy.yml`）不进入本地运行路径；
- 新增配置项必须同时更新 `docs/local-dev.md` 与各服务 `.env.example`（现有门禁 `check-workspace.sh` 会查未登记文档）。

### 5.13 顺带修复的既有缺口（与 serverless 无关，但上云前必须修）

| 缺口                   | 现状证据                                                                                                                                                                                | 影响                                                        | 修法                                                                                                                                     |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| 镜像未打包 `skills/`   | `deploy/docker/python-service.Dockerfile:31-33` 只 COPY `packages`/`services`；`skills.py:16` 用 `parents[4]/skills` 定位，镜像内不存在 → 启动告警并返回空技能清单（`skills.py:30-34`） | 技能包功能在容器部署下一直是空的（本地 compose 同样受影响） | Dockerfile 增加 `COPY skills ./skills`（或 `AGENT_ORCHESTRATOR__SKILLS_DIR` 指向只读挂载）；加一条启动后 `GET /v1/skills` 非空的冒烟测试 |
| outbox claim 无锁      | `packages/events/events/outbox.py:119-133` 普通 SELECT；`drain_once` 注释承认第 2、3 步之间崩溃会重投（`:267-311`）                                                                     | 多实例 dispatcher 会放大重复投递                            | 单实例 worker 兜底；后续给 claim 加 `FOR UPDATE SKIP LOCKED` + `dispatching` 状态                                                        |
| jobs 租约无续期        | `repositories.py:337-356` 只在 claim 时写 `lease_until`；`jobs_worker.py:148,161` 只清空，无心跳                                                                                        | run 执行超过 60s 会被另一实例重领重跑                       | worker 单实例兜底；后续在 `_dispatch` 期间周期续租 + `JOBS_LEASE_SECONDS` 调到覆盖最长 run（如 900）                                     |
| 启动自动建表           | `service_kit/lifespan.py:26-41` 仅在 `auto_create_tables=true` 时 `create_all`                                                                                                          | 误配会让多实例并发建表                                      | 生产保持 false；新增 `MIGRATE_ON_STARTUP=false` 并启动校验                                                                               |
| 交付物本地目录         | workspace `artifact_store=filesystem` + `./.artifacts`（`workspace/core/config.py:19-20`）；orchestrator `./.documents`（`:126`）                                                       | serverless 本地盘不共享/不持久                              | 短期 workspace 改 `database`（零代码），中期对象存储（§5.5）                                                                             |
| BFF readiness 依赖上游 | `bff/core/config.py:38` `ready_probe_upstreams=False`（默认不探测）                                                                                                                     | 若打开，冷启动时会因上游未起而一直 not ready                | 保持 false；平台 readiness 只看 BFF 自身 DB/进程健康                                                                                     |

## 6. 分阶段实施步骤

排期按「先消除硬阻塞，再搬无状态，再搬有状态与后台，最后切流量」；每阶段都能独立回滚。人日按 1 名熟悉仓库的工程师估算。

### 阶段 0：决策与 spike（2-3 人日，阻塞后续）

| 任务           | 内容                                                                                                                                                 | 验收                                                                                                      |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| 0.1 沙箱 spike | 用 E2B、Modal Sandboxes、Fly Machines 各写一个最小 PoC：`create → exec(argv+stdin) → write_file/read_file → destroy`；测 10 并发、p95 延迟、单次计费 | 三选一（或二选一）结论写回本文 §4.5；tool-service 的 helper 协议（stdin JSON → stdout JSON）在 PoC 上跑通 |
| 0.2 长流验证   | Cloud Run 30 分钟以上 SSE + LB backend timeout + 断线重连（`Last-Event-ID`）；同时验证 BFF 流式代理不缓冲                                            | 事件不丢不重；断线 5s 内自动续流；BFF 无 `ReadTimeout`                                                    |
| 0.3 PG 与迁移  | 建 Cloud SQL/Neon 实例，跑 `deploy/serverless/migrate.sh`（6 库），再起一个服务连上去                                                                | 6 库 `alembic_version` 正确；服务 `/readyz` 通过                                                          |
| 0.4 镜像改造   | `PORT` 入口、`COPY skills/`、探针、`MIGRATE_ON_STARTUP` 校验；本地 compose 回归                                                                      | `docker run -e PORT=xxxx` 正常；`scripts/golden-run/golden-run.sh` 9/9；技能清单非空                      |

**退出条件**：沙箱方案拍板 + 平台请求模型确认（SSE 能跑）+ 迁移链路跑通。任一不满足则暂停上云，先修阻塞。

### 阶段 1：无状态服务上云（2-3 人日）

1. 部署 account-service / llm-gateway / connector-service / knowledge-service / bff 到 staging（容器镜像、内部 ingress、Secret Manager、平台 URL 注入）。
2. 固定 staging 域名：`staging-api.example.com`（LB → BFF），account/其他服务用内部 URL。
3. BFF 配置：`APP_ORIGIN`、`COOKIE_SECURE`、`SESSION_SECRET`、6 个上游 URL、`INTERNAL_SECRET`。
4. 冒烟：登录/刷新 token/知识库检索/outbox 运维接口（若启用）全链路走 BFF。

**验收**：`curl https://staging-api.example.com/api/health` 200；登录后 Cookie 正确；两个 BFF 实例间会话可通（无粘性）。

### 阶段 2：orchestrator 上云（3-4 人日）

1. 加 `ROLE=api|worker|all`（§5.3），orchestrator 与 workspace 各拆出一个 worker 部署单元；worker pool 先 `max-instances=1`。
2. PG checkpointer 强制启用（`postgres` URL 即走 `AsyncPostgresSaver`），并把 saver 建表放进迁移 Job（避免并发 `setup()`）。
3. 迁移 Job 进流水线；`MIGRATE_ON_STARTUP=false`。
4. SSE：Cloud Run timeout 3600s、LB backend timeout 3600s、BFF 流式 client 独立超时；验证 ask_question/审批挂起 10 分钟后 resume 落到另一个实例。
5. 黄金 run（进程内零网络）在 CI 继续跑；新增 staging 上的真实 run 冒烟（建 run → 审批 → 完成）。

**验收**：staging 建 run 并完成；两个实例轮换（手动 kill 一个）不影响恢复；SSE 断线续传无重复事件。

### 阶段 3：workspace / tool-service / 沙箱 / 对象存储（4-6 人日）

1. 实现选定的 `SandboxProvider`（E2B/Modal/Fly），保留 `microsandbox` 用于本地/自建；配置 `WORKSPACE__SANDBOX_PROVIDER=<new>`。
2. workspace 改 `ARTIFACT_STORE=database`（短期）或接入对象存储（§5.5）；沙箱 reaper 交 provider TTL 或 worker 单实例。
3. tool-service 上云（内部 URL），`AGENT_ORCHESTRATOR__TOOL_SERVICE_ENABLED=true`；文件工具/搜索/写工具在沙箱内 E2E。
4. ask_question E2E：提问挂起 → 前端卡片 → answer API → 图恢复（跨实例）。
5. 沙箱隔离回归：路径逃逸/symlink/硬链/双租户用例在真实 provider 上跑一遍（不能只跑 fake）。

**验收**：staging 上 read/write/edit/grep/glob 全部可用；写工具审批后落盘；沙箱销毁后文件不可访问；工具审计表有记录。

### 阶段 4：前端静态与流量切换（2-3 人日）

1. `apps/web` 产物上 GCS + CDN；LB path routing（`/api/*` → BFF，`/*` → bucket），SPA 回退。
2. 域名与证书：`app.example.com`；Cookie 域名/secure/samesite 校验；CSP/安全头在 LB 或 CDN 层补。
3. 灰度：先内部用户 → 5% → 50% → 100%；回滚用 LB 权重切回旧环境（Fly/容器）。
4. 观测：Cloud Monitoring 看板 + 告警（5xx、SSE 断连率、run 滞留数、沙箱失败率、PG 连接数）。

**验收**：真实用户全流程（登录 → 对话 → 任务 → 审批 → 提问 → 交付物下载）走通；错误预算内；回滚演练一次。

### 阶段 5（可选）：收口与清理（1-2 人日）

- 下线 compose 生产用途（保留本地开发）；删 web nginx 镜像（若保留自建则不动）；
- 更新 `docs/architecture.md` §10 部署章节、`docs/local-dev.md`、`docs/roadmap.md`；
- CI 增加 staging 冒烟 job；`deploy/serverless/` 纳入版本管理。

## 7. 备选路线的差异改造

主线（Cloud Run）的清单在 §5-§6；若最终选其他路线，共性改造（PORT/优雅退出、worker 拆分、迁移 Job、密钥注入、对象存储、沙箱 provider）不变，差异如下。

### 7.1 Fly.io（改造量最小）

**部署形态**：一个 app 多 process group（app + worker）共用同一镜像；`[processes]` 区分命令，`[http_service]` 只挂在 app 组；worker 组必须 `auto_stop_machines = false` + `min_machines_running = 1`——Fly 官方明确应用无法告知 proxy「我在忙」，常驻后台循环会被 autostop 当成空闲杀掉。

```toml
# fly.toml（示意，仅示意，不落仓库）
[processes]
  app = "uvicorn bff.main:app --host 0.0.0.0 --port 8080"
  worker = "python -m agent_orchestrator.worker"

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = "suspend"
  min_machines_running = 1
```

**差异点**：

- SSE：Fly 无请求时长上限，`/stream` 直接长连，不需要强制续流（但仍保留 Last-Event-ID 兜底）。
- 沙箱：**不要尝试嵌套 microsandbox**（Machines 本身是 microVM，不提供嵌套虚拟化）；把 SandboxProvider 实现为 Fly Machines（REST `POST /v1/apps/{app}/machines/{id}/exec`，command 是 argv 数组 + stdin + timeout，语义最接近现有 microsandbox），workspace-service 自己维护机器生命周期与 TTL。
- 数据库：Fly Managed Postgres 或 Neon；`DATABASE_URL` 直接注入；迁移用一次性 `fly machine run --rm`（或在 CI 里跑 alembic）。
- 对象存储：Tigris（S3 兼容），`ARTIFACT_STORE=s3`。
- 密钥：`fly secrets set`（加密存储、运行时注入）；PEM 可用 base64 env + 启动脚本落地（需小改 account 启动逻辑或写 entrypoint）。
- 成本：8 服务 + worker 常驻，shared-cpu-1x 1GB 约 6.7 美元/月/机；小体量比 Cloud SQL 便宜。
- 风险：无出网 allowlist（沙箱 deny 只能近似）、多区域/合规弱、机器生命周期自运维。

### 7.2 Modal（Python 原生 + 托管沙箱）

**部署形态**：每个服务一个 Modal App，用 `@modal.asgi_app()` 挂 FastAPI；orchestrator 的 worker 循环不能 `while True` 常驻，要改成「一次 drain 一批」的函数并按定时/队列触发。

```python
# modal_app.py（示意，仅示意，不落仓库）
app = modal.App("beauty-orchestrator")

@app.function(image=orchestrator_image(), secrets=[modal.Secret.from_name("beauty-env")])
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def api():
    from agent_orchestrator.main import app
    return app

@app.function(image=orchestrator_image(), schedule=modal.Period(seconds=30))
def sweep():
    # 复用现有 drain_once / process_one，改成短任务形态
    ...
```

**差异点**：

- 后台循环：把 `outbox.drain_once`、job `process_one`、interrupt sweeper 改成幂等短任务（现有代码已基本按「一次一批」实现，改动中等）；sweeper 的幂等缺口（见 §8 R3）在此路线被放大。
- 沙箱：Modal Sandboxes 直接作为 provider（exec argv + stdin + filesystem + idle_timeout + block_network），**这条路线沙箱契约匹配度最高**。
- 对象存储：Modal Volumes 不适合多实例共享大文件，用外部 S3/GCS。
- 数据库：Modal 不托管 PG，用 Neon/Supabase/自建。
- SSE：Streaming endpoint 可用，但需实测长连接与平台超时；更合适的形态是「BFF/orchestrator 在 Cloud Run/Fly，代码执行与沙箱在 Modal」的混合部署。
- 风险：平台绑定较强（编程模型、镜像构建、定时语义），团队需接受 Modal 的部署心智。

### 7.3 AWS（LWA + Fargate + Aurora）

**部署形态**：HTTP 服务用 Lambda + Lambda Web Adapter（容器镜像直接跑 uvicorn，LWA 负责事件转换与 response streaming）；后台循环用 ECS Fargate 常驻（`ROLE=worker`）；迁移用 ECS RunTask 或 CodeBuild。

**差异点**：

- SSE：LWA 支持流式响应，但同步 HTTP 调用上限 15 分钟；长 run 必须靠 Last-Event-ID 续流 + 前端重连。orchestrator 的 SSE 建议放 ALB + Fargate（无请求时长上限），否则 15 分钟必断。
- 后台循环：Fargate 常驻、无 scale-to-zero，成本高于 Cloud Run worker pool。
- 数据库：Aurora Serverless v2 或 RDS PG16；Lambda 多并发必须配 RDS Proxy（否则连接打满）。
- 对象存储：S3（`ARTIFACT_STORE=s3`）+ 预签名。
- 密钥：Secrets Manager + task role / Lambda 执行角色；PEM 需 init/sidecar 写卷（Fargate 不能直接挂 secret 为文件）。
- 沙箱：评估 2026-10 新增 Lambda MicroVMs（有状态 microVM，支持挂起/恢复）；或接 E2B/Modal；Bedrock AgentCore Code Interpreter 只适合代码解释器类工具（会话 + executeCode 语义），不适配通用 argv 契约，不作为 SandboxProvider。
- CI：GitHub OIDC → ECR → Lambda 更新 / ECS 部署；迁移用 CodeBuild。改造量最大（25+ 人日）。

### 7.4 Azure Container Apps

**差异点**：ingress 请求超时默认 240s、最大 2400s（40 分钟）→ SSE 必须续流；后台循环放无 ingress 的 container app 且 `minReplicas=1`（不能缩到 0）；迁移用 Container Apps Jobs（manual/schedule/event）；数据库 Azure Database for PostgreSQL Flexible Server；对象存储 Blob（S3 兼容层或原生 SDK）；密钥 Key Vault；伸缩走 KEDA 规则。

### 7.5 路线对比

| 路线              | 改造量                      | 长流上限                        | 后台循环                      | 沙箱                       | 成本量级 | 主要风险                              |
| ----------------- | --------------------------- | ------------------------------- | ----------------------------- | -------------------------- | -------- | ------------------------------------- |
| Cloud Run（主线） | 中（13-19 人日）            | 60 分钟 + 续流                  | worker pools（可 1 实例常驻） | 外部 provider（Modal/E2B） | 中       | 沙箱外置；Cloud Run 数值需 spike 复核 |
| Fly.io            | 小（6-10 人日）             | 无硬上限                        | process group 常驻            | 自建 Fly Machines provider | 低       | 出网策略弱、沙箱自运维                |
| Modal             | 中（8-12 人日，含后台重写） | Streaming（需实测）             | 定时/队列短任务               | 原生 Sandboxes（最优）     | 低-中    | 平台绑定、后台模型改造                |
| AWS               | 大（25+ 人日）              | 15 分钟（Lambda）/无（Fargate） | Fargate 常驻                  | Lambda MicroVMs 或外部     | 高       | 双层网络、复杂度、连接池              |
| Azure ACA         | 中（12-16 人日）            | 40 分钟 + 续流                  | minReplicas≥1                 | 外部 provider              | 中       | 常驻 worker 无法缩容                  |

## 8. 风险与开放问题

### 8.1 风险

| 编号 | 风险                                                                               | 影响                                                                                                   | 缓解                                                                                                |
| ---- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| R1   | 沙箱供应商锁定/涨价/合规                                                           | 文件工具不可用或成本失控                                                                               | SandboxProvider 协议保持窄；至少两家 PoC；数据出境与合同先确认                                      |
| R2   | 平台 SSE 上限（Cloud Run 60 分钟、ACA 40 分钟、Lambda 15 分钟）                    | 长 run 断流                                                                                            | Last-Event-ID 续流是硬要求（已实现）；前端提示刷新恢复；orchestrator SSE 尽量走无上限入口           |
| R3   | 后台循环多副本正确性缺口（outbox 无锁 claim、jobs 租约无心跳、sweeper 幂等不彻底） | 重复投递、长 run 被重领重跑、重复超时事件                                                              | 本期 worker `max-instances=1`；后续补锁与心跳（独立任务，不在本期）                                 |
| R4   | 迁移窗口与滚动发布                                                                 | 旧代码 + 新表结构不兼容                                                                                | expand/contract：先加列/表，代码兼容后一批再删；迁移 Job 成功才放流量                               |
| R5   | 密钥轮换                                                                           | account 签名密钥轮换需 JWKS 多公钥并存（当前只暴露单 key，`account/core/idp.py:82-87`），轮换=全站重登 | 建议进 v1：JWKS 输出全部公钥、按 JWT header `kid` 验签（约 1 人日 + 测试）；否则固定密钥 + 维护窗口 |
| R6   | 同源 Cookie 与跨域                                                                 | 登录/CSRF 失败                                                                                         | LB 路由 `/api` 同源；分域则 `SameSite=None; Secure` 或 token 模式                                   |
| R7   | 冷启动                                                                             | 首请求慢（llm-gateway、knowledge 本地嵌入模型）                                                        | 关键路径 min-instances=1；镜像精简；预热 Job                                                        |
| R8   | PG 连接数                                                                          | 多实例 × 连接池打满数据库                                                                              | 连接池限额 + PgBouncer/Neon pooler/RDS Proxy                                                        |
| R9   | 可观测成本                                                                         | 日志/追踪量放大                                                                                        | 采样、结构化日志、按环境分级                                                                        |
| R10  | 平台配额                                                                           | 并发、沙箱并发、出网被限                                                                               | 提前申请配额；压测；退避                                                                            |
| R11  | 出网依赖                                                                           | 模型 API、知识库抓取、沙箱拉镜像                                                                       | 平台出网白名单；llm-gateway 稳定出口                                                                |
| R12  | 本地与云端沙箱语义分裂                                                             | microsandbox 与托管 provider 行为不同                                                                  | 同一套 provider 契约测试（fake + 真 provider 各一套），staging 跑一次 E2E                           |

### 8.2 已拍板问题

| 编号 | 问题                                                                     | 结论                                                        | 依据                                                       |
| ---- | ------------------------------------------------------------------------ | ----------------------------------------------------------- | ---------------------------------------------------------- |
| A    | 是否选定 GCP？是否要求云中立（Terraform 抽象、双栈）                     | 选 GCP Cloud Run 为主线；Terraform 只抽接口，不做双栈       | 双栈成本翻倍且拖慢落地；Terraform 已隔离大部分差异         |
| B    | 沙箱三选一：自建嵌套虚拟化 K8s / Modal·E2B / Cloud Run Sandboxes Preview | Modal Sandboxes 为 v1，E2B 备选，Fly Machines 若选 Fly 平台 | 契约匹配度、出网 deny、Python SDK、按秒计费；自建 K8s 排除 |
| C    | 长 run 语义：HTTP 请求内跑完 vs 异步 run + 重连                          | 保持 HTTP 内跑完 + Last-Event-ID 续流；异步任务化列 P3+     | Cloud Run 3600s 硬顶可接受；改异步要动状态机与前端         |
| D    | BFF/orchestrator 常驻 min-instances 成本是否可接受                       | 接受：worker 1 实例，BFF/orchestrator 关键路径 min=1        | 首请求体验与 SSE 稳定性优先                                |

### 8.3 仍开放的问题

| 编号 | 问题                                        | 当前倾向                                                  | 关闭时点 |
| ---- | ------------------------------------------- | --------------------------------------------------------- | -------- |
| Q1   | 月度预算上限                                | 需产品确认；固定成本主要是 Cloud SQL + 常驻 worker + 沙箱 | 阶段 0   |
| Q2   | 域名与证书归属                              | `app.example.com` + 通配符证书；由 LB 托管                | 阶段 4   |
| Q3   | 双轨期长度（compose 与 serverless 并存）    | compose 只留本地开发；生产单轨                            | 阶段 5   |
| Q4   | account JWKS 多公钥（无感轮换）是否进 v1    | 建议进 v1（约 1 人日，避免每次轮换全站重登）              | 阶段 1   |
| Q5   | 数据地域与合规（跨境沙箱调用）              | 单区域（如 asia-east1）；沙箱区域与数据区域一致           | 阶段 0   |
| Q6   | 是否接受 1-3 秒冷启动                       | 接受；关键路径 min-instances=1                            | 阶段 1   |
| Q7   | 对象存储最终形态（S3 兼容 / GCS 原生）      | v1 先 S3 兼容（一份实现多云），GCS interop（HMAC）验证    | 阶段 3   |
| Q8   | 是否保留 microsandbox 作为本地开发 provider | 保留；云端只用托管 provider                               | 阶段 0   |

## 9. 附录

### 9.1 平台必须注入的配置矩阵（最小集）

| 配置                                                                           | 服务                                                    | 当前默认                        | 云端注入                                    |
| ------------------------------------------------------------------------------ | ------------------------------------------------------- | ------------------------------- | ------------------------------------------- |
| `<PREFIX>__DATABASE_URL`                                                       | account/orchestrator/connector/knowledge/workspace/tool | `sqlite+aiosqlite:///./*.db`    | 托管 PG URL（asyncpg + ssl）                |
| `<PREFIX>__AUTO_CREATE_TABLES`                                                 | 同上                                                    | false（service-kit 默认）       | 保持 false                                  |
| 启动期迁移开关                                                                 | 同上                                                    | 不存在                          | 新增 MIGRATE_ON_STARTUP=false；迁移只走 Job |
| `INTERNAL_SECRET`                                                              | account/orchestrator/tool/bff                           | 空或 dev 值                     | Secret Manager，四服务同值                  |
| `<PREFIX>__AUTH_ISSUER`                                                        | account/orchestrator/tool/workspace                     | `http://127.0.0.1:8001`         | account 内网/公网 URL                       |
| `<PREFIX>__AUTH_ENABLED`                                                       | 同上                                                    | true                            | 生产 true（compose 演示 false）             |
| `ACCOUNT__SIGNING_KEY_PATH`                                                    | account                                                 | 空（进程内生成）                | 持久 PEM（secret 卷挂载）                   |
| `BFF__SESSION_SECRET`                                                          | bff                                                     | dev 默认                        | 平台 secret；dev 默认在生产启动即失败       |
| `BFF__APP_ORIGIN`、`BFF__COOKIE_SECURE`                                        | bff                                                     | `http://localhost:5173` / false | `https://app.example.com` / true            |
| `BFF__*_BASE_URL`（6 上游）                                                    | bff                                                     | 全部 `127.0.0.1`                | 平台内部域名                                |
| `BFF__RUNTIME_PUBLIC_URL`                                                      | bff                                                     | `127.0.0.1:8002`                | 公网 runtime 域（SSE 直连）                 |
| `BFF__STREAM_READ_TIMEOUT_SECONDS`                                             | bff                                                     | 不存在（30s 总超时）            | 0（不限制，推荐）                           |
| `AGENT_ORCHESTRATOR__*_SERVICE_URL`                                            | orchestrator                                            | 全部 `127.0.0.1`                | 平台内部域名                                |
| `AGENT_ORCHESTRATOR__ROLE`                                                     | orchestrator                                            | 不存在（all）                   | `api` / `worker`                            |
| `AGENT_ORCHESTRATOR__TOOL_SERVICE_ENABLED`                                     | orchestrator                                            | false                           | true（tool-service 是唯一文件工具路径）     |
| `WORKSPACE__ARTIFACT_STORE`                                                    | workspace                                               | `filesystem`                    | `database`（过渡）/`s3`（推荐）             |
| `WORKSPACE__ARTIFACT_BUCKET`/`REGION`/`ENDPOINT_URL`/`PRESIGN_TTL_SECONDS`     | workspace                                               | 不存在                          | 见 §5.5 与 §9.3                             |
| `WORKSPACE__SANDBOX_PROVIDER`                                                  | workspace                                               | `microsandbox`                  | `modal` / `e2b` / `fly`                     |
| `TOOL__WORKSPACE_SERVICE_URL`、`TOOL__KNOWLEDGE_SERVICE_URL`                   | tool                                                    | `127.0.0.1:8005/8006`           | 平台内部域名                                |
| `LLM_GATEWAY__API_KEY`                                                         | llm-gateway                                             | 空                              | Secret Manager                              |
| `CONNECTOR__CREDENTIAL_SECRET`                                                 | connector                                               | dev 默认                        | Secret Manager（轮换需重加密）              |
| `AGENT_ORCHESTRATOR__MCP_SECRET`、`MCP_RESOURCE_URL`、`MCP_OAUTH_REDIRECT_URI` | orchestrator                                            | dev 值 / 127.0.0.1              | Secret Manager / 公网域名                   |
| `<PREFIX>__SENTRY_DSN`、OTLP endpoint                                          | 全部                                                    | 空（no-op）                     | 平台可观测端点                              |

### 9.2 敏感信息清单

- 必须进 Secret Manager：`INTERNAL_SECRET`、`BFF__SESSION_SECRET`、account 签名 PEM、`CONNECTOR__CREDENTIAL_SECRET`、`AGENT_ORCHESTRATOR__MCP_SECRET`、`LLM_GATEWAY__API_KEY`、沙箱 provider token、DB 密码（或改用 IAM 数据库认证）。
- 禁止：写进镜像、Terraform state 明文、GitHub Actions 明文、前端 bundle；CI 用 OIDC 身份，运行时从平台 secret 注入。
- 轮换语义（现状）：`SIGNING_KEY_PATH` 轮换 = 全站重登；`BFF__SESSION_SECRET` 轮换 = 全员会话失效；`INTERNAL_SECRET` 轮换要求 8 服务同时切换（需双值支持）；`CONNECTOR__CREDENTIAL_SECRET`、`MCP_SECRET` 轮换会让旧密文不可解（需重加密脚本或重新授权）。
- 生产启动 fail-fast：签名密钥缺失时 account 会在进程内新生成密钥（多副本不一致），必须在启动时断言 PEM 存在且可解析。

### 9.3 对象存储要点（S3 兼容）

- 新增 `S3ArtifactStore` 实现现有 `ArtifactStore` Protocol（put/get/delete），boto3 同步客户端 + `anyio.to_thread`，不引 aiobotocore；key 继续用现有 `build_artifact_key`，S3 只加 prefix。
- 预签名下载两案：BFF 透传 307（省带宽，暴露 bucket 域）或 BFF 跟随 307 拉流转发（行为不变、多一跳）。v1 选后者，前者作带宽优化项。
- 切换前注意：现有 `load_bytes` 不按 row 的 storage 分派（`packages/common/common/artifacts.py:162-180`），直接改 env 会让历史 `database` 对象读不到；需先按原 key 回填对象，或补读取分派（v1.5，支持零停机回退）。
- 测试：`moto`（dev 依赖）+ botocore Stubber，零网络；真实 S3 集成测试放 nightly 可选 job。

### 9.4 平台事实来源与限制

- 已抓取正文：AWS Lambda 配额（15 分钟/90 分钟、10240MB、1000 并发）、Lambda Web Adapter README、Fly Machines/Autostop 文档、Modal Sandboxes/Web Functions 文档、E2B 文档索引与沙箱页、Cloudflare Workers 限制、Vercel Functions 限制、Azure Container Apps ingress/Jobs 文档。
- 未能抓取正文：`cloud.google.com`（网络限制）→ Cloud Run 请求超时/Jobs/worker pools/并发数值以官方文档为准，阶段 0 spike 复核；E2B 部分页面 CSS 阻塞正文。
- 价格为转引（Fly 汇总文章，2026-09/10），下单前以官方价目复核。

### 9.5 与现有文档的关系

- 架构与选型：[architecture.md](../architecture.md)（§0 最终选型、§10 容器平台待补）。本文补齐 serverless 章节，实施完成后把结论回写 §10 与 [roadmap.md](../roadmap.md)。
- 内置工具服务：[tool-service.md](./tool-service.md)（沙箱执行协议、限额、错误码）；本文只替换 provider，不改工具协议。
- 本地开发：[local-dev.md](../local-dev.md)（compose 一键启动）；serverless 不改变本地路径。
- 质量门禁：`scripts/check-docs.sh`、`scripts/check-workspace.sh`、`scripts/check-versions.sh`、`scripts/golden-run/golden-run.sh` 对本次改动同样适用。

### 9.6 待核实清单

1. Cloud Run 请求超时 / Jobs 任务超时 / worker pools / 并发的准确数值与 2026-10 政策。
2. Cloud Run code execution（2026 新增）是否支持任意 argv + 文件系统读写，能否替代外部沙箱。
3. LangGraph `AsyncPostgresSaver.setup()` 在并发冷启动下是否幂等（建议放进迁移 Job 建表）。
4. 各沙箱 provider 的 exec stdin 与 argv 实际行为（PoC 验证，特别是 E2B 命令字符串语义）。
5. 平台对跨域 Cookie（`SameSite=None; Secure`）与 CORS credentials 的默认行为。
6. 平台 Secret 挂载为只读文件时，account 的「缺失则写盘」逻辑是否安全（现有实现仅缺失时写）。

文档状态：设计定稿（2026-10-25），未实施；实施时按 §6 分期推进，每阶段完成后更新 §1.3 与本文档状态。
